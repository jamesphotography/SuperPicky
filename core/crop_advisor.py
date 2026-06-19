# -*- coding: utf-8 -*-
"""
裁剪建议编排模块 / Crop Advisor.

对单张鸟照生成多比例裁剪候选(CACNet 定构图 + 标准比例套框 + 三分法),
用 TOPIQ 排序;并支持对用户手动框打分。全程非破坏性。
"""
from __future__ import annotations

import os
import cv2
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Callable

import numpy as np

from config import config

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

    Compute target crop-box center so bird eye lands on rule-of-thirds crossing
    opposite the gaze direction. Facing right → eye at left 1/3.
    """
    sx1, sy1, sx2, sy2 = subject
    need_w = (sx2 - sx1) + 2 * min_margin_px
    box_w = max(need_w, ((sy2 - sy1) + 2 * min_margin_px) * ratio_wh)
    box_h = box_w / ratio_wh
    ex, ey = eye_xy
    # 水平:朝右→眼在 1/3 处(中心 = 眼 + box_w/6);朝左→眼在 2/3 处(中心 = 眼 - box_w/6)
    # Horizontal: facing right → eye at 1/3 (center = eye + box_w/6); left → eye at 2/3
    face_right = beak_xy[0] >= ex
    cx = ex + box_w / 6.0 if face_right else ex - box_w / 6.0
    # 垂直:眼放上 1/3(中心 = 眼 + box_h/6,使眼偏上)
    # Vertical: eye at upper 1/3 (center = eye + box_h/6 to push eye upward)
    cy = ey + box_h / 6.0
    return (cx, cy)


# ── 默认依赖实现 / Default dependencies ───────────────────────────────────────

def _detect_birds(image_bgr: np.ndarray) -> List[Tuple[Box, float]]:
    """
    默认 YOLO 检测,返回所有鸟类框 [(bbox,conf)](未按阈值过滤)。
    Default YOLO detection; returns all bird boxes [(bbox, conf)] without threshold filtering.

    参数 / Parameters:
        image_bgr (np.ndarray): BGR 格式原图 / Input image in BGR format.

    返回 / Returns:
        List[Tuple[Box, float]]: 所有鸟类检测框和置信度 / All bird bboxes with confidence scores.
    """
    from ai_model import load_yolo_model
    from config import get_lazy_registry, get_best_device
    registry = get_lazy_registry()
    model = registry.get_or_create("crop_advisor.yolo", load_yolo_model)
    results = model(image_bgr, device=get_best_device().type)
    boxes = results[0].boxes.xyxy.cpu().numpy()
    confs = results[0].boxes.conf.cpu().numpy()
    clss = results[0].boxes.cls.cpu().numpy()
    out: List[Tuple[Box, float]] = []
    for (x1, y1, x2, y2), c, k in zip(boxes, confs, clss):
        if int(k) == config.ai.BIRD_CLASS_ID:
            out.append(((int(x1), int(y1), int(x2), int(y2)), float(c)))
    return out


def _keypoints(image_bgr: np.ndarray, bird_bbox: Box) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """
    默认关键点检测:返回 (eye_px, beak_px) 或 None。坐标为原图像素。
    Default keypoint detection; returns (eye_px, beak_px) in original image pixel coords, or None.

    参数 / Parameters:
        image_bgr (np.ndarray): BGR 格式原图 / Input image in BGR format.
        bird_bbox (Box): 鸟类检测框 (x1,y1,x2,y2) / Bird detection box.

    返回 / Returns:
        Optional[Tuple[...]]: (眼睛像素坐标, 喙像素坐标) 或 None / (eye pixel coords, beak pixel coords) or None.
    """
    from core.keypoint_detector import get_keypoint_detector
    x1, y1, x2, y2 = bird_bbox
    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    kp = get_keypoint_detector().detect(rgb, box=(x1, y1, x2 - x1, y2 - y1))
    if kp is None or kp.visible_eye is None:
        return None
    cw, ch = (x2 - x1), (y2 - y1)
    eye = kp.left_eye if kp.left_eye_vis >= kp.right_eye_vis else kp.right_eye
    eye_px = (x1 + eye[0] * cw, y1 + eye[1] * ch)
    beak_px = (x1 + kp.beak[0] * cw, y1 + kp.beak[1] * ch)
    return (eye_px, beak_px)


def _topiq(crop_bgr: np.ndarray) -> Optional[float]:
    """
    默认 TOPIQ 图像质量打分。
    Default TOPIQ image quality scoring.

    参数 / Parameters:
        crop_bgr (np.ndarray): BGR 格式裁剪图 / Cropped image in BGR format.

    返回 / Returns:
        Optional[float]: TOPIQ 分数,失败返回 None / TOPIQ score, or None on failure.
    """
    from iqa_scorer import get_iqa_scorer
    return get_iqa_scorer().calculate_from_array(crop_bgr)


# ── 编排 / Orchestration ─────────────────────────────────────────────────────

def score_manual_crop(image_bgr: np.ndarray, box: Box,
                      topiq_fn: Optional[Callable] = None) -> Optional[float]:
    """
    对用户手动框打 TOPIQ 分(无主体/边距约束)。
    Score a user-drawn manual crop box with TOPIQ (no subject/margin constraints).

    参数 / Parameters:
        image_bgr (np.ndarray): BGR 格式原图 / Input image in BGR format.
        box (Box): 用户手动框 (x1,y1,x2,y2) / User-drawn crop box.
        topiq_fn (Optional[Callable]): 可注入的打分函数,默认使用 _topiq / Injectable scorer, defaults to _topiq.

    返回 / Returns:
        Optional[float]: TOPIQ 分数,裁剪为空时返回 None / TOPIQ score, or None if crop is empty.
    """
    topiq_fn = topiq_fn or _topiq
    x1, y1, x2, y2 = box
    crop = image_bgr[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return None
    return topiq_fn(crop)


def advise_crops(image_path: str, *,
                 detect_fn: Optional[Callable] = None,
                 cacnet_fn: Optional[Callable] = None,
                 keypoint_fn: Optional[Callable] = None,
                 topiq_fn: Optional[Callable] = None,
                 _image_loader: Optional[Callable] = None) -> CropAdviceResult:
    """
    生成多比例裁剪建议(按 TOPIQ 降序)。依赖可注入以便测试。
    Generate multi-ratio crop suggestions sorted by TOPIQ score descending.
    All dependencies are injectable for testing without loading real models.

    参数 / Parameters:
        image_path (str): 图片文件路径 / Path to the image file.
        detect_fn: 鸟类检测函数(image_bgr→[(bbox,conf)]);默认 _detect_birds。
                   Bird detection fn (image_bgr→[(bbox,conf)]); default _detect_birds.
        cacnet_fn: CACNet 构图建议函数(image_bgr→bbox);默认 get_cacnet_cropper().predict_box。
                   CACNet composition fn (image_bgr→bbox); default get_cacnet_cropper().predict_box.
        keypoint_fn: 关键点函数(image_bgr,bird_bbox→(eye_px,beak_px)|None);默认 _keypoints。
                     Keypoint fn (image_bgr,bird_bbox→(eye_px,beak_px)|None); default _keypoints.
        topiq_fn: 图像质量打分函数(crop_bgr→float|None);默认 _topiq。
                  IQA scoring fn (crop_bgr→float|None); default _topiq.
        _image_loader: 图片读取函数(path→ndarray|None);默认 read_image_bgr。仅供测试注入。
                       Image loader fn (path→ndarray|None); default read_image_bgr. Test-only.

    返回 / Returns:
        CropAdviceResult: status ∈ {ok, no_bird, too_many_birds},含排好序的裁剪建议列表。
                          status ∈ {ok, no_bird, too_many_birds}, with sorted crop suggestions.
    """
    # ── 依赖绑定 / Bind dependencies ──────────────────────────────────────────
    detect_fn = detect_fn or _detect_birds
    keypoint_fn = keypoint_fn or _keypoints
    topiq_fn = topiq_fn or _topiq
    if cacnet_fn is None:
        from core.cacnet_cropper import get_cacnet_cropper
        cacnet_fn = lambda img: get_cacnet_cropper().predict_box(img)
    if _image_loader is None:
        from ai_model import read_image_bgr
        _image_loader = read_image_bgr

    # ── 读图 / Load image ─────────────────────────────────────────────────────
    image_bgr = _image_loader(image_path)
    if image_bgr is None:
        return CropAdviceResult(status="no_bird", bird_count=0)
    img_h, img_w = image_bgr.shape[:2]
    short_side = min(img_w, img_h)
    min_margin_px = int(short_side * MIN_MARGIN_RATIO)

    # ── 检测鸟 / Detect birds ─────────────────────────────────────────────────
    # 严格使用 > 阈值(不含等于)/ Strict threshold: strictly greater than
    birds = [b for b in detect_fn(image_bgr) if b[1] > BIRD_CONF_THRESHOLD]
    n = len(birds)
    if n == 0:
        return CropAdviceResult(status="no_bird", bird_count=0)
    if n > MAX_BIRDS_FOR_AUTO:
        return CropAdviceResult(status="too_many_birds", bird_count=n)

    # ── 候选中心生成 / Candidate center generation ────────────────────────────
    if n == 1:
        # 单鸟:用 CACNet 中心 + 关键点三分法
        # Single bird: use CACNet center + keypoint rule-of-thirds
        subject = birds[0][0]
        b_star = cacnet_fn(image_bgr)
        cacnet_center = ((b_star[0] + b_star[2]) / 2.0, (b_star[1] + b_star[3]) / 2.0)
        kp = keypoint_fn(image_bgr, subject)
    else:
        # 多鸟(2-3):取并集,跳过 CACNet 和关键点
        # Multiple birds (2-3): use union bbox, skip CACNet and keypoints
        subject = _union_bbox([b[0] for b in birds])
        cacnet_center = ((subject[0] + subject[2]) / 2.0, (subject[1] + subject[3]) / 2.0)
        kp = None  # 多鸟不做三分法 / No rule-of-thirds for multi-bird

    # ── 按比例生成裁剪候选 / Generate crops per ratio ─────────────────────────
    ratios = RATIOS_LANDSCAPE if _pick_orientation(subject) == "landscape" else RATIOS_PORTRAIT

    suggestions: List[CropSuggestion] = []
    for label in ratios:
        rwh = _parse_ratio(label)
        centers = [cacnet_center]
        if kp is not None:
            eye, beak = kp
            centers.append(_thirds_center(subject, eye, beak, rwh, img_w, img_h, min_margin_px))
        # 对同一比例的所有候选中心打分,取最优 / Score all candidate centers for this ratio, keep best
        best: Optional[CropSuggestion] = None
        for center in centers:
            box = _fit_ratio_box(subject, rwh, center, img_w, img_h, min_margin_px)
            if box is None:
                continue
            crop = image_bgr[box[1]:box[3], box[0]:box[2]]
            score = topiq_fn(crop)
            if score is None:
                continue
            if best is None or score > best.topiq_score:
                best = CropSuggestion(ratio_label=label, box=box,
                                      topiq_score=float(score), preview_bgr=crop)
        if best is not None:
            suggestions.append(best)

    # TOPIQ 降序排列 / Sort by TOPIQ descending
    suggestions.sort(key=lambda s: s.topiq_score, reverse=True)
    return CropAdviceResult(suggestions=suggestions, status="ok", bird_count=n)
