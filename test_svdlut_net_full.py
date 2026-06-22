# -*- coding: utf-8 -*-
"""整网纯 torch 前向 vs 官方参考 net_out,PSNR ≥ 50dB(P2 整网)。"""
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


@pytest.mark.skipif(not (os.path.exists(FIX) and os.path.exists(_wpath())),
                    reason="需 P0 夹具 + 权重")
def test_full_net_matches_reference():
    d = np.load(FIX)
    net = SVDLUTNet().eval()
    sd = torch.load(_wpath(), map_location="cpu", weights_only=True)
    net.load_state_dict(sd.get("state_dict", sd), strict=False)
    with torch.no_grad():
        out = net(torch.from_numpy(d["img"]))
    if isinstance(out, (tuple, list)):
        out = out[0]
    mse = float(np.mean((out.numpy() - d["net_out"]) ** 2))
    psnr = 99.0 if mse < 1e-12 else 10 * np.log10(1.0 / mse)
    assert psnr >= 50.0, f"PSNR={psnr:.1f} < 50"
