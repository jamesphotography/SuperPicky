# -*- coding: utf-8 -*-
"""
合并多目录时的 schema 漂移测试。

现网问题（2026-09-08 实测）：把一个父目录下 25 个已处理目录合并浏览时直接抛
``sqlite3.OperationalError: SELECTs to the left and right of UNION ALL do not
have the same number of result columns``。

原因是各目录的 report.db 列数不一致。ReportDB 的 _reconcile_photo_columns 会
**补齐缺失**的列，但对**多出来**的列无能为力——某个旧版本引入又废弃的字段
（analysis_state / metadata_state / session_id / last_error）仍留在老库里，而
MergedReportDB 用 ``SELECT *`` 做 UNION ALL，一个库多四列就全盘失败。

用户的真实数据里 21 个目录 51 列、1 个目录 55 列，就因为那一个目录，整个
合并功能不可用，且报错完全无法自解释。

修法：按 PHOTO_COLUMNS 显式列出要取的列——缺的补 NULL、多的忽略。这样跨多少
个 schema 版本都能合，也不必要求用户重新处理老目录；将来再废弃字段同样不会
重现这个崩溃。

Schema drift across per-directory databases broke merged browsing: extra
columns left over from an older version made SELECT * mismatch in UNION ALL.
Select PHOTO_COLUMNS explicitly instead.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def _make_db(root: str, *, extra_cols=(), drop_cols=(), rows=()):
    """
    在 root 下造一个 report.db，可人为增删列以模拟 schema 漂移。

    参数 / Args:
        root:       目录（会创建 .superpicky/report.db）
        extra_cols: 额外多出的列名（模拟旧版本遗留字段）
        drop_cols:  要去掉的列名（模拟更老的库还没有这些列）
        rows:       [(filename, bird_species_cn, rating)] 要写入的记录
    """
    from tools.report_db import PHOTO_COLUMNS

    os.makedirs(os.path.join(root, ".superpicky"), exist_ok=True)
    path = os.path.join(root, ".superpicky", "report.db")

    cols = [(n, t) for n, t, _ in PHOTO_COLUMNS if n not in drop_cols]
    defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
    defs += [f"{n} {t}" for n, t in cols]
    defs += [f"{n} TEXT" for n in extra_cols]

    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE photos ({', '.join(defs)})")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    for filename, species, rating in rows:
        con.execute(
            "INSERT INTO photos (filename, bird_species_cn, rating) VALUES (?,?,?)",
            (filename, species, rating),
        )
    con.commit()
    con.close()
    return root


def test_merges_directories_whose_schemas_differ(tmp_path):
    """
    各目录列数不同也要能合并 —— 这正是现网 25 个目录合并失败的场景。
    """
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    a = _make_db(os.path.join(root, "2026-09-01"),
                 rows=[("A1", "家燕", 3)])
    # 旧版本遗留的四个字段，现行 PHOTO_COLUMNS 里已经没有
    b = _make_db(os.path.join(root, "2026-03-18"),
                 extra_cols=("analysis_state", "metadata_state",
                             "session_id", "last_error"),
                 rows=[("B1", "大山雀", 3)])

    db = MergedReportDB(root, [a, b])
    try:
        photos = db.get_all_photos()
    finally:
        db.close()

    assert len(photos) == 2, f"两个目录各一张，实际 {len(photos)}"
    assert {p["bird_species_cn"] for p in photos} == {"家燕", "大山雀"}


def test_missing_columns_come_back_as_none(tmp_path):
    """
    更老的库还没有某些列时，合并结果里该列为 None，而不是报错或错位。
    """
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    a = _make_db(os.path.join(root, "new"), rows=[("A1", "家燕", 3)])
    b = _make_db(os.path.join(root, "old"),
                 drop_cols=("gbif_rarity_100", "iucn_category"),
                 rows=[("B1", "大山雀", 3)])

    db = MergedReportDB(root, [a, b])
    try:
        photos = {p["filename"]: p for p in db.get_all_photos()}
    finally:
        db.close()

    assert photos["B1"]["gbif_rarity_100"] is None
    assert photos["B1"]["iucn_category"] is None
    assert "gbif_rarity_100" in photos["A1"], "新库的列不能丢"


def test_extra_columns_are_not_exposed(tmp_path):
    """
    多出来的旧字段不进入合并结果 —— 结果的字段集必须稳定，
    否则下游按列名取值的代码会随目录组合而变。
    """
    from tools.merged_report_db import MergedReportDB
    from tools.report_db import PHOTO_COLUMNS

    root = str(tmp_path)
    a = _make_db(os.path.join(root, "d1"),
                 extra_cols=("session_id", "last_error"),
                 rows=[("A1", "家燕", 3)])

    db = MergedReportDB(root, [a])
    try:
        photo = db.get_all_photos()[0]
    finally:
        db.close()

    assert "session_id" not in photo
    assert "last_error" not in photo
    expected = {c[0] for c in PHOTO_COLUMNS} | {"source_dir"}
    assert set(photo) == expected, f"字段集应固定，多出/缺少: {set(photo) ^ expected}"


def test_filtering_still_works_across_drifted_schemas(tmp_path):
    """筛选查询（走同一套 UNION SQL）在混合 schema 下同样要正常。"""
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    a = _make_db(os.path.join(root, "d1"), rows=[("A1", "家燕", 3), ("A2", "家燕", 1)])
    b = _make_db(os.path.join(root, "d2"), extra_cols=("legacy_flag",),
                 rows=[("B1", "大山雀", 3)])

    db = MergedReportDB(root, [a, b])
    try:
        hits = db.get_photos_by_filters({"ratings": [3]})
    finally:
        db.close()

    assert {p["filename"] for p in hits} == {"A1", "B1"}


def test_species_list_works_across_drifted_schemas(tmp_path):
    """鸟种下拉的跨库查询同样不能被 schema 漂移打断。"""
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    a = _make_db(os.path.join(root, "d1"), rows=[("A1", "家燕", 3)])
    b = _make_db(os.path.join(root, "d2"), extra_cols=("legacy_flag",),
                 rows=[("B1", "大山雀", 3)])

    db = MergedReportDB(root, [a, b])
    try:
        species = db.get_distinct_species(use_en=False)
    finally:
        db.close()

    assert set(species) >= {"家燕", "大山雀"}


def test_merges_more_than_ten_directories(tmp_path):
    """
    超过 10 个目录也要全部合并 —— 这是本功能的核心场景。

    SQLite 编译期上限 MAX_ATTACHED=10，原实现用 ATTACH 把各库挂在一起，
    第 11 个起必然失败，而失败被 ``except Exception: pass`` 静默吞掉：
    用户看到的照片数与鸟种数只含前 10 个目录，却没有任何提示——比直接报错
    更危险，因为统计数字看起来是正常的。

    实际使用中一天可能分上下午两个目录，十天就是 20 个，10 个的上限完全不够。

    More than ten directories must all be merged; SQLite caps ATTACH at 10 and
    the old code swallowed the failures, silently dropping data.
    """
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    dirs = []
    for i in range(14):
        d = _make_db(os.path.join(root, f"2026-09-{i+1:02d}"),
                     rows=[(f"P{i}", f"鸟种{i}", 3)])
        dirs.append(d)

    db = MergedReportDB(root, dirs)
    try:
        photos = db.get_all_photos()
        species = db.get_distinct_species(use_en=False)
    finally:
        db.close()

    assert len(photos) == 14, f"14 个目录各一张，实际只有 {len(photos)} 张"
    assert len({p["source_dir"] for p in photos}) == 14, "每个目录都应有数据"
    assert len(set(species)) == 14, f"应有 14 个鸟种，实际 {len(set(species))}"
