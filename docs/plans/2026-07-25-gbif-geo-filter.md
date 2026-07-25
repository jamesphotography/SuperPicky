# GBIF 地理分布过滤器实施计划 / GBIF Geo-Distribution Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 GBIF CC0/CC-BY 快照自建的 `class_id × 1°网格 × 观察次数` 数据集，替换 `avonet.db` 与 428 个离线 eBird 清单，并把硬屏蔽改成分层候选集逐层放宽。

**Architecture:** 新增 `birdid/data/geo_distribution.db`（两表：`cell_species` 网格分布、`country_species` 国家汇总）与 `birdid/geo_filter.py`（分层候选迭代器）。`bird_identifier.identify_bird` 从"一次性拿候选集 + 三级断裂兜底"改为"遍历候选层，命中即停"。生成脚本 `scripts_dev/build_geo_distribution.py` 复用现有 DuckDB+S3 管线，数据升级 = 换快照日期重跑。

**Tech Stack:** Python 3.x、SQLite3、DuckDB 1.5.3（读 S3 Parquet）、pytest 8.4.2、reverse_geocoder（已在用）、PyInstaller（.spec 打包）

设计依据 / Spec: `docs/specs/2026-07-25-gbif-geo-filter-design.md`

## Global Constraints

- **UTF-8 强制**：所有文件读写用 `open(..., encoding='utf-8')`；不得用 sed/awk 处理含中文文件，优先 Python。
- **注释规范**：中文注释 + 同格式英文注释；函数/类用 docstring 写明功能、参数、返回值、异常。
- **类型注解**：函数入参与返回值均标注类型，避免过于宽泛的标注。
- **跨平台**：Windows + macOS 均需可用；路径用 `os.path` / `pathlib`，不得硬编码分隔符。
- **打包路径解析**：新数据文件的路径解析必须覆盖三条分支——打包 Windows 用 `config.get_install_scoped_resource_path`，打包非 Windows 用 `config.get_runtime_meipass`，开发环境用相对项目根路径。参照 `core/region_data.py:22-54` 的既有实现。
- **测试文件被 gitignore**：`.gitignore:31` 含 `test_*.py`，新建测试文件提交时**必须用 `git add -f`**，否则静默漏提交。
- **Python 解释器**：一律用 `.venv/bin/python`，不得用系统 Python。
- **改动后编译检查**：每个任务结束前对改动的 .py 跑 `.venv/bin/python -m py_compile`。
- **验收数据集**：回归用的真实样本在 `.superpicky_backup_JJ2TB_20260723-215817/report.db`（433 张法罗/冰岛照片，348 张带 GPS）。该文件**只读，不得修改或删除**。
- **cell_id 编码（全计划统一）**：`cell_id = (lat_bin + 90) * 360 + (lon_bin + 180)`，其中 `lat_bin = clamp(floor(lat), -90, 89)`、`lon_bin = clamp(floor(lon), -180, 179)`。取值域 `0..64799`。

---

### Task 1: 标定 L1 阈值（阻塞后续全部任务）

Spec §5.1 明确 L1 阈值不能是固定绝对值：悉尼单格原鸽 45,213 条记录，`n≥5` 几乎不筛（624→502）；冰岛稀疏，同样阈值排掉 78%（497→111）。本任务用真实采样在三个候选方案中选定一个，结论写回 spec。

**Files:**
- Create: `scripts_dev/calibrate_geo_threshold.py`
- Modify: `docs/specs/2026-07-25-gbif-geo-filter-design.md`（§5.1 未决项改为结论）

**Interfaces:**
- Consumes: 无（本任务是全计划的前置）
- Produces: 阈值函数的最终形式，供 Task 2 写入 `meta.tier1_threshold`、供 Task 3 的 `_tier1_filter` 实现。三个候选：
  - `absolute`: `n >= 5`
  - `cumulative`: 按 n 降序累加，保留累积占该格总记录 99.5% 的物种
  - `hybrid`: `n >= max(2, 0.0001 * cell_total)`

- [ ] **Step 1: 写采样脚本**

创建 `scripts_dev/calibrate_geo_threshold.py`：

