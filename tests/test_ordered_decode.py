# -*- coding: utf-8 -*-
"""并行解码闸门：序号分配、交付顺序、幂等哨兵与中止唤醒。

Ordered decode gate: index handout, delivery order, exactly-once sentinel and
abort wake-up.

背景 / Background:
解码侧改成多线程后，完成顺序与文件顺序无关，但下游（推理线程 → 主线程）必须按
files_tbr 顺序消费，否则 report.db 行序、连拍分组与按鸟种分配的星级配额都会错位。
顺序逻辑因此被抽成不持图像、不碰队列、不做 I/O 的 OrderedDecodeGate，本文件直接
覆盖它的四条关键路径：空批次、顺序交付、停止请求、异常中止；再加一条真实多线程
用例，确认「后发的先完成」时交付顺序仍然是文件顺序。

Decoding now runs on several threads, so completion order no longer matches file
order while the downstream stages must still consume in files_tbr order. The
ordering logic lives in OrderedDecodeGate (no images, no queues, no I/O) and this
file covers its four critical paths — empty batch, in-order delivery, stop
request, abort — plus one real multi-threaded case where later files finish first
and must still be delivered in file order.
"""
import threading
import time

from core.ordered_decode import OrderedDecodeGate


def _gate(total, workers=3, stop=None):
    """构造一个默认不会停止的闸门 / Build a gate that never stops by default."""
    return OrderedDecodeGate(total, workers, stop or (lambda: False))


# ---------------------------------------------------------------------------
# 边界条件：空批次、非法参数
# Boundary conditions: empty batch and invalid arguments
# ---------------------------------------------------------------------------

def test_empty_batch_claims_nothing():
    """空批次：没有任何序号可领，也不算中止。"""
    gate = _gate(0)
    assert gate.total == 0
    assert gate.claim_index() is None
    assert gate.aborted is False


def test_invalid_arguments_are_clamped():
    """负数总数按 0、线程数 <1 按 1 处理，不会出现除零或死等。"""
    gate = OrderedDecodeGate(-5, 0, None)
    assert gate.total == 0
    assert gate.workers == 1
    assert gate.claim_index() is None


def test_single_worker_claims_then_exhausted():
    """单线程：领一张、交付一张、再领下一张，全部交付后领取返回 None。

    窗口是 emit_next + workers，所以单线程必须交替交付，不能连领。
    """
    gate = _gate(3, workers=1)
    claimed = []
    while True:
        pos = gate.claim_index()
        if pos is None:
            break
        claimed.append(pos)
        assert gate.wait_turn(pos) is True
        gate.mark_delivered(pos)
    assert claimed == [0, 1, 2]
    assert gate.delivered == 3


# ---------------------------------------------------------------------------
# 交付顺序：领取窗口与按序入队
# Claim window and in-order delivery
# ---------------------------------------------------------------------------

def test_claim_window_caps_frames_in_flight():
    """未交付时最多领 workers 张：第 workers+1 张必须等前序交付。

    这条同时守住内存契约——每张 45MP 预览解码后约 172MB，窗口敞开会直接把内存打爆。
    """
    gate = _gate(10, workers=2)
    assert gate.claim_index() == 0
    assert gate.claim_index() == 1
    pending = []
    waiter = threading.Thread(target=lambda: pending.append(gate.claim_index()), daemon=True)
    waiter.start()
    waiter.join(0.2)
    assert pending == []  # 窗口已满，仍在等待
    assert gate.wait_turn(0) is True
    assert gate.mark_delivered(0) is False
    waiter.join(2.0)
    assert pending == [2]


def test_delivery_walks_forward_and_last_marks_complete():
    """按序交付：每次推进一格，只有最后一个序号返回 True。"""
    gate = _gate(3, workers=3)
    assert [gate.claim_index() for _ in range(3)] == [0, 1, 2]
    assert gate.wait_turn(0) is True
    assert gate.mark_delivered(0) is False
    assert gate.delivered == 1
    assert gate.wait_turn(1) is True
    assert gate.mark_delivered(1) is False
    assert gate.wait_turn(2) is True
    assert gate.mark_delivered(2) is True
    assert gate.delivered == 3


