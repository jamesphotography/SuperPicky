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

        # 鸟种英文名显示格式 V4.x (AviList mapping)
        #   "default"    = OSEA model original names
        #   "avilist"    = AviList v2025 English names
        #   "clements"   = Clements / eBird v2024 English names
        #   "birdlife"   = BirdLife v9 English names
        #   "scientific" = Scientific name only
        "name_format": "default",

        # V4.2.7: 分目录布局策略
        # "rating-first"  (默认) — 3星_优选/白胸鸲鹟/photo.NEF
        # "species-first"        — 白胸鸲鹟/3星_优选/photo.NEF
        # 1星/0星/-1星 永远只用 rating 目录，与本设置无关
        "folder_layout": "rating-first",

        # 外部编辑应用（右键菜单 "用 X 打开"）
        # 每项格式：{"name": "显示名称", "path": "/Applications/...app"}
        "external_apps": [],

        # 浏览器排序偏好（用户上次选择）
        # 可选值: "rarity_desc" | "filename" | "sharpness_desc" | "aesthetic_desc"
        "browser_sort": "rarity_desc",

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

        # V4.3 Phase 1: 视频分析配置 / Video analysis config
        # video_max_frames        : 单视频抽帧总数上限（处理时间与视频时长解耦）
        #                           范围 30-240，默认 60（macOS MPS 下 ~15-25s/视频）
        # video_yolo_threshold    : YOLO 鸟类检测置信度阈值 (0.3-0.9)
        # video_min_segment_frames: 时间段最少帧数（过滤误检），默认 2 帧
        "video_max_frames": 60,
        "video_yolo_threshold": 0.5,
        "video_min_segment_frames": 2,
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

    # Setter方法
    def set_min_confidence(self, value):
        """设置AI置信度阈值 (0.3-0.7)"""
        self.config["min_confidence"] = max(0.3, min(0.7, float(value)))

    def set_min_sharpness(self, value):
        """设置锐度最低阈值 (100-500) - 头部区域锐度"""
        self.config["min_sharpness"] = max(100, min(500, int(value)))

    def set_min_nima(self, value):
        """设置美学最低阈值 (0.0-5.0)"""
        self.config["min_nima"] = max(0.0, min(5.0, float(value)))

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
        获取针对当前文件的 ARW 写入策略。
        若 file_path 为 ARW 格式，强制返回 "sidecar"（只写 XMP 侧车，不修改 ARW 本体）。
        """
        if file_path and Path(file_path).suffix.lower() == ".arw":
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
        """设置自选模式下的锐度阈值 (200-600)"""
        self.config["custom_sharpness"] = max(200, min(600, int(value)))
    
    def set_custom_aesthetics(self, value):
        """设置自选模式下的美学阈值 (4.0-7.0)"""
        self.config["custom_aesthetics"] = max(4.0, min(7.0, float(value)))

    # V4.1: 临时文件管理 getter/setter
    @property
    def keep_temp_files(self):
        return self.config.get("keep_temp_files", True)

    def set_keep_temp_files(self, value):
        self.config["keep_temp_files"] = bool(value)

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
        """设置分目录布局: rating-first (默认) 或 species-first。"""
        from core.folder_layout import VALID_LAYOUTS, DEFAULT_LAYOUT
        self.config["folder_layout"] = value if value in VALID_LAYOUTS else DEFAULT_LAYOUT

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


def get_advanced_config():
    """获取全局配置实例（单例模式）"""
    registry = get_lazy_registry()
    return registry.get_or_create("advanced_config.instance", AdvancedConfig)
