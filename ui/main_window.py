# -*- coding: utf-8 -*-
"""
SuperPicky - 主窗口
PySide6 版本 - 极简艺术风格
"""

import os
import sys
import threading
import subprocess
from types import SimpleNamespace
from pathlib import Path


def get_resource_path(relative_path):
    """获取资源文件路径（兼容 PyInstaller 打包环境）"""
    # PyInstaller 打包后会设置 _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str):
        return os.path.join(meipass, relative_path)
    # 开发环境
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), relative_path)

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QProgressBar,
    QTextEdit, QGroupBox, QCheckBox, QMenuBar, QMenu,
    QFileDialog, QMessageBox, QSizePolicy, QFrame, QSpacerItem,
    QDialog,
    QSystemTrayIcon, QApplication  # V4.0: 系统托盘图标
)
from PySide6.QtCore import (
    Qt, Signal, QObject, Slot, QTimer, QPropertyAnimation, QEasingCurve, QMimeData,
    QThread, QStandardPaths, QSize, QRect,
)
from PySide6.QtGui import QFont, QPixmap, QIcon, QAction, QTextCursor, QColor, QDragEnterEvent, QDropEvent, QKeySequence

from tools.i18n import get_i18n, set_primary_language
from advanced_config import get_advanced_config
from config import config as app_config, get_app_config_dir
from ui.styles import (
    GLOBAL_STYLE, TITLE_STYLE, SUBTITLE_STYLE, VERSION_STYLE, VALUE_STYLE,
    COLORS, FONTS, LOG_COLORS, PROGRESS_INFO_STYLE, PROGRESS_PERCENT_STYLE
)
from ui.custom_dialogs import StyledMessageBox
from ui.icon_utils import load_tinted_icon, checkbox_indicator_qss, tinted_png_path, ICON_IDLE
from ui.skill_level_dialog import get_skill_level_thresholds
from ui.welcome_onboarding_dialog import EnvironmentRepairDialog, WelcomeOnboardingDialog

import re as _re
# 运行日志去 emoji:覆盖 emoji 主区 / Dingbats / 杂项符号,
# 但保留文本符号 ★☆(2605/2606)、箭头 →、分隔线 ━、乘号 ×。
_LOG_EMOJI_RE = _re.compile(
    "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\U00002B00-\U00002BFF"
    "\u2600-\u2604\u2607-\u26FF\uFE0F]+"
)
from core.initialization_manager import InitializationManager


# V3.9: 支持拖放的目录输入框
class DropLineEdit(QLineEdit):
    """支持拖放目录的 QLineEdit"""
    pathDropped = Signal(str)  # 拖放目录后发射此信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """验证拖入的内容"""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if os.path.isdir(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        """处理拖放"""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.setText(path)
                self.pathDropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


def _format_wall_clock(ts: float) -> str:
    """
    把 epoch 时间戳格式化为本地墙钟 HH:MM:SS，供完成报告显示。

    用户可以拿报告里的开始/结束时间与自己的手表对照，自验总耗时是否
    真实。时间戳缺失（0 或负数）时返回占位符，避免显示 1970 年的误导值。

    参数:
    ts (float): epoch 时间戳（time.time()）。

    返回:
    str: 本地时间 "HH:MM:SS"，缺失时为 "--:--:--"。

    Format an epoch timestamp as local wall-clock HH:MM:SS for the
    completion report, so users can verify the total duration against a
    real clock. Missing timestamps (0 or negative) yield a placeholder
    instead of a misleading 1970 time.

    Parameters:
    ts (float): Epoch timestamp from time.time().

    Return:
    str: Local "HH:MM:SS", or "--:--:--" when missing.
    """
    if not ts or ts <= 0:
        return "--:--:--"
    from datetime import datetime as _dt2

    return _dt2.fromtimestamp(ts).strftime("%H:%M:%S")


class WorkerSignals(QObject):
    """工作线程信号"""
    progress = Signal(int)
    log = Signal(str, str)  # message, tag
    finished = Signal(dict)
    error = Signal(str)
    crop_preview = Signal(object, object, object)  # 裁剪预览图(numpy BGR) + focus_status + rating(星级)
    update_check_done = Signal(bool, object)  # V4.2: 更新检测完成 (has_update, update_info)


