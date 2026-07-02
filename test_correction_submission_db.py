# -*- coding: utf-8 -*-
"""tools/report_db.py 纠错表 + 同鸟种正样本查询单测。"""
import os
import tempfile

from tools.report_db import ReportDB


def _fresh_db() -> ReportDB:
    d = tempfile.mkdtemp()
    return ReportDB(d)


def test_insert_and_get_corrections():
    db = _fresh_db()
    db.insert_correction({
        "filename": "IMG_0001",
        "wrong_cn": "长嘴捕蛛鸟", "wrong_en": "Little Spiderhunter",
        "corrected_model_class_id": 7447,
        "corrected_cn": "黄腹花蜜鸟", "corrected_en": "Olive-backed Sunbird",
        "birdid_confidence": 0.012,
    })
    rows = db.get_corrections()
    assert len(rows) == 1
    r = rows[0]
    assert r["filename"] == "IMG_0001"
    assert r["wrong_cn"] == "长嘴捕蛛鸟"
    assert r["corrected_model_class_id"] == 7447
    assert r["corrected_cn"] == "黄腹花蜜鸟"
    assert r["created_at"]  # 自动填充
    db.close()


def test_get_photos_by_species_matches_cn_excludes_failed_and_rejects():
    db = _fresh_db()
    # 三张同鸟种正确样本 + 一张被改正图（排除自身）+ 一张废片(rating=-1) + 一张异种
    db.insert_photo({"filename": "GOOD_1", "has_bird": 1, "rating": 3,
                     "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird"})
    db.insert_photo({"filename": "GOOD_2", "has_bird": 1, "rating": 2,
                     "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird"})
    db.insert_photo({"filename": "SELF", "has_bird": 1, "rating": 3,
                     "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird"})
    db.insert_photo({"filename": "REJECT", "has_bird": 1, "rating": -1,
                     "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird"})
    db.insert_photo({"filename": "OTHER", "has_bird": 1, "rating": 3,
                     "bird_species_cn": "白头鹎", "bird_species_en": "Light-vented Bulbul"})
    got = db.get_photos_by_species(cn="黄腹花蜜鸟", en=None, exclude_filename="SELF")
    names = {p["filename"] for p in got}
    assert names == {"GOOD_1", "GOOD_2"}
    db.close()
