# -*- coding: utf-8 -*-
"""
裁剪建议弹窗(布局 A:主预览 + 候选条 + 手动裁剪)。非破坏性。
后台线程跑 advise_crops,完成回主线程渲染。

模式说明:
- 建议模式(默认):主预览显示选中候选的裁剪区域;候选条可切换;禁止鼠标拖拽。
- 手动模式:主预览显示完整原图;允许鼠标拖拽框选任意区域;松手后对全图坐标打分。

Crop Advisor Dialog (Layout A: main preview + filmstrip + manual crop). Non-destructive.
Background thread runs advise_crops; result is rendered on main thread via Signal.

Mode notes:
- Suggestion mode (default): main preview shows the selected suggestion's crop; filmstrip
  switches suggestions; mouse dragging is DISABLED.
- Manual mode: main preview shows the FULL original image; mouse dragging is ENABLED;
  on release, score_manual_crop is called with coords mapped to the full original image.
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
    仅当 drawing_enabled 为 True 时,才响应鼠标事件(手动模式下开启)。

    Extended QLabel that supports drag-to-draw a crop rectangle and emits crop_drawn signal.
    Mouse drag is only active when drawing_enabled is True (set in manual mode).

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
        self.drawing_enabled: bool = False           # 手动模式开关 / Manual mode gate

    def clear_rubber_band(self) -> None:
        """
        清除已绘制的橡皮筋矩形。在切换模式时调用。
        Clear any drawn rubber-band rectangle. Call when switching modes.
        """
        self._drag_start = None
        self._drag_end = None
        self._drawing = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """
        按下左键记录起点,清除上次绘制。仅在 drawing_enabled 时响应。
        Left-press records the start point and clears the previous rectangle.
        Only active when drawing_enabled is True.
        """
        if self.drawing_enabled and event.button() == Qt.LeftButton:
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
        if self.drawing_enabled and self._drawing and (event.buttons() & Qt.LeftButton):
            self._drag_end = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """
        松开左键:确定终点,发出 crop_drawn 信号,停止绘制状态。仅在 drawing_enabled 时响应。
        Left-release: finalize end point, emit crop_drawn signal, end drawing state.
        Only active when drawing_enabled is True.
        """
        if self.drawing_enabled and event.button() == Qt.LeftButton and self._drawing:
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
        绘制底图后叠加半透明红色矩形框(仅手动模式下可见)。
        Paint the underlying pixmap then overlay a dashed red rectangle (visible in manual mode only).
        """
        super().paintEvent(event)
        if self.drawing_enabled and self._drag_start is not None and self._drag_end is not None:
            rect = QRect(self._drag_start, self._drag_end).normalized()
            painter = QPainter(self)
            pen = QPen(Qt.red, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.end()


# ── 弹窗主体 / Dialog ─────────────────────────────────────────────────────────


class CropAdvisorDialog(QDialog):
    """
    裁剪建议弹窗:布局 A(顶部状态栏 + 主预览 + 横向候选条 + 按钮行)。

    两种模式:
    - 建议模式(默认):主预览显示选中候选的裁剪图;候选条切换;禁止拖拽。
    - 手动模式:主预览显示完整原图;允许拖拽框选;松手调 score_manual_crop 打分。

    Crop Advisor Dialog: Layout A (status label + main preview + horizontal filmstrip + buttons).

    Two modes:
    - Suggestion mode (default): main preview shows the selected suggestion's crop;
      filmstrip switches; dragging DISABLED.
    - Manual mode: main preview shows the FULL original image; dragging ENABLED;
      on release, score_manual_crop is called with correctly mapped full-image coords.
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

        # 当前展示的候选(用于模式切换时还原) / Currently displayed suggestion (for mode-switch restore)
        self._current_suggestion: Optional[CropSuggestion] = None

        # 手动模式状态 / Manual mode state
        self._manual_mode: bool = False

        # 手动模式下全图的实际显示尺寸(像素图宽高),用于坐标映射。
        # In manual mode: the actual displayed pixmap size of the full image, used for mapping.
        self._full_pix_w: int = 0
        self._full_pix_h: int = 0

        self.setWindowTitle(self._i18n.t("crop_advisor.title"))
        self.resize(640, 720)

        root = QVBoxLayout(self)

        # ── 状态标签 / Status label ───────────────────────────────────────────
        self._status_lbl = QLabel(self._i18n.t("crop_advisor.computing"))
        self._status_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self._status_lbl)

        # ── 主预览(支持手动模式下鼠标绘制) / Main preview (mouse drawing in manual mode) ─
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

        # ── 按钮行:手动裁剪切换 + 关闭 / Button row: manual-mode toggle + close ─
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._manual_btn = QPushButton(self._i18n.t("crop_advisor.manual_mode"))
        self._manual_btn.setCheckable(True)
        self._manual_btn.setChecked(False)
        self._manual_btn.clicked.connect(self._toggle_manual_mode)
        btn_row.addWidget(self._manual_btn)

        close_btn = QPushButton(self._i18n.t("crop_advisor.close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

        # ── 启动后台线程 / Start background thread ────────────────────────────
        self._worker = _Worker(image_path)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    # ── 窗口关闭事件 / Window close event ─────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        弹窗关闭前等待后台工作线程退出,避免对象析构后信号仍连接到已销毁的槽。
        Wait for the background worker to finish before closing to avoid use-after-free
        when the worker still holds a live signal connection to _on_done.

        参数 / Parameters:
            event: Qt 关闭事件 / Qt close event.
        """
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)  # 最多等待 2 秒 / Wait up to 2 seconds
        super().closeEvent(event)

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
        # 仅在非手动模式时更新状态标签,避免覆盖"手动裁剪"标签 /
        # Only update status label when NOT in manual mode to avoid overwriting "Manual crop" label.
        if not self._manual_mode:
            self._status_lbl.setText(
                f"{self._i18n.t('crop_advisor.recommended')}: "
                f"{top.ratio_label} · {top.topiq_score:.2f}"
            )
        self._show_main(top)
        self._build_strip()

    # ── 模式切换 / Mode toggle ────────────────────────────────────────────────

    def _toggle_manual_mode(self, checked: bool) -> None:
        """
        切换手动/建议模式:
        - 手动模式 ON:主预览切换为完整原图;开启鼠标拖拽;清除橡皮筋。
        - 手动模式 OFF:恢复当前候选(或首条候选)的裁剪预览;禁用拖拽;清除橡皮筋。

        Toggle between manual and suggestion modes:
        - Manual ON:  show full original image; enable mouse drag; clear rubber band.
        - Manual OFF: restore selected (or top) suggestion crop; disable drag; clear rubber band.

        参数 / Parameters:
            checked (bool): 按钮按下状态 / Button checked state.
        """
        self._manual_mode = checked
        self._preview.drawing_enabled = checked
        self._preview.clear_rubber_band()

        if checked:
            # 手动模式:显示完整原图 / Manual mode: display the full original image
            self._show_full_image()
        else:
            # 建议模式:恢复上次选中候选(或首条候选);若尚无候选则清空预览 /
            # Suggestion mode: restore last-selected suggestion (fallback to first); clear if none.
            if self._suggestions:
                restore = self._current_suggestion if self._current_suggestion is not None else self._suggestions[0]
                self._show_main(restore)
            else:
                # 尚无候选时清空预览 / No suggestions yet; clear preview
                self._preview.clear()
                self._status_lbl.setText(self._i18n.t("crop_advisor.computing"))

    def _show_full_image(self) -> None:
        """
        在手动模式下,将完整原图缩放后显示在主预览中,并记录实际像素图尺寸用于坐标映射。
        In manual mode, display the full original image scaled to fit the preview,
        and record the actual pixmap size for coordinate mapping.
        """
        if self._image_bgr is None:
            return
        pix = _bgr_to_pixmap(self._image_bgr, max_side=480)
        self._full_pix_w = pix.width()
        self._full_pix_h = pix.height()
        self._preview.setPixmap(pix)
        self._status_lbl.setText(self._i18n.t("crop_advisor.manual_mode"))

    # ── 主预览(建议模式) / Main preview (suggestion mode) ─────────────────────

    def _show_main(self, s: CropSuggestion) -> None:
        """
        将指定候选渲染为主预览(建议模式)。手动模式下调用此方法会被忽略。
        Render the given suggestion as the main preview (suggestion mode).
        Calls in manual mode are ignored to avoid overwriting the full-image view.

        参数 / Parameters:
            s (CropSuggestion): 要展示的裁剪候选 / The crop suggestion to display.
        """
        if self._manual_mode:
            # 手动模式下点击候选条不切换主预览 / Ignore filmstrip clicks in manual mode
            return
        # 记录当前展示的候选,供退出手动模式时还原 / Track for mode-switch restore
        self._current_suggestion = s
        pix = _bgr_to_pixmap(s.preview_bgr)
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

    def _pixmap_rect_in_label(self, pix_w: int, pix_h: int) -> QRect:
        """
        计算指定尺寸的像素图在 QLabel(AlignCenter)内的实际绘制矩形(居中偏移量)。
        Compute the actual drawn rect of a pixmap of given size inside the QLabel
        when the label uses AlignCenter.

        原理 / Rationale:
            居中偏移量 = (标签边长 - 像素图边长) // 2。
            Centering offset = (label_side - pixmap_side) // 2.

        参数 / Parameters:
            pix_w (int): 像素图宽度(像素) / Pixmap width in pixels.
            pix_h (int): 像素图高度(像素) / Pixmap height in pixels.

        返回 / Returns:
            QRect: 像素图在标签内的绘制矩形 / Drawn rect of the pixmap within the label.
        """
        lw = self._preview.width()
        lh = self._preview.height()
        ox = (lw - pix_w) // 2
        oy = (lh - pix_h) // 2
        return QRect(ox, oy, pix_w, pix_h)

    def _map_manual_to_orig(
        self,
        rel_x: float,
        rel_y: float,
        pix_w: int,
        pix_h: int,
    ) -> Tuple[int, int]:
        """
        将手动模式下像素图内坐标(相对于像素图左上角)映射回原图像素坐标。

        Map a point in pixmap-local coords (relative to pixmap top-left) back to
        original-image pixel coordinates.

        映射原理 / Mapping rationale:
            手动模式下像素图是对全图等比缩放的结果:
                scale = pix_w / orig_w  (== pix_h / orig_h,因保持宽高比)
            因此:
                orig_x = rel_x / scale = rel_x * orig_w / pix_w
                orig_y = rel_y / scale = rel_y * orig_h / pix_h

            In manual mode the pixmap is the full image scaled uniformly:
                scale = pix_w / orig_w  (== pix_h / orig_h, aspect preserved)
            Therefore:
                orig_x = rel_x / scale = rel_x * orig_w / pix_w
                orig_y = rel_y / scale = rel_y * orig_h / pix_h

        参数 / Parameters:
            rel_x (float): 相对于像素图左上角的 x 坐标 / x relative to pixmap top-left.
            rel_y (float): 相对于像素图左上角的 y 坐标 / y relative to pixmap top-left.
            pix_w (int): 像素图宽度(像素) / Pixmap width in pixels.
            pix_h (int): 像素图高度(像素) / Pixmap height in pixels.

        返回 / Returns:
            Tuple[int, int]: 原图像素坐标 (px, py) / Original image pixel coords (px, py).
        """
        if self._image_bgr is None or pix_w == 0 or pix_h == 0:
            return 0, 0
        oh, ow = self._image_bgr.shape[:2]
        px = int(rel_x * ow / pix_w)
        py = int(rel_y * oh / pix_h)
        return px, py

    # ── 手动裁剪回调 / Manual crop callback ──────────────────────────────────

    def _on_crop_drawn(self, label_rect: QRect) -> None:
        """
        用户在主预览上拖拽释放后触发(仅手动模式有效):
        将标签坐标正确映射回全图原图坐标后打分。

        Triggered on mouse release after drawing a rectangle (only valid in manual mode):
        maps label coords back to full-original-image coords and scores the manual crop.

        坐标映射步骤 / Coordinate mapping steps:
            1. 获取全图像素图在 QLabel 内的居中偏移矩形 pix_rect。
               Get the centering-offset rect of the full-image pixmap inside QLabel.
            2. 将鼠标框 label_rect 裁剪到 pix_rect 内,防止越界。
               Clip the drawn label_rect to pix_rect to prevent out-of-bounds.
            3. 减去偏移量得到像素图内相对坐标。
               Subtract offset to get pixmap-local relative coords.
            4. 按 orig/pix 比例换算为原图像素坐标 (x1,y1,x2,y2)。
               Scale by orig/pix ratio to get original-image pixel coords.
            5. 调用 score_manual_crop,将分数写入状态标签。
               Call score_manual_crop and write the score to the status label.

        参数 / Parameters:
            label_rect (QRect): 鼠标拖拽框,相对于 QLabel / Mouse drag rect in label-local coords.
        """
        # 仅手动模式下处理 / Only process in manual mode
        if not self._manual_mode:
            return
        if self._image_bgr is None or self._full_pix_w == 0 or self._full_pix_h == 0:
            return

        # Step 1: 全图像素图在标签内的实际矩形 / Actual pixmap rect within label
        pix_rect = self._pixmap_rect_in_label(self._full_pix_w, self._full_pix_h)

        # Step 2: 将鼠标框裁剪到像素图区域内 / Clip to pixmap bounds
        clipped = label_rect.intersected(pix_rect)
        if clipped.isEmpty():
            return

        # Step 3: 转换为像素图内相对坐标 / Convert to pixmap-local coords
        # QRect.right()/bottom() 是包含端点(inclusive),需 +1 转为 exclusive end,
        # 以便拖到图像边缘时坐标正好等于像素图宽度/高度。
        # QRect.right()/bottom() are inclusive; add 1 for exclusive end so a drag to the
        # exact image edge maps to the full pixmap width/height (not one pixel short).
        rel_x1 = float(clipped.left() - pix_rect.left())
        rel_y1 = float(clipped.top() - pix_rect.top())
        rel_x2 = float(clipped.right() + 1 - pix_rect.left())
        rel_y2 = float(clipped.bottom() + 1 - pix_rect.top())

        # Step 4: 映射回全图原图坐标 / Map to full-original-image coords
        ox1, oy1 = self._map_manual_to_orig(rel_x1, rel_y1, self._full_pix_w, self._full_pix_h)
        ox2, oy2 = self._map_manual_to_orig(rel_x2, rel_y2, self._full_pix_w, self._full_pix_h)

        # 保证 x1<x2, y1<y2 且坐标不超图边界 / Ensure x1<x2, y1<y2, clamped to image bounds
        oh, ow = self._image_bgr.shape[:2]
        x1 = max(0, min(ox1, ox2))
        y1 = max(0, min(oy1, oy2))
        x2 = min(ow, max(ox1, ox2))
        y2 = min(oh, max(oy1, oy2))

        # 退化框(零面积)不打分 / Reject degenerate (zero-area) box
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
