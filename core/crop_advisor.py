# -*- coding: utf-8 -*-
"""
裁剪建议编排模块 / Crop Advisor.

对单张鸟照生成多比例裁剪候选(标准比例套框 + 主体居中/鸟眼三分法 + TOPIQ 择优),
用 TOPIQ 排序;并支持对用户手动框打分。全程非破坏性。
"""
from __future__ import annotations

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


def _log(msg: str) -> None:
    """裁剪建议诊断日志(打到 stdout,随应用日志面板可见)。Crop Advisor diagnostic log."""
    print(f"🪶 [CropAdvisor] {msg}")


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
                   beak_xy: Optional[Tuple[float, float]], ratio_wh: float,
                   img_w: int, img_h: int, min_margin_px: int) -> Tuple[float, float]:
    """
    计算使"鸟眼落在视线对侧三分交点"的目标裁剪框中心。
    朝向由 眼→喙 向量判定:喙在眼右 → 鸟朝右 → 眼放画面左 1/3(前方留空)。
    若喙不可见(beak_xy=None),无法判定朝向,水平方向直接以眼为中心;
    垂直三分法规则无论如何都生效。

    Compute target crop-box center so bird eye lands on rule-of-thirds crossing
    opposite the gaze direction. Facing right → eye at left 1/3.
    When beak_xy is None (beak occluded / low confidence), gaze direction is
    unknown; center horizontally on the eye without bias. Vertical rule-of-thirds
    is applied regardless.

    参数 / Parameters:
        subject (Box): 鸟类检测框 (x1,y1,x2,y2) / Bird detection box.
        eye_xy: 眼睛的原图像素坐标 / Eye pixel coords in original image.
        beak_xy: 喙的原图像素坐标;可见度不足时为 None / Beak pixel coords, or None if occluded.
        ratio_wh (float): 裁剪框宽高比 / Crop box width-to-height ratio.
        img_w (int): 图像宽度 / Image width.
        img_h (int): 图像高度 / Image height.
        min_margin_px (int): 最小边距像素 / Minimum margin in pixels.

    返回 / Returns:
        Tuple[float, float]: 目标裁剪框中心 (cx, cy) / Target crop-box center (cx, cy).
    """
    sx1, sy1, sx2, sy2 = subject
    need_w = (sx2 - sx1) + 2 * min_margin_px
    box_w = max(need_w, ((sy2 - sy1) + 2 * min_margin_px) * ratio_wh)
    box_h = box_w / ratio_wh
    ex, ey = eye_xy
    if beak_xy is None:
        # 喙不可见,朝向未知:水平方向以眼为中心,不做朝向偏置
        # Beak occluded / unknown gaze: no horizontal bias, center on eye
        cx = ex
    else:
        # 水平:朝右→眼在 1/3 处(中心 = 眼 + box_w/6);朝左→眼在 2/3 处(中心 = 眼 - box_w/6)
        # Horizontal: facing right → eye at 1/3 (center = eye + box_w/6); left → eye at 2/3
        face_right = beak_xy[0] >= ex
        cx = ex + box_w / 6.0 if face_right else ex - box_w / 6.0
    # 垂直:眼放上 1/3(中心 = 眼 + box_h/6,使眼偏上)
    # Vertical: eye at upper 1/3 (center = eye + box_h/6 to push eye upward)
    cy = ey + box_h / 6.0
    return (cx, cy)


# ── 模块私有工具 / Module-private utilities ───────────────────────────────────

