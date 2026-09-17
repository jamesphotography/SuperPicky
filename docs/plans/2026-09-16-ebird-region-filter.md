# eBird 区域过滤 实施计划 / eBird Region Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 eBird 区域清单（中澳美省州级 + 全球国家级）替换 GBIF 1° 网格地理过滤，并删除 35 MB 的 `geo_distribution.db`。

**Architecture:** 开发者本机运行构建脚本，从 eBird API 拉清单、映射成模型类别编号，连同 Natural Earth 行政区边界写入 `birdid/data/ebird_regions.db`。运行时 `region_locator` 用纯 Python 点在多边形判断把 GPS 定位到国家/省州，`geo_filter.RegionFilter` 按「省州 → 国家 → 不过滤」产出候选集，`identify_bird` 逐层放宽。

**Tech Stack:** Python 3（本机为系统 `python3`，见下方约束）、sqlite3、zlib、struct、urllib、pytest、PySide6（UI 测试 offscreen）。

**Spec:** `docs/specs/2026-09-16-ebird-region-filter-design.md`

## Global Constraints

- 所有 Python 命令用 `python3`（本机真实环境是系统 python3.13；`.venv` 是僵尸环境，勿用、勿重建）。
- 测试命令：`python3 -m pytest <file> -v`；每个改动的 Python 文件都要过 `python3 -m py_compile <file>`。
- `.gitignore` 忽略了 `scripts_dev/` 与根目录 `test_*.py`：这些文件提交时必须 `git add -f`。
- 文件读写一律 UTF-8；**不要用 sed/awk 改含中文的文件**，用 Edit 工具或 Python。
- 注释与 docstring：中文 + 对应英文，函数写参数/返回/异常。
- 不引入新的第三方运行时依赖（不加 shapely、reverse_geocoder、pycountry）。
- eBird API key 只从环境变量 `EBIRD_API_KEY` 读取，不得写入任何文件、日志、提交。
- 新库只存模型类别编号，**不得存 eBird speciesCode**（许可：派生数据，spec §2.3）。
- 省州级只覆盖 `CN`、`AU`、`US`（常量 `SUBNATIONAL_COUNTRIES`）。
- 离岸容差 `OFFSHORE_TOLERANCE_KM = 370.0`。
- 边界坐标量化精度 0.01°（`SCALE = 100`）。
- 层标签：`region_subnational` / `region_country` / `none`。
- `ebird_regions.db` 体积上限 6 MB。
- 测试不得读写开发者真实 `advanced_config.json`（用 `AdvancedConfig(config_file=tmp)` + monkeypatch `get_advanced_config`）。
- 提交信息末尾加：`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`。

## 文件结构 / File Map

| 文件 | 动作 | 职责 |
|---|---|---|
| `birdid/region_geometry.py` | 新建 | 纯几何：量化、打包/解包环、点在环内、点到环距离（运行时 + 构建共用） |
| `scripts_dev/ebird_region_mapping.py` | 新建 | eBird 分类归并 + speciesCode→模型类别映射（五步）+ 映射卡口 |
| `scripts_dev/ebird_region_boundaries.py` | 新建 | Natural Earth 要素 → 边界行；中国代码对照；中文名选取与繁体检查 |
| `scripts_dev/build_ebird_regions.py` | 新建 | 抓取（带缓存）、组装、全部卡口、写库、CLI |
| `scripts_dev/ebird_overrides.json` | 新建 | 人工映射覆盖 `map` / 允许一类多种 `allow_multi` |
| `birdid/data/ebird_regions.db` | 新建（构建产物） | regions / region_species / boundaries / meta |
| `birdid/region_locator.py` | 新建 | GPS → (国家, 省州)，带缓存的单例 |
| `birdid/geo_filter.py` | 重写 | `RegionFilter`、层常量、`describe_tier`、`get_geo_filter`、`default_db_path` |
| `birdid/bird_identifier.py` | 修改 | 去掉 reverse_geocoder；GPS 定位与手选分开传入过滤器 |
| `birdid_server.py` / `birdid_cli.py` / `ui/birdid_dock.py` | 修改 | 新 geo_info 键与告警条件 |
| `core/region_data.py` | 修改 | 从新库读国家与省州 |
| `ui/settings_center.py` | 修改 | 提示文案注释、署名读新 meta |
| `locales/zh_CN.json` / `locales/en_US.json` | 修改 | 层文案、提示、署名、日志键 |
| `SuperPicky.spec` / `SuperPicky_win64.spec` | 修改 | hiddenimports |
| `scripts_dev/validate_ebird_regions.py` | 新建 | 验收脚本（哨兵、体积、性能、遗留引用、report.db 对比） |
| 删除 | — | `birdid/data/geo_distribution.db`、`birdid/data/land_cells.json`、`scripts_dev/build_geo_distribution.py`、`scripts_dev/calibrate_geo_threshold.py`、`scripts_dev/validate_geo_filter.py`、`test_geo_filter.py` |

测试文件：`test_region_geometry.py`、`test_ebird_region_mapping.py`、`test_ebird_region_boundaries.py`、`test_build_ebird_regions.py`、`test_region_locator.py`、`test_region_locator_real_db.py`、`test_region_filter.py`、改写 `test_geo_filter_wiring.py`、`test_geo_config_migration.py`、`test_settings_center.py`。

---

### Task 1: 纯几何模块 `birdid/region_geometry.py`

**Files:**
- Create: `birdid/region_geometry.py`
- Test: `test_region_geometry.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SCALE: int = 100`
  - `quantize_ring(coords: Sequence[Sequence[float]]) -> List[Tuple[int, int]]`（输入 GeoJSON 的 `[lon, lat]` 序列，输出 `(lon*100, lat*100)` 整数点，去连续重复与闭合重复点）
  - `pack_ring(points: Sequence[Tuple[int, int]]) -> bytes`
  - `unpack_ring(blob: bytes) -> List[Tuple[int, int]]`
  - `ring_bbox(points) -> Tuple[float, float, float, float]`（`min_lat, max_lat, min_lon, max_lon`，单位度）
  - `point_in_ring(lat: float, lon: float, points) -> bool`
  - `distance_to_ring_km(lat: float, lon: float, points) -> float`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
区域几何工具单测 / Unit tests for region geometry helpers.
"""
import pytest

from birdid.region_geometry import (
    distance_to_ring_km,
    pack_ring,
    point_in_ring,
    quantize_ring,
    ring_bbox,
    unpack_ring,
)

SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]


def test_quantize_drops_duplicates_and_closing_point():
    """连续重复点与首尾闭合点都应去掉 / Drop consecutive dupes and the closing point."""
    pts = quantize_ring([[0.001, 0.001], [0.002, 0.002], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]])
    assert pts == [(0, 0), (100, 0), (100, 100)]


def test_pack_roundtrip():
    """打包再解包得到原点列 / Pack then unpack returns the same points."""
    pts = quantize_ring(SQUARE)
    assert unpack_ring(pack_ring(pts)) == pts


def test_bbox_in_degrees():
    """外包框以度为单位 / Bounding box is expressed in degrees."""
    assert ring_bbox(quantize_ring(SQUARE)) == (0.0, 10.0, 0.0, 10.0)


def test_point_in_ring():
    """内部为真、外部为假 / Inside is True, outside is False."""
    pts = quantize_ring(SQUARE)
    assert point_in_ring(5.0, 5.0, pts) is True
    assert point_in_ring(11.0, 5.0, pts) is False
    assert point_in_ring(5.0, -0.5, pts) is False


def test_distance_one_degree_south():
    """正南 1° 距离约 110.57 km / One degree due south is about 110.57 km."""
    pts = quantize_ring(SQUARE)
    assert distance_to_ring_km(-1.0, 5.0, pts) == pytest.approx(110.574, abs=0.5)


def test_distance_inside_point_is_to_nearest_edge():
    """内部点到最近边的距离 / Inside point measures to the nearest edge."""
    pts = quantize_ring(SQUARE)
    assert distance_to_ring_km(9.0, 5.0, pts) == pytest.approx(110.574, abs=0.5)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_region_geometry.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'birdid.region_geometry'`

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""
区域边界几何工具 / Geometry helpers for region boundaries.

边界环以 0.01° 精度量化为整数点 (lon*100, lat*100)，zlib 压缩后存入 ebird_regions.db。
本模块只做纯计算，构建脚本与运行时定位器共用，保证编码与解码永远一致。

Boundary rings are quantized to integer points (lon*100, lat*100) at 0.01
degree precision and stored zlib-compressed in ebird_regions.db. This module is
pure computation shared by the build script and the runtime locator, so the
encoder and decoder can never drift apart.
"""
from __future__ import annotations

import math
import struct
import zlib
from typing import List, Sequence, Tuple

SCALE = 100
_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LON_EQUATOR = 111.320

Point = Tuple[int, int]


def quantize_ring(coords: Sequence[Sequence[float]]) -> List[Point]:
    """
    把 GeoJSON 环量化为整数点 / Quantize a GeoJSON ring into integer points.

    去掉量化后连续重复的点，以及与首点相同的闭合尾点。

    Removes points that become consecutive duplicates after quantization, and
    the closing point that repeats the first one.

    参数 / Parameters:
        coords (Sequence[Sequence[float]]): GeoJSON 的 [lon, lat] 序列 / [lon, lat] pairs.

    返回 / Returns:
        list[tuple[int, int]]: (lon*100, lat*100) 点列，可能少于 3 点 /
            Quantized points; may contain fewer than three points.
    """
    out: List[Point] = []
    prev = None
    for pair in coords:
        p = (int(round(float(pair[0]) * SCALE)), int(round(float(pair[1]) * SCALE)))
        if p != prev:
            out.append(p)
            prev = p
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def pack_ring(points: Sequence[Point]) -> bytes:
    """
    打包为 zlib 压缩的小端 int32 序列 / Pack as zlib-compressed little-endian int32.

    参数 / Parameters:
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        bytes: 压缩后的字节串 / Compressed bytes.
    """
    flat = [v for p in points for v in p]
    return zlib.compress(struct.pack(f"<{len(flat)}i", *flat), 9)


def unpack_ring(blob: bytes) -> List[Point]:
    """
    解包 pack_ring 的输出 / Unpack the output of pack_ring.

    参数 / Parameters:
        blob (bytes): 压缩字节串 / Compressed bytes.

    返回 / Returns:
        list[tuple[int, int]]: 量化点列 / Quantized points.

    异常 / Exceptions:
        zlib.error / struct.error: 数据损坏时抛出 / Raised on corrupt data.
    """
    raw = zlib.decompress(blob)
    flat = struct.unpack(f"<{len(raw) // 4}i", raw)
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


def ring_bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    """
    环的外包框（度）/ Bounding box of a ring in degrees.

    参数 / Parameters:
        points (Sequence[tuple[int, int]]): 量化点列（非空）/ Non-empty quantized points.

    返回 / Returns:
        tuple: (min_lat, max_lat, min_lon, max_lon)
    """
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lats) / SCALE, max(lats) / SCALE, min(lons) / SCALE, max(lons) / SCALE)


def point_in_ring(lat: float, lon: float, points: Sequence[Point]) -> bool:
    """
    射线法判断点是否在环内 / Ray-casting point-in-ring test.

    参数 / Parameters:
        lat (float): 纬度 / Latitude.
        lon (float): 经度 / Longitude.
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        bool: 在环内为 True / True when inside.
    """
    x = lon * SCALE
    y = lat * SCALE
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def distance_to_ring_km(lat: float, lon: float, points: Sequence[Point]) -> float:
    """
    点到环边界的最短距离（km，局部等距投影近似）/ Shortest distance to a ring edge.

    在点所在纬度做等距矩形投影，数百公里内误差可忽略，足以判断离岸容差。

    Uses an equirectangular projection at the point's latitude; the error is
    negligible within a few hundred kilometres, which is all the offshore
    tolerance needs.

    参数 / Parameters:
        lat (float): 纬度 / Latitude.
        lon (float): 经度 / Longitude.
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        float: 距离 km；空环返回 inf / Distance in km; inf for an empty ring.
    """
    if not points:
        return math.inf
    kx = _KM_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)) / SCALE
    ky = _KM_PER_DEG_LAT / SCALE
    px = lon * SCALE * kx
    py = lat * SCALE * ky
    best = math.inf
    n = len(points)
    for i in range(n):
        ax, ay = points[i][0] * kx, points[i][1] * ky
        bx, by = points[(i + 1) % n][0] * kx, points[(i + 1) % n][1] * ky
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
    return best
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_region_geometry.py -v && python3 -m py_compile birdid/region_geometry.py`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add birdid/region_geometry.py
git add -f test_region_geometry.py
git commit -m "feat(birdid): 区域边界几何工具（量化/打包/点在环内/离岸距离）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 物种映射库 `scripts_dev/ebird_region_mapping.py`

映射顺序（**比 spec §4.2 多一步「同科种加词」、人工覆盖提到最前**，本任务同步改 spec）：

1. `override`：`ebird_overrides.json` 的 `map`，人工结论优先于一切自动结果；
2. `cornell`：`avilist_map.cornell_code`；
3. `sci`：`BirdCountInfo.scientific_name` 精确匹配；
4. `gbif`：GBIF v2 species match 得到的 accepted key 命中 `gbif_rarity_100.specieskey`；
5. `epithet`：在**未被前四步占用的模型类别**中，找「同科 + 种加词词干相同」且双方都唯一的配对（处理属名调整，如 `Thinornis dubius` ↔ `Charadrius dubius`；设计阶段实测 111 例全部正确，不加同科约束则会出现 `Crithagra`↔`Nystalus` 这类错配）。

**Files:**
- Create: `scripts_dev/ebird_region_mapping.py`
- Modify: `docs/specs/2026-09-16-ebird-region-filter-design.md`（§4.2、§4.3）
- Test: `test_ebird_region_mapping.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `EXCLUDED_CATEGORIES: frozenset[str]`
  - `normalize_to_species(codes: Iterable[str], taxonomy: Dict[str, dict]) -> Set[str]`
  - `epithet_stem(sci_name: str) -> str`
  - `@dataclass ModelIndex(by_cornell: Dict[str, Set[int]], by_sci: Dict[str, Set[int]], by_gbif_key: Dict[int, Set[int]], class_sci: Dict[int, str], genus_family: Dict[str, str])`
  - `load_model_index(ref_db_path: str) -> ModelIndex`
  - `parse_gbif_v2_match(payload: dict) -> Optional[int]`
  - `@dataclass SpeciesMapping(classes: Dict[str, Set[int]], steps: Dict[str, str], unmapped: List[str])`
  - `build_species_mapping(taxonomy: Dict[str, dict], index: ModelIndex, resolve_gbif: Callable[[str], Optional[int]], overrides: Dict[str, List[int]]) -> SpeciesMapping`
  - `classes_for_region(species_codes: Iterable[str], mapping: SpeciesMapping) -> Set[int]`
  - `find_overloaded_classes(mapping: SpeciesMapping, allow: Set[int], limit: int = 3) -> Dict[int, List[str]]`

`taxonomy` 的形状：`{speciesCode: {"speciesCode", "sciName", "comName", "category", "reportAs"?, "familySciName"?}}`（即 eBird `/v2/ref/taxonomy/ebird` 列表按 `speciesCode` 建索引）。

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
eBird 物种映射单测 / Unit tests for eBird species mapping.

用临时 SQLite 模拟 bird_reference.sqlite 的四张表，覆盖五个映射步骤与卡口。
Builds a temporary stand-in for bird_reference.sqlite covering all five
mapping steps and the gates.
"""
import sqlite3

import pytest

from scripts_dev.ebird_region_mapping import (
    build_species_mapping,
    classes_for_region,
    epithet_stem,
    find_overloaded_classes,
    load_model_index,
    normalize_to_species,
    parse_gbif_v2_match,
)

TAXONOMY = {
    "brotho1": {"speciesCode": "brotho1", "sciName": "Acanthiza pusilla", "category": "species", "familySciName": "Acanthizidae"},
    "grucra1": {"speciesCode": "grucra1", "sciName": "Grus virgo", "category": "species", "familySciName": "Gruidae"},
    "auspra1": {"speciesCode": "auspra1", "sciName": "Glareola isabella", "category": "species", "familySciName": "Glareolidae"},
    "lirplo": {"speciesCode": "lirplo", "sciName": "Thinornis dubius", "category": "species", "familySciName": "Charadriidae"},
    "kilplo": {"speciesCode": "kilplo", "sciName": "Charadrius vociferus", "category": "species", "familySciName": "Charadriidae"},
    "nomodel1": {"speciesCode": "nomodel1", "sciName": "Nullus absentis", "category": "species", "familySciName": "Nullidae"},
    "brotho1x": {"speciesCode": "brotho1x", "sciName": "Acanthiza pusilla diemenensis", "category": "issf", "reportAs": "brotho1"},
    "hybr1": {"speciesCode": "hybr1", "sciName": "A x B", "category": "hybrid"},
    "thorn1": {"speciesCode": "thorn1", "sciName": "Acanthiza sp.", "category": "spuh"},
}


@pytest.fixture
def ref_db(tmp_path):
    """
    构造最小参考库 / Build a minimal reference database.

    类别 1 褐刺嘴莺（cornell 命中）、2 蓑羽鹤（学名命中）、3 澳洲燕鸻（GBIF 命中）、
    4 金眶鸻（种加词命中，模型仍用旧属名 Charadrius）、5 双领鸻（学名命中，
    用来提供 Charadrius → Charadriidae 的属科关系）。
    """
    p = tmp_path / "ref.sqlite"
    db = sqlite3.connect(str(p))
    db.executescript(
        """
        CREATE TABLE avilist_map (model_class_id INTEGER, cornell_code TEXT, family TEXT);
        CREATE TABLE BirdCountInfo (model_class_id INTEGER, scientific_name TEXT);
        CREATE TABLE gbif_rarity_100 (model_class_id INTEGER, specieskey INTEGER);
        CREATE TABLE bird_ioc (genus TEXT, family_scientific TEXT);
        """
    )
    db.executemany("INSERT INTO avilist_map VALUES (?,?,?)", [(1, "brotho1", "Acanthizidae"), (None, "lirplo", None)])
    db.executemany(
        "INSERT INTO BirdCountInfo VALUES (?,?)",
        [(1, "Acanthiza pusilla"), (2, "Grus virgo"), (3, "Stiltia isabella"),
         (4, "Charadrius dubius"), (5, "Charadrius vociferus")],
    )
    db.executemany("INSERT INTO gbif_rarity_100 VALUES (?,?)", [(3, 2480749)])
    db.commit()
    db.close()
    return str(p)


def fake_gbif(sci_name):
    """只有澳洲燕鸻能解析到 accepted key / Only the pratincole resolves."""
    return 2480749 if sci_name == "Glareola isabella" else None


def test_normalize_rolls_up_and_excludes():
    """亚种归并到物种，杂交与属级记录剔除 / Roll issf up; drop hybrid and spuh."""
    out = normalize_to_species(["brotho1x", "hybr1", "thorn1", "grucra1", "unknown"], TAXONOMY)
    assert out == {"brotho1", "grucra1"}


@pytest.mark.parametrize(
    "a,b",
    [("Trichoglossus concinnus", "Glossopsitta concinna"),
     ("Emblema modestum", "Aidemosyne modesta"),
     ("Idiopsar speculifer", "Idiopsar speculiferus")],
)
def test_epithet_stem_ignores_gender_endings(a, b):
    """词尾阴阳性不同也视为同一词干 / Gender endings map to the same stem."""
    assert epithet_stem(a) == epithet_stem(b)


def test_parse_gbif_synonym_returns_accepted_key():
    """同物异名返回 accepted key / A synonym yields the accepted key."""
    payload = {
        "usage": {"key": "111", "rank": "SPECIES", "status": "SYNONYM"},
        "acceptedUsage": {"key": "2480749"},
        "diagnostics": {"matchType": "EXACT"},
    }
    assert parse_gbif_v2_match(payload) == 2480749


def test_parse_gbif_rejects_higherrank():
    """只匹配到属时不可用 / A genus-level match is rejected."""
    payload = {"usage": {"key": "2480339", "rank": "GENUS"}, "diagnostics": {"matchType": "HIGHERRANK"}}
    assert parse_gbif_v2_match(payload) is None


def test_mapping_steps(ref_db):
    """五个步骤各自命中 / Each of the five steps hits."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    assert m.classes["brotho1"] == {1} and m.steps["brotho1"] == "cornell"
    assert m.classes["grucra1"] == {2} and m.steps["grucra1"] == "sci"
    assert m.classes["auspra1"] == {3} and m.steps["auspra1"] == "gbif"
    assert m.classes["lirplo"] == {4} and m.steps["lirplo"] == "epithet"
    assert m.steps["kilplo"] == "sci"
    assert m.unmapped == ["nomodel1"]


