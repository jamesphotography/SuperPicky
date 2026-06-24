# -*- coding: utf-8 -*-
"""
SettingsCenter 骨架测试 — Task 2 TDD

测试左侧导航 QListWidget 6 项 + 右侧 QStackedWidget 页切换。

SettingsCenter skeleton tests — Task 2 TDD

Tests the left-side QListWidget with 6 items + right-side QStackedWidget page switching.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_nav_has_six_pages_and_switch():
    """
    验证导航列表有 6 个分页，且 show_page 能正确切换 stack。

    Verify the nav list has 6 pages and show_page correctly switches the stack.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    w = SettingsCenter(get_i18n())
    assert PAGE_ORDER == ["culling", "birdid", "output", "video", "apps", "about"]
    assert w._nav.count() == 6
    w.show_page("about")
    assert w._stack.currentIndex() == PAGE_ORDER.index("about")
    w.close()
