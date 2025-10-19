#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为现有report.csv添加NIQE评分
使用现有crop图像，移除绿色掩码后计算NIQE
"""

import pandas as pd
import os
import sys
from pathlib import Path
import pyiqa
import torch
from PIL import Image
import numpy as np
from tqdm import tqdm
import cv2

def remove_green_mask(img_bgr):
    """
    移除crop图像上的绿色半透明掩码
    掩码是通过 cv2.addWeighted 添加的半透明绿色(0, 255, 0)

    Args:
        img_bgr: BGR格式的图像

    Returns:
        移除掩码后的BGR图像
    """
    # 检测绿色像素：G通道明显高于R和B
    # 掩码特征：G > R + threshold 且 G > B + threshold
    img_float = img_bgr.astype(np.float32)
    b, g, r = cv2.split(img_float)

    # 找到绿色掩码区域（G明显大于R和B）
    threshold = 30
    green_mask = (g > r + threshold) & (g > b + threshold)

    # 对于绿色掩码区域，估算原始颜色
    # 由于掩码是用addWeighted(img, 1.0, green, 0.4, 0)添加的
    # 所以 result = img * 1.0 + green * 0.4
    # 我们可以反推: img ≈ (result - green * 0.4) / 1.0
    result = img_bgr.copy()

    if np.any(green_mask):
        # 估算绿色掩码的贡献并移除
        # green_contribution = (0, 255, 0) * 0.4 = (0, 102, 0)
        result[green_mask, 1] = np.clip(
            result[green_mask, 1] - 102,  # 移除绿色通道的掩码贡献
            0, 255
        )

    return result


def calculate_niqe_for_report(report_csv_path, crop_dir):
    """
    为report.csv中的每张照片计算NIQE评分
    使用现有crop图像，移除绿色掩码后计算

    Args:
        report_csv_path: report.csv路径
        crop_dir: crop图像目录
    """
    print("=" * 80)
    print("🔬 NIQE评分计算")
    print("=" * 80)

    # 检查文件
    if not os.path.exists(report_csv_path):
        print(f"❌ 报告文件不存在: {report_csv_path}")
        return

    # 读取CSV
    print(f"📂 读取报告: {report_csv_path}")
    df = pd.read_csv(report_csv_path)
    print(f"   总记录数: {len(df)}")

    # 检查是否已有NIQE列
    if 'NIQE技术' in df.columns:
        print("⚠️  报告中已存在NIQE列，将覆盖")
        df = df.drop(columns=['NIQE技术'])

    # 初始化NIQE模型
    print("\n🤖 初始化NIQE模型...")
    # NIQE需要float64，MPS不支持，强制使用CPU
    device = torch.device('cpu')
    print(f"   设备: {device} (NIQE需要float64，MPS不支持)")

    niqe_model = pyiqa.create_metric('niqe', device=device, as_loss=False)
    print("   ✅ NIQE模型加载完成")

    # 为每张照片计算NIQE
    print(f"\n📊 计算NIQE评分 (移除掩码后的crop图像)...")
    print(f"   Crop目录: {crop_dir}")

    niqe_scores = []
    success_count = 0
    failed_count = 0
    no_bird_count = 0
    no_crop_count = 0

    for idx, row in tqdm(df.iterrows(), total=len(df), desc="处理照片"):
        filename = row['文件名']
        has_bird = row['是否有鸟']

        # 只对有鸟的照片计算NIQE
        if has_bird != '是':
            niqe_scores.append('-')
            no_bird_count += 1
            continue

        # 查找crop图像
        crop_path = os.path.join(crop_dir, f"Crop_{filename}.jpg")
        if not os.path.exists(crop_path):
            niqe_scores.append('-')
            no_crop_count += 1
            continue

        # 计算NIQE
        try:
            # 加载crop图像(BGR格式)
            crop_img_bgr = cv2.imread(crop_path)
            if crop_img_bgr is None:
                niqe_scores.append('-')
                failed_count += 1
                continue

            # 移除绿色掩码
            crop_clean = remove_green_mask(crop_img_bgr)

            # 转换为RGB
            crop_rgb = cv2.cvtColor(crop_clean, cv2.COLOR_BGR2RGB)

            # 转换为PIL Image（pyiqa需要）
            crop_pil = Image.fromarray(crop_rgb)

            # 计算NIQE
            with torch.no_grad():
                score = niqe_model(crop_pil)

            # 转换为Python float
            if isinstance(score, torch.Tensor):
                score = score.item()

            # NIQE分数：越低越好（无固定范围，通常0-100）
            score = float(score)
            niqe_scores.append(f"{score:.2f}")
            success_count += 1

        except Exception as e:
            niqe_scores.append('-')
            failed_count += 1
            if failed_count <= 5:  # 只打印前5个错误
                print(f"\n   ⚠️  {filename} 计算失败: {e}")

    # 添加NIQE列到DataFrame
    df['NIQE技术'] = niqe_scores

    # 重新排列列顺序（NIQE放在BRISQUE后面）
    if 'BRISQUE技术' in df.columns:
        cols = df.columns.tolist()
        # 找到BRISQUE的位置
        brisque_idx = cols.index('BRISQUE技术')
        # 移除NIQE（在末尾）
        cols.remove('NIQE技术')
        # 插入到BRISQUE后面
        cols.insert(brisque_idx + 1, 'NIQE技术')
        df = df[cols]

    # 保存更新后的CSV
    df.to_csv(report_csv_path, index=False, encoding='utf-8-sig')

    print("\n" + "=" * 80)
    print("📊 统计结果")
    print("=" * 80)
    print(f"✅ 成功计算: {success_count} 张")
    print(f"⚠️  无鸟照片: {no_bird_count} 张")
    print(f"⚠️  无crop图像: {no_crop_count} 张")
    print(f"❌ 计算失败: {failed_count} 张")
    print(f"📁 总计: {len(df)} 条记录")

    # 显示NIQE统计
    df_valid = df[df['NIQE技术'] != '-'].copy()
    if len(df_valid) > 0:
        df_valid['NIQE_数值'] = pd.to_numeric(df_valid['NIQE技术'], errors='coerce')
        print("\n📈 NIQE分数统计（越低越好）:")
        print(f"   平均值: {df_valid['NIQE_数值'].mean():.2f}")
        print(f"   中位数: {df_valid['NIQE_数值'].median():.2f}")
        print(f"   标准差: {df_valid['NIQE_数值'].std():.2f}")
        print(f"   最小值: {df_valid['NIQE_数值'].min():.2f} (最好)")
        print(f"   最大值: {df_valid['NIQE_数值'].max():.2f} (最差)")

    print(f"\n✅ 报告已更新: {report_csv_path}")
    print("=" * 80)


if __name__ == "__main__":
    # 默认路径
    default_report = "/Volumes/990PRO4TB/2025/2025-10-17/_tmp/report.csv"
    default_crop_dir = "/Volumes/990PRO4TB/2025/2025-10-17/_tmp"

    if len(sys.argv) > 1:
        report_path = sys.argv[1]
    else:
        report_path = default_report

    if len(sys.argv) > 2:
        crop_directory = sys.argv[2]
    else:
        crop_directory = default_crop_dir

    print(f"📂 报告路径: {report_path}")
    print(f"📂 Crop目录: {crop_directory}")
    print()

    if not os.path.exists(crop_directory):
        print(f"❌ Crop目录不存在: {crop_directory}")
        sys.exit(1)

    calculate_niqe_for_report(report_path, crop_directory)
