# -*- coding: utf-8 -*-
"""
Natural Earth 行政区边界 → ebird_regions.db 边界行 / Natural Earth boundaries to DB rows.

国家级用 ne_50m_admin_0_countries（海外领地改取 ne_10m_admin_0_map_units，见下方
TERRITORY_UNITS 说明），中澳美省州级用 ne_10m_admin_1_states_provinces（均为公有领域）。
代码统一转换为 eBird 区域代码，运行时只认 eBird 代码。

Countries come from ne_50m_admin_0_countries (overseas territories from
ne_10m_admin_0_map_units instead, see TERRITORY_UNITS) and CN/AU/US subnational
units from ne_10m_admin_1_states_provinces (all public domain). All codes are
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

# ---------------------------------------------------------------------------
# 海外领地 / Overseas territories
#
# 问题：ne_50m_admin_0_countries 把 eBird 视为独立「国家」的海外领地并入宗主国要素
# （法属圭亚那、瓜德罗普、马提尼克、留尼汪、马约特并入 FR；荷兰加勒比区并入 NL；
# 斯瓦尔巴与扬马延并入 NO；托克劳并入 NZ；圣诞岛、科科斯、阿什莫尔并入 AU 名下要素），
# 或干脆没有收录（美国本土外小岛屿、直布罗陀、克利珀顿、布韦岛、珊瑚海群岛）。
# 结果是在卡宴拍的照片被当成法国本土，用法国清单过滤（707 种里缺 612 种）。
#
# 方案（二选一中取了 b）：领地几何统一取自 Natural Earth 10m 的 admin_0_map_units，
# 该图层把上述领地都拆成了独立要素（且包含 50m 根本没有的中途岛等），由 TERRITORY_UNITS
# 按 GU_A3 显式映射为 eBird 代码；同时用 TERRITORY_CARVE_BOXES 这张小外包框表，把 50m
# 宗主国要素里落在领地范围内的多边形分块整块剔除，保证领地点不会同时被宗主国环包含
# （否则严格包含会出现两个国家并列）。只用 50m 分块改派（方案 a）做不到，因为中途岛等
# 在 50m 里不存在。代价：领地与邻国的陆地边界（仅法属圭亚那）是 10m 对 50m，河界处可能
# 有几公里的细缝，细缝内的点按「外包框面积小者优先」归法属圭亚那，误差量级与 50m 本身相当。
#
# Problem: ne_50m_admin_0_countries folds territories that eBird treats as
# separate countries into their sovereign's feature (GF/GP/MQ/RE/YT into FR,
# BQ into NL, SJ into NO, TK into NZ, CX/CC/AC into AU-coded features), or omits
# them entirely (UM, GI, CP, BV, CS). A photo from Cayenne was therefore
# filtered with the France list (612 of 707 species missing).
#
# Approach (option b of two): territory geometry comes from Natural Earth 10m
# admin_0_map_units, which splits every one of them into its own feature (and
# includes Midway etc. that 50m lacks), mapped to eBird codes by GU_A3 in
# TERRITORY_UNITS. TERRITORY_CARVE_BOXES, a small explicit bbox table, removes
# the sovereign's 50m polygon parts that fall inside a territory, so a
# territory point is never also contained by the parent's ring (which would
# tie under strict containment). Reassigning 50m parts alone (option a) cannot
# work because Midway and others do not exist at 50m. Trade-off: the one land
# border (French Guiana) is 10m against 50m neighbours; points in the few-km
# slivers resolve to GF via the smaller-bbox rule, an error on the same order
# as 50m itself.
# ---------------------------------------------------------------------------

# 10m map_units 的 GU_A3 → eBird 国家代码 / 10m map-unit GU_A3 to eBird country code.
# 扬马延在 Natural Earth 里属 NO，但 eBird 把它与熊岛一起归入 SJ（2026-09-17 用 eBird
# 热点接口实测）；美国本土外小岛屿在 map_units 里是 9 个独立要素，全部归 UM。
# Jan Mayen is NO in Natural Earth but SJ in eBird (verified via the eBird
# hotspot API on 2026-09-17); UM is nine separate map units.
TERRITORY_UNITS: Dict[str, str] = {
    "GUF": "GF", "GLP": "GP", "MTQ": "MQ", "REU": "RE", "MYT": "YT", "CLP": "CP",
    "NLY": "BQ",
    "NSV": "SJ", "NJM": "SJ", "BVT": "BV",
    "TKL": "TK",
    "CXR": "CX", "CCK": "CC", "ATC": "AC", "CSI": "CS",
    "GIB": "GI",
    "JQI": "UM", "DQI": "UM", "FQI": "UM", "HQI": "UM", "WQI": "UM",
    "MQI": "UM", "BQI": "UM", "LQI": "UM", "KQI": "UM",
}

# 领地代码 → (50m 宗主国要素的 ISO_A2_EH, 外包框 (min_lat, max_lat, min_lon, max_lon) 列表)。
# 50m 要素中外环外包框完全落在某个框内的多边形分块（连同其内环）被剔除。只列 50m 里
# 确实存在对应分块的领地；构建卡口要求每个框至少剔除一块，防止表过期后静默失效。
# 框只比领地略大（约 0.3°），且只作用于指定宗主国，不会误伤邻国或本土近岸岛屿。
#
# Territory code -> (ISO_A2_EH of the sovereign's 50m feature, bboxes as
# (min_lat, max_lat, min_lon, max_lon)). A 50m polygon part whose outer-ring
# bbox lies entirely inside a box is dropped together with its holes. Only
# territories that really have parts at 50m are listed; the build gate requires
# each box to remove at least one part so a stale table cannot fail silently.
# Boxes are only slightly larger than the territory (~0.3 deg) and apply to the
# named sovereign only, so neighbours and near-shore mainland islands are safe.
TERRITORY_CARVE_BOXES: Dict[str, Tuple[str, Tuple[Tuple[float, float, float, float], ...]]] = {
    "GF": ("FR", ((1.8, 6.1, -54.9, -51.3),)),
    "GP": ("FR", ((15.6, 16.8, -62.0, -60.8),)),
    "MQ": ("FR", ((14.1, 15.2, -61.5, -60.5),)),
    "RE": ("FR", ((-21.7, -20.5, 54.9, 56.1),)),
    "YT": ("FR", ((-13.3, -12.3, 44.7, 45.5),)),
    "BQ": ("NL", ((11.7, 12.6, -68.7, -67.9), (17.2, 17.9, -63.5, -62.6))),
    "SJ": ("NO", ((73.9, 81.5, 9.5, 35.0), (70.5, 71.5, -9.5, -7.5))),
    "TK": ("NZ", ((-9.8, -8.2, -172.8, -170.9),)),
    "CX": ("AU", ((-10.9, -10.1, 105.3, 106.0),)),
    "CC": ("AU", ((-12.5, -11.8, 96.5, 97.2),)),
    "AC": ("AU", ((-12.8, -12.0, 122.7, 123.9),)),
}

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


def _bbox_within(
    coords: Sequence[Sequence[float]], box: Tuple[float, float, float, float]
) -> bool:
    """
    原始环坐标的外包框是否完全落在框内 / Whether a raw ring's bbox lies inside a box.

    参数 / Parameters:
        coords (Sequence[Sequence[float]]): GeoJSON [lon, lat] 序列 / [lon, lat] pairs.
        box (tuple): (min_lat, max_lat, min_lon, max_lon)

    返回 / Returns:
        bool: 完全落在框内为 True；空环为 False / True when fully inside; False when empty.
    """
    if not coords:
        return False
    lons = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    min_lat, max_lat, min_lon, max_lon = box
    return min_lat <= min(lats) and max(lats) <= max_lat and min_lon <= min(lons) and max(lons) <= max_lon


def carve_territory_parts(
    feature: dict,
    carve_boxes: Dict[str, Tuple[str, Tuple[Tuple[float, float, float, float], ...]]],
) -> Tuple[dict, Dict[Tuple[str, int], int]]:
    """
    从 50m 宗主国要素中剔除落在领地框内的多边形分块 / Remove territory parts from a sovereign's 50m feature.

    只处理 ISO_A2_EH 与表中宗主国一致的要素；判断单位是「多边形分块」（外环+其内环），
    外环外包框完全落在某个框内即整块剔除。领地几何另由 territory_rings 从 10m map_units 提供。

    Only features whose ISO_A2_EH equals the table's sovereign are touched. The
    unit is a polygon part (outer ring plus its holes); a part is dropped when
    its outer ring's bbox lies entirely inside a box. Territory geometry itself
    comes from 10m map units via territory_rings.

    参数 / Parameters:
        feature (dict): GeoJSON 要素 / GeoJSON feature.
        carve_boxes (dict): 形如 TERRITORY_CARVE_BOXES 的表 / A table shaped like TERRITORY_CARVE_BOXES.

    返回 / Returns:
        tuple: (剔除后的要素（未改动时原样返回）, {(领地代码, 框序号): 剔除的分块数}) /
            (the feature with parts removed, or unchanged; {(territory, box index): parts removed}).
    """
    props = feature.get("properties") or {}
    code = str(props.get("ISO_A2_EH") or "")
    boxes = [
        (territory, i, box)
        for territory, (parent, territory_boxes) in sorted(carve_boxes.items())
        if parent == code
        for i, box in enumerate(territory_boxes)
    ]
    geometry = feature.get("geometry") or {}
    gtype = geometry.get("type")
    if not boxes or gtype not in ("Polygon", "MultiPolygon"):
        return feature, {}
    polygons = [geometry.get("coordinates") or []] if gtype == "Polygon" else (geometry.get("coordinates") or [])
    kept: List[list] = []
    carved: Dict[Tuple[str, int], int] = {}
    for polygon in polygons:
        outer = polygon[0] if polygon else []
        hit = next(((t, i) for t, i, box in boxes if _bbox_within(outer, box)), None)
        if hit is None:
            kept.append(polygon)
        else:
            carved[hit] = carved.get(hit, 0) + 1
    if not carved:
        return feature, {}
    new_feature = dict(feature)
    new_feature["geometry"] = {"type": "MultiPolygon", "coordinates": kept}
    return new_feature, carved


def admin0_rings(
    features: List[dict],
    valid_codes: Set[str],
    carve_boxes: Optional[Dict[str, Tuple[str, Tuple[Tuple[float, float, float, float], ...]]]] = None,
) -> Tuple[List[RingRow], List[str], Dict[Tuple[str, int], int]]:
    """
    提取国家边界，并剔除海外领地分块 / Extract country boundaries, carving out overseas territories.

    参数 / Parameters:
        features (list[dict]): ne_50m_admin_0 的要素 / Admin-0 features.
        valid_codes (set[str]): eBird 国家代码 / Valid eBird country codes.
        carve_boxes (Optional[dict]): 领地剔除框表；None 时用 TERRITORY_CARVE_BOXES /
            Territory carve table; TERRITORY_CARVE_BOXES when None.

    返回 / Returns:
        tuple: (行, eBird 中不存在而被跳过的代码（排序去重）, {(领地, 框序号): 剔除分块数}) /
            (rows, sorted skipped codes, {(territory, box index): parts removed}).
    """
    boxes = TERRITORY_CARVE_BOXES if carve_boxes is None else carve_boxes
    rows: List[RingRow] = []
    next_id: Dict[str, int] = {}
    skipped: Set[str] = set()
    carved_total: Dict[Tuple[str, int], int] = {}
    for feat in features:
        props = feat.get("properties") or {}
        code = str(props.get("ISO_A2_EH") or "")
        if not code or code == "-99":
            continue
        if code not in valid_codes:
            skipped.add(code)
            continue
        feat, carved = carve_territory_parts(feat, boxes)
        for key, count in carved.items():
            carved_total[key] = carved_total.get(key, 0) + count
        new_rows = _rows_for_feature(feat, code, 0, next_id.get(code, 0))
        rows.extend(new_rows)
        next_id[code] = next_id.get(code, 0) + len(new_rows)
    return rows, sorted(skipped), carved_total


def territory_rings(
    features: List[dict], valid_codes: Set[str], units: Optional[Dict[str, str]] = None
) -> Tuple[List[RingRow], List[str]]:
    """
    从 10m admin_0_map_units 提取海外领地的国家级边界 / Extract territory country rings from 10m map units.

    参数 / Parameters:
        features (list[dict]): ne_10m_admin_0_map_units 的要素 / Map-unit features.
        valid_codes (set[str]): eBird 国家代码 / Valid eBird country codes.
        units (Optional[dict]): GU_A3 → eBird 代码；None 时用 TERRITORY_UNITS /
            GU_A3 to eBird code; TERRITORY_UNITS when None.

    返回 / Returns:
        tuple: (行, 表中列出但图层里找不到或 eBird 无此代码的 GU_A3（排序）) /
            (rows, sorted GU_A3 values listed in the table but absent from the layer or eBird).
    """
    table = TERRITORY_UNITS if units is None else units
    rows: List[RingRow] = []
    next_id: Dict[str, int] = {}
    found: Set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        unit = str(props.get("GU_A3") or "")
        code = table.get(unit)
        if code is None or code not in valid_codes:
            continue
        found.add(unit)
        new_rows = _rows_for_feature(feat, code, 0, next_id.get(code, 0))
        rows.extend(new_rows)
        next_id[code] = next_id.get(code, 0) + len(new_rows)
    return rows, sorted(set(table) - found)


def find_traditional_names(names: Dict[str, str]) -> Dict[str, str]:
    """
    找出含繁体字的中文名 / Find Chinese names containing traditional characters.

    参数 / Parameters:
        names (dict[str, str]): 代码 → 中文名 / Code to Chinese name.

    返回 / Returns:
        dict[str, str]: 含繁体字的条目 / Entries with traditional characters.
    """
    return {code: name for code, name in names.items() if any(ch in _TRADITIONAL_ONLY for ch in name)}
