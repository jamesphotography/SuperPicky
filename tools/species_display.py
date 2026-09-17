# -*- coding: utf-8 -*-
"""
鸟种显示文字 / Species display text.

结果浏览器的缩略图与详情面板共用：确认鸟种优先；没有确认鸟种、但识鸟给出了低于阈值的
候选鸟种时，显示「鸟名（待确定 N%）」，帮助用户在「其他鸟类」里快速判断大概是什么鸟。
候选只用于显示与 EXIF 标题提示，不参与分目录、报告鸟种名录与 eBird 导出。

Shared by the results browser tiles and detail panel: a confirmed species wins;
otherwise a below-threshold Bird ID candidate renders as "name (unconfirmed N%)"
so users can tell roughly what an "Other birds" photo shows. Candidates are for
display and the EXIF title hint only; they never drive folders, the report's
species list, or eBird export.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

# 待确定候选的最低显示置信度（用户 2026-09-17 拍板）。历史 421 个候选中位数仅 24%，
# 三分之一低于 10%，基本是噪声；低于该值的候选不显示、不写 EXIF 标题。
# Minimum confidence for showing an unconfirmed candidate (decided 2026-09-17).
# Historical candidates have a 24% median and a third fall below 10%, i.e. noise.
UNCONFIRMED_SPECIES_MIN_CONFIDENCE = 30.0


def _pick(cn: Optional[str], en: Optional[str], is_en: bool) -> str:
    """
    按界面语言取名并回退另一语言 / Pick a name by UI language with fallback.

    参数 / Parameters:
        cn (Optional[str]): 中文名 / Chinese name.
        en (Optional[str]): 英文名 / English name.
        is_en (bool): 界面是否英文 / Whether the UI is English.

    返回 / Returns:
        str: 名称，可能为空串 / Name, possibly empty.
    """
    cn = (cn or "").strip()
    en = (en or "").strip()
    return (en or cn) if is_en else (cn or en)


def species_display_text(
    photo: Mapping[str, object],
    is_en: bool,
    format_unconfirmed: Callable[[str, str], str],
) -> str:
    """
    生成照片的鸟种显示文字 / Build a photo's species display text.

    参数 / Parameters:
        photo (Mapping): 照片记录，读取 bird_species_cn/en 与 alt_species_cn/en、alt_confidence /
            Photo record.
        is_en (bool): 界面是否英文 / Whether the UI is English.
        format_unconfirmed (Callable[[str, str], str]): (鸟名, 取整置信度字符串) → 显示文字，
            通常是 i18n 的 birdid.species_unconfirmed / Formatter for candidates.

    返回 / Returns:
        str: 确认鸟种名、候选显示文字，或空串（无候选或候选置信度低于
            UNCONFIRMED_SPECIES_MIN_CONFIDENCE）/ Confirmed name, candidate text, or ""
            (no candidate, or below UNCONFIRMED_SPECIES_MIN_CONFIDENCE).
    """
    confirmed = _pick(photo.get("bird_species_cn"), photo.get("bird_species_en"), is_en)
    if confirmed:
        return confirmed
    candidate = _pick(photo.get("alt_species_cn"), photo.get("alt_species_en"), is_en)
    confidence = photo.get("alt_confidence")
    if not candidate or confidence is None:
        return ""
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return ""
    if value < UNCONFIRMED_SPECIES_MIN_CONFIDENCE:
        return ""
    return format_unconfirmed(candidate, f"{value:.0f}")
