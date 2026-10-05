# -*- coding: utf-8 -*-
"""
设置中心「界面语言」选择测试。

背景：英文系统的用户想用中文界面，却找不到菜单栏里的语言子菜单，于是在设置中心
左栏底部常驻一个语言下拉。这里验证：选项齐全且用各语言自己的文字书写、选择即落盘、
「跟随系统」存成 None、提示用目标语言书写。

配置一律注入临时文件，不碰用户真实的 advanced_config.json。

Tests for the Settings Center UI-language picker: options use native names,
choosing persists immediately, "Follow system" stores None, and the restart
notice is written in the target language. Config is injected via a temp file.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tools.i18n import get_i18n, language_changed_notice

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


@pytest.fixture
def notices(monkeypatch):
    """
    截获设置中心弹出的提示框（离屏测试不能真的弹模态框）。

    返回:
    list: 每次弹框的 (标题, 正文)

    Capture the notice boxes instead of showing modal dialogs.
    """
    import ui.settings_center as sc

    shown = []
    monkeypatch.setattr(sc.StyledMessageBox, "information",
                        staticmethod(lambda _parent, title, msg, *a, **k: shown.append((title, msg))))
    return shown


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


def test_picker_options_and_persistence(injected_config, notices) -> None:
    """选项齐全、选择即落盘、跟随系统存 None / Options, persistence and auto."""
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter

    cfg, config_file = injected_config
    w = SettingsCenter(get_i18n())
    try:
        combo = w._lang_combo
        data = [combo.itemData(i) for i in range(combo.count())]
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert data == [None, "zh_CN", "zh_TW", "en_US"]
        # 各语言用自己的文字书写，看不懂当前界面的人也认得出
        assert texts[1:] == ["简体中文", "繁體中文 TW", "English"]
        # 未设置时停在「跟随系统」
        assert combo.currentIndex() == 0

        combo.setCurrentIndex(data.index("zh_CN"))
        assert cfg.language == "zh_CN"
        assert AdvancedConfig(config_file=str(config_file)).language == "zh_CN"
        assert notices[-1][0] == "语言已更改"

        combo.setCurrentIndex(data.index("en_US"))
        assert AdvancedConfig(config_file=str(config_file)).language == "en_US"
        assert notices[-1][0] == "Language Changed"

        combo.setCurrentIndex(0)
        assert AdvancedConfig(config_file=str(config_file)).language is None
        assert len(notices) == 3
    finally:
        w.close()


def test_picker_reflects_saved_language(injected_config, notices) -> None:
    """打开时选中已保存的语言，且不弹提示 / Opens on the saved language silently."""
    from ui.settings_center import SettingsCenter

    cfg, _ = injected_config
    cfg.set_language("zh_TW")
    w = SettingsCenter(get_i18n())
    try:
        assert w._lang_combo.currentData() == "zh_TW"
        assert notices == []
    finally:
        w.close()
