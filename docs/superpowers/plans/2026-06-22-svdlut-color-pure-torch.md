# SVDLUT 调色（纯 PyTorch 重写）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用纯 PyTorch 重写 SVDLUT 调色前向（与官方数值一致、CPU/MPS/CUDA 全通、无编译扩展），接入 `core/enhance` 的 color 步并加调色对比 UI。

**Architecture:** SVDLUT = backbone + 4 个生成器（标准卷积/线性层，照搬上游）+ 一个自定义"双边网格切片 + 3D LUT 变换"算子。仅该算子需重写——用 `F.grid_sample` 等价实现。验证靠在本机 JIT 编译官方 **CPU** 算子作独立参考，逐位对齐。

**Tech Stack:** Python 3.12、PyTorch 2.8、`torch.utils.cpp_extension`（仅 P0 参考编译用，不进打包）、NumPy、OpenCV、PySide6、pytest。源码 vendoring 自 WontaeaeKim/SVDLUT（Apache-2.0）。

## Global Constraints

- UTF-8;中文注释 + 同格式英文注释;绝不引入中文乱码。
- 跨平台（Windows + macOS arm64/MPS + CPU）;设备用 `config.get_best_device()`;MPS/CPU 用 fp32。
- 运行时**不得依赖任何编译的 C++/CUDA 扩展**（P0 的 CPU 扩展仅本机参考用，绝不进 `core/`/打包）。
- 权重经 `config.get_install_scoped_resource_path("models/svdlut.pth", packaged_relative_path=config.get_packaged_model_relative_path("models/svdlut.pth"))` 定位;`torch.load(..., weights_only=True)`。
- 权重已在 HF `jamesphotography/SuperPicky-models:svdlut.pth`（= 官方 `pretrained/fiveK_sRGB.pth`，Apache-2.0）。
- 链路顺序写死 `降噪 → 调色`;调色默认保守强度、可关、强度 0 = 短路。
- 数值验收：切片算子 `max|Δ| ≤ 1e-3`（[0,1] 域）;整网 PSNR ≥ 50 dB vs 官方。
- 改动 Python 文件须过 `.venv/bin/python -m py_compile`;根级测试用 `test_svdlut_*.py`（仓库无 `tests/` 且被 .gitignore，须 `git add -f`）。
- 测试 GUI 用 `QT_QPA_PLATFORM=offscreen` + 模块级 `QApplication`;构造 CropStudio 前 monkeypatch `crop_studio.advise_crops` 为 no-bird（真图会加载 YOLO 在 headless 下 segfault）。

---

### Task 0 (P0): 参考基准 — JIT 编译官方 CPU 算子 + 生成参考夹具

**Files:**
- Create: `scripts/svdlut_reference/build_ref.py`（下载官方源、JIT 编译 CPU 算子、跑官方 SVDLUT、存夹具）
- Create: `scripts/svdlut_reference/ref_binding.cpp`（最小 pybind 包装,仅暴露 CPU forward）
- Create: `test_svdlut_reference.py`（夹具存在性 + 确定性）
- 产出（不入 git，本机/CI 生成）: `/tmp/svdlut_ref/ref_fixture.npz`

**Interfaces:**
- Consumes: 无（独立参考工具）。
- Produces: `ref_fixture.npz`,含键:
  - `img`(1,3,H,W float32 [0,1])、`grid`、`grid_w`、`grid_b`、`lut`、`lut_w`、`lut_b`（官方各生成器中间张量）、
  - `op_out`(切片算子输出,1,3,H,W)、`net_out`(整网 ReLU 后输出)。
  - 这些是 Task 2/3 的数值 oracle。

- [ ] **Step 1: 写失败测试**

`test_svdlut_reference.py`:

