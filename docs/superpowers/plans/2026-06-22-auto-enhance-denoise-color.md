# 自动修图（降噪 + 调色）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Crop Studio 全屏后期工作区中，对选中裁剪框在「裁剪后、导出前」执行非破坏性自动修图（SCUNet 盲降噪 → SVDLUT 空间感知调色），支持工作区实时预览 before/after。

**Architecture:** 新增纯算法层 `core/enhance/`（不依赖 Qt、不碰文件 I/O），编排固定链路 `denoise→color`，由 `crop_export` 与 `crop_studio` 共享。模型封装为懒加载单例，权重经 `config.get_install_scoped_resource_path` 定位、`config.get_best_device()` 选设备。预览跑降采样图、导出跑全分辨率，SCUNet 用 tiling 控显存。

**Tech Stack:** Python 3.13、PyTorch、timm（已在用）、OpenCV、NumPy、PySide6、pytest。模型源码 vendoring 自 cszn/SCUNet 与 WontaeaeKim/SVDLUT。

## Global Constraints

- UTF-8 优先；中文注释 + 同格式英文注释；绝不引入中文乱码。
- 跨平台（Windows + macOS）：路径用 `pathlib`/`os.path`；设备分支用 `config.get_best_device()`（macOS arm64=MPS，Intel mac/CPU=CPU，其他 CUDA>CPU）。
- 非破坏性：绝不覆盖原图；导出仍为 `*_crop.jpg`；EXIF 复制逻辑不变。
- 设备精度：CUDA 可半精度；MPS/CPU 一律 fp32。
- 模型权重经 HuggingFace 下载、落 `models/`，运行时经 `config.get_install_scoped_resource_path("models/<file>", config.get_packaged_model_relative_path("models/<file>"))` 定位。
- 默认预设：`denoise_on=True, denoise_strength=0.5, color_on=True, color_strength=0.4`（保守）。
- 链路顺序写死：`denoise → color`，用户仅开关/调强度。
- 改动 Python 文件须过 `.venv*/bin/python -m py_compile`。
- 许可证：SVDLUT = Apache-2.0（已确认）；SCUNet 落盘前须核验（见 Task 8）。

---

### Task 1: EnhanceOptions 与 pipeline 编排骨架（纯逻辑，可注入）

**Files:**
- Create: `core/enhance/__init__.py`
- Create: `core/enhance/options.py`
- Create: `core/enhance/pipeline.py`
- Test: `tests/enhance/test_pipeline.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `core.enhance.options.EnhanceOptions`（dataclass，字段见下）。
  - `core.enhance.pipeline.enhance(img_rgb, opts, *, denoise_fn=None, color_fn=None, device=None, progress_cb=None) -> np.ndarray`。
  - 默认 `denoise_fn`/`color_fn` 为 `None` 时延迟到真实封装（Task 4 接线）；本任务只测注入路径。

- [ ] **Step 1: 写失败测试**

`tests/enhance/test_pipeline.py`:

```python
# -*- coding: utf-8 -*-
"""pipeline 编排与开关 gating 测试 / pipeline ordering & gating tests."""
import numpy as np
import pytest

from core.enhance.options import EnhanceOptions
from core.enhance import pipeline


def _img():
    return np.full((8, 8, 3), 100, dtype=np.uint8)


def test_order_denoise_before_color():
    calls = []

    def denoise_fn(img, strength, device, progress_cb=None):
        calls.append("denoise")
        return img

    def color_fn(img, strength, device):
        calls.append("color")
        return img

    pipeline.enhance(_img(), EnhanceOptions(),
                     denoise_fn=denoise_fn, color_fn=color_fn)
    assert calls == ["denoise", "color"]


def test_denoise_strength_zero_short_circuits():
    called = {"denoise": False}

    def denoise_fn(img, strength, device, progress_cb=None):
        called["denoise"] = True
        return img

    pipeline.enhance(_img(), EnhanceOptions(denoise_strength=0.0),
                     denoise_fn=denoise_fn, color_fn=lambda i, s, d: i)
    assert called["denoise"] is False


def test_color_off_skips_color():
    called = {"color": False}

    def color_fn(img, strength, device):
        called["color"] = True
        return img

    pipeline.enhance(_img(), EnhanceOptions(color_on=False),
                     denoise_fn=lambda i, s, d, progress_cb=None: i, color_fn=color_fn)
    assert called["color"] is False


def test_returns_ndarray_same_shape():
    out = pipeline.enhance(_img(), EnhanceOptions(),
                           denoise_fn=lambda i, s, d, progress_cb=None: i,
                           color_fn=lambda i, s, d: i)
    assert out.shape == (8, 8, 3)
    assert out.dtype == np.uint8
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_pipeline.py -v`
Expected: FAIL（`ModuleNotFoundError: core.enhance`）。

- [ ] **Step 3: 写最小实现**

`core/enhance/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""自动修图后期管线 / Auto post-processing enhance pipeline."""
```

`core/enhance/options.py`:

```python
# -*- coding: utf-8 -*-
"""修图选项数据类 / Enhance options dataclass."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EnhanceOptions:
    """
    自动修图选项 / Auto-enhance options.

    denoise_on:       是否启用降噪 / enable SCUNet denoise.
    denoise_strength: 0..1；0 表示短路跳过推理 / 0 short-circuits inference.
    color_on:         是否启用调色 / enable SVDLUT color.
    color_strength:   0..1 调色混合强度 / color blend strength.
    """
    denoise_on: bool = True
    denoise_strength: float = 0.5
    color_on: bool = True
    color_strength: float = 0.4
