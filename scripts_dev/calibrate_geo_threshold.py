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

import functools
import json
import os
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


# 标定实际使用的 60 个采样网格 (south, west)。
# 原先由 `random.seed(2026)` + `random.sample()` 从 avonet.db 的 places 表抽取，
# 依赖该表的行顺序才能复现；avonet.db 已随 GBIF 迁移删除，故把当时抽中的坐标
# 固化于此，使 spec §5.1 记录的数字始终可复现。
# The 60 sampled cells (south, west) actually used for calibration. They were
# drawn with `random.seed(2026)` + `random.sample()` from avonet.db's places
# table and therefore depended on that table's row order; avonet.db was removed
# in the GBIF migration, so the drawn coordinates are frozen here to keep the
# numbers recorded in spec section 5.1 reproducible.
SAMPLE_CELLS: List[Tuple[float, float]] = [
    (-11.42, -53.0), (29.2, 98.0), (60.26, 146.0),
    (61.81, 159.0), (-16.09, 30.0), (11.42, 41.0),
    (73.67, -107.0), (46.41, 84.0), (69.0, 107.0),
    (58.78, 31.0), (49.78, 81.0), (15.3, 32.0),
    (-87.13, -37.0), (-20.87, -55.0), (-13.74, -59.0),
    (24.14, 35.0), (-16.88, -53.0), (50.96, 120.0),
    (-71.19, -67.0), (58.78, 11.0), (28.34, 98.0),
    (9.11, -76.0), (43.23, -107.0), (17.67, 26.0),
    (34.53, 0.0), (35.45, 136.0), (39.23, 35.0),
    (63.43, -139.0), (-22.49, 144.0), (32.72, 108.0),
    (-19.26, 141.0), (73.67, -71.0), (67.0, 80.0),
    (24.97, 38.0), (24.14, 48.0), (52.17, 130.0),
    (-6.82, 122.0), (27.49, 101.0), (-56.0, -67.0),
    (38.27, -98.0), (37.32, -92.0), (53.41, -1.0),
    (46.41, 137.0), (-18.46, -64.0), (43.23, -2.0),
    (71.19, 104.0), (60.26, 13.0), (-12.19, -49.0),
    (47.51, 20.0), (61.81, -70.0), (58.78, 36.0),
    (43.23, -77.0), (65.16, -92.0), (19.26, -70.0),
    (46.41, 63.0), (76.58, -37.0), (56.0, 128.0),
    (63.43, -160.0), (63.43, 50.0), (-54.68, -36.0),
]


def main() -> None:
    key2cls = load_key_to_class()
    sample = list(SAMPLE_CELLS)

    # "cumulative" 是硬要求判定失败后按 Step 3 指示放宽 cover 到 0.999 重跑的
    # 结果（spec §5.1 采用的正是这一版本）；"cumulative_995" 保留初次实测的
    # cover=0.995 版本，spec 同时记录了两轮数字，脚本须能各自复现。
    # "cumulative" is the cover=0.999 rerun mandated by Step 3 after the hard
    # requirement failed at cover=0.995 (this is the version spec §5.1 adopts);
    # "cumulative_995" keeps the first-round cover=0.995 result so the script
    # can reproduce both numbers the spec cites.
    strategies = {
        "absolute": strategy_absolute,
        "cumulative_995": functools.partial(strategy_cumulative, cover=0.995),
        "cumulative": functools.partial(strategy_cumulative, cover=0.999),
        "hybrid": strategy_hybrid,
    }
    stats: Dict[str, List[int]] = {name: [] for name in strategies}
    violations: Dict[str, int] = {name: 0 for name in strategies}
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
    print(f"{'方案':<16}{'中位':>7}{'均值':>8}{'P10':>7}{'P90':>7}{'top30违规格数':>14}")
    for name in strategies:
        v = sorted(stats[name])
        if not v:
            continue
        p10 = v[max(0, int(len(v) * 0.10) - 1)]
        p90 = v[min(len(v) - 1, int(len(v) * 0.90))]
        print(
            f"{name:<16}{statistics.median(v):>7.0f}{statistics.mean(v):>8.0f}"
            f"{p10:>7}{p90:>7}{violations[name]:>14}"
        )


if __name__ == "__main__":
    main()
