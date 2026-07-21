# -*- coding: utf-8 -*-
"""
_burst_representative 接线冒烟测试:验证组内"最佳"用分层排序,而非纯头锐。
Wiring smoke test: representative uses tiered ranking, not raw head sharpness.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.results_browser_window import _burst_representative


def test_representative_prefers_focus_best_over_sharpest():
    # 现状(纯 head_sharp)会选 ②(头95);接线后应选 ③(对焦 BEST+眼实)
    group = [
        {"filename": "1", "head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"},
        {"filename": "2", "head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"},
        {"filename": "3", "head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"},
        {"filename": "4", "head_sharp": 70, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"},
    ]
    assert _burst_representative(group)["filename"] == "3"


def test_representative_all_fields_missing_does_not_crash():
    # 退化保护:字段全缺失时不抛异常,仍返回组内某一张
    group = [{"filename": "a"}, {"filename": "b"}]
    assert _burst_representative(group)["filename"] in {"a", "b"}
