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

阈值 / Thresholds（非均匀切分，让「常见」档紧贴真街边鸟）
  [0, 8)   → 常见  (~7%)   真街边鸟（家麻雀/麻雀/白头鹎/戴胜级）
  [8, 25)  → 能见  (~17%)  努力能见到（澳洲花园鸟/中国湿地鸟级）
  [25, 50) → 少见  (~25%)  专程找
  [50, 75) → 罕见  (~25%)  远途+耐心
  [75, 100]→ 传奇  (~26%)  一生一遇
"""

from __future__ import annotations

from typing import Optional


# 5 个 Unicode 圆形充填字符（U+25CB / U+25D4 / U+25D1 / U+25D5 / U+25CF）
# Five Unicode "Geometric Shapes" circle glyphs representing fill levels.
TIER_ICONS = ["○", "◔", "◑", "◕", "●"]

# 中文 tier 名（摄影师视角的观察难度）
# Chinese tier names — photographer-centric observation difficulty.
# 注：第 2 档用「能见」而非「偶见」，避免和第 3 档「少见」产生语义歧义
# Note: tier 2 uses 「能见」("findable") to avoid Chinese semantic
# collision with tier 3「少见」(both could read as "sometimes seen").
TIER_NAMES_ZH = ["常见", "能见", "少见", "罕见", "传奇"]

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

# 4 个内部边界（左闭右开），第 5 档是 [75, +∞)。
# Four inner boundaries (left-inclusive, right-exclusive); the 5th tier
# is [75, +infinity).
# 非均匀切分：让「常见」档收紧到真街边鸟（前 ~7%），避免和澳洲花园鸟等
# 「中国出片但本地常见」鸟混档。Non-uniform thresholds collapse the
# "Common" bucket to true everyday birds (~top 7%), preventing collision
# with regionally-common species that feel rare to off-region photographers.
_TIER_THRESHOLDS = [8.0, 25.0, 50.0, 75.0]


# 手动 override 表 / Manual override table
#
# 对极少数 GBIF 算法明显失真的鸟，按学名做硬编码降级。
# 这些鸟通常是「分类学新拆分 / 区域偏远 / 学名变更」导致 GBIF CC0+CC-BY
# 子集里 count 极低，被百分位算法误判为「传奇」。
# 每条 override 必须附理由 + 提议人 + 日期，方便审计与回滚。
#
# Hardcoded overrides for the handful of species where GBIF's count-based
# percentile clearly mis-ranks them (taxonomic splits, remote regions,
# license filter cutoffs). Each entry must carry a rationale + proposer +
# date so the table stays auditable and reversible.
HARDCODE_OVERRIDES = {
    # 学名 → (override 分数 0-100, 理由)
    # scientific name → (override score 0-100, reason)
    "Quoyornis georgianus": (
        40.0,
        "白胸鸲鹟 — 2020 年从 Eopsaltria 拆出新属，GBIF backbone 仍按旧学名"
        "索引大量记录；西澳森林局部常见但全球 cc 子集 count=183 → 误判为传奇。"
        "Recently split from Eopsaltria; majority of records still indexed "
        "under the old binomial. Locally common in SW Australia. "
        "[james 2026-05-30]",
    ),
    "Butorides sundevalli": (
        12.0,
        "加岛绿鹭 — 加拉帕戈斯特有亚种近年提为独立种，GBIF 数据稀少 (count=319)"
        "但岛上观鸟者几乎必见。懂鸟 4.57 印证其实际相当容易遇到。"
        "Recently elevated Galapagos subspecies; sparse GBIF data but "
        "trivially encountered on any Galapagos visit. "
        "[james 2026-05-30]",
    ),
    "Pyrocephalus obscurus": (
        18.0,
        "朱红霸鹟 — 2016 年从 Pyrocephalus rubinus 拆出，原属拆分后 GBIF 数据"
        "尚未完全归位；实为美洲西海岸常见鸟。懂鸟 6.64 印证不属传奇级。"
        "Recently split from P. rubinus; under-indexed in GBIF backbone but "
        "common along American west coast. [james 2026-05-30]",
    ),
    "Pteroglossus erythropygius": (
        20.0,
        "褐嘴/淡嘴簇舌巨嘴鸟 — 厄瓜多尔/哥伦比亚低地森林相对常见；CC0+CC-BY 子集"
        "记录稀少 (count=152) 但当地观鸟项目几乎必中。懂鸟 7.22 偏稀少端。"
        "Common in Ecuador / W Colombia lowland forest; sparse cc-licensed "
        "GBIF data but reliable on local birding trips. [james 2026-05-30]",
    ),
}


def get_score_override(scientific_name: Optional[str]) -> Optional[float]:
    """
    检查学名是否在手动 override 表里。命中返回 override 分数，否则返回 None。

    Return the manually-overridden score for a scientific name, or None
    if the species is not in the override table.
    """
    if not scientific_name:
        return None
    entry = HARDCODE_OVERRIDES.get(scientific_name.strip())
    if entry is None:
        return None
    return float(entry[0])


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
