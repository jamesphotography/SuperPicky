# -*- coding: utf-8 -*-
"""
ISO 锐度折算：高 ISO 照片的噪点会抬高梯度锐度，评星前按 ISO 打折。

ISO sharpness normalization: high-ISO noise inflates gradient sharpness, so
sharpness is discounted by ISO before rating.

评星（处理流程）与界面显示（详情面板、排序、识鸟面板、导出报告）都用这里的同一个
公式，保证用户在各处看到的「头部锐度」就是评星实际使用的数值，与题注一致。
数据库只存原始头部锐度（head_sharp）和 ISO（iso），显示时据此还原。

Rating and every display (detail panel, sorting, Bird ID panel, exported
report) share this formula, so the "head sharpness" users see is exactly the
value the rating used — matching the caption. The DB stores raw head_sharp and
iso; displays recompute the normalized value from them.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

ISO_BASE = 800             # 基准 ISO（此值及以下不折算）/ no discount at or below
ISO_PENALTY_FACTOR = 0.05  # 每翻一倍 ISO 扣 5% / 5% per doubling
ISO_MIN_FACTOR = 0.5       # 最低系数（最多扣 50%）/ floor of the factor


def iso_sharpness_factor(iso_value: Optional[float]) -> float:
    """
    计算 ISO 锐度折算系数。

    基于对数衰减：每翻一倍 ISO 扣 5%，例如 ISO 800 = 1.0、1600 = 0.95、3200 = 0.90。

    参数:
    iso_value (Optional[float]): ISO 值；None 或不大于基准时不折算

    返回:
    float: 折算系数（0.5 - 1.0）

    Log-decay factor: minus 5% per ISO doubling above ISO 800, floored at 0.5.
    """
    if iso_value is None or iso_value <= ISO_BASE:
        return 1.0
    penalty = ISO_PENALTY_FACTOR * math.log2(iso_value / ISO_BASE)
    return max(ISO_MIN_FACTOR, 1.0 - penalty)


def display_head_sharpness(photo: Mapping[str, Any]) -> Optional[float]:
    """
    从 report.db 记录还原评星使用的头部锐度（原始头部锐度 × ISO 折算系数）。

    参数:
    photo (Mapping[str, Any]): report.db 的一行（需含 head_sharp，iso 可缺）

    返回:
    Optional[float]: 折算后的头部锐度；没有头部锐度时为 None

    Normalized head sharpness (raw head_sharp × ISO factor), or None.
    """
    raw = photo.get("head_sharp")
    if raw is None:
        return None
    try:
        raw = float(raw)
        iso = photo.get("iso")
        return raw * iso_sharpness_factor(float(iso) if iso not in (None, "") else None)
    except (TypeError, ValueError):
        return None


def display_aesthetic(photo: Mapping[str, Any]) -> Optional[float]:
    """
    评星使用的美学分（TOPIQ 原始分，未乘对焦/飞鸟系数），与题注一致。

    参数:
    photo (Mapping[str, Any]): report.db 的一行

    返回:
    Optional[float]: 美学分；缺失时为 None

    The raw aesthetics score used for rating (no focus/flight multipliers).
    """
    v = photo.get("nima_score")
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
