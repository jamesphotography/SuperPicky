# -*- coding: utf-8 -*-
"""
对焦 / 飞行信息缺失的照片不应被对应筛选挡掉（#159）。

0★ 照片常常没有对焦状态（NULL），用户把它手动改成 4★ 后依旧是 NULL。
此前查询只在选了 0★ 时才放行 NULL，于是这张 4★ 在取消 0★ 后从网格里消失，
且任何筛选组合下都找不回来。

Photos lacking focus / flight data must not be dropped by those filters (#159):
a 0★ shot re-rated to 4★ keeps NULL focus and used to vanish once 0★ was
deselected.
"""
from tools.report_db import ReportDB

# 结果浏览器的典型筛选：3★ 筹码（含 4★/5★）、对焦勾了「精准 + 良好」、飞行全勾。
# Typical browser filters: the 3★ chip (incl. 4★/5★), focus Best+Good, both flight states.
_FILTERS = {
    "ratings": [3, 4, 5],
    "focus_statuses": ["BEST", "GOOD"],
    "is_flying": [0, 1],
    "sort_by": "filename",
}


def _row(name: str, rating: int, focus, flying) -> dict:
    """构造一条最小照片记录 / Build a minimal photo row."""
    return {
        "filename": name, "current_path": name + ".CR3", "original_path": name + ".CR3",
        "rating": rating, "has_bird": 1, "focus_status": focus, "is_flying": flying,
    }


def test_rerated_photo_without_focus_data_stays_visible(tmp_path):
    """0★→4★ 的照片（对焦/飞行为 NULL）在只选 3★ 筹码时仍然显示。"""
    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo(_row("a_best", 3, "BEST", 0))
        db.insert_photo(_row("b_rerated", 0, None, None))
        db.update_photo("b_rerated", {"rating": 4})

        got = [p["filename"] for p in db.get_photos_by_filters(_FILTERS)]
        assert got == ["a_best", "b_rerated"]
    finally:
        db.close()


def test_focus_filter_still_excludes_known_bad_focus(tmp_path):
    """有对焦信息的照片照常按筛选判断：失焦（BAD/WORST）不在勾选范围内就不显示。"""
    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo(_row("a_good", 4, "GOOD", 1))
        db.insert_photo(_row("b_bad", 4, "BAD", 1))
        db.insert_photo(_row("c_worst", 5, "WORST", 0))
        db.insert_photo(_row("d_empty", 4, "", 0))

        got = [p["filename"] for p in db.get_photos_by_filters(_FILTERS)]
        assert got == ["a_good", "d_empty"]
    finally:
        db.close()


def test_flight_filter_still_applies_to_known_values(tmp_path):
    """只勾「飞行」时，非飞行照片照常不显示（放行 NULL 不能连带放行 0）。"""
    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo(_row("a_fly", 3, "BEST", 1))
        db.insert_photo(_row("b_perched", 3, "BEST", 0))

        got = [p["filename"] for p in db.get_photos_by_filters(
            dict(_FILTERS, is_flying=[1]))]
        assert got == ["a_fly"]
    finally:
        db.close()
