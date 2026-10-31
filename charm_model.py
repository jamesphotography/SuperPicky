#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立 Charm (CVPR 2025) 美学评估模型实现
脱离 transformers / Charm_tokenizer / ml_collections 运行，用于鸟类摄影美学评分。

Standalone Charm (CVPR 2025) Aesthetic Assessment Model Implementation
Runs without transformers / Charm_tokenizer / ml_collections for bird photography
aesthetic scoring.

基于 / Based on:
- "Charm: The Missing Piece in ViT Fine-Tuning for Image Aesthetic Assessment"
  Fatemeh Behrad et al., CVPR 2025 (https://github.com/FBehrad/Charm)
- 权重 / Weights: FatemehBehrad/Charm (tad66k_charm.pth + dino_small_pos.pt)
  合并转换后的半精度纯张量权重: models/charm_tad66k_fp16.pth (44.2 MB)
"""

import math
import os
import sys
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as T
from PIL import Image

from config import get_best_device
from tools.i18n import t as _t

# 优先使用半精度合并权重，兼顾开发环境的备选名称
# Prefer the fp16 merged checkpoint; fall back to fp32 if present in dev checkouts
CHARM_WEIGHT_NAMES: Tuple[str, ...] = (
    "charm_tad66k_fp16.pth",
    "charm_tad66k.pth",
)

# TAD66K 训练序列长度上限为 768 tokens；长边上限设为 672 (24 * 28) 时，
# 3:2 图像共 24x16 = 384 个 28x28 宏块，经 64 个高频块 4 裂变后总 token 数为
# 1 + 64*4 + 320 = 577 <= 768，完全落在训练视野内，实测鸟图人眼盲测秩相关最高 (ρ=0.7905)。
# TAD66K was trained with a 768-token horizon. Capping the long edge at 672 (24 * 28)
# yields 24x16 = 384 macro-blocks (28x28) for a 3:2 photo -> 1 + 64*4 + 320 = 577
# tokens <= 768, matching the training horizon and achieving peak Spearman ρ=0.7905.
CHARM_MAX_EDGE: int = 672


def resize_for_charm(img: Image.Image, max_edge: int = CHARM_MAX_EDGE) -> Image.Image:
    """
    按原图长宽比将 PIL 图像长边限制在 max_edge 以内（仅缩小不放大）。

    参数:
    img (Image.Image): 输入 RGB PIL 图像
    max_edge (int): 最大长边像素上限，默认 672

    返回:
    Image.Image: 保持长宽比缩放后的 PIL 图像

    Resize a PIL image while preserving aspect ratio so its long edge does not
    exceed max_edge (downscale only, never upscale).

    Parameters:
    img (Image.Image): Input RGB PIL image
    max_edge (int): Maximum long-edge pixel limit, default 672

    Return:
    Image.Image: Aspect-ratio-preserved PIL image
    """
    width, height = img.size
    longest = max(width, height)
    if longest <= max_edge:
        return img
    scale = max_edge / float(longest)
    new_w = max(28, int(width * scale))
    new_h = max(28, int(height * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


class CharmTokenizer:
    """
    向量化、确定性的 Charm 多尺度分块分词器 (纯 PyTorch 实现)。
    对 28x28 宏块批量计算 2D FFT 频率能量，保留前 64 个高频细节块为 4x(14x14)
    原始分辨率，其余低频背景块降采样为 1x(14x14)，同时保留构图、长宽比与高频羽丝细节。

    Vectorized, deterministic Charm multi-scale patch tokenizer in pure PyTorch.
    Computes batched 2D FFT frequency energy over 28x28 macro-blocks, keeping the
    top 64 high-frequency detail blocks at full 4x(14x14) resolution while
    downsampling remaining background blocks to 1x(14x14).
    """

    def __init__(
        self,
        pos_embed: Optional[torch.Tensor] = None,
        patch_size: int = 14,
        hidden_tokens: int = 768,
        initial_hidden_size: int = 512,
        num_scales: int = 2,
        factor: float = 0.5,
        interpolate_offset: float = 0.1,
    ) -> None:
        """
        初始化 Charm 分词器。

        参数:
        pos_embed (Optional[torch.Tensor]): 预训练 DINOv2 位置编码 (1, 1370, 384)
        patch_size (int): ViT 基础 patch 大小，DINOv2 为 14
        hidden_tokens (int): 触发多尺度压缩的补丁数阈值，TAD66K 为 768
        initial_hidden_size (int): 决定高频宏块配额的基准大小，默认为 512
        num_scales (int): 多尺度层级数，默认为 2
        factor (float): 低频块缩放因子，默认为 0.5
        interpolate_offset (float): 位置编码双三次插值偏移量，默认为 0.1

        返回:
        None

        Initialize the Charm tokenizer.

        Parameters:
        pos_embed (Optional[torch.Tensor]): Pretrained DINOv2 pos_embed (1, 1370, 384)
        patch_size (int): Base ViT patch size (14 for DINOv2)
        hidden_tokens (int): Patch threshold to trigger multi-scale compression (768)
        initial_hidden_size (int): Base size determining high-res patch quota (512)
        num_scales (int): Number of scales (2)
        factor (float): Downscaling factor for low-res patches (0.5)
        interpolate_offset (float): Bicubic interpolation offset for pos_embed (0.1)

        Return:
        None
        """
        self.patch_size = patch_size
        self.hidden_size = hidden_tokens + 1
        self.initial_hidden_size = initial_hidden_size
        self.num_scales = num_scales
        self.factor = factor
        self.interpolate_offset = interpolate_offset
        self.coarse_size = patch_size * (2 ** (num_scales - 1))  # 28
        self.n_high_patches = int(
            (initial_hidden_size * (1.0 / num_scales)) / ((2 ** (num_scales - 1)) ** 2)
        )  # 64
        self._pos_embed: Optional[torch.Tensor] = (
            pos_embed.detach().float().cpu() if pos_embed is not None else None
        )
        self._pos_cache: Dict[Tuple[int, int], torch.Tensor] = {}

    def set_pos_embed(self, pos_embed: torch.Tensor) -> None:
        """
        设置或更新基础位置编码并清空插值缓存。

        参数:
        pos_embed (torch.Tensor): 形状为 (1, 1370, 384) 的位置编码张量

        返回:
        None

        Set or update the base positional embedding and clear the interpolation cache.

        Parameters:
        pos_embed (torch.Tensor): Positional embedding tensor of shape (1, 1370, 384)

        Return:
        None
        """
        self._pos_embed = pos_embed.detach().float().cpu()
        self._pos_cache.clear()

    def _get_interpolated_pos_embed(self, size: Tuple[int, int]) -> torch.Tensor:
        """
        获取或计算指定网格尺寸 (h_patches, w_patches) 的双三次插值位置编码。

        参数:
        size (Tuple[int, int]): (网格行数, 网格列数)

        返回:
        torch.Tensor: 插值后的位置编码，形状为 (1, 1 + h*w, 384)

        Get or compute the bicubic-interpolated positional embedding for a given
        grid size (h_patches, w_patches).

        Parameters:
        size (Tuple[int, int]): (grid_rows, grid_cols)

        Return:
        torch.Tensor: Interpolated positional embedding of shape (1, 1 + h*w, 384)
        """
        if size in self._pos_cache:
            return self._pos_cache[size]
        if self._pos_embed is None:
            raise RuntimeError("CharmTokenizer pos_embed has not been loaded.")

        w0, h0 = size[0] + self.interpolate_offset, size[1] + self.interpolate_offset
        num_patches = self._pos_embed.shape[1] - 1
        dim = self._pos_embed.shape[-1]
        class_pos = self._pos_embed[:, 0]
        patch_pos = self._pos_embed[:, 1:]
        sqrt_n = int(math.sqrt(num_patches))
        sx, sy = float(w0) / sqrt_n, float(h0) / sqrt_n

        interp = F.interpolate(
            patch_pos.reshape(1, sqrt_n, sqrt_n, dim).permute(0, 3, 1, 2),
            scale_factor=(sx, sy),
            mode="bicubic",
            align_corners=False,
        )
        interp = interp.permute(0, 2, 3, 1).view(1, -1, dim)
        full_pos = torch.cat((class_pos.unsqueeze(0), interp), dim=1)
        self._pos_cache[size] = full_pos
        return full_pos

    def _pad_or_crop(self, img: torch.Tensor, multiple: int) -> torch.Tensor:
        """
        与官方 Charm_Tokenizer.pad_or_crop 逐位一致的边界对齐逻辑。

        参数:
        img (torch.Tensor): 形状为 (3, H, W) 的图像张量
        multiple (int): 对齐倍数 (14 或 28)

        返回:
        torch.Tensor: 对齐后的 (3, H', W') 张量

        Bit-exact equivalent of official Charm_Tokenizer.pad_or_crop boundary alignment.

        Parameters:
        img (torch.Tensor): Image tensor of shape (3, H, W)
        multiple (int): Alignment multiple (14 or 28)

        Return:
        torch.Tensor: Aligned tensor of shape (3, H', W')
        """
        _, height, width = img.shape
        crop_w = (width % multiple) or 1
        crop_h = (height % multiple) or 1
        total_crop = crop_h * crop_w

        pad_w_decision = ((multiple - (width % multiple)) % multiple) if (width % multiple != 0) else 1
        pad_h_decision = ((multiple - (height % multiple)) % multiple) if (height % multiple != 0) else 1
        total_pad = pad_h_decision * pad_w_decision

        if total_pad > total_crop:
            end_x = width - (width % multiple)
            end_y = height - (height % multiple)
            return img[:, :end_y, :end_x]

        pad_w = (multiple - (width % multiple)) % multiple
        pad_h = (multiple - (height % multiple)) % multiple
        if pad_w == 0 and pad_h == 0:
            return img
        return F.pad(img, (0, pad_w, 0, pad_h))

    def _normal_flow(
        self, img: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        小图直接分块流 (当 14x14 补丁总数 <= 769 时调用)。

        参数:
        img (torch.Tensor): 形状为 (3, H, W) 的 CPU 浮点图像张量

        返回:
        Tuple[torch.Tensor, torch.Tensor, torch.Tensor]: (tokens, pos_embed, mask)

        Direct patch flow for smaller images (invoked when total 14x14 patches <= 769).

        Parameters:
        img (torch.Tensor): CPU float image tensor of shape (3, H, W)

        Return:
        Tuple[torch.Tensor, torch.Tensor, torch.Tensor]: (tokens, pos_embed, mask)
        """
        p = self.patch_size
        _, height, width = img.shape
        pad_w = (p - (width % p)) % p
        pad_h = (p - (height % p)) % p
        if pad_w or pad_h:
            img = F.pad(img, (0, pad_w, 0, pad_h))

        _, h_pad, w_pad = img.shape
        n_rows, n_cols = h_pad // p, w_pad // p
        patches = (
            img.unfold(1, p, p)
            .unfold(2, p, p)
            .permute(1, 2, 0, 3, 4)
            .reshape(-1, 3, p, p)
        )
        cls_pad = torch.zeros((1, 3, p, p), dtype=patches.dtype)
        tokens = torch.cat((cls_pad, patches), dim=0)
        pos_embed = self._get_interpolated_pos_embed((n_rows, n_cols)).squeeze(0)
        mask = torch.zeros(tokens.shape[0], dtype=torch.float32)
        return tokens, pos_embed, mask

    def tokenize(
        self, img: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        将单张 (3, H, W) 图像张量分词为多尺度 patch 序列、位置编码与尺度掩码。

        参数:
        img (torch.Tensor): 数值范围 [0, 1]、形状 (3, H, W) 的 RGB 图像张量

        返回:
        Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            - tokens: (1 + N_tokens, 3, 14, 14)
            - pos_embeds: (1 + N_tokens, 384)
            - masks: (1 + N_tokens,) 尺度编码 (0=低频尺度, 1=高频尺度)

        Tokenize a single (3, H, W) image tensor into multi-scale patch tokens,
        positional embeddings, and scale masks.

        Parameters:
        img (torch.Tensor): RGB image tensor in [0, 1] of shape (3, H, W)

        Return:
        Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            - tokens: (1 + N_tokens, 3, 14, 14)
            - pos_embeds: (1 + N_tokens, 384)
            - masks: (1 + N_tokens,) scale indices (0=coarse scale, 1=fine scale)
        """
        img_cpu = img.detach().float().cpu()
        _, height, width = img_cpu.shape
        n_crops_w = math.ceil(width / self.patch_size)
        n_crops_h = math.ceil(height / self.patch_size)

        if n_crops_h * n_crops_w <= self.hidden_size:
            return self._normal_flow(img_cpu)

        img_aligned = self._pad_or_crop(img_cpu, self.coarse_size)
        _, h_al, w_al = img_aligned.shape
        n_row = h_al // self.coarse_size
        n_col = w_al // self.coarse_size
        num_coarse = n_row * n_col

        if num_coarse == 0:
            return self._normal_flow(img_cpu)

        # 向量化提取 28x28 宏块: (num_coarse, 3, 28, 28)
        # Vectorized extraction of 28x28 macro-blocks: (num_coarse, 3, 28, 28)
        coarse_patches = (
            img_aligned.unfold(1, self.coarse_size, self.coarse_size)
            .unfold(2, self.coarse_size, self.coarse_size)
            .permute(1, 2, 0, 3, 4)
            .reshape(-1, 3, self.coarse_size, self.coarse_size)
        )

        # 批量 2D FFT 频率能量排序（与官方 calculate_blur_metric 数学逐位等价）
        # Batched 2D FFT frequency energy sorting (bit-exact with official calculate_blur_metric)
        fft_img = torch.fft.fftshift(torch.fft.fft2(coarse_patches), dim=(-2, -1))
        blur_metric = (20.0 * torch.log10(torch.abs(fft_img))).mean(dim=(1, 2, 3))

        n_high = min(self.n_high_patches, num_coarse)
        sorted_indices = sorted(range(num_coarse), key=lambda idx: blur_metric[idx].item())
        sel_l1 = sorted(sorted_indices[-n_high:]) if n_high > 0 else []
        sel_l1_set = set(sel_l1)
        sel_l0 = [idx for idx in range(num_coarse) if idx not in sel_l1_set]

        p = self.patch_size
        token_parts: List[torch.Tensor] = [torch.zeros((1, 3, p, p), dtype=torch.float32)]
        mask_parts: List[torch.Tensor] = [torch.zeros(1, dtype=torch.float32)]

        if sel_l0:
            l0_tokens = F.interpolate(
                coarse_patches[sel_l0], size=(p, p), mode="bicubic"
            )
            token_parts.append(l0_tokens)
            mask_parts.append(torch.zeros(len(sel_l0), dtype=torch.float32))

        if sel_l1:
            l1_tokens = (
                coarse_patches[sel_l1]
                .unfold(2, p, p)
                .unfold(3, p, p)
                .permute(0, 2, 3, 1, 4, 5)
                .reshape(-1, 3, p, p)
            )
            token_parts.append(l1_tokens)
            mask_parts.append(torch.ones(l1_tokens.shape[0], dtype=torch.float32))

        tokens = torch.cat(token_parts, dim=0)
        masks = torch.cat(mask_parts, dim=0)

        # 构造多尺度位置编码 (与 prepare_pos_embed_ms 完全等价)
        # Construct multi-scale positional embeddings (identical to prepare_pos_embed_ms)
        pos_l0_full = self._get_interpolated_pos_embed((n_row, n_col))
        pos_l1_full = self._get_interpolated_pos_embed((2 * n_row, 2 * n_col))
        cls_pos = pos_l1_full[:, 0, :]

        mask_l0 = torch.zeros(num_coarse, dtype=torch.bool)
        if sel_l0:
            mask_l0[sel_l0] = True
        fine_pos_l0 = pos_l0_full[:, 1:, :][:, mask_l0, :]

        mask_l1_grid = torch.zeros(num_coarse, dtype=torch.bool)
        if sel_l1:
            mask_l1_grid[sel_l1] = True
        mask_l1 = (
            mask_l1_grid.view(n_row, n_col)
            .repeat_interleave(2, dim=0)
            .repeat_interleave(2, dim=1)
            .flatten()
        )
        fine_pos_l1 = pos_l1_full[:, 1:, :][:, mask_l1, :]

        pos_embeds = torch.cat([cls_pos.unsqueeze(0), fine_pos_l0, fine_pos_l1], dim=1).squeeze(0)
        return tokens, pos_embeds, masks


class CharmPatchEmbeddings(nn.Module):
    """
    Charm 补丁嵌入层（含可学习尺度编码 se、CLS token 与 14x14 卷积投影）。
    属性命名与官方 tad66k_charm.pth 状态字典严格一致。

    Charm Patch Embeddings (with learnable scale embeddings `se`, CLS token,
    and 14x14 conv projection). Attribute names strictly match official state_dict.
    """

    def __init__(
        self,
        patch_size: int = 14,
        embed_dim: int = 384,
        scales: int = 2,
    ) -> None:
        super().__init__()
        self.scales = scales
        self.patch_size = patch_size
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.se = nn.Parameter(torch.zeros(1, scales, embed_dim))
        self.mask_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.patch_embedder = nn.Conv2d(
            3, embed_dim, kernel_size=patch_size, stride=patch_size
        )

    def forward(
        self,
        patches: torch.Tensor,
        pos_embeds: torch.Tensor,
        masks: Optional[torch.Tensor],
    ) -> torch.Tensor:
        """
        前向计算补丁嵌入并叠加尺度编码与位置编码。

        参数:
        patches (torch.Tensor): (B, 1 + L, 3, 14, 14) 补丁张量
        pos_embeds (torch.Tensor): (B, 1 + L, 384) 位置编码张量
        masks (Optional[torch.Tensor]): (B, 1 + L) 尺度掩码张量

        返回:
        torch.Tensor: (B, 1 + L, 384) 嵌入特征序列

        Forward pass computing patch embeddings plus scale and positional embeddings.

        Parameters:
        patches (torch.Tensor): (B, 1 + L, 3, 14, 14) patch tensor
        pos_embeds (torch.Tensor): (B, 1 + L, 384) positional embedding tensor
        masks (Optional[torch.Tensor]): (B, 1 + L) scale mask tensor

        Return:
        torch.Tensor: (B, 1 + L, 384) embedded token sequence
        """
        visual_patches = patches[:, 1:, :, :, :]
        batch_size, seq_len, channels, height, width = visual_patches.shape
        flat_patches = visual_patches.reshape(-1, channels, height, width)

        embedding = self.patch_embedder(flat_patches).reshape(batch_size, seq_len, -1)

        if masks is not None and self.scales != 1:
            scale_idx = masks[:, 1:].long().clamp(0, self.scales - 1)
            scale_embed = self.se[0][scale_idx].to(dtype=embedding.dtype)
            embedding = embedding + scale_embed

        cls_tokens = self.cls_token.expand(batch_size, -1, -1).to(dtype=embedding.dtype)
        embedding = torch.cat((cls_tokens, embedding), dim=1)
        embedding = embedding + pos_embeds.to(dtype=embedding.dtype)
        return embedding


class _Dinov2SelfAttention(nn.Module):
    """DINOv2 多头自注意力查询/键/值投影子模块 / DINOv2 QKV projection submodule."""

    def __init__(self, embed_dim: int = 384, num_heads: int = 6) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        self.query = nn.Linear(embed_dim, embed_dim)
        self.key = nn.Linear(embed_dim, embed_dim)
        self.value = nn.Linear(embed_dim, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size, seq_len, embed_dim = x.shape
        q = (
            self.query(x)
            .view(batch_size, seq_len, self.num_heads, self.head_dim)
            .transpose(1, 2)
        )
        k = (
            self.key(x)
            .view(batch_size, seq_len, self.num_heads, self.head_dim)
            .transpose(1, 2)
        )
        v = (
            self.value(x)
            .view(batch_size, seq_len, self.num_heads, self.head_dim)
            .transpose(1, 2)
        )
        attn_out = F.scaled_dot_product_attention(q, k, v)
        return attn_out.transpose(1, 2).contiguous().view(batch_size, seq_len, embed_dim)


class _Dinov2SelfOutput(nn.Module):
    """DINOv2 自注意力输出线性层 / DINOv2 attention output linear projection."""

    def __init__(self, embed_dim: int = 384) -> None:
        super().__init__()
        self.dense = nn.Linear(embed_dim, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dense(x)


class _Dinov2Attention(nn.Module):
    """DINOv2 注意力封装模块 / DINOv2 attention wrapper matching HF state_dict keys."""

    def __init__(self, embed_dim: int = 384, num_heads: int = 6) -> None:
        super().__init__()
        self.attention = _Dinov2SelfAttention(embed_dim, num_heads)
        self.output = _Dinov2SelfOutput(embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.output(self.attention(x))


class _Dinov2LayerScale(nn.Module):
    """DINOv2 LayerScale 缩放层 / DINOv2 LayerScale module."""

    def __init__(self, embed_dim: int = 384) -> None:
        super().__init__()
        self.lambda1 = nn.Parameter(torch.ones(embed_dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * self.lambda1


class _Dinov2MLP(nn.Module):
    """DINOv2 前馈多层感知机 / DINOv2 feed-forward MLP."""

    def __init__(self, embed_dim: int = 384, mlp_ratio: int = 4) -> None:
        super().__init__()
        hidden_features = embed_dim * mlp_ratio
        self.fc1 = nn.Linear(embed_dim, hidden_features)
        self.fc2 = nn.Linear(hidden_features, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(F.gelu(self.fc1(x)))


class _Dinov2Layer(nn.Module):
    """DINOv2 单层 Transformer 块 / Single DINOv2 Transformer block."""

    def __init__(self, embed_dim: int = 384, num_heads: int = 6) -> None:
        super().__init__()
        self.norm1 = nn.LayerNorm(embed_dim, eps=1e-6)
        self.attention = _Dinov2Attention(embed_dim, num_heads)
        self.layer_scale1 = _Dinov2LayerScale(embed_dim)
        self.norm2 = nn.LayerNorm(embed_dim, eps=1e-6)
        self.mlp = _Dinov2MLP(embed_dim)
        self.layer_scale2 = _Dinov2LayerScale(embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.layer_scale1(self.attention(self.norm1(x)))
        x = x + self.layer_scale2(self.mlp(self.norm2(x)))
        return x


class _Dinov2EncoderInner(nn.Module):
    """匹配 encoder.encoder.layer.{i} 键层级的内部容器 / Inner container matching state_dict."""

    def __init__(self, depth: int = 12, embed_dim: int = 384, num_heads: int = 6) -> None:
        super().__init__()
        self.layer = nn.ModuleList(
            [_Dinov2Layer(embed_dim, num_heads) for _ in range(depth)]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for blk in self.layer:
            x = blk(x)
        return x


class _CharmTransformerEncoder(nn.Module):
    """DINOv2-small 12 层编码器与末尾 LayerNorm / DINOv2-small 12-layer encoder + LayerNorm."""

    def __init__(self, depth: int = 12, embed_dim: int = 384, num_heads: int = 6) -> None:
        super().__init__()
        self.encoder = _Dinov2EncoderInner(depth, embed_dim, num_heads)
        self.layernorm = nn.LayerNorm(embed_dim, eps=1e-6)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layernorm(self.encoder(x))


class _CharmMlpHead(nn.Module):
    """
    Charm 美学预测回归头：拼接 CLS token 与 patch 平均池化特征 (384*2=768) -> Linear -> ReLU。
    直接输出 [1, 10] 分制的 TAD66K MOS 预测分（与 tad66k_charm.pth 序列化对象中的 MlpHead 完全一致）。

    Charm aesthetic regression head: concatenates CLS token and mean-pooled patch
    tokens (384 * 2 = 768) -> Linear(768, 1) -> ReLU, directly outputting a
    [1, 10] TAD66K MOS score (matching the pickled MlpHead in tad66k_charm.pth).
    """

    def __init__(self, embed_dim: int = 384, num_classes: int = 1) -> None:
        super().__init__()
        self.linear = nn.Linear(embed_dim * 2, num_classes)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        cls_token_output = hidden_states[:, 0, :]
        patch_tokens = hidden_states[:, 1:, :]
        linear_input = torch.cat([cls_token_output, patch_tokens.mean(dim=1)], dim=1)
        return F.relu(self.linear(linear_input))


class CharmNet(nn.Module):
    """
    独立 Charm-TAD66K 美学评分网络。
    同时支持：
    1. 直接传入 4D 图像张量 (B, 3, H, W) 并返回 [1, 10] MOS 分数（兼容旧 CFANet 调用约定）；
    2. 传入已分词的 (patches, pos_embeds, masks) 三元组。

    Standalone Charm-TAD66K aesthetic scoring network.
    Supports both:
    1. Direct 4D image tensor (B, 3, H, W) returning [1, 10] MOS score (drop-in compatible
       with legacy CFANet call convention);
    2. Pre-tokenized (patches, pos_embeds, masks) tuple.
    """

    def __init__(
        self,
        embed_dim: int = 384,
        depth: int = 12,
        num_heads: int = 6,
        patch_size: int = 14,
        hidden_tokens: int = 768,
    ) -> None:
        super().__init__()
        self.patch_embeddings = CharmPatchEmbeddings(
            patch_size=patch_size,
            embed_dim=embed_dim,
            scales=2,
        )
        self.encoder = _CharmTransformerEncoder(
            depth=depth, embed_dim=embed_dim, num_heads=num_heads
        )
        self.mlp_head = _CharmMlpHead(embed_dim=embed_dim, num_classes=1)
        self.register_buffer("pos_embed", torch.zeros(1, 1370, embed_dim), persistent=True)
        self.tokenizer = CharmTokenizer(
            pos_embed=self.pos_embed,
            patch_size=patch_size,
            hidden_tokens=hidden_tokens,
        )

    def sync_tokenizer_pos_embed(self) -> None:
        """
        权重加载后将 buffer 中的 pos_embed 同步给分词器。

        参数:
        无

        返回:
        None

        Synchronize the loaded pos_embed buffer into the tokenizer after loading weights.

        Parameters:
        None

        Return:
        None
        """
        self.tokenizer.set_pos_embed(self.pos_embed)

    def forward_tokens(
        self,
        patches: torch.Tensor,
        pos_embeds: torch.Tensor,
        masks: Optional[torch.Tensor] = None,
        return_mos: bool = True,
    ) -> torch.Tensor:
        """
        对已分词的补丁序列执行前向推理。

        参数:
        patches (torch.Tensor): (B, 1 + L, 3, 14, 14) 补丁张量
        pos_embeds (torch.Tensor): (B, 1 + L, 384) 位置编码张量
        masks (Optional[torch.Tensor]): (B, 1 + L) 尺度掩码张量
        return_mos (bool): 若为 True 则乘 10 映射到 [0, 10] MOS 分制，否则返回 [0, 1]

        返回:
        torch.Tensor: 预测美学分数张量

        Run forward inference on pre-tokenized patch sequences.

        Parameters:
        patches (torch.Tensor): (B, 1 + L, 3, 14, 14) patch tensor
        pos_embeds (torch.Tensor): (B, 1 + L, 384) positional embedding tensor
        masks (Optional[torch.Tensor]): (B, 1 + L) scale mask tensor
        return_mos (bool): Multiply by 10.0 for [0, 10] MOS scale if True, else [0, 1]

        Return:
        torch.Tensor: Predicted aesthetic score tensor
        """
        param = self.patch_embeddings.cls_token
        target_device = param.device
        target_dtype = param.dtype

        patches = patches.to(device=target_device, dtype=target_dtype)
        pos_embeds = pos_embeds.to(device=target_device, dtype=target_dtype)
        if masks is not None:
            masks = masks.to(device=target_device)

        embedding = self.patch_embeddings(patches, pos_embeds, masks)
        encoded = self.encoder(embedding)
        pred = self.mlp_head(encoded)
        if return_mos:
            return pred
        return pred / 10.0

    def forward(
        self,
        x: torch.Tensor,
        pos_embeds: Optional[torch.Tensor] = None,
        masks: Optional[torch.Tensor] = None,
        return_mos: bool = True,
    ) -> torch.Tensor:
        """
        统一前向入口：
        - 若传入 4D 图像张量 (B, 3, H, W)，自动对每张图执行 CharmTokenizer 分词并推理；
        - 若传入 5D 补丁张量 (B, 1+L, 3, 14, 14) 及 pos_embeds/masks，直接推理。

        参数:
        x (torch.Tensor): 4D 图像张量 (B, 3, H, W) 或 5D 补丁张量 (B, 1+L, 3, 14, 14)
        pos_embeds (Optional[torch.Tensor]): 5D 模式下的位置编码张量
        masks (Optional[torch.Tensor]): 5D 模式下的尺度掩码张量
        return_mos (bool): 是否返回 10 分制分数，默认 True

        返回:
        torch.Tensor: 美学评分结果

        Unified forward entry point:
        - If given a 4D image tensor (B, 3, H, W), automatically tokenizes and scores each image;
        - If given a 5D patch tensor (B, 1+L, 3, 14, 14) with pos_embeds/masks, scores directly.

        Parameters:
        x (torch.Tensor): 4D image tensor (B, 3, H, W) or 5D patch tensor (B, 1+L, 3, 14, 14)
        pos_embeds (Optional[torch.Tensor]): Positional embeddings when x is 5D
        masks (Optional[torch.Tensor]): Scale masks when x is 5D
        return_mos (bool): Return score on 10-point scale if True (default True)

        Return:
        torch.Tensor: Aesthetic score tensor
        """
        if x.dim() == 5 and pos_embeds is not None:
            return self.forward_tokens(x, pos_embeds, masks, return_mos=return_mos)

        if x.dim() == 3:
            x = x.unsqueeze(0)
        if x.dim() != 4:
            raise ValueError(f"Expected 3D/4D image tensor or 5D patch tensor, got shape {x.shape}")

        scores: List[torch.Tensor] = []
        for idx in range(x.shape[0]):
            tokens, pe, msk = self.tokenizer.tokenize(x[idx])
            score = self.forward_tokens(
                tokens.unsqueeze(0),
                pe.unsqueeze(0),
                msk.unsqueeze(0),
                return_mos=return_mos,
            )
            scores.append(score)
        return torch.cat(scores, dim=0)


def load_charm_weights(
    model: CharmNet,
    weight_path: str,
    device: Union[str, torch.device] = "cpu",
) -> None:
    """
    安全加载 Charm 权重文件 (强制 weights_only=True) 并同步位置编码。

    参数:
    model (CharmNet): 待加载权重的 CharmNet 实例
    weight_path (str): 权重文件路径
    device (Union[str, torch.device]): 目标计算设备

    返回:
    None

    Securely load Charm checkpoint weights (enforcing weights_only=True) and
    synchronize positional embeddings into the tokenizer.

    Parameters:
    model (CharmNet): CharmNet instance to populate
    weight_path (str): Path to the checkpoint file
    device (Union[str, torch.device]): Target compute device

    Return:
    None
    """
    try:
        # 先加载到 CPU 再由调用方移动到目标设备 / load on CPU first; caller moves to target device
        state_dict = torch.load(weight_path, map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise RuntimeError(
            "Current PyTorch version does not support safe weights_only loading. "
            "Please upgrade PyTorch to load model weights securely."
        ) from exc

    if isinstance(state_dict, dict) and "params" in state_dict:
        state_dict = state_dict["params"]

    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    if missing:
        print(_t("logs.topiq_weight_missing", count=len(missing)))
    if unexpected:
        print(_t("logs.topiq_weight_unexpected", count=len(unexpected)))

    model.sync_tokenizer_pos_embed()
    print(_t("logs.charm_loaded"))


def get_charm_weight_path() -> str:
    """
    查找 Charm-TAD66K 权重文件路径。
    支持 PyInstaller 打包目录 (_MEIPASS)、源码 models/ 目录、热补丁 sys.path 及 macOS .app Resources。

    参数:
    无

    返回:
    str: 存在的权重文件绝对路径

    Locate the Charm-TAD66K checkpoint file path.
    Supports PyInstaller (_MEIPASS), source models/ directory, hot-patch sys.path,
    and macOS .app bundle Resources.

    Parameters:
    None

    Return:
    str: Resolved path to the existing weight file
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    search_paths: List[str] = []

    for weight_name in CHARM_WEIGHT_NAMES:
        candidates: List[str] = []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(getattr(sys, "_MEIPASS"), "models", weight_name))
        candidates.append(os.path.join(base_dir, "models", weight_name))
        candidates.append(os.path.join(base_dir, weight_name))
        for p in sys.path:
            if p and os.path.isdir(p):
                candidates.append(os.path.join(p, "models", weight_name))
        candidates.append(os.path.join(exe_dir, "models", weight_name))
        candidates.append(
            os.path.normpath(os.path.join(exe_dir, "..", "Resources", "models", weight_name))
        )
        for c in candidates:
            if c not in search_paths:
                search_paths.append(c)

    for path in search_paths:
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        f"Charm 美学权重文件未找到。请确保 models/{CHARM_WEIGHT_NAMES[0]} 存在。\n"
        f"搜索路径: {search_paths}"
    )
