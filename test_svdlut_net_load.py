# -*- coding: utf-8 -*-
"""SVDLUTNet 生成器加载 FiveK 权重 & 中间张量对齐参考夹具(P1)。"""
import os

import numpy as np
import torch
import pytest

from config import (get_install_scoped_resource_path,
                    get_packaged_model_relative_path)
from core.enhance.nets.svdlut_net import SVDLUTNet

FIX = "/tmp/svdlut_ref/ref_fixture.npz"


def _wpath():
    return str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))


@pytest.mark.skipif(not os.path.exists(_wpath()), reason="svdlut.pth 未就位")
def test_generators_load_strict():
    net = SVDLUTNet().eval()
    sd = torch.load(_wpath(), map_location="cpu", weights_only=True)
    missing, unexpected = net.load_state_dict(sd.get("state_dict", sd), strict=False)
    gen = ("backbone.", "gen_2d_lut.", "gen_2d_lut_weight_bias.",
           "gen_2d_bilateral.", "gen_2d_grid_weight_bias.")
    assert not [k for k in missing if k.startswith(gen)], f"missing: {missing}"
    assert not [k for k in unexpected if k.startswith(gen)], f"unexpected: {unexpected}"


@pytest.mark.skipif(not (os.path.exists(FIX) and os.path.exists(_wpath())),
                    reason="需 P0 夹具 + 权重")
def test_generator_outputs_match_reference():
    d = np.load(FIX)
    net = SVDLUTNet().eval()
    sd = torch.load(_wpath(), map_location="cpu", weights_only=True)
    net.load_state_dict(sd.get("state_dict", sd), strict=False)
    img = torch.from_numpy(d["img"])
    with torch.no_grad():
        feat = net.backbone(img)
        lut, _ = net.gen_2d_lut(feat)
        lut_w, lut_b = net.gen_2d_lut_weight_bias(feat)
        grid, _ = net.gen_2d_bilateral(feat)
        grid_w, grid_b = net.gen_2d_grid_weight_bias(feat)
    assert np.allclose(lut.numpy(), d["lut"], atol=1e-4), "3D LUT 与参考不一致"
    assert np.allclose(grid.numpy(), d["grid"], atol=1e-4), "bilateral grid 与参考不一致"
    assert np.allclose(lut_w.numpy(), d["lut_w"], atol=1e-4)
    assert np.allclose(grid_b.numpy(), d["grid_b"], atol=1e-4)
