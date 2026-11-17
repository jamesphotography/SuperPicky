# -*- coding: utf-8 -*-
"""
模型搬到 GPU 失败时退回 CPU（CLAUDE.md：先 CPU 加载，CUDA 失败可见地回退 CPU）。

get_best_device 只检查架构不实跑；显存不足 / 驱动异常要到搬模型时才暴露。此前关键点、
飞版、TOPIQ 直接 map_location=GPU，一出错整次选片中断。本机没有 CUDA，to("cuda")
本身就会抛错，正好模拟 GPU 不可用。

Moving a model to an unusable GPU falls back to the CPU instead of aborting.
"""
import os

import pytest
import torch

from config import move_model_to_device

pytestmark = pytest.mark.skipif(torch.cuda.is_available(), reason="需要一台没有可用 CUDA 的机器来模拟失败")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_falls_back_to_cpu_fp32():
    """搬到不可用的 CUDA：回到 CPU、全精度、不抛错。"""
    model = torch.nn.Linear(4, 2)
    model, device, use_half = move_model_to_device(model, torch.device("cuda"), "Test", half=True)
    assert device.type == "cpu"
    assert use_half is False
    assert next(model.parameters()).dtype == torch.float32
    assert model(torch.zeros(1, 4)).shape == (1, 2)


def test_cpu_target_unchanged():
    """目标就是 CPU：原样返回，不转半精度。"""
    model, device, use_half = move_model_to_device(torch.nn.Linear(2, 2), torch.device("cpu"), "Test", half=True)
    assert device.type == "cpu" and use_half is False


def test_keypoint_detector_survives_unusable_gpu():
    """关键点模型目标设备是不可用的 CUDA 时仍能加载并推理（端到端）。"""
    path = os.path.join(_ROOT, "models", "cub200_keypoint_resnet50_slim.pth")
    if not os.path.exists(path):
        pytest.skip("关键点模型文件不在本机")
    import numpy as np
    from core.keypoint_detector import KeypointDetector

    det = KeypointDetector(path)
    det.device = torch.device("cuda")
    det.load_model()
    assert det.device.type == "cpu" and det._use_fp16 is False
    result = det.detect(np.zeros((200, 200, 3), dtype=np.uint8))
    assert result is not None
