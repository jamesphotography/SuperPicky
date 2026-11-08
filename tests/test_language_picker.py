# -*- coding: utf-8 -*-
"""
界面语言设置测试（菜单栏「设置 → 语言」所用的配置与提示）。

2026-11 设置中心整合（spec D4）：界面语言只留在菜单栏，设置中心左栏底部的语言
下拉已移除，只测它的用例随之删除（「已移除」由 tests/test_settings_center.py
锁定）。这里保留的是菜单切换语言共用的部分：「跟随系统」存成 None、不支持的值
忽略、提示用目标语言书写。

配置一律注入临时文件，不碰用户真实的 advanced_config.json。

UI-language tests for the menu-bar Settings → Language path. The Settings
Center picker was removed (spec D4) along with its tests; what remains is the
shared config / notice behavior. Config is injected via a temp file.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tools.i18n import language_changed_notice

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def injected_config(tmp_path):
    """
    用临时文件替换全局配置单例，测试结束后清除。

    返回:
    tuple: (AdvancedConfig 实例, 配置文件路径)

    Swap the config singleton for a temp-file instance; cleared afterwards.
    """
    from advanced_config import AdvancedConfig
    from config import get_lazy_registry

    config_file = tmp_path / "advanced_config.json"
    cfg = AdvancedConfig(config_file=str(config_file))
    registry = get_lazy_registry()
    registry.set("advanced_config.instance", cfg)
    try:
        yield cfg, config_file
    finally:
        registry.clear("advanced_config.instance")


def test_set_language_accepts_none_and_rejects_unknown(injected_config) -> None:
    """None = 跟随系统；不支持的值忽略；旧值 'en' 兼容 / None, unknown and legacy values."""
    cfg, _ = injected_config
    cfg.set_language("zh_TW")
    assert cfg.language == "zh_TW"
    cfg.set_language("fr_FR")
    assert cfg.language == "zh_TW"
    cfg.set_language("en")
    assert cfg.language == "en_US"
    cfg.set_language(None)
    assert cfg.language is None


def test_notice_is_written_in_target_language() -> None:
    """提示用目标语言书写 / The notice uses the target language."""
    assert language_changed_notice("en_US")[0] == "Language Changed"
    assert language_changed_notice("zh_TW")[0] == "語言已變更"
    assert language_changed_notice("zh_CN")[0] == "语言已更改"
    title, msg = language_changed_notice(None)
    assert title and msg