def test_override_wins(ref_db):
    """人工覆盖优先于自动结果 / Overrides beat automatic steps."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={"grucra1": [5]})
    assert m.classes["grucra1"] == {5} and m.steps["grucra1"] == "override"


def test_classes_for_region(ref_db):
    """区域清单合并为类别集合，未映射的忽略 / Region lists union to classes."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    assert classes_for_region(["brotho1", "lirplo", "nomodel1"], m) == {1, 4}


def test_overloaded_classes(ref_db):
    """一个类别被映射到超过 3 个物种要报出，白名单除外 / Flag >3 species per class."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    for i in range(4):
        m.classes[f"x{i}"] = {9}
    assert set(find_overloaded_classes(m, allow=set())) == {9}
    assert find_overloaded_classes(m, allow={9}) == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_ebird_region_mapping.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'scripts_dev.ebird_region_mapping'`

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""
eBird 物种代码 → 模型类别映射 / Map eBird species codes to model class ids.

旧版 ebird_classid_mapping.json 有 42 个类别被误映射为 ostric2、大量旧代码失配，
导致本地常见鸟被永久屏蔽（spec §2.2）。本模块按五个步骤逐级映射，并提供卡口函数，
让同类错误在构建期暴露而不是在用户手里静默发生。

The legacy mapping mis-mapped 42 classes to ostric2 and carried stale codes,
permanently masking common local birds (spec section 2.2). This module maps in
five ordered steps and exposes gate helpers so that class of error surfaces at
build time instead of silently in users' hands.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

# 不代表单一物种的记录类型：杂交、属级、「A/B」二选一、过渡型
# Record types that do not denote a single species.
EXCLUDED_CATEGORIES = frozenset({"hybrid", "spuh", "slash", "intergrade"})

# 词尾阴阳性变化：属名调整后种加词会随属的性改变词尾（concinna → concinnus）
# Gender endings: moving a species to a new genus can change the epithet ending.
_EPITHET_SUFFIXES = ("us", "um", "is", "ae", "a", "e", "i")
_MIN_STEM = 4


def normalize_to_species(codes: Iterable[str], taxonomy: Dict[str, dict]) -> Set[str]:
    """
    把区域清单归并到物种级 / Reduce a region list to species-level codes.

    参数 / Parameters:
        codes (Iterable[str]): 清单中的 speciesCode / Codes from a region list.
        taxonomy (dict): speciesCode → 分类条目 / Taxonomy entries by code.

    返回 / Returns:
        set[str]: 物种级 speciesCode；分类表里查不到的代码被忽略 /
            Species-level codes; codes missing from the taxonomy are ignored.
    """
    out: Set[str] = set()
    for code in codes:
        entry = taxonomy.get(code)
        if entry is None:
            continue
        category = entry.get("category")
        if category == "species":
            out.add(code)
            continue
        if category in EXCLUDED_CATEGORIES:
            continue
        target = entry.get("reportAs")
        if target and taxonomy.get(target, {}).get("category") == "species":
            out.add(target)
    return out


def epithet_stem(sci_name: str) -> str:
    """
    种加词去掉阴阳性词尾后的词干 / Epithet stem without gender endings.

    参数 / Parameters:
        sci_name (str): 学名 / Scientific name.

    返回 / Returns:
        str: 小写词干 / Lower-case stem.
    """
    epithet = sci_name.strip().split()[-1].lower()
    for suffix in _EPITHET_SUFFIXES:
        if epithet.endswith(suffix) and len(epithet) - len(suffix) >= _MIN_STEM:
            return epithet[: -len(suffix)]
    return epithet


@dataclass
class ModelIndex:
    """
    模型类别的各种查找索引 / Lookup indexes over model classes.

    属性 / Attributes:
        by_cornell: cornell_code → 类别集合 / cornell code to classes.
        by_sci: 模型学名 → 类别集合 / model scientific name to classes.
        by_gbif_key: GBIF specieskey → 类别集合 / GBIF key to classes.
        class_sci: 类别 → 模型学名 / class to model scientific name.
        genus_family: IOC 属 → 科，作 eBird 属科表的兜底 / IOC genus to family fallback.
    """

    by_cornell: Dict[str, Set[int]] = field(default_factory=dict)
    by_sci: Dict[str, Set[int]] = field(default_factory=dict)
    by_gbif_key: Dict[int, Set[int]] = field(default_factory=dict)
    class_sci: Dict[int, str] = field(default_factory=dict)
    genus_family: Dict[str, str] = field(default_factory=dict)


def load_model_index(ref_db_path: str) -> ModelIndex:
    """
    从 bird_reference.sqlite 读取映射所需索引 / Load indexes from bird_reference.sqlite.

    参数 / Parameters:
        ref_db_path (str): 参考库路径 / Reference database path.

    返回 / Returns:
        ModelIndex: 索引集合 / The indexes.

    异常 / Exceptions:
        sqlite3.Error: 表缺失或库损坏时抛出 / Raised when tables are missing.
    """
    index = ModelIndex()
    conn = sqlite3.connect(ref_db_path)
    try:
        by_cornell: Dict[str, Set[int]] = defaultdict(set)
        for code, cls in conn.execute(
            "SELECT cornell_code, model_class_id FROM avilist_map "
            "WHERE model_class_id IS NOT NULL AND cornell_code IS NOT NULL AND cornell_code != ''"
        ):
            by_cornell[str(code)].add(int(cls))
        by_sci: Dict[str, Set[int]] = defaultdict(set)
        for sci, cls in conn.execute(
            "SELECT scientific_name, model_class_id FROM BirdCountInfo "
            "WHERE model_class_id IS NOT NULL AND scientific_name IS NOT NULL"
        ):
            by_sci[str(sci)].add(int(cls))
            index.class_sci[int(cls)] = str(sci)
        by_key: Dict[int, Set[int]] = defaultdict(set)
        for key, cls in conn.execute(
            "SELECT specieskey, model_class_id FROM gbif_rarity_100 "
            "WHERE specieskey IS NOT NULL AND model_class_id IS NOT NULL"
        ):
            by_key[int(key)].add(int(cls))
        for genus, family in conn.execute(
            "SELECT genus, family_scientific FROM bird_ioc "
            "WHERE genus IS NOT NULL AND family_scientific IS NOT NULL"
        ):
            index.genus_family.setdefault(str(genus), str(family))
    finally:
        conn.close()
    index.by_cornell = dict(by_cornell)
    index.by_sci = dict(by_sci)
    index.by_gbif_key = dict(by_key)
    return index


def parse_gbif_v2_match(payload: dict) -> Optional[int]:
    """
    从 GBIF v2 species match 响应取可用的物种 key / Extract a usable species key.

    只接受物种级的精确/模糊匹配；同物异名返回其 accepted key。

    Only species-rank exact/fuzzy/variant matches are accepted; synonyms return
    their accepted key.

    参数 / Parameters:
        payload (dict): `GET /v2/species/match` 的 JSON / Response JSON.

    返回 / Returns:
        Optional[int]: 物种 key；不可用时 None / Species key or None.
    """
    diagnostics = payload.get("diagnostics") or {}
    if diagnostics.get("matchType") not in ("EXACT", "FUZZY", "VARIANT"):
        return None
    usage = payload.get("usage") or {}
    if str(usage.get("rank", "")).upper() != "SPECIES":
        return None
    accepted = payload.get("acceptedUsage") or {}
    key = accepted.get("key") or usage.get("key")
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


@dataclass
class SpeciesMapping:
    """
    映射结果 / Mapping result.

    属性 / Attributes:
        classes: 已映射的 speciesCode → 类别集合 / Mapped code to classes.
        steps: speciesCode → 命中步骤名 / Code to the step that mapped it.
        unmapped: 未映射的物种代码（已排序）/ Sorted unmapped codes.
    """

    classes: Dict[str, Set[int]]
    steps: Dict[str, str]
    unmapped: List[str]


def build_species_mapping(
    taxonomy: Dict[str, dict],
    index: ModelIndex,
    resolve_gbif: Callable[[str], Optional[int]],
    overrides: Dict[str, List[int]],
) -> SpeciesMapping:
    """
    对分类表中全部物种执行五步映射 / Run the five-step mapping over all species.

    映射对象是整个分类表而非某个清单：第五步需要知道哪些模型类别没被任何物种占用，
    只看单个清单会把别处已占用的类别误当成空闲。

    The whole taxonomy is mapped rather than one list: step five needs to know
    which model classes no species has claimed, and a single list would mistake
    classes claimed elsewhere for free ones.

    参数 / Parameters:
        taxonomy (dict): speciesCode → 分类条目 / Taxonomy by code.
        index (ModelIndex): 模型索引 / Model indexes.
        resolve_gbif (Callable): 学名 → GBIF 物种 key 或 None / Name to key or None.
        overrides (dict): 人工映射 speciesCode → 类别列表 / Manual mappings.

    返回 / Returns:
        SpeciesMapping: 映射结果 / The mapping.
    """
    classes: Dict[str, Set[int]] = {}
    steps: Dict[str, str] = {}
    species = sorted(c for c, e in taxonomy.items() if e.get("category") == "species")

    pending: List[str] = []
    for code in species:
        sci = taxonomy[code].get("sciName", "")
        if code in overrides:
            classes[code] = {int(c) for c in overrides[code]}
            steps[code] = "override"
            continue
        hit = index.by_cornell.get(code)
        step = "cornell"
        if not hit:
            hit = index.by_sci.get(sci)
            step = "sci"
        if hit:
            classes[code] = set(hit)
            steps[code] = step
        else:
            pending.append(code)

    still: List[str] = []
    for code in pending:
        key = resolve_gbif(taxonomy[code].get("sciName", ""))
        hit = index.by_gbif_key.get(key) if key is not None else None
        if hit:
            classes[code] = set(hit)
            steps[code] = "gbif"
        else:
            still.append(code)

    genus_family: Dict[str, str] = {}
    for entry in taxonomy.values():
        family = entry.get("familySciName")
        sci = entry.get("sciName") or ""
        if family and sci:
            genus_family.setdefault(sci.split()[0], family)

    def model_family(cls: int) -> Optional[str]:
        genus = index.class_sci[cls].split()[0]
        return genus_family.get(genus) or index.genus_family.get(genus)

    used: Set[int] = set()
    for cs in classes.values():
        used |= cs
    orphan_index: Dict[tuple, Set[int]] = defaultdict(set)
    for cls, sci in index.class_sci.items():
        if cls in used:
            continue
        family = model_family(cls)
        if family:
            orphan_index[(epithet_stem(sci), family)].add(cls)

    keys = {
        code: (epithet_stem(taxonomy[code].get("sciName", "")), taxonomy[code].get("familySciName"))
        for code in still
    }
    demand = Counter(keys.values())
    unmapped: List[str] = []
    for code in still:
        key = keys[code]
        candidates = orphan_index.get(key, set())
        if key[1] and len(candidates) == 1 and demand[key] == 1:
            classes[code] = set(candidates)
            steps[code] = "epithet"
        else:
            unmapped.append(code)
    return SpeciesMapping(classes=classes, steps=steps, unmapped=sorted(unmapped))


def classes_for_region(species_codes: Iterable[str], mapping: SpeciesMapping) -> Set[int]:
    """
    区域物种代码 → 模型类别并集 / Union of model classes for a region.

    参数 / Parameters:
        species_codes (Iterable[str]): 物种级代码 / Species-level codes.
        mapping (SpeciesMapping): 映射结果 / The mapping.

    返回 / Returns:
        set[int]: 类别集合 / Class ids.
    """
    out: Set[int] = set()
    for code in species_codes:
        out |= mapping.classes.get(code, set())
    return out


def find_overloaded_classes(
    mapping: SpeciesMapping, allow: Set[int], limit: int = 3
) -> Dict[int, List[str]]:
    """
    找出被映射到过多物种的类别 / Find classes mapped from too many species.

    旧版 42 个类别都被映射成 ostric2 这种「默认值污染」，在反方向表现为一个值被
    大量复用；这里检查类别侧的同类异常。

    The legacy ostric2 pollution shows up as one value reused many times; this
    checks for the same anomaly on the class side.

    参数 / Parameters:
        mapping (SpeciesMapping): 映射结果 / The mapping.
        allow (set[int]): 确认合理的类别白名单 / Classes confirmed as legitimate.
        limit (int): 允许的最大物种数 / Maximum species per class.

    返回 / Returns:
        dict[int, list[str]]: 超限类别 → 物种代码 / Offending class to codes.
    """
    by_class: Dict[int, List[str]] = defaultdict(list)
    for code, cs in mapping.classes.items():
        for cls in cs:
            by_class[cls].append(code)
    return {
        cls: sorted(codes)
        for cls, codes in by_class.items()
        if len(codes) > limit and cls not in allow
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_ebird_region_mapping.py -v && python3 -m py_compile scripts_dev/ebird_region_mapping.py`
Expected: 10 passed（参数化 3 例计入）

- [ ] **Step 5: 同步 spec**

用 Edit 工具修改 `docs/specs/2026-09-16-ebird-region-filter-design.md`：

§4.2 第 2 点整段替换为：

```markdown
2. **映射到模型类别编号**（得到集合，允许一对多），对**整个分类表**的物种按顺序取第一个非空结果：
   1. 人工覆盖 `scripts_dev/ebird_overrides.json` 的 `map`（人工结论优先于自动结果）；
   2. `avilist_map.cornell_code == code` 且 `model_class_id` 非空；
   3. eBird `sciName` 精确匹配 `BirdCountInfo.scientific_name`；
   4. GBIF v2 species match 把 eBird `sciName` 解析为物种级 accepted key，命中 `gbif_rarity_100.specieskey`；
   5. **同科种加词**：在前四步未占用的模型类别中，找「同科 + 种加词词干（去阴阳性词尾）相同」且双方唯一的配对。
      科名取 eBird 分类表的属→科关系，缺失时用 `bird_ioc`。设计阶段实测 111 例全部为属名调整且配对正确；
      不加同科约束会出现 `Crithagra striatipectus` ↔ `Nystalus striatipectus` 这类跨科错配。
```

§4.3 整段替换为：

```markdown
### 4.3 构建卡口 / Build gate

模型 10,964 类、eBird 物种 11,167 个，天然存在模型没有的物种，无法自动区分「模型没有」与「漏接」，
因此卡口改为可判定的规则：

- **硬失败**：哨兵物种缺失——`SENTINELS` 中列出的（区域, 模型学名）必须出现在该区域候选集中
  （取自 §2.2 旧版误杀案例：褐刺嘴莺、红耳绿鹦鹉、澳洲燕鸻、黄腹山雀、蓑羽鹤、金眶鸻、蒙古沙鸻、黄林莺）。
- **硬失败**：任一模型类别被映射到超过 3 个 eBird 物种，且不在 `allow_multi` 白名单中。
- **硬失败**：任一区域物种数为 0；任一中澳美省州没有边界；`CN`/`AU`/`US` 没有国家边界。
- **硬失败**：国家中文名缺失（`tools/country_names.py` 未收录），或省州中文名含繁体字。
- **只报告**：未映射物种清单（代码、学名、英文名、出现在多少个区域）与第 5 步配对清单，写入
  `scripts_dev/.ebird_cache/mapping_report.txt` 供人工复核，发现错配就加进 `map` 覆盖。
```

- [ ] **Step 6: 提交**

```bash
git add docs/specs/2026-09-16-ebird-region-filter-design.md
git add -f scripts_dev/ebird_region_mapping.py test_ebird_region_mapping.py
git commit -m "feat(scripts): eBird 物种五步映射库与映射卡口

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 边界提取库 `scripts_dev/ebird_region_boundaries.py`

Natural Earth 实测事实（spec §2.6）决定了本任务的几张对照表：中国是 ISO 字母码需转 eBird 数字码；
澳洲有 4 条非州级要素；50m 国界中「Indian Ocean Ter.」（圣诞岛 + 科科斯）标为 `AU`，而 eBird 把
圣诞岛 `CX`、科科斯 `CC` 作独立国家，所以排除该要素；部分中文名是繁体。

**Files:**
- Create: `scripts_dev/ebird_region_boundaries.py`
- Test: `test_ebird_region_boundaries.py`

**Interfaces:**
- Consumes: `birdid.region_geometry.quantize_ring / pack_ring / ring_bbox`（Task 1）
- Produces:
  - `SUBNATIONAL_COUNTRIES: Tuple[str, ...] = ("CN", "AU", "US")`
  - `CN_ISO_TO_EBIRD: Dict[str, str]`（31 项）
  - `@dataclass RingRow(region: str, level: int, ring_id: int, min_lat: float, max_lat: float, min_lon: float, max_lon: float, is_hole: int, coords: bytes)`
  - `ebird_subnational_code(iso_3166_2: str) -> Optional[str]`
  - `iter_geometry_rings(geometry: dict) -> Iterator[Tuple[Sequence, bool]]`
  - `admin1_rings(features: List[dict], valid_codes: Set[str]) -> Tuple[List[RingRow], Dict[str, str], List[str]]`（行、代码→中文名、跳过的 ISO 码）
  - `admin0_rings(features: List[dict], valid_codes: Set[str]) -> Tuple[List[RingRow], List[str]]`
  - `find_traditional_names(names: Dict[str, str]) -> Dict[str, str]`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
Natural Earth 边界提取单测 / Unit tests for Natural Earth boundary extraction.
"""
from scripts_dev.ebird_region_boundaries import (
    CN_ISO_TO_EBIRD,
    admin0_rings,
    admin1_rings,
    ebird_subnational_code,
    find_traditional_names,
)

