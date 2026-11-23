#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI Processor - 命令行处理器
简化版 - 调用核心 PhotoProcessor
"""

import os
import signal
from threading import current_thread, main_thread
from types import FrameType
from typing import Dict, List, Optional

from advanced_config import get_advanced_config
from core.incremental_cull import scan_root_photos
from core.photo_processor import (
    PhotoProcessor,
    ProcessingCancelled,
    ProcessingSettings,
    ProcessingCallbacks,
    ProcessingResult
)
from .resume_state import ResumeStateManager
from .utils import log_message


class CLIResumeError(RuntimeError):
    """续跑检查失败，reason 用于本地化 / Resume rejected; reason is a translation suffix."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CLIProcessingPaused(RuntimeError):
    """处理在安全检查点停止 / Processing stopped at a cooperative cancellation point."""

    def __init__(self, checkpoint_available: bool):
        super().__init__("Photo processing interrupted")
        self.checkpoint_available = checkpoint_available


class _CLIResumeState(ResumeStateManager):
    """复用 GUI 进度文件，显式续跑不回退为新一轮 / Reuse GUI state without fresh-run fallback."""

    def __init__(self, directory: str):
        super().__init__(directory)
        self.active = False

    def _pending(self) -> List[str]:
        """验证进度文件并读取待处理前缀 / Validate the checkpoint and return pending stems."""
        if not self.exists():
            raise CLIResumeError("missing")
        state = self.load()
        if not state or state.get("version") != 1 or state.get("status") != "running":
            raise CLIResumeError("invalid")
        pending = state.get("pending_prefixes")
        total = state.get("total_files")
        if (not isinstance(pending, list) or type(total) is not int or total < len(pending)
                or not all(isinstance(p, str) and p for p in pending)):
            raise CLIResumeError("invalid")
        return pending

    def validate(self, available_prefixes: List[str]) -> None:
        """检查是否还有在场的待处理照片 / Reject a checkpoint with no available pending photo."""
        if not set(self._pending()).intersection(available_prefixes):
            raise CLIResumeError("no_pending")

    def get_resume_plan(self, available_prefixes: List[str]) -> Dict:
        """在 RAW 提取后再次验证；不让核心回退到全新处理 / Recheck after RAW extraction."""
        self.validate(available_prefixes)
        plan = super().get_resume_plan(available_prefixes)
        if not plan:
            raise CLIResumeError("no_pending")
        self.active = True
        return plan

    def start(self, ordered_prefixes: List[str]) -> bool:
        """记录这次运行真正启用的检查点 / Track whether this run initialized a checkpoint."""
        self.active = super().start(ordered_prefixes)
        return self.active

    def can_continue(self) -> bool:
        """不要声称旧状态或不可读的状态已保存 / Do not advertise stale or unreadable state."""
        try:
            return self.active and bool(self._pending())
        except CLIResumeError:
            return False


