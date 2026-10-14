# -*- coding: utf-8 -*-
"""
结果浏览器删除 / 全屏导航一组回归测试（#127 #131 #132 #133）。

- #127 全屏用方向键翻页时，右侧详情面板（裁切 / YOLO 图）必须跟着刷新图片；
- #131 删除照片时 XMP 边车与配套相机 JPEG 移入回收站、.superpicky 缓存图删除；
- #132 全屏删除后，方向键导航列表里不能再有被删的照片；
- #133 网格删除后，后面的卡片前移补位，不留空格。

Regression tests for the results-browser delete/navigation group.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

N_PHOTOS = 5


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """
    建一个真实 ResultsBrowserWindow：5 张 3★ 照片，每张带 XMP 边车与一张缓存预览。
    回收站换成记录 + 删除的假实现，删除确认直接通过。

    A real browser with 5 photos, each with an .xmp sidecar and a cached
    preview; the trash is faked and confirmation auto-accepted.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    cache_dir = os.path.join(root, ".superpicky", "cache", "temp_preview")
    os.makedirs(cache_dir)
    db = ReportDB(root)
    for i in range(N_PHOTOS):
        stem = f"IMG_{i}"
        for path in (os.path.join(root, stem + ".CR3"), os.path.join(root, stem + ".xmp"),
                     os.path.join(cache_dir, stem + ".jpg")):
            open(path, "w").close()
        if i == 2:
            # IMG_2 是 RAW+JPEG 拍摄 / IMG_2 is a RAW+JPEG shot
            open(os.path.join(root, stem + ".JPG"), "w").close()
        db.insert_photo({
            "filename": stem, "current_path": stem + ".CR3", "original_path": stem + ".CR3",
            "temp_jpeg_path": os.path.join(".superpicky", "cache", "temp_preview", stem + ".jpg"),
            "rating": 3, "has_bird": 1,
        })
    db.close()

    trashed = []

    def fake_trash(path: str) -> bool:
        """记录并删除 / record and remove."""
        trashed.append(os.path.basename(path))
        os.remove(path)
        return True

    monkeypatch.setattr(rbw, "_move_to_trash", fake_trash)
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete", lambda self, photos: True)

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    _wait_grid_built(win)
    yield win, root, trashed
    win.close()


def _wait_grid_built(win, timeout: float = 5.0) -> None:
    """等网格分批建完卡片 / Pump events until all cards are built."""
    grid = win._thumb_grid
    deadline = time.time() + timeout
    while time.time() < deadline:
        _app.processEvents()
        if grid._photos and grid._current_batch_idx >= len(grid._photos) and not grid._pending_photos:
            return
        time.sleep(0.01)
    raise AssertionError("grid not built")


def _names(photos) -> list:
    return [p["filename"] for p in photos]


def test_delete_trashes_sidecar_and_removes_cache(browser):
    """#131：删除后 XMP 与配套 JPEG 进回收站、缓存预览被删，其他照片的附属文件不动。"""
    win, root, trashed = browser
    target = next(p for p in win._filtered_photos if p["filename"] == "IMG_2")
    win._delete_photos([target], in_fullscreen=False)

    # 大小写不敏感的文件系统上 sibling_jpeg 可能以 .jpg 命中同一个文件
    # On case-insensitive filesystems the JPEG may be reported as .jpg
    assert sorted(n.lower() for n in trashed) == ["img_2.cr3", "img_2.jpg", "img_2.xmp"]
    assert not os.path.exists(os.path.join(root, "IMG_2.JPG"))
    assert not os.path.exists(os.path.join(root, ".superpicky", "cache", "temp_preview", "IMG_2.jpg"))
    assert os.path.exists(os.path.join(root, "IMG_1.xmp"))
    assert os.path.exists(os.path.join(root, ".superpicky", "cache", "temp_preview", "IMG_1.jpg"))


def test_companions_never_include_the_main_file(tmp_path):
    """纯 JPEG 照片：同名 JPEG 就是本体，不能被当成配套文件再删一次。"""
    from ui.results_browser_window import _photo_companion_files
    main = tmp_path / "IMG_9.JPG"
    main.write_text("")
    (tmp_path / "IMG_9.xmp").write_text("")
    trash, caches = _photo_companion_files({"current_path": str(main)})
    assert [os.path.basename(p) for p in trash] == ["IMG_9.xmp"]
    assert caches == []


def test_fullscreen_delete_removes_photo_from_arrow_key_navigation(browser):
    """#132：全屏删除后，方向键导航与所有缓存列表都不再含被删照片。"""
    win, _, _ = browser
    order = _names(win._filtered_photos)
    victim = order[2]
    win._enter_fullscreen(win._filtered_photos[2])

    win._on_delete_photo(win._fullscreen._current_photo)

    for lst in (win._fullscreen_nav_photos, win._filtered_photos,
                win._all_photos, win._raw_filtered_photos):
        assert victim not in _names(lst)
    # 自动跳到原位置的下一张，再按左键回到上一张（不是被删的那张）
    # Lands on the next photo; Left goes to the one before the deleted photo
    assert win._fullscreen._current_photo["filename"] == order[3]
    win._fullscreen_prev()
    assert win._fullscreen._current_photo["filename"] == order[1]


def test_grid_delete_leaves_no_gap(browser):
    """#133：删除后剩余卡片按新下标重新排布（行, 列）连续无空格。"""
    win, _, _ = browser
    grid = win._thumb_grid
    win._delete_photos([win._filtered_photos[1]], in_fullscreen=False)

    from ui.thumbnail_grid import _photo_key
    cols = grid._current_col_count
    assert len(grid._photos) == N_PHOTOS - 1
    for idx, photo in enumerate(grid._photos):
        card = grid._cards[_photo_key(photo)]
        row, col, _, _ = grid._grid.getItemPosition(grid._grid.indexOf(card))
        assert (row, col) == divmod(idx, cols)


def test_fullscreen_arrow_keys_refresh_detail_panel_image(browser, monkeypatch):
    """#127：全屏方向键翻页时，可见的详情面板必须重新加载图片（不只同步文字）。"""
    win, _, _ = browser
    win._enter_fullscreen(win._filtered_photos[0])
    assert win._detail_panel.isVisible()

    refreshed = []
    monkeypatch.setattr(win._detail_panel, "_refresh_image",
                        lambda: refreshed.append(win._detail_panel._current_photo["filename"]))
    win._fullscreen_next()

    current = win._fullscreen._current_photo["filename"]
    assert refreshed and refreshed[-1] == current
