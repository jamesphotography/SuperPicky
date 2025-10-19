"""
工具函数模块
提供日志记录和CSV报告功能
"""
import os
import csv
from datetime import datetime


def log_message(message: str, directory: str = None):
    """
    记录日志消息到控制台和日志文件

    Args:
        message: 日志消息
        directory: 工作目录（可选，如果提供则写入该目录/_tmp/process_log.txt）
    """
    # 打印到控制台
    print(message)

    # 如果提供了目录，写入日志文件到_tmp子目录
    if directory:
        # 确保_tmp目录存在
        tmp_dir = os.path.join(directory, "_tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        log_file = os.path.join(tmp_dir, "process_log.txt")
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            print(f"Warning: Could not write to log file: {e}")


def write_to_csv(data: dict, directory: str, header: bool = False):
    """
    将数据写入CSV报告文件

    Args:
        data: 要写入的数据字典（如果为None且header=True，则只创建文件并写表头）
        directory: 工作目录
        header: 是否写入表头（第一次写入时为True）
    """
    # 确保_tmp目录存在
    tmp_dir = os.path.join(directory, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    report_file = os.path.join(tmp_dir, "report.csv")

    # 定义CSV列顺序
    fieldnames = [
        "文件名", "是否有鸟", "置信度", "X坐标", "Y坐标",
        "鸟占比", "像素数", "原始锐度", "归一化锐度", "NIMA美学", "BRISQUE技术", "星等", "评分",
        "面积达标", "居中", "锐度达标", "类别ID"
    ]

    try:
        # 如果是初始化表头（data为None）
        if data is None and header:
            with open(report_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            return

        file_exists = os.path.exists(report_file)
        mode = 'a' if file_exists else 'w'

        with open(report_file, mode, newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)

            # 如果文件不存在或者明确要求写表头，则写入表头
            if not file_exists or header:
                writer.writeheader()

            if data:
                writer.writerow(data)
    except Exception as e:
        log_message(f"Warning: Could not write to CSV file: {e}", directory)