SQUARE = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
HOLED = [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]]


def feature(props, coords, gtype="Polygon"):
    """构造 GeoJSON 要素 / Build a GeoJSON feature."""
    return {"type": "Feature", "properties": props, "geometry": {"type": gtype, "coordinates": coords}}


def test_cn_table_covers_31_provinces():
    """对照表覆盖 31 个省级单位且值唯一 / 31 provinces with unique targets."""
    assert len(CN_ISO_TO_EBIRD) == 31
    assert len(set(CN_ISO_TO_EBIRD.values())) == 31
    assert CN_ISO_TO_EBIRD["CN-BJ"] == "CN-11" and CN_ISO_TO_EBIRD["CN-XJ"] == "CN-65"


def test_subnational_code_rules():
    """中国转数字码，澳洲特殊要素归并或跳过，美国原样 / Code reconciliation rules."""
    assert ebird_subnational_code("CN-HA") == "CN-41"
    assert ebird_subnational_code("AU-X02~") == "AU-ACT"
    assert ebird_subnational_code("AU-X03~") == "AU-TAS"
    assert ebird_subnational_code("AU-X04~") is None
    assert ebird_subnational_code("CN-X01~") is None
    assert ebird_subnational_code("US-CA") == "US-CA"


def test_admin1_rings_picks_largest_name_and_skips():
    """同一代码取面积最大要素的中文名；无效代码记为跳过 / Largest feature names the region."""
    feats = [
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "豪勋爵岛", "area_sqkm": 15}, SQUARE),
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "新南威尔士州", "area_sqkm": 800000}, SQUARE),
        feature({"iso_a2": "AU", "iso_3166_2": "AU-X04~", "name_zh": "阿什莫尔", "area_sqkm": 1}, SQUARE),
        feature({"iso_a2": "JP", "iso_3166_2": "JP-13", "name_zh": "东京都", "area_sqkm": 2000}, SQUARE),
    ]
    rows, names, skipped = admin1_rings(feats, valid_codes={"AU-NSW"})
    assert {r.region for r in rows} == {"AU-NSW"}
    assert [r.ring_id for r in rows] == [0, 1]
    assert all(r.level == 1 for r in rows)
    assert names == {"AU-NSW": "新南威尔士州"}
    assert skipped == ["AU-X04~"]


def test_admin1_marks_holes():
    """内环标为洞 / Inner rings are flagged as holes."""
    feats = [feature({"iso_a2": "US", "iso_3166_2": "US-CA", "name_zh": "加利福尼亚州", "area_sqkm": 1}, HOLED)]
    rows, _, _ = admin1_rings(feats, valid_codes={"US-CA"})
    assert [r.is_hole for r in rows] == [0, 1]
    assert (rows[0].min_lat, rows[0].max_lat, rows[0].min_lon, rows[0].max_lon) == (0.0, 4.0, 0.0, 4.0)


def test_admin0_rules():
    """用 ISO_A2_EH；排除印度洋领地；无效与 -99 跳过 / Admin-0 rules."""
    feats = [
        feature({"ISO_A2_EH": "FR", "NAME": "France"}, [SQUARE], "MultiPolygon"),
        feature({"ISO_A2_EH": "AU", "NAME": "Indian Ocean Ter."}, SQUARE),
        feature({"ISO_A2_EH": "-99", "NAME": "Somaliland"}, SQUARE),
        feature({"ISO_A2_EH": "ZZ", "NAME": "Nowhere"}, SQUARE),
    ]
    rows, skipped = admin0_rings(feats, valid_codes={"FR", "AU"})
    assert [(r.region, r.level) for r in rows] == [("FR", 0)]
    assert skipped == ["ZZ"]


def test_find_traditional_names():
    """含繁体字的中文名要报出 / Names with traditional characters are reported."""
    names = {"US-NE": "內布拉斯加州", "US-TX": "得克萨斯州"}
    assert find_traditional_names(names) == {"US-NE": "內布拉斯加州"}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_ebird_region_boundaries.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# -*- coding: utf-8 -*-
"""
Natural Earth 行政区边界 → ebird_regions.db 边界行 / Natural Earth boundaries to DB rows.

国家级用 ne_50m_admin_0_countries，中澳美省州级用 ne_10m_admin_1_states_provinces
（均为公有领域）。代码统一转换为 eBird 区域代码，运行时只认 eBird 代码。

Countries come from ne_50m_admin_0_countries and CN/AU/US subnational units
from ne_10m_admin_1_states_provinces (both public domain). All codes are
converted to eBird region codes; the runtime only ever sees eBird codes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from birdid.region_geometry import pack_ring, quantize_ring, ring_bbox

SUBNATIONAL_COUNTRIES: Tuple[str, ...] = ("CN", "AU", "US")

# Natural Earth 用 ISO 3166-2 字母码，eBird 沿用 GB/T 2260 数字码（2026-09-16 用 API 实测）
# Natural Earth uses ISO 3166-2 letter codes; eBird keeps GB/T 2260 numeric codes.
CN_ISO_TO_EBIRD: Dict[str, str] = {
    "CN-BJ": "CN-11", "CN-TJ": "CN-12", "CN-HE": "CN-13", "CN-SX": "CN-14", "CN-NM": "CN-15",
    "CN-LN": "CN-21", "CN-JL": "CN-22", "CN-HL": "CN-23",
    "CN-SH": "CN-31", "CN-JS": "CN-32", "CN-ZJ": "CN-33", "CN-AH": "CN-34", "CN-FJ": "CN-35",
    "CN-JX": "CN-36", "CN-SD": "CN-37",
    "CN-HA": "CN-41", "CN-HB": "CN-42", "CN-HN": "CN-43", "CN-GD": "CN-44", "CN-GX": "CN-45",
    "CN-HI": "CN-46",
    "CN-CQ": "CN-50", "CN-SC": "CN-51", "CN-GZ": "CN-52", "CN-YN": "CN-53", "CN-XZ": "CN-54",
    "CN-SN": "CN-61", "CN-GS": "CN-62", "CN-QH": "CN-63", "CN-NX": "CN-64", "CN-XJ": "CN-65",
}

# 非州级要素的归属：值为 None 表示只参与国家级 / Non-state features; None = country level only.
# 杰维斯湾行政上附属首都领地，麦夸里岛属塔斯马尼亚；阿什莫尔、西沙群岛无对应省州。
# Jervis Bay is administered with the ACT and Macquarie Island belongs to
# Tasmania; Ashmore and the Paracel Islands have no matching subnational unit.
_SUBNATIONAL_OVERRIDES: Dict[str, Optional[str]] = {
    "AU-X02~": "AU-ACT",
    "AU-X03~": "AU-TAS",
    "AU-X04~": None,
    "CN-X01~": None,
}

# 50m 国界把圣诞岛与科科斯合并为 AU 名下的「Indian Ocean Ter.」，而 eBird 将二者作为独立
# 国家 CX / CC；若保留会让这些岛上的照片套用澳洲大陆清单，故排除（交给离岸容差或手选）。
# The 50m layer merges Christmas and Cocos Islands under AU, while eBird treats
# them as separate countries; keeping it would filter island photos with the
# mainland list, so it is excluded.
_ADMIN0_NAME_EXCLUDE = frozenset({"Indian Ocean Ter."})

# 只在繁体中出现的常用字，用于发现 Natural Earth 中文名里的繁体 / Traditional-only characters.
# 只收简繁异形字；「斯」「瓦」这类简繁同形字会误报「得克萨斯州」等正常名称。
# Only characters whose simplified form differs; shared forms would cause false positives.
_TRADITIONAL_ONLY = frozenset("內維亞傑灣區華爾蘭東會島門與過際國號達邁納羅")


@dataclass
class RingRow:
    """
    boundaries 表的一行 / One row of the boundaries table.

    属性 / Attributes:
        region: eBird 区域代码 / eBird region code.
        level: 0 国家、1 省州 / 0 country, 1 subnational.
        ring_id: 区域内序号 / Sequence within the region.
        min_lat, max_lat, min_lon, max_lon: 外包框（度）/ Bounding box in degrees.
        is_hole: 1 表示内环 / 1 for an inner ring.
        coords: pack_ring 输出 / Packed coordinates.
    """

    region: str
    level: int
    ring_id: int
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    is_hole: int
    coords: bytes


def ebird_subnational_code(iso_3166_2: str) -> Optional[str]:
    """
    Natural Earth 省州代码 → eBird 代码 / Convert a Natural Earth code to eBird.

    参数 / Parameters:
        iso_3166_2 (str): Natural Earth 的 iso_3166_2 字段 / The iso_3166_2 field.

    返回 / Returns:
        Optional[str]: eBird 代码；只参与国家级的要素返回 None / eBird code or None.
    """
    if iso_3166_2 in _SUBNATIONAL_OVERRIDES:
        return _SUBNATIONAL_OVERRIDES[iso_3166_2]
    return CN_ISO_TO_EBIRD.get(iso_3166_2, iso_3166_2)


def iter_geometry_rings(geometry: dict) -> Iterator[Tuple[Sequence, bool]]:
    """
    遍历 Polygon / MultiPolygon 的所有环 / Iterate every ring of a (Multi)Polygon.

    参数 / Parameters:
        geometry (dict): GeoJSON geometry / GeoJSON geometry.

    返回 / Returns:
        Iterator[tuple]: (环坐标, 是否内环) / (ring coordinates, is_hole).
    """
    gtype = geometry.get("type")
    if gtype == "Polygon":
        polygons = [geometry.get("coordinates") or []]
    elif gtype == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
    else:
        return
    for polygon in polygons:
        for i, ring in enumerate(polygon):
            yield ring, i > 0


def _rows_for_feature(feature: dict, region: str, level: int, start_id: int) -> List[RingRow]:
    """
    把一个要素的所有有效环转成行 / Convert a feature's valid rings into rows.

    参数 / Parameters:
        feature (dict): GeoJSON 要素 / Feature.
        region (str): eBird 区域代码 / Region code.
        level (int): 0 或 1 / 0 or 1.
        start_id (int): 起始 ring_id / First ring id.

    返回 / Returns:
        list[RingRow]: 行列表（量化后不足 3 点的环被丢弃）/ Rows; degenerate rings dropped.
    """
    rows: List[RingRow] = []
    ring_id = start_id
    for coords, is_hole in iter_geometry_rings(feature.get("geometry") or {}):
        points = quantize_ring(coords)
        if len(points) < 3:
            continue
        min_lat, max_lat, min_lon, max_lon = ring_bbox(points)
        rows.append(
            RingRow(region, level, ring_id, min_lat, max_lat, min_lon, max_lon,
                    1 if is_hole else 0, pack_ring(points))
        )
        ring_id += 1
    return rows


def admin1_rings(
    features: List[dict], valid_codes: Set[str]
) -> Tuple[List[RingRow], Dict[str, str], List[str]]:
    """
    提取中澳美省州边界 / Extract CN/AU/US subnational boundaries.

    参数 / Parameters:
        features (list[dict]): ne_10m_admin_1 的要素 / Admin-1 features.
        valid_codes (set[str]): eBird 省州代码 / Valid eBird subnational codes.

    返回 / Returns:
        tuple: (行, 代码→面积最大要素的中文名, 被跳过的 Natural Earth 代码（排序）) /
            (rows, code to Chinese name of the largest feature, sorted skipped codes).
    """
    rows: List[RingRow] = []
    names: Dict[str, str] = {}
    best_area: Dict[str, float] = {}
    next_id: Dict[str, int] = {}
    skipped: List[str] = []
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("iso_a2") not in SUBNATIONAL_COUNTRIES:
            continue
        iso = str(props.get("iso_3166_2") or "")
        code = ebird_subnational_code(iso)
        if code is None or code not in valid_codes:
            skipped.append(iso)
            continue
        new_rows = _rows_for_feature(feat, code, 1, next_id.get(code, 0))
        rows.extend(new_rows)
        next_id[code] = next_id.get(code, 0) + len(new_rows)
        area = float(props.get("area_sqkm") or 0)
        if area >= best_area.get(code, -1.0):
            best_area[code] = area
            names[code] = str(props.get("name_zh") or "")
    return rows, names, sorted(skipped)


def admin0_rings(features: List[dict], valid_codes: Set[str]) -> Tuple[List[RingRow], List[str]]:
    """
    提取国家边界 / Extract country boundaries.

    参数 / Parameters:
        features (list[dict]): ne_50m_admin_0 的要素 / Admin-0 features.
        valid_codes (set[str]): eBird 国家代码 / Valid eBird country codes.

    返回 / Returns:
        tuple: (行, eBird 中不存在而被跳过的代码（排序去重）) / (rows, sorted skipped codes).
    """
    rows: List[RingRow] = []
    next_id: Dict[str, int] = {}
    skipped: Set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("NAME") in _ADMIN0_NAME_EXCLUDE:
            continue
        code = str(props.get("ISO_A2_EH") or "")
        if not code or code == "-99":
            continue
        if code not in valid_codes:
            skipped.add(code)
            continue
        new_rows = _rows_for_feature(feat, code, 0, next_id.get(code, 0))
        rows.extend(new_rows)
        next_id[code] = next_id.get(code, 0) + len(new_rows)
    return rows, sorted(skipped)


def find_traditional_names(names: Dict[str, str]) -> Dict[str, str]:
    """
    找出含繁体字的中文名 / Find Chinese names containing traditional characters.

    参数 / Parameters:
        names (dict[str, str]): 代码 → 中文名 / Code to Chinese name.

    返回 / Returns:
        dict[str, str]: 含繁体字的条目 / Entries with traditional characters.
    """
    return {code: name for code, name in names.items() if any(ch in _TRADITIONAL_ONLY for ch in name)}
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_ebird_region_boundaries.py -v && python3 -m py_compile scripts_dev/ebird_region_boundaries.py`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add -f scripts_dev/ebird_region_boundaries.py test_ebird_region_boundaries.py
git commit -m "feat(scripts): Natural Earth 边界提取与 eBird 代码对照

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 构建脚本 `scripts_dev/build_ebird_regions.py`

**Files:**
- Create: `scripts_dev/build_ebird_regions.py`
- Create: `scripts_dev/ebird_overrides.json`
- Test: `test_build_ebird_regions.py`

**Interfaces:**
- Consumes:
  - Task 2：`normalize_to_species`、`load_model_index`、`parse_gbif_v2_match`、`build_species_mapping`、`classes_for_region`、`find_overloaded_classes`、`SpeciesMapping`
  - Task 3：`SUBNATIONAL_COUNTRIES`、`RingRow`、`admin0_rings`、`admin1_rings`、`find_traditional_names`
  - `tools.country_names.country_display_names(code) -> Tuple[str, str]`（未收录时中英文都回退为代码本身）
- Produces:
  - `SCHEMA: str`
  - `write_database(path: str, regions: List[Tuple[str, Optional[str], str, str, int]], region_species: Dict[str, Set[int]], rings: List[RingRow], meta: Dict[str, str]) -> None`
  - `check_sentinels(region_species: Dict[str, Set[int]], class_by_sci: Dict[str, Set[int]], sentinels: Sequence[Tuple[str, str]]) -> List[str]`
  - `country_rows(countries: List[dict], region_species: Dict[str, Set[int]]) -> Tuple[List[tuple], List[str]]`
  - `class CachedFetcher(cache_dir: str, headers: Dict[str, str], refresh: bool = False, opener: Optional[Callable] = None)`，方法 `get_json(url: str, cache_name: str) -> Any`
  - `load_overrides(path: str) -> Tuple[Dict[str, List[int]], Set[int]]`
  - `main(argv: Optional[List[str]] = None) -> int`
  - 数据库表结构（Task 6、7、10 依赖）：

```sql
CREATE TABLE regions (code TEXT PRIMARY KEY, parent TEXT, name_en TEXT NOT NULL, name_zh TEXT NOT NULL, species_count INTEGER NOT NULL);
CREATE TABLE region_species (region TEXT NOT NULL, class_id INTEGER NOT NULL, PRIMARY KEY (region, class_id)) WITHOUT ROWID;
CREATE TABLE boundaries (region TEXT NOT NULL, level INTEGER NOT NULL, ring_id INTEGER NOT NULL, min_lat REAL NOT NULL, max_lat REAL NOT NULL, min_lon REAL NOT NULL, max_lon REAL NOT NULL, is_hole INTEGER NOT NULL, coords BLOB NOT NULL, PRIMARY KEY (region, level, ring_id));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
```

  `meta` 键：`taxonomy_version`、`fetched_at`（`YYYY-MM-DD`）、`ebird_terms_url`、`attribution`、`boundaries_source`、`builder_version`。
  （boundaries 主键含 `level`：同一代码理论上不会同时是国家与省州，但把 level 放进主键让这一点不依赖数据巧合。）

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
"""
eBird 区域库构建脚本单测（不联网）/ Offline unit tests for the region DB builder.
"""
import json
import sqlite3

from birdid.region_geometry import pack_ring, quantize_ring
from scripts_dev.build_ebird_regions import (
    CachedFetcher,
    check_sentinels,
    country_rows,
    load_overrides,
    write_database,
)
from scripts_dev.ebird_region_boundaries import RingRow


def square_row(region, level, lat0, lon0, size=1.0):
    """构造一个正方形边界行 / Build a square boundary row."""
    pts = quantize_ring([[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size]])
    return RingRow(region, level, 0, lat0, lat0 + size, lon0, lon0 + size, 0, pack_ring(pts))


def test_write_database_roundtrip(tmp_path):
    """写入后四张表内容可读回 / All four tables read back."""
    path = tmp_path / "out.db"
    write_database(
        str(path),
        regions=[("AU", None, "Australia", "澳大利亚", 2), ("AU-NSW", "AU", "New South Wales", "新南威尔士州", 1)],
        region_species={"AU": {1, 2}, "AU-NSW": {1}},
        rings=[square_row("AU", 0, -40, 110, 40), square_row("AU-NSW", 1, -37, 141, 8)],
        meta={"fetched_at": "2026-09-16"},
    )
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT code, parent FROM regions ORDER BY code").fetchall() == [("AU", None), ("AU-NSW", "AU")]
    assert db.execute("SELECT class_id FROM region_species WHERE region='AU' ORDER BY 1").fetchall() == [(1,), (2,)]
    assert db.execute("SELECT COUNT(*) FROM boundaries").fetchone()[0] == 2
    assert db.execute("SELECT value FROM meta WHERE key='fetched_at'").fetchone()[0] == "2026-09-16"
    db.close()


def test_write_database_replaces_existing(tmp_path):
    """已有文件被整体替换而不是追加 / An existing file is replaced, not appended."""
    path = tmp_path / "out.db"
    for count in (1, 1):
        write_database(str(path), [("IS", None, "Iceland", "冰岛", count)], {"IS": {7}}, [], {})
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT COUNT(*) FROM regions").fetchone()[0] == 1
    db.close()


def test_check_sentinels():
    """缺失的哨兵逐条报出 / Each missing sentinel is reported."""
    errors = check_sentinels(
        region_species={"AU-NSW": {6065}, "CN-11": set()},
        class_by_sci={"Acanthiza pusilla": {6065}, "Grus virgo": {1418}},
        sentinels=[("AU-NSW", "Acanthiza pusilla"), ("CN-11", "Grus virgo"), ("CN-11", "Nullus absentis")],
    )
    assert len(errors) == 2
    assert "Grus virgo" in errors[0] and "Nullus absentis" in errors[1]


def test_country_rows_reports_missing_chinese_names():
    """未收录中文名的国家要报出 / Countries without a Chinese name are reported."""
    rows, missing = country_rows(
        [{"code": "AU", "name": "Australia"}, {"code": "QQ", "name": "Q Land"}],
        {"AU": {1, 2}, "QQ": {3}},
    )
    assert rows[0] == ("AU", None, "Australia", "澳大利亚", 2)
    assert missing == ["QQ"]


def test_fetcher_uses_cache_without_network(tmp_path):
    """缓存命中时不调用网络 / A cache hit never touches the network."""
    (tmp_path / "a.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("不应联网 / must not hit the network")

    fetcher = CachedFetcher(str(tmp_path), headers={}, opener=boom)
    assert fetcher.get_json("https://example.invalid/a", "a.json") == {"x": 1}


def test_fetcher_writes_cache(tmp_path):
    """网络结果写入缓存 / Network results are cached."""
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps([1, 2]).encode("utf-8")

    fetcher = CachedFetcher(str(tmp_path), headers={}, opener=lambda req, timeout: Resp())
    assert fetcher.get_json("https://example.invalid/b", "sub/b.json") == [1, 2]
    assert json.loads((tmp_path / "sub" / "b.json").read_text(encoding="utf-8")) == [1, 2]


def test_load_overrides(tmp_path):
    """覆盖文件的两个字段 / Both override fields load."""
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"map": {"lirplo": [1513]}, "allow_multi": [42]}), encoding="utf-8")
    mapping, allow = load_overrides(str(p))
    assert mapping == {"lirplo": [1513]} and allow == {42}
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_build_ebird_regions.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 创建 `scripts_dev/ebird_overrides.json`**

```json
{
  "_comment": "map: eBird speciesCode -> 模型类别列表，人工结论优先于自动映射；allow_multi: 确认可以对应超过 3 个 eBird 物种的模型类别。",
  "map": {},
  "allow_multi": []
}
```

- [ ] **Step 4: 实现 `scripts_dev/build_ebird_regions.py`**

```python
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
```

注意：`print` 只输出 ASCII 与代码/学名（本仓库出过构建期在 cp1252 控制台打印中文导致构建失败的事故），中文只写入 UTF-8 文件。

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest test_build_ebird_regions.py -v && python3 -m py_compile scripts_dev/build_ebird_regions.py`
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
git add -f scripts_dev/build_ebird_regions.py scripts_dev/ebird_overrides.json test_build_ebird_regions.py
git commit -m "feat(scripts): eBird 区域库构建脚本（缓存抓取/卡口/原子写库）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: 实际构建 `ebird_regions.db` 并处理卡口

本任务联网、需要 key，产出提交入库的数据文件。卡口失败是**预期中**的——要逐条处理直到通过，不得删卡口或放宽阈值来过关。

**Files:**
- Create: `birdid/data/ebird_regions.db`
- Modify: `scripts_dev/ebird_overrides.json`、`tools/country_names.py`（补中文名）、`scripts_dev/build_ebird_regions.py`（仅 `SUBNATIONAL_ZH_OVERRIDES`）

**Interfaces:**
- Consumes: Task 4 的 `main`
- Produces: `birdid/data/ebird_regions.db`（Task 6 起所有任务依赖）

- [ ] **Step 1: 设置 key 并首次运行**

key 沿用旧版硬编码值（用户决定），只放在当前 shell 环境变量里：

```bash
export EBIRD_API_KEY=$(git show '6f342a3d^:birdid/ebird_country_filter.py' | grep -o "EBIRD_API_KEY', '[^']*'" | head -1 | sed -E "s/.*, '([^']*)'/\1/")
test ${#EBIRD_API_KEY} -eq 12 && echo key-ok
python3 -m scripts_dev.build_ebird_regions
```

Expected：约 340 个清单依次打印 `list XX: n -> m species`，结尾很可能 `N gate error(s)` 且退出码 1。首次运行约 5–15 分钟（含约 700 次 GBIF 查询）。

- [ ] **Step 2: 逐类处理卡口错误**

打开 `scripts_dev/.ebird_cache/mapping_report.txt`，按错误类型处理，每处理一批就重跑 Step 1 的最后一条命令（全部走缓存，几秒完成）：

| 错误 | 处理 |
|---|---|
| `country missing Chinese name ...: XX` | 在 `tools/country_names.py` 的 `_NAMES` 按字母序补 `"XX": ("English", "中文名")`，英文名取 `scripts_dev/.ebird_cache/countries.json` 中的 name，中文名用通行译名 |
| `traditional characters ...` | 在 `SUBNATIONAL_ZH_OVERRIDES` 补简体名 |
| `subnational missing Chinese name` / `no boundary` | 查 `mapping_report.txt` 末尾的 skipped admin-1 列表，确认是代码对照问题后修 `scripts_dev/ebird_region_boundaries.py` 的对照表，并为该情形补一条单测 |
| `sentinel missing` | 在报告的 unmapped 段找到对应 eBird 物种代码，在 `ebird_overrides.json` 的 `map` 加 `"code": [class_id]`；class_id 用 `python3 -c "import sqlite3;print(sqlite3.connect('birdid/data/bird_reference.sqlite').execute(\"select model_class_id from BirdCountInfo where scientific_name=?\",('学名',)).fetchall())"` 查 |
| `class N mapped from [...]` | 逐个核对这些 eBird 物种是否确实是同一模型类别（模型合并种）；是则加入 `allow_multi`，否则为错配，在 `map` 中为错配的物种写正确类别 |

- [ ] **Step 3: 复核报告中的非卡口项**

1. 「epithet-step pairs」逐条抽查至少 30 条，确认是属名调整；发现错配，在 `map` 中为该物种写正确类别（找不到正确类别就写 `[]` 让它明确不映射——`build_species_mapping` 会把空列表视为 override 命中的空集合）。
2. 「unmapped species」按出现区域数降序看前 50 条：对模型确有该种（用学名/英文名在 `BirdCountInfo` 里查得到）的，加进 `map`。
3. 把抽查结论（条数、发现几处错配、处理方式）记下来，Step 6 提交信息里写明。

- [ ] **Step 4: 通过后核对产物**

```bash
python3 - <<'EOF'
import sqlite3, os
p = 'birdid/data/ebird_regions.db'
c = sqlite3.connect(p)
print('MB', round(os.path.getsize(p) / 1048576, 2))
print('countries', c.execute("select count(*) from regions where parent is null").fetchone()[0])
print('subnationals', c.execute("select parent, count(*) from regions where parent is not null group by 1").fetchall())
print('species', c.execute("select code, species_count from regions where code in ('AU','AU-NSW','CN','CN-11','CN-53','US','US-CA','IS')").fetchall())
print('meta', c.execute("select key, value from meta").fetchall())
EOF
```

Expected：体积 ≤ 6 MB；国家约 253；省州 `[('AU', 8), ('CN', 31), ('US', 51)]`；`AU-NSW` 约 600、`CN-11` 约 500、`US-CA` 约 900（数量级与 spec §2.1 一致，偏差超过 ±20% 要停下查原因）。

- [ ] **Step 5: 跑一遍全部新单测，确认 Task 2–4 的修改没有回归**

Run: `python3 -m pytest test_region_geometry.py test_ebird_region_mapping.py test_ebird_region_boundaries.py test_build_ebird_regions.py -v`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add birdid/data/ebird_regions.db tools/country_names.py
git add -f scripts_dev/ebird_overrides.json scripts_dev/build_ebird_regions.py scripts_dev/ebird_region_boundaries.py
git commit -m "data(birdid): 生成 ebird_regions.db（eBird 国家级+中澳美省州级，派生类别清单+Natural Earth 边界）

<在此写：体积、区域数、映射步骤统计、epithet 抽查条数与错配处理、新增 override 条数>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: GPS 定位 `birdid/region_locator.py`

**Files:**
- Create: `birdid/region_locator.py`
- Test: `test_region_locator.py`（合成库）、`test_region_locator_real_db.py`（真实库，对应 spec §8 验收 3）

**Interfaces:**
- Consumes:
  - Task 1：`unpack_ring`、`point_in_ring`、`distance_to_ring_km`
  - Task 4：`write_database`、`RingRow`（仅测试）；数据库表结构
  - Task 7 会提供 `birdid.geo_filter.default_db_path()`；`get_region_locator` 在函数内延迟导入它（Task 7 完成前该函数不会被调用）
  - `SUBNATIONAL_COUNTRIES` 的**唯一定义**在本模块；`geo_filter` 不导入本模块（避免循环依赖），其他模块一律 `from birdid.region_locator import SUBNATIONAL_COUNTRIES`
- Produces:
  - `SUBNATIONAL_COUNTRIES: frozenset = frozenset({"CN", "AU", "US"})`
  - `OFFSHORE_TOLERANCE_KM: float = 370.0`
  - `@dataclass(frozen=True) LocateResult(country: Optional[str], subnational: Optional[str])`
  - `class RegionLocator(db_path: str)`，方法 `locate(lat: float, lon: float) -> LocateResult`
  - `get_region_locator() -> Optional[RegionLocator]`（进程级单例，注册键 `"birdid.region_locator"`）

- [ ] **Step 1: 写合成库失败测试 `test_region_locator.py`**

```python
# -*- coding: utf-8 -*-
"""
GPS 区域定位单测（合成边界）/ Region locator tests on synthetic boundaries.

