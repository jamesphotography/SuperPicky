# -*- coding: utf-8 -*-
"""pipeline 编排与开关 gating 测试 / pipeline ordering & gating tests."""
import numpy as np
import pytest

from core.enhance.options import EnhanceOptions
from core.enhance import pipeline


def _img():
    return np.full((8, 8, 3), 100, dtype=np.uint8)


def test_order_denoise_before_color():
    calls = []

    def denoise_fn(img, strength, device, progress_cb=None):
        calls.append("denoise")
        return img

    def color_fn(img, strength, device):
        calls.append("color")
        return img

    pipeline.enhance(_img(), EnhanceOptions(),
                     denoise_fn=denoise_fn, color_fn=color_fn)
    assert calls == ["denoise", "color"]


def test_denoise_strength_zero_short_circuits():
    called = {"denoise": False}

    def denoise_fn(img, strength, device, progress_cb=None):
        called["denoise"] = True
        return img

    pipeline.enhance(_img(), EnhanceOptions(denoise_strength=0.0),
                     denoise_fn=denoise_fn, color_fn=lambda i, s, d: i)
    assert called["denoise"] is False


def test_color_off_skips_color():
    called = {"color": False}

    def color_fn(img, strength, device):
        called["color"] = True
        return img

    pipeline.enhance(_img(), EnhanceOptions(color_on=False),
                     denoise_fn=lambda i, s, d, progress_cb=None: i, color_fn=color_fn)
    assert called["color"] is False


def test_returns_ndarray_same_shape():
    out = pipeline.enhance(_img(), EnhanceOptions(),
                           denoise_fn=lambda i, s, d, progress_cb=None: i,
                           color_fn=lambda i, s, d: i)
    assert out.shape == (8, 8, 3)
    assert out.dtype == np.uint8


def test_unavailable_color_model_degrades_gracefully():
    """调色模型不可用(NotImplementedError)→ 跳过调色,返回降噪结果,不崩溃。"""
    img = _img()

    def color_unavailable(i, s, d):
        raise NotImplementedError("SVDLUT not ready")

    out = pipeline.enhance(img, EnhanceOptions(),
                           denoise_fn=lambda i, s, d, progress_cb=None: i + 1,
                           color_fn=color_unavailable)
    # 降噪生效(+1),调色被优雅跳过 / denoise applied, color skipped
    assert int(out[0, 0, 0]) == 101


def test_genuine_bug_still_propagates():
    """非「模型不可用」类异常(真 bug)不被掩盖,应向上抛出。"""
    def color_buggy(i, s, d):
        raise ValueError("real bug")

    with pytest.raises(ValueError):
        pipeline.enhance(_img(), EnhanceOptions(),
                         denoise_fn=lambda i, s, d, progress_cb=None: i,
                         color_fn=color_buggy)