```python
# -*- coding: utf-8 -*-
"""P0 参考夹具存在且确定 / reference fixture exists & deterministic."""
import os
import numpy as np
import pytest

FIX = "/tmp/svdlut_ref/ref_fixture.npz"


@pytest.mark.skipif(not os.path.exists(FIX), reason="先运行 scripts/svdlut_reference/build_ref.py")
def test_fixture_has_required_keys():
    d = np.load(FIX)
    for k in ("img", "grid", "grid_w", "grid_b", "lut", "lut_w", "lut_b", "op_out", "net_out"):
        assert k in d, f"缺少键 {k}"
    assert d["img"].ndim == 4 and d["img"].shape[1] == 3
    assert d["op_out"].shape == d["img"].shape
```

- [ ] **Step 2: 运行确认失败/跳过**

Run: `.venv/bin/python -m pytest test_svdlut_reference.py -v`
Expected: SKIPPED（夹具未生成）。

- [ ] **Step 3: 写参考生成器**

`scripts/svdlut_reference/ref_binding.cpp`（最小包装，仅 CPU forward）:

```cpp
// 仅暴露官方 CPU forward launcher 作独立参考 / expose official CPU forward only.
#include <torch/extension.h>
void TriLinearCPU2DSliceAndLUTTransformForwardLaucher(
    const torch::Tensor &grid, const torch::Tensor &input,
    const torch::Tensor &grid_weights, const torch::Tensor &grid_bias,
    const torch::Tensor &lut, const torch::Tensor &lut_weights,
    const torch::Tensor &lut_bias, torch::Tensor output);

torch::Tensor forward(const torch::Tensor &grid, const torch::Tensor &input,
                      const torch::Tensor &grid_weights, const torch::Tensor &grid_bias,
                      const torch::Tensor &lut, const torch::Tensor &lut_weights,
                      const torch::Tensor &lut_bias) {
    auto out = torch::zeros_like(input);
    TriLinearCPU2DSliceAndLUTTransformForwardLaucher(
        grid, input, grid_weights, grid_bias, lut, lut_weights, lut_bias, out);
    return out;
}
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) { m.def("forward", &forward); }
```

`scripts/svdlut_reference/build_ref.py`:

