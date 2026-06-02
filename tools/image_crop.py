#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 2 — 图像裁剪工具

提供 smart_square_crop：在 bbox 周围按 padding 比例扩展，输出方形 numpy 帧，
不足部分用黑色填充。鸟类识别模型通常需要方形输入，此函数保证输出无变形。

设计参考：SuperVideo 项目 detector.py 的 _smart_square_crop（基于 PIL）
本实现完全基于 numpy + cv2，无 PIL 依赖。

Image cropping utility module.
Provides smart_square_crop: pad bbox to square, fill borders with black.
Bird-ID models usually need square input; this guarantees aspect-ratio-preserving output.
"""

from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np


def smart_square_crop(
    image: np.ndarray,
    bbox: Tuple[int, int, int, int],
    padding_ratio: float = 0.15,
    fill_color: Tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """
    以 bbox 为中心，按 padding 比例扩展并裁剪为方形 numpy 帧

    算法：
        1. 计算 bbox 的最长边
        2. 目标边长 = 最长边 × (1 + padding_ratio)
        3. 以 bbox 中心为基准，在原图上裁出 target_side × target_side
        4. 若裁剪区域超出原图边界，先裁出可用部分再填充至方形

    参数:
        image (np.ndarray): 原图 BGR (HxWx3 uint8)
        bbox (Tuple[int,int,int,int]): 鸟类 bbox (x1, y1, x2, y2)
        padding_ratio (float): 边缘 padding 比例，默认 0.15（即多扩 15%）
        fill_color (Tuple[int,int,int]): 填充色 BGR，默认黑色

    返回:
        np.ndarray: 方形 BGR 帧 (target_side x target_side x 3 uint8)

    异常:
        ValueError: bbox 无效（x1>=x2 或 y1>=y2 等）

    Crop image into a square centered on bbox, with padding and border fill.

    Parameters:
        image: source BGR frame
        bbox: bird bounding box (x1, y1, x2, y2)
        padding_ratio: extra padding around bbox, default 0.15 (15%)
        fill_color: BGR border fill color, default black

    Return:
        Square BGR frame.

    Raises:
        ValueError: invalid bbox.
    """
    if image is None or image.size == 0:
        raise ValueError("image 不能为空 / image must not be empty")

    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"无效 bbox / Invalid bbox: {bbox}")

    img_h, img_w = image.shape[:2]
    bw = x2 - x1
    bh = y2 - y1
    max_side = max(bw, bh)
    target_side = int(round(max_side * (1.0 + padding_ratio)))
    if target_side < 1:
        target_side = 1

    # 以 bbox 中心为基准计算理想裁剪区域
    # Center the crop on the bbox center
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    half = target_side // 2

    ideal_x1 = cx - half
    ideal_y1 = cy - half
    ideal_x2 = ideal_x1 + target_side
    ideal_y2 = ideal_y1 + target_side

    # 裁剪到原图实际可用范围
    # Clip to actual image bounds
    crop_x1 = max(0, ideal_x1)
    crop_y1 = max(0, ideal_y1)
    crop_x2 = min(img_w, ideal_x2)
    crop_y2 = min(img_h, ideal_y2)

    cropped = image[crop_y1:crop_y2, crop_x1:crop_x2]

    # 如果实际裁剪不足 target × target，用 fill_color 填充至方形
    # If actual crop is smaller than target, pad with fill_color to make square
    actual_h, actual_w = cropped.shape[:2]
    if actual_h == target_side and actual_w == target_side:
        return cropped

    # 创建目标尺寸方形帧，把 cropped 粘贴到正确位置（保持中心对齐）
    # Create target-size square, paste cropped at correctly offset position
    square = np.full((target_side, target_side, 3), fill_color, dtype=np.uint8)
    paste_x = crop_x1 - ideal_x1   # 左侧填充宽度
    paste_y = crop_y1 - ideal_y1   # 上侧填充高度
    square[paste_y:paste_y + actual_h, paste_x:paste_x + actual_w] = cropped
    return square


def resize_square(image: np.ndarray, target_size: int = 224,
                  interpolation: int = cv2.INTER_LANCZOS4) -> np.ndarray:
    """
    将方形图像缩放到指定尺寸

    参数:
        image (np.ndarray): 方形 BGR 帧（H == W）
        target_size (int): 目标边长，默认 224（适配多数 CNN 输入）
        interpolation: cv2 插值方法，默认 LANCZOS4（高质量缩小）

    返回:
        np.ndarray: 缩放后的 BGR 帧 (target_size x target_size x 3 uint8)

    Resize a square image to target_size × target_size using LANCZOS4 by default.
    """
    h, w = image.shape[:2]
    if h == target_size and w == target_size:
        return image
    return cv2.resize(image, (target_size, target_size), interpolation=interpolation)
