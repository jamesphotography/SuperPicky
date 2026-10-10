# -*- coding: utf-8 -*-
"""
平铺布局下 current_path 指向缓存预览图的回归测试（issue #118）。
Regression tests: current_path pointing at the cache preview under the flat layout.

动因：处理时 ai_model 把「送进 YOLO 的那张图」写成 current_path——RAW 照片就是
.superpicky/cache/temp_preview/ 下的预览 JPG。正常布局在整理阶段用 RAW 的真实位置
覆盖它；平铺布局不整理（organize_files=False），于是 current_path 一直是预览图，
浏览器里「用外部应用打开 / 复制路径 / 改星级 / 删除」作用的全是预览图而不是
RAW（导入照片 App 会优先挑 RAW，不受影响）。这里锁住两道修复：
1. 源头：build_path_update_data 让 current_path 指向原图本体；
2. 存量：ReportDB 打开时把指向内部缓存的 current_path 纠正回 original_path。

Under the flat layout nothing reorganises files, so the cache-preview path the
AI step recorded as current_path was never corrected. Both the source and the
already-written databases are fixed here.
"""
import os

from tools.report_db import ReportDB


# ── 1. 源头 / Source ─────────────────────────────────────────────────────────

def _item(root, filename, raw_path=None, prefix=None):
    """构造 photo_processor 的 yolo_item 片段 / Minimal yolo_item."""
    return {
        "filename": filename,
        "filepath": os.path.join(root, filename),
        "file_prefix": prefix or os.path.splitext(os.path.basename(filename))[0],
        "raw_path": raw_path,
    }


def test_raw_photo_current_path_is_the_raw_not_the_cache_preview(tmp_path):
    """RAW 照片：current_path 与 original_path 都是 RAW，预览图只进 temp_jpeg_path。"""
    from core.photo_processor import build_path_update_data

    root = str(tmp_path)
    raw = tmp_path / "_T3A8951.CR3"
    raw.write_bytes(b"raw")
    preview = os.path.join(".superpicky", "cache", "temp_preview", "_T3A8951.jpg")
    (tmp_path / ".superpicky" / "cache" / "temp_preview").mkdir(parents=True)
    (tmp_path / preview).write_bytes(b"jpg")

    data = build_path_update_data(_item(root, preview, raw_path=str(raw)), root)
    assert data["current_path"] == "_T3A8951.CR3"
    assert data["original_path"] == "_T3A8951.CR3"
    assert data["temp_jpeg_path"] == preview


def test_jpeg_only_photo_points_at_itself(tmp_path):
    """纯 JPEG 照片：current_path / original_path / temp_jpeg_path 都是它自己。"""
    from core.photo_processor import build_path_update_data

    root = str(tmp_path)
    (tmp_path / "IMG_1.jpg").write_bytes(b"jpg")
    data = build_path_update_data(_item(root, "IMG_1.jpg"), root)
    assert data["current_path"] == "IMG_1.jpg"
    assert data["original_path"] == "IMG_1.jpg"


def test_cache_file_without_raw_never_becomes_current_path(tmp_path):
    """找不到 RAW 的缓存预览图不能当成原图 / A cache file is never the original."""
    from core.photo_processor import build_path_update_data

    root = str(tmp_path)
    preview = os.path.join(".superpicky", "cache", "temp_preview", "X.jpg")
    (tmp_path / ".superpicky" / "cache" / "temp_preview").mkdir(parents=True)
    (tmp_path / preview).write_bytes(b"jpg")
    data = build_path_update_data(_item(root, preview), root)
    assert "current_path" not in data
    assert "original_path" not in data


# ── 2. 存量数据库 / Existing databases ──────────────────────────────────────

def _insert(db, filename, current, original):
    db.insert_photo({"filename": filename, "current_path": current, "original_path": original})


def _paths(directory, filename):
    db = ReportDB(directory)
    try:
        row = db.get_photo(filename)
        return row["current_path"], row["original_path"]
    finally:
        db.close()


def test_reopening_a_database_repairs_cache_current_paths(tmp_path):
    """打开时把指向缓存的 current_path 改回 original_path，POSIX 与 Windows 分隔符都认。"""
    root = str(tmp_path)
    db = ReportDB(root)
    _insert(db, "A", ".superpicky/cache/temp_preview/A.jpg", "A.CR3")
    _insert(db, "B", ".superpicky\\cache\\temp_preview\\B.jpg", "B.NEF")
    _insert(db, "C", "3星_优选/C.ARW", "C.ARW")           # 正常记录不动
    _insert(db, "D", ".superpicky/cache/temp_preview/D.jpg", None)  # 没有原图路径：不动
    db.close()

    assert _paths(root, "A") == ("A.CR3", "A.CR3")
    assert _paths(root, "B") == ("B.NEF", "B.NEF")
    assert _paths(root, "C") == ("3星_优选/C.ARW", "C.ARW")
    assert _paths(root, "D") == (".superpicky/cache/temp_preview/D.jpg", None)


# ── 3. 浏览器端到端 / Browser end to end ────────────────────────────────────

def test_browser_actions_target_the_raw_for_an_old_flat_batch(tmp_path):
    """
    旧平铺批次（库里 current_path 是预览图）用浏览器打开后，改星级写入、删除、
    复制路径用的都是 CR3 / Rating writes and deletes hit the CR3, not the preview.
    """
    from test_species_change_regression import _make_jpeg, _settle_grid, _pin_chinese_locale  # noqa: F401
    import ui.results_browser_window as rbw

    root = str(tmp_path)
    (tmp_path / "_T3A8951.CR3").write_bytes(b"raw")
    preview = os.path.join(".superpicky", "cache", "temp_preview", "_T3A8951.jpg")
    _make_jpeg(os.path.join(root, preview))
    db = ReportDB(root)
    db.insert_photo({"filename": "_T3A8951", "current_path": preview,
                     "original_path": "_T3A8951.CR3", "temp_jpeg_path": preview,
                     "rating": 3, "has_bird": 1})
    db.close()

    win = rbw.ResultsBrowserWindow()
    try:
        win.open_directory(root)
        _settle_grid(win)
        photo = next(p for p in win._filtered_photos if p["filename"] == "_T3A8951")
        assert photo["current_path"] == os.path.join(root, "_T3A8951.CR3")
        assert win._get_photo_file_path(photo) == os.path.join(root, "_T3A8951.CR3")
    finally:
        win.close()
