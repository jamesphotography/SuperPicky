"""
SuperPicky 配置管理模块
统一管理所有常量和配置项
"""
import os
import sys
from dataclasses import dataclass
from typing import List, Dict


def resource_path(relative_path):
    """获取资源文件路径，支持 PyInstaller 打包"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


@dataclass
class FileConfig:
    """文件处理相关配置"""
    RAW_EXTENSIONS: List[str] = None
    JPG_EXTENSIONS: List[str] = None
    
    def __post_init__(self):
        if self.RAW_EXTENSIONS is None:
            self.RAW_EXTENSIONS = ['.nef', '.cr2', '.cr3', '.arw', '.raf', 
                                 '.orf', '.rw2', '.pef', '.dng', '.3fr', '.iiq']
        if self.JPG_EXTENSIONS is None:
            self.JPG_EXTENSIONS = ['.jpg', '.jpeg']


@dataclass 
class DirectoryConfig:
    """目录名称配置"""
    EXCELLENT_DIR: str = "优秀"          # 优质照片目录
    STANDARD_DIR: str = "标准"           # 标准照片目录  
    NO_BIRDS_DIR: str = "没鸟"           # 无鸟照片目录
    TEMP_DIR: str = "_temp"              # 临时目录（原 Resize）
    REDBOX_DIR: str = "Redbox"           # 标注目录（原 Box）
    CROP_TEMP_DIR: str = ".crop_temp"     # 裁剪图片临时目录（隐藏）
    
    # 对比测试目录
    OLD_ALGORITHM_EXCELLENT: str = "老算法优秀"    # 老算法选择的优秀照片
    NEW_ALGORITHM_EXCELLENT: str = "新算法优秀"    # 新算法选择的优秀照片
    BOTH_ALGORITHMS_EXCELLENT: str = "双算法优秀"   # 两种算法都选择的照片
    ALGORITHM_DIFF_DIR: str = "算法差异"           # 算法结果不同的照片
    
    # 日志和报告文件（隐藏）
    LOG_FILE: str = ".process_log.txt"
    REPORT_FILE: str = ".report.csv"
    COMPARISON_REPORT_FILE: str = ".algorithm_comparison.csv"


@dataclass
class AIConfig:
    """AI 模型相关配置"""
    MODEL_FILE: str = "models/yolo11m-seg.pt"  # 使用 yolo11m 分割模型（更快，精度略降1.9%）
    BIRD_CLASS_ID: int = 14              # YOLO 模型中鸟类的类别 ID
    TARGET_IMAGE_SIZE: int = 1024        # 图像预处理目标尺寸（保持1024以维持锐度值一致性）
    CENTER_THRESHOLD: float = 0.15       # 鸟类位置中心阈值

    # 锐度计算配置
    SHARPNESS_NORMALIZATION: str = None  # 锐度归一化方法：None(推荐), 'sqrt', 'linear', 'log', 'gentle'

    def get_model_path(self) -> str:
        """获取模型文件完整路径"""
        return resource_path(self.MODEL_FILE)


@dataclass
class UIConfig:
    """UI 界面相关配置"""
    # 滑块配置
    CONFIDENCE_SCALE: float = 100.0      # 置信度滑块缩放系数
    AREA_SCALE: float = 1000.0           # 面积滑块缩放系数
    SHARPNESS_SCALE: int = 20            # 清晰度滑块缩放系数
    
    # 进度条配置
    PROGRESS_MIN: int = 0
    PROGRESS_MAX: int = 100
    
    # 系统音效重复次数
    BEEP_COUNT: int = 3


@dataclass
class CSVConfig:
    """CSV 报告相关配置"""
    HEADERS: List[str] = None
    
    def __post_init__(self):
        if self.HEADERS is None:
            self.HEADERS = [
                # 基本信息
                "filename", "found_bird", "AI score", "bird_centre_x", 
                "bird_centre_y", "bird_area", "s_bird_area", 
                # 新算法 - 核心指标
                "laplacian_var", "sobel_var", "fft_high_freq", "contrast", 
                "edge_density", "background_complexity", "motion_blur",
                "normalized_new", "composite_score", "result_new",
                # 位置和基本判断
                "dominant_bool", "centred_bool", "sharp_bool", "class_id"
            ]


class Config:
    """主配置类，统一管理所有配置项"""
    
    def __init__(self):
        self.file = FileConfig()
        self.directory = DirectoryConfig()
        self.ai = AIConfig()
        self.ui = UIConfig()
        self.csv = CSVConfig()
    
    def get_directory_names(self) -> Dict[str, str]:
        """获取所有目录名称映射"""
        return {
            'excellent': self.directory.EXCELLENT_DIR,
            'standard': self.directory.STANDARD_DIR,
            'no_birds': self.directory.NO_BIRDS_DIR,
            'temp': self.directory.TEMP_DIR,
            'redbox': self.directory.REDBOX_DIR,
            'crop_temp': self.directory.CROP_TEMP_DIR
        }
    
    def is_raw_file(self, filename: str) -> bool:
        """检查是否为 RAW 格式文件"""
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.file.RAW_EXTENSIONS
    
    def is_jpg_file(self, filename: str) -> bool:
        """检查是否为 JPG 格式文件"""
        _, ext = os.path.splitext(filename)
        return ext.lower() in self.file.JPG_EXTENSIONS


# 全局配置实例
config = Config()