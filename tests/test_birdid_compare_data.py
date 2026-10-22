# -*- coding: utf-8 -*-
"""
识鸟对比模式下线后的数据层与浏览器单测 / Data-layer and browser tests after Bird-ID compare mode was retired.

verify_* 列是 4.6.4 RC 对比模式留下的：列保留（免迁移），但不再写、不再读，「只看分歧」筛选已删除。
The verify_* columns are left over from the 4.6.4 RC compare mode: kept (no migration), never written
or read, and the "Disagreements only" filter is gone.
"""
import os
import sqlite3

import pytest

from tools.report_db import ReportDB


def test_legacy_db_keeps_verify_columns(tmp_path):
    """库里仍有 verify_* 列，RC 期间处理过的旧库照常打开 / The retired columns still exist."""
    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "x", "rating": 2})
    db.close()
    path = os.path.join(str(tmp_path), ".superpicky", "report.db")
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(photos)")]
    con.close()
    assert {"verify_model", "verify_species_cn", "verify_species_en", "verify_confidence"} <= set(cols)


def test_disagree_filter_key_is_ignored(tmp_path):
    """旧调用方传 disagree_only 也不再收窄结果 / A leftover disagree_only key no longer narrows."""
    db = ReportDB(str(tmp_path))
    try:
        for name in ("a", "b"):
            db.insert_photo({"filename": name, "rating": 3, "bird_species_cn": "褐鹰"})
        db.update_photo("b", {"verify_species_cn": "苍鹰"})
        names = sorted(r["filename"] for r in db.get_photos_by_filters({"disagree_only": True}))
        assert names == ["a", "b"]
    finally:
        db.close()


def test_filter_panel_has_no_disagree_switch():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(get_i18n())
    assert not hasattr(panel, "_disagree_only_cb")
    assert "disagree_only" not in panel.get_filters()
    panel.deleteLater()