class CLIProcessor:
    """CLI 处理器 - 只负责命令行交互"""
    
    def __init__(
        self, 
        dir_path: str, 
        ui_settings: List = None, 
        verbose: bool = True, 
        detect_flight: bool = True,
        settings: ProcessingSettings = None  # V4.0: 直接传入完整设置
    ):
        """
        初始化处理器
        
        Args:
            dir_path: 处理目录
            ui_settings: [ai_confidence, sharpness_threshold, nima_threshold, save_crop, norm_mode] (向后兼容)
            verbose: 详细输出
            detect_flight: 是否启用飞鸟检测
            settings: 直接传入完整的 ProcessingSettings (优先使用)
        """
        self.verbose = verbose
        self.dir_path = dir_path  # 保存目录路径用于日志
        self._stop_requested = False
        
        # V4.0: 如果直接传入了 ProcessingSettings，使用它
        if settings is not None:
            pass  # 直接使用传入的 settings
        else:
            # 向后兼容：从 ui_settings 构建 ProcessingSettings
            # V3.9.4: 修正默认值，与 GUI 保持完全一致
            # GUI 默认: sharpness=400, nima=5.0, exposure=True, burst=True
            if ui_settings is None:
                ui_settings = [50, 400, 5.0, False, 'log_compression']
            
            # 转换为 ProcessingSettings
            settings = ProcessingSettings(
                ai_confidence=ui_settings[0],
                sharpness_threshold=ui_settings[1],
                nima_threshold=ui_settings[2],
                save_crop=ui_settings[3] if len(ui_settings) > 3 else False,
                normalization_mode=ui_settings[4] if len(ui_settings) > 4 else 'log_compression',
                detect_flight=detect_flight,
                detect_exposure=True,   # V3.9.4: 默认开启曝光检测，与 GUI 一致
                detect_burst=True       # V3.9.4: 默认开启连拍检测，与 GUI 一致
            )
        
        # 创建核心处理器
        self.processor = PhotoProcessor(
            dir_path=dir_path,
            settings=settings,
            callbacks=ProcessingCallbacks(
                log=self._log,
                progress=self._progress,
                should_stop=lambda: self._stop_requested,
            )
        )
        self._resume_state = _CLIResumeState(dir_path)
        self.processor.resume_state = self._resume_state
    
    def _log(self, msg: str, level: str = "info"):
        """日志回调 - 带颜色输出并写入文件"""
        if not self.verbose:
            return
        
        # ANSI颜色代码
        colors = {
            "success": "\033[92m",  # 绿色
            "error": "\033[91m",    # 红色
            "warning": "\033[93m",  # 黄色
            "info": "\033[94m",     # 蓝色
            "reset": "\033[0m"
        }
        
        color = colors.get(level, "")
        reset = colors["reset"] if color else ""
        
        # 输出到终端（带颜色）
        print(f"{color}{msg}{reset}")
        
        # 同时写入日志文件（不带颜色，不重复打印）
        log_message(msg, self.dir_path, file_only=True)
    
    def _progress(self, percent: int):
        """进度回调 - CLI可选"""
        # CLI 模式下可以选择是否显示进度
        # 目前不显示，避免输出过多
        pass

    def _available_stems(self) -> List[str]:
        """按核心格式筛选检查 JPEG 和待提取 RAW / Find eligible JPEG and RAW stems."""
        scan = scan_root_photos(
            self.dir_path, allowed_exts=get_advanced_config().get_allowed_extensions()
        )
        stems = set(scan.raw_dict) | {os.path.splitext(name)[0] for name in scan.files_tbr}
        return sorted(stems, key=lambda name: (name.lower(), name))

    def process(
        self, organize_files: bool = True, cleanup_temp: bool = True, *, resume: bool = False
    ) -> Dict:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG
            resume: 仅继续可用的已有进度 / Continue only an existing usable checkpoint
            
        Returns:
            处理统计字典

        Raises:
            CLIResumeError: 显式续跑不可用 / Explicit resume is unavailable
            CLIProcessingPaused: 处理协作停止 / Cooperative stop interrupted processing
        """
        self._stop_requested = False
        self._resume_state.active = False
        if resume:
            self._resume_state.validate(self._available_stems())
            self._resume_state.active = True

        def request_pause(_signum: int, _frame: Optional[FrameType]) -> None:
            """让核心在安全边界停止 / Let the core stop at a cooperative boundary."""
            self._stop_requested = True

        # Python 只允许主线程设置信号处理器；退出时恢复调用方的设置。
        # Only the main thread can install a signal handler; restore it on exit.
        previous_handler = None
        if current_thread() is main_thread():
            previous_handler = signal.signal(signal.SIGINT, request_pause)
        try:
            self._print_banner()
            result = self.processor.process(
                organize_files=organize_files,
                cleanup_temp=cleanup_temp,
                resume=resume,
            )
        except ProcessingCancelled as exc:
            # RAW 预览提取可能比核心写检查点更早停止。此时没有开始评分，
            # 所有候选都待处理；绝不拿这个清单覆盖以前的检查点。
            # RAW extraction can stop before core creates the checkpoint. No
            # photos have been scored yet; record candidates without replacing old state.
            if not self._resume_state.active and not self._resume_state.exists():
                pending = self._available_stems()
                if pending:
                    self._resume_state.start(pending)
            raise CLIProcessingPaused(self._resume_state.can_continue()) from exc
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
        
        # 打印摘要
        self._print_summary(result)
        
        return result.stats
    
    def _print_banner(self):
        """Print CLI banner"""
        self._log("\n" + "="*60)
        from constants import APP_VERSION
        from tools.i18n import t
        self._log(t("cli.banner", version=APP_VERSION))
        self._log("="*60 + "\n")
        
        self._log(t("cli.phase_scan"), "info")
    
    def _print_summary(self, result: ProcessingResult):
        """打印完成摘要（使用共享格式化模块）"""
        from core.stats_formatter import format_processing_summary, print_summary
        
        lines = format_processing_summary(result.stats, include_time=True)
        print_summary(lines, self._log)
