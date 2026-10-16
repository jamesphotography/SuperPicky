# -*- coding: utf-8 -*-
"""
#128 回归测试：Ctrl(macOS 为 Command)+1…9 用第 1…9 个外部应用打开当前照片。

- 打开的是当前照片、对应序号的应用；
- Ctrl+数字不能再被打星分支当成打星（打星分支不看修饰键）；
- 右键菜单的「用 XX 打开」显示对应快捷键。

#128: Ctrl/Cmd+1…9 opens the current photo with external app 1…9.
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
    open(os.path.join(root, "IMG_1.CR3"), "w").close()
    db = ReportDB(root)
    db.insert_photo({"filename": "IMG_1", "current_path": "IMG_1.CR3",
                     "original_path": "IMG_1.CR3", "rating": 2, "has_bird": 1})
    db.close()

    monkeypatch.setattr(ac.AdvancedConfig, "get_external_apps", lambda self: list(APPS))
    opened = []
    monkeypatch.setattr(rbw, "_open_with_external_app", lambda fp, ap: opened.append((fp, ap)))

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win._detail_panel.show_photo(win._filtered_photos[0])
    yield win, root, opened
    win.close()


def _press(win, key, mods=Qt.ControlModifier) -> None:
    win.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, mods))


def test_ctrl_number_opens_current_photo_with_that_app(browser):
    """Ctrl+2 = 第 2 个有效应用（不完整的那条不占编号）。/ Ctrl+2 = 2nd valid app."""
    win, root, opened = browser
    _press(win, Qt.Key_2)
    assert opened == [(os.path.join(root, "IMG_1.CR3"), "/Applications/DxO PureRAW.app")]


def test_ctrl_number_beyond_configured_apps_does_nothing(browser):
    win, _, opened = browser
    _press(win, Qt.Key_5)
    assert opened == []


def test_ctrl_number_does_not_change_rating(browser):
    """Ctrl+1 曾落进打星分支被当成打 1 星。/ Ctrl+1 used to set a 1-star rating."""
    win, _, _ = browser
    _press(win, Qt.Key_1)
    assert win._filtered_photos[0]["rating"] == 2


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
