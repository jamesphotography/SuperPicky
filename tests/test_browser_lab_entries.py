# -*- coding: utf-8 -*-
"""
结果浏览器导出菜单：报告 / eBird 入口受实验室开关控制。
Results browser export menu: report / eBird entries follow the Labs switches.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from advanced_config import get_advanced_config

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def browser():
    """空的真实浏览器窗口，两个实验室开关复位为关 / bare browser, both flags off."""
    import ui.results_browser_window as rbw
    cfg = get_advanced_config()
    cfg.set_lab_report_export(False)
    cfg.set_lab_ebird_export(False)
    win = rbw.ResultsBrowserWindow()
    yield win
    cfg.set_lab_report_export(False)
    cfg.set_lab_ebird_export(False)
    win.close()


def test_entries_hidden_by_default(browser):
    browser._refresh_lab_entries()
    assert not browser._report_action.isVisible() and not browser._ebird_action.isVisible()


def test_entries_follow_flags(browser):
    get_advanced_config().set_lab_ebird_export(True)
    browser._refresh_lab_entries()
    assert browser._ebird_action.isVisible() and not browser._report_action.isVisible()


def test_export_button_hidden_when_menu_empty(browser):
    """没有任何可见导出项时整个按钮隐藏 / hide the button when no item is visible."""
    if getattr(browser, "_apple_photos_action", None) is not None:
        browser._apple_photos_action.setVisible(False)  # 模拟 Windows / simulate Windows
    browser._refresh_lab_entries()
    assert browser._export_menu_btn.isHidden()
    get_advanced_config().set_lab_report_export(True)
    browser._refresh_lab_entries()
    assert not browser._export_menu_btn.isHidden()
