# -*- coding: utf-8 -*-
"""
构建 birdid/data/ebird_regions.db / Build birdid/data/ebird_regions.db.

从 eBird API 拉全球国家级与中澳美省州级物种清单，映射为模型类别编号，
连同 Natural Earth 行政区边界写入 SQLite。产物只含模型类别编号，不含 eBird
原始代码（eBird API 条款禁止以原始格式再分发，spec §2.3）。

用法 / Usage:
    EBIRD_API_KEY=... python3 -m scripts_dev.build_ebird_regions [--refresh]

Fetches national lists worldwide and subnational lists for CN/AU/US from the
eBird API, maps them to model class ids, and writes them with Natural Earth
boundaries into SQLite. Only class ids are stored, never raw eBird codes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts_dev.ebird_region_boundaries import (  # noqa: E402
    SUBNATIONAL_COUNTRIES,
    RingRow,
    admin0_rings,
    admin1_rings,
    find_traditional_names,
)
from scripts_dev.ebird_region_mapping import (  # noqa: E402
    SpeciesMapping,
    build_species_mapping,
    classes_for_region,
    find_overloaded_classes,
    load_model_index,
    normalize_to_species,
    parse_gbif_v2_match,
)

EBIRD_API = "https://api.ebird.org/v2/"
GBIF_MATCH = "https://api.gbif.org/v2/species/match"
NE_BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
NE_ADMIN0 = "ne_50m_admin_0_countries.geojson"
NE_ADMIN1 = "ne_10m_admin_1_states_provinces.geojson"

CACHE_DIR = os.path.join(PROJECT_ROOT, "scripts_dev", ".ebird_cache")
OUTPUT = os.path.join(PROJECT_ROOT, "birdid", "data", "ebird_regions.db")
REF_DB = os.path.join(PROJECT_ROOT, "birdid", "data", "bird_reference.sqlite")
OVERRIDES = os.path.join(PROJECT_ROOT, "scripts_dev", "ebird_overrides.json")
BUILDER_VERSION = "1"
MAX_DB_BYTES = 6 * 1024 * 1024

# 旧版误杀案例，缺任何一个都说明映射回退了（spec §2.2、§4.3）
# Species the legacy mapping masked; any miss means the mapping regressed.
SENTINELS: Tuple[Tuple[str, str], ...] = (
    ("AU-NSW", "Acanthiza pusilla"),
    ("AU-NSW", "Glossopsitta concinna"),
    ("AU-NSW", "Stiltia isabella"),
    ("CN-11", "Pardaliparus venustulus"),
    ("CN-11", "Grus virgo"),
    ("CN-11", "Charadrius dubius"),
    ("CN-11", "Charadrius mongolus"),
    ("US-CA", "Setophaga petechia"),
)

# Natural Earth 中文名的人工修正（繁体或不规范）/ Manual fixes for Natural Earth Chinese names.
SUBNATIONAL_ZH_OVERRIDES: Dict[str, str] = {
    "US-NE": "内布拉斯加州",
    "US-WV": "西弗吉尼亚州",
}

SCHEMA = """
CREATE TABLE regions (
    code TEXT PRIMARY KEY,
    parent TEXT,
    name_en TEXT NOT NULL,
    name_zh TEXT NOT NULL,
    species_count INTEGER NOT NULL
);
CREATE TABLE region_species (
    region TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    PRIMARY KEY (region, class_id)
) WITHOUT ROWID;
CREATE TABLE boundaries (
    region TEXT NOT NULL,
    level INTEGER NOT NULL,
    ring_id INTEGER NOT NULL,
    min_lat REAL NOT NULL,
    max_lat REAL NOT NULL,
    min_lon REAL NOT NULL,
    max_lon REAL NOT NULL,
    is_hole INTEGER NOT NULL,
    coords BLOB NOT NULL,
    PRIMARY KEY (region, level, ring_id)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def write_database(
    path: str,
    regions: List[Tuple[str, Optional[str], str, str, int]],
    region_species: Dict[str, Set[int]],
    rings: List[RingRow],
    meta: Dict[str, str],
) -> None:
    """
    原子地写出区域库 / Atomically write the region database.

    先写同目录临时文件再替换，构建中途失败不会留下半成品库。

    Writes a temp file in the same directory and then replaces the target, so a
    failed build never leaves a half-written database behind.

    参数 / Parameters:
        path (str): 输出路径 / Output path.
        regions (list[tuple]): (code, parent, name_en, name_zh, species_count)
        region_species (dict[str, set[int]]): 区域 → 类别 / Region to classes.
        rings (list[RingRow]): 边界行 / Boundary rows.
        meta (dict[str, str]): 元数据 / Metadata.

    异常 / Exceptions:
        sqlite3.Error / OSError: 写入失败时抛出 / Raised on write failure.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=directory)
    os.close(fd)
    os.remove(tmp)
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO regions VALUES (?,?,?,?,?)", regions)
            conn.executemany(
                "INSERT INTO region_species VALUES (?,?)",
                [(code, cls) for code, classes in sorted(region_species.items()) for cls in sorted(classes)],
            )
            conn.executemany(
                "INSERT INTO boundaries VALUES (?,?,?,?,?,?,?,?,?)",
                [(r.region, r.level, r.ring_id, r.min_lat, r.max_lat, r.min_lon, r.max_lon, r.is_hole, r.coords)
                 for r in rings],
            )
            conn.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def check_sentinels(
    region_species: Dict[str, Set[int]],
    class_by_sci: Dict[str, Set[int]],
    sentinels: Sequence[Tuple[str, str]],
) -> List[str]:
    """
    检查哨兵物种都在对应区域里 / Check every sentinel is present in its region.

    参数 / Parameters:
        region_species (dict): 区域 → 类别 / Region to classes.
        class_by_sci (dict): 模型学名 → 类别 / Model name to classes.
        sentinels (Sequence[tuple]): (区域, 模型学名) / (region, model name).

    返回 / Returns:
        list[str]: 每个缺失一条错误信息 / One message per missing sentinel.
    """
    errors: List[str] = []
    for region, sci in sentinels:
        classes = class_by_sci.get(sci, set())
        if not classes or not (classes & region_species.get(region, set())):
            errors.append(f"sentinel missing: {sci} not in {region}")
    return errors


def country_rows(
    countries: List[dict], region_species: Dict[str, Set[int]]
) -> Tuple[List[tuple], List[str]]:
    """
    生成国家行并找出缺中文名的国家 / Build country rows and list missing Chinese names.

    参数 / Parameters:
        countries (list[dict]): eBird 国家列表 [{code, name}] / eBird country list.
        region_species (dict): 区域 → 类别 / Region to classes.

    返回 / Returns:
        tuple: (行列表, 缺中文名的代码) / (rows, codes missing a Chinese name).
    """
    from tools.country_names import country_display_names

    rows: List[tuple] = []
    missing: List[str] = []
    for entry in countries:
        code = str(entry["code"])
        _english, chinese = country_display_names(code)
        if chinese == code:
            missing.append(code)
        rows.append((code, None, str(entry.get("name") or code), chinese, len(region_species.get(code, set()))))
    return rows, sorted(missing)


class CachedFetcher:
    """
    带磁盘缓存与重试的 JSON 抓取器 / JSON fetcher with disk cache and retries.

    缓存让「修覆盖表 → 重跑」不必重复请求 eBird/GBIF，也让构建可离线复现。

    The cache means "edit overrides, rerun" never refetches, and makes a build
    reproducible offline.

    参数 / Parameters:
        cache_dir (str): 缓存目录 / Cache directory.
        headers (dict): 请求头 / Request headers.
        refresh (bool): True 时忽略已有缓存 / Ignore existing cache when True.
        opener (Callable): 测试注入用，默认 urllib.request.urlopen / Injected for tests.
    """

    def __init__(
        self,
        cache_dir: str,
        headers: Dict[str, str],
        refresh: bool = False,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.headers = headers
        self.refresh = refresh
        self.opener = opener or urllib.request.urlopen

    def get_json(self, url: str, cache_name: str) -> Any:
        """
        读缓存或联网获取 JSON / Read JSON from cache or the network.

        参数 / Parameters:
            url (str): 请求地址 / URL.
            cache_name (str): 缓存相对路径 / Cache path relative to cache_dir.

        返回 / Returns:
            Any: 解析后的 JSON / Parsed JSON.

        异常 / Exceptions:
            RuntimeError: 三次重试均失败 / All three attempts failed.
        """
        path = os.path.join(self.cache_dir, cache_name)
        if not self.refresh and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers=self.headers)
                with self.opener(request, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"fetch failed: {url.split('?')[0]}: {last_error}")


def load_overrides(path: str) -> Tuple[Dict[str, List[int]], Set[int]]:
    """
    读取人工覆盖文件 / Load the manual override file.

    参数 / Parameters:
        path (str): ebird_overrides.json 路径 / Path to the overrides file.

    返回 / Returns:
        tuple: (speciesCode → 类别列表, 允许一类多种的类别集合) /
            (code to classes, classes allowed to map from many species).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mapping = {str(k): [int(v) for v in vs] for k, vs in (data.get("map") or {}).items()}
    allow = {int(v) for v in data.get("allow_multi") or []}
    return mapping, allow


def _write_report(
    path: str,
    taxonomy: Dict[str, dict],
    mapping: SpeciesMapping,
    region_lists: Dict[str, Set[str]],
    skipped_admin1: List[str],
    skipped_admin0: List[str],
    errors: List[str],
) -> None:
    """
    写映射报告供人工复核 / Write the mapping report for manual review.

    参数 / Parameters:
        path (str): 报告路径 / Report path.
        taxonomy (dict): 分类表 / Taxonomy.
        mapping (SpeciesMapping): 映射结果 / Mapping.
        region_lists (dict): 区域 → 物种级代码 / Region to species codes.
        skipped_admin1 (list[str]): 跳过的省州代码 / Skipped admin-1 codes.
        skipped_admin0 (list[str]): 跳过的国家代码 / Skipped admin-0 codes.
        errors (list[str]): 卡口错误 / Gate errors.
    """
    occurrences: Counter = Counter()
    for codes in region_lists.values():
        occurrences.update(codes)
    lines = ["# gate errors", *errors, "", "# step counts"]
    lines += [f"{step}: {count}" for step, count in sorted(Counter(mapping.steps.values()).items())]
    lines += ["", "# unmapped species that appear in at least one region (code | sciName | comName | regions)"]
    for code in sorted(mapping.unmapped, key=lambda c: -occurrences[c]):
        if occurrences[code]:
            e = taxonomy[code]
            lines.append(f"{code} | {e.get('sciName')} | {e.get('comName')} | {occurrences[code]}")
    lines += ["", "# epithet-step pairs (review these)"]
    for code, step in sorted(mapping.steps.items()):
        if step == "epithet":
            lines.append(f"{code} | {taxonomy[code].get('sciName')} -> {sorted(mapping.classes[code])}")
    lines += ["", "# skipped Natural Earth admin-1 codes", *skipped_admin1]
    lines += ["", "# Natural Earth countries absent from eBird", *skipped_admin0]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    """
    构建入口 / Build entry point.

    参数 / Parameters:
        argv (Optional[list[str]]): 命令行参数 / CLI arguments.

    返回 / Returns:
        int: 0 成功；1 卡口失败（不写库）；2 缺少 API key /
            0 success; 1 gate failure (no DB written); 2 missing API key.
    """
    parser = argparse.ArgumentParser(description="Build birdid/data/ebird_regions.db")
    parser.add_argument("--refresh", action="store_true", help="ignore the download cache")
    parser.add_argument("--output", default=OUTPUT)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    args = parser.parse_args(argv)

    api_key = os.environ.get("EBIRD_API_KEY", "").strip()
    if not api_key:
        print("EBIRD_API_KEY is not set")
        return 2

    ebird = CachedFetcher(args.cache_dir, {"X-eBirdApiToken": api_key}, refresh=args.refresh)
    plain = CachedFetcher(args.cache_dir, {"User-Agent": "SuperPicky-build"}, refresh=args.refresh)

    taxonomy_list = plain.get_json(EBIRD_API + "ref/taxonomy/ebird?fmt=json", "taxonomy.json")
    taxonomy = {e["speciesCode"]: e for e in taxonomy_list}
    versions = ebird.get_json(EBIRD_API + "ref/taxonomy/versions", "taxonomy_versions.json")
    latest = next((v for v in versions if v.get("latest")), versions[-1] if versions else {})

    countries = ebird.get_json(EBIRD_API + "ref/region/list/country/world", "countries.json")
    subnationals: List[dict] = []
    for country in SUBNATIONAL_COUNTRIES:
        for entry in ebird.get_json(EBIRD_API + f"ref/region/list/subnational1/{country}", f"sub_{country}.json"):
            subnationals.append({"code": entry["code"], "name": entry["name"], "parent": country})

    region_lists: Dict[str, Set[str]] = {}
    for entry in countries + subnationals:
        code = entry["code"]
        raw = ebird.get_json(EBIRD_API + f"product/spplist/{code}", f"spplist/{code}.json")
        region_lists[code] = normalize_to_species(raw, taxonomy)
        print(f"list {code}: {len(raw)} -> {len(region_lists[code])} species")

    def resolve_gbif(sci_name: str) -> Optional[int]:
        query = urllib.parse.urlencode({"scientificName": sci_name, "class": "Aves"})
        cache = "gbif/" + sci_name.replace(" ", "_").replace("/", "_") + ".json"
        return parse_gbif_v2_match(plain.get_json(f"{GBIF_MATCH}?{query}", cache))

    index = load_model_index(REF_DB)
    overrides, allow_multi = load_overrides(OVERRIDES)
    mapping = build_species_mapping(taxonomy, index, resolve_gbif, overrides)
    region_species = {code: classes_for_region(codes, mapping) for code, codes in region_lists.items()}

    admin0 = plain.get_json(NE_BASE + NE_ADMIN0, "ne/" + NE_ADMIN0)["features"]
    admin1 = plain.get_json(NE_BASE + NE_ADMIN1, "ne/" + NE_ADMIN1)["features"]
    country_codes = {e["code"] for e in countries}
    sub_codes = {e["code"] for e in subnationals}
    rows0, skipped0 = admin0_rings(admin0, country_codes)
    rows1, names_zh, skipped1 = admin1_rings(admin1, sub_codes)
    names_zh.update(SUBNATIONAL_ZH_OVERRIDES)

    errors: List[str] = []
    errors += [f"empty species list: {code}" for code, classes in sorted(region_species.items()) if not classes]
    errors += [f"no boundary: {code}" for code in sorted(sub_codes - {r.region for r in rows1})]
    errors += [f"no country boundary: {code}" for code in SUBNATIONAL_COUNTRIES
               if code not in {r.region for r in rows0}]
    errors += [f"class {cls} mapped from {codes}" for cls, codes in
               sorted(find_overloaded_classes(mapping, allow_multi).items())]
    errors += check_sentinels(region_species, index.by_sci, SENTINELS)
    regions, missing_zh = country_rows(countries, region_species)
    errors += [f"country missing Chinese name (add to tools/country_names.py): {code}" for code in missing_zh]
    for entry in subnationals:
        name = names_zh.get(entry["code"], "")
        if not name:
            errors.append(f"subnational missing Chinese name: {entry['code']}")
        regions.append((entry["code"], entry["parent"], entry["name"], name,
                        len(region_species.get(entry["code"], set()))))
    errors += [f"traditional characters (add to SUBNATIONAL_ZH_OVERRIDES): {code} {name}"
               for code, name in sorted(find_traditional_names(names_zh).items())]

    report = os.path.join(args.cache_dir, "mapping_report.txt")
    _write_report(report, taxonomy, mapping, region_lists, skipped1, skipped0, errors)
    print(f"steps: {dict(Counter(mapping.steps.values()))}; unmapped: {len(mapping.unmapped)}")
    print(f"report: {report}")
    if errors:
        print(f"{len(errors)} gate error(s):")
        for message in errors:
            print("  " + message)
        return 1

    meta = {
        "taxonomy_version": str(latest.get("authorityVer", "")),
        "fetched_at": datetime.date.today().isoformat(),
        "ebird_terms_url": "https://www.birds.cornell.edu/home/ebird-api-terms-of-use/",
        "attribution": "eBird, Cornell Lab of Ornithology; derived class-id lists, eBird API Terms of Use apply",
        "boundaries_source": f"Natural Earth v5.1.2 (public domain): {NE_ADMIN0}, {NE_ADMIN1}",
        "builder_version": BUILDER_VERSION,
    }
    write_database(args.output, regions, region_species, rows0 + rows1, meta)
    size = os.path.getsize(args.output)
    print(f"wrote {args.output}: {size / 1024 / 1024:.2f} MB, {len(regions)} regions")
    if size > MAX_DB_BYTES:
        print(f"database exceeds {MAX_DB_BYTES} bytes")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
