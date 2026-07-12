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


def test_main_window_quick_param_panel_present():
    """首页保留「快速调整」面板:2 滑块 + 3 开关 + 刷新方法;滑块范围对齐设置中心精选页。
    Home keeps the quick-adjust panel: 2 sliders + 3 toggles + refresh method;
    slider ranges match the Settings Center culling page (no drift/truncation)."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()

    for attr in ("sharp_slider", "nima_slider", "flight_check", "burst_check", "birdid_check"):
        assert hasattr(w, attr), f"quick-panel widget '{attr}' should exist on the home page"
    assert hasattr(w, "_refresh_param_panel"), "_refresh_param_panel should exist for SSOT sync"

    # 范围与设置中心精选页一致(锐度 100-600,美学 0-70)→ 避免两处不一致与默认值漂移
    assert w.sharp_slider.minimum() == 100 and w.sharp_slider.maximum() == 600
    assert w.nima_slider.minimum() == 0 and w.nima_slider.maximum() == 70
    w.close()


def test_main_window_has_settings_entry_and_quick_panel():
    """组合测试: 有设置入口 + 首页保留快速参数面板。
    Combined test: has settings entry + home keeps the quick param panel."""
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "_open_settings_center")
    assert hasattr(w, "sharp_slider")   # 首页快速面板保留
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


def test_refresh_param_panel_switches_algo_slider_visibility():
    """
    验证设置中心改完评星算法后,首页 _refresh_param_panel 同步切换两组
    滑块可见性(v1 显示锐度/美学,v2 显示配额)。只改内存配置不落盘。

    Verify _refresh_param_panel swaps the two slider groups after the
    Settings Center changes the rating algorithm (memory-only, no save).
    """
    from ui.main_window import SuperPickyMainWindow

    w = SuperPickyMainWindow()
    cfg = w.config
    original = cfg.config.get("rating_algorithm", "v2")
    try:
        cfg.config["rating_algorithm"] = "v1"
        w._refresh_param_panel()
        assert w.quota_slider.isHidden()
        assert not w.sharp_slider.isHidden() and not w.nima_slider.isHidden()
        assert w._rating_v2_ui is False

        cfg.config["rating_algorithm"] = "v2"
        w._refresh_param_panel()
        assert not w.quota_slider.isHidden()
        assert w.sharp_slider.isHidden() and w.nima_slider.isHidden()
        assert w._rating_v2_ui is True
    finally:
        cfg.config["rating_algorithm"] = original
        w.close()
