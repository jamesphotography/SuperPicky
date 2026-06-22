# -*- coding: utf-8 -*-
"""
Crop Studio — 全屏「后期工作区」/ fullscreen post-processing workspace.

把旧的裁剪建议弹窗(CropAdvisorDialog)升级为专业看图体验:
- 顶栏:鸟种名 / 文件名 / 星级 / 罕见度 / IUCN + 返回 / 导出。
- 左竖工具栏:裁剪 / 特写 / 鸟种 / 自动 / 删除。
- 中画布:深灰背景 + fit-to-window + 投影 + 浮动 zoom 工具条。
- 右候选:双列 letterbox 候选,点选切换画布预览。

本文件按实现计划分任务逐步搭建;本提交(Task 2)只搭骨架 + 后台候选加载,
后续任务填充画布(T3)/候选(T4)/顶栏与工具栏(T5)/手动裁剪(T6)/导出(T7)。

Crop Studio — a fullscreen post-processing workspace replacing the old
CropAdvisorDialog. This file is built up task-by-task per the implementation
plan; this commit (Task 2) only lays down the skeleton + background advice
loading. Non-destructive throughout.
"""
from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.crop_advisor import (
    BIRD_ONLY_LABEL,
    ORIGINAL_LABEL,
    CropAdviceResult,
    CropSuggestion,
    advise_crops,
)
from core.crop_export import default_out_path, export_crop
from core.rarity_tier import gbif_score_to_tier, tier_color, tier_icon, tier_name
from ui.icon_utils import (
    ICON_ACTIVE,
    ICON_DANGER,
    ICON_DISABLED,
    ICON_IDLE,
    load_tinted_icon,
    stars_pixmap,
)

try:
    from ui.styles import COLORS
except Exception:  # 主题缺失时的安全兜底 / Safe fallback if theme unavailable
    COLORS = {}


# 画布背景中灰 / Canvas neutral-gray background.
CANVAS_BG: str = "#808080"


def _c(key: str, fallback: str) -> str:
    """读取主题色,缺失则用兜底值 / Read a theme color with a fallback."""
    return COLORS.get(key, fallback)


# 画布源像素图最大边(限制内存;1:1 仍能呈现足够细节)/
# Max side of the canvas source pixmap (bounds memory; still detailed enough for 1:1).
_CANVAS_SRC_MAX: int = 6000


