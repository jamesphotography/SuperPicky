#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归测试:旧目录的 report.db(缺 picked 列)在打开后必须被补齐,
picked_only 过滤不得抛 sqlite3.OperationalError: no such column: picked。

Regression test: a legacy report.db (missing the `picked` column) must be
reconciled on open so that picked_only filtering does not raise
"no such column: picked".
"""

import os
import sqlite3
import tempfile

from tools.report_db import ReportDB, PHOTO_COLUMNS


def _create_legacy_db_without_picked(directory: str) -> None:
    """构造一个不含 picked 列的旧版 photos 表(模拟历史目录)。"""
    superpicky_dir = os.path.join(directory, ".superpicky")
    os.makedirs(superpicky_dir, exist_ok=True)
    db_path = os.path.join(superpicky_dir, "report.db")

    # 仅保留除 picked 之外的列,模拟旧 schema(picked 引入前)
    col_defs = []
    for name, type_def, _ in PHOTO_COLUMNS:
        if name == "picked":
            continue
        col_defs.append(f"    {name} {type_def}")

    create_sql = (
        "CREATE TABLE photos (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        + ",\n".join(col_defs)
        + "\n)"
    )

    conn = sqlite3.connect(db_path)
    conn.execute(create_sql)
    conn.execute(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    # 故意标记为最新版本,模拟"版本号已到 8 但 picked 从未被迁移"的真实情况
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', '8')"
    )
    conn.execute(
        "INSERT INTO photos (filename, rating) VALUES ('IMG_0001', 3)"
    )
    conn.commit()
    conn.close()


def test_legacy_db_picked_column_reconciled():
    """旧 DB 缺 picked 列,ReportDB 打开后应补齐,picked_only 过滤不报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        _create_legacy_db_without_picked(tmp)

        db = ReportDB(tmp)
        try:
            # picked 列必须存在
            cursor = db._conn.execute("PRAGMA table_info(photos)")
            cols = {row[1] for row in cursor.fetchall()}
            assert "picked" in cols, f"picked 列未被补齐: {cols}"

            # picked_only 过滤不得抛 OperationalError
            result = db.get_photos_by_filters({"picked_only": True})
            assert result == [], "旧数据 picked 默认 0,精选结果应为空"
        finally:
            db.close()


if __name__ == "__main__":
    test_legacy_db_picked_column_reconciled()
    print("✅ PASS")