AA：纬 0–10、经 0–10 的国家；CN：纬 20–30、经 100–110，省级 CN-11（经 100–105）
与 CN-12（经 105–110）；BB：纬 40–50、经 0–10，中间挖洞 44–46/4–6，洞里是 CC。
"""
import pytest

from birdid.region_geometry import pack_ring, quantize_ring, ring_bbox
from birdid.region_locator import LocateResult, RegionLocator
from scripts_dev.build_ebird_regions import write_database
from scripts_dev.ebird_region_boundaries import RingRow


def ring(region, level, ring_id, lat0, lat1, lon0, lon1, is_hole=0):
    """矩形边界行 / Rectangular boundary row."""
    pts = quantize_ring([[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1]])
    return RingRow(region, level, ring_id, *ring_bbox(pts), is_hole, pack_ring(pts))


@pytest.fixture
def locator(tmp_path):
    """合成定位库 / Synthetic locator database."""
    path = tmp_path / "regions.db"
    write_database(
        str(path),
        regions=[
            ("AA", None, "A", "甲", 1), ("BB", None, "B", "乙", 1), ("CC", None, "C", "丙", 1),
            ("CN", None, "China", "中国", 1), ("CN-11", "CN", "Beijing", "北京市", 1),
            ("CN-12", "CN", "Tianjin", "天津市", 1),
        ],
        region_species={},
        rings=[
            ring("AA", 0, 0, 0, 10, 0, 10),
            ring("CN", 0, 0, 20, 30, 100, 110),
            ring("CN-11", 1, 0, 20, 30, 100, 105),
            ring("CN-12", 1, 0, 20, 30, 105, 110),
            ring("BB", 0, 0, 40, 50, 0, 10),
            ring("BB", 0, 1, 44, 46, 4, 6, is_hole=1),
            ring("CC", 0, 0, 44, 46, 4, 6),
        ],
        meta={},
    )
    return RegionLocator(str(path))


def test_inside_country_without_subnational(locator):
    """非中澳美国家只返回国家 / Other countries return the country only."""
    assert locator.locate(5.0, 5.0) == LocateResult("AA", None)


def test_subnational_split(locator):
    """省界两侧各归各省 / Each side of a provincial border."""
    assert locator.locate(25.0, 104.9) == LocateResult("CN", "CN-11")
    assert locator.locate(25.0, 105.1) == LocateResult("CN", "CN-12")


def test_hole_belongs_to_enclave(locator):
    """洞里的点属于洞中的国家 / A point in a hole belongs to the enclave."""
    assert locator.locate(45.0, 5.0) == LocateResult("CC", None)
    assert locator.locate(42.0, 5.0) == LocateResult("BB", None)


def test_offshore_within_tolerance(locator):
    """离岸约 110 km 归入最近国家 / About 110 km offshore snaps to the nearest country."""
    assert locator.locate(-1.0, 5.0) == LocateResult("AA", None)


def test_offshore_subnational_snaps(locator):
    """离岸点在省级同样就近 / Offshore points also snap at the subnational level."""
    assert locator.locate(25.0, 111.0) == LocateResult("CN", "CN-12")


def test_far_ocean_is_unlocated(locator):
    """远海返回空 / The open ocean is not located."""
    assert locator.locate(-20.0, 5.0) == LocateResult(None, None)


def test_cache_returns_same_result(locator):
    """缓存命中结果一致 / Cached lookups match."""
    first = locator.locate(25.0, 104.9)
    assert locator.locate(25.0, 104.9) == first
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_region_locator.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'birdid.region_locator'`

- [ ] **Step 3: 实现 `birdid/region_locator.py`**

```python
# -*- coding: utf-8 -*-
"""
GPS → eBird 国家/省州离线定位 / Offline GPS to eBird country/subnational lookup.

取代 reverse_geocoder：后者从未进入打包版（不在任何 requirements 中），且按最近城市判定，
在省界与海岸附近会出错（spec §2.4）。这里用 ebird_regions.db 中的 Natural Earth 边界做
点在多边形判断；落在海上时在 200 海里（约 370 km）内就近归属。

