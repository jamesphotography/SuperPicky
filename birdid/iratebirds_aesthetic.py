# -*- coding: utf-8 -*-
"""
iRateBird 鸟种美学指数纯变换函数（无 IO/Qt 依赖，便于单测）。

数据来源: Santangeli et al. 2023, Scientific Data (s41597-023-02169-0),
CC-BY 4.0。物种颜值 1–10 众包评分，本模块负责归一化与雌雄二态取值。

Pure transforms for the iRateBird species aesthetic index (no IO/Qt deps).
"""
from typing import Optional


def normalize_score(raw_1_10: Optional[float]) -> Optional[float]:
    """
    把 1–10 原始颜值分归一化到 0–100（与罕见度 UI 同量纲）。

    参数:
    raw_1_10 (Optional[float]): iRateBird full_model 原始分（1–10）

    返回:
    Optional[float]: 0–100 分，保留 1 位小数；输入 None 返 None

    Normalize a 1–10 raw score to 0–100 (same scale as the rarity UI).
    """
    if raw_1_10 is None:
        return None
    return round((raw_1_10 - 1.0) / 9.0 * 100.0, 1)


def derive_default_score(
    species_100: Optional[float],
    male_100: Optional[float],
    female_100: Optional[float],
) -> Optional[float]:
    """
    计算展示用默认颜值分：二态种取 max(雄,雌)（该种最佳颜值），
    无雌雄分则回退物种级分。

    参数:
    species_100 (Optional[float]): 物种级归一化分（0–100）
    male_100 (Optional[float]): 雄鸟归一化分，无则 None
    female_100 (Optional[float]): 雌鸟归一化分，无则 None

    返回:
    Optional[float]: 默认展示分（0–100）；全 None 返 None

    Default display score: max(male, female) for dichromatic species (the
    species' best-case beauty), else fall back to the species-level score.
    """
    sexes = [s for s in (male_100, female_100) if s is not None]
    if sexes:
        return max(sexes)
    return species_100


def is_dimorphic(male_100: Optional[float], female_100: Optional[float]) -> int:
    """
    是否雌雄二态（sex-level 数据同时含雄与雌分）。

    参数:
    male_100 (Optional[float]): 雄鸟分
    female_100 (Optional[float]): 雌鸟分

    返回:
    int: 1=雌雄均有分, 0=否

    Whether the species is sexually dichromatic (both male and female
    scores present in the sex-level data).
    """
    return 1 if (male_100 is not None and female_100 is not None) else 0
