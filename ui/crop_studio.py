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

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from core.crop_advisor import CropAdviceResult, advise_crops

try:
    from ui.styles import COLORS
except Exception:  # 主题缺失时的安全兜底 / Safe fallback if theme unavailable
    COLORS = {}


# 画布背景中灰 / Canvas neutral-gray background.
CANVAS_BG: str = "#808080"


def _c(key: str, fallback: str) -> str:
    """读取主题色,缺失则用兜底值 / Read a theme color with a fallback."""
    return COLORS.get(key, fallback)


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

        self._canvas_host = self._build_canvas_host()
        main_row.addWidget(self._canvas_host, 1)

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

    def _build_canvas_host(self) -> QWidget:
        """中画布占位(Task 3 替换为 _Canvas:深灰 fit/zoom/投影)。"""
        host = QFrame()
        host.setObjectName("cropStudioCanvas")
        host.setStyleSheet(
            f"QFrame#cropStudioCanvas {{ background: {CANVAS_BG}; }}"
        )
        return host

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