```

`core/enhance/pipeline.py`:

```python
# -*- coding: utf-8 -*-
"""
修图链路编排 / Enhance pipeline orchestration.

固定顺序 denoise→color；纯函数，不碰文件 I/O，不依赖 Qt。
denoise_fn/color_fn 可注入（测试 / 解耦）；为 None 时用真实封装（见 wrappers）。
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from core.enhance.options import EnhanceOptions


def enhance(
    img_rgb: np.ndarray,
    opts: EnhanceOptions,
    *,
    denoise_fn: Optional[Callable] = None,
    color_fn: Optional[Callable] = None,
    device=None,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> np.ndarray:
    """
    按 denoise→color 链路修图，返回新 RGB ndarray（uint8, HxWx3）。

    参数 / Parameters:
        img_rgb: 输入 RGB ndarray（uint8）。
        opts: EnhanceOptions。
        denoise_fn: (img, strength, device, progress_cb) -> img；None 时懒加载真实封装。
        color_fn: (img, strength, device) -> img；None 时懒加载真实封装。
        device: torch 设备；None 时用 config.get_best_device()。
        progress_cb: 进度回调 0..1（传给降噪 tiling）。

    返回 / Returns:
        np.ndarray: 修图后 RGB（uint8）。
    """
    out = img_rgb
    if opts.denoise_on and opts.denoise_strength > 0.0:
        fn = denoise_fn
        if fn is None:
            from core.enhance.models.scunet import denoise as fn  # noqa: PLC0415
        out = fn(out, opts.denoise_strength, device, progress_cb=progress_cb)
    if opts.color_on:
        fn = color_fn
        if fn is None:
            from core.enhance.models.svdlut import colorize as fn  # noqa: PLC0415
        out = fn(out, opts.color_strength, device)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_pipeline.py -v`
Expected: 4 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/__init__.py core/enhance/options.py core/enhance/pipeline.py
git add core/enhance/__init__.py core/enhance/options.py core/enhance/pipeline.py tests/enhance/test_pipeline.py
git commit -m "feat(enhance): pipeline 编排骨架 + EnhanceOptions(denoise→color gating)"
```

---

### Task 2: SVDLUT 调色封装（vendor 网络 + 懒加载 + 强度混合）

**Files:**
- Create: `core/enhance/nets/__init__.py`
- Create: `core/enhance/nets/svdlut_net.py`  （vendor，见 Step 3）
- Create: `core/enhance/models/__init__.py`
- Create: `core/enhance/models/svdlut.py`
- Modify: `scripts/download_models.py:164`（MODELS_TO_DOWNLOAD 追加 svdlut 条目）
- Test: `tests/enhance/test_svdlut.py`

**Interfaces:**
- Consumes: `config.get_best_device`, `config.get_install_scoped_resource_path`, `config.get_packaged_model_relative_path`。
- Produces: `core.enhance.models.svdlut.colorize(img_rgb, strength, device=None) -> np.ndarray`。
  - 内部 `_load_model(device)` 懒加载单例；`strength` 在 [原图, 满调色] 之间线性混合：`out = (1-s)*img + s*graded`。

- [ ] **Step 1: 写失败测试**（用 monkeypatch 注入 identity「满调色」，验证强度混合与短路，不依赖真实权重）

`tests/enhance/test_svdlut.py`:

```python
# -*- coding: utf-8 -*-
"""SVDLUT 调色封装：强度混合与单例 / blend & singleton tests."""
import numpy as np

from core.enhance.models import svdlut


def test_strength_blend(monkeypatch):
    img = np.full((4, 4, 3), 100, dtype=np.uint8)
    graded = np.full((4, 4, 3), 200, dtype=np.uint8)
    # 桩：把「满调色」结果固定为 graded
    monkeypatch.setattr(svdlut, "_apply_lut", lambda im, model, device: graded)
    monkeypatch.setattr(svdlut, "_load_model", lambda device: object())

    out = svdlut.colorize(img, strength=0.5, device="cpu")
    # 0.5 混合：(1-0.5)*100 + 0.5*200 = 150
    assert np.allclose(out, 150, atol=1)
    assert out.dtype == np.uint8


def test_strength_zero_returns_original(monkeypatch):
    img = np.full((4, 4, 3), 100, dtype=np.uint8)
    monkeypatch.setattr(svdlut, "_apply_lut",
                        lambda im, model, device: np.full_like(im, 200))
    monkeypatch.setattr(svdlut, "_load_model", lambda device: object())
    out = svdlut.colorize(img, strength=0.0, device="cpu")
    assert np.array_equal(out, img)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_svdlut.py -v`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: vendor 网络 + 写封装**

`core/enhance/nets/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""Vendored model architectures (SCUNet, SVDLUT). 见各文件头部许可证。"""
```

`core/enhance/nets/svdlut_net.py`：**逐字 vendor** 自官方仓库
`https://github.com/WontaeaeKim/SVDLUT`（推理用网络定义文件，含 LUT 生成与 trilinear 插值的 `nn.Module`）。
保留原始 Apache-2.0 许可证头，文件顶部加一行注释标明来源 commit。**不要重写网络结构**，只移除训练相关、保留前向推理所需类（导出统一类名 `SVDLUTNet`，若上游类名不同则在文件底部加 `SVDLUTNet = <原类名>` 别名）。

`core/enhance/models/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""修图模型封装 / Enhance model wrappers."""
```

`core/enhance/models/svdlut.py`:

