# -*- coding: utf-8 -*-
"""
BirdTrace 识鸟模型单测 / Unit tests for the BirdTrace model wrapper.

不依赖模型文件的部分总是运行；需要 222 MB 权重的冒烟测试在权重缺失时跳过。
Weight-free checks always run; the smoke test needing the 222 MB weights is skipped when absent.
"""
import json
import os

import pytest
from PIL import Image

from birdid.birdtrace_classifier import (
    BIRDTRACE_CLASS_MAP_FILE,
    BIRDTRACE_INPUT,
    BIRDTRACE_WEIGHTS_FILE,
    birdtrace_available,
    square_input,
)
from birdid.class_mapping import NUM_CLASSES

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS = os.path.join(REPO, "models", BIRDTRACE_WEIGHTS_FILE)
CLASS_MAP = os.path.join(REPO, "birdid", "data", BIRDTRACE_CLASS_MAP_FILE)


def test_class_map_is_committed_and_well_formed():
    with open(CLASS_MAP, encoding="utf-8") as f:
        data = json.load(f)
    cm = data["class_map"]
    assert len(cm) == 10480 == data["meta"]["classes"]
    assert all(-1 <= c < NUM_CLASSES for c in cm)
    assert sum(c >= 0 for c in cm) == data["meta"]["mapped"]


def test_square_input_center_crops_full_images_and_resizes_crops():
    wide = Image.new("RGB", (800, 400), (255, 0, 0))
    assert square_input(wide, False).size == (BIRDTRACE_INPUT, BIRDTRACE_INPUT)
    assert square_input(Image.new("L", (100, 100)), True).mode == "RGB"


def test_available_requires_both_files(tmp_path):
    w, m = tmp_path / "w.safetensors", tmp_path / "m.json"
    assert not birdtrace_available(str(w), str(m))
    w.write_bytes(b"x")
    assert not birdtrace_available(str(w), str(m))
    m.write_text("{}", encoding="utf-8")
    assert birdtrace_available(str(w), str(m))


@pytest.mark.skipif(not os.path.isfile(WEIGHTS), reason="BirdTrace 权重未下载 / weights not downloaded")
def test_smoke_inference_returns_species_db_probabilities():
    from birdid.birdtrace_classifier import BirdTraceClassifier

    clf = BirdTraceClassifier(WEIGHTS, CLASS_MAP)
    p = clf.probs(Image.new("RGB", (500, 500), (120, 140, 90)), True)
    assert p.shape == (NUM_CLASSES,)
    assert 0.0 < p.sum() <= 1.0 + 1e-6
