# -*- coding: utf-8 -*-
"""
SVDLUT 双边网格切片 + 2D LUT 变换的纯 PyTorch 等价实现 / pure-torch slicing op.

等价于官方 CUDA-only 自定义算子 `bilateral2D_slicing_LUTTransform`(见上游
kernel_code/.../trilinear2D_slice_LUTTransform_cpu.cpp),用 F.grid_sample 实现,
无需编译、CPU/MPS/CUDA 全通。数值对官方 CPU 参考逐位对齐(≤1e-3)。

每个像素(空间归一坐标 x,y;输入颜色 r,g,b):
  双边切片 int_img[c] = Σ_{ch∈{2c,2c+1}} [ w0·slice_xy + w1·slice_{x,color_c}
                        + w2·slice_{y,color_c} + bias_ch ]
  LUT      out[i]     = int_img[i] + lw0·lerp_rg + lw1·lerp_rb + lw2·lerp_gb + lb_i
其中各 bilinear/双线性插值 ≡ grid_sample(mode=bilinear, align_corners=True,
padding=border)(与官方 floor+frac+clamp 一致)。
"""
from __future__ import annotations

import torch
import torch.nn.functional as F


def _bilerp(plane: torch.Tensor, gx: torch.Tensor, gy: torch.Tensor) -> torch.Tensor:
    """
    在 (N,Hp,Wp) 的平面上,对每像素按 (gx→W 轴, gy→H 轴) 做双线性采样。
    gx, gy ∈ [0,1],形状 (N,H,W);返回 (N,H,W)。
    """
    grid = torch.stack([gx * 2.0 - 1.0, gy * 2.0 - 1.0], dim=-1)  # (N,H,W,2)
    out = F.grid_sample(plane.unsqueeze(1), grid, mode="bilinear",
                        align_corners=True, padding_mode="border")
    return out[:, 0]


def bilateral_slice_lut_transform(
    grid: torch.Tensor,      # (N, 2*3, 3, Dg, Dg) 双边网格 / bilateral grids
    img: torch.Tensor,       # (N, 3, H, W) 输入 RGB[0,1]
    grid_weights: torch.Tensor,  # 每图 18 个(3*ch+j)/ per-image, flat 18
    grid_bias: torch.Tensor,     # 每图 6 个(ch)
    lut: torch.Tensor,       # (N, 3, 3, Dl, Dl) 2D LUT
    lut_weights: torch.Tensor,   # 每图 9 个(3*i+j)
    lut_bias: torch.Tensor,      # 每图 3 个(i)
) -> torch.Tensor:
    """纯 torch 等价切片算子,返回 (N,3,H,W)(未 ReLU)。"""
    n, _, h, w = img.shape
    dev, dt = img.device, img.dtype
    r, g, b = img[:, 0], img[:, 1], img[:, 2]            # 各 (N,H,W)
    colors = (r, g, b)

    # 空间归一坐标:复刻官方 C++ `scalar_t x = x_ / (width-1)` 的整数除法语义——
    # x_, width 均为 int,故 x 恒为 0,仅最后一列(x_==width-1)为 1;y 同理。
    # Replicate the official kernel's integer division: x is 0 everywhere except the
    # last column (==1), y except the last row. (This is the released kernel's behavior.)
    xv = torch.zeros(w, device=dev, dtype=dt)
    yv = torch.zeros(h, device=dev, dtype=dt)
    if w > 1:
        xv[w - 1] = 1.0
    if h > 1:
        yv[h - 1] = 1.0
    x = xv.view(1, 1, w).expand(n, h, w)
    y = yv.view(1, h, 1).expand(n, h, w)

    gw = grid_weights.reshape(n, -1)   # [3*ch+j]
    gb = grid_bias.reshape(n, -1)      # [ch]
    grid_per_ch = grid.shape[1] // 3   # =2

    int_img = [torch.zeros(n, h, w, device=dev, dtype=dt) for _ in range(3)]
    for ch in range(grid.shape[1]):    # ch = i + grid_per_ch*c
        c = ch // grid_per_ch
        col = colors[c]
        s_xy = _bilerp(grid[:, ch, 0], x, y)        # xy 面 / spatial
        s_xc = _bilerp(grid[:, ch, 1], x, col)      # x-color 面
        s_yc = _bilerp(grid[:, ch, 2], y, col)      # y-color 面
        w0 = gw[:, 3 * ch + 0].view(n, 1, 1)
        w1 = gw[:, 3 * ch + 1].view(n, 1, 1)
        w2 = gw[:, 3 * ch + 2].view(n, 1, 1)
        bias = gb[:, ch].view(n, 1, 1)
        int_img[c] = int_img[c] + w0 * s_xy + w1 * s_xc + w2 * s_yc + bias

    lw = lut_weights.reshape(n, -1)    # [3*i+j]
    lb = lut_bias.reshape(n, -1)       # [i]
    outs = []
    for i in range(3):
        o_rg = _bilerp(lut[:, i, 0], r, g)   # rg 面 (W=r,H=g)
        o_rb = _bilerp(lut[:, i, 1], r, b)   # rb 面 (W=r,H=b)
        o_gb = _bilerp(lut[:, i, 2], g, b)   # gb 面 (W=g,H=b)
        o = (int_img[i]
             + lw[:, 3 * i + 0].view(n, 1, 1) * o_rg
             + lw[:, 3 * i + 1].view(n, 1, 1) * o_rb
             + lw[:, 3 * i + 2].view(n, 1, 1) * o_gb
             + lb[:, i].view(n, 1, 1))
        outs.append(o)
    return torch.stack(outs, dim=1)
