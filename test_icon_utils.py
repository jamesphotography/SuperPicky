#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui/icon_utils.py 单元测试:SVG 加载 + 上色。"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# 无显示环境下用 offscreen 平台,保证 QImage/QPainter 可用。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QGuiApplication

from ui import icon_utils as iu

_app = QGuiApplication.instance() or QGuiApplication([])

ICONS = [
    "focus.svg", "lock.svg", "bird.svg", "square-pen.svg", "AI-Srop.svg",
    "trash-2.svg", "gallery-thumbnails.svg", "crop.svg", "image-plus.svg",
]


@pytest.mark.parametrize("name", ICONS)
def test_render_tinted_image_colored(name):
    """每个图标渲染后应非空,且实心像素被染成目标色(抗锯齿边缘像素不检查)。"""
    img = iu.render_tinted_image(name, "#00d4aa", size=48)
    assert not img.isNull()
    assert img.width() == 48 and img.height() == 48
    tr, tg, tb = QColor("#00d4aa").red(), QColor("#00d4aa").green(), QColor("#00d4aa").blue()
    solid = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() >= 250:  # 实心描边像素 / fully-opaque stroke pixels
                solid += 1
                # ±2 容差:预乘格式在近全opaque 边缘有 1 级舍入误差 / premultiplied rounding
                assert abs(c.red() - tr) <= 2 and abs(c.green() - tg) <= 2 and abs(c.blue() - tb) <= 2
    assert solid > 0, f"{name} 渲染后无实心像素(SVG 可能无效/未染色)"


def test_stars_image_width_scales_with_count():
    """N 颗星的图宽随 count 增加;count=0 返回空图。"""
    empty = iu.stars_image(0, "#d4a800", size=16)
    assert empty.isNull()
    one = iu.stars_image(1, "#d4a800", size=16, dpr=1.0)
    three = iu.stars_image(3, "#d4a800", size=16, dpr=1.0)
    assert not one.isNull() and not three.isNull()
    assert three.width() > one.width()  # 3 星比 1 星宽
    # 有金色实心像素
    target = QColor("#d4a800")
    assert any(
        three.pixelColor(x, y).alpha() >= 250
        for y in range(three.height()) for x in range(three.width())
    )


def test_missing_svg_returns_transparent():
    """不存在的 SVG 返回透明图而非崩溃。"""
    img = iu.render_tinted_image("__nope__.svg", "#00d4aa", size=20)
    assert not img.isNull()
    assert all(
        img.pixelColor(x, y).alpha() == 0
        for y in range(img.height()) for x in range(img.width())
    )