Replaces reverse_geocoder, which never shipped in packaged builds and judges by
nearest city, failing near borders and coasts (spec section 2.4). This uses the
Natural Earth boundaries stored in ebird_regions.db for point-in-polygon tests,
snapping offshore points to the nearest region within 200 nautical miles.
"""
from __future__ import annotations

import math
import os
import sqlite3
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from birdid.region_geometry import distance_to_ring_km, point_in_ring, unpack_ring

SUBNATIONAL_COUNTRIES = frozenset({"CN", "AU", "US"})

# 200 海里 = 专属经济区宽度；eBird 把离岸观察归入相邻行政区 / 200 nmi, the EEZ width.
OFFSHORE_TOLERANCE_KM = 370.0

_CACHE_LIMIT = 4096
_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LON_EQUATOR = 111.320


@dataclass(frozen=True)
class LocateResult:
    """
    定位结果 / Location result.

    属性 / Attributes:
        country: eBird 国家代码，无法定位为 None / Country code or None.
        subnational: eBird 省州代码，仅中澳美 / Subnational code (CN/AU/US only) or None.
    """

    country: Optional[str]
    subnational: Optional[str]


@dataclass
class _Ring:
    """已加载的边界环 / A loaded boundary ring."""

    region: str
    is_hole: bool
    bbox: Tuple[float, float, float, float]
    blob: bytes
    points: Optional[List[Tuple[int, int]]] = None

    @property
    def area(self) -> float:
        """外包框面积（度²），用于重叠时优先小区域 / Bbox area, smaller wins on overlap."""
        return (self.bbox[1] - self.bbox[0]) * (self.bbox[3] - self.bbox[2])


class RegionLocator:
    """
    GPS 区域定位器 / GPS region locator.

    参数 / Parameters:
        db_path (str): ebird_regions.db 路径 / Path to ebird_regions.db.

    异常 / Exceptions:
        sqlite3.Error: 库缺表或损坏时抛出 / Raised when the database is unusable.
    """

    def __init__(self, db_path: str) -> None:
        self._groups: Dict[Tuple[int, Optional[str]], List[_Ring]] = defaultdict(list)
        self._cache: "OrderedDict[Tuple[float, float], LocateResult]" = OrderedDict()
        self._lock = threading.Lock()
        conn = sqlite3.connect(db_path)
        try:
            parents = dict(conn.execute("SELECT code, parent FROM regions").fetchall())
            for region, level, is_hole, a, b, c, d, blob in conn.execute(
                "SELECT region, level, is_hole, min_lat, max_lat, min_lon, max_lon, coords "
                "FROM boundaries ORDER BY region, level, ring_id"
            ):
                key = (int(level), parents.get(region) if int(level) == 1 else None)
                self._groups[key].append(_Ring(region, bool(is_hole), (a, b, c, d), bytes(blob)))
        finally:
            conn.close()

    def locate(self, lat: float, lon: float) -> LocateResult:
        """
        定位坐标所在的国家与省州 / Locate the country and subnational unit.

        结果按 0.01° 取整缓存：同一批照片多拍于同一地点。

        Results are cached at 0.01 degree: a batch of photos usually shares a location.

        参数 / Parameters:
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            LocateResult: 定位结果 / The result.
        """
        key = (round(float(lat), 2), round(float(lon), 2))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        country = self._find(0, None, float(lat), float(lon))
        subnational = None
        if country in SUBNATIONAL_COUNTRIES:
            subnational = self._find(1, country, float(lat), float(lon))
        result = LocateResult(country, subnational)
        with self._lock:
            self._cache[key] = result
            if len(self._cache) > _CACHE_LIMIT:
                self._cache.popitem(last=False)
        return result

    def _points(self, ring: _Ring) -> List[Tuple[int, int]]:
        """
        延迟解码环坐标 / Decode ring coordinates lazily.

        并发下可能重复解码同一环，结果相同，无需加锁。

        Concurrent callers may decode the same ring twice; results are identical,
        so no lock is needed.
        """
        if ring.points is None:
            ring.points = unpack_ring(ring.blob)
        return ring.points

    def _find(self, level: int, parent: Optional[str], lat: float, lon: float) -> Optional[str]:
        """
        在某一层级中找包含或最近的区域 / Find the containing or nearest region at a level.

        参数 / Parameters:
            level (int): 0 国家、1 省州 / 0 country, 1 subnational.
            parent (Optional[str]): 省州层的上级国家 / Parent country for level 1.
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            Optional[str]: 区域代码或 None / Region code or None.
        """
        rings = self._groups.get((level, parent), [])
        outer_area: Dict[str, float] = {}
        holed = set()
        for ring in rings:
            a, b, c, d = ring.bbox
            if not (a <= lat <= b and c <= lon <= d):
                continue
            if point_in_ring(lat, lon, self._points(ring)):
                if ring.is_hole:
                    holed.add(ring.region)
                else:
                    outer_area[ring.region] = min(ring.area, outer_area.get(ring.region, math.inf))
        inside = sorted((area, region) for region, area in outer_area.items() if region not in holed)
        if inside:
            return inside[0][1]

        dlat = OFFSHORE_TOLERANCE_KM / _KM_PER_DEG_LAT
        dlon = OFFSHORE_TOLERANCE_KM / (_KM_PER_DEG_LON_EQUATOR * max(math.cos(math.radians(lat)), 0.05))
        best: Optional[str] = None
        best_km = OFFSHORE_TOLERANCE_KM
        for ring in rings:
            if ring.is_hole:
                continue
            a, b, c, d = ring.bbox
            if not (a - dlat <= lat <= b + dlat and c - dlon <= lon <= d + dlon):
                continue
            km = distance_to_ring_km(lat, lon, self._points(ring))
            if km < best_km:
                best, best_km = ring.region, km
        return best


def get_region_locator() -> Optional[RegionLocator]:
    """
    进程级单例；库缺失或损坏时返回 None / Process-wide singleton, None when unusable.

    返回 / Returns:
        Optional[RegionLocator]: 定位器或 None / Locator or None.
    """
    from config import get_lazy_registry

    def _factory() -> Optional[RegionLocator]:
        from birdid.geo_filter import default_db_path

        path = default_db_path()
        if not os.path.exists(path):
            return None
        try:
            return RegionLocator(path)
        except sqlite3.Error as exc:
            print(f"[RegionLocator] init failed: {exc}")
            return None

    return get_lazy_registry().get_or_create("birdid.region_locator", _factory)
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest test_region_locator.py -v && python3 -m py_compile birdid/region_locator.py`
Expected: 7 passed

- [ ] **Step 5: 写真实库测试 `test_region_locator_real_db.py`**

```python
# -*- coding: utf-8 -*-
"""
真实 ebird_regions.db 定位验收（spec §8 验收 3）/ Acceptance on the real database.
"""
import os

import pytest

from birdid.region_locator import LocateResult, RegionLocator

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "birdid", "data", "ebird_regions.db")

pytestmark = pytest.mark.skipif(not os.path.exists(DB), reason="ebird_regions.db not built")


@pytest.fixture(scope="module")
def locator():
    """真实库定位器 / Locator over the real database."""
    return RegionLocator(DB)


@pytest.mark.parametrize(
    "name,lat,lon,expected",
    [
        ("北京", 39.90, 116.40, LocateResult("CN", "CN-11")),
        ("上海", 31.23, 121.47, LocateResult("CN", "CN-31")),
        ("乌鲁木齐", 43.83, 87.62, LocateResult("CN", "CN-65")),
        ("拉萨", 29.65, 91.13, LocateResult("CN", "CN-54")),
        ("香港", 22.32, 114.17, LocateResult("HK", None)),
        ("台北", 25.03, 121.56, LocateResult("TW", None)),
        ("堪培拉", -35.28, 149.13, LocateResult("AU", "AU-ACT")),
        ("霍巴特", -42.88, 147.33, LocateResult("AU", "AU-TAS")),
        ("豪勋爵岛", -31.55, 159.08, LocateResult("AU", "AU-NSW")),
        ("洛杉矶", 34.05, -118.24, LocateResult("US", "US-CA")),
        ("华盛顿特区", 38.90, -77.04, LocateResult("US", "US-DC")),
        ("安克雷奇", 61.22, -149.90, LocateResult("US", "US-AK")),
        ("檀香山", 21.31, -157.86, LocateResult("US", "US-HI")),
        ("雷克雅未克", 64.15, -21.94, LocateResult("IS", None)),
        ("巴黎", 48.86, 2.35, LocateResult("FR", None)),
        ("奥斯陆", 59.91, 10.75, LocateResult("NO", None)),
        # 省界两侧 / Both sides of subnational borders
        ("黄金海岸 Currumbin", -28.13, 153.48, LocateResult("AU", "AU-QLD")),
        ("默威伦巴", -28.33, 153.40, LocateResult("AU", "AU-NSW")),
        ("廊坊", 39.52, 116.68, LocateResult("CN", "CN-13")),
        ("南太浩湖", 38.94, -119.98, LocateResult("US", "US-CA")),
        ("卡森城", 39.16, -119.77, LocateResult("US", "US-NV")),
        # 离岸 / Offshore
        ("悉尼以东约 45 km", -33.87, 151.75, LocateResult("AU", "AU-NSW")),
        ("塔斯曼海中部", -37.0, 162.0, LocateResult(None, None)),
    ],
)
def test_known_locations(locator, name, lat, lon, expected):
    """已知坐标定位正确 / Known coordinates resolve correctly."""
    assert locator.locate(lat, lon) == expected, name
```

- [ ] **Step 6: 运行真实库测试**

Run: `python3 -m pytest test_region_locator_real_db.py -v`
Expected: 23 passed。

若有失败：先用 `python3 -c "from birdid.region_locator import RegionLocator as R; print(R('birdid/data/ebird_regions.db').locate(LAT, LON))"` 看实际结果，判断是**坐标选得不好**（例如离边界不足 5 km，换一个更明确的点并在注释写明）还是**定位逻辑/数据错误**（回到 Task 3/4 修，补单测）。不得通过删除用例来过关；「塔斯曼海中部」若因诺福克岛等小岛命中，换一个距所有陆地 >400 km 的点。

- [ ] **Step 7: 性能检查（spec §8 验收 8）**

```bash
python3 - <<'EOF'
import random, statistics, time
t0 = time.perf_counter()
from birdid.region_locator import RegionLocator
loc = RegionLocator('birdid/data/ebird_regions.db')
loc.locate(39.9, 116.4)
print('cold ms', round((time.perf_counter() - t0) * 1000, 1))
random.seed(7)
pts = [(random.uniform(-45, 60), random.uniform(-160, 160)) for _ in range(300)]
cold = []
for lat, lon in pts:
    s = time.perf_counter(); loc.locate(lat, lon); cold.append((time.perf_counter() - s) * 1000)
hot = []
for lat, lon in pts:
    s = time.perf_counter(); loc.locate(lat, lon); hot.append((time.perf_counter() - s) * 1000)
print('uncached median ms', round(statistics.median(cold), 2), 'p95', round(sorted(cold)[284], 2))
print('cached max ms', round(max(hot), 3))
EOF
```

Expected：冷启动 ≤ 500 ms；未命中中位 ≤ 20 ms；缓存命中最大 ≤ 1 ms。不达标时优先检查是否每次都在解码大环（`_points` 缓存是否生效），不要先改算法。

- [ ] **Step 8: 提交**

```bash
git add birdid/region_locator.py
git add -f test_region_locator.py test_region_locator_real_db.py
git commit -m "feat(birdid): 离线 GPS 区域定位（Natural Earth 边界 + 200 海里离岸容差）

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: 重写 `birdid/geo_filter.py` 为 eBird 区域过滤

**Files:**
- Modify（整体重写）: `birdid/geo_filter.py`
- Modify: `locales/zh_CN.json`、`locales/en_US.json`（`birdid.geo_tier_*`、`logs.geo_*`）
- Create: `test_region_filter.py`
- Delete: `test_geo_filter.py`（网格版单测，被 `test_region_filter.py` 取代）

**Interfaces:**
- Consumes: Task 5 的数据库表结构（本模块不导入 `birdid.region_locator`，避免循环依赖）
- Produces:
  - `TIER_SUBNATIONAL = "region_subnational"`、`TIER_COUNTRY = "region_country"`、`TIER_NONE = "none"`
  - `DB_FILENAME = "ebird_regions.db"`
  - `default_db_path() -> str`
  - `class RegionFilter(db_path: Optional[str] = None)`：
    - `is_available() -> bool`
    - `has_region(code: Optional[str]) -> bool`
    - `parent_of(code: str) -> Optional[str]`
    - `display_name(code: str, english: bool) -> str`
    - `species_for(code: str) -> Set[int]`
    - `iter_candidates(gps_country: Optional[str], gps_subnational: Optional[str], manual_country: Optional[str], manual_subnational: Optional[str]) -> Iterator[Tuple[Optional[Set[int]], str, Optional[str]]]`（产出 `(候选集或 None, 层标签, 该层区域代码或 None)`，最后一项恒为 `(None, TIER_NONE, None)`）
    - `close() -> None`
  - `describe_tier(geo_info: Optional[dict]) -> str`（读取 `tier`、`species_count`、`region_code`）
  - `get_geo_filter() -> Optional[RegionFilter]`（注册键 `"birdid.geo_filter"` 不变）

- [ ] **Step 1: 写失败测试 `test_region_filter.py`**

```python
# -*- coding: utf-8 -*-
"""
eBird 区域过滤器单测 / Unit tests for the eBird region filter.
"""
import pytest

from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL, RegionFilter
from scripts_dev.build_ebird_regions import write_database


@pytest.fixture
def region_filter(tmp_path):
    """
    AU 国家 {1,2,3}、AU-NSW {1,2}、AU-QLD 空清单、IS {9}、JP 空国家。
    """
    path = tmp_path / "regions.db"
    write_database(
        str(path),
        regions=[
            ("AU", None, "Australia", "澳大利亚", 3),
            ("AU-NSW", "AU", "New South Wales", "新南威尔士州", 2),
            ("AU-QLD", "AU", "Queensland", "昆士兰州", 0),
            ("IS", None, "Iceland", "冰岛", 1),
            ("JP", None, "Japan", "日本", 0),
        ],
        region_species={"AU": {1, 2, 3}, "AU-NSW": {1, 2}, "IS": {9}},
        rings=[],
        meta={"fetched_at": "2026-09-16"},
    )
    f = RegionFilter(str(path))
    yield f
    f.close()


def labels(items):
    """只取 (层标签, 区域) / Keep only (tier, region)."""
    return [(tier, region) for _, tier, region in items]


def test_gps_subnational_then_country(region_filter):
    """有 GPS：省州 → 国家 → 不过滤 / GPS: subnational, country, none."""
    out = list(region_filter.iter_candidates("AU", "AU-NSW", None, None))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]
    assert out[0][0] == {1, 2} and out[1][0] == {1, 2, 3} and out[2][0] is None


def test_gps_wins_over_manual(region_filter):
    """GPS 与手选同时存在时以 GPS 为准 / GPS beats the manual selection."""
    out = list(region_filter.iter_candidates("IS", None, "AU", "AU-NSW"))
    assert labels(out) == [(TIER_COUNTRY, "IS"), (TIER_NONE, None)]


def test_no_gps_uses_manual_subnational(region_filter):
    """无 GPS 用手选省州（老配置 AU-NSW 回归，spec §2.5）/ Legacy AU-NSW works again."""
    out = list(region_filter.iter_candidates(None, None, "AU", "AU-NSW"))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_manual_subnational_must_match_country(region_filter):
    """省州与国家不一致视为整个国家 / A mismatched subnational falls back to the country."""
    out = list(region_filter.iter_candidates(None, None, "IS", "AU-NSW"))
    assert labels(out) == [(TIER_COUNTRY, "IS"), (TIER_NONE, None)]


def test_unknown_or_global_manual_is_unfiltered(region_filter):
    """GLOBAL、未知代码都不过滤 / GLOBAL and unknown codes are unfiltered."""
    assert labels(region_filter.iter_candidates(None, None, "GLOBAL", None)) == [(TIER_NONE, None)]
    assert labels(region_filter.iter_candidates(None, None, "ZZ", None)) == [(TIER_NONE, None)]


def test_unknown_gps_country_falls_back_to_manual(region_filter):
    """GPS 国家不在库里时改用手选 / An unknown GPS country falls back to manual."""
    out = list(region_filter.iter_candidates("ZZ", None, "AU", None))
    assert labels(out) == [(TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_empty_lists_are_skipped(region_filter):
    """空清单层被跳过，不会屏蔽全部类别 / Empty lists are skipped."""
    assert labels(region_filter.iter_candidates("AU", "AU-QLD", None, None)) == [
        (TIER_COUNTRY, "AU"), (TIER_NONE, None)
    ]
    assert labels(region_filter.iter_candidates("JP", None, None, None)) == [(TIER_NONE, None)]


def test_display_name(region_filter):
    """中英文名 / Display names."""
    assert region_filter.display_name("AU-NSW", english=False) == "新南威尔士州"
    assert region_filter.display_name("AU-NSW", english=True) == "New South Wales"
    assert region_filter.display_name("ZZ", english=True) == "ZZ"


def test_missing_db_is_unavailable(tmp_path):
    """库不存在时不可用且只产出不过滤 / Missing DB yields only the unfiltered tier."""
    f = RegionFilter(str(tmp_path / "missing.db"))
    assert f.is_available() is False
    assert labels(f.iter_candidates("AU", "AU-NSW", None, None)) == [(TIER_NONE, None)]


def test_describe_tier_covers_every_tier(monkeypatch, region_filter):
    """每层都有本地化文案 / Every tier has localized text."""
    import birdid.geo_filter as gf
    from birdid.geo_filter import describe_tier

    monkeypatch.setattr(gf, "get_geo_filter", lambda: region_filter)
    for tier, region in ((TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)):
        text = describe_tier({"tier": tier, "species_count": 42, "region_code": region})
        assert text and not text.startswith("birdid."), f"{tier}: {text}"
    assert describe_tier(None) and not describe_tier(None).startswith("birdid.")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest test_region_filter.py -v`
Expected: FAIL，`ImportError: cannot import name 'TIER_SUBNATIONAL'`

- [ ] **Step 3: 整体重写 `birdid/geo_filter.py`**

```python
# -*- coding: utf-8 -*-
"""
eBird 区域候选过滤器 / eBird region candidate filter.

基于 `birdid/data/ebird_regions.db`（eBird 清单派生的模型类别编号）按层产出候选集：
省州（仅中澳美）→ 国家 → 不过滤。有 GPS 时用 GPS 定位结果，否则用用户手选。
取代 4.6 的 GBIF 1° 网格实现（spec docs/specs/2026-09-16-ebird-region-filter-design.md）。

Yields candidate sets from ebird_regions.db in widening tiers: subnational
(CN/AU/US only), country, unfiltered. GPS location is used when available,
otherwise the user's manual selection. Replaces the 4.6 GBIF 1-degree grid.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from typing import Dict, Iterator, Optional, Set, Tuple

from tools.i18n import t as _t

TIER_SUBNATIONAL = "region_subnational"
TIER_COUNTRY = "region_country"
TIER_NONE = "none"

DB_FILENAME = "ebird_regions.db"


def default_db_path() -> str:
    """
    解析 ebird_regions.db 路径，兼容开发与打包环境 / Resolve the database path.

    返回 / Returns:
        str: 绝对路径 / Absolute path.
    """
    rel = os.path.join("birdid", "data", DB_FILENAME)
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


class RegionFilter:
    """
    eBird 区域候选过滤器 / eBird region candidate filter.

    区域元数据在构造时全部读入（约 340 行）；物种集合按需读取并缓存，
    避免一次性把几十万个类别编号装进内存。

    Region metadata (about 340 rows) is loaded up front; species sets are read on
    demand and cached, rather than loading hundreds of thousands of ids at once.

    参数 / Parameters:
        db_path (Optional[str]): 数据库路径；None 时自动解析 / Path, auto-resolved when None.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._regions: Dict[str, Tuple[Optional[str], str, str]] = {}
        self._species: Dict[str, frozenset] = {}
        if not os.path.exists(self.db_path):
            return
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            for code, parent, name_en, name_zh in self._conn.execute(
                "SELECT code, parent, name_en, name_zh FROM regions"
            ):
                self._regions[str(code)] = (parent, str(name_en), str(name_zh))
        except sqlite3.Error as e:
            print(_t("logs.geo_db_failed", e=e))
            self._conn = None
            self._regions = {}

    def is_available(self) -> bool:
        """
        数据库是否可用 / Whether the database is usable.

        返回 / Returns:
            bool: 连接正常且有区域数据 / Connected with region data.
        """
        return self._conn is not None and bool(self._regions)

    def has_region(self, code: Optional[str]) -> bool:
        """
        区域代码是否存在 / Whether a region code exists.

        参数 / Parameters:
            code (Optional[str]): 区域代码 / Region code.

        返回 / Returns:
            bool: 存在为 True / True when present.
        """
        return bool(code) and code in self._regions

    def parent_of(self, code: str) -> Optional[str]:
        """
        省州的上级国家；国家或未知代码返回 None / Parent country of a subnational code.

        参数 / Parameters:
            code (str): 区域代码 / Region code.

        返回 / Returns:
            Optional[str]: 国家代码或 None / Country code or None.
        """
        entry = self._regions.get(code)
        return entry[0] if entry else None

    def display_name(self, code: str, english: bool) -> str:
        """
        区域显示名 / Display name of a region.

        参数 / Parameters:
            code (str): 区域代码 / Region code.
            english (bool): True 取英文名 / English when True.

        返回 / Returns:
            str: 名称；未知代码返回代码本身 / Name, or the code when unknown.
        """
        entry = self._regions.get(code)
        if not entry:
            return code
        return entry[1] if english else (entry[2] or entry[1])

    def species_for(self, code: str) -> Set[int]:
        """
        区域候选类别 / Candidate classes of a region.

        参数 / Parameters:
            code (str): 区域代码 / Region code.

        返回 / Returns:
            set[int]: 类别集合的副本；失败或未知时为空集 / A copy; empty on failure.
        """
        if self._conn is None or code not in self._regions:
            return set()
        with self._lock:
            cached = self._species.get(code)
            if cached is None:
                try:
                    rows = self._conn.execute(
                        "SELECT class_id FROM region_species WHERE region=?", (code,)
                    ).fetchall()
                except sqlite3.Error as e:
                    print(_t("logs.geo_region_failed", e=e))
                    return set()
                cached = frozenset(int(r[0]) for r in rows)
                self._species[code] = cached
        return set(cached)

    def iter_candidates(
        self,
        gps_country: Optional[str],
        gps_subnational: Optional[str],
        manual_country: Optional[str],
        manual_subnational: Optional[str],
    ) -> Iterator[Tuple[Optional[Set[int]], str, Optional[str]]]:
        """
        按层产出候选集，调用方逐层放宽直到有结果 / Yield tiers until recognition succeeds.

        GPS 国家存在于库中时只走 GPS 链（照片可能拍于他处，手选不应覆盖实际拍摄地）；
        否则走手选链。省州必须隶属于同链的国家，不一致的省州按「整个国家」处理——
        老配置里残留的省州代码因此不会再让过滤失效（spec §2.5、§6.3）。空清单层被跳过。

        When the GPS country exists in the database only the GPS chain is used (the
        photo may be from elsewhere, so the manual choice must not override it);
        otherwise the manual chain is used. A subnational code must belong to the
        chain's country or it is treated as "entire country". Empty tiers are skipped.

        参数 / Parameters:
            gps_country (Optional[str]): GPS 定位国家 / Country from GPS.
            gps_subnational (Optional[str]): GPS 定位省州 / Subnational from GPS.
            manual_country (Optional[str]): 手选国家，"GLOBAL" 视同未选 / Manual country.
            manual_subnational (Optional[str]): 手选省州 / Manual subnational.

        返回 / Returns:
            Iterator[tuple]: (候选集或 None, 层标签, 区域代码或 None)，最后一项恒为
                (None, TIER_NONE, None) / Last item is always the unfiltered tier.
        """
        if not self.is_available():
            yield None, TIER_NONE, None
            return

        if self._is_country(gps_country):
            country, subnational = gps_country, gps_subnational
        elif self._is_country(manual_country):
            country, subnational = manual_country, manual_subnational
        else:
            country, subnational = None, None

        if country and subnational and self.parent_of(subnational) == country:
            species = self.species_for(subnational)
            if species:
                yield species, TIER_SUBNATIONAL, subnational
        if country:
            species = self.species_for(country)
            if species:
                yield species, TIER_COUNTRY, country
        yield None, TIER_NONE, None

    def _is_country(self, code: Optional[str]) -> bool:
        """
        是否为库中的国家代码 / Whether a code is a known country.

        参数 / Parameters:
            code (Optional[str]): 代码 / Code.

        返回 / Returns:
            bool: 国家为 True / True for a country.
        """
        return self.has_region(code) and self.parent_of(str(code)) is None

    def close(self) -> None:
        """关闭连接 / Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "RegionFilter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False


