# -*- coding: utf-8 -*-
"""
设置中心「专有 RAW 也写入文件本体」是「写入文件」的子选项（issue #139）。

The "Also write into proprietary RAW files" switch is a sub-option of
"Write to file" in the Settings Center (issue #139).

背景 / Background:
该开关原先排在三个写入模式单选项之后，圆圈样式又与单选项相同，用户把它当成第四个
互斥选项；「写入文件」的说明还写着「直接写入 ARW/CR3」，而专有 RAW 默认只写边车。
现在开关紧挨「写入文件」并缩进，只在该模式下可用（与 advanced_config 的实际判定一致：
边车模式下开关无效，不写入模式下什么都不写）。
The switch sat after all three radios with the same round indicator and read
as a fourth choice; it now sits under "Write to file" and is enabled only
there, matching how advanced_config actually applies it.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """提供进程级 QApplication。/ Provide a process-wide QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def center(qapp):
    """
    构建设置中心（钉住 en_US 语言；全局配置已由根 conftest 隔离）。

    Build the Settings Center with en_US pinned; global config is isolated by
    the root conftest.
    """
    from tools.i18n import I18n
    from ui.settings_center import SettingsCenter

    c = SettingsCenter(I18n("en_US"), start_page="output")
    yield c
    c.deleteLater()


def test_switch_is_enabled_only_under_write_to_file(center) -> None:
    """选「写入文件」时可用，选边车或不写入时置灰，且不改动开关自身的值 / Enabled only for embedded."""
    from advanced_config import get_advanced_config

    assert center._xmp_embedded.isChecked()          # 默认模式 / default mode
    assert center._raw_embed.isEnabled()

    center._raw_embed.setChecked(True)
    center._xmp_sidecar.setChecked(True)
    assert not center._raw_embed.isEnabled()
    assert center._raw_embed.isChecked()             # 置灰不清掉用户的选择 / value kept
    assert get_advanced_config().get_metadata_write_mode() == "sidecar"

    center._xmp_none.setChecked(True)
    assert not center._raw_embed.isEnabled()

    center._xmp_embedded.setChecked(True)
    assert center._raw_embed.isEnabled()
    assert get_advanced_config().raw_embed_metadata is True


def test_switch_sits_right_under_write_to_file(center) -> None:
    """开关在「写入文件」与「XMP 边车」之间，不再排在三个单选项之后 / Placed under the first radio."""
    lay = center._raw_embed.parentWidget().layout()
    order = [lay.indexOf(w) for w in (center._xmp_embedded, center._raw_embed,
                                      center._xmp_sidecar, center._xmp_none)]
    assert -1 not in order
    assert order == sorted(order)


def test_embedded_hint_no_longer_claims_raw_is_written(center) -> None:
    """「写入文件」说明不再声称直接写入 CR3，并提到边车 / Hint mentions the sidecar."""
    for lang in ("en_US", "zh_CN", "zh_TW"):
        from tools.i18n import I18n
        hint = I18n(lang).t("advanced_settings.xmp_mode_embedded_hint")
        assert ".xmp" in hint, (lang, hint)
        assert "ARW/CR3 raw files" not in hint and "直接写入" not in hint and "直接寫入" not in hint
        # 单选项本身也不能叫「写入 RAW」：英文一直是 Write to file
        # The radio itself must not say "write RAW"; English was always "Write to file".
        label = I18n(lang).t("advanced_settings.write_embedded")
        assert "RAW" not in label, (lang, label)


def test_indicator_still_renders_with_the_extra_rules(center) -> None:
    """
    追加的缩进/置灰规则不能让 Qt 丢弃整张样式表：选中态仍是绿色带勾圆圈。

    The added rules must not make Qt drop the stylesheet: checked still
    renders the green circled check.
    """
    from PySide6.QtWidgets import QCheckBox

    def render(checked: bool):
        cb = QCheckBox("Also write into proprietary RAW files")
        cb.setStyleSheet(center._raw_embed.styleSheet())
        cb.setChecked(checked)
        cb.resize(300, 30)
        return cb.grab().toImage()

    img_u, img_c = render(False), render(True)
    greens = []
    for y in range(30):
        for x in range(60):  # 含 24px 缩进的指示器区 / indicator zone incl. the indent
            cu, cc = img_u.pixelColor(x, y), img_c.pixelColor(x, y)
            if (cu.red(), cu.green(), cu.blue()) != (cc.red(), cc.green(), cc.blue()) \
                    and cc.green() > 120 and cc.green() > cc.red() + 30:
                greens.append(cc)
    assert greens, "选中态没有绿色图标——样式表可能被 Qt 丢弃"
