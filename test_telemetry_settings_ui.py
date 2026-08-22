#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测开关的 i18n 文案测试 + 接线测试。

不构造 MainWindow——构造它会切换全局 i18n 语言，导致本地化断言假失败
（既往教训）。i18n 部分只读 locale JSON 文件。

接线测试（test_on_telemetry_changed_*）验证 `SettingsCenter._on_telemetry_changed`
真的把开关状态写回 `advanced_config` 并落盘，而不是仅仅"文案齐全但没接线"。
用未绑定方式调用该方法（传一个只带 `_telemetry_checkbox` 的桩对象），不构造
`SettingsCenter`/`MainWindow`；配置走 `AdvancedConfig(config_file=临时路径)`
注入 `LazyRegistry`，不触碰用户真实的 advanced_config.json（该注入点是
advanced_config.py 显式为测试隔离设计的，见其 __init__ 文档字符串）。

导入 `ui.settings_center` 前设置 QT_QPA_PLATFORM=offscreen，避免在无显示环境的
CI 上因 Qt 平台插件问题失败——本文件不构造任何 QWidget/QApplication，只调用
纯 Python 方法体，理论上不需要平台插件，但显式设置更稳妥、零成本。

i18n coverage for the telemetry toggle, plus a wiring test proving
`_on_telemetry_changed` actually persists via `advanced_config` (set + save),
not just that the label text exists. Deliberately avoids constructing
MainWindow, which switches the global i18n language.
"""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

LOCALES = Path(__file__).parent / "locales"


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_keys_exist(filename: str) -> None:
    """两种语言都必须有开关文案，缺一个就是界面上的空标签。/ Both locales."""
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    assert "telemetry_label" in data["settings"]
    assert "telemetry_desc" in data["settings"]


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_desc_states_what_is_not_sent(filename: str) -> None:
    """
    说明文案必须写明「不含照片/路径/个人信息」。

    opt-out 默认开启的前提是说明必须到位；这条测试守住它不被简化掉。

    The description must state what is NOT collected — the premise of opt-out.
    """
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    desc = data["settings"]["telemetry_desc"].lower()
    assert ("照片" in desc or "photo" in desc)
    assert ("个人信息" in desc or "personal information" in desc)


class _FakeCheckbox:
    """桩控件：只提供 isChecked()，不依赖真实 QCheckBox/QApplication。
    Stub widget providing only isChecked(), no real QCheckBox/QApplication needed."""

    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _FakeSettingsCenter:
    """桩 self：只带 `_on_telemetry_changed` 读取的那一个属性。
    Stub `self` carrying only the one attribute `_on_telemetry_changed` reads."""

    def __init__(self, checked: bool) -> None:
        self._telemetry_checkbox = _FakeCheckbox(checked)


def test_on_telemetry_changed_persists_to_advanced_config(tmp_path) -> None:
    """
    真实接线测试：不读 locale，直接验证 `_on_telemetry_changed` 确实调用了
    `set_telemetry_enabled` 与 `save()`。

    以未绑定方式调用 `SettingsCenter._on_telemetry_changed`（不构造完整
    `SettingsCenter`/`MainWindow`），配置通过 `AdvancedConfig(config_file=临时路径)`
    注入 `LazyRegistry`，全程不碰用户真实的 advanced_config.json。

    断言链：
    1. 调用后内存中的配置值确实变了（不是走了个空调用）；
    2. save() 确实被调用——独立重新从磁盘 load 一份新实例，仍读到新值，
       这是最容易被未来重构悄悄删掉的一步；
    3. Qt 的 int 勾选状态（0/2）经 `isChecked()` 也能正确落成 bool。

    Wiring test: verifies `_on_telemetry_changed` actually calls
    `set_telemetry_enabled` + `save()`, not just that i18n labels exist.
    """
    from advanced_config import AdvancedConfig
    from config import get_lazy_registry
    from ui.settings_center import SettingsCenter

    config_file = tmp_path / "advanced_config.json"
    injected_cfg = AdvancedConfig(config_file=str(config_file))
    registry = get_lazy_registry()
    # 注入临时配置实例替换单例，测试结束后必须清掉，避免污染后续测试/真实单例。
    # Swap the singleton for a temp-backed instance; must clear it afterwards
    # to avoid leaking into later tests or the real singleton.
    registry.set("advanced_config.instance", injected_cfg)
    try:
        assert injected_cfg.telemetry_enabled is True  # 默认开启 / default on

        # Qt CheckState.Unchecked == 0 → 关闭
        SettingsCenter._on_telemetry_changed(_FakeSettingsCenter(False), 0)

        # 断言 1：内存值确实变了 / in-memory value actually changed
        assert injected_cfg.telemetry_enabled is False

        # 断言 2：save() 真的落盘了——独立重新加载一份新实例来验证，
        # 而不是复用同一个 injected_cfg（避免只测到内存赋值、漏测持久化）。
        # save() actually persisted — verified via an independently reloaded
        # instance, not the same injected_cfg object, so persistence itself
        # (not just the in-memory attribute) is under test.
        reloaded_off = AdvancedConfig(config_file=str(config_file))
        assert reloaded_off.telemetry_enabled is False

        # 断言 3：Qt CheckState.Checked == 2 → 开启，且能从 False 切回 True
        SettingsCenter._on_telemetry_changed(_FakeSettingsCenter(True), 2)
        assert injected_cfg.telemetry_enabled is True
        reloaded_on = AdvancedConfig(config_file=str(config_file))
        assert reloaded_on.telemetry_enabled is True
    finally:
        registry.clear("advanced_config.instance")
