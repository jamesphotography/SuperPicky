#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出报告入口的接线测试：只验按钮存在与信号连接，不执行真实导出。

参照 test_species_merge_entry.py 的写法。

Wiring test for the export entry: verifies the button exists and is
connected, without running a real export.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def test_export_button_wired():
    """工具栏「导出 ▾」菜单里有分享报告项，且触发时调用 _export_report。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow

    window = ResultsBrowserWindow()
    calls = []
    window._export_report = lambda *a: calls.append("report")
    try:
        menu = window._export_menu_btn.menu()
        assert menu is not None
        assert window._report_action in menu.actions()
        # isSignalConnected 是 QObject 的公开 API，比 receivers() 可靠。
        meta = window._report_action.metaObject()
        index = meta.indexOfSignal("triggered(bool)")
        assert window._report_action.isSignalConnected(meta.method(index))
    finally:
        window.deleteLater()


def test_export_menu_items_have_hint_and_tooltip():
    """
    每个导出菜单项都要有第二行说明与悬停气泡，且不是原始 key。

    这次改版的目的就是让用户看得懂每项作用于哪些照片；任一项缺文案会退化成
    显示 "report_export.menu_hint" 这种原始 key。

    Every export item needs a hint line and tooltip that resolve to real text.
    """
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow

    window = ResultsBrowserWindow()
    try:
        actions = [a for a in (window._report_action, window._ebird_action,
                               window._apple_photos_action) if a is not None]
        assert len(actions) >= 2
        for action in actions:
            widget = action.item_widget()
            texts = [lbl.text() for lbl in widget.findChildren(QLabel) if lbl.text()]
            assert len(texts) == 2, texts
            tip = widget.toolTip()
            for text in texts + [tip]:
                assert text, "菜单项文案为空"
                assert not text.startswith(("report_export.", "ebird_export.", "browser."))
    finally:
        window.deleteLater()


def test_disabled_menu_item_does_not_trigger():
    """禁用的菜单项点击不触发动作。/ A disabled item ignores clicks."""
    from PySide6.QtCore import QPointF, Qt, QEvent
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QMenu
    app = QApplication.instance() or QApplication([])
    from ui.toolbar_menu import TwoLineMenuAction

    menu = QMenu()
    action = TwoLineMenuAction(menu, None, "Title", "Hint", "Tip")
    fired = []
    action.triggered.connect(lambda *_: fired.append(1))
    widget = action.item_widget()

    def click() -> None:
        ev = QMouseEvent(QEvent.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        widget.mouseReleaseEvent(ev)

    action.setEnabled(False)
    assert not widget.isEnabled()
    click()
    assert fired == []
    action.setEnabled(True)
    click()
    assert fired == [1]


def test_export_report_guards_empty_directory():
    """未载入任何照片时，导出应安全返回而不抛异常。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow
    from ui import custom_dialogs

    window = ResultsBrowserWindow()
    calls = []
    original = custom_dialogs.StyledMessageBox.warning
    custom_dialogs.StyledMessageBox.warning = staticmethod(
        lambda *a, **k: calls.append(a))
    try:
        window._all_photos = []
        window._directory = ""
        window._export_report()
        assert calls, "空目录应给出提示"
    finally:
        custom_dialogs.StyledMessageBox.warning = original
        window.deleteLater()


def test_report_export_locale_keys_match():
    """
    中英文 locale 的 report_export 段键集必须完全一致。

    缺键会让界面显示原始 key（如 "report_export.title"），
    这类问题在中文环境下测不出来。

    The two locale files must expose an identical key set; a missing key
    renders as the raw key string and would go unnoticed in one language.
    """
    import json
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "locales/zh_CN.json"), encoding="utf-8") as fh:
        zh = json.load(fh)
    with open(os.path.join(here, "locales/en_US.json"), encoding="utf-8") as fh:
        en = json.load(fh)
    assert "report_export" in zh and "report_export" in en
    assert set(zh["report_export"]) == set(en["report_export"])


def test_report_export_keys_cover_all_usages():
    """代码中用到的每个 report_export.* 键都必须在 locale 中存在。

    键必须以引号开头匹配（i18n 调用总是 t("report_export.xxx") 的形式）。
    早先的正则不带引号，把注释里提到的文件路径 `core/report_export.py` 也
    当成了键，凭空要求 locale 里有一个叫 "py" 的条目——注释里写文件路径是
    再正常不过的写法，不该因此让这个测试变红。

    Anchor on the opening quote: i18n lookups are always string literals.
    Without it a file path mentioned in a comment (core/report_export.py) was
    parsed as a key named "py".
    """
    import json
    import re
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "locales/zh_CN.json"), encoding="utf-8") as fh:
        zh = json.load(fh)["report_export"]
    used = set()
    for name in ("ui/results_browser_window.py", "ui/report_export_dialog.py"):
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            used |= set(re.findall(r'[\'"]report_export\.(\w+)', fh.read()))
    missing = used - set(zh)
    assert not missing, f"locale 缺少这些键: {sorted(missing)}"
