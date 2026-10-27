# -*- coding: utf-8 -*-
"""
#128 回归测试：Ctrl(macOS 为 Command)+1…9 用第 1…9 个外部应用打开当前照片。

- 打开的是当前照片、对应序号的应用；
- Ctrl+数字不能再被打星分支当成打星（打星分支不看修饰键）；
- 右键菜单的「用 XX 打开」显示对应快捷键；
- 多选时右键与快捷键都把整个勾选集一次交给外部应用（用户反馈：选多少张都只开一张）；
- 超过 20 张先确认；Windows 命令行超长时拆批。

#128: Ctrl/Cmd+1…9 opens the current photo with external app 1…9. Multi-
selection now opens every checked photo at once (confirm above 20; Windows
command lines are batched).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

APPS = [
    {"name": "Photoshop", "path": "/Applications/Adobe Photoshop.app"},
    {"name": "", "path": "/Applications/Broken.app"},          # 不完整，跳过 / skipped
    {"name": "DxO", "path": "/Applications/DxO PureRAW.app"},
]


def _wait_for_grid(win, timeout: float = 3.0) -> None:
    """
    等网格建好：load_photos 用 50ms 定时器延迟构建，之前 _photos 为空、勾选集取不到。

    Wait for the deferred grid build; the checked set is empty until then.
    """
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
    一张照片的真实浏览器窗口；外部应用列表与「打开」动作都换成假的。

    A real browser with one photo; the app list and the launcher are faked.
    """
    import advanced_config as ac
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    for i in (1, 2, 3):
        open(os.path.join(root, f"IMG_{i}.CR3"), "w").close()
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.CR3",
                         "original_path": f"IMG_{i}.CR3", "rating": 2, "has_bird": 1})
    db.close()

    monkeypatch.setattr(ac.AdvancedConfig, "get_external_apps", lambda self: list(APPS))
    opened = []
    monkeypatch.setattr(rbw, "_open_with_external_app", lambda fps, ap: opened.append((list(fps), ap)))

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    _wait_for_grid(win)
    win._detail_panel.show_photo(win._filtered_photos[0])
    yield win, root, opened
    win.close()


def _press(win, key, mods=Qt.ControlModifier) -> None:
    win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, mods))


def test_ctrl_number_opens_current_photo_with_that_app(browser):
    """Ctrl+2 = 第 2 个有效应用（不完整的那条不占编号）。/ Ctrl+2 = 2nd valid app."""
    win, root, opened = browser
    _press(win, Qt.Key_2)
    first = win._filtered_photos[0]["filename"]
    assert opened == [([os.path.join(root, f"{first}.CR3")], "/Applications/DxO PureRAW.app")]


def test_ctrl_number_beyond_configured_apps_does_nothing(browser):
    win, _, opened = browser
    _press(win, Qt.Key_5)
    assert opened == []


def test_ctrl_number_does_not_change_rating(browser):
    """Ctrl+1 曾落进打星分支被当成打 1 星。/ Ctrl+1 used to set a 1-star rating."""
    win, _, _ = browser
    _press(win, Qt.Key_1)
    assert all(p["rating"] == 2 for p in win._filtered_photos)


def test_context_menu_shows_shortcut_hints(browser):
    """右键菜单的「用 XX 打开」带 Ctrl+1 / Ctrl+2 快捷键提示。"""
    from PySide6.QtGui import QKeySequence
    from ui.results_browser_window import _build_context_menu
    from tools.i18n import get_i18n
    win, root, _ = browser
    i18n = get_i18n()
    menu = _build_context_menu(win, win._filtered_photos[0], root)
    shortcuts = {a.text(): a.shortcut() for a in menu.actions()}
    assert shortcuts[i18n.t("browser.ctx_open_with").format(app_name="Photoshop")] == QKeySequence("Ctrl+1")
    assert shortcuts[i18n.t("browser.ctx_open_with").format(app_name="DxO")] == QKeySequence("Ctrl+2")


def _paths(root, photos) -> list:
    return [os.path.join(root, f"{p['filename']}.CR3") for p in photos]


def test_ctrl_number_opens_all_checked_photos_at_once(browser):
    """网格勾选多张时 Ctrl+1 一次打开全部勾选的照片。/ Opens every checked photo."""
    win, root, opened = browser
    win._thumb_grid.select_all()
    _press(win, Qt.Key_1)
    assert opened == [(_paths(root, win._filtered_photos), "/Applications/Adobe Photoshop.app")]


def test_context_menu_opens_checked_set_with_count_label(browser):
    """右键点中勾选集里的照片：文案带张数，触发后打开整个勾选集。"""
    from ui.results_browser_window import _build_context_menu
    from tools.i18n import get_i18n
    win, root, opened = browser
    win._thumb_grid.select_all()
    label = get_i18n().t("browser.ctx_open_with_batch").format(app_name="DxO", count=3)
    menu = _build_context_menu(win, win._filtered_photos[0], root)
    action = next(a for a in menu.actions() if a.text() == label)
    action.trigger()
    assert opened == [(_paths(root, win._filtered_photos), "/Applications/DxO PureRAW.app")]


def test_context_menu_on_unchecked_photo_opens_only_that_photo(browser):
    """右键点中的照片不在勾选集里时只开这一张（与修改鸟种 / 删除同规则）。"""
    from ui.results_browser_window import _build_context_menu
    from tools.i18n import get_i18n
    win, root, opened = browser
    photos = win._filtered_photos
    win._thumb_grid.select_all()
    win._thumb_grid._multi_selected.discard(photos[2]["filename"])
    label = get_i18n().t("browser.ctx_open_with").format(app_name="Photoshop")
    menu = _build_context_menu(win, photos[2], root)
    next(a for a in menu.actions() if a.text() == label).trigger()
    assert opened == [(_paths(root, [photos[2]]), "/Applications/Adobe Photoshop.app")]


def test_many_files_ask_first_and_cancel_opens_nothing(browser, monkeypatch):
    """超过门槛先确认；选「否」什么都不开。/ Above the threshold, No opens nothing."""
    import ui.results_browser_window as rbw
    win, _, opened = browser
    monkeypatch.setattr(rbw, "_EXTERNAL_OPEN_CONFIRM_THRESHOLD", 2)
    asked = []
    monkeypatch.setattr(rbw.QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or rbw.QMessageBox.No)
    win._thumb_grid.select_all()
    _press(win, Qt.Key_1)
    assert len(asked) == 1 and opened == []


def test_few_files_do_not_ask(browser, monkeypatch):
    import ui.results_browser_window as rbw
    win, _, opened = browser
    monkeypatch.setattr(rbw.QMessageBox, "question",
                        lambda *a, **k: pytest.fail("不该弹确认框 / should not ask"))
    win._thumb_grid.select_all()
    _press(win, Qt.Key_1)
    assert len(opened) == 1


def test_windows_cmdline_batches_stay_within_budget():
    """拆批：每批不超预算、顺序不变、一个不丢；单个超长路径独占一批。"""
    from ui.results_browser_window import _chunk_paths_for_cmdline
    app = "C:\\Program Files\\App\\app.exe"
    paths = [f"D:\\照片\\鸟\\IMG_{i:05d}.CR3" for i in range(500)]
    batches = _chunk_paths_for_cmdline(paths, app, budget=1000)
    assert len(batches) > 1
    assert [p for b in batches for p in b] == paths
    for b in batches:
        assert len(app) + 3 + sum(len(p) + 3 for p in b) <= 1000
    huge = "X" * 2000
    assert _chunk_paths_for_cmdline(["a", huge, "b"], app, budget=1000) == [["a"], [huge], ["b"]]
