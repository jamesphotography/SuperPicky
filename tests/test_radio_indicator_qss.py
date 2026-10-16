# -*- coding: utf-8 -*-
"""单选按钮指示器样式：QRadioButton 必须与 QCheckBox 用同一套圆圈图标方案。

Radio-button indicator styling: QRadioButton must use the same circle-icon
scheme as QCheckBox.

背景 / Background:
设置中心的 5 个 QRadioButton（元数据写入模式 3 个 + 识鸟数据源 2 个）此前
只设了文字样式，指示器交给 Qt 原生绘制。Windows 深色界面下控件挂了自定义
stylesheet 后走 QStyleSheetStyle 渲染，选中态圆点颜色与深色背景融合——
用户实际看到「未选中的空心圆圈可见、被选中那项反而隐形」（用户 Paul 于
4.5.0RC2 报告）。修法与 CLAUDE.md 的开关样式约定对齐：未选=空心圆圈，
选中=绿色带勾圆圈（circle.svg / circle-check.svg）。

The 5 QRadioButtons in the Settings Center only styled their text, leaving
the indicator to native rendering. On Windows dark UI, QStyleSheetStyle
draws the checked dot in a color that blends into the background — the
selected option appears indicator-less (reported by user Paul on 4.5.0RC2).
Fix mirrors the CLAUDE.md checkbox convention: hollow circle unchecked,
green circled check when checked.
"""
import os
import re

from ui.icon_utils import radio_indicator_qss


def _extract_image_paths(qss: str) -> list:
    """从 QSS 中提取全部 image: url(...) 路径 / Extract image url() paths."""
    return re.findall(r"image:\s*url\(([^)]+)\)", qss)


def test_qss_targets_radio_indicator_states():
    """QSS 必须同时定义未选/选中两种指示器状态。"""
    qss = radio_indicator_qss()
    assert "QRadioButton::indicator:unchecked" in qss
    assert "QRadioButton::indicator:checked" in qss
    # 选中与未选中必须是不同的图标 / distinct icons per state
    paths = _extract_image_paths(qss)
    assert len(set(paths)) >= 2


def test_qss_image_files_exist():
    """QSS 引用的染色 PNG 必须已实际生成（url 指向不存在的文件=隐形指示器）。"""
    for path in _extract_image_paths(radio_indicator_qss()):
        assert os.path.exists(path), f"指示器图标未生成: {path}"


def test_settings_center_radios_use_shared_indicator():
    """设置中心所有 QRadioButton 必须挂统一的圆圈指示器样式。

    源码级检查：settings_center 引用 radio_indicator_qss，且不再有
    只设文字、不设指示器的裸 QRadioButton 样式。
    """
    import inspect

    import ui.settings_center as sc

    src = inspect.getsource(sc)
    assert "radio_indicator_qss" in src, "settings_center 未使用共享单选指示器样式"
    # 全部 5 个 QRadioButton（识鸟数据源 2 + 元数据写入模式 3）都必须
    # 通过统一 helper 拿样式，不再各自拼只有文字的裸样式。
    # All 5 radios (2 BirdID source + 3 metadata write mode) must get their
    # style from the shared helper, not a bare text-only stylesheet.
    radio_names = [
        "_bid_ebird",
        "_bid_gbif",
        "_xmp_embedded",
        "_xmp_sidecar",
        "_xmp_none",
    ]
    for name in radio_names:
        pattern = rf"self\.{name}\.setStyleSheet\(_radio_style\(\)\)"
        assert re.search(pattern, src), f"{name} 未挂统一单选指示器样式"


def test_checked_state_renders_green_indicator():
    """离屏实渲染：选中态必须显示绿色图标，且与未选中态视觉可区分。

    这是本 bug 的端到端锁定——源码级检查抓不住「QSS 裸声明与选择器规则
    混用导致整张样式表被 Qt 丢弃、静默回落原生渲染」这类失效（实现过程中
    真实发生过），只有渲染出来数像素才可靠。

    Offscreen real rendering: the checked state must show a green icon,
    visually distinct from unchecked. Source-level checks cannot catch
    "mixed bare declarations + selector rules make Qt discard the whole
    stylesheet and silently fall back to native rendering" (which actually
    happened during implementation) — only rendered pixels can.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QRadioButton

    from ui.settings_center import _radio_style

    app = QApplication.instance() or QApplication([])

    def render(checked: bool):
        rb = QRadioButton("XMP Sidecar (all files)")
        rb.setStyleSheet(_radio_style())
        rb.setChecked(checked)
        rb.resize(200, 30)
        return rb.grab().toImage()

    img_u, img_c = render(False), render(True)
    diff_colors = []
    for y in range(30):
        for x in range(30):  # 只看左侧指示器区 / indicator zone only
            cu, cc = img_u.pixelColor(x, y), img_c.pixelColor(x, y)
            if (cu.red(), cu.green(), cu.blue()) != (cc.red(), cc.green(), cc.blue()):
                diff_colors.append(cc)

    assert len(diff_colors) > 20, "选中与未选中视觉无差异——指示器隐形回归!"
    greens = [c for c in diff_colors if c.green() > 120 and c.green() > c.red() + 30]
    assert greens, "选中态没有绿色(accent)图标"
