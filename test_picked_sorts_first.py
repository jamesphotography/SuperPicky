#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精选(picked)置顶与「只看精选」开关语义的回归测试。

产品结论 / Product decisions pinned here
----------------------------------------
1. **精选恒置顶**：picked 是「3★ 中锐度 top% ∩ 美学 top%」的交集，比任一单项
   指标都强；但按单项排序时它必然被「该项很高、另一项不够」的照片挤散（实测某
   真实批次的 12 张精选，按锐度排序落在第 2/4/8/…/44 名，按罕见度更散到第 120
   名），精选这个结论因此被稀释。故所有**质量类**排序都以 picked 为首要键。
2. **文件名排序不置顶**：它的唯一用途是还原拍摄顺序，插队会毁掉该语义。
3. **「只看精选」是收窄而非并集**：它落成 ``WHERE picked = 1``，与星级条件是
   AND。因此它不能混在并集筹码那一排里（会被误读成「再加进来一批」，实际是把
   结果塌成十几张），也必须在「重置筛选」和「无结果回退」时一并解除。

Pins: picked sorts first for quality-based orders, filename order stays
chronological, and the picked-only switch stays an AND narrowing that must be
cleared on reset and on the empty-result fallback.
"""

import os
import tempfile

from tools.report_db import ReportDB

# (filename, rating, adj_sharpness, adj_topiq, gbif_rarity_100, picked)
# 关键构造：精选那张锐度/美学/罕见度都**不是**最高的，这样"置顶"才有可观测效果；
# 若它本来就最高，测试会假绿。
# The picked row is deliberately NOT the best on any axis, so "sorts first" is
# actually observable rather than trivially true.
_ROWS = [
    ("a_best_sharp",   3, 900.0, 5.0, 10.0, 0),
    ("b_best_aes",     3, 500.0, 9.0, 20.0, 0),
    ("c_best_rare",    3, 400.0, 4.0, 99.0, 0),
    ("d_picked",       3, 700.0, 7.0, 50.0, 1),
    ("e_picked_lower", 3, 600.0, 6.0, 30.0, 1),
    ("f_plain",        2, 300.0, 3.0, 15.0, 0),
]


def _make_db(directory: str) -> ReportDB:
    """建库并写入 _ROWS。"""
    db = ReportDB(directory)
    for name, rating, sharp, topiq, rarity, picked in _ROWS:
        db.insert_photo({
            "filename": name,
            "rating": rating,
            "adj_sharpness": sharp,
            "adj_topiq": topiq,
            # 排序按 ISO 折算后的头部锐度、原始美学分（与详情面板/题注同口径）
            "head_sharp": sharp,
            "nima_score": topiq,
            "gbif_rarity_100": rarity,
            "picked": picked,
            "focus_status": "BEST",
            "is_flying": 0,
        })
    return db


def _names(rows) -> list:
    return [r["filename"] for r in rows]


def test_picked_sorts_first_for_quality_orders():
    """锐度 / 美学 / 罕见度排序下，精选必须占据最前面的位置。"""
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(d)
        for sort_by in ("sharpness_desc", "aesthetic_desc", "rarity_desc"):
            rows = db.get_photos_by_filters({
                "ratings": [2, 3, 4, 5], "sort_by": sort_by,
            })
            names = _names(rows)
            assert names[:2] == ["d_picked", "e_picked_lower"], (
                f"{sort_by}: 精选未置顶 -> {names}"
            )


def test_picked_group_still_honours_the_chosen_axis():
    """置顶只是分组，组内与组外都仍按用户选择的维度排序。"""
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(d)
        rows = db.get_photos_by_filters({
            "ratings": [2, 3, 4, 5], "sort_by": "sharpness_desc",
        })
        names = _names(rows)
        # 组内按锐度递减(700 > 600)，其后的非精选也按锐度递减(900 > 500 > 400 > 300)
        assert names == [
            "d_picked", "e_picked_lower",
            "a_best_sharp", "b_best_aes", "c_best_rare", "f_plain",
        ], names


def test_filename_order_stays_chronological():
    """按文件名排序不置顶——它的用途是还原拍摄顺序。"""
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(d)
        rows = db.get_photos_by_filters({
            "ratings": [2, 3, 4, 5], "sort_by": "filename",
        })
        assert _names(rows) == sorted(_names(rows)), _names(rows)


def test_picked_only_still_narrows():
    """「只看精选」仍是 AND 收窄，不能因为置顶改动而失效。"""
    with tempfile.TemporaryDirectory() as d:
        db = _make_db(d)
        rows = db.get_photos_by_filters({
            "ratings": [2, 3, 4, 5], "sort_by": "sharpness_desc",
            "picked_only": True,
        })
        assert _names(rows) == ["d_picked", "e_picked_lower"], _names(rows)


def test_legacy_db_without_picked_values_still_sorts():
    """
    旧目录 picked 列全为 0（或补列后为 NULL）时排序不得报错，且退化为纯维度排序。

    A legacy directory where nothing is picked must still sort cleanly.
    """
    with tempfile.TemporaryDirectory() as d:
        db = ReportDB(d)
        for name, sharp in (("x", 100.0), ("y", 300.0), ("z", 200.0)):
            db.insert_photo({
                "filename": name, "rating": 3, "adj_sharpness": sharp, "head_sharp": sharp,
                "focus_status": "BEST", "is_flying": 0,
            })
        rows = db.get_photos_by_filters({
            "ratings": [3], "sort_by": "sharpness_desc",
        })
        assert _names(rows) == ["y", "z", "x"], _names(rows)


# ==========================================================================
#  筛选面板侧：「只看精选」的语义与联动
#  Filter panel side: semantics and wiring of the picked-only switch
# ==========================================================================

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _panel():
    """构造筛选面板（离屏）。"""
    from PySide6.QtWidgets import QApplication
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    QApplication.instance() or QApplication([])
    return FilterPanel(get_i18n())


def test_rating_chips_are_pure_union():
    """
    评分筹码那一排必须只剩并集分支——「精选」不能再混在里面。

    混排是原来的病根：4 个筹码是并集、1 个是 AND 收窄，外观却一模一样，勾上
    「精选」不是多包含一批而是把结果塌成十几张。
    """
    from ui.filter_panel import _RATING_OPTIONS

    modes = {mode for mode, _, _ in _RATING_OPTIONS}
    assert "picked" not in modes, f"精选不应在并集筹码里: {modes}"
    assert modes == {"3", "2", "1", "nobird"}, modes


def test_picked_only_switch_drives_the_filter():
    """「只看精选」开关直接决定 picked_only，且默认关闭。"""
    panel = _panel()
    assert panel.get_filters()["picked_only"] is False

    panel._picked_only_cb.setChecked(True)
    assert panel.get_filters()["picked_only"] is True


def test_reset_clears_picked_only():
    """重置筛选必须解除收窄，否则「重置」后结果反而更少。"""
    panel = _panel()
    panel._picked_only_cb.setChecked(True)
    panel.reset_all()
    assert panel.get_filters()["picked_only"] is False


def test_empty_result_fallback_clears_picked_only():
    """
    无结果回退必须解除收窄。

    否则 picked_only 作为 AND 条件会继续把结果卡成空集——放宽星级根本救不
    回来（旧目录 picked 全为 0 时尤其明显）。
    """
    panel = _panel()
    panel._picked_only_cb.setChecked(True)
    panel.select_all_ratings()
    assert panel.get_filters()["picked_only"] is False
