# -*- coding: utf-8 -*-
"""
连拍组「最佳一张」分层排序 / Burst best-pick tiered ranking.

无 Qt 依赖的纯函数模块。把一张照片的结果字典映射成一个可比较的排序键,
供 `max(photos, key=burst_composite_key)` 选出组内"最佳"。

分层语义:先按对焦仲裁 focus_status 分档(对焦准 > 一切),同档内再按
"眼清为主 + 头锐为辅"的加权分精排。eye 与 head_sharp 同源于同一清晰度
度量、同量纲,可直接加权,无需跨量纲归一化。

Pure, Qt-free module. Maps a photo result dict to a comparable ranking key
for `max(photos, key=burst_composite_key)`. Tier by focus verdict first
(focus beats everything), then within a tier weight eye sharpness over head
sharpness. eye and head_sharp share one sharpness metric (same scale), so a
direct weighted sum needs no cross-scale normalization.
"""
from typing import Tuple

# 权重与档位(默认值;后续用真实数据 A/B 标定)/ weights & tiers (defaults; A/B-tuned later)
W_EYE: float = 0.7
W_HEAD: float = 0.3
DEFAULT_TIER: int = 2  # focus_status 缺失 → 中性 GOOD 档
FOCUS_TIER = {"BEST": 3, "GOOD": 2, "BAD": 1, "WORST": 0}


def _to_float(value) -> float:
    """安全转 float,失败返回 0.0 / safe float, 0.0 on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def eye_sharp(photo: dict) -> float:
    """
    眼睛清晰度:取左右眼的较大者(鸟多侧拍,只有对焦侧那只眼有效)。
    Eye sharpness: max of the two eyes (side profiles expose one in-focus eye).
    """
    return max(_to_float(photo.get("left_eye")), _to_float(photo.get("right_eye")))


def focus_tier(photo: dict) -> int:
    """
    对焦仲裁分档:BEST=3/GOOD=2/BAD=1/WORST=0;缺失或未知值 → 中性 GOOD 档(2),
    避免"没算出对焦"的照片一律沉底。
    Focus verdict tier; missing/unknown → neutral GOOD tier (2).
    """
    status = str(photo.get("focus_status") or "").strip().upper()
    return FOCUS_TIER.get(status, DEFAULT_TIER)


def burst_composite_key(photo: dict) -> Tuple[int, float]:
    """
    组内"最佳"分层排序键:(对焦档, 眼清为主+头锐为辅的加权分)。
    元组字典序:先比对焦档,同档再比加权分。供 max(photos, key=...) 使用。
    Tiered key (focus_tier, W_EYE*eye + W_HEAD*head) for max(...).
    """
    layer_score = W_EYE * eye_sharp(photo) + W_HEAD * _to_float(photo.get("head_sharp"))
    return (focus_tier(photo), layer_score)
