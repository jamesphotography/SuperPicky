#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core Photo Processor - 核心照片处理器
提取自 GUI 和 CLI 的共享业务逻辑

职责：
- 文件扫描和 RAW 转换
- 调用 AI 检测
- 调用 RatingEngine 评分
- 写入 EXIF 元数据
- 文件移动和清理
"""

import os
import sys
import time
import json
import math
import subprocess
import shutil
import threading
import queue
from collections import deque
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# 现有模块
from tools.find_bird_util import raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds, read_image_bgr
from tools.report_db import ReportDB
from tools.exiftool_manager import get_exiftool_manager
from tools.file_utils import ensure_hidden_directory, clear_readonly_attribute
from tools.resume_state import ResumeStateManager
from advanced_config import get_advanced_config
from core.rating_engine import RatingEngine, create_rating_engine_from_config
from core.keypoint_detector import KeypointDetector, get_keypoint_detector
from core.flight_detector import FlightDetector, get_flight_detector, FlightResult
from core.exposure_detector import ExposureDetector, get_exposure_detector, ExposureResult
from core.focus_point_detector import get_focus_detector, verify_focus_in_bbox, arbitrate_focus_weights

from constants import RATING_FOLDER_NAMES, RAW_EXTENSIONS, JPG_EXTENSIONS, HEIF_EXTENSIONS, get_rating_folder_names

# 国际化
from tools.i18n import get_i18n


@dataclass
class ProcessingSettings:
    """处理参数配置"""
    ai_confidence: int = 50
    sharpness_threshold: int = 400   # 头部区域锐度达标阈值 (200-600)
    nima_threshold: float = 5.0      # V3.9.4: TOPIQ 美学达标阈值，与 GUI 滑块默认值一致
    save_crop: bool = True
    normalization_mode: str = 'log_compression'  # 默认使用log_compression，与GUI一致
    detect_flight: bool = True       # V3.4: 飞版检测开关
    detect_exposure: bool = True     # V3.9.4: 曝光检测开关（默认开启，与 GUI 一致）
    exposure_threshold: float = 0.10 # V3.8: 曝光阈值 (0.05-0.20)
    detect_burst: bool = True        # V4.0: 连拍检测开关（默认开启）
    # BirdID 自动识别设置
    auto_identify: bool = False       # 选片时自动识别鸟种（默认关闭）
    birdid_use_ebird: bool = True     # 使用 eBird 过滤
    birdid_country_code: str = None   # eBird 国家代码
    birdid_region_code: str = None    # eBird 区域代码
    birdid_confidence_threshold: float = 50.0  # 置信度阈值（默认 50%，可在「高级设置 → 自动识鸟」调整 50-95%）
    # 鸟种英文名显示格式 (AviList mapping)
    name_format: str = "default"       # "default" | "avilist" | "clements" | "birdlife" | "scientific"
    # 性能日志模式
    perf_logging: bool = False         # 是否输出性能分解日志
    perf_log_every: int = 25           # 每处理 N 张输出一次中间性能摘要
    perf_system_metrics: bool = False  # 是否尝试输出 CPU/内存快照（需 psutil）


@dataclass
class ProcessingCallbacks:
    """回调函数（用于进度更新和日志输出）"""
    log: Optional[Callable[[str, str], None]] = None
    progress: Optional[Callable[[int], None]] = None
    should_stop: Optional[Callable[[], bool]] = None
    crop_preview: Optional[Callable[[any], None]] = None  # V4.2: 裁剪预览回调


@dataclass
class ProcessingResult:
    """处理结果数据"""
    stats: Dict[str, any] = field(default_factory=dict)
    file_ratings: Dict[str, int] = field(default_factory=dict)
    star_3_photos: List[Dict] = field(default_factory=list)
    total_time: float = 0.0
    avg_time: float = 0.0


class ProcessingCancelled(RuntimeError):
    """Raised when processing is cancelled by the caller."""


def compute_xmp_label(is_flying: bool, focus_status: Optional[str], translate) -> Optional[str]:
    """
    计算 XMP:Label 颜色名(B+ 默认映射,Paul P2):
    飞鸟=蓝(优先) > 精焦 BEST=绿 > 脱焦 BAD/WORST=红;GOOD/无鸟不打标签。
    Lightroom 按本地化字符串匹配标签色,语言包缺 key 时回退英文色名,
    绝不把 key 串写进 LR(4.3.0 白框陷阱防御)。

    Compute the XMP:Label color name (B+ default mapping): flying=Blue
    (highest priority) > BEST=Green > BAD/WORST=Red; GOOD or no bird gets
    no label. Falls back to English color names when the language pack
    lacks a key (LR matches labels by localized string).

    参数 / Parameters:
        is_flying (bool): 是否飞鸟 / whether the bird is flying.
        focus_status (Optional[str]): BEST/GOOD/BAD/WORST 或 None。
        translate: i18n.t 同签名的翻译函数 / i18n.t-compatible callable.

    返回 / Returns:
        Optional[str]: 本地化颜色名;None=不写标签。
    """
    if is_flying:
        label = translate("xmp_labels.flight")
        return "Blue" if label == "xmp_labels.flight" else label
    if focus_status == "BEST":
        label = translate("xmp_labels.focus")
        return "Green" if label == "xmp_labels.focus" else label
    if focus_status in ("BAD", "WORST"):
        label = translate("xmp_labels.defocus")
        return "Red" if label == "xmp_labels.defocus" else label
    return None


class PhotoProcessor:
    """
    核心照片处理器
    
    封装所有业务逻辑，GUI 和 CLI 都调用这个类
    """
    
    def __init__(
        self,
        dir_path: str,
        settings: ProcessingSettings,
        callbacks: Optional[ProcessingCallbacks] = None
    ):
        """
        初始化处理器
        
        Args:
            dir_path: 处理目录路径
            settings: 处理参数
            callbacks: 回调函数（进度、日志）
        """
        self.dir_path = dir_path
        self.settings = settings
        self.callbacks = callbacks or ProcessingCallbacks()
        self.config = get_advanced_config()
        
        # 初始化评分引擎
        self.rating_engine = create_rating_engine_from_config(self.config)
        # 使用 UI 设置更新达标阈值
        self.rating_engine.update_thresholds(
            sharpness_threshold=settings.sharpness_threshold,
            nima_threshold=settings.nima_threshold
        )
        
        # 获取国际化实例
        self.i18n = get_i18n()
        
        
        # 统计数据（支持 0/1/2/3 星）
        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_1': 0,  # 普通照片（合格）
            'star_0': 0,  # 普通照片（问题）
            'no_bird': 0,
            'failed': 0,  # V4.5: 处理异常被跳过的照片计数 / photos skipped due to per-photo errors
            'flying': 0,  # V3.6: 飞鸟照片计数
            'focus_precise': 0,  # V4.2: 精焦照片计数（红色标签）
            'exposure_issue': 0,  # V3.8: 曝光问题计数
            'bird_species': [],  # V4.2: 识别的鸟种列表 [{'cn_name': '...', 'en_name': '...'}]
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }
        
        # 内部状态
        self.file_ratings = {}
        self.star_3_photos = []
        # V4.5: 处理异常被跳过的照片文件名，供最终汇总提示（这些照片未评分/未整理）
        # V4.5: Filenames skipped by per-photo error handling, surfaced in the
        # end-of-run summary (these photos were neither rated nor organized).
        self.failed_photos: List[str] = []
        self.temp_converted_jpegs = set()  # V4.0: Track temp-converted JPEGs to avoid deleting user originals
        self.file_bird_species = {}  # V4.0: Track bird species per file: {'cn_name': '...', 'en_name': '...'}
        self.burst_map = {}  # V4.0.4: Track burst group IDs: {filepath: group_id}, 0 = not a burst
        # SQLite 报告数据库（替代 CSV 缓存）
        self.report_db = None  # 在 _run_ai_detection 中初始化
        self.resume_state = ResumeStateManager(dir_path)
        self._stop_requested = False
        
        # 性能日志开关（支持 settings 和环境变量）
        env_perf = os.getenv("SUPERPICKY_PERF_LOG", "").strip().lower() in {"1", "true", "yes", "on"}
        env_perf_sys = os.getenv("SUPERPICKY_PERF_SYS", "").strip().lower() in {"1", "true", "yes", "on"}
        env_perf_every = os.getenv("SUPERPICKY_PERF_EVERY", "").strip()
        
        self._perf_enabled = bool(settings.perf_logging or env_perf)
        self._perf_system_metrics = bool(settings.perf_system_metrics or env_perf_sys)
        self._perf_log_every = max(1, int(settings.perf_log_every or 25))
        if env_perf_every.isdigit():
            self._perf_log_every = max(1, int(env_perf_every))
        
        self._perf_stats = {
            'photos': 0,
            'photo_total_ms': 0.0,
            'early_exit': 0,
            'stage_ms': {},
            'exif_flush_count': 0,
            'checkpoints': 0,
        }
        
        if self._perf_enabled:
            self._log(
                f"⏱ PERF mode enabled (every={self._perf_log_every}, "
                f"system_metrics={'on' if self._perf_system_metrics else 'off'})"
            )
    
    def _log(self, msg: str, level: str = "info"):
        """内部日志方法"""
        if self.callbacks.log:
            self.callbacks.log(msg, level)
    
    def _progress(self, percent: int):
        """内部进度更新"""
        if self.callbacks.progress:
            self.callbacks.progress(percent)

    def request_stop(self) -> None:
        self._stop_requested = True

    def _should_stop(self) -> bool:
        if self._stop_requested:
            return True
        if not self.callbacks.should_stop:
            return False
        try:
            return bool(self.callbacks.should_stop())
        except Exception:
            return False

    def _check_cancelled(self) -> None:
        if self._should_stop():
            raise ProcessingCancelled("Processing cancelled")
    
    def _perf_add_stage(self, stage: str, ms: float):
        """累计阶段耗时（毫秒）"""
        if not self._perf_enabled:
            return
        if ms is None:
            return
        ms = max(0.0, float(ms))
        self._perf_stats['stage_ms'][stage] = self._perf_stats['stage_ms'].get(stage, 0.0) + ms
    
    def _perf_record_photo(self, photo_ms: float, photo_stage_ms: Dict[str, float], early_exit: bool = False):
        """记录单张耗时并按间隔输出检查点"""
        if not self._perf_enabled:
            return
        
        self._perf_stats['photos'] += 1
        self._perf_stats['photo_total_ms'] += max(0.0, float(photo_ms))
        if early_exit:
            self._perf_stats['early_exit'] += 1
        
        for stage, ms in photo_stage_ms.items():
            self._perf_add_stage(stage, ms)
        
        if self._perf_stats['photos'] % self._perf_log_every == 0:
            self._perf_stats['checkpoints'] += 1
            self._perf_log_checkpoint()
    
    def _perf_system_snapshot(self) -> str:
        """可选系统资源快照（依赖 psutil）"""
        if not self._perf_enabled or not self._perf_system_metrics:
            return ""
        try:
            import psutil
            p = psutil.Process(os.getpid())
            rss_gb = p.memory_info().rss / (1024 ** 3)
            cpu = psutil.cpu_percent(interval=None)
            return f", cpu={cpu:.0f}%, rss={rss_gb:.1f}GB"
        except Exception:
            return ""
    
    def _perf_log_checkpoint(self):
        """输出中间性能摘要"""
        if not self._perf_enabled:
            return
        photos = self._perf_stats['photos']
        if photos <= 0:
            return
        
        avg_ms = self._perf_stats['photo_total_ms'] / photos
        stage = self._perf_stats['stage_ms']
        yolo = stage.get('yolo', 0.0) / photos
        keypoint = stage.get('keypoint', 0.0) / photos
        topiq = stage.get('topiq', 0.0) / photos
        flight = stage.get('flight', 0.0) / photos
        exposure = stage.get('exposure', 0.0) / photos
        focus = stage.get('focus', 0.0) / photos
        self._log(
            f"⏱ PERF [{photos}] avg={avg_ms/1000:.3f}s "
            f"(yolo={yolo:.0f}ms kp={keypoint:.0f}ms topiq={topiq:.0f}ms "
            f"flight={flight:.0f}ms exp={exposure:.0f}ms focus={focus:.0f}ms"
            f"{self._perf_system_snapshot()})"
        )
    
    def _perf_finalize(self):
        """输出最终性能摘要并写入 stats"""
        if not self._perf_enabled:
            return
        photos = self._perf_stats['photos']
        if photos <= 0:
            return
        
        avg_ms = self._perf_stats['photo_total_ms'] / photos
        stage_avg = {k: (v / photos) for k, v in self._perf_stats['stage_ms'].items()}
        
        self._log("⏱ PERF Summary:")
        self._log(
            f"  photos={photos}, early_exit={self._perf_stats['early_exit']}, "
            f"avg={avg_ms/1000:.3f}s/photo, exif_flush={self._perf_stats['exif_flush_count']}"
        )
        if stage_avg:
            # 只打印前 10 个最重阶段
            sorted_items = sorted(stage_avg.items(), key=lambda kv: kv[1], reverse=True)[:10]
            stage_text = ", ".join([f"{k}={v:.0f}ms" for k, v in sorted_items])
            self._log(f"  stage_avg: {stage_text}{self._perf_system_snapshot()}")
        
        self.stats['perf'] = {
            'enabled': True,
            'photos': photos,
            'early_exit': self._perf_stats['early_exit'],
            'avg_ms_per_photo': avg_ms,
            'stage_avg_ms': stage_avg,
            'exif_flush_count': self._perf_stats['exif_flush_count'],
        }
    
    # ============ V4.3: ISO 锐度归一化 ============
    # 高 ISO 噪点会虚高 Tenengrad 锐度值，需要根据 ISO 进行归一化补偿
    ISO_BASE = 800          # 基准 ISO（此值及以下不惩罚）
    ISO_PENALTY_FACTOR = 0.05   # 每翻一倍 ISO 扣 5%
    ISO_MIN_FACTOR = 0.5        # 最低系数（最多扣 50%）
    
    def _read_iso(self, filepath: str) -> int:
        """
        从 EXIF 读取 ISO 值
        
        V4.0.5: 优化 - 复用 focus_detector 的常驻 exiftool 进程，避免每次启动新进程
        
        Args:
            filepath: 图片文件路径（RAW 或 JPEG）
            
        Returns:
            ISO 值（整数），读取失败返回 None
        """
        try:
            # V4.0.5: 复用 focus_detector 的常驻 exiftool 进程
            focus_detector = get_focus_detector()
            exif_data = focus_detector._read_exif(filepath, ['ISO'])
            if exif_data and 'ISO' in exif_data:
                return int(exif_data['ISO'])
        except Exception:
            pass
        return None
    
    def _read_all_exif_metadata(self, filepath: str) -> dict:
        """
        V2: 一次性读取所有需要的 EXIF 元数据
        
        复用 focus_detector 的常驻 ExifTool 进程，一次性读取所有字段，
        避免多次启动 ExifTool 进程，大幅提升性能。
        
        Args:
            filepath: 图片文件路径（RAW 或 JPEG）
            
        Returns:
            包含所有 EXIF 字段的字典，读取失败的字段值为 None
        """
        exif_fields = [
            # 相机设置
            'ISO', 'ShutterSpeed', 'Aperture', 'FocalLength',
            'FocalLengthIn35mmFormat', 'Model', 'LensModel',
            # GPS
            'GPSLatitude', 'GPSLongitude', 'GPSAltitude',
            # IPTC 元数据
            'Title', 'Caption-Abstract', 'City', 'State', 'Country',
            # 时间
            'DateTimeOriginal',
        ]
        
        result = {
            'iso': None,
            'shutter_speed': None,
            'aperture': None,
            'focal_length': None,
            'focal_length_35mm': None,
            'camera_model': None,
            'lens_model': None,
            'gps_latitude': None,
            'gps_longitude': None,
            'gps_altitude': None,
            'title': None,
            'caption': None,
            'city': None,
            'state_province': None,
            'country': None,
            'date_time_original': None,
        }
        
        try:
            focus_detector = get_focus_detector()
            exif_data = focus_detector._read_exif(filepath, exif_fields)
            
            if exif_data:
                # 相机设置
                if 'ISO' in exif_data:
                    try:
                        result['iso'] = int(exif_data['ISO'])
                    except:
                        pass
                
                result['shutter_speed'] = exif_data.get('ShutterSpeed')
                result['aperture'] = exif_data.get('Aperture')
                
                if 'FocalLength' in exif_data:
                    try:
                        # FocalLength 可能是 "500.0 mm" 格式
                        fl_str = str(exif_data['FocalLength']).replace('mm', '').strip()
                        result['focal_length'] = float(fl_str)
                    except:
                        pass
                
                if 'FocalLengthIn35mmFormat' in exif_data:
                    try:
                        result['focal_length_35mm'] = int(exif_data['FocalLengthIn35mmFormat'])
                    except:
                        pass
                
                result['camera_model'] = exif_data.get('Model')
                result['lens_model'] = exif_data.get('LensModel')
                
                # GPS
                if 'GPSLatitude' in exif_data:
                    try:
                        result['gps_latitude'] = float(exif_data['GPSLatitude'])
                    except:
                        pass
                
                if 'GPSLongitude' in exif_data:
                    try:
                        result['gps_longitude'] = float(exif_data['GPSLongitude'])
                    except:
                        pass
                
                if 'GPSAltitude' in exif_data:
                    try:
                        result['gps_altitude'] = float(exif_data['GPSAltitude'])
                    except:
                        pass
                
                # IPTC 元数据
                result['title'] = exif_data.get('Title')
                result['caption'] = exif_data.get('Caption-Abstract')
                result['city'] = exif_data.get('City')
                result['state_province'] = exif_data.get('State')
                result['country'] = exif_data.get('Country')
                
                # 时间
                result['date_time_original'] = exif_data.get('DateTimeOriginal')
        
        except Exception as e:
            # 静默失败，返回空值
            pass
        
        return result
    
    def _get_iso_sharpness_factor(self, iso_value: int) -> float:
        """
        计算 ISO 锐度归一化系数
        
        基于对数衰减：每翻一倍 ISO 扣 5%
        例如：ISO 800 = 1.0, ISO 1600 = 0.95, ISO 3200 = 0.90, ISO 6400 = 0.85
        
        Args:
            iso_value: ISO 值
            
        Returns:
            归一化系数 (0.5 - 1.0)
        """
        if iso_value is None or iso_value <= self.ISO_BASE:
            return 1.0
        
        # penalty = 0.05 * log₂(ISO / 800)
        penalty = self.ISO_PENALTY_FACTOR * math.log2(iso_value / self.ISO_BASE)
        factor = max(self.ISO_MIN_FACTOR, 1.0 - penalty)
        return factor

    @staticmethod
    def _resume_prefix(filename: str) -> str:
        return os.path.splitext(os.path.basename(filename))[0]

    def _sort_processing_files(self, files_tbr: List[str]) -> List[str]:
        return sorted(files_tbr, key=lambda item: self._resume_prefix(item).lower())
    
    def process(
        self,
        organize_files: bool = True,
        cleanup_temp: bool = True,
        resume: bool = False
    ) -> ProcessingResult:
        """
        主处理流程
        
        Args:
            organize_files: 是否移动文件到分类文件夹
            cleanup_temp: 是否清理临时JPG文件
            
        Returns:
            ProcessingResult 包含统计数据和处理结果
        """
        start_time = time.time()
        self.stats['start_time'] = start_time
        exiftool_mgr = None
        exiftool_session_opened = False
        advanced_config = get_advanced_config()
        metadata_write_mode = str(advanced_config.get_metadata_write_mode()).strip().lower()

        try:
            if metadata_write_mode != "none":
                exiftool_mgr = get_exiftool_manager()
                exiftool_mgr.open_persistent_session("photo_processor.process")
                exiftool_session_opened = True

            # 阶段1: 文件扫描
            raw_dict, jpg_dict, files_tbr = self._scan_files()
            
            # 阶段1.5: V4.0.4 早期连拍检测（只基于时间戳）
            if self.settings.detect_burst:
                self.burst_map = self._detect_bursts_early(raw_dict)
            
            # 阶段2: RAW转换
            raw_files_to_convert = self._identify_raws_to_convert(raw_dict, jpg_dict, files_tbr)
            if raw_files_to_convert:
                self._convert_raws(raw_files_to_convert, files_tbr)

            files_tbr = self._sort_processing_files(files_tbr)
            display_start = 1
            display_total = len(files_tbr)
            ordered_prefixes = [self._resume_prefix(item) for item in files_tbr]
            if resume:
                plan = self.resume_state.get_resume_plan(ordered_prefixes)
                if plan:
                    prefix_to_file = {self._resume_prefix(item): item for item in files_tbr}
                    files_tbr = [prefix_to_file[prefix] for prefix in plan["pending_prefixes"] if prefix in prefix_to_file]
                    display_start = int(plan["next_index"])
                    display_total = int(plan["total_files"])
                else:
                    self.resume_state.start(ordered_prefixes)
            else:
                self.resume_state.start(ordered_prefixes)

            self._check_cancelled()
            
            # 阶段3: AI检测与评分
            self._process_images(files_tbr, raw_dict, display_start=display_start, display_total=display_total)
            
            # 阶段4: 精选旗标计算（metadata_write_mode=none 时跳过）
            if metadata_write_mode != "none":
                self._calculate_picked_flags()
            
            # 阶段5: 文件组织
            if organize_files:
                self._move_files_to_rating_folders(raw_dict)
            
            # 阶段6: V4.0.4 跨目录连拍合并（在文件整理完成后）
            # V4.6(Paul P1): burst_group_folders 关闭时连拍不聚子目录,
            # 照片按星级/鸟种常规归档(检测与整理解耦)。
            # V4.6 (Paul P1): with burst_group_folders off, burst shots are
            # filed normally instead of into burst_NNN subfolders.
            if (self.settings.detect_burst and self.burst_map and organize_files
                    and self.config.burst_group_folders):
                burst_stats = self._consolidate_burst_groups(raw_dict)
                self.stats['burst_groups'] = burst_stats.get('groups', 0)
                self.stats['burst_moved'] = burst_stats.get('moved', 0)
            
            # 阶段7: 临时文件处理
            if cleanup_temp:
                self._cleanup_temp_files(files_tbr, raw_dict)
            else:
                # V4.0.5: 保留临时文件时，将路径写入数据库
                self._save_temp_paths_to_db()
                
            # 阶段8: 清理过期缓存 (V4.1)
            self._cleanup_expired_cache()

            # V4.5: 全部阶段（含识鸟收尾/精选/文件整理）完成后才发 100%，
            # 与统一工作单元进度（封顶 99%）配套，杜绝「100% 后还在干活」。
            # V4.5: Emit 100% only after every phase (BirdID drain / picks /
            # organizing) finishes — pairs with the 99%-capped unit progress
            # so the bar never claims completion while work remains.
            self._progress(100)

            # 记录结束时间
            end_time = time.time()
            self.stats['end_time'] = end_time
            self.stats['total_time'] = end_time - start_time
            self.stats['avg_time'] = (
                self.stats['total_time'] / self.stats['total']
                if self.stats['total'] > 0 else 0
            )
            
            # 关闭数据库连接（在所有阶段完成后）
            if hasattr(self, 'report_db') and self.report_db:
                self.report_db.close()
                self.report_db = None

            # V4.5: 汇总处理异常被跳过的照片——它们未评分/未整理，仍留在原目录，
            # 避免用户只看到"处理完成"却不知道少了几张。
            # V4.5: Summarize photos skipped by per-photo error handling — they
            # were neither rated nor organized and remain in the source folder;
            # without this the user only sees "done" and never learns some
            # photos were silently missing.
            if self.stats['failed'] > 0:
                shown = "、".join(self.failed_photos[:10])
                more = f" …(共{self.stats['failed']}张)" if self.stats['failed'] > 10 else ""
                self._log(
                    f"⚠️ {self.stats['failed']} 张照片处理异常被跳过（未评分/未整理，仍在原目录）: {shown}{more}",
                    "warning"
                )

            self.resume_state.clear()
            
            return ProcessingResult(
                stats=self.stats.copy(),
                file_ratings=self.file_ratings.copy(),
                star_3_photos=self.star_3_photos.copy(),
                total_time=self.stats['total_time'],
                avg_time=self.stats['avg_time']
            )
        finally:
            if exiftool_session_opened and exiftool_mgr is not None:
                try:
                    exiftool_mgr.close_persistent_session("photo_processor.process")
                except Exception as e:
                    self._log(f"⚠️ ExifTool session close failed: {e}", "warning")
    
    def _scan_files(self) -> Tuple[dict, dict, list]:
        """扫描目录文件"""
        scan_start = time.time()
        
        raw_dict = {}
        jpg_dict = {}
        heif_dict = {}               # HIF/HEIF 文件暂存
        heif_processed_as_raw = set() # 被当作 RAW 处理的 HIF 前缀
        files_tbr = []
        
        for filename in os.listdir(self.dir_path):
            if filename.startswith('.'):
                continue
                
            # V4.0.5: 忽略临时文件（tmp_ 或 temp_ 开头）
            if filename.lower().startswith(('tmp_', 'temp_')):
                continue

            # V3.9: 忽略 Windows 系统文件
            if filename.lower() == 'desktop.ini' or filename.lower() == 'thumbs.db':
                continue
            
            file_prefix, file_ext = os.path.splitext(filename)
            if file_ext.lower() in RAW_EXTENSIONS:
                raw_dict[file_prefix] = file_ext
            elif file_ext.lower() in HEIF_EXTENSIONS:
                # HEIF/HIF: 仅当同名前缀没有 RAW 时才加入（RAW 优先）
                heif_dict[file_prefix] = file_ext
            if file_ext.lower() in JPG_EXTENSIONS:
                jpg_dict[file_prefix] = file_ext
                files_tbr.append(filename)
        
        # 将 HIF 作为 RAW 处理（仅对同名前缀无 RAW 文件的）
        for prefix, ext in heif_dict.items():
            if prefix not in raw_dict:
                raw_dict[prefix] = ext
                heif_processed_as_raw.add(prefix)

        scan_time = (time.time() - scan_start) * 1000
        self._log(self.i18n.t("logs.scan_time", time=scan_time))
        
        return raw_dict, jpg_dict, files_tbr
    
    def _detect_bursts_early(self, raw_dict: Dict[str, str]) -> Dict[str, int]:
        """
        V4.0.4: 早期连拍检测（在评分之前）
        只基于时间戳检测连拍组，与有没有鸟、是什么鸟无关
        
        Args:
            raw_dict: RAW 文件字典 {prefix: extension}
            
        Returns:
            burst_map: {filepath: group_id}，0 表示不属于连拍组
        """
        if not self.settings.detect_burst:
            return {}
        
        from core.burst_detector import BurstDetector
        
        # 收集所有 RAW 文件路径
        raw_filepaths = []
        for prefix, ext in raw_dict.items():
            filepath = os.path.join(self.dir_path, prefix + ext)
            if os.path.exists(filepath):
                raw_filepaths.append(filepath)
        
        if len(raw_filepaths) < 4:  # 少于 4 张不检测
            return {}
        
        self._log(self.i18n.t("logs.burst_early_detecting", count=len(raw_filepaths)))
        
        detector = BurstDetector(use_phash=False)  # 早期检测不用 pHash，后期再验证
        
        # 读取时间戳
        photos = detector.read_timestamps(raw_filepaths)
        
        # 纯时间戳检测（不过滤星级）
        groups = detector.detect_groups_by_time_only(photos)
        
        # 构建映射
        burst_map = {}
        for group in groups:
            for photo in group.photos:
                burst_map[photo.filepath] = group.group_id
        
        if groups:
            total_burst_photos = sum(len(g.photos) for g in groups)
            self._log(self.i18n.t("logs.burst_early_detected", groups=len(groups), photos=total_burst_photos))
        
        return burst_map
    
    def _consolidate_burst_groups(self, raw_dict: Dict[str, str]) -> Dict[str, int]:
        """
        V4.0.4: 后期连拍合并（跨目录）
        在文件整理完成后，将同一连拍组的照片移到最高星级目录的 burst 子目录
        
        Args:
            raw_dict: RAW 文件字典 {prefix: extension}
            
        Returns:
            stats: {'groups': n, 'moved': n}
        """
        import shutil
        from collections import defaultdict
        from core.burst_detector import BurstDetector
        from tools.exiftool_manager import get_exiftool_manager

        stats = {'groups': 0, 'moved': 0}
        
        if not self.burst_map:
            return stats
        
        # 按 group_id 分组收集文件
        groups = defaultdict(list)
        for filepath, group_id in self.burst_map.items():
            if group_id > 0:
                groups[group_id].append(filepath)
        
        if not groups:
            return stats
        
        self._log(self.i18n.t("logs.burst_consolidating", groups=len(groups)))
        
        detector = BurstDetector(use_phash=True)  # 后期验证用 pHash
        exiftool_mgr = get_exiftool_manager()

        # V4.3.0: 文件已按 layout 落地（rating-first 或 species-first），位置因 layout 而异。
        # 旧逻辑只按 rating-first 猜路径，species-first 下找不到文件 → 连拍组凑不齐 →
        # 不建连拍目录。改为先建一次「文件名 → 当前路径」索引，对任意 layout/嵌套都成立。
        # 排除 .superpicky（避免命中 temp_preview 预览图）与隐藏目录。
        # Build a filename→path index once so burst consolidation finds files under ANY
        # layout (rating-first / species-first / nested), fixing missing burst dirs.
        file_index: Dict[str, str] = {}
        for _root, _dirs, _files in os.walk(self.dir_path):
            _dirs[:] = [d for d in _dirs if d != '.superpicky' and not d.startswith('.')]
            for _fn in _files:
                file_index.setdefault(_fn, os.path.join(_root, _fn))

        for group_id, original_filepaths in groups.items():
            # 找到每个文件当前的实际位置和星级
            current_files = []
            for orig_path in original_filepaths:
                prefix = os.path.splitext(os.path.basename(orig_path))[0]
                ext = raw_dict.get(prefix, os.path.splitext(orig_path)[1])
                rating = self.file_ratings.get(prefix, 0)
                
                # V4.3.0: 用索引按文件名定位当前路径（layout 无关），找不到再回退原位
                # Locate via the layout-agnostic index, falling back to the original path.
                current_path = file_index.get(prefix + ext)
                if not current_path or not os.path.exists(current_path):
                    current_path = orig_path if os.path.exists(orig_path) else None

                if current_path:
                    current_files.append({
                        'path': current_path,
                        'prefix': prefix,
                        'rating': rating,
                        'sharpness': 0.0,
                        'topiq': 0.0
                    })
            
            if len(current_files) < 4:  # 少于 4 张跳过
                continue
            
            # 找最高星级
            highest_rating = max(f['rating'] for f in current_files)
            
            # V4.0.4: 优化逻辑 - 如果连拍组中所有照片都在 0-1 星，则不合并（不创建 burst 目录）
            if highest_rating < 2:
                continue

            # V4.0.5: 查找连拍组中是否有鸟种识别，优先查找最高星级照片的鸟种
            bird_species_name = None
            # 先查找最高星级的照片
            for f in current_files:
                if f['rating'] == highest_rating:
                    prefix = f['prefix']
                    if prefix in self.file_bird_species:
                        bird_info = self.file_bird_species[prefix]
                        if self.i18n.current_lang.startswith('en'):
                            bird_species_name = bird_info.get('en_name', '').replace(' ', '_')
                        else:
                            bird_species_name = bird_info.get('cn_name', '')
                        if bird_species_name:
                            break
            # 如果最高星级照片没有鸟种，查找其他任意照片
            if not bird_species_name:
                for f in current_files:
                    prefix = f['prefix']
                    if prefix in self.file_bird_species:
                        bird_info = self.file_bird_species[prefix]
                        if self.i18n.current_lang.startswith('en'):
                            bird_species_name = bird_info.get('en_name', '').replace(' ', '_')
                        else:
                            bird_species_name = bird_info.get('cn_name', '')
                        if bird_species_name:
                            break

            
            # 读取评分数据选择最佳
            for f in current_files:
                csv_data = self._get_photo_scores_from_csv(f['prefix'])
                if csv_data:
                    f['sharpness'] = csv_data.get('sharpness', 0)
                    f['topiq'] = csv_data.get('topiq', 0)
            
            # 按综合分数选最佳
            best_file = max(current_files, key=lambda x: x['sharpness'] * 0.5 + x['topiq'] * 0.5)
            
            # V4.3.0: burst 目录始终走 compute_target_folder，与移动逻辑(_move)完全一致：
            # 关识鸟时 bird_species_name=None → 落「其他鸟类/{评分}」；highest_rating>=2 已由
            # 上方 `if highest_rating < 2: continue` 保证。修复 species-first 下连拍目录消失。
            # Always build the burst dir via the shared layout helper so it matches the move
            # logic in every case (identify-off → "Other Birds/{rating}").
            from core.folder_layout import compute_target_folder
            other_birds = self.i18n.t("logs.folder_other_birds")
            target = compute_target_folder(
                highest_rating,
                bird_species_name,
                self.config.folder_layout,
                other_birds,
            )
            burst_dir = os.path.join(self.dir_path, target, f"burst_{group_id:03d}")
            os.makedirs(burst_dir, exist_ok=True)

            
            # V4.0.4: 移动所有连拍照片到 burst 目录（包括最佳照片）
            for f in current_files:
                try:
                    filename = os.path.basename(f['path'])
                    dest = os.path.join(burst_dir, filename)
                    if os.path.exists(f['path']) and not os.path.exists(dest):
                        shutil.move(f['path'], dest)
                        stats['moved'] += 1

                        # V4.1.1: 同步更新 DB 中的 current_path，避免路径与实际位置不符
                        if hasattr(self, 'report_db') and self.report_db:
                            try:
                                rel_dest = os.path.relpath(dest, self.dir_path)
                                self.report_db.update_photo(f['prefix'], {'current_path': rel_dest})
                            except Exception as db_e:
                                self._log(f"    ⚠️ DB current_path update failed: {db_e}", "warning")

                        # 移动 sidecar 文件
                        file_base = os.path.splitext(f['path'])[0]
                        for sidecar_ext in ['.xmp', '.jpg', '.JPG']:
                            sidecar = file_base + sidecar_ext
                            if os.path.exists(sidecar):
                                try:
                                    shutil.move(sidecar, os.path.join(burst_dir, os.path.basename(sidecar)))
                                except:
                                    pass
                except Exception as e:
                    self._log(f"    ⚠️ Move failed: {e}", "warning")

            
            stats['groups'] += 1
        
        if stats['groups'] > 0:
            self._log(self.i18n.t("logs.burst_consolidate_complete", groups=stats['groups'], moved=stats['moved']))
        
        return stats
    
    def _get_photo_scores_from_csv(self, prefix: str) -> Optional[Dict]:
        """从 report.db 获取照片的评分数据"""
        if self.report_db is None:
            return None
        
        photo = self.report_db.get_photo(prefix)
        if photo:
            sharpness = float(photo.get('head_sharp') or 0)
            topiq = float(photo.get('nima_score') or 0)
            return {'sharpness': sharpness, 'topiq': topiq}
        return None
    
    def _identify_raws_to_convert(self, raw_dict, jpg_dict, files_tbr):
        """识别需要转换的RAW文件"""
        raw_files_to_convert = []
        
        for key, value in raw_dict.items():
            if key in jpg_dict:
                jpg_dict.pop(key)
                continue
            else:
                raw_file_path = os.path.join(self.dir_path, key + value)
                raw_files_to_convert.append((key, raw_file_path))
        
        return raw_files_to_convert
    
    def _convert_raws(self, raw_files_to_convert, files_tbr):
        """并行转换RAW文件"""
        raw_start = time.time()
        import multiprocessing
        max_workers = min(4, multiprocessing.cpu_count())
        
        self._log(self.i18n.t("logs.raw_conversion_start", count=len(raw_files_to_convert), threads=max_workers))
        
        def convert_single(args):
            key, raw_path = args
            try:
                jpg_path = raw_to_jpeg(raw_path)
                return (key, True, jpg_path)
            except Exception as e:
                return (key, False, str(e))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_raw = {
                executor.submit(convert_single, args): args 
                for args in raw_files_to_convert
            }
            converted_count = 0
            
            for future in as_completed(future_to_raw):
                key, success, result = future.result()
                if success:
                    # V4.1.0: result 是生成的 JPEG 绝对路径
                    # 计算相对路径添加到 files_tbr
                    jpeg_filename = os.path.relpath(result, self.dir_path)
                    files_tbr.append(jpeg_filename)
                    self.temp_converted_jpegs.add(jpeg_filename)  # 标记为临时文件
                    converted_count += 1
                else:
                    self._log(f"  ❌ {self.i18n.t('logs.batch_failed', start=key, end=key, error=result)}", "error")
        
        raw_time = time.time() - raw_start
        avg_time = raw_time / len(raw_files_to_convert) if len(raw_files_to_convert) > 0 else 0
        # Format time string
        time_str = f"{raw_time:.1f}s" if raw_time >= 1 else f"{raw_time*1000:.0f}ms"
        self._log(self.i18n.t("logs.raw_conversion_time", time_str=time_str, avg=avg_time))
    
    def _process_images(self, files_tbr, raw_dict, display_start: int = 1, display_total: int = None):
        """处理所有图片 - AI检测、关键点检测与评分"""
        advanced_config = get_advanced_config()
        detail_metadata_for_rejected = advanced_config.get_detail_metadata_for_rejected()

        # 获取模型（已在启动时预加载，此处仅获取引用）
        # 用列表包装，使闭包可替换（MPS 周期重载时需要）
        _yolo_model_box = [load_yolo_model()]
        
        # 初始化 SQLite 报告数据库
        self.report_db = ReportDB(self.dir_path)
        
        # 获取关键点检测模型
        keypoint_detector = get_keypoint_detector()
        try:
            keypoint_detector.load_model()
            use_keypoints = True
        except FileNotFoundError:
            self._log("⚠️  Keypoint model not found, using traditional sharpness", "warning")
            use_keypoints = False
        
        # V3.4: 飞版检测模型
        use_flight = False
        flight_detector = None
        if self.settings.detect_flight:
            flight_detector = get_flight_detector()
            try:
                flight_detector.load_model()
                use_flight = True
            except FileNotFoundError:
                self._log("⚠️  Flight model not found, skipping flight detection", "warning")
                use_flight = False
        
        total_files = display_total if display_total is not None else len(files_tbr)
        self._log(self.i18n.t("logs.files_to_process", total=total_files))

        def mark_resume_completed(prefix: str):
            if prefix:
                self.resume_state.mark_completed(prefix)
        
        exiftool_mgr = get_exiftool_manager()
        metadata_batch: List[Dict] = []
        metadata_batch_size = 64
        env_exif_batch = os.getenv("SUPERPICKY_EXIF_BATCH_SIZE", "").strip()
        if env_exif_batch.isdigit():
            metadata_batch_size = max(8, int(env_exif_batch))
        
        metadata_async_enabled = os.getenv("SUPERPICKY_EXIF_ASYNC", "1").strip().lower() not in {"0", "false", "no", "off"}
        metadata_queue_max_batches = 6
        env_exif_qmax = os.getenv("SUPERPICKY_EXIF_QUEUE_MAX", "").strip()
        if env_exif_qmax.isdigit():
            metadata_queue_max_batches = max(2, int(env_exif_qmax))
        
        metadata_queue = queue.Queue(maxsize=metadata_queue_max_batches) if metadata_async_enabled else None
        metadata_writer_thread = None
        metadata_writer_errors: List[Exception] = []
        metadata_writer_stats = {'flush_ms': 0.0, 'flush_count': 0}
        metadata_writer_stats_lock = threading.Lock()
        
        if metadata_async_enabled:
            def metadata_writer_worker():
                while True:
                    batch = metadata_queue.get()
                    if batch is None:
                        metadata_queue.task_done()
                        break
                    exif_start = time.time()
                    try:
                        exiftool_mgr.batch_set_metadata(batch)
                    except Exception as e:
                        metadata_writer_errors.append(e)
                    finally:
                        with metadata_writer_stats_lock:
                            metadata_writer_stats['flush_ms'] += (time.time() - exif_start) * 1000
                            metadata_writer_stats['flush_count'] += 1
                        metadata_queue.task_done()
            
            metadata_writer_thread = threading.Thread(
                target=metadata_writer_worker,
                daemon=True,
                name="sp-exif-writer"
            )
            metadata_writer_thread.start()
            if self._perf_enabled:
                self._log(
                    f"  ⚙️ EXIF async queue: on (batch={metadata_batch_size}, qmax={metadata_queue_max_batches})"
                )
        elif self._perf_enabled:
            self._log(f"  ⚙️ EXIF async queue: off (batch={metadata_batch_size})")
        
        def flush_metadata_batch():
            if not metadata_batch:
                return
            batch = metadata_batch.copy()
            metadata_batch.clear()
            if metadata_async_enabled and metadata_queue is not None:
                enqueue_start = time.time()
                metadata_queue.put(batch)  # 队列满时会背压，避免内存无限增长
                enqueue_wait_ms = (time.time() - enqueue_start) * 1000
                if enqueue_wait_ms > 0.1:
                    self._perf_add_stage('exif_enqueue_wait', enqueue_wait_ms)
                return
            exif_start = time.time()
            exiftool_mgr.batch_set_metadata(batch)
            exif_ms = (time.time() - exif_start) * 1000
            self._perf_add_stage('exif_flush', exif_ms)
            self._perf_stats['exif_flush_count'] += 1
        
        def queue_metadata(item: Dict):
            if not item or not item.get('file'):
                return
            metadata_batch.append(item)
            if len(metadata_batch) >= metadata_batch_size:
                flush_metadata_batch()

        def queue_star_metadata(item: Dict, original_prefix: str, in_pool: bool):
            """
            星级相关 EXIF 条目的路由:V2 排序池内的照片先挂起,收尾统一定星
            后回填 rating/caption 再入队;其余照片(硬门槛终局)直接入队。

            Route star-dependent EXIF items: photos in the V2 ranking pool are
            parked until the post-pass finalizes their rating/caption; photos
            finalized by hard gates are queued immediately.
            """
            if in_pool and original_prefix in v2_pending:
                v2_pending[original_prefix]['items'].append(item)
            else:
                queue_metadata(item)
        
        # UI设置转为列表格式
        ui_settings = [
            self.settings.ai_confidence,
            self.settings.sharpness_threshold,
            self.settings.nima_threshold,
            self.settings.save_crop,
            self.settings.normalization_mode
        ]
        focus_supported_raw_exts = {'.nef', '.nrw', '.arw', '.cr3', '.cr2', '.orf', '.raf', '.rw2'}
        
        ai_total_start = time.time()
        
        # 预获取 TOPIQ scorer（单例）并在循环中复用，减少重复导入/查找开销
        topiq_scorer = None
        try:
            from iqa_scorer import get_iqa_scorer
            from config import get_best_device
            topiq_scorer = get_iqa_scorer(device=get_best_device().type)
        except Exception:
            topiq_scorer = None
        
        # 推理线程池：用于将飞版检测与主线程关键点/TOPIQ并行
        inference_pool = ThreadPoolExecutor(max_workers=2)
        
        # BirdID 异步队列：将识别耗时与主处理流程重叠
        # CPU 推理可多线程并行；MPS/CUDA 设备并发线程安全性有限，保持单线程
        from config import get_best_device
        _birdid_device = str(get_best_device())
        _birdid_workers = 4 if _birdid_device == 'cpu' else 1
        birdid_executor = ThreadPoolExecutor(max_workers=_birdid_workers) if self.settings.auto_identify else None
        birdid_tasks = deque()
        identify_bird_fn = None

        # V4.5: 统一工作单元进度——照片与识鸟任务同权重计入一个分数，
        # 使循环结束后的 BirdID 收尾阶段进度条持续前进，而非停在 100%。
        # V4.5: Unified work-unit progress — photos and BirdID tasks share one
        # fraction so the bar keeps advancing during the post-loop BirdID
        # drain instead of sitting at 100%.
        progress_state = {
            'photos_done': 0,       # 已处理照片数（display 口径，兼容续跑）/ photos done (display scale)
            'birdid_submitted': 0,  # 已提交识鸟任务数 / BirdID tasks submitted
            'birdid_done': 0,       # 已完成识鸟任务数 / BirdID tasks finished
            'last_percent': 0,      # 单调钳位：进度只增不减 / monotonic clamp
        }

        def update_unit_progress():
            """
            按已完成工作单元更新进度条（封顶 99%，只增不减）。

            单元 = 已处理照片 + 已完成识鸟任务；分母 = 照片总数 + 已提交识鸟
            任务数。分母随任务提交增长，可能使分数瞬时回退，故用单调钳位；
            100% 由 process() 在全部阶段（含文件整理）完成后单独发出。

            Update the progress bar from completed work units (capped at 99%,
            monotonic). Units = processed photos + finished BirdID tasks over
            total photos + submitted BirdID tasks. The denominator grows as
            tasks are submitted, so the monotonic clamp prevents regressions;
            100% is emitted by process() only after every phase (including
            file organizing) has finished.
            """
            total_units = total_files + progress_state['birdid_submitted']
            if total_units <= 0:
                return
            done_units = progress_state['photos_done'] + progress_state['birdid_done']
            percent = min(99, int(done_units * 100 / total_units))
            if percent > progress_state['last_percent']:
                progress_state['last_percent'] = percent
                self._progress(percent)

        # V4.6(rating-v2/T3b): 两遍定星状态。循环内只做硬门槛判定与指标采集;
        # 过硬门槛(进入排序池)照片的星级相关 EXIF/统计/评分记录全部挂起在
        # v2_pending,收尾阶段用 core.rating_quota.assign_ratings 按批内配额
        # 统一定星后回填。循环中日志显示的星级为 v1 预估,最终以收尾为准。
        # V4.6 (rating-v2/T3b): two-pass rating state. During the loop, photos
        # passing every hard gate only collect metrics; their star-dependent
        # EXIF items / stats / rating records are parked in v2_pending and
        # finalized in the post-pass via assign_ratings (batch quota).
        from core.rating_quota import (
            PhotoMetricsV2,
            assign_ratings as assign_ratings_v2,
            gate_photo as gate_photo_v2,
            get_quota3_for_skill,
            get_quota2_for_skill,
        )
        v2_enabled = self.config.rating_algorithm == "v2"
        v2_pending: Dict[str, Dict] = {}

        if self.settings.auto_identify:
            try:
                from birdid.bird_identifier import identify_bird as identify_bird_fn
            except Exception as e:
                identify_bird_fn = None
                self._log(f"  ⚠️ BirdID import failed: {e}", "warning")
        
        def submit_birdid_task(
            file_prefix: str,
            image_path: str,
            title_targets: List[str],
            source_filename: Optional[str] = None,
            bird_crop_pil=None,  # 主流水线已裁剪的 PIL Image，避免 BirdID 重跑 YOLO
        ):
            if birdid_executor is None or identify_bird_fn is None:
                return
            if not title_targets:
                return
            source_display = source_filename or file_prefix or os.path.basename(image_path)
            try:
                submit_start = time.time()
                nf = self.settings.name_format if self.settings.name_format != "default" else None
                future = birdid_executor.submit(
                    identify_bird_fn,
                    image_path,
                    True,   # use_yolo
                    True,   # use_gps
                    self.settings.birdid_use_ebird,
                    self.settings.birdid_country_code,
                    self.settings.birdid_region_code,
                    1,      # top_k
                    nf,     # name_format
                    bird_crop_pil,  # preloaded_crop
                )
                self._perf_add_stage('birdid_submit', (time.time() - submit_start) * 1000)
                birdid_tasks.append((future, file_prefix, list(title_targets), source_display))
                progress_state['birdid_submitted'] += 1
            except Exception as e:
                self._log(f"  ⚠️ Bird ID failed [{source_display}]: {e}", "warning")
        
        def apply_birdid_result(
            file_prefix: str,
            title_targets: List[str],
            birdid_result: Dict,
            source_filename: Optional[str] = None
        ):
            if not birdid_result:
                return
            if birdid_result.get('error'):
                self._log(f"  ⚠️ BirdID error [{source_filename or file_prefix}]: {birdid_result['error']}", "warning")
            if not birdid_result.get('success') or not birdid_result.get('results'):
                return
            source_display = source_filename or file_prefix or "?"
            top_result = birdid_result['results'][0]
            birdid_confidence = top_result.get('confidence', 0)
            cn_name = top_result.get('cn_name', '')
            en_name = top_result.get('en_name', '')
            iucn_category = top_result.get('iucn_category')  # IUCN 等级 (LC/NT/VU/EN/CR/...)，可能为 None
            gbif_rarity_100 = top_result.get('gbif_rarity_100')  # GBIF 全球罕见度 (0-100)，可能为 None
            aesthetic_index = top_result.get('aesthetic_index')  # iRateBird 颜值 (0-100)，可能为 None

            if birdid_confidence >= self.settings.birdid_confidence_threshold:
                if self.i18n.current_lang.startswith('en'):
                    bird_log = en_name or cn_name
                    bird_title = en_name or cn_name
                else:
                    bird_log = cn_name or en_name
                    bird_title = cn_name or en_name
                
                # V4.2.7: 跟随鸟名输出 GBIF 罕见度 tier（5 级圆形充填图标 + 中英文）
                # V4.2.7: Append GBIF rarity tier to the bird-id log line.
                tier_suffix = ""
                tier_idx = None
                if gbif_rarity_100 is not None:
                    from core.rarity_tier import gbif_score_to_tier, tier_icon, tier_name
                    tier_idx = gbif_score_to_tier(gbif_rarity_100)
                    is_zh = not self.i18n.current_lang.startswith('en')
                    tier_suffix = f"  {tier_icon(tier_idx)} {tier_name(tier_idx, is_zh=is_zh)}"

                self._log(f"  🐦 Bird ID [{source_display}]: {bird_log} ({birdid_confidence:.0f}%){tier_suffix}", "species")

                species_entry = {'cn_name': cn_name, 'en_name': en_name}
                if tier_idx is not None:
                    species_entry['gbif_tier'] = tier_idx
                    species_entry['gbif_score'] = gbif_rarity_100
                if not any(s.get('cn_name') == cn_name for s in self.stats['bird_species']):
                    self.stats['bird_species'].append(species_entry)
                if cn_name:
                    self.file_bird_species[file_prefix] = {
                        'cn_name': cn_name,
                        'en_name': en_name
                    }

                # 写入数据库，供结果浏览器筛选面板和详情面板使用
                if self.report_db and (cn_name or en_name):
                    try:
                        db_updates = {
                            'bird_species_cn': cn_name,
                            'bird_species_en': en_name,
                            'birdid_confidence': birdid_confidence,
                        }
                        # V4.2.7: IUCN + GBIF 独立写入 report.db 列，供 detail_panel 单独展示
                        # V4.2.7: Persist IUCN + GBIF metrics in dedicated columns.
                        if iucn_category:
                            db_updates['iucn_category'] = iucn_category
                        if gbif_rarity_100 is not None:
                            db_updates['gbif_rarity_100'] = gbif_rarity_100
                        if aesthetic_index is not None:
                            db_updates['aesthetic_index'] = aesthetic_index
                        self.report_db.update_photo(file_prefix, db_updates)
                        # 将鸟种 + IUCN 追加到已生成的 DB caption 最前面
                        # Prepend species + IUCN lines to the DB caption.
                        existing = self.report_db.get_photo(file_prefix) or {}
                        old_cap = existing.get('caption') or ''
                        # V4.3.0: 鸟种名跟随界面语言（bird_title 已按语言选名），标签走 i18n
                        # V4.3.0: Species name follows UI language (bird_title already
                        # picks en/cn by locale); labels via i18n.
                        prefix_lines = [self.i18n.t("logs.caption_species", name=bird_title)]
                        if iucn_category:
                            prefix_lines.append(self.i18n.t("logs.caption_iucn", category=iucn_category))
                        prefix_block = "\n".join(prefix_lines)
                        # 去重检查兼容中英双语前缀，避免跨语言重复处理时重复添加
                        # Dedup check covers both zh/en prefixes for cross-language reprocessing.
                        already_prefixed = old_cap.startswith(
                            ('鸟种：', 'Species: ', '备选鸟种', 'Alt. species')
                        )
                        if old_cap and not already_prefixed:
                            self.report_db.update_photo(file_prefix, {'caption': prefix_block + '\n' + old_cap})
                        elif not old_cap:
                            self.report_db.update_photo(file_prefix, {'caption': prefix_block})
                    except Exception as _e:
                        self._log(f"  ⚠️ Bird species DB write failed [{file_prefix}]: {_e}", "warning")

                for target_file in title_targets:
                    if target_file and os.path.exists(target_file):
                        meta_item = {
                            'file': target_file,
                            'title': bird_title,
                        }
                        # V4.2.7: IUCN + GBIF 随 Title 一起写入对应 XMP 字段
                        # V4.2.7: Push IUCN + GBIF alongside Title in one EXIF batch.
                        if iucn_category:
                            meta_item['iucn_category'] = iucn_category
                        if gbif_rarity_100 is not None:
                            meta_item['gbif_rarity_100'] = gbif_rarity_100
                        if aesthetic_index is not None:
                            meta_item['aesthetic_index'] = aesthetic_index
                        # 鸟名关键字(Paul P1-1):开关开启时随 Title 一起 merge-add
                        # 写入 XMP-dc:Subject(bird_title 已按界面语言选名)。
                        # Species keyword (Paul P1-1): when enabled, merge-add
                        # into XMP-dc:Subject alongside the Title write.
                        if self.config.birdid_write_keywords:
                            meta_item['keywords'] = [bird_title]
                        queue_metadata(meta_item)
            else:
                # 低置信度：记日志，并将候选鸟名存入 file_bird_species 供 caption 使用
                low_conf_name = (en_name or cn_name) if self.i18n.current_lang.startswith('en') else (cn_name or en_name)
                self._log(self.i18n.t(
                    "logs.birdid_low_confidence",
                    source=source_display,
                    name=low_conf_name or '?',
                    confidence=birdid_confidence,
                    threshold=self.settings.birdid_confidence_threshold,
                ))
                if cn_name:
                    self.file_bird_species[file_prefix] = {
                        'cn_name': cn_name,
                        'en_name': en_name,
                        'low_confidence': True,
                        'confidence': birdid_confidence,
                    }
                    # 低置信度：只写 Caption / DB，不写 EXIF Title，不用于分目录
                    # 将候选鸟名追加到 DB caption 最前面（备选鸟种）
                    if self.report_db:
                        try:
                            existing = self.report_db.get_photo(file_prefix) or {}
                            old_cap = existing.get('caption') or ''
                            # V4.3.0: \u5907\u9009\u9e1f\u79cd\u540d\u8ddf\u968f\u754c\u9762\u8bed\u8a00\uff08low_conf_name\uff09\uff0c\u6807\u7b7e/\u628a\u63e1\u5ea6\u8d70 i18n
                            # V4.3.0: Alt-species name follows UI language; labels via i18n.
                            bird_line = self.i18n.t(
                                "logs.caption_alt_species",
                                name=low_conf_name,
                                confidence=f"{birdid_confidence:.0f}",
                            )
                            if old_cap and not old_cap.startswith(('\u5907\u9009\u9e1f\u79cd', 'Alt. species')):
                                self.report_db.update_photo(file_prefix, {'caption': bird_line + '\n' + old_cap})
                            elif not old_cap:
                                self.report_db.update_photo(file_prefix, {'caption': bird_line})
                        except Exception as _e:
                            self._log(f"  \u26a0\ufe0f Low conf caption update failed [{file_prefix}]: {_e}", "warning")


        def collect_birdid_tasks(wait: bool = False):
            """Collect completed BirdID tasks.
            Non-blocking mode drains only finished tasks to keep logs near per-photo processing.
            """
            while birdid_tasks:
                future, file_prefix, title_targets, source_filename = birdid_tasks[0]
                if not wait and not future.done():
                    break

                birdid_tasks.popleft()
                try:
                    if wait:
                        birdid_wait_start = time.time()
                        birdid_result = future.result()
                        self._perf_add_stage('birdid_wait', (time.time() - birdid_wait_start) * 1000)
                    else:
                        birdid_result = future.result()
                    birdid_apply_start = time.time()
                    apply_birdid_result(file_prefix, title_targets, birdid_result, source_filename)
                    self._perf_add_stage('birdid_apply', (time.time() - birdid_apply_start) * 1000)
                except Exception as e:
                    self._log(f"  ⚠️ Bird ID failed [{source_filename or file_prefix}]: {e}", "warning")
                # 失败任务同样计入已完成单元，避免进度条卡在收尾阶段
                # Failed tasks also count as done units so the bar never stalls
                progress_state['birdid_done'] += 1
                update_unit_progress()
        
        # 轻量 Job 调度：在 MPS 上默认关闭 YOLO 预取，避免与 TOPIQ 并发争用
        # 如需强制开启/关闭，可通过 SUPERPICKY_YOLO_PREFETCH 覆盖。
        mps_available = False
        try:
            from config import get_best_device
            mps_available = bool(get_best_device().type == 'mps')
        except Exception:
            mps_available = False
        
        env_yolo_prefetch_raw = os.getenv("SUPERPICKY_YOLO_PREFETCH", "").strip().lower()
        if env_yolo_prefetch_raw:
            yolo_prefetch_enabled = env_yolo_prefetch_raw not in {"0", "false", "no", "off"}
        else:
            # yolo_infer_lock 已串行化所有 YOLO 推理调用，MPS 不存在并发访问风险
            yolo_prefetch_enabled = True
        
        yolo_prefetch_depth = 3
        env_yolo_prefetch_depth = os.getenv("SUPERPICKY_YOLO_PREFETCH_DEPTH", "").strip()
        if env_yolo_prefetch_depth.isdigit():
            yolo_prefetch_depth = max(2, int(env_yolo_prefetch_depth))
        
        yolo_result_queue = queue.Queue(maxsize=yolo_prefetch_depth) if yolo_prefetch_enabled else None
        yolo_prefetch_thread = None
        yolo_infer_lock = threading.Lock()
        focus_exif_lock = threading.Lock()

        def normalize_path_for_match(path_value: str) -> str:
            """Normalize separators so cache-path checks work on both Windows and POSIX."""
            return str(path_value).replace("\\", "/")
        
        def resolve_file_context(in_filename: str) -> Dict[str, any]:
            in_filepath = os.path.join(self.dir_path, in_filename)
            in_file_prefix, _ = os.path.splitext(in_filename)
            in_filename_norm = normalize_path_for_match(in_filename)
            
            # V4.0.4: 从 tmp_*.jpg 提取原始文件前缀用于匹配 raw_dict
            # V4.1.0: 兼容 .superpicky/cache/ 下的临时文件
            in_original_prefix = in_file_prefix
            if in_file_prefix.startswith('tmp_'):
                in_original_prefix = in_file_prefix[4:]  # 去掉 "tmp_" 前缀
            elif '.superpicky/cache' in in_filename_norm:
                # 处理缓存文件路径: .superpicky/cache/_Z9W0291.jpg -> _Z9W0291
                in_original_prefix = os.path.splitext(os.path.basename(in_filename))[0]
            
            in_raw_ext = raw_dict.get(in_original_prefix)
            in_raw_path = os.path.join(self.dir_path, in_original_prefix + in_raw_ext) if in_raw_ext else None
            in_can_read_focus_raw = bool(
                in_raw_ext and in_raw_ext.lower() in focus_supported_raw_exts and in_raw_path and os.path.exists(in_raw_path)
            )
            
            return {
                'filename': in_filename,
                'filepath': in_filepath,
                'file_prefix': in_file_prefix,
                'original_prefix': in_original_prefix,
                'raw_ext': in_raw_ext,
                'raw_path': in_raw_path,
                'can_read_focus_raw': in_can_read_focus_raw,
            }
        
        def run_yolo_detection(
            in_filepath: str,
            focus_point: Optional[Tuple[float, float]] = None,
            decoded_image: Optional[np.ndarray] = None,
        ):
            # 单模型实例在”预取线程 + 主线程复选”两处复用，串行化推理调用以保证稳定性
            with yolo_infer_lock:
                return detect_and_draw_birds(
                    in_filepath, _yolo_model_box[0], None, self.dir_path, ui_settings, None,
                    skip_nima=True, focus_point=focus_point,
                    report_db=self.report_db,
                    decoded_image=decoded_image,
                )
        
        def read_focus_result_safe(in_raw_path: Optional[str]):
            if not in_raw_path:
                return None
            with focus_exif_lock:
                focus_detector = get_focus_detector()
                return focus_detector.detect(in_raw_path)
        
        def read_iso_safe(in_filepath: Optional[str]):
            if not in_filepath:
                return None
            with focus_exif_lock:
                return self._read_iso(in_filepath)

        def read_detail_exif_safe(ctx: Dict[str, object], prefetched: Optional[dict]) -> dict:
            """
            为早期拒绝照片读取结果浏览器可显示的相机元数据。

            优先复用 EXIF 预取结果；未预取时按 RAW → 当前 JPEG 的顺序读取，避免重复散落的读取逻辑。

            Read camera metadata for early-rejected photos that the result
            browser can display.

            Reuse prefetched EXIF first; when it is unavailable, read RAW then
            current JPEG so the fallback order stays consistent in one place.
            """
            if prefetched:
                return dict(prefetched)

            candidates = [
                ctx.get("raw_path"),
                ctx.get("filepath"),
            ]
            for candidate in candidates:
                if not candidate or not os.path.exists(str(candidate)):
                    continue
                with focus_exif_lock:
                    exif_data = self._read_all_exif_metadata(str(candidate))
                if exif_data and any(v is not None for v in exif_data.values()):
                    return exif_data
            return {}

        def calculate_rejected_quality_detail(
            in_filepath: str,
            decoded_bgr: Optional[np.ndarray] = None,
        ) -> dict:
            """
            为无鸟/早期拒绝照片计算可定义的质量详情。

            无鸟照片没有鸟头区域和鸟框，因此这里使用整张图的 Tenengrad 锐度与整张图 TOPIQ 美学分。
            这些值仅用于结果浏览器展示，不参与原有评星逻辑。
            优先复用预取阶段已解码的 BGR 图（decoded_bgr），避免对同一张图
            重复 imdecode（45MP 约 200-400ms/张）。

            Calculate defined quality detail for no-bird/early-rejected photos.

            A no-bird photo has no bird head region or bird bbox, so this uses
            whole-image Tenengrad sharpness and whole-image TOPIQ aesthetics.
            These values are for result-browser display only and do not affect
            the existing rating logic. Reuses the prefetch-stage decoded BGR
            frame (decoded_bgr) when available to skip a duplicate imdecode
            (~200-400ms per 45MP frame).
            """
            nonlocal topiq_scorer
            import cv2

            image_bgr = decoded_bgr
            if image_bgr is None:
                try:
                    image_bgr = cv2.imdecode(
                        np.fromfile(in_filepath, dtype=np.uint8),
                        cv2.IMREAD_COLOR,
                    )
                except Exception:
                    image_bgr = None

            if image_bgr is None:
                return {}

            detail = {}
            try:
                image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                full_mask = np.ones(image_rgb.shape[:2], dtype=np.uint8)
                whole_sharpness = keypoint_detector._calculate_sharpness(
                    image_rgb,
                    full_mask,
                )
                detail["head_sharp"] = whole_sharpness
                detail["adj_sharpness"] = whole_sharpness
            except Exception:
                pass

            try:
                scorer = topiq_scorer
                if scorer is None:
                    from iqa_scorer import get_iqa_scorer
                    from config import get_best_device
                    scorer = get_iqa_scorer(device=get_best_device().type)
                    topiq_scorer = scorer
                whole_topiq = scorer.calculate_from_array(image_bgr)
                if whole_topiq is not None:
                    detail["nima_score"] = whole_topiq
                    detail["adj_topiq"] = whole_topiq
            except Exception:
                pass

            return detail
        
        def build_yolo_item(index: int, in_filename: str) -> Dict[str, any]:
            ctx = resolve_file_context(in_filename)
            in_filepath = ctx['filepath']
            
            yolo_start = time.time()
            decoded_image = read_image_bgr(in_filepath)
            yolo_result = None
            yolo_error = None
            try:
                yolo_result = run_yolo_detection(in_filepath, None, decoded_image)
                if yolo_result is None:
                    yolo_error = self.i18n.t("logs.cannot_process", filename=in_filename)
            except Exception as e:
                yolo_error = self.i18n.t("logs.processing_error", filename=in_filename, error=str(e))
            
            return {
                'index': index,
                'filename': ctx['filename'],
                'filepath': ctx['filepath'],
                'file_prefix': ctx['file_prefix'],
                'original_prefix': ctx['original_prefix'],
                'raw_ext': ctx['raw_ext'],
                'raw_path': ctx['raw_path'],
                'can_read_focus_raw': ctx['can_read_focus_raw'],
                'decoded_image': decoded_image,
                'result': yolo_result,
                'error': yolo_error,
                'yolo_ms': (time.time() - yolo_start) * 1000,
            }
        
        # MPS 上每 N 张照片强制重载 YOLO，防止 MPS 显存状态累积导致模型输出崩溃
        # 经实测：M5 在处理 5000 张时约第 1900 张完全失效，300 张间隔可有效预防
        _YOLO_MPS_RELOAD_INTERVAL = 300

        def _reload_yolo_if_mps():
            """在 yolo_infer_lock 保护下重载 YOLO，完整释放旧模型的 MPS 状态。"""
            if not mps_available:
                return
            with yolo_infer_lock:
                old_model = _yolo_model_box[0]
                _yolo_model_box[0] = None
                del old_model
                try:
                    import torch, gc
                    torch.mps.empty_cache()
                    gc.collect()
                except Exception:
                    pass
                _yolo_model_box[0] = load_yolo_model()
            self._log(f"  🔄 YOLO 模型已重载（MPS 显存复位）", "info")

        if yolo_prefetch_enabled and yolo_result_queue is not None:
            def yolo_prefetch_worker():
                try:
                    for idx, queued_filename in enumerate(files_tbr, 1):
                        # MPS 周期重载：在推理前执行，确保新模型处理后续批次
                        if mps_available and idx > 1 and (idx - 1) % _YOLO_MPS_RELOAD_INTERVAL == 0:
                            _reload_yolo_if_mps()
                        yolo_result_queue.put(build_yolo_item(idx, queued_filename))
                finally:
                    # 结束哨兵，保证主线程可正常退出
                    yolo_result_queue.put(None)
            
            yolo_prefetch_thread = threading.Thread(
                target=yolo_prefetch_worker,
                daemon=True,
                name="sp-yolo-prefetch"
            )
            yolo_prefetch_thread.start()
            if self._perf_enabled:
                self._log(f"  ⚙️ YOLO prefetch: on (depth={yolo_prefetch_depth})")
        elif self._perf_enabled:
            if env_yolo_prefetch_raw:
                self._log("  ⚙️ YOLO prefetch: off")
            else:
                self._log(f"  ⚙️ YOLO prefetch: off (auto, mps={'on' if mps_available else 'off'})")
        
        # EXIF 异步预取：把 EXIF 元数据读取与主流程并行，减少主线程等待
        # V2: 扩展为读取所有 EXIF 字段（相机设置、GPS、IPTC、时间等）
        env_exif_prefetch = os.getenv("SUPERPICKY_EXIF_PREFETCH", "1").strip().lower()
        exif_prefetch_enabled = env_exif_prefetch not in {"0", "false", "no", "off"}
        exif_prefetch_thread = None
        exif_prefetch_results = {}
        exif_prefetch_done = False
        exif_prefetch_cond = threading.Condition()

        def cancel_processing() -> None:
            if not self._should_stop():
                return
            if metadata_async_enabled and metadata_queue is not None:
                try:
                    metadata_queue.put_nowait(None)
                except Exception:
                    pass
            if birdid_executor is not None:
                try:
                    birdid_executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    birdid_executor.shutdown(wait=False)
                except Exception:
                    pass
            try:
                inference_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                inference_pool.shutdown(wait=False)
            except Exception:
                pass
            if self.report_db:
                try:
                    self.report_db.close()
                except Exception:
                    pass
                self.report_db = None
            raise ProcessingCancelled("Processing cancelled")
        
        if exif_prefetch_enabled:
            def exif_prefetch_worker():
                nonlocal exif_prefetch_done
                try:
                    for idx, queued_filename in enumerate(files_tbr, 1):
                        ctx = resolve_file_context(queued_filename)
                        prefetched_exif = None
                        # 优先从 RAW 文件读取
                        if ctx['raw_path'] and os.path.exists(ctx['raw_path']):
                            with focus_exif_lock:
                                prefetched_exif = self._read_all_exif_metadata(ctx['raw_path'])
                        # 回退到 JPEG
                        if prefetched_exif is None or prefetched_exif.get('iso') is None:
                            with focus_exif_lock:
                                prefetched_exif = self._read_all_exif_metadata(ctx['filepath'])
                        with exif_prefetch_cond:
                            exif_prefetch_results[idx] = prefetched_exif
                            exif_prefetch_cond.notify_all()
                finally:
                    with exif_prefetch_cond:
                        exif_prefetch_done = True
                        exif_prefetch_cond.notify_all()
            
            exif_prefetch_thread = threading.Thread(
                target=exif_prefetch_worker,
                daemon=True,
                name="sp-exif-prefetch"
            )
            exif_prefetch_thread.start()
            if self._perf_enabled:
                self._log("  ⚙️ EXIF prefetch: on (v2: full metadata)")
        elif self._perf_enabled:
            self._log("  ⚙️ EXIF prefetch: off")

        # 周期性 GPU 显存清理间隔（MPS 每 50 张，CUDA 每 200 张）
        # 提前计算避免在循环内 import torch 引发 UnboundLocalError
        try:
            import torch as _torch_module
            import gc as _gc_module
            # 以 get_best_device() 为唯一真相源，与 Intel Mac 走 CPU 的策略保持一致，
            # 避免 raw mps.is_available() 在 Intel+老 AMD 卡上误报 True 而做无谓的 mps 缓存清理。
            # Use get_best_device() as the single source of truth so Intel Macs (which run on
            # CPU) don't trigger pointless MPS cache clears from a raw is_available() check.
            from config import get_best_device
            _device_type = get_best_device().type
            _use_mps = (_device_type == 'mps')
            _use_cuda = (_device_type == 'cuda')
            _cache_interval = 50 if _use_mps else 200
        except Exception:
            _torch_module = None
            _gc_module = None
            _use_mps = False
            _use_cuda = False
            _cache_interval = 200

        for local_index in range(1, len(files_tbr) + 1):
            cancel_processing()
            i = display_start + local_index - 1
            photo_stage_ms = {}
            
            def add_photo_stage(stage: str, ms: float):
                photo_stage_ms[stage] = photo_stage_ms.get(stage, 0.0) + max(0.0, float(ms))

            # 单张照片的预设日志占位符：即便下面的处理在拿到 yolo_item/original_prefix
            # 之前就抛异常，except 分支也有值可用于日志和续跑标记。
            # Pre-set fallbacks for logging/resume-marking so the except branch below
            # always has something usable even if the body raises before yolo_item /
            # original_prefix are assigned.
            filename = files_tbr[local_index - 1]
            original_prefix = None

            try:
                # Non-blocking BirdID harvest so logs appear during per-photo processing.
                collect_birdid_tasks(wait=False)
            
                # 从预取队列获取 YOLO 结果；未启用预取时回退为同步执行
                if yolo_result_queue is not None:
                    yolo_wait_start = time.time()
                    while True:
                        cancel_processing()
                        try:
                            yolo_item = yolo_result_queue.get(timeout=0.1)
                            break
                        except queue.Empty:
                            continue
                    yolo_wait_ms = (time.time() - yolo_wait_start) * 1000
                    if yolo_wait_ms > 0.1:
                        add_photo_stage('yolo_queue_wait', yolo_wait_ms)
                    if yolo_item is None:
                        break
                else:
                    filename_inline = files_tbr[local_index - 1]
                    yolo_item = build_yolo_item(i, filename_inline)
            
                prefetched_exif = None
                exif_prefetched = False
                if exif_prefetch_enabled:
                    exif_wait_start = time.time()
                    with exif_prefetch_cond:
                        while local_index not in exif_prefetch_results and not exif_prefetch_done:
                            cancel_processing()
                            exif_prefetch_cond.wait(timeout=0.01)
                        if local_index in exif_prefetch_results:
                            prefetched_exif = exif_prefetch_results.pop(local_index)
                            exif_prefetched = True
                    exif_wait_ms = (time.time() - exif_wait_start) * 1000
                    if exif_wait_ms > 0.1:
                        add_photo_stage('exif_prefetch_wait', exif_wait_ms)
            
                # 从预取结果中提取 ISO（用于锐度归一化）
                prefetched_iso_value = None
                if prefetched_exif and prefetched_exif.get('iso'):
                    prefetched_iso_value = prefetched_exif['iso']
            
                yolo_ms = yolo_item.get('yolo_ms', 0.0) or 0.0
                add_photo_stage('yolo', yolo_ms)
            
                filename = yolo_item['filename']
                filepath = yolo_item['filepath']
                file_prefix = yolo_item['file_prefix']
                file_prefix = yolo_item['file_prefix']
                original_prefix = yolo_item['original_prefix']
            
                # V4.1: 更新路径信息到数据库
                path_update_data = {}
                yolo_filename_norm = normalize_path_for_match(yolo_item.get('filename', ''))
                yolo_filepath_norm = normalize_path_for_match(yolo_item.get('filepath', ''))
            
                # 1. original_path
                if yolo_item.get('raw_path'):
                     path_update_data['original_path'] = os.path.relpath(yolo_item['raw_path'], self.dir_path)
                elif not str(yolo_item.get('file_prefix', '')).startswith('tmp_') and '.superpicky/cache' not in yolo_filename_norm:
                     path_update_data['original_path'] = os.path.relpath(yolo_item['filepath'], self.dir_path)
            
                # 2. temp_jpeg_path
                if '.superpicky/cache' in yolo_filepath_norm:
                     path_update_data['temp_jpeg_path'] = os.path.relpath(yolo_item['filepath'], self.dir_path)
                elif str(yolo_item.get('file_prefix', '')).startswith('tmp_'):
                     path_update_data['temp_jpeg_path'] = yolo_item['filename']
                elif yolo_item.get('filepath', '').lower().endswith(('.jpg', '.jpeg')):
                     # RAW+JPG 配对照片或纯 JPG：直接将 JPG 路径写入 temp_jpeg_path
                     path_update_data['temp_jpeg_path'] = os.path.relpath(yolo_item['filepath'], self.dir_path)
                 
                raw_ext = yolo_item['raw_ext']
                raw_path = yolo_item['raw_path']
                can_read_focus_raw = yolo_item['can_read_focus_raw']

                writable_targets = []
                if raw_path and os.path.exists(raw_path):
                    writable_targets.append(raw_path)

                filepath_basename = os.path.basename(filepath).lower()
                is_temp_preview_path = (
                    '.superpicky/cache' in yolo_filepath_norm or
                    filepath_basename.startswith(('tmp_', 'temp_'))
                )
                if filepath and os.path.exists(filepath) and not is_temp_preview_path and filepath not in writable_targets:
                    writable_targets.append(filepath)

                for original_file_path in writable_targets:
                    try:
                        clear_readonly_attribute(original_file_path)
                    except Exception as e:
                        self._log(
                            f"  ⚠️ 移除只读属性失败 [{os.path.basename(original_file_path)}]: {e}",
                            "warning"
                        )
            
                # 后处理阶段开始时间（最终日志会叠加 yolo_ms，保持单图耗时口径一致）
                photo_start_time = time.time()
            
                # 延迟对焦点读取：仅在必要时触发，避免在早期退出样本上浪费 IO
                preloaded_focus_result = None
                focus_point_for_selection = None
            
                # 更新进度（统一工作单元口径：照片 + 识鸟任务）
                # Update progress (unified work units: photos + BirdID tasks)
                progress_state['photos_done'] = i
                should_update = (i % 5 == 0 or i == total_files or i == 1)
                if should_update:
                    update_unit_progress()

                if i % _cache_interval == 0 and _torch_module is not None:
                    try:
                        if _use_mps:
                            _torch_module.mps.empty_cache()
                            self._log(self.i18n.t("logs.mps_cache_cleared", index=i), "info")
                        elif _use_cuda:
                            _torch_module.cuda.empty_cache()
                            self._log(self.i18n.t("logs.cuda_cache_cleared", index=i), "info")
                        else:
                            self._log(f"  🧹 [第{i}张] GC 已执行", "info")
                        _gc_module.collect()
                    except Exception:
                        pass

                # 非预取模式下的 MPS YOLO 周期重载（预取模式已在 worker 里处理）
                if (not yolo_prefetch_enabled) and mps_available and i > 1 and (i - 1) % _YOLO_MPS_RELOAD_INTERVAL == 0:
                    _reload_yolo_if_mps()
            
                result = yolo_item.get('result')
                if result is None:
                    self._log(yolo_item.get('error') or self.i18n.t("logs.cannot_process", filename=filename), "error")
                    mark_resume_completed(original_prefix)
                    continue
            
                # V4.2: 解构 AI 结果（现在有 10 个返回值，包含 bird_count 和 rescued）
                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count, rescued = result
            
                # 多鸟场景才补读对焦点，并在需要时做一次 YOLO 复选（避免全量样本都读 RAW 对焦）
                if detected and bird_count > 1 and can_read_focus_raw:
                    pre_focus_start = time.time()
                    try:
                        preloaded_focus_result = read_focus_result_safe(raw_path)
                        if preloaded_focus_result is not None:
                            focus_point_for_selection = (preloaded_focus_result.x, preloaded_focus_result.y)
                    except Exception:
                        preloaded_focus_result = None
                    add_photo_stage('focus_prefetch', (time.time() - pre_focus_start) * 1000)
                
                    if focus_point_for_selection is not None:
                        refine_start = time.time()
                        try:
                            refined_result = run_yolo_detection(
                                filepath,
                                focus_point_for_selection,
                                yolo_item.get('decoded_image'),
                            )
                            if refined_result is not None:
                                detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count, rescued = refined_result
                        except Exception:
                            pass
                        add_photo_stage('yolo_refine', (time.time() - refine_start) * 1000)
            
                # V4.1/V4.2: 无鸟或低置信度先标记为拒绝；是否早期退出由详情元数据设置决定。
                # V4.1/V4.2: Mark no-bird/low-confidence photos as rejected first;
                # detail-metadata settings decide whether the processor can exit early.
                confidence_threshold = self.settings.ai_confidence / 100.0
                # V4.6: 救回照片已经过两因子核验(YOLO候选+鸟种分类器)，豁免二次
                # 置信度门槛——否则弱候选救回(conf≈0.3)会被默认0.5阈值再杀一遍。
                # V4.6: Rescued photos passed two-factor verification (YOLO
                # candidate + species classifier); exempt them from this gate,
                # otherwise the default 0.5 threshold would re-kill weak rescues.
                rejected_by_detection = not detected or (
                    detected and not rescued and confidence < confidence_threshold)
                needs_expensive_rejected_detail = (
                    detail_metadata_for_rejected
                    and detected
                )
                if rejected_by_detection and not needs_expensive_rejected_detail:
                    photo_time_ms = (time.time() - photo_start_time) * 1000 + yolo_ms
                
                    if not detected:
                        rating_value = -1
                        reason = self.i18n.t("logs.reject_no_bird")
                    else:
                        rating_value = 0
                        # V4.2: Show actual confidence and threshold
                        reason = self.i18n.t("logs.quality_low_confidence", confidence=confidence, threshold=confidence_threshold)
                
                    # 简化日志
                    self._log_photo_result_simple(i, total_files, filename, rating_value, reason, photo_time_ms, False, False, None)
                
                    # 记录统计
                    self._update_stats(rating_value, False, False)
                
                    # 记录评分（用于文件移动）- V4.0.4: 使用 original_prefix 确保匹配 NEF
                    self.file_ratings[original_prefix] = rating_value

                    if path_update_data and self.report_db:
                        self.report_db.update_photo(original_prefix, path_update_data)

                    if detail_metadata_for_rejected and self.report_db:
                        rejected_detail = {
                            'filename': original_prefix,
                            'has_bird': 1 if detected else 0,
                            'confidence': confidence,
                            'rating': rating_value,
                            'caption': f"{rating_value}星 | {reason}",
                        }
                        rejected_detail.update(
                            read_detail_exif_safe(yolo_item, prefetched_exif)
                        )
                        rejected_detail.update(
                            calculate_rejected_quality_detail(
                                filepath, yolo_item.get('decoded_image'))
                        )
                        self.report_db.insert_photo(rejected_detail)
                
                    # 写入简化 EXIF
                    if original_prefix in raw_dict:
                        raw_extension = raw_dict[original_prefix]
                        target_file_path = os.path.join(self.dir_path, original_prefix + raw_extension)
                        if os.path.exists(target_file_path):
                            queue_metadata({
                                'file': target_file_path,
                                'rating': 0 if rating_value >= 0 else 0,  # -1星也写0
                                'pick': -1 if rating_value == -1 else 0,
                                'sharpness': None,
                                'nima_score': None,
                                'label': None,
                                'focus_status': None,
                                'caption': f"{rating_value}星 | {reason}",
                            })
                
                    mark_resume_completed(original_prefix)
                    self._perf_record_photo(photo_time_ms, photo_stage_ms, early_exit=True)

                    # 即使置信度不足，只要检测到鸟就生成 crop_debug 供浏览预览
                    # (yolo_debug_path 已由 ai_model.py 写入 DB，crop_debug 同步生成保持一致)
                    should_build_debug = bool(self.callbacks.crop_preview or self.settings.save_crop)
                    if detected and should_build_debug and bird_bbox is not None and img_dims is not None:
                        try:
                            _orig = yolo_item.get('decoded_image')
                            if _orig is not None:
                                _h, _w = _orig.shape[:2]
                                _sw, _sh = img_dims
                                _sx, _sy = _w / _sw, _h / _sh
                                _bx, _by, _bw, _bh = bird_bbox
                                _ox = int(max(0, _bx * _sx))
                                _oy = int(max(0, _by * _sy))
                                _ow = int(min(_bw * _sx, _w - _ox))
                                _oh = int(min(_bh * _sy, _h - _oy))
                                _crop = _orig[_oy:_oy + _oh, _ox:_ox + _ow]
                                if _crop.size > 0:
                                    debug_img = self._save_debug_crop(
                                        filename,
                                        _crop,
                                        write_file=self.settings.save_crop,
                                    )
                                    if debug_img is not None and self.callbacks.crop_preview:
                                        self.callbacks.crop_preview(debug_img, None)
                        except Exception:
                            pass

                    continue  # 跳过后续所有检测
            
                # Phase 2: 关键点检测（在裁剪区域上执行，更准确）
                all_keypoints_hidden = False
                both_eyes_hidden = False  # 保留用于日志/调试
                best_eye_visibility = 0.0  # V3.8: 眼睛最高置信度，用于封顶逻辑
                head_sharpness = 0.0
                flight_future = None  # 与关键点阶段并行提交飞版检测
                has_visible_eye = False
                has_visible_beak = False
                left_eye_vis = 0.0
                right_eye_vis = 0.0
                beak_vis = 0.0
            
                # V3.9: 头部区域信息（用于对焦验证）
                head_center_orig = None
                head_radius_val = None
            
                # V3.9.4: 原图尺寸和裁剪偏移（用于对焦点坐标转换）
                # 这些变量必须在循环开始时初始化，确保后续代码可用
                w_orig, h_orig = None, None
                x_orig, y_orig = 0, 0  # 裁剪偏移默认为 0
            
                # V3.2优化: 只读取原图一次，在关键点检测和NIMA计算中复用
                orig_img = None  # 原图缓存
                bird_crop_bgr = None  # 裁剪区域缓存（BGR）
                bird_crop_mask = None # 裁剪区域掩码缓存
                bird_mask_orig = None  # V3.9: 原图尺寸的分割掩码（用于对焦验证）
            
                keypoint_start = time.time()
                if use_keypoints and detected and bird_bbox is not None and img_dims is not None:
                    try:
                        import cv2
                        orig_img = yolo_item.get('decoded_image')
                        if orig_img is None:
                            orig_img = read_image_bgr(filepath)
                        if orig_img is not None:
                            h_orig, w_orig = orig_img.shape[:2]
                            # 获取YOLO处理时的图像尺寸
                            w_resized, h_resized = img_dims
                        
                            # 计算缩放比例：原图 / 缩放图
                            scale_x = w_orig / w_resized
                            scale_y = h_orig / h_resized
                        
                            # 将bbox从缩放尺寸转换到原图尺寸
                            x, y, w, h = bird_bbox
                            x_orig = int(x * scale_x)
                            y_orig = int(y * scale_y)
                            w_orig_box = int(w * scale_x)
                            h_orig_box = int(h * scale_y)
                        
                            # V4.3: 与 BirdID 保持一致，加 15% padding
                            # 防止鸟头在 bbox 边缘时被裁切，导致关键点模型看不到眼睛
                            pad = int(max(w_orig_box, h_orig_box) * 0.15)
                            x_orig_pad = max(0, x_orig - pad)
                            y_orig_pad = max(0, y_orig - pad)
                            x2_pad = min(w_orig, x_orig + w_orig_box + pad)
                            y2_pad = min(h_orig, y_orig + h_orig_box + pad)
                            # 更新裁切区域（含 padding）
                            x_orig = x_orig_pad
                            y_orig = y_orig_pad
                            w_orig_box = x2_pad - x_orig_pad
                            h_orig_box = y2_pad - y_orig_pad
                        
                            # 确保边界有效
                            x_orig = max(0, min(x_orig, w_orig - 1))
                            y_orig = max(0, min(y_orig, h_orig - 1))
                            w_orig_box = min(w_orig_box, w_orig - x_orig)
                            h_orig_box = min(h_orig_box, h_orig - y_orig)
                        
                            # 裁剪鸟的区域（保存BGR版本供关键点/飞版/曝光使用）
                            # .copy() 断开对 orig_img 的 view 依赖，使 orig_img 可在 TOPIQ 后提前释放
                            bird_crop_bgr = orig_img[y_orig:y_orig+h_orig_box, x_orig:x_orig+w_orig_box].copy()
                        
                            # 同样裁剪 mask (如果存在)
                            if bird_mask is not None:
                                # 缩放 mask 到原图尺寸 (Mask是整图的)
                                # bird_mask 是 (h_resized, w_resized)，需要放大到 (h_orig, w_orig)
                                if bird_mask.shape[:2] != (h_orig, w_orig):
                                    # 使用最近邻插值保持二值特性
                                    bird_mask_orig = cv2.resize(bird_mask, (w_orig, h_orig), interpolation=cv2.INTER_NEAREST)
                                else:
                                    bird_mask_orig = bird_mask
                                
                                bird_crop_mask = bird_mask_orig[y_orig:y_orig+h_orig_box, x_orig:x_orig+w_orig_box]
                        
                            if bird_crop_bgr.size > 0:
                                # 关键点与飞版并行：飞版在线程池异步执行，主线程继续关键点检测
                                if use_flight:
                                    try:
                                        flight_future = inference_pool.submit(flight_detector.detect, bird_crop_bgr)
                                    except Exception:
                                        flight_future = None
                            
                                crop_rgb = cv2.cvtColor(bird_crop_bgr, cv2.COLOR_BGR2RGB)
                                # 在裁剪区域上进行关键点检测，传入分割掩码
                                kp_result = keypoint_detector.detect(
                                    crop_rgb, 
                                    box=(x_orig, y_orig, w_orig_box, h_orig_box),
                                    seg_mask=bird_crop_mask  # 传入分割掩码
                                )
                                if kp_result is not None:
                                    both_eyes_hidden = kp_result.both_eyes_hidden  # 保留兼容
                                    all_keypoints_hidden = kp_result.all_keypoints_hidden  # 新属性
                                    best_eye_visibility = kp_result.best_eye_visibility  # V3.8
                                    has_visible_eye = kp_result.visible_eye is not None
                                    has_visible_beak = kp_result.beak_vis >= 0.3  # V3.8: 降低到 0.3
                                    left_eye_vis = kp_result.left_eye_vis
                                    right_eye_vis = kp_result.right_eye_vis
                                    beak_vis = kp_result.beak_vis
                                    head_sharpness = kp_result.head_sharpness
                                
                                    # V3.9: 计算头部区域中心和半径（用于对焦验证）
                                    ch, cw = bird_crop_bgr.shape[:2]
                                    # 选择更可见的眼睛作为头部中心
                                    if left_eye_vis >= right_eye_vis and left_eye_vis >= 0.3:
                                        eye_px = (int(kp_result.left_eye[0] * cw), int(kp_result.left_eye[1] * ch))
                                    elif right_eye_vis >= 0.3:
                                        eye_px = (int(kp_result.right_eye[0] * cw), int(kp_result.right_eye[1] * ch))
                                    else:
                                        eye_px = None
                                
                                    if eye_px is not None:
                                        # 转换到原图坐标
                                        head_center_orig = (eye_px[0] + x_orig, eye_px[1] + y_orig)
                                        # 计算半径
                                        beak_px = (int(kp_result.beak[0] * cw), int(kp_result.beak[1] * ch))
                                        if beak_vis >= 0.3:
                                            import math
                                            dist = math.sqrt((eye_px[0] - beak_px[0])**2 + (eye_px[1] - beak_px[1])**2)
                                            head_radius_val = int(dist * 1.2)
                                        else:
                                            head_radius_val = int(max(cw, ch) * 0.15)
                                        head_radius_val = max(20, min(head_radius_val, min(cw, ch) // 2))
                    except Exception as e:
                        self._log(f"  ⚠️ Keypoint detection error: {e}", "warning")
                        # import traceback
                        # self._log(traceback.format_exc(), "error")
                        pass
                    add_photo_stage('keypoint', (time.time() - keypoint_start) * 1000)
            
                # Phase 3: 根据关键点可见性决定是否计算TOPIQ
                # V4.0: 眼睛可见度 < 30% 时也跳过 TOPIQ（节省时间）
                topiq = None
                if detected and not all_keypoints_hidden and best_eye_visibility >= 0.3:
                    # 双眼可见，需要计算NIMA以进行星级判定
                    topiq_start = time.time()
                    try:
                        import time as time_module
                    
                        step_start = time_module.time()
                        scorer = topiq_scorer
                        if scorer is None:
                            from iqa_scorer import get_iqa_scorer
                            from config import get_best_device
                            scorer = get_iqa_scorer(device=get_best_device().type)
                            topiq_scorer = scorer
                    
                        # V4.6(rating-v2/T2): TOPIQ 改打鸟裁剪区。整图分在鸟占画面
                        # 小时实测≈给背景打分(与裁剪分相关性仅 0.24)，裁剪分才反映
                        # 「这只鸟拍得好不好」；无裁剪时回退整图，保持向后兼容。
                        # V4.6 (rating-v2/T2): score TOPIQ on the bird crop. When the
                        # bird is small, whole-frame TOPIQ effectively rates the
                        # background (r=0.24 vs crop, measured); fall back to the
                        # whole image when no crop exists.
                        if bird_crop_bgr is not None and bird_crop_bgr.size > 0:
                            topiq = scorer.calculate_from_array(bird_crop_bgr)
                        elif orig_img is not None:
                            topiq = scorer.calculate_from_array(orig_img)
                        else:
                            topiq = scorer.calculate_nima(filepath)
                    except Exception as e:
                        pass  # V3.3: 简化日志，静默 TOPIQ 计算失败
                    finally:
                        # TOPIQ 计算后立即释放原图（bird_crop_bgr 已是独立 copy，不受影响）
                        del orig_img
                        orig_img = None
                    add_photo_stage('topiq', (time.time() - topiq_start) * 1000)
                # V3.8: 移除跳过日志，改用 all_keypoints_hidden 后跳过的情况会少很多
            
                # Phase 4: V3.4 飞版检测（在鸟的裁剪区域上执行）
                is_flying = False
                flight_confidence = 0.0
                flight_stage_start = time.time()
                if flight_future is not None:
                    try:
                        flight_result = flight_future.result()
                        is_flying = flight_result.is_flying
                        flight_confidence = flight_result.confidence
                    except Exception as e:
                        self._log(f"  ⚠️ Flight detection error: {e}", "warning")
                elif use_flight and detected and bird_crop_bgr is not None and bird_crop_bgr.size > 0:
                    try:
                        flight_result = flight_detector.detect(bird_crop_bgr)
                        is_flying = flight_result.is_flying
                        flight_confidence = flight_result.confidence
                        # DEBUG: 输出飞版检测结果
                        # self._log(f"  🦅 飞版检测: is_flying={is_flying}, conf={flight_confidence:.2f}")
                    except Exception as e:
                        self._log(f"  ⚠️ Flight detection error: {e}", "warning")
                if flight_future is not None or (use_flight and detected and bird_crop_bgr is not None and bird_crop_bgr.size > 0):
                    add_photo_stage('flight', (time.time() - flight_stage_start) * 1000)
            
                # Phase 5: V3.8 曝光检测（在鸟的裁剪区域上执行）
                is_overexposed = False
                is_underexposed = False
                if self.settings.detect_exposure and detected and bird_crop_bgr is not None and bird_crop_bgr.size > 0:
                    exposure_start = time.time()
                    try:
                        exposure_detector = get_exposure_detector()
                        exposure_result = exposure_detector.detect(
                            bird_crop_bgr, 
                            threshold=self.settings.exposure_threshold
                        )
                        is_overexposed = exposure_result.is_overexposed
                        is_underexposed = exposure_result.is_underexposed
                    except Exception as e:
                        pass  # 曝光检测失败不影响处理
                    add_photo_stage('exposure', (time.time() - exposure_start) * 1000)
            
                # V4.3: ISO 锐度归一化 - 高 ISO 噪点会虚高锐度值，需要补偿
                # 从 RAW 或 JPEG 读取 ISO 值并计算归一化系数
                iso_start = time.time()
                iso_value = prefetched_iso_value if exif_prefetched else None
                iso_sharpness_factor = 1.0
            
                # 未命中预取时回退为同步读取
                if not exif_prefetched:
                    # 优先从 RAW 文件读取 ISO（更可靠）
                    if raw_path and os.path.exists(raw_path):
                        iso_value = read_iso_safe(raw_path)
                
                    # 如果 RAW 没有 ISO，尝试从 JPEG 读取
                    if iso_value is None:
                        iso_value = read_iso_safe(filepath)
            
                # 计算归一化系数（ISO 800 及以下为 1.0，之后每翻倍扣 5%）
                iso_sharpness_factor = self._get_iso_sharpness_factor(iso_value)
            
                # 应用 ISO 归一化到锐度
                normalized_sharpness = head_sharpness * iso_sharpness_factor
                add_photo_stage('iso', (time.time() - iso_start) * 1000)
            
                # V4.0 优化: 先计算初步评分（不考虑对焦），只对 1 星以上做对焦检测
                # 这样 0 星和 -1 星照片不需要调用 exiftool，节省大量时间
                # V4.3: 使用 ISO 归一化后的锐度进行评分
                prelim_start = time.time()
                preliminary_result = self.rating_engine.calculate(
                    detected=detected,
                    confidence=confidence,
                    sharpness=normalized_sharpness,   # V4.3: 使用 ISO 归一化后的锐度
                    topiq=topiq,                # V4.0: 原始美学（飞鸟加成在引擎内）
                    all_keypoints_hidden=all_keypoints_hidden,
                    best_eye_visibility=best_eye_visibility,
                    is_overexposed=is_overexposed,
                    is_underexposed=is_underexposed,
                    focus_sharpness_weight=1.0,  # 初步评分不考虑对焦
                    focus_topiq_weight=1.0,
                    is_flying=False,             # 初步评分不考虑飞鸟加成
                )
                add_photo_stage('rating_pre', (time.time() - prelim_start) * 1000)
            
                # Phase 6: V4.0 对焦点验证
                # 4 层检测返回两个权重: 锐度权重 + 美学权重
                focus_start = time.time()
                focus_sharpness_weight = 1.0  # 默认无影响
                focus_topiq_weight = 1.0      # 默认无影响
                focus_x, focus_y = None, None
                focus_result = preloaded_focus_result  # 复用预读结果
                focus_data_available = focus_result is not None  # V3.9.3: 标记是否有对焦点数据
                if focus_data_available:
                    focus_x, focus_y = focus_result.x, focus_result.y
            
                # 对焦点坐标获取：默认只对潜在 1 星及以上样本补读；详情元数据开启时也服务低置信度样本。
                # Focus-point lookup: by default only for potential 1-star+ photos;
                # when detail metadata is enabled, also serve low-confidence samples.
                should_read_focus_for_detail = (
                    detail_metadata_for_rejected
                    and rejected_by_detection
                )
                if (
                    (preliminary_result.rating >= 1 or should_read_focus_for_detail)
                    and detected
                    and bird_bbox is not None
                    and img_dims is not None
                ):
                    # 只在未预读到结果时再尝试一次
                    if not focus_data_available and can_read_focus_raw:
                        pre_focus_start = time.time()
                        try:
                            focus_result = read_focus_result_safe(raw_path)
                            if focus_result is not None:
                                focus_data_available = True
                                focus_x, focus_y = focus_result.x, focus_result.y
                        except Exception:
                            pass  # 对焦检测失败不影响处理
                        add_photo_stage('focus_prefetch', (time.time() - pre_focus_start) * 1000)
            
                # V4.0: 对焦权重计算（通常仅 1 星以上；详情元数据可扩展到低置信度样本）
                # V4.0: Focus weighting, normally for 1-star+ photos; detail metadata
                # can extend it to low-confidence samples.
                if preliminary_result.rating >= 1 or should_read_focus_for_detail:
                    if focus_data_available and focus_result is not None:
                        # V3.9.4 修复：使用原图尺寸而非 resize 后的 img_dims
                        # 如果 w_orig/h_orig 为 None，使用 img_dims 作为后备
                        if w_orig is not None and h_orig is not None:
                            orig_dims = (w_orig, h_orig)
                        else:
                            orig_dims = img_dims
                    
                        # V3.9.3: 修复 BBox 坐标系不匹配 bug
                        if img_dims is not None and bird_bbox is not None:
                            scale_x = orig_dims[0] / img_dims[0]
                            scale_y = orig_dims[1] / img_dims[1]
                            bx, by, bw, bh = bird_bbox
                            bird_bbox_orig = (
                                int(bx * scale_x),
                                int(by * scale_y),
                                int(bw * scale_x),
                                int(bh * scale_y)
                            )
                        else:
                            bird_bbox_orig = bird_bbox
                    
                        # V4.0: 返回元组 (锐度权重, 美学权重)
                        focus_sharpness_weight, focus_topiq_weight = verify_focus_in_bbox(
                            focus_result, 
                            bird_bbox_orig,
                            orig_dims,
                            seg_mask=bird_mask_orig,
                            head_center=head_center_orig,
                            head_radius=head_radius_val,
                        )
                    elif raw_ext is not None:
                        # V3.9.3: 支持对焦检测的 RAW 文件但无法获取对焦点数据
                        if raw_ext.lower() in focus_supported_raw_exts and raw_path is not None:
                            # 检查是否是手动对焦模式。
                            # 走常驻 ExifTool 读进程而非每张 spawn 子进程:
                            # 子进程冷启动 Mac ~30-80ms、Windows 100-300ms
                            # (进程创建 + Defender 扫描),对焦点缺失的批次每张都付一次。
                            # Check for manual-focus mode via the persistent
                            # ExifTool read process instead of spawning a
                            # subprocess per photo (~30-80ms on Mac, 100-300ms
                            # on Windows with process creation + Defender).
                            is_manual_focus = False
                            try:
                                _fm_meta = exiftool_mgr.read_metadata(
                                    raw_path, extra_args=['-FocusMode'])
                                focus_mode = str(
                                    (_fm_meta or {}).get('FocusMode', '')).strip().lower()
                                if 'manual' in focus_mode or focus_mode == 'mf' or focus_mode == 'm':
                                    is_manual_focus = True
                            except Exception:
                                pass
                        
                            if is_manual_focus:
                                focus_sharpness_weight = 1.0
                                focus_topiq_weight = 1.0
                            else:
                                focus_sharpness_weight = 0.7
                                focus_topiq_weight = 0.9
                    # V4.7(issue#107): 锐度仲裁——BAD/WORST(权重<0.9)且鸟头实测锐度
                    # 达标(≥用户阈值,与评星硬门槛同源)时升为GOOD(0.9/1.0)。
                    # 像素证据优先于EXIF对焦点元数据;真糊照片锐度不达标维持原判。
                    # V4.7 (issue #107): sharpness arbitration — when the verdict
                    # is BAD/WORST (weight < 0.9) and the measured head sharpness
                    # meets the user threshold (same source as the rating hard
                    # gate), upgrade to GOOD (0.9/1.0). Pixel evidence beats EXIF
                    # focus-point metadata; truly-blurred shots keep the verdict.
                    _orig_focus_w = focus_sharpness_weight
                    (focus_sharpness_weight, focus_topiq_weight), _focus_arbitrated = arbitrate_focus_weights(
                        (focus_sharpness_weight, focus_topiq_weight),
                        normalized_sharpness,
                        float(self.settings.sharpness_threshold),
                    )
                    if _focus_arbitrated:
                        self._log(self.i18n.t(
                            "logs.focus_arbitrated",
                            orig=_orig_focus_w,
                            sharp=normalized_sharpness,
                            thr=float(self.settings.sharpness_threshold),
                        ))
                add_photo_stage('focus', (time.time() - focus_start) * 1000)
            
                # V4.0: 最终评分计算（传入对焦权重和飞鸟状态）
                # 注意: 现在总是重新计算，因为需要传入 is_flying 参数
                # V4.3: 使用 ISO 归一化后的锐度
                rating_final_start = time.time()
                rating_result = self.rating_engine.calculate(
                    detected=detected,
                    confidence=confidence,
                    sharpness=normalized_sharpness,  # V4.3: 使用 ISO 归一化后的锐度
                    topiq=topiq,              # V4.0: 使用原始美学，权重在引擎内应用
                    all_keypoints_hidden=all_keypoints_hidden,
                    best_eye_visibility=best_eye_visibility,
                    is_overexposed=is_overexposed,
                    is_underexposed=is_underexposed,
                    focus_sharpness_weight=focus_sharpness_weight,  # V4.0: 锐度权重
                    focus_topiq_weight=focus_topiq_weight,          # V4.0: 美学权重
                    is_flying=is_flying,                            # V4.0: 飞鸟乘法加成
                )
                add_photo_stage('rating_final', (time.time() - rating_final_start) * 1000)
            
                rating_value = rating_result.rating
                pick = rating_result.pick
                reason = rating_result.reason
            
                # V4.0: 根据 focus_sharpness_weight 计算对焦状态文本
                # 只有检测到鸟才设置对焦状态，避免无鸟照片也写入
                focus_status = None
                focus_status_en = None  # English version for debug image
                if detected:  # Only calculate focus status if bird detected
                    if focus_sharpness_weight > 1.0:
                        focus_status = "BEST"
                        focus_status_en = "BEST"
                    elif focus_sharpness_weight >= 0.9:
                        focus_status = "GOOD"
                        focus_status_en = "GOOD"
                    elif focus_sharpness_weight >= 0.7:
                        focus_status = "BAD"
                        focus_status_en = "BAD"
                    elif focus_sharpness_weight < 0.7:
                        focus_status = "WORST"
                        focus_status_en = "WORST"
            
                # V3.9: 生成调试可视化图（仅对有鸟的照片）
                # V4.6(rating-v2): 判定是否进入 V2 排序池(过全部硬门槛)。
                # 前置到 debug 预览/日志之前:池内照片星级待收尾统一分配,
                # 过程中预览与日志不显示星级,只显示指标/飞鸟/对焦。
                # V4.6 (rating-v2): pool-membership check, moved before the debug
                # preview/log — pool photos get stars in the post-pass, so the
                # live preview/log show metrics/flight/focus instead of stars.
                v2_in_pool = False
                if v2_enabled and detected:
                    _v2_exposure = is_overexposed or is_underexposed
                    _v2_metrics = PhotoMetricsV2(
                        key=original_prefix,
                        detected=True,
                        confidence=confidence,
                        norm_sharpness=normalized_sharpness,
                        topiq=topiq,
                        best_eye=best_eye_visibility,
                        beak_vis=beak_vis,
                        is_flying=is_flying,
                        focus_status=focus_status or "",
                        has_exposure_issue=_v2_exposure,
                        burst_id=self.burst_map.get(filepath) if self.burst_map else None,
                    )
                    if gate_photo_v2(_v2_metrics, min_confidence=confidence_threshold) is None:
                        v2_in_pool = True
                        v2_pending[original_prefix] = {
                            'metrics': _v2_metrics,
                            'items': [],
                            'v1_rating': rating_value,
                            'is_flying': is_flying,
                            'has_exposure_issue': _v2_exposure,
                            'is_focus_precise': focus_sharpness_weight > 1.0,
                            'target_file': None,
                            'adj_sharpness': None,
                            'adj_topiq': None,
                        }

                should_build_debug = bool(self.callbacks.crop_preview or self.settings.save_crop)
                if detected and should_build_debug and bird_crop_bgr is not None:
                    # 计算裁剪区域内的坐标
                    head_center_crop = None
                    if head_center_orig is not None:
                        # 转换到裁剪区域坐标
                        head_center_crop = (head_center_orig[0] - x_orig, head_center_orig[1] - y_orig)
                
                    focus_point_crop = None
                    if focus_x is not None and focus_y is not None:
                        # V3.9.4: 对焦点从归一化坐标转换为裁剪区域坐标
                        # 使用 w_orig, h_orig（优先）或 bird_crop_bgr 尺寸 + 偏移（后备）
                        img_w_for_focus = w_orig
                        img_h_for_focus = h_orig
                    
                        # 如果原图尺寸未知，尝试从裁剪图推算（不太准确但总比没有好）
                        if img_w_for_focus is None or img_h_for_focus is None:
                            if img_dims is not None:
                                # 使用 YOLO resize 的尺寸 + 缩放比例
                                w_resized, h_resized = img_dims
                                if bird_crop_bgr is not None:
                                    ch, cw = bird_crop_bgr.shape[:2]
                                    # 估算原图尺寸（使用 bbox 比例）
                                    if bird_bbox is not None:
                                        bx, by, bw, bh = bird_bbox
                                        scale_x = cw / bw if bw > 0 else 1
                                        scale_y = ch / bh if bh > 0 else 1
                                        img_w_for_focus = int(w_resized * scale_x)
                                        img_h_for_focus = int(h_resized * scale_y)
                    
                        if img_w_for_focus is not None and img_h_for_focus is not None:
                            fx_px = int(focus_x * img_w_for_focus) - x_orig
                            fy_px = int(focus_y * img_h_for_focus) - y_orig
                            focus_point_crop = (fx_px, fy_px)
                
                    debug_start = time.time()
                    try:
                        debug_img = self._save_debug_crop(
                            filename,
                            bird_crop_bgr,
                            bird_crop_mask if 'bird_crop_mask' in dir() else None,
                            head_center_crop,
                            head_radius_val,
                            focus_point_crop,
                            focus_status_en,  # 使用英文标签
                            write_file=self.settings.save_crop,
                        )
                        # V4.2: 发送裁剪预览到 UI（同时传对焦状态供 dock 显示）
                        if debug_img is not None and self.callbacks.crop_preview:
                            self.callbacks.crop_preview(debug_img, focus_status_en, None if v2_in_pool else rating_value)
                    except Exception as e:
                        print(f"  ⚠️ debug_crop 保存失败 [{filename}]: {e}")  # 调试图生成失败不影响主流程
                    add_photo_stage('debug_viz', (time.time() - debug_start) * 1000)
            
                # 计算真正总耗时并输出简化日志
                photo_time_ms = (time.time() - photo_start_time) * 1000 + yolo_ms
                has_exposure_issue = is_overexposed or is_underexposed
                if v2_in_pool:
                    # V4.6(rating-v2): 池内照片星级待定,日志只报指标/飞鸟/对焦
                    # V4.6 (rating-v2): pool photos log metrics only; stars come later
                    pending_reason = self.i18n.t(
                        "logs.pending_metrics",
                        sharp=f"{normalized_sharpness:.0f}",
                        nima=(f"{topiq:.1f}" if topiq is not None else "-"))
                    self._log_photo_result_simple(i, total_files, filename, None, pending_reason, photo_time_ms, is_flying, has_exposure_issue, focus_status)
                else:
                    self._log_photo_result_simple(i, total_files, filename, rating_value, reason, photo_time_ms, is_flying, has_exposure_issue, focus_status)

                # 记录统计（V4.2: 添加精焦判定）
                is_focus_precise = focus_sharpness_weight > 1.0 if 'focus_sharpness_weight' in dir() else False

                if not v2_in_pool:
                    self._update_stats(rating_value, is_flying, has_exposure_issue, is_focus_precise)
            
                # V3.4: 确定要处理的目标文件（RAW 优先，没有则用 JPEG）
                target_file_path = None
                target_extension = None
            
                # V4.0: 标签、对焦状态、详细评分说明（RAW 与纯 JPEG 共用，纯 JPEG 也写入 EXIF 题注/星级）
                # V4.3.0: 色标文字跟随界面语言写入 xmp:Label。
                # Lightroom 色标按「当前色标集的本地化名称」精确匹配文字：跨语言
                # 文字对不上会显示为白框，故按 i18n 语言写对应颜色名，语言包缺
                # key 时回退英文（详见 compute_xmp_label docstring）。
                # V4.6(Paul P2/B+): 蓝=飞鸟 > 绿=精焦(BEST) > 红=脱焦(BAD/WORST),
                # GOOD 无标签。
                # V4.3.0: Localize the xmp:Label text by UI language (LR matches
                # labels by localized string; fallback to English on missing keys).
                # V4.6 (Paul P2/B+): Blue=flying > Green=BEST > Red=BAD/WORST;
                # GOOD gets no label.
                label = compute_xmp_label(is_flying, focus_status, self.i18n.t)
            
                caption_lines = []
                caption_lines.append(self.i18n.t("logs.caption_final", rating=rating_value, reason=reason))
                sharpness_str = f"{head_sharpness:.2f}" if head_sharpness else "N/A"
                topiq_str = f"{topiq:.2f}" if topiq else "N/A"
                caption_lines.append(self.i18n.t("logs.caption_data", conf=confidence, sharp=sharpness_str, nima=topiq_str, vis=best_eye_visibility))
                flying_str = self.i18n.t("logs.flying_yes") if is_flying else self.i18n.t("logs.flying_no")
                caption_lines.append(self.i18n.t("logs.caption_factors", sharp_w=focus_sharpness_weight, aes_w=focus_topiq_weight, flying=flying_str))
                # V4.6(rating-v2/T5): adj 值统一用 ISO 归一化后的锐度(评星实际
                # 输入口径),修复 DB/EXIF 存的 adj 与评分依据不一致的旧漂移。
                # V4.6 (rating-v2/T5): adjusted values use the ISO-normalized
                # sharpness (the actual rating input), fixing the old drift
                # between stored adj_* and what the rating actually saw.
                adj_sharpness = normalized_sharpness * focus_sharpness_weight if normalized_sharpness else 0
                if is_flying and head_sharpness:
                    adj_sharpness = adj_sharpness * 1.2
                adj_topiq_val = 0.0
                if topiq:
                    adj_topiq_val = topiq * focus_topiq_weight
                    if is_flying:
                        adj_topiq_val = adj_topiq_val * 1.1
                caption_lines.append(self.i18n.t("logs.caption_adjusted", sharp=adj_sharpness, nima=adj_topiq_val))
                visibility_weight = max(0.5, min(1.0, best_eye_visibility * 2))
                if visibility_weight < 1.0:
                    caption_lines.append(self.i18n.t("logs.caption_vis_weight", weight=visibility_weight))
                caption = "\n".join(caption_lines)
            
                if original_prefix in raw_dict:
                    # 有对应的 RAW 文件
                    raw_extension = raw_dict[original_prefix]
                    target_file_path = os.path.join(self.dir_path, original_prefix + raw_extension)
                    target_extension = raw_extension
                
                    if os.path.exists(target_file_path):
                        birdid_title_targets = [target_file_path]
                        queue_star_metadata({
                            'file': target_file_path,
                            'rating': rating_value if rating_value >= 0 else 0,
                            'pick': pick,
                            'sharpness': adj_sharpness,
                            'nima_score': adj_topiq_val,
                            'label': label,
                            'focus_status': focus_status,
                            'caption': caption,
                        }, original_prefix, v2_in_pool)
                        # RAW+JPEG 时也写入当前 JPEG，便于单独查看 JPEG 时也有星级/题注（DNG/ARW/NEF 等同理）
                        # V4.0.5: 跳过临时预览文件，避免无用写入。
                        # V4.7 修复: V4.1.0 起 RAW 转换的预览存 .superpicky/cache/
                        # 且不带 tmp_ 前缀，仅查 basename 的旧判断全部漏网——每张
                        # RAW 照片的星级+题注都被完整写进缓存预览 JPG 本体
                        # (实测 ~190ms/张,474 张批次纯浪费 ~90s,是 exif_flush
                        # 占大头的真正元凶)。补上缓存路径检查,与上文
                        # is_temp_preview_path 同判据。
                        # V4.7 fix: since V4.1.0 converted-RAW previews live in
                        # .superpicky/cache/ WITHOUT the tmp_ prefix, so the
                        # basename-only check missed them all — every RAW photo's
                        # star+caption was fully written into the cached preview
                        # JPG body (~190ms each, ~90s wasted per 474-photo batch;
                        # the real culprit behind exif_flush dominance). Add the
                        # cache-path check, same criterion as is_temp_preview_path.
                        filepath_basename = os.path.basename(filepath)
                        is_temp_file = (
                            filepath_basename.startswith(('tmp_', 'tmp.'))
                            or '.superpicky/cache' in normalize_path_for_match(filepath)
                        )
                        if target_file_path != filepath and os.path.exists(filepath) and not is_temp_file:
                            birdid_title_targets.append(filepath)
                            queue_star_metadata({
                                'file': filepath,
                                'rating': rating_value if rating_value >= 0 else 0,
                                'pick': pick,
                                'sharpness': adj_sharpness,
                                'nima_score': adj_topiq_val,
                                'label': label,
                                'focus_status': focus_status,
                                'caption': caption,
                            }, original_prefix, v2_in_pool)
                    
                        # BirdID 异步提交（2星及以上）
                        # V4.6(rating-v2/T3): 识鸟门控从「星级≥2」改为硬门槛+锐度粗筛。
                        # V2 定星延后到批处理末尾,循环中不再有即时星级可依赖;
                        # 粗筛挡掉明显进不了 2★ 的样本,控制识鸟任务量(约+25%)。
                        # V4.6 (rating-v2/T3): gate BirdID on hard gates + a coarse
                        # sharpness screen instead of "rating >= 2" — V2 assigns
                        # stars in the post-pass, so no instant rating exists here.
                        if self.settings.auto_identify and (
                            rating_value >= 2 or (
                                detected
                                and confidence >= 0.5
                                and not all_keypoints_hidden
                                and normalized_sharpness >= 250
                            )
                        ):
                            _birdid_crop_pil = None
                            if bird_crop_bgr is not None:
                                try:
                                    from PIL import Image as _PILImage
                                    import cv2 as _cv2_birdid
                                    _birdid_crop_pil = _PILImage.fromarray(
                                        _cv2_birdid.cvtColor(bird_crop_bgr, _cv2_birdid.COLOR_BGR2RGB)
                                    )
                                except Exception:
                                    pass
                            submit_birdid_task(
                                original_prefix,
                                filepath,
                                birdid_title_targets,
                                os.path.basename(target_file_path),
                                _birdid_crop_pil,
                            )
                else:
                    # V3.4: 纯 JPEG 文件（没有对应 RAW）
                    target_file_path = filepath
                    target_extension = os.path.splitext(filename)[1]
                
                    if os.path.exists(target_file_path):
                        queue_star_metadata({
                            'file': target_file_path,
                            'rating': rating_value if rating_value >= 0 else 0,
                            'pick': pick,
                            'sharpness': adj_sharpness,
                            'nima_score': adj_topiq_val,
                            'label': label,
                            'focus_status': focus_status,
                            'caption': caption,
                        }, original_prefix, v2_in_pool)
                        # BirdID 异步提交（2星及以上）
                        # V4.6(rating-v2/T3): 识鸟门控从「星级≥2」改为硬门槛+锐度粗筛。
                        # V2 定星延后到批处理末尾,循环中不再有即时星级可依赖;
                        # 粗筛挡掉明显进不了 2★ 的样本,控制识鸟任务量(约+25%)。
                        # V4.6 (rating-v2/T3): gate BirdID on hard gates + a coarse
                        # sharpness screen instead of "rating >= 2" — V2 assigns
                        # stars in the post-pass, so no instant rating exists here.
                        if self.settings.auto_identify and (
                            rating_value >= 2 or (
                                detected
                                and confidence >= 0.5
                                and not all_keypoints_hidden
                                and normalized_sharpness >= 250
                            )
                        ):
                            _birdid_crop_pil = None
                            if bird_crop_bgr is not None:
                                try:
                                    from PIL import Image as _PILImage
                                    import cv2 as _cv2_birdid
                                    _birdid_crop_pil = _PILImage.fromarray(
                                        _cv2_birdid.cvtColor(bird_crop_bgr, _cv2_birdid.COLOR_BGR2RGB)
                                    )
                                except Exception:
                                    pass
                            submit_birdid_task(
                                original_prefix,
                                filepath,
                                [target_file_path],
                                os.path.basename(target_file_path),
                                _birdid_crop_pil,
                            )

                # V3.4: 以下操作对 RAW 和纯 JPEG 都执行
                if target_file_path and os.path.exists(target_file_path):
                    # V4.1: 计算调整后锐度（用于 CSV，保证重新评星一致性）
                    adj_sharpness_csv = normalized_sharpness * focus_sharpness_weight if normalized_sharpness else 0
                    if is_flying and head_sharpness:
                        adj_sharpness_csv = adj_sharpness_csv * 1.2
                    adj_topiq_csv = topiq * focus_topiq_weight if topiq else None
                    if is_flying and adj_topiq_csv:
                        adj_topiq_csv = adj_topiq_csv * 1.1
                
                    # 更新 CSV 中的关键点数据（V4.1: 添加 adj_sharpness, adj_topiq）
                    # 注意：必须用 original_prefix（DB 主键），而非 file_prefix（含缓存路径前缀）
                    csv_update_start = time.time()
                    self._update_csv_keypoint_data(
                        original_prefix,
                        head_sharpness,  # V4.1: 原始头部锐度
                        has_visible_eye,
                        has_visible_beak,
                        left_eye_vis,
                        right_eye_vis,
                        beak_vis,
                        topiq,  # V4.1: 原始美学分数
                        rating_value,
                        is_flying,
                        flight_confidence,
                        focus_status,  # V3.9: 对焦状态
                        focus_x,  # V3.9: 对焦点X坐标
                        focus_y,  # V3.9: 对焦点Y坐标
                        adj_sharpness_csv,  # V4.1: 调整后锐度
                        adj_topiq_csv,  # V4.1: 调整后美学
                        prefetched_exif,  # V2: EXIF 元数据
                        caption,  # V4.1: 评分说明
                        path_update_data,  # 合并路径字段，减少一次 DB update
                    )
                    add_photo_stage('csv_update', (time.time() - csv_update_start) * 1000)
                
                    # V4.6(rating-v2/T3b): 池内照片的 3星收集/评分记录延后到收尾定星;
                    # 此处仅补齐终评所需的目标文件与调整后指标。
                    # V4.6 (rating-v2/T3b): pool photos defer star-3 collection and
                    # rating records to the post-pass; capture finalization inputs here.
                    if v2_in_pool:
                        v2_pending[original_prefix]['target_file'] = target_file_path
                        v2_pending[original_prefix]['adj_sharpness'] = adj_sharpness_csv
                        v2_pending[original_prefix]['adj_topiq'] = adj_topiq_csv
                    else:
                        # 收集3星照片（V4.1: 使用调整后的值）
                        if rating_value == 3 and adj_topiq_csv is not None:
                            self.star_3_photos.append({
                                'file': target_file_path,
                                'nima': adj_topiq_csv,  # V4.1: 调整后美学
                                'sharpness': adj_sharpness_csv  # V4.1: 调整后锐度
                            })

                        # 记录评分（用于文件移动）- V4.0.4: 使用 original_prefix 确保匹配 NEF
                        self.file_ratings[original_prefix] = rating_value
                
                    # V4.0.1: 自动鸟种识别（移至共同路径，对 RAW 和纯 JPG 都执行）
                    # V4.0.5: 纯 JPEG 的识鸟已移到 EXIF 写入前，这里只处理 RAW 的后续操作
                    # 注意：对于 RAW 文件，在上面的分支中已经执行过
                
                else:
                    # 目标文件不存在时仍写入路径字段，确保 DB 记录不丢失
                    # Write path fields even when the target file is missing to keep DB record intact
                    if path_update_data and self.report_db:
                        self.report_db.update_photo(original_prefix, path_update_data)

                self._perf_record_photo(photo_time_ms, photo_stage_ms, early_exit=False)
                mark_resume_completed(original_prefix)
            except ProcessingCancelled:
                raise
            except Exception as e:
                # 单张照片处理异常：记录日志、计入失败统计并跳到下一张，避免拖垮
                # 整个目录剩余照片。不标记 resume 完成——语义见 _handle_photo_failure。
                # Log, count the failure, and move on to the next photo instead of
                # aborting the whole remaining batch. Deliberately NOT marked
                # resume-completed — see _handle_photo_failure for the semantics.
                self._handle_photo_failure(i, filename, original_prefix, e)
                continue
        
        if self._should_stop():
            if metadata_async_enabled and metadata_queue is not None:
                try:
                    metadata_queue.put_nowait(None)
                except Exception:
                    pass
            if birdid_executor is not None:
                try:
                    birdid_executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    birdid_executor.shutdown(wait=False)
                except Exception:
                    pass
            try:
                inference_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                inference_pool.shutdown(wait=False)
            except Exception:
                pass
            raise ProcessingCancelled("Processing cancelled")

        if yolo_prefetch_thread is not None:
            try:
                yolo_prefetch_thread.join(timeout=30)
            except Exception:
                pass
        if exif_prefetch_thread is not None:
            try:
                exif_prefetch_thread.join(timeout=30)
            except Exception:
                pass
        
        # 回收 BirdID 异步任务：补写标题并更新鸟种映射（用于后续分类目录）
        if birdid_tasks:
            self._log(self.i18n.t("logs.birdid_waiting", count=len(birdid_tasks)))
        collect_birdid_tasks(wait=True)
        
        if birdid_executor is not None:
            try:
                birdid_executor.shutdown(wait=True)
            except Exception:
                pass
        
        try:
            inference_pool.shutdown(wait=True)
        except Exception:
            pass

        # V4.6(rating-v2/T3b): 收尾统一定星——对排序池照片按批内配额分配星级,
        # 回填挂起的 EXIF 条目(rating/caption 首行)、评分记录、统计与 DB。
        # V4.6 (rating-v2/T3b): post-pass star assignment — quota-rank the pool,
        # then back-fill parked EXIF items (rating + caption head line), rating
        # records, stats, and the report DB.
        if v2_enabled and v2_pending:
            # V4.6(rating-v2): 识鸟结果此刻已全部就绪(collect wait=True 在前),
            # 填入 species 后配额将按鸟种分组执行;未识别/未开识鸟为 None(单组)。
            # V4.6 (rating-v2): Bird ID results are all in by now; with species
            # filled the quota runs per species (None = unknown / Bird ID off).
            for prefix, pend in v2_pending.items():
                info = self.file_bird_species.get(prefix)
                if info:
                    pend['metrics'].species = info.get('en_name') or info.get('cn_name')
            quota3 = get_quota3_for_skill(self.config.skill_level, self.config)
            quota2 = get_quota2_for_skill(self.config.skill_level, self.config)
            v2_results = assign_ratings_v2(
                [p['metrics'] for p in v2_pending.values()],
                quota3=quota3,
                quota2=quota2,
                min_confidence=self.settings.ai_confidence / 100.0,
            )
            v2_changed = 0
            for prefix, pend in v2_pending.items():
                res = v2_results.get(prefix)
                if res is None:
                    continue
                final_rating = res.rating
                if final_rating != pend['v1_rating']:
                    v2_changed += 1
                reason_final = self.i18n.t(res.reason_key, **res.reason_args)
                final_caption = None   # V2 终评重写后的 caption,用于同步回 DB
                for item in pend['items']:
                    old_caption = item.get('caption')
                    if old_caption:
                        # caption 首行固定为 caption_final(星级+原因),按终评重写
                        # The caption head line is always caption_final; rewrite it
                        parts = old_caption.split('\n', 1)
                        head = self.i18n.t("logs.caption_final",
                                           rating=final_rating, reason=reason_final)
                        item['caption'] = head + ('\n' + parts[1] if len(parts) > 1 else '')
                        final_caption = item['caption']
                    item['rating'] = final_rating
                    queue_metadata(item)
                self.file_ratings[prefix] = final_rating
                self._update_stats(final_rating, pend['is_flying'],
                                   pend['has_exposure_issue'], pend['is_focus_precise'])
                if (final_rating == 3 and pend.get('adj_topiq') is not None
                        and pend.get('target_file')):
                    self.star_3_photos.append({
                        'file': pend['target_file'],
                        'nima': pend['adj_topiq'],
                        'sharpness': pend['adj_sharpness'],
                    })
                if self.report_db:
                    try:
                        # V2 终评须同步 rating 与 caption,否则 DB caption 停留在初始
                        # V1 措辞(与浏览器 rating 不符,即「选片备注/星级」错位 bug)。
                        # V2 must sync both rating and caption to the DB, else the
                        # caption keeps the stale V1 head and disagrees with rating.
                        db_update = {'rating': final_rating}
                        if final_caption is not None:
                            db_update['caption'] = final_caption
                        self.report_db.update_photo(prefix, db_update)
                    except Exception:
                        pass
            self._log(self.i18n.t(
                "logs.rating_v2_summary",
                total=len(v2_pending),
                three=sum(1 for p, r in v2_results.items()
                          if p in v2_pending and r.rating == 3),
                quota=int(quota3),
                overall=round(sum(1 for pfx, r in v2_results.items()
                                  if pfx in v2_pending and r.rating == 3)
                              * 100 / max(1, total_files)),
                changed=v2_changed,
            ))
            # 按鸟种打一行 3星/池内 分布(仅当存在识鸟结果时)
            # Per-species "3-star / pool" breakdown (only when species exist)
            if any(pend['metrics'].species for pend in v2_pending.values()):
                is_zh = not self.i18n.current_lang.startswith('en')
                unknown_label = "未识别" if is_zh else "unknown"
                sp_stats: Dict[str, list] = {}
                for prefix, pend in v2_pending.items():
                    info = self.file_bird_species.get(prefix)
                    if info:
                        name = (info.get('cn_name') if is_zh else info.get('en_name')) \
                            or info.get('en_name') or info.get('cn_name')
                    else:
                        name = unknown_label
                    entry = sp_stats.setdefault(name, [0, 0])
                    entry[1] += 1
                    r = v2_results.get(prefix)
                    if r is not None and r.rating == 3:
                        entry[0] += 1
                detail = " · ".join(
                    f"{name} {c3}/{cn}" for name, (c3, cn) in
                    sorted(sp_stats.items(), key=lambda kv: -kv[1][1]))
                self._log(f"    {detail}")

        # 批量落盘 EXIF 队列（避免每张图一次写入）
        if metadata_batch:
            pending_with_caption = sum(1 for it in metadata_batch if it.get('caption'))
            self._log(self.i18n.t("logs.exif_batch_submit",
                count=len(metadata_batch), caption_count=pending_with_caption))
        flush_metadata_batch()
        if metadata_async_enabled and metadata_queue is not None:
            pending_batches = metadata_queue.qsize()
            if pending_batches > 0:
                self._log(self.i18n.t("logs.exif_queue_wait", batches=pending_batches))
            else:
                self._log(self.i18n.t("logs.exif_thread_wait"))
            exif_wait_start = time.time()
            metadata_queue.put(None)  # writer 退出哨兵
            metadata_queue.join()
            if metadata_writer_thread is not None:
                metadata_writer_thread.join(timeout=30)
            self._perf_add_stage('exif_wait', (time.time() - exif_wait_start) * 1000)
            with metadata_writer_stats_lock:
                async_flush_ms = metadata_writer_stats['flush_ms']
                async_flush_count = metadata_writer_stats['flush_count']
            if async_flush_ms > 0:
                self._perf_add_stage('exif_flush', async_flush_ms)
            self._perf_stats['exif_flush_count'] += async_flush_count
            if metadata_writer_errors:
                self._log(f"  ⚠️ EXIF async writer errors: {len(metadata_writer_errors)}", "warning")
        
        # SQLite 数据库会在 _update_csv_keypoint_data 中自动提交
        # 无需手动 flush
        
        # 注意：report_db 在 run() 方法结束时关闭，因为后续阶段仍需要使用
        
        self._perf_finalize()
        
        ai_total_time = time.time() - ai_total_start
        avg_ai_time = ai_total_time / total_files if total_files > 0 else 0
        self._log(self.i18n.t("logs.ai_detection_total", time_str=f"{ai_total_time:.1f}s", avg=avg_ai_time))

        # V4.2.7: 跑批结束输出 GBIF 罕见度 tier 分布统计
        # V4.2.7: Print GBIF rarity tier breakdown at the end of the batch.
        self._log_tier_summary()
    
    def _log_tier_summary(self) -> None:
        """
        输出本次跑批识别到的鸟种按 GBIF 罕见度 tier 分组的统计。

        Print a rarity-tier breakdown for all species identified in this
        batch, ordered from rarest (● 传奇) to most common (○ 常见). Names
        are localized to the current UI language.
        """
        bird_species = self.stats.get('bird_species', [])
        if not bird_species:
            return

        from collections import defaultdict
        from core.rarity_tier import TIER_ICONS, TIER_NAMES_ZH, TIER_NAMES_EN

        tier_groups = defaultdict(list)
        no_tier = []
        for entry in bird_species:
            tidx = entry.get('gbif_tier')
            if tidx is None:
                no_tier.append(entry)
            else:
                tier_groups[tidx].append(entry)

        if not tier_groups and not no_tier:
            return

        is_zh = not self.i18n.current_lang.startswith('en')
        tier_names = TIER_NAMES_ZH if is_zh else TIER_NAMES_EN
        primary_key = 'cn_name' if is_zh else 'en_name'
        fallback_key = 'en_name' if is_zh else 'cn_name'

        def _name(entry: Dict) -> str:
            return entry.get(primary_key) or entry.get(fallback_key) or '?'

        header = "🐦 鸟种罕见度分布:" if is_zh else "🐦 Species rarity breakdown:"
        unit = "种" if is_zh else "spp."
        unknown_label = "未知" if is_zh else "unknown"

        self._log("")
        self._log(header)

        # 从最罕见 (●) 到最常见 (○) 排列
        for tidx in range(4, -1, -1):
            entries = tier_groups.get(tidx, [])
            if not entries:
                continue
            names = ", ".join(_name(e) for e in entries)
            self._log(
                f"  {TIER_ICONS[tidx]} {tier_names[tidx]}: {len(entries)} {unit}  ({names})"
            )

        if no_tier:
            names = ", ".join(_name(e) for e in no_tier)
            self._log(f"  ? {unknown_label}: {len(no_tier)} {unit}  ({names})")

    # 注意: _calculate_rating 方法已移至 core/rating_engine.py
    # 现在使用 self.rating_engine.calculate() 替代
    
    def _log_photo_result(
        self, 
        rating: int, 
        reason: str, 
        conf: float, 
        sharp: float, 
        nima: Optional[float]
    ):
        """记录照片处理结果（详细版，保留用于调试）"""
        iqa_text = ""
        if nima is not None:
            iqa_text += f", 美学:{nima:.2f}"
        
        if rating == 3:
            self._log(self.i18n.t("logs.excellent_photo", confidence=conf, sharpness=sharp, iqa_text=iqa_text), "success")
        elif rating == 2:
            self._log(self.i18n.t("logs.good_photo", confidence=conf, sharpness=sharp, iqa_text=iqa_text), "info")
        elif rating == 1:
            self._log(self.i18n.t("logs.average_photo", confidence=conf, sharpness=sharp, iqa_text=iqa_text), "warning")
        elif rating == 0:
            self._log(self.i18n.t("logs.poor_quality", reason=reason, confidence=conf, iqa_text=iqa_text), "warning")
        else:  # -1
            self._log(f"  ❌ No bird - {reason}", "error")
    
    def _log_photo_result_simple(
        self,
        index: int,
        total: int,
        filename: str,
        rating: int,
        reason: str,
        time_ms: float,
        is_flying: bool = False,  # V3.4: 飞鸟标识
        has_exposure_issue: bool = False,  # V3.8: 曝光问题标识
        focus_status: str = None  # V3.9: 对焦状态
    ):
        """记录照片处理结果（简化版，单行输出）"""
        # Star text mapping - use short English format
        # V4.6(rating-v2): rating=None 表示星级待收尾统一分配 / None = pending post-pass
        star_map = {3: "3★", 2: "2★", 1: "1★", 0: "0★", -1: "-1★", None: "⏳"}
        star_text = star_map.get(rating, "?★")
        
        # V3.4: Flight tag
        flight_tag = "[FLY]" if is_flying else ""
        
        # V3.8: 曝光问题标识（已在reason中显示"欠曝/过曝"，故不再单独显示标签）
        # exposure_tag = "【曝光】" if has_exposure_issue else ""
        
        # V3.9: 对焦状态标识（已在reason中显示"精焦/合焦/失焦/脱焦"，故不再单独显示标签）
        # focus_tag = ""
        # if focus_status:
        #     focus_tag = f"【{focus_status}】"
        
        # 简化原因显示（V3.9: 增加到35字符避免截断）
        reason_short = reason if len(reason) < 35 else reason[:32] + "..."
        
        # 时间格式化
        if time_ms >= 1000:
            time_text = f"{time_ms/1000:.1f}s"
        else:
            time_text = f"{time_ms:.0f}ms"
        
        # 输出简化格式（只显示文件名,去掉目录前缀；3星行文件名之后染绿）
        display_name = os.path.basename(filename)
        # V4.6(rating-v2): rating=None(待定)按普通级别着色 / None (pending) uses default level
        level = "photo_good" if (rating is not None and rating >= 3) else "default"
        self._log(f"[{index:03d}/{total}] {display_name} | {star_text} ({reason_short}) {flight_tag}| {time_text}", level)
    
    def _save_debug_crop(
        self,
        filename: str,
        bird_crop_bgr: np.ndarray,
        bird_crop_mask: np.ndarray = None,
        head_center_crop: tuple = None,
        head_radius: int = None,
        focus_point_crop: tuple = None,
        focus_status: str = None,
        write_file: bool = True,
    ):
        """
        V3.9: 保存调试可视化图片到 .superpicky/debug_crops/ 目录
        
        标注内容：
        - 🟢 绿色半透明: SEG mask 鸟身区域
        - 🔵 蓝色圆圈: 头部检测区域
        - 🔴 红色十字: 对焦点位置
        """
        import cv2
        
        # 复制原图
        debug_img = bird_crop_bgr.copy()
        h, w = debug_img.shape[:2]
        
        # 1. 绘制 SEG mask（绿色半透明覆盖）
        if bird_crop_mask is not None and bird_crop_mask.shape[:2] == (h, w):
            green_overlay = np.zeros_like(debug_img)
            green_overlay[:] = (0, 255, 0)  # BGR 绿色
            mask_bool = bird_crop_mask > 0
            # 半透明叠加
            debug_img[mask_bool] = cv2.addWeighted(
                debug_img[mask_bool], 0.7,
                green_overlay[mask_bool], 0.3, 0
            )
        
        # 2. 绘制头部圆圈（蓝色）
        if head_center_crop is not None and head_radius is not None:
            cx, cy = head_center_crop
            cv2.circle(debug_img, (cx, cy), head_radius, (255, 0, 0), 2)  # 蓝色圆圈
            cv2.circle(debug_img, (cx, cy), 3, (255, 0, 0), -1)  # 圆心
        
        # 3. 绘制对焦点（红色十字）- V3.9.3 加大加粗更醒目
        if focus_point_crop is not None:
            fx, fy = focus_point_crop
            cross_size = 30  # 原来15，加大到30
            thickness = 4    # 原来2，加粗到4
            cv2.line(debug_img, (fx - cross_size, fy), (fx + cross_size, fy), (0, 0, 255), thickness)
            cv2.line(debug_img, (fx, fy - cross_size), (fx, fy + cross_size), (0, 0, 255), thickness)
            # 额外画一个红色圆点作为中心标记
            cv2.circle(debug_img, (fx, fy), 6, (0, 0, 255), -1)
        
        # 4. 添加状态文字
        if focus_status:
            cv2.putText(debug_img, focus_status, (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        
        if write_file:
            # 创建调试目录（Windows 下自动隐藏）
            debug_dir = os.path.join(self.dir_path, ".superpicky", "cache", "crop_debug")
            ensure_hidden_directory(os.path.join(self.dir_path, ".superpicky"))
            os.makedirs(debug_dir, exist_ok=True)

            # 保存调试图
            # V4.0.5: filename 可能包含子目录前缀（如 .superpicky/cache/_Z9W1029.jpg），需取 basename
            file_prefix = os.path.splitext(os.path.basename(filename))[0]
            debug_path = os.path.join(debug_dir, f"{file_prefix}.jpg")
            cv2.imwrite(debug_path, debug_img, [cv2.IMWRITE_JPEG_QUALITY, 85])

            # NOTE: debug_crop_path 由 ai_model.py 的 insert_photo() 统一写入数据库
            # V4.2: 现在 debug_crop_path 专门指代 crop_debug 图片，此处需要回写数据库
            if hasattr(self, 'report_db') and self.report_db:
                try:
                    rel_path = os.path.relpath(debug_path, self.dir_path)
                    # 更新数据库中的 debug_crop_path 字段
                    self.report_db.update_photo(file_prefix, {"debug_crop_path": rel_path})
                except Exception:
                    pass
        
        # V4.2: 返回标注后的图像，用于 UI 实时预览
        return debug_img
    
    def _handle_photo_failure(
        self,
        index: int,
        filename: str,
        original_prefix: Optional[str],
        error: Exception,
    ) -> None:
        """
        单张照片处理异常的统一兜底：记录日志并计入失败统计。

        关键语义：**不**调用 resume_state.mark_completed()——失败照片必须留在
        pending 列表中，这样中断后续跑会重试它；正常跑完时 process() 末尾的
        resume_state.clear() 会统一清理，不会残留状态文件。若在这里误标完成，
        照片会既不被移动分类、也不计入统计、续跑还永久跳过（历史"丢照片"
        事故的同类根因）。

        参数:
        index (int): 照片在本次处理中的序号（1 基），用于日志。
        filename (str): 照片文件名，用于日志与失败清单。
        original_prefix (Optional[str]): 照片前缀；异常发生早于赋值时为 None，
            仅作 filename 缺失时的兜底显示，绝不用于标记 resume 完成。
        error (Exception): 捕获到的异常对象。

        返回:
        None

        Unified per-photo failure handler: log the error and count the failure.

        Key semantics: this deliberately does **not** call
        resume_state.mark_completed() — the failed photo must stay in the
        pending list so an interrupted-then-resumed run retries it; a normal
        completion clears the whole state via resume_state.clear() at the end
        of process(). Marking it completed here would leave the photo
        unmoved, uncounted, and permanently skipped on resume (the same root
        cause as the historical "lost photos" incident).

        Parameters:
        index (int): 1-based photo index within this run, for logging.
        filename (str): Photo filename for the log line and failure list.
        original_prefix (Optional[str]): Photo prefix; None when the error
            happened before assignment. Only a display fallback — never used
            to mark resume completion.
        error (Exception): The caught exception.

        Return:
        None
        """
        display_name = filename or original_prefix or f"#{index}"
        self._log(
            f"  ⚠️ 第{index}张处理异常，已跳过 [{display_name}]: {error}",
            "error"
        )
        self.stats['failed'] += 1
        self.failed_photos.append(display_name)

    def _update_stats(self, rating: int, is_flying: bool = False, has_exposure_issue: bool = False, is_focus_precise: bool = False):
        """更新统计数据"""
        self.stats['total'] += 1
        if rating == 3:
            self.stats['star_3'] += 1
        elif rating == 2:
            self.stats['star_2'] += 1
        elif rating == 1:
            self.stats['star_1'] += 1  # 普通照片（合格）
        elif rating == 0:
            self.stats['star_0'] += 1  # 普通照片（问题）
        else:  # -1
            self.stats['no_bird'] += 1
        
        # V3.6: 统计飞鸟照片
        if is_flying:
            self.stats['flying'] += 1
        
        # V4.2: 统计精焦照片（红色标签）
        if is_focus_precise:
            self.stats['focus_precise'] += 1
        
        # V3.8: 统计曝光问题照片
        if has_exposure_issue:
            self.stats['exposure_issue'] += 1
    
    def _update_csv_keypoint_data(
            self,
            filename: str,
            head_sharpness: float,
            has_visible_eye: bool,
            has_visible_beak: bool,
            left_eye_vis: float,
            right_eye_vis: float,
            beak_vis: float,
            nima: float,
            rating: int,
            is_flying: bool = False,
            flight_confidence: float = 0.0,
            focus_status: str = None,  # V3.9: 对焦状态
            focus_x: float = None,  # V3.9: 对焦点X坐标
            focus_y: float = None,  # V3.9: 对焦点Y坐标
            adj_sharpness: float = None,  # V4.1: 调整后锐度
            adj_topiq: float = None,  # V4.1: 调整后美学
            exif_data: dict = None,  # V2: EXIF 元数据
            caption: str = None,  # V4.1: 评分说明
            extra_data: Optional[Dict] = None,
    ):
        """更新报告数据库中的关键点数据和评分（SQLite 版本）"""
        if self.report_db is None:
            return
        
        data = {
            'head_sharp': head_sharpness if head_sharpness > 0 else None,
            'left_eye': left_eye_vis,
            'right_eye': right_eye_vis,
            'beak': beak_vis,
            'nima_score': nima,
            'is_flying': 1 if is_flying else 0,
            'flight_conf': flight_confidence,
            'rating': rating,
            'focus_status': focus_status,
            'focus_x': focus_x,
            'focus_y': focus_y,
            'adj_sharpness': adj_sharpness,
            'adj_topiq': adj_topiq,
        }

        # V2: 合并 EXIF 元数据（先合并，再覆盖 caption，避免 exif_data 里的空值覆盖评分说明）
        if exif_data:
            data.update(exif_data)

        if extra_data:
            data.update(extra_data)

        # caption 最后写入，确保不被 exif_data 里的空 Caption-Abstract 覆盖
        if caption is not None:
            data['caption'] = caption

        self.report_db.update_photo(filename, data)
    
    # _load_csv_cache 和 _flush_csv_cache 已被 SQLite (ReportDB) 替代
    # 详见 tools/report_db.py
    
    def _calculate_picked_flags(self):
        """Calculate picked flags - intersection of aesthetics + sharpness rankings among 3-star photos"""
        if len(self.star_3_photos) == 0:
            self._log("\nℹ️  No 3-star photos, skipping picked flag calculation")
            return
        
        self._log(self.i18n.t("logs.picked_calculation_start", count=len(self.star_3_photos)))
        top_percent = self.config.picked_top_percentage / 100.0
        top_count = max(1, int(len(self.star_3_photos) * top_percent))
        
        # 美学排序
        sorted_by_nima = sorted(self.star_3_photos, key=lambda x: x['nima'], reverse=True)
        nima_top_files = set([photo['file'] for photo in sorted_by_nima[:top_count]])
        
        # 锐度排序
        sorted_by_sharpness = sorted(self.star_3_photos, key=lambda x: x['sharpness'], reverse=True)
        sharpness_top_files = set([photo['file'] for photo in sorted_by_sharpness[:top_count]])
        
        # 交集
        picked_files = nima_top_files & sharpness_top_files
        
        if len(picked_files) > 0:
            self._log(self.i18n.t("logs.picked_aesthetic_top", percent=self.config.picked_top_percentage, count=len(nima_top_files)))
            self._log(self.i18n.t("logs.picked_sharpness_top", percent=self.config.picked_top_percentage, count=len(sharpness_top_files)))
            self._log(self.i18n.t("logs.picked_intersection", count=len(picked_files)))
            
            # Debug: show picked file paths
            for file_path in picked_files:
                pass  # picked file confirmed
            
            # 批量写入
            picked_batch = [{
                'file': file_path,
                'rating': 3,
                'pick': 1
            } for file_path in picked_files]
            
            exiftool_mgr = get_exiftool_manager()
            picked_stats = exiftool_mgr.batch_set_metadata(picked_batch)
            
            if picked_stats['failed'] == 0:
                self._log(self.i18n.t("logs.picked_exif_success"))
            else:
                self._log(self.i18n.t("logs.picked_exif_failed", failed=picked_stats['failed']), "warning")

            self.stats['picked'] = len(picked_files) - picked_stats.get('failed', 0)

            # 同步写入 report.db 的 picked 列(供结果浏览器筛选与皇冠角标读取)
            # Persist picked flag into report.db so the browser can filter/draw the crown.
            if getattr(self, "report_db", None):
                for _fp in picked_files:
                    _prefix = os.path.splitext(os.path.basename(_fp))[0]
                    try:
                        self.report_db.update_photo(_prefix, {"picked": 1})
                    except Exception:
                        pass
        else:
            self._log(self.i18n.t("logs.picked_no_intersection"))
            self.stats['picked'] = 0
    
    def _move_files_to_rating_folders(self, raw_dict):
        """移动文件到分类文件夹（V4.2.7: layout 由 folder_layout 决定）"""
        from core.folder_layout import compute_target_folder
        other_birds = self.i18n.t("logs.folder_other_birds")
        layout = self.config.folder_layout

        # 筛选需要移动的文件（包括所有星级，确保原目录为空）
        files_to_move = []
        for prefix, rating in self.file_ratings.items():
            if rating in [-1, 0, 1, 2, 3]:
                # V4.2.7: 抽取鸟种名 → 调 compute_target_folder 统一 layout
                # V4.2.7: Resolve species name then delegate to the layout helper.
                bird_name = None
                if rating >= 2:
                    bird_info = self.file_bird_species.get(prefix)
                    if bird_info and not bird_info.get('low_confidence'):
                        if self.i18n.current_lang.startswith('en'):
                            bird_name = bird_info.get('en_name', '').replace(' ', '_')
                        else:
                            bird_name = bird_info.get('cn_name', '')
                        if not bird_name:
                            bird_name = (
                                bird_info.get('cn_name', '')
                                or bird_info.get('en_name', '').replace(' ', '_')
                                or 'Unknown'
                            )
                folder = compute_target_folder(rating, bird_name, layout, other_birds)
                
                if prefix in raw_dict:
                    # 有对应的 RAW 文件
                    raw_ext = raw_dict[prefix]
                    raw_path = os.path.join(self.dir_path, prefix + raw_ext)
                    if os.path.exists(raw_path):
                        files_to_move.append({
                            'filename': prefix + raw_ext,
                            'rating': rating,
                            'folder': folder,
                            'bird_species': self.file_bird_species.get(prefix, '')  # V4.0: 记录鸟种用于 manifest
                        })

                    # 若存在 XMP 侧车文件，随 RAW 一并移动
                    xmp_path = os.path.join(self.dir_path, prefix + '.xmp')
                    if os.path.exists(xmp_path):
                        files_to_move.append({
                            'filename': prefix + '.xmp',
                            'rating': rating,
                            'folder': folder,
                            'bird_species': self.file_bird_species.get(prefix, '')
                        })
                    
                    # V4.0: 同时移动同名 JPEG（如果存在）
                    for jpg_ext in ['.jpg', '.jpeg', '.JPG', '.JPEG']:
                        jpg_path = os.path.join(self.dir_path, prefix + jpg_ext)
                        if os.path.exists(jpg_path):
                            files_to_move.append({
                                'filename': prefix + jpg_ext,
                                'rating': rating,
                                'folder': folder,
                                'bird_species': self.file_bird_species.get(prefix, '')
                            })
                            break  # 只找一个 JPEG
                else:
                    # V3.4: 纯 JPEG 文件
                    for jpg_ext in ['.jpg', '.jpeg', '.JPG', '.JPEG']:
                        jpg_path = os.path.join(self.dir_path, prefix + jpg_ext)
                        if os.path.exists(jpg_path):
                            files_to_move.append({
                                'filename': prefix + jpg_ext,
                                'rating': rating,
                                'folder': folder,
                                'bird_species': self.file_bird_species.get(prefix, '')
                            })
                            break  # 找到就跳出
        
        if not files_to_move:
            self._log("\n📂 No files to move")
            return
        
        # V4.3.0: 文件整理阶段进度反馈。主进度条在 AI 分析阶段已占满 100%，此后
        # 移动上千个文件（尤其在存储卡上）很耗时；持续上报进度并提示「请勿关闭」，
        # 避免用户误以为程序卡死而强制结束，导致文件移动到一半、照片散落各文件夹。
        # V4.3.0: Progress feedback for the file-organizing stage. The main bar is
        # already at 100% after AI analysis; moving thousands of files (especially on
        # a memory card) is slow, so keep reporting progress and warn against closing.
        total_to_move = len(files_to_move)
        self._log("\n" + self.i18n.t("logs.organizing_start", count=total_to_move), "info")

        # 创建文件夹（使用实际的目录名，支持多层）
        folders_in_use = set(f['folder'] for f in files_to_move)
        for folder_name in folders_in_use:
            folder_path = os.path.join(self.dir_path, folder_name)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)

        # 移动文件
        moved_count = 0
        for idx, file_info in enumerate(files_to_move, 1):
            src_path = os.path.join(self.dir_path, file_info['filename'])
            dst_folder = os.path.join(self.dir_path, file_info['folder'])
            dst_path = os.path.join(dst_folder, file_info['filename'])

            try:
                if os.path.exists(dst_path):
                    continue
                shutil.move(src_path, dst_path)
                moved_count += 1
            except Exception as e:
                self._log(self.i18n.t("logs.move_failed", filename=file_info['filename'], error=str(e)), "warning")

            # 每 50 张或最后一张上报一次，让用户看到「100% 之后仍在整理文件」
            if idx % 50 == 0 or idx == total_to_move:
                self._log(self.i18n.t("logs.organizing_progress", done=idx, total=total_to_move), "info")

        self._log(self.i18n.t("logs.organizing_complete", moved=moved_count), "info")
        
        # V4.0.5: 更正 current_path - 更新数据库中所有移动文件的位置
        # 这确保 current_path 指向最新的原始文件位置 (如 3star_excellent/Bird/DSC_1234.NEF)
        if hasattr(self, 'report_db') and self.report_db:
            try:
                for file_info in files_to_move:
                    # 原文件名（带后缀）
                    orig_filename = file_info['filename']
                    # XMP 侧车文件与 RAW 共用同一个 prefix，跳过 XMP 的 current_path 更新
                    # 否则 XMP 会覆盖 RAW 已写入的正确路径，导致连拍合并时定位不到原图
                    if orig_filename.lower().endswith('.xmp'):
                        continue
                    # 文件前缀（不带后缀，也是数据库的主键/索引）
                    file_prefix = os.path.splitext(orig_filename)[0]
                    # 新的相对路径
                    new_rel_path = os.path.join(file_info['folder'], orig_filename)
                    
                    update_data = {'current_path': new_rel_path}
                    # 若移动的是 JPG 文件，同步更新 temp_jpeg_path 使路径始终有效
                    if orig_filename.lower().endswith(('.jpg', '.jpeg')):
                        update_data['temp_jpeg_path'] = new_rel_path
                    self.report_db.update_photo(file_prefix, update_data)
            except Exception as e:
                self._log(f"  ⚠️  Failed to update current_path in DB: {e}", "warning")

        
        # 生成manifest（V4.0: 增加鸟种分类信息和临时 JPEG 列表）
        manifest = {
            "version": "2.0",  # V4.0: 更新版本号
            "created": datetime.now().isoformat(),
            "app_version": "V4.0.5",
            "original_dir": self.dir_path,
            "folder_structure": get_rating_folder_names(),
            "bird_species_dirs": True,  # V4.0: 标记使用了鸟种分目录
            "files": files_to_move,
            "temp_jpegs": list(self.temp_converted_jpegs),  # V4.0: 记录临时转换的 JPEG，Reset 时需删除
            "stats": {"total_moved": moved_count}
        }
        
        manifest_path = os.path.join(self.dir_path, ".superpicky_manifest.json")
        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            self._log(f"  ✅ Moved {moved_count} photos")
        except Exception as e:
            self._log(f"  ⚠️  Manifest save failed: {e}", "warning")
    
    def _cleanup_temp_files(self, files_tbr, raw_dict):
        """V4.0.6: Clean up entire cache directory (temp_preview + yolo_debug + crop_debug)"""
        import shutil
        self._log(self.i18n.t("logs.cleaning_temp"))

        cache_dir = os.path.join(self.dir_path, ".superpicky", "cache")
        if os.path.exists(cache_dir):
            try:
                shutil.rmtree(cache_dir)
                self._log(self.i18n.t("logs.temp_files_cleaned", count=len(self.temp_converted_jpegs)))
                
                # 清除数据库中已删除的 debug_crop_path
                if hasattr(self, 'report_db') and self.report_db:
                    try:
                        self.report_db.clear_cache_paths()
                    except Exception as e:
                        self._log(f"⚠️ Failed to clear DB paths: {e}", "warning")
            except Exception as e:
                self._log(f"⚠️ Failed to remove cache directory: {e}", "warning")
        else:
            self._log(self.i18n.t("logs.temp_files_cleaned", count=0))
    
    def _save_temp_paths_to_db(self):
        """V4.0.5: 保留临时文件时，将路径写入数据库的 temp_jpeg_path 列"""
        if not self.temp_converted_jpegs:
            return
        
        saved_count = 0
        for rel_path in self.temp_converted_jpegs:
            # rel_path 格式: .superpicky/cache/XXXX.jpg
            # 提取原始文件前缀 (去掉路径和扩展名)
            basename = os.path.basename(rel_path)
            file_prefix = os.path.splitext(basename)[0]
            
            try:
                if hasattr(self, 'report_db') and self.report_db:
                    self.report_db.update_photo(file_prefix, {
                        'temp_jpeg_path': rel_path
                    })
                    saved_count += 1
            except Exception as e:
                self._log(self.i18n.t("logs.cache_path_save_failed", prefix=file_prefix, e=e), "warning")
        
        if saved_count > 0:
            self._log(self.i18n.t("logs.cache_paths_saved", count=saved_count))

    def _cleanup_expired_cache(self):
        """V4.3: 已移除基于天数的定期清理（auto_cleanup_days 已删除）。
        缓存保留与否由 keep_temp_files 控制，此方法保留为空操作以兼容调用方。"""
        pass
