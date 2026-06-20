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

import hashlib
import os
import sys
import tempfile

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


def stars_image(count: int, color: str, size: int = 16, gap: int = 2, dpr: float = 2.0) -> QImage:
    """
    横排渲染 count 颗 star.svg 染成 color,返回一张 QImage(headless 安全)。
    count<=0 返回空图。dpr 用于 Retina 锐利显示。

    Render `count` star.svg horizontally tinted to `color` into one QImage.
    """
    count = max(0, int(count))
    if count == 0:
        return QImage()
    s = max(1, int(round(size * dpr)))
    g = max(0, int(round(gap * dpr)))
    star = render_tinted_image("star.svg", color, size=s, dpr=1.0)  # s×s 像素,dpr=1
    total_w = count * s + (count - 1) * g
    row = QImage(total_w, s, QImage.Format_ARGB32_Premultiplied)
    row.fill(Qt.transparent)
    painter = QPainter(row)
    for i in range(count):
        painter.drawImage(i * (s + g), 0, star)
    painter.end()
    row.setDevicePixelRatio(dpr)
    return row


def stars_pixmap(count: int, color: str, size: int = 16, gap: int = 2, dpr: float = 2.0) -> QPixmap:
    """stars_image 的 QPixmap 版(供 QLabel.setPixmap,需 QGuiApplication)。"""
    return QPixmap.fromImage(stars_image(count, color, size, gap, dpr))


_PNG_CACHE_DIR = os.path.join(tempfile.gettempdir(), "superpicky_icons")


def tinted_png_path(svg_name: str, color: str, size: int = 12, dpr: float = 2.0) -> str:
    """
    渲染染色图标并存为 PNG,返回可用于 Qt 样式表 `image: url(...)` 的正斜杠路径。
    供 QComboBox::down-arrow 等只能用图片 URL 的样式场景。结果按参数缓存。

    Render a tinted icon to a PNG and return a forward-slash path usable in
    Qt stylesheet `image: url(...)` (e.g. QComboBox::down-arrow). Cached by params.
    """
    os.makedirs(_PNG_CACHE_DIR, exist_ok=True)
    key = hashlib.md5(f"{svg_name}|{color}|{size}|{dpr}".encode("utf-8")).hexdigest()[:12]
    path = os.path.join(_PNG_CACHE_DIR, f"{key}.png")
    if not os.path.exists(path):
        render_tinted_image(svg_name, color, size, dpr).save(path, "PNG")
    return path.replace(os.sep, "/")
