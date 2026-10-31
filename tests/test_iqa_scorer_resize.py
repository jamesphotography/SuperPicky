# -*- coding: utf-8 -*-
"""
IQA 美学评分器两段式等比例缩放提速与共享推理路径单元测试。

Unit tests for IQA aesthetic scorer two-stage aspect-ratio-preserving resize
speedup and shared inference path.
"""

import os
import time

import numpy as np
import pytest
from PIL import Image

from charm_model import CHARM_MAX_EDGE, CHARM_WEIGHT_NAMES, resize_for_charm
from config import get_best_device
from iqa_scorer import _TOPIQ_PRESHRINK_SIZE, _preshrink_if_large, get_iqa_scorer


def test_large_image_gets_preshrunk():
    """明显大于阈值的图应该按原图长宽比降到长边等于 _TOPIQ_PRESHRINK_SIZE。"""
    big = np.zeros((5504, 8256, 3), dtype=np.uint8)
    out = _preshrink_if_large(big)
    assert max(out.shape[:2]) == _TOPIQ_PRESHRINK_SIZE
    assert out.shape[0] == int(round(5504 * (_TOPIQ_PRESHRINK_SIZE / 8256.0)))
    assert out.shape[1] == _TOPIQ_PRESHRINK_SIZE


def test_small_image_is_left_untouched():
    """小于阈值的图不应该被放大或修改尺寸。"""
    small = np.zeros((600, 800, 3), dtype=np.uint8)
    out = _preshrink_if_large(small)
    assert out.shape == small.shape


def test_preshrink_never_upscales_short_side():
    """长边超阈值、短边低于阈值时，短边必须等比例缩小，绝不能被放大。"""
    wide = np.zeros((1000, 2000, 3), dtype=np.uint8)
    out = _preshrink_if_large(wide)
    assert out.shape[0] <= 1000, "短边(高)不应被放大"
    assert out.shape[1] == _TOPIQ_PRESHRINK_SIZE, "长边(宽)应降到预降尺寸"

    tall = np.zeros((3000, 900, 3), dtype=np.uint8)
    out = _preshrink_if_large(tall)
    assert out.shape[0] == _TOPIQ_PRESHRINK_SIZE, "长边(高)应降到预降尺寸"
    assert out.shape[1] <= 900, "短边(宽)不应被放大"


def test_preshrink_preserves_dtype_and_channels():
    """预降不应该改变 dtype 或通道数。"""
    big = (np.random.rand(4000, 6000, 3) * 255).astype(np.uint8)
    out = _preshrink_if_large(big)
    assert out.dtype == np.uint8
    assert out.shape[2] == 3


_REAL_PHOTO = "img/_Z9W0960.jpg"
_CHARM_WEIGHT = next(
    (
        os.path.join("models", n)
        for n in CHARM_WEIGHT_NAMES
        if os.path.exists(os.path.join("models", n))
    ),
    os.path.join("models", CHARM_WEIGHT_NAMES[0]),
)


def _reference_score_from_array(scorer, img_bgr):
    """按不走 cv2 INTER_AREA 预降、整图直接 PIL LANCZOS 算一次分数，作为比对基准。"""
    import cv2
    import torch

    charm_model = scorer._load_topiq()
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    img = resize_for_charm(Image.fromarray(img_rgb), max_edge=CHARM_MAX_EDGE)
    tensor = scorer._transform(img).unsqueeze(0)
    with torch.inference_mode():
        score = charm_model(tensor, return_mos=True)
    return max(1.0, min(10.0, float(score.item() if hasattr(score, "item") else score)))


def _min_ms_of(fn, repeats: int = 3) -> float:
    """多次执行取最小耗时（毫秒）。"""
    best = float("inf")
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