def test_mark_delivered_ignores_out_of_order_index():
    """防御性校验：没轮到的序号不得推进交付指针。"""
    gate = _gate(3, workers=3)
    for _ in range(3):
        gate.claim_index()
    assert gate.mark_delivered(2) is False
    assert gate.delivered == 0


def test_wait_turn_blocks_until_predecessor_delivers():
    """后序结果必须等前序交付完成才能入队。"""
    gate = _gate(2, workers=2)
    gate.claim_index()
    gate.claim_index()
    returned = []
    waiter = threading.Thread(target=lambda: returned.append(gate.wait_turn(1)), daemon=True)
    waiter.start()
    waiter.join(0.2)
    assert returned == []
    assert gate.wait_turn(0) is True
    gate.mark_delivered(0)
    waiter.join(2.0)
    assert returned == [True]


# ---------------------------------------------------------------------------
# 停止与中止：不挂死、可重复调用
# Stop and abort: no hang, idempotent
# ---------------------------------------------------------------------------

def test_stop_request_ends_claims_and_marks_aborted():
    """停止请求：领取立刻返回 None，并标记为已中止。"""
    gate = _gate(5, workers=2, stop=lambda: True)
    assert gate.claim_index() is None
    assert gate.aborted is True


def test_stop_request_breaks_wait_turn():
    """等待轮次期间收到停止请求要立即返回 False，不能死等。"""
    stop = {'flag': False}
    gate = _gate(2, workers=2, stop=lambda: stop['flag'])
    gate.claim_index()
    gate.claim_index()
    returned = []
    waiter = threading.Thread(target=lambda: returned.append(gate.wait_turn(1)), daemon=True)
    waiter.start()
    waiter.join(0.2)
    assert returned == []
    stop['flag'] = True
    waiter.join(2.0)
    assert returned == [False]
    assert gate.aborted is True


def test_abort_releases_every_waiter_and_blocks_new_claims():
    """中止唤醒全部等待者，并让后续领取返回 None。"""
    gate = _gate(4, workers=4)
    for _ in range(4):
        gate.claim_index()
    returned = []
    waiters = [
        threading.Thread(target=lambda i=index: returned.append(gate.wait_turn(i)), daemon=True)
        for index in (1, 2, 3)
    ]
    for waiter in waiters:
        waiter.start()
    gate.abort()
    for waiter in waiters:
        waiter.join(2.0)
    assert returned == [False, False, False]
    assert gate.aborted is True
    assert gate.claim_index() is None


def test_abort_is_idempotent():
    """中止可重复调用（每个解码线程退出时都会调一次）。"""
    gate = _gate(2, workers=1)
    gate.abort()
    gate.abort()
    assert gate.aborted is True


# ---------------------------------------------------------------------------
# 结束哨兵：最多一次
# Sentinel: at most once
# ---------------------------------------------------------------------------

def test_sentinel_is_claimed_exactly_once():
    """无论多少线程走到收尾，只有一个能拿到哨兵发送权。"""
    gate = _gate(2, workers=1)
    assert gate.take_sentinel() is True
    assert gate.take_sentinel() is False
    assert gate.take_sentinel() is False


# ---------------------------------------------------------------------------
# 真实多线程：完成顺序被打乱，交付顺序不变
# Real threads: shuffled completion, unchanged delivery order
# ---------------------------------------------------------------------------

def test_parallel_workers_deliver_in_file_order():
    """24 张、4 线程，越靠前的照片「解码」越慢，交付顺序仍等于文件顺序。"""
    total = 24
    workers = 4
    gate = _gate(total, workers)
    delivered = []
    lock = threading.Lock()

    def worker() -> None:
        while True:
            pos = gate.claim_index()
            if pos is None:
                return
            # 序号越小睡得越久 → 完成顺序与文件顺序相反
            # Earlier files take longer, so completion order is reversed
            time.sleep((total - pos) * 0.002)
            if not gate.wait_turn(pos):
                return
            with lock:
                delivered.append(pos)
            if gate.mark_delivered(pos):
                return

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)

    assert delivered == list(range(total))
    assert gate.delivered == total