```python
# -*- coding: utf-8 -*-
"""
SVDLUT 空间感知调色封装 / SVDLUT spatial-aware color wrapper.

懒加载单例；colorize() 在原图与满调色结果间按 strength 线性混合。
权重：models/svdlut.pth（HF 下载）。设备：get_best_device()，MPS/CPU 用 fp32。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import torch

from config import (get_best_device, get_install_scoped_resource_path,
                    get_packaged_model_relative_path)

_MODEL = None  # 单例缓存（按设备）/ singleton cache
_MODEL_DEVICE = None
_WEIGHT_REL = "models/svdlut.pth"


def _weight_path() -> str:
    return str(get_install_scoped_resource_path(
        _WEIGHT_REL, get_packaged_model_relative_path(_WEIGHT_REL)))


def _load_model(device):
    """懒加载 SVDLUT 网络并载入权重，按设备缓存单例。"""
    global _MODEL, _MODEL_DEVICE
    if _MODEL is not None and _MODEL_DEVICE == str(device):
        return _MODEL
    from core.enhance.nets.svdlut_net import SVDLUTNet  # noqa: PLC0415
    model = SVDLUTNet()
    # weights_only=True：仅反序列化张量,杜绝 pickle 任意代码执行 / avoid RCE on load
    state = torch.load(_weight_path(), map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("state_dict", state), strict=False)
    model.eval().to(device)
    _MODEL, _MODEL_DEVICE = model, str(device)
    return model


def _apply_lut(img_rgb: np.ndarray, model, device) -> np.ndarray:
    """对整图跑 SVDLUT，返回满调色 RGB uint8。"""
    x = torch.from_numpy(img_rgb).float().div(255.0).permute(2, 0, 1).unsqueeze(0).to(device)
    with torch.no_grad():
        y = model(x)
    if isinstance(y, (tuple, list)):
        y = y[0]
    y = y.clamp(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()
    return (y * 255.0 + 0.5).astype(np.uint8)


def colorize(img_rgb: np.ndarray, strength: float, device=None) -> np.ndarray:
    """
    自动调色，按 strength 在 [原图, 满调色] 间线性混合。

    参数:
        img_rgb: RGB uint8 ndarray。
        strength: 0..1；0 返回原图。
        device: torch 设备；None=get_best_device()。
    返回: 调色后 RGB uint8 ndarray。
    """
    if strength <= 0.0:
        return img_rgb
    if device is None:
        device = get_best_device()
    model = _load_model(device)
    graded = _apply_lut(img_rgb, model, device)
    s = float(min(max(strength, 0.0), 1.0))
    blended = (1.0 - s) * img_rgb.astype(np.float32) + s * graded.astype(np.float32)
    return (blended + 0.5).astype(np.uint8)
```

`scripts/download_models.py` 在 `MODELS_TO_DOWNLOAD` 列表内追加（仿现有条目格式，repo_id 用项目模型仓）：

```python
    {
        "resource_id": "color_model",
        "category": "Enhance",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "svdlut.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["enhance"],
        "required": False,
        "sha256": None,
    },
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_svdlut.py -v`
Expected: 2 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/models/svdlut.py core/enhance/nets/svdlut_net.py scripts/download_models.py
git add core/enhance/nets core/enhance/models/__init__.py core/enhance/models/svdlut.py scripts/download_models.py tests/enhance/test_svdlut.py
git commit -m "feat(enhance): SVDLUT 调色封装(vendor 网络+强度混合)+下载清单"
```

> 注：`svdlut.pth` 需先上传到 `jamesphotography/SuperPicky-models`（Task 8 交付项）。

---

### Task 3: SCUNet 降噪封装（vendor 网络 + tiling + 强度混合）

**Files:**
- Create: `core/enhance/nets/scunet_net.py`  （vendor，见 Step 3）
- Create: `core/enhance/models/scunet.py`
- Modify: `scripts/download_models.py`（追加 scunet 条目）
- Test: `tests/enhance/test_scunet.py`

**Interfaces:**
- Consumes: 同 Task 2 的 config 函数。
- Produces: `core.enhance.models.scunet.denoise(img_rgb, strength, device=None, *, progress_cb=None) -> np.ndarray`。
  - 内部 `_tiled_infer(img, model, device, progress_cb)` 按瓦片+重叠拼接；`_load_model(device)` 懒加载单例。
  - tiling 参数：`TILE=512, OVERLAP=32`；图小于 TILE 时整图直跑。

- [ ] **Step 1: 写失败测试**（核心：tiling 在 identity 模型下重建结果等于输入；强度混合）

`tests/enhance/test_scunet.py`:

```python
# -*- coding: utf-8 -*-
"""SCUNet 降噪封装：tiling 无缝 + 强度混合 / seamless tiling & blend."""
import numpy as np

from core.enhance.models import scunet


class _IdentityModel:
    """桩模型：对 NCHW 张量原样返回（验证 tiling 拼接无缝）。"""
    def __call__(self, x):
        return x
    def eval(self):
        return self
    def to(self, *_a, **_k):
        return self


def test_tiling_identity_reconstructs(monkeypatch):
    # 比 TILE 大的图，强制走多瓦片路径
    img = (np.random.rand(700, 900, 3) * 255).astype(np.uint8)
    monkeypatch.setattr(scunet, "_load_model", lambda device: _IdentityModel())
    out = scunet.denoise(img, strength=1.0, device="cpu")
    # identity 去噪 + 满强度 → 应≈原图（边界拼接误差 ≤1）
    assert out.shape == img.shape
    assert np.abs(out.astype(int) - img.astype(int)).max() <= 1


def test_strength_blend(monkeypatch):
    img = np.full((300, 300, 3), 100, dtype=np.uint8)

    class _ConstModel:
        def __call__(self, x):
            return x * 0 + 200.0 / 255.0  # 「满降噪」恒为 200
        def eval(self):
            return self
        def to(self, *_a, **_k):
            return self

    monkeypatch.setattr(scunet, "_load_model", lambda device: _ConstModel())
    out = scunet.denoise(img, strength=0.5, device="cpu")
    assert np.allclose(out, 150, atol=2)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_scunet.py -v`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: vendor 网络 + 写封装**

`core/enhance/nets/scunet_net.py`：**逐字 vendor** 自
`https://github.com/cszn/SCUNet/blob/main/models/network_scunet.py`（类 `SCUNet`）。保留许可证头，注明来源 commit。仅保留前向推理所需类，导出类名 `SCUNet`。

`core/enhance/models/scunet.py`:

