#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IQA (Image Quality Assessment) 评分器
使用 TOPIQ 美学评分模型

V3.7: 切换到 TOPIQ 模型，更好的鸟类摄影美学评估
- TOPIQ 使用 Top-down 语义理解，对主体识别更准确
- 比 NIMA 快约 40%
- 基于 ResNet50 + CFANet 架构
"""

import os
import torch
from typing import Tuple, Optional
import numpy as np
from PIL import Image
import torchvision.transforms as T

# 使用 TOPIQ 模型
from topiq_model import CFANet, load_topiq_weights, get_topiq_weight_path
from tools.i18n import t as _t

from config import get_best_device, get_lazy_registry

import cv2

# V4.5: TOPIQ 输入尺寸固定 384x384；源图明显更大时先用 cv2 INTER_AREA
# 快速预降，再交给 PIL LANCZOS 做最终精修，避免直接对几千万像素的原图
# 做 LANCZOS(耗时随源图像素数线性增长)。
# V4.5: TOPIQ's input size is fixed at 384x384. When the source is much
# larger, fast-shrink with cv2 INTER_AREA first, then hand off to PIL
# LANCZOS for the final pass — avoids running LANCZOS directly on a
# multi-megapixel source, whose cost scales with source pixel count.
_TOPIQ_INPUT_SIZE = 384
_TOPIQ_PRESHRINK_SIZE = _TOPIQ_INPUT_SIZE * 4  # 1536


def _preshrink_if_large(img_array: np.ndarray) -> np.ndarray:
    """
    源图明显大于 TOPIQ 输入尺寸时，用 cv2 INTER_AREA 快速降到中间尺寸。

    INTER_AREA 是专为降采样设计的区域平均算法，比 PIL LANCZOS 快得多，
    用它先降到约 4 倍最终目标尺寸，再由调用方做最后一步 PIL LANCZOS 精修，
    实测（棋盘图案 + 真实 45MP 鸟类照片）显示最终评分误差 < 0.02
    (1-10 分制)，可忽略。小图（小于预降尺寸）原样返回，不做无意义的放大。

    If the source is much larger than the TOPIQ input size, fast-shrink it
    with cv2 INTER_AREA first. INTER_AREA is an area-averaging algorithm
    designed for downscaling and is much faster than PIL LANCZOS; shrinking
    to ~4x the final target size here, then letting the caller do a final
    PIL LANCZOS pass, keeps the measured score drift under 0.02 (on a 1-10
    scale) on both a synthetic checkerboard pattern and a real 45MP bird
    photo. Small images (already below the pre-shrink size) are returned
    unchanged — no point upscaling them.

    Args:
        img_array: HWC numpy array，通道顺序不限（BGR 或 RGB 均可，只做
            空间 resize，不涉及颜色通道）。

    Returns:
        resize 后（或原样，小图时）的 HWC numpy array。
    """
    if max(img_array.shape[:2]) > _TOPIQ_PRESHRINK_SIZE:
        img_array = cv2.resize(
            img_array,
            (_TOPIQ_PRESHRINK_SIZE, _TOPIQ_PRESHRINK_SIZE),
            interpolation=cv2.INTER_AREA,
        )
    return img_array


class IQAScorer:
    """IQA 评分器 - 使用 TOPIQ 美学评分"""

    def __init__(self, device='mps'):
        """
        初始化 IQA 评分器

        Args:
            device: 计算设备 ('mps', 'cuda', 'cpu')
        """
        self.device = get_best_device()
        print(f"🎨 IQA 评分器初始化中... (设备: {self.device})")

        # 延迟加载模型（第一次使用时才加载）
        self._topiq_model = None

        # V4.0.5: 复用 transform 实例，避免每次调用新建
        self._transform = T.ToTensor()

        print("✅ IQA 评分器已就绪 (TOPIQ模型将在首次使用时加载)")

    def _load_topiq(self):
        """延迟加载 TOPIQ 模型"""
        if self._topiq_model is None:
            print(_t("logs.topiq_loading"))
            try:
                # 获取权重路径
                weight_path = get_topiq_weight_path()
                
                # 初始化 TOPIQ 模型
                self._topiq_model = CFANet()
                load_topiq_weights(self._topiq_model, weight_path, self.device)
                self._topiq_model.to(self.device)
                
                # V4.0.5: 启用 FP16 半精度推理，提速约 30-50%
                if self.device.type in ('mps', 'cuda'):
                    self._topiq_model = self._topiq_model.half()
                    self._use_fp16 = True
                else:
                    self._use_fp16 = False
                    
                self._topiq_model.eval()
                print("✅ TOPIQ 模型加载完成")
            except Exception as e:
                raise RuntimeError(f"TOPIQ 模型加载失败: {e}")
        return self._topiq_model

    def preload(self) -> None:
        """
        预热:立即加载 TOPIQ 权重(供启动预加载调用),避免首次评分时才现加载。
        Warm up: load TOPIQ weights now (called by startup preload) so the first
        scoring call (e.g. crop advisor / bird selection) doesn't trigger a load.
        """
        self._load_topiq()

    def calculate_nima(self, image_path: str) -> Optional[float]:
        """
        计算美学评分 (使用 TOPIQ，保持接口名称兼容)

        Args:
            image_path: 图片路径

        Returns:
            美学分数 (1-10, 越高越好) 或 None (失败时)
        """
        return self.calculate_aesthetic(image_path)

    def calculate_aesthetic(self, image_path: str) -> Optional[float]:
        """
        计算 TOPIQ 美学评分

        Args:
            image_path: 图片路径

        Returns:
            美学分数 (1-10, 越高越好) 或 None (失败时)
        """
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None

        try:
            # 加载模型
            topiq_model = self._load_topiq()

            # 加载图片
            img = Image.open(image_path).convert('RGB')

            # V4.5: 先用 cv2 INTER_AREA 快速预降(大图才生效)，再 PIL LANCZOS
            # 精修到 384x384(TOPIQ 推荐尺寸，避免 MPS 兼容性问题)。
            # V4.5: Fast-preshrink with cv2 INTER_AREA first (large images
            # only), then PIL LANCZOS to the final 384x384 (TOPIQ's
            # recommended size, avoids an MPS compatibility issue).
            img_array = _preshrink_if_large(np.array(img))
            img = Image.fromarray(img_array).resize(
                (_TOPIQ_INPUT_SIZE, _TOPIQ_INPUT_SIZE), Image.LANCZOS
            )

            # 转为张量（复用实例变量）
            img_tensor = self._transform(img).unsqueeze(0).to(self.device)
            
            # V4.0.5: 使用 FP16 和 inference_mode 优化推理
            if hasattr(self, '_use_fp16') and self._use_fp16:
                img_tensor = img_tensor.half()

            # 计算评分
            with torch.inference_mode():
                score = topiq_model(img_tensor, return_mos=True)

            # 转换为 Python float
            if isinstance(score, torch.Tensor):
                score = score.item()

            # 分数范围 [1, 10]
            score = float(score)
            score = max(1.0, min(10.0, score))

            return score

        except Exception as e:
            print(f"❌ TOPIQ 计算失败: {e}")
            return None

    def calculate_from_array(self, img_bgr: np.ndarray) -> Optional[float]:
        """
        V4.0.5: 从已加载的 BGR numpy array 计算 TOPIQ 美学评分
        
        避免二次磁盘读取：主流程 cv2.imread 已读过图片，
        直接传入 numpy array 复用，省去 Image.open 的 JPEG 解码。
        
        Args:
            img_bgr: OpenCV BGR 格式的 numpy array
            
        Returns:
            美学分数 (1-10, 越高越好) 或 None (失败时)
        """
        if img_bgr is None or img_bgr.size == 0:
            return None

        try:
            topiq_model = self._load_topiq()

            # V4.5: 先用 cv2 INTER_AREA 快速预降(大图才生效)，再 BGR→RGB→PIL
            # →LANCZOS 精修，避免对整张原图做代价高昂的 LANCZOS。
            # V4.5: Fast-preshrink with cv2 INTER_AREA first (only kicks in
            # for large sources), then BGR→RGB→PIL→LANCZOS for the final
            # pass — avoids running expensive LANCZOS on the full-res source.
            img_bgr = _preshrink_if_large(img_bgr)
            # del img_rgb 在 resize 前释放全分辨率副本，避免 50-70 MB 驻留到推理结束
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(img_rgb)
            del img_rgb
            img = img.resize((_TOPIQ_INPUT_SIZE, _TOPIQ_INPUT_SIZE), Image.LANCZOS)

            # 转为张量（复用实例变量）
            img_tensor = self._transform(img).unsqueeze(0).to(self.device)

            # FP16 推理
            if hasattr(self, '_use_fp16') and self._use_fp16:
                img_tensor = img_tensor.half()

            # 计算评分
            with torch.inference_mode():
                score = topiq_model(img_tensor, return_mos=True)

            if isinstance(score, torch.Tensor):
                score = score.item()

            score = float(score)
            score = max(1.0, min(10.0, score))
            return score

        except Exception as e:
            print(f"❌ TOPIQ (from array) 计算失败: {e}")
            return None

    def calculate_brisque(self, image_input) -> Optional[float]:
        """
        计算 BRISQUE 技术质量评分 (已弃用，返回 None)
        
        保留此方法以保持接口兼容性
        """
        # BRISQUE 已弃用
        return None

    def calculate_both(self,
                       full_image_path: str,
                       crop_image) -> Tuple[Optional[float], Optional[float]]:
        """
        计算美学评分 (BRISQUE 已弃用)

        Args:
            full_image_path: 全图路径 (用于美学评分)
            crop_image: 不再使用

        Returns:
            (aesthetic_score, None) 元组
        """
        aesthetic_score = self.calculate_aesthetic(full_image_path)
        return aesthetic_score, None


def get_iqa_scorer(device='mps') -> IQAScorer:
    """
    获取 IQA 评分器单例

    Args:
        device: 计算设备

    Returns:
        IQAScorer 实例
    """
    registry = get_lazy_registry()
    return registry.get_or_create(f"iqa_scorer.instance::{device}", lambda: IQAScorer(device=device))


# 便捷函数 (保持向后兼容)
def calculate_nima(image_path: str) -> Optional[float]:
    """计算美学评分的便捷函数 (使用 TOPIQ)"""
    scorer = get_iqa_scorer()
    return scorer.calculate_aesthetic(image_path)


def calculate_brisque(image_input) -> Optional[float]:
    """计算 BRISQUE 评分 (已弃用)"""
    return None


if __name__ == "__main__":
    # 测试代码
    print("=" * 70)
    print("IQA 评分器测试 (TOPIQ)")
    print("=" * 70)

    # 初始化评分器
    scorer = IQAScorer(device='mps')

    # 测试图片路径
    test_image = "img/_Z9W0960.jpg"

    if os.path.exists(test_image):
        print(f"\n📷 测试图片: {test_image}")

        import time
        start = time.time()
        score = scorer.calculate_aesthetic(test_image)
        elapsed = time.time() - start

        if score is not None:
            print(f"   ✅ TOPIQ 分数: {score:.2f} / 10")
            print(f"   ⏱️  耗时: {elapsed*1000:.0f}ms")
        else:
            print(f"   ❌ 评分计算失败")

    else:
        print(f"\n⚠️  测试图片不存在: {test_image}")
        print("   请提供有效的测试图片路径")

    print("\n" + "=" * 70)