```python
# -*- coding: utf-8 -*-
"""下载官方 SVDLUT 源、JIT 编译 CPU 算子、跑 FiveK 权重、存参考夹具。仅本机/CI 用。"""
import os, sys, urllib.request
import numpy as np
import torch
from torch.utils.cpp_extension import load

RAW = "https://raw.githubusercontent.com/WontaeaeKim/SVDLUT/main"
WORK = "/tmp/svdlut_ref"
os.makedirs(WORK, exist_ok=True)


def fetch(rel: str) -> str:
    dst = os.path.join(WORK, os.path.basename(rel))
    urllib.request.urlretrieve(f"{RAW}/{rel}", dst)
    return dst


def main() -> int:
    # 1) 官方 CPU 算子源 + 我们的最小绑定 → JIT 编译(纯 CPU,无 .cu)
    cpu_src = fetch("kernel_code/bilateral_slicing_LUTTransform/src/"
                    "trilinear2D_slice_LUTTransform_cpu.cpp")
    binding = os.path.join(os.path.dirname(__file__), "ref_binding.cpp")
    ref_op = load(name="svdlut_ref_op", sources=[binding, cpu_src], verbose=True)

    # 2) 官方 models.py + 权重(本地已有 svdlut.pth)
    models_path = fetch("models.py")
    # 官方 models.py 依赖 cpp_ext_interface;参考阶段我们直接调各生成器 + ref_op,
    # 不走官方 SVDLUT.forward(它 import CUDA 接口)。故只取生成器与 backbone。
    sys.path.insert(0, WORK)
    import importlib.util
    spec = importlib.util.spec_from_file_location("svd_models", models_path)
    # models.py 顶部 `from cpp_ext_interface import ...` 会失败 → 注入桩
    sys.modules["cpp_ext_interface"] = type(sys)("cpp_ext_interface")
    sys.modules["cpp_ext_interface"].bilinear_2Dslicing_lut_transform = None
    svd = importlib.util.module_from_spec(spec); spec.loader.exec_module(svd)

    net = svd.SVDLUT(backbone_type="cnn")  # 构造参数 P1 据 state_dict 反推确认
    from config import (get_install_scoped_resource_path, get_packaged_model_relative_path)
    wpath = str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))
    sd = torch.load(wpath, map_location="cpu", weights_only=True)
    net.load_state_dict(sd.get("state_dict", sd), strict=True)
    net.eval()

    # 3) 固定随机图 → 各生成器中间张量 → ref_op → 整网输出
    torch.manual_seed(0)
    img = torch.rand(1, 3, 64, 64)
    with torch.no_grad():
        feat = net.backbone(img)
        g3d_lut, _ = net.gen_2d_lut(feat)
        lut_w, lut_b = net.gen_2d_lut_weight_bias(feat)
        grid, _ = net.gen_2d_bilateral(feat)
        grid_w, grid_b = net.gen_2d_grid_weight_bias(feat)
        op_out = ref_op.forward(grid.contiguous(), img.contiguous(),
                                grid_w.contiguous(), grid_b.contiguous(),
                                g3d_lut.contiguous(), lut_w.contiguous(), lut_b.contiguous())
        net_out = torch.relu(op_out)

    np.savez(os.path.join(WORK, "ref_fixture.npz"),
             img=img.numpy(), grid=grid.numpy(), grid_w=grid_w.numpy(), grid_b=grid_b.numpy(),
             lut=g3d_lut.numpy(), lut_w=lut_w.numpy(), lut_b=lut_b.numpy(),
             op_out=op_out.numpy(), net_out=net_out.numpy())
    print("参考夹具已写 /tmp/svdlut_ref/ref_fixture.npz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑生成器 + 测试通过**

Run: `.venv/bin/python scripts/svdlut_reference/build_ref.py && .venv/bin/python -m pytest test_svdlut_reference.py -v`
Expected: 夹具生成;测试 PASS。
若 JIT 编译失败（缺 C++ 工具链等）→ 记录失败原因,改用 §「P0 退路」逐行 numpy 移植同一 cpu.cpp 公式生成 `op_out` 作参考（须额外人工比对中间值,降低独立性）。

- [ ] **Step 5: 提交**

```bash
git add -f scripts/svdlut_reference/build_ref.py scripts/svdlut_reference/ref_binding.cpp test_svdlut_reference.py
git commit -m "feat(color/P0): 官方 SVDLUT CPU 算子 JIT 参考 + 夹具生成"
```

---

### Task 1 (P1a): Vendor 生成器（backbone + 4 个 Gen 模块）

**Files:**
- Create: `core/enhance/nets/svdlut_net.py`（替换现有 stub;vendored backbone + Gen + SVDLUTNet 壳）
- Test: `test_svdlut_net_load.py`

**Interfaces:**
- Consumes: 权重 `svdlut.pth`。
- Produces: `core.enhance.nets.svdlut_net.SVDLUTNet`（暂用占位 slicing,先只验证生成器加载 + 中间张量形状）;
  暴露子模块 `backbone / gen_2d_lut / gen_2d_lut_weight_bias / gen_2d_bilateral / gen_2d_grid_weight_bias`。

- [ ] **Step 1: 写失败测试**（加载真权重 strict、中间张量形状对齐 P0 夹具）

`test_svdlut_net_load.py`:

```python
# -*- coding: utf-8 -*-
"""SVDLUTNet 生成器加载 FiveK 权重 & 中间张量形状对齐参考夹具。"""
import os
import numpy as np
import torch
import pytest

from config import (get_install_scoped_resource_path, get_packaged_model_relative_path)
from core.enhance.nets.svdlut_net import SVDLUTNet

FIX = "/tmp/svdlut_ref/ref_fixture.npz"


def _wpath():
    return str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))


