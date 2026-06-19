# -*- coding: utf-8 -*-
"""
裁剪建议编排模块 / Crop Advisor.

对单张鸟照生成多比例裁剪候选(CACNet 定构图 + 标准比例套框 + 三分法),
用 TOPIQ 排序;并支持对用户手动框打分。全程非破坏性。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable

import numpy as np

# ── 常量 / Constants ─────────────────────────────────────────────────────────
BIRD_CONF_THRESHOLD: float = 0.50   # 算作一只鸟的 YOLO 置信度下限
MAX_BIRDS_FOR_AUTO: int = 3         # 超过则放弃自动裁剪
MIN_MARGIN_RATIO: float = 0.10      # 呼吸边距最小地板(短边占比)

RATIOS_LANDSCAPE: List[str] = ["1:1", "5:4", "4:3", "3:2", "7:5", "8:5", "16:9", "2.39:1"]
RATIOS_PORTRAIT: List[str] = ["1:1", "4:5", "3:4", "2:3", "5:7", "5:8", "9:16"]

Box = Tuple[int, int, int, int]  # (x1,y1,x2,y2)


@dataclass
class CropSuggestion:
    """单个裁剪候选。"""
    ratio_label: str
    box: Box
    topiq_score: float
    preview_bgr: Optional[np.ndarray] = None


@dataclass
class CropAdviceResult:
    """advise_crops 的返回。status ∈ {ok, no_bird, too_many_birds}。"""
    suggestions: List[CropSuggestion] = field(default_factory=list)
    status: str = "ok"
    bird_count: int = 0


# ── 纯几何 / Pure geometry ───────────────────────────────────────────────────
def _parse_ratio(label: str) -> float:
    """'宽:高' → 宽/高 浮点。"""
    w, h = label.split(":")
    return float(w) / float(h)


def _pick_orientation(subject_bbox: Box) -> str:
    """主体框宽>=高 → landscape;否则 portrait。"""
    x1, y1, x2, y2 = subject_bbox
    return "landscape" if (x2 - x1) >= (y2 - y1) else "portrait"


def _union_bbox(boxes: List[Box]) -> Box:
    """多个框的并集外接框。"""
    xs1 = min(b[0] for b in boxes); ys1 = min(b[1] for b in boxes)
    xs2 = max(b[2] for b in boxes); ys2 = max(b[3] for b in boxes)
    return (xs1, ys1, xs2, ys2)


def _fit_ratio_box(subject: Box, ratio_wh: float, center: Tuple[float, float],
                   img_w: int, img_h: int, min_margin_px: int) -> Optional[Box]:
    """
    求一个长宽比为 ratio_wh(宽/高)的裁剪框,满足:
      (a) 完整包含 subject,且四周 >= min_margin_px;
      (b) 完全落在 [0,img_w]x[0,img_h] 内;
      (c) 在 (a)(b) 约束下中心尽量贴近 center。
    放不下返回 None。
    """
    sx1, sy1, sx2, sy2 = subject
    # 必须容纳的最小内容尺寸(主体 + 两侧边距)
    need_w = (sx2 - sx1) + 2 * min_margin_px
    need_h = (sy2 - sy1) + 2 * min_margin_px
    # 由比例确定一个同时覆盖 need_w/need_h 的最小外框尺寸
    box_w = max(need_w, need_h * ratio_wh)
    box_h = box_w / ratio_wh
    if box_h < need_h:  # 数值兜底
        box_h = need_h
        box_w = box_h * ratio_wh
    # 放不进图像
    if box_w > img_w or box_h > img_h:
        return None
    # 以 center 为目标摆放,再平移使其完全含主体+边距并落在图内
    cx, cy = center
    x1 = cx - box_w / 2.0
    y1 = cy - box_h / 2.0
    # 先保证含主体+边距
    if x1 > sx1 - min_margin_px:
        x1 = sx1 - min_margin_px
    if x1 + box_w < sx2 + min_margin_px:
        x1 = (sx2 + min_margin_px) - box_w
    if y1 > sy1 - min_margin_px:
        y1 = sy1 - min_margin_px
    if y1 + box_h < sy2 + min_margin_px:
        y1 = (sy2 + min_margin_px) - box_h
    # 再夹到图界
    x1 = max(0.0, min(x1, img_w - box_w))
    y1 = max(0.0, min(y1, img_h - box_h))
    box = (int(round(x1)), int(round(y1)),
           int(round(x1 + box_w)), int(round(y1 + box_h)))
    # 最终校验:确实含主体(夹紧后可能因主体过大而失败)
    if box[0] <= sx1 and box[1] <= sy1 and box[2] >= sx2 and box[3] >= sy2:
        return box
    return None


def _thirds_center(subject: Box, eye_xy: Tuple[float, float],
                   beak_xy: Tuple[float, float], ratio_wh: float,
                   img_w: int, img_h: int, min_margin_px: int) -> Tuple[float, float]:
    """
    计算使"鸟眼落在视线对侧三分交点"的目标裁剪框中心。
    朝向由 眼→喙 向量判定:喙在眼右 → 鸟朝右 → 眼放画面左 1/3(前方留空)。
    """
    sx1, sy1, sx2, sy2 = subject
    need_w = (sx2 - sx1) + 2 * min_margin_px
    box_w = max(need_w, ((sy2 - sy1) + 2 * min_margin_px) * ratio_wh)
    box_h = box_w / ratio_wh
    ex, ey = eye_xy
    # 水平:朝右→眼在 1/3 处(中心 = 眼 + box_w/6);朝左→眼在 2/3 处(中心 = 眼 - box_w/6)
    face_right = beak_xy[0] >= ex
    cx = ex + box_w / 6.0 if face_right else ex - box_w / 6.0
    # 垂直:眼放上 1/3(中心 = 眼 + box_h/6,使眼偏上)
    cy = ey + box_h / 6.0
    return (cx, cy)