```python
# -*- coding: utf-8 -*-
"""
SCUNet 盲降噪封装 / SCUNet blind-denoise wrapper.

懒加载单例；denoise() 按 strength 在 [原图, 满降噪] 间线性混合。
大图 tiling（TILE=512, OVERLAP=32）控显存；MPS/CPU 用 fp32。
权重：models/scunet_color_real.pth（HF 下载）。
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import torch

from config import (get_best_device, get_install_scoped_resource_path,
                    get_packaged_model_relative_path)

_MODEL = None
_MODEL_DEVICE = None
_WEIGHT_REL = "models/scunet_color_real.pth"
TILE = 512
OVERLAP = 32


def _weight_path() -> str:
    return str(get_install_scoped_resource_path(
        _WEIGHT_REL, get_packaged_model_relative_path(_WEIGHT_REL)))


def _load_model(device):
    """懒加载 SCUNet 并载入权重，按设备缓存单例。"""
    global _MODEL, _MODEL_DEVICE
    if _MODEL is not None and _MODEL_DEVICE == str(device):
        return _MODEL
    from core.enhance.nets.scunet_net import SCUNet  # noqa: PLC0415
    model = SCUNet(in_nc=3, config=[4, 4, 4, 4, 4, 4, 4], dim=64)
    # weights_only=True：仅反序列化张量,杜绝 pickle 任意代码执行 / avoid RCE on load
    state = torch.load(_weight_path(), map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("state_dict", state), strict=True)
    model.eval().to(device)
    _MODEL, _MODEL_DEVICE = model, str(device)
    return model


def _infer_tensor(x: torch.Tensor, model, device) -> torch.Tensor:
    with torch.no_grad():
        return model(x.to(device)).clamp(0, 1).cpu()


def _tiled_infer(img_rgb: np.ndarray, model, device,
                 progress_cb: Optional[Callable[[float], None]]) -> np.ndarray:
    """按瓦片+重叠跑模型并拼接，返回满降噪 RGB uint8。"""
    h, w = img_rgb.shape[:2]
    x = torch.from_numpy(img_rgb).float().div(255.0).permute(2, 0, 1).unsqueeze(0)
    if h <= TILE and w <= TILE:
        y = _infer_tensor(x, model, device)
        out = y.squeeze(0).permute(1, 2, 0).numpy()
        return (out * 255.0 + 0.5).astype(np.uint8)

    step = TILE - OVERLAP
    acc = np.zeros((h, w, 3), np.float32)
    wgt = np.zeros((h, w, 1), np.float32)
    ys = list(range(0, max(h - TILE, 0) + 1, step)) or [0]
    xs = list(range(0, max(w - TILE, 0) + 1, step)) or [0]
    if ys[-1] != max(h - TILE, 0):
        ys.append(max(h - TILE, 0))
    if xs[-1] != max(w - TILE, 0):
        xs.append(max(w - TILE, 0))
    total = len(ys) * len(xs)
    done = 0
    for ty in ys:
        for tx in xs:
            th = min(TILE, h - ty)
            tw = min(TILE, w - tx)
            tile = x[:, :, ty:ty + th, tx:tx + tw]
            y = _infer_tensor(tile, model, device)
            patch = y.squeeze(0).permute(1, 2, 0).numpy()
            acc[ty:ty + th, tx:tx + tw, :] += patch
            wgt[ty:ty + th, tx:tx + tw, :] += 1.0
            done += 1
            if progress_cb is not None:
                progress_cb(done / total)
    out = acc / np.maximum(wgt, 1e-6)
    return (out * 255.0 + 0.5).astype(np.uint8)


def denoise(img_rgb: np.ndarray, strength: float, device=None, *,
            progress_cb: Optional[Callable[[float], None]] = None) -> np.ndarray:
    """
    盲降噪，按 strength 在 [原图, 满降噪] 间线性混合。

    参数:
        img_rgb: RGB uint8 ndarray。
        strength: 0..1；0 返回原图。
        device: torch 设备；None=get_best_device()。
        progress_cb: 瓦片进度回调 0..1。
    返回: 降噪后 RGB uint8 ndarray。
    """
    if strength <= 0.0:
        return img_rgb
    if device is None:
        device = get_best_device()
    model = _load_model(device)
    full = _tiled_infer(img_rgb, model, device, progress_cb)
    s = float(min(max(strength, 0.0), 1.0))
    blended = (1.0 - s) * img_rgb.astype(np.float32) + s * full.astype(np.float32)
    return (blended + 0.5).astype(np.uint8)
```

`scripts/download_models.py` 追加 scunet 条目：

```python
    {
        "resource_id": "denoise_model",
        "category": "Enhance",
        "repo_id": "jamesphotography/SuperPicky-models",
        "filename": "scunet_color_real.pth",
        "dest_dir": "models",
        "packaged_dest_dir": "models",
        "feature_tags": ["enhance"],
        "required": False,
        "sha256": None,
    },
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_scunet.py -v`
Expected: 2 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/enhance/models/scunet.py core/enhance/nets/scunet_net.py
git add core/enhance/nets/scunet_net.py core/enhance/models/scunet.py scripts/download_models.py tests/enhance/test_scunet.py
git commit -m "feat(enhance): SCUNet 降噪封装(vendor 网络+tiling+强度混合)+下载清单"
```

> 注：`scunet_color_real.pth` 由 Task 8 上传到模型仓（原始权重见 cszn/SCUNet model_zoo `scunet_color_real_psnr.pth`）。

---

### Task 4: export_crop 集成修图（裁剪后、写盘前）

**Files:**
- Modify: `core/crop_export.py:33-92`（`export_crop` 增加 `enhance_opts` 形参 + 调用 pipeline）
- Test: `tests/enhance/test_export_integration.py`

**Interfaces:**
- Consumes: `core.enhance.pipeline.enhance`, `core.enhance.options.EnhanceOptions`。
- Produces: `export_crop(..., enhance_opts: Optional[EnhanceOptions]=None)`；`enhance_opts=None` 时行为与现状完全一致。
  - 注意色彩空间：`export_crop` 用 cv2 解码得 **BGR**；pipeline 约定 RGB。集成处转换 BGR→RGB 喂 pipeline，再 RGB→BGR 写盘。

- [ ] **Step 1: 写失败测试**（注入假 loader + monkeypatch pipeline.enhance，验证被调用且色彩空间转换正确）

`tests/enhance/test_export_integration.py`:

```python
# -*- coding: utf-8 -*-
"""export_crop 修图集成 / enhance integration in export."""
import cv2
import numpy as np

from core import crop_export
from core.enhance.options import EnhanceOptions


