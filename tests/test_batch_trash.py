# -*- coding: utf-8 -*-
"""
多张删除提速:按批交给回收站,后台线程 + 进度条,语义与逐张删除一致。

此前每个文件单独起一次 osascript(实测 195ms/文件,一张照片带 XMP / JPEG 是
2~3 个文件),且整个循环在主线程上跑,删 100 张界面卡死约一分钟。

Faster multi-delete: batches handed to the trash on a worker thread with a
progress dialog, keeping the per-photo semantics. Previously one osascript
per file (195ms each) ran on the GUI thread.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.results_browser_window as rbw  # noqa: E402


def _make_jobs(tmp_path, n: int) -> list:
    """造 n 张照片的删除任务:本体 + XMP + 缓存 / n jobs with sidecar and cache."""
    jobs = []
    for i in range(n):
        main = tmp_path / f"IMG_{i}.NEF"
        xmp = tmp_path / f"IMG_{i}.xmp"
        cache = tmp_path / f"cache_{i}.jpg"
        for p in (main, xmp, cache):
            p.write_bytes(b"x")
        jobs.append((str(main), [str(xmp)], [str(cache)]))
    return jobs


@pytest.fixture
def fake_trash(monkeypatch):
    """
    替换批量回收站:记录每次调用的批,按 fail 集合模拟失败。
    Fake batch trash: records each batch, fails paths in `fail`.
    """
    state = {"calls": [], "fail": set()}

    def _fake(paths):
        state["calls"].append(list(paths))
        out = []
        for p in paths:
            if os.path.basename(p) in state["fail"]:
                out.append(False)
            else:
                os.remove(p)
                out.append(True)
        return out

    monkeypatch.setattr(rbw, "_move_many_to_trash", _fake)
    return state


def test_jobs_are_trashed_in_batches(tmp_path, fake_trash):
    """45 张按批处理:每批先本体、再附属文件,进度按批上报。"""
    b = rbw._TRASH_BATCH_PHOTOS
    jobs = _make_jobs(tmp_path, 45)
    progress = []
    outcome = rbw._trash_photo_jobs(jobs, progress=lambda d, t: progress.append((d, t)))
    assert outcome == [True] * 45
    sizes = [min(b, 45 - start) for start in range(0, 45, b)]
    assert [len(c) for c in fake_trash["calls"]] == [n for n in sizes for _ in (0, 1)]
    assert progress == [(min(start + b, 45), 45) for start in range(0, 45, b)]
    assert os.listdir(tmp_path) == []


def test_failed_main_keeps_its_companions_and_cache(tmp_path, fake_trash):
    """本体没进回收站时,它的 XMP 与缓存都不能动(#131 语义)。"""
    jobs = _make_jobs(tmp_path, 3)
    fake_trash["fail"].add("IMG_1.NEF")
    outcome = rbw._trash_photo_jobs(jobs)
    assert outcome == [True, False, True]
    assert sorted(os.listdir(tmp_path)) == ["IMG_1.NEF", "IMG_1.xmp", "cache_1.jpg"]


def test_failed_companion_does_not_fail_the_photo(tmp_path, fake_trash):
    jobs = _make_jobs(tmp_path, 1)
    fake_trash["fail"].add("IMG_0.xmp")
    assert rbw._trash_photo_jobs(jobs) == [True]


def test_cancel_stops_before_next_batch(tmp_path, fake_trash):
    """取消后当前批做完、后续批不开始,未处理的标 None。"""
    b = rbw._TRASH_BATCH_PHOTOS
    jobs = _make_jobs(tmp_path, b * 3)
    calls = {"n": 0}

    def _cancelled():
        calls["n"] += 1
        return calls["n"] > 1          # 第一批之后取消 / cancel after batch 1

    outcome = rbw._trash_photo_jobs(jobs, cancelled=_cancelled)
    assert outcome == [True] * b + [None] * (b * 2)
    assert os.path.exists(tmp_path / f"IMG_{b * 3 - 1}.NEF")


def test_pathless_record_counts_as_deleted(fake_trash):
    assert rbw._trash_photo_jobs([("", [], [])]) == [True]
    assert fake_trash["calls"] == [[], []]


def test_many_to_trash_refuses_directories_and_missing(tmp_path, monkeypatch):
    """目录与不存在的路径一律失败,且不交给 Finder / 回收站。"""
    called = []
    monkeypatch.setattr(rbw, "_move_to_trash", lambda p: called.append(p) or True)
    monkeypatch.setattr(rbw.subprocess, "run", lambda *a, **k: called.append(a) or None)
    res = rbw._move_many_to_trash([str(tmp_path), str(tmp_path / "missing.NEF"), ""])
    assert res == [False, False, False]
    assert called == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Finder 批量脚本 / macOS only")
def test_many_to_trash_maps_finder_flags(tmp_path, monkeypatch):
    """
    Finder 脚本返回的 1/0 串逐个映射回原下标;脚本报失败但文件已不在的算成功。
    Map Finder's flag string back by index; a vanished file counts as done.
    """
    a, b, c = (tmp_path / n for n in ("a.NEF", "b.NEF", "c.NEF"))
    for p in (a, b, c):
        p.write_bytes(b"x")

    class _Res:
        returncode = 0
        stdout = "100\n"
        stderr = ""

    def _fake_run(cmd, **kw):
        assert cmd[:2] == ["osascript", "-e"] and cmd[3] == "--"
        assert cmd[4:] == [str(a), str(c)]          # 缺失文件不进 argv / missing file excluded
        os.remove(a)
        os.remove(c)                                 # c 已不在但脚本报 0 / gone but flagged 0
        return _Res()

    monkeypatch.setattr(rbw.subprocess, "run", _fake_run)
    os.remove(b)
    assert rbw._move_many_to_trash([str(a), str(b), str(c)]) == [True, False, True]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Finder 批量脚本 / macOS only")
def test_finder_chunks_follow_measured_speed(tmp_path, monkeypatch):
    """
    每次交给 Finder 的文件数按实测每文件耗时调整,单次控制在目标时长内,
    避免 Finder 超过约 2 秒时自己弹「废纸篓」进度窗(与我们的进度框重复)。

    Chunk size follows measured per-file time so a Finder call stays under
    the target and Finder never pops its own Trash progress window.
    """
    paths = []
    for i in range(20):
        p = tmp_path / f"f{i}.NEF"
        p.write_bytes(b"x")
        paths.append(str(p))
    clock = {"t": 0.0}
    sizes = []

    class _Res:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kw):
        files = cmd[4:]
        sizes.append(len(files))
        clock["t"] += 0.2 * len(files)            # 慢盘:0.2 秒/文件 / slow disk
        for f in files:
            os.remove(f)
        r = _Res()
        r.stdout = "1" * len(files)
        return r

    monkeypatch.setattr(rbw.subprocess, "run", _fake_run)
    monkeypatch.setattr(rbw.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(rbw, "_finder_secs_per_file", 0.1)
    assert rbw._move_many_to_trash(paths) == [True] * 20
    # 首块按初始估计 0.8/0.1=8 个;测得 0.2 秒/文件后每块 4 个(0.8 秒)
    assert sizes == [8, 4, 4, 4]
    assert all(n * 0.2 <= rbw._FINDER_CALL_TARGET_SEC + 1e-9 for n in sizes[1:])


# ── 窗口层:多张删除走后台 + 进度框,失败照片留在列表 ─────────────────────


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """5 张 CR3 的浏览器,批量回收站打桩 / browser with 5 photos, trash stubbed."""
    from tools.report_db import ReportDB
    root = str(tmp_path)
    db = ReportDB(root)
    for i in range(5):
        open(os.path.join(root, f"IMG_{i}.CR3"), "w").close()
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.CR3",
                         "original_path": f"IMG_{i}.CR3", "rating": 3, "has_bird": 1})
    db.close()

    fail = {"IMG_3.CR3"}

    def _fake(paths):
        out = []
        for p in paths:
            ok = os.path.basename(p) not in fail
            if ok:
                os.remove(p)
            out.append(ok)
        return out

    warnings = []
    monkeypatch.setattr(rbw, "_move_many_to_trash", _fake)
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete",
                        lambda self, photos, jobs, permanent=False, **_kw: "permanent" if permanent else "trash")
    monkeypatch.setattr(rbw.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a)), raising=False)
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    deadline = time.time() + 5
    grid = win._thumb_grid
    while time.time() < deadline:
        _app.processEvents()
        if grid._photos and grid._current_batch_idx >= len(grid._photos) and not grid._pending_photos:
            break
        time.sleep(0.01)
    yield win, warnings
    win.close()


def _wait(win, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _app.processEvents()
        if win._delete_cancel is None:
            return
        time.sleep(0.01)
    raise AssertionError("delete not finished")


def test_multi_delete_runs_in_background_and_reports_failures(browser):
    win, warnings = browser
    targets = [p for p in win._filtered_photos if p["filename"] in ("IMG_1", "IMG_3", "IMG_4")]
    win._delete_photos(targets, in_fullscreen=False)
    assert win._delete_progress_dialog is not None     # 显示了进度框 / dialog shown
    # 进行中再次删除被忽略 / re-entry ignored while running
    win._delete_photos([win._filtered_photos[0]], in_fullscreen=False)
    _wait(win)
    names = [p["filename"] for p in win._filtered_photos]
    assert names == ["IMG_0", "IMG_2", "IMG_3"]        # 失败的 IMG_3 留下 / failed one stays
    assert len(warnings) == 1 and "IMG_3.CR3" in warnings[0][2]
    assert win._db.get_photo("IMG_3") is not None
    assert win._db.get_photo("IMG_1") is None
