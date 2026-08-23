# -*- coding: utf-8 -*-
"""
设置中心下拉框深色样式回归测试。

背景：设置中心各页的滚动内容容器 `inner` 用了**无选择器**的
`setStyleSheet("background: transparent;")`。Qt 中无选择器规则会传播到该
子树内所有子控件，且「离目标更近的祖先」优先级高于更远的祖先，因此它会
压过主窗口 GLOBAL_STYLE 里的 `QComboBox QAbstractItemView` 规则，把下拉的
弹出列表背景也变成透明。透明的 popup 在 macOS 上由系统绘制原生浅色窗口底，
表现为「白底 + 浅灰文字」，几乎不可读（国家 / 鸟名显示格式两个下拉）。

Regression test for the Settings Center combo boxes' dark styling.

Each page's scroll content widget `inner` used a selector-less
`setStyleSheet("background: transparent;")`. In Qt such a rule propagates to
every descendant widget, and a nearer ancestor's stylesheet outranks a more
distant one, so it overrode the `QComboBox QAbstractItemView` rule inherited
from the main window's GLOBAL_STYLE and made the popup transparent. On macOS a
transparent popup falls back to the native light window background, rendering
as white with pale grey text.
"""
import os
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow, QLabel, QSpinBox

from tools.i18n import get_i18n
from ui.styles import COLORS, GLOBAL_STYLE

_app = QApplication.instance() or QApplication([])


def _dominant_color(widget) -> str:
    """
    抓取控件渲染结果并返回出现最多的颜色（≈背景色）。

    参数 / Parameters:
        widget: 待测量的 QWidget。

    返回 / Return:
        str: 形如 "#1a1a1a" 的颜色名；透明区域会渲染为 "#000000"。
    """
    img = widget.grab().toImage()
    counter: Counter = Counter()
    for y in range(0, img.height(), 3):
        for x in range(0, img.width(), 3):
            counter[img.pixelColor(x, y).name()] += 1
    return counter.most_common(1)[0][0]


def _make_center(tmp_path):
    """
    构造挂在带 GLOBAL_STYLE 的主窗口下的 SettingsCenter（复现真实继承链）。

    配置文件指向 tmp_path，避免测试写坏用户本机的 advanced_config.json。

    Build a SettingsCenter parented to a GLOBAL_STYLE main window (mirroring the
    real inheritance chain), with the config file redirected into tmp_path.
    """
    from advanced_config import get_advanced_config
    from ui.settings_center import SettingsCenter

    get_advanced_config().config_file = str(tmp_path / "advanced_config.json")

    win = QMainWindow()
    win.setStyleSheet(GLOBAL_STYLE)
    win.setCentralWidget(QLabel("main"))
    win.show()

    center = SettingsCenter(get_i18n(), parent=win, start_page="birdid")
    center.show()
    return win, center


def test_settings_combo_popups_keep_dark_background(tmp_path):
    """
    设置中心里未单独设样式的下拉，其弹出列表必须是 GLOBAL_STYLE 的深色背景，
    不能被页面级 transparent 规则打成透明（透明在 macOS 上会显示为白底）。

    Combo popups in the Settings Center must keep GLOBAL_STYLE's dark background
    instead of being flattened to transparent by the page-level rule.
    """
    win, center = _make_center(tmp_path)
    expected = COLORS["bg_elevated"]

    for attr in ("_bid_country", "_bid_name_format", "_folder_layout_combo"):
        combo = getattr(center, attr, None)
        assert isinstance(combo, QComboBox), f"{attr} 不存在或不是 QComboBox"
        combo.showPopup()
        _app.processEvents()
        actual = _dominant_color(combo.view())
        combo.hidePopup()
        _app.processEvents()
        assert actual == expected, (
            f"{attr} 的弹出列表背景为 {actual}，期望 {expected}"
            f"（#000000 表示背景透明，在 macOS 上会渲染成白底）"
        )

    center.close()
    win.close()


def test_settings_page_transparent_rule_is_scoped(tmp_path):
    """
    页面滚动容器的透明规则必须带选择器（只作用于自身），否则会传播到全部子控件。

    The page scroll container's transparent rule must be scoped with a selector,
    otherwise it propagates to every child widget.
    """
    win, center = _make_center(tmp_path)

    combo = center._bid_country
    node = combo.parentWidget()
    offenders = []
    while node is not None and node is not win:
        qss = node.styleSheet()
        if qss and "transparent" in qss and "{" not in qss:
            offenders.append(f"{type(node).__name__}(objectName={node.objectName()!r}): {qss!r}")
        node = node.parentWidget()

    assert not offenders, "存在无选择器的样式表，会传播到子控件：" + "; ".join(offenders)

    center.close()
    win.close()


def test_settings_spinbox_keeps_dark_background(tmp_path):
    """
    精选页的连拍 fps 输入框必须是深色输入底色。

    此前它是靠页面那条裸 `background: transparent;` 意外变深的；把该规则收窄到
    容器自身后，QSpinBox 会回落到 GLOBAL_STYLE——而 GLOBAL_STYLE 原本没有
    QSpinBox 规则，会退回 macOS 原生浅色渲染，变成「白底 + 浅灰数字」看不清。
    因此 GLOBAL_STYLE 必须补齐 QSpinBox 样式。

    The burst-fps spin box on the culling page must keep the dark input
    background. It previously went dark only as a side effect of the page's bare
    `background: transparent;` rule; once that rule is scoped, QSpinBox falls
    back to GLOBAL_STYLE, which had no QSpinBox rule and would render with the
    native light macOS look (pale digits on white).
    """
    win, center = _make_center(tmp_path)
    center.show_page("culling")
    _app.processEvents()

    spin = center._cull_burst_fps
    assert isinstance(spin, QSpinBox)
    actual = _dominant_color(spin)
    assert actual == COLORS["bg_input"], (
        f"连拍 fps 输入框背景为 {actual}，期望 {COLORS['bg_input']}"
    )

    center.close()
    win.close()
