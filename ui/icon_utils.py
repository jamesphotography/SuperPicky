# -*- coding: utf-8 -*-
"""
SVG 图标加载与上色助手。

把 img/ico/ 下的 Lucide SVG(stroke=currentColor)渲染为位图并整体染色,
供大图模式左侧工具栏使用:常态灰 / 激活绿 / 禁用暗灰 / 危险红。

SVG icon loader & tinting helper. Lucide SVGs use stroke="currentColor", which
QtSvg renders as black; we render to an image then recolor every opaque pixel
via QPainter SourceIn so the same icon can show idle/active/disabled colors.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui.styles import COLORS

# 三态/危险态颜色 / State colors
ICON_IDLE: str = COLORS.get("text_secondary", "#a1a1a1")     # 常态灰
ICON_ACTIVE: str = COLORS.get("accent", "#00d4aa")           # 激活=软件标准青绿
ICON_DISABLED: str = "#5a5a5a"                               # 禁用暗灰
ICON_DANGER: str = "#ff6666"                                 # 危险(删除)红


def _ico_dir() -> str:
    """img/ico 目录(兼容 PyInstaller 打包的 _MEIPASS)。"""
    meipass = getattr(sys, "_MEIPASS", None)
    root = meipass if isinstance(meipass, str) else os.path.dirname(os.path.dirname(__file__))
    return os.path.join(root, "img", "ico")


def render_tinted_image(svg_name: str, color: str, size: int = 20, dpr: float = 1.0) -> QImage:
    """
    渲染指定 SVG 并整体染成 color,返回 QImage(headless 安全,可单测)。

    参数 / Parameters:
        svg_name: img/ico 下的文件名,如 "focus.svg"。
        color: 目标颜色(任意 QColor 可解析字符串)。
        size: 逻辑像素边长(正方形)。
        dpr: 设备像素比(Retina 传 >1 以保持锐利)。

    返回 / Returns:
        QImage:染色后的图标;SVG 无效时返回透明图。
    """
    px = max(1, int(round(size * dpr)))
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    renderer = QSvgRenderer(os.path.join(_ico_dir(), svg_name))
    if renderer.isValid():
        painter = QPainter(img)
        renderer.render(painter, QRectF(0, 0, px, px))
        # SourceIn:保留已绘制像素的 alpha,颜色统一替换为 color
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(img.rect(), QColor(color))
        painter.end()
    img.setDevicePixelRatio(dpr)
    return img


def load_tinted_icon(svg_name: str, color: str, size: int = 20, dpr: float = 1.0) -> QIcon:
    """渲染并染色为 QIcon(运行时使用,需 QGuiApplication)。"""
    return QIcon(QPixmap.fromImage(render_tinted_image(svg_name, color, size, dpr)))
