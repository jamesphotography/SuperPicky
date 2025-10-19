#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行批处理脚本
"""

import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from find_bird_util import reset, raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds
from utils import write_to_csv
from exiftool_manager import get_exiftool_manager
from temp_file_manager import get_temp_manager
import time

def process_directory(dir_path, settings=None):
    """处理目录中的所有NEF文件"""

    # 默认设置
    if settings is None:
        settings = {
            'confidence_threshold': 0.25,
            'sharpness_threshold': 100,
            'area_threshold': 1.0,
            'quality_weight_sharpness': 0.4,
            'quality_weight_area': 0.3,
            'quality_weight_confidence': 0.3,
            'center_tolerance': 0.25,
        }

    print(f"\n{'='*80}")
    print(f"开始处理目录: {dir_path}")
    print(f"{'='*80}\n")

    # 创建临时目录
    work_dir = Path(dir_path) / "_tmp"
    work_dir.mkdir(exist_ok=True)

    # 获取所有NEF文件
    nef_files = list(Path(dir_path).glob("*.NEF"))
    nef_files.extend(list(Path(dir_path).glob("*.nef")))

    if not nef_files:
        print(f"错误: 在 {dir_path} 中没有找到NEF文件")
        return

    print(f"找到 {len(nef_files)} 个NEF文件\n")

    # 重置环境
    print("正在重置环境...")
    reset(str(work_dir))

    # 加载YOLO模型
    print("正在加载YOLO模型...")
    model = load_yolo_model()
    if model is None:
        print("错误: 无法加载YOLO模型")
        return

    # 获取ExifTool管理器
    exiftool_mgr = get_exiftool_manager()

    # 统计数据
    stats = {
        'total': len(nef_files),
        'processed': 0,
        'star_3': 0,
        'star_2': 0,
        'star_1': 0,
        'no_bird': 0,
        'start_time': time.time()
    }

    # 准备CSV
    csv_path = work_dir / "report.csv"
    headers = ['文件名', '是否有鸟', '置信度', 'X坐标', 'Y坐标', '鸟占比', '像素数',
               '原始锐度', '归一化锐度', 'NIMA美学', '星等', '面积达标', '居中', '锐度达标', '类别ID']

    # 处理每个文件
    for idx, nef_path in enumerate(nef_files, 1):
        try:
            filename = nef_path.stem
            print(f"[{idx}/{stats['total']}] 处理: {filename}")

            # 1. 转换NEF到JPG
            jpg_path = raw_to_jpeg(str(nef_path))
            if not jpg_path or not Path(jpg_path).exists():
                print(f"  ⚠️  转换失败，跳过")
                continue

            # 2. 检测鸟
            result = detect_and_draw_birds(
                image_path=jpg_path,
                model=model,
                confidence_threshold=settings['confidence_threshold'],
                draw_boxes=True,
                output_dir=str(work_dir),
                exiftool_manager=exiftool_mgr
            )

            if not result:
                print(f"  ❌ 未检测到鸟")
                stats['no_bird'] += 1
                stats['processed'] += 1

                # 写入CSV
                csv_row = [filename, '否', '', '', '', '', '', '', '', '', '❌', '否', '否', '否', '']
                write_to_csv(csv_path, csv_row, headers)
                continue

            # 3. 提取结果
            has_bird = result.get('has_bird', False)
            if not has_bird:
                print(f"  ❌ 未检测到鸟")
                stats['no_bird'] += 1
                stats['processed'] += 1
                csv_row = [filename, '否', '', '', '', '', '', '', '', '❌', '否', '否', '否', '']
                write_to_csv(csv_path, csv_row, headers)
                continue

            # 提取数据
            confidence = result.get('confidence', 0)
            x_center = result.get('x_center', 0)
            y_center = result.get('y_center', 0)
            bird_ratio = result.get('bird_ratio', 0)
            bird_pixels = result.get('bird_pixels', 0)
            raw_sharpness = result.get('raw_sharpness', 0)
            norm_sharpness = result.get('normalized_sharpness', 0)
            nima_score = result.get('nima_score', 0)
            class_id = result.get('class_id', '')

            # 评判标准
            meets_area = bird_ratio >= settings['area_threshold']
            is_centered = abs(x_center - 0.5) <= settings['center_tolerance'] and \
                         abs(y_center - 0.5) <= settings['center_tolerance']
            meets_sharpness = norm_sharpness >= settings['sharpness_threshold']

            # 评星
            passed_count = sum([meets_area, is_centered, meets_sharpness])
            if passed_count >= 3:
                star_rating = '⭐⭐⭐'
                stats['star_3'] += 1
            elif passed_count >= 2:
                star_rating = '⭐⭐'
                stats['star_2'] += 1
            else:
                star_rating = '⭐'
                stats['star_1'] += 1

            stats['processed'] += 1

            # 显示结果
            print(f"  ✓ {star_rating} | 置信度:{confidence:.2f} | 鸟占比:{bird_ratio:.2f}% | 锐度:{norm_sharpness:.0f} | NIMA:{nima_score:.1f}")

            # 写入CSV
            csv_row = [
                filename, '是', f'{confidence:.2f}', f'{x_center:.2f}', f'{y_center:.2f}',
                f'{bird_ratio:.2f}%', bird_pixels, f'{raw_sharpness:.2f}', f'{norm_sharpness:.2f}',
                f'{nima_score:.2f}', star_rating, '是' if meets_area else '否',
                '是' if is_centered else '否', '是' if meets_sharpness else '否', class_id
            ]
            write_to_csv(csv_path, csv_row, headers)

        except Exception as e:
            print(f"  ⚠️  处理出错: {str(e)}")
            continue

    # 处理完成
    stats['end_time'] = time.time()
    stats['total_time'] = stats['end_time'] - stats['start_time']

    print(f"\n{'='*80}")
    print("处理完成!")
    print(f"{'='*80}")
    print(f"总文件数: {stats['total']}")
    print(f"已处理: {stats['processed']}")
    print(f"⭐⭐⭐ 三星: {stats['star_3']} ({stats['star_3']/stats['processed']*100:.1f}%)" if stats['processed'] > 0 else "")
    print(f"⭐⭐  二星: {stats['star_2']} ({stats['star_2']/stats['processed']*100:.1f}%)" if stats['processed'] > 0 else "")
    print(f"⭐   一星: {stats['star_1']} ({stats['star_1']/stats['processed']*100:.1f}%)" if stats['processed'] > 0 else "")
    print(f"❌   无鸟: {stats['no_bird']} ({stats['no_bird']/stats['processed']*100:.1f}%)" if stats['processed'] > 0 else "")
    print(f"总耗时: {stats['total_time']:.1f}秒")
    print(f"平均每张: {stats['total_time']/stats['processed']*1000:.0f}毫秒" if stats['processed'] > 0 else "")
    print(f"\nCSV报告: {csv_path}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python run_batch.py <目录路径>")
        sys.exit(1)

    dir_path = sys.argv[1]
    if not os.path.isdir(dir_path):
        print(f"错误: {dir_path} 不是一个有效的目录")
        sys.exit(1)

    process_directory(dir_path)
