#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 Charm-TAD66K 官方权重 (tad66k_charm.pth + dino_small_pos.pt) 提取并合并为
纯净的半精度 (FP16) 状态字典文件 models/charm_tad66k_fp16.pth。

Convert official Charm-TAD66K checkpoints (tad66k_charm.pth + dino_small_pos.pt)
into a clean, self-contained half-precision (FP16) state_dict file
models/charm_tad66k_fp16.pth.

背景与设计目的 / Background & Motivation:
1. 官方发布的 tad66k_charm.pth (86.3 MB FP32) 使用了 torch.save(model_instance)
   直接序列化自定义 Python 类 (model.Model 与 ml_collections.ConfigDict)，
   导致无法使用安全的 torch.load(..., weights_only=True)，且强依赖
   transformers 与 ml_collections。
2. 官方 Charm_Tokenizer 在初始化时还会额外请求 dino_small_pos.pt (2.11 MB)。
3. 本脚本将二者合并提取为纯 PyTorch Tensor 字典 {'params': {...}}，并将全部
   浮点张量转换为 float16 存储：
   - 磁盘体积从 88.4 MB (对比旧 TOPIQ FP32 294 MB / FP16 147 MB) 降至 44.2 MB；
   - 支持 torch.load(..., weights_only=True) 安全加载；
   - 运行时彻底脱离 transformers / ml_collections / Charm_tokenizer 第三方依赖。
