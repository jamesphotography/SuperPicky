# -*- coding: utf-8 -*-
"""SVDLUT 调色封装:强度混合与单例 / blend & singleton tests."""
import numpy as np

from core.enhance.models import svdlut


def test_strength_blend(monkeypatch):
    img = np.full((4, 4, 3), 100, dtype=np.uint8)
    graded = np.full((4, 4, 3), 200, dtype=np.uint8)
    # 桩:把「满调色」结果固定为 graded
    monkeypatch.setattr(svdlut, "_apply_lut", lambda im, model, device: graded)
    monkeypatch.setattr(svdlut, "_load_model", lambda device: object())

    out = svdlut.colorize(img, strength=0.5, device="cpu")
    # 0.5 混合:(1-0.5)*100 + 0.5*200 = 150
    assert np.allclose(out, 150, atol=1)
    assert out.dtype == np.uint8


def test_strength_zero_returns_original(monkeypatch):
    img = np.full((4, 4, 3), 100, dtype=np.uint8)
    monkeypatch.setattr(svdlut, "_apply_lut",
                        lambda im, model, device: np.full_like(im, 200))
    monkeypatch.setattr(svdlut, "_load_model", lambda device: object())
    out = svdlut.colorize(img, strength=0.0, device="cpu")
    assert np.array_equal(out, img)
