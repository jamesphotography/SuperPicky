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
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
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


_GLYPH_PIXMAP_CACHE: dict = {}


def glyph_pixmap(glyph: str, color: str, size: int = 16, dpr: float = 2.0,
                 fill_ratio: float = 0.80) -> QPixmap:
    """
    把单个字形渲染成固定 size 的方形 pixmap,并按其「实际墨水边界」缩放+居中,
    使一组字形(如罕见度 ○◔◑◕●)视觉大小一致 —— 规避 Unicode 几何字符在各平台
    字体下大小/基线不一的问题。墨水边界用像素扫描求得(比 tightBoundingRect 更
    可靠,直接量渲染结果)。结果按 (字形,色,尺寸,dpr,比例) 缓存。

    Render a single glyph into a fixed square pixmap, scaled & centered by its
    actual ink bounding box (found by pixel scan) so a set of glyphs renders at a
    consistent visual size across platform fonts. Cached.
    """
    key = (glyph, color, size, round(dpr, 2), fill_ratio)
    cached = _GLYPH_PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached

    px = max(1, int(round(size * dpr)))
    work = 48
    tmp = QImage(work, work, QImage.Format_ARGB32_Premultiplied)
    tmp.fill(Qt.transparent)
    p = QPainter(tmp)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    f = QFont()
    f.setPixelSize(int(work * 0.72))
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, work, work), int(Qt.AlignCenter), glyph)
    p.end()

    # 扫描非透明像素的实际边界 / scan ink bbox
    minx, miny, maxx, maxy = work, work, -1, -1
    for y in range(work):
        for x in range(work):
            if (tmp.pixel(x, y) >> 24) & 0xFF:
                minx = x if x < minx else minx
                maxx = x if x > maxx else maxx
                miny = y if y < miny else miny
                maxy = y if y > maxy else maxy

    out = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    if maxx >= minx and maxy >= miny:
        gw, gh = maxx - minx + 1, maxy - miny + 1
        scale = (px * fill_ratio) / max(gw, gh)
        tw, th = gw * scale, gh * scale
        p2 = QPainter(out)
        p2.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p2.drawImage(QRectF((px - tw) / 2.0, (px - th) / 2.0, tw, th),
                     tmp, QRectF(minx, miny, gw, gh))
        p2.end()

    pm = QPixmap.fromImage(out)
    pm.setDevicePixelRatio(dpr)
    _GLYPH_PIXMAP_CACHE[key] = pm
    return pm


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


def checkbox_indicator_qss(size: int = 15, unchecked_color: str = None, checked_color: str = None) -> str:
    """
    返回把 QCheckBox 指示器换成 circle.svg(未选)/circle-check.svg(选中)的样式片段。
    未选=圆圈(灰),选中=带勾圆圈(默认 accent 绿,可指定)。

    Return a QSS snippet replacing the QCheckBox indicator with circle.svg (unchecked)
    and circle-check.svg (checked). Append it to a widget's existing QCheckBox style.
    """
    uc = unchecked_color or COLORS.get("text_muted", "#8a8a8a")
    cc = checked_color or COLORS.get("accent", "#00d4aa")
    u = tinted_png_path("circle.svg", uc, size)
    c = tinted_png_path("circle-check.svg", cc, size)
    # 覆盖全局 QCheckBox::indicator 的方框/绿色背景与边框,只留圆圈图标本身。
    # Override the global indicator's box background/border so only the circle icon shows.
    return (
        f"QCheckBox::indicator {{ width: {size}px; height: {size}px;"
        f" border: none; background: transparent; border-radius: 0px; }}"
        f"QCheckBox::indicator:unchecked {{ image: url({u}); border: none; background: transparent; }}"
        f"QCheckBox::indicator:checked {{ image: url({c}); border: none; background: transparent; }}"
        f"QCheckBox::indicator:hover {{ border: none; background: transparent; }}"
        f"QCheckBox::indicator:checked:hover {{ image: url({c}); border: none; background: transparent; }}"
    )