"""

import argparse
import hashlib
import os
import sys
import types
from typing import Dict

import torch


def _install_unpickle_shims(charm_pkg_dir: str, hf_snapshot_dir: str) -> None:
    """
    安装反序列化官方 tad66k_charm.pth 所需的最小兼容桩模块。

    参数:
    charm_pkg_dir (str): 解包后的 Charm_tokenizer 根目录路径
    hf_snapshot_dir (str): 存放 tad66k_charm.pth 与 dino_small_pos.pt 的目录路径

    返回:
    None

    Install minimal compatibility shims required to unpickle the official
    tad66k_charm.pth checkpoint.

    Parameters:
    charm_pkg_dir (str): Path to the unpacked Charm_tokenizer root directory
    hf_snapshot_dir (str): Directory containing tad66k_charm.pth and dino_small_pos.pt

    Return:
    None
    """
    ml_mod = types.ModuleType("ml_collections")
    ml_dt = types.ModuleType("ml_collections.config_dict")
    ml_dt_sub = types.ModuleType("ml_collections.config_dict.config_dict")

    class ConfigDict(dict):
        """轻量替代 ml_collections.ConfigDict / Lightweight ConfigDict shim."""

        def __getattr__(self, key: str):
            try:
                return self[key]
            except KeyError as exc:
                raise AttributeError(key) from exc

        def __setattr__(self, key: str, value) -> None:
            self[key] = value

    ml_mod.ConfigDict = ConfigDict  # type: ignore[attr-defined]
    ml_dt.ConfigDict = ConfigDict  # type: ignore[attr-defined]
    ml_dt_sub.ConfigDict = ConfigDict  # type: ignore[attr-defined]
    sys.modules["ml_collections"] = ml_mod
    sys.modules["ml_collections.config_dict"] = ml_dt
    sys.modules["ml_collections.config_dict.config_dict"] = ml_dt_sub

    pkg_inner = os.path.join(charm_pkg_dir, "Charm_tokenizer")
    for p in (charm_pkg_dir, pkg_inner):
        if p not in sys.path:
            sys.path.insert(0, p)

    import huggingface_hub
    import Charm_tokenizer.Backbone as bb  # type: ignore[import-not-found]
    import Charm_tokenizer.ImageProcessor as ip  # type: ignore[import-not-found]

    def _local_hf_download(repo_id: str, filename: str, **kwargs) -> str:
        candidate = os.path.join(hf_snapshot_dir, filename)
        if os.path.exists(candidate):
            return candidate
        raise FileNotFoundError(f"Missing local Charm file: {candidate}")

    huggingface_hub.hf_hub_download = _local_hf_download
    bb.hf_hub_download = _local_hf_download
    ip.hf_hub_download = _local_hf_download


def convert_charm_checkpoint(
    charm_ckpt_path: str,
    pos_embed_path: str,
    dst_path: str,
    charm_pkg_dir: str,
) -> str:
    """
    提取并合并 Charm 模型参数与位置编码，保存为纯 Tensor 的 FP16 权重文件。

    参数:
    charm_ckpt_path (str): 官方 tad66k_charm.pth 路径
    pos_embed_path (str): 官方 dino_small_pos.pt 路径
    dst_path (str): 输出 models/charm_tad66k_fp16.pth 路径
    charm_pkg_dir (str): 解包后的 Charm_tokenizer 目录路径

    返回:
    str: 输出权重文件的 SHA256 校验值

    Extract and merge Charm model weights and positional embeddings into a
    pure-tensor FP16 checkpoint file.

    Parameters:
    charm_ckpt_path (str): Path to official tad66k_charm.pth
    pos_embed_path (str): Path to official dino_small_pos.pt
    dst_path (str): Destination path for models/charm_tad66k_fp16.pth
    charm_pkg_dir (str): Unpacked Charm_tokenizer directory path

    Return:
    str: SHA256 hex digest of the written checkpoint file
    """
    hf_dir = os.path.dirname(os.path.abspath(charm_ckpt_path))
    _install_unpickle_shims(charm_pkg_dir, hf_dir)

    raw_ckpt = torch.load(charm_ckpt_path, map_location="cpu", weights_only=False)
    model_obj = raw_ckpt["model_state_dict"]
    raw_state: Dict[str, torch.Tensor] = model_obj.state_dict()

    pos_embed = torch.load(pos_embed_path, map_location="cpu", weights_only=False)
    if isinstance(pos_embed, torch.Tensor):
        pos_tensor = pos_embed.detach().cpu()
    else:
        raise TypeError(f"Unexpected pos_embed type: {type(pos_embed)}")

    params_fp16: Dict[str, torch.Tensor] = {}
    for key, tensor in raw_state.items():
        t_cpu = tensor.detach().cpu()
        params_fp16[key] = t_cpu.half() if t_cpu.is_floating_point() else t_cpu

    params_fp16["pos_embed"] = pos_tensor.half()

    os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
    torch.save({"params": params_fp16}, dst_path)

    sha256_hash = hashlib.sha256()
    with open(dst_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def main() -> None:
    """
    命令行入口函数。

    参数:
    无

    返回:
    None

    CLI entry point.

    Parameters:
    None

    Return:
    None
    """
    default_hf = os.path.expanduser(
        "~/.cache/huggingface/hub/models--FatemehBehrad--Charm/"
        "snapshots/7feb32371796b4e6732e6f662c55fa4e87538835"
    )
    default_pkg = (
        "/Users/jameszhenyu/.gemini/antigravity-cli/brain/"
        "7fc93ffe-99ee-4021-aa92-b91b5ed4f50c/scratch/charm_pkg"
    )
    parser = argparse.ArgumentParser(
        description="Convert Charm-TAD66K weights to pure-tensor FP16 format."
    )
    parser.add_argument(
        "--src-ckpt",
        default=os.path.join(default_hf, "tad66k_charm.pth"),
        help="Path to official tad66k_charm.pth",
    )
    parser.add_argument(
        "--src-pos",
        default=os.path.join(default_hf, "dino_small_pos.pt"),
        help="Path to official dino_small_pos.pt",
    )
    parser.add_argument(
        "--charm-pkg",
        default=default_pkg,
        help="Path to unpacked Charm_tokenizer directory",
    )
    parser.add_argument(
        "--dst",
        default="models/charm_tad66k_fp16.pth",
        help="Output path for merged FP16 checkpoint",
    )
    args = parser.parse_args()

    digest = convert_charm_checkpoint(
        charm_ckpt_path=args.src_ckpt,
        pos_embed_path=args.src_pos,
        dst_path=args.dst,
        charm_pkg_dir=args.charm_pkg,
    )
    size_bytes = os.path.getsize(args.dst)
    print(f"Saved: {args.dst} ({size_bytes:,} bytes / {size_bytes / 1e6:.2f} MB)")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
