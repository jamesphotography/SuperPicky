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


def test_preshrink_preserves_dtype_and_channels():
    """预降不应该改变 dtype 或通道数。"""
    big = (np.random.rand(4000, 6000, 3) * 255).astype(np.uint8)
    out = _preshrink_if_large(big)
    assert out.dtype == np.uint8
    assert out.shape[2] == 3
