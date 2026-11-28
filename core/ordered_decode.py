# -*- coding: utf-8 -*-
"""
保序并行解码的调度闸门 / Ordered parallel decode gate.

多线程解码时，解码完成的先后顺序与文件顺序无关，但下游（推理线程 → 主线程）必须按
files_tbr 的原始顺序消费，否则 report.db 行序、连拍分组与按鸟种分配的星级配额都会错位。
本模块只负责两件事：谁可以领取下一个序号、谁现在可以交付；它不持有图像、不碰队列、
不做任何 I/O，因此可以脱离 PhotoProcessor 单独测试。

Parallel decoding finishes out of order, while the downstream stages must consume
results in files_tbr order. This gate only hands out indices and allows a worker to
deliver when its index is the one expected next. It holds no image data, touches no
queue and performs no I/O, so it can be unit-tested on its own.
"""

import threading
from typing import Callable, Optional

# 等待对端进展时的轮询间隔（秒）：既不让线程空转烧 CPU，也不让停止请求延迟太久响应。
# Poll interval while waiting for progress: no busy spin, but stop requests stay responsive.
_POLL_INTERVAL_S = 0.05


class OrderedDecodeGate:
    """
    保序并行解码闸门：分配序号 + 控制交付顺序 + 幂等结束。

    工作线程循环执行「领取序号 → 解码 → 等待轮次 → 交付」，序号从 0 到 total-1。
    领取上限是 emit_next + workers，因此同时在解码的帧不超过 workers 张；队列里最多再留
    一张待消费的帧，合计在途大图 <= workers + 1，线程数因而既是吞吐旋钮也是内存上限。

    A gate for ordered parallel decoding. Workers loop over "claim an index -> decode ->
    wait for turn -> deliver", with indices 0..total-1. Claims are capped at
    emit_next + workers, so at most workers frames decode at once and at most one more
    waits in the queue: in-flight frames stay at workers + 1, which makes the worker
    count a memory cap as well as a throughput knob.

    参数:
    total (int): 待解码的照片总数，<=0 表示空批次（任何线程都领不到序号）
    workers (int): 并行解码线程数，<1 按 1 处理
    should_stop (Callable[[], bool]): 停止请求查询函数，返回 True 时所有等待立即结束

    返回:
    OrderedDecodeGate: 闸门实例

    Parameters:
    total (int): number of photos to decode; <=0 means an empty batch
    workers (int): parallel decoder count, values below 1 are treated as 1
    should_stop (Callable[[], bool]): stop-request probe; True ends every wait at once
    """

    def __init__(self, total: int, workers: int, should_stop: Callable[[], bool]) -> None:
        """初始化闸门的序号分配与交付状态 / Initialise the index and delivery state."""
        self._total = max(0, int(total))
        self._workers = max(1, int(workers))
        self._should_stop = should_stop or (lambda: False)
        # next_index: 下一个待领取的序号；emit_next: 下一个该交付的序号
        # next_index: next index to hand out; emit_next: next index the consumer may receive
        self._next_index = 0
        self._emit_next = 0
        self._sentinel_claimed = False
        self._aborted = False
        self._cond = threading.Condition()

    @property
    def total(self) -> int:
        """待解码照片总数 / Total number of photos to decode."""
        return self._total

    @property
    def workers(self) -> int:
        """并行解码线程数（至少 1） / Decoder count, always >= 1."""
        return self._workers

    @property
    def aborted(self) -> bool:
        """是否已中止（停止请求或未预期异常） / Whether the stage has been aborted."""
        with self._cond:
            return self._aborted

    @property
    def delivered(self) -> int:
        """已按序交付的数量 / Number of results delivered in order so far."""
        with self._cond:
            return self._emit_next

    def claim_index(self) -> Optional[int]:
        """
        领取下一个待解码序号。

        在交付窗口内（序号 < 已交付序号 + 线程数）才会成功；窗口满时阻塞等待其他线程
        推进。收到停止请求或闸门已中止时立刻返回 None，让线程干净退出。

        返回:
        Optional[int]: 0 基序号；None 表示没有更多任务或需要停止

        Claim the next index to decode. Claims stay within the delivery window
        (index < delivered + workers); when the window is full the caller blocks until
        another worker advances it. Returns None once the batch is exhausted, the stage
        is aborted, or a stop was requested.
        """
        with self._cond:
            while True:
                if self._aborted or self._should_stop():
                    self._abort_locked()
                    return None
                if self._emit_next >= self._total:
                    return None
                pos = self._next_index
                if pos < self._total and pos < self._emit_next + self._workers:
                    self._next_index = pos + 1
                    return pos
                self._cond.wait(timeout=_POLL_INTERVAL_S)

    def wait_turn(self, index: int) -> bool:
        """
        等待轮到自己交付。

        解码完成的顺序是乱的，只有序号等于 emit_next 的线程可以入队，其余线程在这里等待，
        直到前序结果全部交付完成。停止请求或中止会打破等待，避免永久阻塞。

        参数:
        index (int): 本线程持有的 0 基序号

        返回:
        bool: True 可以交付；False 已中止，调用方应直接退出

        Wait until this index is the one to deliver. Only the worker holding emit_next
        may enqueue; the rest wait here until earlier results are delivered. A stop
        request or abort breaks the wait instead of blocking forever.
        """
        with self._cond:
            while self._emit_next != index:
                if self._aborted or self._should_stop():
                    self._abort_locked()
                    return False
                self._cond.wait(timeout=_POLL_INTERVAL_S)
            return True

    def mark_delivered(self, index: int) -> bool:
        """
        记录某个序号已交付，并唤醒其余等待中的线程。

        参数:
        index (int): 刚刚交付的 0 基序号

        返回:
        bool: True 表示全部结果都已交付，调用方可以结束线程

        Record that this index has been delivered and wake the other waiters.
        Returns True when every result has been delivered, so the caller can finish.
        """
        with self._cond:
            if index != self._emit_next:
                # 调用方必须先 wait_turn 成功，这里只做防御性校验，不推进序号
                # Callers must have passed wait_turn; this is a defensive no-op check.
                return self._emit_next >= self._total
            self._emit_next = index + 1
            is_last = self._emit_next >= self._total
            self._cond.notify_all()
            return is_last

    def abort(self) -> None:
        """
        中止解码阶段并唤醒所有等待者（幂等）。

        线程无论是正常结束、单张失败、停止请求还是未预期异常退出，都调用一次；
        被唤醒的线程看到 aborted 后立即返回，不会苦等一个永远不会到来的序号。

        Abort the stage and release every waiter (idempotent). Called on any exit path,
        so no thread keeps waiting for an index that will never come.
        """
        with self._cond:
            self._abort_locked()

    def take_sentinel(self) -> bool:
        """
        抢占「结束哨兵发送权」，保证哨兵最多发送一次。

        返回:
        bool: True 表示本次调用应当发送哨兵；False 表示已经有人发过了

        Claim the right to send the end sentinel, so it is emitted at most once.
        Returns True for the caller that must send it.
        """
        with self._cond:
            if self._sentinel_claimed:
                return False
            self._sentinel_claimed = True
            return True

    def _abort_locked(self) -> None:
        """在已持有条件锁的前提下标记中止并唤醒等待者 / Mark aborted; caller holds the lock."""
        self._aborted = True
        self._cond.notify_all()