def test_enhance_called_with_rgb(tmp_path, monkeypatch):
    # 蓝色 BGR 图（B=255）→ pipeline 应收到 RGB（R 通道=255 在 idx2）
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
    # 喂给 pipeline 的应是 RGB：蓝色像素 → [0,0,255]
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_export_integration.py -v`
Expected: FAIL（`export_crop` 无 `enhance_opts` 参数 → TypeError）。

- [ ] **Step 3: 改 export_crop**

在 `core/crop_export.py` 顶部 import 区下方加间接层（便于测试 monkeypatch）：

```python
def _enhance(img_rgb, opts, **kw):
    """间接调用 pipeline.enhance，便于测试替换 / indirection for testability."""
    from core.enhance.pipeline import enhance as _e  # noqa: PLC0415
    return _e(img_rgb, opts, **kw)
```

`export_crop` 签名增加形参（在 `out_size` 之后、`_image_loader` 之前）：

```python
                out_size: Optional[Tuple[int, int]] = None,
                enhance_opts: Optional["object"] = None,
                _image_loader: Optional[Callable[[str], "object"]] = None) -> str:
```

在裁剪之后（`img = img[y1:y2, x1:x2]`）、`out_size` 重采样**之前**插入：

```python
    # 自动修图（裁剪后、导出前）：cv2 为 BGR，pipeline 约定 RGB，故来回转换。
    # Auto-enhance after crop, before write: cv2 is BGR, pipeline expects RGB.
    if enhance_opts is not None:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        rgb = _enhance(rgb, enhance_opts)
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
```

并在 docstring 的 Parameters 增加一行：

```python
        enhance_opts (Optional[EnhanceOptions]): 修图选项;None=不修图(行为同现状)。
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_export_integration.py -v`
Expected: 2 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/crop_export.py
git add core/crop_export.py tests/enhance/test_export_integration.py
git commit -m "feat(enhance): export_crop 接入修图管线(裁剪后/写盘前,BGR↔RGB)"
```

---

### Task 5: crop_studio 预览 worker（_EnhanceWorker）

**Files:**
- Modify: `ui/crop_studio.py`（新增 `_EnhanceWorker`；在类中加预览状态与 `_request_preview()`）
- Test: `tests/enhance/test_enhance_worker.py`

**Interfaces:**
- Consumes: `core.enhance.pipeline.enhance`, `core.enhance.options.EnhanceOptions`。
- Produces: `ui.crop_studio._EnhanceWorker(img_rgb, opts)`，信号 `done: Signal(object)`（回传修图后 RGB ndarray）；失败回传原图。
  - 预览输入：当前裁剪框降采样到长边 ≤ `PREVIEW_LONG_EDGE=1280`。

- [ ] **Step 1: 写失败测试**（worker 在子线程跑 pipeline，回传 ndarray；用 monkeypatch 替换 enhance 避免真权重）

`tests/enhance/test_enhance_worker.py`:

```python
# -*- coding: utf-8 -*-
"""_EnhanceWorker 回传修图结果 / worker emits enhanced array."""
import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from ui import crop_studio  # noqa: E402
from core.enhance.options import EnhanceOptions  # noqa: E402


def _spin_until(pred, timeout_ms=3000):
    app = QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: pred() and loop.quit())
    t.start(10)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    t.stop()


def test_worker_emits_enhanced(monkeypatch):
    img = np.full((16, 16, 3), 100, np.uint8)
    monkeypatch.setattr(crop_studio, "_pipeline_enhance",
                        lambda rgb, opts, **kw: np.full_like(rgb, 200),
                        raising=False)
    got = {}
    w = crop_studio._EnhanceWorker(img, EnhanceOptions())
    w.done.connect(lambda arr: got.__setitem__("arr", arr))
    w.start()
    _spin_until(lambda: "arr" in got)
    assert got["arr"][0, 0, 0] == 200
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_enhance_worker.py -v`
Expected: FAIL（无 `_EnhanceWorker`）。

- [ ] **Step 3: 加 worker + 间接层**

在 `ui/crop_studio.py` import 区附近加：

```python
def _pipeline_enhance(img_rgb, opts, **kw):
    """间接调用，便于测试替换 / indirection for testability."""
    from core.enhance.pipeline import enhance as _e  # noqa: PLC0415
    return _e(img_rgb, opts, **kw)
```

在 `_ExportWorker` 之后加：

```python
PREVIEW_LONG_EDGE = 1280


class _EnhanceWorker(QThread):
    """
    后台对预览图跑修图管线,完成回传修图后 RGB ndarray;失败回传原图。
    Runs the enhance pipeline off the UI thread; emits enhanced RGB ndarray.
    """

    done: Signal = Signal(object)

    def __init__(self, img_rgb, opts) -> None:
        super().__init__()
        self._img, self._opts = img_rgb, opts

    def run(self) -> None:
        try:
            out = _pipeline_enhance(self._img, self._opts)
        except Exception:  # noqa: BLE001 — 预览失败回退原图 / fall back to original
            out = self._img
        self.done.emit(out)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_enhance_worker.py -v`
Expected: 1 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/crop_studio.py
git add ui/crop_studio.py tests/enhance/test_enhance_worker.py
git commit -m "feat(enhance): crop_studio 预览 _EnhanceWorker(后台跑管线)"
```

---

### Task 6: crop_studio UI 面板（智能修图按钮 + 微调）+ 预览接线 + 导出传参

**Files:**
- Modify: `ui/crop_studio.py`（工具栏加「智能修图」按钮 + 可折叠面板；接 `_request_preview` 防抖；`_do_export` 传 `enhance_opts`）
- Modify: `ui/crop_studio.py`（`_EnhanceWorker` 已在 Task 5；此处接线）
- Modify: `tools/i18n` 词条（新增 `crop_studio.enhance` / `denoise` / `color` / `cancel_color`）
- Test: `tests/enhance/test_studio_export_opts.py`

**Interfaces:**
- Consumes: `_EnhanceWorker`, `EnhanceOptions`, 现有 `_do_export`/`_ExportWorker`。
- Produces:
  - `CropStudio._current_enhance_opts() -> Optional[EnhanceOptions]`（面板关→None）。
  - `_ExportWorker.__init__` 增加 `enhance_opts` 形参并透传给 `export_crop`。
  - `_do_export` 把 `_current_enhance_opts()` 传入 `_ExportWorker`。

- [ ] **Step 1: 写失败测试**（核心可测点：面板开关映射到 EnhanceOptions，并随导出透传）

`tests/enhance/test_studio_export_opts.py`:

```python
# -*- coding: utf-8 -*-
"""导出透传修图选项 / export passes enhance_opts through."""
import pytest

