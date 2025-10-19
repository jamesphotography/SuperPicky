#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将report.csv中的NIMA和BRISQUE评分写入NEF照片的EXIF
- NIMA美学 -> IPTC:City (城市)
- BRISQUE技术 -> IPTC:Province-State (省/州)
"""

import pandas as pd
import os
import sys
from exiftool_manager import get_exiftool_manager
from tqdm import tqdm


def write_iqa_to_exif(report_csv_path, image_dir):
    """
    从report.csv读取NIMA和BRISQUE数据，写入NEF文件EXIF

    Args:
        report_csv_path: report.csv路径
        image_dir: NEF文件所在目录
    """
    print("=" * 80)
    print("📝 将IQA评分写入EXIF")
    print("=" * 80)

    # 检查文件
    if not os.path.exists(report_csv_path):
        print(f"❌ 报告文件不存在: {report_csv_path}")
        return

    if not os.path.exists(image_dir):
        print(f"❌ 图像目录不存在: {image_dir}")
        return

    # 读取CSV
    print(f"\n📂 读取报告: {report_csv_path}")
    df = pd.read_csv(report_csv_path)
    print(f"   总记录数: {len(df)}")

    # 过滤有鸟的照片
    df_birds = df[df['是否有鸟'] == '是'].copy()
    print(f"   有鸟照片: {len(df_birds)} 张")

    # 获取exiftool manager
    manager = get_exiftool_manager()

    # 统计
    success_count = 0
    failed_count = 0
    no_file_count = 0
    no_data_count = 0

    print(f"\n🔄 开始写入EXIF...")
    print(f"   NIMA美学 -> IPTC:Country-PrimaryLocationName (国家)")
    print(f"   BRISQUE技术 -> IPTC:Province-State (省/州)")
    print()

    for idx, row in tqdm(df_birds.iterrows(), total=len(df_birds), desc="处理照片"):
        filename = row['文件名']
        nima = row['NIMA美学']
        brisque = row['BRISQUE技术']

        # 检查数据有效性
        if pd.isna(nima) or pd.isna(brisque) or nima == '-' or brisque == '-':
            no_data_count += 1
            continue

        # 查找NEF文件（CSV中文件名没有扩展名，需要加上.NEF）
        image_path = os.path.join(image_dir, filename + '.NEF')
        if not os.path.exists(image_path):
            # 尝试小写.nef
            image_path = os.path.join(image_dir, filename + '.nef')
            if not os.path.exists(image_path):
                no_file_count += 1
                continue

        try:
            # 转换为float
            nima_val = float(nima)
            brisque_val = float(brisque)

            # 直接调用exiftool写入IPTC字段
            # NIMA -> IPTC:Country-PrimaryLocationName (国家)
            # BRISQUE -> IPTC:Province-State (省/州)
            import subprocess

            cmd = [
                manager.exiftool_path,
                f'-IPTC:Country-PrimaryLocationName={nima_val:05.2f}',
                f'-IPTC:Province-State={brisque_val:06.2f}',
                '-overwrite_original',
                image_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                success_count += 1
            else:
                failed_count += 1
                if failed_count <= 5:
                    print(f"\n   ⚠️  {filename} 写入失败: {result.stderr}")

        except Exception as e:
            failed_count += 1
            if failed_count <= 5:
                print(f"\n   ⚠️  {filename} 处理失败: {e}")

    print("\n" + "=" * 80)
    print("📊 统计结果")
    print("=" * 80)
    print(f"✅ 成功写入: {success_count} 张")
    print(f"⚠️  无有效数据: {no_data_count} 张")
    print(f"⚠️  文件不存在: {no_file_count} 张")
    print(f"❌ 写入失败: {failed_count} 张")
    print(f"📁 总计: {len(df_birds)} 张有鸟照片")
    print("=" * 80)

    if success_count > 0:
        print(f"\n✅ 完成！在Lightroom中可以查看:")
        print(f"   国家 (Country) = NIMA美学评分")
        print(f"   省/州 (Province-State) = BRISQUE技术评分")


if __name__ == "__main__":
    # 默认路径
    default_report = "/Volumes/990PRO4TB/2025/2025-10-17/_tmp/report.csv"
    default_image_dir = "/Volumes/990PRO4TB/2025/2025-10-17"

    if len(sys.argv) > 1:
        report_path = sys.argv[1]
    else:
        report_path = default_report

    if len(sys.argv) > 2:
        image_directory = sys.argv[2]
    else:
        image_directory = default_image_dir

    print(f"📂 报告路径: {report_path}")
    print(f"📂 图像目录: {image_directory}")
    print()

    write_iqa_to_exif(report_path, image_directory)
