# -*- coding: utf-8 -*-
"""纯 torch 切片算子 vs 官方 CPU 参考逐位对齐(P2)。"""
import os

import numpy as np
import torch
import pytest

from core.enhance.nets.svdlut_slicing import bilateral_slice_lut_transform

FIX = "/tmp/svdlut_ref/ref_fixture.npz"


@pytest.mark.skipif(not os.path.exists(FIX), reason="先生成 P0 夹具")
def test_op_matches_reference():
    d = np.load(FIX)
    t = lambda k: torch.from_numpy(d[k])
    out = bilateral_slice_lut_transform(
        t("grid"), t("img"), t("grid_w"), t("grid_b"),
        t("lut"), t("lut_w"), t("lut_b"))
    ref = d["op_out"]
    md = float(np.abs(out.numpy() - ref).max())
    assert md <= 1e-3, f"max|Δ|={md} 超过 1e-3"