_TIER_I18N_KEYS = {
    TIER_SUBNATIONAL: "birdid.geo_tier_subnational",
    TIER_COUNTRY: "birdid.geo_tier_country",
    TIER_NONE: "birdid.geo_tier_none",
}


def describe_tier(geo_info: Optional[dict]) -> str:
    """
    把 geo_info 渲染成一行过滤状态说明 / Render geo_info as one status line.

    参数 / Parameters:
        geo_info (Optional[dict]): identify_bird 返回的 geo_info / geo_info from identify_bird.

    返回 / Returns:
        str: 本地化文本；None 按未过滤处理 / Localized text; None means unfiltered.
    """
    info = geo_info or {}
    tier = info.get("tier", TIER_NONE)
    key = _TIER_I18N_KEYS.get(tier, _TIER_I18N_KEYS[TIER_NONE])
    code = info.get("region_code") or ""
    name = code
    if code:
        try:
            from tools.i18n import get_i18n

            english = str(get_i18n().current_lang or "").startswith("en")
            geo = get_geo_filter()
            if geo is not None:
                name = geo.display_name(code, english)
        except Exception:  # noqa: BLE001
            name = code
    return _t(key, count=info.get("species_count") or 0, region=name)


def get_geo_filter() -> Optional[RegionFilter]:
    """
    进程级单例 / Process-wide singleton.

    返回 / Returns:
        Optional[RegionFilter]: 可用时返回实例，否则 None / Instance or None.
    """
    from config import get_lazy_registry

    def _factory() -> Optional[RegionFilter]:
        try:
            f = RegionFilter()
            if f.is_available():
                return f
            print(_t("logs.geo_unavailable"))
        except Exception as e:  # noqa: BLE001
            print(_t("logs.geo_init_failed", e=e))
        return None

    return get_lazy_registry().get_or_create("birdid.geo_filter", _factory)
```

- [ ] **Step 4: 更新 locales**

用 Python 按键修改两个语言文件（保持 2 空格缩进、`ensure_ascii=False`、末尾换行）：

```bash
python3 - <<'EOF'
import json
changes = {
    "zh_CN": {
        ("birdid", "geo_tier_subnational"): "🗺️ 按省/州过滤：{region}（{count} 种）",
        ("birdid", "geo_tier_country"): "🗺️ 按国家过滤：{region}（{count} 种）",
        ("birdid", "geo_tier_none"): "⚠️ 无地理过滤，按全球鸟种识别",
        ("logs", "geo_region_failed"): "[GeoFilter] 区域查询失败: {e}",
        ("logs", "geo_unavailable"): "[GeoFilter] 区域清单库不可用，地理过滤将被跳过",
    },
    "en_US": {
        ("birdid", "geo_tier_subnational"): "🗺️ Filtered by state/province: {region} ({count} species)",
        ("birdid", "geo_tier_country"): "🗺️ Filtered by country: {region} ({count} species)",
        ("birdid", "geo_tier_none"): "⚠️ No geographic filter, identifying against all species",
        ("logs", "geo_region_failed"): "[GeoFilter] Region query failed: {e}",
        ("logs", "geo_unavailable"): "[GeoFilter] Region list DB unavailable, geo filtering skipped",
    },
}
removed = [("birdid", "geo_tier_cell_strong"), ("birdid", "geo_tier_cell_all"),
           ("birdid", "geo_tier_neighborhood"), ("logs", "geo_cell_failed"), ("logs", "geo_country_failed")]
for lang, kv in changes.items():
    path = f"locales/{lang}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for section, key in removed:
        data[section].pop(key, None)
    for (section, key), value in kv.items():
        data[section][key] = value
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
EOF
git diff --stat locales/
```

Expected：两个文件各只变动十余行。若 `git diff --stat` 显示整文件重排（数百行），说明原文件格式与 `json.dump` 不同——`git checkout locales/` 撤销，改用 Edit 工具逐键修改。

- [ ] **Step 5: 删除网格版单测并运行**

```bash
git rm test_geo_filter.py
python3 -m pytest test_region_filter.py -v
python3 -m py_compile birdid/geo_filter.py
```

Expected: 10 passed。
（`test_geo_filter_wiring.py`、`bird_identifier.py` 此时仍引用旧常量而导入失败，是预期状态，Task 8 修复；本任务不要运行全量测试。）

- [ ] **Step 6: 提交**

```bash
git add birdid/geo_filter.py locales/zh_CN.json locales/en_US.json
git add -f test_region_filter.py
git commit -m "refactor(birdid): geo_filter 改为 eBird 区域候选（省州→国家→不过滤）

GPS 国家有效时只走 GPS 链；省州须隶属同链国家，老配置残留省州代码不再导致不过滤。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `identify_bird` 接线（去掉 reverse_geocoder）

**Files:**
- Modify: `birdid/bird_identifier.py`（约 `:23`、`:45-53`、`:65-87`、`:1003-1063`、`:1114-1195`）
- Modify（整体重写）: `test_geo_filter_wiring.py`

**Interfaces:**
- Consumes:
  - Task 6：`birdid.region_locator.get_region_locator() -> Optional[RegionLocator]`，`RegionLocator.locate(lat, lon) -> LocateResult`
  - Task 7：`TIER_NONE`、`get_geo_filter()`、`RegionFilter.iter_candidates(gps_country, gps_subnational, manual_country, manual_subnational)`
- Produces:
  - `_locate_gps(lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]`
  - `_identify_with_tiers(image, top_k: int, gps_country: Optional[str], gps_subnational: Optional[str], manual_country: Optional[str], manual_subnational: Optional[str], is_yolo_cropped: bool, name_format: Optional[str], photo_country_code: Optional[str]) -> Tuple[List[Dict], str, Optional[int], Optional[str]]`
  - `identify_bird(...)` 签名不变；返回的 `gps_info` 可含 `country_code`、`subnational_code`；`geo_info` 键为 `enabled`、`tier`、`species_count`、`region_code`（**不再有 `country_code`**）

- [ ] **Step 1: 重写 `test_geo_filter_wiring.py`（先失败）**

```python
# -*- coding: utf-8 -*-
"""
identify_bird 区域候选接线测试 / Wiring tests for region candidates in identify_bird.

用假的过滤器与假的 predict 验证「逐层放宽、命中即停」，以及 GPS 定位与手选
分开传给过滤器，不加载真实模型。

Verifies widen-until-hit with a fake filter and predict, and that GPS location
and manual selection reach the filter separately, without loading the model.
"""
from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL


class FakeFilter:
    """按预设脚本产出候选层并记录调用参数 / Scripted tiers; records call arguments."""

    def __init__(self, tiers):
        self._tiers = tiers
        self.calls = []

    def is_available(self) -> bool:
        return True

    def iter_candidates(self, gps_country, gps_subnational, manual_country, manual_subnational):
        self.calls.append((gps_country, gps_subnational, manual_country, manual_subnational))
        return iter(self._tiers)


def run_tiers(bi, **overrides):
    """用默认参数调用 _identify_with_tiers / Call _identify_with_tiers with defaults."""
    kwargs = dict(
        top_k=1, gps_country=None, gps_subnational=None, manual_country=None,
        manual_subnational=None, is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    kwargs.update(overrides)
    return bi._identify_with_tiers(object(), **kwargs)


def test_stops_at_first_tier_with_results(monkeypatch):
    """省州层就有结果 → 不再放宽 / Stop at the first tier with results."""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 1, "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([
        ({1, 2}, TIER_SUBNATIONAL, "AU-NSW"),
        ({1, 2, 3}, TIER_COUNTRY, "AU"),
        (None, TIER_NONE, None),
    ]))
    results, tier, used, region = run_tiers(bi, gps_country="AU", gps_subnational="AU-NSW")
    assert (tier, used, region) == (TIER_SUBNATIONAL, 2, "AU-NSW")
    assert len(calls) == 1


def test_widens_when_tier_empty(monkeypatch):
    """省州层无结果 → 放宽到国家 / Widen to the country when the subnational tier is empty."""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        return [{"class_id": 3, "confidence": 70.0}] if species_class_ids and 3 in species_class_ids else []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([
        ({1, 2}, TIER_SUBNATIONAL, "AU-NSW"),
        ({1, 2, 3}, TIER_COUNTRY, "AU"),
        (None, TIER_NONE, None),
    ]))
    results, tier, used, region = run_tiers(bi)
    assert (tier, used, region) == (TIER_COUNTRY, 3, "AU")
    assert results[0]["class_id"] == 3


def test_falls_through_to_unfiltered(monkeypatch):
    """所有层都无结果 → 不过滤 / Fall through to unfiltered."""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        return [{"class_id": 9, "confidence": 50.0}] if species_class_ids is None else []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([({5}, TIER_COUNTRY, "AU"), (None, TIER_NONE, None)]))
    results, tier, used, region = run_tiers(bi, manual_country="AU")
    assert (tier, used, region) == (TIER_NONE, None, None)
    assert results[0]["class_id"] == 9


def test_no_filter_available(monkeypatch):
    """过滤器不可用 → 直接无过滤识别一次 / No filter: identify once unfiltered."""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 7, "confidence": 60.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: None)
    _, tier, _, region = run_tiers(bi, gps_country="AU")
    assert (tier, region) == (TIER_NONE, None)
    assert calls == [None]


def _patch_identify(monkeypatch, bi, gps, located):
    """
    替换 identify_bird 的外部依赖 / Stub identify_bird's external dependencies.

    参数 / Parameters:
        gps (tuple): extract_gps_from_exif 的返回 / Return of extract_gps_from_exif.
        located (tuple): _locate_gps 的返回 / Return of _locate_gps.

    返回 / Returns:
        FakeFilter: 记录了调用参数的假过滤器 / The recording fake filter.
    """
    fake = FakeFilter([({1}, TIER_SUBNATIONAL, "AU-NSW"), (None, TIER_NONE, None)])
    monkeypatch.setattr(bi, "get_geo_filter", lambda: fake)
    monkeypatch.setattr(bi, "predict_bird", lambda image, **kw: [{"class_id": 1, "confidence": 80.0}])
    monkeypatch.setattr(bi, "extract_gps_from_exif", lambda path: gps)
    monkeypatch.setattr(bi, "_locate_gps", lambda lat, lon: located)
    return fake


def test_identify_passes_manual_region_without_gps(monkeypatch):
    """无 GPS：手选国家与省州原样传给过滤器（spec §2.5 回归）/ Manual selection reaches the filter."""
    from birdid import bird_identifier as bi

    fake = _patch_identify(monkeypatch, bi, gps=(None, None, "no gps"), located=(None, None))
    result = bi.identify_bird("x.jpg", country_code="AU", region_code="AU-NSW", preloaded_crop=object())
    assert fake.calls == [(None, None, "AU", "AU-NSW")]
    assert result["geo_info"] == {"enabled": True, "tier": TIER_SUBNATIONAL, "species_count": 1, "region_code": "AU-NSW"}


def test_identify_passes_gps_location(monkeypatch):
    """有 GPS：定位结果写入 gps_info 并传给过滤器 / GPS location is recorded and passed on."""
    from birdid import bird_identifier as bi

    fake = _patch_identify(monkeypatch, bi, gps=(-33.87, 151.21, "ok"), located=("AU", "AU-NSW"))
    result = bi.identify_bird("x.jpg", country_code="CN", region_code="CN-11", preloaded_crop=object())
    assert fake.calls == [("AU", "AU-NSW", "CN", "CN-11")]
    assert result["gps_info"]["country_code"] == "AU"
    assert result["gps_info"]["subnational_code"] == "AU-NSW"


def test_reverse_geocoder_is_gone():
    """不再引用 reverse_geocoder / reverse_geocoder is no longer referenced."""
    import inspect

    from birdid import bird_identifier as bi

    source = inspect.getsource(bi)
    assert "import reverse_geocoder" not in source and "_RG_" not in source
    assert not hasattr(bi, "_resolve_country_code_from_gps")
```

Run: `python3 -m pytest test_geo_filter_wiring.py -v`
Expected: FAIL（导入 `bird_identifier` 时 `TIER_*` 或 `_identify_with_tiers` 签名不符）

- [ ] **Step 2: 删除 reverse_geocoder 单例全局量**

用 Edit 把 `birdid/bird_identifier.py` 中：

```python
# V4.2.7: reverse_geocoder lazy 单例 — 首次用时加载 ~70MB cKDTree 数据，
# 之后所有线程共享同一个只读索引。
# V4.2.7: reverse_geocoder lazy singleton — first use loads ~70MB cKDTree,
# subsequent calls share the read-only index across threads.
import threading

_RG_LOCK = threading.Lock()
_RG_INSTANCE: Any = None  # 标记是否已初始化（None 表示未尝试）
_RG_AVAILABLE = True
```

替换为：

```python
import threading
```

- [ ] **Step 3: 用 `_locate_gps` 替换 `_resolve_country_code_from_gps`**

用 Edit 把整个 `def _resolve_country_code_from_gps(...)` 函数（从 `def` 行到 `    return None` 结束，约 23 行）替换为：

```python
def _locate_gps(lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]:
    """
    GPS → eBird 国家与省州代码 / GPS to eBird country and subnational codes.

    旧实现依赖 reverse_geocoder，但它从未进入打包版，打包版里判国一直静默失效
    （spec §2.4）。现改用 ebird_regions.db 自带的边界离线定位。

    The old implementation relied on reverse_geocoder, which never shipped in
    packaged builds, so country lookup silently failed there (spec section 2.4).
    This uses the boundaries bundled in ebird_regions.db instead.

    参数 / Parameters:
        lat (float): 纬度 / Latitude.
        lon (float): 经度 / Longitude.

    返回 / Returns:
        tuple: (国家代码或 None, 省州代码或 None)；定位器不可用或出错时均为 None /
            (country or None, subnational or None); both None when unavailable.
    """
    try:
        from birdid.region_locator import get_region_locator

        locator = get_region_locator()
        if locator is None:
            return None, None
        located = locator.locate(lat, lon)
        return located.country, located.subnational
    except Exception:  # noqa: BLE001
        return None, None
```

- [ ] **Step 4: 重写 `_identify_with_tiers`**

用 Edit 把整个 `def _identify_with_tiers(...)` 函数（到 `    return [], TIER_NONE, None` 为止）替换为：

```python
def _identify_with_tiers(
    image,
    top_k: int,
    gps_country: Optional[str],
    gps_subnational: Optional[str],
    manual_country: Optional[str],
    manual_subnational: Optional[str],
    is_yolo_cropped: bool,
    name_format: Optional[str],
    photo_country_code: Optional[str],
) -> Tuple[List[Dict], str, Optional[int], Optional[str]]:
    """
    遍历区域候选层，命中即停 / Walk the region candidate tiers, stopping at the first hit.

    注意 predict_bird 只有在 top-100 中无一落入候选集时才返回空，因此放宽只在候选集
    完全不含相近种时发生；效果取决于候选集是否准确，而不是放宽机制。

    predict_bird returns nothing only when none of its top 100 falls in the
    candidate set, so widening happens only when the set lacks any similar
    species; accuracy depends on the set, not on the widening.

    参数 / Parameters:
        image: 待识别图像 / Image to identify.
        top_k (int): 返回结果数 / Number of results.
        gps_country (Optional[str]): GPS 定位国家 / Country from GPS.
        gps_subnational (Optional[str]): GPS 定位省州 / Subnational from GPS.
        manual_country (Optional[str]): 手选国家 / Manually selected country.
        manual_subnational (Optional[str]): 手选省州 / Manually selected subnational.
        is_yolo_cropped (bool): 是否已由 YOLO 裁剪 / Whether YOLO already cropped.
        name_format (Optional[str]): 鸟名格式 / Bird name format.
        photo_country_code (Optional[str]): 拍摄国家，供罕见度使用 / Shooting country for rarity.

    返回 / Returns:
        tuple: (结果列表, 层标签, 该层候选数或 None, 该层区域代码或 None) /
            (results, tier label, candidate count or None, region code or None).
    """
    geo = get_geo_filter()
    if geo is None:
        results = predict_bird(
            image,
            top_k=top_k,
            species_class_ids=None,
            is_yolo_cropped=is_yolo_cropped,
            name_format=name_format,
            photo_country_code=photo_country_code,
        )
        return results, TIER_NONE, None, None

    for candidates, tier, region in geo.iter_candidates(
        gps_country, gps_subnational, manual_country, manual_subnational
    ):
        results = predict_bird(
            image,
            top_k=top_k,
            species_class_ids=candidates,
            is_yolo_cropped=is_yolo_cropped,
            name_format=name_format,
            photo_country_code=photo_country_code,
        )
        if results:
            return results, tier, (len(candidates) if candidates else None), region
    return [], TIER_NONE, None, None
```

- [ ] **Step 5: 改 `identify_bird` 的 GPS 与过滤段**

用 Edit 把 `identify_bird` 中从 `        lat = lon = None` 开始、到 `else:` 分支里 `"country_code": None,\n            }` 结束的整段替换为：

