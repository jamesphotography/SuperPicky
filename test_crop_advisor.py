#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/crop_advisor.py 单元测试。"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from core import crop_advisor as ca


def test_parse_ratio():
    assert ca._parse_ratio("3:2") == pytest.approx(1.5)
    assert ca._parse_ratio("2.39:1") == pytest.approx(2.39)
    assert ca._parse_ratio("1:1") == pytest.approx(1.0)


def test_pick_orientation():
    assert ca._pick_orientation((0, 0, 200, 100)) == "landscape"  # 宽>高
    assert ca._pick_orientation((0, 0, 100, 200)) == "portrait"   # 高>宽
    assert ca._pick_orientation((0, 0, 100, 100)) == "landscape"  # 宽=高→横


def test_union_bbox():
    assert ca._union_bbox([(10, 10, 50, 50), (40, 5, 80, 60)]) == (10, 5, 80, 60)


def test_fit_ratio_box_contains_subject_and_inside_image():
    # 主体 (40,40,60,60),图 200x200,比例 1:1,锚点居中,边距 5px
    box = ca._fit_ratio_box((40, 40, 60, 60), 1.0, (50, 50), 200, 200, 5)
    assert box is not None
    x1, y1, x2, y2 = box
    # 包含主体+边距
    assert x1 <= 35 and y1 <= 35 and x2 >= 65 and y2 >= 65
    # 在图界内
    assert 0 <= x1 and 0 <= y1 and x2 <= 200 and y2 <= 200
    # 比例约为 1:1
    assert (x2 - x1) == pytest.approx(y2 - y1, abs=1)


def test_fit_ratio_box_returns_none_when_cannot_fit():
    # 主体几乎占满整图,2.39:1 无法在图内容纳主体+边距 → None
    box = ca._fit_ratio_box((5, 5, 195, 195), 2.39, (100, 100), 200, 200, 10)
    assert box is None


def test_thirds_center_places_eye_opposite_gaze():
    # 鸟朝右(喙在眼右侧)→ 眼应落在左 1/3 → 目标中心应偏右于眼
    subject = (60, 60, 140, 140)
    eye = (90, 100); beak = (130, 100)  # 朝右
    cx, cy = ca._thirds_center(subject, eye, beak, 1.5, 400, 300, 10)
    assert cx > eye[0]  # 中心在眼右侧,使眼落在画面左侧


# ── Task 3 测试:编排函数(全部用假依赖) / Task 3 tests: orchestration with fakes ──

def _fake_topiq(crop_bgr):
    # 越宽的裁剪给越高分,使排序可预测
    # Wider crops get higher scores to make sort order predictable
    h, w = crop_bgr.shape[:2]
    return 5.0 + w / 10000.0


def _img(w=400, h=300):
    return ca.np.zeros((h, w, 3), dtype=ca.np.uint8)


def test_advise_no_bird():
    res = ca.advise_crops(
        "x.jpg",
        detect_fn=lambda img: [],
        cacnet_fn=lambda img: (0, 0, 10, 10),
        keypoint_fn=lambda img, b: None,
        topiq_fn=_fake_topiq,
        _image_loader=lambda p: _img(),
    )
    assert res.status == "no_bird" and res.suggestions == []


def test_advise_too_many_birds():
    birds = [((10, 10, 30, 30), 0.9)] * 4
    res = ca.advise_crops(
        "x.jpg",
        detect_fn=lambda img: birds,
        cacnet_fn=lambda img: (0, 0, 10, 10),
        keypoint_fn=lambda img, b: None,
        topiq_fn=_fake_topiq,
        _image_loader=lambda p: _img(),
    )
    assert res.status == "too_many_birds" and res.bird_count == 4


def test_advise_single_bird_sorted_desc():
    res = ca.advise_crops(
        "x.jpg",
        detect_fn=lambda img: [((160, 120, 240, 180), 0.95)],
        cacnet_fn=lambda img: (140, 100, 260, 200),
        keypoint_fn=lambda img, b: ((200, 150), (235, 150)),
        topiq_fn=_fake_topiq,
        _image_loader=lambda p: _img(400, 300),
    )
    assert res.status == "ok"
    assert len(res.suggestions) >= 1
    scores = [s.topiq_score for s in res.suggestions]
    assert scores == sorted(scores, reverse=True)
    for s in res.suggestions:  # 每个候选都含主体 / each candidate contains the subject
        x1, y1, x2, y2 = s.box
        assert x1 <= 160 and y1 <= 120 and x2 >= 240 and y2 >= 180


def test_advise_two_birds_uses_union():
    res = ca.advise_crops(
        "x.jpg",
        detect_fn=lambda img: [((40, 40, 80, 80), 0.9), ((300, 200, 340, 260), 0.8)],
        cacnet_fn=lambda img: (0, 0, 1, 1),  # 多鸟不应被调用,给哨兵
        keypoint_fn=lambda img, b: (_ for _ in ()).throw(AssertionError("多鸟不应调用关键点")),
        topiq_fn=_fake_topiq,
        _image_loader=lambda p: _img(400, 300),
    )
    assert res.status == "ok" and res.bird_count == 2
    for s in res.suggestions:  # 含两鸟并集 (40,40,340,260) / contains union of two birds
        x1, y1, x2, y2 = s.box
        assert x1 <= 40 and y1 <= 40 and x2 >= 340 and y2 >= 260
