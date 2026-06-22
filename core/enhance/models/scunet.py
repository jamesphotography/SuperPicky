# -*- coding: utf-8 -*-
"""
SCUNet 盲降噪封装 / SCUNet blind-denoise wrapper.

懒加载单例;denoise() 按 strength 在 [原图, 满降噪] 间线性混合。
大图 tiling(TILE=512, OVERLAP=32)控显存;MPS/CPU 用 fp32。
权重:models/scunet_color_real.pth(HF 下载)。

Lazy-loaded singleton; denoise() linearly blends original and fully-denoised by
strength. Large images use overlap-tiling to bound memory. fp32 on MPS/CPU.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import torch

from config import (get_best_device, get_install_scoped_resource_path,
                    get_packaged_model_relative_path)

_MODEL = None  # 单例缓存(按设备) / singleton cache (per device)
_MODEL_DEVICE = None
_WEIGHT_REL = "models/scunet_color_real.pth"
TILE = 512
OVERLAP = 32


def _weight_path() -> str:
    """返回权重绝对路径(兼容打包/开发) / absolute weight path."""
    return str(get_install_scoped_resource_path(
        _WEIGHT_REL,
        packaged_relative_path=get_packaged_model_relative_path(_WEIGHT_REL)))


def _load_model(device):
    """懒加载 SCUNet 并载入权重,按设备缓存单例 / lazy-load singleton."""
    global _MODEL, _MODEL_DEVICE
    if _MODEL is not None and _MODEL_DEVICE == str(device):
        return _MODEL
    from core.enhance.nets.scunet_net import SCUNet  # noqa: PLC0415
    model = SCUNet(in_nc=3, config=[4, 4, 4, 4, 4, 4, 4], dim=64)
    # weights_only=True:仅反序列化张量,杜绝 pickle 任意代码执行 / avoid RCE on load
    state = torch.load(_weight_path(), map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("state_dict", state), strict=True)
    model.eval().to(device)
    _MODEL, _MODEL_DEVICE = model, str(device)
    return model


def _infer_tensor(x: torch.Tensor, model, device) -> torch.Tensor:
    """对单块 NCHW 张量推理并搬回 CPU / infer one tile, move back to CPU."""
    with torch.no_grad():
        return model(x.to(device)).clamp(0, 1).cpu()


def _tiled_infer(img_rgb: np.ndarray, model, device,
                 progress_cb: Optional[Callable[[float], None]]) -> np.ndarray:
    """
    按瓦片+重叠跑模型并拼接(重叠区平均),返回满降噪 RGB uint8。
    Run the model tile-by-tile with overlap and average-blend seams.
    """
    h, w = img_rgb.shape[:2]
    x = torch.from_numpy(img_rgb).float().div(255.0).permute(2, 0, 1).unsqueeze(0)
    if h <= TILE and w <= TILE:
        y = _infer_tensor(x, model, device)
        out = y.squeeze(0).permute(1, 2, 0).numpy()
        return (out * 255.0 + 0.5).astype(np.uint8)

    step = TILE - OVERLAP
    acc = np.zeros((h, w, 3), np.float32)
    wgt = np.zeros((h, w, 1), np.float32)
    ys = list(range(0, max(h - TILE, 0) + 1, step)) or [0]
    xs = list(range(0, max(w - TILE, 0) + 1, step)) or [0]
    if ys[-1] != max(h - TILE, 0):
        ys.append(max(h - TILE, 0))
    if xs[-1] != max(w - TILE, 0):
        xs.append(max(w - TILE, 0))
    total = len(ys) * len(xs)
    done = 0
    for ty in ys:
        for tx in xs:
            th = min(TILE, h - ty)
            tw = min(TILE, w - tx)
            tile = x[:, :, ty:ty + th, tx:tx + tw]
            y = _infer_tensor(tile, model, device)
            patch = y.squeeze(0).permute(1, 2, 0).numpy()
            acc[ty:ty + th, tx:tx + tw, :] += patch
            wgt[ty:ty + th, tx:tx + tw, :] += 1.0
            done += 1
            if progress_cb is not None:
                progress_cb(done / total)
    out = acc / np.maximum(wgt, 1e-6)
    return (out * 255.0 + 0.5).astype(np.uint8)


def denoise(img_rgb: np.ndarray, strength: float, device=None, *,
            progress_cb: Optional[Callable[[float], None]] = None) -> np.ndarray:
    """
    盲降噪,按 strength 在 [原图, 满降噪] 间线性混合。

    参数 / Parameters:
        img_rgb (np.ndarray): RGB uint8 ndarray。
        strength (float): 0..1;0 返回原图。
        device: torch 设备;None=get_best_device()。
        progress_cb (Callable): 瓦片进度回调 0..1。

    返回 / Returns:
        np.ndarray: 降噪后 RGB uint8 ndarray。
    """
    if strength <= 0.0:
        return img_rgb
    if device is None:
        device = get_best_device()
    model = _load_model(device)
    full = _tiled_infer(img_rgb, model, device, progress_cb)
    s = float(min(max(strength, 0.0), 1.0))
    blended = (1.0 - s) * img_rgb.astype(np.float32) + s * full.astype(np.float32)
    return (blended + 0.5).astype(np.uint8)
