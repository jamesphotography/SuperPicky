# -*- coding: utf-8 -*-
"""
结果浏览器换目录后必须回到缩略图网格（issue #172）。

结果浏览器是复用的单例：离开时只是隐藏，停在哪一页就留在哪一页。以前
open_directory 只换数据库和照片列表，不动页面——在 A 目录全屏看图时离开，
再选完 B 目录，窗口一打开就是全屏页：大图仍是 A 的照片，右侧详情却是 B 的
第一张；此时键盘打星 / 删除 / 改鸟种都作用在 A 的照片上，却写进 B 的库。
对比页（2-up）同理。

The results browser is a reused singleton that is only hidden on leave, so it
kept whichever page it was on. open_directory swapped the database and photo
list but not the page: leaving folder A in fullscreen and culling folder B
reopened on the fullscreen page showing A's photo next to B's details, and
fullscreen actions targeted A's photo while writing to B's database (#172).
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _make_folder(root: str, prefix: str, count: int = 3) -> str:
    """
    建一个含 count 张 3★ 照片的已处理目录（文件名带 prefix，便于区分两个目录）。

    参数 / Args:
        root:   目录路径（会被创建）
        prefix: 文件名前缀 / filename prefix
        count:  照片数 / number of photos

    返回 / Returns:
        str: 目录路径 / the folder path
    """
    from tools.report_db import ReportDB

    os.makedirs(root, exist_ok=True)
    db = ReportDB(root)
    for i in range(count):
        stem = f"{prefix}_{i}"
        open(os.path.join(root, stem + ".CR3"), "w").close()
        db.insert_photo({
            "filename": stem, "current_path": stem + ".CR3", "original_path": stem + ".CR3",
            "rating": 3, "has_bird": 1,
        })
    db.close()
    return root


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


@pytest.fixture
def two_folders(tmp_path):
    """两个互不相干的已处理目录 A、B / two unrelated processed folders."""
    folder_a = _make_folder(str(tmp_path / "2026" / "11" / "25"), "A")
    folder_b = _make_folder(str(tmp_path / "2026" / "11" / "24"), "B")
    return folder_a, folder_b


@pytest.fixture
def browser(two_folders):
    """打开 A 目录的真实结果浏览器 / a real browser opened on folder A."""
    import ui.results_browser_window as rbw

    folder_a, _ = two_folders
    win = rbw.ResultsBrowserWindow()
    win.open_directory(folder_a)
    win.show()
    _wait_grid_built(win)
    yield win
    win.close()


def _names(photos: list) -> set:
    """取照片列表的文件名集合 / filenames of a photo list."""
    return {p.get("filename") for p in photos}


def test_reopen_from_fullscreen_returns_to_grid(browser, two_folders):
    """A 目录全屏时离开、再打开 B：回到网格，全屏导航与详情都只含 B 的照片。"""
    import ui.results_browser_window as rbw

    _, folder_b = two_folders
    photo_a = browser._filtered_photos[0]
    browser._enter_fullscreen(photo_a)
    assert browser._stack.currentIndex() == rbw._PAGE_FULLSCREEN

    # 主窗口的「返回」只隐藏窗口；随后选完 B，主窗口对同一实例调 open_directory
    # The main window only hides the browser, then reuses it for folder B
    browser._go_back_to_main()
    browser.open_directory(folder_b)
    browser.show()
    _wait_grid_built(browser)

    assert browser._stack.currentIndex() == rbw._PAGE_GRID
    assert _names(browser._fullscreen_nav_photos) == {"B_0", "B_1", "B_2"}
    assert browser._detail_panel._current_photo["filename"].startswith("B_")


def test_reopen_from_comparison_returns_to_grid(browser, two_folders):
    """A 目录停在对比页时打开 B：回到网格，且详情面板重新显示。"""
    import ui.results_browser_window as rbw

    _, folder_b = two_folders
    browser._thumb_grid.select_all()
    browser._enter_comparison()
    assert browser._stack.currentIndex() == rbw._PAGE_COMPARISON
    assert browser._detail_panel.isHidden()

    browser._go_back_to_main()
    browser.open_directory(folder_b)
    browser.show()
    _wait_grid_built(browser)

    assert browser._stack.currentIndex() == rbw._PAGE_GRID
    assert not browser._detail_panel.isHidden()
