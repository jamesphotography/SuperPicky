# -*- coding: utf-8 -*-
"""
识鸟对比模式的数据层与浏览器筛选单测 / Data-layer and browser-filter tests for Bird-ID compare mode.
"""
import os
import sqlite3
import threading

import pytest

from tools.report_db import ReportDB


def test_disagree_only_filter_follows_current_species(tmp_path):
    """「只看分歧」：验证结果与当前鸟种（或待确定候选）不同才入选，改鸟种后即时更新。"""
    db = ReportDB(str(tmp_path))
    try:
        rows = [
            ("a", {"bird_species_cn": "冠翎鹑鸠", "verify_species_cn": "冠翎鹑鸠"}),          # 一致
            ("b", {"bird_species_cn": "巴布亚蛙口夜鹰", "verify_species_cn": "茶色蛙口夜鹰"}),  # 分歧
            ("c", {"alt_species_cn": "哈氏弯嘴鹛", "verify_species_cn": "灰冠弯嘴鹛"}),       # 候选分歧
            ("d", {"bird_species_cn": "冠翎鹑鸠"}),                                            # 没开对比
        ]
        for name, data in rows:
            db.insert_photo({"filename": name, "rating": 3})
            db.update_photo(name, data)
        pick = lambda: sorted(r["filename"] for r in db.get_photos_by_filters({"disagree_only": True}))
        assert pick() == ["b", "c"]
        db.update_photo("b", {"bird_species_cn": "茶色蛙口夜鹰"})   # 用户在浏览器里改成验证模型的答案
        assert pick() == ["c"]
    finally:
        db.close()


def test_legacy_db_gets_verify_columns(tmp_path):
    """老版本建的 report.db 打开时自动补上 verify_* 列 / Legacy DBs gain the verify columns."""
    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "x", "rating": 2})
    db.close()
    path = os.path.join(str(tmp_path), ".superpicky", "report.db")
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(photos)")]
    con.close()
    assert {"verify_model", "verify_species_cn", "verify_species_en", "verify_confidence"} <= set(cols)


def test_concurrent_verify_writes_and_reads(tmp_path):
    """多线程同时写 verify_* 与读筛选，不出现事务状态错误（CLAUDE.md DB/线程验证要求）。"""
    db = ReportDB(str(tmp_path))
    try:
        names = [f"p{i:03d}" for i in range(120)]
        for n in names:
            db.insert_photo({"filename": n, "rating": 2, "bird_species_cn": "褐鹰"})
        errors = []

        def writer(chunk):
            try:
                for n in chunk:
                    db.update_photo(n, {"verify_model": "OSEA", "verify_species_cn": "苍鹰",
                                        "verify_species_en": "Northern Goshawk", "verify_confidence": 41.5})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader():
            try:
                for _ in range(40):
                    db.get_photos_by_filters({"disagree_only": True})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(names[i::4],)) for i in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(db.get_photos_by_filters({"disagree_only": True})) == len(names)
    finally:
        db.close()


def test_filter_panel_disagree_row_follows_data():
    """「只看分歧」默认隐藏；有验证数据才显示，没有时隐藏并取消勾选。"""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(get_i18n())
    assert panel._disagree_row.isHidden()
    panel.set_disagree_available(True)
    assert not panel._disagree_row.isHidden()
    panel._disagree_only_cb.setChecked(True)
    panel.set_disagree_available(False)
    assert panel._disagree_row.isHidden() and not panel._disagree_only_cb.isChecked()
    panel.deleteLater()
