# -*- coding: utf-8 -*-
"""
Task 8 TDD 测试：识鸟面板从 advanced_config 读取配置。
Task 8 TDD test: BirdID dock reads configuration from advanced_config.
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
    required_keys = {"use_ebird", "auto_identify", "selected_country", "country_code",
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
