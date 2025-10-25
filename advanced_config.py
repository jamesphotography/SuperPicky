#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V3.1 - 高级配置管理
用于管理所有可配置的硬编码参数
"""

import json
import os
from pathlib import Path


class AdvancedConfig:
    """高级配置类 - 管理所有硬编码参数"""

    # 默认配置
    DEFAULT_CONFIG = {
        # 评分阈值（影响0星判定）
        "min_confidence": 0.5,      # AI置信度最低阈值 (0.3-0.7) - 低于此值判定为0星
        "min_sharpness": 4000,      # 锐度最低阈值 (2000-6000) - 低于此值判定为0星
        "min_nima": 4.0,            # NIMA美学最低阈值 (3.0-5.0) - 低于此值判定为0星
        "max_brisque": 30,          # BRISQUE噪点最高阈值 (20-50) - 高于此值判定为0星

        # 精选设置
        "picked_top_percentage": 25, # 精选旗标Top百分比 (10-50) - 3星照片中美学+锐度双排名在此百分比内的设为精选

        # 输出设置
        "save_csv": True,           # 是否保存CSV报告
        "log_level": "detailed",    # 日志详细程度: "simple" | "detailed"

        # 语言设置（后续实现）
        "language": "zh_CN",        # zh_CN | en_US
    }

    def __init__(self, config_file="advanced_config.json"):
        """初始化配置"""
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
                print(f"✅ 已加载高级配置: {self.config_file}")
            except Exception as e:
                print(f"⚠️  加载配置失败，使用默认值: {e}")

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            print(f"✅ 已保存高级配置: {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ 保存配置失败: {e}")
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

    @property
    def max_brisque(self):
        return self.config["max_brisque"]

    @property
    def picked_top_percentage(self):
        return self.config["picked_top_percentage"]

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
        """设置锐度最低阈值 (2000-6000)"""
        self.config["min_sharpness"] = max(2000, min(6000, int(value)))

    def set_min_nima(self, value):
        """设置美学最低阈值 (3.0-5.0)"""
        self.config["min_nima"] = max(3.0, min(5.0, float(value)))

    def set_max_brisque(self, value):
        """设置噪点最高阈值 (20-50)"""
        self.config["max_brisque"] = max(20, min(50, int(value)))

    def set_picked_top_percentage(self, value):
        """设置精选旗标Top百分比 (10-50)"""
        self.config["picked_top_percentage"] = max(10, min(50, int(value)))

    def set_save_csv(self, value):
        """设置是否保存CSV"""
        self.config["save_csv"] = bool(value)

    def set_log_level(self, value):
        """设置日志详细程度"""
        if value in ["simple", "detailed"]:
            self.config["log_level"] = value

    def set_language(self, value):
        """设置语言"""
        if value in ["zh_CN", "en_US"]:
            self.config["language"] = value

    def get_dict(self):
        """获取配置字典（用于传递给其他模块）"""
        return self.config.copy()


# 全局配置实例
_config_instance = None


def get_advanced_config():
    """获取全局配置实例（单例模式）"""
    global _config_instance
    if _config_instance is None:
        _config_instance = AdvancedConfig()
    return _config_instance