```python
        lat = lon = None
        photo_country_code: Optional[str] = None
        gps_subnational: Optional[str] = None

        # 提取 GPS 并离线定位到 eBird 国家/省州；定位结果同时供罕见度与地理过滤使用
        # Extract GPS and locate the eBird country/subnational offline; the result
        # feeds both rarity and the geo filter.
        if use_gps:
            try:
                lat, lon, _gps_msg = extract_gps_from_exif(image_path)
                # 0.0 是合法坐标（赤道/本初子午线），必须用 is not None 语义判断
                # 0.0 is a legal coordinate — presence must use is-not-None semantics.
                if _gps_coords_present(lat, lon):
                    result["gps_info"] = {
                        "latitude": lat,
                        "longitude": lon,
                        "info": _gps_msg,
                    }
                    photo_country_code, gps_subnational = _locate_gps(lat, lon)
                    if photo_country_code:
                        result["gps_info"]["country_code"] = photo_country_code
                    if gps_subnational:
                        result["gps_info"]["subnational_code"] = gps_subnational
            except Exception:
                pass

        # 地理过滤：有可定位的 GPS 时按拍摄地的省州/国家，否则按手选；GPS 与手选分开传入，
        # 不再把省州代码当国家代码混用（spec §2.5 的根因）。
        # Geo filter: use the photo's located subnational/country when available,
        # otherwise the manual selection. They are passed separately instead of
        # conflating a subnational code with a country code (root cause, spec 2.5).
        if use_geo_filter:
            results, tier, count, used_region = _identify_with_tiers(
                image,
                top_k=top_k,
                gps_country=photo_country_code,
                gps_subnational=gps_subnational,
                manual_country=country_code,
                manual_subnational=region_code,
                is_yolo_cropped=is_yolo_cropped,
                name_format=name_format,
                photo_country_code=photo_country_code,
            )
            result["geo_info"] = {
                "enabled": tier != TIER_NONE,
                "tier": tier,
                "species_count": count,
                "region_code": used_region,
            }
        else:
            results = predict_bird(
                image,
                top_k=top_k,
                species_class_ids=None,
                is_yolo_cropped=is_yolo_cropped,
                name_format=name_format,
                photo_country_code=photo_country_code,
            )
            result["geo_info"] = {
                "enabled": False,
                "tier": TIER_NONE,
                "species_count": None,
                "region_code": None,
            }
```

- [ ] **Step 6: 检查残留并运行测试**

```bash
grep -n "_RG_\|import reverse_geocoder\|_resolve_country_code_from_gps\|effective_region" birdid/bird_identifier.py
python3 -m py_compile birdid/bird_identifier.py
python3 -m pytest test_geo_filter_wiring.py test_bird_identifier_gps_zero_coords.py -v
```

Expected：grep 无输出；测试 10 passed（wiring 7 + zero coords 3）。
若 `Any` 因删除 `_RG_INSTANCE: Any` 变成未使用导入，保留即可（文件其他位置使用了 `cast(Any, ...)`；用 `grep -n "Any" birdid/bird_identifier.py` 确认）。

- [ ] **Step 7: 提交**

```bash
git add birdid/bird_identifier.py
git add -f test_geo_filter_wiring.py
git commit -m "refactor(birdid): identify_bird 改用离线区域定位并分开传 GPS 与手选地区

移除从未进入打包版的 reverse_geocoder；geo_info.country_code 改为 region_code。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: 调用方 `birdid_server.py` / `birdid_cli.py` / `ui/birdid_dock.py`

`birdid_cli.py:155` 与 `ui/birdid_dock.py:1768` 只调用 `describe_tier`，签名不变、无需改代码；本任务改的是服务端读取 `geo_info` 的旧键、告警条件，以及把 GPS 定位到的省州同步回设置。

**Files:**
- Modify: `birdid_server.py`（`update_gui_settings_from_gps` 约 `:96-135`；识别结果处理约 `:377-425`）
- Test: `test_birdid_server_geo.py`（新建）

**Interfaces:**
- Consumes: Task 7 `TIER_COUNTRY`、`TIER_NONE`、`describe_tier`、`get_geo_filter`；Task 6 `SUBNATIONAL_COUNTRIES`；Task 8 的 `geo_info` / `gps_info` 键
- Produces:
  - `update_gui_settings_from_gps(country_code: str, subnational_code: Optional[str] = None) -> None`
  - `geo_filter_warning(geo_info: Optional[dict]) -> Optional[str]`（新增模块级函数，便于测试）

- [ ] **Step 1: 写失败测试 `test_birdid_server_geo.py`**

```python
# -*- coding: utf-8 -*-
"""
识鸟服务端地理过滤相关逻辑单测 / Server-side geo filter logic tests.
"""
import tempfile

from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL


def test_warning_rules():
    """
    告警条件：未过滤；或中澳美照片只落到国家级（说明省州层没用上）。
    其他国家的国家级是正常层级，不告警。
    """
    from birdid_server import geo_filter_warning

    assert geo_filter_warning({"tier": TIER_SUBNATIONAL, "region_code": "AU-NSW", "species_count": 600}) is None
    assert geo_filter_warning({"tier": TIER_COUNTRY, "region_code": "IS", "species_count": 400}) is None
    assert geo_filter_warning({"tier": TIER_COUNTRY, "region_code": "AU", "species_count": 900})
    assert geo_filter_warning({"tier": TIER_NONE, "region_code": None, "species_count": None})
    assert geo_filter_warning(None)


def test_update_gui_settings_writes_subnational(monkeypatch):
    """GPS 同步把国家与省州一起写入配置 / GPS sync writes country and subnational."""
    import advanced_config as ac
    import birdid_server

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as f:
        f.write("{}")
        path = f.name
    cfg = ac.AdvancedConfig(config_file=path)
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)

    birdid_server.update_gui_settings_from_gps("AU", "AU-NSW")
    assert cfg.birdid_country_code == "AU"
    assert cfg.birdid_region_code == "AU-NSW"

    birdid_server.update_gui_settings_from_gps("IS")
    assert cfg.birdid_country_code == "IS"
    assert cfg.birdid_region_code is None
```

Run: `python3 -m pytest test_birdid_server_geo.py -v`
Expected: FAIL，`ImportError: cannot import name 'geo_filter_warning'`

- [ ] **Step 2: 重写 `update_gui_settings_from_gps`**

先 `Read birdid_server.py` 的 90–140 行确认函数边界，再用 Edit 把整个函数替换为：

```python
def update_gui_settings_from_gps(country_code: str, subnational_code: Optional[str] = None) -> None:
    """
    把 GPS 定位到的国家/省州同步到设置中心 / Sync the GPS-located region to settings.

    国家与省州由 identify_bird 用 ebird_regions.db 边界离线定位，放在 gps_info 里，
    此处不再重复判定。省州只对中澳美存在，其他国家传 None。

    The country and subnational unit were located offline by identify_bird from
    the ebird_regions.db boundaries and carried in gps_info; no second lookup.

    参数 / Parameters:
        country_code (str): eBird 国家代码 / eBird country code.
        subnational_code (Optional[str]): eBird 省州代码 / eBird subnational code.

    异常 / Exceptions:
        不抛出异常；失败仅打印日志 / Never raises; failures are logged only.
    """
    if not country_code:
        return
    try:
        from advanced_config import get_advanced_config
        from birdid.geo_filter import get_geo_filter
        from tools.country_names import country_display_names

        cfg = get_advanced_config()
        english = str(cfg.language or "").startswith("en")
        geo = get_geo_filter()
        if geo is not None:
            country_display = geo.display_name(country_code, english)
            region_display = geo.display_name(subnational_code, english) if subnational_code else ""
        else:
            en_name, zh_name = country_display_names(country_code)
            country_display = en_name if english else zh_name
            region_display = subnational_code or ""
        cfg.set_birdid_region(
            cfg.birdid_use_geo_filter,
            country_code,
            country_display,
            subnational_code,
            region_display,
        )
        print(t("server.sync_gps_success", country=country_display, region=region_display))
    except Exception as e:
        print(t("server.sync_gps_failed", error=e))


def geo_filter_warning(geo_info: Optional[dict]) -> Optional[str]:
    """
    识别结果需要附带的地理过滤告警 / Geo filter warning to attach to a result.

    未过滤一定告警；中澳美照片只落到国家级说明省州层没用上（没有可用省州或候选里
    没有相近种），也告警；其他国家的国家级本就是最细层级，不告警。

    Unfiltered always warns; a CN/AU/US photo landing on the country tier means
    the subnational tier was not used, so it warns too; for other countries the
    country tier is already the finest level.

    参数 / Parameters:
        geo_info (Optional[dict]): identify_bird 返回的 geo_info / geo_info from identify_bird.

    返回 / Returns:
        Optional[str]: 告警文本或 None / Warning text or None.
    """
    from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, describe_tier
    from birdid.region_locator import SUBNATIONAL_COUNTRIES

    info = geo_info or {}
    tier = info.get("tier", TIER_NONE)
    if tier == TIER_NONE or (tier == TIER_COUNTRY and info.get("region_code") in SUBNATIONAL_COUNTRIES):
        return describe_tier(info)
    return None
```

确认文件顶部已 `from typing import Optional`（`grep -n "^from typing" birdid_server.py`），没有就补上。

- [ ] **Step 3: 改识别结果处理段**

先 `Read birdid_server.py` 的 370–430 行。用 Edit 做三处替换：

1) 无结果分支：

```python
                region = geo_info.get('country_code') or 'Unknown'
```

替换为：

```python
                region = geo_info.get('region_code') or 'Unknown'
```

2) 告警段：

```python
        # 过滤降级警告：命中 L3/L4/L5 说明候选层被放宽了
        # Degradation warning: hitting L3/L4/L5 means the tier was widened
        from birdid.geo_filter import (
            TIER_COUNTRY, TIER_NEIGHBORHOOD, TIER_NONE, describe_tier,
        )
        geo_info = result.get('geo_info') or {}
        if geo_info.get('tier') in (TIER_NEIGHBORHOOD, TIER_COUNTRY, TIER_NONE):
            response['warning'] = describe_tier(geo_info)
```

替换为：

```python
        # 过滤降级警告：未过滤，或中澳美照片只用上国家级
        # Degradation warning: unfiltered, or a CN/AU/US photo only reached the country tier
        warning = geo_filter_warning(result.get('geo_info'))
        if warning:
            response['warning'] = warning
```

3) GPS 同步段：把以 `# 同步 GPS 反查到的国家到设置中心` 开头的注释块及其后
`update_gui_settings_from_gps(gps_info['country_code'])` 调用整段替换为：

```python
        # 同步 GPS 定位到的国家/省州到设置中心（identify_bird 已离线定位，此处不重复判定）
        # Sync the GPS-located country/subnational to settings (already located by identify_bird)
        gps_info = result.get('gps_info')
        if gps_info and gps_info.get('country_code'):
            update_gui_settings_from_gps(gps_info['country_code'], gps_info.get('subnational_code'))
```

- [ ] **Step 4: 检查残留并运行测试**

```bash
grep -n "TIER_NEIGHBORHOOD\|reverse_geocoder\|geo_info.get('country_code')" birdid_server.py birdid_cli.py ui/birdid_dock.py
python3 -m py_compile birdid_server.py birdid_cli.py ui/birdid_dock.py
python3 -m pytest test_birdid_server_geo.py -v
```

Expected：grep 无输出；2 passed。

- [ ] **Step 5: 提交**

```bash
git add birdid_server.py
git add -f test_birdid_server_geo.py
git commit -m "fix(server): 识鸟服务读 region_code，告警只针对未过滤与中澳美落到国家级，GPS 同步带上省州

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: 地区数据与设置中心

`ui/settings_center.py` 与 `ui/birdid_dock.py` 早已支持「国家带 `regions` 列表时显示省州下拉」，
只是 4.6 的数据源不给省州；本任务让 `load_regions_data` 从新库给出省州，并更新提示文案与署名。

**Files:**
- Modify: `core/region_data.py`（`load_regions_data`，约 `:57-122`）
- Modify: `ui/settings_center.py`（提示注释约 `:836-840`；地区行注释约 `:870-875`；`_populate_bid_regions` 末尾注释约 `:1014-1019`；`_geo_attribution_text` 约 `:2508-2541`；关于页注释约 `:2737-2740`）
- Modify: `locales/zh_CN.json`、`locales/en_US.json`（`settings.birdid_region_hint`、`settings.geo_attribution`）
- Modify: `test_geo_config_migration.py`（`test_region_data_from_geo_db`、`test_region_data_has_display_names`、`test_about_page_shows_gbif_attribution`）
- Modify: `test_settings_center.py`（`test_birdid_subnational_region_hidden`）

**Interfaces:**
- Consumes: Task 5 数据库；Task 7 `default_db_path()`
- Produces: `load_regions_data() -> dict`，结构不变：`{"countries": [{"code", "name", "name_cn", "is_continent": False, "has_regions": bool, "regions_count": int, "regions": [{"code", "name", "name_cn", "species_count"}], "species_count": int}]}`

- [ ] **Step 1: 改写测试（先失败）**

在 `test_geo_config_migration.py` 中，用 Edit 把 `test_region_data_from_geo_db` 与 `test_region_data_has_display_names` 两个函数整体替换为：

```python
def test_region_data_from_ebird_db():
    """国家列表来自 ebird_regions.db，且不含空清单国家 / Countries come from ebird_regions.db."""
    from core.region_data import load_regions_data

    countries = load_regions_data()["countries"]
    assert len(countries) >= 200, "eBird 国家级应覆盖 200+ 国家/地区"
    assert all(c["species_count"] > 0 for c in countries), "不应有空清单国家"
    codes = {c["code"] for c in countries}
    assert {"IS", "PA", "UG", "BO", "TW", "HK", "MO"} <= codes


def test_region_data_subnational_for_cn_au_us():
    """只有中澳美带省州，数量与 eBird 一致 / Only CN/AU/US carry subnational lists."""
    from core.region_data import load_regions_data

    by_code = {c["code"]: c for c in load_regions_data()["countries"]}
    assert (by_code["CN"]["regions_count"], by_code["AU"]["regions_count"], by_code["US"]["regions_count"]) == (31, 8, 51)
    assert by_code["IS"]["has_regions"] is False and by_code["IS"]["regions"] == []
    nsw = next(r for r in by_code["AU"]["regions"] if r["code"] == "AU-NSW")
    assert nsw["name_cn"] == "新南威尔士州" and nsw["species_count"] > 0


def test_region_data_has_display_names():
    """每个国家与省州都有中英文名 / Every country and subnational unit has both names."""
    from core.region_data import load_regions_data

    for c in load_regions_data()["countries"]:
        assert c["name"] and c["name_cn"], f"{c['code']} 缺名称"
        for r in c["regions"]:
            assert r["name"] and r["name_cn"], f"{r['code']} 缺名称"