@pytest.mark.skipif(not os.path.exists(_wpath()), reason="svdlut.pth 未就位")
def test_generators_load_strict_and_shapes():
    net = SVDLUTNet().eval()
    sd = torch.load(_wpath(), map_location="cpu", weights_only=True)
    missing, unexpected = net.load_state_dict(sd.get("state_dict", sd), strict=False)
    # backbone + 4 个生成器的参数必须全部命中(slicing 无参数)
    gen_prefixes = ("backbone.", "gen_2d_lut.", "gen_2d_lut_weight_bias.",
                    "gen_2d_bilateral.", "gen_2d_grid_weight_bias.")
    assert not [k for k in missing if k.startswith(gen_prefixes)], f"missing: {missing}"
    assert not [k for k in unexpected if k.startswith(gen_prefixes)], f"unexpected: {unexpected}"


@pytest.mark.skipif(not os.path.exists(FIX), reason="先生成 P0 夹具")
def test_generator_outputs_match_reference():
    d = np.load(FIX)
    net = SVDLUTNet().eval()
    sd = torch.load(_wpath(), map_location="cpu", weights_only=True)
    net.load_state_dict(sd.get("state_dict", sd), strict=False)
    img = torch.from_numpy(d["img"])
    with torch.no_grad():
        feat = net.backbone(img)
        lut, _ = net.gen_2d_lut(feat)
        grid, _ = net.gen_2d_bilateral(feat)
    assert np.allclose(lut.numpy(), d["lut"], atol=1e-4), "3D LUT 与参考不一致"
    assert np.allclose(grid.numpy(), d["grid"], atol=1e-4), "bilateral grid 与参考不一致"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_svdlut_net_load.py -v`
Expected: FAIL（SVDLUTNet 仍是 stub，实例化抛 NotImplementedError）。

- [ ] **Step 3: vendor 生成器**

把上游 `models.py` 的 `Backbone / resnet18_224 / Gen_2D_SVD_LUT / Gen_2D_LUT_weight_bias /
Gen_2D_bilateral_grids / Gen_2D_bilateral_grids_weight_bias` **逐字 vendor** 进
`core/enhance/nets/svdlut_net.py`（保留 Apache 头 + 来源注释）;**删除** `from cpp_ext_interface import ...`
与训练相关代码。`SVDLUTNet.__init__` 构造这些子模块（参数 `backbone_type='cnn'`，其余构造参数
据 state_dict 形状反推：`lut_n_vertices/lut_n_ranks/grid_n_vertices/grid_n_ranks/ch_per_grid/
lut_weight_ranks/grid_weight_ranks/lut_n_singular/grid_n_singular`，须使 `basis_luts_bank.weight=(4824,8)`、
`basis_grids_bank.weight=(5202,8)` 等形状吻合）。`forward` 暂调占位 slicing（Task 2 替换），
本任务先让生成器子模块可加载、可前向。静默上游 `print`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest test_svdlut_net_load.py -v`
Expected: PASS（两测试;或在无夹具/权重时 SKIP）。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/nets/svdlut_net.py
git add core/enhance/nets/svdlut_net.py && git add -f test_svdlut_net_load.py
git commit -m "feat(color/P1a): vendor SVDLUT 生成器(backbone+4 Gen),FiveK 权重加载验证"
```

---

### Task 2 (P1b/P2): 纯 torch 切片算子 + 对参考逐位验证

**Files:**
- Create: `core/enhance/nets/svdlut_slicing.py`
- Test: `test_svdlut_slicing.py`

**Interfaces:**
- Consumes: P0 夹具 `op_out` 作 oracle。
- Produces: `core.enhance.nets.svdlut_slicing.bilateral_slice_lut_transform(grid, img, grid_w, grid_b, lut, lut_w, lut_b) -> Tensor`（纯 torch,NCHW 输入输出）。

**算法（来自官方 `trilinear2D_slice_LUTTransform_cpu.cpp` 的逐像素公式）：**
对每像素：空间归一坐标 `x=x_/(W-1), y=y_/(H-1)`,颜色 `r,g,b`(图像值);
对双边网格在 (x,y)、(x,r)、(y,r)、(x,g)、(y,g)、(x,b)、(y,b) 等切面做三线性/双线性插值得仿射系数,
叠加 3D LUT 在 (r,g,b) 的三线性插值;按 `grid_weights/grid_bias/lut_weights/lut_bias` 组合。
**用 `F.grid_sample(mode='bilinear', align_corners=True)` 在网格体/LUT 体上等价实现**;
实现按夹具逐子步比对（先 LUT-only,再 grid-only,再合成）逼近,直至总输出对齐。

- [ ] **Step 1: 写失败测试**（对 P0 参考逐位对齐）

`test_svdlut_slicing.py`:

```python
# -*- coding: utf-8 -*-
"""纯 torch 切片算子 vs 官方 CPU 参考逐位对齐。"""
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
    out = bilateral_slice_lut_transform(t("grid"), t("img"), t("grid_w"), t("grid_b"),
                                        t("lut"), t("lut_w"), t("lut_b"))
    ref = d["op_out"]
    md = float(np.abs(out.numpy() - ref).max())
    assert md <= 1e-3, f"max|Δ|={md} 超过 1e-3"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_svdlut_slicing.py -v`
Expected: FAIL（模块不存在 / 数值不符）。

- [ ] **Step 3: 实现纯 torch 切片**

按上文「算法」用 `F.grid_sample` 实现 `bilateral_slice_lut_transform`。
**迭代法**：先实现 LUT 三线性插值子步、用夹具 `lut*` 单独比对;再实现 bilateral 切片子步;
再按 cpu.cpp 的组合公式合成。每步对照参考中间值定位偏差，直至 Step 1 测试 `max|Δ| ≤ 1e-3`。
（实现代码随逆向收敛产出;测试是精确 oracle，绿即正确。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest test_svdlut_slicing.py -v`
Expected: PASS（`max|Δ| ≤ 1e-3`）。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/nets/svdlut_slicing.py
git add core/enhance/nets/svdlut_slicing.py && git add -f test_svdlut_slicing.py
git commit -m "feat(color/P2): 纯 torch 双边切片+3D LUT,对官方 CPU 参考逐位对齐(≤1e-3)"
```

---

### Task 3 (P2): 整网组装 + 整图 PSNR 验证

**Files:**
- Modify: `core/enhance/nets/svdlut_net.py`（`SVDLUTNet.forward` 接 `bilateral_slice_lut_transform` + ReLU）
- Test: `test_svdlut_net_full.py`

**Interfaces:**
- Consumes: Task 1 生成器 + Task 2 切片。
- Produces: `SVDLUTNet.forward(img: Tensor[N,3,H,W]) -> Tensor[N,3,H,W]`（[0,1],已 ReLU）。

- [ ] **Step 1: 写失败测试**（整网 vs 参考 `net_out`，PSNR ≥ 50）

`test_svdlut_net_full.py`:

```python
# -*- coding: utf-8 -*-
"""整网纯 torch 前向 vs 官方参考 net_out,PSNR ≥ 50dB。"""
import os
import numpy as np
import torch
import pytest