pytest.importorskip("PySide6")
from core.enhance.options import EnhanceOptions  # noqa: E402
from ui import crop_studio  # noqa: E402


def test_export_worker_forwards_enhance_opts(monkeypatch):
    captured = {}

    def fake_export(src, box, out, *, exif_src=None, jpeg_quality=95,
                    out_size=None, enhance_opts=None):
        captured["opts"] = enhance_opts
        return out

    monkeypatch.setattr(crop_studio, "export_crop", fake_export)
    opts = EnhanceOptions(denoise_strength=0.3)
    w = crop_studio._ExportWorker("s", None, "o.jpg", None, enhance_opts=opts)
    w.run()
    assert captured["opts"] is opts
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/enhance/test_studio_export_opts.py -v`
Expected: FAIL（`_ExportWorker` 无 `enhance_opts`）。

- [ ] **Step 3: 实现**

(a) `_ExportWorker.__init__` 增 `enhance_opts=None` 并存储；`run()` 透传：

```python
    def __init__(self, src: str, box, out: str, exif_src: Optional[str], *,
                 jpeg_quality: int = 95, out_size: Optional[tuple] = None,
                 enhance_opts=None) -> None:
        super().__init__()
        self._src, self._box, self._out, self._exif_src = src, box, out, exif_src
        self._quality = jpeg_quality
        self._out_size = out_size
        self._enhance_opts = enhance_opts

    def run(self) -> None:
        try:
            out = export_crop(self._src, self._box, self._out, exif_src=self._exif_src,
                              jpeg_quality=self._quality, out_size=self._out_size,
                              enhance_opts=self._enhance_opts)
            self.done.emit(True, out)
        except Exception as e:  # noqa: BLE001
            self.done.emit(False, str(e))
```

(b) 在工具栏构建处（`export_btn` 附近，约 `ui/crop_studio.py:900`）加「智能修图」勾选按钮 + 可折叠面板（两滑块 + 取消调色），并维护 `self._enhance_panel_on`、`self._denoise_slider`、`self._color_slider`、`self._color_on`。

(c) 加方法：

```python
    def _current_enhance_opts(self):
        """面板状态→EnhanceOptions；总开关关时返回 None（不修图）。"""
        if not getattr(self, "_enhance_panel_on", False):
            return None
        from core.enhance.options import EnhanceOptions  # noqa: PLC0415
        return EnhanceOptions(
            denoise_on=self._denoise_slider.value() > 0,
            denoise_strength=self._denoise_slider.value() / 100.0,
            color_on=bool(self._color_on.isChecked()),
            color_strength=self._color_slider.value() / 100.0,
        )

    def _request_preview(self) -> None:
        """防抖 300ms 后对降采样裁剪启 _EnhanceWorker，回传刷新对比图。"""
        if not hasattr(self, "_preview_timer"):
            from PySide6.QtCore import QTimer  # noqa: PLC0415
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._run_preview_worker)
        self._preview_timer.start(300)
```

`_run_preview_worker` 取当前裁剪框、BGR→RGB、按 `PREVIEW_LONG_EDGE` 降采样、启 `_EnhanceWorker`，`done` 回调把结果显示到画布对比层。滑块/勾选的 `valueChanged`/`toggled` 接 `_request_preview`。

(d) `_do_export` 传入 opts：

```python
        self._export_worker = _ExportWorker(
            self._image_path, self._current_box, out_path, exif_src,
            jpeg_quality=jpeg_quality, out_size=out_size,
            enhance_opts=self._current_enhance_opts(),
        )
```

(e) i18n 词条：在中文与英文 locale 各加 `crop_studio.enhance="智能修图"/"Smart Enhance"`、`crop_studio.denoise="降噪"/"Denoise"`、`crop_studio.color="调色"/"Color"`、`crop_studio.cancel_color="取消自动调色"/"Cancel auto color"`。

- [ ] **Step 4: 运行测试确认通过 + 全量回归**

Run: `.venv/bin/python -m pytest tests/enhance/ tests/test_crop_studio.py tests/test_crop_export.py -v`
Expected: 全部 passed（含既有裁剪/导出测试不回归）。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/crop_studio.py
git add ui/crop_studio.py tools/i18n* tests/enhance/test_studio_export_opts.py
git commit -m "feat(enhance): crop_studio 智能修图面板+预览接线+导出透传选项"
```

---

### Task 7: 打包清单接入（build specs + initialization_manager）

**Files:**
- Modify: `build_release_win.py:328`（`load_required_models` 默认清单追加两权重）
- Modify: `build_release_mac.py`（对应模型清单追加两权重）
- Modify: `core/initialization_manager.py`（首启下载列表含 enhance 权重，`required=False`）
- Test: `tests/enhance/test_packaging_manifest.py`

**Interfaces:**
- Consumes: `scripts/download_models.py:MODELS_TO_DOWNLOAD`（Task 2/3 已加条目）。
- Produces: 打包/初始化清单包含 `svdlut.pth`、`scunet_color_real.pth`。

- [ ] **Step 1: 写失败测试**（校验下载清单含两权重，且 build 默认清单与之一致）

`tests/enhance/test_packaging_manifest.py`:

```python
# -*- coding: utf-8 -*-
"""打包清单含 enhance 权重 / packaging manifest includes enhance weights."""
import importlib


def test_download_manifest_has_enhance_weights():
    mod = importlib.import_module("scripts.download_models")
    names = {m["filename"] for m in mod.MODELS_TO_DOWNLOAD}
    assert "svdlut.pth" in names
    assert "scunet_color_real.pth" in names
```

