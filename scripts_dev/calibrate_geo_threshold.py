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