class WorkerThread(threading.Thread):
    """处理线程"""

    def __init__(self, dir_path, ui_settings, signals, i18n=None, resume=False, scan_results=None):
        super().__init__(daemon=True)
        self.dir_path = dir_path
        self.ui_settings = ui_settings
        self.signals = signals
        self.i18n = i18n or get_i18n()
        self.resume = resume
        self.scan_results = list(scan_results) if scan_results is not None else None
        self._stop_event = threading.Event()
        self._active_processor = None
        self.caffeinate_process = None

        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_1': 0,
            'star_0': 0,
            'no_bird': 0,
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }

    def run(self):
        """执行处理"""
        try:
            self._start_caffeinate()
            self.process_files()
            # V4.3 Phase 4: 照片处理完后追加视频处理（共用 signals/stop_event）
            # Phase 4: append video processing after photos (shared signals).
            self._process_videos()
            self.signals.finished.emit(self.stats)
        except Exception as e:
            if e.__class__.__name__ == "ProcessingCancelled":
                self.signals.log.emit("Processing cancelled.", "warning")
            else:
                self.signals.error.emit(str(e))
        finally:
            self._stop_caffeinate()

    # ──────────────────────────────────────────────────────────────────
    # V4.3 Phase 4: 视频处理阶段
    # V4.3 Phase 4: Video processing stage
    # ──────────────────────────────────────────────────────────────────

    def _process_videos(self):
        """
        WorkerThread 内的视频处理阶段：
            - 从 self.scan_results 收集所有视频路径
            - 检查 advanced_config 是否启用主流程视频处理
            - 调用 VideoBatchEngine 串行处理
            - 共用主信号 (log / progress / 中断)

        Video stage inside WorkerThread: pull videos from scan_results,
        call VideoBatchEngine with shared signals.
        """
        # 收集所有目录中的视频文件 / Gather all videos across scanned dirs
        if not self.scan_results:
            return
        video_paths = []
        for item in self.scan_results:
            video_paths.extend(item.video_files)
        if not video_paths:
            return

        # 读配置 / Read config
        try:
            from advanced_config import get_advanced_config
            cfg = get_advanced_config()
        except Exception:
            return
        if not cfg.video_auto_process_in_main:
            self.signals.log.emit(
                f"⏭ 检测到 {len(video_paths)} 个视频，但视频处理未启用（参数设置可开启）",
                "info"
            )
            return

        # V4.6(Paul P1): 平铺布局下跳过视频自动归类——视频处理的落地产物
        # 就是移动+改名(组织器无 no-op 模式),不移动则无产出,整体跳过并留日志。
        # V4.6 (Paul P1): under the flat layout skip video auto-organize —
        # its only durable output is the move+rename, so skip entirely.
        from core.folder_layout import LAYOUT_FLAT
        if cfg.folder_layout == LAYOUT_FLAT:
            from tools.i18n import get_i18n
            self.signals.log.emit(get_i18n().t("logs.video_skip_flat"), "info")
            return

        # 实例化批量引擎 / Build batch engine
        from core.video_batch_engine import VideoBatchEngine
        engine = VideoBatchEngine(
            max_frames=int(cfg.config.get("video_max_frames") or 60),
            yolo_threshold=float(cfg.config.get("video_yolo_threshold") or 0.5),
            min_segment_frames=int(cfg.config.get("video_min_segment_frames") or 2),
            species_mode=str(cfg.config.get("video_species_mode") or "instant"),
            enable_species=bool(cfg.config.get("video_enable_species_id", True)),
            enable_flight=bool(cfg.config.get("video_enable_flight", True)),
        )

        # 包装回调 → 转发到 WorkerSignals / Wrap callbacks to forward to signals
        def _log_cb(msg: str, level: str):
            self.signals.log.emit(msg, level)

        def _progress_cb(cur: int, total: int):
            # 视频阶段进度独立显示在状态条；总进度条由照片阶段已经占满
            # Video stage progress shown in status bar; main bar stays at 100%.
            pass

        def _stop_cb() -> bool:
            return self._stop_event.is_set()

        stats = engine.process(
            video_paths=video_paths,
            log_cb=_log_cb,
            progress_cb=_progress_cb,
            stop_cb=_stop_cb,
        )

        # 把视频统计合并到主 stats（便于 finished 弹窗显示）
        # Merge video stats into main stats for the finished dialog.
        self.stats['video_total'] = stats.total
        self.stats['video_organized'] = stats.organized
        self.stats['video_failed'] = stats.failed
        self.stats['video_species_counts'] = dict(stats.species_counts)
        self.stats['video_total_time'] = stats.total_wall_ms / 1000.0

    def request_stop(self):
        self._stop_event.set()
        if self._active_processor is not None:
            try:
                self._active_processor.request_stop()
            except Exception:
                pass

    def _start_caffeinate(self):
        """启动防休眠"""
        if sys.platform != 'darwin':
            return  # 目前仅在 macOS 上支持 caffeinate
            
        try:
            # V4.6: 移除原 V3.8.1 的 `killall caffeinate`。它会杀掉本机所有
            # caffeinate 进程(包括用户在终端里为别的长任务开的)，属于越界操作；
            # 且它只是在给「退出时未清理」这个根因打补丁——根因已在
            # _cleanup_on_quit 中无条件调用 _stop_caffeinate() 解决。
            # V4.6: dropped the V3.8.1 `killall caffeinate`. It killed every
            # caffeinate on the machine, including ones the user started for
            # unrelated long-running tasks. It only papered over the real leak,
            # which is now fixed by an unconditional _stop_caffeinate() in
            # _cleanup_on_quit.
            self.caffeinate_process = subprocess.Popen(
                ['caffeinate', '-d', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            if self.i18n:
                self.signals.log.emit(self.i18n.t("logs.caffeinate_started"), "info")
        except Exception as e:
            if self.i18n:
                self.signals.log.emit(self.i18n.t("logs.caffeinate_failed", error=str(e)), "warning")

    def _stop_caffeinate(self):
        """停止防休眠"""
        if self.caffeinate_process:
            try:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=2)
            except Exception:
                try:
                    self.caffeinate_process.kill()
                except Exception:
                    pass
            finally:
                self.caffeinate_process = None

    @staticmethod
    def _finalize_photo_stage_timing(stats: dict, wall_start: float, wall_end: float) -> None:
        """
        用「worker 开始干活 → 照片阶段完成」的 wall time 收口计时统计。

        统一单目录与批量模式的口径：起点是 process_files() 入口（用户确认
        弹窗已点完、真正开始干活），终点是照片阶段全部完成（视频阶段之前）。
        单目录模式下会覆盖 PhotoProcessor 内部口径的旧值——那个口径从
        process() 入口才开始，漏掉预扫描/系统信息采集等 worker 前置开销。

        参数:
        stats (dict): 统计字典，原地写入 start_time/end_time/total_time/avg_time。
        wall_start (float): 照片阶段起点时间戳（time.time()）。
        wall_end (float): 照片阶段终点时间戳。

        返回:
        None

        Finalize timing stats with the wall time from "worker starts real
        work" to "photo stage complete", unifying single-dir and batch
        semantics: start = process_files() entry (user confirmations done),
        end = photo stage done (before the video stage). In single-dir mode
        this overrides PhotoProcessor's internal timing, which starts at
        process() entry and misses pre-scan/system-info overhead.

        Parameters:
        stats (dict): Stats dict; start/end/total/avg written in place.
        wall_start (float): Photo-stage start timestamp (time.time()).
        wall_end (float): Photo-stage end timestamp.

        Return:
        None
        """
        stats['start_time'] = wall_start
        stats['end_time'] = wall_end
        stats['total_time'] = wall_end - wall_start
        total = stats.get('total', 0)
        stats['avg_time'] = stats['total_time'] / total if total > 0 else 0

    def process_files(self):
        """处理文件"""
        import time as _time
        # 照片阶段计时起点：用户确认完毕、worker 真正开始干活（含预扫描、
        # 会话头系统信息采集等前置开销），终点在照片阶段完成处收口。
        # Photo-stage wall-clock start: user confirmations done, real work
        # begins (includes pre-scan and session-header overhead); finalized
        # when the photo stage completes.
        _photo_stage_wall_start = _time.time()
        from tools.utils import log_message as _log_to_file  # 日志文件写入（全程可用）
        from core.photo_processor import (
            PhotoProcessor,
            ProcessingSettings,
            ProcessingCallbacks
        )

        # 读取 BirdID 设置
        # V4.2: 从 ui_settings 读取识鸟开关状态（索引 8），而不是从文件
        birdid_auto_identify = self.ui_settings[8] if len(self.ui_settings) > 8 else False
        birdid_use_geo_filter = True
        birdid_country_code = None
        birdid_region_code = None

        # V4.2: 从高级配置读取识别置信度阈值
        from advanced_config import get_advanced_config
        birdid_confidence_threshold = get_advanced_config().birdid_confidence

        # Task8: 直接从统一设置中心（get_advanced_config）读取国家/区域配置，
        # 避免依赖已废弃的 birdid_dock_settings.json（_save_settings 已删除）。
        # Task8: Read country/region config directly from the unified Settings Center
        # (get_advanced_config) to avoid relying on the deprecated birdid_dock_settings.json
        # (whose writer _save_settings was removed in Task 8).
        _adv_birdid = get_advanced_config()
        birdid_use_geo_filter = _adv_birdid.birdid_use_geo_filter
        birdid_country_code = _adv_birdid.birdid_country_code
        birdid_region_code = _adv_birdid.birdid_region_code

        settings = ProcessingSettings(
            ai_confidence=self.ui_settings[0],
            sharpness_threshold=self.ui_settings[1],
            nima_threshold=self.ui_settings[2],
            save_crop=self.ui_settings[3] if len(self.ui_settings) > 3 else False,
            normalization_mode=self.ui_settings[4] if len(self.ui_settings) > 4 else 'log_compression',
            detect_flight=self.ui_settings[5] if len(self.ui_settings) > 5 else True,
            detect_exposure=self.ui_settings[6] if len(self.ui_settings) > 6 else False,  # V3.8: 默认关闭
            detect_burst=self.ui_settings[7] if len(self.ui_settings) > 7 else True,  # V4.0: 默认开启
            # BirdID 设置
            auto_identify=birdid_auto_identify,
            birdid_use_geo_filter=birdid_use_geo_filter,
            birdid_country_code=birdid_country_code or "",
            birdid_region_code=birdid_region_code or "",
            birdid_confidence_threshold=float(birdid_confidence_threshold),  # V4.2
        )

        # ── 写完整会话头（含所有设置）到日志文件 ────────────────
        from datetime import datetime as _dt
        try:
            _adv = get_advanced_config()
            _on_off = lambda b: "On" if b else "Off"
            _session_header = "\n".join([
                "",
                "=" * 60,
                f"  [Session Start]  {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"  Directory : {self.dir_path}",
                "=" * 60,
                "[UI Settings]",
                f"  AI Confidence      : {settings.ai_confidence}%",
                f"  Sharpness          : {settings.sharpness_threshold}",
                f"  Aesthetics (TOPIQ) : {settings.nima_threshold}",
                f"  Normalization      : {settings.normalization_mode}",
                f"  Flight Detection   : {_on_off(settings.detect_flight)}",
                f"  Exposure Detection : {_on_off(settings.detect_exposure)}",
                f"  Burst Detection    : {_on_off(settings.detect_burst)}",
                f"  BirdID Auto ID     : {_on_off(settings.auto_identify)}",
                f"  BirdID Country     : {settings.birdid_country_code or 'Auto(GPS)'}",
                f"  BirdID Region      : {settings.birdid_region_code or 'All'}",
                f"  BirdID Confidence  : {settings.birdid_confidence_threshold}%",
                "[Advanced Config]",
                f"  Min Confidence     : {_adv.min_confidence}",
                f"  Min Sharpness      : {_adv.min_sharpness}",
                f"  Min Aesthetics     : {_adv.min_nima}",
                f"  Picked Top %       : {_adv.picked_top_percentage}%",
                f"  Exposure Threshold : {_adv.exposure_threshold}",
                f"  Burst FPS          : {_adv.burst_fps}",
                f"  Burst Min Count    : {_adv.burst_min_count}",
                f"  BirdID Confidence  : {_adv.birdid_confidence}%",
                f"  ARW Write Mode     : {_adv.arw_write_mode}",
                f"  Metadata Mode      : {_adv.get_metadata_write_mode()}",
                f"  Skill Level        : {_adv.skill_level}",
                f"  Language           : {_adv.language or 'Auto'}",
            ])
            try:
                from tools.system_logger import collect_system_info as _collect_sys
                _si = _collect_sys()
                _sys_lines = [
                    "[System]",
                    f"  App Version        : {_si.get('app_version', '?')}",
                    f"  Launch Mode        : {_si.get('launch_mode', '?')}",
                    f"  OS                 : {_si.get('os', '?')} {_si.get('os_release', '')}",
                ]
                if 'macos_version' in _si:
                    _sys_lines.append(f"  macOS              : {_si['macos_version']}")
                _sys_lines += [
                    f"  Machine            : {_si.get('machine', '?')}",
                    f"  Python             : {_si.get('python_version', '?')}",
                    f"  RAM Total          : {_si.get('ram_total_gb', '?')} GB",
                    f"  RAM Free           : {_si.get('ram_available_gb', '?')} GB",
                    f"  AI Device          : {_si.get('ai_device', '?')}",
                ]
                if 'gpu_name' in _si:
                    _sys_lines.append(f"  GPU                : {_si['gpu_name']}")
                if 'gpu_vram_gb' in _si:
                    _sys_lines.append(f"  VRAM               : {_si['gpu_vram_gb']} GB")
                if 'cuda_version' in _si:
                    _sys_lines.append(f"  CUDA               : {_si['cuda_version']}")
                _session_header = _session_header + "\n" + "\n".join(_sys_lines)
            except Exception:
                pass
            _session_header = _session_header + "\n" + "=" * 60
        except Exception as _hdr_err:
            # 会话头生成失败时写一个最简版本，不阻断处理流程
            _session_header = "\n".join([
                "",
                "=" * 60,
                f"  [Session Start]  {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"  Directory : {self.dir_path}",
                f"  [Header error: {_hdr_err}]",
                "=" * 60,
            ])
        _log_to_file(_session_header, self.dir_path, file_only=True)
        # ────────────────────────────────────────────────────────

        def log_callback(msg, level="info"):
            self.signals.log.emit(msg, level)
            # 同步写入日志文件（与 CLI 模式保持一致）
            _log_to_file(msg, self.dir_path, file_only=True)

        def progress_callback(value):
            self.signals.progress.emit(int(value))

        # V4.2: 裁剪预览回调
        def crop_preview_callback(debug_img, focus_status=None, rating=None):
            self.signals.crop_preview.emit(debug_img, focus_status, rating)

        callbacks = ProcessingCallbacks(
            log=log_callback,
            progress=progress_callback,
            should_stop=self._stop_event.is_set,
            crop_preview=crop_preview_callback
        )

        # Detect batch mode: check for subdirectories with photos
        from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH, scan_directories

        scan_results = self.scan_results
        if scan_results is None:
            scan_results = scan_directories(self.dir_path, max_depth=DEFAULT_SCAN_MAX_DEPTH)

        sub_dirs = [item.path for item in scan_results]

        if len(scan_results) <= 1:
            # Single directory mode (original behavior)
            # 若扫描到的实际目录与根目录不同（根目录无图片、子目录有图片），使用实际目录
            single_dir = scan_results[0].path if scan_results else self.dir_path
            processor = PhotoProcessor(
                dir_path=single_dir,
                settings=settings,
                callbacks=callbacks
            )
            self._active_processor = processor

            from advanced_config import get_advanced_config
            adv_config = get_advanced_config()

            # V4.6(Paul P1): 平铺布局 → 识别评分但不移动文件(Lightroom 友好)
            # V4.6 (Paul P1): flat layout — rate in place, no file moves.
            from core.folder_layout import LAYOUT_FLAT
            _organize_enabled = adv_config.folder_layout != LAYOUT_FLAT

            try:
                result = processor.process(
                    organize_files=_organize_enabled,
                    cleanup_temp=not adv_config.keep_temp_files,
                    resume=self.resume
                )

                burst_groups = result.stats.get('burst_groups', 0)
                burst_moved = result.stats.get('burst_moved', 0)

                if burst_groups > 0:
                    log_callback(self.i18n.t("logs.burst_complete", groups=burst_groups, moved=burst_moved), "success")
                elif settings.detect_burst:
                    log_callback(self.i18n.t("logs.burst_none_detected"), "info")

                self.stats = result.stats
                # 用 worker 级 wall time 覆盖 processor 内部口径（含预扫描/
                # 会话头等前置开销），与批量模式口径一致。
                # Override the processor-internal timing with the worker-level
                # wall time (includes pre-scan/session-header overhead),
                # matching batch-mode semantics.
                self._finalize_photo_stage_timing(
                    self.stats, _photo_stage_wall_start, _time.time()
                )
            finally:
                self._active_processor = None
        else:
            # Batch mode: process each subdirectory
            from advanced_config import get_advanced_config
            adv_config = get_advanced_config()

            log_callback(f"\n{'='*56}", "info")
            log_callback(f"  \U0001f4c2 Batch mode: {len(scan_results)} directories detected", "info")
            log_callback(f"{'='*56}", "info")

            # Count total photos across all dirs for progress
            total_all = sum(item.photo_count for item in scan_results)

            processed_so_far = 0
            aggregated = {
                'total': 0, 'star_3': 0, 'picked': 0, 'star_2': 0,
                'star_1': 0, 'star_0': 0, 'no_bird': 0,
                'start_time': 0, 'end_time': 0, 'total_time': 0,
                'flying': 0, 'focus_precise': 0, 'exposure_issue': 0,
                'burst_groups': 0, 'burst_moved': 0,
                'bird_species': [],
            }
            # 起点用 process_files() 入口的 wall time，与单目录口径一致
            # Start from the process_files() entry wall time, matching
            # single-directory semantics.
            aggregated['start_time'] = _photo_stage_wall_start

            for idx, scanned_dir in enumerate(scan_results, 1):
                sub_dir = scanned_dir.path
                rel = os.path.relpath(sub_dir, self.dir_path)
                n_photos = scanned_dir.photo_count
                if n_photos == 0:
                    continue

                log_callback(f"\n{'_'*40}", "info")
                log_callback(f"\U0001f4c1 [{idx}/{len(sub_dirs)}] {rel}/ ({n_photos} photos)", "info")
                log_callback(f"{'_'*40}", "info")

                # Wrap progress to map sub-dir progress to global progress
                dir_base = processed_so_far
                dir_count = n_photos

                def make_progress_cb(base, count):
                    def _progress(val):
                        if total_all > 0:
                            global_pct = (base + count * val / 100.0) / total_all * 100
                            self.signals.progress.emit(int(global_pct))
                    return _progress

                sub_callbacks = ProcessingCallbacks(
                    log=log_callback,
                    progress=make_progress_cb(dir_base, dir_count),
                    should_stop=self._stop_event.is_set,
                    crop_preview=crop_preview_callback
                )

                processor = PhotoProcessor(
                    dir_path=sub_dir,
                    settings=settings,
                    callbacks=sub_callbacks
                )
                self._active_processor = processor

                # V4.6(Paul P1): 平铺布局 → 识别评分但不移动文件
                # V4.6 (Paul P1): flat layout — rate in place, no file moves.
                from core.folder_layout import LAYOUT_FLAT
                _organize_enabled = adv_config.folder_layout != LAYOUT_FLAT

                try:
                    result = processor.process(
                        organize_files=_organize_enabled,
                        cleanup_temp=not adv_config.keep_temp_files,
                        resume=self.resume
                    )
                    s = result.stats
                    for key in ('total', 'star_3', 'picked', 'star_2', 'star_1',
                                'star_0', 'no_bird', 'flying', 'focus_precise',
                                'exposure_issue', 'burst_groups', 'burst_moved'):
                        aggregated[key] = aggregated.get(key, 0) + s.get(key, 0)
                    aggregated['bird_species'].extend(s.get('bird_species', []))

                    r3 = s.get('star_3', 0)
                    r2 = s.get('star_2', 0)
                    r1 = s.get('star_1', 0)
                    r0 = s.get('star_0', 0)
                    nb = s.get('no_bird', 0)
                    tt = s.get('total_time', 0)
                    log_callback(
                        f"  \u2705 Done ({tt:.1f}s): "
                        f"3\u2605={r3} 2\u2605={r2} 1\u2605={r1} 0\u2605={r0} no_bird={nb}",
                        "success"
                    )
                except Exception as e:
                    log_callback(f"  \u274c Error: {e}", "error")
                finally:
                    self._active_processor = None

                processed_so_far += dir_count

            # 与单目录路径共用同一个收口函数，保证两种模式口径一致
            # Shared finalizer keeps both modes' timing semantics identical.
            self._finalize_photo_stage_timing(
                aggregated, _photo_stage_wall_start, _time.time()
            )

            # Deduplicate bird species
            seen = set()
            unique_species = []
            for sp in aggregated['bird_species']:
                key = str(sp)
                if key not in seen:
                    seen.add(key)
                    unique_species.append(sp)
            aggregated['bird_species'] = unique_species

            log_callback(f"\n{'='*56}", "info")
            log_callback(
                f"  \U0001f4ca Batch complete: {len(sub_dirs)} dirs, "
                f"{aggregated['total']} photos, {aggregated['total_time']:.1f}s",
                "success"
            )
            log_callback(f"{'='*56}", "info")

            self.stats = aggregated

        # ── 写会话结束摘要到日志文件 ──────────────────────────
        _s = self.stats
        _total    = _s.get('total', 0)
        _star_3   = _s.get('star_3', 0)
        _star_2   = _s.get('star_2', 0)
        _star_1   = _s.get('star_1', 0)
        _star_0   = _s.get('star_0', 0)
        _no_bird  = _s.get('no_bird', 0)
        _picked   = _s.get('picked', 0)
        _flying   = _s.get('flying', 0)
        _focus_p  = _s.get('focus_precise', 0)
        _exp_issue = _s.get('exposure_issue', 0)
        _burst_g  = _s.get('burst_groups', 0)
        _burst_m  = _s.get('burst_moved', 0)
        _t_time   = _s.get('total_time', 0)
        _avg_time = _s.get('avg_time', 0)

        # 格式化识别鸟种列表（双语）
        _species_raw = _s.get('bird_species', [])
        if _species_raw:
            _sp_parts = []
            for _sp in _species_raw:
                if isinstance(_sp, dict):
                    _cn = _sp.get('cn_name', '')
                    _en = _sp.get('en_name', '')
                    _sp_parts.append(f"{_cn}/{_en}" if _cn and _en else _cn or _en)
                else:
                    _sp_parts.append(str(_sp))
            _species_str = ', '.join(_sp_parts)
        else:
            _species_str = 'None'

        def _pct(n):
            return f" ({n / _total * 100:.1f}%)" if _total > 0 else ""

        _end_lines = [
            "",
            "=" * 60,
            f"  [Session End]  {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "[Selection Results]",
            f"  Total Photos       : {_total}",
            f"  ⭐⭐⭐ 3-Star       : {_star_3}{_pct(_star_3)}",
            f"    └─ 🏆 Picked     : {_picked}" + (
                f" ({_picked / _star_3 * 100:.0f}% of 3★)" if _star_3 > 0 else ""
            ),
            f"  ⭐⭐   2-Star       : {_star_2}{_pct(_star_2)}",
            f"  ⭐     1-Star       : {_star_1}{_pct(_star_1)}",
            f"  0⭐    0-Star       : {_star_0}{_pct(_star_0)}",
            f"  ❌    No Bird       : {_no_bird}{_pct(_no_bird)}",
            "",
            "[Flags]",
            f"  🦅 Flying          : {_flying}",
            f"  🎯 Precise Focus   : {_focus_p}",
            f"  💡 Exposure Issue  : {_exp_issue}",
            f"  📦 Burst Groups    : {_burst_g}  (moved {_burst_m})",
            "",
            "[BirdID Identified]",
            f"  {_species_str}",
            "",
            "[Performance]",
            f"  Started at         : {_format_wall_clock(_s.get('start_time', 0))}",
            f"  Finished at        : {_format_wall_clock(_s.get('end_time', 0))}",
            f"  Total Time         : {_t_time:.1f}s  ({_t_time / 60:.1f} min)",
            f"  Avg per Photo      : {_avg_time:.1f}s",
            "=" * 60,
        ]
        _log_to_file("\n".join(_end_lines), self.dir_path, file_only=True)
        # ────────────────────────────────────────────────────────


class SuperPickyMainWindow(QMainWindow):
    """SuperPicky 主窗口 - 极简艺术风格"""

    # V3.6: 重置操作的信号
    reset_log_signal = Signal(str)
    reset_complete_signal = Signal(bool, dict, dict)
    
    # V4.2.1: 日志信号，确保线程安全
    log_signal = Signal(str, str)
    reset_error_signal = Signal(str)

    def __init__(self):
        super().__init__()

        # 记录启动时系统信息（后台线程，不阻塞 UI）
        import threading
        threading.Thread(
            target=self._write_startup_log,
            daemon=True
        ).start()

        # 初始化配置和国际化
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)
        set_primary_language(self.config.language)  # 让所有 get_i18n() 无参调用返回同一语言

        # 状态变量
        self.directory_path = ""
        self.worker = None
        self.worker_signals = None
        self.current_progress = 0
        self.total_files = 0
        self._main_window_placement_saved = False

        # 设置窗口
        self._setup_window()
        self._setup_menu()
        self._setup_ui()
        self._setup_birdid_dock()  # V4.0: 识鸟停靠面板
        self._show_initial_help()
        self._init_manager = InitializationManager(self)

        # 连接重置信号
        # 连接重置信号
        self.reset_log_signal.connect(self._log)
        # 修复Crash: 确保日志信号连接到主线程槽
        # noinspection PyUnresolvedReferences
        self.log_signal.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.reset_complete_signal.connect(self._on_reset_complete)
        self.reset_error_signal.connect(self._on_reset_error)
        
        # V4.2: 更新检测信号
        self._update_signals = WorkerSignals()
        self._update_signals.update_check_done.connect(self._show_update_result_dialog)

        # V4.0: 自动启动识鸟 API 服务器
        self._birdid_server_process = None
        QTimer.singleShot(1000, self._auto_start_birdid_server)

        # ExtremeSimple: 在线更新检测已从入口彻底剥离（tools/update_checker.py 保留不动，
        # 未来若要恢复只需把这段启动触发和菜单项接回去）。
        # ExtremeSimple: online update checking stripped from all entry points
        # (tools/update_checker.py kept intact; re-wire this startup trigger +
        # the menu action to bring it back).

        # V4.2: 启动时预加载所有模型（延迟3秒，后台加载不阻塞UI）
        QTimer.singleShot(3000, self._preload_all_models)
        
        # V4.0: 设置系统托盘图标（关闭窗口时最小化到托盘）
        self._setup_system_tray()
        self._really_quit = False  # 标记是否真正退出
        self._tray_hint_shown = False  # 首次隐藏到托盘时提示一次（每次会话）
        self._suppress_results_browser_once = False
        self._resume_prompt_handled = False
        self._initialization_dialog_open = False
        self._initialization_prompt_dismissed = False
        
        # osk flex,countly.com 63fda2e
        self._startup_prompts_ran = False
        self._preload_done = False  # 模型预加载是否完成
        
        # V4.2: 使用默认窗口大小，不最大化
        # self.showMaximized()  # 注释掉这行，使用默认大小
        
        # 首次启动欢迎向导由 run_startup_prompts 统一调度，避免重复弹窗。
        # NOTE: onboarding 只替代“首次启动设置流程”，不替代后续手动设置入口。
        # 因此这里仅在非首次运行时预先应用已保存的等级阈值，不在 __init__ 里直接弹窗。
        if not self.config.is_first_run:
            # 非首次运行：根据保存的水平设置滑块
            self._apply_skill_level_thresholds(self.config.skill_level)



    @staticmethod
    def _write_startup_log():
        """后台记录一次系统信息到 SuperPicky 配置目录的 startup.log"""
        try:
            from tools.system_logger import write_startup_log
            log_path = write_startup_log()
            if log_path:
                print(f"[startup] System info written to: {log_path}")
        except Exception as e:
            print(f"[startup] Failed to write system info: {e}")

    def _get_app_icon(self):
        """获取应用图标"""
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return None

    def _show_message(self, title, message, msg_type="info"):
        """显示消息框"""
        if msg_type == "info":
            return StyledMessageBox.information(self, title, message)
        elif msg_type == "warning":
            return StyledMessageBox.warning(self, title, message)
        elif msg_type == "error":
            return StyledMessageBox.critical(self, title, message)
        elif msg_type == "question":
            return StyledMessageBox.question(self, title, message)
        else:
            return StyledMessageBox.information(self, title, message)

    def _setup_window(self):
        """设置窗口属性"""
        self.setWindowTitle(self.i18n.t("app.window_title"))
        self.setMinimumSize(800, 720)
        if not self._restore_main_window_geometry():
            self.resize(960, 820)
        if self.config.main_window_maximized:
            QTimer.singleShot(0, self.showMaximized)

        # 应用全局样式表
        self.setStyleSheet(GLOBAL_STYLE)

        # 设置图标
        icon_path = get_resource_path("img/icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _restore_main_window_geometry(self) -> bool:
        """
        恢复主窗口普通状态下的几何信息。

        返回:
        bool: 成功恢复返回 True，否则回退默认窗口大小。

        Restore the main window normal-state geometry.

        Return:
        bool: True when restored; False when the default size should be used.
        """
        saved = self.config.get_main_window_geometry()
        if not saved:
            return False

        rect = QRect(saved["x"], saved["y"], saved["width"], saved["height"])
        if not self._is_valid_main_window_rect(rect):
            return False

        self.setGeometry(rect)
        return True

    def _is_valid_main_window_rect(self, rect: QRect) -> bool:
        """
        校验保存的窗口矩形是否仍适合当前屏幕环境。

        参数:
        rect (QRect): 待校验的窗口矩形。

        返回:
        bool: 矩形尺寸达标且至少与一个可用屏幕相交时返回 True。

        Validate whether a saved window rectangle still fits the current screens.

        Parameters:
        rect (QRect): Window rectangle to validate.

        Return:
        bool: True when size is acceptable and it intersects an available screen.
        """
        if not rect.isValid() or rect.width() < 800 or rect.height() < 720:
            return False

        screens = QApplication.screens()
        if not screens:
            return True

        return any(screen.availableGeometry().intersects(rect) for screen in screens)

    def _save_main_window_placement(self) -> None:
        """
        保存主窗口普通几何和最大化状态。

        最大化状态与普通几何分开保存；这样下次以最大化打开后，取消最大化仍能回到
        用户上次的普通窗口大小。最小化状态不保存，避免应用下次启动时不可见。

        Save the main window normal geometry and maximized state.

        Maximized state is stored separately from normal geometry so restoring from
        maximized returns to the user's previous normal size. Minimized state is
        intentionally ignored so the app never reopens invisible.
        """
        if self._main_window_placement_saved:
            return

        if self.isMaximized() or self.isMinimized():
            rect = self.normalGeometry()
        else:
            rect = self.geometry()

        if self._is_valid_main_window_rect(rect):
            self.config.set_main_window_geometry(
                {
                    "x": rect.x(),
                    "y": rect.y(),
                    "width": rect.width(),
                    "height": rect.height(),
                }
            )
        self.config.set_main_window_maximized(self.isMaximized())
        if self.config.save():
            self._main_window_placement_saved = True

    def _setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()

        # 识鸟菜单
        birdid_menu = menubar.addMenu(self.i18n.t("menu.birdid"))

        # 识鸟面板（可勾选显示/隐藏）
        self.birdid_dock_action = QAction(self.i18n.t("menu.toggle_dock"), self)
        self.birdid_dock_action.setCheckable(True)
        self.birdid_dock_action.setChecked(True)
        self.birdid_dock_action.triggered.connect(self._toggle_birdid_dock)
        birdid_menu.addAction(self.birdid_dock_action)

        # ExtremeSimple: 「视频分析」菜单已从菜单栏剥离（_open_video_analyzer 保留在
        # 下方，ui/video_analyzer_window.py 等文件原封不动；未来要恢复只需把这段
        # 菜单创建代码加回来）。_video_analyzer_window 属性仍初始化，避免
        # _cleanup_on_quit 的 hasattr 检查失效。
        # ExtremeSimple: the "Video Analysis" menu is stripped from the menu bar
        # (_open_video_analyzer stays below; ui/video_analyzer_window.py etc. are
        # untouched). Re-add this menu-creation block to bring it back.
        # _video_analyzer_window still initialized so _cleanup_on_quit's hasattr
        # check keeps working.
        self._video_analyzer_window = None  # 懒加载 / lazy-loaded singleton

        # ── 最近目录子菜单 ──────────────────────────────────
        self._recent_menu = menubar.addMenu(self.i18n.t("menu.recent_dirs"))
        self._refresh_recent_menu()

        # 设置菜单 — Task 7: 合并为单一「设置」入口，所有配置页在设置中心内完成
        # Settings menu — Task 7: collapsed into a single "Settings" entry; all config inside SettingsCenter
        settings_menu = menubar.addMenu(self.i18n.t("menu.settings_menu"))

        # 单一设置入口 → 打开设置中心 / Single settings entry → open SettingsCenter
        settings_action = QAction(self.i18n.t("menu.settings"), self)
        # NoRole：英文文案 "Preferences..." 会命中 macOS 的菜单文字启发式
        # (PreferencesRole)，被 Qt 自动挪到应用菜单，从「设置」菜单里消失；
        # 中文「参数设置...」不命中、行为不同。显式 NoRole 保证所有平台、
        # 所有语言下入口都固定在这里。
        # NoRole: the English label "Preferences..." matches macOS's text
        # heuristic (PreferencesRole) and Qt silently relocates the item to
        # the application menu — it vanishes from this Settings menu, while
        # the Chinese label stays put. Explicit NoRole pins it here on every
        # platform and language.
        settings_action.setMenuRole(QAction.MenuRole.NoRole)
        # Ctrl+, 在 macOS 显示为 ⌘,（打开设置的标准快捷键），Windows 为 Ctrl+,
        # Ctrl+, renders as Cmd+, on macOS (the standard open-settings key).
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(lambda: self._open_settings_center("culling"))
        settings_menu.addAction(settings_action)

        repair_action = QAction(self.i18n.t("menu.environment_repair"), self)
        repair_action.triggered.connect(self._show_environment_repair_dialog)
        settings_menu.addAction(repair_action)

        settings_menu.addSeparator()

        # 界面语言子菜单
        lang_menu = settings_menu.addMenu(self.i18n.t("menu.language"))

        # 简体中文
        zh_action = QAction(self.i18n.t("menu.lang_zh"), self)
        zh_action.setCheckable(True)
        zh_action.setChecked(self.config.language == "zh_CN")
        zh_action.triggered.connect(lambda: self._change_language("zh_CN"))
        lang_menu.addAction(zh_action)

        # English
        en_action = QAction(self.i18n.t("menu.lang_en"), self)
        en_action.setCheckable(True)
        en_action.setChecked(self.config.language == "en")
        en_action.triggered.connect(lambda: self._change_language("en"))
        lang_menu.addAction(en_action)

        self.lang_actions = {"zh_CN": zh_action, "en": en_action}

        # 帮助菜单 — 关于已移入设置中心 / Help menu — About is now inside SettingsCenter
        help_menu = menubar.addMenu(self.i18n.t("menu.help"))

        # 关于入口保留：用 _open_settings_center("about") 打开设置中心内的关于页
        # About entry retained: opens the About page inside SettingsCenter
        about_action = QAction(self.i18n.t("menu.about"), self)
        about_action.triggered.connect(lambda: self._open_settings_center("about"))
        help_menu.addAction(about_action)

    def _refresh_recent_menu(self):
        """重建「最近目录」子菜单内容（每次选目录后调用）。"""
        if not hasattr(self, '_recent_menu'):
            return
        self._recent_menu.clear()
        dirs = self.config.get_recent_directories()
        offline_prefix = self.i18n.t("menu.recent_dirs_offline")  # "(脱机)" or "(Offline)"
        if dirs:
            for d in dirs:
                available = os.path.isdir(d)
                label = d if available else f"{offline_prefix} {d}"
                action = QAction(label, self)
                if available:
                    action.triggered.connect(lambda checked=False, path=d: self._handle_directory_selection(path))
                else:
                    action.triggered.connect(
                        lambda checked=False, msg=self.i18n.t("messages.dir_unavailable"):
                        self._show_message(self.i18n.t("messages.warning"), msg, "warning")
                    )
                self._recent_menu.addAction(action)
            self._recent_menu.addSeparator()
        # 清除历史按钮
        clear_action = QAction(self.i18n.t("menu.recent_dirs_clear"), self)
        clear_action.triggered.connect(self._clear_recent_directories)
        self._recent_menu.addAction(clear_action)

    def _clear_recent_directories(self):
        """清空最近目录历史。"""
        self.config.config["recent_directories"] = []
        self.config.save()
        self._refresh_recent_menu()

    def _setup_ui(self):
        """设置主 UI"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(0)

        # 头部区域
        self._create_header_section(main_layout)
        main_layout.addSpacing(24)

        # 目录选择
        self._create_directory_section(main_layout)
        main_layout.addSpacing(20)

        # 首页「快速调整」参数面板:2 滑块(锐度/美学)+ 3 开关(飞行/连拍/识鸟)。
        # 这些控件是 advanced_config(单一事实源)的快捷编辑器,与设置中心双向同步。
        # Home quick-adjust panel: 2 sliders + 3 toggles, two-way bound to advanced_config (SSOT).
        self._create_parameters_section(main_layout)
        main_layout.addSpacing(20)

        # 日志区域
        self._create_log_section(main_layout)
        main_layout.addSpacing(16)

        # 进度区域
        self._create_progress_section(main_layout)
        main_layout.addSpacing(4)

        # 状态条（进度条下方、按钮上方）
        self._create_status_banner(main_layout)
        main_layout.addSpacing(6)

        # 控制按钮
        self._create_button_section(main_layout)

    def _setup_birdid_dock(self):
        """
        设置识鸟停靠面板，并连接 open_settings_requested signal。
        Set up the BirdID dock panel and connect the open_settings_requested signal.
        """
        from .birdid_dock import BirdIDDockWidget

        self.birdid_dock = BirdIDDockWidget(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.birdid_dock)

        # 设置 dock 初始宽度为最小值，让主区域更宽
        # Set initial dock width to minimum so the main area is wider
        self.birdid_dock.setFixedWidth(280)
        # 延迟解除固定宽度限制，让用户可以调整
        # Defer removal of fixed-width so the user can later resize
        QTimer.singleShot(100, lambda: self.birdid_dock.setFixedWidth(16777215))  # QWIDGETSIZE_MAX

        # Task 8: "设置"链接 → 打开设置中心 birdid 页
        # Task 8: "Settings" link → open Settings Center on the birdid page
        self.birdid_dock.open_settings_requested.connect(self._open_settings_center)

        # 更新菜单动作的状态
        # Update menu action state when dock visibility changes
        self.birdid_dock.visibilityChanged.connect(self._on_birdid_dock_visibility_changed)

    def _on_birdid_dock_visibility_changed(self, visible):
        """识鸟面板可见性变化"""
        if hasattr(self, 'birdid_dock_action'):
            self.birdid_dock_action.setChecked(visible)
            # 这里的文字其实不用动态改变，保持 "打开/关闭" 即可，或者更复杂点
            # 暂时保持简单
            pass # self.birdid_dock_action.setText("关闭识鸟面板" if visible else "打开识鸟面板")
    
    def _setup_system_tray(self):
        """V4.0: 设置系统托盘图标"""
        # 检查系统是否支持托盘图标
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("⚠️ 系统不支持托盘图标")
            return
        
        # 创建托盘图标
        self.tray_icon = QSystemTrayIcon(self)
        
        # 设置图标（使用裁剪后的托盘专用图标）
        icon_path = get_resource_path("img/icon_tray.png")
        if not os.path.exists(icon_path):
            # 回退到原始图标
            icon_path = get_resource_path("img/icon.png")
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # 使用窗口图标作为备选
            self.tray_icon.setIcon(self.windowIcon())
        
        # 创建托盘菜单
        tray_menu = QMenu()
        
        # 显示/隐藏主窗口
        show_action = QAction(self.i18n.t("server.tray_show_window"), self)
        show_action.triggered.connect(self._show_main_window)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        # 服务器状态（只读显示）
        self.tray_server_status = QAction(self.i18n.t("server.tray_server_running"), self)
        self.tray_server_status.setEnabled(False)
        tray_menu.addAction(self.tray_server_status)
        
        tray_menu.addSeparator()
        
        # 完全退出
        quit_action = QAction(self.i18n.t("server.tray_quit"), self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # 点击托盘图标显示窗口
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        # 设置提示文字
        self.tray_icon.setToolTip(self.i18n.t("server.tray_tooltip"))
        
        # 显示托盘图标
        self.tray_icon.show()
        
        print(self.i18n.t("server.tray_icon_enabled"))
    
    def _on_tray_activated(self, reason):
        """托盘图标被点击"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # 单击：显示/隐藏窗口
            self._show_main_window()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # 双击：显示窗口
            self._show_main_window()
    
    def _show_main_window(self):
        """显示主窗口"""
        # 窗口重新可见后复位保存标志：隐藏到托盘时已保存过一次位置，若不复位，
        # 用户重新打开窗口后再移动/调整大小，退出时会因标志已置位而不再保存，
        # 新位置静默丢失（PR#105 原实现的缺陷，托盘驻留是 nightly 的默认关窗
        # 行为，此路径是主路径而非边缘场景）。
        # Reset the placement-saved flag once the window is visible again:
        # hiding to tray already saved once, and without this reset any
        # move/resize after reopening would be silently lost at quit (a defect
        # in the original PR#105; with tray residency as nightly's default
        # close behavior this is the main path, not an edge case).
        self._main_window_placement_saved = False
        # macOS: 恢复 Dock 图标
        if sys.platform == 'darwin':
            try:
                import importlib
                appkit = importlib.import_module("AppKit")
                appkit.NSApp.setActivationPolicy_(appkit.NSApplicationActivationPolicyRegular)
                print("✅ 已恢复 Dock 图标")
            except Exception:
                pass
        
        self.show()
        self.raise_()
        self.activateWindow()
        # 确保窗口获得焦点
        self.setWindowState(
            self.windowState()
            & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
    
    def _tray_resident_available(self) -> bool:
        """
        判断是否具备"隐藏到托盘常驻"的条件（托盘图标已创建且系统支持）。

        Return whether hide-to-tray residency is available (tray icon created
        and the system supports a tray).

        返回 / Return:
        bool: True 表示关窗可以安全地转为托盘驻留 / closing can hide to tray.
        """
        return (
            hasattr(self, 'tray_icon')
            and self.tray_icon is not None
            and QSystemTrayIcon.isSystemTrayAvailable()
        )

    def _hide_to_tray(self):
        """
        隐藏主窗口到托盘，进程与识鸟 API 服务器继续驻留运行。

        打包版的 API 服务器是主进程内的线程，只有进程活着 Lightroom 插件
        才能连上 5156 端口——这是"真驻留"的关键（旧的"后台模式"退出进程，
        服务器随之死亡，插件就连不上了）。

        Hide the main window to the tray; the process and the BirdID API
        server stay resident. In packaged builds the API server is a thread
        inside this process, so the Lightroom plugin can only reach port 5156
        while the process lives — the old "background mode" quit the process
        and silently killed the server.
        """
        self.hide()

        # macOS: 没有其他可见窗口时隐藏 Dock 图标（Accessory 模式），
        # _show_main_window 会恢复 Regular。托盘（菜单栏）图标始终保留。
        # macOS: hide the Dock icon (Accessory) when no other window is
        # visible; _show_main_window restores Regular. The tray icon remains.
        if sys.platform == 'darwin':
            try:
                others_visible = any(
                    w.isVisible() and w.isWindow()
                    for w in QApplication.topLevelWidgets()
                    if w is not self
                )
                if not others_visible:
                    import importlib
                    appkit = importlib.import_module("AppKit")
                    appkit.NSApp.setActivationPolicy_(
                        appkit.NSApplicationActivationPolicyAccessory
                    )
            except Exception:
                pass

        # 每次会话首次隐藏时用托盘气泡提示驻留状态与退出方式
        # Show a one-per-session tray balloon explaining residency and how to quit
        if not self._tray_hint_shown and self._tray_resident_available():
            self._tray_hint_shown = True
            try:
                self.tray_icon.showMessage(
                    self.i18n.t("server.tray_resident_title"),
                    self.i18n.t("server.tray_resident_msg"),
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            except Exception:
                pass

    def _quit_app(self):
        """完全退出应用（清理由 aboutToQuit 信号统一处理）"""
        # 任务进行中时先确认，避免托盘"完全退出"误杀处理任务
        # Confirm first if a task is running, so tray "Quit" cannot kill it by accident
        if self.worker and self.worker.is_alive():
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.exit_title"),
                self.i18n.t("messages.exit_confirm"),
                yes_text=self.i18n.t("buttons.cancel"),
                no_text=self.i18n.t("labels.yes")
            )
            if reply != StyledMessageBox.No:  # 未确认退出 / quit not confirmed
                return
            self.worker.request_stop()
            self.worker._stop_caffeinate()  # V3.8.1: 确保终止 caffeinate 进程
        self._force_quit()

    def _force_quit(self):
        """不再确认，直接退出（清理由 aboutToQuit 信号统一处理）"""
        self._save_main_window_placement()
        self._really_quit = True
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()         # 先隐藏托盘，避免用户二次点击
        QApplication.quit()               # 触发 aboutToQuit → _cleanup_on_quit

    def _cleanup_on_quit(self):
        """统一退出清理（由 app.aboutToQuit 信号调用）
        无论通过 X按鈕 / Cmd+Q / 托盘退出，都会经过此处。
        Mac 和 Windows 均适用。
        """
        self._save_main_window_placement()

        if self.worker and self.worker.is_alive():
            try:
                self.worker.request_stop()
                self.worker.join(timeout=5)
            except Exception:
                pass

        # V4.6: 无条件停止 caffeinate，不能依赖 worker.run() 的 finally。
        # worker 是 daemon 线程：上面的 join 一旦超时(处理大批 RAW 时很常见)，
        # 主进程继续退出会直接终止该线程，finally 不会执行，caffeinate 就会残留，
        # 用户的 Mac 从此不再自动休眠。进程句柄在主线程手上，terminate 不依赖
        # worker 线程是否存活；_stop_caffeinate() 自带 None 判断，可安全重复调用。
        # V4.6: stop caffeinate unconditionally — worker.run()'s finally is not a
        # reliable release point. worker is a daemon thread, so once the join above
        # times out (common with large RAW batches) the interpreter terminates it
        # without running finally, leaking caffeinate and leaving the user's Mac
        # unable to sleep. The handle lives on the main thread, so terminate does
        # not need the worker alive; _stop_caffeinate() is idempotent.
        if self.worker is not None:
            try:
                self.worker._stop_caffeinate()
            except Exception as e:
                print(f"⚠️  caffeinate cleanup failed: {e}")

        if hasattr(self, '_init_manager') and self._init_manager is not None:
            try:
                self._init_manager.cancel()
            except Exception:
                pass
        if hasattr(self, '_results_browser') and self._results_browser:
            try:
                self._results_browser.cleanup()
            except Exception as e:
                print(f"⚠️  Results browser cleanup failed: {e}")

        # 先于 Python 解释器析构清理 QThread 密集型组件，防止 SIGABRT 崩溃
        # Clean up QThread-heavy components before Python finalizer destructs them,
        # preventing SIGABRT (QThread destroyed while still running -> qFatal).
        if hasattr(self, 'birdid_dock') and self.birdid_dock is not None:
            try:
                self.birdid_dock.cleanup()
            except Exception as e:
                print(f"⚠️  BirdID dock cleanup failed: {e}")
        if hasattr(self, '_video_analyzer_window') and self._video_analyzer_window is not None:
            try:
                self._video_analyzer_window.cleanup()
            except Exception as e:
                print(f"⚠️  Video analyzer cleanup failed: {e}")

        self._stop_birdid_server()        # 停止 Flask/BirdID 进程

        
        # 清理 ExifTool 进程
        try:
            from tools.exiftool_manager import get_exiftool_manager
            exiftool_mgr = get_exiftool_manager()
            exiftool_mgr.shutdown()
        except Exception as e:
            print(f"⚠️  ExifTool cleanup failed: {e}")
            
        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide()         # 清托盘图标（备用，_quit_app 已调过一次也无害）

    def _create_header_section(self, parent_layout):
        """创建头部区域 - 品牌展示"""
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)

        # 左侧: 品牌
        brand_layout = QHBoxLayout()
        brand_layout.setSpacing(16)

        # 品牌图标
        icon_path = get_resource_path("img/icon.png")
        if os.path.exists(icon_path):
            icon_container = QFrame()
            icon_container.setFixedSize(48, 48)
            icon_container.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {COLORS['accent']}, stop:1 {COLORS['accent_deep']});
                    border-radius: 12px;
                }}
            """)
            icon_inner_layout = QHBoxLayout(icon_container)
            icon_inner_layout.setContentsMargins(2, 2, 2, 2)

            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(
                44,
                44,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label.setPixmap(pixmap)
            icon_inner_layout.addWidget(icon_label)
            brand_layout.addWidget(icon_container)

        # 品牌文字
        brand_text_layout = QVBoxLayout()
        brand_text_layout.setSpacing(2)

        title_label = QLabel(self.i18n.t("app.brand_name"))
        title_label.setStyleSheet(TITLE_STYLE)
        brand_text_layout.addWidget(title_label)

        subtitle_label = QLabel(self.i18n.t("labels.subtitle"))
        subtitle_label.setStyleSheet(SUBTITLE_STYLE)
        brand_text_layout.addWidget(subtitle_label)

        brand_layout.addLayout(brand_text_layout)
        header_layout.addLayout(brand_layout)

        header_layout.addStretch()

        # 右侧: 版本号 + commit hash
        # 右侧: 版本号 + commit hash
        from constants import APP_VERSION
        from core.build_info import COMMIT_HASH
        
        # COMMIT_HASH 为 None 时（本地开发环境），自动从 git 获取当前 hash
        commit_hash = COMMIT_HASH
        if not commit_hash:
            try:
                import subprocess
                subprocess_kwargs = {}
                if sys.platform == "win32":
                    subprocess_kwargs["creationflags"] = getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    )
                commit_hash = subprocess.check_output(
                    ['git', 'rev-parse', '--short', 'HEAD'],
                    stderr=subprocess.DEVNULL,
                    **subprocess_kwargs,
                ).strip().decode('utf-8')
            except Exception:
                commit_hash = 'dev'

        version_text = f"V{APP_VERSION}\n{commit_hash}"
        
        version_label = QLabel(version_text)
        version_label.setStyleSheet(VERSION_STYLE)
        version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        header_layout.addWidget(version_label)

        # Task 7: 技能水平 chip — 只读展示，点击后打开设置中心（culling 页）
        # Task 7: skill level chip — read-only display; click opens SettingsCenter (culling page)
        self.skill_level_label = QLabel("")
        self.skill_level_label.setStyleSheet(f"""
            color: {COLORS['accent']};
            font-size: 11px;
            padding: 2px 6px;
            background-color: {COLORS['accent']}15;
            border-radius: 4px;
        """)
        self.skill_level_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skill_level_label.mousePressEvent = lambda _ev: self._open_settings_center("culling")
        header_layout.addSpacing(8)
        header_layout.addWidget(self.skill_level_label)

        parent_layout.addWidget(header)

    def _create_directory_section(self, parent_layout):
        """创建目录选择区域"""
        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(8)

        # V3.9: 使用支持拖放的 DropLineEdit
        self.dir_input = DropLineEdit()
        self.dir_input.clear()  # 防止 macOS 窗口状态恢复保留残留内容导致启动时误触发验证
        self.dir_input.setPlaceholderText(self.i18n.t("labels.dir_placeholder"))
        self.dir_input.returnPressed.connect(self._on_path_entered)
        self.dir_input.editingFinished.connect(self._on_path_entered)  # V3.9: 失焦时也验证
        self.dir_input.pathDropped.connect(self._on_path_dropped)     # V3.9: 拖放目录
        dir_layout.addWidget(self.dir_input, 1)

        browse_btn = QPushButton("  " + self.i18n.t("labels.browse"))
        browse_btn.setIcon(load_tinted_icon("folder.svg", ICON_IDLE, 16))
        browse_btn.setIconSize(QSize(16, 16))
        browse_btn.setObjectName("browse")
        browse_btn.setMinimumWidth(100)
        browse_btn.clicked.connect(self._browse_directory)
        dir_layout.addWidget(browse_btn)

        parent_layout.addLayout(dir_layout)

    def _create_log_section(self, parent_layout):
        """创建日志区域"""
        # 日志头部
        log_header = QHBoxLayout()

        log_label = QLabel(self.i18n.t("labels.console").upper())
        log_label.setObjectName("sectionLabel")
        log_header.addWidget(log_label)

        log_header.addStretch()

        # 状态指示器
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['accent']};
                border-radius: 3px;
            }}
        """)
        status_layout.addWidget(self.status_dot)

        self.status_label = QLabel(self.i18n.t("labels.ready"))
        self.status_label.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 11px;")
        status_layout.addWidget(self.status_label)

        log_header.addLayout(status_layout)
        parent_layout.addLayout(log_header)
        parent_layout.addSpacing(8)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(220)
        parent_layout.addWidget(self.log_text, 1)

    def _create_progress_section(self, parent_layout):
        """创建进度区域"""
        # 进度条 - 直接添加到父布局
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        parent_layout.addWidget(self.progress_bar)

        parent_layout.addSpacing(2)

        # 进度信息
        progress_info_layout = QHBoxLayout()
        progress_info_layout.setContentsMargins(0, 0, 0, 0)

        self.progress_info_label = QLabel("")
        self.progress_info_label.setStyleSheet(PROGRESS_INFO_STYLE)
        progress_info_layout.addWidget(self.progress_info_label)

        progress_info_layout.addStretch()

        self.progress_percent_label = QLabel("")
        self.progress_percent_label.setStyleSheet(PROGRESS_PERCENT_STYLE)
        progress_info_layout.addWidget(self.progress_percent_label)

        parent_layout.addLayout(progress_info_layout)

    def _create_status_banner(self, parent_layout):
        """创建状态条（进度条下方，按钮上方）"""
        self._status_banner = QLabel(self.i18n.t("labels.support_format_hint"))
        self._status_banner.setFixedHeight(32)
        self._status_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_banner.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['bg_card']};
                color: {COLORS['text_tertiary']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 6px;
                font-size: 12px;
                padding: 0 12px;
            }}
        """)
        parent_layout.addWidget(self._status_banner)

    def _create_button_section(self, parent_layout):
        """创建按钮区域"""
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # 次要按钮区域（左侧）
        # 重置/重新处理按钮 (幽灵按钮)
        self.reset_btn = QPushButton(self.i18n.t("labels.reset_short"))
        self.reset_btn.setObjectName("tertiary")
        self.reset_btn.setMinimumWidth(100)
        self.reset_btn.setMinimumHeight(40)
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self._reset_directory)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()

        # 查看选鸟结果按钮（主按钮，默认隐藏）
        self.view_results_btn = QPushButton(self.i18n.t("labels.view_results_arrow") + "  ")
        self.view_results_btn.setIcon(load_tinted_icon("arrow-right.svg", ICON_IDLE, 16))
        self.view_results_btn.setIconSize(QSize(16, 16))
        self.view_results_btn.setLayoutDirection(Qt.RightToLeft)  # 箭头置于文字右侧
        self.view_results_btn.setMinimumWidth(160)
        self.view_results_btn.setMinimumHeight(40)
        self.view_results_btn.clicked.connect(self._open_results_smart)
        self.view_results_btn.setVisible(False)
        btn_layout.addWidget(self.view_results_btn)

        # 开始按钮 (主按钮)
        self.start_btn = QPushButton(self.i18n.t("labels.start_processing"))
        self.start_btn.setMinimumWidth(140)
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self._start_processing)
        btn_layout.addWidget(self.start_btn)

        parent_layout.addLayout(btn_layout)

    # ========== 槽函数 ==========

    @Slot()
    def _on_path_entered(self):
        """路径输入回车或失焦"""
        directory = self.dir_input.text().strip()
        if directory and os.path.isdir(directory):
            # V3.9: 防止重复处理（editingFinished 和 returnPressed 可能同时触发）
            normalized = os.path.normpath(directory)
            if normalized != os.path.normpath(self.directory_path or ""):
                self._handle_directory_selection(directory)
        elif directory:
            StyledMessageBox.critical(
                self,
                self.i18n.t("errors.error_title"),
                self.i18n.t("errors.dir_not_exist", directory=directory)
            )
            # 清空无效路径，防止下次启动时 macOS 状态恢复重复触发此错误
            self.dir_input.clear()

    @Slot()
    def _browse_directory(self):
        """浏览目录

        起始目录：优先当前输入框/已选目录（若仍存在），否则回退到系统「图片」目录。
        Start the dialog at the currently selected/entered directory if it still
        exists, otherwise fall back to the system Pictures location.
        """
        start_dir = (self.dir_input.text().strip() or self.directory_path or "")
        if not (start_dir and os.path.isdir(start_dir)):
            start_dir = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation
            ) or ""
        directory = QFileDialog.getExistingDirectory(
            self,
            self.i18n.t("labels.select_photo_dir"),
            start_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        if directory:
            self._handle_directory_selection(directory)
    
    @Slot(str)
    def _on_path_dropped(self, directory: str):
        """V3.9: 处理拖放的目录"""
        if directory and os.path.isdir(directory):
            self._handle_directory_selection(directory)

    def _handle_directory_selection(self, directory):
        """处理目录选择"""
        # V3.9: 归一化路径并防止重复
        directory = os.path.normpath(directory)
        if directory == os.path.normpath(self.directory_path or ""):
            return  # 同一个目录，跳过

        self.directory_path = directory
        self.dir_input.setText(directory)

        self._log(self.i18n.t("messages.dir_selected", directory=directory))
        self._check_directory_health(directory)

        # 写入最近目录历史并刷新菜单
        self.config.add_recent_directory(directory)
        self._refresh_recent_menu()

        # 状态条 + 按钮由 _check_report_csv 根据是否有历史数据决定
        # 重置弹窗移到「重新处理」按钮点击时再询问（_reset_directory 保留确认逻辑）
        self._resume_prompt_handled = False
        self._check_report_csv()
        self._maybe_prompt_resume_after_selection()

    def _check_directory_health(self, directory: str):
        """检查目标目录的磁盘空间和写权限，结果输出到 UI 日志。"""
        import shutil
        try:
            usage = shutil.disk_usage(directory)
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)

            # 写权限检查（跨平台：os.access + 实际写测试）
            can_write = os.access(directory, os.W_OK)
            if can_write:
                # 部分网络盘 os.access 返回 True 但实际不可写，做一次实写验证
                try:
                    test_path = os.path.join(directory, ".superpicky_write_test")
                    with open(test_path, "w") as _f:
                        _f.write("")
                    os.remove(test_path)
                except Exception:
                    can_write = False

            write_icon = "✅" if can_write else "❌"
            write_label = self.i18n.t("health.writable") if can_write else self.i18n.t("health.not_writable")

            if free_gb < 1.0:
                space_icon = "❌"
                level = "warning"
            elif free_gb < 5.0:
                space_icon = "⚠️"
                level = "warning"
            else:
                space_icon = "✅"
                level = "info"

            self._log(
                self.i18n.t(
                    "health.disk_status",
                    free=f"{free_gb:.1f}",
                    total=f"{total_gb:.0f}",
                    space_icon=space_icon,
                    write_icon=write_icon,
                    write_label=write_label,
                ),
                level,
            )
        except Exception as e:
            self._log(self.i18n.t("health.disk_check_failed", error=str(e)), "warning")

    # ========== 状态条 + 结果浏览器辅助 ==========

    def _maybe_prompt_resume_after_selection(self):
        if self._resume_prompt_handled or not self.directory_path:
            return
        self._resume_prompt_handled = True
        try:
            from tools.resume_state import ResumeStateManager
            resume_state = ResumeStateManager(self.directory_path)
            if not resume_state.exists():
                return
            resume_reply = StyledMessageBox.question(
                self,
                self.i18n.t("dialogs.unfinished_title"),
                self.i18n.t("dialogs.unfinished_body"),
                yes_text=self.i18n.t("dialogs.continue_btn"),
                no_text=self.i18n.t("dialogs.restart_btn")
            )
            if resume_reply == StyledMessageBox.Yes:
                self._start_processing()
            else:
                resume_state.clear()
                self._suppress_results_browser_once = True
                self._quick_restore_directory()
        except Exception as resume_err:
            self._log(f"⚠️ 恢复状态检查失败: {resume_err}", "warning")

    def _load_result_counts(self) -> dict:
        """从 report.db 读取评分统计，供状态条显示。"""
        from tools.report_db import ReportDB
        try:
            db = ReportDB(self.directory_path)
            stats = db.get_statistics()
            db.close()
            return stats
        except Exception:
            return {}

    def _open_results_smart(self):
        """用户主动点击「查看结果」按鈕时的路由：
        True  → 打开结果浏览器（有预览图）
        False → 打开 Finder 显示分目录结果（无预览图）
        """
        from advanced_config import get_advanced_config
        if get_advanced_config().keep_temp_files:
            self._auto_open_results()
        else:
            self._open_finder_results()

    def _auto_open_results(self):
        """打开/切换结果浏览器窗口，并隐藏主窗口。"""
        if not self.directory_path:
            return
        from ui.results_browser_window import ResultsBrowserWindow
        if not hasattr(self, '_results_browser') or self._results_browser is None:
            self._results_browser = ResultsBrowserWindow(parent=None)
            # 浏览器关闭时恢复主窗口（避免无可见窗口的"幽灵"状态）
            self._results_browser.closed.connect(self._show_main_window)
        self._results_browser.open_directory(self.directory_path)
        self._results_browser.show()
        self._results_browser.raise_()
        self._results_browser.activateWindow()
        # 浏览器打开后隐藏主窗口（托盘图标保持可用）
        self.hide()

    def _open_finder_results(self):
        """不保留预览图时，直接在 Finder 打开结果目录。"""
        if not self.directory_path:
            return
        import sys
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(['open', self.directory_path])
            elif sys.platform == 'win32':
                subprocess.Popen(['explorer', self.directory_path])
            else:
                subprocess.Popen(['xdg-open', self.directory_path])
        except Exception as e:
            self._log(f"  ⚠️ 打开目录失败: {e}", "warning")

    def _status_folder_icon_html(self, px: int = 14) -> str:
        """
        把 folder.svg 染成状态栏文字色,转成内联 base64 <img>,供富文本 QLabel
        复用「浏览」按钮同款图标(替代旧的 📂 emoji)。结果缓存,避免重复渲染。

        Render folder.svg (tinted to the banner text color) as an inline base64
        <img> so the rich-text QLabel can reuse the Browse button's icon,
        replacing the old 📂 emoji. Cached after first build.
        """
        cached = getattr(self, "_folder_icon_html_cache", None)
        if cached:
            return cached
        import base64
        from PySide6.QtCore import QBuffer, QByteArray
        pix = load_tinted_icon("folder.svg", COLORS["text_secondary"], px).pixmap(px, px)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        buf.close()
        b64 = bytes(ba.toBase64()).decode("ascii")
        self._folder_icon_html_cache = (
            f'<img src="data:image/png;base64,{b64}" '
            f'width="{px}" height="{px}" style="vertical-align:middle;">'
        )
        return self._folder_icon_html_cache

    def _update_status_banner(self, state: str, data=None):
        """更新状态条显示。

        state: "idle" | "ready" | "has_results" | "processing" | "done"
        data: 对 has_results/done 传入 stats dict；对 processing 传入 filename str
        """
        if not hasattr(self, '_status_banner'):
            return
        if state == "idle":
            self._status_banner.setText(self.i18n.t("labels.support_format_hint"))
            self._status_banner.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLORS['bg_card']};
                    color: {COLORS['text_tertiary']};
                    border: 1px solid {COLORS['border_subtle']};
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 0 12px;
                }}
            """)
        elif state == "ready":
            dirname = os.path.basename(self.directory_path) if self.directory_path else ""
            # 复用「浏览」按钮同款 folder.svg(内联染色 img)替代旧 📂 emoji;
            # 富文本会折叠空格,故文案空格转 &nbsp; 保留原间距 / keep spacing in rich text
            import html as _html
            text = self.i18n.t("labels.dir_ready").format(
                dirname=_html.escape(dirname)).replace(" ", "&nbsp;")
            self._status_banner.setText(self._status_folder_icon_html() + "&nbsp;&nbsp;" + text)
            self._status_banner.setStyleSheet(f"""
                QLabel {{
                    background-color: {COLORS['bg_card']};
                    color: {COLORS['text_secondary']};
                    border: 1px solid {COLORS['accent']};
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 0 12px;
                }}
            """)
        elif state == "has_results":
            counts = data or {}
            by_rating = counts.get("by_rating", {})
            total = counts.get("total", 0)
            n3 = by_rating.get(3, 0)
            n2 = by_rating.get(2, 0)
            n1 = by_rating.get(1, 0)
            self._status_banner.setText(
                self.i18n.t("labels.status_processed").format(total=total, n3=n3, n2=n2, n1=n1)
            )
            self._status_banner.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(34, 197, 94, 0.08);
                    color: {COLORS['success']};
                    border: 1px solid {COLORS['success']};
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 0 12px;
                }}
            """)
        elif state == "processing":
            filename = data or ""
            text = self.i18n.t("labels.status_processing").format(filename=filename) if filename else self.i18n.t("labels.status_processing_idle")
            self._status_banner.setText(text)
            self._status_banner.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(234, 179, 8, 0.08);
                    color: {COLORS['warning']};
                    border: 1px solid {COLORS['warning']};
                    border-radius: 6px;
                    font-size: 12px;
                    padding: 0 12px;
                }}
            """)
        elif state == "done":
            counts = data or {}
            by_rating = counts.get("by_rating", {})
            total = counts.get("total", 0)
            n3 = by_rating.get(3, 0)
            n2 = by_rating.get(2, 0)
            n1 = by_rating.get(1, 0)
            self._status_banner.setText(
                self.i18n.t("labels.status_done").format(total=total, n3=n3, n2=n2, n1=n1)
            )
            self._status_banner.setStyleSheet(f"""
                QLabel {{
                    background-color: rgba(34, 197, 94, 0.15);
                    color: {COLORS['success']};
                    border: 1px solid {COLORS['success']};
                    border-radius: 6px;
                    font-size: 13px;
                    font-weight: bold;
                    padding: 0 12px;
                }}
            """)

    def _update_action_buttons(self, state: str):
        """根据状态更新按钮区域。

        state: "idle" | "ready" | "has_results" | "processing"
        """
        if state == "idle":
            self.reset_btn.setEnabled(False)
            self.reset_btn.setText(self.i18n.t("labels.reset_short"))
            self.reset_btn.setObjectName("tertiary")
            self.start_btn.setEnabled(False)
            self.start_btn.setText(self.i18n.t("labels.start_processing"))
            self.start_btn.setObjectName("")
            if hasattr(self, 'view_results_btn'):
                self.view_results_btn.setVisible(False)
        elif state == "ready":
            self.reset_btn.setEnabled(True)
            self.reset_btn.setText(self.i18n.t("labels.reset_short"))
            self.reset_btn.setObjectName("tertiary")
            self.start_btn.setEnabled(True)
            self.start_btn.setText(self.i18n.t("labels.start_processing"))
            self.start_btn.setObjectName("")
            if hasattr(self, 'view_results_btn'):
                self.view_results_btn.setVisible(False)
        elif state == "has_results":
            self.reset_btn.setEnabled(True)
            self.reset_btn.setText(self.i18n.t("labels.reprocess"))
            self.reset_btn.setObjectName("tertiary")
            self.start_btn.setEnabled(True)
            self.start_btn.setText(self.i18n.t("labels.start_processing"))
            self.start_btn.setObjectName("tertiary")
            if hasattr(self, 'view_results_btn'):
                self.view_results_btn.setVisible(True)
                self.view_results_btn.setObjectName("")
        elif state == "processing":
            self.reset_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            if hasattr(self, 'view_results_btn'):
                self.view_results_btn.setVisible(False)
        # 刷新样式（objectName 变化后需要 unpolish/polish）
        for btn in [self.reset_btn, self.start_btn]:
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if hasattr(self, 'view_results_btn') and self.view_results_btn.isVisible():
            self.view_results_btn.style().unpolish(self.view_results_btn)
            self.view_results_btn.style().polish(self.view_results_btn)

    def _check_report_csv(self):
        """检查是否有 report.db，更新状态条，有结果时自动弹出浏览器。"""
        if not self.directory_path:
            return

        try:
            from tools.resume_state import ResumeStateManager
            if ResumeStateManager(self.directory_path).exists():
                self._update_status_banner("ready")
                self._update_action_buttons("ready")
                return
        except Exception:
            pass

        report_path = os.path.join(self.directory_path, ".superpicky", "report.db")
        if os.path.exists(report_path):
            counts = self._load_result_counts()
            self._update_status_banner("has_results", counts)
            self._update_action_buttons("has_results")
            # 只有保留预览图时才自动弹出浏览器（无预览图时浏览器无内容）
            from advanced_config import get_advanced_config as _get_adv
            if _get_adv().keep_temp_files:
                QTimer.singleShot(300, self._auto_open_results)
        else:
            self._update_status_banner("ready")
            self._update_action_buttons("ready")

    def _update_status(self, text, color=None):
        """更新状态指示器"""
        self.status_label.setText(text)
        if color:
            self.status_dot.setStyleSheet(f"""
                QLabel {{
                    background-color: {color};
                    border-radius: 3px;
                }}
            """)

    @Slot()
    def _is_removable_source(self) -> bool:
        """
        检测源目录是否位于可移动磁盘（存储卡 / U 盘）。检测失败时保守返回 False。
        Detect whether the source folder sits on a removable drive (card / USB).
        Returns False on any detection error so it never blocks legitimate use.
        """
        path = self.directory_path
        if not path:
            return False
        try:
            if sys.platform == "win32":
                import ctypes
                drive = os.path.splitdrive(os.path.abspath(path))[0]
                if not drive:
                    return False
                DRIVE_REMOVABLE = 2
                return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOVABLE
            if sys.platform == "darwin":
                return os.path.realpath(path).startswith("/Volumes/")
        except Exception:
            return False
        return False

    def _start_processing(self):
        """开始处理"""
        if not self._require_initialization_for_processing():
            return

        if not self.directory_path:
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return

        # V4.3.0: 源目录在存储卡/可移动磁盘上时强烈警告。直接在卡上处理会移动上千个
        # 文件，卡 IO 慢、文件系统脆弱，中途中断或拔卡极易丢照片；建议先复制到硬盘。
        if self._is_removable_source():
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.removable_warning_title"),
                self.i18n.t("messages.removable_warning_message"),
                yes_text=self.i18n.t("labels.yes"),
                no_text=self.i18n.t("labels.no")
            )
            if reply != StyledMessageBox.Yes:
                return

        if self.worker and self.worker.is_alive():
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.processing")
            )
            return

        # 确认弹窗 - 动态构建消息(HTML:emoji 换 SVG 图标,QLabel 自动按富文本渲染)
        import html as _html
        from ui.styles import COLORS as _C
        _green = _C.get("focus_best", "#00cc44")
        _accent = _C.get("accent", "#00d4aa")
        _sec = _C.get("text_secondary", "#a1a1a1")

        def _ico(svg, color, size=14):
            p = tinted_png_path(svg, color, size)
            return f'<img src="{p}" width="{size}" height="{size}" style="vertical-align:middle"> '

        def _esc(s):
            return _html.escape(str(s), quote=False)

        # Task 7: 从 advanced_config 读取开关状态，不再读取已删除的参数面板控件
        # Task 7: read switch states from advanced_config; old panel widgets removed
        _adv_confirm = get_advanced_config()
        extra_notes = []
        if _adv_confirm.flight_check:
            extra_notes.append(_ico("bird.svg", _green) + _esc(self.i18n.t("dialogs.note_flight")))
        if _adv_confirm.birdid_auto_identify:
            extra_notes.append(_ico("eye.svg", _accent) + _esc(self.i18n.t("dialogs.note_birdid")))
            # 显示当前国家/区域设置（从 advanced_config 读取，Task 8 后不再依赖 dock 内控件）
            # Show current country/region from advanced_config (Task 8: no longer reads dock widgets)
            country_display = _adv_confirm.birdid_selected_country or ""
            region_display = _adv_confirm.birdid_selected_region or ""
            if country_display:
                location_info = f"&nbsp;&nbsp;&nbsp;&nbsp;{_esc(country_display)}"
                if region_display and region_display != self.i18n.t("birdid.region_entire_country"):
                    location_info += f" - {_esc(region_display)}"
                extra_notes.append(location_info)
            # V4.3: 检查是否选择了国家，如果是 Auto Detect GPS 则提示
            # V4.3: Prompt if country is still in Auto GPS mode
            country_code = _adv_confirm.birdid_country_code
            if country_code is None:  # "自动检测 (GPS)" 模式 / Auto GPS mode
                reply = StyledMessageBox.question(
                    self,
                    self.i18n.t("birdid.country_prompt_title"),
                    self.i18n.t("birdid.country_prompt_message"),
                    yes_text=self.i18n.t("labels.yes"),
                    no_text=self.i18n.t("labels.no")
                )
                if reply == StyledMessageBox.Yes:
                    # 用户选择现在配置国家：打开设置中心 birdid 页
                    # User chose to configure now: open Settings Center on birdid page
                    self._open_settings_center("birdid")
                    return  # 等用户配置后再开始 / Wait for user to configure
        if _adv_confirm.burst_check:
            extra_notes.append(_ico("square-stack.svg", _sec) + _esc(self.i18n.t("dialogs.note_burst")))

        notes_block = ""
        if extra_notes:
            notes_block = "<br>" + "<br>".join(extra_notes) + "<br>"

        # 正文转富文本:escape + 换行→<br>,再把哨兵替换为已是 HTML 的附注块
        _sentinel = "@@EXTRA_NOTES@@"
        _raw = self.i18n.t("dialogs.file_organization_msg", extra_notes=_sentinel)
        base_msg = _esc(_raw).replace("\n", "<br>").replace(_sentinel, notes_block)

        reply = StyledMessageBox.question(
            self,
            self.i18n.t("dialogs.file_organization_title"),
            base_msg,
            yes_text=self.i18n.t("labels.yes"),
            no_text=self.i18n.t("labels.no")
        )

        if reply != StyledMessageBox.Yes:
            return

        # V4.3 Phase 4: 视频首次提示 — 检测到视频且用户从未被提示过时弹一次
        # V4.3 Phase 4: First-run prompt — show once when videos are found
        self._maybe_prompt_video_first_run()

        resume_processing = False
        try:
            from tools.resume_state import ResumeStateManager
            resume_state = ResumeStateManager(self.directory_path)
            if resume_state.exists() and self._resume_prompt_handled:
                resume_processing = True
            elif resume_state.exists():
                resume_reply = StyledMessageBox.question(
                    self,
                    self.i18n.t("dialogs.unfinished_title"),
                    self.i18n.t("dialogs.unfinished_body"),
                    yes_text=self.i18n.t("dialogs.continue_btn"),
                    no_text=self.i18n.t("dialogs.restart_btn")
                )
                if resume_reply == StyledMessageBox.Yes:
                    resume_processing = True
                else:
                    resume_state.clear()
                    self._suppress_results_browser_once = True
                    self._quick_restore_directory()
                    return
        except Exception as resume_err:
            self._log(f"⚠️ 恢复状态检查失败: {resume_err}", "warning")
        finally:
            self._resume_prompt_handled = False

        # ── 开始前自检 ──────────────────────────────────────────
        # 1. ExifTool 健康检查（阻断型）
        try:
            from tools.exiftool_manager import get_exiftool_manager
            get_exiftool_manager()  # 触发 _verify_exiftool()，失败会 raise RuntimeError
        except Exception as _et_err:
            StyledMessageBox.warning(
                self,
                self.i18n.t("health.exiftool_error_title"),
                self.i18n.t("health.exiftool_error_msg", error=str(_et_err)),
            )
            return

        # 2. 照片数量预扫描（阻断型）
        scan_results = None
        try:
            from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH, is_dangerous_root, scan_directories

            is_dangerous, reason = is_dangerous_root(self.directory_path)
            if is_dangerous:
                StyledMessageBox.warning(
                    self,
                    self.i18n.t("health.dangerous_dir_title"),
                    self.i18n.t(
                        "health.dangerous_dir_msg",
                        directory=self.directory_path,
                        reason=reason,
                    ),
                )
                return

            scan_results = scan_directories(self.directory_path, max_depth=DEFAULT_SCAN_MAX_DEPTH)
            if not scan_results:
                StyledMessageBox.warning(
                    self,
                    self.i18n.t("health.no_photos_title"),
                    self.i18n.t("health.no_photos_msg", directory=self.directory_path),
                )
                return
        except Exception:
            pass  # 扫描失败不阻断，交给 worker 处理

        # 3. 模型预加载状态提示（非阻断）
        if not self._preload_done:
            self._log(self.i18n.t("health.models_still_loading"), "warning")
        # ────────────────────────────────────────────────────────

        # 清空日志和进度
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.progress_info_label.setText("")
        self.progress_percent_label.setText("")

        self._update_status(self.i18n.t("labels.processing"), COLORS['warning'])
        self._log(self.i18n.t("logs.processing_start"))

        # Task 7: 准备 UI 设置 — 改从 advanced_config 读取，不再依赖已删除的参数面板控件
        # Task 7: Prepare UI settings — read from advanced_config; old panel widgets removed
        _adv = get_advanced_config()
        ui_settings = [
            int(_adv.min_confidence * 100),   # [0] AI 置信度 / AI confidence
            int(_adv.min_sharpness),           # [1] 锐度阈值 / sharpness threshold
            _adv.min_nima,                     # [2] NIMA 美学阈值 / NIMA aesthetics threshold
            True,                              # [3] 始终保存裁切 / always save crop
            "log_compression",                 # [4] 归一化模式 / normalization mode
            bool(_adv.flight_check),           # [5] 飞鸟检测 / flight detection
            False,                             # [6] 曝光检测已移除，固定 False / exposure removed
            bool(_adv.burst_check),            # [7] 连拍检测 / burst detection
            bool(_adv.birdid_auto_identify),   # [8] 识鸟开关 / bird ID auto-identify
        ]

        # 创建信号
        self.worker_signals = WorkerSignals()
        self.worker_signals.progress.connect(self._on_progress)
        self.worker_signals.log.connect(self._on_log)
        self.worker_signals.finished.connect(self._on_finished)
        self.worker_signals.error.connect(self._on_error)
        # V4.2: 裁剪预览信号连接到 BirdID Dock
        if hasattr(self, 'birdid_dock') and self.birdid_dock:
            self.worker_signals.crop_preview.connect(self.birdid_dock.update_crop_preview)

        # 禁用按钮，更新状态条
        self._update_action_buttons("processing")
        self._update_status_banner("processing")

        # 启动工作线程
        self.worker = WorkerThread(
            self.directory_path,
            ui_settings,
            self.worker_signals,
            self.i18n,
            resume=resume_processing,
            scan_results=scan_results,
        )
        self.worker.start()

    @Slot(int)
    def _on_progress(self, value):
        """进度更新"""
        self.progress_bar.setValue(value)
        self.progress_percent_label.setText(f"{value}%")

    @Slot(str, str)
    def _on_log(self, message, tag):
        """日志更新"""
        self._log(message, tag)
        # 改动 7: 处理中状态条实时显示当前文件名
        # 格式示例: "📸 处理照片 12/265: IMG_1234.JPG" 或 "[12/265] 处理: IMG_1234.JPG"
        if tag == "progress" or ("处理" in message and "/" in message and ":" in message):
            import re
            m = re.search(r':\s*(.+\.(jpg|jpeg|png|cr2|cr3|arw|nef|orf|rw2|dng))', message, re.IGNORECASE)
            if m:
                self._update_status_banner("processing", m.group(1))

    @Slot(dict)
    def _on_finished(self, stats):
        """处理完成"""
        self.progress_bar.setValue(100)
        self.progress_percent_label.setText("100%")
        self.progress_info_label.setText(self.i18n.t("labels.complete"))

        self._update_status(self.i18n.t("labels.complete"), COLORS['success'])

        # 更新状态条为完成状态
        # 直接从 stats 参数构建（避免 DB 时序问题：处理线程可能还未关闭连接）
        counts = {
            "total": stats.get("total", 0),
            "by_rating": {
                3:  stats.get("star_3", 0),
                2:  stats.get("star_2", 0),
                1:  stats.get("star_1", 0),
                0:  stats.get("star_0", 0),
                -1: stats.get("no_bird", 0),
            },
        }
        self._update_status_banner("done", counts)
        self._update_action_buttons("has_results")

        # 显示报告（不清空之前的日志；HTML 渲染,评级/飞版/精焦用 SVG）
        self._show_statistics_report(stats)
        # Lightroom 指南已停用（用户群体太少）：保留 _show_lightroom_guide 方法备用

        # V4.2: 通知 BirdIDDock 显示完成信息（传入 stats 替代 debug_dir）
        if hasattr(self, 'birdid_dock') and self.birdid_dock:
            self.birdid_dock.show_completion_message(stats)

        # 播放完成音效
        self._play_completion_sound()

        # 800ms 后按设置决定行为
        from advanced_config import get_advanced_config as _gc
        if _gc().keep_temp_files:
            QTimer.singleShot(800, self._auto_open_results)
        else:
            QTimer.singleShot(800, self._open_finder_results)

    @Slot(str)
    def _on_error(self, error_msg):
        """处理错误"""
        self._log(f"Error: {error_msg}", "error")
        self._update_status(self.i18n.t("errors.error_title"), COLORS['error'])
        self._check_report_csv()  # 恢复按钮状态 + 状态条

    @Slot()
    def _quick_restore_directory(self):
        """V4.0.4: 快速复原目录（只移动文件，不重置EXIF）
        
        用于重新处理时的确认弹窗，因为EXIF会被新的处理结果覆盖
        """
        self._do_reset_directory(skip_exif_reset=True, skip_confirm=True)
    
    @Slot()
    def _reset_directory(self):
        """完整重置目录（移动文件 + 重置EXIF）"""
        self._do_reset_directory(skip_exif_reset=False, skip_confirm=False)
    
    def _do_reset_directory(self, skip_exif_reset=False, skip_confirm=False):
        """执行目录重置
        
        Args:
            skip_exif_reset: 是否跳过EXIF重置（快速复原模式）
            skip_confirm: 是否跳过确认弹窗
        """
        if not self.directory_path:
            StyledMessageBox.warning(
                self,
                self.i18n.t("messages.hint"),
                self.i18n.t("messages.select_dir_first")
            )
            return

        if not skip_confirm:
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.reset_confirm_title"),
                self.i18n.t("messages.reset_confirm"),
                yes_text=self.i18n.t("labels.yes"),
                no_text=self.i18n.t("labels.no")
            )

            if reply != StyledMessageBox.Yes:
                return

        # V4.3.1: 「按目录名摊平」从「无 manifest 才询问」改为 reset 末尾无条件自动兜底
        # （见 run_reset 内的 force_flatten_directory 调用）。manifest 可能不完整
        # （如连拍成员从未入库），仅靠 manifest 恢复会把残留在 鸟种/星级/burst_ 子目录
        # 里的文件永久遗漏；force_flatten 幂等安全（同名不覆盖、只动 SuperPicky 目录、
        # 不碰用户目录），因此始终执行即可，无需再询问用户。
        # V4.3.1: name-based flatten is now an unconditional safety net at the end of
        # run_reset (no longer gated on "no manifest"), because manifests can be
        # incomplete (e.g. burst members never recorded) and manifest-only restore
        # would otherwise strand files in species/rating/burst_ subdirs.
        self.log_text.clear()
        self.reset_btn.setEnabled(False)
        self.start_btn.setEnabled(False)

        # V4.0.4: 根据模式显示不同状态
        if skip_exif_reset:
            self._update_status(self.i18n.t("labels.quick_restoring"), COLORS['warning'])
            self._log(self.i18n.t("logs.quick_restore_start"))
        else:
            self._update_status(self.i18n.t("labels.resetting"), COLORS['warning'])
            self._log(self.i18n.t("logs.reset_start"))

        directory_path = self.directory_path
        i18n = self.i18n
        log_signal = self.reset_log_signal
        complete_signal = self.reset_complete_signal
        error_signal = self.reset_error_signal
        _skip_exif_reset = skip_exif_reset  # 传递给线程

        def run_reset():
            restore_stats = {'restored': 0, 'failed': 0}
            exif_stats = {'success': 0, 'failed': 0}

            def emit_log(msg):
                log_signal.emit(msg)

            try:
                from tools.exiftool_manager import get_exiftool_manager
                from tools.find_bird_util import reset
                import shutil

                exiftool_mgr = get_exiftool_manager()

                # V4.3.0: 先复原视频归类（按「归类清单」把视频移回原位、删 SRT、清空子目录）。
                # 视频鸟种子目录不是照片评分目录，照片端 reset 不认识，需独立复原。
                # V4.3.0: Restore video organization first (manifest-driven undo): move videos
                # back, delete SRTs, prune empty species folders. Video species folders are not
                # photo rating folders, so the photo reset below won't touch them.
                try:
                    from tools.video_organizer import (
                        restore_organized_videos, VIDEO_MANIFEST_NAME,
                    )
                    vid_total = {'restored': 0, 'dirs_removed': 0, 'manifests': 0}
                    for _root, _dirs, _files in os.walk(directory_path):
                        _dirs[:] = [d for d in _dirs if not d.startswith('.')]
                        if VIDEO_MANIFEST_NAME in _files:
                            vstats = restore_organized_videos(_root, log=emit_log)
                            if vstats.get('manifest'):
                                vid_total['manifests'] += 1
                                vid_total['restored'] += vstats.get('restored', 0)
                                vid_total['dirs_removed'] += vstats.get('dirs_removed', 0)
                    if vid_total['manifests']:
                        emit_log(i18n.t("logs.video_restore_done",
                                        restored=vid_total['restored'],
                                        dirs=vid_total['dirs_removed']))
                except Exception as _ve:
                    emit_log(i18n.t("logs.video_restore_failed", error=_ve))

                # Batch mode: reset processed subdirectories first (deepest first)
                from core.recursive_scanner import is_processed
                sub_dirs_to_reset = []
                for root_d, subdirs, files in os.walk(directory_path):
                    subdirs[:] = [d for d in subdirs if not d.startswith('.')]
                    from constants import RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN
                    star_names = set(RATING_FOLDER_NAMES.values()) | set(RATING_FOLDER_NAMES_EN.values())
                    subdirs[:] = [d for d in subdirs if d not in star_names and not d.startswith('burst_')]
                    for d in subdirs:
                        full = os.path.join(root_d, d)
                        if is_processed(full):
                            sub_dirs_to_reset.append(full)

                if sub_dirs_to_reset:
                    # Reset deepest first
                    sub_dirs_to_reset.sort(key=lambda p: p.count(os.sep), reverse=True)
                    emit_log(f"\n\U0001f4c2 Batch reset: {len(sub_dirs_to_reset)} subdirectories")
                    for idx, sub_dir in enumerate(sub_dirs_to_reset, 1):
                        rel = os.path.relpath(sub_dir, directory_path)
                        emit_log(f"\n\U0001f504 [{idx}/{len(sub_dirs_to_reset)}] {rel}/")
                        try:
                            # Reuse CLI reset logic
                            _args = SimpleNamespace(directory=sub_dir, yes=True)
                            from superpicky_cli import cmd_reset as _cli_reset
                            _cli_reset(_args)
                            emit_log(f"  \u2705 {rel}/ reset done")
                        except Exception as e:
                            emit_log(f"  \u274c {rel}/ reset failed: {e}")

                # Now reset the root directory
                emit_log(i18n.t("logs.reset_step0"))
                rating_dirs = ['3star_excellent', '2star_good', '1star_average', '0star_reject',
                               '3星_优选', '2星_良好', '1星_普通', '0星_放弃']
                subdir_stats = {'dirs_removed': 0, 'files_restored': 0}
                
                for rating_dir in rating_dirs:
                    rating_path = os.path.join(directory_path, rating_dir)
                    if not os.path.exists(rating_path):
                        continue
                    
                    for entry in os.listdir(rating_path):
                        entry_path = os.path.join(rating_path, entry)
                        if os.path.isdir(entry_path):
                            # 递归将所有文件移回评分目录（V4.3.0: 同名跳过、绝不覆盖删除）
                            for root, dirs, files in os.walk(entry_path):
                                for filename in files:
                                    src = os.path.join(root, filename)
                                    dst = os.path.join(rating_path, filename)
                                    if os.path.isfile(src):
                                        if os.path.exists(dst):
                                            continue  # 同名保留两者，不覆盖（数据安全）
                                        try:
                                            shutil.move(src, dst)
                                            subdir_stats['files_restored'] += 1
                                        except Exception as e:
                                            emit_log(i18n.t("logs.move_failed", filename=filename, error=e))

                            # 删除子目录（V4.3.0: 仅当其内已无任何文件，避免误删残留）
                            try:
                                if os.path.isdir(entry_path) and not any(
                                    fs for _r, _d, fs in os.walk(entry_path)
                                ):
                                    shutil.rmtree(entry_path, ignore_errors=True)
                                    subdir_stats['dirs_removed'] += 1
                            except Exception as e:
                                emit_log(i18n.t("logs.burst_clean_failed", entry=entry, error=e))
                
                if subdir_stats['dirs_removed'] > 0:
                    emit_log(i18n.t("logs.burst_cleaned", dirs=subdir_stats['dirs_removed'], files=subdir_stats['files_restored']))
                else:
                    emit_log(i18n.t("logs.burst_no_clean"))

                emit_log(i18n.t("logs.reset_step1"))
                restore_stats = exiftool_mgr.restore_files_from_manifest(
                    directory_path, log_callback=emit_log, i18n=i18n
                )

                restored_count = restore_stats.get('restored', 0)
                if restored_count > 0:
                    emit_log(i18n.t("logs.restored_files", count=restored_count))
                
                # V4.0.5: Manifest 可能不包含所有文件，扫描评分目录将残留文件移回根目录
                fallback_restored = 0
                for rating_dir in rating_dirs:
                    rating_path = os.path.join(directory_path, rating_dir)
                    if not os.path.exists(rating_path):
                        continue
                    
                    for filename in os.listdir(rating_path):
                        src = os.path.join(rating_path, filename)
                        dst = os.path.join(directory_path, filename)
                        if os.path.isfile(src):
                            if os.path.exists(dst):
                                continue  # V4.3.0: 同名不覆盖根目录原文件（数据安全）
                            try:
                                shutil.move(src, dst)
                                fallback_restored += 1
                            except Exception as e:
                                emit_log(i18n.t("logs.move_failed", filename=filename, error=e))
                
                if fallback_restored > 0:
                    emit_log(i18n.t("logs.restored_files", count=fallback_restored))

                # V4.3.1: 无条件「按目录名摊平」兜底——把仍残留在 鸟种/星级/burst_
                # 子目录里的文件递归移回根目录。manifest 可能不完整（连拍成员未入库等），
                # 仅靠上面的 manifest/根目录评分扫描会遗漏鸟种优先布局下的深层文件。
                # force_flatten 幂等安全（同名不覆盖、只动 SuperPicky 目录、不碰用户目录），
                # 无残留时 moved=0，因此始终执行无副作用。
                # V4.3.1: unconditional name-based flatten safety net — recursively move
                # any files still stranded in species/rating/burst_ subdirs back to root.
                flatten_moved = 0
                try:
                    from tools.find_bird_util import force_flatten_directory
                    _fstats = force_flatten_directory(directory_path, log_callback=emit_log, i18n=i18n)
                    flatten_moved = int(_fstats.get("moved", 0)) if _fstats else 0
                except Exception as _fe:
                    emit_log(f"⚠️ flatten fallback failed: {_fe}")

                # 恢复总数 = manifest 恢复 + 评分目录兜底 + 按目录名摊平兜底（合并显示,避免数字与实际不符）
                total_restored = restored_count + fallback_restored + flatten_moved
                if total_restored == 0:
                    emit_log(i18n.t("logs.no_files_to_restore"))
                else:
                    emit_log(i18n.t("logs.restored_files", count=total_restored))

                # V4.0.4: 根据模式决定是否重置EXIF
                if _skip_exif_reset:
                    emit_log("\n" + i18n.t("logs.skip_exif_reset"))
                    success = True
                else:
                    emit_log("\n" + i18n.t("logs.reset_step2"))
                    success = reset(directory_path, log_callback=emit_log, i18n=i18n)
                
                # V3.9: 删除评分目录（所有文件已移走）
                emit_log(i18n.t("logs.reset_step3"))
                from tools.find_bird_util import cleanup_ignorable_reset_residue
                deleted_dirs = 0
                for rating_dir in rating_dirs:
                    rating_path = os.path.join(directory_path, rating_dir)
                    if os.path.exists(rating_path) and os.path.isdir(rating_path):
                        # 先清理系统元数据残留，再判断是否仍有真实文件需要保留。
                        # Remove OS metadata residue first, then preserve any real files that remain.
                        cleanup_ignorable_reset_residue(rating_path)
                        # V4.3.0: 仅当评分目录内已无任何文件才删除，避免误删残留（数据安全）
                        residual_files = []
                        for _r, _d, fs in os.walk(rating_path):
                            for _filename in fs:
                                _rel = os.path.relpath(os.path.join(_r, _filename), rating_path)
                                residual_files.append(_rel)
                        if residual_files:
                            sample = ", ".join(residual_files[:3])
                            if len(residual_files) > 3:
                                sample = f"{sample}, ..."
                            emit_log(i18n.t("logs.empty_dir_delete_failed",
                                            dir=rating_dir,
                                            error=f"仍有残留文件，保留: {sample}"))
                            continue
                        try:
                            shutil.rmtree(rating_path, ignore_errors=True)
                            emit_log(i18n.t("logs.empty_dir_deleted", dir=rating_dir))
                            deleted_dirs += 1
                        except Exception as e:
                            emit_log(i18n.t("logs.empty_dir_delete_failed", dir=rating_dir, error=e))
                
                # V4.0.5: 清理 .superpicky 隐藏目录和 manifest 文件
                # Quick Restore: 重新处理时保留 .superpicky 缓存（预览图复用，节省时间）
                superpicky_dir = os.path.join(directory_path, ".superpicky")
                if not _skip_exif_reset and os.path.exists(superpicky_dir):
                    try:
                        shutil.rmtree(superpicky_dir)
                        emit_log("  ✅ .superpicky/")
                        deleted_dirs += 1
                    except Exception:
                        # 尝试系统命令强制删除
                        try:
                            import subprocess
                            subprocess.run(['rm', '-rf', superpicky_dir], check=True)
                            emit_log("  ✅ .superpicky/ (force)")
                            deleted_dirs += 1
                        except Exception as e2:
                            emit_log(f"  ⚠️ .superpicky 删除失败: {e2}")
                elif _skip_exif_reset:
                    emit_log("  ✅ .superpicky/ 缓存已保留（快速复原：预览图复用）")
                
                manifest_file = os.path.join(directory_path, ".superpicky_manifest.json")
                if os.path.exists(manifest_file):
                    try:
                        os.remove(manifest_file)
                        emit_log("  ✅ .superpicky_manifest.json")
                    except Exception as e:
                        emit_log(f"  ⚠️ manifest 删除失败: {e}")
                
                # 清理 macOS ._burst_XXX 残留文件
                for filename in os.listdir(directory_path):
                    if filename.startswith('._burst_') or filename.startswith('._其他') or filename.startswith('._栗'):
                        try:
                            os.remove(os.path.join(directory_path, filename))
                        except Exception:
                            pass
                
                if deleted_dirs > 0:
                    emit_log(i18n.t("logs.empty_dirs_cleaned", count=deleted_dirs))
                else:
                    emit_log(i18n.t("logs.no_empty_dirs"))

                emit_log("\n" + i18n.t("logs.reset_complete"))
                complete_signal.emit(success, restore_stats, exif_stats)

            except Exception as e:
                import traceback
                error_msg = str(e)
                emit_log(f"\n{i18n.t('errors.error_title')}: {error_msg}")
                traceback.print_exc()
                error_signal.emit(error_msg)

        threading.Thread(target=run_reset, daemon=True).start()

    def _on_reset_complete(self, success, restore_stats=None, exif_stats=None):
        """重置完成"""
        if success:
            self._update_status(self.i18n.t("labels.ready"), COLORS['accent'])
            self._log(self.i18n.t("messages.reset_complete_log"))

            msg_parts = [self.i18n.t("messages.reset_complete_msg") + "\n"]

            if restore_stats:
                restored = restore_stats.get('restored', 0)
                if restored > 0:
                    msg_parts.append(self.i18n.t("messages.files_restored", count=restored))

            if exif_stats:
                exif_success = exif_stats.get('success', 0)
                if exif_success > 0:
                    msg_parts.append(self.i18n.t("messages.exif_reset_count", count=exif_success))

            msg_parts.append("\n" + self.i18n.t("messages.ready_for_analysis"))

            self._show_message(
                self.i18n.t("messages.reset_complete_title"),
                "\n".join(msg_parts),
                "info"
            )
        else:
            self._update_status(self.i18n.t("labels.error"), COLORS['error'])
            self._log(self.i18n.t("messages.reset_failed_log"))
        if self._suppress_results_browser_once:
            self._suppress_results_browser_once = False
            self._update_status_banner("ready")
            self._update_action_buttons("ready")
            return

        self._check_report_csv()

    def _on_reset_error(self, error_msg):
        """重置错误"""
        self._log(f"Error: {error_msg}", "error")
        self._update_status("Error", COLORS['error'])
        self._show_message(
            self.i18n.t("errors.error_title"),
            error_msg,
            "error"
        )
        self._check_report_csv()

    @Slot()
    def _maybe_prompt_video_first_run(self):
        """
        V4.3 Phase 4: 首次发现视频时弹一次性提示

        触发条件：当前目录扫描到至少 1 个视频文件 + 用户从未被提示过
        提示内容：告知主流程会自动处理视频；用户可关闭
        副作用：写 advanced_config 标记已提示，下次不再弹

        First-run prompt when videos are detected; one-time only.
        """
        try:
            from advanced_config import get_advanced_config
            cfg = get_advanced_config()
        except Exception:
            return
        if cfg.config.get("video_first_run_prompted"):
            return
        # 检测是否有视频 / Check if any video exists
        try:
            from core.recursive_scanner import scan_directories
            results = scan_directories(self.directory_path)
            total_videos = sum(item.video_count for item in results)
        except Exception:
            return
        if total_videos == 0:
            return

        # 弹一次性提示 / Show one-time dialog
        msg = self.i18n.t("dialogs.video_first_tip_body", count=total_videos)
        reply = StyledMessageBox.question(
            self, self.i18n.t("dialogs.video_first_tip_title"), msg,
            yes_text=self.i18n.t("dialogs.enable_btn"), no_text=self.i18n.t("dialogs.skip_btn")
        )
        # 不论用户怎么选，都标记已提示
        # Mark as prompted regardless of choice
        cfg.config["video_first_run_prompted"] = True
        cfg.set_video_auto_process_in_main(reply == StyledMessageBox.Yes)
        # 首页「视频」开关需同步反映这里的选择，否则会显示旧值直到设置中心刷新一次
        # Keep the home-panel "Video" toggle in sync with this choice
        self._refresh_param_panel()

    def _open_video_analyzer(self):
        """
        打开视频分析独立窗口（Phase 1）

        懒加载：首次点击时才 import 并创建窗口；之后复用同一实例。

        Open the standalone video analyzer window (Phase 1). Lazy-loaded singleton.
        """
        if self._video_analyzer_window is None:
            from ui.video_analyzer_window import VideoAnalyzerWindow
            self._video_analyzer_window = VideoAnalyzerWindow(self)
        self._video_analyzer_window.show()
        self._video_analyzer_window.raise_()
        self._video_analyzer_window.activateWindow()

    def _change_language(self, lang_code):
        """切换界面语言"""
        from ui.custom_dialogs import StyledMessageBox
        
        # 更新菜单选中状态
        for code, action in self.lang_actions.items():
            action.setChecked(code == lang_code)
        
        # 保存设置
        self.config.set_language(lang_code)
        if self.config.save():
            # 根据目标语言显示对应的提示
            if lang_code == "en":
                title = "Language Changed"
                msg = "Language changed. Restart the app to take effect."
            else:
                title = "语言已更改"
                msg = "界面语言已更改，重启应用后生效。"
            StyledMessageBox.information(self, title, msg)

    def _open_settings_center(self, start_page: str = "culling") -> None:
        """打开设置中心弹窗，关闭后刷新依赖值（技能 chip、识鸟面板状态）。

        Task 7: 统一设置入口。所有参数、水平、识鸟等配置都在设置中心内完成。
        关闭后调用 _refresh_skill_chip 保证 header chip 与当前配置同步；
        通过 Task 8 guard 调用 birdid_dock.reload_from_config()（Task 8 提供方法）。

        Open the SettingsCenter dialog; refresh dependent values after it closes.
        Task 7: Unified settings entry. All config lives inside SettingsCenter.
        After close: refresh skill chip + call birdid_dock.reload_from_config (Task 8).

        Parameters:
            start_page (str): 设置中心初始显示页 key / Initial page key ("culling"/"about"/etc.)
        """
        from ui.settings_center import SettingsCenter
        dlg = SettingsCenter(self.i18n, parent=self, start_page=start_page)
        dlg.exec()

        # 刷新技能 chip 标签，确保与 advanced_config 中当前值一致
        # Refresh the skill level chip so it reflects the current advanced_config value
        self._refresh_skill_chip()
        # 刷新首页快速参数面板,与设置中心改动保持一致(SSOT 双向同步)
        self._refresh_param_panel()

        # Task 8 guard: birdid_dock.reload_from_config 由 Task 8 提供
        # Task 8 guard: reload_from_config is provided by Task 8
        dock = getattr(self, "birdid_dock", None)
        if dock is not None and hasattr(dock, "reload_from_config"):
            dock.reload_from_config()

    def _refresh_skill_chip(self) -> None:
        """从 advanced_config 读取当前技能等级并刷新 header chip 标签。

        供 _open_settings_center 关闭后调用，确保 chip 显示值与持久化配置一致。

        Read current skill level from advanced_config and refresh the header chip label.
        Called by _open_settings_center after the dialog closes.
        """
        _cfg = get_advanced_config()
        self._update_skill_level_label(_cfg.skill_level)

    def _create_parameters_section(self, parent_layout):
        """
        首页「快速调整」参数面板:锐度/美学两滑块 + 飞行/连拍/识鸟/视频四开关。

        所有控件双向绑定 advanced_config(单一事实源):初值从 config 读,改动写回 config;
        范围与设置中心精选页一致(锐度 100-600,美学 0-70 即 0.0-7.0),避免两处不一致或截断。
        手动改滑块视为自定义档(skill_level=custom 并同步 custom_*),与设置中心精选页协同一致。
        初值在 connect 之前设置,故不会在构建时误触发回调。
        「视频」开关默认关闭(大多数用户不需要视频识鸟),绑定 video_auto_process_in_main,
        与设置中心「视频」页的总开关双向同步。

        Home quick-adjust panel: sharpness/aesthetics sliders + flight/burst/birdid/video toggles.
        Two-way bound to advanced_config (SSOT); ranges match the Settings Center culling page.
        Editing a slider marks the skill level as custom (consistent with the culling page).
        The "Video" toggle defaults to off (most users don't need video bird-ID) and is bound
        to video_auto_process_in_main, kept in sync with the Video page in Settings Center.
        """
        cfg = self.config
        params_frame = QFrame()
        params_frame.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_elevated']}; border-radius: 10px; }}")
        params_layout = QVBoxLayout(params_frame)
        params_layout.setContentsMargins(20, 16, 20, 16)
        params_layout.setSpacing(16)

        # 头部:标题 + 三开关 / Header: title + three toggles
        header_layout = QHBoxLayout()
        params_title = QLabel(self.i18n.t("labels.selection_params"))
        params_title.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 500;")
        header_layout.addWidget(params_title)
        header_layout.addStretch()

        def _toggle(label_text: str, checked: bool) -> QCheckBox:
            box = QHBoxLayout()
            box.setSpacing(10)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
            box.addWidget(lbl)
            cb = QCheckBox()
            cb.setChecked(checked)   # 设初值在 connect 之前 / set before connect
            cb.setStyleSheet(checkbox_indicator_qss(16, COLORS['text_muted'], COLORS['accent']))
            box.addWidget(cb)
            header_layout.addLayout(box)
            return cb

        self.flight_check = _toggle(self.i18n.t("labels.flight_detection"), bool(cfg.flight_check))
        self.burst_check = _toggle(self.i18n.t("labels.burst"), bool(cfg.burst_check))
        self.birdid_check = _toggle(self.i18n.t("menu.birdid_label"), bool(cfg.birdid_auto_identify))
        # ExtremeSimple: 首页「视频」总开关已剥离（_on_video_check_changed 保留在下方，
        # video_auto_process_in_main 默认就是 False；未来要恢复只需把这行 _toggle(...)
        # 创建 + 下面的 connect 加回来）。
        # ExtremeSimple: the home-screen "video" toggle is stripped
        # (_on_video_check_changed stays below; video_auto_process_in_main
        # already defaults to False). Re-add the _toggle(...) call + connect
        # below to bring it back.
        self.flight_check.stateChanged.connect(self._save_check_states)
        self.burst_check.stateChanged.connect(self._save_check_states)
        self.birdid_check.stateChanged.connect(self._on_birdid_check_changed)
        params_layout.addLayout(header_layout)

        # 滑块区:锐度 + 美学(范围对齐设置中心精选页)/ Sliders aligned with culling page
        sliders_layout = QVBoxLayout()
        sliders_layout.setSpacing(16)

        sharp_layout = QHBoxLayout()
        sharp_layout.setSpacing(16)
        sharp_label = QLabel(self.i18n.t("labels.sharpness_short"))
        sharp_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        sharp_layout.addWidget(sharp_label)
        self.sharp_slider = QSlider(Qt.Orientation.Horizontal)
        self.sharp_slider.setRange(100, 600)
        self.sharp_slider.setSingleStep(10)
        self.sharp_slider.setPageStep(10)
        self.sharp_slider.setValue(int(cfg.min_sharpness))   # 初值在 connect 之前
        self.sharp_slider.valueChanged.connect(self._on_sharp_changed)
        sharp_layout.addWidget(self.sharp_slider)
        self.sharp_value = QLabel(str(int(cfg.min_sharpness)))
        self.sharp_value.setStyleSheet(VALUE_STYLE)
        self.sharp_value.setFixedWidth(50)
        self.sharp_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sharp_layout.addWidget(self.sharp_value)
        sliders_layout.addLayout(sharp_layout)

        nima_layout = QHBoxLayout()
        nima_layout.setSpacing(16)
        nima_label = QLabel(self.i18n.t("labels.aesthetics"))
        nima_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        nima_layout.addWidget(nima_label)
        self.nima_slider = QSlider(Qt.Orientation.Horizontal)
        self.nima_slider.setRange(0, 70)
        self.nima_slider.setValue(int(round(cfg.min_nima * 10)))   # 初值在 connect 之前
        self.nima_slider.valueChanged.connect(self._on_nima_changed)
        nima_layout.addWidget(self.nima_slider)
        self.nima_value = QLabel(f"{cfg.min_nima:.1f}")
        self.nima_value.setStyleSheet(VALUE_STYLE)
        self.nima_value.setFixedWidth(50)
        self.nima_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        nima_layout.addWidget(self.nima_value)
        sliders_layout.addLayout(nima_layout)

        # V4.6+三段配额:「星级配额分配条」QuotaBar(批内相对评星)取代原单一
        # 3星配额滑块——一条 3★/2★/1★ 三段条,拖分隔点即改配额(和恒 100%,1★ 为
        # 余量)。v2 下只显示此条,旧阈值滑块隐藏(仍构建,供 v1 回滚与既有测试)。
        # 约束与 set_custom_quota3/2 clamp 一致(SSOT),与设置中心精选页双向同步。
        # V4.6 + 3-seg quota: the QuotaBar (3★/2★/1★ split) replaces the single
        # 3-star quota slider; ranges match the setter clamps (SSOT), two-way
        # synced with the Settings Center culling page.
        from core.rating_quota import get_quota3_for_skill, get_quota2_for_skill
        from ui.quota_bar import QuotaBar
        quota_layout = QHBoxLayout()
        quota_layout.setSpacing(16)
        quota_label = QLabel(self.i18n.t("labels.quota_split_short"))
        quota_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        quota_layout.addWidget(quota_label)
        self.quota_bar = QuotaBar(
            int(get_quota3_for_skill(cfg.skill_level, cfg)),
            int(get_quota2_for_skill(cfg.skill_level, cfg)),
        )
        self.quota_bar.quotasChanged.connect(self._on_quota_changed)
        quota_layout.addWidget(self.quota_bar, 1)
        sliders_layout.addLayout(quota_layout)

        # V4.6(rating-v2/UI): 行控件存实例引用,供设置中心改算法后运行时切换
        # V4.6 (rating-v2/UI): keep row-widget refs so visibility can be
        # re-applied after the Settings Center changes the algorithm.
        self._sharp_row_widgets = (sharp_label, self.sharp_slider, self.sharp_value)
        self._nima_row_widgets = (nima_label, self.nima_slider, self.nima_value)
        self._quota_row_widgets = (quota_label, self.quota_bar)
        self._apply_algo_visibility()

        params_layout.addLayout(sliders_layout)
        parent_layout.addWidget(params_frame)

    def _on_sharp_changed(self, value):
        """锐度滑块:更新显示 + 写 advanced_config(转自定义档,与设置中心协同)。"""
        self.sharp_value.setText(str(value))
        if getattr(self, "_params_loading", False):
            return
        self.config.set_min_sharpness(value)
        self._mark_custom_skill()

    def _on_nima_changed(self, value):
        """美学滑块:更新显示(value/10=NIMA)+ 写 advanced_config(转自定义档)。"""
        self.nima_value.setText(f"{value / 10.0:.1f}")
        if getattr(self, "_params_loading", False):
            return
        self.config.set_min_nima(value / 10.0)
        self._mark_custom_skill()

    def _on_quota_changed(self, q3, q2):
        """
        星级配额分配条(V2):写 custom_quota3/quota2 并转自定义档。

        1★ = 100 − q3 − q2 为算术余量,不单独存储。QuotaBar 拖动才发信号,
        程序化 set_quotas 不触发,故无需额外 loading 守卫即可避免回环;仍保留
        _params_loading 判断与其它滑块一致。

        Star quota split (V2): persist custom_quota3/quota2 and switch the skill
        level to "custom" (1★ is the derived remainder). Consistent with the
        Settings Center culling page.
        """
        if getattr(self, "_params_loading", False):
            return
        cfg = self.config
        cfg.set_custom_quota3(q3)
        cfg.set_custom_quota2(q2)
        if cfg.skill_level != "custom":
            cfg.set_skill_level("custom")
        self._refresh_skill_chip()

    def _mark_custom_skill(self):
        """手动改阈值 → 技能档转「自定义」并同步 custom_*,与设置中心精选页协同一致。"""
        cfg = self.config
        cfg.set_custom_sharpness(self.sharp_slider.value())
        cfg.set_custom_aesthetics(self.nima_slider.value() / 10.0)
        if cfg.skill_level != "custom":
            cfg.set_skill_level("custom")
        self._refresh_skill_chip()

    def _save_check_states(self, *_):
        """飞行/连拍开关 → 写 advanced_config。"""
        if getattr(self, "_params_loading", False):
            return
        cfg = self.config
        cfg.config["flight_check"] = bool(self.flight_check.isChecked())
        cfg.config["burst_check"] = bool(self.burst_check.isChecked())
        cfg.save()

    def _on_birdid_check_changed(self, *_):
        """识鸟开关 → 写 advanced_config.birdid_auto_identify。"""
        if getattr(self, "_params_loading", False):
            return
        self.config.set_birdid_auto_identify(bool(self.birdid_check.isChecked()))

    def _on_video_check_changed(self, *_):
        """视频开关 → 写 advanced_config.video_auto_process_in_main。"""
        if getattr(self, "_params_loading", False):
            return
        self.config.set_video_auto_process_in_main(bool(self.video_check.isChecked()))

    def _apply_algo_visibility(self):
        """
        按 advanced_config.rating_algorithm 切换首页两组滑块可见性：
        v2 显示「3星配额」行，v1 显示锐度/美学行，并同步 _rating_v2_ui。

        Toggle the home quick-panel slider groups by rating_algorithm:
        quota row under v2, legacy rows under v1; refresh _rating_v2_ui.
        """
        self._rating_v2_ui = self.config.rating_algorithm == "v2"
        for w in self._quota_row_widgets:
            w.setVisible(self._rating_v2_ui)
        for w in self._sharp_row_widgets + self._nima_row_widgets:
            w.setVisible(not self._rating_v2_ui)

    def _refresh_param_panel(self):
        """设置中心关闭后,从 advanced_config 刷新首页参数控件,保持两处一致(loading 守卫避免回写)。"""
        if not hasattr(self, "sharp_slider"):
            return
        cfg = self.config
        self._params_loading = True
        try:
            self.sharp_slider.setValue(int(cfg.min_sharpness))
            self.nima_slider.setValue(int(round(cfg.min_nima * 10)))
            self.flight_check.setChecked(bool(cfg.flight_check))
            self.burst_check.setChecked(bool(cfg.burst_check))
            self.birdid_check.setChecked(bool(cfg.birdid_auto_identify))
            self.sharp_value.setText(str(int(cfg.min_sharpness)))
            self.nima_value.setText(f"{cfg.min_nima:.1f}")
            # V4.6+三段配额: 同步配额分配条(设置中心改过技能档/配额后刷新);
            # set_quotas 不发信号,不会回写 config,天然避免回环。
            # V4.6 + 3-seg quota: refresh the QuotaBar after Settings Center edits
            # (set_quotas emits nothing, so no write-back loop).
            if hasattr(self, "quota_bar"):
                from core.rating_quota import (
                    get_quota3_for_skill, get_quota2_for_skill)
                self.quota_bar.set_quotas(
                    int(get_quota3_for_skill(cfg.skill_level, cfg)),
                    int(get_quota2_for_skill(cfg.skill_level, cfg)))
            # V4.6(rating-v2/UI): 设置中心可能改了评星算法 → 重应用滑块可见性
            # V4.6 (rating-v2/UI): the Settings Center may have switched the
            # rating algorithm — re-apply slider-row visibility.
            if hasattr(self, "_quota_row_widgets"):
                self._apply_algo_visibility()
        finally:
            self._params_loading = False

    @Slot()
    def _toggle_birdid_dock(self, checked):
        """显示/隐藏识鸟停靠面板"""
        if hasattr(self, 'birdid_dock'):
            self.birdid_dock.setVisible(checked)



    def _auto_start_birdid_server(self):
        """自动启动识鸟 API 服务器（使用服务器管理器） - 在后台线程中运行"""
        if not self._skip_until_initialized("首次初始化尚未完成，暂不启动识鸟 API 服务器。"):
            return

        import threading
        
        def start_server_task():
            try:
                from server_manager import get_server_status, start_server_daemon, start_server_thread

                # 检查是否已有服务器在运行
                status = get_server_status()
                if status['healthy']:
                    self.log_signal.emit(self.i18n.t("server.api_reused"), "success")
                    return

                # pythonw.exe 无控制台窗口，subprocess 会报错，改用线程模式
                use_thread_mode = (
                    sys.platform == "win32"
                    and not getattr(sys, "frozen", False)
                    and os.path.basename(sys.executable).lower() == "pythonw.exe"
                )

                if use_thread_mode:
                    success, msg, pid = start_server_thread()
                else:
                    # 启动服务器（守护进程模式）
                    success, msg, pid = start_server_daemon(log_callback=lambda m: print(m))

                if success:
                    self.log_signal.emit(self.i18n.t("server.api_auto_started", port=5156), "success")
                else:
                    self.log_signal.emit(self.i18n.t("server.start_failed", error=msg), "warning")
                    
            except Exception as e:
                self.log_signal.emit(self.i18n.t("server.start_failed", error=str(e)), "warning")
        
        # 在后台线程中启动服务器，不阻塞UI
        thread = threading.Thread(target=start_server_task, daemon=True)
        thread.start()

    def _stop_birdid_server(self):
        """停止识鸟 API 服务器（使用服务器管理器）"""
        try:
            from server_manager import stop_server
            success, msg = stop_server()
            if success:
                self._log(self.i18n.t("server.api_stopped"), "info")
            else:
                self._log(f"停止服务器失败: {msg}", "warning")
        except Exception as e:
            self._log(f"停止服务器异常: {e}", "error")

    # ========== 辅助方法 ==========

    def _log(self, message, tag=None):
        """输出日志"""
        from datetime import datetime
        
        # 线程安全检查：如果在非主线程中调用，通过信号发送（修复 preloading_models 导致的 Crash）
        # tag 可能是 None，但 Signal(str, str) 不接受 None，所以转为空字符串
        if QThread.currentThread() != self.thread():
            self.log_signal.emit(message, tag if tag else "")
            return

        print(message)

        # 运行日志统一去除 emoji(横幅与「预加载完成」绿勾另行用 SVG 图标)
        message = _LOG_EMOJI_RE.sub("", message)

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # 根据标签选择颜色
        if tag == "error":
            color = LOG_COLORS['error']
        elif tag == "warning":
            color = LOG_COLORS['warning']
        elif tag in ("success", "success_check"):
            color = LOG_COLORS['success']
        elif tag == "info":
            color = LOG_COLORS['info']
        else:
            color = LOG_COLORS['default']

        # 时间戳
        timestamp = datetime.now().strftime("%H:%M:%S")
        time_color = LOG_COLORS['time']

        # success_check:仅「所有模型预加载完成」用绿勾 SVG(唯一保留的状态标记)
        icon_html = ""
        if tag == "success_check":
            _p = tinted_png_path("check.svg", LOG_COLORS['success'], 14)
            icon_html = f'<img src="{_p}" width="14" height="14" style="vertical-align:middle"> '

        # V3.9: 转义 HTML 特殊字符（防止 < > & 被解释为 HTML）
        import html

        # 构造正文 HTML：photo_good/species 做行内分段染色（只染关键信息那段）
        if tag == "photo_good":
            # 3星逐张：文件名之后（第一个 |）染绿，序号/文件名保持灰
            _d, _g = LOG_COLORS['default'], LOG_COLORS['photo_good']
            _parts = message.split("|", 1)
            if len(_parts) == 2:
                body_html = (f'<span style="color: {_d};">{html.escape(_parts[0])}</span>'
                             f'<span style="color: {_g};">|{html.escape(_parts[1])}</span>')
            else:
                body_html = f'<span style="color: {_g};">{html.escape(message)}</span>'
        elif tag == "species":
            # Bird ID：鸟名之后（第一个 :）染红，前缀保持灰
            _d, _r = LOG_COLORS['default'], LOG_COLORS['species']
            _parts = message.split(":", 1)
            if len(_parts) == 2:
                body_html = (f'<span style="color: {_d};">{html.escape(_parts[0])}:</span>'
                             f'<span style="color: {_r};">{html.escape(_parts[1])}</span>')
            else:
                body_html = f'<span style="color: {_r};">{html.escape(message)}</span>'
        else:
            _m = html.escape(message).replace('\n', '<br>')
            body_html = f'<span style="color: {color};">{_m}</span>'

        # 对于简短消息添加时间戳
        if len(message) < 100 and '\n' not in message:
            cursor.insertHtml(
                f'<span style="color: {time_color};">{timestamp}</span> '
                f'{icon_html}{body_html}<br>'
            )
        else:
            cursor.insertHtml(f'{icon_html}{body_html}<br>')

        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def _show_initial_help(self):
        """显示初始帮助信息(HTML:图标左对齐、同行图标与文字垂直居中)"""
        import html as _html
        from constants import APP_VERSION
        from ui.icon_utils import tinted_png_path
        from ui.styles import COLORS
        t = self.i18n.t

        gold = COLORS.get("star_gold", "#ffcc00")
        green = COLORS.get("focus_best", "#00cc44")   # 飞版绿
        red = "#ff5555"                                # 精焦红
        muted = COLORS.get("text_muted", "#8a8a8a")
        sec = COLORS.get("text_secondary", "#a1a1a1")
        pri = COLORS.get("text_primary", "#e0e0e0")
        accent = COLORS.get("accent", "#00d4aa")

        def ico(svg, color, size=14):
            p = tinted_png_path(svg, color, size)
            return f'<img src="{p}" width="{size}" height="{size}" style="vertical-align:middle"> '

        def esc(s):
            return _html.escape(str(s))

        line = "━" * 30
        pct = self.config.picked_top_percentage

        # 居中:分隔线 + 欢迎语 + 分隔线
        center_top = (
            f'<div align="center" style="color:{sec}">{line}<br>'
            f'<span style="color:{accent};font-weight:bold">'
            f'{esc(t("help.welcome_title", version=APP_VERSION))}</span>'
            f'<br>{line}</div>'
        )

        # 左对齐:使用步骤 + 评分规则(同一 div 内 <br> 分行,行距正常)
        rows = [f'<span style="color:{pri};font-weight:bold">{esc(t("help.usage_steps_title"))}</span>']
        for i, key in enumerate(("step1", "step2", "step3", "step4"), 1):
            rows.append(f'<span style="color:{sec}">&nbsp;&nbsp;{i}. {esc(t("help." + key))}</span>')
        rows.append('')
        rows.append(f'<span style="color:{pri};font-weight:bold">{esc(t("help.rating_rules_title"))}</span>')
        rules = [
            (ico("star.svg", gold) * 3, esc(t("help.rule_3_star"))),
            ('&nbsp;&nbsp;&nbsp;&nbsp;' + ico("crown.svg", gold),
             esc(t("help.rule_picked", percentage=pct))),
            (ico("star.svg", gold) * 2, esc(t("help.rule_2_star"))),
            (ico("star.svg", gold), esc(t("help.rule_1_star"))),
            (ico("circle-off.svg", muted), esc(t("help.rule_0_star"))),
            (ico("bird.svg", green), esc(t("help.rule_flying"))),
            (ico("scan-eye.svg", red), esc(t("help.rule_focus"))),
            (ico("square-stack.svg", sec), esc(t("help.burst_info"))),
        ]
        for ic, txt in rules:
            rows.append(f'<span style="color:{sec}">&nbsp;&nbsp;{ic}{txt}</span>')
        left_block = '<div align="left">' + '<br>'.join(rows) + '</div>'

        center_bottom = f'<div align="left" style="color:{accent}">{esc(t("help.ready"))}</div>'

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(center_top + '<div>&nbsp;</div>' + left_block
                          + '<div>&nbsp;</div>' + center_bottom + '<br>')
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def _show_statistics_report(self, stats):
        """统计报告(HTML 渲染:评级用星/冠 SVG、飞版/精焦用对应 SVG、鸟种红色)"""
        import html as _html
        from ui.icon_utils import tinted_png_path
        from ui.styles import COLORS
        t = self.i18n.t

        gold = COLORS.get("star_gold", "#ffcc00")
        green = COLORS.get("focus_best", "#00cc44")   # 飞版绿
        red = "#ff5555"                                # 精焦红 / 鸟种红
        muted = COLORS.get("text_muted", "#8a8a8a")
        sec = COLORS.get("text_secondary", "#a1a1a1")
        accent = COLORS.get("accent", "#00d4aa")

        def ico(svg, color, size=14):
            p = tinted_png_path(svg, color, size)
            return f'<img src="{p}" width="{size}" height="{size}" style="vertical-align:middle"> '

        def esc(s):
            return _html.escape(str(s))

        total = stats.get('total', 0)
        star_3 = stats.get('star_3', 0)
        star_2 = stats.get('star_2', 0)
        star_1 = stats.get('star_1', 0)
        star_0 = stats.get('star_0', 0)
        no_bird = stats.get('no_bird', 0)
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time', 0)
        picked = stats.get('picked', 0)
        flying = stats.get('flying', 0)
        focus_precise = stats.get('focus_precise', 0)
        bird_total = star_3 + star_2 + star_1 + star_0

        line = "━" * 30
        head = (
            f'<div align="center" style="color:{sec}">{line}<br>'
            f'<span style="color:{accent};font-weight:bold">{esc(t("report.title"))}</span>'
            f'<br>{line}</div>'
        )

        # 开始/结束墙钟时间：用户可对表自验总耗时是否真实
        # Wall-clock start/end so users can verify the duration on a real clock
        start_clock = _format_wall_clock(stats.get('start_time', 0))
        end_clock = _format_wall_clock(stats.get('end_time', 0))

        rows = [
            f'<span style="color:{sec}">{esc(t("report.total_photos", total=total))}</span>',
            f'<span style="color:{sec}">{esc(t("report.start_at", time=start_clock))}</span>',
            f'<span style="color:{sec}">{esc(t("report.end_at", time=end_clock))}</span>',
            f'<span style="color:{sec}">{esc(t("report.total_time", time_sec=total_time, time_min=total_time/60))}</span>',
            f'<span style="color:{sec}">{esc(t("report.avg_time", avg=avg_time))}</span>',
            '',
        ]
        if total > 0:
            def pct(n):
                return f"{n/total*100:.1f}%"
            rows.append(f'<span style="color:{sec}">{ico("star.svg", gold)*3}{star_3} ({pct(star_3)})</span>')
            if picked > 0 and star_3 > 0:
                rows.append(f'<span style="color:{sec}">&nbsp;&nbsp;&nbsp;&nbsp;└─ {ico("crown.svg", gold)}{picked} ({picked/star_3*100:.0f}%)</span>')
            rows.append(f'<span style="color:{sec}">{ico("star.svg", gold)*2}{star_2} ({pct(star_2)})</span>')
            rows.append(f'<span style="color:{sec}">{ico("star.svg", gold)}{star_1} ({pct(star_1)})</span>')
            if star_0 > 0:
                rows.append(f'<span style="color:{sec}">{ico("star.svg", muted)}{star_0} ({pct(star_0)})</span>')
            rows.append(f'<span style="color:{sec}">{ico("circle-off.svg", muted)}{esc(t("browser.focus_no_bird"))} {no_bird} ({pct(no_bird)})</span>')
            rows.append('')
            rows.append(f'<span style="color:{sec}">{esc(t("report.bird_total", count=bird_total, percent=bird_total/total*100))}</span>')
            if flying > 0:
                rows.append(f'<span style="color:{sec}">{ico("bird.svg", green)}{esc(t("help.rule_flying"))}: {flying}</span>')
            if focus_precise > 0:
                rows.append(f'<span style="color:{sec}">{ico("scan-eye.svg", red)}{esc(t("help.rule_focus"))}: {focus_precise}</span>')

            # 识别鸟种(红色文字, language-aware)
            bird_species = stats.get('bird_species', [])
            if bird_species:
                from core.rarity_tier import tier_name_color
                is_chinese = self.i18n.current_lang.startswith('zh')
                parts = []  # 逐种按罕见度着色:常见默认/能见橙/少见以上红
                for sp in bird_species:
                    if isinstance(sp, dict):
                        name = sp.get('cn_name', '') if is_chinese else sp.get('en_name', '')
                        if not name:
                            name = sp.get('en_name', '') if is_chinese else sp.get('cn_name', '')
                        tier = sp.get('gbif_tier')
                    else:
                        name, tier = str(sp), None
                    if name:
                        c = tier_name_color(tier, default=sec)
                        parts.append(f'<span style="color:{c}">{esc(name)}</span>')
                if parts:
                    rows.append('')
                    _sentinel = "@@SPLIST@@"
                    _line = esc(t("logs.bird_species_identified", count=len(parts), species=_sentinel))
                    rows.append(f'<span style="color:{sec}">{_line.replace(_sentinel, ", ".join(parts))}</span>')

        body = '<div align="left">' + '<br>'.join(rows) + '</div>'
        tail = f'<div align="center" style="color:{sec}">{line}</div>'

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(head + '<div>&nbsp;</div>' + body + tail + '<br>')
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def _show_lightroom_guide(self):
        """显示 Lightroom 指南"""
        t = self.i18n.t
        guide = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  {t("lightroom_guide.title")}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{t("lightroom_guide.method1_title")}
  1. {t("lightroom_guide.method1_step1")}
  2. {t("lightroom_guide.method1_step2")}
  3. {t("lightroom_guide.method1_step3")}
  4. {t("lightroom_guide.method1_step4")}
  5. {t("lightroom_guide.method1_step5")}

