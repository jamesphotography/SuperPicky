# -*- coding: utf-8 -*-
"""
修图链路编排 / Enhance pipeline orchestration.

固定顺序 denoise→color;纯函数,不碰文件 I/O,不依赖 Qt。
denoise_fn/color_fn 可注入(测试 / 解耦);为 None 时懒加载真实封装。

Fixed order denoise→color; pure function, no file I/O, no Qt dependency.
denoise_fn/color_fn are injectable (for tests/decoupling); when None the real
wrappers are lazily imported.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from core.enhance.options import EnhanceOptions


def enhance(
    img_rgb: np.ndarray,
    opts: EnhanceOptions,
    *,
    denoise_fn: Optional[Callable] = None,
    color_fn: Optional[Callable] = None,
    device=None,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> np.ndarray:
    """
    按 denoise→color 链路修图,返回新 RGB ndarray(uint8, HxWx3)。

    参数 / Parameters:
        img_rgb (np.ndarray): 输入 RGB ndarray(uint8)。
        opts (EnhanceOptions): 修图选项。
        denoise_fn (Callable): (img, strength, device, progress_cb) -> img;
                               None 时懒加载 SCUNet 封装。
        color_fn (Callable): (img, strength, device) -> img;None 时懒加载 SVDLUT 封装。
        device: torch 设备;None 时由各封装用 config.get_best_device()。
        progress_cb (Callable): 进度回调 0..1(传给降噪 tiling)。

    返回 / Returns:
        np.ndarray: 修图后 RGB(uint8)。

    This applies denoise then color and returns a new RGB uint8 array.
    """
    out = img_rgb
    if opts.denoise_on and opts.denoise_strength > 0.0:
        fn = denoise_fn
        if fn is None:
            from core.enhance.models.scunet import denoise as fn  # noqa: PLC0415
        out = fn(out, opts.denoise_strength, device, progress_cb=progress_cb)
    if opts.color_on:
        fn = color_fn
        if fn is None:
            from core.enhance.models.svdlut import colorize as fn  # noqa: PLC0415
        out = fn(out, opts.color_strength, device)
    return out
