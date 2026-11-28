# -*- coding: utf-8 -*-
"""
内存预算探测 + 在途解码帧的字节额度（无第三方依赖）。
Memory-budget probing plus a byte budget for in-flight decoded frames (stdlib only).

本模块回答三个问题：
This module answers three questions:

1. 当前进程还能拿到多少可用内存（跨平台；拿不到时返回 None）。
   How much memory is currently available to this process (None when unknown).
2. 在「当前在途帧已占 N 字节」的前提下，下一帧还进不进得来。
   Given the bytes already in flight, may one more frame be admitted.
3. 批次大小、队列批数这类轻量计数该落在什么区间。
   Which range a light counter such as a batch size must fall into.

为什么不再预设「一张照片占多少内存」：
Why there is no hard-coded per-photo footprint any more:

真实目录里 24MP / 45MP / 61MP / 100MP 甚至 151MP 会混放（机身不同、裁剪不同、RAW 预览
与用户 JPG 混放），任何固定常量都同时错两个方向：小图被无谓限流，大图被放行到 OOM。
现在改成「解码完成后用 ndarray.nbytes 上报真实字节数」，额度器据此实时决定还能放几帧。
FALLBACK_FRAME_BYTES 只在「一帧都还没解出来」时用于第一次准入，随后立刻被实测值取代。
Real folders mix 24MP / 45MP / 61MP / 100MP and even 151MP files (different bodies, different
crops, RAW previews next to user JPEGs), so any fixed constant is wrong in both directions at
once: it throttles small-frame machines and lets large-frame machines run into an OOM. The
decode thread now reports the real size via ndarray.nbytes, and the budget decides from that
measurement how many more frames may enter. FALLBACK_FRAME_BYTES is used only for the very
first admission, before any frame has been measured.

为什么以 stdlib 为主、psutil 只做兜底：
Why stdlib comes first and psutil is only a fallback:

本项目的直接依赖清单（requirements*.txt / *.spec / pyproject.toml）里没有 psutil，它只是
ultralytics 的传递依赖 —— 我们既没有声明它，也没有为它锁版本，上游换版就可能让它消失。
而这里的探测是防 OOM 的安全网，必须始终可用，所以首选 stdlib（Windows:
kernel32!GlobalMemoryStatusEx；Linux: /proc/meminfo；macOS: sysconf）。只有当 stdlib 拿不到
数字时才尝试 psutil —— 它在 macOS 上走 mach host_statistics64，能拿到 Darwin 缺失的真实
可用内存；没装该包或导入失败时安静退化为「未知」。
psutil is absent from this project's direct dependency lists (requirements*.txt / *.spec /
pyproject.toml); it only arrives transitively through ultralytics, which we neither declare it
for nor version-pin against, so an upstream bump could remove it. This probe is the OOM safety
net and must always work, so stdlib comes first (Windows: kernel32!GlobalMemoryStatusEx; Linux:
/proc/meminfo; macOS: sysconf). psutil is tried only when stdlib yields no number — on macOS it
goes through mach host_statistics64 and reports the real available memory that Darwin's sysconf
lacks; a missing package or a failed import quietly degrades to "unknown".

macOS 现状（已对 Darwin 头文件核实）：Apple libc 与 xnu 都没有定义 _SC_AVPHYS_PAGES，
CPython 的 sysconf 名字表也用 #ifdef 包着它，所以 sysconf 分支在 macOS 上恒返回 None，随后
由 psutil 兜底；两者都拿不到时额度器只信硬上限，不据此收紧。
Current macOS status (verified against the Darwin headers): neither Apple libc nor xnu defines
_SC_AVPHYS_PAGES, and CPython's sysconf name table is #ifdef-guarded on it, so the sysconf branch
always returns None there and the psutil fallback takes over; if neither yields a number the
budget trusts only the hard caps instead of tightening on a guess.

预算口径（用户要求：留 20% 空闲、最少 2 条解码线程、允许借 swap）：
Budget policy (per the user: keep 20% free, never fewer than 2 decoders, swap allowed):

- 解码可用量 = 当前可用内存 x 80%，其余 20% 留给模型、TOPIQ 与系统。
  Decoding may use 80% of the memory currently available; the other 20% stays free for the
  models, TOPIQ and the OS.
- 可用内存未知时只信硬上限，不据此收紧（既不把「未知」当零，也不当无限）。
  When memory is unknown only the hard caps apply — "unknown" is neither zero nor infinity.
- 允许借 swap：Windows 取「可用物理内存」与「可提交余量」的较小值，Linux 用 MemAvailable，
  两者都已包含可回收 / 可换出的部分。
  Swap may be borrowed: Windows takes the smaller of available physical memory and remaining
  commit allowance, Linux uses MemAvailable; both already include reclaimable / swappable memory.
- 帧字节数由实测决定，不再有「一张照片 = N MB」的假设。
  Frame size is measured, never assumed.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import deque
from typing import Deque, Dict, Optional, Tuple

# 「一帧都还没解出来」时的首次准入估计：实测 60MP 预览解码后 BGR 数组为 172MiB，
# 这里取偏大一档，宁可第一张慢一点，也不要在没有实测量时把额度放大。
# First-admission estimate used before any frame has been measured: a measured 60MP
# preview is 172MiB in BGR, so err on the larger side rather than over-admitting blind.
FALLBACK_FRAME_BYTES: int = 172 * 1024 * 1024

# 解码允许占用的可用内存比例：留 20% 空闲给模型、TOPIQ 与系统。
# Share of available memory decoding may use; 20% stays free for the models, TOPIQ and the OS.
DECODE_MEMORY_SHARE: float = 0.80

# 解码线程下限：再小的机器也至少两条，否则并行解码形同虚设（用户明确要求）。
# Lower bound for decoders: even tiny machines get two, or parallel decode is pointless.
MIN_DECODE_WORKERS: int = 2

# 硬上限：任何环境变量都不能突破，防止把线程数当作无成本旋钮。
# Hard caps no environment variable may exceed.
MAX_DECODE_WORKERS: int = 8
MAX_YOLO_PREFETCH_DEPTH: int = 8
MAX_EXIF_BATCH_SIZE: int = 256
MAX_EXIF_QUEUE_BATCHES: int = 32

# 每 N 次准入重探一次可用内存：跟得上别的进程的起伏，又不让探测本身成为开销
# （stdlib 探测约 21us，psutil 兜底约 289us，每帧都探没有必要）。
# Re-probe available memory every N admissions: tracks other processes without turning the
# probe into a cost (stdlib ~21us, psutil fallback ~289us, so per-frame probing is wasteful).
MEMORY_REFRESH_INTERVAL: int = 32

# 实测帧字节的滑动窗口长度。取窗口内最大值估计下一帧：既保守防 OOM，又能在切换到
# 小图目录后逐步回落，而不是被目录里最大的那张永远钉死。
# Sliding window of measured frame sizes. The window maximum estimates the next frame, so the
# estimate stays conservative yet decays after switching to a smaller-frame folder instead of
# being pinned forever to the largest file ever seen.
FRAME_SIZE_WINDOW: int = 8


def _available_windows() -> Optional[int]:
    """
    通过 GlobalMemoryStatusEx 读取 Windows 可用内存（字节）。
    Reads available memory on Windows via GlobalMemoryStatusEx (bytes).

    取「可用物理内存」与「可提交余量」的较小值：commit 上限（物理内存 + 页面文件）比物理
    内存更紧时，后者才是真正会失败的那个数。ullAvailPageFile 本身已包含可借的 swap 余量，
    因此这个 min 就是「允许借 swap、但两边都不超」的口径。
    Uses the smaller of available physical memory and remaining commit allowance: when the
    commit limit (RAM + page file) is tighter than RAM, that is the figure that actually fails.
    ullAvailPageFile already includes the borrowable swap headroom, so the min expresses
    "swap may be borrowed, but neither side may be exceeded".

    返回 / Returns:
        Optional[int]: 可用字节数；查询失败返回 None / bytes, or None on failure
    """
    import ctypes

    class _MemoryStatusEx(ctypes.Structure):
        """MEMORYSTATUSEX 结构体 / the MEMORYSTATUSEX structure."""

        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    avail_phys = int(status.ullAvailPhys)
    avail_commit = int(status.ullAvailPageFile)
    if avail_commit <= 0:
        return avail_phys
    return min(avail_phys, avail_commit)


def _available_linux() -> Optional[int]:
    """
    从 /proc/meminfo 读取 MemAvailable（字节，缺失时退到 MemFree）。
    Reads MemAvailable from /proc/meminfo (falls back to MemFree).

    MemAvailable 已把可回收的页缓存与可换出的匿名页算进来，因此「允许借 swap」天然成立。
    MemAvailable already counts reclaimable page cache and swappable anonymous pages, so
    "swap may be borrowed" holds without extra arithmetic.

    返回 / Returns:
        Optional[int]: 可用字节数；读取失败返回 None / bytes, or None on failure
    """
    with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as handle:
        values = {}
        for line in handle:
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip()
            if key not in ("MemAvailable", "MemFree"):
                continue
            number = parts[1].strip().split(" ", 1)[0]
            if number.isdigit():
                values[key] = int(number) * 1024
    return values.get("MemAvailable") or values.get("MemFree")


def _available_macos() -> Optional[int]:
    """
    估算 macOS 可用内存（字节）；当前 Darwin 上恒返回 None，由 psutil 兜底。
    Estimate available memory on macOS (bytes); always None on current Darwin, so the
    psutil fallback handles it.

    Darwin 不提供「可用内存」的 sysconf 项：Apple libc 的 include/unistd.h 与 xnu 的
    bsd/sys/unistd.h 都只定义总量语义的 _SC_PHYS_PAGES，没有 _SC_AVPHYS_PAGES；
    CPython 的 Modules/posixmodule.c 里对应名字表同样被 #ifdef _SC_AVPHYS_PAGES 包着，
    名字查不到就抛 ValueError: unrecognized configuration name。这里保留调用只是为了
    将来某个 Darwin 版本补齐该常量时能自动生效，抛错时安静降级为「未知」。

    Darwin exposes no "available memory" sysconf entry: Apple libc's include/unistd.h and
    xnu's bsd/sys/unistd.h define only the total-oriented _SC_PHYS_PAGES, never
    _SC_AVPHYS_PAGES; CPython's Modules/posixmodule.c guards its matching name-table entry
    with #ifdef _SC_AVPHYS_PAGES, and an unknown name makes os.sysconf raise
    ValueError: unrecognized configuration name. The call is kept only so a future Darwin
    release that adds the constant takes effect automatically.

    返回 / Returns:
        Optional[int]: 当前恒为 None（Darwin 缺该 sysconf 项）/ always None on Darwin
    """
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        # Darwin 未定义 _SC_AVPHYS_PAGES（os.sysconf 抛 ValueError）；某些平台连
        # os.sysconf 都没有（AttributeError）。两者都视为「内存未知」，交给 psutil 或硬上限。
        # Darwin lacks _SC_AVPHYS_PAGES (ValueError); some platforms have no os.sysconf at
        # all (AttributeError). Both mean "unknown" and fall through to psutil / hard caps.
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return int(pages) * int(page_size)


def _available_psutil() -> Optional[int]:
    """
    用 psutil 兜底探测可用内存（仅在 stdlib 拿不到数字时调用）。
    Fallback probe through psutil (only called when stdlib yields no number).

    psutil 不是本项目的直接依赖，只随 ultralytics 传递安装，所以这里必须把「没装」当成
    正常情况：导入失败一律返回 None，不抛异常、不打印、不影响调用方。
    psutil is not a direct dependency here and only arrives through ultralytics, so "not
    installed" must be a normal outcome: any import failure returns None without raising,
    printing, or otherwise affecting the caller.

    返回 / Returns:
        Optional[int]: 可用字节数；psutil 不可用或查询失败返回 None / bytes, or None
    """
    try:
        # 可选依赖：必须延迟导入，且允许 ImportError（未安装 / 被打包裁剪）
        # Optional dependency: import lazily and tolerate ImportError.
        import psutil
    except Exception:
        return None
    try:
        value = int(psutil.virtual_memory().available)
    except Exception:
        return None
    return value if value > 0 else None


def available_memory_bytes() -> Optional[int]:
    """
    返回当前可用内存字节数，跨平台且绝不抛异常。
    Returns currently available memory in bytes; cross-platform and never raises.

    先用 stdlib 按平台探测；拿不到数字时（macOS 恒如此、未知平台、探测异常）再尝试
    psutil 兜底。两条路都失败就返回 None，由调用方按「最保守的已知边界」处理。
    stdlib is tried first per platform; when it yields no number (always on macOS, on
    unknown platforms, or on error) the psutil fallback is tried. If both fail this returns
    None and callers fall back to the most conservative known bound.

    返回 / Returns:
        Optional[int]: 可用字节数；未知/不支持/探测出错时返回 None
                       bytes, or None when unknown, unsupported or on error
    """
    value: Optional[int] = None
    try:
        if sys.platform.startswith("win"):
            value = _available_windows()
        elif sys.platform == "darwin":
            value = _available_macos()
        elif sys.platform.startswith("linux"):
            value = _available_linux()
    except Exception:
        # 探测只是优化：任何失败都退化成「未知」，再交给 psutil 兜底
        # Probing is advisory: any failure degrades to "unknown" and falls through to psutil.
        value = None
    if value is not None and value > 0:
        return value
    return _available_psutil()


class InflightByteBudget:
    """
    在途解码帧的字节额度器：按实测帧大小动态决定还能放几帧进来。
    Byte budget for in-flight decoded frames: decides from measured frame sizes how many
    more frames may enter.

    为什么需要它：解码线程数只能表达「同时解几张」，无法表达「一张有多大」。同样 3 条
    线程，24MP 目录只压住约 220MB，151MP 目录却要压住约 1.3GB。额度器把内存口径从
    「帧数」换成「字节数」，于是同一套并发配置在两种目录下都安全；混合像素目录也不需要
    任何特殊处理，大图自然少放、小图自然多放。

    用法（解码线程与主循环配合，tag 用 0 基序号）：
        budget = InflightByteBudget()
        reserved = budget.try_acquire(pos)     # 返回 0 表示额度不足，稍后重试
        item = decode_photo(pos + 1, name)
        budget.resize(pos, frame_bytes)        # 用实测字节校正预占
        ...
        budget.release(pos)                    # 本张处理完毕，归还额度

    参数 / Parameters:
        available_bytes (Optional[int]): 初始可用内存；None 表示首次准入时再探
        share (float): 允许占用的比例，落在 [0.05, 1.0]
        refresh_interval (int): 每多少次准入重探一次可用内存，>=1
    """

    def __init__(
        self,
        available_bytes: Optional[int] = None,
        share: float = DECODE_MEMORY_SHARE,
        refresh_interval: int = MEMORY_REFRESH_INTERVAL,
    ) -> None:
        """初始化额度器状态 / Initialise the budget state."""
        self._lock = threading.Lock()
        self._share = min(max(float(share), 0.05), 1.0)
        self._refresh_interval = max(1, int(refresh_interval))
        # holds: tag -> 该 tag 当前记账的字节数；inflight: 尚未释放的 tag 数量
        # holds: tag -> bytes charged to that tag; inflight: number of unreleased tags
        self._holds: Dict[int, int] = {}
        self._inflight = 0
        self._inflight_bytes = 0
        self._sizes: Deque[int] = deque(maxlen=FRAME_SIZE_WINDOW)
        self._available: Optional[int] = available_bytes
        # 构造时已给出可用内存 -> 它在本次就是「刚探到的」，先跑满一个刷新周期再重探；
        # 否则第一次准入就把入参覆盖掉，等于每 N 次里白探一次，也让入参失去意义。
        # A supplied available_bytes counts as a fresh probe: wait a full refresh interval
        # before re-probing, or the first admission overwrites the caller's value.
        if available_bytes is not None and available_bytes > 0:
            self._probe_countdown = self._refresh_interval
        else:
            self._probe_countdown = 0
        # 最近一次准入是否被内存收紧（供 PERF 日志解释「为什么这么小」）
        # Whether the most recent admission was memory-limited (explains small values in logs)
        self.mem_limited = False
        self.probe_count = 0

    # -- 估算与容量 ---------------------------------------------------------

    def estimate_next_bytes(self) -> int:
        """
        估计下一帧会占用多少字节（含解码期的瞬时缓冲）。
        Estimate the bytes the next frame will occupy (including decode-time scratch).

        实测显示 cv2.imdecode 解码期间的瞬时缓冲约等于一整个数组（单张 60MP 峰值 379MB
        = 数组 172MB + 缓冲 168MB），所以估计值取「窗口峰值 x 2」；已经解码完成、在队列里
        排队的帧只占数组那一份，由 resize() 按实测值记账。
        Measurement shows the decode-time scratch of cv2.imdecode is about one full array
        (a single 60MP frame peaks at ~379MB = 172MB array + 168MB scratch), so the estimate
        is "window peak x 2"; frames already decoded and queued hold only the array and are
        accounted by resize() from the measured size.

        返回 / Returns:
            int: 估计字节数（恒为正）/ estimated bytes, always positive
        """
        with self._lock:
            return self._estimate_locked()

    def _estimate_locked(self) -> int:
        """已持有锁时的估计值 / Estimate while the lock is held."""
        peak = max(self._sizes) if self._sizes else 0
        if peak <= 0:
            return FALLBACK_FRAME_BYTES * 2
        return int(peak) * 2

    def bytes_limit(self) -> Optional[int]:
        """
        当前允许在途的字节上限；可用内存未知时返回 None。
        Current byte ceiling for in-flight frames, or None when memory is unknown.

        返回 / Returns:
            Optional[int]: 上限字节数 / ceiling in bytes, or None
        """
        with self._lock:
            return self._limit_locked()

    def _limit_locked(self) -> Optional[int]:
        """已持有锁时的上限 / Ceiling while the lock is held."""
        if self._available is None or self._available <= 0:
            return None
        return int(self._available * self._share)

    # -- 探测 ----------------------------------------------------------------

    def refresh(self, force: bool = False) -> Optional[int]:
        """
        按刷新节奏重新探测可用内存（force=True 时立刻探测）。
        Re-probe available memory on the refresh cadence (immediately when forced).

        参数 / Parameters:
            force (bool): True 表示跳过计数立刻探测 / skip the counter and probe now

        返回 / Returns:
            Optional[int]: 探测到的可用字节；未知时为 None / bytes, or None
        """
        with self._lock:
            if not force and self._probe_countdown > 0:
                self._probe_countdown -= 1
                return self._available
            self._probe_countdown = self._refresh_interval
            self._available = available_memory_bytes()
            self.probe_count += 1
            return self._available

    def _maybe_refresh_locked(self) -> None:
        """按节奏重探（调用方已持锁，探测本身足够快）/ Re-probe on cadence; lock is held."""
        if self._probe_countdown > 0:
            self._probe_countdown -= 1
            return
        self._probe_countdown = self._refresh_interval
        self._available = available_memory_bytes()
        self.probe_count += 1

    # -- 准入与记账 ----------------------------------------------------------

    def can_admit(self, extra_bytes: Optional[int] = None) -> bool:
        """
        判断「再放进一帧」是否会超出额度。
        Whether one more frame can enter without exceeding the budget.

        空场（inflight == 0）永远放行：哪怕算术算出了负数，也必须让流水线动起来，否则
        整批照片会永久卡在准入判定上。可用内存未知时同样放行（此时只信硬上限）。
        An empty field (inflight == 0) is always admitted: even if the arithmetic came out
        negative, the pipeline must move, or the whole batch stalls on admission forever.
        Unknown memory is admitted too — only the hard caps apply then.

        参数 / Parameters:
            extra_bytes (Optional[int]): 新帧字节；None 表示用 estimate_next_bytes()

        返回 / Returns:
            bool: True 可以放行 / True when admissible
        """
        with self._lock:
            return self._can_admit_locked(extra_bytes)

    def _can_admit_locked(self, extra_bytes: Optional[int] = None) -> bool:
        """已持有锁时的准入判定 / Admission check while the lock is held."""
        if self._inflight <= 0:
            self.mem_limited = False
            return True
        self._maybe_refresh_locked()
        limit = self._limit_locked()
        if limit is None:
            # 内存未知：只信硬上限，不据此收紧（「未知」不是「零」）
            # Unknown memory: trust only the hard caps; unknown is not zero.
            self.mem_limited = False
            return True
        need = int(extra_bytes) if extra_bytes is not None else self._estimate_locked()
        if self._inflight_bytes + max(0, need) <= limit:
            self.mem_limited = False
            return True
        self.mem_limited = True
        return False

    def try_acquire(self, tag: int, nbytes: Optional[int] = None) -> int:
        """
        为某个 tag 预占额度；成功返回已占用字节，失败返回 0。
        Reserve budget for one tag; returns the reserved bytes, or 0 on refusal.

        参数 / Parameters:
            tag (int): 唯一标识（解码侧用 0 基序号）
            nbytes (Optional[int]): 预占字节；None 表示用 estimate_next_bytes()

        返回 / Returns:
            int: 占用字节数；0 表示额度不足，调用方应稍后重试
        """
        with self._lock:
            existing = self._holds.get(tag)
            if existing is not None:
                return existing
            need = int(nbytes) if nbytes is not None else self._estimate_locked()
            need = max(0, need)
            if not self._can_admit_locked(need):
                return 0
            self._holds[tag] = need
            self._inflight += 1
            self._inflight_bytes += need
            return need

    def resize(self, tag: int, nbytes: int) -> int:
        """
        用实测字节校正某个 tag 的占用（解码完成后立刻调用）。
        Correct a tag's charge with the measured size (called right after decoding).

        参数 / Parameters:
            tag (int): 唯一标识
            nbytes (int): 实测字节数（decoded_image.nbytes；解码失败传 0）

        返回 / Returns:
            int: 校正后的占用字节数；tag 不存在时返回 0
        """
        with self._lock:
            existing = self._holds.get(tag)
            if existing is None:
                return 0
            updated = max(0, int(nbytes))
            self._holds[tag] = updated
            self._inflight_bytes += updated - existing
            if self._inflight_bytes < 0:
                self._inflight_bytes = 0
            if updated > 0:
                # 记入滑动窗口，供后续帧的估计使用
                # Record it in the sliding window that estimates later frames.
                self._sizes.append(updated)
            return updated

    def release(self, tag: int) -> int:
        """
        释放某个 tag 的额度（幂等；tag 不存在时返回 0）。
        Release a tag's budget (idempotent; returns 0 when the tag is unknown).

        参数 / Parameters:
            tag (int): 唯一标识

        返回 / Returns:
            int: 实际释放的字节数
        """
        with self._lock:
            held = self._holds.pop(tag, None)
            if held is None:
                return 0
            if held > 0:
                self._inflight_bytes -= held
                if self._inflight_bytes < 0:
                    self._inflight_bytes = 0
            if self._inflight > 0:
                self._inflight -= 1
            return max(0, held)

    # -- 观测 ----------------------------------------------------------------

    def snapshot(self) -> Dict[str, object]:
        """
        返回一份可打印的状态快照，供 PERF 日志解释「为什么这么小」。
        Return a printable state snapshot so the PERF log can explain small values.

        返回 / Returns:
            Dict[str, object]: 含上限/占用/在途帧数/帧峰值/估计值/是否被内存收紧
        """
        mib = 1024 * 1024
        with self._lock:
            limit = self._limit_locked()
            peak = max(self._sizes) if self._sizes else 0
            return {
                'avail_mb': None if self._available is None else int(self._available // mib),
                'limit_mb': None if limit is None else int(limit // mib),
                'inflight_mb': int(self._inflight_bytes // mib),
                'inflight': int(self._inflight),
                'frame_peak_mb': int(peak // mib),
                'estimate_mb': int(self._estimate_locked() // mib),
                'mem_limited': bool(self.mem_limited),
                'samples': len(self._sizes),
            }


def clamp_prefetch_workers(
    requested: int,
    per_frame_bytes: int = FALLBACK_FRAME_BYTES,
    available_bytes: Optional[int] = None,
    max_workers: int = MAX_DECODE_WORKERS,
    share: float = DECODE_MEMORY_SHARE,
) -> Tuple[int, bool]:
    """
    把「想要的解码线程数」收进内存能承受的区间。
    Clamp the wanted decoder-thread count into what memory can sustain.

    线程数只是解码并行的上限；真正决定能同时解几张的是 InflightByteBudget 的字节额度。
    这里仍按内存给一个上限，是为了在小内存机器上连线程栈与调度都不必开满，同时保证任何
    情况下都不低于 MIN_DECODE_WORKERS（用户要求：最少 2 条解码线程）。

    The thread count is only the ceiling of decode parallelism; how many frames really run
    concurrently is decided by InflightByteBudget. The memory-derived ceiling still matters
    on small machines (no point spawning threads that all block), and it never drops below
    MIN_DECODE_WORKERS (per the user: at least two decoders).

    解码期间的瞬时缓冲约为一个完整数组，因此在途一帧按 per_frame_bytes x 2 记账；这与
    InflightByteBudget._estimate_locked() 的口径一致，两处不能各写一套。

    Decode-time scratch is about one full array, so one in-flight frame is charged at
    per_frame_bytes x 2 — the same convention as InflightByteBudget._estimate_locked(); the
    two must not drift apart.

    参数 / Parameters:
        requested (int): 请求的线程数 / requested thread count
        per_frame_bytes (int): 单帧（已解码数组）字节数 / decoded array bytes per frame
        available_bytes (Optional[int]): 可用内存；None 表示现探 / available memory, None probes
        max_workers (int): 硬上限 / hard cap
        share (float): 允许占用的比例 / share of available memory

    返回 / Returns:
        Tuple[int, bool]: (允许的线程数, 是否被内存收紧) / (workers, memory_limited)
    """
    try:
        workers = int(requested)
    except (TypeError, ValueError):
        workers = MIN_DECODE_WORKERS
    workers = max(MIN_DECODE_WORKERS, min(workers, int(max_workers)))

    if available_bytes is None:
        available_bytes = available_memory_bytes()
    if available_bytes is None or available_bytes <= 0:
        # 内存未知：只信硬上限，绝不因为「未知」把线程数砍到 1
        # Unknown memory: trust only the hard caps; never cut to one over an unknown.
        return workers, False

    effective_share = min(max(float(share), 0.05), 1.0)
    budget = int(available_bytes * effective_share)
    per_frame = max(1, int(per_frame_bytes)) * 2
    max_by_memory = max(MIN_DECODE_WORKERS, budget // per_frame)
    if workers <= max_by_memory:
        return workers, False
    return int(max_by_memory), True


def clamp_queue_depth(
    requested: int,
    max_depth: int = MAX_YOLO_PREFETCH_DEPTH,
    available_bytes: Optional[int] = None,
    per_frame_bytes: int = FALLBACK_FRAME_BYTES,
    share: float = DECODE_MEMORY_SHARE,
) -> Tuple[int, bool]:
    """
    把「预取队列深度」收进内存能承受的区间。
    Clamp the prefetch-queue depth into what memory can sustain.

    队列里的帧已经解码完成，只持有数组本身、不再带解码瞬时缓冲，所以这里按
    per_frame_bytes 单份记账（与线程那处 x2 的口径不同是有意的）。

    Frames sitting in the queue are already decoded: they hold only the array and no scratch,
    so one queue slot is charged at a single per_frame_bytes (intentionally unlike the x2 used
    for decoding threads).

    参数 / Parameters:
        requested (int): 请求的深度 / requested depth
        max_depth (int): 硬上限 / hard cap
        available_bytes (Optional[int]): 可用内存；None 表示现探 / available memory, None probes
        per_frame_bytes (int): 单帧（已解码数组）字节数 / decoded array bytes per frame
        share (float): 允许占用的比例 / share of available memory

    返回 / Returns:
        Tuple[int, bool]: (允许的深度, 是否被内存收紧) / (depth, memory_limited)
    """
    try:
        depth = int(requested)
    except (TypeError, ValueError):
        depth = 1
    depth = max(1, min(depth, int(max_depth)))

    if available_bytes is None:
        available_bytes = available_memory_bytes()
    if available_bytes is None or available_bytes <= 0:
        return depth, False

    effective_share = min(max(float(share), 0.05), 1.0)
    budget = int(available_bytes * effective_share)
    per_frame = max(1, int(per_frame_bytes))
    max_by_memory = max(1, budget // per_frame)
    if depth <= max_by_memory:
        return depth, False
    return int(max_by_memory), True


def clamp_count(requested: int, minimum: int, maximum: int) -> int:
    """
    把任意计数收进 [minimum, maximum]，并容忍非法输入。
    Clamp an arbitrary counter into [minimum, maximum], tolerating invalid input.

    参数 / Parameters:
        requested (int): 请求值 / requested value
        minimum (int): 下限 / lower bound
        maximum (int): 上限 / upper bound

    返回 / Returns:
        int: 落在区间内的整数 / the clamped integer
    """
    try:
        value = int(requested)
    except (TypeError, ValueError):
        value = minimum
    low = int(minimum)
    high = int(maximum)
    if high < low:
        high = low
    return max(low, min(value, high))
