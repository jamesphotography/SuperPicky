"""
图像处理服务 - 服务层
协调核心层组件，提供高级业务逻辑
"""
import os
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum

from core.config_manager import config_manager
from core.file_manager import file_manager, ProcessingDirectories
from core.bird_detector import bird_detector, DetectionResult, ProcessingThresholds

# 动态导入 RAW 处理模块，如果不可用则使用替代方案
try:
    import rawpy
    import imageio
    RAW_PROCESSING_AVAILABLE = True
except ImportError:
    RAW_PROCESSING_AVAILABLE = False


class ProcessingStatus(Enum):
    """处理状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ProcessingStats:
    """处理统计信息"""
    total_files: int
    processed_files: int
    excellent_count: int
    standard_count: int
    no_birds_count: int
    error_count: int
    raw_converted_count: int


@dataclass
class ProcessingProgress:
    """处理进度信息"""
    current_file: str
    current_index: int
    total_files: int
    status: ProcessingStatus
    message: str
    stats: ProcessingStats


class ImageProcessingService:
    """图像处理服务，提供完整的图像处理工作流"""
    
    def __init__(self):
        self.config_manager = config_manager
        self.file_manager = file_manager
        self.bird_detector = bird_detector
        
        # 处理状态
        self._processing_directories: Optional[ProcessingDirectories] = None
        self._progress_callback: Optional[Callable[[ProcessingProgress], None]] = None
        
        # 临时文件跟踪 - 记录每个文件是否为临时生成
        self._temp_jpg_files: Dict[str, str] = {}  # filename -> full_path 映射
        
    # ============ 服务配置 ============
    def set_progress_callback(self, callback: Callable[[ProcessingProgress], None]) -> None:
        """设置进度回调函数"""
        self._progress_callback = callback
    
    def _report_progress(self, progress: ProcessingProgress) -> None:
        """报告处理进度"""
        if self._progress_callback:
            self._progress_callback(progress)
    
    # ============ 主要处理流程 ============
    def process_directory(self, directory_path: str, ui_settings: List[Any]) -> bool:
        """
        处理指定目录中的所有图像
        
        Args:
            directory_path: 要处理的目录路径
            ui_settings: UI设置 [confidence, area_percent, sharpness]
            
        Returns:
            bool: 处理是否成功完成
        """
        try:
            # 1. 验证输入
            if not os.path.exists(directory_path):
                return False
            
            if not self.config_manager.validate_ui_settings(ui_settings):
                return False
            
            # 2. 初始化处理环境
            self._setup_processing_environment(directory_path)
            thresholds = self._create_processing_thresholds(ui_settings)
            
            # 3. 扫描和准备文件
            raw_dict, jpg_dict, files_to_process = self.file_manager.scan_directory(directory_path)
            
            # 4. 处理RAW文件转换
            files_to_process = self._handle_raw_conversion(
                directory_path, raw_dict, jpg_dict, files_to_process
            )
            
            # 5. 处理所有图像文件
            stats = self._process_all_images(
                directory_path, files_to_process, thresholds
            )
            
            # 6. 生成最终报告
            self._generate_final_report(directory_path, ui_settings, stats)
            
            return True
            
        except Exception as e:
            self.file_manager.write_log(f"ERROR in process_directory: {e}", directory_path)
            return False
    
    def _setup_processing_environment(self, directory_path: str) -> None:
        """设置处理环境"""
        # 创建所有需要的目录
        self._processing_directories = self.file_manager.create_processing_directories(directory_path)
        
        # 初始化CSV报告
        self.file_manager.initialize_csv_report(directory_path)
        
        self.file_manager.write_log("=" * 80, directory_path)
        self.file_manager.write_log("SuperPicky 处理开始", directory_path)
        self.file_manager.write_log("=" * 80, directory_path)
    
    def _create_processing_thresholds(self, ui_settings: List[Any]) -> ProcessingThresholds:
        """创建处理阈值"""
        thresholds_dict = self.config_manager.get_processing_thresholds(ui_settings)
        
        return ProcessingThresholds(
            ai_confidence=thresholds_dict['ai_confidence'],
            area_threshold=thresholds_dict['area_threshold'],
            sharpness_threshold=thresholds_dict['sharpness_threshold'],
            center_threshold=self.config_manager.get_center_threshold()
        )
    
    # ============ RAW文件处理 ============
    def _handle_raw_conversion(self, directory_path: str, raw_dict: Dict[str, str],
                              jpg_dict: Dict[str, str], files_to_process: List[str]) -> List[str]:
        """处理RAW文件转换"""
        converted_files = files_to_process.copy()
        # 重置临时文件跟踪
        self._temp_jpg_files.clear()
        
        for file_prefix, raw_extension in raw_dict.items():
            if file_prefix in jpg_dict:
                # 已有JPG文件，跳过转换
                self.file_manager.write_log(
                    f"FILE: [{file_prefix}] has raw and jpg files", directory_path
                )
                continue
            
            # 转换RAW到JPG
            raw_file_path = os.path.join(directory_path, file_prefix + raw_extension)
            if self._convert_raw_to_jpg(raw_file_path):
                jpg_filename = file_prefix + ".jpg"
                jpg_filepath = os.path.join(directory_path, jpg_filename)
                
                # 记录为临时文件：filename -> original_path
                self._temp_jpg_files[jpg_filename] = jpg_filepath
                converted_files.append(jpg_filename)
                
                self.file_manager.write_log(
                    f"FILE: [{file_prefix}] converted to temporary JPG", directory_path
                )
            else:
                self.file_manager.write_log(
                    f"ERROR: Failed to convert RAW file [{file_prefix}]", directory_path
                )
        
        return converted_files
    
    def _convert_raw_to_jpg(self, raw_file_path: str) -> bool:
        """转换单个RAW文件到JPG"""
        if not RAW_PROCESSING_AVAILABLE:
            self.file_manager.write_log(
                "WARNING: RAW processing not available, skipping RAW conversion",
                os.path.dirname(raw_file_path)
            )
            return False
            
        try:
            filename = os.path.basename(raw_file_path)
            file_prefix, _ = os.path.splitext(filename)
            directory_path = os.path.dirname(raw_file_path)
            jpg_file_path = os.path.join(directory_path, file_prefix + ".jpg")
            
            if os.path.exists(jpg_file_path):
                return True  # 已存在JPG文件
            
            if not os.path.exists(raw_file_path):
                return False
            
            with rawpy.imread(raw_file_path) as raw:
                thumbnail = raw.extract_thumb()
                if thumbnail is None:
                    return False
                
                if thumbnail.format == rawpy.ThumbFormat.JPEG:
                    with open(jpg_file_path, 'wb') as f:
                        f.write(thumbnail.data)
                    return True
                elif thumbnail.format == rawpy.ThumbFormat.BITMAP:
                    imageio.imsave(jpg_file_path, thumbnail.data)
                    return True
            
            return False
            
        except Exception as e:
            self.file_manager.write_log(f"ERROR converting RAW file: {e}", os.path.dirname(raw_file_path))
            return False
    
    # ============ 图像处理 ============
    def _process_all_images(self, directory_path: str, files_to_process: List[str],
                           thresholds: ProcessingThresholds) -> ProcessingStats:
        """处理所有图像文件"""
        total_files = len(files_to_process)
        stats = ProcessingStats(
            total_files=total_files,
            processed_files=0,
            excellent_count=0,
            standard_count=0,
            no_birds_count=0,
            error_count=0,
            raw_converted_count=0
        )
        
        processed_files = set()
        
        for index, filename in enumerate(files_to_process):
            # 报告进度
            progress = ProcessingProgress(
                current_file=filename,
                current_index=index + 1,
                total_files=total_files,
                status=ProcessingStatus.PROCESSING,
                message=f"Processing {filename}",
                stats=stats
            )
            self._report_progress(progress)
            
            # 跳过重复文件
            if filename in processed_files:
                self.file_manager.write_log(f"Skipping {filename}, already processed", directory_path)
                continue
            
            # 处理单个文件
            result = self._process_single_image(directory_path, filename, thresholds)
            
            if result is not None:
                # 保存检测结果到CSV
                csv_data = self.bird_detector.detection_result_to_csv_data(result, filename)
                self.file_manager.write_csv_row(csv_data, directory_path)
                
                # 移动文件到相应目录
                target_dir = self._determine_target_directory(result)
                file_prefix = os.path.splitext(filename)[0]
                
                if self.file_manager.move_file_group(file_prefix, directory_path, target_dir):
                    # 更新统计
                    if result.bird_selected:
                        stats.excellent_count += 1
                    elif result.found_bird:
                        stats.standard_count += 1
                    else:
                        stats.no_birds_count += 1
                else:
                    stats.error_count += 1
                    
                stats.processed_files += 1
                processed_files.add(filename)
                
                # 立即删除临时JPG文件（如果这是临时生成的）
                self._cleanup_temp_jpg_immediately(filename, directory_path)
            else:
                stats.error_count += 1
                # 处理失败也要清理临时文件
                self._cleanup_temp_jpg_immediately(filename, directory_path)
        
        return stats
    
    def _process_single_image(self, directory_path: str, filename: str,
                             thresholds: ProcessingThresholds) -> Optional[DetectionResult]:
        """处理单个图像文件"""
        self.file_manager.write_log("-" * 80, directory_path)
        
        filepath = os.path.join(directory_path, filename)
        
        if not os.path.exists(filepath):
            self.file_manager.write_log(
                f"ERROR: attempting to process file that does not exist {filename}",
                directory_path
            )
            return None
        
        # 使用鸟类检测器处理图像
        crop_temp_dir = self._processing_directories.crop_temp_dir if self._processing_directories else None
        result = self.bird_detector.detect_birds_in_image(filepath, thresholds, crop_temp_dir)
        
        if result is None:
            self.file_manager.write_log(
                f"ERROR: Failed to process image {filename}",
                directory_path
            )
        
        return result
    
    def _determine_target_directory(self, result: DetectionResult) -> str:
        """根据检测结果确定目标目录"""
        if not self._processing_directories:
            raise RuntimeError("Processing directories not initialized")
        
        if result.bird_selected:
            return self._processing_directories.excellent_dir
        elif result.found_bird:
            return self._processing_directories.standard_dir
        else:
            return self._processing_directories.no_birds_dir
    
    # ============ 报告生成 ============
    def _generate_final_report(self, directory_path: str, ui_settings: List[Any], 
                              stats: ProcessingStats) -> None:
        """生成最终处理报告"""
        self.file_manager.write_log("=" * 50, directory_path)
        self.file_manager.write_log(f"Process Completed, files processed: {stats.processed_files}", directory_path)
        self.file_manager.write_log(
            f"SETTINGS: Confidence: {ui_settings[0]} - "
            f"Area: {ui_settings[1]}% - Sharpness: {ui_settings[2]}",
            directory_path
        )
        self.file_manager.write_log(
            f"RESULTS: Excellent: {stats.excellent_count}, "
            f"Standard: {stats.standard_count}, No Birds: {stats.no_birds_count}, "
            f"Errors: {stats.error_count}",
            directory_path
        )
        
        # 清理任何遗留的临时JPG文件（异常情况处理）
        remaining_temp_files = len(self._temp_jpg_files)
        if remaining_temp_files > 0:
            cleaned_count = self._cleanup_remaining_temp_files(directory_path)
            # 静默清理，不记录日志
        
        self.file_manager.write_log("=" * 50, directory_path)
        
        # 最终进度报告
        final_progress = ProcessingProgress(
            current_file="",
            current_index=stats.total_files,
            total_files=stats.total_files,
            status=ProcessingStatus.COMPLETED,
            message="Processing completed",
            stats=stats
        )
        self._report_progress(final_progress)
    
    
    def _cleanup_temp_jpg_immediately(self, filename: str, directory_path: str) -> None:
        """
        立即清理单个临时JPG文件
        无论文件在哪个目录，只要是临时生成的就删除
        
        Args:
            filename: 要检查的文件名
            directory_path: 原始处理目录路径
        """
        if filename not in self._temp_jpg_files:
            return  # 不是临时文件，跳过
        
        try:
            # 可能的文件位置
            possible_locations = [
                directory_path,  # 原目录
            ]
            
            # 添加分类目录
            if self._processing_directories:
                possible_locations.extend([
                    self._processing_directories.excellent_dir,
                    self._processing_directories.standard_dir,
                    self._processing_directories.no_birds_dir
                ])
            
            # 查找并删除临时JPG文件
            deleted = False
            for location in possible_locations:
                file_path = os.path.join(location, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    deleted = True
                    break
            
            # 静默处理，不记录未找到的文件
            
            # 从跟踪列表中移除
            self._temp_jpg_files.pop(filename, None)
            
        except Exception as e:
            # 静默处理异常，避免日志冗余
            pass
    
    def _cleanup_remaining_temp_files(self, directory_path: str) -> int:
        """
        清理任何遗留的临时文件（最终清理，处理异常情况）
        
        Args:
            directory_path: 原始处理目录路径
            
        Returns:
            int: 清理的文件数量
        """
        cleaned_count = 0
        
        # 可能的文件位置
        possible_locations = [directory_path]
        if self._processing_directories:
            possible_locations.extend([
                self._processing_directories.excellent_dir,
                self._processing_directories.standard_dir,
                self._processing_directories.no_birds_dir
            ])
        
        for filename in list(self._temp_jpg_files.keys()):
            try:
                deleted = False
                for location in possible_locations:
                    file_path = os.path.join(location, filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        cleaned_count += 1
                        deleted = True
                        break
                
                # 从跟踪列表中移除
                self._temp_jpg_files.pop(filename, None)
                
            except Exception as e:
                # 静默处理清理失败，避免日志冗余
                pass
        
        return cleaned_count
    
    # ============ 重置操作 ============
    def reset_directory(self, directory_path: str) -> bool:
        """重置目录，将所有文件移回原位置"""
        try:
            return self.file_manager.reset_processing_directories(directory_path)
        except Exception as e:
            self.file_manager.write_log(f"ERROR in reset_directory: {e}", directory_path)
            return False
    
    # ============ 配置访问 ============
    def get_ui_scales(self) -> Dict[str, float]:
        """获取UI缩放配置"""
        return self.config_manager.get_ui_scales()
    
    def get_progress_bar_config(self) -> Dict[str, int]:
        """获取进度条配置"""
        return self.config_manager.get_progress_bar_config()
    
    def get_beep_count(self) -> int:
        """获取提示音配置"""
        return self.config_manager.get_beep_count()


# 全局图像处理服务实例
image_processing_service = ImageProcessingService()