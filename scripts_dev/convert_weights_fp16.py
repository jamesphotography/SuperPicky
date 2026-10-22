# -*- coding: utf-8 -*-
"""
把模型权重转成半精度（fp16）存储：TOPIQ 美学 294 MB → 147 MB，关键点 99 MB → 50 MB。
Convert model weights to half-precision storage: TOPIQ aesthetic 294 -> 147 MB, keypoint 99 -> 50 MB.

背景 / Background:
    Windows CUDA 安装包逼近 GitHub Release 单文件 2 GiB 上限，加入 BirdTrace 后放不下。
    这两个模型在 MPS/CUDA 上本来就整体 .half() 推理（iqa_scorer、keypoint_detector），所以 GPU
    用户的结果与原权重逐位一致；只有纯 CPU 推理（加载回 fp32）有极小差异。只适用于 GPU 上
    本来就半精度推理的模型。关键点模型的半精度版目前没有随包分发：CPU 上头部锐度个别照片
    会差到约 40 分（120 张里 1 张），等 RC7 实测 CUDA 包体积后再定。
    The Windows CUDA installer is near GitHub's 2 GiB per-asset limit. Both models already run
    in .half() on MPS/CUDA, so GPU results are bit-identical; only CPU-only inference (loaded back
    to fp32) differs slightly. Use this only for models that already run at half precision on GPU.
    The fp16 keypoint file is not shipped yet: on CPU, head sharpness moved by ~40 points on 1 of
    120 photos; revisit after measuring the RC7 CUDA installer size.

用法 / Usage:
    python3 scripts_dev/convert_weights_fp16.py models/cfanet_iaa_ava_res50-3cd62bb3.pth \
        models/cfanet_iaa_ava_res50_fp16.pth
    python3 scripts_dev/convert_weights_fp16.py models/cub200_keypoint_resnet50_slim.pth \
        models/cub200_keypoint_resnet50_slim_fp16.pth

输出文件保持原文件的结构（{'params': {...}}、{'model_state_dict': {...}} 或纯 state_dict），
加载代码无需区分。
The output keeps the source layout ({'params': ...}, {'model_state_dict': ...} or a bare
state_dict), so loaders need no changes.
"""
import argparse
import hashlib

import torch


def convert(src: str, dst: str) -> str:
    """
    转换权重并返回输出文件的 SHA256 / Convert the weights and return the output SHA256.

    参数 / Parameters:
        src (str): 原 fp32 权重路径 / Source fp32 checkpoint.
        dst (str): 输出 fp16 权重路径 / Output fp16 checkpoint.

    返回 / Returns:
        str: 输出文件 SHA256 / SHA256 of the output file.
    """
    ckpt = torch.load(src, map_location="cpu", weights_only=True)
    wrapper = next((k for k in ("params", "model_state_dict") if k in ckpt), None)
    params = ckpt[wrapper] if wrapper else ckpt
    # 只转浮点张量，整型 buffer（如 BN 计数）保持原样 / only float tensors; integer buffers unchanged
    half = {k: (v.half() if v.is_floating_point() else v) for k, v in params.items()}
    torch.save({wrapper: half} if wrapper else half, dst)
    h = hashlib.sha256()
    with open(dst, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    """命令行入口 / CLI entry."""
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    a = ap.parse_args()
    print(f"sha256 {convert(a.src, a.dst)}")


if __name__ == "__main__":
    main()
