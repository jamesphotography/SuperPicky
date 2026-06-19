# -*- coding: utf-8 -*-
"""
CACNet 裁剪构图封装。固定 CPU 推理以规避 MPS 的 grid_sample/pixel_shuffle 兼容坑。
CACNet cropping wrapper. Fixed CPU inference to avoid MPS grid_sample/pixel_shuffle issues.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

import cv2
import numpy as np
import torch
import torchvision.transforms as T
from PIL import Image

from config import get_install_scoped_resource_path

# ── 常量 / Constants ──────────────────────────────────────────────────────────
# 与 CACNet config_cropping.cfg.image_size 对应
# Matches CACNet config_cropping.cfg.image_size
_NET_SIZE: int = 224
_MEAN: List[float] = [0.485, 0.456, 0.406]
_STD: List[float] = [0.229, 0.224, 0.225]


def _default_weight_path() -> str:
    """
    返回 cacnet_cropping.pth 的安装范围路径。
    Returns the install-scoped path for cacnet_cropping.pth.
    """
    return str(get_install_scoped_resource_path(os.path.join("models", "cacnet_cropping.pth")))


def _rescale_box(
    net_box: Tuple[float, float, float, float],
    net_w: int,
    net_h: int,
    orig_w: int,
    orig_h: int,
) -> Tuple[int, int, int, int]:
    """
    把网络输入尺度的 (x1,y1,x2,y2) 还原到原图像素并夹界。
    Rescale a (x1,y1,x2,y2) box from network-input scale back to original image pixels,
    clamped to [0, orig_w] x [0, orig_h].

    参数 / Args:
        net_box:  网络尺度下的坐标 / Coordinates in network-input scale.
        net_w:    网络输入宽度 / Network input width.
        net_h:    网络输入高度 / Network input height.
        orig_w:   原图宽度 / Original image width.
        orig_h:   原图高度 / Original image height.

    返回 / Returns:
        Tuple[int,int,int,int]: 原图像素坐标 (x1,y1,x2,y2) / Pixel coords in original image.
    """
    x1, y1, x2, y2 = net_box
    x1 = x1 / net_w * orig_w
    x2 = x2 / net_w * orig_w
    y1 = y1 / net_h * orig_h
    y2 = y2 / net_h * orig_h
    x1 = max(0, min(orig_w, x1))
    x2 = max(0, min(orig_w, x2))
    y1 = max(0, min(orig_h, y1))
    y2 = max(0, min(orig_h, y2))
    return (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))


class CACNetCropper:
    """
    懒加载 CACNet 裁剪预测器，固定使用 CPU 设备。
    Lazy-loading CACNet crop predictor, fixed to CPU device.

    CPU 固定原因：MPS 对 grid_sample / pixel_shuffle 的支持存在兼容问题。
    Reason for CPU-only: MPS has compatibility issues with grid_sample / pixel_shuffle.
    """

    def __init__(self, weight_path: Optional[str] = None):
        """
        参数 / Args:
            weight_path: 模型权重路径，None 时使用默认安装路径。
                         Path to model weights; uses default install path when None.
        """
        self.weight_path: str = weight_path or _default_weight_path()
        # 固定 CPU，不使用 MPS/CUDA / Fixed CPU, no MPS/CUDA
        self.device: torch.device = torch.device("cpu")
        self._model: Optional[object] = None
        self._tf = T.Compose([T.ToTensor(), T.Normalize(mean=_MEAN, std=_STD)])

    def _ensure_loaded(self) -> None:
        """
        懒加载模型权重。权重文件缺失时抛出 FileNotFoundError。
        Lazily load model weights. Raises FileNotFoundError if weight file is missing.

        异常 / Raises:
            FileNotFoundError: 权重文件路径不存在。/ Weight file path does not exist.
        """
        if self._model is not None:
            return
        if not os.path.exists(self.weight_path):
            raise FileNotFoundError(f"CACNet 权重不存在: {self.weight_path}")
        from core.vendor.cacnet.cacnet_model import CACNet
        model = CACNet(loadweights=False)
        # weights_only=True：权重为纯 state_dict，避免 unpickle 任意对象的 RCE 风险
        # weights_only=True: weights are a plain state_dict; prevents arbitrary object unpickling (RCE risk).
        # （与 core/keypoint_detector.py 保持一致 / Consistent with core/keypoint_detector.py）
        state = torch.load(self.weight_path, map_location=self.device, weights_only=True)
        model.load_state_dict(state)
        self._model = model.to(self.device).eval()

    def predict_box(self, image_bgr: np.ndarray) -> Tuple[int, int, int, int]:
        """
        对输入图像预测最优构图裁剪框（原图像素坐标）。
        Predict the best composition crop box for the input image (original pixel coords).

        参数 / Args:
            image_bgr (np.ndarray): BGR 格式图像（OpenCV 默认格式）。
                                    Image in BGR format (OpenCV default).

        返回 / Returns:
            Tuple[int,int,int,int]: 最优裁剪框 (x1,y1,x2,y2)，原图像素坐标。
                                    Best crop box (x1,y1,x2,y2) in original pixel coords.
        """
        self._ensure_loaded()
        orig_h, orig_w = image_bgr.shape[:2]
        # BGR → RGB，缩放至网络输入尺寸 / BGR → RGB, resize to network input size
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb).resize((_NET_SIZE, _NET_SIZE), Image.BILINEAR)
        tensor = self._tf(pil).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            # only_classify=False 同时返回构图 logits、KCM 热力图和裁剪框
            # only_classify=False returns composition logits, KCM heatmap, and crop box
            _logits, _kcm, crop = self._model(tensor, only_classify=False)
        # crop shape: (1, 4)，坐标为网络输入尺度 / crop shape: (1,4), in network-input scale
        net_box = crop[0].cpu().numpy().tolist()  # [x1, y1, x2, y2]
        return _rescale_box(net_box, _NET_SIZE, _NET_SIZE, orig_w, orig_h)


# ── 单例 / Singleton ──────────────────────────────────────────────────────────
_instance: Optional[CACNetCropper] = None


def get_cacnet_cropper() -> CACNetCropper:
    """
    返回 CACNetCropper 的全局单例（懒初始化）。
    Returns the global CACNetCropper singleton (lazily initialized).

    返回 / Returns:
        CACNetCropper: 全局唯一实例 / The global singleton instance.
    """
    global _instance
    if _instance is None:
        _instance = CACNetCropper()
    return _instance
