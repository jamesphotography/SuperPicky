# -*- coding: utf-8 -*-
"""
ai_model._rescue_scan 单测：用 FakeModel 模拟 ultralytics 结果，
不加载真实 YOLO/BirdID 模型。

覆盖 4 条路径：直接接受 / 弱候选识鸟通过 / 弱候选识鸟拒绝 / 无候选。

_rescue_scan unit tests with a FakeModel mimicking ultralytics results;
no real YOLO/BirdID model is loaded. Covers direct-accept, gate-accept,
gate-reject and no-candidate paths.
"""
import numpy as np
import torch

import ai_model


class FakeBoxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = torch.tensor(xyxy, dtype=torch.float32)
        self.conf = torch.tensor(conf, dtype=torch.float32)
        self.cls = torch.tensor(cls, dtype=torch.float32)

    def __len__(self):
        return len(self.conf)


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes
        self.masks = None


class FakeModel:
    """返回预设检测结果的假 YOLO / Fake YOLO returning canned detections."""

    def __init__(self, xyxy, conf, cls):
        self._r = FakeResult(FakeBoxes(xyxy, conf, cls))

    def __call__(self, image, **kwargs):
        return [self._r]


IMG = np.zeros((683, 1024, 3), dtype=np.uint8)


def test_direct_accept_bird_above_threshold():
    model = FakeModel([[10, 10, 60, 60]], [0.8], [14])
    r = ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None)
    assert r is not None and r["source"] == "bird"
    assert abs(r["conf"] - 0.8) < 1e-6


def test_weak_bird_gate_accept(monkeypatch):
    model = FakeModel([[10, 10, 60, 60]], [0.12], [14])
    monkeypatch.setattr(ai_model, "_birdid_confirm",
                        lambda image, xyxy: ("红脚鹬", 81.7))
    r = ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None)
    assert r is not None and r["species"] == "红脚鹬"


def test_kite_candidate_gate_reject(monkeypatch):
    model = FakeModel([[10, 10, 60, 60]], [0.85], [33])  # kite
    monkeypatch.setattr(ai_model, "_birdid_confirm",
                        lambda image, xyxy: ("某鸟", 4.0))
    assert ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None) is None


def test_no_candidate_returns_none():
    model = FakeModel([[10, 10, 60, 60]], [0.9], [0])  # person
    assert ai_model._rescue_scan(model, IMG, 0.5, 10, ".", None) is None


def test_detect_returns_10_tuple_no_bird(tmp_path, monkeypatch):
    """空检测 + 补救关闭 → 10 元组，末位 rescued=False。
    Empty detections with rescue disabled → 10-tuple ending rescued=False."""
    import cv2

    jpg = str(tmp_path / "t.jpg")
    cv2.imwrite(jpg, np.zeros((64, 64, 3), dtype=np.uint8))

    class _Cfg:
        rescue_scan_enabled = False
        rescue_birdid_gate = 10

    monkeypatch.setattr(ai_model, "get_advanced_config", lambda: _Cfg())
    model = FakeModel(np.zeros((0, 4)), [], [])
    result = ai_model.detect_and_draw_birds(
        jpg, model, None, str(tmp_path), [50, 300, 5.0, False], None)
    assert len(result) == 10
    assert result[0] is False and result[9] is False
