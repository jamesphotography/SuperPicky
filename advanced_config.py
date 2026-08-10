#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V3.2 - 高级配置管理
用于管理所有可配置的硬编码参数
"""

import json
import os
from pathlib import Path
import sys
from typing import Dict, Optional
from tools.i18n import t as _t
from config import get_app_config_dir, get_lazy_registry


class AdvancedConfig:
    """高级配置类 - 管理所有硬编码参数"""

    # 默认配置
    DEFAULT_CONFIG = {
        # 评分阈值（影响0星判定）
        "min_confidence": 0.5,      # AI置信度最低阈值 (0.3-0.7) - 低于此值判定为0星
        "min_sharpness": 100,       # 锐度最低阈值 - 低于此值判定为0星（头部区域锐度）
        "min_nima": 3.5,            # NIMA美学最低阈值 (3.0-5.0) - 低于此值判定为0星
        # V3.2: 移除 max_brisque（不再使用 BRISQUE 评估）

        # 精选设置
        "picked_top_percentage": 25, # 精选旗标Top百分比 (10-50) - 3星照片中美学+锐度双排名在此百分比内的设为精选
        
        # 曝光检测设置 V3.8
        "exposure_threshold": 0.10,  # 曝光阈值 (0.05-0.20) - 过曝/欠曝像素占比超过此值将降级一星
        
        # 连拍检测设置 V4.0.4
        "burst_fps": 10,  # 连拍速度 (4-20张/秒) - 拍摄速度快于此值视为连拍
        "burst_min_count": 4,         # 连拍最少张数 (3-10) - 至少此数量连续照片才算连拍组
        
        # 鸟种识别设置 V4.2
        "birdid_confidence": 50,      # 识别置信度阈值 (30-95) - 低于此值不写入EXIF

        # 输出设置
        "save_csv": True,           # 是否保存CSV报告
        "log_level": "detailed",    # 日志详细程度: "simple" | "detailed"

        # 语言设置（后续实现）
        "language": None,           # zh_CN | en_US | None (Auto)
        
        # V4.3: 摄影水平预设 (Skill Level Presets)
        "skill_level": "intermediate",  # 摄影水平: "beginner" | "intermediate" | "master" | "custom"
        "is_first_run": True,           # 是否首次运行
        "custom_sharpness": 380,        # 自选模式下的锐度阈值
        "custom_aesthetics": 4.8,       # 自选模式下的美学阈值

        # V4.6: 评星 V2(批内相对+配额) / Rating V2 (batch-relative + quota)
        "rating_algorithm": "v2",       # "v1"(绝对阈值,回滚用) | "v2"(批内相对+配额)
        # 自选档的起点取「大师」标准(3★20/2★30),用户可自行往宽调
        # Custom mode starts from the strictest preset (master); users loosen from there.
        "custom_quota3": 20,            # 自选模式下的 3★ 配额百分比 (5-50)
        "custom_quota2": 30,            # 自选模式下的 2★ 配额百分比 (5-60,且 3★+2★≤95)

        # ARW 写入策略:
        #   sidecar: 只写 XMP 侧车，不修改 ARW（最安全，推荐）
        #   embedded: 直接写入 ARW
        #   inplace: 尝试 in-place 写入 ARW（可能失败）
        #   auto: 尝试 embedded/inplace，若检测到结构变化则回退 sidecar
        "arw_write_mode": "embedded",

        # 全局元数据写入模式（覆盖所有文件类型）:
        #   embedded: 默认行为（ARW 走 arw_write_mode，JPG 直写）
        #   sidecar:  所有文件统一写 .xmp 侧车，不修改原文件
        #   none:     跳过所有元数据写入，仅按评分整理目录
        "metadata_write_mode": "embedded",
        
        # 临时文件管理 V4.1
        "keep_temp_files": True,        # 保留临时预览图片（统一控制 tmp JPG + debug crops）
        "completion_sound_enabled": True,  # 处理完成提示音 / Completion sound after processing

        # 鸟种英文名显示格式 V4.x (AviList mapping)
        #   "default"    = OSEA model original names
        #   "avilist"    = AviList v2025 English names
        #   "clements"   = Clements / eBird v2024 English names
        #   "birdlife"   = BirdLife v9 English names
        #   "scientific" = Scientific name only
        "name_format": "default",

        # V4.2.7 / V4.3 Phase 4: 分目录布局策略
        # "rating-first"           — 3星_优选/白胸鸲鹟/photo.NEF
        # "species-first" (默认)   — 白胸鸲鹟/3星_优选/photo.NEF
        # 1星/0星/-1星 永远聚到「其他鸟类」分支，与本设置无关
        # V4.3 Phase 4: 默认改为 species-first，让视频和照片共享鸟种目录
        "folder_layout": "species-first",
        "burst_group_folders": True,  # 连拍归入 burst_NNN 子目录(关=按星级/鸟种常规归档,Paul P1)

        # V4.6: 无鸟补救扫描 (spec: docs/specs/2026-07-14-no-bird-rescue-scan-design.md)
        # V4.6: No-bird rescue scan
        "rescue_scan_enabled": True,   # 判无鸟/低置信度时触发 1024px 重扫 + 识鸟守门
        "rescue_birdid_gate": 10,      # 弱候选的识鸟确认门槛 (0-100, top1 置信度百分比)

        # 外部编辑应用（右键菜单 "用 X 打开"）
        # 每项格式：{"name": "显示名称", "path": "/Applications/...app"}
        "external_apps": [],

        # 浏览器排序偏好（用户上次选择）
        # 可选值: "rarity_desc" | "filename" | "sharpness_desc" | "aesthetic_desc"
        "browser_sort": "rarity_desc",

        # 为 0 星/无鸟照片补充详情面板元数据。关闭后保留早期退出以提升速度。
        # Populate detail-panel metadata for 0-star/no-bird photos. Disable to keep faster early exits.
        "detail_metadata_for_rejected": False,

        # 浏览器删除确认弹窗（首次弹窗后可勾选「不再确认」关闭）
        "delete_confirm": True,

        # 更新提醒控制
        "ignored_update_version": None,  # 跳过提醒的版本号，如 "4.3.0"
        "include_prerelease": False,      # 是否接收 Beta/RC 更新提醒
        "auto_check_updates": False,       # 启动时自动检查更新（含补丁）

        # V4.3+: 轻量底包首启初始化状态
        "initialization_completed": False,
        "initialization_manifest_version": "v1",
        "initialization_in_progress": False,
        "last_init_exit_reason": "none",
        "last_init_mode": "none",

        # V4.3+: 运行时选择与能力探测
        "selected_runtime_variant": "auto",   # auto | cpu | cuda | mac
        "detected_cuda_capable": False,
        "runtime_install_location_preference": None,  # None | default | install
        "resolved_runtime_dir": None,

        # V4.3+: 首启启用的功能集与资源记录
        "enabled_feature_set": [
            "core_detection",
            "quality",
            "keypoint",
            "flight",
            "birdid",
        ],
        "downloaded_resources": {},
        "resolved_source_map": {},
        "last_init_error": None,

        # 主界面复选框状态
        "flight_check": False,   # 飞鸟检测默认关闭（开启后速度较慢，用户可手动开启）
        "burst_check": False,    # 连拍检测默认关闭（开启后速度较慢，用户可手动开启）
        "exposure_check": False, # 曝光检测默认关闭

        # 最近选鸟目录历史（最多保留 10 个，按最近使用时间倒序）
        "recent_directories": [],

        # 主窗口位置和最大化状态。普通几何与最大化状态分开存储，便于跨屏幕校验。
        # Main-window placement and maximized state. Normal geometry is stored
        # separately from maximized state so it can be validated across monitors.
        "main_window_geometry": None,
        "main_window_maximized": False,

        # V4.3 Phase 1: 视频分析配置 / Video analysis config
        # video_max_frames        : 单视频抽帧总数上限（处理时间与视频时长解耦）
        #                           范围 30-240，默认 60（macOS MPS 下 ~15-25s/视频）
        # video_yolo_threshold    : YOLO 鸟类检测置信度阈值 (0.3-0.9)
        # video_min_segment_frames: 时间段最少帧数（过滤误检），默认 2 帧
        "video_max_frames": 60,
        "video_yolo_threshold": 0.5,
        "video_min_segment_frames": 2,

        # V4.3 Phase 4: 主流程视频集成 / Main-flow video integration
        # video_auto_process_in_main : 选鸟时是否自动分析视频（默认关——大多数用户不拍视频，
        #                              首页快速面板「视频」开关 + 首次发现视频的一次性提示均可开启）
        # video_species_mode         : 默认识别模式 instant/fast/full（默认 instant 极速）
        # video_enable_species_id    : 是否启用鸟种识别（默认开）
        # video_enable_flight        : 是否启用飞行检测（默认开）
        # video_first_run_prompted   : 首次发现视频弹一次性提示后标记（避免重复弹）
        "video_auto_process_in_main": False,
        "video_species_mode": "instant",
        "video_enable_species_id": True,
        "video_enable_flight": True,
        "video_first_run_prompted": False,

        # V4.4: 识鸟设置统一进 advanced_config(原 birdid_dock_settings.json)
        "birdid_auto_identify": False,
        "birdid_write_keywords": True,  # 识别后写鸟名到 XMP-dc:Subject(LR 关键字,Paul P1-1)
        "birdid_use_geo_filter": True,
        "birdid_country_code": None,
        "birdid_selected_country": "自动检测 (GPS)",
        "birdid_region_code": None,
        "birdid_selected_region": "整个国家",
        "birdid_dock_settings_migrated": False,

        # 纠错样本提交：首次是否已弹过「自愿说明」/ Correction submission first-run consent
        "correction_consent_shown": False,
    }

    def __init__(self, config_file=None):
        """初始化配置"""
        # 如果没有指定配置文件路径，使用用户目录
        if config_file is None:
            config_dir = get_app_config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            self.config_file = str(config_dir / "advanced_config.json")
        else:
            self.config_file = config_file

        self.config = self.DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        """从文件加载配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # 合并配置（保留默认值中有但加载配置中没有的项）
                    self.config.update(loaded_config)
                    # 旧键 birdid_use_ebird 一次性迁移到 birdid_use_geo_filter。
                    # 判断必须基于磁盘上的 loaded_config：self.config 已合并
                    # DEFAULT_CONFIG，新键在其中恒存在，用它判断会让迁移永不触发，
                    # 老用户关闭过的开关会被静默重置为默认 True。
                    # One-time migration from the legacy birdid_use_ebird key.
                    # The check must use loaded_config: self.config has already
                    # merged DEFAULT_CONFIG, so the new key always exists there
                    # and would make this migration dead code, silently resetting
                    # the switch for users who had turned it off.
                    if (
                        "birdid_use_geo_filter" not in loaded_config
                        and "birdid_use_ebird" in loaded_config
                    ):
                        self.config["birdid_use_geo_filter"] = bool(
                            loaded_config["birdid_use_ebird"]
                        )
                    self.config.pop("birdid_use_ebird", None)
                print(f"✅ Advanced config loaded: {self.config_file}")
            except Exception as e:
                print(_t("logs.config_load_failed", e=e))

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            print(_t("logs.config_saved", path=self.config_file))
            return True
        except Exception as e:
            print(_t("logs.config_save_failed", e=e))
            return False

    def reset_to_default(self):
        """重置为默认配置"""
        self.config = self.DEFAULT_CONFIG.copy()

    # Getter方法
    @property
    def min_confidence(self):
        return self.config["min_confidence"]

    @property
    def min_sharpness(self):
        return self.config["min_sharpness"]

    @property
    def min_nima(self):
        return self.config["min_nima"]

    # V3.2: 移除 max_brisque 属性

    @property
    def rating_algorithm(self) -> str:
        """评星算法: "v2"=批内相对+配额(默认) | "v1"=绝对阈值(回滚开关)"""
        value = self.config.get("rating_algorithm", "v2")
        return value if value in ("v1", "v2") else "v2"

    def set_rating_algorithm(self, value: str):
        self.config["rating_algorithm"] = value if value in ("v1", "v2") else "v2"
        self.save()

    @property
    def custom_quota3(self) -> float:
        """自选模式下的 3★ 配额百分比 (clamp 5-50,与 UI 滑块范围一致)"""
        return float(self.config.get("custom_quota3", 20))

    def set_custom_quota3(self, value: float):
        self.config["custom_quota3"] = max(5, min(50, int(value)))
        self.save()

    @property
    def custom_quota2(self) -> float:
        """自选模式下的 2★ 配额百分比 (clamp 5-60,与 QuotaBar 2★ 段宽范围一致)。

        1★ 为算术余量 (100 − 3★ − 2★),不单独存储;3★+2★≤95 的联合约束由
        QuotaBar 拖动时保证(保留 1★ 最小 5%)。

        Custom-mode 2-star quota percentage (clamp 5-60, matching the QuotaBar
        2-star segment range). 1★ is the remainder (100 − 3★ − 2★) and is not
        stored; the joint 3★+2★≤95 constraint is enforced by QuotaBar on drag.
        """
        return float(self.config.get("custom_quota2", 30))

    def set_custom_quota2(self, value: float):
        self.config["custom_quota2"] = max(5, min(60, int(value)))
        self.save()

    @property
    def picked_top_percentage(self):
        return self.config["picked_top_percentage"]
    
    @property
    def exposure_threshold(self):
        return self.config.get("exposure_threshold", 0.10)
    
    @property
    def burst_fps(self):
        """连拍速度 (4-20张/秒)"""
        return self.config.get("burst_fps", 10)
    
    @property
    def burst_time_threshold(self):
        """连拍时间阈值 (ms) - 从 FPS 计算"""
        fps = self.burst_fps
        return int(1000 / fps)  # 10 FPS = 100ms
    
    @property
    def burst_min_count(self):
        return self.config.get("burst_min_count", 4)
    
    @property
    def birdid_confidence(self):
        return self.config.get("birdid_confidence", 50)

    @property
    def save_csv(self):
        return self.config["save_csv"]

    @property
    def log_level(self):
        return self.config["log_level"]

    @property
    def language(self):
        return self.config["language"]

    @property
    def correction_consent_shown(self) -> bool:
        """纠错样本提交的首次自愿说明是否已展示过。"""
        return bool(self.config.get("correction_consent_shown", False))

    @property
    def rescue_scan_enabled(self):
        """无鸟补救扫描开关 / No-bird rescue scan toggle."""
        return self.config.get("rescue_scan_enabled", True)

    @property
    def rescue_birdid_gate(self):
        """补救识鸟确认门槛 (0-100) / Rescue BirdID gate percent (0-100)."""
        return self.config.get("rescue_birdid_gate", 10)

    # Setter方法
    def set_min_confidence(self, value):
        """设置AI置信度阈值 (0.3-0.7)"""
        self.config["min_confidence"] = max(0.3, min(0.7, float(value)))

    def set_min_sharpness(self, value):
        """设置锐度最低阈值 (100-600) - 头部区域锐度"""
        self.config["min_sharpness"] = max(100, min(600, int(value)))

    def set_min_nima(self, value):
        """设置美学最低阈值 (0.0-7.0)"""
        self.config["min_nima"] = max(0.0, min(7.0, float(value)))

    # V3.2: 移除 set_max_brisque 方法

    def set_picked_top_percentage(self, value):
        """设置精选旗标Top百分比 (10-50)"""
        self.config["picked_top_percentage"] = max(10, min(50, int(value)))
    
    def set_exposure_threshold(self, value):
        """设置曝光阈值 (0.05-0.20)"""
        self.config["exposure_threshold"] = max(0.05, min(0.20, float(value)))
    
    def set_burst_fps(self, value):
        """设置连拍速度 (4-20张/秒)"""
        self.config["burst_fps"] = max(4, min(20, int(value)))
    
    def set_burst_min_count(self, value):
        """设置连拍最少张数 (3-10)"""
        self.config["burst_min_count"] = max(3, min(10, int(value)))
    
    def set_birdid_confidence(self, value):
        """设置鸟种识别置信度阈值 (30-95)"""
        self.config["birdid_confidence"] = max(30, min(95, int(value)))

    def set_save_csv(self, value):
        """设置是否保存CSV"""
        self.config["save_csv"] = bool(value)

    def set_correction_consent_shown(self, value: bool) -> None:
        """设置纠错样本提交首次说明已展示，并持久化。"""
        self.config["correction_consent_shown"] = bool(value)
        self.save()

    def set_rescue_scan_enabled(self, value):
        """设置无鸟补救扫描开关 / Toggle the no-bird rescue scan."""
        self.config["rescue_scan_enabled"] = bool(value)

    def set_rescue_birdid_gate(self, value):
        """设置补救识鸟确认门槛 (0-100) / Rescue BirdID gate percent (0-100)."""
        self.config["rescue_birdid_gate"] = max(0, min(100, int(value)))

    def set_log_level(self, value):
        """设置日志详细程度"""
        if value in ["simple", "detailed"]:
            self.config["log_level"] = value

    def set_language(self, value):
        """设置语言"""
        # 兼容性处理：如果传入 'en'，自动转换为 'en_US'
        if value == 'en':
            value = 'en_US'
            
        if value in ["zh_CN", "en_US"]:
            self.config["language"] = value

    # V4.3: 摄影水平预设 (Skill Level Presets)
    @property
    def skill_level(self):
        return self.config.get("skill_level", "intermediate")
    
    @property
    def is_first_run(self):
        return self.config.get("is_first_run", True)
    
    @property
    def custom_sharpness(self):
        return self.config.get("custom_sharpness", 380)
    
    @property
    def custom_aesthetics(self):
        return self.config.get("custom_aesthetics", 4.8)

    @property
    def arw_write_mode(self):
        return self.config.get("arw_write_mode", "sidecar")

    def get_arw_write_mode_for_file(self, file_path=None):
        """
        获取针对当前文件的 RAW 写入策略。
        若 file_path 为专有 RAW 格式（SIDECAR_RAW_EXTENSIONS，DNG 除外），
        强制返回 "sidecar"（只写 XMP 侧车，不修改 RAW 本体）。
        原先仅 ARW 强制侧车，现扩展到全部专有 RAW（快 ~33% 且不动本体更安全）。

        Get the RAW write mode for this file. Proprietary RAW formats
        (SIDECAR_RAW_EXTENSIONS, DNG excluded) are forced to "sidecar" —
        XMP sidecar only, never rewriting the RAW body. Previously only ARW
        was forced; now all proprietary RAW are (~33% faster, safer).
        """
        from constants import SIDECAR_RAW_EXTENSIONS
        if file_path and Path(file_path).suffix.lower() in SIDECAR_RAW_EXTENSIONS:
            return "sidecar"
        return self.config.get("arw_write_mode", "sidecar")

    def set_arw_write_mode(self, value):
        """设置 ARW 写入策略: sidecar | embedded | inplace | auto"""
        if value in ("sidecar", "embedded", "inplace", "auto"):
            self.config["arw_write_mode"] = value

    def get_metadata_write_mode(self) -> str:
        """获取全局元数据写入模式: embedded | sidecar | none"""
        return self.config.get("metadata_write_mode", "embedded")

    def set_metadata_write_mode(self, value):
        """设置全局元数据写入模式: embedded | sidecar | none"""
        if value in ("embedded", "sidecar", "none"):
            self.config["metadata_write_mode"] = value
    
    def set_skill_level(self, value):
        """设置摄影水平: beginner | intermediate | master | custom"""
        if value in ["beginner", "intermediate", "master", "custom"]:
            self.config["skill_level"] = value
    
    def set_is_first_run(self, value):
        """设置是否首次运行"""
        self.config["is_first_run"] = bool(value)
    
    def set_custom_sharpness(self, value):
        """设置自选模式下的锐度阈值 (100-600) - 与 set_min_sharpness clamp 对齐"""
        # V4.8: 下限 200→100,与 set_min_sharpness 及 UI 滑块(100-600)一致。
        # 此前 200 会在拖到 100-199 时静默截断,导致 custom 档恢复时默认值漂移。
        self.config["custom_sharpness"] = max(100, min(600, int(value)))
    
    def set_custom_aesthetics(self, value):
        """设置自选模式下的美学阈值 (0.0-7.0) - 与 set_min_nima clamp 对齐"""
        # V4.6: 下限 4.0→0.0,与 set_min_nima 及 UI 滑块(0-70,即 0.0-7.0)一致。
        # 此前 4.0 会在拖到 0.0-3.9 时静默截断(滑块下半段 56% 行程失效),
        # 导致 custom 档恢复时把用户阈值改写回 4.0,GUI 与 CLI 评星结果同时受影响。
        # V4.6: lower bound 4.0→0.0, matching set_min_nima and the UI slider
        # (0-70 => 0.0-7.0). The old 4.0 floor silently truncated the lower 56%
        # of the slider, so restoring the custom preset overwrote the user's
        # threshold back to 4.0 — affecting both GUI and CLI ratings.
        self.config["custom_aesthetics"] = max(0.0, min(7.0, float(value)))

    # V4.1: 临时文件管理 getter/setter
    @property
    def keep_temp_files(self):
        return self.config.get("keep_temp_files", True)

    def set_keep_temp_files(self, value):
        self.config["keep_temp_files"] = bool(value)

    @property
    def completion_sound_enabled(self) -> bool:
        return self.config.get("completion_sound_enabled", True)

    def set_completion_sound_enabled(self, value: bool):
        self.config["completion_sound_enabled"] = bool(value)

    # V4.x: 鸟种英文名显示格式
    @property
    def name_format(self):
        return self.config.get("name_format", "default")

    def set_name_format(self, value):
        """设置鸟种英文名显示格式: default | avilist | clements | birdlife | scientific"""
        if value in ("default", "avilist", "clements", "birdlife", "scientific"):
            self.config["name_format"] = value

    # V4.2.7: 分目录布局 (rating-first 或 species-first)
    # V4.2.7: Output folder layout (rating-first or species-first)
    @property
    def folder_layout(self) -> str:
        from core.folder_layout import normalize_layout
        return normalize_layout(self.config.get("folder_layout"))

    def set_folder_layout(self, value: str) -> None:
        """设置分目录布局: rating-first / species-first / flat(平铺,不移动文件)。"""
        from core.folder_layout import VALID_LAYOUTS, DEFAULT_LAYOUT
        self.config["folder_layout"] = value if value in VALID_LAYOUTS else DEFAULT_LAYOUT

    @property
    def burst_group_folders(self) -> bool:
        """
        连拍照片是否归入独立 burst_NNN 子目录(默认 True=现状)。
        关闭后连拍照片按各自星级/鸟种走常规归档;连拍检测、DB burst 列、
        评分阶段连拍 3★ 封顶均不受影响。

        Whether burst groups get their own burst_NNN subfolder (default
        True). When off, burst shots are filed like normal photos; burst
        detection, DB columns and the 3-star burst cap are unaffected.
        """
        return bool(self.config.get("burst_group_folders", True))

    def set_burst_group_folders(self, value: bool) -> None:
        """
        设置「连拍归入独立子文件夹」开关(不内部 save,由设置页统一保存)。

        参数:
        value (bool): 是否分子文件夹

        Set the burst-subfolder toggle (no internal save; the settings
        page persists via its own cfg.save()).

        Parameters:
        value (bool): Whether to group bursts into subfolders.
        """
        self.config["burst_group_folders"] = bool(value)

    # 外部应用配置 getter/setter
    def get_external_apps(self) -> list:
        """返回外部编辑应用列表，每项 {"name": str, "path": str}。"""
        return list(self.config.get("external_apps", []))

    def set_external_apps(self, apps: list):
        """保存外部编辑应用列表。"""
        self.config["external_apps"] = list(apps)

    def get_browser_sort(self) -> str:
        """返回浏览器排序偏好: rarity_desc | filename | sharpness_desc | aesthetic_desc"""
        return self.config.get("browser_sort", "rarity_desc")

    def set_browser_sort(self, value: str):
        """保存浏览器排序偏好。"""
        if value in ("rarity_desc", "filename", "sharpness_desc", "aesthetic_desc"):
            self.config["browser_sort"] = value

    def get_detail_metadata_for_rejected(self) -> bool:
        """
        返回是否为 0 星/无鸟照片补充详情面板元数据。

        开启后，处理器会为早期拒绝照片写入可获得的相机 EXIF；对于低置信度但检测到鸟的照片，
        还会继续执行必要的质量分析，以便结果浏览器显示锐度、美学、飞行和对焦信息。

        Return whether detail-panel metadata should be populated for 0-star/no-bird photos.

        When enabled, the processor writes available camera EXIF for early
        rejects. For low-confidence photos where a bird is detected, it also
        continues the needed quality analysis so the result browser can show
        sharpness, aesthetics, flying, and focus information.
        """
        return bool(
            self.config.get(
                "detail_metadata_for_rejected",
                self.DEFAULT_CONFIG["detail_metadata_for_rejected"],
            )
        )

    def set_detail_metadata_for_rejected(self, enabled: bool):
        """
        保存 0 星/无鸟照片详情元数据补充开关。

        参数:
        enabled (bool): True 表示补充可获得的详情元数据，False 表示保留快速早期退出。

        Save the detail-metadata population flag for 0-star/no-bird photos.

        Parameters:
        enabled (bool): True to populate available detail metadata, False to
        keep the faster early-exit behavior.
        """
        self.config["detail_metadata_for_rejected"] = bool(enabled)

    @property
    def delete_confirm(self) -> bool:
        return self.config.get("delete_confirm", True)

    @property
    def ignored_update_version(self):
        return self.config.get("ignored_update_version", None)

    @property
    def include_prerelease(self) -> bool:
        return self.config.get("include_prerelease", False)

    def set_delete_confirm(self, value: bool):
        self.config["delete_confirm"] = bool(value)

    def set_ignored_update_version(self, value):
        """设置要跳过提醒的版本号，传 None 清除。"""
        self.config["ignored_update_version"] = value if isinstance(value, str) else None

    def set_include_prerelease(self, value: bool):
        """设置是否接收预发布版本提醒。"""
        self.config["include_prerelease"] = bool(value)

    @property
    def auto_check_updates(self) -> bool:
        return self.config.get("auto_check_updates", False)

    def set_auto_check_updates(self, value: bool):
        """设置启动时是否自动检查更新。"""
        self.config["auto_check_updates"] = bool(value)

    # V4.3+: 首启初始化状态 getter/setter
    def _set_init_config(self, key: str, value):
        self.config[key] = value

    @property
    def initialization_completed(self) -> bool:
        return self.config.get("initialization_completed", False)

    def set_initialization_completed(self, value: bool):
        self._set_init_config("initialization_completed", bool(value))

    @property
    def initialization_manifest_version(self) -> str:
        return str(self.config.get("initialization_manifest_version", "v1"))

    def set_initialization_manifest_version(self, value: str):
        self._set_init_config("initialization_manifest_version", str(value or "v1"))

    @property
    def initialization_in_progress(self) -> bool:
        return self.config.get("initialization_in_progress", False)

    def set_initialization_in_progress(self, value: bool):
        self._set_init_config("initialization_in_progress", bool(value))

    @property
    def last_init_exit_reason(self) -> str:
        value = str(self.config.get("last_init_exit_reason", "none") or "none")
        return value if value in ("none", "interrupted", "failed") else "none"

    def set_last_init_exit_reason(self, value: str):
        normalized = value if value in ("none", "interrupted", "failed") else "none"
        self._set_init_config("last_init_exit_reason", normalized)

    @property
    def last_init_mode(self) -> str:
        value = str(self.config.get("last_init_mode", "none") or "none")
        return value if value in ("none", "init", "repair") else "none"

    def set_last_init_mode(self, value: str):
        normalized = value if value in ("none", "init", "repair") else "none"
        self._set_init_config("last_init_mode", normalized)

    @property
    def selected_runtime_variant(self) -> str:
        return str(self.config.get("selected_runtime_variant", "auto"))

    def set_selected_runtime_variant(self, value: str):
        if value in ("auto", "cpu", "cuda", "mac"):
            self._set_init_config("selected_runtime_variant", value)

    @property
    def detected_cuda_capable(self) -> bool:
        return self.config.get("detected_cuda_capable", False)

    def set_detected_cuda_capable(self, value: bool):
        self._set_init_config("detected_cuda_capable", bool(value))

    @property
    def runtime_install_location_preference(self):
        value = self.config.get("runtime_install_location_preference", None)
        return value if value in ("default", "install", None) else None

    def set_runtime_install_location_preference(self, value):
        normalized = value if value in ("default", "install") else None
        self._set_init_config("runtime_install_location_preference", normalized)

    @property
    def resolved_runtime_dir(self):
        value = self.config.get("resolved_runtime_dir", None)
        return None if value in (None, "") else str(value)

    def set_resolved_runtime_dir(self, value):
        self._set_init_config("resolved_runtime_dir", None if not value else str(value))

    @property
    def enabled_feature_set(self) -> list:
        return list(self.config.get("enabled_feature_set", []))

    def set_enabled_feature_set(self, features: list):
        self._set_init_config("enabled_feature_set", list(features))

    @property
    def downloaded_resources(self) -> dict:
        return dict(self.config.get("downloaded_resources", {}))

    def set_downloaded_resources(self, resources: dict):
        self._set_init_config("downloaded_resources", dict(resources))

    @property
    def resolved_source_map(self) -> dict:
        return dict(self.config.get("resolved_source_map", {}))

    def set_resolved_source_map(self, source_map: dict):
        self._set_init_config("resolved_source_map", dict(source_map))

    @property
    def last_init_error(self):
        return self.config.get("last_init_error", None)

    def set_last_init_error(self, value):
        self._set_init_config("last_init_error", value if value is None else str(value))

    # 主界面复选框状态 getter/setter
    @property
    def flight_check(self):
        return self.config.get("flight_check", False)

    @property
    def burst_check(self):
        return self.config.get("burst_check", False)

    @property
    def exposure_check(self):
        return self.config.get("exposure_check", False)

    def set_flight_check(self, value):
        self.config["flight_check"] = bool(value)

    def set_burst_check(self, value):
        self.config["burst_check"] = bool(value)

    def set_exposure_check(self, value):
        self.config["exposure_check"] = bool(value)

    # ──────────────────────────────────────────────
    # 主窗口位置状态
    # ──────────────────────────────────────────────
    def get_main_window_geometry(self) -> Optional[Dict[str, int]]:
        """
        获取主窗口普通状态下的几何信息。

        返回:
        Optional[Dict[str, int]]: 包含 x/y/width/height 的字典；缺失或格式异常时返回 None。

        Get the main window normal-state geometry.

        Return:
        Optional[Dict[str, int]]: Dict with x/y/width/height, or None when missing/invalid.
        """
        value = self.config.get("main_window_geometry")
        if not isinstance(value, dict):
            return None

        required_keys = ("x", "y", "width", "height")
        if not all(key in value for key in required_keys):
            return None

        try:
            return {key: int(value[key]) for key in required_keys}
        except (TypeError, ValueError):
            return None

    def set_main_window_geometry(self, value: Optional[Dict[str, int]]) -> None:
        """
        保存主窗口普通状态下的几何信息。

        参数:
        value (Optional[Dict[str, int]]): 包含 x/y/width/height 的字典；None 表示清空。

        Save the main window normal-state geometry.

        Parameters:
        value (Optional[Dict[str, int]]): Dict with x/y/width/height, or None to clear.
        """
        if value is None:
            self.config["main_window_geometry"] = None
            return

        self.config["main_window_geometry"] = {
            "x": int(value["x"]),
            "y": int(value["y"]),
            "width": int(value["width"]),
            "height": int(value["height"]),
        }

    @property
    def main_window_maximized(self) -> bool:
        return bool(self.config.get("main_window_maximized", False))

    def set_main_window_maximized(self, value: bool) -> None:
        self.config["main_window_maximized"] = bool(value)

    # ──────────────────────────────────────────────
    # 最近选鸟目录历史
    # ──────────────────────────────────────────────
    def get_recent_directories(self) -> list:
        """返回全部历史目录列表（不过滤），最多 10 条，供菜单使用。"""
        return list(self.config.get("recent_directories", []))

    def get_available_recent_directories(self, n: int = 3) -> list:
        """返回当前可访问的最近目录（os.path.isdir 过滤），最多 n 条，供地址栏弹出使用。"""
        return [
            d for d in self.config.get("recent_directories", [])
            if os.path.isdir(d)
        ][:n]

    def add_recent_directory(self, directory: str) -> None:
        """将目录插入历史列表头部，去重，最多保留 10 条，并保存。"""
        dirs = [d for d in self.config.get("recent_directories", []) if d != directory]
        dirs.insert(0, directory)
        self.config["recent_directories"] = dirs[:10]
        self.save()

    def get_dict(self):
        """获取配置字典（用于传递给其他模块）"""
        return self.config.copy()

    # V4.4: 识鸟设置 (Bird Identification Settings)
    @property
    def birdid_auto_identify(self) -> bool:
        """
        获取自动识鸟开关。

        返回:
        bool: 是否启用自动识鸟

        Get the auto bird identification flag.

        Return:
        bool: Whether auto bird identification is enabled.
        """
        return bool(self.config.get("birdid_auto_identify", False))

    @property
    def birdid_write_keywords(self) -> bool:
        """
        识别成功后是否把鸟名写入照片关键字(XMP-dc:Subject,Lightroom Keywords)。

        返回:
        bool: 是否写入关键字,默认 True

        Whether to write the species name into the photo's keywords
        (XMP-dc:Subject) after identification.

        Return:
        bool: Defaults to True.
        """
        return bool(self.config.get("birdid_write_keywords", True))

    def set_birdid_write_keywords(self, value: bool):
        """
        设置「识别后写入关键字」开关并保存。

        参数:
        value (bool): 是否写入关键字

        Set the write-keywords toggle and save.

        Parameters:
        value (bool): Whether to write species keywords.
        """
        self.config["birdid_write_keywords"] = bool(value)
        self.save()

    @property
    def birdid_use_geo_filter(self) -> bool:
        """
        获取是否启用地理过滤（GPS 网格 + 国家级候选层）。

        该开关控制整个地理过滤链路，而非仅某个数据源；旧键 `birdid_use_ebird`
        的迁移在 `load()` 中完成，此处只读新键。

        返回:
        bool: 是否启用（默认 True）

        Get whether geographic filtering is enabled (GPS grid + country tiers).
        The switch governs the whole geo-filter pipeline, not one data source;
        migration from the legacy `birdid_use_ebird` key happens in `load()`.

        Return:
        bool: Whether enabled (default True).
        """
        return bool(self.config.get("birdid_use_geo_filter", True))

    @property
    def birdid_country_code(self):
        """
        获取识鸟国家代码。

        返回:
        str or None: ISO 国家代码（如 "AU"），无选择时为 None

        Get the bird identification country code.

        Return:
        str or None: ISO country code (e.g., "AU"), None if not set.
        """
        return self.config.get("birdid_country_code")

    @property
    def birdid_selected_country(self) -> str:
        """
        获取识鸟国家显示名称。

        返回:
        str: 用户选择的国家显示名称（默认 "自动检测 (GPS)"）

        Get the bird identification country display name.

        Return:
        str: User-selected country name (default "自动检测 (GPS)").
        """
        return self.config.get("birdid_selected_country", "自动检测 (GPS)")

    @property
    def birdid_region_code(self):
        """
        获取识鸟地区代码。

        返回:
        str or None: 地区代码（如 "AU-QLD"），无选择时为 None

        Get the bird identification region code.

        Return:
        str or None: Region code (e.g., "AU-QLD"), None if not set.
        """
        return self.config.get("birdid_region_code")

    @property
    def birdid_selected_region(self) -> str:
        """
        获取识鸟地区显示名称。

        返回:
        str: 用户选择的地区显示名称（默认 "整个国家"）

        Get the bird identification region display name.

        Return:
        str: User-selected region name (default "整个国家").
        """
        return self.config.get("birdid_selected_region", "整个国家")

    def set_birdid_auto_identify(self, value: bool):
        """
        设置自动识鸟开关并保存。

        参数:
        value (bool): 是否启用自动识鸟

        Set the auto bird identification flag and save.

        Parameters:
        value (bool): Whether to enable auto bird identification.
        """
        self.config["birdid_auto_identify"] = bool(value)
        self.save()

    # V4.3 Phase 4: 主流程视频总开关 (Main-flow video auto-process toggle)
    @property
    def video_auto_process_in_main(self) -> bool:
        """
        获取选鸟时是否自动分析视频。

        返回:
        bool: 是否启用主流程视频自动处理（默认 False）

        Get whether videos are auto-processed during the main culling flow.

        Return:
        bool: Whether main-flow video auto-processing is enabled (default False).
        """
        return bool(self.config.get("video_auto_process_in_main", False))

    def set_video_auto_process_in_main(self, value: bool):
        """
        设置选鸟时是否自动分析视频并保存。

        参数:
        value (bool): 是否启用主流程视频自动处理

        Set whether videos are auto-processed during the main culling flow, and save.

        Parameters:
        value (bool): Whether to enable main-flow video auto-processing.
        """
        self.config["video_auto_process_in_main"] = bool(value)
        self.save()

    def set_birdid_region(
        self,
        use_geo_filter: bool,
        country_code,
        selected_country: str,
        region_code,
        selected_region: str,
    ):
        """
        一次性设置全部识鸟地区相关字段并保存。

        参数:
        use_geo_filter (bool): 是否启用地理过滤
        country_code (str or None): ISO 国家代码
        selected_country (str): 国家显示名称
        region_code (str or None): 地区代码
        selected_region (str): 地区显示名称

        Set all bird identification region settings at once and save.

        Parameters:
        use_geo_filter (bool): Whether geographic filtering is enabled.
        country_code (str or None): ISO country code.
        selected_country (str): Country display name.
        region_code (str or None): Region code.
        selected_region (str): Region display name.
        """
        self.config["birdid_use_geo_filter"] = bool(use_geo_filter)
        self.config["birdid_country_code"] = country_code
        self.config["birdid_selected_country"] = selected_country
        self.config["birdid_region_code"] = region_code
        self.config["birdid_selected_region"] = selected_region
        self.save()

    def migrate_birdid_dock_settings(self, legacy_path: str = None) -> bool:
        """
        一次性把旧 birdid_dock_settings.json 搬入 advanced_config。幂等(哨兵字段);旧文件保留。

        参数:
        legacy_path (str, optional): 旧配置文件路径。如果为 None，使用默认应用配置目录下的文件

        返回:
        bool: 若成功迁移返回 True，若已迁移或文件不存在或读取失败返回 False

        Migrate legacy birdid_dock_settings.json to advanced_config. Idempotent (sentinel field); legacy file is kept.

        Parameters:
        legacy_path (str, optional): Path to legacy config file. If None, uses default app config directory.

        Return:
        bool: True if migration succeeded, False if already migrated, file not found, or read failed.
        """
        from config import get_app_config_dir

        # 哨兵检查:已迁移过则直接返回 False / Sentinel check: skip if already migrated
        if self.config.get("birdid_dock_settings_migrated", False):
            return False

        if legacy_path is None:
            legacy_path = os.path.join(
                str(get_app_config_dir()), "birdid_dock_settings.json"
            )

        if not os.path.exists(legacy_path):
            # 全新用户无旧文件:标记已处理,避免每次启动重复探测
            # New user has no legacy file: mark as processed to avoid repeated detection on startup
            self.config["birdid_dock_settings_migrated"] = True
            self.save()
            return False

        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            return False  # 读失败不置位,下次重试 / Don't set flag on read failure; retry next time

        self.config["birdid_use_geo_filter"] = bool(old.get("use_ebird", True))
        self.config["birdid_country_code"] = old.get("country_code")
        self.config["birdid_selected_country"] = old.get(
            "selected_country", "自动检测 (GPS)"
        )
        self.config["birdid_region_code"] = old.get("region_code")
        self.config["birdid_selected_region"] = old.get("selected_region", "整个国家")
        self.config["birdid_dock_settings_migrated"] = True
        self.save()
        return True


def get_advanced_config():
    """获取全局配置实例（单例模式）"""
    registry = get_lazy_registry()
    return registry.get_or_create("advanced_config.instance", AdvancedConfig)
