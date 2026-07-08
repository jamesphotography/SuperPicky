# -*- coding: utf-8 -*-
"""真权重 SVDLUT 调色端到端封装(P3a)。"""
import os

import numpy as np
import pytest

from config import (get_install_scoped_resource_path,
                    get_packaged_model_relative_path)
from core.enhance.models import svdlut


def _wpath():
    return str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))


@pytest.mark.skipif(not os.path.exists(_wpath()), reason="svdlut.pth 未就位")
def test_colorize_changes_image_and_preserves_shape():
    rng = np.random.default_rng(0)
    img = (rng.random((128, 128, 3)) * 255).astype(np.uint8)
    out = svdlut.colorize(img, strength=1.0, device="cpu")
    assert out.shape == img.shape and out.dtype == np.uint8
    assert int(np.abs(out.astype(int) - img.astype(int)).mean()) > 0  # 确有调色

    same = svdlut.colorize(img, strength=0.0, device="cpu")
    assert np.array_equal(same, img)  # 强度 0 = 原图
