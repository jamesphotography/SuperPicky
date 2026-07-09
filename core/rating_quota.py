#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评星 V2 核心:批内相对排序 + 配额制(纯函数,无 Qt/IO 依赖)。

设计(2026-07-09 审定,详见 docs/plans/2026-07-09-rating-v2-quota.md):
- 硬门槛保留绝对值:无鸟→-1;置信度<50%→0;归一化锐度<100→0;TOPIQ<3.5→0;
  关键点全不可见→1(角度不佳)。
- 综合分 Q = 0.65×锐度批内百分位 + 0.35×TOPIQ批内百分位,
  外加小幅加减分:飞鸟+0.06、精焦+0.04、脱焦−0.06、曝光问题−0.06。
- 配额定星:3★ = Q 前 quota3%(且归一化锐度≥300 兜底),2★ = 其后 quota2%,
  其余 1★;眼睛可见度<0.5 的照片星级封顶 2★;同一连拍组 3★ 封顶 N 张。

Rating V2 core: batch-relative ranking with quotas (pure functions, no Qt/IO).
Hard gates keep absolute semantics; the composite score Q ranks the gated
photos within the batch; stars are assigned by quota with absolute floors,
an eye-visibility cap, and a per-burst 3-star cap.
"""

import bisect
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---- 常量(与审定方案一致)/ constants per approved design ----
MIN_CONFIDENCE = 0.50     # 置信度硬门槛 / confidence hard gate
MIN_SHARPNESS = 100.0     # 归一化锐度硬门槛 / normalized sharpness hard gate
MIN_TOPIQ = 3.5           # TOPIQ 硬门槛 / TOPIQ hard gate
KEYPOINT_VIS_MIN = 0.3    # 关键点可见判定阈值 / keypoint visibility threshold

W_SHARP = 0.65            # Q 中锐度百分位权重 / sharpness percentile weight
W_TOPIQ = 0.35            # Q 中美学百分位权重 / aesthetics percentile weight
BONUS_FLYING = 0.06       # 飞鸟加分 / flying bonus
BONUS_FOCUS_BEST = 0.04   # 精焦加分 / head-focus bonus
PENALTY_FOCUS_WORST = -0.06   # 脱焦减分 / focus-outside penalty
PENALTY_EXPOSURE = -0.06      # 曝光问题减分 / exposure issue penalty

EYE_CAP_THRESHOLD = 0.5   # 眼睛可见度低于此值 → 星级封顶 2★ / eye-visibility star cap
QUOTA3_SHARP_FLOOR = 300.0    # 3★ 绝对兜底:归一化锐度下限 / absolute floor for 3★

DEFAULT_QUOTA3 = 20.0     # 3★ 默认配额 % / default 3-star quota
DEFAULT_QUOTA2 = 25.0     # 2★ 默认配额 %(接在 3★ 之后)/ 2-star quota after 3★
DEFAULT_BURST_CAP3 = 2    # 连拍组内 3★ 上限 / per-burst 3-star cap

# 技能等级 → 3★ 配额映射 / skill level → 3-star quota
SKILL_QUOTA3 = {
    "beginner": 25.0,
    "intermediate": 20.0,
    "master": 10.0,
}


@dataclass
class PhotoMetricsV2:
    """
    单张照片进入 V2 定星的全部指标(循环阶段采集,后置阶段消费)。

    All metrics one photo carries into V2 star assignment; collected during
    the per-photo loop and consumed in the post-pass.
    """
    key: str                          # 唯一键(original_prefix)/ unique key
    detected: bool = False
    confidence: float = 0.0
    norm_sharpness: float = 0.0       # ISO 归一化后的头部锐度 / ISO-normalized head sharpness
    topiq: Optional[float] = None     # 鸟裁剪区 TOPIQ / bird-crop TOPIQ
    best_eye: float = 0.0             # 双眼最高可见度 / best eye visibility
    beak_vis: float = 0.0             # 鸟喙可见度 / beak visibility
    is_flying: bool = False
    focus_status: str = ""            # 'BEST'/'GOOD'/'BAD'/'WORST'/''(无数据)
    has_exposure_issue: bool = False
    burst_id: Optional[int] = None    # 连拍组 / burst group


@dataclass
class RatingV2Result:
    """V2 定星结果 / V2 star assignment result."""
    rating: int                       # -1/0/1/2/3
    q_score: Optional[float] = None   # 综合分(硬门槛淘汰者为 None)/ composite Q
    reason_key: str = ""              # i18n 原因键 / i18n reason key
    reason_args: dict = field(default_factory=dict)


def _percentile_fn(values: List[float]):
    """返回 v→批内百分位(0-1) 的查询函数 / batch percentile lookup."""
    s = sorted(values)
    n = len(s)

    def pct(v: float) -> float:
        return bisect.bisect_left(s, v) / n if n else 0.0

    return pct


def compute_q(photo: PhotoMetricsV2, pct_sharp, pct_topiq) -> float:
    """
    计算单张照片的综合质量分 Q。

    参数:
        photo: 照片指标
        pct_sharp / pct_topiq: 批内百分位查询函数

    返回:
        float: Q 分(理论范围约 -0.12 ~ 1.10)
    """
    q = W_SHARP * pct_sharp(photo.norm_sharpness)
    q += W_TOPIQ * pct_topiq(photo.topiq if photo.topiq is not None else 0.0)
    if photo.is_flying:
        q += BONUS_FLYING
    if photo.focus_status == "BEST":
        q += BONUS_FOCUS_BEST
    elif photo.focus_status == "WORST":
        q += PENALTY_FOCUS_WORST
    if photo.has_exposure_issue:
        q += PENALTY_EXPOSURE
    return q


def gate_photo(
    photo: PhotoMetricsV2,
    min_confidence: float = MIN_CONFIDENCE,
) -> Optional[RatingV2Result]:
    """
    硬门槛判定。返回 None 表示通过全部门槛(进入排序);否则返回终局结果。

    参数:
        photo: 照片指标
        min_confidence: 置信度门槛(跟随用户 AI 置信度设置,默认 0.5)

    Hard-gate check. None means the photo passes every gate and enters the
    ranking pool; otherwise the returned result is final. min_confidence
    follows the user's AI-confidence setting.
    """
    if not photo.detected:
        return RatingV2Result(-1, reason_key="rating_engine.reject_no_bird")
    if photo.confidence < min_confidence:
        return RatingV2Result(
            0, reason_key="rating_engine.low_confidence",
            reason_args={"confidence": photo.confidence, "threshold": min_confidence})
    if photo.best_eye < KEYPOINT_VIS_MIN and photo.beak_vis < KEYPOINT_VIS_MIN:
        return RatingV2Result(1, reason_key="rating_engine.angle_poor")
    if photo.norm_sharpness < MIN_SHARPNESS:
        return RatingV2Result(
            0, reason_key="rating_engine.low_sharpness",
            reason_args={"val": photo.norm_sharpness, "threshold": MIN_SHARPNESS})
    if photo.topiq is not None and photo.topiq < MIN_TOPIQ:
        return RatingV2Result(
            0, reason_key="rating_engine.low_aesthetics",
            reason_args={"val": photo.topiq, "threshold": MIN_TOPIQ})
    return None


def assign_ratings(
    photos: List[PhotoMetricsV2],
    quota3: float = DEFAULT_QUOTA3,
    quota2: float = DEFAULT_QUOTA2,
    burst_cap3: int = DEFAULT_BURST_CAP3,
    min_confidence: float = MIN_CONFIDENCE,
) -> Dict[str, RatingV2Result]:
    """
    对一批照片统一定星(V2 主入口)。

    参数:
        photos: 全批照片指标列表
        quota3: 3★ 配额百分比(如 20 → Q 前 20%)
        quota2: 2★ 配额百分比(接在 3★ 之后)
        burst_cap3: 同一连拍组内 3★ 上限,0 表示不限制

    返回:
        Dict[key, RatingV2Result]:每张照片的星级与原因

    Assign stars for the whole batch (V2 entry point). Hard-gated photos get
    final -1/0/1 immediately; the rest are ranked by Q and starred by quota,
    then the eye-visibility cap and the per-burst 3-star cap are applied.
    """
    results: Dict[str, RatingV2Result] = {}
    pool: List[PhotoMetricsV2] = []

    for p in photos:
        gated = gate_photo(p, min_confidence=min_confidence)
        if gated is not None:
            results[p.key] = gated
        else:
            pool.append(p)

    if not pool:
        return results

    pct_sharp = _percentile_fn([p.norm_sharpness for p in pool])
    pct_topiq = _percentile_fn([(p.topiq if p.topiq is not None else 0.0) for p in pool])

    scored = sorted(
        ((compute_q(p, pct_sharp, pct_topiq), p) for p in pool),
        key=lambda x: -x[0])

    n = len(scored)
    cut3 = quota3 / 100.0
    cut2 = (quota3 + quota2) / 100.0

    for idx, (q, p) in enumerate(scored):
        frac = idx / n
        if frac < cut3 and p.norm_sharpness >= QUOTA3_SHARP_FLOOR:
            star = 3
            reason_key = "rating_v2.top_quota"
        elif frac < cut2:
            star = 2
            reason_key = "rating_v2.mid_quota"
        else:
            star = 1
            reason_key = "rating_v2.rest_quota"
        # 眼睛可见度封顶(保留现行降档精神)/ eye-visibility cap
        if p.best_eye < EYE_CAP_THRESHOLD and star > 2:
            star = 2
            reason_key = "rating_v2.eye_capped"
        results[p.key] = RatingV2Result(
            star, q_score=q, reason_key=reason_key,
            reason_args={"percent": math.ceil(max(frac, 1 / n) * 100)})

    # 连拍组内 3★ 封顶:每组只留 Q 最高的前 N 张,其余降 2★
    # Per-burst 3-star cap: keep the top-N by Q, demote the rest to 2★
    if burst_cap3 > 0:
        burst_threes: Dict[int, List[str]] = {}
        for q, p in scored:
            if p.burst_id is not None and results[p.key].rating == 3:
                burst_threes.setdefault(p.burst_id, []).append(p.key)
        for keys in burst_threes.values():
            for key in keys[burst_cap3:]:  # scored 已按 Q 降序,故切片即淘汰尾部
                results[key].rating = 2
                results[key].reason_key = "rating_v2.burst_capped"

    return results


def get_quota3_for_skill(level_key: str, config=None) -> float:
    """
    技能等级 → 3★ 配额。custom 从 config.custom_quota3 读取。

    Map skill level to the 3-star quota; "custom" reads config.custom_quota3.
    """
    if level_key == "custom" and config is not None:
        return float(getattr(config, "custom_quota3", DEFAULT_QUOTA3))
    return SKILL_QUOTA3.get(level_key, DEFAULT_QUOTA3)
