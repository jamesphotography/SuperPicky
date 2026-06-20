# -*- coding: utf-8 -*-
"""
裁剪建议弹窗(布局:左侧竖排候选条 + 右侧大预览 + 手动裁剪)。非破坏性。
后台线程跑 advise_crops,完成回主线程渲染。

布局说明:
- 笔记本屏幕宽度富余、高度紧张,故候选条置于左侧竖排,右侧留给尽量大的预览。
- 预览按 KeepAspectRatio 同时贴合可用区域的宽与高(完整显示,不裁切、不拉伸),
  并随窗口缩放自适应。

模式说明:
- 建议模式(默认):主预览显示选中候选的裁剪区域;候选条可切换;禁止鼠标拖拽。
- 手动模式:主预览显示完整原图;允许鼠标拖拽框选任意区域;松手后对全图坐标打分。

Crop Advisor Dialog (left vertical filmstrip + large right preview + manual crop).
Non-destructive. Background thread runs advise_crops; rendered on main thread via Signal.
The preview fits BOTH width and height (KeepAspectRatio) and rescales with the window.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect, QThread, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
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
    ORIGINAL_LABEL,
    CropAdviceResult,
    CropSuggestion,
    advise_crops,
    score_manual_crop,
)
from tools.i18n import get_i18n

try:
    from ui.styles import COLORS
except Exception:  # 主题缺失时的安全兜底 / Safe fallback if theme unavailable
    COLORS = {}


def _c(key: str, fallback: str) -> str:
    """读取主题色,缺失则用兜底值 / Read a theme color with a fallback."""
    return COLORS.get(key, fallback)


# 预览/缩略图渲染源图最大边(实际显示再按区域缩放) /
# Max side of the rendered source pixmap (the on-screen size is fit to the area afterwards).
_PREVIEW_SRC_MAX = 1600
# 左侧候选缩略图长边像素 / Left filmstrip thumbnail longest side (px)
_THUMB_MAX = 150
# 左侧候选条固定宽度 / Fixed width of the left filmstrip (px)
_STRIP_WIDTH = 184


# ── 辅助函数 / Helper functions ───────────────────────────────────────────────


def _bgr_to_pixmap(bgr: np.ndarray, max_side: int = _PREVIEW_SRC_MAX) -> QPixmap:
    """
    将 BGR ndarray 转换为 QPixmap,长边最大为 max_side 像素。
    Convert a BGR ndarray to QPixmap, with longest side capped at max_side pixels.
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
        super().__init__()
        self._path = image_path

    def run(self) -> None:
        try:
            result = advise_crops(self._path)
        except Exception:  # 线程内不崩 / Never crash the thread
            result = CropAdviceResult(status="no_bird", bird_count=0)
        self.done.emit(result)


# ── 支持鼠标绘制矩形 + 自适应缩放的预览标签 / Preview label: rubber-band + fit-scaling ─


class _PreviewLabel(QLabel):
    """
    扩展 QLabel:
    - 持有"源像素图"并按 KeepAspectRatio 缩放到当前控件尺寸(完整显示,随窗口自适应);
    - 手动模式下支持鼠标拖拽绘制裁剪矩形并发出 crop_drawn 信号。

    Extended QLabel that:
    - holds a "source pixmap" and scales it to the current widget size with KeepAspectRatio
      (full display, rescales with the window);
    - supports drag-to-draw a crop rectangle (emits crop_drawn) in manual mode.
    """

    crop_drawn: Signal = Signal(QRect)  # 携带标签内矩形 / label-local rect

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._src: Optional[QPixmap] = None     # 源像素图 / source pixmap
        self._disp_w: int = 0                    # 当前显示像素图宽 / displayed pixmap width
        self._disp_h: int = 0                    # 当前显示像素图高 / displayed pixmap height

        self._drag_start: Optional[QPoint] = None
        self._drag_end: Optional[QPoint] = None
        self._drawing: bool = False
        self.drawing_enabled: bool = False       # 手动模式开关 / manual mode gate

    # ── 源图与自适应缩放 / Source image & fit-scaling ─────────────────────────

    def set_source(self, pixmap: Optional[QPixmap]) -> None:
        """设置源像素图并按当前尺寸缩放显示。Set source pixmap and scale to fit current size."""
        self._src = pixmap
        self._apply_scaled()

    def clear_source(self) -> None:
        """清空预览。Clear the preview."""
        self._src = None
        self._disp_w = self._disp_h = 0
        self.clear()

    def _apply_scaled(self) -> None:
        """把源图按 KeepAspectRatio 缩放到当前控件尺寸,完整显示。"""
        if self._src is None or self._src.isNull():
            self._disp_w = self._disp_h = 0
            return
        target = self.size()
        scaled = self._src.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._disp_w = scaled.width()
        self._disp_h = scaled.height()
        self.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """窗口缩放时重新适配;清除已绘橡皮筋(坐标已失效)。"""
        self._apply_scaled()
        self.clear_rubber_band()
        super().resizeEvent(event)

    def current_pixmap_rect(self) -> QRect:
        """当前显示像素图在标签内的居中矩形(用于手动坐标映射)。"""
        if self._disp_w == 0 or self._disp_h == 0:
            return QRect()
        ox = (self.width() - self._disp_w) // 2
        oy = (self.height() - self._disp_h) // 2
        return QRect(ox, oy, self._disp_w, self._disp_h)

    # ── 橡皮筋绘制 / Rubber-band drawing ──────────────────────────────────────

    def clear_rubber_band(self) -> None:
        self._drag_start = None
        self._drag_end = None
        self._drawing = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.drawing_enabled and event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_end = None
            self._drawing = True
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drawing_enabled and self._drawing and (event.buttons() & Qt.LeftButton):
            self._drag_end = event.position().toPoint()
            self.update()
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
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self.drawing_enabled and self._drag_start is not None and self._drag_end is not None:
            rect = QRect(self._drag_start, self._drag_end).normalized()
            painter = QPainter(self)
            painter.setPen(QPen(Qt.red, 2, Qt.DashLine))
            painter.drawRect(rect)
            painter.end()


