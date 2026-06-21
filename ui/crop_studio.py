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
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.crop_advisor import CropAdviceResult, advise_crops
from ui.icon_utils import ICON_ACTIVE, ICON_IDLE, load_tinted_icon

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

        self._img = QLabel()
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet("border: 2px solid #ffffff; background: transparent;")
        shadow = QGraphicsDropShadowEffect(self._img)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._img.setGraphicsEffect(shadow)
        self._scroll.setWidget(self._img)

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
        self._zoom_bar.show()
        self.fit()

    def _apply(self) -> None:
        """按当前缩放因子渲染源图到内部 QLabel。"""
        if self._src is None or self._src.isNull():
            return
        w = max(1, int(round(self._src.width() * self._zoom)))
        h = max(1, int(round(self._src.height() * self._zoom)))
        scaled = self._src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._img.setPixmap(scaled)
        self._img.adjustSize()
        self._sync_controls()

    def _sync_controls(self) -> None:
        """同步滑块/百分比显示(屏蔽信号防回环)。"""
        pct = int(round(self._zoom * 100))
        self._pct_lbl.setText(f"{pct}%")
        blocked = self._slider.blockSignals(True)
        self._slider.setValue(max(self.PCT_MIN, min(self.PCT_MAX, pct)))
        self._slider.blockSignals(blocked)

    def set_zoom(self, factor: float) -> None:
        """设置绝对缩放因子(1.0 = 1:1),夹到 [PCT_MIN, PCT_MAX]。"""
        factor = max(self.PCT_MIN / 100.0, min(self.PCT_MAX / 100.0, float(factor)))
        self._zoom = factor
        self._apply()
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

    def __init__(self, photo: dict, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._photo: dict = photo
        self._i18n = i18n

        # 当前选中裁剪框(全分辨率坐标);None = 原图不裁剪。导出用。
        # Currently selected crop box (full-res coords); None = full frame. For export.
        self._current_box = None
        self._mode: str = "crop"  # crop | manual | auto

        self._image_path: str = self._resolve_image_path(photo)

        self.setStyleSheet(f"QWidget {{ background: {_c('bg_primary', '#111111')}; }}")

        # ── 顶层垂直布局:顶栏 + 主行 / Root vertical: top bar + main row ──────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._top_bar = self._build_top_bar()
        root.addWidget(self._top_bar)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)

        self._toolbar = self._build_toolbar()
        main_row.addWidget(self._toolbar)

        self._canvas = _Canvas()
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

    def _build_top_bar(self) -> QWidget:
        """顶栏占位(Task 5 填充鸟种/文件名/星级/罕见度/IUCN/返回/导出)。"""
        bar = QFrame()
        bar.setObjectName("cropStudioTopBar")
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame#cropStudioTopBar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-bottom: 1px solid {_c('border', '#2a2a2a')}; }}"
        )
        return bar

    def _build_toolbar(self) -> QWidget:
        """左竖工具栏占位(Task 5 填充:裁剪/特写/鸟种/自动/删除)。"""
        tb = QFrame()
        tb.setObjectName("cropStudioToolbar")
        tb.setFixedWidth(72)
        tb.setStyleSheet(
            f"QFrame#cropStudioToolbar {{ background: {_c('bg_elevated', '#1a1a1a')};"
            f" border-right: 1px solid {_c('border', '#2a2a2a')}; }}"
        )
        return tb

    def _build_candidate_panel(self) -> QWidget:
        """右候选占位(Task 4 填充:letterbox 双列候选)。"""
        panel = QFrame()
        panel.setObjectName("cropStudioCandidates")
        panel.setFixedWidth(320)
        panel.setStyleSheet(
            f"QFrame#cropStudioCandidates {{ background: {_c('bg_card', '#1f1f1f')};"
            f" border-left: 1px solid {_c('border', '#2a2a2a')}; }}"
        )
        return panel

    # ── 后台结果回调 / Background result callback ─────────────────────────────

    def _on_advice(self, result: CropAdviceResult) -> None:
        """
        后台 advise_crops 完成后在主线程回调。Task 4 在此填充右栏候选并选中最优;
        本任务(骨架)仅记录结果占位。

        Called on the UI thread when advise_crops finishes. Task 4 populates the
        candidate panel here; the skeleton just stores the result.
        """
        self._advice_result = result

    # ── 关闭 / Close ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """关闭前等待后台线程退出,避免槽连接到已销毁对象。"""
        if self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(2000)
        self.closed.emit()
        super().closeEvent(event)