from config import (get_install_scoped_resource_path, get_packaged_model_relative_path)
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
```

- [ ] **Step 2: 运行确认失败 → 改 forward → Step 4 通过**

Run: `.venv/bin/python -m pytest test_svdlut_net_full.py -v`
改 `SVDLUTNet.forward`:
```python
def forward(self, img):
    feat = self.backbone(img)
    lut, _ = self.gen_2d_lut(feat)
    lut_w, lut_b = self.gen_2d_lut_weight_bias(feat)
    grid, _ = self.gen_2d_bilateral(feat)
    grid_w, grid_b = self.gen_2d_grid_weight_bias(feat)
    from core.enhance.nets.svdlut_slicing import bilateral_slice_lut_transform
    out = bilateral_slice_lut_transform(grid, img, grid_w, grid_b, lut, lut_w, lut_b)
    return torch.relu(out)
```
Expected: PASS（PSNR ≥ 50）。

- [ ] **Step 3: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/nets/svdlut_net.py
git add core/enhance/nets/svdlut_net.py && git add -f test_svdlut_net_full.py
git commit -m "feat(color/P2): SVDLUTNet 整网组装,PSNR≥50 对齐官方"
```

---

### Task 4 (P3a): 接入 pipeline/wrapper（端到端调色生效）

