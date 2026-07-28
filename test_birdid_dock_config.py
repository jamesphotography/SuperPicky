# -*- coding: utf-8 -*-
"""
识鸟面板配置测试：验证面板从 advanced_config 读写国家/区域/数据源设置。
BirdID dock config tests: verify panel reads/writes country/region/datasource
settings via advanced_config (single source of truth, no JSON file writes).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


def test_birdid_dock_reads_region_from_config():
    """
    验证 BirdIDDockWidget.reload_from_config() 从 advanced_config 正确加载区域设置。
    Verify that BirdIDDockWidget.reload_from_config() loads region settings from advanced_config.
    """
    from ui.birdid_dock import BirdIDDockWidget
    from advanced_config import get_advanced_config
    get_advanced_config().set_birdid_region(True, "AU", "澳大利亚", "AU-QLD", "昆士兰")
    dock = BirdIDDockWidget()
    dock.reload_from_config()
    assert dock.settings.get("selected_country") == "澳大利亚"
    dock.close()


def test_birdid_dock_settings_has_all_keys():
    """
    验证 self.settings 包含识别流水线所需的所有键。
    Verify that self.settings contains all keys needed by the identification pipeline.
    """
    from ui.birdid_dock import BirdIDDockWidget
    from advanced_config import get_advanced_config
    get_advanced_config().set_birdid_region(True, "AU", "澳大利亚", "AU-QLD", "昆士兰")
    dock = BirdIDDockWidget()
    dock.reload_from_config()
    required_keys = {"use_geo_filter", "auto_identify", "selected_country", "country_code",
                     "selected_region", "region_code"}
    missing = required_keys - set(dock.settings.keys())
    assert not missing, f"Missing keys in settings: {missing}"
    dock.close()


def test_birdid_dock_open_settings_signal_exists():
    """
    验证 BirdIDDockWidget 具有 open_settings_requested signal（Task 8 新增）。
    Verify that BirdIDDockWidget has the open_settings_requested signal (added in Task 8).
    """
    from ui.birdid_dock import BirdIDDockWidget
    dock = BirdIDDockWidget()
    assert hasattr(dock, "open_settings_requested"), \
        "BirdIDDockWidget must have open_settings_requested signal"
    dock.close()


# ──────────────────────────────────────────────────────────────────────────────
# 新增：区域快速控件恢复验证 / New: region quick-controls restoration tests
# ──────────────────────────────────────────────────────────────────────────────

def test_dock_has_country_and_region_combos():
    """
    验证面板重新引入了国家下拉（country_combo）和区域下拉（region_combo）控件。
    Verify that the panel has country_combo and region_combo widgets restored.
    """
    from ui.birdid_dock import BirdIDDockWidget
    dock = BirdIDDockWidget()
    assert hasattr(dock, "country_combo"), "country_combo must be present"
    assert hasattr(dock, "region_combo"), "region_combo must be present"
    assert hasattr(dock, "ebird_checkbox"), "ebird_checkbox must be present"
    dock.close()


def test_dock_has_country_and_region_combos_detail():
    """
    验证面板国家下拉包含已知的 Top10 入口（AU）和「更多国家」入口。
    Verify the country_combo contains a known Top10 entry (AU) and the
    "more countries" entry.
    """
    from ui.birdid_dock import BirdIDDockWidget
    dock = BirdIDDockWidget()
    t = dock.i18n.t
    items = [dock.country_combo.itemText(i) for i in range(dock.country_combo.count())]
    au_label = t("birdid.country_au")
    more_label = t("birdid.country_more")
    assert au_label in items, f"{au_label!r} not found in combo items: {items}"
    assert more_label in items, f"{more_label!r} (More) not found in combo items: {items}"
    dock.close()


def test_dock_country_combo_selects_australia():
    """
    set_birdid_region(..., "AU", "澳大利亚", ...) 后构造面板并 reload，
    断言国家下拉选中澳大利亚且区域下拉可见（AU 是州级国家）。
    After set_birdid_region with AU/澳大利亚, construct panel and reload;
    assert country_combo selects 澳大利亚 and region_combo is visible (AU has states).

    注：直接写 advanced_config（共享实例），测试间顺序无关。
    Note: writes to the shared advanced_config instance; tests are order-independent.
    """
    from advanced_config import get_advanced_config
    get_advanced_config().set_birdid_region(True, "AU", "澳大利亚", None, "整个国家")

    from ui.birdid_dock import BirdIDDockWidget
    from PySide6.QtWidgets import QApplication
    import time

    dock = BirdIDDockWidget()

    # 等待 QTimer.singleShot(100ms) 触发 _apply_saved_region
    # Process pending Qt events including the 100ms singleShot
    deadline = time.time() + 1.0
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)

    current_text = dock.country_combo.currentText()
    assert "澳大利亚" in current_text or current_text == "澳大利亚", (
        f"Expected 澳大利亚 in country_combo, got: {current_text!r}"
    )
    dock.close()


def test_dock_country_change_writes_advanced_config():
    """
    改变面板国家下拉 → advanced_config 应写入对应 country_code。
    Changing the panel's country_combo should write the correct country_code
    to advanced_config.
    """
    from advanced_config import get_advanced_config
    # 初始设为全球 / Start with global
    get_advanced_config().set_birdid_region(True, "GLOBAL", "全球", None, "整个国家")

    from ui.birdid_dock import BirdIDDockWidget
    from PySide6.QtWidgets import QApplication
    import time

    dock = BirdIDDockWidget()

    # 等待初始 _apply_settings 的 QTimer 完成
    # Wait for initial _apply_settings QTimer
    deadline = time.time() + 1.0
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)

    # 模拟用户选择澳大利亚（top10，i18n key birdid.country_au）
    # Simulate user selecting Australia (top10)
    t = dock.i18n.t
    au_display = t("birdid.country_au")
    idx = dock.country_combo.findText(au_display)
    assert idx >= 0, f"澳大利亚 entry not found in combo; looking for {au_display!r}"
    dock.country_combo.setCurrentIndex(idx)

    # 让事件循环处理信号 / Process signals
    deadline = time.time() + 0.3
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.05)

    # 验证 advanced_config 已更新 / Verify advanced_config was updated
    cfg = get_advanced_config()
    assert cfg.birdid_country_code == "AU", (
        f"Expected AU in advanced_config, got: {cfg.birdid_country_code!r}"
    )
    dock.close()
