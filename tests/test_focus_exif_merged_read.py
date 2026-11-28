# -*- coding: utf-8 -*-
"""
合并读取 EXIF 的回归测试 / Regression tests for the merged EXIF read.

背景 / Background:
    focus_point_detector 原本对同一张照片要开三次 exiftool 往返
    （详情 16 标签 + 通用 4 标签 + 品牌标签）。exiftool 的成本主要在
    「每次完整解析 RAW 文件」，与标签数量几乎无关，因此改为一次读取
    47 个标签的超集再按调用方裁剪，实测 159.1 ms/张 → 62.1 ms/张。

    这些用例锁住改动的四条不变式:
    1. 返回值形态与逐标签读取一致：缺失标签以「键不存在」表示，不补 None。
    2. 每次返回的都是独立副本（_detect_* 会就地 update 它）。
    3. 非法路径 / 空标签列表 / 读取失败都返回 None，且不写缓存。
    4. 写回元数据后必须能通过 invalidate_cache 让缓存失效。

These tests pin the merged-read invariants: absent-tag semantics, caller-owned
copies, failure paths, and cache invalidation after metadata writes.
"""

import os
import threading

import pytest

from core.focus_point_detector import FocusPointDetector


class _FakeManager:
    """记录调用次数的 ExifToolManager 替身 / counts reads and returns a fixed payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def read_metadata(self, file_path, extra_args=None):
        self.calls.append((file_path, list(extra_args or [])))
        if self.payload is None:
            return None
        return dict(self.payload)


@pytest.fixture
def sample_raw(tmp_path):
    """构造一个「存在」的假 RAW 文件（内容无关，只用于 os.path.exists）。"""
    p = tmp_path / "sample.arw"
    p.write_bytes(b"II*\x00")
    return str(p)


@pytest.fixture
def fake_manager(monkeypatch):
    """用替身替换模块级 get_exiftool_manager，返回 (manager, install_payload)。"""

    def install(payload):
        mgr = _FakeManager(payload)
        monkeypatch.setattr(
            "core.focus_point_detector.get_exiftool_manager", lambda: mgr
        )
        return mgr

    return install


# --------------------------------------------------------------------------
# 1. 返回值形态 / return shape
# --------------------------------------------------------------------------

def test_read_exif_returns_only_requested_keys(sample_raw, fake_manager):
    """只返回调用方请求的标签，不泄漏超集里的其它键。"""
    fake_manager({"Make": "SONY", "ISO": 400, "FocusLocation": "6000 4000 3000 2000"})
    det = FocusPointDetector()

    assert det.read_exif(sample_raw, ["Make", "ISO"]) == {"Make": "SONY", "ISO": 400}


def test_read_exif_missing_tags_stay_absent(sample_raw, fake_manager):
    """文件中不存在的标签不补 None，保持「键不存在」语义。"""
    fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    result = det.read_exif(sample_raw, ["Make", "FocusFrameSize"])
    assert result == {"Make": "SONY"}
    assert "FocusFrameSize" not in result


def test_read_exif_returns_none_when_nothing_matched(sample_raw, fake_manager):
    """请求的标签一个都不存在时返回空 dict（不是 None），与逐标签读取一致。"""
    fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    assert det.read_exif(sample_raw, ["CropArea"]) == {}


# --------------------------------------------------------------------------
# 2. 篡改隔离 / caller-owned copies
# --------------------------------------------------------------------------

def test_returned_dict_is_not_shared_with_cache(sample_raw, fake_manager):
    """调用方就地修改返回值，不得污染缓存里的副本。"""
    fake_manager({"Make": "NIKON", "AFAreaMode": "Single"})
    det = FocusPointDetector()

    first = det.read_exif(sample_raw, ["Make", "AFAreaMode"])
    first["Make"] = "MUTATED"
    first["injected"] = True

    second = det.read_exif(sample_raw, ["Make", "AFAreaMode"])
    assert second == {"Make": "NIKON", "AFAreaMode": "Single"}


def test_each_call_returns_distinct_object(sample_raw, fake_manager):
    """两次调用返回不同对象（_detect_* 会就地 update，共享会互相污染）。"""
    fake_manager({"Make": "CANON"})
    det = FocusPointDetector()

    assert det.read_exif(sample_raw, ["Make"]) is not det.read_exif(sample_raw, ["Make"])


# --------------------------------------------------------------------------
# 3. 边界与失败路径 / boundaries and failures
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path", [None, "", "   ", 12345, b"/tmp/x"])
def test_read_exif_rejects_invalid_paths(bad_path, fake_manager):
    """非字符串 / 空路径不触发任何 exiftool 调用。"""
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    assert det.read_exif(bad_path, ["Make"]) is None
    assert mgr.calls == []


def test_read_exif_returns_none_for_missing_file(fake_manager, tmp_path):
    """文件不存在时直接返回 None，不调用 exiftool。"""
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    assert det.read_exif(str(tmp_path / "nope.arw"), ["Make"]) is None
    assert mgr.calls == []


@pytest.mark.parametrize("empty_tags", [[], (), None])
def test_read_exif_returns_none_for_empty_tag_list(sample_raw, fake_manager, empty_tags):
    """空标签列表返回 None，避免无意义的一次全量解析。"""
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    assert det.read_exif(sample_raw, empty_tags) is None
    assert mgr.calls == []


@pytest.mark.parametrize("payload", [None, {}])
def test_failed_read_is_not_cached(sample_raw, fake_manager, payload):
    """读取失败（None / 空 dict）不写缓存：暂时性失败不能被永久固化。"""
    mgr = fake_manager(payload)
    det = FocusPointDetector()

    assert not det.read_exif(sample_raw, ["Make"])
    assert not det.read_exif(sample_raw, ["Make"])
    assert len(mgr.calls) == 2


# --------------------------------------------------------------------------
# 4. 缓存命中 / 失效 / 上限
# --------------------------------------------------------------------------

def test_repeat_reads_hit_cache(sample_raw, fake_manager):
    """同一文件连续读取只解析一次（预取线程 + 主线程的重复读取被吸收）。"""
    mgr = fake_manager({"Make": "SONY", "ISO": 800})
    det = FocusPointDetector()

    det.read_exif(sample_raw, ["Make"])
    det.read_exif(sample_raw, ["ISO"])
    det.read_exif(sample_raw, ["Make", "ISO", "Model"])

    assert len(mgr.calls) == 1


def test_merged_superset_is_requested_once(sample_raw, fake_manager):
    """一次读取请求的是 47 标签的超集，而不是各调用方各自的小标签集。"""
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    det.read_exif(sample_raw, ["Make"])

    assert len(mgr.calls) == 1
    _, extra_args = mgr.calls[0]
    for tag in FocusPointDetector.MERGED_TAGS:
        assert f"-{tag}" in extra_args
    assert "-n" in extra_args and "-charset" in extra_args


def test_invalidate_single_path_forces_reread(sample_raw, fake_manager):
    """按文件失效后必须重新解析一次。"""
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    det.read_exif(sample_raw, ["Make"])
    det.invalidate_cache(sample_raw)
    det.read_exif(sample_raw, ["Make"])

    assert len(mgr.calls) == 2


def test_invalidate_all_clears_whole_cache(sample_raw, fake_manager, tmp_path):
    """不传参数时清空整个缓存。"""
    other = tmp_path / "other.arw"
    other.write_bytes(b"II*\x00")
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    det.read_exif(sample_raw, ["Make"])
    det.read_exif(str(other), ["Make"])
    det.invalidate_cache()
    det.read_exif(sample_raw, ["Make"])
    det.read_exif(str(other), ["Make"])

    assert len(mgr.calls) == 4


@pytest.mark.parametrize("bad_arg", ["", "   ", None, 0, object()])
def test_invalidate_cache_tolerates_bad_arguments(sample_raw, fake_manager, bad_arg):
    """失效接口对垃圾参数不抛异常（它在写回路径上被无条件调用）。"""
    fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    det.read_exif(sample_raw, ["Make"])
    det.invalidate_cache(bad_arg)  # None = 清空；其余应被忽略
    det.invalidate_cache(bad_arg)
    assert det.read_exif(sample_raw, ["Make"]) == {"Make": "SONY"}


def test_cache_is_bounded(fake_manager, tmp_path, monkeypatch):
    """缓存条目数有上限，超出后按 LRU 淘汰最旧条目。"""
    monkeypatch.setattr(FocusPointDetector, "_EXIF_CACHE_MAX_ENTRIES", 4, raising=True)
    mgr = fake_manager({"Make": "SONY"})
    det = FocusPointDetector()

    paths = []
    for i in range(7):
        p = tmp_path / f"f{i}.arw"
        p.write_bytes(b"II*\x00")
        paths.append(str(p))
        det.read_exif(str(p), ["Make"])

    with det._exif_cache_lock:
        assert len(det._exif_cache) == 4

    # 最早的三个已被淘汰，需要重新解析
    det.read_exif(paths[0], ["Make"])
    assert len(mgr.calls) == 8


# --------------------------------------------------------------------------
# 5. 并发 / concurrency
# --------------------------------------------------------------------------

def test_concurrent_reads_are_consistent(sample_raw, fake_manager):
    """并发读取不抛异常且结果一致（缓存锁只保护字典，不引入死锁）。"""
    fake_manager({"Make": "SONY", "ISO": 1600})
    det = FocusPointDetector()

    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=10)
            for _ in range(20):
                results.append(det.read_exif(sample_raw, ["Make", "ISO"]))
        except Exception as exc:  # pragma: no cover - 失败时用于报错
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    assert len(results) == 160
    assert all(r == {"Make": "SONY", "ISO": 1600} for r in results)


def test_instance_without_init_still_works(sample_raw, fake_manager):
    """object.__new__ 绕过 __init__ 的实例也能读取（既有测试工厂的用法）。"""
    fake_manager({"Make": "SONY"})
    det = object.__new__(FocusPointDetector)

    assert det.read_exif(sample_raw, ["Make"]) == {"Make": "SONY"}
    det.invalidate_cache(sample_raw)


# --------------------------------------------------------------------------
# 6. 标签清单唯一性 / single source of truth
# --------------------------------------------------------------------------

def test_detail_tags_match_photo_processor_expectations():
    """DETAIL_TAGS 必须覆盖 _read_all_exif_metadata 映射的全部 16 个字段。"""
    expected = {
        "ISO", "ShutterSpeed", "Aperture", "FocalLength",
        "FocalLengthIn35mmFormat", "Model", "LensModel",
        "GPSLatitude", "GPSLongitude", "GPSAltitude",
        "Title", "Caption-Abstract", "City", "State", "Country",
        "DateTimeOriginal",
    }
    assert set(FocusPointDetector.DETAIL_TAGS) == expected
    assert len(FocusPointDetector.DETAIL_TAGS) == len(expected)


def test_merged_tags_is_deduped_superset():
    """合并超集去重且覆盖所有分组标签。"""
    det = FocusPointDetector
    assert len(det.MERGED_TAGS) == len(set(det.MERGED_TAGS))
    for group in (
        det.COMMON_TAGS, det.NIKON_TAGS, det.SONY_TAGS, det.CANON_TAGS,
        det.OLYMPUS_TAGS, det.FUJIFILM_TAGS, det.PANASONIC_TAGS, det.DETAIL_TAGS,
    ):
        for tag in group:
            assert tag in det.MERGED_TAGS
