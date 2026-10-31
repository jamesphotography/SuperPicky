#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IQA (Image Quality Assessment) 评分器
使用 Charm-TAD66K (CVPR 2025) 美学评分模型

V3.7: 切换到 TOPIQ 模型 (ResNet50 + CFANet, AVA 训练)
V4.7 (exp/charm-aesthetic): 切换到 Charm-TAD66K 模型
- 基于 DINOv2-small (ViT-S/14) + Charm 多尺度频率感知分词器
- 保留原图长宽比与前 64 个高频 28x28 细节块，避免 384x384 方形拉伸与全图低分辨率模糊
- 权重体积从 147 MB (TOPIQ FP16) 降至 44.2 MB (Charm FP16)，净省约 103 MB
- 在 76 张实拍鸟图人眼盲测基准上 Spearman 秩相关从 0.7100 提升至 0.7905
"""

import os
from typing import Optional, Tuple

import cv2
import numpy as np
import torch
import torchvision.transforms as T
from PIL import Image

from charm_model import (
    CHARM_MAX_EDGE,
    CharmNet,
    get_charm_weight_path,
    load_charm_weights,
    resize_for_charm,
)
from config import get_best_device, get_lazy_registry

# Charm 目标长边上限为 672 (CHARM_MAX_EDGE)；源图明显更大时先用 cv2 INTER_AREA
# 等比例快速预降到 1536 长边，再交给 PIL LANCZOS 做最终精修到 672，避免直接对
# 几千万像素的原图做 LANCZOS (耗时随源图像素数线性增长)。
# Charm caps the long edge at 672 (CHARM_MAX_EDGE). When the source is much
# larger, fast-shrink proportionally with cv2 INTER_AREA to long-edge 1536
# first, then hand off to PIL LANCZOS for the final 672 pass.
_TOPIQ_INPUT_SIZE: int = CHARM_MAX_EDGE
_TOPIQ_PRESHRINK_SIZE: int = 1536


def _preshrink_if_large(img_array: np.ndarray) -> np.ndarray:
    """
    源图长边明显大于预降阈值时，用 cv2 INTER_AREA 等比例快速降到中间尺寸。

    参数:
    img_array (np.ndarray): HWC 格式的图像数组（BGR 或 RGB 均可）

    返回:
    np.ndarray: 等比例预降后（或小图原样返回）的 HWC numpy 数组

    When the source long edge exceeds the pre-shrink threshold, proportionally
    downscale it using cv2 INTER_AREA to an intermediate resolution before the
    final PIL LANCZOS pass.

    Parameters:
    img_array (np.ndarray): HWC numpy image array (BGR or RGB)

    Return:
    np.ndarray: Proportionally pre-shrunk (or unchanged if small) HWC numpy array
    """
    height, width = img_array.shape[:2]
    longest = max(height, width)
    if longest > _TOPIQ_PRESHRINK_SIZE:
        scale = _TOPIQ_PRESHRINK_SIZE / float(longest)
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        img_array = cv2.resize(
            img_array,
            (new_w, new_h),
            interpolation=cv2.INTER_AREA,
        )
    return img_array


def _t(key: str, **params) -> str:
    """
    延迟导入 i18n，取当前界面语言的文案。

    参数:
    key (str): 国际化翻译键名
    **params: 格式化参数

    返回:
    str: 翻译后的字符串

    Lazily import i18n and translate the given key with the active UI language.

    Parameters:
    key (str): i18n translation key
    **params: Formatting parameters

    Return:
    str: Translated text string
    """
    from tools.i18n import t

    return t(key, **params)


class IQAScorer:
    """
    IQA 美学评分器（底层使用 Charm-TAD66K 模型，对外保持原接口与 [1, 10] 分制完全兼容）。

    IQA aesthetic scorer backed by Charm-TAD66K while preserving full backward
    compatibility with existing public methods and the [1, 10] score range.
    """

    def __init__(self, device: str = "mps") -> None:
        """
        初始化 IQA 评分器。

        参数:
        device (str): 期望计算设备 ('mps', 'cuda', 'cpu')

        返回:
        None

        Initialize the IQA scorer.

        Parameters:
        device (str): Requested compute device ('mps', 'cuda', 'cpu')

        Return:
        None
        """
        self.device = get_best_device()
        print(_t("logs.iqa_init", device=self.device))

        # 延迟加载模型（第一次使用时才加载）
        # Lazily load the model on first use
        self._topiq_model: Optional[CharmNet] = None
        self._use_fp16: bool = False

        # 复用 transform 实例，避免每次调用新建
        # Reuse the ToTensor transform instance across calls
        self._transform = T.ToTensor()

        print(_t("logs.iqa_ready"))

    def _load_topiq(self) -> CharmNet:
        """
        延迟加载 Charm-TAD66K 美学模型（保留 _load_topiq 方法名以兼容旧调用方与单测）。

        参数:
        无

        返回:
        CharmNet: 已加载并置于目标设备的 CharmNet 模型实例

        Lazily load the Charm-TAD66K aesthetic model (keeps the `_load_topiq`
        method name for backward compatibility with existing callers and tests).

        Parameters:
        None

        Return:
        CharmNet: Loaded CharmNet model instance on the target device
        """
        if self._topiq_model is None:
            print(_t("logs.charm_loading"))
            try:
                weight_path = get_charm_weight_path()

                model = CharmNet()
                # 权重先在 CPU 加载 / weights are loaded on CPU first
                load_charm_weights(model, weight_path, "cpu")
                try:
                    model = self._move_to_device(model, self.device)
                except Exception as exc:
                    # GPU 初始化失败 → 回退 CPU，并输出可见日志
                    # GPU init failed -> fall back to CPU with a visible log line
                    print(_t("logs.charm_cpu_fallback", device=self.device, error=exc))
                    self.device = torch.device("cpu")
                    model = self._move_to_device(model.float().cpu(), self.device)

                model.eval()
                self._topiq_model = model
                print(_t("logs.charm_model_loaded"))
            except Exception as exc:
                raise RuntimeError(f"Charm 美学模型加载失败: {exc}") from exc
        return self._topiq_model

    def _move_to_device(self, model: CharmNet, device: torch.device) -> CharmNet:
        """
        将模型移动到目标设备；MPS/CUDA 上转 FP16，并更新 self._use_fp16。

        参数:
        model (CharmNet): 待移动的模型
        device (torch.device): 目标设备

        返回:
        CharmNet: 已在目标设备上的模型

        Move the model to the target device; use FP16 on MPS/CUDA and update
        self._use_fp16.

        Parameters:
        model (CharmNet): Model to move
        device (torch.device): Target device

        Return:
        CharmNet: Model on the target device
        """
        model = model.to(device)
        if device.type in ("mps", "cuda"):
            model = model.half()
            self._use_fp16 = True
        else:
            self._use_fp16 = False
        return model

    def preload(self) -> None:
        """
        预热：立即加载美学模型权重（供启动预加载调用），避免首次评分时才现加载。

        参数:
        无

        返回:
        None

        Warm up: load aesthetic model weights immediately (called by startup
        preload) so the first scoring call does not pay model initialization cost.

        Parameters:
        None

        Return:
        None
        """
        self._load_topiq()

    def calculate_nima(self, image_path: str) -> Optional[float]:
        """
        计算美学评分（calculate_aesthetic 的兼容别名）。

        参数:
        image_path (str): 图像文件路径

        返回:
        Optional[float]: 美学分数 (1-10，越高越好) 或 None（失败时）

        Compute aesthetic score (backward-compatible alias of calculate_aesthetic).

        Parameters:
        image_path (str): Path to the image file

        Return:
        Optional[float]: Aesthetic score in [1, 10] (higher is better) or None on failure
        """
        return self.calculate_aesthetic(image_path)

    def _score_pil_image(self, img: Image.Image) -> float:
        """
        共享推理路径：对 RGB PIL 图像计算 Charm-TAD66K 美学评分。

        calculate_aesthetic()（路径入口）与 calculate_from_array()（数组入口）
        都委托到这里，推理细节（等比例预降、LANCZOS 精修、分词与前向推理、clamp）
        只维护一处，避免两个入口静默分叉。

        参数:
        img (Image.Image): RGB 模式的 PIL 图像

        返回:
        float: 美学分数，已限制到 [1.0, 10.0]

        Shared inference path: compute the Charm-TAD66K aesthetic score for an
        RGB PIL image. Both calculate_aesthetic() and calculate_from_array()
        delegate here so pre-shrink, LANCZOS resize, tokenization, and clamping
        live in one place.

        Parameters:
        img (Image.Image): PIL image in RGB mode

        Return:
        float: Aesthetic score clamped to [1.0, 10.0]
        """
        charm_model = self._load_topiq()

        # 零拷贝判断：只有大图才转 numpy 走 cv2 INTER_AREA 等比例预降
        # Zero-copy check: only large images take the numpy/cv2 pre-shrink
        if max(img.size) > _TOPIQ_PRESHRINK_SIZE:
            img = Image.fromarray(_preshrink_if_large(np.array(img)))
        img = resize_for_charm(img, max_edge=CHARM_MAX_EDGE)

        img_tensor = self._transform(img).unsqueeze(0)

        try:
            with torch.inference_mode():
                score = charm_model(img_tensor, return_mos=True)
        except Exception as exc:
            if self.device.type == "cpu":
                raise
            # GPU 推理失败 → 回退 CPU 重试一次 / GPU inference failed -> retry once on CPU
            print(_t("logs.charm_cpu_fallback", device=self.device, error=exc))
            self.device = torch.device("cpu")
            self._topiq_model = self._move_to_device(charm_model.float().cpu(), self.device)
            with torch.inference_mode():
                score = self._topiq_model(img_tensor, return_mos=True)

        if isinstance(score, torch.Tensor):
            score = score.item()

        return max(1.0, min(10.0, float(score)))

    def calculate_aesthetic(self, image_path: str) -> Optional[float]:
        """
        从图片文件路径计算 Charm-TAD66K 美学评分。

        参数:
        image_path (str): 图片文件路径

        返回:
        Optional[float]: 美学分数 (1-10，越高越好) 或 None（失败时）

        Compute the Charm-TAD66K aesthetic score from an image file path.

        Parameters:
        image_path (str): Image file path

        Return:
        Optional[float]: Aesthetic score in [1, 10] or None on failure
        """
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None

        try:
            img = Image.open(image_path)
            if max(img.size) > _TOPIQ_PRESHRINK_SIZE:
                img.draft("RGB", (_TOPIQ_PRESHRINK_SIZE, _TOPIQ_PRESHRINK_SIZE))
            img = img.convert("RGB")
            return self._score_pil_image(img)
        except Exception as exc:
            print(f"❌ Charm 美学计算失败: {exc}")
            return None

    def calculate_from_array(self, img_bgr: np.ndarray) -> Optional[float]:
        """
        从已加载的 BGR numpy 数组计算 Charm-TAD66K 美学评分。
        避免二次磁盘读取与 JPEG 解码；大图先在 numpy 侧完成 cv2 INTER_AREA 等比例预降。

        参数:
        img_bgr (np.ndarray): OpenCV BGR 格式的图像数组

        返回:
        Optional[float]: 美学分数 (1-10，越高越好) 或 None（失败时）

        Compute the Charm-TAD66K aesthetic score from an in-memory BGR numpy array.
        Avoids redundant disk reads and JPEG decoding; large images are pre-shrunk
        on the numpy side via cv2 INTER_AREA before converting to PIL.

        Parameters:
        img_bgr (np.ndarray): OpenCV BGR image array

        Return:
        Optional[float]: Aesthetic score in [1, 10] or None on failure
        """
        if img_bgr is None or img_bgr.size == 0:
            return None

        try:
            img_bgr = _preshrink_if_large(img_bgr)
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(img_rgb)
            del img_rgb
            return self._score_pil_image(img)
        except Exception as exc:
            print(f"❌ Charm (from array) 计算失败: {exc}")
            return None

    def calculate_brisque(self, image_input) -> Optional[float]:
        """
        计算 BRISQUE 技术质量评分（已弃用，始终返回 None 以保持接口兼容）。

        参数:
        image_input: 任意图像输入（已忽略）

        返回:
        Optional[float]: 始终返回 None

        Deprecated BRISQUE technical quality score (always returns None for
        backward compatibility).

        Parameters:
        image_input: Ignored input

        Return:
        Optional[float]: Always None
        """
        return None

    def calculate_both(
        self, full_image_path: str, crop_image
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        计算美学评分与已弃用的 BRISQUE 评分二元组。

        参数:
        full_image_path (str): 全图路径
        crop_image: 已弃用参数

        返回:
        Tuple[Optional[float], Optional[float]]: (aesthetic_score, None)

        Compute aesthetic score and deprecated BRISQUE tuple.

        Parameters:
        full_image_path (str): Full image path
        crop_image: Deprecated parameter

        Return:
        Tuple[Optional[float], Optional[float]]: (aesthetic_score, None)
        """
        aesthetic_score = self.calculate_aesthetic(full_image_path)
        return aesthetic_score, None


