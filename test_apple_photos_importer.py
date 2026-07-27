# -*- coding: utf-8 -*-
"""
Apple Photos 导入控制器的路径、安全、批次和平台隔离回归测试。

Regression tests for Apple Photos path selection, argument safety, batching,
process cleanup, and platform isolation.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui import apple_photos_importer as photos_importer


_app = QApplication.instance() or QApplication([])


def _candidate(index: int) -> photos_importer.ImportCandidate:
    """构造无需真实文件的控制器候选。/ Build a controller-only candidate."""

    return photos_importer.ImportCandidate(
        path=Path(f"/tmp/SuperPicky import {index}.jpg"),
        photo_identity=("source", f"bird-{index}.jpg"),
    )


def _request(
    album_name: str,
    count: int,
    *,
    batch_size: int = 100,
) -> photos_importer.ApplePhotosImportRequest:
    """构造控制器测试请求。/ Build a controller test request."""

    candidates = tuple(_candidate(index) for index in range(count))
    return photos_importer.ApplePhotosImportRequest(
        album_name=album_name,
        candidates=candidates,
        requested=count,
        preflight_skipped=0,
        batch_size=batch_size,
    )


def _wait_for(predicate, timeout: float = 5.0) -> None:
    """泵送 Qt 事件直到条件成立。/ Pump Qt events until a condition is true."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for Qt process completion")


