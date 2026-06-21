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
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, Signal
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

PREVIEW_LONG_EDGE = 1280  # 预览降采样目标长边 / preview downscale long edge


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

        self._canvas = _Canvas()
        self._canvas.manual_crop.connect(self._on_manual_crop)
        main_row.addWidget(self._canvas, 1)

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
                                           self._toggle_enhance_panel)
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
        构建自动修图微调条:总开关 + 降噪滑块 + 调色开关/滑块 + 预览按钮。
        Build the enhance tune bar: master toggle + denoise/color sliders + preview.
        """
        bar = QFrame()
        bar.setObjectName("cropStudioEnhanceBar")
        bar.setStyleSheet(
            f"QFrame#cropStudioEnhanceBar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-bottom: 1px solid {_c('border', '#2a2a2a')}; }}"
            f" QLabel {{ color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; }}"
            f" QCheckBox {{ color: {_c('text_secondary', '#a1a1a1')}; font-size: 12px; }}"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)

        i18n = self._i18n
        # 总开关(默认开 = 一键默认出片) / master enable (default ON = one-click)
        self._enhance_enable = QCheckBox(i18n.t("crop_studio.enhance"))
        self._enhance_enable.setChecked(True)
        h.addWidget(self._enhance_enable)

        # 降噪 / denoise
        h.addWidget(QLabel(i18n.t("crop_studio.denoise")))
        self._denoise_slider = QSlider(Qt.Horizontal)
        self._denoise_slider.setRange(0, 100)
        self._denoise_slider.setValue(50)
        self._denoise_slider.setFixedWidth(120)
        h.addWidget(self._denoise_slider)

        # 调色 / color
        self._color_check = QCheckBox(i18n.t("crop_studio.color"))
        self._color_check.setChecked(True)
        h.addWidget(self._color_check)
        self._color_slider = QSlider(Qt.Horizontal)
        self._color_slider.setRange(0, 100)
        self._color_slider.setValue(40)
        self._color_slider.setFixedWidth(120)
        h.addWidget(self._color_slider)

        h.addStretch(1)
        self._preview_btn = QPushButton(i18n.t("crop_studio.enhance_preview"))
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.clicked.connect(self._show_enhance_preview)
        h.addWidget(self._preview_btn)
        return bar

    def _toggle_enhance_panel(self) -> None:
        """展开/收起修图微调条;展开即视为启用(一键默认)。"""
        show = not self._enhance_panel.isVisible()
        self._enhance_panel.setVisible(show)
        if show:
            self._enhance_enable.setChecked(True)

    def _current_enhance_opts(self):
        """
        面板状态→EnhanceOptions;面板隐藏或总开关关时返回 None(不修图)。
        Returns EnhanceOptions from panel state, or None when disabled.
        """
        if not getattr(self, "_enhance_panel", None) or not self._enhance_panel.isVisible():
            return None
        if not self._enhance_enable.isChecked():
            return None
        from core.enhance.options import EnhanceOptions  # noqa: PLC0415
        return EnhanceOptions(
            denoise_on=self._denoise_slider.value() > 0,
            denoise_strength=self._denoise_slider.value() / 100.0,
            color_on=bool(self._color_check.isChecked()),
            color_strength=self._color_slider.value() / 100.0,
        )

    def _show_enhance_preview(self) -> None:
        """
        非破坏性预览:对分析图(降采样到 PREVIEW_LONG_EDGE)跑修图,弹 before/after 对话框。
        不触碰主画布(避免清除裁剪框)。
        Non-destructive before/after preview in a dialog; never touches the canvas.
        """
        opts = self._current_enhance_opts()
        if opts is None:
            return
        self._ensure_analysis_bgr()
        if self._analysis_bgr is None:
            return
        bgr = self._analysis_bgr
        h, w = bgr.shape[:2]
        scale = PREVIEW_LONG_EDGE / float(max(h, w))
        if scale < 1.0:
            bgr = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self._preview_before_bgr = bgr
        self._preview_btn.setEnabled(False)
        self._preview_worker = _EnhanceWorker(rgb, opts)
        self._preview_worker.done.connect(self._on_preview_done)
        self._preview_worker.start()

    def _on_preview_done(self, rgb_out) -> None:
        """预览完成:RGB→BGR,弹出 before/after 对话框(非阻塞主画布)。"""
        self._preview_btn.setEnabled(True)
        try:
            after_bgr = cv2.cvtColor(rgb_out, cv2.COLOR_RGB2BGR)
        except Exception:  # noqa: BLE001 — 预览失败静默 / silently ignore preview failure
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self._i18n.t("crop_studio.enhance_preview"))
        lay = QHBoxLayout(dlg)
        before = QLabel()
        before.setPixmap(_bgr_to_qpixmap(self._preview_before_bgr))
        after = QLabel()
        after.setPixmap(_bgr_to_qpixmap(after_bgr))
        lay.addWidget(before)
        lay.addWidget(after)
        dlg.exec()

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
        """关闭前等待后台线程退出,避免槽连接到已销毁对象。"""
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        ew = getattr(self, "_export_worker", None)
        if ew is not None and ew.isRunning():
            ew.wait(8000)  # 导出不可中断,等其完成 / export is atomic, wait it out
        self.closed.emit()
        super().closeEvent(event)