def _load_image_exif_aware(path: str) -> Optional[np.ndarray]:
    """
    读取图片并自动应用 EXIF 方向标签,返回 BGR ndarray。
    使用 PIL.ImageOps.exif_transpose 纠正旋转/翻转,避免 cv2.imdecode 忽略 EXIF 的问题。
    读取失败返回 None。

    Load image with automatic EXIF orientation correction, returning BGR ndarray.
    Uses PIL.ImageOps.exif_transpose to fix rotation/flip, avoiding the issue
    where cv2.imdecode silently ignores the EXIF orientation tag.
    Returns None on failure.

    参数 / Parameters:
        path (str): 图片文件路径 / Path to the image file.

    返回 / Returns:
        Optional[np.ndarray]: BGR 格式图像数组;失败时为 None / BGR image array, or None on failure.
    """
    try:
        from PIL import Image, ImageOps
        pil_img = Image.open(path)
        pil_img = ImageOps.exif_transpose(pil_img)
        rgb = pil_img.convert("RGB")
        arr = np.array(rgb, dtype=np.uint8)
        # RGB → BGR
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        return bgr
    except Exception as e:  # noqa: BLE001 — 读取/转换失败返回 None(并记日志便于定位)
        _log(f"EXIF 加载失败 path={path} err={e!r}")
        return None


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
    # 诊断:打印全部原始检测(类别+置信度),便于判断"漏鸟"是类别错/置信低/还是真没检出
    # Diagnostic: dump every raw detection (class+conf) so we can tell whether a missed
    # bird is a wrong class, a low-confidence box, or genuinely nothing detected.
    bird_cls = config.ai.BIRD_CLASS_ID
    raw = [(int(k), round(float(c), 3)) for k, c in zip(clss, confs)]
    _log(f"YOLO raw detections (cls,conf) = {raw if raw else '[]'} ; bird_class={bird_cls}")
    out: List[Tuple[Box, float]] = []
    for (x1, y1, x2, y2), c, k in zip(boxes, confs, clss):
        if int(k) == bird_cls:
            out.append(((int(x1), int(y1), int(x2), int(y2)), float(c)))
    _log(f"bird-class boxes (before {BIRD_CONF_THRESHOLD} filter) = "
         f"{[(b, round(cf, 3)) for b, cf in out] if out else '[]'}")
    return out