- [ ] **Step 2: 运行测试确认失败/通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_packaging_manifest.py -v`
Expected: 若 Task 2/3 已加条目则 PASS；否则补齐条目。

- [ ] **Step 3: 改 build specs + initialization_manager**

`build_release_win.py` 的 `load_required_models()` 回退默认清单（约 `:330`）追加：

```python
        {"filename": "svdlut.pth", "dest_dir": "models"},
        {"filename": "scunet_color_real.pth", "dest_dir": "models"},
```

`build_release_mac.py` 同步在其模型清单追加上述两项。
`core/initialization_manager.py` 的下载/校验列表纳入两权重（`required=False`，缺失时仅禁用修图功能、不阻断启动）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/enhance/test_packaging_manifest.py -v`
Expected: passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile build_release_win.py build_release_mac.py core/initialization_manager.py
git add build_release_win.py build_release_mac.py core/initialization_manager.py tests/enhance/test_packaging_manifest.py
git commit -m "build(enhance): 打包/初始化清单纳入 SCUNet+SVDLUT 权重"
```

---

### Task 8: 权重托管 + 许可证核验（交付前置）

**Files:**
- Create: `core/enhance/nets/LICENSE-SCUNet`（SCUNet 上游许可证副本）
- Create: `core/enhance/nets/LICENSE-SVDLUT`（SVDLUT Apache-2.0 副本）
- Modify: `core/enhance/nets/scunet_net.py` / `svdlut_net.py` 头部注明来源与许可证

**Interfaces:** 无代码接口；产出可下载权重与合规许可证。

- [ ] **Step 1: 核验 SCUNet 许可证**

打开 `https://github.com/cszn/SCUNet`，确认 LICENSE（应为开源；若为非商用须停下与用户讨论是否改用替代降噪权重）。SVDLUT 已确认 Apache-2.0。

- [ ] **Step 2: 落许可证副本**

把两上游 LICENSE 全文存为 `core/enhance/nets/LICENSE-SCUNet`、`core/enhance/nets/LICENSE-SVDLUT`；在两 `*_net.py` 头部加注释：来源 URL + commit + 许可证名。

- [ ] **Step 3: 上传权重到 HF**

把 `scunet_color_real_psnr.pth`（重命名 `scunet_color_real.pth`）与 SVDLUT 预训练权重（重命名 `svdlut.pth`）上传到 `jamesphotography/SuperPicky-models`（参考 `scripts/upload_to_hf.py`）。

- [ ] **Step 4: 真权重集成冒烟测试**（可 skip-marked，视 CI 是否拉权重）

`tests/enhance/test_real_smoke.py`:

```python
# -*- coding: utf-8 -*-
"""真权重端到端冒烟（本地有权重时跑）/ real-weight smoke (local only)."""
import os
import numpy as np
import pytest

from core.enhance.pipeline import enhance
from core.enhance.options import EnhanceOptions
from config import get_install_scoped_resource_path, get_packaged_model_relative_path


def _has(rel):
    return os.path.exists(str(get_install_scoped_resource_path(
        rel, get_packaged_model_relative_path(rel))))


@pytest.mark.skipif(not (_has("models/scunet_color_real.pth") and _has("models/svdlut.pth")),
                    reason="enhance 权重未就位")
def test_real_pipeline_runs():
    img = (np.random.rand(256, 256, 3) * 255).astype(np.uint8)
    out = enhance(img, EnhanceOptions())
    assert out.shape == img.shape and out.dtype == np.uint8
```

- [ ] **Step 5: 提交**

```bash
git add core/enhance/nets/LICENSE-SCUNet core/enhance/nets/LICENSE-SVDLUT core/enhance/nets/scunet_net.py core/enhance/nets/svdlut_net.py tests/enhance/test_real_smoke.py
git commit -m "chore(enhance): 许可证核验+权重托管+真权重冒烟测试"
```

---

## Self-Review

**Spec coverage：**
- §3 架构 → Task 1（pipeline/options）、Task 2（svdlut）、Task 3（scunet）。
- §4 固定链路 → Task 1 gating 测试 + 顺序写死。
- §5 双分辨率 → 预览=Task 5/6（降采样+worker），导出=Task 4（全分辨率）。
- §6 性能（tiling/懒加载/预览限尺寸/强度 0 短路）→ Task 3 tiling、各封装单例、Task 5 `PREVIEW_LONG_EDGE`、Task 1 短路。
- §7 UI → Task 6。
- §8 打包 → Task 2/3 下载清单 + Task 7 build/init + Task 8 许可证。
- §9 数据流接线 → Task 4（export）、Task 6（preview/export 接线）。
- §10 测试 → 每任务 TDD + Task 8 真权重冒烟。
- §11 风险（CPU 慢/调色过激/许可证）→ Task 3 tiling、默认保守强度（Task 1）、Task 8 核验。

**Placeholder 扫描：** 网络结构源码为「逐字 vendor 自指定文件」的精确指令（非占位），其余代码均完整给出。

**Type 一致性：** `enhance(img_rgb, opts, *, denoise_fn, color_fn, device, progress_cb)`、`denoise(img, strength, device, *, progress_cb)`、`colorize(img, strength, device)`、`export_crop(..., enhance_opts=None)`、`_ExportWorker(..., enhance_opts=None)`、`_EnhanceWorker(img_rgb, opts)` 跨任务签名一致。

**已知前置依赖：** Task 2/3 引用的 `.pth` 由 Task 8 托管；执行顺序上 Task 8 的「许可证核验」应在 Task 2/3 vendoring 前先做（若 SCUNet 许可证不合规需换方案）。建议实际执行序：先做 Task 8 Step 1 许可证核验 → Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 余下步骤。

---

## 实施记录 / Implementation Log (2026-06-22, 自主执行)

**结果：8 任务全部落地，68 passed / 2 skipped / 0 失败**（排除预存的 `test_fp16_infer.py` collection 错误，与本次无关）。测试改用仓库根级 `test_enhance_*.py`（仓库无 `tests/` 目录且其被 .gitignore）。

