"""
基于掩码的锐度计算算法
利用 YOLOv11l-seg 模型提供的像素级掩码，精确计算鸟体区域的锐度
消除背景噪声干扰，实现大小鸟之间的公平比较
"""

import cv2
import numpy as np
from typing import Dict, Optional, Tuple


class MaskBasedSharpnessCalculator:
    """基于掩码的锐度计算器"""

    def __init__(self, method='variance', normalization='log_compression'):
        """
        初始化锐度计算器

        Args:
            method: 锐度计算方法
                - 'variance': 拉普拉斯方差（推荐）
                - 'L2': 拉普拉斯L2范数（兼容旧算法）
            normalization: 归一化方法
                - None: 不归一化，使用原始方差（推荐，与NIMA/BRISQUE相关性最强）
                - 'log_compression': 对数压缩 log(1+x)*1000（V3.1默认，大小鸟公平）
                - 'sqrt': 除以像素数平方根（方案B）
                - 'linear': 除以像素数（文档方案A）
                - 'log': 除以像素数对数（方案C，最温和）
                - 'gentle': 温和归一化（给大鸟适当优势）
        """
        self.method = method
        self.normalization = normalization

    def calculate(self, image: np.ndarray, mask: np.ndarray) -> Dict[str, float]:
        """
        计算基于掩码的锐度

        Args:
            image: 原图（BGR格式）
            mask: 二值掩码（H, W），鸟体区域=1，背景=0

        Returns:
            {
                'total_sharpness': 原始锐度值（方差或L2范数）
                'normalized_sharpness': 归一化锐度
                'effective_pixels': 有效像素数
                'area_ratio': 面积占比
            }
        """
        if image is None or mask is None:
            return self._get_zero_result()

        # 1. 转换为灰度图
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # 2. 确保掩码尺寸与图像一致
        if mask.shape[:2] != gray.shape[:2]:
            mask = cv2.resize(mask, (gray.shape[1], gray.shape[0]),
                            interpolation=cv2.INTER_NEAREST)

        # 3. 二值化掩码
        mask_binary = (mask > 0.5).astype(bool)

        # 4. 计算拉普拉斯响应
        laplacian = cv2.Laplacian(gray, cv2.CV_64F, ksize=3)

        # 5. 仅提取掩码区域的拉普拉斯值
        laplacian_masked = laplacian[mask_binary]

        # 6. 计算总锐度
        if len(laplacian_masked) > 0:
            if self.method == 'variance':
                total_sharpness = np.var(laplacian_masked)
            elif self.method == 'L2':
                total_sharpness = np.linalg.norm(laplacian_masked)
            else:
                total_sharpness = np.var(laplacian_masked)  # 默认使用方差
        else:
            total_sharpness = 0.0

        # 7. 计算面积信息
        effective_pixels = np.sum(mask_binary)
        total_pixels = gray.shape[0] * gray.shape[1]
        area_ratio = effective_pixels / total_pixels if total_pixels > 0 else 0.0

        # 8. 归一化锐度
        normalized_sharpness = self._normalize(total_sharpness, effective_pixels)

        return {
            'total_sharpness': float(total_sharpness),
            'normalized_sharpness': float(normalized_sharpness),
            'effective_pixels': int(effective_pixels),
            'area_ratio': float(area_ratio)
        }

    def calculate_from_bbox(self, image: np.ndarray, bbox: Tuple[int, int, int, int],
                           mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        """
        从边界框和可选掩码计算锐度

        Args:
            image: 原图（BGR格式）
            bbox: 边界框 (x, y, w, h)
            mask: 可选的掩码，如果提供则使用掩码计算，否则退化为BBox计算

        Returns:
            锐度计算结果字典
        """
        x, y, w, h = bbox

        # 裁剪图像区域
        crop_img = image[y:y+h, x:x+w]

        if mask is not None:
            # 裁剪掩码区域
            crop_mask = mask[y:y+h, x:x+w]
            return self.calculate(crop_img, crop_mask)
        else:
            # 如果没有掩码，创建全1掩码（相当于使用整个BBox）
            full_mask = np.ones((h, w), dtype=np.uint8)
            return self.calculate(crop_img, full_mask)

    def _normalize(self, sharpness: float, pixel_count: int) -> float:
        """
        归一化锐度值

        Args:
            sharpness: 原始锐度值
            pixel_count: 有效像素数量

        Returns:
            归一化后的锐度值
        """
        if pixel_count <= 0:
            return 0.0

        if self.normalization is None or self.normalization == 'none':
            # 不归一化，直接返回原始方差（推荐）
            # 与NIMA/BRISQUE相关性最强，对鸟大小偏差最小
            return sharpness

        elif self.normalization == 'log_compression':
            # V3.1 对数压缩：log(1 + x) * 1000
            # 范围：4508-10455，大小鸟公平（比例0.971）
            # 推荐滑块范围：6000-9000，默认7000
            return np.log1p(sharpness) * 1000

        elif self.normalization == 'sqrt':
            # 方案B：除以像素数平方根
            return sharpness / np.sqrt(pixel_count)

        elif self.normalization == 'linear':
            # 方案A（文档原版）：除以像素数
            return sharpness / pixel_count

        elif self.normalization == 'log':
            # 方案C（最温和）：除以像素数对数
            return sharpness / np.log10(pixel_count + 10)

        elif self.normalization == 'gentle':
            # 温和归一化：介于 sqrt 和 log 之间，给大鸟适当优势
            # 使用 pixel_count^0.35 作为归一化因子
            # 0.5 是 sqrt，0 是不归一化，0.35 介于中间
            return sharpness / (pixel_count ** 0.35)

        else:
            # 默认使用原始方差（不归一化）
            return sharpness

    def _get_zero_result(self) -> Dict[str, float]:
        """返回零值结果"""
        return {
            'total_sharpness': 0.0,
            'normalized_sharpness': 0.0,
            'effective_pixels': 0,
            'area_ratio': 0.0
        }


# 兼容旧代码的简单函数接口
def calculate_sharpness_with_mask(image: np.ndarray, mask: np.ndarray) -> float:
    """
    简单接口：计算归一化锐度（使用V3.1推荐配置）

    Args:
        image: 原图（BGR格式）
        mask: 二值掩码

    Returns:
        归一化锐度值
    """
    calculator = MaskBasedSharpnessCalculator(method='variance', normalization='log_compression')
    result = calculator.calculate(image, mask)
    return result['normalized_sharpness']


def calculate_sharpness_legacy(image: np.ndarray) -> float:
    """
    兼容旧算法的接口（不使用掩码）

    Args:
        image: 裁剪后的图像（BGR格式）

    Returns:
        拉普拉斯L2范数
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return cv2.norm(laplacian, cv2.NORM_L2)
