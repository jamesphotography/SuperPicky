# -*- coding: utf-8 -*-
"""
评分筛选「全选 / 全不选」与「筹码亮着 = 显示」语义回归测试（#137）。

- 一个筹码都不亮 = 什么都不显示（以前是不限星级）；
- 「全选」点亮全部筹码，覆盖 -1~5 全部星级；
- 「全不选」不会把已选的鸟种（含「其他鸟类」兜底项）重置掉；
- 打开目录时默认 3★+2★ 无结果，兜底改为点亮全部筹码而非清空。

Regression tests for the rating "All" / "None" buttons (#137).
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _make_browser(root: str, rows: list):
    """
    在 root 建 report.db 并打开一个真实 ResultsBrowserWindow。

    参数 / Args:
        root: 目录
        rows: [(文件名, 星级, 中文鸟种)]

    返回 / Returns:
        ResultsBrowserWindow
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    for stem, rating, species in rows:
        open(os.path.join(root, stem + ".CR3"), "w").close()
        db.insert_photo({
            "filename": stem, "current_path": stem + ".CR3", "original_path": stem + ".CR3",
            "rating": rating, "has_bird": 1 if rating >= 0 else 0,
            "bird_species_cn": species, "bird_species_en": species,
            # 有鸟照片须带对焦与飞行状态，否则被默认的对焦/飞行筛选挡掉
            # Bird photos need focus/flight values or the default filters drop them
            "focus_status": "BEST" if rating >= 0 else None,
            "is_flying": 0 if rating >= 0 else None,
        })
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    deadline = time.time() + 5
    while time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)
        if not win._thumb_grid._pending_photos:
            break
    return win


@pytest.fixture
def browser(tmp_path):
    """3★ 白鹭、2★ 白鹭、1★ 麻雀（只有低星，无目录 → 归「其他鸟类」）、无鸟一张。"""
    win = _make_browser(str(tmp_path), [
        ("IMG_0", 3, "白鹭"), ("IMG_1", 2, "白鹭"), ("IMG_2", 1, "麻雀"), ("IMG_3", -1, ""),
    ])
    yield win
    win.close()


def test_none_shows_nothing_and_all_shows_everything(browser):
    """「全不选」网格为空；「全选」四张全显示。"""
    win = browser
    panel = win._filter_panel

    panel._select_no_rating()
    assert panel.get_filters()["ratings"] == []
    assert win._filtered_photos == []

    panel._select_every_rating()
    assert panel.get_filters()["ratings"] == [-1, 0, 1, 2, 3, 4, 5]
    assert len(win._filtered_photos) == 4


def test_deselecting_last_chip_shows_nothing(browser):
    """手动把默认的 3★、2★ 都点灭，同样什么都不显示。"""
    win = browser
    panel = win._filter_panel
    panel._on_rating_btn("3")
    panel._on_rating_btn("2")
    assert panel.get_filters()["ratings"] == []
    assert win._filtered_photos == []


def test_none_then_one_chip_keeps_other_birds_selection(browser):
    """选「其他鸟类」→ 全不选 → 点亮 1★：鸟种选择仍在，只剩麻雀那张。"""
    from tools.report_db import SPECIES_FILTER_OTHER
    win = browser
    panel = win._filter_panel

    panel._select_every_rating()
    idx = panel.species_combo.findData(SPECIES_FILTER_OTHER)
    assert idx >= 0
    panel.species_combo.setCurrentIndex(idx)

    panel._select_no_rating()
    assert panel.species_combo.currentData() == SPECIES_FILTER_OTHER

    panel._on_rating_btn("1")
    assert [p["filename"] for p in win._filtered_photos] == ["IMG_2"]


def test_open_falls_back_to_all_chips_lit(tmp_path):
    """只有 1★ 照片的目录：默认 3★+2★ 无结果，兜底点亮全部筹码并显示照片。"""
    win = _make_browser(str(tmp_path), [("IMG_0", 1, "麻雀")])
    try:
        panel = win._filter_panel
        assert panel._active_ratings == {"3", "2", "1", "nobird"}
        assert [p["filename"] for p in win._filtered_photos] == ["IMG_0"]
    finally:
        win.close()
