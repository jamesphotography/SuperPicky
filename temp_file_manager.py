"""
临时文件管理模块
管理所有临时文件存储在照片目录的 _tmp 子目录下
"""
import os
import shutil
from pathlib import Path


class TempFileManager:
    """临时文件管理器"""

    def __init__(self):
        """初始化临时文件管理器"""
        pass

    def get_work_dir(self, source_dir: str) -> Path:
        """
        根据源目录获取工作目录（在源目录下创建 _tmp 子目录）

        Args:
            source_dir: 源照片目录路径

        Returns:
            工作目录路径（源目录/_tmp）
        """
        # 在源目录下创建 _tmp 子目录
        work_dir = Path(source_dir) / "_tmp"
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def get_jpg_path(self, work_dir: Path, filename: str) -> Path:
        """获取临时JPG文件路径"""
        return work_dir / filename

    def get_crop_path(self, work_dir: Path, filename: str) -> Path:
        """获取Crop文件路径"""
        base_name = Path(filename).stem
        return work_dir / f"Crop_{base_name}.jpg"

    def get_report_path(self, work_dir: Path) -> Path:
        """获取报告CSV文件路径"""
        return work_dir / "report.csv"

    def get_log_path(self, work_dir: Path) -> Path:
        """获取日志文件路径"""
        return work_dir / "process_log.txt"

    def clear_work_dir(self, work_dir: Path):
        """清空工作目录（保留目录本身）"""
        if work_dir.exists():
            for item in work_dir.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except Exception as e:
                    print(f"删除失败 {item}: {e}")


# 全局单例
_manager = None

def get_temp_manager() -> TempFileManager:
    """获取临时文件管理器单例"""
    global _manager
    if _manager is None:
        _manager = TempFileManager()
    return _manager
