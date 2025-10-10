"""
配置管理器 - 核心层
负责所有配置相关的操作和验证
"""
import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from config import config


class ConfigManager:
    """配置管理器，提供配置访问和验证功能"""
    
    def __init__(self):
        self._config = config
    
    # ============ 文件相关配置 ============
    def is_supported_image_file(self, filename: str) -> bool:
        """检查是否为支持的图像文件"""
        return self._config.is_jpg_file(filename)
    
    def is_raw_file(self, filename: str) -> bool:
        """检查是否为RAW文件"""
        return self._config.is_raw_file(filename)
    
    def get_supported_raw_extensions(self) -> List[str]:
        """获取支持的RAW文件扩展名"""
        return self._config.file.RAW_EXTENSIONS.copy()
    
    def get_supported_jpg_extensions(self) -> List[str]:
        """获取支持的JPG文件扩展名"""
        return self._config.file.JPG_EXTENSIONS.copy()
    
    # ============ 目录相关配置 ============
    def get_directory_names(self) -> Dict[str, str]:
        """获取所有目录名称配置"""
        return self._config.get_directory_names()
    
    def get_excellent_dir_name(self) -> str:
        """获取优秀照片目录名"""
        return self._config.directory.EXCELLENT_DIR
    
    def get_standard_dir_name(self) -> str:
        """获取标准照片目录名"""
        return self._config.directory.STANDARD_DIR
    
    def get_no_birds_dir_name(self) -> str:
        """获取无鸟照片目录名"""
        return self._config.directory.NO_BIRDS_DIR
    
    def get_crop_temp_dir_name(self) -> str:
        """获取裁剪临时目录名"""
        return self._config.directory.CROP_TEMP_DIR
    
    def get_log_file_name(self) -> str:
        """获取日志文件名"""
        return self._config.directory.LOG_FILE
    
    def get_report_file_name(self) -> str:
        """获取报告文件名"""
        return self._config.directory.REPORT_FILE
    
    def get_log_file_path(self, directory_path: str) -> str:
        """获取日志文件完整路径"""
        return os.path.join(directory_path, self.get_log_file_name())
    
    def get_csv_file_path(self, directory_path: str) -> str:
        """获取CSV报告文件完整路径"""
        return os.path.join(directory_path, self.get_report_file_name())
    
    # ============ AI 相关配置 ============
    def get_model_path(self) -> str:
        """获取AI模型文件路径"""
        return self._config.ai.get_model_path()
    
    def get_bird_class_id(self) -> int:
        """获取鸟类类别ID"""
        return self._config.ai.BIRD_CLASS_ID
    
    def get_target_image_size(self) -> int:
        """获取图像处理目标尺寸"""
        return self._config.ai.TARGET_IMAGE_SIZE
    
    def get_center_threshold(self) -> float:
        """获取鸟类位置中心阈值"""
        return self._config.ai.CENTER_THRESHOLD
    
    # ============ UI 相关配置 ============
    def get_ui_scales(self) -> Dict[str, float]:
        """获取UI滑块缩放系数"""
        return {
            'confidence': self._config.ui.CONFIDENCE_SCALE,
            'area': self._config.ui.AREA_SCALE,
            'sharpness': self._config.ui.SHARPNESS_SCALE
        }
    
    def get_progress_bar_config(self) -> Dict[str, int]:
        """获取进度条配置"""
        return {
            'min': self._config.ui.PROGRESS_MIN,
            'max': self._config.ui.PROGRESS_MAX
        }
    
    def get_beep_count(self) -> int:
        """获取系统提示音重复次数"""
        return self._config.ui.BEEP_COUNT
    
    # ============ CSV 相关配置 ============
    def get_csv_headers(self) -> List[str]:
        """获取CSV报告头部字段"""
        return self._config.csv.HEADERS.copy()
    
    # ============ 配置验证 ============
    def validate_ui_settings(self, ui_settings: List[Any]) -> bool:
        """验证UI设置参数的有效性"""
        if len(ui_settings) != 3:
            return False
        
        confidence, area_percent, sharpness = ui_settings
        
        # 验证置信度 (0.0 - 1.0 或 0-100百分比格式)
        if not isinstance(confidence, (int, float)):
            return False
        # 支持两种格式：0-1和0-100
        if not ((0.0 <= confidence <= 1.0) or (0.0 <= confidence <= 100.0)):
            return False
            
        # 验证面积百分比 (0.0 - 100.0)
        if not isinstance(area_percent, (int, float)) or not (0.0 <= area_percent <= 100.0):
            return False
            
        # 验证清晰度阈值 (正数)
        if not isinstance(sharpness, (int, float)) or sharpness <= 0:
            return False
            
        return True
    
    def get_processing_thresholds(self, ui_settings: List[Any]) -> Dict[str, float]:
        """根据UI设置计算处理阈值"""
        if not self.validate_ui_settings(ui_settings):
            raise ValueError("Invalid UI settings provided")
            
        confidence = float(ui_settings[0])
        # 如果置信度>1，说明是百分比格式，需要转换为0-1范围
        if confidence > 1.0:
            confidence = confidence / 100.0
            
        return {
            'ai_confidence': confidence,
            'area_threshold': float(ui_settings[1]) / 100.0,  # 转换为0-1范围
            'sharpness_threshold': float(ui_settings[2])
        }


# 全局配置管理器实例
config_manager = ConfigManager()