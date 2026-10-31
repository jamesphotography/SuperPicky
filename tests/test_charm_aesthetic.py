# -*- coding: utf-8 -*-
"""
Charm-TAD66K 美学评分模型与权重回归测试。

Regression tests for the Charm-TAD66K aesthetic scoring model and weights.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

import charm_model
from charm_model import (
    CHARM_MAX_EDGE,
    CHARM_WEIGHT_NAMES,
    CharmNet,
    CharmTokenizer,
    get_charm_weight_path,
    load_charm_weights,
    resize_for_charm,
)
from iqa_scorer import IQAScorer
from scripts.ci_release import PATCH_ITEMS

REPO_ROOT = Path(__file__).resolve().parent.parent
FP16_NAME, FP32_NAME = CHARM_WEIGHT_NAMES
WEIGHT_FILE = REPO_ROOT / "models" / FP16_NAME


def _isolate_charm_lookup(tmp_path, monkeypatch, names):
    """隔离 get_charm_weight_path 的搜索路径到 tmp_path/models。"""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    for name in names:
        (models_dir / name).write_bytes(b"x")
    monkeypatch.setattr(charm_model, "__file__", str(tmp_path / "charm_model.py"))
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return models_dir


def test_charm_weight_lookup_priority(tmp_path, monkeypatch):
    """验证权重路径查找优先选用半精度版，其次回退原版，缺失时抛出 FileNotFoundError。"""
    models_dir = _isolate_charm_lookup(tmp_path, monkeypatch, [FP32_NAME, FP16_NAME])
    assert get_charm_weight_path() == str(models_dir / FP16_NAME)
    (models_dir / FP16_NAME).unlink()
    assert get_charm_weight_path() == str(models_dir / FP32_NAME)
    (models_dir / FP32_NAME).unlink()
    with pytest.raises(FileNotFoundError):
        get_charm_weight_path()


def test_ci_release_patch_items_includes_charm_model():
    """热补丁白名单必须包含 charm_model.py。"""
    assert "charm_model.py" in PATCH_ITEMS


def test_resize_for_charm_preserves_aspect_ratio():
    """resize_for_charm 应保持长宽比且不放大小图。"""
    small = Image.new("RGB", (400, 300))
    assert resize_for_charm(small, max_edge=CHARM_MAX_EDGE).size == (400, 300)

    large_3_2 = Image.new("RGB", (3000, 2000))
    out = resize_for_charm(large_3_2, max_edge=672)
    assert out.size == (672, 448)


def test_charm_tokenizer_determinism_both_flows():
    """验证 CharmTokenizer 在小图 (normal_flow) 与大图 (highResPreserve_ms) 下均完全确定。"""
    torch.manual_seed(42)
    dummy_pos = torch.randn(1, 1370, 384)
    tokenizer = CharmTokenizer(pos_embed=dummy_pos)

    # 1. normal_flow: 280x280 -> 20x20 = 400 <= 769 patches
    small_img = torch.rand(3, 280, 280)
    t1, p1, m1 = tokenizer.tokenize(small_img)
    t2, p2, m2 = tokenizer.tokenize(small_img)
    assert torch.equal(t1, t2)
    assert torch.equal(p1, p2)
    assert torch.equal(m1, m2)
    assert t1.shape[0] == 401

    # 2. highResPreserve_ms: 448x672 -> 32x48 = 1536 > 769 patches
    large_img = torch.rand(3, 448, 672)
    lt1, lp1, lm1 = tokenizer.tokenize(large_img)
    lt2, lp2, lm2 = tokenizer.tokenize(large_img)
    assert torch.equal(lt1, lt2)
    assert torch.equal(lp1, lp2)
    assert torch.equal(lm1, lm2)
    # 16 * 24 = 384 coarse blocks -> 1 CLS + (384 - 64) * 1 + 64 * 4 = 577 tokens
    assert lt1.shape == (577, 3, 14, 14)
    assert lp1.shape == (577, 384)
    assert lm1.shape == (577,)


@pytest.mark.skipif(
    not WEIGHT_FILE.is_file(),
    reason="models/charm_tad66k_fp16.pth 未生成",
)
def test_charm_fp16_checkpoint_structure_and_size():
    """验证半精度权重文件支持 weights_only=True 安全加载，且体积 < 46 MB、全部浮点参数为 fp16。"""
    size_bytes = WEIGHT_FILE.stat().st_size
    assert 40_000_000 < size_bytes < 46_000_000, f"Unexpected weight size: {size_bytes}"

    ckpt = torch.load(str(WEIGHT_FILE), map_location="cpu", weights_only=True)
    assert "params" in ckpt
    params = ckpt["params"]
    assert "pos_embed" in params
    assert params["pos_embed"].shape == (1, 1370, 384)
    assert len(params) == 226
    for key, tensor in params.items():
        if tensor.is_floating_point():
            assert tensor.dtype == torch.float16, f"{key} is not float16"


@pytest.mark.skipif(
    not WEIGHT_FILE.is_file(),
    reason="models/charm_tad66k_fp16.pth 未生成",
)
def test_charm_net_fp32_and_fp16_inference_consistency():
    """验证 CharmNet 在 FP32 与 FP16 模式下均可正常推理且分数高度一致。"""
    torch.manual_seed(7)
    img_tensor = torch.rand(1, 3, 448, 672)

    net_fp32 = CharmNet()
    load_charm_weights(net_fp32, str(WEIGHT_FILE), device="cpu")
    net_fp32.eval()

    net_fp16 = CharmNet()
    load_charm_weights(net_fp16, str(WEIGHT_FILE), device="cpu")
    net_fp16.half().eval()

    with torch.inference_mode():
        s32 = net_fp32(img_tensor, return_mos=True).item()
        s16 = net_fp16(img_tensor.half(), return_mos=True).item()

    assert 1.0 <= s32 <= 10.0
    assert 1.0 <= s16 <= 10.0
    assert abs(s32 - s16) < 0.05, f"FP32 ({s32}) vs FP16 ({s16}) drift too large"


@pytest.mark.skipif(
    not WEIGHT_FILE.is_file(),
    reason="models/charm_tad66k_fp16.pth 未生成",
)
def test_iqa_scorer_end_to_end_determinism(tmp_path):
    """验证 IQAScorer 从路径与 BGR 数组计算美学分数的端到端确定性。"""
    rng = np.random.default_rng(123)
    bgr = rng.integers(0, 255, size=(600, 900, 3), dtype=np.uint8)
    img_path = tmp_path / "sample.png"
    Image.fromarray(bgr[:, :, ::-1]).save(img_path)

    scorer = IQAScorer(device="cpu")
    s_arr_1 = scorer.calculate_from_array(bgr)
    s_arr_2 = scorer.calculate_from_array(bgr)
    s_path = scorer.calculate_aesthetic(str(img_path))

    assert s_arr_1 is not None and 1.0 <= s_arr_1 <= 10.0
    assert s_arr_1 == s_arr_2
    assert s_path is not None and abs(s_arr_1 - s_path) < 1e-4
