"""
算法对比服务
提供新旧算法的并行对比测试功能
"""

import os
import shutil
import csv
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

from core.config_manager import config_manager
from core.file_manager import file_manager
from core.bird_detector import bird_detector, DetectionResult, ProcessingThresholds
from config import config


class AlgorithmChoice(Enum):
    """算法选择枚举"""
    OLD_ONLY = "old_only"
    NEW_ONLY = "new_only" 
    BOTH = "both"
    NEITHER = "neither"


@dataclass
class ComparisonResult:
    """对比结果数据类"""
    filename: str
    detection_result: DetectionResult
    old_algorithm_selected: bool
    new_algorithm_selected: bool
    algorithm_choice: AlgorithmChoice
    area_category: str  # "小面积", "中面积", "大面积"
    
    
@dataclass
class ComparisonStats:
    """对比统计信息"""
    total_processed: int
    birds_found: int
    old_selected: int
    new_selected: int
    both_selected: int
    old_only: int
    new_only: int
    neither: int
    area_stats: Dict[str, Dict[str, int]]  # 面积分类统计


class AlgorithmComparisonService:
    """算法对比服务"""
    
    def __init__(self):
        self.config_manager = config_manager
        self.file_manager = file_manager
        self.bird_detector = bird_detector
        
    def compare_algorithms_in_directory(self, directory_path: str, ui_settings: List[Any], 
                                       use_2pct_area: bool = True) -> bool:
        """
        在指定目录中运行算法对比测试
        
        Args:
            directory_path: 要处理的目录路径
            ui_settings: UI设置 [confidence, area_percent, sharpness]
            use_2pct_area: 是否使用2%面积阈值
            
        Returns:
            bool: 处理是否成功完成
        """
        try:
            print("🔬 启动算法对比测试")
            print("="*60)
            
            # 调整面积阈值
            if use_2pct_area:
                ui_settings = [ui_settings[0], 2, ui_settings[2]]
                print(f"📐 使用2%面积阈值")
            
            print(f"🔧 处理参数: 置信度{ui_settings[0]}%, 面积{ui_settings[1]}%, 锐度{ui_settings[2]}")
            
            # 1. 验证输入和初始化
            if not os.path.exists(directory_path):
                print(f"❌ 目录不存在: {directory_path}")
                return False
            
            # 2. 创建对比测试目录结构
            comparison_dirs = self._create_comparison_directories(directory_path)
            thresholds = self._create_processing_thresholds(ui_settings)
            
            # 3. 扫描和准备文件
            raw_dict, jpg_dict, files_to_process = self.file_manager.scan_directory(directory_path)
            
            # 4. 处理所有图像并进行对比分析
            comparison_results = self._process_and_compare_images(
                directory_path, files_to_process, thresholds, comparison_dirs
            )
            
            # 5. 生成对比报告
            self._generate_comparison_report(directory_path, ui_settings, comparison_results)
            
            # 6. 显示统计结果
            self._display_comparison_summary(comparison_results)
            
            print("\n✅ 算法对比测试完成!")
            return True
            
        except Exception as e:
            self.file_manager.write_log(f"ERROR in compare_algorithms_in_directory: {e}", directory_path)
            print(f"❌ 对比测试失败: {e}")
            return False
    
    def _create_comparison_directories(self, base_path: str) -> Dict[str, str]:
        """创建对比测试目录结构"""
        dirs = {
            'old_excellent': os.path.join(base_path, config.directory.OLD_ALGORITHM_EXCELLENT),
            'new_excellent': os.path.join(base_path, config.directory.NEW_ALGORITHM_EXCELLENT),
            'both_excellent': os.path.join(base_path, config.directory.BOTH_ALGORITHMS_EXCELLENT),
            'algorithm_diff': os.path.join(base_path, config.directory.ALGORITHM_DIFF_DIR),
            'standard': os.path.join(base_path, config.directory.STANDARD_DIR),
            'no_birds': os.path.join(base_path, config.directory.NO_BIRDS_DIR),
            'crop_temp': os.path.join(base_path, config.directory.CROP_TEMP_DIR),
            'redbox': os.path.join(base_path, config.directory.REDBOX_DIR)
        }
        
        for dir_path in dirs.values():
            os.makedirs(dir_path, exist_ok=True)
        
        print(f"📁 创建对比目录结构:")
        print(f"   • {config.directory.OLD_ALGORITHM_EXCELLENT}")
        print(f"   • {config.directory.NEW_ALGORITHM_EXCELLENT}")
        print(f"   • {config.directory.BOTH_ALGORITHMS_EXCELLENT}")
        print(f"   • {config.directory.ALGORITHM_DIFF_DIR}")
        
        return dirs
    
    def _create_processing_thresholds(self, ui_settings: List[Any]) -> ProcessingThresholds:
        """创建处理阈值"""
        thresholds_dict = self.config_manager.get_processing_thresholds(ui_settings)
        
        return ProcessingThresholds(
            ai_confidence=thresholds_dict['ai_confidence'],
            area_threshold=thresholds_dict['area_threshold'],
            sharpness_threshold=thresholds_dict['sharpness_threshold'],
            center_threshold=self.config_manager.get_center_threshold()
        )
    
    def _process_and_compare_images(self, directory_path: str, files_to_process: List[str],
                                   thresholds: ProcessingThresholds, 
                                   comparison_dirs: Dict[str, str]) -> List[ComparisonResult]:
        """处理所有图像并进行对比分析"""
        comparison_results = []
        total_files = len(files_to_process)
        
        print(f"\n🔄 开始处理 {total_files} 张图片...")
        
        for index, filename in enumerate(files_to_process):
            print(f"\n📷 处理 {index + 1}/{total_files}: {filename}")
            
            # 处理单个文件
            result = self._process_single_image_comparison(
                directory_path, filename, thresholds, comparison_dirs
            )
            
            if result:
                comparison_results.append(result)
                
                # 显示处理结果
                choice_icon = {
                    AlgorithmChoice.BOTH: "✅✅",
                    AlgorithmChoice.OLD_ONLY: "🟡❌", 
                    AlgorithmChoice.NEW_ONLY: "❌🟡",
                    AlgorithmChoice.NEITHER: "❌❌"
                }
                
                print(f"   结果: {choice_icon[result.algorithm_choice]} "
                      f"老算法={'选择' if result.old_algorithm_selected else '不选'} | "
                      f"新算法={'选择' if result.new_algorithm_selected else '不选'}")
                      
                if result.detection_result.found_bird:
                    print(f"   详情: AI={result.detection_result.confidence:.3f}, "
                          f"面积={result.detection_result.bird_area_ratio*100:.2f}%, "
                          f"综合评分={result.detection_result.composite_score:.4f}")
        
        return comparison_results
    
    def _process_single_image_comparison(self, directory_path: str, filename: str,
                                       thresholds: ProcessingThresholds,
                                       comparison_dirs: Dict[str, str]) -> Optional[ComparisonResult]:
        """处理单个图像的对比分析"""
        self.file_manager.write_log("-" * 80, directory_path)
        self.file_manager.write_log(f"COMPARISON: Processing {filename}", directory_path)
        
        filepath = os.path.join(directory_path, filename)
        
        if not os.path.exists(filepath):
            self.file_manager.write_log(f"ERROR: File not found {filename}", directory_path)
            return None
        
        # 使用鸟类检测器处理图像
        detection_result = self.bird_detector.detect_birds_in_image(
            filepath, thresholds, comparison_dirs['crop_temp']
        )
        
        if detection_result is None:
            self.file_manager.write_log(f"ERROR: Failed to process {filename}", directory_path)
            return None
        
        # 分析对比结果
        old_selected = detection_result.bird_selected  # 原算法结果
        new_selected = detection_result.result_new     # 新算法结果
        
        # 确定算法选择类型
        if old_selected and new_selected:
            choice = AlgorithmChoice.BOTH
        elif old_selected and not new_selected:
            choice = AlgorithmChoice.OLD_ONLY
        elif not old_selected and new_selected:
            choice = AlgorithmChoice.NEW_ONLY
        else:
            choice = AlgorithmChoice.NEITHER
        
        # 面积分类
        if detection_result.found_bird:
            area_pct = detection_result.bird_area_ratio * 100
            if area_pct < 3:
                area_category = "小面积"
            elif area_pct < 6:
                area_category = "中面积"
            else:
                area_category = "大面积"
        else:
            area_category = "无鸟"
        
        # 移动文件到相应目录
        self._move_file_to_comparison_directory(
            filepath, filename, choice, detection_result.found_bird, comparison_dirs
        )
        
        return ComparisonResult(
            filename=filename,
            detection_result=detection_result,
            old_algorithm_selected=old_selected,
            new_algorithm_selected=new_selected,
            algorithm_choice=choice,
            area_category=area_category
        )
    
    def _move_file_to_comparison_directory(self, filepath: str, filename: str, 
                                         choice: AlgorithmChoice, found_bird: bool,
                                         comparison_dirs: Dict[str, str]) -> bool:
        """移动文件到相应的对比目录"""
        try:
            file_prefix = os.path.splitext(filename)[0]
            source_dir = os.path.dirname(filepath)
            
            # 确定目标目录
            if not found_bird:
                target_dir = comparison_dirs['no_birds']
            elif choice == AlgorithmChoice.BOTH:
                target_dir = comparison_dirs['both_excellent']
            elif choice == AlgorithmChoice.OLD_ONLY:
                target_dir = comparison_dirs['old_excellent']
            elif choice == AlgorithmChoice.NEW_ONLY:
                target_dir = comparison_dirs['new_excellent']
            else:  # NEITHER
                target_dir = comparison_dirs['standard']
            
            # 移动文件组（RAW + JPG）
            return self.file_manager.move_file_group(file_prefix, source_dir, target_dir)
            
        except Exception as e:
            self.file_manager.write_log(f"ERROR moving file {filename}: {e}", source_dir)
            return False
    
    def _generate_comparison_report(self, directory_path: str, ui_settings: List[Any],
                                  comparison_results: List[ComparisonResult]) -> None:
        """生成对比分析报告"""
        csv_file = os.path.join(directory_path, config.directory.COMPARISON_REPORT_FILE)
        
        # 扩展的CSV头部
        extended_headers = config.csv.HEADERS + [
            'old_algorithm_selected', 'new_algorithm_selected', 
            'algorithm_choice', 'area_category'
        ]
        
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=extended_headers)
            writer.writeheader()
            
            for result in comparison_results:
                # 获取基础CSV数据
                csv_data = self.bird_detector.detection_result_to_csv_data(
                    result.detection_result, result.filename
                )
                
                # 添加对比字段
                csv_data.update({
                    'old_algorithm_selected': result.old_algorithm_selected,
                    'new_algorithm_selected': result.new_algorithm_selected,
                    'algorithm_choice': result.algorithm_choice.value,
                    'area_category': result.area_category
                })
                
                writer.writerow(csv_data)
        
        print(f"\n📄 对比报告已生成: {csv_file}")
    
    def _display_comparison_summary(self, comparison_results: List[ComparisonResult]) -> None:
        """显示对比测试总结"""
        if not comparison_results:
            print("❌ 没有处理任何文件")
            return
        
        # 计算统计数据
        stats = ComparisonStats(
            total_processed=len(comparison_results),
            birds_found=sum(1 for r in comparison_results if r.detection_result.found_bird),
            old_selected=sum(1 for r in comparison_results if r.old_algorithm_selected),
            new_selected=sum(1 for r in comparison_results if r.new_algorithm_selected),
            both_selected=sum(1 for r in comparison_results if r.algorithm_choice == AlgorithmChoice.BOTH),
            old_only=sum(1 for r in comparison_results if r.algorithm_choice == AlgorithmChoice.OLD_ONLY),
            new_only=sum(1 for r in comparison_results if r.algorithm_choice == AlgorithmChoice.NEW_ONLY),
            neither=sum(1 for r in comparison_results if r.algorithm_choice == AlgorithmChoice.NEITHER),
            area_stats={}
        )
        
        # 面积分类统计
        for category in ["小面积", "中面积", "大面积", "无鸟"]:
            category_results = [r for r in comparison_results if r.area_category == category]
            stats.area_stats[category] = {
                'total': len(category_results),
                'old_selected': sum(1 for r in category_results if r.old_algorithm_selected),
                'new_selected': sum(1 for r in category_results if r.new_algorithm_selected),
                'both': sum(1 for r in category_results if r.algorithm_choice == AlgorithmChoice.BOTH)
            }
        
        # 显示统计结果
        print(f"\n📊 算法对比测试总结")
        print("="*60)
        print(f"总处理文件: {stats.total_processed}")
        print(f"检测到鸟类: {stats.birds_found}")
        print(f"")
        print(f"算法选择对比:")
        print(f"  老算法选择: {stats.old_selected} ({stats.old_selected/stats.birds_found*100:.1f}%)")
        print(f"  新算法选择: {stats.new_selected} ({stats.new_selected/stats.birds_found*100:.1f}%)")
        print(f"")
        print(f"选择类型分布:")
        print(f"  两算法都选: {stats.both_selected}")
        print(f"  仅老算法选: {stats.old_only}")
        print(f"  仅新算法选: {stats.new_only}")
        print(f"  两算法都不选: {stats.neither}")
        
        print(f"\n📊 面积分类统计:")
        for category, data in stats.area_stats.items():
            if data['total'] > 0:
                print(f"  {category}: 总数{data['total']}, "
                      f"老算法{data['old_selected']}, "
                      f"新算法{data['new_selected']}, "
                      f"都选择{data['both']}")
        
        if stats.old_only > 0 or stats.new_only > 0:
            print(f"\n⚠️  算法差异: {stats.old_only + stats.new_only} 张图片的结果不一致")
            print(f"   这些图片已分别放入 '{config.directory.OLD_ALGORITHM_EXCELLENT}' "
                  f"和 '{config.directory.NEW_ALGORITHM_EXCELLENT}' 目录")


# 全局算法对比服务实例
algorithm_comparison_service = AlgorithmComparisonService()