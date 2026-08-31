#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分类目录布局共享模块 / Shared folder layout helper.

把「先按星级再按鸟种」（rating-first）和「先按鸟种再按星级」（species-first）
两种布局抽到一个函数里，photo_processor 移动文件和创建 burst 目录两处都
调用同一份逻辑，避免 if/elif 在多处分叉。

Centralizes path layout policy so both single-photo moves and burst-group
directory creation in photo_processor go through the same code path,
avoiding parallel if/elif branches scattered across the file.
"""

from __future__ import annotations

import os
from typing import Optional

from constants import get_rating_folder_name

# 布局策略 / Layout strategy identifiers
LAYOUT_RATING_FIRST = "rating-first"
LAYOUT_SPECIES_FIRST = "species-first"
# V4.6(Paul P1): 平铺——识别评分但不移动文件(Lightroom 友好);
# organize 阶段整体跳过,此常量供 GUI 判断 organize_files 传值。
# V4.6 (Paul P1): flat — rate in place, no file moves (Lightroom-friendly).
# The organize stage is skipped entirely; GUI uses this to derive
# the organize_files argument.
LAYOUT_FLAT = "flat"
# V4.3 Phase 4: 默认改 species-first 以兼容视频集成
# 视频不分星，species-first 让视频和照片自然共享同一个鸟种目录
# V4.3 Phase 4: default switched to species-first so videos (no star rating)
# can naturally live alongside photos in the same species folder.
DEFAULT_LAYOUT = LAYOUT_SPECIES_FIRST

VALID_LAYOUTS = {LAYOUT_RATING_FIRST, LAYOUT_SPECIES_FIRST, LAYOUT_FLAT}


def compute_target_folder(
    rating: int,
    bird_name: Optional[str],
    layout: str = DEFAULT_LAYOUT,
    other_birds_label: str = "其他鸟类",
) -> str:
    """
    计算分类后的相对目录路径（不含 burst_xxx 部分）。

    Args:
        rating: 星级（-1/0/1/2/3 自动评分；4/5 为浏览器内手动升星）
        bird_name: 鸟种名（None 或空字符串表示未识别/低置信度）
        layout: LAYOUT_RATING_FIRST / LAYOUT_SPECIES_FIRST / LAYOUT_FLAT
        other_birds_label: 「其他鸟类」目录的本地化名称

    Returns:
        相对路径，例如：
          - rating-first  + 白胸鸲鹟      →  "3星_优选/白胸鸲鹟"
          - species-first + 白胸鸲鹟      →  "白胸鸲鹟/3星_优选"
          - rating-first  + 未识别 / 低星 →  "3星_优选/其他鸟类"
          - species-first + 未识别 / 低星 →  "其他鸟类/3星_优选"
          - 任何 layout + 1星              →  "[layout 决定]/其他鸟类"
          - flat + 任意                    →  ""（留在根目录,不分目录）

    Rules:
    - 1星 / 0星 / -1星 永远走「其他鸟类」分支（即使识别到鸟种）
      原因：低星照片本身已被标记为低质量/废片，按鸟种分散反而碎片化；
      统一走「其他鸟类」保持目录视觉一致性，方便整体快速处理。
    - 2星及以上（含手动升的 4/5 星）+ 鸟种识别 → 按 layout 决定 rating 和
      species 谁外层
    - 2星及以上 + 未识别 → 走「其他鸟类」分支，仍按 layout 切换内外
    """
    # 平铺:不分目录,留在根(防御性——organize gate 下正常不会走到这里)
    # Flat: no subfolders, stay in root (defensive; the organize gate
    # normally prevents this from being called at all).
    if layout == LAYOUT_FLAT:
        return ""

    base = get_rating_folder_name(rating)

    # 低星 (< 2) 强制走「其他鸟类」分支，即使有 bird_name 也忽略
    # Low stars are always lumped into "Other Birds" — even if a species
    # was detected, the photo itself is already flagged as low-quality so
    # splitting by species would only fragment the trash pile.
    if rating < 2:
        bird_name = None

    species = (bird_name or "").strip() or other_birds_label

    if layout == LAYOUT_SPECIES_FIRST:
        return os.path.join(species, base)
    # 默认 rating-first / Default: rating-first
    return os.path.join(base, species)


def normalize_layout(value: Optional[str]) -> str:
    """
    把外部传入的 layout 值规范化为合法选项；无效或 None 返回默认值。

    Coerce any external layout value into a valid identifier, falling back
    to the default when input is missing or unknown.
    """
    if value in VALID_LAYOUTS:
        return value
    return DEFAULT_LAYOUT