def get_iqa_scorer(device: str = "mps") -> IQAScorer:
    """
    获取 IQA 评分器延迟单例。

    参数:
    device (str): 计算设备标识

    返回:
    IQAScorer: 缓存的 IQAScorer 单例实例

    Get the lazy singleton IQAScorer instance.

    Parameters:
    device (str): Compute device identifier

    Return:
    IQAScorer: Cached IQAScorer singleton instance
    """
    registry = get_lazy_registry()
    return registry.get_or_create(
        f"iqa_scorer.instance::{device}", lambda: IQAScorer(device=device)
    )


def calculate_nima(image_path: str) -> Optional[float]:
    """
    计算美学评分的模块级便捷函数。

    参数:
    image_path (str): 图片文件路径

    返回:
    Optional[float]: 美学分数 (1-10) 或 None

    Module-level convenience function to compute the aesthetic score.

    Parameters:
    image_path (str): Path to the image file

    Return:
    Optional[float]: Aesthetic score in [1, 10] or None
    """
    scorer = get_iqa_scorer()
    return scorer.calculate_aesthetic(image_path)


def calculate_brisque(image_input) -> Optional[float]:
    """
    已弃用的模块级 BRISQUE 评分函数（返回 None）。

    参数:
    image_input: 忽略的输入

    返回:
    Optional[float]: None

    Deprecated module-level BRISQUE function (returns None).

    Parameters:
    image_input: Ignored input

    Return:
    Optional[float]: None
    """
    return None
