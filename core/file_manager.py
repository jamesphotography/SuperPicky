"""
文件管理器 - 核心层
负责所有文件和目录相关的操作
"""
import os
import shutil
import csv
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass
from .config_manager import config_manager


@dataclass
class FileInfo:
    """文件信息数据类"""
    filename: str
    filepath: str
    file_prefix: str
    file_extension: str
    is_raw: bool
    is_jpg: bool


@dataclass
class ProcessingDirectories:
    """处理目录信息数据类"""
    base_dir: str
    excellent_dir: str
    standard_dir: str
    no_birds_dir: str
    crop_temp_dir: str
    log_file: str
    report_file: str


class FileManager:
    """文件管理器，处理所有文件和目录操作"""
    
    def __init__(self):
        self.config = config_manager
    
    # ============ 文件扫描和分析 ============
    def scan_directory(self, directory_path: str) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
        """
        扫描目录，分类RAW和JPG文件
        
        Returns:
            Tuple[raw_dict, jpg_dict, files_to_process]
        """
        if not os.path.exists(directory_path):
            raise FileNotFoundError(f"Directory not found: {directory_path}")
        
        raw_dict = {}  # {file_prefix: file_extension}
        jpg_dict = {}  # {file_prefix: file_extension}
        files_to_process = []
        
        for filename in os.listdir(directory_path):
            file_prefix, file_ext = os.path.splitext(filename)
            
            if self.config.is_raw_file(filename):
                raw_dict[file_prefix] = file_ext
            elif self.config.is_supported_image_file(filename):
                jpg_dict[file_prefix] = file_ext
                files_to_process.append(filename)
        
        return raw_dict, jpg_dict, files_to_process
    
    def get_file_info(self, directory_path: str, filename: str) -> FileInfo:
        """获取文件详细信息"""
        filepath = os.path.join(directory_path, filename)
        file_prefix, file_ext = os.path.splitext(filename)
        
        return FileInfo(
            filename=filename,
            filepath=filepath,
            file_prefix=file_prefix,
            file_extension=file_ext,
            is_raw=self.config.is_raw_file(filename),
            is_jpg=self.config.is_supported_image_file(filename)
        )
    
    # ============ 目录管理 ============
    def create_processing_directories(self, base_directory: str) -> ProcessingDirectories:
        """
        创建所有处理需要的目录
        
        Returns:
            ProcessingDirectories 包含所有目录路径
        """
        directories = ProcessingDirectories(
            base_dir=base_directory,
            excellent_dir=self._create_directory(base_directory, self.config.get_excellent_dir_name()),
            standard_dir=self._create_directory(base_directory, self.config.get_standard_dir_name()),
            no_birds_dir=self._create_directory(base_directory, self.config.get_no_birds_dir_name()),
            crop_temp_dir=self._create_directory(base_directory, self.config.get_crop_temp_dir_name()),
            log_file=os.path.join(base_directory, self.config.get_log_file_name()),
            report_file=os.path.join(base_directory, self.config.get_report_file_name())
        )
        
        return directories
    
    def _create_directory(self, parent_dir: str, dir_name: str) -> str:
        """创建目录，如果不存在的话"""
        dir_path = os.path.join(parent_dir, dir_name)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
        return dir_path
    
    # ============ 文件移动和复制 ============
    def move_file_group(self, file_prefix: str, source_dir: str, target_dir: str) -> bool:
        """
        移动同一前缀的所有文件（RAW + JPG）到目标目录
        
        Args:
            file_prefix: 文件前缀（不含扩展名）
            source_dir: 源目录
            target_dir: 目标目录
            
        Returns:
            bool: 是否成功移动所有文件
        """
        related_files = self._get_related_files(file_prefix, source_dir)
        
        if not related_files:
            return False
        
        success = True
        for filename in related_files:
            source_path = os.path.join(source_dir, filename)
            target_path = os.path.join(target_dir, filename)
            
            try:
                if os.path.exists(source_path):
                    if not os.access(source_path, os.W_OK):
                        success = False
                        continue
                    
                    shutil.move(source_path, target_path)
                else:
                    success = False
            except (PermissionError, Exception):
                success = False
        
        return success
    
    def _get_related_files(self, file_prefix: str, directory: str) -> List[str]:
        """获取指定前缀的所有相关文件"""
        related_files = []
        
        if not os.path.exists(directory):
            return related_files
        
        for filename in os.listdir(directory):
            name, _ = os.path.splitext(filename)
            if name == file_prefix:
                related_files.append(filename)
        
        return related_files
    
    # ============ 日志管理 ============
    def write_log(self, message: str, directory: str) -> None:
        """写入日志消息"""
        log_file_path = os.path.join(directory, self.config.get_log_file_name())
        
        try:
            with open(log_file_path, "a", encoding='utf-8') as log_file:
                log_file.write(message + "\n")
        except Exception as e:
            print(f"Failed to write log: {e}")
        
        # 同时输出到控制台（除了分隔线）
        if message != ("-" * 80):
            print(message)
    
    # ============ CSV 报告管理 ============
    def initialize_csv_report(self, directory: str) -> None:
        """初始化CSV报告文件，写入头部"""
        report_file_path = os.path.join(directory, self.config.get_report_file_name())
        
        try:
            with open(report_file_path, mode='w', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=self.config.get_csv_headers())
                writer.writeheader()
        except Exception as e:
            self.write_log(f"Failed to initialize CSV report: {e}", directory)
    
    def write_csv_row(self, data: Dict, directory: str) -> None:
        """写入CSV报告行数据"""
        report_file_path = os.path.join(directory, self.config.get_report_file_name())
        
        try:
            with open(report_file_path, mode='a', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=self.config.get_csv_headers())
                writer.writerow(data)
        except Exception as e:
            self.write_log(f"Failed to write CSV row: {e}", directory)
    
    # ============ 清理操作 ============
    def cleanup_directory(self, directory_path: str) -> bool:
        """清理指定目录中的文件"""
        if not os.path.exists(directory_path):
            return False
        
        try:
            for filename in os.listdir(directory_path):
                file_path = os.path.join(directory_path, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            return True
        except Exception:
            return False
    
    def remove_directory(self, directory_path: str) -> bool:
        """删除目录及其内容"""
        if not os.path.exists(directory_path):
            return True
        
        try:
            if os.path.isfile(directory_path):
                os.remove(directory_path)
            elif os.path.isdir(directory_path):
                shutil.rmtree(directory_path)
            return True
        except Exception:
            return False
    
    # ============ 重置操作 ============  
    def reset_processing_directories(self, base_directory: str) -> bool:
        """重置处理目录，将所有文件移回主目录并清理所有处理文件"""
        self.write_log("开始重置处理目录到原始状态", base_directory)
        success = True
        
        directories = self.config.get_directory_names()
        
        # 1. 将所有子目录中的文件移回主目录
        for dir_key, dir_name in directories.items():
            dir_path = os.path.join(base_directory, dir_name)
            if os.path.exists(dir_path):
                self.write_log(f"处理目录: {dir_name}", base_directory)
                try:
                    # 移回文件（强制覆盖）
                    self._move_files_back_to_parent_force(dir_path, base_directory)
                    # 删除目录
                    if self.remove_directory(dir_path):
                        self.write_log(f"已删除目录: {dir_name}", base_directory)
                    else:
                        self.write_log(f"警告: 无法完全删除目录 {dir_name}", base_directory)
                        success = False
                except Exception as e:
                    self.write_log(f"错误: 处理目录 {dir_name} 时出错: {e}", base_directory)
                    success = False
        
        # 2. 删除日志文件
        log_file_path = os.path.join(base_directory, self.config.get_log_file_name())
        if os.path.exists(log_file_path):
            try:
                os.remove(log_file_path)
                print("已删除日志文件")
            except Exception as e:
                print(f"警告: 无法删除日志文件: {e}")
                success = False
        
        # 3. 删除CSV报告文件
        report_file_path = os.path.join(base_directory, self.config.get_report_file_name())
        if os.path.exists(report_file_path):
            try:
                os.remove(report_file_path)
                print("已删除CSV报告文件")
            except Exception as e:
                print(f"警告: 无法删除CSV报告文件: {e}")
                success = False
        
        if success:
            print("重置完成：所有文件已恢复到原始位置，所有处理文件已删除")
        else:
            print("重置完成但有警告：某些文件或目录可能未完全清理")
            
        return success
    
    def _move_files_back_to_parent_force(self, child_dir: str, parent_dir: str) -> None:
        """将子目录中的文件强制移回父目录（包括裁剪图片等所有文件）"""
        if not os.path.exists(child_dir):
            return
        
        for filename in os.listdir(child_dir):
            child_path = os.path.join(child_dir, filename)
            parent_path = os.path.join(parent_dir, filename)
            
            if os.path.isfile(child_path):
                try:
                    # 如果是裁剪图片（Crop_开头），直接删除不移回
                    if filename.startswith('Crop_'):
                        os.remove(child_path)
                        print(f"已删除裁剪图片: {filename}")
                        continue
                    
                    # 对于其他文件，强制移回（覆盖已存在的文件）
                    if os.path.exists(parent_path):
                        os.remove(parent_path)  # 先删除已存在的文件
                    
                    shutil.move(child_path, parent_path)
                    print(f"已移回文件: {filename}")
                    
                except Exception as e:
                    print(f"警告: 无法处理文件 {filename}: {e}")
                    
            elif os.path.isdir(child_path):
                # 递归处理子目录
                new_parent = os.path.join(parent_dir, filename)
                if not os.path.exists(new_parent):
                    os.makedirs(new_parent)
                self._move_files_back_to_parent_force(child_path, new_parent)
    
    def _move_files_back_to_parent(self, child_dir: str, parent_dir: str) -> None:
        """将子目录中的文件移回父目录（保持向后兼容）"""
        if not os.path.exists(child_dir):
            return
        
        for filename in os.listdir(child_dir):
            child_path = os.path.join(child_dir, filename)
            parent_path = os.path.join(parent_dir, filename)
            
            if os.path.isfile(child_path):
                # 如果父目录已存在同名文件，跳过
                if not os.path.exists(parent_path):
                    shutil.move(child_path, parent_path)
            elif os.path.isdir(child_path):
                # 递归处理子目录
                new_parent = os.path.join(parent_dir, filename)
                if not os.path.exists(new_parent):
                    os.makedirs(new_parent)
                self._move_files_back_to_parent(child_path, new_parent)


# 全局文件管理器实例
file_manager = FileManager()