def _keypoints(
    image_bgr: np.ndarray, bird_bbox: Box
) -> Optional[Tuple[Tuple[float, float], Optional[Tuple[float, float]]]]:
    """
    默认关键点检测:返回 (eye_px, beak_px) 或 (eye_px, None) 或 None。
    当喙的可见度低于 KeypointDetector.VISIBILITY_THRESHOLD 时,返回 (eye_px, None),
    以避免低置信喙坐标翻转三分法朝向判断。

    Default keypoint detection; returns (eye_px, beak_px), (eye_px, None), or None.
    When beak visibility is below KeypointDetector.VISIBILITY_THRESHOLD, returns
    (eye_px, None) to prevent a low-confidence beak coord from flipping the
    rule-of-thirds gaze direction.

    参数 / Parameters:
        image_bgr (np.ndarray): BGR 格式原图 / Input image in BGR format.
        bird_bbox (Box): 鸟类检测框 (x1,y1,x2,y2) / Bird detection box.

    返回 / Returns:
        Optional[Tuple[...]]: (眼睛坐标, 喙坐标|None) 或 None
                              (eye coords, beak coords or None) or None if eye undetected.
    """
    from core.keypoint_detector import KeypointDetector, get_keypoint_detector
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
    # 喙可见度守卫:低置信喙坐标不可用于朝向判断,返回 None
    # Beak visibility guard: low-confidence beak coords must not flip gaze direction
    if kp.beak_vis < KeypointDetector.VISIBILITY_THRESHOLD:
        return (eye_px, None)
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
    # 用 get_best_device().type 作为单例 key,与 ai_model 的 TOPIQ 单例保持一致,
    # 避免再加载一份 TOPIQ 模型(否则 'mps' 默认 key 与主程序 key 不同会重复加载)。
    # Key the singleton by get_best_device().type to match ai_model's TOPIQ instance,
    # so we reuse the already-loaded model instead of loading a second copy.
    from iqa_scorer import get_iqa_scorer
    from config import get_best_device
    return get_iqa_scorer(device=get_best_device().type).calculate_from_array(crop_bgr)


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
                 keypoint_fn: Optional[Callable] = None,
                 topiq_fn: Optional[Callable] = None,
                 _image_loader: Optional[Callable] = None) -> CropAdviceResult:
    """
    生成多比例裁剪建议(按 TOPIQ 降序)。依赖可注入以便测试。
    Generate multi-ratio crop suggestions sorted by TOPIQ score descending.
    All dependencies are injectable for testing without loading real models.

    构图策略:每个标准比例下,以"主体居中"和(单鸟时)"鸟眼三分法"两种中心生成候选,
    再由 TOPIQ 打分择优。不依赖任何专用裁剪模型。
    Composition: per ratio, generate candidates centered on the subject and (single bird)
    on the rule-of-thirds eye position, then pick the best by TOPIQ. No dedicated crop model.

    参数 / Parameters:
        image_path (str): 图片文件路径 / Path to the image file.
        detect_fn: 鸟类检测函数(image_bgr→[(bbox,conf)]);默认 _detect_birds。
                   Bird detection fn (image_bgr→[(bbox,conf)]); default _detect_birds.
        keypoint_fn: 关键点函数(image_bgr,bird_bbox→(eye_px,beak_px)|None);默认 _keypoints。
                     Keypoint fn (image_bgr,bird_bbox→(eye_px,beak_px)|None); default _keypoints.
        topiq_fn: 图像质量打分函数(crop_bgr→float|None);默认 _topiq。
                  IQA scoring fn (crop_bgr→float|None); default _topiq.
        _image_loader: 图片读取函数(path→ndarray|None);默认 EXIF 感知加载。仅供测试注入。
                       Image loader fn (path→ndarray|None); default EXIF-aware loader. Test-only.

    返回 / Returns:
        CropAdviceResult: status ∈ {ok, no_bird, too_many_birds},含排好序的裁剪建议列表。
                          status ∈ {ok, no_bird, too_many_birds}, with sorted crop suggestions.
    """
    # ── 依赖绑定 / Bind dependencies ──────────────────────────────────────────
    detect_fn = detect_fn or _detect_birds
    keypoint_fn = keypoint_fn or _keypoints
    topiq_fn = topiq_fn or _topiq
    if _image_loader is None:
        _image_loader = _load_image_exif_aware

    # ── 读图 / Load image ─────────────────────────────────────────────────────
    _log(f"advise_crops 输入路径 = {image_path}")
    image_bgr = _image_loader(image_path)
    if image_bgr is None:
        _log("图像加载失败(None)→ no_bird。常见原因:路径不可解码(如 RAW)或文件损坏。")
        return CropAdviceResult(status="no_bird", bird_count=0)
    img_h, img_w = image_bgr.shape[:2]
    _log(f"已加载图像 {img_w}x{img_h}")
    short_side = min(img_w, img_h)
    min_margin_px = int(short_side * MIN_MARGIN_RATIO)

    # ── 检测鸟 / Detect birds ─────────────────────────────────────────────────
    # 严格使用 > 阈值(不含等于)/ Strict threshold: strictly greater than
    birds = [b for b in detect_fn(image_bgr) if b[1] > BIRD_CONF_THRESHOLD]
    n = len(birds)
    _log(f"过滤后(conf>{BIRD_CONF_THRESHOLD})合格鸟数 N={n}")
    if n == 0:
        _log("N=0 → no_bird")
        return CropAdviceResult(status="no_bird", bird_count=0)
    if n > MAX_BIRDS_FOR_AUTO:
        _log(f"N>{MAX_BIRDS_FOR_AUTO} → too_many_birds")
        return CropAdviceResult(status="too_many_birds", bird_count=n)

    # ── 候选中心生成 / Candidate center generation ────────────────────────────
    if n == 1:
        # 单鸟:主体居中作锚点 + 关键点三分法
        # Single bird: subject-centered anchor + keypoint rule-of-thirds
        subject = birds[0][0]
        kp = keypoint_fn(image_bgr, subject)
        _log(f"单鸟 subject={subject} 关键点={'有(三分法)' if kp else '无(仅居中)'}")
    else:
        # 多鸟(2-3):取并集,跳过关键点(不做三分法)
        # Multiple birds (2-3): use union bbox; skip keypoints (no rule-of-thirds)
        subject = _union_bbox([b[0] for b in birds])
        kp = None  # 多鸟不做三分法 / No rule-of-thirds for multi-bird
        _log(f"多鸟 N={n} 并集 subject={subject}")

    anchor_center = ((subject[0] + subject[2]) / 2.0, (subject[1] + subject[3]) / 2.0)

    # ── 按比例生成裁剪候选 / Generate crops per ratio ─────────────────────────
    orientation = _pick_orientation(subject)
    ratios = RATIOS_LANDSCAPE if orientation == "landscape" else RATIOS_PORTRAIT
    _log(f"方向={orientation} 比例集={ratios}")

    suggestions: List[CropSuggestion] = []
    for label in ratios:
        rwh = _parse_ratio(label)
        centers = [anchor_center]
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
    _log(f"生成候选 {len(suggestions)} 个: "
         f"{[(s.ratio_label, round(s.topiq_score, 2)) for s in suggestions]}")
    return CropAdviceResult(suggestions=suggestions, status="ok", bird_count=n)
