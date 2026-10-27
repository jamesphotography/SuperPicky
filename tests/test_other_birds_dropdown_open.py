# -*- coding: utf-8 -*-
"""
#148 回归测试：打开结果目录时，「其他鸟类」兜底项必须按当前星级筛选决定去留。

打开目录时 reset_all 已按默认星级（2★/3★）刷新过鸟种下拉，随后又按「不限星级」
重算了一遍，把只在 0★ / 无鸟照片里才有的兜底项加了回来——选中后查出来是空的，
下拉随即退回「全部」、兜底项消失。另外选项改用易读文案，不再显示 Other_Birds。

#148: on open, the "Other birds" item must follow the active rating filter;
an unrestricted recompute used to re-add an item that is empty under it.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _open_browser(root: str, rows: list):
    """
    用给定照片记录建库并打开真实的结果浏览器。

    Build a report DB from rows and open a real results browser on it.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    for row in rows:
        open(os.path.join(root, row["filename"] + ".CR3"), "w").close()
        db.insert_photo({"current_path": row["filename"] + ".CR3",
                         "original_path": row["filename"] + ".CR3", **row})
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    return win


def _bird(name: str, rating: int, cn: str = "", en: str = "") -> dict:
    """
    一条照片记录；有星的照片像真实处理结果一样带对焦状态与飞行标记，否则会被
    默认对焦筛选滤掉，浏览器随即自动「全选星级」，测试前提就不成立了。

    A photo row; rated photos carry focus/flight like real output, or the
    default focus filter drops them and the browser falls back to all ratings.
    """
    row = {"filename": name, "rating": rating, "has_bird": 1 if rating >= 0 else 0,
           "bird_species_cn": cn, "bird_species_en": en}
    if rating > 0:
        row.update({"focus_status": "BEST", "is_flying": 0})
    return row


def test_other_item_hidden_when_empty_under_default_ratings(tmp_path):
    """
    2★/3★ 全部识别出鸟种，只有一张无鸟照片 → 默认筛选下不该出现兜底项。

    Every 2-3 star photo is identified; only a no-bird photo is "other" →
    no catch-all item under the default 2-3 star filter.
    """
    from tools.report_db import SPECIES_FILTER_OTHER
    win = _open_browser(str(tmp_path), [
        _bird("IMG_1", 3, "白鹭", "Little Egret"),
        _bird("IMG_2", 2, "白鹭", "Little Egret"),
        _bird("IMG_3", -1),
    ])
    try:
        assert win._filter_panel.species_combo.findData(SPECIES_FILTER_OTHER) == -1
    finally:
        win.close()


def test_other_item_shown_and_selectable_when_it_has_photos(tmp_path):
    """
    默认星级里有未识别的照片 → 兜底项出现、文案易读，选中后能查出这张照片。

    An unidentified 2-star photo → the item shows with a readable label, and
    selecting it returns that photo.
    """
    from tools.i18n import get_i18n
    from tools.report_db import SPECIES_FILTER_OTHER
    win = _open_browser(str(tmp_path), [
        _bird("IMG_1", 3, "白鹭", "Little Egret"),
        _bird("IMG_2", 2),
    ])
    try:
        combo = win._filter_panel.species_combo
        idx = combo.findData(SPECIES_FILTER_OTHER)
        assert idx >= 0
        assert combo.itemText(idx) == get_i18n().t("browser.species_other")
        assert "_" not in combo.itemText(idx)
        combo.setCurrentIndex(idx)
        assert combo.currentData() == SPECIES_FILTER_OTHER
        assert [p["filename"] for p in win._raw_filtered_photos] == ["IMG_2"]
    finally:
        win.close()
