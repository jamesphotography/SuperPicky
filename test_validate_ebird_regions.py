# -*- coding: utf-8 -*-
"""
验收脚本的 report.db 比对逻辑单测 / Tests for the report.db comparison.
"""
import sqlite3

from scripts_dev.validate_ebird_regions import compare_report_dbs


def make_report(path, rows):
    """构造最小 report.db / Build a minimal report.db."""
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE photos (bird_species_cn TEXT, birdid_confidence REAL, gps_latitude REAL)")
    db.executemany("INSERT INTO photos VALUES (?,?,?)", rows)
    db.commit()
    db.close()


def test_compare_passes_when_errors_gone_and_rate_kept(tmp_path):
    """跨半球错误清零且识别数不降 → 通过 / Pass when errors vanish and naming holds."""
    make_report(tmp_path / "old.db", [("北极海鹦", 90, 63.4), ("角海鹦", 80, 63.4), ("", 0, None)])
    make_report(tmp_path / "new.db", [("北极海鹦", 90, 63.4), ("北极海鹦", 85, 63.4), ("", 0, None)])
    results = compare_report_dbs(str(tmp_path / "old.db"), str(tmp_path / "new.db"))
    assert all(ok for _, ok, _ in results), results


def test_compare_fails_on_residual_error_species(tmp_path):
    """仍有跨半球错误 → 失败 / Fail when an error species remains."""
    make_report(tmp_path / "old.db", [("北极海鹦", 90, 63.4)])
    make_report(tmp_path / "new.db", [("小企鹅", 60, 63.4)])
    results = dict((name, ok) for name, ok, _ in compare_report_dbs(str(tmp_path / "old.db"), str(tmp_path / "new.db")))
    assert results["cross_hemisphere_errors"] is False


def test_compare_fails_when_naming_drops(tmp_path):
    """识别数下降 → 失败 / Fail when fewer photos get a species."""
    make_report(tmp_path / "old.db", [("北极海鹦", 90, 63.4), ("北极海鹦", 90, 63.4)])
    make_report(tmp_path / "new.db", [("北极海鹦", 90, 63.4), ("", 0, 63.4)])
    results = dict((name, ok) for name, ok, _ in compare_report_dbs(str(tmp_path / "old.db"), str(tmp_path / "new.db")))
    assert results["named_not_lower"] is False


def test_compare_fails_when_baseline_missing_and_creates_no_file(tmp_path):
    """baseline 路径不存在 → 失败且绝不创建文件 / Missing baseline fails and creates no file."""
    make_report(tmp_path / "new.db", [("北极海鹦", 90, 63.4)])
    missing = tmp_path / "missing.db"
    results = dict(
        (name, ok) for name, ok, _ in compare_report_dbs(str(missing), str(tmp_path / "new.db"))
    )
    assert results["report_db_readable"] is False
    assert not missing.exists()


def test_compare_fails_when_photos_table_missing(tmp_path):
    """report.db 缺 photos 表 → 失败且不抛异常 / DB without a photos table fails, no exception."""
    bad_db = tmp_path / "bad.db"
    conn = sqlite3.connect(str(bad_db))
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()
    make_report(tmp_path / "new.db", [("北极海鹦", 90, 63.4)])
    results = dict(
        (name, ok) for name, ok, _ in compare_report_dbs(str(bad_db), str(tmp_path / "new.db"))
    )
    assert results["report_db_readable"] is False
