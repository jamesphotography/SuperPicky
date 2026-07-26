# -*- coding: utf-8 -*-
"""
从 GBIF Occurrence API 生成地理分布库 / Build the geo-distribution DB from the GBIF API.

对每个 1°网格调用 GBIF 的 speciesKey facet，拿到「该格每个物种的观察记录数」，
映射到 OSEA class_id 后写入 SQLite，再按国家汇总。服务端完成聚合，无需下载原始记录。

For each 1-degree cell, call the GBIF speciesKey facet to get per-species
occurrence counts, map them to OSEA class ids, write them to SQLite, and roll up
by country. The aggregation happens server-side; no raw records are downloaded.

支持断点续传：已处理的网格记录在 _build_progress 表，重跑时自动跳过。
Resumable: processed cells are tracked in _build_progress and skipped on re-run.

用法 / Usage:
    .venv/bin/python scripts_dev/build_geo_distribution.py --tier1 cumulative:0.999
    .venv/bin/python scripts_dev/build_geo_distribution.py --resume   # 续跑
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(PROJ, "birdid", "data", "geo_distribution.db")
AVES_CLASS_KEY = 212
LICENSES = ("CC0_1_0", "CC_BY_4_0")
WORKERS = 8
BATCH = 200
MAX_RETRY = 5


def cell_id_of(lat_bin: int, lon_bin: int) -> int:
    """
    网格编号 / Encode a 1-degree cell as a single integer.

    参数 / Parameters:
        lat_bin (int): floor(纬度)，-90..89 / floor(latitude).
        lon_bin (int): floor(经度)，-180..179 / floor(longitude).

    返回 / Returns:
        int: 0..64799 的网格编号 / Cell id in 0..64799.
    """
    lat_bin = max(-90, min(89, lat_bin))
    lon_bin = max(-180, min(179, lon_bin))
    return (lat_bin + 90) * 360 + (lon_bin + 180)


def fetch_cell(lat_bin: int, lon_bin: int) -> Optional[Dict[int, int]]:
    """
    拉取单个网格内的鸟种及观察记录数，带 429/网络错误退避重试。

    Fetch per-species occurrence counts for one cell, with backoff retries on
    HTTP 429 and transient network errors.

    参数 / Parameters:
        lat_bin (int): 网格南边界纬度 / Southern latitude of the cell.
        lon_bin (int): 网格西边界经度 / Western longitude of the cell.

    返回 / Returns:
        Optional[dict]: {gbif_species_key: count}；重试耗尽仍失败时返回 None /
            {gbif_species_key: count}, or None when all retries are exhausted.
    """
    params = [
        ("classKey", str(AVES_CLASS_KEY)),
        ("decimalLatitude", f"{lat_bin},{lat_bin + 1}"),
        ("decimalLongitude", f"{lon_bin},{lon_bin + 1}"),
        ("hasCoordinate", "true"),
        ("hasGeospatialIssue", "false"),
        ("facet", "speciesKey"),
        ("facetLimit", "1200"),
        ("limit", "0"),
    ]
    for lic in LICENSES:
        params.append(("license", lic))
    url = "https://api.gbif.org/v1/occurrence/search?" + urllib.parse.urlencode(params)

    for attempt in range(MAX_RETRY):
        req = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-build/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            out: Dict[int, int] = {}
            for f in data.get("facets", []):
                if f.get("field") == "SPECIES_KEY":
                    for c in f.get("counts", []):
                        out[int(c["name"])] = int(c["count"])
            return out
        except urllib.error.HTTPError as e:
            if e.code != 429:
                if attempt == MAX_RETRY - 1:
                    print(f"[build] 网格 ({lat_bin},{lon_bin}) HTTP {e.code}，放弃")
                    return None
            time.sleep(2 ** attempt)
        except Exception:
            if attempt == MAX_RETRY - 1:
                return None
            time.sleep(2 ** attempt)
    return None


def load_key_to_class() -> Dict[int, int]:
    """
    GBIF specieskey → model_class_id 映射（覆盖 10963/10964）。

    Mapping from GBIF specieskey to model class id (covers 10963/10964).

    返回 / Returns:
        dict[int, int]: {specieskey: model_class_id}
    """
    db = sqlite3.connect(os.path.join(PROJ, "birdid", "data", "bird_reference.sqlite"))
    m: Dict[int, int] = {}
    for cid, skey in db.execute(
        "SELECT model_class_id, specieskey FROM gbif_rarity_100 WHERE specieskey IS NOT NULL"
    ):
        try:
            m[int(skey)] = int(cid)
        except (TypeError, ValueError):
            continue
    db.close()
    return m


def land_cells() -> List[Tuple[int, int]]:
    """
    枚举待扫描的陆地网格 / Enumerate the land cells to scan.

    用 avonet.db 中有分布记录的网格作为枚举源（18,709 个）。该依赖是一次性的：
    数据落地后 Task 7 删除 avonet.db 不影响本库。

    Uses the cells with distribution records in avonet.db (18,709) as the
    enumeration source. This dependency is one-shot: once the data is built,
    Task 7's removal of avonet.db does not affect this database.

    返回 / Returns:
        list[tuple[int, int]]: [(lat_bin, lon_bin), ...]
    """
    path = os.path.join(PROJ, "birdid", "data", "avonet.db")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"网格枚举源缺失 / cell enumeration source missing: {path}"
        )
    av = sqlite3.connect(path)
    rows = av.execute(
        "SELECT p.south, p.west FROM places p WHERE EXISTS "
        "(SELECT 1 FROM distributions d WHERE d.worldid = p.worldid)"
    ).fetchall()
    av.close()
    # 必须去重：places 的边界不是整数对齐（如 south=-87.13），多行会 floor 到
    # 同一个 1°网格。18,709 行去重后为 16,882 格；不去重会让完成度统计虚低，
    # 把"已全部完成"误报成"仍有 1,840 格未完成"。
    # Deduplicate: places boundaries are not integer-aligned (e.g. south=-87.13),
    # so several rows floor to the same 1-degree cell. 18,709 rows collapse to
    # 16,882 cells; without this, the completion count reads far too low and a
    # finished run is misreported as "1,840 cells remaining".
    return sorted({(int(s // 1), int(w // 1)) for s, w in rows})


def init_db(path: str, resume: bool) -> sqlite3.Connection:
    """
    打开（必要时重建）目标库并确保表结构存在。

    Open (recreating when not resuming) the target database and ensure the schema.

    参数 / Parameters:
        path (str): 数据库路径 / Database path.
        resume (bool): True 时保留已有数据续跑 / Keep existing data when True.

    返回 / Returns:
        sqlite3.Connection: 已就绪的连接 / A ready connection.
    """
    if not resume and os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(
        """
        -- WITHOUT ROWID + 复合主键：主键 B 树即是表本身，省掉隐藏 rowid 与
        -- 一份独立的 cell_id 索引；同时主键约束天然防止同一网格被重复写入。
        -- 普通 rowid 表实测 86.3 MB，改此结构后显著缩小。
        -- WITHOUT ROWID with a composite primary key: the key B-tree *is* the
        -- table, dropping both the hidden rowid and a separate cell_id index,
        -- and the constraint prevents duplicate rows for a cell outright.
        -- A plain rowid table measured 86.3 MB for the same data.
        CREATE TABLE IF NOT EXISTS cell_species (
            cell_id  INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            n        INTEGER NOT NULL,
            PRIMARY KEY (cell_id, class_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS country_species (
            country  TEXT NOT NULL,
            class_id INTEGER NOT NULL,
            n        INTEGER NOT NULL,
            PRIMARY KEY (country, class_id)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS _build_progress (cell_id INTEGER PRIMARY KEY);
        """
    )
    db.commit()
    return db


def harvest(db: sqlite3.Connection, key2cls: Dict[int, int], cells: List[Tuple[int, int]]) -> int:
    """
    并发拉取所有网格并分批写入，跳过已完成的网格。

    Fetch all cells concurrently and write in batches, skipping completed cells.

    参数 / Parameters:
        db (sqlite3.Connection): 目标库连接 / Target database connection.
        key2cls (dict): specieskey → class_id 映射 / mapping.
        cells (list): 待扫描网格 / Cells to scan.

    返回 / Returns:
        int: 本次新处理的网格数 / Number of cells processed in this run.
    """
    done: Set[int] = {r[0] for r in db.execute("SELECT cell_id FROM _build_progress")}
    todo = [c for c in cells if cell_id_of(c[0], c[1]) not in done]
    print(f"[build] 待扫描 / to scan: {len(todo)}（已完成 / done: {len(done)}）")

    t0 = time.time()
    processed = 0
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results = list(ex.map(lambda c: (c, fetch_cell(c[0], c[1])), chunk))

        rows: List[Tuple[int, int, int]] = []
        progress: List[Tuple[int]] = []
        for (lat_bin, lon_bin), counts in results:
            if counts is None:
                continue                      # 失败的格不标记完成，留待续跑重试
            cid = cell_id_of(lat_bin, lon_bin)
            acc: Dict[int, int] = {}
            for skey, n in counts.items():
                cls = key2cls.get(skey)
                if cls is not None:
                    acc[cls] = acc.get(cls, 0) + n
            rows.extend((cid, cls, n) for cls, n in acc.items())
            progress.append((cid,))

        db.executemany(
            "INSERT OR REPLACE INTO cell_species (cell_id, class_id, n) VALUES (?,?,?)", rows
        )
        db.executemany("INSERT OR IGNORE INTO _build_progress (cell_id) VALUES (?)", progress)
        db.commit()
        processed += len(progress)

        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        remain = (len(todo) - processed) / rate / 60 if rate > 0 else 0
        print(f"[build] {processed}/{len(todo)} 格  {rate:.1f} 格/秒  剩余约 {remain:.0f} 分钟", flush=True)

    return processed


def rollup_countries(db: sqlite3.Connection) -> int:
    """
    按国家汇总网格数据 / Roll up cell data by country.

    每个网格中心用 reverse_geocoder 反查 ISO 国家代码后聚合。

    Each cell centre is reverse-geocoded to an ISO country code, then aggregated.

    参数 / Parameters:
        db (sqlite3.Connection): 目标库连接 / Target database connection.

    返回 / Returns:
        int: 写入的国家级行数 / Number of country-level rows written.
    """
    import reverse_geocoder as rg

    cell_ids = [r[0] for r in db.execute("SELECT DISTINCT cell_id FROM cell_species")]
    if not cell_ids:
        return 0
    coords = []
    for cid in cell_ids:
        lat_bin, lon_bin = divmod(cid, 360)
        coords.append((lat_bin - 90 + 0.5, lon_bin - 180 + 0.5))
    print(f"[build] 反查国家 / reverse-geocoding {len(coords)} cells ...", flush=True)
    results = rg.search(coords, mode=2, verbose=False)
    cell_country = {
        cid: str(r.get("cc", "")).upper()
        for cid, r in zip(cell_ids, results)
        if r.get("cc")
    }

    acc: Dict[Tuple[str, int], int] = {}
    for cid, cls, n in db.execute("SELECT cell_id, class_id, n FROM cell_species"):
        cc = cell_country.get(cid)
        if not cc:
            continue
        acc[(cc, cls)] = acc.get((cc, cls), 0) + n

    db.execute("DELETE FROM country_species")
    db.executemany(
        "INSERT OR REPLACE INTO country_species (country, class_id, n) VALUES (?,?,?)",
        [(cc, cls, n) for (cc, cls), n in acc.items()],
    )
    db.commit()
    return len(acc)


def finalize(db: sqlite3.Connection, tier1: str) -> None:
    """
    建索引、写 meta、清理进度表并压实。

    Create indexes, write meta, drop the progress table, and vacuum.

    参数 / Parameters:
        db (sqlite3.Connection): 目标库连接 / Target database connection.
        tier1 (str): Task 1 标定的 L1 方案 / The calibrated L1 strategy.
    """
    # 两表均为 WITHOUT ROWID + 复合主键，主键 B 树已按 cell_id / country 前缀
    # 排序，范围查询直接走主键，无需额外索引。
    # Both tables are WITHOUT ROWID with composite keys, so the primary-key
    # B-tree already orders by cell_id / country; no extra index is needed.
    db.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
        [
            ("snapshot_date", date.today().isoformat()),
            ("gbif_doi", "GBIF.org Occurrence Search API (facet aggregation)"),
            ("license", "CC0-1.0 / CC-BY-4.0"),
            ("attribution", "GBIF.org occurrence data; CC0 and CC-BY-4.0 records only"),
            ("builder_version", "2"),
            ("tier1_threshold", tier1),
        ],
    )
    db.execute("DROP TABLE IF EXISTS _build_progress")
    db.commit()
    db.execute("VACUUM")


def main() -> None:
    p = argparse.ArgumentParser(description="Build geo_distribution.db from the GBIF API")
    p.add_argument("--tier1", default="cumulative:0.999",
                   help="Task 1 标定的 L1 方案 / calibrated L1 strategy")
    p.add_argument("--resume", action="store_true",
                   help="保留已有数据续跑 / keep existing data and resume")
    a = p.parse_args()

    key2cls = load_key_to_class()
    print(f"[build] specieskey→class_id 映射: {len(key2cls)}")
    cells = land_cells()
    print(f"[build] 陆地网格 / land cells: {len(cells)}")

    db = init_db(OUT_PATH, a.resume)
    processed = harvest(db, key2cls, cells)
    remaining = len(cells) - db.execute("SELECT COUNT(*) FROM _build_progress").fetchone()[0]
    if remaining > 0:
        print(f"[build] ⚠️ 仍有 {remaining} 格未完成，用 --resume 续跑")
        db.close()
        sys.exit(1)

    n_country = rollup_countries(db)
    finalize(db, a.tier1)
    db.close()
    size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
    print(f"[build] 完成 / done: 本次 {processed} 格，国家行 {n_country}，{size_mb:.1f} MB")


if __name__ == "__main__":
    main()
