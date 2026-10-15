# -*- coding: utf-8 -*-
"""
双眼不可见照片的锐度与评星回归测试 (V4.8)。

锁定三件事：
1. 低可见度分支可达且不抛 NameError（此前 beak_vis 未定义）；
2. 双眼不可见但画面锐利的照片能算出非零锐度，不再被 0 锐度判废；
3. 真糊的照片仍然拿 0 锐度 → 仍判 0★，修复不是无脑放行。

Regression tests for photos where neither eye is visible (V4.8).
"""
import numpy as np
import pytest

from core.keypoint_detector import KeypointDetector
from core.rating_quota import PhotoMetricsV2, gate_photo


def _detector() -> KeypointDetector:
    """构造不加载模型的检测器实例（只测纯 CV 计算路径）。"""
    return KeypointDetector.__new__(KeypointDetector)


def _sharp_crop(size: int = 300) -> np.ndarray:
    """高频棋盘格：模拟羽毛纹理清晰的锐利鸟身。"""
    xs = np.arange(size)
    board = (((xs[:, None] // 3) + (xs[None, :] // 3)) % 2 * 255).astype(np.uint8)
    return np.repeat(board[:, :, None], 3, axis=2)


def _blurry_crop(size: int = 300) -> np.ndarray:
    """平滑渐变：模拟严重虚焦，几乎无梯度。"""
    ramp = np.linspace(90, 110, size, dtype=np.float32)
    img = np.repeat(ramp[None, :], size, axis=0).astype(np.uint8)
    return np.repeat(img[:, :, None], 3, axis=2)


# 关键点布局：双眼可见度极低、鸟喙清晰可见（真实误删样本的典型形态）
LOW_VIS_KWARGS = dict(
    left_eye=(0.40, 0.35), right_eye=(0.45, 0.35), beak=(0.60, 0.45),
    left_eye_vis=0.08, right_eye_vis=0.05, beak_vis=0.92,
)


def test_low_visibility_branch_does_not_raise():
    """双眼不可见分支可达且不再抛 NameError。"""
    det = _detector()
    result = det._calculate_head_sharpness(
        _sharp_crop(), **LOW_VIS_KWARGS, box=None, seg_mask=None)
    assert isinstance(result, float)


def test_sharp_photo_without_visible_eyes_scores_above_hard_gate():
    """看不到眼但画面锐利 → 锐度必须超过 V2 硬门槛 100，不再被判废。"""
    det = _detector()
    score = det._calculate_head_sharpness(
        _sharp_crop(), **LOW_VIS_KWARGS, box=None, seg_mask=None)
    assert score > 100.0, f"锐利照片仍被归零/低估: {score}"


def test_blurry_photo_without_visible_eyes_still_rejected():
    """真糊的照片仍拿 0 锐度——修复不是无脑放行。"""
    det = _detector()
    score = det._calculate_head_sharpness(
        _blurry_crop(), **LOW_VIS_KWARGS, box=None, seg_mask=None)
    assert score == 0.0, f"虚焦照片不应拿到锐度分: {score}"


def test_low_visibility_penalty_applied():
    """低可见度结果应为同区域正常计算的 0.8 倍（LOW_VIS_PENALTY）。"""
    det = _detector()
    crop = _sharp_crop()
    low = det._calculate_head_sharpness(
        crop, **LOW_VIS_KWARGS, box=None, seg_mask=None)
    # 对照组必须落在**同一只眼**上：LOW_VIS_KWARGS 里 left_eye_vis(0.08) >
    # right_eye_vis(0.05)，低可见度分支取的是左眼，故这里让左眼可见。
    # 若改让右眼可见，两条路径的眼-喙距离不同 → 半径不同 → 头部 ROI 不同，
    # 锐度尺寸补偿会按不同 ROI 扣分，0.8 的比例自然不成立（那是对照组的
    # 构造错误，不是惩罚系数失效）。
    # The control must use the SAME eye: with left(0.08) > right(0.05) the
    # low-visibility branch picks the left eye, so make the left eye visible
    # here. Picking the other eye changes the eye-beak distance, hence the
    # radius and the head ROI — and the size compensation then scales the two
    # paths differently, breaking the 0.8 ratio for reasons unrelated to it.
    normal = det._calculate_head_sharpness(
        crop,
        left_eye=LOW_VIS_KWARGS["left_eye"], right_eye=LOW_VIS_KWARGS["right_eye"],
        beak=LOW_VIS_KWARGS["beak"],
        left_eye_vis=0.80, right_eye_vis=0.05,   # 左眼可见 → 与低可见度分支同一只眼
        beak_vis=0.92, box=None, seg_mask=None)
    assert normal > 0
    assert low == pytest.approx(normal * 0.8, rel=0.02)


def test_beak_visible_no_longer_scores_below_beak_hidden():
    """
    修复前的逻辑倒置：喙可见(0★) 反而低于喙不可见(1★)。

    喙不可见时走 angle_poor 门返回 1★；喙可见时绕过该门，
    若锐度为 0 就掉进 low_sharpness 判 0★——看到的信息更多反而更低分。
    锐度不再恒为 0 之后，倒置必须消失。
    """
    common = dict(key="k", detected=True, confidence=0.9, topiq=5.0, best_eye=0.1)

    beak_hidden = gate_photo(PhotoMetricsV2(
        norm_sharpness=0.0, beak_vis=0.1, **common))
    assert beak_hidden is not None and beak_hidden.rating == 1

    # 喙可见 + 修复后算得出的真实锐度 → 进入排序池（gate 返回 None）
    beak_visible = gate_photo(PhotoMetricsV2(
        norm_sharpness=380.0, beak_vis=0.92, **common))
    assert beak_visible is None, "喙清晰且锐度达标的照片不应被硬门槛拦下"


def test_eye_cap_still_limits_to_two_stars():
    """产品决策：救回的照片仍受眼睛可见度封顶约束，最高 2★。"""
    from core.rating_quota import assign_ratings

    photos = [
        PhotoMetricsV2(key=f"p{i}", detected=True, confidence=0.9,
                       norm_sharpness=900.0 - i, topiq=8.0, best_eye=0.1,
                       beak_vis=0.9)
        for i in range(10)
    ]
    results = assign_ratings(photos, quota3=50.0, quota2=30.0)
    assert all(r.rating <= 2 for r in results.values()), \
        "眼睛可见度 <0.5 的照片不应拿到 3★"
