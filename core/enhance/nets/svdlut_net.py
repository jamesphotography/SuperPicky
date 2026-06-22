# -*- coding: utf-8 -*-
"""
SVDLUT 网络结构占位 / SVDLUT architecture placeholder.

来源 / Source: https://github.com/WontaeaeKim/SVDLUT  (models.py)
许可证 / License: Apache-2.0 (见同目录 LICENSE-SVDLUT / see LICENSE-SVDLUT).

⚠️ 阻塞说明 / BLOCKER
官方 SVDLUT 的前向 `SVDLUT.forward` 依赖编译的 CUDA 自定义算子
`bilateral2D_slicing_LUTTransform`(见上游 `cpp_ext_interface.py` 与
`kernel_code/`),它是 **CUDA-only**,没有 CPU/MPS 实现。本项目要求跨平台
(Windows + macOS arm64/MPS + CPU)且保证 CPU/MPS 速度,故**不能直接 vendor**
上游 `models.py`。

可选解决路径(需与维护者决策,见 docs/superpowers/specs):
  A. 用纯 PyTorch(F.grid_sample)重写 bilateral-grid slicing + 3D LUT trilinear,
     并以官方 CUDA 内核输出 + 真权重做逐位验证后方可信任。
  B. 改用无自定义 CUDA 内核的纯 PyTorch 调色模型。
  C. 本期仅交付 SCUNet 降噪,调色作为后续增量。

在解决前,`SVDLUTNet()` 主动抛 NotImplementedError;`models/svdlut.py:colorize`
的调用方(pipeline)对此做优雅降级(跳过调色,不崩溃),保证 app 稳定。

The official SVDLUT forward depends on a compiled CUDA-only custom op, which
violates this project's cross-platform (Windows + macOS/MPS + CPU) constraint.
Until resolved (pure-torch reimplementation validated against the CUDA kernel,
or a model swap), instantiation raises NotImplementedError and the pipeline
degrades gracefully (skips color) instead of crashing.
"""
from __future__ import annotations


class SVDLUTNet:
    """SVDLUT 网络占位:实例化即抛错,直至跨平台前向就绪 / placeholder, raises until ready."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "SVDLUT 调色模型尚未就绪:官方实现依赖 CUDA-only 自定义算子,"
            "需纯 PyTorch 重写或更换模型(见 svdlut_net.py 头部说明)。"
            " / SVDLUT color model not ready: official impl is CUDA-only."
        )