# ── 弹窗主体 / Dialog ─────────────────────────────────────────────────────────


class CropAdvisorDialog(QDialog):
    """
    裁剪建议弹窗:左侧竖排候选条 + 右侧(状态徽标 + 大预览 + 按钮行)。

    两种模式:
    - 建议模式(默认):主预览显示选中候选的裁剪图;候选条切换;禁止拖拽。
    - 手动模式:主预览显示完整原图;允许拖拽框选;松手调 score_manual_crop 打分。
    """

    def __init__(self, image_path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = get_i18n()
        self._image_path: str = image_path
        self._image_bgr: Optional[np.ndarray] = read_image_bgr(image_path)
        self._suggestions: List[CropSuggestion] = []
        self._strip_cells: List[Tuple[QFrame, CropSuggestion]] = []

        self._current_suggestion: Optional[CropSuggestion] = None
        self._manual_mode: bool = False

        self.setWindowTitle(self._i18n.t("crop_advisor.title"))
        self.resize(1100, 820)
        self.setStyleSheet(self._build_stylesheet())

        # ── 顶层左右布局 / Top-level left|right layout ────────────────────────
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # ── 左侧:竖排候选条 / Left: vertical filmstrip ───────────────────────
        self._strip = QScrollArea()
        self._strip.setObjectName("filmstrip")
        self._strip.setWidgetResizable(True)
        self._strip.setFixedWidth(_STRIP_WIDTH)
        self._strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._strip.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._strip_inner = QWidget()
        self._strip_layout = QVBoxLayout(self._strip_inner)
        self._strip_layout.setContentsMargins(6, 6, 6, 6)
        self._strip_layout.setSpacing(10)
        self._strip_layout.setAlignment(Qt.AlignTop)
        self._strip.setWidget(self._strip_inner)
        root.addWidget(self._strip)

        # ── 右侧:状态徽标 + 大预览 + 按钮行 / Right column ────────────────────
        right = QVBoxLayout()
        right.setSpacing(12)

        self._status_lbl = QLabel("🔍 " + self._i18n.t("crop_advisor.computing"))
        self._status_lbl.setObjectName("statusBadge")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        right.addWidget(self._status_lbl)

        preview_container = QFrame()
        preview_container.setObjectName("previewContainer")
        pc_layout = QVBoxLayout(preview_container)
        pc_layout.setContentsMargins(10, 10, 10, 10)
        self._preview = _PreviewLabel()
        self._preview.crop_drawn.connect(self._on_crop_drawn)
        pc_layout.addWidget(self._preview)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 6)
        preview_container.setGraphicsEffect(shadow)
        right.addWidget(preview_container, 1)

        # 打开即显示未裁剪原图,避免空白;分析完成后替换为裁剪结果 /
        # Show the uncropped full image immediately so it isn't blank.
        if self._image_bgr is not None:
            self._preview.set_source(_bgr_to_pixmap(self._image_bgr))

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._manual_btn = QPushButton(self._i18n.t("crop_advisor.manual_mode"))
        self._manual_btn.setObjectName("manualBtn")
        self._manual_btn.setCheckable(True)
        self._manual_btn.setCursor(Qt.PointingHandCursor)
        self._manual_btn.clicked.connect(self._toggle_manual_mode)
        btn_row.addWidget(self._manual_btn)
        close_btn = QPushButton(self._i18n.t("crop_advisor.close"))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        right.addLayout(btn_row)

        root.addLayout(right, 1)

        # ── 启动后台线程 / Start background thread ────────────────────────────
        self._worker = _Worker(image_path)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _build_stylesheet(self) -> str:
        """构建弹窗深色主题样式。Build the dialog's dark theme stylesheet."""
        bg = _c("bg_elevated", "#1e1e24")
        card = _c("bg_card", "#26262e")
        border = _c("border_subtle", "#3a3a44")
        text = _c("text_primary", "#e8e8ea")
        accent = _c("accent", "#3b82f6")
        return f"""
            QDialog {{ background: {bg}; }}
            QLabel {{ color: {text}; }}
            QLabel#statusBadge {{
                color: {text}; font-size: 15px; font-weight: 600;
                padding: 8px 14px; background: {card};
                border: 1px solid {border}; border-radius: 10px;
            }}
            QFrame#previewContainer {{
                background: {card}; border: 1px solid {border}; border-radius: 14px;
            }}
            QScrollArea#filmstrip {{ background: {card}; border: 1px solid {border}; border-radius: 12px; }}
            QPushButton {{
                color: {text}; background: {card}; border: 1px solid {border};
                border-radius: 9px; padding: 8px 18px; font-size: 13px;
            }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton#manualBtn:checked {{
                background: {accent}; border-color: {accent}; color: #ffffff; font-weight: 600;
            }}
        """

    # ── 窗口关闭事件 / Window close event ─────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """关闭前等待后台线程退出,避免对象析构后信号仍连接到已销毁的槽。"""
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        super().closeEvent(event)

    def _label_for(self, s: CropSuggestion) -> str:
        """候选标签:原图哨兵显示本地化"原图",其余显示比例。Localize the candidate label."""
        if s.ratio_label == ORIGINAL_LABEL:
            return self._i18n.t("crop_advisor.original")
        return s.ratio_label

    # ── 后台结果回调 / Background result callback ─────────────────────────────

    def _on_done(self, result: CropAdviceResult) -> None:
        """后台完成后在主线程渲染结果。"""
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
        if not self._manual_mode:
            self._status_lbl.setText(
                f"{self._i18n.t('crop_advisor.recommended')}: "
                f"{self._label_for(top)} · {top.topiq_score:.2f}"
            )
        self._show_main(top)
        self._build_strip()

    # ── 模式切换 / Mode toggle ────────────────────────────────────────────────

    def _toggle_manual_mode(self, checked: bool) -> None:
        """切换手动/建议模式。"""
        self._manual_mode = checked
        self._preview.drawing_enabled = checked
        self._preview.clear_rubber_band()

        if checked:
            self._show_full_image()
        else:
            if self._suggestions:
                restore = self._current_suggestion or self._suggestions[0]
                self._show_main(restore)
            else:
                self._preview.clear_source()
                self._status_lbl.setText(self._i18n.t("crop_advisor.computing"))

    def _show_full_image(self) -> None:
        """手动模式:显示完整原图(自适应缩放)。"""
        if self._image_bgr is None:
            return
        self._preview.set_source(_bgr_to_pixmap(self._image_bgr))
        self._status_lbl.setText(self._i18n.t("crop_advisor.manual_mode"))

    # ── 主预览(建议模式) / Main preview (suggestion mode) ─────────────────────

    def _show_main(self, s: CropSuggestion) -> None:
        """将指定候选渲染为主预览(完整显示)。手动模式下忽略。"""
        if self._manual_mode:
            return
        self._current_suggestion = s
        self._preview.set_source(_bgr_to_pixmap(s.preview_bgr))
        self._status_lbl.setText(
            f"{self._i18n.t('crop_advisor.recommended')}: {self._label_for(s)} · {s.topiq_score:.2f}"
        )
        self._highlight_selected()

    # ── 候选条(左侧竖排) / Filmstrip (left, vertical) ────────────────────────

    def _build_strip(self) -> None:
        """清空并重建左侧竖排缩略图候选条。"""
        while self._strip_layout.count():
            item = self._strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._strip_cells = []

        accent = _c("accent", "#3b82f6")
        border = _c("border_subtle", "#3a3a44")
        card = _c("bg_elevated", "#1e1e24")
        sub = _c("text_secondary", "#a8a8b0")

        for idx, s in enumerate(self._suggestions):
            cell = QFrame()
            cell.setProperty("selected", "false")
            cell.setCursor(Qt.PointingHandCursor)
            cell.setStyleSheet(
                f'QFrame {{ background: {card}; border: 2px solid {border}; border-radius: 10px; }}'
                f'QFrame[selected="true"] {{ border: 2px solid {accent}; }}'
            )
            v = QVBoxLayout(cell)
            v.setContentsMargins(6, 6, 6, 6)
            v.setSpacing(4)

            thumb = QLabel()
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            thumb.setPixmap(_bgr_to_pixmap(s.preview_bgr, max_side=_THUMB_MAX))
            v.addWidget(thumb)

            tag = "★ " if idx == 0 else ""
            score_lbl = QLabel(f"{tag}{self._label_for(s)} · {s.topiq_score:.2f}")
            score_lbl.setAlignment(Qt.AlignCenter)
            score_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            score_lbl.setStyleSheet(
                "border: none; background: transparent; font-size: 12px; "
                f"font-weight: {'700' if idx == 0 else '500'}; "
                f"color: {accent if idx == 0 else sub};"
            )
            v.addWidget(score_lbl)

            cell.mousePressEvent = lambda _e, sug=s: self._on_thumb_clicked(sug)
            self._strip_layout.addWidget(cell)
            self._strip_cells.append((cell, s))

        self._strip_layout.addStretch(1)
        self._highlight_selected()

    def _on_thumb_clicked(self, s: CropSuggestion) -> None:
        """点击候选缩略图:建议模式下切换主预览。"""
        if self._manual_mode:
            return
        self._show_main(s)

    def _highlight_selected(self) -> None:
        """根据当前候选高亮对应缩略图边框。"""
        for cell, s in self._strip_cells:
            cell.setProperty("selected", "true" if s is self._current_suggestion else "false")
            cell.style().unpolish(cell)
            cell.style().polish(cell)

    # ── 手动裁剪坐标映射 / Manual crop coordinate mapping ────────────────────

    def _map_manual_to_orig(
        self, rel_x: float, rel_y: float, pix_w: int, pix_h: int
    ) -> Tuple[int, int]:
        """像素图内相对坐标 → 原图像素坐标(按 orig/pix 比例)。"""
        if self._image_bgr is None or pix_w == 0 or pix_h == 0:
            return 0, 0
        oh, ow = self._image_bgr.shape[:2]
        return int(rel_x * ow / pix_w), int(rel_y * oh / pix_h)

    def _on_crop_drawn(self, label_rect: QRect) -> None:
        """手动模式拖拽释放:映射回原图坐标并打分。"""
        if not self._manual_mode or self._image_bgr is None:
            return

        pix_rect = self._preview.current_pixmap_rect()
        if pix_rect.isEmpty():
            return
        pix_w, pix_h = pix_rect.width(), pix_rect.height()

        clipped = label_rect.intersected(pix_rect)
        if clipped.isEmpty():
            return

        # QRect.right()/bottom() 为包含端点,+1 转 exclusive,使拖到边缘正好等于全宽/全高。
        rel_x1 = float(clipped.left() - pix_rect.left())
        rel_y1 = float(clipped.top() - pix_rect.top())
        rel_x2 = float(clipped.right() + 1 - pix_rect.left())
        rel_y2 = float(clipped.bottom() + 1 - pix_rect.top())

        ox1, oy1 = self._map_manual_to_orig(rel_x1, rel_y1, pix_w, pix_h)
        ox2, oy2 = self._map_manual_to_orig(rel_x2, rel_y2, pix_w, pix_h)

        oh, ow = self._image_bgr.shape[:2]
        x1 = max(0, min(ox1, ox2))
        y1 = max(0, min(oy1, oy2))
        x2 = min(ow, max(ox1, ox2))
        y2 = min(oh, max(oy1, oy2))
        if x2 <= x1 or y2 <= y1:
            return

        score = score_manual_crop(self._image_bgr, (x1, y1, x2, y2))
        if score is not None:
            self._status_lbl.setText(
                f"{self._i18n.t('crop_advisor.manual_score')}: {score:.2f}"
            )
        else:
            self._status_lbl.setText(self._i18n.t("crop_advisor.manual_score") + ": —")
