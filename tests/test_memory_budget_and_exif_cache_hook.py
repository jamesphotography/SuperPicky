# -*- coding: utf-8 -*-
"""
内存预算钳制 + EXIF 读取缓存失效链路的回归测试。
Regression tests for the memory-budget clamps and the EXIF cache-invalidation chain.

背景 / Background:
    第 4 步引入两组必须长期成立的约束：

    1. core/memory_budget.py 把「预取深度 / 解码线程数 / EXIF 批次 / EXIF 队列批数」
       从「环境变量可以无限放大」改成「硬上限 + 可用内存收紧」。此前
       SUPERPICKY_DECODE_PREFETCH_DEPTH=32 会在途 33 张 172MB 预览（≈5.7GB）直接 OOM。
    2. 写元数据后必须让 FocusPointDetector 的 47 标签合并读缓存（1024 条 LRU）失效，
       否则同一进程内后续读取仍返回写之前的快照。失效由
       core.focus_point_detector.invalidate_exif_cache 统一实现，
       ExifToolManager 通过 set_metadata_cache_invalidator 注入的无依赖钩子回调它
       （管理器不能反向 import core，会循环导入）。

    Step 4 turned two unbounded knobs into bounded ones, and made metadata writes
    invalidate the merged-EXIF read cache through an injected hook (the manager
    cannot import core without a cycle).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import focus_point_detector as fpd
from core import memory_budget as mb
from core.memory_budget import (
    DECODE_MEMORY_SHARE,
    FRAME_BYTES_45MP,
    MAX_DECODE_WORKERS,
    MAX_EXIF_BATCH_SIZE,
    MAX_EXIF_QUEUE_BATCHES,
    MAX_YOLO_PREFETCH_DEPTH,
    available_memory_bytes,
    clamp_count,
    clamp_prefetch_workers,
    clamp_queue_depth,
)
from tools.exiftool_manager import ExifToolManager

GIB = 1024 ** 3


# --------------------------------------------------------------------------
# 1. 内存探测 / memory probing
# --------------------------------------------------------------------------

def test_available_memory_never_raises_and_is_sane():
    """探测绝不抛异常；拿到值时必须是正数 / never raises, positive when known."""
    value = available_memory_bytes()
    assert value is None or (isinstance(value, int) and value > 0)


def test_unknown_memory_falls_back_to_hard_cap_only():
    """内存未知时只信硬上限，不据此收紧（避免把未知当无限，也避免当零）。"""
    workers, limited = clamp_prefetch_workers(999, available_bytes=None)
    assert workers == MAX_DECODE_WORKERS
    assert limited is False

    depth, depth_limited = clamp_queue_depth(999, available_bytes=None)
    assert depth == MAX_YOLO_PREFETCH_DEPTH
    assert depth_limited is False


def test_zero_or_negative_memory_is_treated_as_unknown():
    """0 / 负数可用内存按未知处理，仍然只受硬上限约束。"""
    assert clamp_prefetch_workers(4, available_bytes=0) == (4, False)
    assert clamp_queue_depth(4, available_bytes=-1) == (4, False)


def test_macos_branch_degrades_to_none_when_sysconf_lacks_the_name(monkeypatch):
    """
    macOS 走不通时必须安静降级为 None，绝不能抛异常。
    The macOS path must degrade silently to None, never raise.

    Darwin 没有 _SC_AVPHYS_PAGES（Apple libc 与 xnu 都只定义总量语义的
    _SC_PHYS_PAGES），CPython 的 sysconf 名字表也被 #ifdef 包着，因此真实 macOS 上
    os.sysconf("SC_AVPHYS_PAGES") 抛 ValueError: unrecognized configuration name；
    部分平台干脆没有 os.sysconf（AttributeError）。三种失败都必须回到 None。
    """
    def raise_value_error(name: str) -> int:
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(os, "sysconf", raise_value_error, raising=False)
    assert mb._available_macos() is None

    def raise_os_error(name: str) -> int:
        raise OSError("sysconf unavailable")

    monkeypatch.setattr(os, "sysconf", raise_os_error, raising=False)
    assert mb._available_macos() is None

    monkeypatch.delattr(os, "sysconf", raising=False)
    assert mb._available_macos() is None


def test_macos_branch_computes_bytes_when_the_name_exists(monkeypatch):
    """
    若未来 Darwin 补齐该常量，公式必须立刻正确生效，无需改代码。
    When a future Darwin adds the constant, the formula must take effect unchanged.
    """
    def fake_sysconf(name: str) -> int:
        return 1000 if name == "SC_AVPHYS_PAGES" else 4096

    monkeypatch.setattr(os, "sysconf", fake_sysconf, raising=False)
    assert mb._available_macos() == 1000 * 4096

    def zero_sysconf(name: str) -> int:
        return 0

    monkeypatch.setattr(os, "sysconf", zero_sysconf, raising=False)
    assert mb._available_macos() is None


# --------------------------------------------------------------------------
# 2. 预取线程数 / prefetch workers
# --------------------------------------------------------------------------

def test_workers_are_capped_even_with_plenty_of_memory():
    """硬上限优先：内存再充裕，环境变量的 32 也不能真的开出 32 条解码线程。"""
    workers, limited = clamp_prefetch_workers(32, available_bytes=64 * GIB)
    assert workers == MAX_DECODE_WORKERS
    assert limited is False, "被硬上限钳住不算被内存收紧"


def test_workers_shrink_under_memory_pressure():
    """可用内存 1GB、25% 预算、每帧 172MB：在途 = workers + 1，只能开 1 条线程。"""
    workers, limited = clamp_prefetch_workers(8, available_bytes=1 * GIB)
    assert workers == 1
    assert limited is True


def test_workers_defaults_survive_a_normal_machine():
    """本机这类 8GB+ 可用内存的机器上，默认 3 线程不应被内存收紧（保住既有性能）。"""
    workers, limited = clamp_prefetch_workers(3, available_bytes=8 * GIB)
    assert workers == 3
    assert limited is False


def test_worker_memory_math_uses_in_flight_frames():
    """预算刚好够 N 张在途时应给 N-1 条线程（在途 = 线程数 + 1）。"""
    budget = int(2 * FRAME_BYTES_45MP / DECODE_MEMORY_SHARE)  # 25% 预算 = 2 帧
    workers, limited = clamp_prefetch_workers(8, available_bytes=budget)
    assert workers == 1
    assert limited is True


# --------------------------------------------------------------------------
# 3. 预取队列深度 / prefetch queue depth
# --------------------------------------------------------------------------

def test_queue_depth_capped_by_hard_limit():
    """SUPERPICKY_YOLO_PREFETCH_DEPTH=32 不能真的压 32 张 172MB 大图。"""
    depth, limited = clamp_queue_depth(32, available_bytes=64 * GIB)
    assert depth == MAX_YOLO_PREFETCH_DEPTH
    assert limited is False


def test_queue_depth_shrinks_under_memory_pressure():
    """队列不再减 1（后端没有额外在途槽位），1GB 可用 → 25% 预算只够 1 个槽。"""
    depth, limited = clamp_queue_depth(8, available_bytes=1 * GIB)
    assert depth == 1
    assert limited is True


def test_queue_depth_never_below_one():
    """极端小内存也必须留 1 个槽，否则流水线无法推进。"""
    depth, limited = clamp_queue_depth(3, available_bytes=1024)
    assert depth == 1
    assert limited is True


# --------------------------------------------------------------------------
# 4. 计数钳制 / light counters
# --------------------------------------------------------------------------

@pytest.mark.parametrize("requested,expected", [
    (1, 8),          # 低于下限 → 抬到下限
    (64, 64),        # 区间内 → 原值
    (100000, 256),   # 高于上限 → 压到上限
])
def test_clamp_count_for_exif_batch(requested, expected):
    """EXIF 批次大小只能落在 8..256（此前设置了无上限）。"""
    assert clamp_count(requested, 8, MAX_EXIF_BATCH_SIZE) == expected


def test_clamp_count_for_exif_queue_batches():
    """EXIF 队列批数只能落在 2..32（此前设置了无上限）。"""
    assert clamp_count(1, 2, MAX_EXIF_QUEUE_BATCHES) == 2
    assert clamp_count(6, 2, MAX_EXIF_QUEUE_BATCHES) == 6
    assert clamp_count(500, 2, MAX_EXIF_QUEUE_BATCHES) == MAX_EXIF_QUEUE_BATCHES


# --------------------------------------------------------------------------
# 5. 模块级失效函数 / module-level invalidator
# --------------------------------------------------------------------------

class _RecordingDetector:
    """记录失效调用的检测器替身 / records every invalidate_cache call."""

    def __init__(self):
        self.calls = []

    def invalidate_cache(self, file_path=None):
        self.calls.append(file_path)


@pytest.fixture
def recording_detector(monkeypatch):
    """把模块级单例替换成记录替身 / swap the module-level singleton."""
    detector = _RecordingDetector()
    monkeypatch.setattr(fpd, "_focus_detector", detector)
    return detector


def test_invalidate_single_path(recording_detector):
    """传单个字符串 → 只失效这一条。"""
    fpd.invalidate_exif_cache("C:/photos/a.arw")
    assert recording_detector.calls == ["C:/photos/a.arw"]


def test_invalidate_many_paths(recording_detector):
    """传可迭代对象 → 逐个失效。"""
    fpd.invalidate_exif_cache(["a.arw", "b.arw"])
    assert recording_detector.calls == ["a.arw", "b.arw"]


def test_invalidate_none_clears_whole_cache(recording_detector):
    """传 None → 清空整张表（invalidate_cache(None) 的既有语义）。"""
    fpd.invalidate_exif_cache(None)
    assert recording_detector.calls == [None]


def test_invalidate_skips_non_string_entries(recording_detector):
    """
    混合列表里的 None / 数字会被跳过：绝不能因为一个 None 就退化成「清空全表」。
    Non-string entries are skipped so a stray None can never clear the whole cache.
    """
    fpd.invalidate_exif_cache(["a.arw", None, 42])
    assert recording_detector.calls == ["a.arw"]


def test_invalidate_without_singleton_does_not_construct(monkeypatch):
    """单例尚未创建时直接返回，不为失效动作去构造检测器（构造会探测 ExifTool）。"""
    monkeypatch.setattr(fpd, "_focus_detector", None)
    fpd.invalidate_exif_cache(["a.arw"])
    assert fpd._focus_detector is None


def test_invalidate_swallows_detector_errors(monkeypatch):
    """检测器失效内部抛异常必须被吞掉：缓存只是加速手段，不能中断选片。"""

    class _Broken:
        def invalidate_cache(self, file_path=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(fpd, "_focus_detector", _Broken())
    fpd.invalidate_exif_cache(["a.arw"])  # 不抛异常即通过


# --------------------------------------------------------------------------
# 6. ExifToolManager 钩子 / the manager-side hook
# --------------------------------------------------------------------------

def _fresh_manager() -> ExifToolManager:
    """绕过 __init__ 的替身（不启动真实 exiftool）。

    A double that skips __init__ so no exiftool process is started.
    """
    return object.__new__(ExifToolManager)


def test_hook_can_be_registered_and_called():
    """注册后写成功即回调，且过滤掉非字符串 / 空值。"""
    manager = _fresh_manager()
    seen = []
    manager.set_metadata_cache_invalidator(seen.append)
    manager._notify_metadata_cache_invalidator(["a.arw", None, "", 7])
    assert seen == [["a.arw"]]


def test_hook_is_optional():
    """未注册回调时通知是空操作，绝不抛异常。"""
    manager = _fresh_manager()
    manager._notify_metadata_cache_invalidator(["a.arw"])
    manager._notify_metadata_cache_invalidator([])
    assert getattr(manager, "_metadata_cache_invalidator", None) is None


def test_hook_can_be_unregistered():
    """传 None 注销后不再回调 / None unregisters the callback."""
    manager = _fresh_manager()
    seen = []
    manager.set_metadata_cache_invalidator(seen.append)
    manager.set_metadata_cache_invalidator(None)
    manager._notify_metadata_cache_invalidator(["a.arw"])
    assert seen == []


def test_hook_swallows_callback_errors():
    """回调自身抛异常必须被吞掉：绝不能因此把一次成功的写入报成失败。"""
    manager = _fresh_manager()

    def boom(paths):
        raise RuntimeError("boom")

    manager.set_metadata_cache_invalidator(boom)
    manager._notify_metadata_cache_invalidator(["a.arw"])  # 不抛异常即通过


def test_hook_works_on_instances_without_init_attributes():
    """
    历史测试用 object.__new__ 绕过 __init__，实例上没有钩子字段与锁；
    注册与通知都必须走 getattr 兜底，不能 AttributeError。
    """
    manager = _fresh_manager()
    assert not hasattr(manager, "_metadata_cache_invalidator_lock")
    seen = []
    manager.set_metadata_cache_invalidator(seen.append)
    manager._notify_metadata_cache_invalidator(["a.arw"])
    assert seen == [["a.arw"]]


class TestSetMetadataInvalidatesCache:
    """④ 的核心诉求：birdid 写 Title / Caption 之后，读取缓存必须失效。"""

    @staticmethod
    def _manager(write_ok: bool = True):
        """造替身：直接写本体路径（JPEG），_send_to_process 返回给定结果。"""
        manager = object.__new__(ExifToolManager)
        manager._get_metadata_write_mode = lambda: "embedded"
        manager._send_to_process = lambda args, timeout=30.0: write_ok
        return manager

    def test_title_write_notifies_invalidator(self, tmp_path):
        """set_metadata 写成功 → 回调收到该文件（birdid_server 的 /exif/write-title 路径）。"""
        path = tmp_path / "bird.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        manager = self._manager(write_ok=True)
        seen = []
        manager.set_metadata_cache_invalidator(seen.append)

        assert manager.set_metadata(str(path), {"Title": "黑尾鸥"}) is True
        assert seen == [[str(path)]]

    def test_failed_write_does_not_notify(self, tmp_path):
        """写入失败（返回 False）不得触发失效——避免掩盖真实失败原因。"""
        path = tmp_path / "bird.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        manager = self._manager(write_ok=False)
        seen = []
        manager.set_metadata_cache_invalidator(seen.append)

        assert manager.set_metadata(str(path), {"Title": "黑尾鸥"}) is False
        assert seen == []

    def test_write_mode_none_does_not_notify(self, tmp_path):
        """metadata_write_mode=none 时根本没有写盘，不应触发失效。"""
        path = tmp_path / "bird.jpg"
        path.write_bytes(b"\xff\xd8\xff\xd9")
        manager = self._manager(write_ok=True)
        manager._get_metadata_write_mode = lambda: "none"
        seen = []
        manager.set_metadata_cache_invalidator(seen.append)

        assert manager.set_metadata(str(path), {"Title": "黑尾鸥"}) is True
        assert seen == []
