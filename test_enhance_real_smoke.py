# -*- coding: utf-8 -*-
"""真权重端到端冒烟(本地有权重时跑)/ real-weight smoke (local only)."""
import os

import numpy as np
import pytest

from config import (get_install_scoped_resource_path,
                    get_packaged_model_relative_path)
from core.enhance.options import EnhanceOptions
from core.enhance.pipeline import enhance


def _has(rel: str) -> bool:
    path = str(get_install_scoped_resource_path(
        rel, packaged_relative_path=get_packaged_model_relative_path(rel)))
    return os.path.exists(path)


@pytest.mark.skipif(not _has("models/scunet_color_real.pth"),
                    reason="SCUNet 权重未就位 / weight not present")
def test_real_denoise_runs():
    """真权重降噪端到端:形状/类型保持。"""
    rng = np.random.default_rng(0)
    img = (rng.random((256, 256, 3)) * 255).astype(np.uint8)
    out = enhance(img, EnhanceOptions(denoise_on=True, color_on=False))
    assert out.shape == img.shape and out.dtype == np.uint8


@pytest.mark.skipif(not _has("models/svdlut.pth"),
                    reason="SVDLUT 权重未就位/模型未就绪 / weight or net not ready")
def test_real_color_runs():
    """真权重调色端到端(SVDLUT 跨平台前向就绪后才会跑)。"""
    rng = np.random.default_rng(1)
    img = (rng.random((256, 256, 3)) * 255).astype(np.uint8)
    out = enhance(img, EnhanceOptions(denoise_on=False, color_on=True))
    assert out.shape == img.shape and out.dtype == np.uint8
