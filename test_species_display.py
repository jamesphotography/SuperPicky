# -*- coding: utf-8 -*-
"""
鸟种显示文字单测 / Unit tests for species display text.

确认鸟种优先；没有确认鸟种但有候选时显示「鸟名（待确定 N%）」；两者都没有返回空串。
Confirmed species wins; otherwise a candidate renders as "name (unconfirmed N%)";
neither yields an empty string.
"""
import sqlite3
import tempfile

from tools.species_display import species_display_text


def fmt(name: str, confidence: str) -> str:
    """测试用格式化 / Test formatter."""
    return f"{name}（待确定 {confidence}%）"


def test_confirmed_species_wins():
    """有确认鸟种时忽略候选 / A confirmed species ignores the candidate."""
    photo = {"bird_species_cn": "红颈滨鹬", "bird_species_en": "Red-necked Stint",
             "alt_species_cn": "小滨鹬", "alt_confidence": 40}
    assert species_display_text(photo, is_en=False, format_unconfirmed=fmt) == "红颈滨鹬"
    assert species_display_text(photo, is_en=True, format_unconfirmed=fmt) == "Red-necked Stint"


def test_candidate_is_formatted_with_rounded_confidence():
    """无确认鸟种时显示候选与取整置信度 / Candidate shows with rounded confidence."""
    photo = {"bird_species_cn": None, "alt_species_cn": "小滨鹬",
             "alt_species_en": "Little Stint", "alt_confidence": 44.6}
    assert species_display_text(photo, is_en=False, format_unconfirmed=fmt) == "小滨鹬（待确定 45%）"
    assert species_display_text(photo, is_en=True, format_unconfirmed=fmt) == "Little Stint（待确定 45%）"


def test_language_fallback_for_candidate():
    """候选缺当前语言名时回退另一语言 / Candidate falls back to the other language."""
    photo = {"alt_species_cn": "小滨鹬", "alt_species_en": "", "alt_confidence": 30}
    assert species_display_text(photo, is_en=True, format_unconfirmed=fmt) == "小滨鹬（待确定 30%）"


def test_candidate_below_minimum_is_hidden():
    """低于 30% 的候选不显示 / Candidates below 30% are hidden."""
    photo = {"alt_species_cn": "纹胸鵙鹟", "alt_confidence": 29.9}
    assert species_display_text(photo, is_en=False, format_unconfirmed=fmt) == ""


def test_empty_when_no_species_and_no_candidate():
    """都没有返回空串 / Nothing to show yields an empty string."""
    assert species_display_text({}, is_en=False, format_unconfirmed=fmt) == ""
    assert species_display_text({"alt_species_cn": "小滨鹬"}, is_en=False, format_unconfirmed=fmt) == ""


def test_report_db_persists_candidate_columns():
    """report.db 新增候选列可写可读，旧库打开时自动补齐 / Candidate columns round-trip."""
    from tools.report_db import ReportDB, PHOTO_COLUMNS

    names = {c[0] for c in PHOTO_COLUMNS}
    assert {"alt_species_cn", "alt_species_en", "alt_confidence"} <= names
    with tempfile.TemporaryDirectory() as tmp:
        db = ReportDB(tmp)
        try:
            db.insert_photo({"filename": "IMG_1"})
            db.update_photo("IMG_1", {"alt_species_cn": "小滨鹬", "alt_species_en": "Little Stint",
                                      "alt_confidence": 44.6})
            row = db.get_photo("IMG_1")
            assert (row["alt_species_cn"], row["alt_species_en"], row["alt_confidence"]) == (
                "小滨鹬", "Little Stint", 44.6)
        finally:
            db.close()