{t("lightroom_guide.sort_title")}
  · {t("lightroom_guide.sort_step3_city")}
  · {t("lightroom_guide.sort_step3_state")}
  · {t("lightroom_guide.field_caption")}

{t("lightroom_guide.debug_title")}
  {t("lightroom_guide.debug_tip")}
  · {t("lightroom_guide.debug_explain1")}
  · {t("lightroom_guide.debug_explain2")}
  · {t("lightroom_guide.debug_explain3")}
  · {t("lightroom_guide.debug_explain4")}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        self._log(guide)

    def _play_completion_sound(self):
        """播放完成音效"""
        from advanced_config import get_advanced_config

        if not get_advanced_config().completion_sound_enabled:
            return

        sound_path = os.path.join(
            os.path.dirname(__file__), "..",
            "img", "toy-story-short-happy-audio-logo-short-cartoony-intro-outro-music-125627.mp3"
        )

        if os.path.exists(sound_path) and sys.platform == 'darwin':
            try:
                subprocess.Popen(
                    ['afplay', sound_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def closeEvent(self, event):
        """
        窗口关闭事件：默认隐藏到托盘驻留，而不是退出进程。

        真驻留修复：打包版识鸟 API 服务器运行在主进程线程里，进程一退
        Lightroom 插件就连不上 5156。因此关窗只隐藏窗口；真正退出统一走
        托盘"完全退出"（_quit_app）。系统注销/关机时不拦截，托盘不可用时
        回退为旧的退出行为。

        Close event: hide to tray by default instead of quitting. In packaged
        builds the BirdID API server is a thread of this process, so quitting
        kills the port the Lightroom plugin depends on. Real quit goes through
        the tray "Quit" action (_quit_app). Session shutdown is never blocked,
        and we fall back to the old quit behavior when no tray is available.
        """
        app = QApplication.instance()
        saving_session = bool(app is not None and app.isSavingSession())

        if (
            not getattr(self, '_really_quit', False)
            and not saving_session
            and self._tray_resident_available()
        ):
            # 隐藏进托盘前保存窗口位置——此刻窗口仍可见，几何信息是准确的；
            # 之后从托盘重新显示时会复位保存标志（见 _show_main_window）。
            # Save placement before hiding to tray — the window is still
            # visible so geometry is accurate; the saved flag is reset when
            # the window is shown again (see _show_main_window).
            self._save_main_window_placement()
            event.ignore()
            self._hide_to_tray()
            return

        # 兜底退出路径（托盘不可用 / 系统注销）：维持原有确认与清理逻辑
        # Fallback quit path (no tray / session shutdown): keep old confirm + cleanup
        if self.worker and self.worker.is_alive() and not saving_session:
            reply = StyledMessageBox.question(
                self,
                self.i18n.t("messages.exit_title"),
                self.i18n.t("messages.exit_confirm"),
                yes_text=self.i18n.t("buttons.cancel"),
                no_text=self.i18n.t("labels.yes")
            )

            if reply == StyledMessageBox.No:  # 用户点击"是"退出
                self.worker.request_stop()
                self.worker._stop_caffeinate()  # V3.8.1: 确保终止 caffeinate 进程
                self._stop_birdid_server()  # V4.0: 停止识鸟 API 服务
                self._force_quit()          # 已确认过，不再二次弹窗 / already confirmed
                event.accept()
            else:
                event.ignore()
        else:
            self._save_main_window_placement()
            QApplication.quit()           # 触发 aboutToQuit → _cleanup_on_quit
            event.accept()

    # ========== V4.2: 模型预加载功能 ==========

    def _preload_all_models(self):
        """后台预加载所有AI模型（不阻塞UI）"""
        if not self._skip_until_initialized("首次初始化尚未完成，跳过模型预加载。"):
            return

        import threading

        def _emit_and_log(msg, level="info"):
            """同时发送到 UI 和 superpicky.log"""
            self.log_signal.emit(msg, level)
            try:
                from tools.utils import log_message
                from tools.utils import get_active_log_directory
                d = get_active_log_directory()
                if d:
                    log_message(msg, d, file_only=True)
            except Exception:
                pass

        def preload_task():
            # RAM 检查（psutil 可选依赖，缺失时跳过）
            try:
                import psutil
                vm = psutil.virtual_memory()
                free_gb = vm.available / (1024 ** 3)
                if free_gb < 4.0:
                    _emit_and_log(
                        self.i18n.t("health.ram_low", free=f"{free_gb:.1f}"),
                        "warning",
                    )
                else:
                    _emit_and_log(
                        self.i18n.t("health.ram_ok", free=f"{free_gb:.1f}"),
                        "info",
                    )
            except ImportError:
                pass  # psutil 未安装，跳过 RAM 检查

            _emit_and_log(self.i18n.t("preload.preloading_models"), "info")
            results = []

            # 1. YOLO 检测模型
            try:
                from ai_model import load_yolo_model
                load_yolo_model(log_callback=lambda msg, tag="info": self.log_signal.emit(msg, tag))
                self.log_signal.emit(self.i18n.t("preload.yolo_loaded"), "success")
                results.append(("YOLO", True, None))
            except Exception as e:
                self.log_signal.emit(self.i18n.t("preload.preload_failed", error=f"YOLO: {e}"), "warning")
                results.append(("YOLO", False, str(e)))

            # 2. 关键点检测模型
            try:
                from core.keypoint_detector import get_keypoint_detector
                get_keypoint_detector().load_model()
                self.log_signal.emit(self.i18n.t("preload.keypoint_loaded"), "success")
                results.append(("Keypoint", True, None))
            except Exception as e:
                self.log_signal.emit(self.i18n.t("preload.preload_failed", error=f"Keypoint: {e}"), "warning")
                results.append(("Keypoint", False, str(e)))

            # 3. 飞版检测模型
            try:
                from core.flight_detector import get_flight_detector
                get_flight_detector().load_model()
                self.log_signal.emit(self.i18n.t("preload.flight_loaded"), "success")
                results.append(("Flight", True, None))
            except Exception as e:
                self.log_signal.emit(self.i18n.t("preload.preload_failed", error=f"Flight: {e}"), "warning")
                results.append(("Flight", False, str(e)))

            # 4. IQA/TOPIQ 美学评分模型
            try:
                from config import get_best_device
                from iqa_scorer import get_iqa_scorer
                device = get_best_device()
                self.log_signal.emit(self.i18n.t("preload.iqa_loading", device=device.type), "info")
                # 真正加载 TOPIQ 权重(而非仅创建评分器对象),使其在启动时即就绪,
                # 之后裁剪建议/选鸟复用同一已热实例,不再触发现加载。
                # Actually load the TOPIQ weights now (not just create the scorer object),
                # so crop advisor / bird selection reuse the warm singleton later.
                get_iqa_scorer(device=device.type).preload()
                self.log_signal.emit(self.i18n.t("preload.iqa_loaded"), "success")
                results.append(("IQA", True, None))
            except Exception as e:
                self.log_signal.emit(self.i18n.t("preload.preload_failed", error=f"IQA: {e}"), "warning")
                results.append(("IQA", False, str(e)))

            # 5. 识鸟模型
            try:
                from birdid.bird_identifier import get_classifier
                get_classifier()
                self.log_signal.emit(self.i18n.t("preload.birdid_loaded"), "success")
                results.append(("BirdID", True, None))
            except Exception as e:
                self.log_signal.emit(self.i18n.t("preload.preload_failed", error=f"BirdID: {e}"), "warning")
                results.append(("BirdID", False, str(e)))

            # 汇总：GUI 只显示一行结论，详情写入日志文件
            ok_names = [name for name, s, _ in results if s]
            fail_items = [(name, err) for name, s, err in results if not s]
            summary_lines = ["[Preload Summary]"]
            for name in ok_names:
                summary_lines.append(f"  ✅ {name}")
            for name, err in fail_items:
                summary_lines.append(f"  ❌ {name}: {err}")
            try:
                from tools.utils import log_message, get_active_log_directory
                d = get_active_log_directory()
                if d:
                    log_message("\n".join(summary_lines), d, file_only=True)
            except Exception:
                pass

            if not fail_items:
                self.log_signal.emit(self.i18n.t("preload.preload_complete"), "success_check")
            else:
                failed_str = ", ".join(name for name, _ in fail_items)
                self.log_signal.emit(
                    self.i18n.t("preload.preload_complete_with_errors", failed=failed_str),
                    "warning"
                )
            self._preload_done = True

        thread = threading.Thread(target=preload_task, daemon=True)
        thread.start()

    # ========== V4.0.1: 更新检测功能 ==========

    def _show_update_center(self):
        """显示版本信息对话框：展示当前版本/渠道 + 前往官网下载入口。

        4.3.0 起应用内在线更新检测已停用（见 tools/update_checker.ONLINE_UPDATE_CHECK_DISABLED）；
        升级一律前往官网手动下载，故此对话框不再提供检查/自动更新设置。
        Show a version-info dialog linking to the official download page. Online
        update checking is disabled since 4.3.0, so upgrades go through the website.
        """
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                       QLabel, QPushButton, QFrame)
        from tools.update_checker import get_version_channel
        from constants import APP_VERSION
        import webbrowser

        # 读取实际渠道（优先 build_info.RELEASE_CHANNEL）
        try:
            from core.build_info import RELEASE_CHANNEL as _rc
            channel = _rc if _rc in ('nightly', 'official') else get_version_channel(APP_VERSION)
        except Exception:
            channel = get_version_channel(APP_VERSION)

        # 官网下载页（升级走网页手动下载）
        try:
            from config import config as _cfg
            download_page = _cfg.endpoints.UPDATE_DOWNLOAD_PAGE
        except Exception:
            # 官网域名是 superpicky.app；旧的 superpicky.jamesphotography.com.au
            # 已无 DNS 记录，勿再写回。/ The legacy host no longer resolves.
            download_page = "https://superpicky.app/#download"

        dialog = QDialog(self)
        dialog.setWindowTitle(self.i18n.t("update.update_center_title"))
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {COLORS['bg_primary']}; }}
            QLabel  {{ color: {COLORS['text_primary']}; font-size: 13px; }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        # ── 標題 ──────────────────────────────────
        title = QLabel(self.i18n.t("update.update_center_title"))
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {COLORS['text_primary']};")
        layout.addWidget(title)

        # ── 版本信息區 ─────────────────────────────
        info_frame = QFrame()
        info_frame.setStyleSheet(f"background-color: {COLORS['bg_elevated']}; border-radius: 8px;")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(8)

        def _row(label_text, value_text, value_color=None):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
            row.addWidget(lbl)
            row.addStretch()
            val = QLabel(value_text)
            color = value_color or COLORS['text_primary']
            val.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 500;")
            row.addWidget(val)
            info_layout.addLayout(row)
            return val

        # 版本 + 渠道（只读展示；升级走官网手动下载）
        channel_text = {
            'official': self.i18n.t("update.update_center_channel_official"),
            'nightly': self.i18n.t("update.update_center_channel_nightly"),
            'dev': self.i18n.t("update.update_center_channel_dev"),
        }.get(channel, channel)
        _row(self.i18n.t("update.current_version_label"), f"V{APP_VERSION}")
        _row(self.i18n.t("update.update_center_channel_label"), channel_text)

        layout.addWidget(info_frame)

        # ── 下载指引 ───────────────────────────────
        hint = QLabel(self.i18n.t("update.download_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
        layout.addWidget(hint)

        # ── 按鈕行 ─────────────────────────────────
        btn_row = QHBoxLayout()

        btn_style_primary = f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none; border-radius: 6px;
                padding: 9px 18px; font-size: 13px; font-weight: 500;
            }}
            QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}
            QPushButton:disabled {{ background-color: {COLORS['bg_card']}; color: {COLORS['text_muted']}; }}
        """
        btn_style_secondary = f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                color: {COLORS['text_secondary']};
                border-radius: 6px; padding: 9px 18px; font-size: 13px;
            }}
            QPushButton:hover {{ border-color: {COLORS['text_muted']}; color: {COLORS['text_primary']}; }}
        """

        visit_btn = QPushButton(self.i18n.t("update.update_center_btn_visit_site"))
        visit_btn.setStyleSheet(btn_style_primary)
        visit_btn.clicked.connect(lambda: webbrowser.open(download_page))
        btn_row.addWidget(visit_btn)

        btn_row.addStretch()

        close_btn = QPushButton(self.i18n.t("update.close"))
        close_btn.setStyleSheet(btn_style_secondary)
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)
        dialog.exec()

    def _show_environment_repair_dialog(self):
        """显示环境修复对话框，复用初始化修复逻辑但不走首启欢迎页。"""
        dialog = EnvironmentRepairDialog(self.i18n, self.config, self)
        dialog.start_repair()
        dialog.exec()

    def _check_for_updates(self, silent=False):
        """检查更新
        
        Args:
            silent: 如果为 True，只在有更新时显示弹窗（用于启动时自动检查）
        """
        import threading
        
        if not silent:
            self._log(self.i18n.t("update.checking"), "info")
        
        def _do_check():
            try:
                from tools.update_checker import UpdateChecker
                from advanced_config import get_advanced_config as _get_cfg
                _cfg = _get_cfg()
                checker = UpdateChecker()
                has_update, update_info = checker.check_for_updates(
                    include_prerelease=_cfg.include_prerelease
                )
                # 静默模式下，只有有更新或应用了补丁时才弹窗
                if silent and not has_update:
                    if not (update_info and update_info.get('patch_applied')):
                        return

                # 静默模式：跳过用户已选择忽略的版本
                if silent and has_update and update_info:
                    latest = update_info.get('version', '')
                    if latest and latest == _cfg.ignored_update_version:
                        return

                # 使用信号发送到主线程
                self._update_signals.update_check_done.emit(has_update, update_info)
            except Exception as e:
                import traceback
                print(f"⚠️ 更新检测失败: {e}")
                traceback.print_exc()
                # 静默模式下不显示错误
                if not silent:
                    error_info = {'error': str(e), 'current_version': '4.0.0', 'version': '检查失败'}
                    self._update_signals.update_check_done.emit(False, error_info)
        
        # 在后台线程执行
        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()

    def _show_update_result_dialog(self, has_update: bool, update_info):
        """显示更新检测结果对话框"""
        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
            import webbrowser
            
            dialog = QDialog(self)
            dialog.setWindowTitle(self.i18n.t("update.window_title"))
            dialog.setMinimumWidth(420)
            dialog.setStyleSheet(f"""
                QDialog {{
                    background-color: {COLORS['bg_primary']};
                }}
                QLabel {{
                    color: {COLORS['text_primary']};
                    font-size: 13px;
                }}
            """)
            
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            
            # 获取版本信息
            current_version = update_info.get('current_version', '4.0.0') if update_info else '4.0.0'
            latest_version = update_info.get('version', '未知') if update_info else '未知'
            has_error = update_info.get('error') if update_info else None
            patch_applied = update_info.get('patch_applied', False) if update_info else False
            patch_version = update_info.get('patch_version') if update_info else None

            # 补丁模式：无整包更新但应用了热补丁
            if not has_update and not has_error and patch_applied:
                title = QLabel(self.i18n.t("update.patch_applied_title"))
                title.setStyleSheet(f"color: {COLORS['accent']}; font-size: 18px; font-weight: 600;")
                layout.addWidget(title)
                layout.addSpacing(4)

                info_frame = QFrame()
                info_frame.setStyleSheet(f"background-color: {COLORS['bg_elevated']}; border-radius: 8px;")
                info_layout = QVBoxLayout(info_frame)
                info_layout.setContentsMargins(16, 12, 16, 12)
                info_layout.setSpacing(8)

                cur_row = QHBoxLayout()
                cur_label = QLabel(self.i18n.t("update.current_version_label"))
                cur_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
                cur_row.addWidget(cur_label)
                cur_row.addStretch()
                cur_val = QLabel(f"V{current_version}")
                cur_val.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 500;")
                cur_row.addWidget(cur_val)
                info_layout.addLayout(cur_row)

                if patch_version:
                    pv_row = QHBoxLayout()
                    pv_label = QLabel(self.i18n.t("update.patch_version_label"))
                    pv_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
                    pv_row.addWidget(pv_label)
                    pv_row.addStretch()
                    pv_val = QLabel(patch_version)
                    pv_val.setStyleSheet(f"color: {COLORS['accent']}; font-size: 13px; font-weight: 600;")
                    pv_row.addWidget(pv_val)
                    info_layout.addLayout(pv_row)

                layout.addWidget(info_frame)

                hint = QLabel(self.i18n.t("update.patch_restart_hint"))
                hint.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 12px;")
                hint.setWordWrap(True)
                layout.addWidget(hint)

                layout.addSpacing(8)
                btn_row = QHBoxLayout()
                btn_row.addStretch()

                restart_btn = QPushButton(self.i18n.t("update.restart_now"))
                restart_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLORS['accent']};
                        color: {COLORS['bg_void']};
                        border: none;
                        border-radius: 6px;
                        padding: 10px 20px;
                        font-size: 13px;
                        font-weight: 500;
                    }}
                    QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}
                """)
                from PySide6.QtWidgets import QApplication
                def _restart_app():
                    dialog.accept()
                    app = QApplication.instance()
                    if app is not None:
                        app.quit()
                restart_btn.clicked.connect(_restart_app)
                btn_row.addWidget(restart_btn)

                btn_row.addSpacing(8)
                close_btn2 = QPushButton(self.i18n.t("update.close"))
                close_btn2.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLORS['bg_card']};
                        border: 1px solid {COLORS['border']};
                        color: {COLORS['text_secondary']};
                        border-radius: 6px;
                        padding: 10px 20px;
                        font-size: 13px;
                    }}
                    QPushButton:hover {{
                        border-color: {COLORS['text_muted']};
                        color: {COLORS['text_primary']};
                    }}
                """)
                close_btn2.clicked.connect(dialog.accept)
                btn_row.addWidget(close_btn2)
                layout.addLayout(btn_row)

                dialog.exec()
                return

            if has_error:
                title = QLabel(self.i18n.t("update.check_failed_title"))
                title.setStyleSheet(f"color: {COLORS['warning']}; font-size: 18px; font-weight: 600;")
            elif has_update:
                title = QLabel(self.i18n.t("update.new_version_found"))
                title.setStyleSheet(f"color: {COLORS['accent']}; font-size: 18px; font-weight: 600;")
            else:
                title = QLabel(self.i18n.t("update.up_to_date_title"))
                title.setStyleSheet(f"color: {COLORS['success']}; font-size: 18px; font-weight: 600;")
            layout.addWidget(title)
            
            layout.addSpacing(4)
            
            # 版本信息区域
            version_frame = QFrame()
            version_frame.setStyleSheet(f"background-color: {COLORS['bg_elevated']}; border-radius: 8px;")
            version_layout = QVBoxLayout(version_frame)
            version_layout.setContentsMargins(16, 12, 16, 12)
            version_layout.setSpacing(8)
            
            # 当前版本
            current_row = QHBoxLayout()
            current_label = QLabel(self.i18n.t("update.current_version_label"))
            current_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
            current_row.addWidget(current_label)
            current_row.addStretch()
            current_value = QLabel(f"V{current_version}")
            current_value.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 500;")
            current_row.addWidget(current_value)
            version_layout.addLayout(current_row)
            
            # 发布版本
            latest_row = QHBoxLayout()
            latest_label = QLabel(self.i18n.t("update.latest_version_label"))
            latest_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px;")
            latest_row.addWidget(latest_label)
            latest_row.addStretch()
            latest_value = QLabel(f"V{latest_version}")
            if has_update:
                latest_value.setStyleSheet(f"color: {COLORS['accent']}; font-size: 13px; font-weight: 600;")
            else:
                latest_value.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 500;")
            latest_row.addWidget(latest_value)
            version_layout.addLayout(latest_row)
            
            layout.addWidget(version_frame)
            
            # 提示和下载按钮
            if not has_error:
                download_url = app_config.endpoints.UPDATE_DOWNLOAD_PAGE

                # 选当前平台对应的 installer asset；选到了用"下载并安装"流程，
                # 选不到 fallback 到打开下载页（旧行为）。
                # Pick the installer asset for this platform. When found we use
                # the in-app download/install flow; otherwise fall back to the
                # legacy "open browser to download page" buttons.
                from tools.installer_updater import select_installer_asset
                assets = (update_info or {}).get('assets', [])
                installer_asset = select_installer_asset(assets) if assets else None

                btn_frame = QFrame()
                btn_frame.setStyleSheet(f"background-color: {COLORS['bg_elevated']}; border-radius: 8px;")
                btn_layout = QHBoxLayout(btn_frame)
                btn_layout.setContentsMargins(16, 12, 16, 12)
                btn_layout.setSpacing(12)

                if installer_asset is not None:
                    install_btn = QPushButton(self.i18n.t("update.download_and_install"))
                    install_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {COLORS['accent']};
                            color: {COLORS['bg_void']};
                            border: none;
                            border-radius: 6px;
                            padding: 10px 16px;
                            font-size: 13px;
                            font-weight: 500;
                        }}
                        QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}
                    """)
                    install_btn.clicked.connect(
                        lambda: self._start_installer_update(
                            installer_asset, latest_version, dialog, download_url
                        )
                    )
                    btn_layout.addWidget(install_btn)

                    open_browser_btn = QPushButton(self.i18n.t("update.download_failed_open_browser"))
                    open_browser_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {COLORS['bg_card']};
                            border: 1px solid {COLORS['border']};
                            color: {COLORS['text_secondary']};
                            border-radius: 6px;
                            padding: 10px 16px;
                            font-size: 13px;
                        }}
                        QPushButton:hover {{
                            border-color: {COLORS['text_muted']};
                            color: {COLORS['text_primary']};
                        }}
                    """)
                    open_browser_btn.clicked.connect(lambda: webbrowser.open(download_url))
                    btn_layout.addWidget(open_browser_btn)
                else:
                    # 选不到 asset（如 Linux 或未来未知平台）：保留原来的"去官网"按钮
                    msg = QLabel(self.i18n.t("update.download_hint"))
                    msg.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 12px;")
                    layout.addWidget(msg)
                    layout.addSpacing(8)

                    mac_btn = QPushButton(self.i18n.t("update.mac_version"))
                    mac_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {COLORS['accent']};
                            color: {COLORS['bg_void']};
                            border: none;
                            border-radius: 6px;
                            padding: 10px 16px;
                            font-size: 13px;
                            font-weight: 500;
                        }}
                        QPushButton:hover {{ background-color: {COLORS['accent_hover']}; }}
                    """)
                    mac_btn.clicked.connect(lambda: webbrowser.open(download_url))
                    btn_layout.addWidget(mac_btn)

                    win_btn = QPushButton(self.i18n.t("update.windows_version"))
                    win_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: {COLORS['bg_card']};
                            border: 1px solid {COLORS['border']};
                            color: {COLORS['text_secondary']};
                            border-radius: 6px;
                            padding: 10px 16px;
                            font-size: 13px;
                            font-weight: 500;
                        }}
                        QPushButton:hover {{
                            border-color: {COLORS['text_muted']};
                            color: {COLORS['text_primary']};
                        }}
                    """)
                    win_btn.clicked.connect(lambda: webbrowser.open(download_url))
                    btn_layout.addWidget(win_btn)

                layout.addWidget(btn_frame)
            
            layout.addSpacing(8)

            # include_prerelease 勾选框（仅有更新时显示）
            if has_update:
                from PySide6.QtWidgets import QCheckBox
                from advanced_config import get_advanced_config as _get_cfg
                _cfg = _get_cfg()
                prerelease_cb = QCheckBox(self.i18n.t("update.include_prerelease"))
                prerelease_cb.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
                prerelease_cb.setChecked(_cfg.include_prerelease)
                def _on_prerelease_toggled(checked):
                    _c = _get_cfg()
                    _c.set_include_prerelease(checked)
                    _c.save()
                prerelease_cb.toggled.connect(_on_prerelease_toggled)
                layout.addWidget(prerelease_cb)
                layout.addSpacing(4)

            # 关闭 / 跳过此版本 按钮行
            close_layout = QHBoxLayout()
            close_layout.addStretch()

            if has_update and update_info:
                skip_btn = QPushButton(self.i18n.t("update.skip_version"))
                skip_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLORS['bg_card']};
                        border: 1px solid {COLORS['border']};
                        color: {COLORS['text_muted']};
                        border-radius: 6px;
                        padding: 8px 16px;
                        font-size: 13px;
                    }}
                    QPushButton:hover {{
                        border-color: {COLORS['text_muted']};
                        color: {COLORS['text_secondary']};
                    }}
                """)
                def _on_skip():
                    from advanced_config import get_advanced_config as _get_cfg
                    _cfg = _get_cfg()
                    _cfg.set_ignored_update_version(update_info.get('version', ''))
                    _cfg.save()
                    dialog.accept()
                skip_btn.clicked.connect(_on_skip)
                close_layout.addWidget(skip_btn)
                close_layout.addSpacing(8)

            close_btn = QPushButton(self.i18n.t("update.close"))
            close_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS['bg_card']};
                    border: 1px solid {COLORS['border']};
                    color: {COLORS['text_secondary']};
                    border-radius: 6px;
                    padding: 8px 24px;
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    border-color: {COLORS['text_muted']};
                    color: {COLORS['text_primary']};
                }}
            """)
            close_btn.clicked.connect(dialog.accept)
            close_layout.addWidget(close_btn)

            layout.addLayout(close_layout)

            dialog.exec()
            
        except Exception as e:
            import traceback
            print(f"[ERROR] 显示更新弹窗失败: {e}")
            traceback.print_exc()

    def _start_installer_update(self, asset, version: str, source_dialog, fallback_url: str):
        """启动"下载并安装"流程：下载进度 → 确认 → 触发安装包 → 退出主程序。

        Start the in-app installer update flow: progress dialog → confirm
        dialog → hand off to the OS installer and quit this application.

        Args:
            asset: 由 select_installer_asset 选出的 InstallerAsset。
            version: 新版本号字符串（用于 UI 文案）。
            source_dialog: 调用方的 update result dialog；触发流程前先关闭。
            fallback_url: 下载失败时退路（浏览器打开下载页）。
        """
        # 关掉触发本流程的更新检测对话框，避免它阻挡进度窗。
        try:
            source_dialog.accept()
        except Exception:
            pass

        from ui.installer_update_dialog import run_installer_update_flow
        from PySide6.QtWidgets import QApplication

        try:
            should_quit = run_installer_update_flow(
                asset,
                version,
                self.i18n,
                parent=self,
                fallback_browser_url=fallback_url,
            )
        except Exception as e:
            import traceback
            print(f"[ERROR] installer update flow 失败: {e}")
            traceback.print_exc()
            return

        if should_quit:
            # 用户已确认"立即安装"，installer 已启动；当前进程退出让出
            # 文件占用，确保 Windows Inno Setup / macOS DMG 替换不被挡。
            self._log(self.i18n.t("update.install_now"), "info")
            app = QApplication.instance()
            if app is not None:
                app.quit()

    # ========== V4.3: 摄影水平预设 ==========

    def _show_first_run_skill_level_dialog(
        self,
        *,
        auto_start_initialization: bool = False,
    ):
        """首次运行：显示轻量欢迎向导。"""
        if self._initialization_dialog_open or self._initialization_prompt_dismissed:
            return

        # Safety guard: onboarding 只允许作为首启流程出现。
        # 如果未来旧代码路径误调用这里，非首次运行时直接跳过，避免重复打断用户。
        # NOTE:
        # We intentionally keep this legacy entrypoint. The dialog now embeds
        # lightweight-package initialization, while full packages can still use
        # the same onboarding shell as a compatibility path.
        if not self.config.is_first_run and self._initialization_ready():
            return

        dialog = WelcomeOnboardingDialog(
            self.i18n,
            self,
            auto_start_initialization=auto_start_initialization,
        )
        dialog.onboarding_completed.connect(self._on_welcome_onboarding_completed)
        self._initialization_dialog_open = True
        result = QDialog.DialogCode.Rejected
        try:
            result = dialog.exec()
        finally:
            self._initialization_dialog_open = False

        if result == QDialog.DialogCode.Rejected and dialog.interrupted_by_user:
            self._initialization_prompt_dismissed = True
            QTimer.singleShot(0, QApplication.quit)

    def _initialization_ready(self) -> bool:
        return self._init_manager.is_ready_for_main_ui()

    def _skip_until_initialized(self, log_message: str) -> bool:
        if self._initialization_ready():
            return True
        self.log_signal.emit(log_message, "info")
        return False

    def _require_initialization_for_processing(self) -> bool:
        if self._initialization_ready():
            return True
        StyledMessageBox.warning(
            self,
            self.i18n.t("messages.hint"),
            self.i18n.t("messages.initialization_required"),
        )
        self._show_first_run_skill_level_dialog()
        return False

    def _resume_post_initialization_flow(self):
        """初始化完成后补触发被首启门禁跳过的后台流程。"""
        if not self._initialization_ready():
            return

        self.config = get_advanced_config()
        self._apply_skill_level_thresholds(self.config.skill_level)
        self._update_skill_level_label(self.config.skill_level)

        # 首次轻量初始化完成后，这些任务之前可能被跳过，这里补一次。
        QTimer.singleShot(200, self._preload_all_models)
        QTimer.singleShot(400, self._auto_start_birdid_server)

    def run_startup_prompts(self):
        """在启动统计同意流程结束后继续启动期弹窗/预设应用。"""
        if self._startup_prompts_ran:
            return

        # Centralized first-run gating: 所有首启提示都从这里统一进入。
        # 这样 telemetry / consent 完成后只会决策一次，避免 onboarding 被其他启动路径重复触发。
        self._startup_prompts_ran = True
        needs_init = self._init_manager.needs_initialization()
        resume_initialization = (
            needs_init
            and self.config.last_init_exit_reason == "interrupted"
            and self.config.last_init_mode == "init"
        )
        if (
            needs_init
            and not self.config.is_first_run
            and self.config.last_init_exit_reason == "interrupted"
            and self.config.last_init_mode == "repair"
        ):
            self._show_environment_repair_dialog()
            return
        if self.config.is_first_run or needs_init:
            self._show_first_run_skill_level_dialog(
                auto_start_initialization=resume_initialization,
            )
        else:
            # 非首次运行不再进入 onboarding，只恢复上次保存的摄影等级阈值。
            self._apply_skill_level_thresholds(self.config.skill_level)
    
    def _on_skill_level_selected(self, level_key: str):
        """处理水平选择"""
        # 保存设置
        self.config.set_skill_level(level_key)
        self.config.set_is_first_run(False)
        self.config.save()
        
        # 应用阈值到滑块
        self._apply_skill_level_thresholds(level_key)
        
        # 更新水平显示标签
        self._update_skill_level_label(level_key)
        
        print(self.i18n.t("logs.skill_level_selected", level=level_key))

    def _on_welcome_onboarding_completed(self, level_key: str, auto_update_enabled: bool):
        """处理首次启动欢迎向导完成。"""
        self._initialization_prompt_dismissed = False

        # Keep signal payload order stable: (level_key, auto_update_enabled)
        # 这里同时负责首启设置持久化与立即生效，避免状态已保存但主界面仍停留在旧阈值。
        self.config.set_skill_level(level_key)
        self.config.set_auto_check_updates(auto_update_enabled)
        self.config.set_is_first_run(False)
        self.config.set_initialization_completed(self._initialization_ready())
        self.config.save()

        self._apply_skill_level_thresholds(level_key)
        self._update_skill_level_label(level_key)
        self._resume_post_initialization_flow()

        print(
            f"[onboarding] first-run setup saved: "
            f"skill_level={level_key}, auto_check_updates={auto_update_enabled}"
        )
    
    def _apply_skill_level_thresholds(self, level_key: str):
        """应用水平预设的阈值到 advanced_config（参数面板已删除，不再写滑块）。

        Task 7: 锐度/美学阈值持久化到 advanced_config 而非 UI 控件。
        原来写 sharp_slider / nima_slider 的逻辑已移除。

        Apply skill-level preset thresholds to advanced_config (parameter panel
        removed; no more sliders to write to).

        Parameters:
            level_key (str): 技能等级键值 ("beginner"/"intermediate"/"master"/"custom")
        """
        sharpness, aesthetics = get_skill_level_thresholds(level_key, self.config)

        # Task 7: 持久化到 advanced_config，供下次 _start_processing 读取
        # Task 7: persist to advanced_config so next _start_processing picks it up
        _cfg = get_advanced_config()
        _cfg.set_min_sharpness(int(sharpness))
        _cfg.set_min_nima(aesthetics)
        _cfg.save()

        # 同步刷新主窗口的技能 chip 标签 / Refresh skill chip label on main window
        self._update_skill_level_label(level_key)

    def _update_skill_level_label(self, level_key: str):
        """更新主界面的水平显示标签"""
        if hasattr(self, 'skill_level_label'):
            level_names = {
                "beginner": self.i18n.t("skill_level.beginner"),
                "intermediate": self.i18n.t("skill_level.intermediate"),
                "master": self.i18n.t("skill_level.master"),
                "custom": self.i18n.t("skill_level.custom")
            }
            level_name = level_names.get(level_key, level_key)
            self.skill_level_label.setText(self.i18n.t("skill_level.current_label", level=level_name))
