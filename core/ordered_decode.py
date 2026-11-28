# -*- coding: utf-8 -*-
"""
保序并行解码的调度闸门 / Ordered parallel decode gate.

多线程解码时，解码完成的先后顺序与文件顺序无关，但下游（推理线程 → 主线程）必须按
files_tbr 的原始顺序消费，否则 report.db 行序、连拍分组与按鸟种分配的星级配额都会错位。
本模块只负责三件事：谁可以领取下一个序号、谁现在可以交付、这个序号是否还放得进
内存额度；它不持有图像、不碰队列、不做任何 I/O，因此可以脱离 PhotoProcessor 单独测试。

「放得进内存额度」是可选的：调用方传入 admit 回调时，闸门会在交出序号之前先问一句
「再来一帧还行吗」。判定必须发生在交号顺序上，这一点是关键 —— 若让各线程先领号再各自
抢额度，持有靠后序号的线程可能先占满内存，而持有靠前序号的线程永远等不到额度，两者互相
等成死锁。把准入放在闸门里，序号与额度就永远同序。

Parallel decoding finishes out of order, while the downstream stages must consume
results in files_tbr order. This gate hands out indices, allows a worker to deliver when
its index is the one expected next, and optionally asks whether one more frame still fits
in the memory budget. It holds no image data, touches no queue and performs no I/O, so it
can be unit-tested on its own.

Budget admission is optional: when the caller passes an admit callback, the gate asks
"does one more frame still fit?" before handing out an index. Doing the check on the
handout path is essential — if workers claimed indices first and then competed for bytes,
the thread holding a later index could take the last slot while the thread holding an
earlier index waits forever, and the two would deadlock. Keeping admission inside the
gate keeps indices and bytes in the same order.
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
    领取上限是 emit_next + workers，因此同时在解码的帧不超过 workers 张 —— 线程数只决定
    「最多几条并行」，真正的内存闸门是可选的 admit 回调（按实测字节判定），二者取更紧者。

    注意：在途大图并不仅由本闸门决定。已交付但主线程尚未消费/释放的帧、推理线程正在处理
    的帧、yolo_result_queue 里排队的帧都各自持有整幅图，因此真实在途量由字节额度器统一
    记账（core.memory_budget.InflightByteBudget），不再用「workers + 1」描述。

    A gate for ordered parallel decoding. Workers loop over "claim an index -> decode ->
    wait for turn -> deliver", with indices 0..total-1. Claims are capped at
    emit_next + workers, so at most workers frames decode at once: the worker count only
    says how much parallelism is possible, while the real memory gate is the optional
    admit callback driven by measured bytes. The tighter of the two wins.

    Note that in-flight frames are not bounded by this gate alone: frames already delivered
    but not yet consumed by the main thread, the frame under inference and the frames
    queued in yolo_result_queue each hold a full image. The real accounting lives in the
    byte budget (core.memory_budget.InflightByteBudget), so "workers + 1" no longer
    describes it.

    参数:
    total (int): 待解码的照片总数，<=0 表示空批次（任何线程都领不到序号）
    workers (int): 并行解码线程数，<1 按 1 处理
    should_stop (Callable[[], bool]): 停止请求查询函数，返回 True 时所有等待立即结束
    admit (Optional[Callable[[int], bool]]): 字节额度准入回调，收到待领取的序号，
        返回 False 表示暂时放不下、调用方必须等待；None 表示不做额度判定（旧行为）

    返回:
    OrderedDecodeGate: 闸门实例

    Parameters:
    total (int): number of photos to decode; <=0 means an empty batch
    workers (int): parallel decoder count, values below 1 are treated as 1
    should_stop (Callable[[], bool]): stop-request probe; True ends every wait at once
    admit (Optional[Callable[[int], bool]]): byte-budget admission hook receiving the index
        about to be handed out; False means "does not fit yet, wait"; None disables it
    """

    def __init__(
        self,
        total: int,
        workers: int,
        should_stop: Callable[[], bool],
        admit: Optional[Callable[[int], bool]] = None,
    ) -> None:
        """初始化闸门的序号分配与交付状态 / Initialise the index and delivery state."""
        self._total = max(0, int(total))
        self._workers = max(1, int(workers))
        self._should_stop = should_stop or (lambda: False)
        # admit 为 None 时完全保持旧行为（只看序号窗口），既有调用方与单测无需改动。
        # A None admit keeps the previous index-window-only behaviour, so existing callers
        # and unit tests are unaffected.
        self._admit = admit
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

        若构造时传入了 admit 回调，交出序号之前还要问一句「这一帧放得进内存额度吗」。
        回调必须自己保证「什么都没在途时一定放行」—— 字节额度器就是这样实现的 ——
        因此额度再紧也只会退化成逐帧串行，不会把流水线彻底卡死。admit 内部通常顺带
        预占额度，拿到序号就代表已经占好位置；调用方最终没用上该序号时必须自行释放。

        返回:
        Optional[int]: 0 基序号；None 表示没有更多任务或需要停止

        Claim the next index to decode. Claims stay within the delivery window
        (index < delivered + workers); when the window is full the caller blocks until
        another worker advances it. Returns None once the batch is exhausted, the stage
        is aborted, or a stop was requested.

        With an admit callback the gate also asks whether this frame still fits the memory
        budget before handing out the index. The callback must itself admit whenever
        nothing is in flight (the byte budget does), so a tight budget degrades the
        pipeline to one frame at a time instead of stalling it. admit usually reserves
        the bytes as a side effect, so a returned index means the slot is already held;
        a caller that ends up not using the index must release it itself.
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
                    # 额度准入：admit 通常顺带预占字节，所以每一帧都必须问一次——
                    # 若为了「保证至少一帧」而跳过第一帧，那一帧就不会被记账，后面
                    # 所有准入都看不见它占的内存，额度形同虚设。放行由 admit 自己
                    # 保证（见下）。
                    # Every frame must be asked: admit usually reserves bytes as a side
                    # effect, so skipping the first frame would leave it uncharged and
                    # every later check would ignore the memory it holds. admit itself
                    # guarantees the empty-field case is admitted.
                    if self._admit is not None and not self._admit(pos):
                        self._cond.wait(timeout=_POLL_INTERVAL_S)
                        continue
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

    def notify_progress(self) -> None:
        """
        唤醒所有等待中的线程，提示「外部条件可能已变化，重新判定一次」。

        额度释放（主线程消费完一张大图）不会自己触发闸门的条件变量，若只靠 _POLL_INTERVAL_S
        轮询，每次准入会平白多等最多 50ms。释放方在放掉字节后调用本方法，等待中的解码线程
        就能立刻重新问一次额度。本方法不改变任何序号状态，重复调用无副作用。

        Wake every waiter because an external condition (byte budget) may have changed.
        Releasing budget does not touch this gate's condition variable, so without this
        call each admission would idle for up to _POLL_INTERVAL_S. The releaser calls it
        after freeing bytes; no index state changes, so repeated calls are harmless.
        """
        with self._cond:
            self._cond.notify_all()

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
