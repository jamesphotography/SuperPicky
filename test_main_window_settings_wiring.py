# -*- coding: utf-8 -*-
"""
Task 7: 主窗口接线设置中心 — 失败/通过测试

测试目标:
1. 主窗口有 _open_settings_center 方法
2. 主窗口没有旧参数面板控件（sharp_slider / nima_slider / flight_check / burst_check / birdid_check）
3. ui_settings 读取 advanced_config，不读取控件
4. skill chip 可点击打开设置中心（_open_settings_center）

Task 7: Main window settings center wiring — fail/pass tests.

Test objectives:
1. Main window has _open_settings_center method
2. Main window does NOT have old parameter panel widgets
3. ui_settings reads from advanced_config, not from widgets
4. Skill chip is clickable and opens settings center
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


def test_main_window_has_open_settings_center():
    """主窗口应有 _open_settings_center 方法。
    Main window should have the _open_settings_center method."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "_open_settings_center"), (
        "_open_settings_center method should exist on SuperPickyMainWindow"
    )
    w.close()


def test_main_window_no_param_panel_widgets():
    """参数面板控件（滑块/复选框）应被移除。
    Parameter panel widgets (sliders/checkboxes) should be removed."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()

    # 旧参数面板控件，Task 7 完成后不应存在
    # Old parameter panel widgets — must NOT exist after Task 7
    deleted_widgets = [
        "sharp_slider",
        "nima_slider",
        "flight_check",
        "burst_check",
        "birdid_check",
    ]
    for attr in deleted_widgets:
        assert not hasattr(w, attr), (
            f"Widget '{attr}' should have been removed with the parameter panel"
        )
    w.close()


def test_main_window_has_settings_entry_and_no_param_panel():
    """组合测试: 有设置入口 + 无参数面板。
    Combined test: has settings entry + no param panel."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "_open_settings_center")
    assert not hasattr(w, "sharp_slider")   # 参数面板已移除
    w.close()


def test_skill_level_label_exists():
    """技能水平标签（chip）应保留，作为只读展示。
    Skill level label (chip) should be kept as a read-only display."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "skill_level_label"), (
        "skill_level_label should still exist as a read-only chip"
    )
    w.close()


def test_refresh_skill_chip_method_exists():
    """主窗口应有 _refresh_skill_chip 方法，供设置中心关闭后刷新 chip。
    Main window should have _refresh_skill_chip for post-settings-center refresh."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "_refresh_skill_chip"), (
        "_refresh_skill_chip method should exist for chip refresh"
    )
    w.close()