def _bgr_to_qpixmap(bgr: np.ndarray, max_side: int = _CANVAS_SRC_MAX) -> QPixmap:
    """
    BGR ndarray → QPixmap,长边超过 max_side 时按比例缩小。
    Convert a BGR ndarray to QPixmap, downscaling if the longest side exceeds max_side.
    """
    h, w = bgr.shape[:2]
    scale = min(1.0, max_side / max(h, w)) if max(h, w) else 1.0
    if scale < 1.0:
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hh, ww = rgb.shape[:2]
    img = QImage(rgb.data, ww, hh, 3 * ww, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


def _letterbox_pixmap(bgr: np.ndarray, box_w: int, box_h: int) -> QPixmap:
    """
    把 BGR 图按真实比例缩放并居中放入 box_w×box_h 的画框(letterbox,上下/左右留黑边),
    返回 QPixmap。用于右侧候选缩略图,使不同比例的候选都在等高格子内对齐。

    Scale a BGR image to fit a box_w×box_h frame keeping aspect ratio, centered
    with letterbox padding. Used for the right-panel candidate thumbnails so
    candidates of differing ratios align inside equal-height cells.
    """
    h, w = bgr.shape[:2]
    if w <= 0 or h <= 0:
        return QPixmap(box_w, box_h)
    scale = min(box_w / w, box_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((box_h, box_w, 3), np.uint8)  # 黑底 letterbox / black letterbox
    ox, oy = (box_w - nw) // 2, (box_h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, box_w, box_h, 3 * box_w, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


# ── 后台候选加载线程 / Background advice worker ───────────────────────────────


class _AdviceWorker(QThread):
    """
    后台线程运行 advise_crops,完成后通过 done 信号回传 CropAdviceResult。
    线程内绝不抛异常(失败回传 no_bird 结果)。

    Runs advise_crops off the UI thread; emits the CropAdviceResult via `done`.
    Never raises inside the thread (a failure emits a no_bird result instead).
    """

    done: Signal = Signal(object)  # CropAdviceResult

    def __init__(self, image_path: str) -> None:
        super().__init__()
        self._path = image_path

    def run(self) -> None:
        try:
            result = advise_crops(self._path)
        except Exception:  # noqa: BLE001 — 线程内不崩 / never crash the thread
            result = CropAdviceResult(status="no_bird", bird_count=0)
        self.done.emit(result)


class _ExportWorker(QThread):
    """
    后台线程执行裁剪导出(export_crop),完成后通过 done 信号回传 (ok, out_or_err)。
    Runs export_crop off the UI thread; emits (ok: bool, out_path_or_error: str).
    """

    done: Signal = Signal(bool, str)

    def __init__(self, src: str, box, out: str, exif_src: Optional[str], *,
                 jpeg_quality: int = 95, out_size: Optional[tuple] = None,
                 enhance_opts=None) -> None:
        super().__init__()
        self._src, self._box, self._out, self._exif_src = src, box, out, exif_src
        self._quality = jpeg_quality
        self._out_size = out_size
        self._enhance_opts = enhance_opts

    def run(self) -> None:
        try:
            out = export_crop(self._src, self._box, self._out, exif_src=self._exif_src,
                              jpeg_quality=self._quality, out_size=self._out_size,
                              enhance_opts=self._enhance_opts)
            self.done.emit(True, out)
        except Exception as e:  # noqa: BLE001 — 失败回传错误信息 / report error
            self.done.emit(False, str(e))


# ── 修图预览 / Enhance preview ────────────────────────────────────────────────

PREVIEW_LONG_EDGE = 2048  # 预览降采样目标长边;放大到此分辨率以便 100% 看清降噪
# preview downscale long edge; high enough that 1:1 zoom reveals real denoise.


def _pipeline_enhance(img_rgb, opts, **kw):
    """
    间接调用 pipeline.enhance,便于测试替换 / indirection for testability.

    懒导入,避免无修图时引入 torch/enhance 依赖。
    Lazy import so non-enhance paths don't pull in torch/enhance.
    """
    from core.enhance.pipeline import enhance as _e  # noqa: PLC0415
    return _e(img_rgb, opts, **kw)


class _EnhanceWorker(QThread):
    """
    后台对预览图跑修图管线,完成回传修图后 RGB ndarray;失败回传原图。
    Runs the enhance pipeline off the UI thread; emits the enhanced RGB ndarray
    (or the original on failure, so the UI always gets a usable image).
    """

    done: Signal = Signal(object)

    def __init__(self, img_rgb, opts) -> None:
        super().__init__()
        self._img, self._opts = img_rgb, opts

    def run(self) -> None:
        try:
            out = _pipeline_enhance(self._img, self._opts)
        except Exception:  # noqa: BLE001 — 预览失败回退原图 / fall back to original
            out = self._img
        self.done.emit(out)


# ── 可框选图片标签 / Crop-drawing image label ─────────────────────────────────


class _CropLabel(QLabel):
    """
    承载缩放后像素图的 QLabel,手动模式下支持鼠标拖拽绘制裁剪矩形(红虚线),
    松手发出 crop_drawn(label 内矩形)。移植自 crop_advisor_dialog._PreviewLabel。

    QLabel holding the zoomed pixmap; in manual mode supports drag-to-draw a crop
    rectangle (red dashed) and emits crop_drawn (label-local rect) on release.
    Ported from crop_advisor_dialog._PreviewLabel.
    """

    crop_drawn: Signal = Signal(QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.drawing_enabled: bool = False
        self._drag_start: Optional[QPoint] = None
        self._drag_end: Optional[QPoint] = None
        self._drawing: bool = False
        # 平移(hand)状态;非手动模式下拖拽=移动画面 / pan state (hand tool)
        self._scroll_ref: Optional[QScrollArea] = None
        self._panning: bool = False
        self._pan_origin: Optional[QPoint] = None
        self._pan_h0: int = 0
        self._pan_v0: int = 0

    def refresh_cursor(self) -> None:
        """按模式设置光标:手动=十字;否则=可抓取的手型。"""
        if self.pixmap() is None or self.pixmap().isNull():
            self.unsetCursor()
        elif self.drawing_enabled:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def pixmap_rect(self) -> QRect:
        """当前像素图在标签内的矩形(扣除 2px 白边,居中)。"""
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return QRect()
        pw, ph = pm.width(), pm.height()
        ox = (self.width() - pw) // 2
        oy = (self.height() - ph) // 2
        return QRect(ox, oy, pw, ph)

    def clear_rubber_band(self) -> None:
        """清除已绘橡皮筋。"""
        self._drag_start = self._drag_end = None
        self._drawing = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            if self.drawing_enabled:
                self._drag_start = event.position().toPoint()
                self._drag_end = None
                self._drawing = True
                self.update()
            elif self._scroll_ref is not None:
                # 非手动模式:开始平移(抓取画面)/ start panning (grab the image)
                self._panning = True
                self._pan_origin = event.globalPosition().toPoint()
                self._pan_h0 = self._scroll_ref.horizontalScrollBar().value()
                self._pan_v0 = self._scroll_ref.verticalScrollBar().value()
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drawing_enabled and self._drawing and (event.buttons() & Qt.LeftButton):
            self._drag_end = event.position().toPoint()
            self.update()
        elif self._panning and (event.buttons() & Qt.LeftButton) and self._scroll_ref is not None:
            delta = event.globalPosition().toPoint() - self._pan_origin
            self._scroll_ref.horizontalScrollBar().setValue(self._pan_h0 - delta.x())
            self._scroll_ref.verticalScrollBar().setValue(self._pan_v0 - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.drawing_enabled and event.button() == Qt.LeftButton and self._drawing:
            self._drag_end = event.position().toPoint()
            self._drawing = False
            self.update()
            if self._drag_start is not None and self._drag_end is not None:
                rect = QRect(self._drag_start, self._drag_end).normalized()
                if rect.width() > 4 and rect.height() > 4:
                    self.crop_drawn.emit(rect)
        elif self._panning and event.button() == Qt.LeftButton:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self.drawing_enabled and self._drag_start is not None and self._drag_end is not None:
            rect = QRect(self._drag_start, self._drag_end).normalized()
            painter = QPainter(self)
            painter.setPen(QPen(Qt.red, 2, Qt.DashLine))
            painter.drawRect(rect)
            painter.end()


# ── 看图画布 / Image canvas ───────────────────────────────────────────────────


class _Canvas(QWidget):
    """
    深灰看图画布:图片居中、fit-to-window、可缩放(滑块/按钮)、白边 + 投影「裱框」感,
    底部浮动 zoom 工具条(− / 滑块 / + / 百分比 / 适应 / 1:1 / 看原图)。

    Neutral-gray viewing canvas: image centered, fit-to-window, zoomable
    (slider/buttons), framed with a 2px white border + drop shadow, plus a
    floating bottom zoom bar (− / slider / + / percent / fit / 1:1 / peek).
    缩放因子 self._zoom 以「源像素图实际像素」为基准:1.0 = 100%(1:1)。
    """

    zoom_changed: Signal = Signal(float)   # 当前缩放因子(0.68 表示 68%)
    peek_changed: Signal = Signal(bool)    # 按住「看原图」(True=按下)
    manual_crop: Signal = Signal(QRect)    # 手动框选(label 内矩形)

    PCT_MIN: int = 10
    PCT_MAX: int = 400

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._src: Optional[QPixmap] = None  # 源像素图(当前显示图的全尺寸)
        self._zoom: float = 1.0              # 缩放因子(相对源像素图实际像素)

        # ── 可滚动图片视口(支持 1:1 超出时滚动)/ Scrollable image viewport ──
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {CANVAS_BG}; border: none; }}"
            f" QScrollArea > QWidget > QWidget {{ background: {CANVAS_BG}; }}"
        )

        self._img = _CropLabel()
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet("border: 2px solid #ffffff; background: transparent;")
        self._img.crop_drawn.connect(self.manual_crop)  # 转发手动框选 / forward manual rect
        shadow = QGraphicsDropShadowEffect(self._img)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._img.setGraphicsEffect(shadow)
        self._scroll.setWidget(self._img)
        self._img._scroll_ref = self._scroll  # 供 hand 平移用 / for pan

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

        self._zoom_bar = self._build_zoom_bar()
        self._zoom_bar.setParent(self)
        self._zoom_bar.raise_()
        self._zoom_bar.hide()  # 有图后再显示 / shown once an image is set

    # ── 浮动 zoom 工具条 / Floating zoom bar ──────────────────────────────────

    def _build_zoom_bar(self) -> QWidget:
        """构建底部浮动 zoom 工具条。"""
        bar = QFrame()
        bar.setObjectName("zoomBar")
        bar.setStyleSheet(
            "QFrame#zoomBar { background: rgba(20,20,20,230); border-radius: 18px; }"
            "QPushButton { background: transparent; border: none; padding: 4px; }"
            "QLabel { color: #e8e8ea; font-size: 12px; background: transparent; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)

        self._btn_minus = self._icon_button("minus.svg", self.zoom_out)
        h.addWidget(self._btn_minus)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setFixedWidth(160)
        self._slider.setRange(self.PCT_MIN, self.PCT_MAX)
        self._slider.setValue(100)
        self._slider.valueChanged.connect(self._on_slider)
        h.addWidget(self._slider)

        self._btn_plus = self._icon_button("plus.svg", self.zoom_in)
        h.addWidget(self._btn_plus)

        self._pct_lbl = QLabel("100%")
        self._pct_lbl.setFixedWidth(44)
        self._pct_lbl.setAlignment(Qt.AlignCenter)
        h.addWidget(self._pct_lbl)

        self._btn_fit = self._icon_button("fullscreen.svg", self.fit)
        h.addWidget(self._btn_fit)

        self._btn_one = QPushButton("1:1")
        self._btn_one.setStyleSheet("color: #e8e8ea; font-size: 12px; background: transparent; border: none;")
        self._btn_one.setCursor(Qt.PointingHandCursor)
        self._btn_one.clicked.connect(self.actual_size)
        h.addWidget(self._btn_one)

        self._btn_eye = self._icon_button("eye.svg", None)
        self._btn_eye.pressed.connect(lambda: self.peek_changed.emit(True))
        self._btn_eye.released.connect(lambda: self.peek_changed.emit(False))
        h.addWidget(self._btn_eye)

        bar.adjustSize()
        return bar

    def _icon_button(self, svg: str, on_click) -> QPushButton:
        """构建一个染色 SVG 图标按钮。"""
        btn = QPushButton()
        btn.setIcon(load_tinted_icon(svg, ICON_IDLE, 18))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(28, 28)
        if on_click is not None:
            btn.clicked.connect(on_click)
        return btn

    # ── 图像与缩放 / Image & zoom ─────────────────────────────────────────────

    def set_image(self, bgr: np.ndarray) -> None:
        """设置画布显示的图像(BGR),并 fit-to-window。"""
        self._src = _bgr_to_qpixmap(bgr)
        self._img.clear_rubber_band()  # 换图后旧框坐标失效 / old rect invalid on new image
        self._zoom_bar.show()
        self.fit()
        self._img.refresh_cursor()

    def set_manual_enabled(self, enabled: bool) -> None:
        """开关手动框选(显示可拖拽红框);手动=十字光标,否则=手型平移。"""
        self._img.drawing_enabled = enabled
        if not enabled:
            self._img.clear_rubber_band()
        self._img.refresh_cursor()

    def displayed_pixmap_rect(self) -> QRect:
        """当前显示像素图在内部 label 内的矩形(供手动坐标映射)。"""
        return self._img.pixmap_rect()

    def _apply(self) -> None:
        """按当前缩放因子渲染源图到内部 QLabel。"""
        if self._src is None or self._src.isNull():
            return
        w = max(1, int(round(self._src.width() * self._zoom)))
        h = max(1, int(round(self._src.height() * self._zoom)))
        scaled = self._src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._img.setPixmap(scaled)
        self._img.adjustSize()
        self._img.clear_rubber_band()  # 缩放后旧框坐标失效 / old rect invalid after rescale
        self._sync_controls()

    def _sync_controls(self) -> None:
        """同步滑块/百分比显示(屏蔽信号防回环)。"""
        pct = int(round(self._zoom * 100))
        self._pct_lbl.setText(f"{pct}%")
        blocked = self._slider.blockSignals(True)
        self._slider.setValue(max(self.PCT_MIN, min(self.PCT_MAX, pct)))
        self._slider.blockSignals(blocked)

    def set_zoom(self, factor: float, focal: Optional[QPoint] = None) -> None:
        """
        设置绝对缩放因子(1.0 = 1:1),夹到 [PCT_MIN, PCT_MAX]。
        focal 为视口坐标的锚点:缩放后该点下的图像内容保持不动(用于「向光标缩放」);
        None 时以视口中心为锚点(居中缩放)。
        """
        factor = max(self.PCT_MIN / 100.0, min(self.PCT_MAX / 100.0, float(factor)))
        if self._src is None or self._src.isNull():
            self._zoom = factor
            self._apply()
            self.zoom_changed.emit(self._zoom)
            return

        vp = self._scroll.viewport()
        hbar, vbar = self._scroll.horizontalScrollBar(), self._scroll.verticalScrollBar()
        if focal is None:
            focal = QPoint(vp.width() // 2, vp.height() // 2)

        # 缩放前:求 focal 下的图像内容点在 label 内的归一化位置 / content fraction under focal
        old_lw, old_lh = max(1, self._img.width()), max(1, self._img.height())
        ox = -hbar.value() if old_lw > vp.width() else (vp.width() - old_lw) // 2
        oy = -vbar.value() if old_lh > vp.height() else (vp.height() - old_lh) // 2
        fx = (focal.x() - ox) / old_lw
        fy = (focal.y() - oy) / old_lh

        self._zoom = factor
        self._apply()  # 重排 label 尺寸 / resizes the label

        # 缩放后:调滚动条让同一内容点仍落在 focal 处 / keep that content point under focal
        new_lw, new_lh = max(1, self._img.width()), max(1, self._img.height())
        hbar.setValue(int(round(fx * new_lw - focal.x())))
        vbar.setValue(int(round(fy * new_lh - focal.y())))
        self.zoom_changed.emit(self._zoom)

    def fit(self) -> None:
        """适应窗口:按视口尺寸等比缩放(可放大至上限,可缩小)。"""
        if self._src is None or self._src.isNull():
            return
        vp = self._scroll.viewport().size()
        sw, sh = self._src.width(), self._src.height()
        if sw <= 0 or sh <= 0 or vp.width() <= 0 or vp.height() <= 0:
            self.set_zoom(1.0)
            return
        # 留一点边距,避免贴边 / leave a small margin so it doesn't touch edges
        factor = min((vp.width() - 24) / sw, (vp.height() - 24) / sh)
        self.set_zoom(max(self.PCT_MIN / 100.0, factor))

    def actual_size(self) -> None:
        """1:1 实际像素。"""
        self.set_zoom(1.0)

    def zoom_in(self) -> None:
        """放大一档(×1.25)。"""
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        """缩小一档(×0.8)。"""
        self.set_zoom(self._zoom * 0.8)

    def _on_slider(self, value: int) -> None:
        """滑块拖动 → 设置缩放。"""
        self.set_zoom(value / 100.0)

    # ── 浮动条定位 / Floating bar positioning ─────────────────────────────────

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        """滚轮缩放,以光标位置为锚点(向光标缩放)。"""
        if self._src is None or self._src.isNull():
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 1.15 if delta > 0 else 1.0 / 1.15
        self.set_zoom(self._zoom * step, focal=event.position().toPoint())
        event.accept()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """重定位浮动 zoom 工具条到底部居中。"""
        super().resizeEvent(event)
        self._reposition_zoom_bar()

    def _reposition_zoom_bar(self) -> None:
        """把 zoom 工具条放到画布底部居中。"""
        bw = self._zoom_bar.sizeHint().width()
        bh = self._zoom_bar.sizeHint().height()
        self._zoom_bar.resize(bw, bh)
        x = (self.width() - bw) // 2
        y = self.height() - bh - 18
        self._zoom_bar.move(max(0, x), max(0, y))


# ── 候选格 / Candidate cell ───────────────────────────────────────────────────

# 候选缩略图画框等高(像素)/ Candidate thumbnail frame height (px).
_CAND_THUMB_H: int = 68
# 候选缩略图画框宽(双列布局下每格内图宽)/ Thumbnail frame width per column.
_CAND_THUMB_W: int = 132


class _CandCell(QFrame):
    """
    单个候选缩略图格:letterbox 居中缩略图 + 比例名/TOPIQ 文字 + 首位「最佳」角标。
    点击发出 clicked(index)。选中时边框高亮。

    One candidate thumbnail cell: a letterbox-centered preview + ratio/TOPIQ
    caption + an optional "Best" badge on the top candidate. Emits clicked(index).
    """

    clicked: Signal = Signal(int)

    def __init__(self, index: int, suggestion: CropSuggestion, caption: str,
                 is_best: bool, best_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._index = index
        self.setProperty("selected", "false")
        self.setCursor(Qt.PointingHandCursor)
        accent = _c("accent", "#00d4aa")
        border = _c("border", "#2a2a2a")
        card = _c("bg_elevated", "#1a1a1a")
        self.setStyleSheet(
            f"_CandCell {{ background: {card}; border: 2px solid {border}; border-radius: 8px; }}"
            f'_CandCell[selected="true"] {{ border: 2px solid {accent}; }}'
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(3)

        thumb = QLabel()
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if suggestion.preview_bgr is not None:
            thumb.setPixmap(_letterbox_pixmap(suggestion.preview_bgr, _CAND_THUMB_W, _CAND_THUMB_H))
        thumb.setFixedHeight(_CAND_THUMB_H)
        v.addWidget(thumb)

        cap = QLabel(caption)
        cap.setAlignment(Qt.AlignCenter)
        cap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        cap.setStyleSheet(
            "border: none; background: transparent; font-size: 11px; "
            f"font-weight: {'700' if is_best else '500'}; "
            f"color: {accent if is_best else _c('text_secondary', '#a1a1a1')};"
        )
        v.addWidget(cap)

        if is_best:
            badge = QLabel(best_text, self)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            badge.setStyleSheet(
                f"background: {accent}; color: #0a0a0a; font-size: 10px; font-weight: 700;"
                " border-radius: 4px; padding: 1px 5px;"
            )
            badge.move(8, 8)
            badge.adjustSize()

    def set_selected(self, selected: bool) -> None:
        """切换选中态边框高亮。"""
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._index)
        super().mousePressEvent(event)


# ── 导出设置对话框 / Export settings dialog ───────────────────────────────────


class _ExportDialog(QDialog):
    """
    导出设置:目标尺寸(宽/高,可锁定比例)+ JPEG 质量。类似 PS 的「图像大小 + 存储质量」。
    Export settings: output size (width/height, aspect-lockable) + JPEG quality.
    """

    def __init__(self, i18n, native_w: int, native_h: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        self._native_w = max(1, int(native_w))
        self._native_h = max(1, int(native_h))
        self._aspect = self._native_w / self._native_h
        self._syncing = False  # 防止宽高联动递归 / guard against width/height feedback

        self.setWindowTitle(i18n.t("crop_studio.export_settings"))
        self.setStyleSheet(self._build_qss())

        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 16)
        form.setSpacing(12)

        info = QLabel(f"{i18n.t('crop_studio.crop_size')}: {self._native_w} × {self._native_h}")
        info.setObjectName("infoLbl")
        form.addRow(info)

        self._w_spin = QSpinBox()
        self._w_spin.setRange(1, 200000)
        self._w_spin.setValue(self._native_w)
        self._w_spin.setSuffix(" px")
        self._w_spin.valueChanged.connect(self._on_width_changed)
        form.addRow(i18n.t("crop_studio.width"), self._w_spin)

        self._h_spin = QSpinBox()
        self._h_spin.setRange(1, 200000)
        self._h_spin.setValue(self._native_h)
        self._h_spin.setSuffix(" px")
        self._h_spin.valueChanged.connect(self._on_height_changed)
        form.addRow(i18n.t("crop_studio.height"), self._h_spin)

        self._lock = QCheckBox(i18n.t("crop_studio.lock_ratio"))
        self._lock.setChecked(True)
        form.addRow("", self._lock)

        qrow = QHBoxLayout()
        self._q_slider = QSlider(Qt.Horizontal)
        self._q_slider.setRange(1, 100)
        self._q_slider.setValue(95)
        self._q_lbl = QLabel("95")
        self._q_lbl.setFixedWidth(32)
        self._q_slider.valueChanged.connect(lambda v: self._q_lbl.setText(str(v)))
        qrow.addWidget(self._q_slider)
        qrow.addWidget(self._q_lbl)
        form.addRow(i18n.t("crop_studio.quality"), qrow)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _build_qss(self) -> str:
        bg = _c("bg_elevated", "#1a1a1a")
        text = _c("text_primary", "#fafafa")
        card = _c("bg_input", "#262626")
        border = _c("border", "#2a2a2a")
        accent = _c("accent", "#00d4aa")
        return (
            f"QDialog {{ background: {bg}; }}"
            f" QLabel {{ color: {text}; font-size: 13px; background: transparent; }}"
            f" QLabel#infoLbl {{ color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; }}"
            f" QSpinBox {{ background: {card}; color: {text}; border: 1px solid {border};"
            f" border-radius: 6px; padding: 4px 8px; }}"
            f" QCheckBox {{ color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; }}"
            f" QPushButton {{ color: {text}; background: {card}; border: 1px solid {border};"
            f" border-radius: 7px; padding: 6px 16px; }}"
            f" QPushButton:hover {{ border-color: {accent}; }}"
        )

    def _on_width_changed(self, w: int) -> None:
        if self._syncing or not self._lock.isChecked():
            return
        self._syncing = True
        self._h_spin.setValue(max(1, round(w / self._aspect)))
        self._syncing = False

    def _on_height_changed(self, h: int) -> None:
        if self._syncing or not self._lock.isChecked():
            return
        self._syncing = True
        self._w_spin.setValue(max(1, round(h * self._aspect)))
        self._syncing = False

    def values(self) -> tuple:
        """返回 (out_size 或 None, jpeg_quality)。尺寸等于原始时返回 None(不重采样)。"""
        w, h = self._w_spin.value(), self._h_spin.value()
        out_size = None if (w == self._native_w and h == self._native_h) else (w, h)
        return out_size, self._q_slider.value()


# ── 工作区主体 / Workspace widget ─────────────────────────────────────────────


class _BeforeAfterView(QWidget):
    """
    左右对比控件:中间竖线揭示 before(左)/after(右),支持 适应/100% 与平移。

    交互 / Interaction:
      - 鼠标悬停移动 → 分割线跟随光标 X 扫动(无需按下)。
      - 100% 模式下按住拖动 → 平移(看 1:1 像素细节,判断降噪是否真起作用)。
      - 适应(fit)模式:整图缩放铺满;100% 模式:原生像素 1:1 显示。

    Divider follows the hovering cursor; in 100% mode drag to pan and inspect
    true 1:1 pixels (so you can tell whether denoise actually worked).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._before: Optional[QPixmap] = None
        self._after: Optional[QPixmap] = None
        self._split: float = 0.5      # 分割线位置 0..1 / divider position
        self._fit: bool = True        # True=适应, False=100% / fit vs 1:1
        self._off: QPoint = QPoint(0, 0)   # 100% 模式平移原点 / pan origin
        self._last: QPoint = QPoint(0, 0)  # 拖动上一位置 / last drag pos
        self._drag_mode: Optional[str] = None  # None / "divider" / "pan"
        self.setMinimumSize(200, 200)
        self.setMouseTracking(True)   # 仅用于光标提示,不移动分割线 / cursor hint only
        self.setStyleSheet("background: #111111;")

    def set_images(self, before_bgr, after_bgr) -> None:
        """设置 before 与 after 两张图(BGR);保持当前缩放/平移。"""
        self._before = _bgr_to_qpixmap(before_bgr)
        self._after = _bgr_to_qpixmap(after_bgr)
        if not self._fit:
            self._recenter_100()
        self.update()

    def set_after(self, after_bgr) -> None:
        """仅更新 after(降噪结果),before 不变。"""
        self._after = _bgr_to_qpixmap(after_bgr)
        self.update()

    def clear(self) -> None:
        """清空两图。"""
        self._before = self._after = None
        self.update()

    def set_zoom(self, fit: bool) -> None:
        """切换 适应/100%;切到 100% 时居中。"""
        self._fit = fit
        self.setCursor(Qt.SplitHCursor if fit else Qt.OpenHandCursor)
        if not fit:
            self._recenter_100()
        self.update()

    def _pixmap(self) -> Optional[QPixmap]:
        return self._before or self._after

    def _recenter_100(self) -> None:
        """100% 模式把图居中。"""
        pm = self._pixmap()
        if pm is None or pm.isNull():
            return
        self._off = QPoint((self.width() - pm.width()) // 2,
                           (self.height() - pm.height()) // 2)

    def _clamp_off(self) -> None:
        """约束平移,避免把图拖出视野。"""
        pm = self._pixmap()
        if pm is None or pm.isNull():
            return
        ww, wh, iw, ih = self.width(), self.height(), pm.width(), pm.height()
        x = (ww - iw) // 2 if iw <= ww else min(0, max(ww - iw, self._off.x()))
        y = (wh - ih) // 2 if ih <= wh else min(0, max(wh - ih, self._off.y()))
        self._off = QPoint(x, y)

    def _displayed_rect(self) -> QRect:
        """图像在控件内的目标矩形(适应=缩放居中;100%=原生尺寸+平移)。"""
        pm = self._pixmap()
        if pm is None or pm.isNull():
            return QRect()
        ww, wh, iw, ih = max(1, self.width()), max(1, self.height()), pm.width(), pm.height()
        if self._fit:
            scale = min(ww / iw, wh / ih)
            dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))
            return QRect((ww - dw) // 2, (wh - dh) // 2, dw, dh)
        self._clamp_off()
        return QRect(self._off, pm.size())  # 原生尺寸 1:1 / native size

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111111"))
        rect = self._displayed_rect()
        if rect.isEmpty():
            p.end()
            return
        # after 铺满图像区,before 仅画分割线左侧 / after full, before clipped left.
        if self._after is not None:
            p.drawPixmap(rect, self._after, self._after.rect())
        split_x = rect.left() + int(rect.width() * self._split)
        if self._before is not None:
            p.save()
            p.setClipRect(QRect(rect.left(), rect.top(),
                                max(0, split_x - rect.left()), rect.height()))
            p.drawPixmap(rect, self._before, self._before.rect())
            p.restore()
        # 分割线 + 圆形手柄(限定在控件可视高度) / divider line + handle
        top = max(rect.top(), 0)
        bot = min(rect.bottom(), self.height())
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawLine(split_x, top, split_x, bot)
        cy = (top + bot) // 2
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRect(split_x - 9, cy - 9, 18, 18))
        # 角标:左「原图」右「降噪」/ corner labels
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(8, 20, "Before")
        p.drawText(self.width() - 48, 20, "After")
        p.end()

    _GRAB_PX = 16  # 抓取分割线的像素阈值 / grab threshold around the divider

    def _split_x(self) -> int:
        """当前分割线在控件内的 x 像素 / divider x in widget pixels."""
        rect = self._displayed_rect()
        if rect.isEmpty():
            return -1
        return rect.left() + int(rect.width() * self._split)

    def _set_split_from_x(self, x: float) -> None:
        rect = self._displayed_rect()
        if rect.isEmpty() or rect.width() <= 0:
            return
        self._split = min(max((x - rect.left()) / rect.width(), 0.0), 1.0)
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # type: ignore[override]
        # 按在分割线附近 → 拖动线;否则(仅 100%)→ 平移。其余情况不动。
        # On the divider → drag it; otherwise (100% only) → pan. Else nothing.
        x = e.position().x()
        if abs(x - self._split_x()) <= self._GRAB_PX:
            self._drag_mode = "divider"
            self.setCursor(Qt.SplitHCursor)
        elif not self._fit:
            self._drag_mode = "pan"
            self._last = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            self._drag_mode = None

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # type: ignore[override]
        if e.buttons() and self._drag_mode == "divider":
            # 仅在按住线拖动时移动分割线 / move divider only while dragging it
            self._set_split_from_x(e.position().x())
        elif e.buttons() and self._drag_mode == "pan":
            cur = e.position().toPoint()
            self._off = self._off + (cur - self._last)
            self._last = cur
            self._clamp_off()
            self.update()
        elif not e.buttons():
            # 悬停只更新光标提示,不移动任何东西 / cursor hint only, move nothing
            near = abs(e.position().x() - self._split_x()) <= self._GRAB_PX
            self.setCursor(Qt.SplitHCursor if near else
                           (Qt.OpenHandCursor if not self._fit else Qt.ArrowCursor))

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_mode = None
        near = abs(e.position().x() - self._split_x()) <= self._GRAB_PX
        self.setCursor(Qt.SplitHCursor if near else
                       (Qt.OpenHandCursor if not self._fit else Qt.ArrowCursor))


class CropStudio(QWidget):
    """
    全屏后期工作区。由结果浏览器在「裁剪建议」入口构造并 showFullScreen()。
    非破坏性:导出写新文件,绝不覆盖原图。

    Fullscreen post-processing workspace, constructed by the results browser at
    the "crop advice" entry point and shown via showFullScreen().

    参数 / Parameters:
        photo (dict): 照片记录,含 current_path / temp_jpeg_path / original_path /
                      bird_species_cn / bird_species_en / rating / gbif_tier /
                      iucn_category / filename 等键。
        i18n: 国际化实例(get_i18n() 返回的 I18n)。
        parent (QWidget | None): 父窗口。
    """

    export_requested: Signal = Signal(dict)  # 导出请求(Task 7 接线)/ export wiring in Task 7
    closed: Signal = Signal()                # 返回 / 关闭信号
    edit_species_requested: Signal = Signal(dict)  # 「鸟种」改名(由浏览器接线)
    delete_requested: Signal = Signal(dict)        # 「删除」(由浏览器接线)

    def __init__(self, photo: dict, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # 即便有 parent 也作为独立顶层窗口,以便 showFullScreen() 全屏显示。
        # Be a top-level window even with a parent, so showFullScreen() works.
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle(i18n.t("crop_advisor.title"))
        self._photo: dict = photo
        self._i18n = i18n

        # 当前选中裁剪框(全分辨率坐标);None = 原图不裁剪。导出用。
        # Currently selected crop box (full-res coords); None = full frame. For export.
        self._current_box = None
        self._mode: str = "crop"  # crop | manual | auto

        # 候选状态 / Candidate state
        self._suggestions: list[CropSuggestion] = []
        self._cells: list[_CandCell] = []
        self._selected_index: int = -1
        # 分析图尺寸(box 坐标所在空间;供导出换算到原图全分辨率)/
        # Analysis-image size (the coordinate space of candidate boxes); used by
        # export to rescale to the full-res original.
        self._analysis_size: Optional[tuple[int, int]] = None

        # 手动裁剪状态 / Manual crop state
        self._analysis_bgr: Optional[np.ndarray] = None  # 懒加载的分析图(打分/映射用)
        self._manual_box: Optional[tuple] = None         # 最近一次手动框(分析图坐标)
        self._topiq_fn = None                            # 可注入打分函数(测试用)

        self._image_path: str = self._resolve_image_path(photo)

        self.setStyleSheet(f"QWidget {{ background: {_c('bg_primary', '#111111')}; }}")

        # ── 顶层垂直布局:顶栏 + 主行 / Root vertical: top bar + main row ──────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._top_bar = self._build_top_bar()
        root.addWidget(self._top_bar)

        # 自动修图微调条:默认隐藏,点左栏「智能修图」展开 / hidden enhance tune bar.
        self._enhance_panel = self._build_enhance_panel()
        self._enhance_panel.hide()
        root.addWidget(self._enhance_panel)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)

        self._toolbar = self._build_toolbar()
        main_row.addWidget(self._toolbar)

        # 中央区:裁剪画布 与 降噪对比视图 用 QStackedWidget 切换。
        # Center: a stack toggling between the crop canvas and the denoise compare view.
        self._canvas = _Canvas()
        self._canvas.manual_crop.connect(self._on_manual_crop)
        self._compare_view = _BeforeAfterView()
        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(self._canvas)        # index 0 = 裁剪 / crop
        self._center_stack.addWidget(self._compare_view)  # index 1 = 对比 / compare
        main_row.addWidget(self._center_stack, 1)

        self._cand_panel = self._build_candidate_panel()
        main_row.addWidget(self._cand_panel)

        root.addLayout(main_row, 1)

        # ── 启动后台候选加载 / Start background advice loading ─────────────────
        self._worker = _AdviceWorker(self._image_path)
        self._worker.done.connect(self._on_advice)
        self._worker.start()

    # ── 路径解析 / Path resolution ────────────────────────────────────────────

    def _resolve_image_path(self, photo: dict) -> str:
        """
        解析用于「分析/预览」的可解码图片路径,优先级与结果浏览器入口一致:
        temp_jpeg_path → current_path → original_path(取第一个存在的;都不存在时
        回退到第一个非空值,便于 headless 测试)。注意:导出走原图全分辨率,不用此路径。

        Resolve the decodable image path for analysis/preview, prioritizing
        temp_jpeg_path → current_path → original_path (first that exists; falls
        back to the first non-empty value for headless tests). Export uses the
        full-res original separately, not this path.
        """
        candidates = [
            photo.get("temp_jpeg_path"),
            photo.get("current_path"),
            photo.get("original_path"),
        ]
        candidates = [p for p in candidates if p]
        for p in candidates:
            if os.path.exists(p):
                return p
        return candidates[0] if candidates else ""

    # ── 骨架占位构件(后续任务填充)/ Skeleton placeholders (filled later) ─────

    def _is_zh(self) -> bool:
        """当前是否中文界面。"""
        return not str(getattr(self._i18n, "current_lang", "")).startswith("en")

    def _build_top_bar(self) -> QWidget:
        """顶栏:鸟种名 + 文件名(灰) + 星级 + 罕见度 pill + IUCN pill + 返回 + 导出。"""
        p = self._photo
        is_zh = self._is_zh()
        bar = QFrame()
        bar.setObjectName("cropStudioTopBar")
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame#cropStudioTopBar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-bottom: 1px solid {_c('border', '#2a2a2a')}; }}"
            " QLabel { background: transparent; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 14, 0)
        h.setSpacing(12)

        # 鸟种名 / Species name
        if is_zh:
            species = p.get("bird_species_cn") or p.get("bird_species_en") or "—"
        else:
            species = p.get("bird_species_en") or p.get("bird_species_cn") or "—"
        sp_lbl = QLabel(species)
        sp_lbl.setStyleSheet(
            f"color: {_c('text_primary', '#fafafa')}; font-size: 16px; font-weight: 700;"
        )
        h.addWidget(sp_lbl)

        # 文件名(灰)/ File name (gray)
        from ui.detail_panel import _display_filename
        fn_lbl = QLabel(_display_filename(p) or p.get("filename", ""))
        fn_lbl.setObjectName("cropStudioFilename")
        fn_lbl.setStyleSheet(f"color: {_c('text_tertiary', '#909090')}; font-size: 12px;")
        h.addWidget(fn_lbl)

        # 星级(SVG 金星;精选→皇冠)/ Rating stars (crown when picked)
        rating = p.get("rating", 0)
        star_lbl = QLabel()
        if p.get("picked"):
            star_lbl.setPixmap(load_tinted_icon("crown.svg", _c("star_gold", "#d4a800"), 18).pixmap(QSize(18, 18)))
        elif isinstance(rating, int) and rating >= 1:
            star_lbl.setPixmap(stars_pixmap(rating, _c("star_gold", "#d4a800"), size=16))
        h.addWidget(star_lbl)
        self._star_label = star_lbl  # 测试断言用

        # 罕见度 pill:仅「少见及以上」(tier≥2)加描边框强调,常见/能见用纯文字。
        # Rarity: box only uncommon-and-rarer (tier>=2); common/occasional are plain text.
        gbif_r = p.get("gbif_rarity_100")
        tidx = gbif_score_to_tier(gbif_r) if gbif_r is not None else None
        if tidx is not None:
            color = tier_color(tidx) or _c("text_secondary", "#a1a1a1")
            pill = QLabel(f"{tier_icon(tidx)} {tier_name(tidx, is_zh=is_zh)}")
            pill.setStyleSheet(self._pill_qss(color, boxed=tidx >= 2))
            h.addWidget(pill)

        # IUCN pill:仅「受威胁」级(VU/EN/CR/EW/EX)加描边框,无危/近危等用纯文字。
        # IUCN: box only threatened categories; LC/NT/etc. are plain colored text.
        iucn = p.get("iucn_category")
        if iucn:
            from ui.detail_panel import _format_iucn
            text, color = _format_iucn(iucn, is_zh)
            threatened = str(iucn).upper() in {"VU", "EN", "CR", "EW", "EX"}
            iucn_pill = QLabel(text)
            iucn_pill.setStyleSheet(self._pill_qss(color, boxed=threatened))
            h.addWidget(iucn_pill)

        h.addStretch(1)

        # 返回 / Back
        back_btn = QPushButton("  " + self._i18n.t("crop_studio.back"))
        back_btn.setIcon(load_tinted_icon("arrow-left.svg", ICON_IDLE, 16))
        back_btn.setIconSize(QSize(16, 16))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setStyleSheet(
            f"QPushButton {{ color: {_c('text_secondary', '#a1a1a1')}; background: transparent;"
            f" border: 1px solid {_c('border', '#2a2a2a')}; border-radius: 8px; padding: 7px 14px;"
            " font-size: 13px; }"
            f"QPushButton:hover {{ background: {_c('bg_card', '#1f1f1f')}; }}"
        )
        back_btn.clicked.connect(self.close)
        h.addWidget(back_btn)

        # 导出(金底)/ Export (gold)
        export_btn = QPushButton("  " + self._i18n.t("crop_studio.export"))
        export_btn.setIcon(load_tinted_icon("download.svg", "#0a0a0a", 16))
        export_btn.setIconSize(QSize(16, 16))
        export_btn.setCursor(Qt.PointingHandCursor)
        gold = _c("star_gold", "#d4a800")
        export_btn.setStyleSheet(
            f"QPushButton {{ color: #0a0a0a; background: {gold}; border: none;"
            " border-radius: 8px; padding: 7px 16px; font-size: 13px; font-weight: 700; }"
            "QPushButton:hover { background: #e8c020; }"
        )
        export_btn.clicked.connect(self._on_export_clicked)
        h.addWidget(export_btn)
        self._export_btn = export_btn
        return bar

    def _pill_qss(self, color: str, boxed: bool = True) -> str:
        """罕见度/IUCN 标签样式。boxed=True 带描边框强调;否则纯彩色文字(无框)。"""
        if boxed:
            return (
                f"color: {color}; border: 1px solid {color}; border-radius: 9px;"
                " padding: 2px 9px; font-size: 11px; font-weight: 600;"
            )
        return (
            f"color: {color}; border: none; background: transparent;"
            " padding: 2px 4px; font-size: 12px; font-weight: 600;"
        )

    def _build_toolbar(self) -> QWidget:
        """左竖工具栏:图标 + 下方文字。裁剪/特写/鸟种/自动/删除(删除红、沉底)。"""
        tb = QFrame()
        tb.setObjectName("cropStudioToolbar")
        tb.setFixedWidth(72)
        tb.setStyleSheet(
            f"QFrame#cropStudioToolbar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-right: 1px solid {_c('border', '#2a2a2a')}; }}"
        )
        v = QVBoxLayout(tb)
        v.setContentsMargins(6, 12, 6, 12)
        v.setSpacing(6)

        self._tool_buttons: dict[str, QToolButton] = {}
        self._btn_crop = self._tool_btn("crop.svg", self._i18n.t("crop_studio.tb_crop"),
                                        lambda: self._set_mode("manual"))
        v.addWidget(self._btn_crop)
        self._btn_closeup = self._tool_btn("bird.svg", self._i18n.t("crop_studio.tb_closeup"),
                                           self._select_bird_only)
        v.addWidget(self._btn_closeup)
        self._btn_species = self._tool_btn("square-pen.svg", self._i18n.t("crop_studio.tb_species"),
                                           lambda: self.edit_species_requested.emit(self._photo))
        v.addWidget(self._btn_species)
        self._btn_auto = self._tool_btn("image-plus.svg", self._i18n.t("crop_studio.tb_auto"),
                                        lambda: self._set_mode("auto"))
        v.addWidget(self._btn_auto)
        self._btn_enhance = self._tool_btn("gem.svg", self._i18n.t("crop_studio.tb_enhance"),
                                           self._toggle_enhance_mode)
        v.addWidget(self._btn_enhance)

        v.addStretch(1)

        self._btn_delete = self._tool_btn("trash-2.svg", self._i18n.t("crop_studio.tb_delete"),
                                          lambda: self.delete_requested.emit(self._photo), danger=True)
        v.addWidget(self._btn_delete)
        return tb

    def _tool_btn(self, svg: str, text: str, on_click, *, danger: bool = False) -> QToolButton:
        """构建一个「图标在上、文字在下」的竖排工具按钮。"""
        btn = QToolButton()
        btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        btn.setIcon(load_tinted_icon(svg, ICON_DANGER if danger else ICON_IDLE, 22))
        btn.setIconSize(QSize(22, 22))
        btn.setText(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("svg", svg)
        btn.setProperty("danger", "true" if danger else "false")
        btn.setFixedSize(60, 52)
        color = ICON_DANGER if danger else _c("text_secondary", "#a1a1a1")
        btn.setStyleSheet(
            f"QToolButton {{ color: {color}; background: transparent; border: none;"
            " border-radius: 8px; font-size: 11px; }"
            f"QToolButton:hover {{ background: {_c('bg_card', '#1f1f1f')}; }}"
        )
        if on_click is not None:
            btn.clicked.connect(on_click)
        return btn

    def _build_candidate_panel(self) -> QWidget:
        """右候选面板:滚动区 + 双列网格 + 状态提示标签。"""
        panel = QFrame()
        panel.setObjectName("cropStudioCandidates")
        panel.setFixedWidth(320)
        panel.setStyleSheet(
            f"QFrame#cropStudioCandidates {{ background: {_c('bg_card', '#1f1f1f')};"
            f" border-left: 1px solid {_c('border', '#2a2a2a')}; }}"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 状态提示(no_bird / too_many_birds / 计算中)/ status hint
        self._cand_hint = QLabel(self._i18n.t("crop_advisor.computing"))
        self._cand_hint.setWordWrap(True)
        self._cand_hint.setAlignment(Qt.AlignCenter)
        self._cand_hint.setStyleSheet(
            f"color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; background: transparent;"
        )
        outer.addWidget(self._cand_hint)

        # 手动模式「存为候选」按钮(默认隐藏)/ Manual "save as candidate" (hidden by default)
        self._manual_save_btn = QPushButton(self._i18n.t("crop_studio.save_as_candidate"))
        self._manual_save_btn.setCursor(Qt.PointingHandCursor)
        self._manual_save_btn.setStyleSheet(
            f"QPushButton {{ color: {_c('accent', '#00d4aa')}; background: transparent;"
            f" border: 1px solid {_c('accent', '#00d4aa')}; border-radius: 8px; padding: 7px;"
            " font-size: 12px; font-weight: 600; }"
            f"QPushButton:hover {{ background: {_c('accent_dim', 'rgba(0,212,170,0.15)')}; }}"
            f"QPushButton:disabled {{ color: {ICON_DISABLED}; border-color: #242424; }}"
        )
        self._manual_save_btn.setEnabled(False)
        self._manual_save_btn.clicked.connect(self._save_manual_as_candidate)
        self._manual_save_btn.hide()
        outer.addWidget(self._manual_save_btn)

        self._cand_scroll = QScrollArea()
        self._cand_scroll.setFrameShape(QFrame.NoFrame)
        self._cand_scroll.setWidgetResizable(True)
        self._cand_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._cand_scroll.setStyleSheet("background: transparent; border: none;")
        self._cand_inner = QWidget()
        self._cand_grid = QGridLayout(self._cand_inner)
        self._cand_grid.setContentsMargins(0, 0, 0, 0)
        self._cand_grid.setSpacing(8)
        self._cand_grid.setAlignment(Qt.AlignTop)
        self._cand_scroll.setWidget(self._cand_inner)
        outer.addWidget(self._cand_scroll, 1)
        return panel

    # ── 后台结果回调 / Background result callback ─────────────────────────────

    def _on_advice(self, result: CropAdviceResult) -> None:
        """
        后台 advise_crops 完成后在主线程回调:status=ok 时填充双列候选并选中最优,
        画布显示该候选裁剪图;否则显示提示文案并在画布显示整图。

        Called on the UI thread when advise_crops finishes. On status=ok, populate
        the two-column candidates, select the best, and show its crop on the canvas;
        otherwise show a hint and the full image.
        """
        self._advice_result = result
        if result.status != "ok" or not result.suggestions:
            key = ("crop_advisor.too_many_birds" if result.status == "too_many_birds"
                   else "crop_advisor.no_bird")
            self._cand_hint.setText(self._i18n.t(key))
            self._cand_hint.show()
            self._show_full_on_canvas()
            return

        self._cand_hint.hide()
        self._suggestions = result.suggestions
        self._capture_analysis_size(result.suggestions)
        self._build_candidates()
        self._select_candidate(0)

    # ── 候选渲染与选择 / Candidate rendering & selection ──────────────────────

    def _label_for(self, s: CropSuggestion) -> str:
        """候选标签:哨兵显示本地化文案,其余显示比例字符串。"""
        if s.ratio_label == ORIGINAL_LABEL:
            return self._i18n.t("crop_advisor.original")
        if s.ratio_label == BIRD_ONLY_LABEL:
            return self._i18n.t("crop_advisor.bird_only")
        return s.ratio_label

    def _capture_analysis_size(self, suggestions: list) -> None:
        """从「原图」候选的框(0,0,W,H)推断分析图尺寸,供导出坐标换算。"""
        for s in suggestions:
            if s.ratio_label == ORIGINAL_LABEL:
                _x1, _y1, w, h = s.box
                self._analysis_size = (int(w), int(h))
                return
        # 兜底:取所有候选框的最大外延 / fallback: max extent of all boxes
        if suggestions:
            w = max(s.box[2] for s in suggestions)
            h = max(s.box[3] for s in suggestions)
            self._analysis_size = (int(w), int(h))

    def _build_candidates(self) -> None:
        """清空并重建右侧双列候选格。"""
        while self._cand_grid.count():
            item = self._cand_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cells = []

        best_text = self._i18n.t("crop_studio.best")
        for idx, s in enumerate(self._suggestions):
            cap = f"{self._label_for(s)} · {s.topiq_score:.2f}"
            cell = _CandCell(idx, s, cap, is_best=(idx == 0), best_text=best_text)
            cell.clicked.connect(self._select_candidate)
            self._cand_grid.addWidget(cell, idx // 2, idx % 2)
            self._cells.append(cell)

    def _select_candidate(self, index: int) -> None:
        """选中第 index 个候选:画布显示其裁剪图,记录全分辨率框(原图候选记 None)。"""
        if not (0 <= index < len(self._suggestions)):
            return
        s = self._suggestions[index]
        self._selected_index = index
        if s.preview_bgr is not None:
            self._canvas.set_image(s.preview_bgr)
        # 原图候选 → 导出不裁剪(None);其余记其框(分析图坐标,导出时换算)
        self._current_box = None if s.ratio_label == ORIGINAL_LABEL else s.box
        self._highlight_cells()

    def _highlight_cells(self) -> None:
        """根据当前选中索引高亮对应候选格边框。"""
        for i, cell in enumerate(self._cells):
            cell.set_selected(i == self._selected_index)

    def _show_full_on_canvas(self) -> None:
        """在画布显示整图(无候选/无鸟时)。解码失败则保持空白。"""
        from core.crop_advisor import _load_image_exif_aware
        img = _load_image_exif_aware(self._image_path)
        if img is not None:
            self._canvas.set_image(img)
            self._current_box = None

    def _select_bird_only(self) -> None:
        """「特写」:切回裁剪模式并选中纯鸟图候选(若有)。"""
        if self._mode != "crop":
            self._set_mode("crop")
        for i, s in enumerate(self._suggestions):
            if s.ratio_label == BIRD_ONLY_LABEL:
                self._select_candidate(i)
                return

    # ── 模式切换 / Mode switching ─────────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        """
        切换工作模式:
          crop   — 裁剪(默认):大图=选中候选裁剪图,右栏=候选。
          manual — 手动裁剪(Task 6 填充拖拽框):大图=整图,可拖拽框。
          auto   — 自动后期(本期占位,仅标记模式)。
        「裁剪」按钮在 manual 下高亮;再次点击回到 crop。
        """
        # 「裁剪」按钮作为 manual 的开关 / the Crop button toggles manual mode
        if mode == "manual" and self._mode == "manual":
            mode = "crop"
        self._mode = mode
        self._update_tool_active()

        if mode == "crop":
            self._canvas.set_manual_enabled(False)
            self._manual_save_btn.hide()
            if self._suggestions:
                self._cand_hint.hide()
                if 0 <= self._selected_index < len(self._suggestions):
                    self._select_candidate(self._selected_index)
        elif mode == "manual":
            self._enter_manual_mode()
        elif mode == "auto":
            # 自动后期占位:本期不改画布,仅提示即将推出
            self._canvas.set_manual_enabled(False)
            self._manual_save_btn.hide()
            self._cand_hint.setText(self._i18n.t("crop_studio.auto_coming_soon"))
            self._cand_hint.show()

    def _enter_manual_mode(self) -> None:
        """进入手动裁剪:画布显示整图 + 启用拖拽框 + 懒加载分析图供打分。"""
        self._ensure_analysis_bgr()
        if self._analysis_bgr is not None:
            self._canvas.set_image(self._analysis_bgr)
            self._current_box = None
        self._canvas.set_manual_enabled(True)
        self._cand_hint.setText(self._i18n.t("crop_advisor.manual_mode"))
        self._cand_hint.show()
        self._manual_save_btn.setEnabled(False)  # 画框后才可存 / enabled once a box is drawn
        self._manual_save_btn.show()

    def _ensure_analysis_bgr(self) -> None:
        """懒加载分析图(EXIF 感知);失败保持 None。"""
        if self._analysis_bgr is None:
            from core.crop_advisor import _load_image_exif_aware
            self._analysis_bgr = _load_image_exif_aware(self._image_path)
            if self._analysis_bgr is not None:
                h, w = self._analysis_bgr.shape[:2]
                self._analysis_size = (int(w), int(h))

    # ── 手动框选 → 原图坐标 + 实时 TOPIQ / Manual rect → coords + live TOPIQ ────

    def _on_manual_crop(self, label_rect: QRect) -> None:
        """
        手动拖拽释放:把 label 内矩形映射回分析图坐标,打分并记为当前导出框。
        映射:label 内像素图矩形 ↔ 分析图,按比例换算(zoom/降采样已并入像素图尺寸)。
        """
        if self._mode != "manual" or self._analysis_bgr is None:
            return
        pix_rect = self._canvas.displayed_pixmap_rect()
        if pix_rect.isEmpty():
            return
        clipped = label_rect.intersected(pix_rect)
        if clipped.isEmpty():
            return

        ah, aw = self._analysis_bgr.shape[:2]
        sx = aw / pix_rect.width()
        sy = ah / pix_rect.height()
        # QRect.right()/bottom() 为包含端点,+1 转 exclusive
        x1 = int((clipped.left() - pix_rect.left()) * sx)
        y1 = int((clipped.top() - pix_rect.top()) * sy)
        x2 = int((clipped.right() + 1 - pix_rect.left()) * sx)
        y2 = int((clipped.bottom() + 1 - pix_rect.top()) * sy)
        x1, x2 = max(0, min(x1, x2)), min(aw, max(x1, x2))
        y1, y2 = max(0, min(y1, y2)), min(ah, max(y1, y2))
        if x2 <= x1 or y2 <= y1:
            return

        box = (x1, y1, x2, y2)
        self._manual_box = box
        self._current_box = box  # 手动框直接作为导出框(分析图坐标)
        self._manual_save_btn.setEnabled(True)

        from core.crop_advisor import score_manual_crop
        try:
            score = score_manual_crop(self._analysis_bgr, box, self._topiq_fn)
        except Exception:  # noqa: BLE001 — 打分失败不影响框选 / scoring failure is non-fatal
            score = None
        label = self._i18n.t("crop_advisor.manual_score")
        self._cand_hint.setText(f"{label}: {score:.2f}" if score is not None else f"{label}: —")

    def _save_manual_as_candidate(self) -> None:
        """把最近手动框存为候选并选中(裁剪图取自分析图)。"""
        if self._manual_box is None or self._analysis_bgr is None:
            return
        x1, y1, x2, y2 = self._manual_box
        crop = self._analysis_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            return
        from core.crop_advisor import score_manual_crop
        try:
            score = score_manual_crop(self._analysis_bgr, self._manual_box, self._topiq_fn)
        except Exception:  # noqa: BLE001
            score = None
        sugg = CropSuggestion(
            ratio_label=f"{x2 - x1}:{y2 - y1}",
            box=self._manual_box,
            topiq_score=float(score) if score is not None else 0.0,
            preview_bgr=crop.copy(),
        )
        self._suggestions.append(sugg)
        new_index = len(self._suggestions) - 1
        # 回到裁剪模式并选中新候选 / back to crop mode, select the new candidate
        self._mode = "crop"
        self._update_tool_active()
        self._canvas.set_manual_enabled(False)
        self._manual_save_btn.hide()
        self._cand_hint.hide()
        self._build_candidates()
        self._select_candidate(new_index)

    def _update_tool_active(self) -> None:
        """根据当前模式高亮工具按钮(裁剪=manual,自动=auto)。"""
        accent = _c("accent", "#00d4aa")
        sub = _c("text_secondary", "#a1a1a1")
        for btn, on in (
            (getattr(self, "_btn_crop", None), self._mode == "manual"),
            (getattr(self, "_btn_auto", None), self._mode == "auto"),
        ):
            if btn is None:
                continue
            svg = btn.property("svg")
            btn.setIcon(load_tinted_icon(svg, ICON_ACTIVE if on else ICON_IDLE, 22))
            color = accent if on else sub
            btn.setStyleSheet(
                f"QToolButton {{ color: {color}; background: transparent; border: none;"
                " border-radius: 8px; font-size: 11px; }"
                f"QToolButton:hover {{ background: {_c('bg_card', '#1f1f1f')}; }}"
            )

    # ── 导出 / Export ─────────────────────────────────────────────────────────

    def _native_crop_size(self) -> tuple:
        """当前裁剪在源图(self._image_path)像素空间的原始尺寸 (宽,高)。"""
        if self._current_box is not None:
            x1, y1, x2, y2 = self._current_box
            return (max(1, x2 - x1), max(1, y2 - y1))
        if self._analysis_size is not None:
            return self._analysis_size
        try:  # 兜底:读源图尺寸(JPEG 头) / fallback: read source dims
            from PIL import Image
            with Image.open(self._image_path) as im:
                return im.size
        except Exception:  # noqa: BLE001
            return (0, 0)

    # ── 自动修图面板 / Auto-enhance panel ─────────────────────────────────────

    def _build_enhance_panel(self) -> QWidget:
        """
        构建降噪对比微调条(本期仅降噪,调色已隐藏):降噪滑块 + 提示 + 完成按钮。
        Build the denoise compare strip (denoise-only this phase; color hidden):
        a denoise slider + hint + a Done button.
        """
        bar = QFrame()
        bar.setObjectName("cropStudioEnhanceBar")
        bar.setStyleSheet(
            f"QFrame#cropStudioEnhanceBar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-bottom: 1px solid {_c('border', '#2a2a2a')}; }}"
            f" QLabel {{ color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)

        i18n = self._i18n

        def _slider(default: int) -> QSlider:
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 10)            # 10% 一档 / 10%-step
            s.setValue(default)
            s.setSingleStep(1)
            s.setPageStep(1)
            s.setTickPosition(QSlider.TicksBelow)
            s.setTickInterval(1)
            s.setFixedWidth(200)
            s.valueChanged.connect(self._request_preview)
            return s

        # 降噪组 / denoise group
        self._denoise_label = QLabel(i18n.t("crop_studio.denoise"))
        h.addWidget(self._denoise_label)
        self._denoise_slider = _slider(7)   # 70% 默认
        h.addWidget(self._denoise_slider)
        self._denoise_val_lbl = QLabel("70%")
        self._denoise_slider.valueChanged.connect(
            lambda v: self._denoise_val_lbl.setText(f"{v * 10}%"))
        h.addWidget(self._denoise_val_lbl)

        # 调色组(默认隐藏,进调色模式才显示) / color group (shown only in color mode)
        self._color_label = QLabel(i18n.t("crop_studio.color"))
        h.addWidget(self._color_label)
        self._color_slider = _slider(4)     # 40% 默认 / conservative
        h.addWidget(self._color_slider)
        self._color_val_lbl = QLabel("40%")
        self._color_slider.valueChanged.connect(
            lambda v: self._color_val_lbl.setText(f"{v * 10}%"))
        h.addWidget(self._color_val_lbl)

        # 状态:处理中 / 已更新(含平均像素差) / status: working / updated (with mean diff)
        self._enhance_status_lbl = QLabel("")
        self._enhance_status_lbl.setStyleSheet(
            f"color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px;")
        self._enhance_status_lbl.setFixedWidth(180)
        h.addWidget(self._enhance_status_lbl)

        h.addStretch(1)
        h.addWidget(QLabel(i18n.t("crop_studio.enhance_hint")))
        # 100% / 适应 切换:看 1:1 像素以判断降噪是否真起作用 / 1:1 toggle for pixel-peeping
        self._zoom_100_btn = QPushButton("100%")
        self._zoom_100_btn.setCheckable(True)
        self._zoom_100_btn.setCursor(Qt.PointingHandCursor)
        self._zoom_100_btn.toggled.connect(
            lambda on: self._compare_view.set_zoom(fit=not on))
        h.addWidget(self._zoom_100_btn)
        self._enhance_done_btn = QPushButton(i18n.t("crop_studio.enhance_done"))
        self._enhance_done_btn.setCursor(Qt.PointingHandCursor)
        self._enhance_done_btn.clicked.connect(self._exit_compare_mode)
        h.addWidget(self._enhance_done_btn)
        return bar

    def apply_initial_action(self, action: str) -> None:
        """
        打开工作区后跳到指定功能(供外部入口如大图「手动裁剪」「自动修图」调用)。
        action: 'manual'=手动裁剪模式;'enhance'=自动修图对比。
        """
        try:
            if action == "manual":
                self._set_mode("manual")
            elif action == "enhance" and not getattr(self, "_enhance_active", False):
                self._enter_compare_mode()
        except Exception:  # noqa: BLE001 — 入口跳转失败不影响工作区打开
            pass

    def _toggle_enhance_mode(self) -> None:
        """点左栏「自动修图」进入/退出对比模式(降噪+调色一起)。"""
        if getattr(self, "_enhance_active", False):
            self._exit_compare_mode()
        else:
            self._enter_compare_mode()

    def _enter_compare_mode(self) -> None:
        """
        进入「自动修图」对比模式:降噪+调色两个滑块并排,after=降噪+调色(最终成品)。
        切到对比视图、立即出一帧预览;预览作用在用户选定的裁剪区(无框则整图)。

        Unified enhance compare: both denoise & color sliders; after = denoise+color.
        """
        self._ensure_analysis_bgr()
        if self._analysis_bgr is None:
            return
        self._enhance_active = True
        self._denoise_engaged = True   # 两者皆启用,完成后导出仍应用 / both persist
        self._color_engaged = True
        self._zoom_100_btn.setChecked(False)   # 默认适应 / default to fit
        self._compare_view.set_zoom(fit=True)
        self._preview_before_bgr = self._current_crop_bgr()
        self._compare_view.set_images(self._preview_before_bgr, self._preview_before_bgr)
        self._center_stack.setCurrentWidget(self._compare_view)
        self._enhance_panel.show()
        self._request_preview()

    def _exit_compare_mode(self) -> None:
        """退出对比模式:切回裁剪画布、隐藏微调条、停掉待触发的预览。"""
        self._enhance_active = False
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.stop()
        self._enhance_panel.hide()
        self._center_stack.setCurrentWidget(self._canvas)

    def _downsample_for_preview(self, bgr):
        """把图降采样到长边 ≤ PREVIEW_LONG_EDGE,供预览快速推理。"""
        h, w = bgr.shape[:2]
        scale = PREVIEW_LONG_EDGE / float(max(h, w))
        if scale < 1.0:
            return cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                              interpolation=cv2.INTER_AREA)
        return bgr.copy()

    def _current_crop_bgr(self):
        """
        取当前裁剪区(分析图坐标)的 BGR;无裁剪框时用整图。再降采样到 ≤ PREVIEW_LONG_EDGE。
        裁剪区通常较小,故多为原生分辨率,100% 下可看清真实降噪。

        Return the current crop region (analysis-image coords) as BGR; whole frame
        if no box. Capped to PREVIEW_LONG_EDGE. Crops are usually small, so this is
        often native resolution — 1:1 zoom shows the real denoise effect.
        """
        bgr = self._analysis_bgr
        box = self._current_box
        if box is not None:
            h, w = bgr.shape[:2]
            x1, y1, x2, y2 = box
            x1 = max(0, min(int(x1), w - 1))
            x2 = max(x1 + 1, min(int(x2), w))
            y1 = max(0, min(int(y1), h - 1))
            y2 = max(y1 + 1, min(int(y2), h))
            bgr = bgr[y1:y2, x1:x2]
        return self._downsample_for_preview(bgr)

    def _enhance_state(self):
        """返回 (降噪开, 降噪强度, 调色开, 调色强度);各自需「进过对应模式」且滑块>0。"""
        d_on = getattr(self, "_denoise_engaged", False) and self._denoise_slider.value() > 0
        c_on = getattr(self, "_color_engaged", False) and self._color_slider.value() > 0
        return (d_on, self._denoise_slider.value() / 10.0,
                c_on, self._color_slider.value() / 10.0)

    def _current_enhance_opts(self):
        """
        导出用修图选项 = 降噪 ∪ 调色(各自:进过对应模式且滑块>0 才计入);都没有则 None。
        点「完成」回裁剪页后仍生效(engaged 持久)。强度 0 或未进入 = 不应用该步。

        Export options = denoise ∪ color (each counts only if its mode was engaged and
        its slider > 0); None if neither. Persists after exiting compare mode.
        """
        d_on, d_s, c_on, c_s = self._enhance_state()
        if not d_on and not c_on:
            return None
        from core.enhance.options import EnhanceOptions  # noqa: PLC0415
        return EnhanceOptions(denoise_on=d_on, denoise_strength=d_s,
                              color_on=c_on, color_strength=c_s)

    def _preview_opts(self):
        """预览选项 = 导出选项(降噪+调色);与最终成品一致。/ preview == export opts."""
        return self._current_enhance_opts()

    def _set_enhance_status(self, text: str) -> None:
        """更新修图状态文字(处理中/已更新…);控件未建好时静默。"""
        lbl = getattr(self, "_enhance_status_lbl", None)
        if lbl is not None:
            lbl.setText(text)

    def _request_preview(self) -> None:
        """滑块变动后防抖 400ms 触发后台预览(慢刷新,避免每格都跑)。"""
        if not getattr(self, "_enhance_active", False):
            return
        self._set_enhance_status(self._i18n.t("crop_studio.enhance_working"))
        if not hasattr(self, "_preview_timer"):
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._run_preview_worker)
        self._preview_timer.start(400)

    def _run_preview_worker(self) -> None:
        """对降采样 before 跑降噪;worker 忙时标记待跑,合并最新一次请求。"""
        if not getattr(self, "_enhance_active", False):
            return
        worker = getattr(self, "_preview_worker", None)
        if worker is not None and worker.isRunning():
            self._preview_pending = True
            return
        if self._preview_before_bgr is None:
            return
        opts = self._preview_opts()
        if opts is None:
            # 强度 0 = 不修图:after 等于 before,差为 0 / strength 0: after == before
            self._compare_view.set_after(self._preview_before_bgr)
            self._show_updated_status(self._preview_before_bgr)
            return
        self._set_enhance_status(self._i18n.t("crop_studio.enhance_working"))
        rgb = cv2.cvtColor(self._preview_before_bgr, cv2.COLOR_BGR2RGB)
        self._preview_worker = _EnhanceWorker(rgb, opts)
        self._preview_worker.done.connect(self._on_preview_done)
        self._preview_worker.start()

    def _show_updated_status(self, after_bgr) -> None:
        """计算 before↔after 平均像素差并显示「已更新 · 平均像素差 X.X」。"""
        try:
            before = self._preview_before_bgr
            n = min(before.shape[0], after_bgr.shape[0])
            m = min(before.shape[1], after_bgr.shape[1])
            d = float(np.abs(before[:n, :m].astype(np.int16)
                             - after_bgr[:n, :m].astype(np.int16)).mean())
        except Exception:  # noqa: BLE001
            d = 0.0
        t = self._i18n.t
        self._set_enhance_status(
            f"{t('crop_studio.enhance_updated')} · {t('crop_studio.enhance_diff')} {d:.1f}")

    def _on_preview_done(self, rgb_out) -> None:
        """预览完成:更新对比视图的 after + 状态(平均像素差);有新请求则再跑。"""
        try:
            after_bgr = cv2.cvtColor(rgb_out, cv2.COLOR_RGB2BGR)
            self._compare_view.set_after(after_bgr)
            self._show_updated_status(after_bgr)
        except Exception:  # noqa: BLE001 — 预览失败静默 / silently ignore
            pass
        if getattr(self, "_preview_pending", False):
            self._preview_pending = False
            self._run_preview_worker()

    def _on_export_clicked(self) -> None:
        """
        导出按钮:先弹「导出设置」(尺寸+质量),再弹保存对话框,后台 export_crop。
        像素来源用分析/预览图(与候选/手动框同坐标系,无需缩放、可解码);
        EXIF 从原始文件复制以保全元数据;命名/默认目录用原图路径。
        """
        from PySide6.QtWidgets import QFileDialog

        nw, nh = self._native_crop_size()
        dlg = _ExportDialog(self._i18n, nw, nh, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        out_size, quality = dlg.values()

        name_src = (self._photo.get("original_path") or self._photo.get("current_path")
                    or self._image_path)
        default = default_out_path(name_src)
        out, _ = QFileDialog.getSaveFileName(
            self, self._i18n.t("crop_studio.export"), default, "JPEG (*.jpg)"
        )
        if not out:
            return
        self._do_export(out, jpeg_quality=quality, out_size=out_size)

    def _do_export(self, out_path: str, *, jpeg_quality: int = 95,
                   out_size: Optional[tuple] = None) -> None:
        """启动后台导出(供按钮与测试调用)。"""
        exif_src = self._photo.get("original_path") or self._photo.get("current_path")
        self._export_btn.setEnabled(False)
        self._export_worker = _ExportWorker(
            self._image_path, self._current_box, out_path, exif_src,
            jpeg_quality=jpeg_quality, out_size=out_size,
            enhance_opts=self._current_enhance_opts(),
        )
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.start()

    def _on_export_done(self, ok: bool, out_or_err: str) -> None:
        """导出完成回调:更新提示并恢复按钮;成功时发出 export_requested。"""
        self._export_btn.setEnabled(True)
        if ok:
            self._cand_hint.setText(f"{self._i18n.t('crop_studio.export_done')}\n{out_or_err}")
            self.export_requested.emit({"out": out_or_err, "photo": self._photo})
        else:
            self._cand_hint.setText(f"{self._i18n.t('crop_studio.export_failed')}: {out_or_err}")
        self._cand_hint.show()

    # ── 关闭 / Close ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """关闭前停掉待触发预览并等待后台线程退出,避免槽连接到已销毁对象。"""
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.stop()
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        ew = getattr(self, "_export_worker", None)
        if ew is not None and ew.isRunning():
            ew.wait(8000)  # 导出不可中断,等其完成 / export is atomic, wait it out
        pw = getattr(self, "_preview_worker", None)
        if pw is not None and pw.isRunning():
            pw.wait(8000)  # 等预览推理结束 / wait out the preview inference
        self.closed.emit()
        super().closeEvent(event)
