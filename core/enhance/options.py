# -*- coding: utf-8 -*-
"""
修图选项数据类 / Enhance options dataclass.

承载自动修图各步骤的开关与强度,跨 UI/管线/导出共享。
Carries on/off switches and strengths for each enhance step, shared across
the UI, pipeline, and export paths.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EnhanceOptions:
    """
    自动修图选项 / Auto-enhance options.

    属性 / Attributes:
        denoise_on (bool):       是否启用降噪 / enable SCUNet denoise.
        denoise_strength (float): 0..1;0 表示短路跳过推理 / 0 short-circuits inference.
        color_on (bool):         是否启用调色 / enable SVDLUT color.
        color_strength (float):  0..1 调色混合强度 / color blend strength.
    """

    denoise_on: bool = True
    denoise_strength: float = 0.5
    color_on: bool = True
    color_strength: float = 0.4
