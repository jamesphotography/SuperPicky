# -*- coding: utf-8 -*-
"""
#167：星级筛选 / 按星级排序下手动改星级，网格要按新星级更新——但等用户离开这张再更新。

此前改星级只刷新缩略图角标，从不重放筛选：3★ 筛选下把一张改成 2★ 仍留在网格里，
按星级排序时也不挪位置，要改一下别的筛选才正常。

立刻刷新会让连按 ↓↓（3★→1★）的第二下落到下一张照片上，批量改星级也会清掉勾选，
所以约定为：照片先留在原位，切到别的照片 / 退出全屏时再重放筛选与排序。

#167: a manual rating change must update a rating-filtered or rating-sorted
grid, deferred until the user leaves the photo (an immediate refresh would make
a second Down key hit the next photo).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_app = QApplication.instance() or QApplication([])


def _join_worker_threads(timeout: float = 8.0) -> None:
    """等改星级起的后台线程（写元数据）跑完 / Wait for metadata workers."""
    import threading
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not threading.current_thread() and t.daemon and t.is_alive()
                 and t.name.startswith("Thread-")]
        if not alive:
            return
        time.sleep(0.02)


@pytest.fixture
def browser(tmp_path):
    """
    三张放在根目录的照片（根目录照片改星级不移动文件），星级依次 3/2/1。
    Three root-level photos rated 3/2/1 (root photos are never moved).
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    for name, rating in (("A", 3), ("B", 2), ("C", 1)):
        open(os.path.join(root, f"{name}.NEF"), "w").close()
        db.insert_photo({
            "filename": name, "current_path": f"{name}.NEF", "original_path": f"{name}.NEF",
            "bird_species_cn": "白喉抚蜜鸟", "bird_species_en": "White-throated Honeyeater",
            "rating": rating, "has_bird": 1,
        })
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    yield win
    _join_worker_threads()
    win.close()


def _use_filters(win, ratings, sort_by="filename") -> None:
    """固定筛选条件并重放 / Pin the filter and re-apply it."""
    filters = dict(win._filter_panel.get_filters())
    filters.update({"ratings": ratings, "sort_by": sort_by})
    win._filter_panel.get_filters = lambda: dict(filters)
    win._apply_filters(dict(filters))


def _names(win) -> list:
    return [p["filename"] for p in win._filtered_photos]


def _photo(win, name: str) -> dict:
    return next(p for p in win._filtered_photos if p["filename"] == name)


def _view(win, name: str) -> None:
    """模拟用户选中某张（网格绿框 + 详情面板）/ Simulate selecting a photo."""
    photo = _photo(win, name)
    win._thumb_grid.select_photo(photo)
    win._on_photo_selected(photo)
    _app.processEvents()


def test_photo_leaving_rating_filter_stays_until_user_moves_on(browser):
    """3★ 筛选下把 A 改成 2★：先留着；切到别的照片后才消失。"""
    _use_filters(browser, [2, 3])
    _view(browser, "A")
    browser._on_rating_changed(_photo(browser, "A"), 1)
    assert "A" in _names(browser), "改完立刻消失——连按 ↓ 的第二下会落到下一张"

    _view(browser, "B")
    assert _names(browser) == ["B"], "离开后仍留在不符合筛选的网格里（#167）"
    assert browser._thumb_grid._selected_key is not None
    assert browser._detail_panel._current_photo["filename"] == "B"


def test_repeated_rating_changes_stay_on_same_photo(browser):
    """连改两次（3★→2★→1★）都作用在 A 上，不会改到别的照片。"""
    _use_filters(browser, [3])
    _view(browser, "A")
    browser._on_rating_changed(_photo(browser, "A"), 2)
    _app.processEvents()
    browser._on_rating_changed(_photo(browser, "A"), 1)
    _app.processEvents()
    assert _photo(browser, "A")["rating"] == 1


def test_rating_sort_reorders_after_leaving(browser):
    """按星级排序：把 C 从 1★ 改到 3★，离开后排到 B 前面。"""
    _use_filters(browser, [1, 2, 3], sort_by="rating_desc")
    assert _names(browser) == ["A", "B", "C"]
    _view(browser, "C")
    browser._on_rating_changed(_photo(browser, "C"), 3)
    assert _names(browser) == ["A", "B", "C"], "改完立刻挪位"

    _view(browser, "B")
    assert _names(browser).index("C") < _names(browser).index("B"), "离开后没按新星级重排（#167）"


def test_change_within_filter_does_not_rebuild(browser):
    """新星级仍在筛选内、且不是按星级排序：不需要刷新。"""
    _use_filters(browser, [1, 2, 3])
    _view(browser, "A")
    browser._on_rating_changed(_photo(browser, "A"), 2)
    assert browser._rating_reflow_anchor is None


def test_fullscreen_change_applies_on_exit(browser):
    """全屏里改星级不动全屏列表；退出全屏回到网格时按新星级筛选。"""
    _use_filters(browser, [3])
    photo = _photo(browser, "A")
    browser._show_fullscreen_photo(photo)
    browser._stack.setCurrentIndex(1)
    browser._on_rating_changed(photo, 2)
    assert "A" in _names(browser)

    browser._exit_fullscreen()
    assert "A" not in _names(browser), "退出全屏后仍显示不符合筛选的照片（#167）"
