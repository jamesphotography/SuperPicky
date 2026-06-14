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
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
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
    拖放区：支持拖入单视频或文件夹（紧凑细条）

    Drop area widget: accepts a single video file or a folder (compact bar).
    """
    pathsDropped = Signal(list)   # list[str] — 视频文件绝对路径列表

    def __init__(self):
        super().__init__()
        self.setObjectName("VideoDropArea")
        self.setAcceptDrops(True)
        self.setFixedHeight(54)
        self.setStyleSheet(f"""
            QFrame#VideoDropArea {{
                border: 1px dashed {COLORS['border']};
                border-radius: 6px;
                background-color: {COLORS['bg_elevated']};
            }}
            QFrame#VideoDropArea:hover {{
                border: 1px dashed {COLORS['accent']};
                background-color: {COLORS['bg_card']};
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        icon = QLabel("🎬")
        icon.setStyleSheet(f"font-size: 24px; color: {COLORS['text_tertiary']}; background: transparent;")
        layout.addWidget(icon)

        hint = QLabel(get_i18n().t("video.drop_hint"))
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; background: transparent;")
        layout.addWidget(hint)

        layout.addStretch()

        ext_hint = QLabel(get_i18n().t("video.supported_formats"))
        ext_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px; background: transparent;")
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


def _format_segments_detail(segments) -> str:
    """
    把段列表展开成多行文本（每段一行），用于结果表「时间段」列

    每行格式示例：
        0:00-0:07  🐦 澳洲蛇鹈 83%  停栖
        0:07-0:08  [无鸟]
        0:08-0:33  🦅 白鹭 94%  飞行

    Expand segment list into multi-line text (one segment per line).
    """
    if not segments:
        return "—"
    # 鸟种名跟随界面语言：英文模式优先 species_en，中文模式优先 species_zh
    # Species name follows UI language: prefer en in English mode, zh otherwise.
    i18n = get_i18n()
    is_en = i18n.current_lang.startswith('en')
    lines = []
    for s in segments:
        time_str = f"{_fmt_mmss(s.start_sec)}-{_fmt_mmss(s.end_sec)}"
        if not s.has_bird:
            lines.append(f"{time_str}  {i18n.t('video.seg_no_bird')}")
            continue
        # 有鸟段：组装鸟种 + 飞行 + 置信度
        parts = [time_str]
        species_name = (s.species_en or s.species_zh) if is_en else (s.species_zh or s.species_en)
        if species_name:
            emoji = "🦅" if s.is_flying else "🐦"
            parts.append(f"{emoji} {species_name}")
            if s.species_conf > 0:
                parts.append(f"{int(round(s.species_conf*100))}%")
        else:
            parts.append(i18n.t('video.seg_has_bird'))
            if s.avg_conf > 0:
                parts.append(f"{int(round(s.avg_conf*100))}%")
        if s.is_flying is not None:
            parts.append(i18n.t('video.seg_flying') if s.is_flying else i18n.t('video.seg_perched'))
        lines.append("  ".join(parts))
    return "\n".join(lines)


def _fmt_mmss(sec: float) -> str:
    """秒数 → m:ss 格式（结果表用，比 SRT 简洁）/ Seconds → m:ss for compact display"""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}:{s:05.2f}"


