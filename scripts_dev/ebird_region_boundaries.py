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

from birdid.region_geometry import SCALE, pack_ring, quantize_ring, ring_bbox

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


def _outer_area(feature: dict) -> float:
    """
    要素外环的量化面积（度²，鞋带公式）/ Quantized outer-ring area (deg^2, shoelace).

    Natural Earth 的 area_sqkm 字段对部分要素（如全部 AU admin-1 要素）恒为 0，
    不能用来判断哪个要素是该省州代码下面积最大的主体（州本体 vs 附属小岛）。
    改用量化后坐标的鞋带公式自算面积：只累加外环（非洞）绝对面积，忽略内环洞，
    与写库时 quantize_ring 用的同一批整数点保证结果与最终入库边界一致。

    Natural Earth's area_sqkm is 0 for every AU admin-1 feature, so it cannot
    tell the state's main body apart from a tiny attached island sharing the
    same code. This computes area with the shoelace formula on the same
    quantized points that get written to the database, summing only outer
    (non-hole) ring areas so the result matches what is actually stored.

    参数 / Parameters:
        feature (dict): GeoJSON 要素 / GeoJSON feature.

    返回 / Returns:
        float: 外环面积之和，单位度² / Sum of outer-ring areas in square degrees.
    """
    total = 0.0
    for coords, is_hole in iter_geometry_rings(feature.get("geometry") or {}):
        if is_hole:
            continue
        points = quantize_ring(coords)
        if len(points) < 3:
            continue
        shoelace = 0.0
        n = len(points)
        for i in range(n):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % n]
            shoelace += x1 * y2 - x2 * y1
        total += abs(shoelace) / 2.0
    return total / (SCALE * SCALE)


def admin1_rings(
    features: List[dict], valid_codes: Set[str]
) -> Tuple[List[RingRow], Dict[str, str], List[str]]:
    """
    提取中澳美省州边界 / Extract CN/AU/US subnational boundaries.

    同一 eBird 代码下可能有多个 Natural Earth 要素（州本体+附属小岛，如
    AU-NSW 还带出豪勋爵岛）；中文名取其中量化外环面积（`_outer_area`）
    最大的要素，而不是 Natural Earth 的 area_sqkm（该字段对 AU 全部为 0，
    会导致"最后一个要素覆盖前面"的错误结果，见 2026-09 复核修复）。

    A single eBird code can span several Natural Earth features (a state's
    main body plus an attached island, e.g. AU-NSW also carries Lord Howe
    Island). The Chinese name is taken from whichever feature has the
    largest quantized outer-ring area (`_outer_area`), not area_sqkm (which
    is 0 for every AU feature and let a later, smaller feature silently
    overwrite the state name — fixed in the 2026-09 review).

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
        area = _outer_area(feat)
        if area > best_area.get(code, -1.0):
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