```python
# -*- coding: utf-8 -*-
"""
L1 阈值标定 / Calibrate the L1 candidate-set threshold.

从 GBIF Occurrence API 采样跨观察强度的 1°网格，比较三种阈值方案在
「候选集规模」与「当地常见种保留率」上的表现，为 geo_filter 的 L1 选定方案。

Sample 1-degree cells across a range of observation intensities from the GBIF
Occurrence API and compare three threshold strategies on candidate-set size and
retention of locally common species, to pick the L1 strategy for geo_filter.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import statistics
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Set, Tuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVES_CLASS_KEY = 212
LICENSES = ("CC0_1_0", "CC_BY_4_0")
TOP_N_MUST_KEEP = 30


def fetch_cell(lat: float, lon: float) -> Dict[int, int]:
    """
    拉取指定 1°网格内的鸟种及记录数 / Fetch bird species + counts for one cell.

    参数 / Parameters:
        lat (float): 网格内任一纬度 / Any latitude inside the cell.
        lon (float): 网格内任一经度 / Any longitude inside the cell.

    返回 / Returns:
        dict[int, int]: {gbif_species_key: occurrence_count}

    异常 / Exceptions:
        urllib.error.URLError: 网络失败时抛出，由调用方处理。
    """
    s, w = float(int(lat // 1)), float(int(lon // 1))
    params = [
        ("classKey", str(AVES_CLASS_KEY)),
        ("decimalLatitude", f"{s},{s + 1}"),
        ("decimalLongitude", f"{w},{w + 1}"),
        ("hasCoordinate", "true"),
        ("hasGeospatialIssue", "false"),
        ("facet", "speciesKey"),
        ("facetLimit", "1200"),
        ("limit", "0"),
    ]
    for lic in LICENSES:
        params.append(("license", lic))
    url = "https://api.gbif.org/v1/occurrence/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-calibrate/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.load(resp)
    out: Dict[int, int] = {}
    for f in data.get("facets", []):
        if f.get("field") == "SPECIES_KEY":
            for c in f.get("counts", []):
                out[int(c["name"])] = int(c["count"])
    return out


def load_key_to_class() -> Dict[int, int]:
    """GBIF specieskey → model_class_id 映射 / mapping."""
    db = sqlite3.connect(os.path.join(PROJ, "birdid/data/bird_reference.sqlite"))
    m = {
        int(k): int(c)
        for c, k in db.execute(
            "SELECT model_class_id, specieskey FROM gbif_rarity_100 "
            "WHERE specieskey IS NOT NULL"
        )
    }
    db.close()
    return m


def strategy_absolute(counts: Dict[int, int]) -> Set[int]:
    """方案 A：固定绝对阈值 n>=5 / Fixed absolute threshold."""
    return {c for c, n in counts.items() if n >= 5}


def strategy_cumulative(counts: Dict[int, int], cover: float = 0.995) -> Set[int]:
    """方案 B：保留累积覆盖 99.5% 记录的物种 / Cumulative coverage."""
    total = sum(counts.values())
    if total == 0:
        return set()
    kept: Set[int] = set()
    acc = 0
    for c, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        kept.add(c)
        acc += n
        if acc >= total * cover:
            break
    return kept


def strategy_hybrid(counts: Dict[int, int]) -> Set[int]:
    """方案 C：绝对下限与相对阈值取大 / max(2, 0.0001 * total)."""
    total = sum(counts.values())
    thr = max(2, int(total * 0.0001))
    return {c for c, n in counts.items() if n >= thr}


def main() -> None:
    key2cls = load_key_to_class()
    av = sqlite3.connect(os.path.join(PROJ, "birdid/data/avonet.db"))
    cells = av.execute(
        "SELECT p.south, p.west FROM places p WHERE EXISTS "
        "(SELECT 1 FROM distributions d WHERE d.worldid = p.worldid)"
    ).fetchall()
    av.close()

    random.seed(2026)
    sample = random.sample(cells, 60)

    stats: Dict[str, List[int]] = {"absolute": [], "cumulative": [], "hybrid": []}
    violations: Dict[str, int] = {"absolute": 0, "cumulative": 0, "hybrid": 0}
    strategies = {
        "absolute": strategy_absolute,
        "cumulative": strategy_cumulative,
        "hybrid": strategy_hybrid,
    }
    empty_cells = 0

    for i, (s, w) in enumerate(sample, 1):
        try:
            raw = fetch_cell(s + 0.5, w + 0.5)
        except Exception as e:
            print(f"  [{i}/60] ({s},{w}) 查询失败 / query failed: {e}")
            continue
        counts: Dict[int, int] = {}
        for k, n in raw.items():
            c = key2cls.get(k)
            if c is not None:
                counts[c] = counts.get(c, 0) + n
        if not counts:
            empty_cells += 1
            continue
        # 该格记录数 top-30 视为当地常见种，任何方案都不应排除
        # Top-30 by count are locally common; no strategy may drop them
        must_keep = {
            c for c, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_MUST_KEEP]
        }
        for name, fn in strategies.items():
            kept = fn(counts)
            stats[name].append(len(kept))
            if must_keep - kept:
                violations[name] += 1
        if i % 10 == 0:
            print(f"  已采样 / sampled {i}/60")
        time.sleep(0.3)

    print(f"\n空网格 / empty cells: {empty_cells}")
    print(f"{'方案':<12}{'中位':>7}{'均值':>8}{'P10':>7}{'P90':>7}{'top30违规格数':>14}")
    for name in ("absolute", "cumulative", "hybrid"):
        v = sorted(stats[name])
        if not v:
            continue
        p10 = v[max(0, int(len(v) * 0.10) - 1)]
        p90 = v[min(len(v) - 1, int(len(v) * 0.90))]
        print(
            f"{name:<12}{statistics.median(v):>7.0f}{statistics.mean(v):>8.0f}"
            f"{p10:>7}{p90:>7}{violations[name]:>14}"
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行标定**

```bash
.venv/bin/python scripts_dev/calibrate_geo_threshold.py
```

预期：约 60 次 API 调用，每次 0.5–0.8 s，总计 1–2 分钟。输出三方案的候选集规模分位数与 top-30 违规格数。

- [ ] **Step 3: 依据判定标准选定方案**

判定顺序（硬要求优先）：

1. **硬要求**：`top30违规格数 == 0`。任何排除了当地 top-30 常见种的方案直接淘汰——这正是 AVONET 屏蔽悉尼家麻雀的失败模式。
2. **规模要求**：中位数落在 100–400 类。低于 100 有冰岛式崩塌风险，高于 400 则 L1 相对 L2 没有区分价值。
3. **稳定性**：P10 不低于 50（避免稀疏格 L1 过窄），P90 不高于 600。

若多个方案同时满足，取中位数更小者（过滤更强）。若无方案满足硬要求，放宽 `cumulative` 的 cover 到 0.999 重跑 Step 2。

- [ ] **Step 4: 把结论写回 spec**

编辑 `docs/specs/2026-07-25-gbif-geo-filter-design.md` 的 §5.1，把标题从「未决：L1 阈值 T 的标定」改为「L1 阈值 T（已标定）」，正文替换为选定方案、判定依据的实测数字、以及被淘汰方案的原因。同时更新 §5 表格中 L1 行的冰岛/悉尼实测值。

- [ ] **Step 5: 提交**

```bash
git add scripts_dev/calibrate_geo_threshold.py docs/specs/2026-07-25-gbif-geo-filter-design.md
git commit -m "chore(geo): 标定 L1 候选集阈值并将结论写回设计文档"
```

---

### Task 2: 生成 geo_distribution.db

**Files:**
- Rewrite: `scripts_dev/build_geo_distribution.py`（当前版本走 S3，已证明不可行，整体重写）
- Create: `birdid/data/geo_distribution.db`（脚本产物，约 16 MB）

**Interfaces:**
- Consumes: Task 1 选定的阈值方案，写入 `meta.tier1_threshold` 的值为**精确字符串 `cumulative:0.999`**
- Produces: SQLite 库，表结构见下。Task 3 依赖 `cell_species(cell_id, class_id, n)`、`country_species(country, class_id, n)`、`meta(key, value)` 三张表及 `idx_cell` / `idx_country` 两个索引。

**为什么不走 S3（已实测否决）/ Why not S3 (measured and rejected):**

原计划用 DuckDB 直读 S3 Parquet，实际执行时暴露三个问题：

1. **列名全错**：GBIF Parquet 没有 `classkey`（只有 `class VARCHAR`，值为 `'Aves'`）、
   没有 `hasgeospatialissues`（只有 `issue VARCHAR[]`），且 `specieskey` 是 **VARCHAR** 不是整数。
2. **规模不可行**：快照有 **8,515 个分片 / 265 GB**；实测单分片聚合 19.4 s，
   串行外推 46 小时。首次尝试即以 `IOException: Timeout ... occurrence.parquet/000018` 失败。
3. **预筛选下载同样不现实**：鸟类 + 有坐标 + CC0/CC-BY 共 **21 亿条**记录
   （eBird 观察数据集在 GBIF 上是 CC-BY-4.0，不被许可过滤排除），SIMPLE_PARQUET 仍有 20–30 GB。

改用 GBIF Occurrence API 的 `speciesKey` facet 逐格聚合——服务端直接返回我们要的
「每格每种计数」，无需下载任何原始记录。实测吞吐：

| 模式 | 等效单格 | 18,709 格外推 | 错误 |
|---|---|---|---|
| 串行 | 0.54 s | 2.8 小时 | 0 |
| 并发 4 | 0.172 s | 0.89 小时 | 2× HTTP 429 |
| **并发 8** | **0.079 s** | **0.41 小时** | 0 |
| 并发 16 | 0.311 s | 1.62 小时 | 1× 超时（过载反降） |

并发 8 是甜点。**429 限流真实存在**，必须有退避重试；**必须支持断点续传**，
中断后能接着跑。该方法与 Task 1 的阈值标定同源，`cumulative:0.999` 直接适用。

- [ ] **Step 1: 重写生成脚本**

把 `scripts_dev/build_geo_distribution.py` 整体替换为：

```python
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
    return [(int(s // 1), int(w // 1)) for s, w in rows]


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
        CREATE TABLE IF NOT EXISTS cell_species (
            cell_id  INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            n        INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS country_species (
            country  TEXT NOT NULL,
            class_id INTEGER NOT NULL,
            n        INTEGER NOT NULL
        );
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

        db.executemany("INSERT INTO cell_species (cell_id, class_id, n) VALUES (?,?,?)", rows)
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
        "INSERT INTO country_species (country, class_id, n) VALUES (?,?,?)",
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
    db.executescript(
        "CREATE INDEX IF NOT EXISTS idx_cell ON cell_species(cell_id);"
        "CREATE INDEX IF NOT EXISTS idx_country ON country_species(country);"
    )
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
```

- [ ] **Step 2: 后台启动构建**

```bash
.venv/bin/python scripts_dev/build_geo_distribution.py --tier1 cumulative:0.999
```

用 `run_in_background: true` 启动并轮询日志。预期 30–60 分钟（并发 8 实测外推 25 分钟，
留出限流与热点格的余量）。脚本每 200 格打印一次进度与预计剩余时间。

若中途失败或有未完成网格（退出码 1），用 `--resume` 续跑，不要从头重来：

```bash
.venv/bin/python scripts_dev/build_geo_distribution.py --resume --tier1 cumulative:0.999
```

- [ ] **Step 3: 校验产物**

```bash
.venv/bin/python -c "
import sqlite3
db = sqlite3.connect('birdid/data/geo_distribution.db')
print('cell rows', db.execute('SELECT count(*) FROM cell_species').fetchone()[0])
print('country rows', db.execute('SELECT count(*) FROM country_species').fetchone()[0])
print('distinct class', db.execute('SELECT count(DISTINCT class_id) FROM cell_species').fetchone()[0])
print('distinct country', db.execute('SELECT count(DISTINCT country) FROM country_species').fetchone()[0])
print('meta', dict(db.execute('SELECT key, value FROM meta').fetchall()))
print('progress table dropped:', not db.execute(
    \"SELECT name FROM sqlite_master WHERE type='table' AND name='_build_progress'\").fetchone())
cid = (-34 + 90) * 360 + (151 + 180)
print('悉尼格类别数', db.execute('SELECT count(*) FROM cell_species WHERE cell_id=?', (cid,)).fetchone()[0])
cid_is = (63 + 90) * 360 + (-20 + 180)
print('冰岛格类别数', db.execute('SELECT count(*) FROM cell_species WHERE cell_id=?', (cid_is,)).fetchone()[0])
"
```

**硬性验收门 / Hard gates**（任一不达标都不得提交，须查明原因）：

- `distinct class` ≥ 10000 — 低于此值说明 `specieskey → model_class_id` 映射有问题
- `distinct country` ≥ 200 — 低于此值说明 reverse_geocoding 失败
- `meta` 六个键齐全，且 `tier1_threshold` **精确等于 `cumulative:0.999`**
- `_build_progress` 表已被 `finalize` 删除
- 悉尼格类别数 ≥ 500、冰岛格类别数 ≥ 300（对照 Task 1 实测：悉尼 624、冰岛 497）

- [ ] **Step 4: 提交**

```bash
git check-ignore -v birdid/data/geo_distribution.db || echo "not ignored, ok"
git add scripts_dev/build_geo_distribution.py birdid/data/geo_distribution.db
git commit -m "feat(geo): 用 GBIF API 逐格聚合生成地理分布库"
```

若 `geo_distribution.db` 被 gitignore 命中，**不要 `-f` 强加**，报告给控制方决定。
---

### Task 3: geo_filter 分层候选模块

**Files:**
- Create: `birdid/geo_filter.py`
- Test: `test_geo_filter.py`

**Interfaces:**
- Consumes: Task 2 的 `geo_distribution.db`（表 `cell_species` / `country_species` / `meta`）
- Produces: 供 Task 4 使用的公开接口：
  - `cell_id_for(lat: float, lon: float) -> int`
  - `class GeoFilter` — `is_available() -> bool`、`iter_candidates(lat: Optional[float], lon: Optional[float], country_code: Optional[str]) -> Iterator[Tuple[Optional[Set[int]], str]]`、`close() -> None`
  - `get_geo_filter() -> Optional[GeoFilter]`（进程级单例，走 `get_lazy_registry()`）
  - 层标签常量：`TIER_CELL_STRONG = "L1_cell_strong"`、`TIER_CELL_ALL = "L2_cell_all"`、`TIER_NEIGHBORHOOD = "L3_neighborhood"`、`TIER_COUNTRY = "L4_country"`、`TIER_NONE = "L5_none"`
  - 最后一层产出 `(None, TIER_NONE)`，`None` 表示不过滤

- [ ] **Step 1: 写失败测试**

创建 `test_geo_filter.py`：

```python
"""
地理分层候选过滤器单测 / Unit tests for the layered geo candidate filter.

用临时 SQLite 构造已知分布，验证 cell_id 编码、分层顺序与逐层放宽行为，
不依赖真实的 geo_distribution.db。

Builds a temporary SQLite with a known distribution to verify cell-id encoding,
tier ordering, and progressive widening, without touching the real database.
"""
import sqlite3

import pytest

from birdid.geo_filter import (
    GeoFilter,
    TIER_CELL_ALL,
    TIER_CELL_STRONG,
    TIER_COUNTRY,
    TIER_NEIGHBORHOOD,
    TIER_NONE,
    cell_id_for,
)


def test_cell_id_encoding_origin():
    """(0,0) 落在 lat_bin=0, lon_bin=0 → (0+90)*360 + (0+180)"""
    assert cell_id_for(0.5, 0.5) == 90 * 360 + 180


def test_cell_id_encoding_negative():
    """悉尼 (-33.87, 151.21) → lat_bin=-34, lon_bin=151"""
    assert cell_id_for(-33.87, 151.21) == (-34 + 90) * 360 + (151 + 180)


def test_cell_id_clamps_poles_and_dateline():
    """lat=90 / lon=180 必须 clamp，不得越界"""
    assert cell_id_for(90.0, 180.0) == (89 + 90) * 360 + (179 + 180)
    assert 0 <= cell_id_for(-90.0, -180.0) <= 64799


@pytest.fixture
def db_path(tmp_path):
    """构造：悉尼格有 3 个种（n=100/3/1），邻格有 1 个种，AU 国家级有 5 个种"""
    p = tmp_path / "geo.db"
    db = sqlite3.connect(str(p))
    db.executescript(
        """
        CREATE TABLE cell_species (cell_id INTEGER, class_id INTEGER, n INTEGER);
        CREATE TABLE country_species (country TEXT, class_id INTEGER, n INTEGER);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX idx_cell ON cell_species(cell_id);
        CREATE INDEX idx_country ON country_species(country);
        """
    )
    sydney = (-34 + 90) * 360 + (151 + 180)
    neighbour = (-33 + 90) * 360 + (151 + 180)
    db.executemany(
        "INSERT INTO cell_species VALUES (?,?,?)",
        [(sydney, 1, 100), (sydney, 2, 3), (sydney, 3, 1), (neighbour, 4, 50)],
    )
    db.executemany(
        "INSERT INTO country_species VALUES (?,?,?)",
        [("AU", i, 10) for i in range(1, 6)],
    )
    db.execute("INSERT INTO meta VALUES ('tier1_threshold','cumulative')")
    db.commit()
    db.close()
    return str(p)


def test_tier_order_and_widening(db_path):
    """分层必须按 L1→L2→L3→L4→L5 顺序产出，且逐层变宽"""
    f = GeoFilter(db_path)
    tiers = list(f.iter_candidates(-33.87, 151.21, "AU"))
    labels = [t for _, t in tiers]
    assert labels == [
        TIER_CELL_STRONG, TIER_CELL_ALL, TIER_NEIGHBORHOOD, TIER_COUNTRY, TIER_NONE
    ]
    l1, l2, l3 = tiers[0][0], tiers[1][0], tiers[2][0]
    assert l1 <= l2 <= l3          # 逐层包含
    assert 3 in l2 and 3 not in l1  # n=1 的种被 L1 排除、L2 保留
    assert 4 in l3                  # 邻格的种只在 L3 出现
    assert tiers[4][0] is None      # L5 表示不过滤


def test_no_gps_starts_at_country(db_path):
    """无 GPS 时从 L4 起步"""
    f = GeoFilter(db_path)
    labels = [t for _, t in f.iter_candidates(None, None, "AU")]
    assert labels == [TIER_COUNTRY, TIER_NONE]


def test_no_gps_no_country_is_unfiltered(db_path):
    """无 GPS 且无国家 → 只有 L5"""
    f = GeoFilter(db_path)
    out = list(f.iter_candidates(None, None, None))
    assert out == [(None, TIER_NONE)]


def test_empty_cell_falls_through_to_neighbourhood(db_path):
    """空网格不产出空候选集，直接降到有内容的层"""
    f = GeoFilter(db_path)
    # 选一个 cell_species 里没有的格（南太平洋）
    tiers = list(f.iter_candidates(-20.5, -150.5, None))
    for cand, label in tiers:
        assert cand is None or len(cand) > 0, f"{label} 产出了空候选集"


def test_unavailable_db_yields_only_none(tmp_path):
    """库缺失时只产出 L5，不抛异常"""
    f = GeoFilter(str(tmp_path / "missing.db"))
    assert f.is_available() is False
    assert list(f.iter_candidates(-33.87, 151.21, "AU")) == [(None, TIER_NONE)]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest test_geo_filter.py -v
```

预期：全部 FAIL，`ModuleNotFoundError: No module named 'birdid.geo_filter'`

- [ ] **Step 3: 实现模块**

创建 `birdid/geo_filter.py`：

```python
# -*- coding: utf-8 -*-
"""
分层地理候选过滤器 / Layered geographic candidate filter.

基于 `birdid/data/geo_distribution.db`（GBIF CC0/CC-BY 快照派生）按层产出
候选物种集合：本格强候选 → 本格全部 → 邻域 3x3 → 国家级 → 不过滤。
调用方逐层放宽直到识别有结果，避免旧实现中候选集过窄时直接崩到无过滤。

Yields candidate species sets in widening tiers from geo_distribution.db
(derived from a GBIF CC0/CC-BY snapshot): strong in-cell, all in-cell, 3x3
neighbourhood, country, unfiltered. Callers widen until recognition returns a
result, avoiding the old implementation's collapse straight to no filtering.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from typing import Iterator, List, Optional, Set, Tuple

from tools.i18n import t as _t

TIER_CELL_STRONG = "L1_cell_strong"
TIER_CELL_ALL = "L2_cell_all"
TIER_NEIGHBORHOOD = "L3_neighborhood"
TIER_COUNTRY = "L4_country"
TIER_NONE = "L5_none"

_CUMULATIVE_COVER = 0.995


def cell_id_for(lat: float, lon: float) -> int:
    """
    把经纬度编码成 1°网格编号 / Encode a coordinate into a 1-degree cell id.

    参数 / Parameters:
        lat (float): 纬度 / Latitude, -90..90.
        lon (float): 经度 / Longitude, -180..180.

    返回 / Returns:
        int: 0..64799 的网格编号 / Cell id in 0..64799.
    """
    lat_bin = max(-90, min(89, int(lat // 1)))
    lon_bin = max(-180, min(179, int(lon // 1)))
    return (lat_bin + 90) * 360 + (lon_bin + 180)


def _neighbour_cells(lat: float, lon: float) -> List[int]:
    """3x3 邻域的 9 个网格编号 / The nine cell ids of the 3x3 neighbourhood."""
    lat_bin = max(-90, min(89, int(lat // 1)))
    lon_bin = max(-180, min(179, int(lon // 1)))
    out: List[int] = []
    for dlat in (-1, 0, 1):
        for dlon in (-1, 0, 1):
            la = lat_bin + dlat
            if la < -90 or la > 89:
                continue
            lo = lon_bin + dlon
            if lo > 179:
                lo -= 360      # 跨日期变更线回绕 / wrap across the dateline
            elif lo < -180:
                lo += 360
            out.append((la + 90) * 360 + (lo + 180))
    return out


def default_db_path() -> str:
    """
    解析 geo_distribution.db 路径，兼容开发与打包环境。

    Resolve geo_distribution.db, covering development and packaged builds.

    返回 / Returns:
        str: 绝对路径 / Absolute path.
    """
    rel = os.path.join("birdid", "data", "geo_distribution.db")
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        from config import get_install_scoped_resource_path
        return str(get_install_scoped_resource_path(rel))
    if getattr(sys, "frozen", False):
        from config import get_runtime_meipass
        meipass = get_runtime_meipass()
        if meipass is not None:
            return os.path.join(meipass, rel)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class GeoFilter:
    """
    分层地理候选过滤器 / Layered geographic candidate filter.

    参数 / Parameters:
        db_path (Optional[str]): 数据库路径；None 时自动解析 /
            Database path; auto-resolved when None.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._tier1_strategy = "cumulative"
        if os.path.exists(self.db_path):
            try:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key='tier1_threshold'"
                ).fetchone()
                if row and row[0]:
                    self._tier1_strategy = str(row[0])
            except sqlite3.Error as e:
                print(_t("logs.geo_db_failed", e=e))
                self._conn = None

    def is_available(self) -> bool:
        """数据库是否可用 / Whether the database is usable."""
        if self._conn is None:
            return False
        try:
            return self._conn.execute("SELECT count(*) FROM cell_species LIMIT 1").fetchone()[0] > 0
        except sqlite3.Error:
            return False

    def _cell_counts(self, cell_ids: List[int]) -> dict:
        """查询若干网格的 {class_id: n} 合并结果 / Merged counts for cells."""
        if self._conn is None or not cell_ids:
            return {}
        try:
            marks = ",".join("?" * len(cell_ids))
            rows = self._conn.execute(
                f"SELECT class_id, SUM(n) FROM cell_species "
                f"WHERE cell_id IN ({marks}) GROUP BY class_id",
                cell_ids,
            ).fetchall()
            return {int(c): int(n) for c, n in rows}
        except sqlite3.Error as e:
            print(_t("logs.geo_cell_failed", e=e))
            return {}

    def _tier1_filter(self, counts: dict) -> Set[int]:
        """
        按标定的方案裁剪出 L1 强候选 / Apply the calibrated L1 strategy.

        参数 / Parameters:
            counts (dict): {class_id: 观察次数} / {class_id: occurrence count}.

        返回 / Returns:
            set[int]: L1 候选 class_id 集合 / L1 candidate class ids.
        """
        if not counts:
            return set()
        if self._tier1_strategy == "absolute":
            return {c for c, n in counts.items() if n >= 5}
        if self._tier1_strategy == "hybrid":
            total = sum(counts.values())
            thr = max(2, int(total * 0.0001))
            return {c for c, n in counts.items() if n >= thr}
        total = sum(counts.values())
        kept: Set[int] = set()
        acc = 0
        for c, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            kept.add(c)
            acc += n
            if acc >= total * _CUMULATIVE_COVER:
                break
        return kept

    def _country_species(self, country_code: str) -> Set[int]:
        """国家级候选 / Country-level candidates."""
        if self._conn is None or not country_code:
            return set()
        try:
            rows = self._conn.execute(
                "SELECT class_id FROM country_species WHERE country=?",
                (country_code.upper(),),
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error as e:
            print(_t("logs.geo_country_failed", e=e))
            return set()

    def iter_candidates(
        self,
        lat: Optional[float],
        lon: Optional[float],
        country_code: Optional[str] = None,
    ) -> Iterator[Tuple[Optional[Set[int]], str]]:
        """
        按层产出候选集，调用方逐层放宽直到有结果。

        Yield candidate sets tier by tier; the caller widens until recognition
        succeeds. Empty tiers are skipped so a sparse cell never produces an
        empty candidate set (which would mask every class).

        参数 / Parameters:
            lat (Optional[float]): 纬度，无 GPS 时为 None / Latitude or None.
            lon (Optional[float]): 经度，无 GPS 时为 None / Longitude or None.
            country_code (Optional[str]): 国家代码，用于 L4 / Country code for L4.

        返回 / Returns:
            Iterator[tuple]: (候选集或 None, 层标签) / (candidates or None, tier label).
            最后一项恒为 (None, TIER_NONE)，表示不过滤 / The last item is always
            (None, TIER_NONE), meaning unfiltered.
        """
        if not self.is_available():
            yield None, TIER_NONE
            return

        has_gps = lat is not None and lon is not None
        if has_gps:
            counts = self._cell_counts([cell_id_for(float(lat), float(lon))])
            l1 = self._tier1_filter(counts)
            if l1:
                yield l1, TIER_CELL_STRONG
            l2 = set(counts)
            if l2 and l2 != l1:
                yield l2, TIER_CELL_ALL
            l3 = set(self._cell_counts(_neighbour_cells(float(lat), float(lon))))
            if l3 and l3 != l2:
                yield l3, TIER_NEIGHBORHOOD

        if country_code:
            l4 = self._country_species(country_code)
            if l4:
                yield l4, TIER_COUNTRY

        yield None, TIER_NONE

    def close(self) -> None:
        """关闭连接 / Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


def get_geo_filter() -> Optional["GeoFilter"]:
    """
    进程级单例 / Process-wide singleton.

    返回 / Returns:
        Optional[GeoFilter]: 可用时返回实例，否则 None / Instance or None.
    """
    from config import get_lazy_registry

    def _factory() -> Optional["GeoFilter"]:
        try:
            f = GeoFilter()
            if f.is_available():
                return f
            print(_t("logs.geo_unavailable"))
        except Exception as e:  # noqa: BLE001
            print(_t("logs.geo_init_failed", e=e))
        return None

    return get_lazy_registry().get_or_create("birdid.geo_filter", _factory)
```

- [ ] **Step 4: 补 locales 键**

在 `locales/zh_CN.json` 的 `logs` 段（`avonet_*` 键附近，约 `:335-341`）加入：

```json
"geo_db_failed": "[GeoFilter] 数据库连接失败: {e}",
"geo_cell_failed": "[GeoFilter] 网格查询失败: {e}",
"geo_country_failed": "[GeoFilter] 国家查询失败: {e}",
"geo_unavailable": "[GeoFilter] 地理分布库不可用，地理过滤将被跳过",
"geo_init_failed": "[GeoFilter] 初始化失败: {e}"
```

在 `locales/en_US.json` 同一段加入：

```json
"geo_db_failed": "[GeoFilter] Database connection failed: {e}",
"geo_cell_failed": "[GeoFilter] Cell query failed: {e}",
"geo_country_failed": "[GeoFilter] Country query failed: {e}",
"geo_unavailable": "[GeoFilter] Geo-distribution DB unavailable, geo filtering skipped",
"geo_init_failed": "[GeoFilter] Init failed: {e}"
```

- [ ] **Step 5: 运行测试确认通过**

```bash
.venv/bin/python -m pytest test_geo_filter.py -v
.venv/bin/python -m py_compile birdid/geo_filter.py
```

预期：9 个测试全 PASS。

- [ ] **Step 6: 提交（测试文件需 -f）**

```bash
git add birdid/geo_filter.py locales/zh_CN.json locales/en_US.json
git add -f test_geo_filter.py
git commit -m "feat(geo): 新增分层地理候选过滤器 GeoFilter"
```

---

### Task 4: bird_identifier 接入分层候选

**Files:**
- Modify: `birdid/bird_identifier.py:1108-1186`（`identify_bird` 的过滤与兜底段）
- Modify: `birdid/bird_identifier.py:318-340`（`get_species_filter` 改为委托 `get_geo_filter`）
- Test: `test_geo_filter_wiring.py`

**Interfaces:**
- Consumes: Task 3 的 `get_geo_filter()`、`iter_candidates()`、五个 `TIER_*` 常量
- Produces: `identify_bird` 返回的 `result["geo_info"]`，结构为 `{"enabled": bool, "tier": str, "species_count": int, "country_code": Optional[str]}`。Task 6 的 UI 与日志读取该字段。旧的 `result["ebird_info"]` 同步保留一个轮换周期（值由 `geo_info` 派生），供尚未迁移的调用方过渡。

- [ ] **Step 1: 写失败测试**

创建 `test_geo_filter_wiring.py`：

```python
"""
identify_bird 分层候选接线测试 / Wiring tests for layered candidates in identify_bird.

用假的过滤器与假的 predict 验证「逐层放宽、命中即停」的控制流，
不加载真实模型。

Verifies the widen-until-hit control flow with a fake filter and fake predict,
without loading the real model.
"""
from typing import Optional, Set

import pytest

from birdid.geo_filter import (
    TIER_CELL_ALL,
    TIER_CELL_STRONG,
    TIER_COUNTRY,
    TIER_NONE,
)


class FakeFilter:
    """按预设脚本产出候选层 / Yields a scripted sequence of tiers."""

    def __init__(self, tiers):
        self._tiers = tiers

    def is_available(self):
        return True

    def iter_candidates(self, lat, lon, country_code=None):
        return iter(self._tiers)


def test_stops_at_first_tier_with_results(monkeypatch):
    """L1 就有结果 → 不应继续放宽"""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 1, "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([({1, 2}, TIER_CELL_STRONG), ({1, 2, 3}, TIER_CELL_ALL), (None, TIER_NONE)]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code="AU",
    )
    assert tier == TIER_CELL_STRONG
    assert len(calls) == 1, "命中后不应再调用更宽的层"


def test_widens_when_tier_empty(monkeypatch):
    """L1 无结果 → 放宽到 L2"""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        if species_class_ids == {1, 2}:
            return []
        return [{"class_id": 3, "confidence": 80.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([({1, 2}, TIER_CELL_STRONG), ({1, 2, 3}, TIER_CELL_ALL), (None, TIER_NONE)]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code="AU",
    )
    assert tier == TIER_CELL_ALL
    assert results and results[0]["class_id"] == 3


def test_falls_through_to_unfiltered(monkeypatch):
    """所有层都无结果 → 最终无过滤"""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        if species_class_ids is None:
            return [{"class_id": 9, "confidence": 50.0}]
        return []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([({1}, TIER_CELL_STRONG), ({5}, TIER_COUNTRY), (None, TIER_NONE)]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=None, lon=None, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    assert tier == TIER_NONE
    assert results[0]["class_id"] == 9


def test_no_filter_available(monkeypatch):
    """过滤器不可用 → 直接无过滤识别一次"""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 7, "confidence": 60.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: None)
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code=None,
        is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    assert tier == TIER_NONE
    assert calls == [None]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest test_geo_filter_wiring.py -v
```

预期：全部 FAIL，`AttributeError: module 'birdid.bird_identifier' has no attribute '_identify_with_tiers'`

- [ ] **Step 3: 新增分层识别函数**

在 `birdid/bird_identifier.py` 中 `identify_bird` 定义之前插入：

```python
def _identify_with_tiers(
    image,
    top_k: int,
    lat: Optional[float],
    lon: Optional[float],
    country_code: Optional[str],
    is_yolo_cropped: bool,
    name_format: Optional[str],
    photo_country_code: Optional[str],
) -> Tuple[List[Dict], str, Optional[int]]:
    """
    遍历地理候选层，命中即停 / Walk the geo candidate tiers, stopping at the first hit.

    旧实现一次性取候选集，过窄时直接崩到无过滤（冰岛 54 类即触发该路径，
    产出跨半球错误）。改为逐层放宽后，稀疏网格会平滑降到邻域或国家级。

    The old implementation took a single candidate set and collapsed straight to
    unfiltered when it was too narrow (Iceland's 54-class cell triggered exactly
    that, yielding cross-hemisphere errors). Widening tier by tier lets sparse
    cells degrade smoothly to the neighbourhood or country level.

    参数 / Parameters:
        image: 待识别图像 / Image to identify.
        top_k (int): 返回结果数 / Number of results.
        lat (Optional[float]): 纬度 / Latitude.
        lon (Optional[float]): 经度 / Longitude.
        country_code (Optional[str]): 国家/地区代码 / Country or region code.
        is_yolo_cropped (bool): 是否已由 YOLO 裁剪 / Whether YOLO already cropped.
        name_format (Optional[str]): 鸟名格式 / Bird name format.
        photo_country_code (Optional[str]): 拍摄国家，用于 GBIF 罕见度 / Shooting country.

    返回 / Returns:
        tuple: (结果列表, 命中的层标签, 该层候选数或 None) /
               (results, tier label, candidate count or None).
    """
    geo = get_geo_filter()
    if geo is None:
        results = predict_bird(
            image, top_k=top_k, species_class_ids=None,
            is_yolo_cropped=is_yolo_cropped, name_format=name_format,
            photo_country_code=photo_country_code,
        )
        return results, TIER_NONE, None

    for candidates, tier in geo.iter_candidates(lat, lon, country_code):
        results = predict_bird(
            image, top_k=top_k, species_class_ids=candidates,
            is_yolo_cropped=is_yolo_cropped, name_format=name_format,
            photo_country_code=photo_country_code,
        )
        if results:
            return results, tier, (len(candidates) if candidates else None)
    return [], TIER_NONE, None
```

在文件顶部的 import 区加入：

```python
from birdid.geo_filter import TIER_NONE, get_geo_filter
```

- [ ] **Step 4: 替换 identify_bird 的过滤段**

把 `birdid/bird_identifier.py:1108-1186` 中从 `if use_ebird:` 起、到全局兜底 `if not results:` 段结束的整块，替换为：

```python
        # 地理过滤：分层候选集逐层放宽，替代旧的「一次性候选 + 三级断裂兜底」
        # Geo filter: layered candidates widened tier by tier, replacing the old
        # single candidate set with three disconnected fallbacks.
        effective_region = region_code or country_code or photo_country_code
        if use_geo_filter:
            results, tier, count = _identify_with_tiers(
                image,
                top_k=top_k,
                lat=lat,
                lon=lon,
                country_code=effective_region,
                is_yolo_cropped=is_yolo_cropped,
                name_format=name_format,
                photo_country_code=photo_country_code,
            )
            result["geo_info"] = {
                "enabled": tier != TIER_NONE,
                "tier": tier,
                "species_count": count,
                "country_code": effective_region,
            }
        else:
            results = predict_bird(
                image, top_k=top_k, species_class_ids=None,
                is_yolo_cropped=is_yolo_cropped, name_format=name_format,
                photo_country_code=photo_country_code,
            )
            result["geo_info"] = {"enabled": False, "tier": TIER_NONE,
                                  "species_count": None, "country_code": None}
```

同时把 `identify_bird` 的形参 `use_ebird: bool = True` 改名为 `use_geo_filter: bool = True`，并删除随之失效的局部变量 `species_filter` / `species_class_ids` 及其相关代码。**`result` 字典初始化处的 `"ebird_info": None` 一并删除，改为 `"geo_info": None`。**

**注意**：`core/photo_processor.py:1230-1240` 按**位置**传参给 `identify_bird`（第 4 个位置实参是 `use_ebird`），改名不影响该调用；但 `birdid_cli.py:49`、`:428` 与 `birdid_server.py:324` 用的是关键字 `use_ebird=`，必须同步改名，否则 `TypeError`。

- [ ] **Step 5: 同步关键字调用点**

- `birdid_cli.py:49` 与 `:428`：`use_ebird=args.ebird` → `use_geo_filter=args.ebird`
- `birdid_server.py:324`：`use_ebird=use_ebird` → `use_geo_filter=use_ebird`

- [ ] **Step 6: 迁移全部 ebird_info 消费点并删除该字段**

`ebird_info` 共有 3 个文件 20 处读取，其中 `country_fallback` / `gps_fallback`
两个布尔标记已被 `tier` 取代，**不得保留兼容层**——保留会让这些分支永远走 False，
UI 在过滤降级时反而显示正常状态。分层信息比布尔标记更丰富，直接展示层级。

先加分层文案。`locales/zh_CN.json` 的 `birdid` 段加入：

```json
"geo_tier_cell_strong": "🗺️ 按拍摄地过滤（{count} 种）",
"geo_tier_cell_all": "🗺️ 按拍摄地过滤（{count} 种，含少见记录）",
"geo_tier_neighborhood": "⚠️ 拍摄地记录较少，已扩大到周边区域（{count} 种）",
"geo_tier_country": "⚠️ 已回退到国家级过滤 {country}（{count} 种）",
"geo_tier_none": "⚠️ 无地理过滤，按全球鸟种识别"
```

`locales/en_US.json` 的 `birdid` 段：

```json
"geo_tier_cell_strong": "🗺️ Filtered by location ({count} species)",
"geo_tier_cell_all": "🗺️ Filtered by location ({count} species, incl. rare records)",
"geo_tier_neighborhood": "⚠️ Few records at this location, widened to nearby area ({count} species)",
"geo_tier_country": "⚠️ Fell back to country-level filter {country} ({count} species)",
"geo_tier_none": "⚠️ No geographic filter, identifying against all species"
```

新增共用的层级文案函数，放在 `birdid/geo_filter.py` 末尾（Task 3 已建该文件）：

```python
_TIER_I18N_KEYS = {
    TIER_CELL_STRONG: "birdid.geo_tier_cell_strong",
    TIER_CELL_ALL: "birdid.geo_tier_cell_all",
    TIER_NEIGHBORHOOD: "birdid.geo_tier_neighborhood",
    TIER_COUNTRY: "birdid.geo_tier_country",
    TIER_NONE: "birdid.geo_tier_none",
}


def describe_tier(geo_info: Optional[dict]) -> str:
    """
    把 geo_info 渲染成一行可读的过滤状态说明。

    Render geo_info into a single human-readable filter-status line.

    参数 / Parameters:
        geo_info (Optional[dict]): identify_bird 返回的 geo_info /
            The geo_info dict returned by identify_bird.

    返回 / Returns:
        str: 已本地化的说明文本；geo_info 为空时按未过滤处理 /
             Localized description; treated as unfiltered when geo_info is None.
    """
    info = geo_info or {}
    tier = info.get("tier", TIER_NONE)
    key = _TIER_I18N_KEYS.get(tier, _TIER_I18N_KEYS[TIER_NONE])
    return _t(
        key,
        count=info.get("species_count") or 0,
        country=info.get("country_code") or "?",
    )
```

然后逐处替换：

- **`birdid_cli.py:154-162`**：整段 `if result.get('ebird_info'):` 块替换为

```python
        if result.get('geo_info'):
            from birdid.geo_filter import describe_tier
            print(describe_tier(result['geo_info']))
```

- **`birdid_server.py:406-415`**：`ebird_info` 改读 `geo_info`，错误响应体的
  `'ebird_info': ebird_info` 改为 `'geo_info': geo_info`，`server.ebird_filter_error`
  的实参改用 `geo_info.get('tier')` 与 `species_count`。
- **`birdid_server.py:428`**：响应字段 `'ebird_info': result.get('ebird_info')` 改为
  `'geo_info': result.get('geo_info')`。
- **`birdid_server.py:432-437`**：整段回退警告替换为

```python
        geo_info = result.get('geo_info') or {}
        if geo_info.get('tier') in (TIER_NEIGHBORHOOD, TIER_COUNTRY, TIER_NONE):
            from birdid.geo_filter import describe_tier
            response['warning'] = describe_tier(geo_info)
```

- **`ui/birdid_dock.py:1752-1774`**：整段「2. 地理过滤状态」替换为

```python
        # 2. 地理过滤状态：直接展示命中的候选层
        # 2. Geo filter status: show which candidate tier was used
        gps_info = result.get('gps_info')
        geo_info = result.get('geo_info')
        if gps_info and gps_info.get('latitude'):
            lat = f"{gps_info['latitude']:.2f}"
            lon = f"{gps_info['longitude']:.2f}"
            info_lines.append(t("birdid.info_gps_coords", lat=lat, lon=lon))
        from birdid.geo_filter import describe_tier
        info_lines.append(describe_tier(geo_info))
```

`locales/zh_CN.json` 的 `birdid` 段补 `"info_gps_coords": "📍 GPS: {lat}, {lon}"`；
`en_US.json` 补 `"info_gps_coords": "📍 GPS: {lat}, {lon}"`。

删除已无引用的旧文案键：`birdid.info_gps`、`birdid.info_region`、`birdid.info_global`、
`birdid.info_gps_fallback`、`birdid.info_country_fallback`、`cli.ebird_info`、
`server.gps_fallback_warning`、`server.country_fallback_warning`（两个语言文件都删）。
用 Python 脚本删除，不得用 sed（含中文）。

- [ ] **Step 7: 确认无残留**

```bash
grep -rn "ebird_info\|country_fallback\|gps_fallback" --include="*.py" --include="*.json" . \
  | grep -v ".venv" | grep -v ".worktrees"
```

预期：无输出。

- [ ] **Step 8: 运行测试**

```bash
.venv/bin/python -m pytest test_geo_filter_wiring.py test_geo_filter.py -v
.venv/bin/python -m py_compile birdid/bird_identifier.py birdid/geo_filter.py birdid_cli.py birdid_server.py ui/birdid_dock.py
.venv/bin/python -c "
import json
for p in ['locales/zh_CN.json','locales/en_US.json']:
    json.load(open(p, encoding='utf-8'))
    print(p, 'JSON 合法')
"
```

预期：全部 PASS，两个 locale 文件均为合法 JSON。

- [ ] **Step 9: 提交**

```bash
git add birdid/bird_identifier.py birdid/geo_filter.py birdid_cli.py birdid_server.py \
        ui/birdid_dock.py locales/zh_CN.json locales/en_US.json
git add -f test_geo_filter_wiring.py
git commit -m "feat(geo): identify_bird 改用分层候选，geo_info 取代 ebird_info"
```

---

### Task 5: 配置迁移与国家列表数据源

**Files:**
- Modify: `advanced_config.py:183`（DEFAULT_CONFIG）、`:882-894`（property）、`:1004-1032`（`set_birdid_region`）、`:1078`（迁移函数）
- Modify: `core/region_data.py:57-79`（`load_regions_data`）
- Modify: `ui/main_window.py:373-403`、`core/photo_processor.py:67-68`
- Test: `test_geo_config_migration.py`

**Interfaces:**
- Consumes: Task 2 的 `country_species` 表
- Produces: `advanced_config.birdid_use_geo_filter` 属性与 `set_birdid_region(use_geo_filter=...)` 关键字；`core.region_data.load_regions_data()` 返回结构不变（`{"countries": [...]}`），但数据来自 `geo_distribution.db`，每项含 `code` / `name` / `name_cn` / `species_count`，`regions` 恒为空列表、`has_regions` 恒为 `False`。

- [ ] **Step 1: 写失败测试**

创建 `test_geo_config_migration.py`：

```python
"""
配置键迁移与国家列表数据源测试 / Config-key migration and country-list source tests.
"""
import json

import pytest


def test_old_key_migrates_to_new(tmp_path, monkeypatch):
    """旧键 birdid_use_ebird=False 应迁移为 birdid_use_geo_filter=False"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text(json.dumps({"birdid_use_ebird": False}), encoding="utf-8")

    from advanced_config import AdvancedConfig

    cfg = AdvancedConfig(str(cfg_file))
    assert cfg.birdid_use_geo_filter is False
    assert "birdid_use_geo_filter" in cfg.config


def test_new_key_wins_over_old(tmp_path):
    """新旧键并存时以新键为准"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text(
        json.dumps({"birdid_use_ebird": False, "birdid_use_geo_filter": True}),
        encoding="utf-8",
    )
    from advanced_config import AdvancedConfig

    cfg = AdvancedConfig(str(cfg_file))
    assert cfg.birdid_use_geo_filter is True


def test_default_is_true(tmp_path):
    """两个键都没有 → 默认开启"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    from advanced_config import AdvancedConfig

    assert AdvancedConfig(str(cfg_file)).birdid_use_geo_filter is True


def test_region_data_from_geo_db():
    """国家列表来自 geo_distribution.db，且不含 species_count=0 的空壳国家"""
    from core.region_data import load_regions_data

    data = load_regions_data()
    countries = data["countries"]
    assert len(countries) >= 200, "GBIF 覆盖应远多于旧的 49 国"
    assert all(c["species_count"] > 0 for c in countries), "不应有空壳国家"
    codes = {c["code"] for c in countries}
    # 旧版三方错位的两类受害者都应出现
    assert {"IS", "FO"} <= codes, "冰岛/法罗群岛缺失（旧版空白区）"
    assert {"PA", "UG", "BO"} <= codes, "旧版有数据但选不到的国家缺失"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest test_geo_config_migration.py -v
```

预期：FAIL，`AttributeError: 'AdvancedConfig' object has no attribute 'birdid_use_geo_filter'`

- [ ] **Step 3: 改配置键**

`advanced_config.py:183` 的 DEFAULT_CONFIG：把 `"birdid_use_ebird": True` 改为 `"birdid_use_geo_filter": True`。

`:882-894` 的 property 替换为：

```python
    @property
    def birdid_use_geo_filter(self) -> bool:
        """
        获取是否启用地理过滤（含 GPS 网格与国家级候选）。

        该开关实际控制的是整个地理过滤链路，而非仅 eBird 数据源；旧键
        `birdid_use_ebird` 的迁移在 `load()` 中完成，此处只读新键。

        返回:
        bool: 是否启用（默认 True）

        Get whether geographic filtering is enabled (GPS grid + country tiers).
        The switch governs the whole geo-filter pipeline, not just an eBird
        source. Migration from the legacy `birdid_use_ebird` key happens in
        `load()`; this property reads the new key only.

        Return:
        bool: Whether enabled (default True).
        """
        return bool(self.config.get("birdid_use_geo_filter", True))
```

**迁移必须写在 `load()` 里，不能写在 property 里。** `__init__` 是
`self.config = self.DEFAULT_CONFIG.copy()` 后 `self.config.update(loaded_config)`
（`advanced_config.py:204-213`），DEFAULT_CONFIG 已含新键，所以 merged `self.config`
中新键**恒存在**——任何基于 `"birdid_use_geo_filter" in self.config` 的判断都恒为真，
迁移永远不会触发。判断必须基于**磁盘上读到的** `loaded_config`。

在 `load()` 中 `self.config.update(loaded_config)` 之后插入：

```python
                    # 旧键 birdid_use_ebird 一次性迁移到 birdid_use_geo_filter。
                    # 必须基于 loaded_config 判断：self.config 已合并 DEFAULT_CONFIG，
                    # 新键恒存在，用它判断会让迁移永不触发。
                    # One-time migration from the legacy birdid_use_ebird key.
                    # The check must use loaded_config: self.config has already
                    # been merged with DEFAULT_CONFIG, so the new key always
                    # exists there and would make this migration dead code.
                    if (
                        "birdid_use_geo_filter" not in loaded_config
                        and "birdid_use_ebird" in loaded_config
                    ):
                        self.config["birdid_use_geo_filter"] = bool(
                            loaded_config["birdid_use_ebird"]
                        )
```

`:1004-1032` 的 `set_birdid_region`：形参 `use_ebird` 改名 `use_geo_filter`，写入行 `self.config["birdid_use_ebird"] = bool(use_ebird)` 改为 `self.config["birdid_use_geo_filter"] = bool(use_geo_filter)`，并同步更新其 docstring 的中英说明。

`:1078` 的 `migrate_birdid_dock_settings`：`self.config["birdid_use_ebird"] = bool(old.get("use_ebird", True))` 改为写入 `birdid_use_geo_filter`。

DEFAULT_CONFIG 中**不保留** `birdid_use_ebird` 旧键——保留会让上面的 `in loaded_config`
判断继续有效但产生两个语义重复的持久化字段。旧键只在磁盘上的历史配置中出现一次，读后即弃。

- [ ] **Step 4: 改国家列表数据源**

`core/region_data.py` 的 `load_regions_data` 改为从 `geo_distribution.db` 读取。保留 `_get_birdid_data_path` 不动（其他调用方仍可能用），新增：

```python
def load_regions_data() -> dict[str, Any]:
    """
    从 `birdid/data/geo_distribution.db` 的 `country_species` 表生成国家列表。

    旧实现读 `ebird_regions.json`，该文件与离线数据、`REGION_BOUNDS` 三方错位
    （11 国选中即落空、14 国有数据却选不到）。改为与网格数据同源后，
    国家列表恒等于实际可用的过滤数据。

    Build the country list from the `country_species` table of
    geo_distribution.db. The previous implementation read `ebird_regions.json`,
    which was out of sync with both the offline data and REGION_BOUNDS.

    返回 / Returns:
        dict: `{"countries": [...]}`；失败时返回 `{"countries": []}` /
              `{"countries": [...]}`, or an empty list on failure.

    异常 / Exceptions:
        不抛出异常；错误以 print 记录 / Never raises; errors are printed.
    """
    import sqlite3

    from birdid.geo_filter import default_db_path

    db_path = default_db_path()
    if not os.path.exists(db_path):
        print(f"[region_data] 地理分布库不存在 / geo DB missing: {db_path}")
        return {"countries": []}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT country, COUNT(*) FROM country_species GROUP BY country "
            "HAVING COUNT(*) > 0 ORDER BY country"
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[region_data] 读取国家列表失败 / failed to read countries: {exc}")
        return {"countries": []}

    from tools.country_names import country_display_names

    countries = []
    for code, cnt in rows:
        en, cn = country_display_names(str(code))
        countries.append({
            "code": str(code),
            "name": en,
            "name_cn": cn,
            "is_continent": False,
            "has_regions": False,
            "regions_count": 0,
            "regions": [],
            "species_count": int(cnt),
        })
    return {"countries": countries}
```

新建 `tools/country_names.py` 提供 ISO 代码到中英文名的映射：

```python
# -*- coding: utf-8 -*-
"""
ISO 3166-1 alpha-2 国家代码显示名 / Display names for ISO 3166-1 alpha-2 codes.

优先用 pycountry（若已安装）取英文名，中文名走内置常用国家表，
未命中时回退为代码本身，保证 UI 永远有可显示文本。

Prefer pycountry for English names when available; Chinese names come from a
built-in table of common countries. Falls back to the raw code so the UI always
has displayable text.
"""
from __future__ import annotations

from typing import Dict, Tuple

_CN_NAMES: Dict[str, str] = {
    "AU": "澳大利亚", "US": "美国", "CN": "中国", "GB": "英国", "CA": "加拿大",
    "TW": "台湾", "HK": "香港", "BR": "巴西", "CO": "哥伦比亚", "PE": "秘鲁",
    "EC": "厄瓜多尔", "IN": "印度", "ID": "印度尼西亚", "MX": "墨西哥",
    "AR": "阿根廷", "ZA": "南非", "KE": "肯尼亚", "TZ": "坦桑尼亚",
    "MG": "马达加斯加", "JP": "日本", "KR": "韩国", "TH": "泰国",
    "VN": "越南", "PH": "菲律宾", "MY": "马来西亚", "SG": "新加坡",
    "NP": "尼泊尔", "FR": "法国", "DE": "德国", "ES": "西班牙", "IT": "意大利",
    "NL": "荷兰", "NO": "挪威", "SE": "瑞典", "FI": "芬兰", "IS": "冰岛",
    "FO": "法罗群岛", "CR": "哥斯达黎加", "CL": "智利", "NZ": "新西兰",
    "UA": "乌克兰", "RU": "俄罗斯", "TR": "土耳其", "EG": "埃及",
    "GR": "希腊", "MA": "摩洛哥", "LK": "斯里兰卡", "PL": "波兰",
    "CH": "瑞士", "PT": "葡萄牙", "MN": "蒙古", "PA": "巴拿马",
    "UG": "乌干达", "BO": "玻利维亚", "VE": "委内瑞拉", "GT": "危地马拉",
    "HN": "洪都拉斯", "NI": "尼加拉瓜", "BZ": "伯利兹", "SV": "萨尔瓦多",
    "ET": "埃塞俄比亚", "GH": "加纳", "NG": "尼日利亚", "CM": "喀麦隆",
    "RO": "罗马尼亚", "AT": "奥地利", "BE": "比利时", "DK": "丹麦",
    "IE": "爱尔兰", "IL": "以色列", "PK": "巴基斯坦", "BD": "孟加拉国",
    "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝", "BT": "不丹",
}


def country_display_names(code: str) -> Tuple[str, str]:
    """
    取国家的英文名与中文名 / Get the English and Chinese names for a country.

    参数 / Parameters:
        code (str): ISO 3166-1 alpha-2 代码 / ISO 3166-1 alpha-2 code.

    返回 / Returns:
        tuple[str, str]: (英文名, 中文名)；未知代码两者均回退为代码本身 /
            (English, Chinese); both fall back to the raw code when unknown.
    """
    code = (code or "").upper()
    en = code
    try:
        import pycountry

        c = pycountry.countries.get(alpha_2=code)
        if c is not None:
            en = getattr(c, "common_name", None) or c.name
    except Exception:  # noqa: BLE001
        pass
    return en, _CN_NAMES.get(code, en)
```

- [ ] **Step 5: 更新两处调用方**

`ui/main_window.py:373-403`：`birdid_use_ebird = _adv_birdid.birdid_use_ebird` 改为 `birdid_use_geo_filter = _adv_birdid.birdid_use_geo_filter`，`ProcessingSettings(...)` 中 `birdid_use_ebird=` 改为 `birdid_use_geo_filter=`。

`core/photo_processor.py:67-68` 的 dataclass 字段 `birdid_use_ebird` 改名 `birdid_use_geo_filter`；`:1235-1236` 附近传参处同步。

- [ ] **Step 6: 运行测试**

```bash
.venv/bin/python -m pytest test_geo_config_migration.py -v
.venv/bin/python -m py_compile advanced_config.py core/region_data.py tools/country_names.py ui/main_window.py core/photo_processor.py
```

预期：4 个测试全 PASS。

- [ ] **Step 7: 提交**

```bash
git add advanced_config.py core/region_data.py tools/country_names.py ui/main_window.py core/photo_processor.py
git add -f test_geo_config_migration.py
git commit -m "feat(geo): 配置键迁移为 birdid_use_geo_filter，国家列表改由地理分布库生成"
```

---

### Task 6: 设置中心 UI 适配

**Files:**
- Modify: `ui/settings_center.py:890-1000`（国家/地区下拉的填充与恢复）
- Modify: `ui/birdid_dock.py:859-861`、`:1307`
- Modify: `locales/zh_CN.json`、`locales/en_US.json`（新增提示文案）

**Interfaces:**
- Consumes: Task 5 的 `load_regions_data()` 新结构（`has_regions` 恒 `False`）、`set_birdid_region(use_geo_filter=...)`
- Produces: 无下游依赖

- [ ] **Step 1: 适配地区下拉恒空的情况**

`_populate_bid_regions` 现依赖 `country_entry["has_regions"]` 填充州级下拉。新数据源无州级数据，该下拉将恒为空。改为：当 `regions` 为空时隐藏地区下拉及其标签（`self._bid_region.setVisible(False)`），只保留国家选择。同步处理 `_restore_birdid_country` 中恢复地区的分支——`saved_region_code` 找不到时不再回退按显示名匹配，直接跳过。

- [ ] **Step 2: 加「仅无 GPS 时生效」提示**

在设置中心国家下拉下方加一行说明标签，文案键 `settings.birdid_region_hint`。

`locales/zh_CN.json` 的 `settings` 段加入：

```json
"birdid_region_hint": "仅在照片没有 GPS 信息时生效；有 GPS 时自动按拍摄位置过滤"
```

`locales/en_US.json`：

```json
"birdid_region_hint": "Only applies to photos without GPS; photos with GPS are filtered by their location"
```

- [ ] **Step 3: 更新 birdid_dock 传参**

`ui/birdid_dock.py:859-861` 构造请求字典处，`"country_code": cfg.birdid_country_code` 保留，把同段的 `use_ebird` 键改为 `use_geo_filter`；`:1307` 的 `set_birdid_region(...)` 调用把 `use_ebird=` 关键字改为 `use_geo_filter=`。

同步 `birdid_server.py:49`、`:60`、`:304`、`:310` 的 `'use_ebird'` 字典键改为 `'use_geo_filter'`（服务端读取 GUI 设置的默认值来源）。

- [ ] **Step 4: 关于页补 GBIF 署名**

Spec §7 要求 CC-BY 署名在关于页展示。在 `ui/settings_center.py` 的关于页（第 6 页）
数据来源区块，参照 iRateBird 的既有署名写法，新增一条读取 `meta` 表的动态署名：

```python
    def _geo_attribution_text(self) -> str:
        """
        读取地理分布库的 meta 生成署名文本 / Build the attribution line from meta.

        返回 / Returns:
            str: 含快照日期与许可的署名；库不可用时返回空串 /
                 Attribution with snapshot date and license; empty when unavailable.
        """
        try:
            import sqlite3

            from birdid.geo_filter import default_db_path

            path = default_db_path()
            if not os.path.exists(path):
                return ""
            conn = sqlite3.connect(path)
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            conn.close()
        except Exception:  # noqa: BLE001
            return ""
        snapshot = meta.get("snapshot_date", "")
        return self.i18n.t("settings.geo_attribution", snapshot=snapshot)
```

`locales/zh_CN.json` 的 `settings` 段加入：

```json
"geo_attribution": "鸟类地理分布：GBIF.org 观察记录快照（{snapshot}），仅含 CC0-1.0 与 CC-BY-4.0 数据"
```

`locales/en_US.json`：

```json
"geo_attribution": "Bird distributions: GBIF.org occurrence snapshot ({snapshot}), CC0-1.0 and CC-BY-4.0 records only"
```

- [ ] **Step 5: 手工验证**

```bash
.venv/bin/python -m py_compile ui/settings_center.py ui/birdid_dock.py birdid_server.py
.venv/bin/python main.py
```

打开设置中心 → 识鸟页，确认：国家下拉含 200+ 国（含冰岛、巴拿马）；地区下拉已隐藏；提示文案显示正常；切换国家后重开设置中心，选择被正确保存。再打开关于页，确认 GBIF 署名行显示且快照日期非空。

**注意**（见 memory 教训）：直接运行 `main.py` 会读写本机真实 `advanced_config.json`，验证后请把国家选择改回原值，避免污染本地设置。

- [ ] **Step 6: 提交**

```bash
git add ui/settings_center.py ui/birdid_dock.py birdid_server.py locales/zh_CN.json locales/en_US.json
git commit -m "feat(geo): 设置中心国家列表接入地理分布库并补 GPS 生效说明"
```

---

### Task 7: 清理旧实现

**Files:**
- Delete: `birdid/ebird_country_filter.py`、`birdid/avonet_filter.py`、`birdid/data/avonet.db`、`birdid/data/offline_ebird_data/`、`birdid/data/ebird_regions.json`、`birdid/data/ebird_classid_mapping.json`
- Modify: `birdid/bird_identifier.py:318-340`（删 `get_species_filter`）
- Modify: `locales/zh_CN.json:316-318,335-341`、`locales/en_US.json` 同段（删 `avonet_*` 键）

**Interfaces:**
- Consumes: Task 3/4 已完成的替代实现
- Produces: 无

- [ ] **Step 1: 确认无残留引用**

```bash
grep -rn "avonet\|AvonetFilter\|ebird_country_filter\|offline_ebird_data\|ebird_regions\|ebird_classid_mapping" \
  --include="*.py" --include="*.spec" . | grep -v ".venv" | grep -v ".worktrees" | grep -v "scripts_dev/calibrate"
```

预期：只剩 `scripts_dev/build_geo_distribution.py` 与 `scripts_dev/calibrate_geo_threshold.py` 中读 `avonet.db` 取网格样本的代码。把这两处改为不依赖 avonet.db——`calibrate` 脚本改用固定的采样坐标列表（把 Task 1 实际用过的 60 组坐标硬编码进脚本，保证可复现），`build` 脚本本就不需要它。

- [ ] **Step 2: 删除文件**

```bash
git rm birdid/ebird_country_filter.py birdid/avonet_filter.py
git rm birdid/data/avonet.db birdid/data/ebird_regions.json birdid/data/ebird_classid_mapping.json
git rm -r birdid/data/offline_ebird_data
```

- [ ] **Step 3: 删 get_species_filter**

删除 `birdid/bird_identifier.py:318-340` 的 `get_species_filter` 函数整体（已由 `geo_filter.get_geo_filter` 取代）。

- [ ] **Step 4: 删 locales 中的 avonet 键**

用 Python 脚本删除（含中文，不得用 sed）：

```bash
.venv/bin/python -c "
import json, collections
for p in ['locales/zh_CN.json','locales/en_US.json']:
    with open(p, encoding='utf-8') as f:
        d = json.load(f, object_pairs_hook=collections.OrderedDict)
    logs = d.get('logs', {})
    removed = [k for k in list(logs) if k.startswith('avonet_')]
    for k in removed:
        del logs[k]
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(p, '删除', len(removed), '个键')
"
```

- [ ] **Step 5: 全量测试与编译检查**

```bash
.venv/bin/python -m pytest test_geo_filter.py test_geo_filter_wiring.py test_geo_config_migration.py -v
.venv/bin/python -m pytest -x -q 2>&1 | tail -20
.venv/bin/python -c "import birdid.bird_identifier, birdid.geo_filter, core.region_data, ui.settings_center"
```

预期：新增测试全 PASS；既有测试套件无新增失败；导入无 `ModuleNotFoundError`。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(geo): 移除 AVONET 与离线 eBird 两套旧实现"
```

---

### Task 8: 打包配置与冒烟测试

**Files:**
- Modify: `SuperPicky_lite_win.spec:65-68`
- Verify: `SuperPicky.spec:57`、`SuperPicky_win64.spec:58`、`SuperPicky_full.spec`

**Interfaces:**
- Consumes: Task 2 的 `geo_distribution.db`
- Produces: 无

- [ ] **Step 1: 改 Lite 打包清单**

`SuperPicky_lite_win.spec` 第 66–68 行（`ebird_classid_mapping.json`、`ebird_regions.json`、`offline_ebird_data`）删除，替换为：

```python
    (os.path.join(base_path, 'birdid', 'data', 'geo_distribution.db'), 'birdid/data'),
```

保留第 65 行的 `bird_reference.sqlite` 不动。Lite 版由此首次获得地理过滤能力（spec §3.4）。

- [ ] **Step 2: 确认另外三份 spec**

```bash
grep -n "birdid" SuperPicky.spec SuperPicky_win64.spec SuperPicky_full.spec
```

`SuperPicky.spec:57` 与 `SuperPicky_win64.spec:58` 打包整个 `birdid/data` 目录，新库自动包含，无需修改。若 `SuperPicky_full.spec` 未列 `birdid/data`，确认其是否继承自其他 spec；若确实缺失则按 `SuperPicky.spec:57` 的写法补上整目录。

- [ ] **Step 3: 打包冒烟（macOS）**

```bash
.venv/bin/python -m PyInstaller SuperPicky.spec --noconfirm 2>&1 | tail -20
ls -la dist/SuperPicky*/**/birdid/data/geo_distribution.db 2>/dev/null || \
  find dist -name "geo_distribution.db"
```

确认新库进入产物。随后启动打包应用，在识鸟面板对一张带 GPS 的照片做一次识别，确认日志出现 `geo_info.tier` 且非 `L5_none`。

- [ ] **Step 4: 验证路径解析三分支**

```bash
.venv/bin/python -c "
from birdid.geo_filter import default_db_path, GeoFilter
import os
p = default_db_path()
print('dev path:', p, os.path.exists(p))
f = GeoFilter()
print('available:', f.is_available())
"
```

打包环境的另两条分支（`get_install_scoped_resource_path` / `get_runtime_meipass`）由 Step 3 的实际启动覆盖。

- [ ] **Step 5: 提交**

```bash
git add SuperPicky_lite_win.spec SuperPicky_full.spec
git commit -m "build(geo): 打包配置改为纳入 geo_distribution.db，Lite 版首次具备地理过滤"
```

---

### Task 9: 回归验证

用 spec §8 的 8 条验收标准对真实样本做端到端验证。

**Files:**
- Create: `scripts_dev/validate_geo_filter.py`
- Modify: `docs/specs/2026-07-25-gbif-geo-filter-design.md`（§8 打勾并附实测数字）

**Interfaces:**
- Consumes: Task 2–8 的全部产物
- Produces: 验收结论

- [ ] **Step 1: 写验证脚本**

创建 `scripts_dev/validate_geo_filter.py`：

```python
# -*- coding: utf-8 -*-
"""
地理过滤回归验证 / Regression validation for the geo filter.

对照 spec §8 的验收标准，用真实备份库中的 433 张法罗/冰岛照片坐标与
若干已知场景，验证分层候选的行为。

Validates the layered candidate behaviour against the acceptance criteria in
spec section 8, using the 433 Faroe/Iceland photo coordinates from the backup
report database plus several known scenarios.
"""
from __future__ import annotations

import os
import sqlite3
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from birdid.geo_filter import TIER_NONE, GeoFilter, cell_id_for  # noqa: E402

BACKUP_DB = os.path.join(PROJ, ".superpicky_backup_JJ2TB_20260723-215817/report.db")


def sci_to_class(ref: sqlite3.Connection, sci: str) -> int | None:
    """学名 → model_class_id / Scientific name to model class id."""
    r = ref.execute(
        "SELECT model_class_id FROM BirdCountInfo WHERE scientific_name=?", (sci,)
    ).fetchone()
    return r[0] if r else None


def main() -> None:
    f = GeoFilter()
    if not f.is_available():
        print("FAIL: geo_distribution.db 不可用")
        sys.exit(1)
    ref = sqlite3.connect(os.path.join(PROJ, "birdid/data/bird_reference.sqlite"))

    def first_tier(lat, lon, cc=None):
        for cand, tier in f.iter_candidates(lat, lon, cc):
            return cand, tier
        return None, TIER_NONE

    ok = True

    # 验收 1: 冰岛/法罗——跨半球错误种必须被排除，真实种必须保留
    print("\n[1] 冰岛/法罗回归")
    for label, lat, lon in [("冰岛", 63.404, -19.103), ("法罗", 62.070, -7.257)]:
        cand, tier = first_tier(lat, lon, "IS")
        must_out = ["Eudyptula minor", "Sula nebouxii", "Fratercula corniculata"]
        must_in = ["Fratercula arctica", "Somateria mollissima"]
        bad = [s for s in must_out if (c := sci_to_class(ref, s)) is not None and c in cand]
        miss = [s for s in must_in if (c := sci_to_class(ref, s)) is not None and c not in cand]
        status = "OK" if not bad and not miss else "FAIL"
        ok &= status == "OK"
        print(f"  {label:6} {tier:18} 候选={len(cand):5} 误含={bad} 漏={miss} → {status}")

    # 验收 2: 悉尼引入种
    print("\n[2] 悉尼引入种")
    cand, tier = first_tier(-33.87, 151.21, "AU")
    intro = ["Passer domesticus", "Columba livia", "Sturnus vulgaris",
             "Turdus merula", "Acridotheres tristis"]
    miss = [s for s in intro if (c := sci_to_class(ref, s)) is not None and c not in cand]
    status = "OK" if not miss else "FAIL"
    ok &= status == "OK"
    print(f"  {tier:18} 候选={len(cand):5} 漏={miss} → {status}")

    # 验收 3: 空网格不落到 L5
    print("\n[3] 稀疏网格降级")
    cand, tier = first_tier(46.41, 127.5, "CN")
    status = "OK" if tier != TIER_NONE else "FAIL"
    ok &= status == "OK"
    print(f"  黑龙江 {tier:18} 候选={len(cand) if cand else 0} → {status}")

    # 验收 4: 原 AVONET 缺失的 391 类在其分布区可进入候选
    print("\n[4] 旧版永久缺失种")
    checks = [("Saxicola stejnegeri", 39.90, 116.40, "CN"),
              ("Anser serrirostris", 39.90, 116.40, "CN"),
              ("Todiramphus sordidus", -16.92, 145.77, "AU")]
    for sci, lat, lon, cc in checks:
        cid = sci_to_class(ref, sci)
        found = False
        for cand2, _ in f.iter_candidates(lat, lon, cc):
            if cand2 is None or (cid is not None and cid in cand2):
                found = cand2 is not None
                break
        status = "OK" if found else "FAIL"
        ok &= status == "OK"
        print(f"  {sci:26} → {status}")

    # 验收 5: 体积
    print("\n[5] 体积")
    size = os.path.getsize(f.db_path) / 1024 / 1024
    status = "OK" if size <= 25 else "FAIL"
    ok &= status == "OK"
    print(f"  geo_distribution.db = {size:.1f} MB (上限 25) → {status}")

    # 验收 6: 备份库全部 GPS 点都不掉到 L5
    print("\n[6] 433 张真实样本")
    if os.path.exists(BACKUP_DB):
        b = sqlite3.connect(BACKUP_DB)
        pts = b.execute(
            "SELECT DISTINCT gps_latitude, gps_longitude FROM photos "
            "WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL"
        ).fetchall()
        b.close()
        fell = sum(1 for la, lo in pts if first_tier(la, lo, "IS")[1] == TIER_NONE)
        status = "OK" if fell == 0 else "FAIL"
        ok &= status == "OK"
        print(f"  {len(pts)} 个坐标，掉到无过滤 {fell} 个 → {status}")
    else:
        print("  SKIP: 备份库不存在")

    print(f"\n总结 / Overall: {'全部通过 PASS' if ok else '存在失败 FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行验证**

```bash
.venv/bin/python scripts_dev/validate_geo_filter.py
```

预期：6 组全部 OK，退出码 0。任何 FAIL 都要回到对应任务修复——冰岛误含说明 L1 阈值过宽（回 Task 1 重标），悉尼漏引入种说明数据生成有误（回 Task 2 检查映射）。

- [ ] **Step 3: 跑完整测试套件**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -15
```

预期：无新增失败。

- [ ] **Step 4: 更新 spec 验收章节**

在 `docs/specs/2026-07-25-gbif-geo-filter-design.md` §8 每条标准后附实测结果与数字，把状态从「待评审」改为「已实施」。

- [ ] **Step 5: 提交**

```bash
git add scripts_dev/validate_geo_filter.py docs/specs/2026-07-25-gbif-geo-filter-design.md
git commit -m "test(geo): 补回归验证脚本并记录验收结果"
```

---

## 任务依赖 / Task Dependencies

```
Task 1 (标定阈值) ──┐
                    ├──> Task 2 (生成库) ──> Task 3 (geo_filter) ──> Task 4 (接线)
                    │                                                    │
                    │                            Task 5 (配置/国家列表) ──┤
                    │                                     │              │
                    │                            Task 6 (UI) ────────────┤
                    │                                                    │
                    └────────────────────────> Task 7 (清理) <───────────┘
                                                     │
                                                Task 8 (打包)
                                                     │
                                                Task 9 (回归验证)
```

Task 1 阻塞全部后续。Task 5 与 Task 6 可在 Task 4 完成后并行。Task 7 必须在 3/4/5/6 全部完成后进行，否则删除旧模块会破坏尚未迁移的调用点。