```

同文件中，用 Edit 把 `test_about_page_shows_gbif_attribution` 整体替换为：

```python
def test_about_page_shows_ebird_attribution(monkeypatch):
    """
    关于页展示 eBird 与 Natural Earth 署名，且带真实获取日期。

    获取日期从 ebird_regions.db 的 meta 表动态读取，不能并入静态 about.content。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    _isolate_advanced_config(monkeypatch)
    w = SettingsCenter(get_i18n())
    attr = w._geo_attribution_text()
    assert attr, "署名文本为空（ebird_regions.db 应可用）"
    assert "eBird" in attr and "Natural Earth" in attr, f"署名不完整: {attr}"
    assert "GBIF" not in attr, f"地理数据已不来自 GBIF: {attr}"

    w.show_page("about")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert attr in texts, "关于页未展示署名行"
    w.close()
```

同文件模块 docstring（第 1–9 行）中两处 `geo_distribution.db` 改为 `ebird_regions.db`，「国家列表」改为「国家与省州列表」，「the country list」改为「the country and subnational lists」。

同文件 `test_settings_center_shows_gps_scope_hint` 的 docstring 第二段改为：
「有 GPS 的照片按拍摄地自动判断省州/国家、手选不参与，这一点若不明说，用户会以为选了没生效。」（代码不变）

在 `test_settings_center.py` 中，用 Edit 把 `test_birdid_subnational_region_hidden` 整个函数替换为以下两个函数：

```python
def _settings_with_region(monkeypatch, country_code, country_name, region_code, region_name):
    """
    用临时配置构造设置中心并打开识鸟页 / Build SettingsCenter on a temp config.

    参数 / Parameters:
        monkeypatch: pytest fixture.
        country_code (str): 国家代码 / Country code.
        country_name (str): 国家显示名 / Country display name.
        region_code (Optional[str]): 省州代码 / Subnational code.
        region_name (str): 省州显示名 / Subnational display name.

    返回 / Returns:
        tuple: (SettingsCenter, AdvancedConfig)
    """
    import tempfile

    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    cfg = AdvancedConfig(config_file=tmp_path)
    cfg.config["birdid_country_code"] = country_code
    cfg.config["birdid_selected_country"] = country_name
    cfg.config["birdid_region_code"] = region_code
    cfg.config["birdid_selected_region"] = region_name
    cfg.save()
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    w = SettingsCenter(get_i18n())
    w.show_page("birdid")
    return w, cfg


def test_birdid_subnational_region_restored(monkeypatch):
    """
    澳洲显示 8 个州且恢复已保存的 AU-ACT（老配置恢复生效，spec §2.5）。

    Australia lists its 8 states and restores the saved AU-ACT, so region codes
    stored by older versions work again.
    """
    w, cfg = _settings_with_region(monkeypatch, "AU", "澳大利亚", "AU-ACT", "澳大利亚首都特区")
    assert w._bid_region.count() == 9, f"应为「整个国家」+ 8 个州，实际 {w._bid_region.count()}"
    assert not w._bid_region.isHidden() and not w._bid_region_label.isHidden()
    assert w._bid_region.currentData() == "AU-ACT"
    assert cfg.birdid_country_code == "AU"
    w.close()


def test_birdid_region_row_hidden_without_subnational(monkeypatch):
    """
    非中澳美国家没有省州，地区行整行隐藏 / The row hides for countries without subnational data.
    """
    w, _cfg = _settings_with_region(monkeypatch, "JP", "日本", None, "整个国家")
    assert w._bid_region.count() == 1
    assert w._bid_region.isHidden() and w._bid_region_label.isHidden()
    w.close()
```

Run: `python3 -m pytest test_geo_config_migration.py test_settings_center.py -v`
Expected: FAIL（`load_regions_data` 仍读旧库；署名仍是 GBIF）

- [ ] **Step 2: 重写 `load_regions_data`**

用 Edit 把 `core/region_data.py` 中整个 `def load_regions_data()` 函数替换为：

```python
def load_regions_data() -> dict[str, Any]:
    """
    从 `birdid/data/ebird_regions.db` 生成国家与中澳美省州列表。

    国家与省州都来自 eBird 区域清单（与过滤用的是同一份数据），列表中出现的每个区域
    都保证有候选物种；只有中国、澳洲、美国带省州。

    Build the country list, with subnational units for CN/AU/US, from
    ebird_regions.db. Both come from the same eBird region lists the filter
    uses, so every listed region has candidate species.

    返回 / Returns:
        dict: 含 `"countries"` 列表；每个国家含 `code` / `name` / `name_cn` /
              `has_regions` / `regions_count` / `regions` / `species_count`；
              失败时返回 `{"countries": []}` /
              Dict with a `"countries"` list; `{"countries": []}` on failure.

    异常 / Exceptions:
        不抛出异常；错误以 print 记录后返回空结构。
        Does not raise; errors are printed and an empty structure is returned.
    """
    import sqlite3

    from birdid.geo_filter import default_db_path

    db_path = default_db_path()
    if not os.path.exists(db_path):
        print(f"[region_data] region DB missing: {db_path}")
        return {"countries": []}

    try:
        conn = sqlite3.connect(db_path)
        try:
            countries_rows = conn.execute(
                "SELECT code, name_en, name_zh, species_count FROM regions "
                "WHERE parent IS NULL AND species_count > 0 ORDER BY code"
            ).fetchall()
            region_rows = conn.execute(
                "SELECT code, parent, name_en, name_zh, species_count FROM regions "
                "WHERE parent IS NOT NULL AND species_count > 0 ORDER BY parent, name_en"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[region_data] failed to read regions: {exc}")
        return {"countries": []}

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for code, parent, name_en, name_zh, count in region_rows:
        by_parent.setdefault(str(parent), []).append(
            {"code": str(code), "name": str(name_en), "name_cn": str(name_zh), "species_count": int(count)}
        )

    countries: list[dict[str, Any]] = []
    for code, name_en, name_zh, count in countries_rows:
        regions = by_parent.get(str(code), [])
        countries.append(
            {
                "code": str(code),
                "name": str(name_en),
                "name_cn": str(name_zh),
                "is_continent": False,
                "has_regions": bool(regions),
                "regions_count": len(regions),
                "regions": regions,
                "species_count": int(count),
            }
        )
    return {"countries": countries}
```

- [ ] **Step 3: 更新设置中心注释与署名**

用 Edit 修改 `ui/settings_center.py`：

1) 提示行上方注释：

```python
        # 生效条件提示：有 GPS 的照片按拍摄位置的 1°网格过滤，手选国家不参与；
        # 这里明说，避免用户以为选了没生效（旧版本无任何提示，是常见困惑点）。
        # Scope hint: photos with GPS are filtered by their location's 1-degree
        # cell and the manual country is not used. Saying so explicitly avoids
        # the common confusion of "I selected a country but nothing changed".
```

替换为：

```python
        # 生效条件提示：有 GPS 的照片按拍摄地自动判断省州/国家，手选不参与；
        # 这里明说，避免用户以为选了没生效（旧版本无任何提示，是常见困惑点）。
        # Scope hint: photos with GPS are matched to their state/country
        # automatically and the manual choice is not used. Saying so avoids the
        # common confusion of "I selected a region but nothing changed".
```

2) 地区行注释：

```python
        # 地区行整体受 _populate_bid_regions 控制：当前数据源(GBIF 网格)不提供
        # 州/省级分区，该行会被隐藏；保留控件是为了兼容旧配置里存过的 region_code。
        # The whole row is toggled by _populate_bid_regions: the current data
        # source (GBIF grid) has no sub-national divisions, so it stays hidden.
        # The widgets remain for compatibility with region_code values stored by
        # older versions.
```

替换为：

```python
        # 地区行整体受 _populate_bid_regions 控制：只有中澳美有省州清单，
        # 其他国家隐藏该行。
        # The whole row is toggled by _populate_bid_regions: only CN/AU/US have
        # subnational lists; the row hides for every other country.
```

3) `_populate_bid_regions` 末尾注释：

```python
        # 无州级数据时隐藏整行，避免展示一个永远只有「整个国家」的空下拉。
        # GBIF 网格已按 GPS 精确到 1°，手选地区只在无 GPS 时作国家级回退。
        # Hide the whole row when there is no sub-national data, rather than
        # showing a dropdown whose only entry is "Entire country". The GBIF grid
        # already resolves to 1 degree by GPS; manual selection is only a
        # country-level fallback for photos without GPS.
```

替换为：

```python
        # 无省州数据时隐藏整行，避免展示一个永远只有「整个国家」的空下拉。
        # Hide the whole row when there is no subnational data, rather than
        # showing a dropdown whose only entry is "Entire country".
```

4) 整个 `_geo_attribution_text` 方法替换为：

```python
    def _geo_attribution_text(self) -> str:
        """
        读取区域清单库的 meta 生成署名文本。

        Build the region-list attribution line from the database meta table.

        返回 / Returns:
            str: 含获取日期的 eBird 与 Natural Earth 署名；库不可用时返回空串
                （调用方据此跳过该行）/ Attribution with the retrieval date; empty
                string when the database is unavailable.

        异常 / Exceptions:
            不抛出异常；任何读取失败都返回空串 / Never raises; returns "" on failure.
        """
        try:
            import sqlite3

            from birdid.geo_filter import default_db_path

            path = default_db_path()
            if not os.path.exists(path):
                return ""
            conn = sqlite3.connect(path)
            try:
                meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            return ""

        fetched_at = meta.get("fetched_at", "")
        if not fetched_at:
            return ""
        return self.i18n.t("settings.geo_attribution", fetched_at=fetched_at)
```

5) 关于页调用处注释：

```python
        # 地理分布数据署名：快照日期从 geo_distribution.db 的 meta 表动态读取，
        # 因此不能并入静态的 about.content 文案。CC-BY 要求署名，见 spec §7。
        # Geo-distribution attribution: the snapshot date is read dynamically from
        # geo_distribution.db's meta table, so it cannot live in the static
        # about.content string. CC-BY requires attribution — see spec section 7.
```

替换为：

```python
        # 区域清单署名：获取日期从 ebird_regions.db 的 meta 表动态读取，因此不能并入
        # 静态的 about.content 文案。eBird 派生数据须附署名与条款说明（spec §2.3、§7.2）。
        # Region-list attribution: the retrieval date is read from ebird_regions.db's
        # meta table, so it cannot live in the static about.content string. eBird-
        # derived data must carry attribution and terms (spec sections 2.3, 7.2).
```

- [ ] **Step 4: 更新两条文案**

```bash
python3 - <<'EOF'
import json
values = {
    "zh_CN": {
        "birdid_region_hint": "有 GPS 的照片按拍摄地自动判断省/州或国家；这里的选择只对没有 GPS 的照片生效",
        "geo_attribution": "鸟类区域清单 - eBird（康奈尔鸟类学实验室），获取于 {fetched_at}，依 eBird API 使用条款提供；行政区边界 - Natural Earth（公有领域）",
    },
    "en_US": {
        "birdid_region_hint": "Photos with GPS are matched to their state/province or country automatically; this selection only applies to photos without GPS",
        "geo_attribution": "Regional bird lists - eBird (Cornell Lab of Ornithology), retrieved {fetched_at}, provided under the eBird API Terms of Use; administrative boundaries - Natural Earth (public domain)",
    },
}
for lang, kv in values.items():
    path = f"locales/{lang}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["settings"].update(kv)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
EOF
git diff --stat locales/
```

Expected：每个文件 2 行增 2 行删。

- [ ] **Step 5: 运行测试**

```bash
python3 -m py_compile core/region_data.py ui/settings_center.py
python3 -m pytest test_geo_config_migration.py test_settings_center.py test_birdid_dock_config.py test_advanced_config_birdid_migration.py -v
```

Expected：全部通过。`test_birdid_dock_config.py` 中断言「选澳洲后区域行可见」的用例在 4.6 下若原本失败，现在应转为通过；若仍失败，读该用例确认是否依赖窗口实际显示（`isVisible()` 在未 show 的窗口恒为 False），这种情况改用 `isHidden()` 断言并在提交信息写明原因。

- [ ] **Step 6: 提交**

```bash
git add core/region_data.py ui/settings_center.py locales/zh_CN.json locales/en_US.json
git add -f test_geo_config_migration.py test_settings_center.py
git commit -m "feat(settings): 恢复中澳美省州下拉，地区与署名改读 eBird 区域库

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: 清理 GBIF 网格遗留与打包配置

**Files:**
- Delete: `birdid/data/geo_distribution.db`、`birdid/data/land_cells.json`、`scripts_dev/build_geo_distribution.py`、`scripts_dev/calibrate_geo_threshold.py`、`scripts_dev/validate_geo_filter.py`
- Modify: `SuperPicky.spec`（约 `:128`）、`SuperPicky_win64.spec`（约 `:125`）
- Modify: `tools/country_names.py`（模块 docstring）、`birdid_server.py`（残留注释）
- Modify: `docs/specs/2026-07-25-gbif-geo-filter-design.md`（状态行）

**Interfaces:**
- Consumes: Task 1–10 全部完成
- Produces: 无新接口

- [ ] **Step 1: 删除文件**

```bash
git rm birdid/data/geo_distribution.db birdid/data/land_cells.json
git rm scripts_dev/build_geo_distribution.py scripts_dev/calibrate_geo_threshold.py scripts_dev/validate_geo_filter.py
```

- [ ] **Step 2: 打包 hiddenimports**

用 Edit 在两个 spec 中，把：

```python
        'birdid.geo_filter',       # 地理过滤：bird_identifier 顶层导入，其余调用点为函数内延迟导入
```

替换为：

```python
        'birdid.geo_filter',       # 地理过滤：bird_identifier 顶层导入，其余调用点为函数内延迟导入
        'birdid.region_locator',   # GPS 区域定位：bird_identifier 函数内延迟导入，PyInstaller 静态分析看不到
        'birdid.region_geometry',  # 区域定位的几何工具，由 region_locator 导入
```

（两个 spec 的该行缩进与注释可能不同，先 `grep -n "birdid.geo_filter" SuperPicky.spec SuperPicky_win64.spec` 取原文再替换。`SuperPicky_full.spec` 没有 birdid hiddenimports，不改。）

`birdid/data` 在两个 spec 中是整目录打包（`SuperPicky.spec:57`、`SuperPicky_win64.spec:58`），`ebird_regions.db` 自动包含，无需改数据段。

- [ ] **Step 3: 更新残留文字**

1) `tools/country_names.py` 模块 docstring 中「覆盖 `geo_distribution.db` 中出现的全部 233 个代码」改为「覆盖 eBird 国家列表中的全部代码（由 `scripts_dev/build_ebird_regions.py` 构建卡口保证）」；英文段对应的 「covering all 233 codes present in `geo_distribution.db`」改为「covering every code in the eBird country list (enforced by the build gate in `scripts_dev/build_ebird_regions.py`)」。

2) `birdid_server.py` 中若仍有提到 `reverse_geocoder` 或「GBIF 网格」的注释（`grep -n "reverse_geocoder\|GBIF 网格\|GBIF grid" birdid_server.py`），按新实现改写为「identify_bird 用 ebird_regions.db 边界离线定位」。

3) `docs/specs/2026-07-25-gbif-geo-filter-design.md` 第 4 行状态改为：

```markdown
状态 / Status: 已被取代（2026-09-16）/ Superseded by `docs/specs/2026-09-16-ebird-region-filter-design.md`
```

- [ ] **Step 4: 遗留引用检查（spec §8 验收 9）**

```bash
git grep -n "geo_distribution\|land_cells\|reverse_geocoder\|cell_id_for\|TIER_CELL\|TIER_NEIGHBORHOOD\|L1_cell_strong" -- ':!docs/' ':!ChangeLog.md' ':!RELEASE_NOTES.md'
```

Expected：只允许出现在说明历史的注释/docstring 中（如 `_locate_gps`、`region_locator` 模块 docstring 提到 reverse_geocoder），不得有代码引用。逐条确认后继续。

- [ ] **Step 5: 编译与全量测试**

```bash
BASE=$(git log --format=%H -1 -- docs/plans/2026-09-16-ebird-region-filter.md)
git diff --name-only "$BASE" -- '*.py' | while read -r f; do test -f "$f" && python3 -m py_compile "$f" && echo "ok $f"; done
python3 -m pytest -x -q 2>&1 | tail -15
```

Expected：全部通过，或只剩开工前就存在的失败。开工前基线用 `git stash` 不可靠（仓库有陈年 stash），改为：若出现失败，`git log --oneline -1 -- <测试文件>` 看该测试是否本计划改过；未改过的，切到 `8d8f0418` 的临时 worktree 跑同一测试确认是否原本就失败，并在提交信息中列出。全量测试不要与打开的 app 或截图脚本并行（内存不足会导致 torch 分配崩溃，退出码 133）。

- [ ] **Step 6: 提交**

```bash
git add -A SuperPicky.spec SuperPicky_win64.spec tools/country_names.py birdid_server.py docs/specs/2026-07-25-gbif-geo-filter-design.md
git commit -m "chore(birdid): 删除 GBIF 网格库与脚本（-35 MB），打包补 region_locator

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: 验收脚本 `scripts_dev/validate_ebird_regions.py`

**Files:**
- Create: `scripts_dev/validate_ebird_regions.py`
- Test: `test_validate_ebird_regions.py`

**Interfaces:**
- Consumes: Task 4 `SENTINELS`、`check_sentinels`、`MAX_DB_BYTES`；Task 2 `load_model_index`；Task 6 `RegionLocator`
- Produces:
  - `CROSS_HEMISPHERE_ERRORS: Tuple[str, ...] = ("小企鹅", "蓝脚鲣鸟", "角海鹦", "弗氏燕鸥")`
  - `compare_report_dbs(baseline_db: str, new_db: str) -> List[Tuple[str, bool, str]]`（每项 `(检查名, 是否通过, 说明)`）
  - `main(argv: Optional[List[str]] = None) -> int`（全部通过返回 0，否则 1）

- [ ] **Step 1: 写失败测试**

```python
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
```

Run: `python3 -m pytest test_validate_ebird_regions.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 2: 实现**

```python
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
from typing import List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CROSS_HEMISPHERE_ERRORS: Tuple[str, ...] = ("小企鹅", "蓝脚鲣鸟", "角海鹦", "弗氏燕鸥")

Check = Tuple[str, bool, str]


def compare_report_dbs(baseline_db: str, new_db: str) -> List[Check]:
    """
    逐项比对两次处理的 report.db（spec §8 验收 5）/ Compare two report.db runs.

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

    old = sqlite3.connect(baseline_db)
    new = sqlite3.connect(new_db)
    try:
        placeholders = ",".join("?" * len(CROSS_HEMISPHERE_ERRORS))
        residual = new.execute(
            f"SELECT COUNT(*) FROM photos WHERE bird_species_cn IN ({placeholders})", CROSS_HEMISPHERE_ERRORS
        ).fetchone()[0]
        old_total, old_named = named(old)
        new_total, new_named = named(new)
        gps = new.execute("SELECT COUNT(*) FROM photos WHERE gps_latitude IS NOT NULL").fetchone()[0]
    finally:
        old.close()
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
```

注意 `_check_legacy_refs` 的 grep 模式本身会出现在本文件里，已用 `:!scripts_dev/validate_ebird_regions.py` 排除。

- [ ] **Step 3: 运行**

```bash
python3 -m pytest test_validate_ebird_regions.py -v
python3 -m py_compile scripts_dev/validate_ebird_regions.py
python3 -m scripts_dev.validate_ebird_regions
```

Expected：3 passed；验收脚本 `N/N passed`。任何 FAIL 都要回到对应任务修复，不得调整阈值。

- [ ] **Step 4: 提交**

```bash
git add -f scripts_dev/validate_ebird_regions.py test_validate_ebird_regions.py
git commit -m "test(scripts): eBird 区域过滤验收脚本

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: 实机验收（需要用户参与）

本任务不写代码，产出是验收记录。每一步都要把实际数字记下来，写进最终汇报；**不达标时停下与用户讨论，不自行调整过滤口径**（spec §8 验收 5）。

**Files:**
- Create: `docs/specs/2026-09-16-ebird-region-filter-acceptance.md`（验收记录）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 验收记录

- [ ] **Step 1: 冰岛批次回归（spec §8 验收 5）**

1. 向用户确认 433 张法罗群岛/冰岛照片当前所在目录，以及该目录下 `.superpicky/report.db`（或实际位置）是否为 4.6 处理结果。
2. 把该 report.db 复制到 scratchpad 作为基线（**只复制，不移动、不删除原文件**）。
3. 请用户在开发版中对该目录执行 reset，保持与 4.6 那次相同的手选国家（冰岛），重新处理。
4. 运行：

```bash
python3 -m scripts_dev.validate_ebird_regions --baseline-db <scratchpad>/baseline_report.db --report-db <目录>/.superpicky/report.db
```

Expected：`cross_hemisphere_errors` 与 `named_not_lower` 通过（4.6 基线为 283/433）。

- [ ] **Step 2: 用户提供的无 GPS 误识别案例（spec §8 验收 6）**

向用户索取案例照片路径与当时手选的国家/省州。对每张照片分别记录：4.6 结果（用户提供或从旧 report.db 读取）、新版结果（识鸟面板拖入，记下命中层与候选数）。汇总成表写入验收记录。

- [ ] **Step 3: 打包冒烟（spec §8 验收 10，CLAUDE.md 要求 `.spec` 改动后必做）**

```bash
python3 build_release_mac.py 2>&1 | tail -20
```

（若构建命令不同，先 `ls build_release*.py` 并读取其 `--help`，按仓库现行方式构建 macOS 包。）

检查：

```bash
APP=$(ls -d dist/*.app | head -1)
find "$APP" -name "ebird_regions.db" | head -1
find "$APP" -name "geo_distribution.db" | head -1
```

Expected：第一条有输出，第二条无输出。
然后启动打包产物至主窗口，把一张带 GPS 的 JPEG 拖入识鸟面板，确认日志/面板显示「按省/州过滤：…」且省州正确；再拖一张 NEF，确认没有拉起第二个窗口（RC5 修复的回归点）。

- [ ] **Step 4: 写验收记录并提交**

`docs/specs/2026-09-16-ebird-region-filter-acceptance.md` 至少包含：验收脚本完整输出、冰岛比对数字、用户案例表、打包冒烟结果、未通过项与处理决定。

```bash
git add docs/specs/2026-09-16-ebird-region-filter-acceptance.md
git commit -m "docs(birdid): eBird 区域过滤验收记录

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 自查记录 / Self-Review

- **Spec 覆盖**：§4 数据构建 → Task 2–5；§5 离线定位 → Task 1、6；§6.1–6.2 过滤链与接线 → Task 7–9；§6.3 配置（老 `AU-NSW` 生效、省州须隶属国家、不写回配置）→ Task 7 `iter_candidates` 与测试、Task 8 接线测试、Task 10 设置中心恢复测试；§7.1 界面 → Task 10；§7.2 署名 → Task 10；§7.3 删除 → Task 7（单测）、Task 11；§7.4 打包 → Task 11、13；§8 验收 1–10 → Task 5、6、12、13。
- **与 spec 的有意差异**（均已在对应任务说明并同步 spec）：映射增加第 5 步「同科种加词」、人工覆盖提到第 1 步、构建卡口改为可判定规则（Task 2 Step 5 改 spec §4.2/§4.3）；`iter_candidates` 用四个字符串参数并产出三元组（含区域代码），而非 spec 写的 `LocateResult` 参数，以免 `geo_filter` 依赖 `region_locator` 形成循环导入。
- **类型一致性**：`RingRow` 字段顺序在 Task 3 定义、Task 4/6 测试按位置构造一致；`_identify_with_tiers` 四元组返回在 Task 8 定义与测试一致；`geo_info.region_code` 在 Task 8 产生、Task 7 `describe_tier` 与 Task 9 服务端读取一致；`SUBNATIONAL_COUNTRIES` 运行时唯一定义在 `birdid.region_locator`（构建侧 `scripts_dev.ebird_region_boundaries` 另有同值元组，用于不依赖运行时包的构建脚本）。
