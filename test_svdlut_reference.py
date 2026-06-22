# -*- coding: utf-8 -*-
"""P0 参考夹具存在且字段完整 / reference fixture exists & complete.

夹具由 scripts/svdlut_reference/build_ref.py 生成(本机 JIT 编译官方 CPU 算子)。
缺失时跳过(CI 未生成夹具时)。
"""
import os

import numpy as np
import pytest

FIX = "/tmp/svdlut_ref/ref_fixture.npz"


@pytest.mark.skipif(not os.path.exists(FIX),
                    reason="先运行 scripts/svdlut_reference/build_ref.py 生成参考夹具")
def test_fixture_has_required_keys():
    d = np.load(FIX)
    for k in ("img", "grid", "grid_w", "grid_b", "lut", "lut_w", "lut_b",
              "op_out", "net_out"):
        assert k in d, f"缺少键 {k}"
    assert d["img"].ndim == 4 and d["img"].shape[1] == 3
    assert d["op_out"].shape == d["img"].shape
    assert d["net_out"].shape == d["img"].shape
    # net_out = relu(op_out) → 非负 / non-negative after ReLU
    assert float(d["net_out"].min()) >= 0.0