def _fmt_duration_short(sec: float) -> str:
    """
    视频时长 → 简短显示

    < 60s   → "16 秒"
    >= 60s  → "1:23"（分:秒）

    Compact duration label for filename suffix.
    """
    if sec < 60:
        return get_i18n().t("video.duration_sec", sec=int(round(sec)))
    m = int(sec) // 60
    s = int(round(sec - m * 60))
    return f"{m}:{s:02d}"


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
                 min_segment_frames: int,
                 enable_species: bool = False,
                 species_mode: str = 'fast',
                 enable_flight: bool = False):
        """
        Parameters:
            video_paths       : 待处理视频路径列表
            max_frames        : 抽帧上限
            yolo_threshold    : YOLO 置信度阈值
            min_segment_frames: 段最小帧数
            enable_species    : 是否启用鸟种识别（Phase 2）
            species_mode      : 'fast' (单帧) / 'full' (多帧投票)
            enable_flight     : 是否启用飞行检测（Phase 2）
        """
        super().__init__()
        self.video_paths = video_paths
        self.max_frames = max_frames
        self.yolo_threshold = yolo_threshold
        self.min_segment_frames = min_segment_frames
        self.enable_species = enable_species
        self.species_mode = species_mode
        self.enable_flight = enable_flight
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

            # Phase 2: 按需实例化鸟种 / 飞行 classifier
            # Phase 2: lazily instantiate species/flight classifiers if enabled.
            species_clf = None
            flight_clf = None
            if self.enable_species:
                from core.birdid_adapter import BirdIDAdapter
                species_clf = BirdIDAdapter()
            if self.enable_flight:
                from core.flight_adapter import FlightAdapter
                flight_clf = FlightAdapter()

            self.model_loaded.emit()

            analyzer = VideoAnalyzer(
                yolo_model=model,
                max_frames=self.max_frames,
                yolo_threshold=self.yolo_threshold,
                min_segment_frames=self.min_segment_frames,
                species_classifier=species_clf,
                flight_classifier=flight_clf,
                species_mode=self.species_mode,
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
        self.setWindowTitle(self.i18n.t("video.title_phase1"))
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
        title = QLabel(self.i18n.t("video.heading"))
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {COLORS['text_primary']};")
        outer.addWidget(title)

        subtitle = QLabel(self.i18n.t("video.subheading"))
        subtitle.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        outer.addWidget(subtitle)

        # 拖放区
        self.drop_area = _VideoDropArea()
        self.drop_area.pathsDropped.connect(self._on_paths_dropped)
        outer.addWidget(self.drop_area)

        # 按钮行：选择文件 / 选择目录
        btn_row = QHBoxLayout()
        self.btn_pick_files = QPushButton(self.i18n.t("video.btn_pick_files"))
        self.btn_pick_files.clicked.connect(self._on_pick_files)
        self.btn_pick_dir = QPushButton(self.i18n.t("video.btn_pick_folder"))
        self.btn_pick_dir.clicked.connect(self._on_pick_dir)
        btn_row.addWidget(self.btn_pick_files)
        btn_row.addWidget(self.btn_pick_dir)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        # 参数面板：抽帧上限 + 阈值
        cfg_row = self._build_config_row()
        outer.addLayout(cfg_row)

        # Phase 2: 鸟种识别 + 飞行检测 开关组
        # Phase 2: species ID + flight detection toggle group
        p2_box = self._build_phase2_box()
        outer.addWidget(p2_box)

        # 进度区
        self.progress_label = QLabel(self.i18n.t("video.status_ready"))
        self.progress_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        outer.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        outer.addWidget(self.progress_bar)

        # 控制按钮：开始 / 停止 / 应用归类（Phase 3）
        # Control buttons: start / stop / organize (Phase 3)
        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton(self.i18n.t("video.btn_start"))
        self.btn_start.clicked.connect(self._on_start)
        self.btn_start.setEnabled(False)
        self.btn_stop = QPushButton(self.i18n.t("video.btn_stop"))
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        self.btn_organize = QPushButton(self.i18n.t("video.btn_apply_organize"))
        self.btn_organize.clicked.connect(self._on_organize)
        self.btn_organize.setEnabled(False)
        self.btn_organize.setToolTip(self.i18n.t("video.organize_tooltip"))
        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_stop)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self.btn_organize)
        outer.addLayout(ctrl_row)

        # 结果表（含状态列：⏳ 等待 / 🔄 处理中 / ✅ 完成）
        # Results table with status column
        from PySide6.QtWidgets import QHeaderView
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(6)
        self.result_table.setHorizontalHeaderLabels([
            self.i18n.t("video.col_status"),
            self.i18n.t("video.col_filename"),
            self.i18n.t("video.col_segments"),
            self.i18n.t("video.col_seg_count"),
            self.i18n.t("video.col_frames"),
            self.i18n.t("video.col_time"),
        ])
        self.result_table.setColumnWidth(0, 100)   # 状态列加宽避免 ✅ 完成 换行
        self.result_table.setColumnWidth(1, 260)   # 文件名带时长后稍宽
        self.result_table.setColumnWidth(3, 50)
        self.result_table.setColumnWidth(4, 80)
        self.result_table.setColumnWidth(5, 80)
        # 时间段列拉伸，且允许换行
        self.result_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.result_table.setWordWrap(True)
        # 行高自适应（多段视频会自动撑高）
        self.result_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # 文件路径 → 表格行索引，便于状态更新
        self._row_by_path: dict[str, int] = {}
        outer.addWidget(self.result_table, 1)

        # 状态栏
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(self.i18n.t("video.statusbar_hint"))

        # 队列：待分析文件路径
        self._queue: List[str] = []

    def _build_phase2_box(self) -> QGroupBox:
        """
        Phase 2 控件：鸟种识别 + 飞行检测 开关 + 识别模式选择

        Phase 2 controls: species ID + flight detection toggles + mode selector.
        """
        box = QGroupBox(self.i18n.t("video.options_group"))
        box.setStyleSheet(f"""
            QGroupBox {{
                color: {COLORS['text_secondary']};
                font-weight: 500;
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding-top: 14px;
                margin-top: 4px;
            }}
            QGroupBox::title {{
                left: 10px;
                padding: 0 4px;
            }}
        """)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        # 鸟种识别开关
        self.species_check = QCheckBox(self.i18n.t("video.enable_birdid"))
        self.species_check.setChecked(True)
        self.species_check.toggled.connect(self._on_species_toggled)
        layout.addWidget(self.species_check)

        # 识别模式下拉（默认极速 — 大多数视频是单一鸟种，跑一帧足够）
        # Mode combo (default 'instant' — most clips have one species, one frame suffices)
        layout.addWidget(QLabel(self.i18n.t("video.mode_label")))
        self.species_mode_combo = QComboBox()
        self.species_mode_combo.addItem(self.i18n.t("video.mode_fast"), "instant")
        self.species_mode_combo.addItem(self.i18n.t("video.mode_standard"), "fast")
        self.species_mode_combo.addItem(self.i18n.t("video.mode_full"), "full")
        self.species_mode_combo.setCurrentIndex(0)
        self.species_mode_combo.setFixedWidth(220)
        self.species_mode_combo.setToolTip(self.i18n.t("video.mode_tooltip"))
        layout.addWidget(self.species_mode_combo)

        layout.addSpacing(16)

        # 飞行检测开关
        self.flight_check = QCheckBox(self.i18n.t("video.enable_flight"))
        self.flight_check.setChecked(True)
        layout.addWidget(self.flight_check)

        layout.addSpacing(16)

        # Phase 3: 分析完成后自动整理（默认勾选）
        # Phase 3: auto-organize after analysis (default ON)
        self.auto_organize_check = QCheckBox(self.i18n.t("video.auto_organize_check"))
        self.auto_organize_check.setChecked(True)
        self.auto_organize_check.setToolTip(self.i18n.t("video.auto_organize_tooltip"))
        layout.addWidget(self.auto_organize_check)

        layout.addStretch()
        return box

    def _on_species_toggled(self, checked: bool):
        """鸟种识别开关切换：未启用时禁用模式选择"""
        self.species_mode_combo.setEnabled(checked)

    def _build_config_row(self) -> QHBoxLayout:
        """构建配置参数行 / Build the config parameter row"""
        row = QHBoxLayout()

        # 抽帧上限
        row.addWidget(QLabel(self.i18n.t("video.max_frames_label")))
        self.max_frames_spin = QSpinBox()
        self.max_frames_spin.setRange(30, 240)
        self.max_frames_spin.setSingleStep(10)
        self.max_frames_spin.setValue(60)
        self.max_frames_spin.setSuffix(self.i18n.t("video.frames_suffix"))
        self.max_frames_spin.setFixedWidth(110)
        row.addWidget(self.max_frames_spin)

        row.addSpacing(20)

        # YOLO 置信度阈值
        row.addWidget(QLabel(self.i18n.t("video.conf_threshold_label")))
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
            self, self.i18n.t("video.dlg_pick_files"), "",
            self.i18n.t("video.dlg_filter", exts=' '.join('*' + e for e in VIDEO_EXTENSIONS_ALL))
        )
        if files:
            self._queue = list(files)
            self._refresh_queue_status()

    def _on_pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, self.i18n.t("video.dlg_pick_dir"))
        if not directory:
            return
        videos = sorted(
            os.path.join(directory, f) for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f)) and _is_video(f)
        )
        if not videos:
            QMessageBox.information(self, self.i18n.t("video.tip_title"), self.i18n.t("video.no_video_found", directory=directory))
            return
        self._queue = videos
        self._refresh_queue_status()

    def _refresh_queue_status(self):
        """
        刷新队列状态：把待处理视频填进结果表（状态=⏳ 等待）

        让用户立刻看到「我选了什么」，避免拖放后没反馈。
        Populate the results table with pending entries so the user can see
        what was selected immediately after drop / pick.
        """
        n = len(self._queue)
        self.btn_start.setEnabled(n > 0)
        # 清空旧待处理行（但保留已完成结果），重新填充
        # Clear previous pending rows; refill with new queue + duration probe.
        from core.video_analyzer import probe_video_metadata
        self.result_table.setRowCount(0)
        self._row_by_path.clear()
        for path in self._queue:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            self._row_by_path[path] = row
            self.result_table.setItem(row, 0, QTableWidgetItem(self.i18n.t("video.status_waiting")))
            # 文件名列附加视频时长（轻量 header 探测，每个视频 ~10-20ms）
            # Filename column shows duration probed from the header (~10-20ms per video).
            base = os.path.basename(path)
            meta = probe_video_metadata(path)
            if meta is not None and meta.duration_sec > 0:
                display_name = f"{base}  ({_fmt_duration_short(meta.duration_sec)})"
            else:
                display_name = base
            self.result_table.setItem(row, 1, QTableWidgetItem(display_name))
            self.result_table.setItem(row, 2, QTableWidgetItem(""))
            self.result_table.setItem(row, 3, QTableWidgetItem(""))
            self.result_table.setItem(row, 4, QTableWidgetItem(""))
            self.result_table.setItem(row, 5, QTableWidgetItem(""))
        self.statusBar().showMessage(self.i18n.t("video.added_videos", count=n))
        self.progress_label.setText(self.i18n.t("video.pending_videos", count=n))

    # ── 分析控制 / Analysis control ───────────────────────────────────

    def _on_start(self):
        if not self._queue:
            return
        # 注意：不清空 result_table，因为 _refresh_queue_status 已把待处理行写入。
        # 这里只清空内部 _results 缓存。状态会在 file_started / file_done 中原地更新。
        # Do NOT reset result_table here; pending rows are already there.
        self._results = []
        self.progress_bar.setValue(0)

        # UI 状态切换
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_pick_files.setEnabled(False)
        self.btn_pick_dir.setEnabled(False)
        self.btn_organize.setEnabled(False)

        # 启动 worker
        self._worker = _AnalysisWorker(
            video_paths=self._queue,
            max_frames=self.max_frames_spin.value(),
            yolo_threshold=self.conf_slider.value() / 100.0,
            min_segment_frames=2,
            enable_species=self.species_check.isChecked(),
            species_mode=self.species_mode_combo.currentData(),
            enable_flight=self.flight_check.isChecked(),
        )
        self._worker.model_loading.connect(
            lambda: self.progress_label.setText(self.i18n.t("video.loading_yolo")))
        self._worker.model_loaded.connect(
            lambda: self.progress_label.setText(self.i18n.t("video.model_loaded")))
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_stop(self):
        if self._worker:
            self._worker.request_stop()
            self.progress_label.setText(self.i18n.t("video.stopping"))
            self.btn_stop.setEnabled(False)

    def _on_file_started(self, name: str, idx: int, total: int):
        self.progress_label.setText(self.i18n.t("video.analyzing", idx=idx, total=total, name=name))
        self.progress_bar.setRange(0, 0)  # 不定进度（每文件首次重置为忙）
        # 找到对应行并把状态改为 🔄 处理中
        # Find the row for this file and update status to 🔄
        # 用 idx-1 即可（idx 是 1-based，按队列顺序）
        row = idx - 1
        if 0 <= row < self.result_table.rowCount():
            self.result_table.setItem(row, 0, QTableWidgetItem(self.i18n.t("video.status_processing")))

    def _on_file_progress(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)

    def _on_file_done(self, result):
        """
        单个视频处理完成：原地更新对应行

        Update the row in place rather than appending a new one.
        """
        self._results.append(result)
        row = self._row_by_path.get(result.video_path)
        if row is None or row >= self.result_table.rowCount():
            return
        self.result_table.setItem(row, 0, QTableWidgetItem(self.i18n.t("video.status_done")))
        # 列 1 文件名已存在，不动 / Column 1 (filename) already set
        self.result_table.setItem(row, 2, QTableWidgetItem(
            _format_segments_detail(result.segments)))
        self.result_table.setItem(row, 3, QTableWidgetItem(str(len(result.segments))))
        self.result_table.setItem(row, 4, QTableWidgetItem(
            f"{result.sampled_frames}/{result.strategy_used}"))
        self.result_table.setItem(row, 5, QTableWidgetItem(
            f"{result.total_wall_ms/1000:.1f}s"))
        # 触发行高自适应 / Trigger row height adjustment
        self.result_table.resizeRowToContents(row)

    def _on_all_done(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_pick_files.setEnabled(True)
        self.btn_pick_dir.setEnabled(True)
        # Phase 3: 有任何成功结果就启用归类按钮（无鸟段也归类到「无鸟/」）
        # Phase 3: enable organize whenever any result is available.
        has_results = len(self._results) > 0
        self.btn_organize.setEnabled(has_results)
        n = len(self._results)
        total_ms = sum(r.total_wall_ms for r in self._results)
        self.progress_label.setText(
            self.i18n.t("video.done_summary", count=n, seconds=f"{total_ms/1000:.1f}"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)

        # Phase 3: 自动整理（如勾选）—— 跳过确认弹窗
        # Phase 3: auto-organize if checkbox is on, bypassing confirmation
        if has_results and self.auto_organize_check.isChecked():
            self.statusBar().showMessage(self.i18n.t("video.auto_organizing"))
            self._do_organize(skip_confirm=True)
        else:
            self.statusBar().showMessage(self.i18n.t("video.done_hint"))

    def _on_error(self, msg: str):
        self.statusBar().showMessage(self.i18n.t("video.error_status", error=msg))

    # ── Phase 3: 应用归类（移动 + 重命名 + SRT）/ Organize ─────────────────

    def _on_organize(self):
        """
        Phase 3 入口（按钮点击）：弹确认后整理

        Phase 3 entry (button click): confirm then organize.
        """
        self._do_organize(skip_confirm=False)

    def _do_organize(self, skip_confirm: bool = False):
        """
        实际整理逻辑：按主鸟种重命名 + 移动 + 写 SRT

        参数:
            skip_confirm (bool): True 时跳过确认弹窗（自动整理时使用）

        Run the actual organize flow. skip_confirm=True bypasses the
        confirmation dialog (used by auto-organize).
        """
        if not self._results:
            return

        n = len(self._results)
        if not skip_confirm:
            confirm = QMessageBox.question(
                self, self.i18n.t("video.confirm_organize_title"),
                self.i18n.t("video.organize_confirm_body", count=n),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        from tools.video_organizer import (
            VideoOrganizer, OrganizeOptions, record_organized_results,
        )
        # 落地命名 + SRT 文案跟随界面语言 / Folder & SRT labels follow UI language
        use_en = self.i18n.current_lang.startswith('en')
        organizer = VideoOrganizer(options=OrganizeOptions(
            operation='move',
            use_english=use_en,
            no_bird_folder=self.i18n.t("video.folder_no_bird"),
            other_species_folder=self.i18n.t("video.folder_other_species"),
            flying_label=self.i18n.t("video.seg_flying"),
            perched_label=self.i18n.t("video.seg_perched"),
        ))

        succeeded = 0
        failed_msgs = []
        org_results = []  # 收集用于写「归类清单」（供复原）/ collect for the manifest
        for r in self._results:
            org_result = organizer.organize(r.video_path, r.segments)
            org_results.append(org_result)
            row = self._row_by_path.get(r.video_path)
            if org_result.success:
                succeeded += 1
                if row is not None:
                    self.result_table.setItem(row, 0, QTableWidgetItem(self.i18n.t("video.status_organized")))
                    new_name = os.path.basename(org_result.target_video_path or '')
                    if new_name:
                        self.result_table.setItem(row, 1, QTableWidgetItem(new_name))
                if org_result.error:
                    failed_msgs.append(f"{os.path.basename(r.video_path)}: {org_result.error}")
            else:
                if row is not None:
                    self.result_table.setItem(row, 0, QTableWidgetItem(self.i18n.t("video.status_failed")))
                failed_msgs.append(f"{os.path.basename(r.video_path)}: {org_result.error}")

        # 写「归类清单」，供主界面「重置」精确复原（仅 move 模式）
        # Persist the organize manifest so the main-window reset can undo it (move only).
        try:
            record_organized_results(org_results)
        except Exception:
            pass

        # 结果总结：自动模式只更新状态栏，避免打扰；手动模式弹对话框
        # Summary: auto-mode quiet status update; manual mode shows dialog.
        if skip_confirm:
            msg = self.i18n.t("video.auto_organize_done", succeeded=succeeded, total=n)
            if failed_msgs:
                msg += self.i18n.t("video.warnings_suffix", count=len(failed_msgs))
            self.statusBar().showMessage(msg)
        elif failed_msgs:
            details = "\n".join(failed_msgs[:10]) + ("\n…" if len(failed_msgs) > 10 else "")
            QMessageBox.warning(
                self, self.i18n.t("video.organize_done_warning_title"),
                self.i18n.t("video.organize_warn_body",
                            succeeded=succeeded, total=n, count=len(failed_msgs), details=details)
            )
        else:
            QMessageBox.information(
                self, self.i18n.t("video.organize_done_title"),
                self.i18n.t("video.organize_success_body", count=succeeded)
            )
        self.btn_organize.setEnabled(False)
        if not skip_confirm:
            self.statusBar().showMessage(self.i18n.t("video.organize_final_status", succeeded=succeeded, total=n))

    # ── 关闭处理 / Close handler ──────────────────────────────────────

    def cleanup(self):
        """退出时优雅停止后台分析线程，防止运行中的 QThread 被 GC 析构崩溃。

        供 main_window._cleanup_on_quit() 在程序退出前调用，
        也在 closeEvent 中复用，避免重复逻辑。
        Called by main_window._cleanup_on_quit() before the app exits,
        and reused by closeEvent to avoid duplicated cleanup logic.
        """
        if self._worker is not None:
            try:
                self._worker.finished.disconnect()
                self._worker.error.disconnect()
            except Exception:
                pass
            if self._worker.isRunning():
                self._worker.request_stop()
                self._worker.wait(5000)
            self._worker = None

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self, self.i18n.t("video.close_confirm_title"),
                self.i18n.t("video.close_confirm_text"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # 复用 cleanup() 确保线程安全退出
            # Reuse cleanup() to ensure thread is safely stopped before destruction.
            self.cleanup()
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