@pytest.mark.skipif(
    not os.path.exists(_CHARM_WEIGHT) or not os.path.exists(_REAL_PHOTO),
    reason="需要本机已有 Charm 权重和样例照片",
)
def test_calculate_from_array_matches_reference_and_is_faster():
    import cv2

    scorer = get_iqa_scorer(device=get_best_device().type)
    img_bgr = cv2.imread(_REAL_PHOTO)
    assert img_bgr is not None, f"无法读取测试照片: {_REAL_PHOTO}"

    # 预热(排除模型加载和首次调用的位置编码插值缓存开销，只比较 resize 策略本身)
    new_score = scorer.calculate_from_array(img_bgr)
    ref_score = _reference_score_from_array(scorer, img_bgr)

    new_ms = _min_ms_of(lambda: scorer.calculate_from_array(img_bgr))
    ref_ms = _min_ms_of(lambda: _reference_score_from_array(scorer, img_bgr))

    assert new_score is not None
    assert abs(new_score - ref_score) < 0.1, (
        f"两段式评分({new_score})和直接 LANCZOS 评分({ref_score})差异过大"
    )
    assert new_ms < ref_ms * 0.7, (
        f"两段式耗时({new_ms:.1f}ms)没有比直接 LANCZOS({ref_ms:.1f}ms)明显更快"
    )


def _reference_score_from_path(scorer, image_path):
    """按不走 cv2 INTER_AREA 预降、整图直接 PIL LANCZOS 算一次分数，作为比对基准。"""
    import torch

    charm_model = scorer._load_topiq()
    img = resize_for_charm(
        Image.open(image_path).convert("RGB"), max_edge=CHARM_MAX_EDGE
    )
    tensor = scorer._transform(img).unsqueeze(0)
    with torch.inference_mode():
        score = charm_model(tensor, return_mos=True)
    return max(1.0, min(10.0, float(score.item() if hasattr(score, "item") else score)))


@pytest.mark.skipif(
    not os.path.exists(_CHARM_WEIGHT) or not os.path.exists(_REAL_PHOTO),
    reason="需要本机已有 Charm 权重和样例照片",
)
def test_calculate_aesthetic_matches_reference_and_is_faster():
    scorer = get_iqa_scorer(device=get_best_device().type)

    new_score = scorer.calculate_aesthetic(_REAL_PHOTO)
    ref_score = _reference_score_from_path(scorer, _REAL_PHOTO)

    new_ms = _min_ms_of(lambda: scorer.calculate_aesthetic(_REAL_PHOTO))
    ref_ms = _min_ms_of(lambda: _reference_score_from_path(scorer, _REAL_PHOTO))

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


def test_public_methods_share_single_inference_path():
    """两个公开入口必须委托同一个 _score_pil_image()，推理细节只维护一处。"""
    import inspect

    from iqa_scorer import IQAScorer

    assert hasattr(IQAScorer, "_score_pil_image")
    assert "_score_pil_image" in inspect.getsource(IQAScorer.calculate_aesthetic)
    assert "_score_pil_image" in inspect.getsource(IQAScorer.calculate_from_array)


@pytest.mark.skipif(
    not os.path.exists(_CHARM_WEIGHT),
    reason="需要本机已有 Charm 权重",
)
def test_small_image_path_skips_numpy_roundtrip(monkeypatch, tmp_path):
    """小于预降阈值的图不得再走 np.array/Image.fromarray 整图往返。"""
    import iqa_scorer as iqa_module

    def _must_not_be_called(img_array):
        raise AssertionError("小图不应进入 _preshrink_if_large 预降路径")

    monkeypatch.setattr(iqa_module, "_preshrink_if_large", _must_not_be_called)

    small_path = tmp_path / "small.jpg"
    Image.new("RGB", (800, 600), color=(120, 140, 90)).save(small_path)

    scorer = get_iqa_scorer(device=get_best_device().type)
    score = scorer.calculate_aesthetic(str(small_path))
    assert score is not None, "小图评分不应因预降路径被触发而失败"
    assert 1.0 <= score <= 10.0
