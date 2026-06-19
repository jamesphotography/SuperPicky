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
