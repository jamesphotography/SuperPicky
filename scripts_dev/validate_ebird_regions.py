# -*- coding: utf-8 -*-
"""
eBird 区域过滤验收脚本（spec §8）/ Acceptance checks for the eBird region filter.

用法 / Usage:
    python3 -m scripts_dev.validate_ebird_regions
    python3 -m scripts_dev.validate_ebird_regions --baseline-db OLD/report.db --report-db NEW/report.db

输出只用 ASCII（避免 cp1252 控制台编码失败）。
Output is ASCII-only to avoid console encoding failures.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CROSS_HEMISPHERE_ERRORS: Tuple[str, ...] = ("小企鹅", "蓝脚鲣鸟", "角海鹦", "弗氏燕鸥")

Check = Tuple[str, bool, str]


def _ascii_error(exc: BaseException) -> str:
    """
    把异常信息转成纯 ASCII，避免控制台编码失败 / Render an exception message as pure ASCII.

    参数 / Parameters:
        exc (BaseException): 原始异常 / The original exception.

    返回 / Returns:
        str: 非 ASCII 字符被转义后的说明 / Message with non-ASCII escaped.
    """
    return str(exc).encode("ascii", "backslashreplace").decode("ascii")


def _open_report_db_readonly(path: str) -> sqlite3.Connection:
    """
    以只读方式打开 report.db，绝不创建或修改文件 / Open report.db read-only, never creating or modifying it.

    参数 / Parameters:
        path (str): report.db 路径 / Path to report.db.

    返回 / Returns:
        sqlite3.Connection: 只读连接 / Read-only connection.

    异常 / Raises:
        sqlite3.Error: 路径不存在或不是合法 SQLite 库时抛出 / Raised when the path is missing
            or is not a valid SQLite database.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("SELECT 1 FROM photos LIMIT 1")
    return conn


def compare_report_dbs(baseline_db: str, new_db: str) -> List[Check]:
    """
    逐项比对两次处理的 report.db（spec §8 验收 5）/ Compare two report.db runs.

    两个库均以只读 URI 方式打开，绝不创建或修改用户真实照片库的 report.db；
    路径不存在或缺 photos 表时返回单条失败检查而不抛异常。
    Both databases are opened read-only via a URI so the user's real report.db
    is never created or modified; a missing path or a missing ``photos`` table
    yields a single failing check instead of raising.

    参数 / Parameters:
        baseline_db (str): 4.6 结果 / The 4.6 run.
        new_db (str): 本次结果 / The new run.

    返回 / Returns:
        list[tuple]: (检查名, 是否通过, 说明) / (name, passed, detail).
    """
    def named(db: sqlite3.Connection) -> Tuple[int, int]:
        total = db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        count = db.execute(
            "SELECT COUNT(*) FROM photos WHERE bird_species_cn IS NOT NULL AND bird_species_cn != ''"
        ).fetchone()[0]
        return int(total), int(count)

    old: Optional[sqlite3.Connection] = None
    new: Optional[sqlite3.Connection] = None
    try:
        try:
            old = _open_report_db_readonly(baseline_db)
        except sqlite3.Error as exc:
            return [("report_db_readable", False, f"baseline: {_ascii_error(exc)}")]
        try:
            new = _open_report_db_readonly(new_db)
        except sqlite3.Error as exc:
            return [("report_db_readable", False, f"new: {_ascii_error(exc)}")]

        placeholders = ",".join("?" * len(CROSS_HEMISPHERE_ERRORS))
        residual = new.execute(
            f"SELECT COUNT(*) FROM photos WHERE bird_species_cn IN ({placeholders})", CROSS_HEMISPHERE_ERRORS
        ).fetchone()[0]
        old_total, old_named = named(old)
        new_total, new_named = named(new)
        gps = new.execute("SELECT COUNT(*) FROM photos WHERE gps_latitude IS NOT NULL").fetchone()[0]
    finally:
        if old is not None:
            old.close()
        if new is not None:
            new.close()
    return [
        ("cross_hemisphere_errors", residual == 0, f"residual={residual}"),
        ("named_not_lower", new_named >= old_named,
         f"{old_named}/{old_total} -> {new_named}/{new_total}"),
        ("gps_coverage_info", True, f"{gps}/{new_total} photos with GPS"),
    ]


