#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NIMA 美学评分使用示例
展示如何在其他脚本中使用 IQA 评分功能
"""

from iqa_scorer import IQAScorer, score_image, get_iqa_scorer
import os

def example_1_basic_usage():
    """示例1: 基础使用 - 对单张图片评分"""
    print("=" * 60)
    print("示例1: 基础使用")
    print("=" * 60)

    test_image = "img/_Z9W0960.jpg"

    if os.path.exists(test_image):
        # 方法1: 使用便捷函数
        score = score_image(test_image, metric='nima')
        print(f"NIMA 评分: {score:.2f}/100")

        # 方法2: 创建评分器实例
        scorer = IQAScorer(metric_name='nima')
        score2 = scorer.score_image(test_image)
        print(f"NIMA 评分 (方法2): {score2:.2f}/100")
    else:
        print(f"测试图片不存在: {test_image}")


def example_2_crop_region():
    """示例2: 对裁剪区域评分"""
    print("\n" + "=" * 60)
    print("示例2: 对裁剪区域评分")
    print("=" * 60)

    test_image = "img/_Z9W0960.jpg"

    if os.path.exists(test_image):
        from PIL import Image

        # 获取图片尺寸
        img = Image.open(test_image)
        w, h = img.size

        # 定义裁剪区域 (中心 50%)
        x = w // 4
        y = h // 4
        crop_w = w // 2
        crop_h = h // 2

        scorer = get_iqa_scorer()

        # 对整张图片评分
        full_score = scorer.score_image(test_image)
        print(f"完整图片评分: {full_score:.2f}/100")

        # 对裁剪区域评分
        crop_score = scorer.score_image(test_image, crop_region=(x, y, crop_w, crop_h))
        print(f"中心区域评分: {crop_score:.2f}/100")

        # 计算差异
        diff = abs(crop_score - full_score)
        print(f"评分差异: {diff:.2f}")
    else:
        print(f"测试图片不存在: {test_image}")


def example_3_batch_scoring():
    """示例3: 批量评分"""
    print("\n" + "=" * 60)
    print("示例3: 批量评分")
    print("=" * 60)

    # 查找 img 目录下的所有图片
    img_dir = "img"
    if os.path.exists(img_dir):
        images = [os.path.join(img_dir, f) for f in os.listdir(img_dir)
                  if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

        if images:
            scorer = get_iqa_scorer()

            print(f"找到 {len(images)} 张图片")

            scores = []
            for img_path in images:
                try:
                    score = scorer.score_image(img_path)
                    scores.append((os.path.basename(img_path), score))
                    print(f"  {os.path.basename(img_path):30s} -> {score:.2f}/100")
                except Exception as e:
                    print(f"  {os.path.basename(img_path):30s} -> 失败: {e}")

            # 按评分排序
            scores.sort(key=lambda x: x[1], reverse=True)

            print(f"\n最高评分:")
            for filename, score in scores[:3]:
                print(f"  {filename}: {score:.2f}/100")
        else:
            print("未找到图片文件")
    else:
        print(f"目录不存在: {img_dir}")


def example_4_multiple_metrics():
    """示例4: 使用多种评分指标"""
    print("\n" + "=" * 60)
    print("示例4: 多种评分指标对比")
    print("=" * 60)

    test_image = "img/_Z9W0960.jpg"

    if os.path.exists(test_image):
        # 支持的评分指标
        metrics = ['nima', 'brisque', 'niqe']

        print(f"测试图片: {test_image}")
        print(f"\n评分对比:")

        for metric in metrics:
            try:
                scorer = IQAScorer(metric_name=metric)
                score = scorer.score_image(test_image)
                print(f"  {metric.upper():15s}: {score:.2f}/100")
            except Exception as e:
                print(f"  {metric.upper():15s}: 失败 ({e})")
    else:
        print(f"测试图片不存在: {test_image}")


if __name__ == '__main__':
    print("\n" + "🎨 NIMA 美学评分使用示例\n")

    # 运行示例
    try:
        example_1_basic_usage()
        example_2_crop_region()
        example_3_batch_scoring()
        example_4_multiple_metrics()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()

    print("\n✅ 示例运行完成!\n")
