#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_db 颜值列迁移 + 排序单测。
report_db aesthetic-column migration + sort tests.

注意 / Note: brief 中给出的 ReportDB 构造/方法名（`ReportDB(db_path)` /
`upsert_photo` / `query_photos`）为常见命名假设，实际 API 不同：
- 构造函数接收「目录」而非 db 文件路径（内部拼 `<dir>/.superpicky/report.db`）。
- 写入方法是 `insert_photo`（不是 `upsert_photo`）。
- 查询方法是 `get_photos_by_filters`（不是 `query_photos`）。
三个断言意图保持不变：(a) aesthetic_index 列存在；(b) 迁移幂等；
(c) species_beauty_desc 排序高分在前、NULL 末位。
The brief's `ReportDB(db_path)` / `upsert_photo` / `query_photos` were
naming guesses; adjusted to the real API confirmed by reading
tools/report_db.py, while keeping the three assertion intents unchanged.
"""
from tools.report_db import ReportDB


def test_aesthetic_column_exists(tmp_path):
    db = ReportDB(str(tmp_path))
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(photos)")]
    assert "aesthetic_index" in cols
    db.close()


def test_migration_idempotent(tmp_path):
    """同目录二次打开（触发迁移路径）不报错、列仍只有一个。"""
    d = str(tmp_path)
    ReportDB(d).close()
    db = ReportDB(d)  # 二次打开走已迁移分支
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(photos)")]
    assert cols.count("aesthetic_index") == 1
    db.close()


def test_species_beauty_sort_nulls_last(tmp_path):
    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "a.jpg", "aesthetic_index": 20.0})
    db.insert_photo({"filename": "b.jpg", "aesthetic_index": 80.0})
    db.insert_photo({"filename": "c.jpg", "aesthetic_index": None})
    rows = db.get_photos_by_filters({"sort_by": "species_beauty_desc"})
    names = [r["filename"] for r in rows]
    assert names == ["b.jpg", "a.jpg", "c.jpg"]  # 高分在前, NULL 末位
    db.close()