def _write_fake_osascript(tmp_path: Path) -> Path:
    """
    写入可预测的 osascript 替身。/ Write a deterministic osascript stand-in.

    argv 布局为 ``-e SCRIPT -- ALBUM PATH...``。DENY 模拟 TCC 拒绝，
    SLOW 用于取消/关闭测试，其余情况返回本批路径数。
    """

    helper = tmp_path / "fake_osascript.py"
    helper.write_text(
        f"""#!{sys.executable}
import sys
import time

album = sys.argv[4]
if album == "DENY":
    print("Not authorized to send Apple events. (-1743)", file=sys.stderr)
    raise SystemExit(1)
if album == "SLOW":
    time.sleep(30)
print(len(sys.argv) - 5)
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    return helper


def test_resolve_prefers_sibling_jpeg_and_ignores_cache(tmp_path: Path) -> None:
    """RAW+JPG 只选同名 JPG，且不选 temp_jpeg_path 缓存。/ Prefer paired JPEG."""

    raw = tmp_path / "鸟照.NEF"
    jpeg = tmp_path / "鸟照.JPG"
    cache = tmp_path / "cache.jpg"
    raw.write_bytes(b"raw")
    jpeg.write_bytes(b"jpeg")
    cache.write_bytes(b"cache")

    resolved = photos_importer.resolve_photo_import_path(
        {
            "current_path": str(raw),
            "original_path": str(raw),
            "temp_jpeg_path": str(cache),
        }
    )

    assert resolved is not None
    assert os.path.samefile(resolved, jpeg)


def test_resolve_prefers_direct_heif_then_falls_back_to_raw(tmp_path: Path) -> None:
    """直接 HEIF 优先；没有显示边车时回退 RAW。/ Prefer HEIF, then RAW."""

    heif = tmp_path / "display.HEIC"
    raw = tmp_path / "raw.ARW"
    heif.write_bytes(b"heif")
    raw.write_bytes(b"raw")

    assert (
        photos_importer.resolve_photo_import_path({"current_path": str(heif)})
        == heif.resolve()
    )
    assert (
        photos_importer.resolve_photo_import_path({"current_path": str(raw)})
        == raw.resolve()
    )


def test_resolve_merged_relative_record_and_preflight_deduplicates(
    tmp_path: Path,
) -> None:
    """合并项目相对路径可解析，重复真实文件只导入一次。/ Resolve merged paths and dedupe."""

    photo_path = tmp_path / "子目录" / "same.jpg"
    photo_path.parent.mkdir()
    photo_path.write_bytes(b"jpeg")
    records = [
        {
            "_base_dir": str(tmp_path),
            "current_path": str(Path("子目录") / "same.jpg"),
            "source_dir": "one",
            "filename": "same.jpg",
        },
        {
            "current_path": str(photo_path),
            "source_dir": "two",
            "filename": "same.jpg",
        },
        {"current_path": str(tmp_path / "missing.jpg"), "filename": "missing.jpg"},
    ]

    result = photos_importer.preflight_photos(records)

    assert result.requested == 3
    assert [candidate.path for candidate in result.candidates] == [photo_path.resolve()]
    assert result.skipped == 2


def test_arguments_keep_unicode_quotes_and_newlines_out_of_script(
    tmp_path: Path,
) -> None:
    """特殊字符只存在 argv，不进入固定 AppleScript。/ Keep special text in argv only."""

    path = tmp_path / '白胸鸲鹟 "精选"\n01.jpg'
    path.write_bytes(b"jpeg")
    album = '鸟类 "精选"\n夏季'
    candidate = photos_importer.ImportCandidate(path.resolve(), ("", path.name))

    arguments = photos_importer.build_osascript_arguments(album, [candidate])

    assert arguments[0] == "-e"
    assert album not in arguments[1]
    assert os.fspath(path.resolve()) not in arguments[1]
    assert arguments[2:] == ["--", '鸟类 "精选" 夏季', os.fspath(path.resolve())]


def test_album_helpers_are_deterministic() -> None:
    """默认相册含目录名和日期，输入空白被安全折叠。/ Album helpers are deterministic."""

    assert (
        photos_importer.default_album_name(
            "/Pictures/鸟图",
            date(2026, 7, 23),
        )
        == "鸟图 - 2026-07-23"
    )
    assert photos_importer.sanitize_album_name("  夏季\n\t鸟图  ") == "夏季 鸟图"


def test_importer_batches_and_reports_photos_not_imported(
    tmp_path: Path, monkeypatch
) -> None:
    """205 张按 100 分三批，并准确累计 Photos 返回数。/ Batch and aggregate counts."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    progress = []
    importer.completed.connect(results.append)
    importer.progress.connect(lambda done, total: progress.append((done, total)))

    importer.start(_request("Birds", 205))
    _wait_for(lambda: bool(results))

    result = results[0]
    assert result.newly_imported == 205
    assert result.completed_batches == 3
    assert result.photos_not_imported == 0
    assert result.remaining == 0
    assert progress == [(0, 205), (100, 205), (200, 205), (205, 205)]


def test_importer_stops_on_permission_failure(tmp_path: Path, monkeypatch) -> None:
    """Apple Events 拒绝后停止并保留可识别错误。/ Stop on Automation denial."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("DENY", 3))
    _wait_for(lambda: bool(results))

    assert results[0].completed_batches == 0
    assert results[0].newly_imported == 0
    assert photos_importer.is_automation_permission_error(results[0].error or "")


def test_importer_cancel_stops_remaining_batches(tmp_path: Path, monkeypatch) -> None:
    """取消终止当前 helper 且不启动余下批次。/ Cancellation leaves later batches unstarted."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("SLOW", 101))
    importer.cancel()
    _wait_for(lambda: bool(results))

    assert results[0].cancelled is True
    assert results[0].attempted == 100
    assert results[0].remaining == 1
    assert importer.is_running is False


def test_importer_shutdown_kills_helper_without_completion(
    tmp_path: Path, monkeypatch
) -> None:
    """窗口退出同步清理 helper，且不回调已销毁 UI。/ Shutdown suppresses completion callbacks."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("SLOW", 1))
    importer.shutdown()

    assert importer.is_running is False
    assert importer._process.state() == photos_importer.QProcess.NotRunning
    assert results == []


def test_browser_target_selection_precedence() -> None:
    """两张及以上多选优先；否则导入当前可见结果。/ Multi-select takes precedence."""

    from ui.results_browser_window import ResultsBrowserWindow

    class _Grid:
        def __init__(self, selected: list[dict]):
            self.selected = selected

        def get_multi_selected_photos(self) -> list[dict]:
            return self.selected

    class _Window:
        _filtered_photos = [{"filename": "visible-a"}, {"filename": "visible-b"}]

    fake = _Window()
    fake._thumb_grid = _Grid([{"filename": "selected-a"}, {"filename": "selected-b"}])
    assert [
        p["filename"] for p in ResultsBrowserWindow._apple_photos_target_photos(fake)
    ] == [
        "selected-a",
        "selected-b",
    ]

    fake._thumb_grid = _Grid([])
    assert [
        p["filename"] for p in ResultsBrowserWindow._apple_photos_target_photos(fake)
    ] == [
        "visible-a",
        "visible-b",
    ]


def test_windows_browser_has_no_apple_photos_action(monkeypatch) -> None:
    """Windows 构造浏览器时不创建 Apple Photos 按钮。/ No Photos action on Windows."""

    from ui import results_browser_window

    monkeypatch.setattr(results_browser_window.sys, "platform", "win32")
    window = results_browser_window.ResultsBrowserWindow()
    try:
        assert window._apple_photos_btn is None
    finally:
        window.cleanup()
        window.close()
