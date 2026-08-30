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

from app_user_stat.telemetry import _build_request_payload

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


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_onboarding_disclosure_keys_exist(filename: str) -> None:
    """
    首启告知的两个文案键在两种语言里都必须存在。

    设计文档 §5.2：「opt-out 不等于不告知」。匿名统计默认开启，若新用户
    只能在设置里翻到它，这个默认值在合规与社区观感上都站不住。缺任一键
    在界面上就是一个空标签——看着正常，实则什么都没告知。

    Both onboarding disclosure keys must exist in both locales; a missing
    key renders as an empty label, i.e. silently no disclosure at all.
    """
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    assert "telemetry_notice" in data["onboarding"]
    assert "telemetry_optout" in data["onboarding"]


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_onboarding_disclosure_states_what_is_not_collected(filename: str) -> None:
    """
    告知文案必须写明「默认发送」「不含照片/文件路径/个人信息」「去哪关」。

    这三件事缺一件，告知就退化成一句无信息的套话。与
    test_telemetry_desc_states_what_is_not_sent 同一把尺子，只是那条守设置
    页、这条守首启第一屏。

    The disclosure must state that it is on by default, what is NOT
    collected, and where to switch it off.
    """
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    notice = data["onboarding"]["telemetry_notice"].lower()
    optout = data["onboarding"]["telemetry_optout"].lower()

    assert ("默认" in notice or "by default" in notice)
    assert ("照片" in notice or "photo" in notice)
    assert ("文件路径" in notice or "file path" in notice)
    assert ("个人信息" in notice or "personal information" in notice)
    # 关闭入口必须指到设置中心「关于」页，而不是含糊的「在设置里」。
    assert ("关于" in optout or "about" in optout)


def test_disclosure_field_list_matches_actual_payload() -> None:
    """
    披露文案不能用「仅/only」把发送字段钉成一份比真实 payload 更短的清单。

    真实字段直接从 `_build_request_payload` 取（而不是在本文件里手抄一份），
    这样以后 payload 加字段时，这条测试会因为字段列表变长而重新核对文案，
    不会像手抄清单那样跟着一起漏改。Residual Fix 2 的起因正是 `arch` 与
    `python_version` 已经在发送、但设置页/首启文案用「仅/only」把清单钉在
    版本号+系统+语言+随机编号四项——文案与代码互相矛盾。

    规则：文案里一旦出现「仅/only」这类穷尽性措辞，payload 里除 `v`（协议
    版本号，不是用户数据）与 `events`（行为数据本身，由另一条测试专门覆盖
    「不含照片」）之外的每个字段，都必须能在文案中找到对应的自然语言线索；
    不出现穷尽性措辞则直接放行——这正是本次修复采用的方案（去掉「仅/only」，
    改用非穷尽的「等/etc.」）。

    The disclosure must not use "仅/only" to pin the sent-field list shorter
    than what `_build_request_payload` actually sends. Fields are read from
    the real payload builder, not retyped here, so a future field addition
    re-triggers this check instead of silently bypassing a hand-copied list.
    """
    payload = _build_request_payload("test-install-id", ["app_start"])
    # v = 协议/schema 版本号，不是关于用户的字段；events = 行为数据本身，
    # 由 test_telemetry_desc_states_what_is_not_sent 一类的测试专门守着。
    # v = protocol/schema version, not user data; events = the behavioral
    # payload itself, already covered by the "no photos" tests.
    field_names = [k for k in payload if k not in ("v", "events")]

    field_hints = {
        "id": ["随机编号", "random id"],
        "app_version": ["版本号", "version"],
        "os": ["操作系统", "operating system"],
        "arch": ["arch", "架构"],
        "python_version": ["python"],
        "locale": ["语言", "language"],
    }
    exhaustive_markers = {"zh_CN.json": "仅", "en_US.json": "only"}

    for filename, marker in exhaustive_markers.items():
        data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
        texts = {
            "settings.telemetry_desc": data["settings"]["telemetry_desc"],
            "onboarding.telemetry_notice": data["onboarding"]["telemetry_notice"],
        }
        for label, text in texts.items():
            text_lower = text.lower()
            if marker not in text:
                continue  # 未声称穷尽，放行 / not claiming exhaustiveness, OK
            for name in field_names:
                hints = field_hints[name]
                assert any(h in text or h.lower() in text_lower for h in hints), (
                    f"{filename}:{label} 用「{marker}」声称穷尽，"
                    f"但没提到实际发送的字段 {name}"
                )


def test_onboarding_welcome_page_renders_the_disclosure() -> None:
    """
    接线测试：欢迎页确实把这两个键渲染出来了，而不是只有文案躺在 JSON 里。

    只读源码而不构造 QDialog——构造 WelcomeOnboardingDialog 会拉起
    InitializationManager 并切换全局 i18n 语言（既往教训，见本文件头部）。
    上面两条守文案，这条守「文案真的被用上了」：Task 9 原本就是标题写着
    「设置中心开关与首启告知」、实际只做了前半截。

    Wiring test: the welcome page actually renders both keys. Source-level
    only — constructing the dialog would spin up InitializationManager and
    switch the global i18n language.
    """
    source = (Path(__file__).parent / "ui/welcome_onboarding_dialog.py").read_text(encoding="utf-8")
    builder = source.split("def _build_welcome_page")[1].split("\n    def ")[0]
    assert "onboarding.telemetry_notice" in builder, "欢迎页没有渲染首启告知正文"
    assert "onboarding.telemetry_optout" in builder, "欢迎页没有渲染关闭入口说明"


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
