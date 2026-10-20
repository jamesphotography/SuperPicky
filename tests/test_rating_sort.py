# -*- coding: utf-8 -*-
"""
结果浏览器「星级」排序回归测试（#137）。

星级高→低；同一星级内精选在前，其余按头部锐度高→低（与锐度排序同口径）。
手动升的 4★/5★ 因此排在最前，用来替代在评分筹码里加「4★+」（宽度放不下）。

Regression tests for the browser's rating sort (#137).
"""
from tools.report_db import ReportDB


def _row(name: str, rating: int, head_sharp, picked: int = 0) -> dict:
    """构造一条最小照片记录 / Build a minimal photo row."""
    return {
        "filename": name, "current_path": name + ".CR3", "original_path": name + ".CR3",
        "rating": rating, "has_bird": 1 if rating >= 0 else 0,
        "head_sharp": head_sharp, "picked": picked,
    }


def test_rating_desc_orders_by_rating_then_picked_then_sharpness(tmp_path):
    """5★ → 4★ → 3★(精选在前，再按锐度) → 2★ → 无鸟；锐度缺失的排本档最后。"""
    db = ReportDB(str(tmp_path))
    try:
        for row in (
            _row("a_3_soft", 3, 100.0),
            _row("b_4", 4, 50.0),
            _row("c_3_sharp", 3, 300.0),
            _row("d_nobird", -1, None),
            _row("e_5", 5, 10.0),
            _row("f_3_picked", 3, 80.0, picked=1),
            _row("g_2", 2, 999.0),
            _row("h_3_nosharp", 3, None),
        ):
            db.insert_photo(row)

        got = [p["filename"] for p in db.get_photos_by_filters({"sort_by": "rating_desc"})]
        assert got == [
            "e_5", "b_4",
            "f_3_picked", "c_3_sharp", "a_3_soft", "h_3_nosharp",
            "g_2", "d_nobird",
        ]
    finally:
        db.close()


def test_rating_filter_still_combines_with_rating_sort(tmp_path):
    """3★ 筹码仍包含 4★/5★；配合星级排序，手动升星的排在 3★ 前面。"""
    db = ReportDB(str(tmp_path))
    try:
        for row in (_row("x_3", 3, 500.0), _row("y_5", 5, 1.0), _row("z_2", 2, 900.0)):
            db.insert_photo(row)
        got = [p["filename"] for p in db.get_photos_by_filters(
            {"ratings": [3, 4, 5], "sort_by": "rating_desc"})]
        assert got == ["y_5", "x_3"]
    finally:
        db.close()
