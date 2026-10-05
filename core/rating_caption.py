# -*- coding: utf-8 -*-
"""
评分说明（题注）生成：把一张照片的评星依据写成用户看得懂的几行文字。

Rating caption builder: turn the facts behind a photo's star rating into a few
lines a photographer can read.

同一段文字既写进照片元数据的题注（Lightroom「题注」/ XMP:Description），也存进
report.db 的 caption 列，在结果浏览器的「选片备注」里显示——两处内容一致。

格式（用户最关心「为什么是这个星级」，所以先回答它）：
- 第 1 行结论：星级 + 档位名（优选 / 良好 / 普通 …）；
- 第 2 行「为什么」：排名照片写出组内名次和各星级的名额分界（前 N 张 3★、第 a–b 张 2★），
  没拿到 3★ 的写明卡在哪条规则；被硬门槛挡下的写出具体数值与最低要求；
- 排名照片再加一行「排名靠什么」（锐度/美学权重与加减分项）；
- 逐项依据：✓ 加分项 / ✗ 扣分项 / · 中性信息，数字后注明参照（本批位置、最低要求）；
- 识别信息：有鸟置信度（YOLO 检测）、鸟种识别置信度（OSEA 模型）；识别置信度不够时说明如何处理。
ISO 本身不是打分依据，不单独成行；只在 ISO 折算明显压低了锐度时，在锐度行注明。

鸟种、IUCN 不写进题注：Lightroom 已有标题字段、浏览器有单独的鸟种行；而鸟种
可在浏览器里修改，写进题注会变成过时信息。

本模块是纯函数（无 Qt / IO），文案全部经调用方传入的 t()（i18n）取得。

The same text goes to the photo's caption (XMP:Description) and to report.db,
where the browser shows it as the culling note. Line 1 is the verdict, then one
line per factor (✓ plus / ✗ minus / · neutral) with reference points, then a
footer. Pure functions; all wording comes from the caller's t().
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, List, Optional

# 手动修改星级/标记无鸟时插入的首行以此开头，与界面语言无关，便于再次修改时识别并替换
# Prefix of the manual-override line; language independent so it can be found again
MANUAL_MARK = "✎"

# reason_key → 结果类型 / reason_key → outcome
_OUTCOME_BY_REASON = {
    "rating_engine.reject_no_bird": "no_bird",
    "rating_engine.low_confidence": "low_confidence",
    "rating_engine.angle_poor": "angle_poor",
    "rating_engine.low_sharpness": "low_sharpness",
    "rating_engine.low_aesthetics": "low_aesthetics",
    "rating_v2.top_quota": "top",
    "rating_v2.floor_capped": "floor_capped",
    "rating_v2.mid_quota": "mid",
    "rating_v2.rest_quota": "rest",
    "rating_v2.eye_capped": "eye_capped",
    "rating_v2.burst_capped": "burst_capped",
}

# 进入 V2 排序池的结果类型（有组内名次与本批百分位）/ outcomes ranked in the V2 pool
RANKED_OUTCOMES = {"top", "floor_capped", "mid", "rest", "eye_capped", "burst_capped"}

# 眼睛「看得清」的可见度门槛，与 V2 的 2★ 封顶门槛一致 / eye-visible threshold (= V2 eye cap)
EYE_CLEAR = 0.5

# 本批排序池至少这么多张，才写「本批前/后 X%」并据此打 ✓/✗；只有几张时百分位没有意义
# （1 张照片会被写成「本批后 1%」）。/ Below this pool size percentiles are meaningless.
MIN_POOL_FOR_PERCENTILE = 10


def outcome_from_reason_key(reason_key: str) -> str:
    """
    把评星原因键映射为题注的结果类型。

    参数:
    reason_key (str): core.rating_quota / RatingEngine 给出的 i18n 原因键

    返回:
    str: 结果类型；无法识别时返回 "v1"（按旧版原因文字原样输出）

    Map a rating reason key to a caption outcome ("v1" when unknown).
    """
    return _OUTCOME_BY_REASON.get(reason_key or "", "v1")


@dataclass
class CaptionFacts:
    """
    生成题注所需的全部事实（取不到的字段留 None，对应行自动省略）。

    Everything the caption needs; missing fields are None and their lines are skipped.
    """

    rating: int                                  # 最终星级 -1..3 / final stars
    outcome: str = "v1"                          # 见 outcome_from_reason_key
    v1_reason: Optional[str] = None              # 旧版评星引擎的原因文字（outcome="v1" 时用）
    confidence: Optional[float] = None           # AI 认鸟把握 0-1
    confidence_threshold: Optional[float] = None  # 用户设置的把握门槛 0-1
    head_sharp: Optional[float] = None           # 头部锐度原始值（ISO 折算前）
    norm_sharpness: Optional[float] = None       # ISO 折算后的头部锐度（评星实际使用）
    sharp_gate: Optional[float] = None           # 锐度硬门槛（0★ 下限）
    topiq: Optional[float] = None                # 美感分（TOPIQ，约 1-10）
    topiq_gate: Optional[float] = None           # 美感硬门槛
    best_eye: Optional[float] = None             # 双眼最高可见度 0-1
    focus_status: Optional[str] = None           # BEST / GOOD / BAD / WORST
    focus_weight: Optional[float] = None         # 对焦锐度权重（区分合焦的几种来源）
    focus_arbitrated: bool = False               # 是否经「鸟头实测清晰」仲裁升为合焦
    is_flying: bool = False
    over_exposed: bool = False
    under_exposed: bool = False
    iso: Optional[int] = None
    # —— V2 排序信息（仅排序池照片）/ V2 ranking (pool photos only) ——
    rank: Optional[int] = None                   # 组内名次（1 = 最好）
    group_size: Optional[int] = None             # 组内张数
    species_known: bool = False                  # 组是否按鸟种划分
    pct_sharp: Optional[float] = None            # 锐度在本批排序池中的百分位 0-1
    pct_topiq: Optional[float] = None            # 美感在本批排序池中的百分位 0-1
    pool_size: Optional[int] = None              # 本批排序池张数（太少时不写百分位）
    quota3: Optional[float] = None               # 3★ 配额 %
    quota2: Optional[float] = None               # 2★ 配额 %
    sharp_floor3: Optional[float] = None         # 3★ 锐度兜底
    burst_cap: Optional[int] = None              # 连拍组 3★ 上限
    weight_sharp: Optional[float] = None         # 综合分中锐度权重（0-1）
    weight_topiq: Optional[float] = None         # 综合分中美感权重（0-1）
    picked: bool = False                         # 是否「精选」
    # —— 鸟种识别（仅排序池照片：识鸟结果在收尾定星时才齐）/ Bird ID (pool photos) ——
    birdid_enabled: bool = False                 # 是否开了自动识鸟
    species_confidence: Optional[float] = None   # 鸟种识别置信度 %（0-100）
    species_low_confidence: bool = False         # 识别置信度低于门槛（鸟种未确定）
    species_candidate: Optional[str] = None      # 未确定时最可能的鸟种名（按界面语言）
    species_threshold: Optional[float] = None    # 用户设置的识鸟置信度门槛 %


Translator = Callable[..., str]


def _stars(rating: int) -> str:
    """星级文字，如 3★ / 0★ / Star text."""
    return f"{max(rating, 0)}★"


def _position(pct: float, t: Translator) -> str:
    """
    把本批百分位写成「本批前 X%」或「本批后 X%」。

    参数:
    pct (float): 严格低于本值的照片占比（core.rating_quota._percentile_fn 的口径）
    t (Translator): i18n

    返回:
    str: 位置描述

    Describe a pool percentile as "top X%" / "bottom X%".
    """
    # 先 round 到 6 位再取整，避免 1-0.97=0.0300…03 这类浮点误差多进一位
    # Round first so float noise (1-0.97=0.0300…03) does not bump the integer
    if pct >= 0.5:
        return t("caption.pos_top", p=max(1, math.ceil(round((1.0 - pct) * 100, 6))))
    return t("caption.pos_bottom", p=min(100, math.floor(round(pct * 100, 6)) + 1))


def _verdict(f: CaptionFacts, t: Translator) -> str:
    """第 1 行结论：星级 + 档位名 / Line 1: stars and tier name."""
    if f.outcome == "no_bird" or f.rating < 0:
        return t("caption.verdict_no_bird")
    head = t(f"caption.verdict_{max(0, min(3, f.rating))}", stars=_stars(f.rating))
    if f.picked:
        head += t("caption.picked_suffix")
    return head


def _span(a: int, b: int, t: Translator) -> str:
    """名次区间文字：第 a 张 / 第 a–b 张 / Rank range text."""
    return t("caption.span_one", a=a) if a >= b else t("caption.span_many", a=a, b=b)


def _why(f: CaptionFacts, t: Translator) -> Optional[str]:
    """
    第 2 行「为什么」：直接回答为什么是这个星级。

    排名照片：写出组内名次与各星级名额分界（3★ = 组内前 ceil(n×配额3) 张，2★ = 其后
    ceil(n×配额2) 张，与 core.rating_quota.assign_ratings 的切法一致）。

    Line 2 answers "why this rating": rank plus the quota cut-offs for ranked
    photos (same slicing as assign_ratings), or the gate that stopped the photo.
    """
    o = f.outcome
    stars = _stars(f.rating)
    if o == "no_bird":
        return t("caption.why_no_bird")
    if o == "low_confidence" and f.confidence is not None and f.confidence_threshold is not None:
        return t("caption.why_low_confidence", stars=stars,
                 conf=f"{f.confidence:.0%}", thr=f"{f.confidence_threshold:.0%}")
    if o == "angle_poor":
        return t("caption.why_angle_poor", stars=stars)
    if o == "low_sharpness" and f.norm_sharpness is not None and f.sharp_gate is not None:
        return t("caption.why_low_sharpness", stars=stars,
                 val=f"{f.norm_sharpness:.0f}", thr=f"{f.sharp_gate:.0f}")
    if o == "low_aesthetics" and f.topiq is not None and f.topiq_gate is not None:
        return t("caption.why_low_aesthetics", stars=stars,
                 val=f"{f.topiq:.1f}", thr=f"{f.topiq_gate:.1f}")
    if (o in RANKED_OUTCOMES and f.rank is not None and f.group_size
            and f.quota3 is not None and f.quota2 is not None):
        n, rank = f.group_size, f.rank
        c3 = min(n, math.ceil(n * f.quota3 / 100.0))
        c2_end = min(n, c3 + math.ceil(n * f.quota2 / 100.0))
        if not f.birdid_enabled:
            group = t("caption.group_batch")
        elif f.species_known:
            group = t("caption.group_species")
        else:
            group = t("caption.group_unknown")
        place = t("caption.place", group=group, n=n, rank=rank)
        if o == "top":
            return t("caption.why_top", place=place, c3=c3)
        if o == "mid":
            return t("caption.why_mid", place=place, c3=c3, span=_span(c3 + 1, c2_end, t))
        if o == "rest":
            return t("caption.why_rest", place=place, c2=c2_end)
        if o == "floor_capped":
            return t("caption.why_floor_capped", place=place,
                     val=f"{(f.norm_sharpness or 0):.0f}", floor=f"{(f.sharp_floor3 or 0):.0f}")
        if o == "eye_capped":
            return t("caption.why_eye_capped", place=place)
        return t("caption.why_burst_capped", place=place, cap=f.burst_cap or 0)
    if f.v1_reason:
        return t("caption.why_v1", stars=stars, reason=f.v1_reason)
    return None


# ISO 折算让锐度至少下降这么多才写说明（约 ISO 1600 起）；差一两个百分点只会添乱
# Only mention the ISO adjustment when it lowers sharpness by at least this much
ISO_NOTE_MIN_DROP = 0.05


def _iso_note(f: CaptionFacts, t: Translator) -> str:
    """ISO 折算说明（折算后明显低于原始值时才写）/ ISO-normalization note."""
    if (f.iso and f.head_sharp and f.norm_sharpness is not None
            and f.norm_sharpness <= f.head_sharp * (1.0 - ISO_NOTE_MIN_DROP)):
        return t("caption.iso_note", iso=f.iso, raw=f"{f.head_sharp:.0f}")
    return ""


def _sharp_line(f: CaptionFacts, t: Translator, ranked: bool) -> Optional[str]:
    """头部锐度行 / Head sharpness line."""
    if f.norm_sharpness is None:
        return None
    val = f"{f.norm_sharpness:.0f}"
    note = _iso_note(f, t)
    if ranked and f.pct_sharp is not None:
        key = "caption.sharp_good" if f.pct_sharp >= 0.5 else "caption.sharp_weak"
        return t(key, val=val, pos=_position(f.pct_sharp, t), note=note)
    return t("caption.sharp_plain", val=val, note=note)


def _topiq_line(f: CaptionFacts, t: Translator, ranked: bool) -> Optional[str]:
    """美感行 / Aesthetics line."""
    if f.topiq is None:
        return None
    val = f"{f.topiq:.1f}"
    if ranked and f.pct_topiq is not None:
        key = "caption.topiq_good" if f.pct_topiq >= 0.5 else "caption.topiq_weak"
        return t(key, val=val, pos=_position(f.pct_topiq, t))
    return t("caption.topiq_plain", val=val)


def _focus_line(f: CaptionFacts, t: Translator) -> Optional[str]:
    """对焦行 / Focus line."""
    s = f.focus_status
    if s == "BEST":
        return t("caption.focus_best")
    if s == "GOOD":
        if f.focus_arbitrated:
            return t("caption.focus_arbitrated")
        if f.focus_weight is not None and f.focus_weight < 1.0:
            return t("caption.focus_body")
        return t("caption.focus_no_data")
    if s == "BAD":
        return t("caption.focus_bad")
    if s == "WORST":
        return t("caption.focus_worst")
    return None


def build_caption(f: CaptionFacts, t: Translator) -> str:
    """
    生成完整题注。

    参数:
    f (CaptionFacts): 评星事实
    t (Translator): i18n 取词函数，签名同 I18n.t(key, **kwargs)

    返回:
    str: 多行题注（\\n 分隔）

    Build the full multi-line caption.
    """
    lines: List[str] = [_verdict(f, t)]
    why = _why(f, t)
    if why:
        lines.append(why)
    o = f.outcome
    if o == "no_bird":
        return "\n".join(lines)
    ranked = o in RANKED_OUTCOMES
    # 排序池太小时，逐项依据不写本批百分位（仍写数值）/ no percentiles for tiny pools
    show_pct = ranked and (f.pool_size or 0) >= MIN_POOL_FOR_PERCENTILE
    if ranked and f.weight_sharp is not None and f.weight_topiq is not None:
        lines.append(t("caption.rule_v2",
                       ws=f"{f.weight_sharp * 100:.0f}", wt=f"{f.weight_topiq * 100:.0f}"))
    if o != "low_confidence":
        for line in (
            _sharp_line(f, t, show_pct),
            _focus_line(f, t),
            _topiq_line(f, t, show_pct),
        ):
            if line:
                lines.append(line)
        if f.best_eye is not None and o != "angle_poor":
            lines.append(t("caption.eye_clear") if f.best_eye >= EYE_CLEAR
                         else t("caption.eye_unclear", vis=f"{f.best_eye:.0%}"))
        if f.over_exposed:
            lines.append(t("caption.over_exposed"))
        if f.under_exposed:
            lines.append(t("caption.under_exposed"))
        if f.is_flying:
            lines.append(t("caption.flying"))
    # 识别信息：有鸟置信度来自 YOLO 检测；鸟种识别置信度来自识鸟模型
    # Identification: bird presence from YOLO, species from the Bird ID model
    if f.confidence is not None and o != "low_confidence":
        lines.append(t("caption.bird_confidence", conf=f"{f.confidence:.0%}"))
    if f.species_low_confidence and f.species_candidate and f.species_confidence is not None:
        key = "caption.species_unconfirmed_ranked" if ranked else "caption.species_unconfirmed"
        lines.append(t(key, name=f.species_candidate, conf=f"{f.species_confidence:.0f}%",
                       thr=f"{(f.species_threshold or 0):.0f}%"))
    elif f.species_confidence is not None:
        lines.append(t("caption.species_confidence", conf=f"{f.species_confidence:.0f}%"))
    return "\n".join(lines)


def mark_manual_change(caption: Optional[str], new_rating: int, t: Translator) -> str:
    """
    用户在浏览器里手动改星级（或标记无鸟）后，在题注最前面插入一行说明，原有内容保留在下方。

    再次修改时只替换这一行，不会层层叠加；原评分说明始终保留，便于对照。

    参数:
    caption (Optional[str]): 原题注（可为空）
    new_rating (int): 新星级，-1 表示标记为无鸟
    t (Translator): i18n

    返回:
    str: 新题注

    Prepend (or replace) a manual-override line; the original explanation stays below.
    """
    if new_rating < 0:
        mark = f"{MANUAL_MARK} " + t("caption.manual_no_bird")
    else:
        mark = f"{MANUAL_MARK} " + t("caption.manual_rating", stars=_stars(new_rating))
    body = caption or ""
    if body.startswith(MANUAL_MARK):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return mark + ("\n" + body if body else "")