| Task | 状态 | commit |
|---|---|---|
| 1 pipeline+options | ✅ | 5309698b |
| 2 SVDLUT 封装+清单 | ✅(封装) | c0ad359f |
| 3 SCUNet 封装+tiling | ✅ | 8653c312 |
| 4 export_crop 集成 | ✅ | 8dcb95b0 |
| 5 _EnhanceWorker | ✅ | afc911f1 |
| 6 UI 面板+预览+透传 | ✅ | 8dc631bd |
| 7 打包清单 | ✅ | 0b132165 |
| 8 vendor+许可证+降级 | ✅(见下) | 42699dcd |

### 与原计划的偏差（均有据）

1. **SVDLUT = CUDA-only,无法跨平台（重大阻塞）**：核实官方实现后发现 `SVDLUT.forward` 依赖编译的
   CUDA 自定义算子 `bilateral2D_slicing_LUTTransform`（`cpp_ext_interface.py` + `kernel_code/*.cu`），
   **无 CPU/MPS 路径**，与「跨 Windows+macOS/MPS + CPU/MPS 速度」硬约束冲突。故未 vendor 上游 `models.py`，
   改为 `svdlut_net.py` stub（实例化抛 `NotImplementedError` 并在头部完整记录阻塞与三条出路）。
   **SVDLUT 的 Apache-2.0 与 SCUNet 的 Apache-2.0 均已确认并落盘** → 许可证非阻塞，CUDA 才是。

2. **管线优雅降级（新增,计划外）**：`pipeline._run_step` 对「模型不可用」类异常
   （ImportError/FileNotFoundError/NotImplementedError/OSError）降级跳过该步并告警，真 bug 仍抛出。
   保证「启用调色但 SVDLUT 未就绪」时导出不崩溃。真实路径已验证：两步均跳过、返回原图、零崩溃。

3. **UI 预览改为非破坏性对话框（计划外,有据）**：`_Canvas.set_image` 会清除裁剪框 + 重置缩放，
   故「滑块变动即刷新主画布」会破坏用户正在画的裁剪框。改为「预览效果」按钮 → before/after 对话框，
   不触碰主画布。真·连续 on-canvas 实时预览需重构 `_Canvas` 为非破坏性叠加层（后续增量）。

4. **latent bug 修复**：写真权重冒烟测试时发现 `get_install_scoped_resource_path(rel, packaged...)`
   误用位置参数（应为关键字 `packaged_relative_path=`），封装运行时也会错；已统一修正。

### 仍需用户处理（无法自主完成）

- **A. SVDLUT 决策**（三选一）：①纯 PyTorch 用 `F.grid_sample` 重写 bilateral-grid slicing + 3D LUT
  trilinear，并以官方 CUDA 内核输出 + 真权重逐位验证后采用；②换无自定义 CUDA 内核的纯 PyTorch 调色模型；
  ③本期仅交付 SCUNet 降噪，调色后续。
- **B. 上传权重到 HF**（`jamesphotography/SuperPicky-models`）：`scunet_color_real.pth`
  （= 上游 `scunet_color_real_psnr.pth` 重命名）；SVDLUT 待 A 决策后再定。上传后真权重冒烟测试自动转绿。
- **C. 真机验证**：装好 SCUNet 权重后，在 macOS(MPS)/Windows 上跑一次真实降噪导出，确认速度与画质达标。

---

## UI 迭代记录 / UI Iteration Log (2026-06-22, 降噪对比预览)

实装后据用户反馈对预览 UI 做了多轮迭代,最终形态如下(均已 dev 提交并推送 origin/dev):

### 最终交互形态
- **中央区 `QStackedWidget`**:裁剪画布 `_Canvas`(index0) ↔ 降噪对比 `_BeforeAfterView`(index1)。
  点左栏「修图」(gem)直接进入对比模式并即时出预览;「完成」退出回裁剪页。对比与裁剪解耦,
  从根上避开 `_Canvas.set_image` 清裁剪框的问题。
- **`_BeforeAfterView`**:中间竖线揭示 before(左)/after(右)。
  - 竖线**仅在按住线本身(±16px)拖动时**才移动;悬停只变光标不动线(早期"悬停即扫动"被否)。
  - **适应 / 100%** 切换按钮;100% 下在线以外拖动 = 平移,看 1:1 像素。
- **预览源 = 用户选定的裁剪区**(`_current_crop_bgr()` 按 `_current_box` 取,无框则整图),
  降采样 ≤ `PREVIEW_LONG_EDGE`(2048);链路严格「裁剪→降噪」,预览即导出成品。
- **降噪滑块 10% 一档**(range 0..10,显示百分比;强度 = value/10),默认 70%。
- **状态反馈**:推理时「处理中…」,完成「已更新 · 平均像素差 X.X」;客观量化变化
  (干净图也看得到数字随强度变,如 70%→2.1 / 100%→2.9 / 0%→0.0)。
- **导出两条路**:不进「修图」或强度=0 → 不降噪导出;进过「修图」且强度>0 → 降噪导出,
  `_enhance_engaged` 持久化,点「完成」回裁剪页后导出仍生效。
- 防抖 400ms 自动预览,worker 忙时合并最新一次请求。

### 关键认知(供调色复用)
- SCUNet 对**干净低噪图几乎不改动**是正确行为;效果与"平均像素差"在高 ISO 噪片上才显著
  (实测干净图 50↔100 仅差 1.6,噪图差 7.4)。评测降噪/调色都须用真实噪片/原片。
- `_current_box` 与导出 `_image_path` 同属**分析图坐标系**,取裁剪区做预览可直接切片。
- 权重经 KAIR release 公开下载(`scunet_color_real_psnr.pth`,69MB,Apache-2.0),已在本机
  装入验证(missing=0/unexpected=0,17.95M 参数);**尚未上传到项目 HF 模型仓**。

### 相关提交
ad173..13a2d0c6 区间;关键:3c7cfbd4(对比模式)、6969e69e(100%/平移)、1d1a111a(裁剪区)、
9838aa24(强度默认+导出持久化)、362dbf5f(线仅按住可拖)、13a2d0c6(状态+像素差+10%档)。
