# -*- coding: utf-8 -*-
"""
内存预算探测与预取/批次规模钳制（无第三方依赖）。
Memory-budget probing and prefetch/batch clamps (stdlib only).

本模块只回答两个问题：
This module answers only two questions:

1. 当前进程还能拿到多少可用内存（跨平台，拿不到时返回 None）；
   How much memory is currently available to this process (None when unknown).
2. 在给定「每帧峰值字节数」和「可用内存」下，预取线程数 / 队列深度 / 批次大小
   最多能开多少。
   Given a per-frame peak size and available memory, how many prefetch workers /
   queue slots / batch entries may be opened.

为什么不用 psutil（尽管它实际会被装上）：本项目的直接依赖清单（requirements*.txt /
*.spec / pyproject.toml）里没有 psutil，它只是 ultralytics 的传递依赖 —— 我们既没有声明它，
也没有为它锁版本，上游换版就可能让它消失。而这里的探测是防 OOM 的安全网，必须始终可用，
因此只走 stdlib（Windows: kernel32!GlobalMemoryStatusEx；Linux: /proc/meminfo；
macOS: sysconf），不依赖任何第三方包，也不需要打包 hook。

macOS 现状（已对 Darwin 头文件核实）：Apple libc 与 xnu 都没有定义 _SC_AVPHYS_PAGES，
CPython 的 sysconf 名字表也用 #ifdef 包着它，所以本模块在 macOS 上恒返回 None ——
即「按可用内存自适应收紧」在 macOS 不生效，只剩硬上限；而默认 3 解码线程本就远低于
硬上限，因此 macOS 用户可见行为完全不变。
Current macOS status (verified against the Darwin headers): neither Apple libc nor xnu defines
_SC_AVPHYS_PAGES, and CPython's sysconf name table is #ifdef-guarded on it, so this module
always returns None on macOS. The adaptive tightening is therefore inert there and only the
hard caps apply; the default of 3 decode workers sits far below them, so user-visible
behaviour on macOS is unchanged.
Why not psutil (even though it does get installed): psutil is absent from this project's
direct dependency lists (requirements*.txt / *.spec / pyproject.toml). It only arrives
transitively through ultralytics, which we neither declare it for nor version-pin against,
so an upstream bump could remove it. This probe is the OOM safety net and must always work,
so it uses only the standard library (Windows: kernel32!GlobalMemoryStatusEx;
Linux: /proc/meminfo; macOS: sysconf) — no third-party package, no packaging hook.

保守原则（用户要求防 OOM）：
Conservative by design (the user asked for OOM protection):

- 探测失败一律当作「内存未知」，按最保守的一档钳制，而不是放开；
  An unknown memory figure is treated as the most conservative case, never as unlimited.
- 解码并行只允许用掉可用内存的一小部分（默认 25%），其余留给模型、TOPIQ 与系统；
  Decoding may use only a small share of the available memory (25% by default); the rest
  is left to the models, TOPIQ and the OS.
- 环境变量只能在本模块给出的硬上限内调整，不能无限放大（此前 32 线程 ≈ 5.7GB 会 OOM）。
  Environment variables can only move within the hard caps below; previously 32 workers
  implied ~5.7GB of in-flight frames and OOMed.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

# 一张 45MP 预览在解码/推理期间的峰值占用（RGB BGR 三通道 + 中间副本）。
# Peak footprint of one 45MP preview during decode/inference (BGR + intermediates).
FRAME_BYTES_45MP: int = 172 * 1024 * 1024

# 解码并行允许占用的可用内存比例（其余留给 YOLO/关键点/TOPIQ 与系统）。
# Share of available memory decoding may use (rest goes to models/TOPIQ/OS).
DECODE_MEMORY_SHARE: float = 0.25

# 硬上限：任何环境变量都不能突破，防止把线程数当作无成本旋钮。
# Hard caps no environment variable may exceed.
MAX_DECODE_WORKERS: int = 8
MAX_YOLO_PREFETCH_DEPTH: int = 8
MAX_EXIF_BATCH_SIZE: int = 256
MAX_EXIF_QUEUE_BATCHES: int = 32


def _available_windows() -> Optional[int]:
    """
    通过 GlobalMemoryStatusEx 读取 Windows 可用内存（字节）。
    Reads available memory on Windows via GlobalMemoryStatusEx (bytes).

    取「可用物理内存」与「可提交额度余量」的较小值：提交上限（页面文件）比物理内存
    更紧时，后者才是真正会失败的那个数。
    Uses the smaller of available physical memory and remaining commit allowance: when the
    commit limit is tighter than RAM, that is the figure that actually fails.

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
    commit_headroom = int(status.ullTotalPageFile) - int(status.ullAvailPageFile)
    if commit_headroom <= 0:
        return avail_phys
    return min(avail_phys, commit_headroom)


