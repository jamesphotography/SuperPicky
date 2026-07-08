# -*- coding: utf-8 -*-
"""SCUNet 降噪封装:tiling 无缝 + 强度混合 / seamless tiling & blend."""
import numpy as np

from core.enhance.models import scunet


class _IdentityModel:
    """桩模型:对 NCHW 张量原样返回(验证 tiling 拼接无缝)。"""

    def __call__(self, x):
        return x

    def eval(self):
        return self

    def to(self, *_a, **_k):
        return self


def test_tiling_identity_reconstructs(monkeypatch):
    # 比 TILE 大的图,强制走多瓦片路径
    rng = np.random.default_rng(0)
    img = (rng.random((700, 900, 3)) * 255).astype(np.uint8)
    monkeypatch.setattr(scunet, "_load_model", lambda device: _IdentityModel())
    out = scunet.denoise(img, strength=1.0, device="cpu")
    # identity 去噪 + 满强度 → 应≈原图(边界拼接误差 ≤1)
    assert out.shape == img.shape
    assert np.abs(out.astype(int) - img.astype(int)).max() <= 1


def test_strength_blend(monkeypatch):
    img = np.full((300, 300, 3), 100, dtype=np.uint8)

    class _ConstModel:
        def __call__(self, x):
            return x * 0 + 200.0 / 255.0  # 「满降噪」恒为 200

        def eval(self):
            return self

        def to(self, *_a, **_k):
            return self

    monkeypatch.setattr(scunet, "_load_model", lambda device: _ConstModel())
    out = scunet.denoise(img, strength=0.5, device="cpu")
    assert np.allclose(out, 150, atol=2)


def test_strength_zero_returns_original(monkeypatch):
    img = np.full((40, 40, 3), 77, dtype=np.uint8)
    monkeypatch.setattr(scunet, "_load_model", lambda device: _IdentityModel())
    out = scunet.denoise(img, strength=0.0, device="cpu")
    assert np.array_equal(out, img)
