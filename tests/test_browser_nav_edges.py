# -*- coding: utf-8 -*-
"""
结果浏览器「上一张/下一张」首尾置灰回归测试（#137）。

- 详情面板的一对按钮跟随缩略图网格：首张禁用「上一张」，末张禁用「下一张」；
- 全屏底栏的一对按钮跟随全屏导航列表，规则相同；
- 删到列表为空时两对按钮都不可用。

Regression tests for greying out Previous/Next at either end (#137).
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

N_PHOTOS = 4


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """
    建一个真实 ResultsBrowserWindow：4 张 3★ 照片；删除确认直接通过、回收站换成直接删除。

    A real browser with 4 photos; delete confirmation auto-accepted and the
    trash replaced by a plain remove.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    for i in range(N_PHOTOS):
        stem = f"IMG_{i}"
        open(os.path.join(root, stem + ".CR3"), "w").close()
        db.insert_photo({
            "filename": stem, "current_path": stem + ".CR3", "original_path": stem + ".CR3",
            "rating": 3, "has_bird": 1,
        })
    db.close()

    def fake_trash(path: str) -> bool:
        """直接删除 / remove directly."""
        os.remove(path)
        return True

    monkeypatch.setattr(rbw, "_move_to_trash", fake_trash)
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete", lambda self, photos: True)

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    _wait_grid_built(win)
    yield win
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


def _detail_state(win) -> tuple:
    return win._detail_panel._prev_btn.isEnabled(), win._detail_panel._next_btn.isEnabled()


def _fullscreen_state(win) -> tuple:
    return win._fullscreen._prev_btn.isEnabled(), win._fullscreen._next_btn.isEnabled()


def test_detail_panel_buttons_follow_grid_position(browser):
    """网格模式：首张只能往后，中间两边都行，末张只能往前；按到头后状态不变。"""
    win = browser
    photos = win._filtered_photos
    assert len(photos) == N_PHOTOS

    win._thumb_grid.select_photo(photos[0])
    win._on_photo_selected(photos[0])
    assert _detail_state(win) == (False, True)

    win._next_photo()
    assert _detail_state(win) == (True, True)

    for _ in range(N_PHOTOS):   # 多按几次，到头后应停在末张
        win._next_photo()
    assert _detail_state(win) == (True, False)

    win._prev_photo()
    assert _detail_state(win) == (True, True)


def test_fullscreen_buttons_follow_fullscreen_nav_list(browser):
    """全屏模式：底栏按钮按全屏导航列表判断首尾。"""
    win = browser
    photos = win._filtered_photos

    win._enter_fullscreen(photos[0])
    assert _fullscreen_state(win) == (False, True)

    win._fullscreen_next()
    assert _fullscreen_state(win) == (True, True)

    win._enter_fullscreen(photos[-1])
    assert _fullscreen_state(win) == (True, False)

    win._fullscreen_prev()
    assert _fullscreen_state(win) == (True, True)


def test_detail_prev_next_in_fullscreen_updates_fullscreen_buttons(browser):
    """全屏下点详情面板的「下一张」换了大图，底栏按钮也要跟着更新。"""
    win = browser
    photos = win._filtered_photos

    win._enter_fullscreen(photos[-2])
    assert _fullscreen_state(win) == (True, True)
    win._next_photo()
    assert _fullscreen_state(win) == (True, False)


def test_buttons_disabled_when_everything_deleted(browser):
    """全部删光后，没有上一张也没有下一张。"""
    win = browser
    win._delete_photos(list(win._filtered_photos), in_fullscreen=False)
    assert win._filtered_photos == []
    assert _detail_state(win) == (False, False)