def _available_linux() -> Optional[int]:
    """
    从 /proc/meminfo 读取 MemAvailable（字节，缺失时退到 MemFree）。
    Reads MemAvailable from /proc/meminfo (falls back to MemFree).

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
    估算 macOS 可用内存（字节）；当前 Darwin 上恒返回 None。
    Estimate available memory on macOS (bytes); always None on current Darwin.

    Darwin 不提供「可用内存」的 sysconf 项：Apple libc 的 include/unistd.h 与 xnu 的
    bsd/sys/unistd.h 都只定义总量语义的 _SC_PHYS_PAGES，没有 _SC_AVPHYS_PAGES；
    CPython 的 Modules/posixmodule.c 里对应名字表同样被 #ifdef _SC_AVPHYS_PAGES 包着，
    名字查不到就抛 ValueError: unrecognized configuration name。这里保留调用只是为了
    将来某个 Darwin 版本补齐该常量时能自动生效，抛错时安静降级为「未知」。

    返回 None 不是故障：clamp_* 系列在内存未知时只按硬上限钳制，而默认 3 个解码线程
    本就远低于硬上限，因此 macOS 上的实际行为与改动前一致。

    Darwin exposes no "available memory" sysconf entry: Apple libc's include/unistd.h and
    xnu's bsd/sys/unistd.h define only the total-oriented _SC_PHYS_PAGES, never
    _SC_AVPHYS_PAGES; CPython's Modules/posixmodule.c guards its matching name-table entry
    with #ifdef _SC_AVPHYS_PAGES, and an unknown name makes os.sysconf raise
    ValueError: unrecognized configuration name. The call is kept only so a future Darwin
    release that adds the constant takes effect automatically; on failure this quietly
    degrades to "unknown".

    Returning None is not a failure: the clamp_* helpers fall back to the hard caps when
    memory is unknown, and the default 3 decode workers sit far below those caps, so actual
    macOS behaviour matches the pre-change behaviour.

    返回 / Returns:
        Optional[int]: 当前恒为 None（Darwin 缺该 sysconf 项）/ always None on Darwin
    """
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        # Darwin 未定义 _SC_AVPHYS_PAGES（os.sysconf 抛 ValueError）；某些平台连
        # os.sysconf 都没有（AttributeError）。两者都视为「内存未知」，由调用方退到
        # 最保守的硬上限。
        # Darwin lacks _SC_AVPHYS_PAGES (ValueError); some platforms have no os.sysconf at
        # all (AttributeError). Both mean "memory unknown" and callers fall back to the
        # hard caps.
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return int(pages) * int(page_size)


def available_memory_bytes() -> Optional[int]:
    """
    返回当前可用内存字节数，跨平台且绝不抛异常。
    Returns currently available memory in bytes; cross-platform and never raises.

    返回 / Returns:
        Optional[int]: 可用字节数；未知/不支持/探测出错时返回 None
                       bytes, or None when unknown, unsupported or on error
    """
    try:
        if sys.platform.startswith("win"):
            return _available_windows()
        if sys.platform == "darwin":
            return _available_macos()
        if sys.platform.startswith("linux"):
            return _available_linux()
    except Exception:
        # 探测只是优化：任何失败都退化成「未知」，由调用方按最保守值处理
        # Probing is advisory: any failure degrades to "unknown" (most conservative).
        return None
    return None


