# -*- coding: utf-8 -*-
"""TOPIQ resize 两段式提速：辅助函数的单元测试。

TOPIQ resize two-stage speedup: unit tests for the helper function.
"""
import numpy as np

from iqa_scorer import _preshrink_if_large, _TOPIQ_PRESHRINK_SIZE


def test_large_image_gets_preshrunk():
    """明显大于阈值的图应该被降到 _TOPIQ_PRESHRINK_SIZE 见方。"""
    big = np.zeros((5504, 8256, 3), dtype=np.uint8)
    out = _preshrink_if_large(big)
    assert out.shape[:2] == (_TOPIQ_PRESHRINK_SIZE, _TOPIQ_PRESHRINK_SIZE)


def test_small_image_is_left_untouched():
    """小于阈值的图不应该被放大或修改尺寸。"""
    small = np.zeros((600, 800, 3), dtype=np.uint8)
    out = _preshrink_if_large(small)
    assert out.shape == small.shape


def test_preshrink_never_upscales_short_side():
    """长边超阈值、短边低于阈值时，短边必须保持原样，不能被放大。

    INTER_AREA 是降采样算法，放大时退化为近似最近邻会引入块状伪影；
    对 2000x1000 这类图，若把短边 1000 强行拉到 1536，后续 LANCZOS 精修
    也无法修复伪影，评分会产生旧路径（直接降采样）没有的漂移。

    When the long side exceeds the threshold but the short side doesn't,
    the short side must be left as-is — INTER_AREA degrades to near-nearest
    when upscaling and the artifacts survive the final LANCZOS pass.
    """
    wide = np.zeros((1000, 2000, 3), dtype=np.uint8)
    out = _preshrink_if_large(wide)
    assert out.shape[0] == 1000, "短边(高)不应被放大"
    assert out.shape[1] == _TOPIQ_PRESHRINK_SIZE, "长边(宽)应降到预降尺寸"

    tall = np.zeros((3000, 900, 3), dtype=np.uint8)
    out = _preshrink_if_large(tall)
    assert out.shape[0] == _TOPIQ_PRESHRINK_SIZE, "长边(高)应降到预降尺寸"
    assert out.shape[1] == 900, "短边(宽)不应被放大"


def test_preshrink_preserves_dtype_and_channels():
    """预降不应该改变 dtype 或通道数。"""
    big = (np.random.rand(4000, 6000, 3) * 255).astype(np.uint8)
    out = _preshrink_if_large(big)
    assert out.dtype == np.uint8
    assert out.shape[2] == 3


import os
import time

import pytest
from PIL import Image

from config import get_best_device
from iqa_scorer import get_iqa_scorer

_REAL_PHOTO = "img/_Z9W0960.jpg"
_TOPIQ_WEIGHT = "models/cfanet_iaa_ava_res50-3cd62bb3.pth"


def _reference_score_from_array(scorer, img_bgr):
    """按改动前的做法(整图直接 PIL LANCZOS)算一次分数，作为比对基准。"""
    import cv2
    import torch

    topiq_model = scorer._load_topiq()
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(img_rgb).resize((384, 384), Image.LANCZOS)
    tensor = scorer._transform(img).unsqueeze(0).to(scorer.device)
    if getattr(scorer, "_use_fp16", False):
        tensor = tensor.half()
    with torch.inference_mode():
        score = topiq_model(tensor, return_mos=True)
    return max(1.0, min(10.0, float(score.item() if hasattr(score, "item") else score)))


@pytest.mark.skipif(
    not os.path.exists(_TOPIQ_WEIGHT) or not os.path.exists(_REAL_PHOTO),
    reason="需要本机已有 TOPIQ 权重和样例照片",
)
def test_calculate_from_array_matches_reference_and_is_faster():
    import cv2

    scorer = get_iqa_scorer(device=get_best_device().type)
    img_bgr = cv2.imread(_REAL_PHOTO)
    assert img_bgr is not None, f"无法读取测试照片: {_REAL_PHOTO}"

    # 预热(排除模型加载和首次调用的编译/缓存开销，只比较 resize 策略本身)
    scorer.calculate_from_array(img_bgr)
    _reference_score_from_array(scorer, img_bgr)

    t0 = time.time()
    new_score = scorer.calculate_from_array(img_bgr)
    new_ms = (time.time() - t0) * 1000

    t0 = time.time()
    ref_score = _reference_score_from_array(scorer, img_bgr)
    ref_ms = (time.time() - t0) * 1000

    assert new_score is not None
    assert abs(new_score - ref_score) < 0.1, (
        f"两段式评分({new_score})和直接 LANCZOS 评分({ref_score})差异过大"
    )
    assert new_ms < ref_ms * 0.7, (
        f"两段式耗时({new_ms:.1f}ms)没有比直接 LANCZOS({ref_ms:.1f}ms)明显更快"
    )


def _reference_score_from_path(scorer, image_path):
    """按改动前的做法(整图直接 PIL LANCZOS)算一次分数，作为比对基准。"""
    import torch

    topiq_model = scorer._load_topiq()
    img = Image.open(image_path).convert("RGB").resize((384, 384), Image.LANCZOS)
    tensor = scorer._transform(img).unsqueeze(0).to(scorer.device)
    if getattr(scorer, "_use_fp16", False):
        tensor = tensor.half()
    with torch.inference_mode():
        score = topiq_model(tensor, return_mos=True)
    return max(1.0, min(10.0, float(score.item() if hasattr(score, "item") else score)))


@pytest.mark.skipif(
    not os.path.exists(_TOPIQ_WEIGHT) or not os.path.exists(_REAL_PHOTO),
    reason="需要本机已有 TOPIQ 权重和样例照片",
)
def test_calculate_aesthetic_matches_reference_and_is_faster():
    scorer = get_iqa_scorer(device=get_best_device().type)

    # 预热
    scorer.calculate_aesthetic(_REAL_PHOTO)
    _reference_score_from_path(scorer, _REAL_PHOTO)

    t0 = time.time()
    new_score = scorer.calculate_aesthetic(_REAL_PHOTO)
    new_ms = (time.time() - t0) * 1000

    t0 = time.time()
    ref_score = _reference_score_from_path(scorer, _REAL_PHOTO)
    ref_ms = (time.time() - t0) * 1000

    assert new_score is not None
    assert abs(new_score - ref_score) < 0.1, (
        f"两段式评分({new_score})和直接 LANCZOS 评分({ref_score})差异过大"
    )
    assert new_ms < ref_ms * 0.7, (
        f"两段式耗时({new_ms:.1f}ms)没有比直接 LANCZOS({ref_ms:.1f}ms)明显更快"
    )


def test_calculate_nima_is_alias_of_calculate_aesthetic():
    """calculate_nima() 只是 calculate_aesthetic() 的别名，改动不应该破坏这层委托。"""
    scorer = get_iqa_scorer(device=get_best_device().type)
    import inspect

    src = inspect.getsource(scorer.calculate_nima)
    assert "calculate_aesthetic" in src
