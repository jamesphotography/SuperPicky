#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI Processor - 命令行处理器
简化版 - 调用核心 PhotoProcessor
"""

from typing import Dict, List
from core.photo_processor import (
    PhotoProcessor,
    ProcessingSettings,
    ProcessingCallbacks,
    ProcessingResult
)
from .utils import log_message


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
                progress=self._progress
            )
        )
    
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
    
    def process(self, organize_files: bool = True, cleanup_temp: bool = True, resume: bool = None) -> Dict:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG
            
        Returns:
            处理统计字典
        """
        # 打印横幅
        self._print_banner()
        
        # 调用核心处理器
        if resume is None:
            resume = getattr(self, "resume", False)

        result = self.processor.process(
            organize_files=organize_files,
            cleanup_temp=cleanup_temp,
            resume=resume
        )
        
        # 打印摘要
        self._print_summary(result)
        
        return result.stats
    
    def _print_banner(self):
        """Print CLI banner"""
        self._log("\n" + "="*60)
        self._log("🐦 SuperPicky CLI - AI Bird Photo Selector")
        self._log("="*60 + "\n")
        
        self._log("📁 Phase 1: File Scanning", "info")
    
    def _print_summary(self, result: ProcessingResult):
        """打印完成摘要（使用共享格式化模块）"""
        from core.stats_formatter import format_processing_summary, print_summary
        
        lines = format_processing_summary(result.stats, include_time=True)
        print_summary(lines, self._log)
