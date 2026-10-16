#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两处「说明/外观与实际配置脱节」的回归测试。

1. 连拍说明的张数必须跟随 ``burst_min_count`` 配置（3-10，默认 4）。此前中英文
   四条文案都把 4 写死在字符串里，用户改了配置说明就在说谎——旁边的星级配额、
   精选百分比早已参数化，唯独这条漏了。
2. 文本控件的右键菜单由 Qt 生成，图标取自平台主题（为浅色界面画的深灰描线），
   在深色菜单上几乎不可见。样式表能改菜单文字色但改不了图标，需在显示前重染；
   重染必须只作用于主题图标，不能碰项目自建菜单里已按设计系统染好色的图标。

Regression tests for two "documentation/appearance vs. actual config" gaps.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu

from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


# ==========================================================================
#  1. 连拍说明的张数
# ==========================================================================

@pytest.mark.parametrize("lang", ["zh_CN", "en_US"])
@pytest.mark.parametrize("key", ["help.burst_info", "dialogs.note_burst"])
def test_burst_note_follows_configured_minimum(lang, key):
    """连拍说明必须代入实际配置值，而不是写死的 4。"""
    i18n = get_i18n()
    i18n.switch_language(lang)
    text = i18n.t(key, count=7)
    assert "7" in text, f"{lang}/{key} 未代入配置值: {text}"
    assert "{count}" not in text, f"{lang}/{key} 占位符未被替换: {text}"


@pytest.mark.parametrize("lang", ["zh_CN", "en_US"])
@pytest.mark.parametrize("key", ["help.burst_info", "dialogs.note_burst"])
def test_burst_note_has_no_hardcoded_four(lang, key):
    """
    模板里不能再残留写死的 4。

    直接检查模板本身，避免「默认值恰好是 4 所以测试假绿」。
    Checks the template itself so a default of 4 cannot mask a hard-coded 4.
    """
    i18n = get_i18n()
    i18n.switch_language(lang)
    template = i18n.t(key, count="{count}")
    assert "4" not in template, f"{lang}/{key} 仍写死张数: {template}"


# ==========================================================================
#  2. 标准右键菜单的图标染色
# ==========================================================================

def _themed_icon() -> QIcon:
    """构造一个「带 name」的图标，模拟平台主题图标。"""
    icon = QIcon.fromTheme("edit-copy")
    if icon.isNull() or not icon.name():
        pytest.skip("当前平台没有主题图标（离屏环境常见）")
    return icon


def test_tinting_skips_project_icons():
    """
    项目自建图标（name 为空）必须原样保留——它们已按设计系统染好色。

    这是这套染色能安全全局生效的前提：整种合并等自建菜单不能被改。
    """
    from ui.context_menu import tint_theme_icons
    from ui.icon_utils import load_tinted_icon
    from ui.styles import COLORS

    menu = QMenu()
    action = menu.addAction(load_tinted_icon("crown.svg", COLORS["star_gold"], 16), "精选")
    before = action.icon().cacheKey()

    assert tint_theme_icons(menu) == 0, "不应染任何项目自建图标"
    assert action.icon().cacheKey() == before, "项目自建图标被改动了"


def test_tint_icon_recolours_to_target():
    """染色结果的不透明像素必须是目标色（形状保留、颜色替换）。"""
    from PySide6.QtCore import QSize
    from ui.context_menu import tint_icon
    from ui.icon_utils import load_tinted_icon

    src = load_tinted_icon("crown.svg", "#808080", 16)
    out = tint_icon(src, "#ff0000", 16)

    img = out.pixmap(QSize(16, 16)).toImage()
    opaque = [
        img.pixelColor(x, y)
        for y in range(img.height()) for x in range(img.width())
        if img.pixelColor(x, y).alpha() > 200
    ]
    assert opaque, "染色后图标全透明"
    assert all(c.red() > 200 and c.green() < 60 and c.blue() < 60 for c in opaque), \
        f"颜色未替换: {opaque[:3]}"


def test_install_is_idempotent():
    """重复安装只装一次过滤器，避免每开一次窗口叠加一层。"""
    from ui import context_menu

    context_menu.install_standard_menu_tinting(_app)
    first = context_menu._tinter
    context_menu.install_standard_menu_tinting(_app)
    assert context_menu._tinter is first
