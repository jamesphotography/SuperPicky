# -*- coding: utf-8 -*-
"""
P0 参考基准:在本机 JIT 编译官方 SVDLUT 的 CPU 算子 + 跑 FiveK 权重,产出参考夹具。
仅本机/CI 使用,绝不进 core/ 或打包。

Build an independent numerical reference for the pure-torch rewrite: JIT-compile
the official CPU slice+LUT op, run the official generators with FiveK weights, and
save intermediate/final tensors to /tmp/svdlut_ref/ref_fixture.npz.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.request

import numpy as np
import torch
from torch.utils.cpp_extension import load

RAW = "https://raw.githubusercontent.com/WontaeaeKim/SVDLUT/main"
WORK = "/tmp/svdlut_ref"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def fetch(rel: str) -> str:
    """下载官方源文件到 WORK,返回本地路径。"""
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, os.path.basename(rel))
    urllib.request.urlretrieve(f"{RAW}/{rel}", dst)
    return dst


def _weight_path() -> str:
    from config import (get_install_scoped_resource_path,
                        get_packaged_model_relative_path)
    return str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))


def main() -> int:
    os.makedirs(WORK, exist_ok=True)

    # 1) JIT 编译官方 CPU 算子(纯 CPU,无 .cu) / JIT-compile the official CPU op
    cpu_src = fetch("kernel_code/bilateral_slicing_LUTTransform/src/"
                    "trilinear2D_slice_LUTTransform_cpu.cpp")
    binding = os.path.join(_HERE, "ref_binding.cpp")
    print("[P0] JIT 编译官方 CPU 算子 …")
    ref_op = load(name="svdlut_ref_op", sources=[binding, cpu_src], verbose=True)

    # 2) 载入官方 models.py(桩掉 CUDA 接口) / load official models.py with CUDA iface stubbed
    models_path = fetch("models.py")
    stub = types.ModuleType("cpp_ext_interface")
    stub.bilinear_2Dslicing_lut_transform = None  # 我们不走官方 forward,只用生成器
    sys.modules["cpp_ext_interface"] = stub
    spec = importlib.util.spec_from_file_location("svd_models", models_path)
    svd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(svd)

    # 3) 构造网络(参数据 FiveK state_dict 形状反推) + strict 加载验证反推正确
    net = svd.SVDLUT(
        backbone_type="cnn", backbone_coef=8,
        lut_n_vertices=33, lut_n_ranks=8, lut_n_singular=8,
        grid_n_vertices=17, grid_n_ranks=8, grid_n_singular=8, ch_per_grid=2,
        lut_weight_ranks=8, grid_weight_ranks=8,
    )
    sd = torch.load(_weight_path(), map_location="cpu", weights_only=True)
    sd = sd.get("state_dict", sd)
    missing, unexpected = net.load_state_dict(sd, strict=False)
    if missing or unexpected:
        print("[P0][警告] 构造参数与权重不完全匹配:")
        print("  missing:", missing)
        print("  unexpected:", unexpected)
        return 2
    print("[P0] 权重 strict 加载成功(构造参数反推正确)")
    net.eval()

    # 4) 固定输入 → 各生成器中间张量 → CPU 参考算子 → 整网输出
    torch.manual_seed(0)
    img = torch.rand(1, 3, 64, 64)
    with torch.no_grad():
        feat = net.backbone(img)
        g3d_lut, _ = net.gen_2d_lut(feat)
        lut_w, lut_b = net.gen_2d_lut_weight_bias(feat)
        grid, _ = net.gen_2d_bilateral(feat)
        grid_w, grid_b = net.gen_2d_grid_weight_bias(feat)
        op_out = ref_op.forward(
            grid.contiguous(), img.contiguous(),
            grid_w.contiguous(), grid_b.contiguous(),
            g3d_lut.contiguous(), lut_w.contiguous(), lut_b.contiguous())
        net_out = torch.relu(op_out)

    out_path = os.path.join(WORK, "ref_fixture.npz")
    np.savez(
        out_path,
        img=img.numpy(), grid=grid.numpy(), grid_w=grid_w.numpy(), grid_b=grid_b.numpy(),
        lut=g3d_lut.numpy(), lut_w=lut_w.numpy(), lut_b=lut_b.numpy(),
        op_out=op_out.numpy(), net_out=net_out.numpy())
    print(f"[P0] 参考夹具已写 {out_path}")
    print(f"     img{tuple(img.shape)} grid{tuple(grid.shape)} lut{tuple(g3d_lut.shape)} "
          f"op_out{tuple(op_out.shape)} 范围[{float(op_out.min()):.3f},{float(op_out.max()):.3f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