def clamp_prefetch_workers(
    requested: int,
    per_frame_bytes: int = FRAME_BYTES_45MP,
    available_bytes: Optional[int] = None,
    max_workers: int = MAX_DECODE_WORKERS,
    share: float = DECODE_MEMORY_SHARE,
) -> Tuple[int, bool]:
    """
    按 CPU 上限与可用内存收紧并行解码线程数（内存未知时按最保守的一档处理）。
    Clamps the parallel decode worker count by the hard cap and available memory
    (an unknown memory figure is handled as the most conservative case).

    在途帧数 = 线程数 + 1（解码结果队列保留 1 个槽位），因此内存换算时按
    `workers + 1` 张计算峰值，而不是 `workers` 张。
    In-flight frames are workers + 1 (one queued slot behind the inference thread), so the
    peak is computed from workers + 1 frames rather than workers frames.

    参数 / Parameters:
        requested (int): 期望线程数（来自默认值或环境变量）/ desired workers
        per_frame_bytes (int): 每帧峰值字节 / peak bytes per frame
        available_bytes (Optional[int]): 可用内存；None 表示未知 / available memory or None
        max_workers (int): 硬上限 / hard cap
        share (float): 允许占用的比例（0, 1] / allowed share of available memory

    返回 / Returns:
        Tuple[int, bool]: (最终线程数, 是否被内存收紧 / (workers, memory_limited))
    """
    hard_cap = max(1, int(max_workers))
    workers = max(1, min(int(requested or 1), hard_cap))
    if not per_frame_bytes or per_frame_bytes <= 0:
        return workers, False
    if available_bytes is None or available_bytes <= 0:
        # 内存未知：只信硬上限（最保守的已知边界）
        # Unknown memory: trust only the hard cap, the most conservative known bound.
        return workers, False
    effective_share = min(max(float(share), 0.05), 1.0)
    budget = int(available_bytes * effective_share)
    max_by_memory = max(1, budget // int(per_frame_bytes) - 1)
    if workers <= max_by_memory:
        return workers, False
    return max_by_memory, True


def clamp_queue_depth(
    requested: int,
    max_depth: int = MAX_YOLO_PREFETCH_DEPTH,
    available_bytes: Optional[int] = None,
    per_frame_bytes: int = FRAME_BYTES_45MP,
    share: float = DECODE_MEMORY_SHARE,
) -> Tuple[int, bool]:
    """
    按硬上限与可用内存收紧预取队列深度。
    Clamps the prefetch queue depth by the hard cap and available memory.

    参数 / Parameters:
        requested (int): 期望深度 / desired depth
        max_depth (int): 硬上限 / hard cap
        available_bytes (Optional[int]): 可用内存；None 表示未知 / available memory or None
        per_frame_bytes (int): 每帧峰值字节 / peak bytes per frame
        share (float): 允许占用的比例 / allowed share of available memory

    返回 / Returns:
        Tuple[int, bool]: (最终深度, 是否被内存收紧 / (depth, memory_limited))
    """
    hard_cap = max(1, int(max_depth))
    depth = max(1, min(int(requested or 1), hard_cap))
    if not per_frame_bytes or per_frame_bytes <= 0:
        return depth, False
    if available_bytes is None or available_bytes <= 0:
        return depth, False
    effective_share = min(max(float(share), 0.05), 1.0)
    budget = int(available_bytes * effective_share)
    max_by_memory = max(1, budget // int(per_frame_bytes))
    if depth <= max_by_memory:
        return depth, False
    return max_by_memory, True


def clamp_count(requested: int, minimum: int, maximum: int) -> int:
    """
    通用的整数钳制（批次大小、队列批数等轻量计数用它即可）。
    Generic integer clamp for light counters such as batch sizes and queued batches.

    参数 / Parameters:
        requested (int): 期望值 / desired value
        minimum (int): 下限 / lower bound
        maximum (int): 上限 / upper bound

    返回 / Returns:
        int: 落在 [minimum, maximum] 内的值 / value within [minimum, maximum]
    """
    low = max(1, int(minimum))
    high = max(low, int(maximum))
    return max(low, min(int(requested or low), high))