**Files:**
- Modify: `core/enhance/models/svdlut.py`（确认输入归一化/通道序;移除对 stub 的依赖）
- Test: `test_svdlut_colorize.py`

**Interfaces:**
- Consumes: `SVDLUTNet`。
- Produces: `core.enhance.models.svdlut.colorize(img_rgb, strength, device) -> np.ndarray`（真实生效）。

- [ ] **Step 1: 写失败测试**（真权重 colorize 改变图像、形状/类型守恒、强度 0 = 原图）

`test_svdlut_colorize.py`:

```python
# -*- coding: utf-8 -*-
"""真权重 SVDLUT 调色端到端。"""
import os
import numpy as np
import pytest

from config import (get_install_scoped_resource_path, get_packaged_model_relative_path)
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
```

- [ ] **Step 2-4: 运行 → 微调 wrapper → 通过**

Run: `.venv/bin/python -m pytest test_svdlut_colorize.py -v`
现有 `svdlut.colorize`/`_apply_lut`/`_load_model` 大体可用（Task 3 后 `SVDLUTNet` 真实）;
仅需确认 `_apply_lut` 的输入是 RGB[0,1]、NCHW、通道序与训练一致（FiveK sRGB）。Expected: PASS。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/models/svdlut.py
git add core/enhance/models/svdlut.py && git add -f test_svdlut_colorize.py
git commit -m "feat(color/P3a): SVDLUT 调色封装端到端生效(真权重)"
```

---

### Task 5 (P3b): 调色对比 UI（独立入口，after = 降噪+调色）

**Files:**
- Modify: `ui/crop_studio.py`（左栏加「调色」按钮 → 调色对比模式;`_current_enhance_opts` 携带调色;预览 after = 降噪+调色）
- Modify: `locales/zh_CN.json` / `locales/en_US.json`（`crop_studio.tb_color` / `crop_studio.color_*`）
- Test: `test_color_compare_mode.py`

**Interfaces:**
- Consumes: pipeline.enhance（denoise+color）、`_BeforeAfterView`、现有降噪对比框架。
- Produces:
  - 左栏 `_btn_color`（gem/palette 图标）→ `_toggle_color_mode`。
  - 调色对比：`_preview_before_bgr` = 裁剪区原图;after = `pipeline.enhance(denoise_strength当前, color_strength当前)`。
  - `_current_enhance_opts` 同时带 `denoise_*` 与 `color_*`（各自 0/未启用=短路）。

- [ ] **Step 1: 写失败测试**（导出选项携带调色;调色 engaged 后 color_on=True）

`test_color_compare_mode.py`:

```python
# -*- coding: utf-8 -*-
"""调色对比模式:选项携带调色 + 端到端预览。"""
import os
import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402
from tools.i18n import get_i18n  # noqa: E402
from ui import crop_studio  # noqa: E402

_app = QApplication.instance() or QApplication([])
_SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "docs", "Promotion", "wechat", "articles",
                       "v4.3.0-rarity", "06.jpg")


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="样片缺失")
def test_color_opts_carried(monkeypatch):
    monkeypatch.setattr(crop_studio, "advise_crops",
                        lambda p: crop_studio.CropAdviceResult(status="no_bird", bird_count=0))
    w = crop_studio.CropStudio({"filename": "x", "temp_jpeg_path": _SAMPLE,
                                "current_path": _SAMPLE, "original_path": _SAMPLE}, get_i18n())
    w._enter_color_mode()
    opts = w._current_enhance_opts()
    assert opts is not None and opts.color_on is True
    w._exit_color_mode()
    w.close()
