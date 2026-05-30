#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GBIF 罕见度 0-100 分 → 5 级 tier 映射 / GBIF rarity score → 5-tier mapping.

5 个 tier 用 Unicode Geometric Shapes 字符表达「圆形充填进度」：
○ (空) → ◔ (1/4) → ◑ (1/2) → ◕ (3/4) → ● (满)
对应 5 档观察难度：常见 / 偶见 / 少见 / 罕见 / 传奇。

Five tiers expressed via Unicode Geometric Shapes — empty ring, quarter,
half, three-quarter, full — mapped to a photographer-friendly difficulty
scale: Common / Occasional / Uncommon / Rare / Legendary.

阈值 / Thresholds（按 GBIF 0-100 分均匀切 20 分一档）
  [0, 20)  → 常见
  [20, 40) → 偶见
  [40, 60) → 少见
  [60, 80) → 罕见
  [80, 100] → 传奇
"""

from __future__ import annotations

from typing import Optional


# 5 个 Unicode 圆形充填字符（U+25CB / U+25D4 / U+25D1 / U+25D5 / U+25CF）
# Five Unicode "Geometric Shapes" circle glyphs representing fill levels.
TIER_ICONS = ["○", "◔", "◑", "◕", "●"]

# 中文 tier 名（摄影师视角的观察难度）
# Chinese tier names — photographer-centric observation difficulty.
TIER_NAMES_ZH = ["常见", "偶见", "少见", "罕见", "传奇"]

# 英文 tier 名
# English tier names.
TIER_NAMES_EN = ["Common", "Occasional", "Uncommon", "Rare", "Legendary"]

# 5 个 tier 颜色（从灰→绿→黄→橙→红，与 Focus / IUCN 配色体系一致）
# Tier colors going gray → green → yellow → orange → red, harmonizing with
# the existing Focus and IUCN palettes.
TIER_COLORS = [
    "#9CA3AF",  # 灰 / gray  — 常见
    "#60C659",  # 绿 / green — 偶见
    "#F9E814",  # 黄 / yellow — 少见
    "#FC7F3F",  # 橙 / orange — 罕见
    "#D81E05",  # 红 / red   — 传奇
]

# 4 个内部边界（左闭右开），第 5 档是 [80, +∞)。
# Four inner boundaries (left-inclusive, right-exclusive); the 5th tier
# is [80, +infinity).
_TIER_THRESHOLDS = [20.0, 40.0, 60.0, 80.0]


def gbif_score_to_tier(score: Optional[float]) -> Optional[int]:
    """
    GBIF 0-100 分 → tier 索引 0..4。

    Args:
        score: GBIF 罕见度分数（0-100），None 时返回 None。

    Returns:
        tier 索引 (0=常见, 4=传奇)，score 为 None 时返回 None。

    Map a GBIF 0-100 rarity score to a 0-4 tier index. Returns None when
    the score is unavailable so callers can render a placeholder.
    """
    if score is None:
        return None
    for i, threshold in enumerate(_TIER_THRESHOLDS):
        if score < threshold:
            return i
    return 4


def tier_name(tier_index: Optional[int], is_zh: bool = True) -> str:
    """
    tier 索引 → 中/英文 tier 名。

    Returns the tier label for the given index, in Chinese (is_zh=True)
    or English. Returns "—" when tier_index is None.
    """
    if tier_index is None or not (0 <= tier_index < 5):
        return "—"
    return (TIER_NAMES_ZH if is_zh else TIER_NAMES_EN)[tier_index]


def tier_icon(tier_index: Optional[int]) -> str:
    """tier 索引 → Unicode 圆形充填字符 / Returns the circle glyph."""
    if tier_index is None or not (0 <= tier_index < 5):
        return ""
    return TIER_ICONS[tier_index]


def tier_color(tier_index: Optional[int]) -> Optional[str]:
    """tier 索引 → hex 颜色 / Returns the hex color for the tier."""
    if tier_index is None or not (0 <= tier_index < 5):
        return None
    return TIER_COLORS[tier_index]
