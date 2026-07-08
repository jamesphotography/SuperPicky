# -*- coding: utf-8 -*-
"""export_crop 修图集成 / enhance integration in export."""
import numpy as np

from core import crop_export
from core.enhance.options import EnhanceOptions


def test_enhance_called_with_rgb(tmp_path, monkeypatch):
    # 蓝色 BGR 图(B=255) → pipeline 应收到 RGB(R 通道=255 在 idx2)
    bgr = np.zeros((20, 20, 3), np.uint8)
    bgr[:, :, 0] = 255  # BGR 的蓝
    seen = {}

    def fake_enhance(img_rgb, opts, **kw):
        seen["first_px"] = img_rgb[0, 0].tolist()
        return img_rgb

    monkeypatch.setattr(crop_export, "_enhance", fake_enhance, raising=False)
    out = tmp_path / "o.jpg"
    crop_export.export_crop(
        "x", None, str(out), copy_exif=False,
        enhance_opts=EnhanceOptions(),
        _image_loader=lambda p: bgr,
    )
    # 喂给 pipeline 的应是 RGB:蓝色像素 → [0,0,255]
    assert seen["first_px"] == [0, 0, 255]
    assert out.exists()


def test_no_enhance_when_opts_none(tmp_path, monkeypatch):
    bgr = np.full((10, 10, 3), 50, np.uint8)
    called = {"v": False}
    monkeypatch.setattr(crop_export, "_enhance",
                        lambda *a, **k: called.__setitem__("v", True),
                        raising=False)
    out = tmp_path / "o.jpg"
    crop_export.export_crop("x", None, str(out), copy_exif=False,
                            _image_loader=lambda p: bgr)
    assert called["v"] is False