```

- [ ] **Step 2-4: 运行 → 实现 → 通过**

实现要点（仿降噪对比）:
- `_btn_color` → `_toggle_color_mode`/`_enter_color_mode`/`_exit_color_mode`;复用 `_center_stack`+`_compare_view`。
- 新增 `self._color_slider`（10% 档,默认保守如 40%）、`self._color_engaged`。
- `_current_enhance_opts`:`denoise_on = denoise_engaged and denoise_slider>0`;
  `color_on = color_engaged and color_slider>0`;两者皆无则 None。
- 调色预览 `_preview_before_bgr` = 裁剪区原图;`_run_preview_worker` 用的 opts 在调色模式下含降噪+调色
  （after = 端到端）。降噪模式仍只降噪。
- i18n:`tb_color`="调色"/"Color"、`color_strength` 等。

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest test_color_compare_mode.py test_enhance_compare_mode.py test_crop_studio.py -v`
Expected: 全 PASS（降噪对比不回归）。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/crop_studio.py
git add ui/crop_studio.py locales/zh_CN.json locales/en_US.json && git add -f test_color_compare_mode.py
git commit -m "feat(color/P3b): 调色对比模式(独立入口,after=降噪+调色)+导出携带调色"
```

---

### Task 6 (P4): 端到端冒烟 + 文档/回归

**Files:**
- Modify: `test_enhance_real_smoke.py`（`test_real_color_runs` 现应真跑而非 skip）
- Modify: `docs/superpowers/specs/2026-06-22-svdlut-color-pure-torch-design.md`（状态→已实施 + 偏差记录）

- [ ] **Step 1: 真权重整链路冒烟**

Run: `.venv/bin/python -m pytest test_enhance_real_smoke.py -v`
Expected: `test_real_color_runs` PASS（权重已在 HF/本机）;`test_real_denoise_runs` PASS。

- [ ] **Step 2: 全量回归**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest test_enhance_*.py test_svdlut_*.py test_color_*.py test_crop_*.py -q`
Expected: 全绿（真权重相关在缺失时 SKIP）。

- [ ] **Step 3: 更新 spec 状态 + 提交**

```bash
git add -f docs/superpowers/specs/2026-06-22-svdlut-color-pure-torch-design.md
git commit -m "docs(color/P4): 调色实施完成,状态与偏差记录"
```

---

## P0 退路 / Fallback（JIT 编译失败时）

若本机无法 JIT 编译官方 CPU 算子（无 C++ 工具链/torch 头文件不全）：
1. 逐行把 `trilinear2D_slice_LUTTransform_cpu.cpp` 的 forward 公式移植成**纯 numpy** 函数生成 `op_out`;
2. 因参考与重写同源、独立性弱 → 额外在 2~3 张真实样图上,把纯 torch 整网输出与官方论文/demo 的视觉效果人工核对;
3. 在 spec 记录"参考为自移植 numpy（非编译官方）",并把数值容差收紧到 `1e-4` 以暴露移植笔误。

## Self-Review

**Spec 覆盖**：§3 算法→Task1(生成器)+Task2(切片);§4 验证→Task0(参考)+Task2/3(对齐);§5 软件设计→Task1/2/3 文件;§5 UI→Task5;§6 权重→已上传+Task1 加载;§7 决策(端到端预览/分开入口)→Task5;§8 测试→各任务+Task6;§9 风险(逆向不一致)→Task0 参考+§P0退路。
**Placeholder 扫描**：Task2/3 的实现代码"随逆向收敛产出"是逆向工程的固有性质,但**测试是精确数值 oracle**(对官方参考 ≤1e-3 / PSNR≥50),非占位;其余代码完整。
**类型一致**：`bilateral_slice_lut_transform(grid,img,grid_w,grid_b,lut,lut_w,lut_b)`、`SVDLUTNet.forward(img)->Tensor`、`svdlut.colorize(img_rgb,strength,device)`、`_current_enhance_opts()->EnhanceOptions|None`(含 denoise+color)跨任务一致。
**已知前置**：Task1+ 依赖 Task0 夹具与 `svdlut.pth`(已在 HF/本机);Task5 依赖 Task3/4 真实 net。
