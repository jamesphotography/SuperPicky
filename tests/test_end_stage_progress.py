# -*- coding: utf-8 -*-
"""
选片收尾「卡在 100%」的回归测试（用户反馈，未立 issue）。

收尾阶段（汇总识鸟、按配额定星、把评分批量写回照片、视频）此前没有任何进度显示；
ExifTool 批量写入的超时是整批总时长（64 张 × 5 秒 = 320 秒），机器上 ExifTool
一直无响应时每批都等满 5 分多钟，超时还只 print 不进日志。改为：
- 「60 秒内一张都没写完才算卡住」的空闲超时，慢但在进展的机器不会被误判；
- 连续 2 批超时就停止写入并明确告知；
- 收尾各阶段与视频阶段经 status 回调显示进度。

Regression tests for the end-of-run "stuck at 100%" reports: idle timeout per
file instead of a 320 s batch budget, a stop after consecutive timeouts, and
stage status reporting for the post-processing and video stages.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ---------- ExifTool 批量读结果：空闲超时 / idle timeout ----------

def _reader(script):
    """
    按脚本依次返回输出或抛超时的替身 read_until_ready。
    A fake read_until_ready that replays outputs or raises TimeoutError.
    """
    calls = {"timeouts": []}
    steps = list(script)

    def read(timeout):
        calls["timeouts"].append(timeout)
        step = steps.pop(0)
        if step == "timeout":
            raise TimeoutError("ExifTool timeout")
        return step
    return read, calls


def test_slow_but_progressing_batch_never_times_out():
    """每张都在空闲超时内写完，不管总时长多长都不算超时；每次等待用的是空闲超时。"""
    from tools.exiftool_manager import ExifToolManager

    read, calls = _reader([b"    1 image files updated\n{ready}\n"] * 5)
    errors, done, timed_out = ExifToolManager._collect_batch_results(read, 5, idle_timeout=60.0)
    assert (errors, done, timed_out) == (0, 5, False)
    assert calls["timeouts"] == [60.0] * 5


def test_stuck_file_reports_timeout_with_done_count():
    from tools.exiftool_manager import ExifToolManager

    ok = b"    1 image files updated\n{ready}\n"
    read, _ = _reader([ok, b"Error: file locked\n{ready}\n", "timeout"])
    errors, done, timed_out = ExifToolManager._collect_batch_results(read, 5, idle_timeout=60.0)
    assert (errors, done, timed_out) == (1, 2, True)


# ---------- 连续超时就停 / stop after consecutive timeouts ----------

def test_guard_stops_after_two_consecutive_timeouts():
    from core.exif_write_guard import ExifWriteGuard

    g = ExifWriteGuard()
    g.add_queued(64 * 4)
    g.record(64, {"success": 64, "failed": 0})
    g.record(64, {"success": 10, "failed": 54, "timed_out": True})
    assert not g.aborted, "一次超时还不放弃 / one timeout is tolerated"
    g.record(64, {"success": 0, "failed": 64, "timed_out": True})
    assert g.aborted
    g.record_skipped(64)
    assert (g.done, g.total, g.failed) == (256, 256, 54 + 64 + 64)


def test_guard_success_resets_timeout_streak():
    from core.exif_write_guard import ExifWriteGuard

    g = ExifWriteGuard()
    g.add_queued(192)
    g.record(64, {"success": 0, "failed": 64, "timed_out": True})
    g.record(64, {"success": 64, "failed": 0})
    g.record(64, {"success": 0, "failed": 64, "timed_out": True})
    assert not g.aborted


def test_guard_counts_photos_not_items():
    """
    #150：一张照片两条（星级 + 标题），进度按张显示，不能把 2 张显示成 4。

    #150: one photo queues two items; progress counts photos, not items.
    """
    from core.exif_write_guard import ExifWriteGuard

    g = ExifWriteGuard()
    g.add_queued(2, ["a.CR3", "b.CR3"])            # 标题条目 / title items
    g.add_queued(2, ["a.CR3", "b.CR3"])            # 星级条目 / rating items
    assert (g.total, g.photos_total, g.photos_done) == (4, 2, 0)
    g.record(2, {"success": 2, "failed": 0}, ["a.CR3", "b.CR3"])
    assert g.photos_done == 0, "每张还剩一条没写 / one item left per photo"
    g.record(1, {"success": 1, "failed": 0}, ["a.CR3"])
    assert g.photos_done == 1
    g.record_skipped(1, ["b.CR3"])
    assert (g.photos_done, g.photos_total) == (2, 2)


def test_guard_photo_reopens_when_more_items_arrive():
    """写完的照片又排了新条目时重新算作未完成，进度不会提前报满。"""
    from core.exif_write_guard import ExifWriteGuard

    g = ExifWriteGuard()
    g.add_queued(1, ["a.CR3"])
    g.record(1, {"success": 1, "failed": 0}, ["a.CR3"])
    assert g.photos_done == 1
    g.add_queued(1, ["a.CR3"])
    assert (g.photos_done, g.photos_total) == (0, 1)


def test_guard_counts_raw_jpeg_pair_as_one_photo():
    """
    #150：RAW+JPEG 时 CR3 与配套 JPEG 都要写，一组按主名只算一张；两个都写完才算完成。
    不同文件夹里的同名照片仍是两张。

    #150: a RAW+JPEG pair counts once (keyed by folder + stem) and is done only
    when both files are written; same stems in different folders stay separate.
    """
    from core.exif_write_guard import ExifWriteGuard

    a_raw = os.path.join("shoot", "IMG_1.CR3")
    a_jpg = os.path.join("shoot", "IMG_1.JPG")
    other = os.path.join("other", "IMG_1.CR3")
    g = ExifWriteGuard()
    g.add_queued(3, [a_raw, a_jpg, other])         # 星级 / ratings
    g.add_queued(2, [a_raw, a_jpg])                # 标题 / titles
    assert (g.total, g.photos_total, g.photos_done) == (5, 2, 0)
    g.record(4, {"success": 4, "failed": 0}, [a_raw, a_jpg, other, a_raw])
    assert g.photos_done == 1, "JPEG 的标题还没写 / the JPEG title is still pending"
    g.record(1, {"success": 1, "failed": 0}, [a_jpg])
    assert (g.photos_done, g.photos_total) == (2, 2)


# ---------- 状态回调与文案 / status channel and strings ----------

def test_processing_callbacks_have_status():
    from core.photo_processor import ProcessingCallbacks

    got = []
    cb = ProcessingCallbacks(status=got.append)
    cb.status("x")
    assert got == ["x"]


def test_video_stage_reports_status(monkeypatch):
    """视频阶段按 第几个 / 共几个 发状态（此前进度回调是空函数）。"""
    from types import SimpleNamespace

    import advanced_config as ac
    import core.video_batch_engine as vbe
    from ui.main_window import WorkerSignals, WorkerThread

    cfg = SimpleNamespace(video_auto_process_in_main=True, folder_layout="rating_first", config={})
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)

    class FakeEngine:
        def __init__(self, **_kw):
            pass

        def process(self, video_paths, log_cb, progress_cb, stop_cb):
            for i in range(1, len(video_paths) + 1):
                progress_cb(i, len(video_paths))
            return SimpleNamespace(total=len(video_paths), organized=0, failed=0,
                                   species_counts={}, total_wall_ms=0)

    monkeypatch.setattr(vbe, "VideoBatchEngine", FakeEngine)
    signals = WorkerSignals()
    got = []
    signals.status.connect(got.append)
    worker = WorkerThread("/tmp/nowhere", {}, signals,
                          scan_results=[SimpleNamespace(video_files=["a.mp4", "b.mp4"])])
    worker._process_videos()
    assert len(got) == 2 and "2/2" in got[-1]


def test_stage_strings_exist_in_all_locales():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "locales"
    for name in ("en_US.json", "zh_CN.json", "zh_TW.json"):
        data = json.loads((root / name).read_text(encoding="utf-8"))
        for section, key in (("labels", "stage_finalize_birdid"), ("labels", "stage_rating"),
                             ("labels", "stage_exif_write"), ("labels", "stage_organizing"),
                             ("labels", "stage_cleanup"), ("labels", "stage_video"),
                             ("logs", "exif_batch_timeout"), ("logs", "exif_write_aborted"),
                             ("logs", "exif_write_summary")):
            assert data[section].get(key), f"{name}: {section}.{key}"
