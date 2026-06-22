# -*- coding: utf-8 -*-
"""
SVDLUT 空间感知调色封装 / SVDLUT spatial-aware color wrapper.

懒加载单例;colorize() 在原图与满调色结果间按 strength 线性混合。
权重:models/svdlut.pth(HF 下载)。设备:get_best_device(),MPS/CPU 用 fp32。

Lazy-loaded singleton; colorize() linearly blends between the original and the
fully-graded result by strength. Weight: models/svdlut.pth (HF download).
"""
from __future__ import annotations

import numpy as np
import torch

from config import (get_best_device, get_install_scoped_resource_path,
                    get_packaged_model_relative_path)

_MODEL = None  # 单例缓存(按设备) / singleton cache (per device)
_MODEL_DEVICE = None
_WEIGHT_REL = "models/svdlut.pth"


def _weight_path() -> str:
    """返回权重绝对路径(兼容打包/开发) / absolute weight path."""
    return str(get_install_scoped_resource_path(
        _WEIGHT_REL,
        packaged_relative_path=get_packaged_model_relative_path(_WEIGHT_REL)))


def _load_model(device):
    """懒加载 SVDLUT 网络并载入权重,按设备缓存单例 / lazy-load singleton."""
    global _MODEL, _MODEL_DEVICE
    if _MODEL is not None and _MODEL_DEVICE == str(device):
        return _MODEL
    from core.enhance.nets.svdlut_net import SVDLUTNet  # noqa: PLC0415
    model = SVDLUTNet()
    # weights_only=True:仅反序列化张量,杜绝 pickle 任意代码执行 / avoid RCE on load
    state = torch.load(_weight_path(), map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("state_dict", state), strict=False)
    model.eval().to(device)
    _MODEL, _MODEL_DEVICE = model, str(device)
    return model


def _apply_lut(img_rgb: np.ndarray, model, device) -> np.ndarray:
    """对整图跑 SVDLUT,返回满调色 RGB uint8 / full-graded RGB uint8."""
    x = torch.from_numpy(img_rgb).float().div(255.0).permute(2, 0, 1).unsqueeze(0).to(device)
    with torch.no_grad():
        y = model(x)
    if isinstance(y, (tuple, list)):
        y = y[0]
    y = y.clamp(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()
    return (y * 255.0 + 0.5).astype(np.uint8)


def colorize(img_rgb: np.ndarray, strength: float, device=None) -> np.ndarray:
    """
    自动调色,按 strength 在 [原图, 满调色] 间线性混合。

    参数 / Parameters:
        img_rgb (np.ndarray): RGB uint8 ndarray。
        strength (float): 0..1;0 返回原图。
        device: torch 设备;None=get_best_device()。

    返回 / Returns:
        np.ndarray: 调色后 RGB uint8 ndarray。
    """
    if strength <= 0.0:
        return img_rgb
    if device is None:
        device = get_best_device()
    model = _load_model(device)
    graded = _apply_lut(img_rgb, model, device)
    s = float(min(max(strength, 0.0), 1.0))
    blended = (1.0 - s) * img_rgb.astype(np.float32) + s * graded.astype(np.float32)
    return (blended + 0.5).astype(np.uint8)