def _check_database() -> List[Check]:
    """
    检查真实库：哨兵、体积、GBIF 库已删 / Check sentinels, size, and GBIF DB removal.

    返回 / Returns:
        list[tuple]: 检查结果 / Check results.
    """
    from birdid.geo_filter import default_db_path
    from scripts_dev.build_ebird_regions import MAX_DB_BYTES, REF_DB, SENTINELS, check_sentinels
    from scripts_dev.ebird_region_mapping import load_model_index

    path = default_db_path()
    if not os.path.exists(path):
        return [("database_exists", False, path)]
    conn = sqlite3.connect(path)
    try:
        region_species: dict = {}
        for region, cls in conn.execute("SELECT region, class_id FROM region_species"):
            region_species.setdefault(region, set()).add(int(cls))
    finally:
        conn.close()
    errors = check_sentinels(region_species, load_model_index(REF_DB).by_sci, SENTINELS)
    size = os.path.getsize(path)
    legacy = os.path.join(PROJECT_ROOT, "birdid", "data", "geo_distribution.db")
    return [
        ("sentinels", not errors, "; ".join(errors) or f"{len(SENTINELS)} present"),
        ("db_size", size <= MAX_DB_BYTES, f"{size / 1048576:.2f} MB"),
        ("gbif_db_removed", not os.path.exists(legacy), legacy),
    ]


def _check_performance() -> List[Check]:
    """
    定位性能（spec §8 验收 8）/ Locator performance.

    返回 / Returns:
        list[tuple]: 检查结果 / Check results.
    """
    from birdid.geo_filter import default_db_path
    from birdid.region_locator import RegionLocator

    # 计时前的两个 import 会触发 birdid/__init__.py 的 torch/ultralytics 预加载
    # （约 900ms，与本检查无关，属既有行为）；因此只计时构造 + 首次 locate。
    # The two imports above trigger birdid/__init__.py's eager torch/ultralytics
    # preload (~900ms, pre-existing and unrelated to this check); we therefore
    # time only the RegionLocator construction plus the first locate() call.
    start = time.perf_counter()
    locator = RegionLocator(default_db_path())
    locator.locate(39.9, 116.4)
    cold_ms = (time.perf_counter() - start) * 1000
    points = [(-60 + (i * 7.3) % 130, -170 + (i * 13.7) % 340) for i in range(300)]
    uncached = []
    for lat, lon in points:
        s = time.perf_counter()
        locator.locate(lat, lon)
        uncached.append((time.perf_counter() - s) * 1000)
    cached = []
    for lat, lon in points:
        s = time.perf_counter()
        locator.locate(lat, lon)
        cached.append((time.perf_counter() - s) * 1000)
    median = statistics.median(uncached)
    return [
        ("locate_cold", cold_ms <= 500, f"{cold_ms:.1f} ms"),
        ("locate_uncached_median", median <= 20, f"{median:.2f} ms"),
        ("locate_cached_max", max(cached) <= 1, f"{max(cached):.3f} ms"),
    ]


def _check_legacy_refs() -> List[Check]:
    """
    代码中无网格实现残留（spec §8 验收 9）/ No grid-era code references.

    返回 / Returns:
        list[tuple]: 检查结果 / Check results.
    """
    pattern = r"geo_distribution\.db|land_cells|cell_id_for|TIER_CELL|TIER_NEIGHBORHOOD|import reverse_geocoder"
    out = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", "*.py", "*.spec",
         ":!scripts_dev/validate_ebird_regions.py", ":!test_*.py"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    return [("legacy_refs", not out, out[:500] or "none")]


def main(argv: Optional[List[str]] = None) -> int:
    """
    运行全部验收检查 / Run all acceptance checks.

    参数 / Parameters:
        argv (Optional[list[str]]): 命令行参数 / CLI arguments.

    返回 / Returns:
        int: 全部通过 0，否则 1 / 0 when all pass, otherwise 1.
    """
    parser = argparse.ArgumentParser(description="Validate the eBird region filter (spec section 8)")
    parser.add_argument("--baseline-db")
    parser.add_argument("--report-db")
    args = parser.parse_args(argv)

    checks = _check_database() + _check_performance() + _check_legacy_refs()
    if args.baseline_db and args.report_db:
        checks += compare_report_dbs(args.baseline_db, args.report_db)
    failed = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += 0 if ok else 1
    print(f"{len(checks) - failed}/{len(checks)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
