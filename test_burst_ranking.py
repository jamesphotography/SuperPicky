# -*- coding: utf-8 -*-
"""
core/burst_ranking.py 纯函数单测 — 连拍组「最佳一张」分层排序。
Pure-function tests for burst best-pick tiered ranking (no Qt).
"""
from core.burst_ranking import eye_sharp, focus_tier, burst_composite_key


def test_eye_sharp_takes_max_of_two_eyes():
    # 鸟多侧拍,只有对焦侧那只眼有效 / side profile: take the in-focus eye
    assert eye_sharp({"left_eye": 20.0, "right_eye": 88.0}) == 88.0
    assert eye_sharp({"left_eye": 88.0, "right_eye": 0.0}) == 88.0


def test_eye_sharp_missing_fields_zero():
    assert eye_sharp({}) == 0.0
    assert eye_sharp({"left_eye": None, "right_eye": None}) == 0.0


def test_focus_tier_mapping_and_default():
    assert focus_tier({"focus_status": "BEST"}) == 3
    assert focus_tier({"focus_status": "GOOD"}) == 2
    assert focus_tier({"focus_status": "BAD"}) == 1
    assert focus_tier({"focus_status": "WORST"}) == 0
    # 缺失/空/未知 → 中性档 2 / missing/unknown → neutral GOOD tier
    assert focus_tier({}) == 2
    assert focus_tier({"focus_status": ""}) == 2
    assert focus_tier({"focus_status": "weird"}) == 2


def test_composite_key_focus_tier_dominates():
    # 跨档:BEST 档一张,即使头锐更低,也胜过 BAD 档最锐那张
    best = {"head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"}
    sharp_but_bad_focus = {"head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"}
    assert burst_composite_key(best) > burst_composite_key(sharp_but_bad_focus)


def test_composite_key_same_tier_eye_leads():
    # 同档:眼清为主(头锐差距不大时,眼清高者胜)
    eye_hi = {"head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"}
    eye_lo = {"head_sharp": 99, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"}
    assert burst_composite_key(eye_hi) > burst_composite_key(eye_lo)


def test_max_over_example_group_picks_focus_best():
    # spec §问题示例组:现状(纯头锐)会选 ②(头95);新逻辑应选 ③(对焦 BEST+眼实)
    group = [
        {"filename": "1", "head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"},
        {"filename": "2", "head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"},
        {"filename": "3", "head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"},
        {"filename": "4", "head_sharp": 70, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"},
    ]
    best = max(group, key=burst_composite_key)
    assert best["filename"] == "3"
