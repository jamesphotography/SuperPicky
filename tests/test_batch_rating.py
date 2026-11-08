# -*- coding: utf-8 -*-
"""
结果浏览器批量改星级测试。

勾选多张后，数字键 / Up、Down / 详情面板 ▲▼ / 右键「设置星级」作用于整个勾选集；
没勾选时只改当前这张。星级真的会变的照片超过 5 张先确认一次。
用途：保护某种罕见鸟的 1★ 照片——筛出它、全选、按 2，再放心批量删 1★。

Batch rating in the results browser: with several photos checked, digit
keys, Up/Down, the panel's up/down buttons and the context-menu "Set
Rating" apply to the whole checked set; more than 5 changes ask first.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _wait_for_grid(win, timeout: float = 3.0) -> None:
    """等网格延迟构建完成 / Wait for the deferred grid build."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if win._thumb_grid._photos:
            QApplication.processEvents()
            return
        time.sleep(0.02)


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """
    8 张 1★ 照片的真实浏览器；移动文件 / 写元数据与确认框都换成假的。
    A real browser with eight 1-star photos; file moves, metadata writes and
    the confirm dialog are faked.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB
    from ui.custom_dialogs import StyledMessageBox

    root = str(tmp_path)
    db = ReportDB(root)
    for i in range(1, 9):
        open(os.path.join(root, f"IMG_{i}.CR3"), "w").close()
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.CR3",
                         "original_path": f"IMG_{i}.CR3", "rating": 1, "has_bird": 1,
                         "focus_status": "BEST"})
    db.close()

    moves = []
    monkeypatch.setattr(rbw, "_trigger_rating_move",
                        lambda base, photo, new, *a, **k: moves.append((photo["filename"], new)))
    asked = {"count": 0, "answer": StyledMessageBox.Yes, "body": ""}

    def _question(parent, title, body, *a, **k):
        asked["count"] += 1
        asked["body"] = body
        return asked["answer"]

    monkeypatch.setattr(StyledMessageBox, "question", staticmethod(_question))

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    _wait_for_grid(win)
    grid = win._thumb_grid
    first = grid._photos[0]
    grid._handle_card_click(first, Qt.NoModifier)
    win._detail_panel.show_photo(first)
    yield win, moves, asked
    win.close()


def _check(win, n: int) -> list:
    """单击第 1 张后 Ctrl+点击到第 n 张，返回勾选的照片 / Check the first n photos."""
    grid = win._thumb_grid
    for p in grid._photos[1:n]:
        grid._handle_card_click(p, Qt.ControlModifier)
    return grid.get_explicitly_selected_photos()


def _press(win, key) -> None:
    win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.NoModifier))


def _db_ratings(win) -> dict:
    return {p["filename"]: p["rating"] for p in win._db.get_photos_by_filters({})}


def test_batch_rating_changes_skips_noops():
    """纯函数：数字直设、Up 各张 +1、已是目标星级的跳过。"""
    from ui.results_browser_window import _batch_rating_changes
    photos = [{"rating": 1}, {"rating": 2}, {"rating": 5}]
    assert [r for _, r in _batch_rating_changes(photos, key=Qt.Key_2)] == [2, 2]
    assert [r for _, r in _batch_rating_changes(photos, key=Qt.Key_Up)] == [2, 3]
    assert [r for _, r in _batch_rating_changes(photos, stars=5)] == [5, 5]


def test_digit_key_rates_whole_selection_without_confirm(browser):
    """勾 3 张按 2：3 张都变 2★，不弹确认；其余照片不动。"""
    win, moves, asked = browser
    checked = {p["filename"] for p in _check(win, 3)}
    _press(win, Qt.Key_2)
    ratings = _db_ratings(win)
    assert {f for f, r in ratings.items() if r == 2} == checked
    assert sorted(f for f, _ in moves) == sorted(checked)
    assert asked["count"] == 0


def test_more_than_five_asks_and_cancel_changes_nothing(browser):
    """勾 7 张按 3：先确认；选「否」一张都不改。"""
    from ui.custom_dialogs import StyledMessageBox
    win, moves, asked = browser
    _check(win, 7)
    asked["answer"] = StyledMessageBox.No
    _press(win, Qt.Key_3)
    assert asked["count"] == 1 and "7" in asked["body"]
    assert set(_db_ratings(win).values()) == {1}
    assert moves == []


def test_more_than_five_confirmed_changes_all(browser):
    """勾 6 张按 0（降星）：确认后 6 张都变 0★。"""
    win, moves, asked = browser
    checked = {p["filename"] for p in _check(win, 6)}
    _press(win, Qt.Key_0)
    assert asked["count"] == 1
    ratings = _db_ratings(win)
    assert {f for f, r in ratings.items() if r == 0} == checked


def test_up_key_steps_each_photo(browser):
    """Up：每张各自 +1（已手动改成 3★ 的那张变 4★，其余 1★→2★）。"""
    win, _, _ = browser
    win._on_rating_changed(win._thumb_grid._photos[1], 3)
    _check(win, 3)
    _press(win, Qt.Key_Up)
    names = [p["filename"] for p in win._thumb_grid._photos[:3]]
    ratings = _db_ratings(win)
    assert [ratings[n] for n in names] == [2, 4, 2]


def test_panel_up_button_steps_once_per_photo(browser):
    """
    详情面板 ▲：面板先就地改了当前这张再发信号，批量时这张不能被多升一次。
    Panel up: the panel mutates its photo first; it must not be raised twice.
    """
    win, _, _ = browser
    _check(win, 3)
    win._detail_panel._on_rating_inc()
    names = [p["filename"] for p in win._thumb_grid._photos[:3]]
    ratings = _db_ratings(win)
    assert [ratings[n] for n in names] == [2, 2, 2]


def test_without_selection_only_current_photo_changes(browser):
    """没有勾选：数字键只改当前这张（原有行为）。"""
    win, _, asked = browser
    _press(win, Qt.Key_4)
    ratings = _db_ratings(win)
    current = win._thumb_grid._photos[0]["filename"]
    assert ratings[current] == 4
    assert sum(1 for r in ratings.values() if r == 4) == 1
    assert asked["count"] == 0


def test_context_menu_set_rating_for_selection(browser):
    """右键「设置选中 3 张的星级」→ 2★：3 张都变 2★。"""
    from ui.results_browser_window import _build_context_menu
    from tools.i18n import get_i18n
    win, _, _ = browser
    checked = _check(win, 3)
    i18n = get_i18n()
    menu = _build_context_menu(win, checked[0], win._directory)
    label = i18n.t("browser.ctx_set_rating_batch").format(count=3)
    sub = next(a.menu() for a in menu.actions() if a.text() == label)
    next(a for a in sub.actions() if a.text() == "2★").trigger()
    ratings = _db_ratings(win)
    assert [ratings[p["filename"]] for p in checked] == [2, 2, 2]


def test_context_menu_single_photo(browser):
    """没有勾选时右键「设置星级」只改点中的这张。"""
    from ui.results_browser_window import _build_context_menu
    from tools.i18n import get_i18n
    win, _, _ = browser
    target = win._thumb_grid._photos[4]
    menu = _build_context_menu(win, target, win._directory)
    sub = next(a.menu() for a in menu.actions()
               if a.text() == get_i18n().t("browser.ctx_set_rating"))
    next(a for a in sub.actions() if a.text() == "5★").trigger()
    ratings = _db_ratings(win)
    assert ratings[target["filename"]] == 5
    assert sum(1 for r in ratings.values() if r == 5) == 1
