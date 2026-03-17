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
import hashlib
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
from ai_model import load_yolo_model, detect_and_draw_birds
from tools.report_db import ReportDB
from tools.exiftool_manager import get_exiftool_manager
from tools.file_utils import ensure_hidden_directory
from advanced_config import get_advanced_config
from core.rating_engine import RatingEngine, create_rating_engine_from_config
from core.keypoint_detector import KeypointDetector, get_keypoint_detector
from core.flight_detector import FlightDetector, get_flight_detector, FlightResult
from core.exposure_detector import ExposureDetector, get_exposure_detector, ExposureResult
from core.focus_point_detector import get_focus_detector, verify_focus_in_bbox

from constants import RATING_FOLDER_NAMES, RAW_EXTENSIONS, JPG_EXTENSIONS, HEIF_EXTENSIONS, get_rating_folder_name, get_rating_folder_names

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
    birdid_confidence_threshold: float = 70.0  # 置信度阈值（70%+才写入）
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
    crop_preview: Optional[Callable[[any], None]] = None  # V4.2: 裁剪预览回调


@dataclass
class ProcessingResult:
    """处理结果数据"""
    stats: Dict[str, any] = field(default_factory=dict)
    file_ratings: Dict[str, int] = field(default_factory=dict)
    star_3_photos: List[Dict] = field(default_factory=list)
    total_time: float = 0.0
    avg_time: float = 0.0


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
        
        # DEBUG: 输出参数
        on_off = lambda b: self.i18n.t("labels.yes") if b else self.i18n.t("labels.no")
        self._log(f"\n🔍 DEBUG - {self.i18n.t('labels.processing')}:")
        self._log(f"  📊 {self.i18n.t('labels.ai_confidence')}: {settings.ai_confidence}")
        self._log(f"  📏 {self.i18n.t('labels.sharpness_short')}: {settings.sharpness_threshold}")
        self._log(f"  🎨 {self.i18n.t('labels.aesthetics')}: {settings.nima_threshold}")
        self._log(f"  🔧 {self.i18n.t('labels.normalization')}: {settings.normalization_mode}")
        self._log(f"  🦅 {self.i18n.t('labels.flight_detection')}: {on_off(settings.detect_flight)}")
        self._log(f"  📸 {self.i18n.t('labels.exposure_detection')}: {on_off(settings.detect_exposure)}")
        self._log(f"  🐦 BirdID: {on_off(settings.auto_identify)}")
        if settings.auto_identify:
            country = settings.birdid_country_code or "Auto(GPS)"
            region = settings.birdid_region_code or "All"
            self._log(f"     └─ Country: {country}, Region: {region}")
        self._log(f"  ⚙️  Min Sharpness: {self.config.min_sharpness}")
        self._log(f"  ⚙️  Min Aesthetics: {self.config.min_nima}\n")
        
        # 统计数据（支持 0/1/2/3 星）
        self.stats = {
            'total': 0,
            'star_3': 0,
            'picked': 0,
            'star_2': 0,
            'star_1': 0,  # 普通照片（合格）
            'star_0': 0,  # 普通照片（问题）
            'no_bird': 0,
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
        self.star2_reasons = {}  # 记录2星原因: 'sharpness' 或 'nima'
        self.star_3_photos = []
        self.temp_converted_jpegs = set()  # V4.0: Track temp-converted JPEGs to avoid deleting user originals
        self.file_bird_species = {}  # V4.0: Track bird species per file: {'cn_name': '...', 'en_name': '...'}
        self.burst_map = {}  # V4.0.4: Track burst group IDs: {filepath: group_id}, 0 = not a burst
        # SQLite 报告数据库（替代 CSV 缓存）
        self.report_db = None  # 在 _run_ai_detection 中初始化
        self._session_id = None
        self._resume_snapshot = {}
        self._resume_active = False
        self._settings_hash = None
        
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
        if self.report_db is None:
            self.report_db = ReportDB(self.dir_path)
        self._settings_hash = self._build_settings_hash(organize_files, cleanup_temp)
        self._resume_snapshot = self.report_db.get_resume_snapshot()
        self._prepare_session(resume=resume)
        
        # 阶段1: 文件扫描
        self.report_db.set_job_stage('scan')
        raw_dict, jpg_dict, files_tbr = self._scan_files()
        
        # 阶段1.5: V4.0.4 早期连拍检测（只基于时间戳）
        if self.settings.detect_burst:
            self.burst_map = self._detect_bursts_early(raw_dict)
        
        # 阶段2: RAW转换
        raw_files_to_convert = self._identify_raws_to_convert(raw_dict, jpg_dict, files_tbr)
        if raw_files_to_convert:
            self._convert_raws(raw_files_to_convert, files_tbr)
        
        # 阶段3: AI检测与评分
        self.report_db.set_job_stage('analyze')
        files_tbr = self._filter_files_for_resume(files_tbr)
        self._process_images(files_tbr, raw_dict)
        
        # 阶段4: 精选旗标计算（metadata_write_mode=none 时跳过）
        self.report_db.set_job_stage('picked')
        self._restore_runtime_state()
        if get_advanced_config().get_metadata_write_mode() != "none":
            self._calculate_picked_flags()
        
        # 阶段5: 文件组织
        self.report_db.set_job_stage('organize')
        self._restore_runtime_state()
        if organize_files:
            self._move_files_to_rating_folders(raw_dict)
        
        # 阶段6: V4.0.4 跨目录连拍合并（在文件整理完成后）
        if self.settings.detect_burst and self.burst_map and organize_files:
            burst_stats = self._consolidate_burst_groups(raw_dict)
            self.stats['burst_groups'] = burst_stats.get('groups', 0)
            self.stats['burst_moved'] = burst_stats.get('moved', 0)
        
        # 阶段7: 临时文件处理
        self.report_db.set_job_stage('cleanup')
        if cleanup_temp:
            self._cleanup_temp_files(files_tbr, raw_dict)
        else:
            # V4.0.5: 保留临时文件时，将路径写入数据库
            self._save_temp_paths_to_db()
            
        # 阶段8: 清理过期缓存 (V4.1)
        self._cleanup_expired_cache()
        
        # 记录结束时间
        end_time = time.time()
        self.stats['end_time'] = end_time
        self.stats['total_time'] = end_time - start_time
        self.stats['avg_time'] = (
            self.stats['total_time'] / self.stats['total']
            if self.stats['total'] > 0 else 0
        )
        self.report_db.set_job_stage('completed')
        self.report_db.finish_session('completed')
        
        # 关闭数据库连接（在所有阶段完成后）
        if hasattr(self, 'report_db') and self.report_db:
            self.report_db.close()
        
        return ProcessingResult(
            stats=self.stats.copy(),
            file_ratings=self.file_ratings.copy(),
            star_3_photos=self.star_3_photos.copy(),
            total_time=self.stats['total_time'],
            avg_time=self.stats['avg_time']
        )

    def _build_settings_hash(self, organize_files: bool, cleanup_temp: bool) -> str:
        payload = {
            'dir_path': os.path.abspath(self.dir_path),
            'settings': self.settings.__dict__.copy(),
            'organize_files': bool(organize_files),
            'cleanup_temp': bool(cleanup_temp),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def _prepare_session(self, resume: bool) -> None:
        snapshot = self._resume_snapshot or {}
        if snapshot.get('can_resume'):
            existing_hash = snapshot.get('job_settings_hash') or ''
            if existing_hash and existing_hash != self._settings_hash:
                raise RuntimeError("发现未完成任务，但当前参数与上次处理不一致。请先重置目录后重跑。")
            if not resume:
                self._log("⏯️ 检测到未完成任务，自动切换为继续上次处理。", "warning")
                resume = True

        session = self.report_db.begin_session(self._settings_hash, resume=bool(resume and snapshot.get('can_resume')))
        self._session_id = session['session_id']
        self._resume_active = bool(resume and snapshot.get('can_resume'))

    def _filter_files_for_resume(self, files_tbr: List[str]) -> List[str]:
        if not self.report_db:
            return files_tbr
        incomplete = set(self.report_db.get_incomplete_photo_prefixes())
        if not incomplete:
            return files_tbr
        filtered = [filename for filename in files_tbr if os.path.splitext(os.path.basename(filename))[0] in incomplete]
        if self._resume_active:
            skipped = len(files_tbr) - len(filtered)
            self._log(f"⏯️ Resume active: skipping {skipped} completed photos")
        return filtered

    def _restore_runtime_state(self):
        if not self.report_db:
            return
        summary = self.report_db.load_processing_summary()
        self.file_ratings = summary.get('file_ratings', {})
        self.star_3_photos = summary.get('star_3_photos', [])
        for photo in self.star_3_photos:
            file_ref = photo.get('file')
            if file_ref and not os.path.isabs(file_ref):
                photo['file'] = os.path.join(self.dir_path, file_ref)
        self.file_bird_species = summary.get('bird_species', {})
        self.stats['bird_species'] = list(summary.get('bird_species', {}).values())
        self._recalculate_stats_from_db()

    def _recalculate_stats_from_db(self):
        if not self.report_db:
            return
        stats = self.report_db.get_statistics()
        by_rating = stats.get('by_rating', {})
        self.stats['total'] = stats.get('total', 0)
        self.stats['no_bird'] = by_rating.get(-1, 0)
        self.stats['star_0'] = by_rating.get(0, 0)
        self.stats['star_1'] = by_rating.get(1, 0)
        self.stats['star_2'] = by_rating.get(2, 0)
        self.stats['star_3'] = by_rating.get(3, 0)
        self.stats['flying'] = stats.get('flying', 0)
    
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
        from constants import get_rating_folder_name
        
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
        
        for group_id, original_filepaths in groups.items():
            # 找到每个文件当前的实际位置和星级
            current_files = []
            for orig_path in original_filepaths:
                prefix = os.path.splitext(os.path.basename(orig_path))[0]
                ext = raw_dict.get(prefix, os.path.splitext(orig_path)[1])
                rating = self.file_ratings.get(prefix, 0)
                
                # 确定当前位置（可能在评分目录或鸟种子目录）
                rating_folder = get_rating_folder_name(rating)
                possible_paths = [
                    os.path.join(self.dir_path, rating_folder, prefix + ext),  # 评分目录根
                    orig_path,  # 原始位置
                ]
                
                # 检查鸟种子目录
                rating_dir = os.path.join(self.dir_path, rating_folder)
                if os.path.isdir(rating_dir):
                    for subdir in os.listdir(rating_dir):
                        subdir_path = os.path.join(rating_dir, subdir)
                        if os.path.isdir(subdir_path) and not subdir.startswith('burst_'):
                            possible_paths.append(os.path.join(subdir_path, prefix + ext))
                
                current_path = None
                for p in possible_paths:
                    if os.path.exists(p):
                        current_path = p
                        break
                
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
            
            highest_rating_folder = get_rating_folder_name(highest_rating)
            highest_rating_dir = os.path.join(self.dir_path, highest_rating_folder)
            
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
            
            # 创建 burst 目录（V4.0.6: 无识别结果时放入"其他鸟类"）
            if bird_species_name and highest_rating >= 2:
                # 有鸟种识别结果，放在鸟种子目录
                burst_dir = os.path.join(highest_rating_dir, bird_species_name, f"burst_{group_id:03d}")
            elif self.settings.auto_identify and highest_rating >= 2:
                # 启用了识鸟功能但没有识别结果，放在"其他鸟类"子目录
                other_birds = self.i18n.t("logs.folder_other_birds")
                burst_dir = os.path.join(highest_rating_dir, other_birds, f"burst_{group_id:03d}")
            else:
                # 未启用识鸟功能或低星级，直接放在评分目录
                burst_dir = os.path.join(highest_rating_dir, f"burst_{group_id:03d}")
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
                    if converted_count % 5 == 0 or converted_count == len(raw_files_to_convert):
                        self._log(self.i18n.t("logs.raw_converted", current=converted_count, total=len(raw_files_to_convert)))
                else:
                    self._log(f"  ❌ {self.i18n.t('logs.batch_failed', start=key, end=key, error=result)}", "error")
        
        raw_time = time.time() - raw_start
        avg_time = raw_time / len(raw_files_to_convert) if len(raw_files_to_convert) > 0 else 0
        # Format time string
        time_str = f"{raw_time:.1f}s" if raw_time >= 1 else f"{raw_time*1000:.0f}ms"
        self._log(self.i18n.t("logs.raw_conversion_time", time_str=time_str, avg=avg_time))
    
    def _process_images(self, files_tbr, raw_dict):
        """处理所有图片 - AI检测、关键点检测与评分"""
        # 获取模型（已在启动时预加载，此处仅获取引用）
        model = load_yolo_model()
        
        # 初始化 SQLite 报告数据库
        if self.report_db is None:
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
        
        total_files = len(files_tbr)
        self._log(self.i18n.t("logs.files_to_process", total=total_files))
        
        exiftool_mgr = get_exiftool_manager()
        metadata_batch: List[Dict] = []
        metadata_batch_prefixes: List[str] = []
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
                    batch_item = metadata_queue.get()
                    if batch_item is None:
                        metadata_queue.task_done()
                        break
                    if isinstance(batch_item, tuple):
                        batch, batch_prefixes = batch_item
                    else:
                        batch, batch_prefixes = batch_item, []
                    exif_start = time.time()
                    try:
                        exiftool_mgr.batch_set_metadata(batch)
                        if self.report_db and batch_prefixes:
                            self.report_db.mark_photos_metadata_done(batch_prefixes, self._session_id or "")
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
            batch_prefixes = metadata_batch_prefixes.copy()
            metadata_batch.clear()
            metadata_batch_prefixes.clear()
            if self.report_db and batch_prefixes:
                self.report_db.mark_photos_metadata_queued(batch_prefixes, self._session_id or "")
            if metadata_async_enabled and metadata_queue is not None:
                enqueue_start = time.time()
                metadata_queue.put(batch)  # 队列满时会背压，避免内存无限增长
                enqueue_wait_ms = (time.time() - enqueue_start) * 1000
                if enqueue_wait_ms > 0.1:
                    self._perf_add_stage('exif_enqueue_wait', enqueue_wait_ms)
                return
            exif_start = time.time()
            exiftool_mgr.batch_set_metadata(batch)
            if self.report_db and batch_prefixes:
                self.report_db.mark_photos_metadata_done(batch_prefixes, self._session_id or "")
            exif_ms = (time.time() - exif_start) * 1000
            self._perf_add_stage('exif_flush', exif_ms)
            self._perf_stats['exif_flush_count'] += 1
        
        def queue_metadata(item: Dict):
            if not item or not item.get('file'):
                return
            metadata_batch.append(item)
            prefix = item.get('db_prefix')
            if not prefix and item.get('file'):
                prefix = os.path.splitext(os.path.basename(item['file']))[0]
            if prefix:
                metadata_batch_prefixes.append(prefix)
            if len(metadata_batch) >= metadata_batch_size:
                flush_metadata_batch()
        
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
        birdid_executor = ThreadPoolExecutor(max_workers=1) if self.settings.auto_identify else None
        birdid_tasks = deque()
        identify_bird_fn = None
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
            source_filename: Optional[str] = None
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
                    nf      # name_format
                )
                self._perf_add_stage('birdid_submit', (time.time() - submit_start) * 1000)
                birdid_tasks.append((future, file_prefix, list(title_targets), source_display))
            except Exception as e:
                self._log(f"  ⚠️ Bird ID failed [{source_display}]: {e}", "warning")
        
        def apply_birdid_result(
            file_prefix: str,
            title_targets: List[str],
            birdid_result: Dict,
            source_filename: Optional[str] = None
        ):
            if not birdid_result or not birdid_result.get('success') or not birdid_result.get('results'):
                return
            source_display = source_filename or file_prefix or "?"
            top_result = birdid_result['results'][0]
            birdid_confidence = top_result.get('confidence', 0)
            cn_name = top_result.get('cn_name', '')
            en_name = top_result.get('en_name', '')
            
            if birdid_confidence >= self.settings.birdid_confidence_threshold:
                if self.i18n.current_lang.startswith('en'):
                    bird_log = en_name or cn_name
                    bird_title = en_name or cn_name
                else:
                    bird_log = cn_name or en_name
                    bird_title = cn_name or en_name
                
                self._log(f"  🐦 Bird ID [{source_display}]: {bird_log} ({birdid_confidence:.0f}%)")
                
                species_entry = {'cn_name': cn_name, 'en_name': en_name}
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
                        self.report_db.update_photo(file_prefix, {
                            'bird_species_cn': cn_name,
                            'bird_species_en': en_name,
                            'birdid_confidence': birdid_confidence,
                        })
                        # 将鸟名追加到已生成的 DB caption 最前面
                        existing = self.report_db.get_photo(file_prefix) or {}
                        old_cap = existing.get('caption') or ''
                        bird_line = f"鸟种：{cn_name or en_name}"
                        if old_cap and not old_cap.startswith('鸟种：') and not old_cap.startswith('备选鸟种'):
                            self.report_db.update_photo(file_prefix, {'caption': bird_line + '\n' + old_cap})
                        elif not old_cap:
                            self.report_db.update_photo(file_prefix, {'caption': bird_line})
                    except Exception as _e:
                        self._log(f"  ⚠️ Bird species DB write failed [{file_prefix}]: {_e}", "warning")

                for target_file in title_targets:
                    if target_file and os.path.exists(target_file):
                        queue_metadata({
                            'file': target_file,
                            'title': bird_title,
                        })
            else:
                # 低置信度：记日志，并将候选鸟名存入 file_bird_species 供 caption 使用
                self._log(
                    f"  \U0001f426 Low confidence [{source_display}]: {top_result.get('cn_name', '?')} "
                    f"({birdid_confidence:.0f}% < {self.settings.birdid_confidence_threshold}%)"
                )
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
                            bird_line = f"\u5907\u9009\u9e1f\u79cd\uff1a{cn_name}\uff1f\uff08\u628a\u63e1\u5ea6 {birdid_confidence:.0f}%\uff09"
                            if old_cap and not old_cap.startswith('\u5907\u9009\u9e1f\u79cd'):
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
            yolo_prefetch_enabled = not mps_available
        
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
        
        def run_yolo_detection(in_filepath: str, focus_point: Optional[Tuple[float, float]] = None):
            # 单模型实例在“预取线程 + 主线程复选”两处复用，串行化推理调用以保证稳定性
            with yolo_infer_lock:
                return detect_and_draw_birds(
                    in_filepath, model, None, self.dir_path, ui_settings, None,
                    skip_nima=True, focus_point=focus_point,
                    report_db=self.report_db
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
        
        def build_yolo_item(index: int, in_filename: str) -> Dict[str, any]:
            ctx = resolve_file_context(in_filename)
            in_filepath = ctx['filepath']
            
            yolo_start = time.time()
            yolo_result = None
            yolo_error = None
            try:
                yolo_result = run_yolo_detection(in_filepath, None)
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
                'result': yolo_result,
                'error': yolo_error,
                'yolo_ms': (time.time() - yolo_start) * 1000,
            }
        
        if yolo_prefetch_enabled and yolo_result_queue is not None:
            def yolo_prefetch_worker():
                try:
                    for idx, queued_filename in enumerate(files_tbr, 1):
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

        for i in range(1, total_files + 1):
            photo_stage_ms = {}
            
            def add_photo_stage(stage: str, ms: float):
                photo_stage_ms[stage] = photo_stage_ms.get(stage, 0.0) + max(0.0, float(ms))

            # Non-blocking BirdID harvest so logs appear during per-photo processing.
            collect_birdid_tasks(wait=False)
            
            # 从预取队列获取 YOLO 结果；未启用预取时回退为同步执行
            if yolo_result_queue is not None:
                yolo_wait_start = time.time()
                yolo_item = yolo_result_queue.get()
                yolo_wait_ms = (time.time() - yolo_wait_start) * 1000
                if yolo_wait_ms > 0.1:
                    add_photo_stage('yolo_queue_wait', yolo_wait_ms)
                if yolo_item is None:
                    break
            else:
                filename_inline = files_tbr[i - 1]
                yolo_item = build_yolo_item(i, filename_inline)
            
            prefetched_exif = None
            exif_prefetched = False
            if exif_prefetch_enabled:
                exif_wait_start = time.time()
                with exif_prefetch_cond:
                    while i not in exif_prefetch_results and not exif_prefetch_done:
                        exif_prefetch_cond.wait(timeout=0.01)
                    if i in exif_prefetch_results:
                        prefetched_exif = exif_prefetch_results.pop(i)
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
            if self.report_db:
                self.report_db.mark_photo_running(original_prefix, self._session_id or "")
            
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
                 
            if path_update_data and self.report_db:
                 self.report_db.update_photo(original_prefix, path_update_data)
            raw_ext = yolo_item['raw_ext']
            raw_path = yolo_item['raw_path']
            can_read_focus_raw = yolo_item['can_read_focus_raw']
            
            # 后处理阶段开始时间（最终日志会叠加 yolo_ms，保持单图耗时口径一致）
            photo_start_time = time.time()
            
            # 延迟对焦点读取：仅在必要时触发，避免在早期退出样本上浪费 IO
            preloaded_focus_result = None
            focus_point_for_selection = None
            
            # 更新进度
            should_update = (i % 5 == 0 or i == total_files or i == 1)
            if should_update:
                progress = int((i / total_files) * 100)
                self._progress(progress)
            
            result = yolo_item.get('result')
            if result is None:
                self._log(yolo_item.get('error') or self.i18n.t("logs.cannot_process", filename=filename), "error")
                if self.report_db:
                    self.report_db.mark_photo_error(
                        original_prefix,
                        self._session_id or "",
                        yolo_item.get('error') or self.i18n.t("logs.cannot_process", filename=filename)
                    )
                continue
            
            # V4.2: 解构 AI 结果（现在有 9 个返回值，包含 bird_count）
            detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count = result
            
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
                        refined_result = run_yolo_detection(filepath, focus_point_for_selection)
                        if refined_result is not None:
                            detected, _, confidence, sharpness, _, bird_bbox, img_dims, bird_mask, bird_count = refined_result
                    except Exception:
                        pass
                    add_photo_stage('yolo_refine', (time.time() - refine_start) * 1000)
            
            # V4.1: 早期退出 - 无鸟或置信度低，跳过所有后续检测
            # V4.2: 使用用户设置的 ai_confidence 阈值（百分比转小数）
            confidence_threshold = self.settings.ai_confidence / 100.0
            if not detected or (detected and confidence < confidence_threshold):
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
                if self.report_db:
                    self.report_db.mark_photo_analysis_done(original_prefix, self._session_id or "")
                
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
                
                self._perf_record_photo(photo_time_ms, photo_stage_ms, early_exit=True)

                # 即使置信度不足，只要检测到鸟就生成 crop_debug 供浏览预览
                # (yolo_debug_path 已由 ai_model.py 写入 DB，crop_debug 同步生成保持一致)
                if detected and bird_bbox is not None and img_dims is not None:
                    try:
                        import cv2 as _cv2_early
                        # _orig = _cv2_early.imread(filepath)
                        _orig = _cv2_early.imdecode(np.fromfile(filepath, dtype=np.uint8), _cv2_early.IMREAD_COLOR)
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
                                self._save_debug_crop(filename, _crop)
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
                    # orig_img = cv2.imread(filepath)  # 只读取一次!
                    orig_img = cv2.imdecode(np.fromfile(filepath, dtype=np.uint8), cv2.IMREAD_COLOR)
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
                        bird_crop_bgr = orig_img[y_orig:y_orig+h_orig_box, x_orig:x_orig+w_orig_box]
                        
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
                    
                    # V4.0.5: 复用已加载的原图，避免二次 JPEG 解码
                    # orig_img 是 cv2.imread 已读取的 BGR numpy array
                    if orig_img is not None:
                        topiq = scorer.calculate_from_array(orig_img)
                    else:
                        topiq = scorer.calculate_nima(filepath)
                except Exception as e:
                    pass  # V3.3: 简化日志，静默 TOPIQ 计算失败
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
            
            # V3.8: 飞版加成（仅当 confidence >= 0.5 且 is_flying 时）
            # 锐度+100，美学+0.5，加成后的值用于评分
            rating_sharpness = head_sharpness
            rating_topiq = topiq
            if is_flying and confidence >= 0.5:
                rating_sharpness = head_sharpness + 100
                if topiq is not None:
                    rating_topiq = topiq + 0.5
            
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
            
            # 对焦点坐标获取：只对潜在 1 星及以上样本补读，减少低价值样本 IO
            if preliminary_result.rating >= 1 and detected and bird_bbox is not None and img_dims is not None:
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
            
            # V4.0: 对焦权重计算（仅对 1 星以上照片，节省时间）
            if preliminary_result.rating >= 1:
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
                        # 检查是否是手动对焦模式
                        is_manual_focus = False
                        try:
                            import subprocess
                            focus_detector = get_focus_detector()
                            exiftool_path = focus_detector._get_exiftool_path()
                            # V3.9.4: 在 Windows 上隐藏控制台窗口
                            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
                            result = subprocess.run(
                                [exiftool_path, '-charset', 'utf8', '-FocusMode', '-s', '-s', '-s', raw_path],
                                capture_output=True, 
                                text=True, 
                                encoding='utf-8',
                                timeout=5,
                                creationflags=creationflags
                            )
                            focus_mode = result.stdout.strip().lower()
                            if 'manual' in focus_mode or focus_mode == 'mf' or focus_mode == 'm':
                                is_manual_focus = True
                        except:
                            pass
                        
                        if is_manual_focus:
                            focus_sharpness_weight = 1.0
                            focus_topiq_weight = 1.0
                        else:
                            focus_sharpness_weight = 0.7
                            focus_topiq_weight = 0.9
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
            if detected and bird_crop_bgr is not None:
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
                        focus_status_en  # 使用英文标签
                    )
                    # V4.2: 发送裁剪预览到 UI（同时传对焦状态供 dock 显示）
                    if debug_img is not None and self.callbacks.crop_preview:
                        self.callbacks.crop_preview(debug_img, focus_status_en)
                except Exception as e:
                    print(f"  ⚠️ debug_crop 保存失败 [{filename}]: {e}")  # 调试图生成失败不影响主流程
                add_photo_stage('debug_viz', (time.time() - debug_start) * 1000)
            
            # 计算真正总耗时并输出简化日志
            photo_time_ms = (time.time() - photo_start_time) * 1000 + yolo_ms
            has_exposure_issue = is_overexposed or is_underexposed
            self._log_photo_result_simple(i, total_files, filename, rating_value, reason, photo_time_ms, is_flying, has_exposure_issue, focus_status)
            
            # 记录统计（V4.2: 添加精焦判定）
            is_focus_precise = focus_sharpness_weight > 1.0 if 'focus_sharpness_weight' in dir() else False
            self._update_stats(rating_value, is_flying, has_exposure_issue, is_focus_precise)
            
            # V3.4: 确定要处理的目标文件（RAW 优先，没有则用 JPEG）
            target_file_path = None
            target_extension = None
            
            # V4.0: 标签、对焦状态、详细评分说明（RAW 与纯 JPEG 共用，纯 JPEG 也写入 EXIF 题注/星级）
            label = None
            if is_flying:
                label = 'Green'
            elif focus_sharpness_weight > 1.0:  # 头部对焦 (1.1)
                label = 'Red'
            
            caption_lines = []
            caption_lines.append(self.i18n.t("logs.caption_final", rating=rating_value, reason=reason))
            sharpness_str = f"{head_sharpness:.2f}" if head_sharpness else "N/A"
            topiq_str = f"{topiq:.2f}" if topiq else "N/A"
            caption_lines.append(self.i18n.t("logs.caption_data", conf=confidence, sharp=sharpness_str, nima=topiq_str, vis=best_eye_visibility))
            flying_str = self.i18n.t("logs.flying_yes") if is_flying else self.i18n.t("logs.flying_no")
            caption_lines.append(self.i18n.t("logs.caption_factors", sharp_w=focus_sharpness_weight, aes_w=focus_topiq_weight, flying=flying_str))
            adj_sharpness = head_sharpness * focus_sharpness_weight if head_sharpness else 0
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
                    queue_metadata({
                        'file': target_file_path,
                        'rating': rating_value if rating_value >= 0 else 0,
                        'pick': pick,
                        'sharpness': adj_sharpness,
                        'nima_score': adj_topiq_val,
                        'label': label,
                        'focus_status': focus_status,
                        'caption': caption,
                    })
                    # RAW+JPEG 时也写入当前 JPEG，便于单独查看 JPEG 时也有星级/题注（DNG/ARW/NEF 等同理）
                    # V4.0.5: 跳过临时预览文件 (tmp_*.jpg)，避免无用写入
                    filepath_basename = os.path.basename(filepath)
                    is_temp_file = filepath_basename.startswith('tmp_') or filepath_basename.startswith('tmp.')
                    if target_file_path != filepath and os.path.exists(filepath) and not is_temp_file:
                        birdid_title_targets.append(filepath)
                        queue_metadata({
                            'file': filepath,
                            'rating': rating_value if rating_value >= 0 else 0,
                            'pick': pick,
                            'sharpness': adj_sharpness,
                            'nima_score': adj_topiq_val,
                            'label': label,
                            'focus_status': focus_status,
                            'caption': caption,
                        })
                    
                    # BirdID 异步提交（2星及以上）
                    if self.settings.auto_identify and rating_value >= 2:
                        submit_birdid_task(
                            original_prefix,
                            filepath,
                            birdid_title_targets,
                            os.path.basename(target_file_path)
                        )
            else:
                # V3.4: 纯 JPEG 文件（没有对应 RAW）
                target_file_path = filepath
                target_extension = os.path.splitext(filename)[1]
                
                if os.path.exists(target_file_path):
                    queue_metadata({
                        'file': target_file_path,
                        'rating': rating_value if rating_value >= 0 else 0,
                        'pick': pick,
                        'sharpness': adj_sharpness,
                        'nima_score': adj_topiq_val,
                        'label': label,
                        'focus_status': focus_status,
                        'caption': caption,
                    })
                    # BirdID 异步提交（2星及以上）
                    if self.settings.auto_identify and rating_value >= 2:
                        submit_birdid_task(
                            original_prefix,
                            filepath,
                            [target_file_path],
                            os.path.basename(target_file_path)
                        )

            # V3.4: 以下操作对 RAW 和纯 JPEG 都执行
            if target_file_path and os.path.exists(target_file_path):
                # V4.1: 计算调整后锐度（用于 CSV，保证重新评星一致性）
                adj_sharpness_csv = head_sharpness * focus_sharpness_weight if head_sharpness else 0
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
                )
                add_photo_stage('csv_update', (time.time() - csv_update_start) * 1000)
                
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
                
                # 记录2星原因（用于分目录）（V3.8: 使用加成后的值）
                if rating_value == 2:
                    sharpness_ok = rating_sharpness >= self.settings.sharpness_threshold
                    topiq_ok = rating_topiq is not None and rating_topiq >= self.settings.nima_threshold
                    if sharpness_ok and not topiq_ok:
                        self.star2_reasons[file_prefix] = 'sharpness'
                    elif topiq_ok and not sharpness_ok:
                        self.star2_reasons[file_prefix] = 'nima'  # 保留原字段名兼容
                    else:
                        self.star2_reasons[file_prefix] = 'both'
            
            self._perf_record_photo(photo_time_ms, photo_stage_ms, early_exit=False)
        
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
            self._log(f"⏳ 正在等待剩余 BirdID 识别结果 ({len(birdid_tasks)} 个任务)...")
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
        star_map = {3: "3★", 2: "2★", 1: "1★", 0: "0★", -1: "-1★"}
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
        
        # 输出简化格式（对焦状态已在reason中显示）
        self._log(f"[{index:03d}/{total}] {filename} | {star_text} ({reason_short}) {flight_tag}| {time_text}")
    
    def _save_debug_crop(
        self,
        filename: str,
        bird_crop_bgr: np.ndarray,
        bird_crop_mask: np.ndarray = None,
        head_center_crop: tuple = None,
        head_radius: int = None,
        focus_point_crop: tuple = None,
        focus_status: str = None
    ):
        """
        V3.9: 保存调试可视化图片到 .superpicky/debug_crops/ 目录
        
        标注内容：
        - 🟢 绿色半透明: SEG mask 鸟身区域
        - 🔵 蓝色圆圈: 头部检测区域
        - 🔴 红色十字: 对焦点位置
        """
        import cv2
        
        # 创建调试目录（Windows 下自动隐藏）
        debug_dir = os.path.join(self.dir_path, ".superpicky", "cache", "crop_debug")
        ensure_hidden_directory(os.path.join(self.dir_path, ".superpicky"))
        os.makedirs(debug_dir, exist_ok=True)
        
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
                exists = os.path.exists(file_path)
                self._log(f"    🔍 Picked: {os.path.basename(file_path)} (exists: {exists})")
            
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
        else:
            self._log(self.i18n.t("logs.picked_no_intersection"))
            self.stats['picked'] = 0
    
    def _move_files_to_rating_folders(self, raw_dict):
        """移动文件到分类文件夹（V4.0: 2星和3星按鸟种分目录）"""
        # 筛选需要移动的文件（包括所有星级，确保原目录为空）
        files_to_move = []
        for prefix, rating in self.file_ratings.items():
            if rating in [-1, 0, 1, 2, 3]:
                base_folder = get_rating_folder_name(rating)
                
                # V4.0: 2-star and 3-star photos go to bird species subdirectories
                # 只有高置信度（无 low_confidence 标记）才按鸟种分目录
                if rating >= 2 and prefix in self.file_bird_species and not self.file_bird_species[prefix].get('low_confidence'):
                    # Photo with confirmed species identification
                    bird_info = self.file_bird_species[prefix]
                    if self.i18n.current_lang.startswith('en'):
                        bird_name = bird_info.get('en_name', '').replace(' ', '_')
                    else:
                        bird_name = bird_info.get('cn_name', '')
                    if not bird_name:
                        bird_name = bird_info.get('cn_name', '') or bird_info.get('en_name', '').replace(' ', '_') or 'Unknown'
                    folder = os.path.join(base_folder, bird_name)
                elif rating >= 2:
                    # 2-star/3-star without species ID, put in "Other Birds"
                    other_birds = self.i18n.t("logs.folder_other_birds")
                    folder = os.path.join(base_folder, other_birds)
                else:
                    # 0-star, 1-star, -1-star go directly to rating folder
                    folder = base_folder
                
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
        
        self._log(f"\n📂 Moving {len(files_to_move)} photos to rating folders...")
        
        # 创建文件夹（使用实际的目录名，支持多层）
        folders_in_use = set(f['folder'] for f in files_to_move)
        for folder_name in folders_in_use:
            folder_path = os.path.join(self.dir_path, folder_name)
            if not os.path.exists(folder_path):
                os.makedirs(folder_path)
                # V4.0: Show clearer folder creation log
                if os.path.sep in folder_name or '/' in folder_name:
                    self._log(f"  📁 Created folder: {folder_name}/")
                else:
                    self._log(f"  📁 Created folder: {folder_name}/")
        
        # 移动文件
        moved_count = 0
        for file_info in files_to_move:
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
            self._log(f"  📋 Manifest: .superpicky_manifest.json")
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
