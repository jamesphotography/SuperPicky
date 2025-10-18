#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IQA (Image Quality Assessment) 评分器
使用 PyIQA 库实现 NIMA 和 BRISQUE 评分
"""

import os
import torch
import pyiqa
from typing import Tuple, Optional
import numpy as np
from PIL import Image


class IQAScorer:
    """IQA 评分器 - 支持 NIMA (美学) 和 BRISQUE (技术质量)"""

    def __init__(self, device='mps'):
        """
        初始化 IQA 评分器

        Args:
            device: 计算设备 ('mps', 'cuda', 'cpu')
        """
        self.device = self._get_device(device)
        print(f"🎨 IQA 评分器初始化中... (设备: {self.device})")

        # 延迟加载模型（第一次使用时才加载）
        self._nima_model = None
        self._brisque_model = None

        print("✅ IQA 评分器已就绪 (模型将在首次使用时加载)")

    def _get_device(self, preferred_device='mps'):
        """
        获取最佳计算设备

        Args:
            preferred_device: 首选设备

        Returns:
            可用的设备
        """
        # 检查 MPS (Apple GPU)
        if preferred_device == 'mps':
            try:
                if torch.backends.mps.is_available():
                    return torch.device('mps')
            except:
                pass

        # 检查 CUDA (NVIDIA GPU)
        if preferred_device == 'cuda' or torch.cuda.is_available():
            return torch.device('cuda')

        # 默认使用 CPU
        return torch.device('cpu')

    def _load_nima(self):
        """延迟加载 NIMA 模型"""
        if self._nima_model is None:
            print("📥 加载 NIMA 美学评分模型...")
            try:
                # PyIQA 的 NIMA 模型
                self._nima_model = pyiqa.create_metric(
                    'nima',
                    device=self.device,
                    as_loss=False
                )
                print("✅ NIMA 模型加载完成")
            except Exception as e:
                print(f"⚠️  NIMA 模型加载失败: {e}")
                print("   尝试使用 CPU 模式...")
                self._nima_model = pyiqa.create_metric(
                    'nima',
                    device=torch.device('cpu'),
                    as_loss=False
                )
        return self._nima_model

    def _load_brisque(self):
        """延迟加载 BRISQUE 模型"""
        if self._brisque_model is None:
            print("📥 加载 BRISQUE 技术质量评分模型...")
            try:
                # PyIQA 的 BRISQUE 模型
                self._brisque_model = pyiqa.create_metric(
                    'brisque',
                    device=self.device,
                    as_loss=False
                )
                print("✅ BRISQUE 模型加载完成")
            except Exception as e:
                print(f"⚠️  BRISQUE 模型加载失败: {e}")
                print("   尝试使用 CPU 模式...")
                self._brisque_model = pyiqa.create_metric(
                    'brisque',
                    device=torch.device('cpu'),
                    as_loss=False
                )
        return self._brisque_model

    def calculate_nima(self, image_path: str) -> Optional[float]:
        """
        计算 NIMA 美学评分 (使用全图)

        Args:
            image_path: 图片路径

        Returns:
            NIMA 分数 (0-10, 越高越好) 或 None (失败时)
        """
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None

        try:
            # 加载模型
            nima_model = self._load_nima()

            # 计算评分
            with torch.no_grad():
                score = nima_model(image_path)

            # 转换为 Python float
            if isinstance(score, torch.Tensor):
                score = score.item()

            # NIMA 分数范围 [0, 10]
            score = float(score)
            score = max(0.0, min(10.0, score))  # 限制在 [0, 10]

            return score

        except Exception as e:
            print(f"❌ NIMA 计算失败: {e}")
            return None

    def calculate_brisque(self, image_input) -> Optional[float]:
        """
        计算 BRISQUE 技术质量评分 (使用 crop 图片)

        Args:
            image_input: 图片路径 (str) 或 numpy 数组 (crop 图片)

        Returns:
            BRISQUE 分数 (0-100, 越低越好) 或 None (失败时)
        """
        try:
            # 加载模型
            brisque_model = self._load_brisque()

            # 处理输入
            if isinstance(image_input, str):
                # 文件路径
                if not os.path.exists(image_input):
                    print(f"❌ 图片不存在: {image_input}")
                    return None
                input_path = image_input
            elif isinstance(image_input, np.ndarray):
                # numpy 数组 (crop 图片)
                # 保存为临时文件
                import tempfile
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, "temp_brisque.jpg")

                # 转换 BGR (OpenCV) 到 RGB (PIL)
                if len(image_input.shape) == 3 and image_input.shape[2] == 3:
                    image_rgb = image_input[:, :, ::-1]  # BGR -> RGB
                else:
                    image_rgb = image_input

                # 保存临时文件
                pil_img = Image.fromarray(image_rgb.astype(np.uint8))
                pil_img.save(temp_path, quality=95)
                input_path = temp_path
            else:
                print(f"❌ 不支持的输入类型: {type(image_input)}")
                return None

            # 计算评分
            with torch.no_grad():
                score = brisque_model(input_path)

            # 转换为 Python float
            if isinstance(score, torch.Tensor):
                score = score.item()

            # BRISQUE 分数范围 [0, 100], 越低越好
            score = float(score)
            score = max(0.0, min(100.0, score))  # 限制在 [0, 100]

            return score

        except Exception as e:
            print(f"❌ BRISQUE 计算失败: {e}")
            return None

    def calculate_both(self,
                       full_image_path: str,
                       crop_image) -> Tuple[Optional[float], Optional[float]]:
        """
        同时计算 NIMA 和 BRISQUE 评分

        Args:
            full_image_path: 全图路径 (用于 NIMA)
            crop_image: Crop 图片路径或 numpy 数组 (用于 BRISQUE)

        Returns:
            (nima_score, brisque_score) 元组
        """
        nima_score = self.calculate_nima(full_image_path)
        brisque_score = self.calculate_brisque(crop_image)

        return nima_score, brisque_score


# 全局单例
_iqa_scorer_instance = None


def get_iqa_scorer(device='mps') -> IQAScorer:
    """
    获取 IQA 评分器单例

    Args:
        device: 计算设备

    Returns:
        IQAScorer 实例
    """
    global _iqa_scorer_instance
    if _iqa_scorer_instance is None:
        _iqa_scorer_instance = IQAScorer(device=device)
    return _iqa_scorer_instance


# 便捷函数
def calculate_nima(image_path: str) -> Optional[float]:
    """计算 NIMA 美学评分的便捷函数"""
    scorer = get_iqa_scorer()
    return scorer.calculate_nima(image_path)


def calculate_brisque(image_input) -> Optional[float]:
    """计算 BRISQUE 技术质量评分的便捷函数"""
    scorer = get_iqa_scorer()
    return scorer.calculate_brisque(image_input)


if __name__ == "__main__":
    # 测试代码
    print("=" * 70)
    print("IQA 评分器测试")
    print("=" * 70)

    # 初始化评分器
    scorer = IQAScorer(device='mps')

    # 测试图片路径
    test_image = "img/_Z9W0960.jpg"

    if os.path.exists(test_image):
        print(f"\n📷 测试图片: {test_image}")

        # 测试 NIMA (全图)
        print("\n1️⃣ 测试 NIMA 美学评分:")
        nima_score = scorer.calculate_nima(test_image)
        if nima_score is not None:
            print(f"   ✅ NIMA 分数: {nima_score:.2f} / 10")
        else:
            print(f"   ❌ NIMA 计算失败")

        # 测试 BRISQUE (全图，实际使用时应该用 crop)
        print("\n2️⃣ 测试 BRISQUE 技术质量评分:")
        brisque_score = scorer.calculate_brisque(test_image)
        if brisque_score is not None:
            print(f"   ✅ BRISQUE 分数: {brisque_score:.2f} / 100 (越低越好)")
        else:
            print(f"   ❌ BRISQUE 计算失败")

        # 测试同时计算
        print("\n3️⃣ 测试同时计算:")
        nima, brisque = scorer.calculate_both(test_image, test_image)
        print(f"   NIMA: {nima:.2f} | BRISQUE: {brisque:.2f}")

    else:
        print(f"\n⚠️  测试图片不存在: {test_image}")
        print("   请提供有效的测试图片路径")

    print("\n" + "=" * 70)
