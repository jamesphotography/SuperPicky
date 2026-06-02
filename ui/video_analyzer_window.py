# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 1 — 视频分析独立窗口

入口：
    主窗口菜单栏「视频」→「视频分析」

功能（Phase 1）：
    - 拖入单个视频 或 选择目录批量分析
    - YOLO 鸟类检测（有鸟/无鸟二分类）
    - 自适应抽帧（max_frames=60 默认，用户可调）
    - 输出每个视频的 SRT 字幕文件（保存到视频旁）

不在 Phase 1 范围：
    - BirdID 鸟种识别（Phase 2）
    - FlightDetector / KeypointDetector（Phase 2）
    - 按鸟种重命名/分目录（Phase 2）
    - Windows 支持（Phase 3）

Video analysis standalone window (Phase 1: YOLO bird/no-bird only).
"""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import VIDEO_EXTENSIONS_ALL
from tools.i18n import get_i18n
from ui.styles import COLORS, FONTS, GLOBAL_STYLE


# ============================================================================
# 拖放区域 / Drop area widget
# ============================================================================

class _VideoDropArea(QFrame):
    """
    拖放区：支持拖入单视频或文件夹

    Drop area widget: accepts a single video file or a folder.
    """
    pathsDropped = Signal(list)   # list[str] — 视频文件绝对路径列表

    def __init__(self):
        super().__init__()
        self.setObjectName("VideoDropArea")
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        self.setStyleSheet(f"""
            QFrame#VideoDropArea {{
                border: 2px dashed {COLORS['border']};
                border-radius: 10px;
                background-color: {COLORS['bg_elevated']};
            }}
            QFrame#VideoDropArea:hover {{
                border: 2px dashed {COLORS['accent']};
                background-color: {COLORS['bg_card']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        icon = QLabel("🎬")
        icon.setStyleSheet(f"font-size: 42px; color: {COLORS['text_tertiary']}; background: transparent;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        hint = QLabel("拖入视频文件 / 文件夹，或点击下方按钮选择")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        ext_hint = QLabel("支持 .mp4 / .mov / .m4v")
        ext_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; background: transparent;")
        ext_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(ext_hint)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        video_paths: List[str] = []
        for url in urls:
            local = url.toLocalFile()
            if os.path.isdir(local):
                for f in sorted(os.listdir(local)):
                    full = os.path.join(local, f)
                    if os.path.isfile(full) and _is_video(full):
                        video_paths.append(full)
            elif _is_video(local):
                video_paths.append(local)
        if video_paths:
            self.pathsDropped.emit(video_paths)


def _is_video(path: str) -> bool:
    """判断是否为支持的视频文件 / Check if file is a supported video"""
    return os.path.splitext(path)[1] in VIDEO_EXTENSIONS_ALL


# ============================================================================
# 后台 Worker：模型加载 + 视频分析 / Background worker
# ============================================================================

class _AnalysisWorker(QThread):
    """
    后台线程：加载 YOLO 模型 + 逐个分析视频

    Signals:
        model_loading  () : 开始加载模型
        model_loaded   () : 模型加载完毕
        file_started   (str, int, int) : 开始处理一个文件 (name, idx, total)
        file_progress  (int, int)      : 单文件帧进度 (done, total_frames)
        file_done      (object)        : 单文件完成，传 VideoAnalysisResult
        all_done       ()              : 所有文件处理完
        error          (str)           : 出错

    Background worker thread: load YOLO model + analyze videos sequentially.
    """
    model_loading = Signal()
    model_loaded = Signal()
    file_started = Signal(str, int, int)
    file_progress = Signal(int, int)
    file_done = Signal(object)
    all_done = Signal()
    error = Signal(str)

    def __init__(self, video_paths: List[str],
                 max_frames: int, yolo_threshold: float,
                 min_segment_frames: int):
        super().__init__()
        self.video_paths = video_paths
        self.max_frames = max_frames
        self.yolo_threshold = yolo_threshold
        self.min_segment_frames = min_segment_frames
        self._stop_requested = False

    def request_stop(self):
        """请求停止处理（在帧粒度上中断）/ Request stop (frame-granularity)"""
        self._stop_requested = True

    def run(self):
        try:
            # 加载模型 / Load model
            self.model_loading.emit()
            from ai_model import load_yolo_model
            from core.video_analyzer import VideoAnalyzer

            model = load_yolo_model()
            self.model_loaded.emit()

            analyzer = VideoAnalyzer(
                yolo_model=model,
                max_frames=self.max_frames,
                yolo_threshold=self.yolo_threshold,
                min_segment_frames=self.min_segment_frames,
            )

            total = len(self.video_paths)
            for idx, path in enumerate(self.video_paths, 1):
                if self._stop_requested:
                    break
                name = os.path.basename(path)
                self.file_started.emit(name, idx, total)
                try:
                    result = analyzer.analyze(
                        path,
                        progress_callback=lambda done, t: self.file_progress.emit(done, t),
                        stop_callback=lambda: self._stop_requested,
                    )
                    self.file_done.emit(result)
                except Exception as e:
                    self.error.emit(f"{name}: {e}")
                    continue

            self.all_done.emit()
        except Exception as e:
            self.error.emit(str(e))


# ============================================================================
# 主窗口 / Main window
# ============================================================================

class VideoAnalyzerWindow(QMainWindow):
    """
    视频分析独立窗口（Phase 1）

    Standalone window for video bird-detection analysis (Phase 1).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self.setWindowTitle("视频分析（Phase 1 · 仅 YOLO 有鸟/无鸟）")
        self.resize(900, 680)
        self.setStyleSheet(GLOBAL_STYLE)

        # 设置图标 / Set window icon (与主窗口一致 / consistent with main window)
        try:
            from config import get_resource_path
            icon_path = get_resource_path("img/icon.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        self._worker: Optional[_AnalysisWorker] = None
        self._results: List[object] = []   # VideoAnalysisResult 列表

        self._build_ui()

    # ── UI 构建 / UI construction ──────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)

        # 标题
        title = QLabel("🎬 视频鸟类分析")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {COLORS['text_primary']};")
        outer.addWidget(title)

        subtitle = QLabel("Phase 1 — 自适应抽帧 + YOLO 有鸟/无鸟检测 + SRT 字幕生成 (macOS)")
        subtitle.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        outer.addWidget(subtitle)

        # 拖放区
        self.drop_area = _VideoDropArea()
        self.drop_area.pathsDropped.connect(self._on_paths_dropped)
        outer.addWidget(self.drop_area)

        # 按钮行：选择文件 / 选择目录
        btn_row = QHBoxLayout()
        self.btn_pick_files = QPushButton("选择视频文件…")
        self.btn_pick_files.clicked.connect(self._on_pick_files)
        self.btn_pick_dir = QPushButton("选择文件夹…")
        self.btn_pick_dir.clicked.connect(self._on_pick_dir)
        btn_row.addWidget(self.btn_pick_files)
        btn_row.addWidget(self.btn_pick_dir)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        # 参数面板：抽帧上限 + 阈值
        cfg_row = self._build_config_row()
        outer.addLayout(cfg_row)

        # 进度区
        self.progress_label = QLabel("就绪")
        self.progress_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        outer.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        outer.addWidget(self.progress_bar)

        # 控制按钮：开始 / 停止 / 保存 SRT
        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton("开始分析")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_start.setEnabled(False)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_save_srt = QPushButton("保存 SRT 字幕到视频旁")
        self.btn_save_srt.clicked.connect(self._on_save_srt)
        self.btn_save_srt.setEnabled(False)
        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_stop)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self.btn_save_srt)
        outer.addLayout(ctrl_row)

        # 结果表
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(5)
        self.result_table.setHorizontalHeaderLabels(
            ["文件名", "有鸟?", "段数", "抽帧", "耗时"]
        )
        self.result_table.horizontalHeader().setStretchLastSection(False)
        self.result_table.setColumnWidth(0, 320)
        self.result_table.setColumnWidth(1, 70)
        self.result_table.setColumnWidth(2, 70)
        self.result_table.setColumnWidth(3, 100)
        self.result_table.setColumnWidth(4, 120)
        outer.addWidget(self.result_table, 1)

        # 状态栏
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("拖入视频或选择文件后点「开始分析」")

        # 队列：待分析文件路径
        self._queue: List[str] = []

    def _build_config_row(self) -> QHBoxLayout:
        """构建配置参数行 / Build the config parameter row"""
        row = QHBoxLayout()

        # 抽帧上限
        row.addWidget(QLabel("抽帧上限:"))
        self.max_frames_spin = QSpinBox()
        self.max_frames_spin.setRange(30, 240)
        self.max_frames_spin.setSingleStep(10)
        self.max_frames_spin.setValue(60)
        self.max_frames_spin.setSuffix(" 帧")
        self.max_frames_spin.setFixedWidth(110)
        row.addWidget(self.max_frames_spin)

        row.addSpacing(20)

        # YOLO 置信度阈值
        row.addWidget(QLabel("置信度阈值:"))
        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(30, 90)
        self.conf_slider.setValue(50)
        self.conf_slider.setFixedWidth(180)
        row.addWidget(self.conf_slider)
        self.conf_value_label = QLabel("0.50")
        self.conf_value_label.setFixedWidth(40)
        row.addWidget(self.conf_value_label)
        self.conf_slider.valueChanged.connect(
            lambda v: self.conf_value_label.setText(f"{v/100:.2f}"))

        row.addStretch()
        return row

    # ── 文件输入 / File input handlers ────────────────────────────────

    def _on_paths_dropped(self, paths: List[str]):
        self._queue = list(paths)
        self._refresh_queue_status()

    def _on_pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            f"视频文件 ({' '.join('*' + e for e in VIDEO_EXTENSIONS_ALL)})"
        )
        if files:
            self._queue = list(files)
            self._refresh_queue_status()

    def _on_pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if not directory:
            return
        videos = sorted(
            os.path.join(directory, f) for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f)) and _is_video(f)
        )
        if not videos:
            QMessageBox.information(self, "提示", f"目录中未找到支持的视频文件\n{directory}")
            return
        self._queue = videos
        self._refresh_queue_status()

    def _refresh_queue_status(self):
        n = len(self._queue)
        self.btn_start.setEnabled(n > 0)
        self.statusBar().showMessage(f"已添加 {n} 个视频，点「开始分析」开始")
        self.progress_label.setText(f"待处理：{n} 个视频")

    # ── 分析控制 / Analysis control ───────────────────────────────────

    def _on_start(self):
        if not self._queue:
            return
        # 清空旧结果
        self._results = []
        self.result_table.setRowCount(0)
        self.progress_bar.setValue(0)

        # UI 状态切换
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_pick_files.setEnabled(False)
        self.btn_pick_dir.setEnabled(False)
        self.btn_save_srt.setEnabled(False)

        # 启动 worker
        self._worker = _AnalysisWorker(
            video_paths=self._queue,
            max_frames=self.max_frames_spin.value(),
            yolo_threshold=self.conf_slider.value() / 100.0,
            min_segment_frames=2,
        )
        self._worker.model_loading.connect(
            lambda: self.progress_label.setText("正在加载 YOLO 模型…"))
        self._worker.model_loaded.connect(
            lambda: self.progress_label.setText("模型已加载，开始分析"))
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_stop(self):
        if self._worker:
            self._worker.request_stop()
            self.progress_label.setText("正在停止…")
            self.btn_stop.setEnabled(False)

    def _on_file_started(self, name: str, idx: int, total: int):
        self.progress_label.setText(f"[{idx}/{total}] 分析中：{name}")
        self.progress_bar.setRange(0, 0)  # 不定进度（每文件首次重置为忙）

    def _on_file_progress(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    def _on_file_done(self, result):
        self._results.append(result)
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)
        name = os.path.basename(result.video_path)
        self.result_table.setItem(row, 0, QTableWidgetItem(name))
        self.result_table.setItem(row, 1, QTableWidgetItem("🐦 是" if result.has_bird else "—"))
        self.result_table.setItem(row, 2, QTableWidgetItem(str(len(result.segments))))
        self.result_table.setItem(row, 3, QTableWidgetItem(
            f"{result.sampled_frames}/{result.strategy_used}"))
        self.result_table.setItem(row, 4, QTableWidgetItem(
            f"{result.total_wall_ms/1000:.1f}s"))

    def _on_all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_pick_files.setEnabled(True)
        self.btn_pick_dir.setEnabled(True)
        self.btn_save_srt.setEnabled(len(self._results) > 0)
        n = len(self._results)
        total_ms = sum(r.total_wall_ms for r in self._results)
        self.progress_label.setText(
            f"完成：处理 {n} 个视频，总耗时 {total_ms/1000:.1f}s")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.statusBar().showMessage(
            f"分析完成。点「保存 SRT」可把字幕写到每个视频旁边")

    def _on_error(self, msg: str):
        self.statusBar().showMessage(f"⚠️ 错误: {msg}")

    # ── SRT 保存 / SRT export ─────────────────────────────────────────

    def _on_save_srt(self):
        from core.video_segment import write_srt
        if not self._results:
            return
        saved, failed = 0, 0
        for r in self._results:
            try:
                video_path = r.video_path
                base, _ = os.path.splitext(video_path)
                srt_path = base + ".srt"
                write_srt(r.segments, srt_path)
                saved += 1
            except Exception as e:
                failed += 1
                self.statusBar().showMessage(f"⚠️ SRT 写入失败: {e}")
        QMessageBox.information(
            self, "SRT 保存完成",
            f"已保存 {saved} 个 SRT 字幕文件" + (f"，{failed} 个失败" if failed else ""))

    # ── 关闭处理 / Close handler ──────────────────────────────────────

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self, "确认关闭",
                "分析正在进行，确定关闭？已处理的视频结果将丢失。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.request_stop()
            self._worker.wait(3000)
        event.accept()


# ============================================================================
# 独立运行入口 / Standalone entry (debug only)
# ============================================================================

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    w = VideoAnalyzerWindow()
    w.show()
    sys.exit(app.exec())
