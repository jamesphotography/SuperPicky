# -*- coding: utf-8 -*-
"""
裁剪建议弹窗(布局 A:主预览 + 候选条 + 手动裁剪)。非破坏性。
后台线程跑 advise_crops,完成回主线程渲染。
手动裁剪:按下鼠标记录起点,释放时映射回原图坐标并调用 score_manual_crop。

Crop Advisor Dialog (Layout A: main preview + filmstrip + manual crop). Non-destructive.
Background thread runs advise_crops; result is rendered on main thread via Signal.
Manual crop: press records start, release maps to original-image pixels via _map_to_orig and calls score_manual_crop.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect, QThread, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ai_model import read_image_bgr
from core.crop_advisor import (
    CropAdviceResult,
    CropSuggestion,
    advise_crops,
    score_manual_crop,
)
from tools.i18n import get_i18n


# ── 辅助函数 / Helper functions ───────────────────────────────────────────────


def _bgr_to_pixmap(bgr: np.ndarray, max_side: int = 480) -> QPixmap:
    """
    将 BGR ndarray 转换为 QPixmap,长边最大为 max_side 像素。
    Convert a BGR ndarray to QPixmap, with longest side capped at max_side pixels.

    参数 / Parameters:
        bgr (np.ndarray): BGR 格式图像 / Image in BGR format.
        max_side (int): 输出最大边长,默认 480 / Max output side length, default 480.

    返回 / Returns:
        QPixmap: 转换后的 Qt 像素图 / Converted Qt pixmap.
    """
    h, w = bgr.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    hh, ww = rgb.shape[:2]
    img = QImage(rgb.data, ww, hh, 3 * ww, QImage.Format_RGB888)
    return QPixmap.fromImage(img.copy())


# ── 后台工作线程 / Background worker thread ──────────────────────────────────


class _Worker(QThread):
    """
    在后台线程中运行 advise_crops,完成后通过信号发送结果。
    Runs advise_crops in a background thread and emits the result via signal.
    """

    done: Signal = Signal(object)  # CropAdviceResult

    def __init__(self, image_path: str) -> None:
        """
        初始化工作线程。
        Initialize the worker thread.

        参数 / Parameters:
            image_path (str): 图片文件路径 / Path to the image file.
        """
        super().__init__()
        self._path = image_path

    def run(self) -> None:
        """
        线程入口:调用 advise_crops,异常时安全降级为 no_bird 结果。
        Thread entry: call advise_crops; on exception, fall back to a safe no_bird result.
        """
        try:
            result = advise_crops(self._path)
        except Exception:  # 线程内不崩 / Never crash the thread
            result = CropAdviceResult(status="no_bird", bird_count=0)
        self.done.emit(result)


# ── 支持鼠标绘制矩形的预览标签 / Preview label that supports mouse-drawn rectangles ─


class _PreviewLabel(QLabel):
    """
    扩展 QLabel,支持鼠标拖拽绘制裁剪矩形并发出 crop_drawn 信号。
    Extended QLabel that supports drag-to-draw a crop rectangle and emits crop_drawn signal.

    用法 / Usage:
        连接 crop_drawn(QRect) 信号;矩形坐标为标签内像素坐标(含居中偏移)。
        Connect the crop_drawn(QRect) signal; rect coords are in label-local pixels
        (already accounting for the centered pixmap offset).
    """

    # 信号:携带标签内矩形(含像素图偏移) / Signal: carries label-local rect (offset included)
    crop_drawn: Signal = Signal(QRect)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._drag_start: Optional[QPoint] = None   # 拖拽起点 / Drag start point
        self._drag_end: Optional[QPoint] = None     # 拖拽终点 / Drag end point
        self._drawing: bool = False                  # 是否正在拖拽 / Actively dragging

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        按下左键记录起点,清除上次绘制。
        Left-press records the start point and clears the previous rectangle.
        """
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_end = None
            self._drawing = True
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """
        拖拽时实时刷新矩形(视觉反馈),不计算分数。
        Update rectangle visually while dragging; no score computed during move (debounce).
        """
        if self._drawing and (event.buttons() & Qt.LeftButton):
            self._drag_end = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """
        松开左键:确定终点,发出 crop_drawn 信号,停止绘制状态。
        Left-release: finalize end point, emit crop_drawn signal, end drawing state.
        """
        if event.button() == Qt.LeftButton and self._drawing:
            self._drag_end = event.position().toPoint()
            self._drawing = False
            self.update()
            if self._drag_start is not None and self._drag_end is not None:
                # 构造标准化矩形(x1<x2,y1<y2) / Build normalized rect (x1<x2, y1<y2)
                rect = QRect(self._drag_start, self._drag_end).normalized()
                if rect.width() > 4 and rect.height() > 4:  # 过滤误触 / Filter accidental clicks
                    self.crop_drawn.emit(rect)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """
        绘制底图后叠加半透明红色矩形框。
        Paint the underlying pixmap then overlay a semi-transparent red rectangle.
        """
        super().paintEvent(event)
        if self._drag_start is not None and self._drag_end is not None:
            rect = QRect(self._drag_start, self._drag_end).normalized()
            painter = QPainter(self)
            pen = QPen(Qt.red, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.end()


# ── 弹窗主体 / Dialog ─────────────────────────────────────────────────────────


class CropAdvisorDialog(QDialog):
    """
    裁剪建议弹窗:布局 A(顶部状态栏 + 主预览 + 横向候选条 + 关闭按钮)。
    支持点击缩略图切换主预览;支持在主预览上拖拽手动选框并打分。

    Crop Advisor Dialog: Layout A (status label + main preview + horizontal filmstrip + close button).
    Supports clicking thumbnails to switch the main preview; supports dragging a manual crop
    rectangle on the main preview and scoring it with score_manual_crop.
    """

    def __init__(self, image_path: str, parent: Optional[QWidget] = None) -> None:
        """
        初始化弹窗,立即启动后台分析线程。
        Initialize the dialog and immediately start the background analysis thread.

        参数 / Parameters:
            image_path (str): 待分析图片的文件路径 / Path to the image to analyze.
            parent (Optional[QWidget]): Qt 父窗口 / Qt parent widget.
        """
        super().__init__(parent)
        self._i18n = get_i18n()
        self._image_path: str = image_path
        self._image_bgr: Optional[np.ndarray] = read_image_bgr(image_path)
        self._suggestions: List[CropSuggestion] = []

        # 当前主预览像素图的实际显示尺寸(用于坐标映射)
        # Actual displayed pixmap size in the preview label (used for coordinate mapping)
        self._disp_w: int = 0
        self._disp_h: int = 0

        self.setWindowTitle(self._i18n.t("crop_advisor.title"))
        self.resize(640, 720)

        root = QVBoxLayout(self)

        # ── 状态标签 / Status label ───────────────────────────────────────────
        self._status_lbl = QLabel(self._i18n.t("crop_advisor.computing"))
        self._status_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self._status_lbl)

        # ── 主预览(支持鼠标绘制) / Main preview (supports mouse drawing) ─────
        self._preview = _PreviewLabel()
        self._preview.crop_drawn.connect(self._on_crop_drawn)
        root.addWidget(self._preview, 1)

        # ── 候选横向滚动条 / Horizontal filmstrip ────────────────────────────
        self._strip = QScrollArea()
        self._strip.setWidgetResizable(True)
        self._strip.setFixedHeight(120)
        self._strip_inner = QWidget()
        self._strip_layout = QHBoxLayout(self._strip_inner)
        self._strip_layout.setAlignment(Qt.AlignLeft)
        self._strip.setWidget(self._strip_inner)
        root.addWidget(self._strip)

        # ── 关闭按钮 / Close button ───────────────────────────────────────────
        close_btn = QPushButton(self._i18n.t("crop_advisor.close"))
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignRight)

        # ── 启动后台线程 / Start background thread ────────────────────────────
        self._worker = _Worker(image_path)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    # ── 后台结果回调 / Background result callback ─────────────────────────────

    def _on_done(self, result: CropAdviceResult) -> None:
        """
        后台线程完成后在主线程渲染结果。
        Render result on main thread after background thread completes.

        参数 / Parameters:
            result (CropAdviceResult): advise_crops 返回值 / Return value from advise_crops.
        """
        if result.status == "no_bird":
            self._status_lbl.setText(self._i18n.t("crop_advisor.no_bird"))
            return
        if result.status == "too_many_birds":
            self._status_lbl.setText(self._i18n.t("crop_advisor.too_many_birds"))
            return

        self._suggestions = result.suggestions
        if not self._suggestions:
            self._status_lbl.setText(self._i18n.t("crop_advisor.no_bird"))
            return

        top = self._suggestions[0]
        self._status_lbl.setText(
            f"{self._i18n.t('crop_advisor.recommended')}: "
            f"{top.ratio_label} · {top.topiq_score:.2f}"
        )
        self._show_main(top)
        self._build_strip()

    # ── 主预览 / Main preview ─────────────────────────────────────────────────

    def _show_main(self, s: CropSuggestion) -> None:
        """
        将指定候选渲染为主预览,并记录像素图的实际显示尺寸。
        Render the given suggestion as the main preview and record the displayed pixmap size.

        参数 / Parameters:
            s (CropSuggestion): 要展示的裁剪候选 / The crop suggestion to display.
        """
        pix = _bgr_to_pixmap(s.preview_bgr)
        self._disp_w = pix.width()
        self._disp_h = pix.height()
        self._preview.setPixmap(pix)
        self._status_lbl.setText(
            f"{self._i18n.t('crop_advisor.recommended')}: {s.ratio_label} · {s.topiq_score:.2f}"
        )

    # ── 候选条 / Filmstrip ────────────────────────────────────────────────────

    def _build_strip(self) -> None:
        """
        清空并重建缩略图候选条。
        Clear and rebuild the thumbnail filmstrip.
        """
        # 清除旧控件 / Remove old widgets
        while self._strip_layout.count():
            item = self._strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for s in self._suggestions:
            cell = QFrame()
            v = QVBoxLayout(cell)

            thumb = QLabel()
            thumb.setPixmap(_bgr_to_pixmap(s.preview_bgr, max_side=96))
            thumb.setCursor(Qt.PointingHandCursor)
            # lambda 默认参数捕获当前 s / lambda default arg captures current s
            thumb.mousePressEvent = lambda _e, sug=s: self._show_main(sug)

            v.addWidget(thumb)
            v.addWidget(QLabel(f"{s.ratio_label} · {s.topiq_score:.2f}"))
            self._strip_layout.addWidget(cell)

    # ── 手动裁剪坐标映射 / Manual crop coordinate mapping ────────────────────

    def _map_to_orig(self, x: float, y: float, disp_w: int, disp_h: int) -> Tuple[int, int]:
        """
        将主预览像素图上的坐标(相对于像素图左上角)映射回原图像素坐标。
        Map a point on the displayed pixmap (relative to pixmap top-left) back to
        original-image pixel coordinates.

        映射原理 / Mapping rationale:
            主预览 QLabel 使用 AlignCenter 居中显示缩放后的像素图(≤480px)。
            鼠标坐标是相对于 QLabel 的,需先减去居中偏移量得到像素图内坐标,
            再按 原图尺寸 / 显示尺寸 的比例换算。
            The QLabel uses AlignCenter; the scaled pixmap is centered inside it.
            Mouse coords are label-local; subtract the centering offset first to
            get pixmap-local coords, then scale by (original / displayed).

        参数 / Parameters:
            x (float): 相对于像素图左上角的 x 坐标(已减偏移) / x relative to pixmap top-left.
            y (float): 相对于像素图左上角的 y 坐标(已减偏移) / y relative to pixmap top-left.
            disp_w (int): 显示像素图宽度(像素) / Displayed pixmap width in pixels.
            disp_h (int): 显示像素图高度(像素) / Displayed pixmap height in pixels.

        返回 / Returns:
            Tuple[int, int]: 原图像素坐标 (px, py) / Original image pixel coords (px, py).
        """
        if self._image_bgr is None:
            return 0, 0
        oh, ow = self._image_bgr.shape[:2]
        px = int(x / disp_w * ow) if disp_w > 0 else 0
        py = int(y / disp_h * oh) if disp_h > 0 else 0
        return px, py

    def _pixmap_rect_in_label(self) -> QRect:
        """
        计算当前像素图在 QLabel 内的实际绘制矩形(居中时的偏移)。
        Compute the actual drawn rect of the pixmap inside the QLabel when centered.

        返回 / Returns:
            QRect: 像素图在标签内的绘制矩形 / Drawn rect of the pixmap within the label.
        """
        lw = self._preview.width()
        lh = self._preview.height()
        pw = self._disp_w
        ph = self._disp_h
        # 居中偏移 / Centering offset
        ox = (lw - pw) // 2
        oy = (lh - ph) // 2
        return QRect(ox, oy, pw, ph)

    # ── 手动裁剪回调 / Manual crop callback ──────────────────────────────────

    def _on_crop_drawn(self, label_rect: QRect) -> None:
        """
        用户在主预览上拖拽释放后触发:将标签坐标映射回原图并打分。
        Triggered on mouse release after drawing a rectangle: maps label coords to original
        image coords and scores the manual crop.

        映射步骤 / Mapping steps:
            1. 计算像素图在 QLabel 内的居中偏移矩形 pixmap_rect。
               Compute the centering offset rect of the pixmap inside QLabel.
            2. 将鼠标框 label_rect 裁剪到 pixmap_rect 内,防止越界。
               Clip the drawn label_rect to pixmap_rect to prevent out-of-bounds.
            3. 减去偏移量得到像素图内相对坐标。
               Subtract offset to get pixmap-local relative coords.
            4. 按原图/显示尺寸比例换算为原图像素坐标(x1,y1,x2,y2)。
               Scale by orig/disp ratio to get original-image pixel coords.
            5. 调用 score_manual_crop,将分数写入状态标签。
               Call score_manual_crop and write the score to the status label.

        参数 / Parameters:
            label_rect (QRect): 鼠标拖拽框,相对于 QLabel / Mouse drag rect in label-local coords.
        """
        if self._image_bgr is None or self._disp_w == 0 or self._disp_h == 0:
            return

        # Step 1: 像素图在标签内的实际矩形 / Actual pixmap rect within label
        pix_rect = self._pixmap_rect_in_label()

        # Step 2: 将鼠标框裁剪到像素图区域内 / Clip to pixmap bounds
        clipped = label_rect.intersected(pix_rect)
        if clipped.isEmpty():
            return

        # Step 3: 转换为像素图内相对坐标 / Convert to pixmap-local coords
        rel_x1 = clipped.left() - pix_rect.left()
        rel_y1 = clipped.top() - pix_rect.top()
        rel_x2 = clipped.right() - pix_rect.left()
        rel_y2 = clipped.bottom() - pix_rect.top()

        # Step 4: 映射回原图坐标 / Map to original image coords
        ox1, oy1 = self._map_to_orig(rel_x1, rel_y1, self._disp_w, self._disp_h)
        ox2, oy2 = self._map_to_orig(rel_x2, rel_y2, self._disp_w, self._disp_h)

        # 保证 x1<x2, y1<y2 且坐标不超图边界 / Ensure x1<x2, y1<y2, clamped to image bounds
        oh, ow = self._image_bgr.shape[:2]
        x1 = max(0, min(ox1, ox2))
        y1 = max(0, min(oy1, oy2))
        x2 = min(ow, max(ox1, ox2))
        y2 = min(oh, max(oy1, oy2))

        if x2 <= x1 or y2 <= y1:
            return

        box = (x1, y1, x2, y2)

        # Step 5: 打分并显示 / Score and display
        score = score_manual_crop(self._image_bgr, box)
        if score is not None:
            self._status_lbl.setText(
                f"{self._i18n.t('crop_advisor.manual_score')}: {score:.2f}"
            )
        else:
            self._status_lbl.setText(self._i18n.t("crop_advisor.manual_score") + ": —")
