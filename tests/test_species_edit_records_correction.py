# -*- coding: utf-8 -*-
"""改鸟种 → 纠错入参拼装（覆盖前抓原预测）单测。"""
from ui.results_browser_window import build_correction_payload


def test_payload_captures_original_prediction_before_overwrite():
    photo = {
        "filename": "IMG_7",
        "bird_species_cn": "长嘴捕蛛鸟",
        "bird_species_en": "Little Spiderhunter",
        "birdid_confidence": 0.012,
    }
    payload = build_correction_payload(
        photo, new_cn="黄腹花蜜鸟", new_en="Olive-backed Sunbird",
        new_latin="Cinnyris jugularis",
    )
    assert payload["filename"] == "IMG_7"
    assert payload["wrong_cn"] == "长嘴捕蛛鸟"       # 原预测
    assert payload["wrong_en"] == "Little Spiderhunter"
    assert payload["corrected_cn"] == "黄腹花蜜鸟"    # 新值
    assert payload["corrected_latin"] == "Cinnyris jugularis"
    assert payload["birdid_confidence"] == 0.012
