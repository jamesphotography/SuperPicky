# -*- coding: utf-8 -*-
"""
Natural Earth 边界提取单测 / Unit tests for Natural Earth boundary extraction.
"""
from scripts_dev.ebird_region_boundaries import (
    CN_ISO_TO_EBIRD,
    TERRITORY_CARVE_BOXES,
    TERRITORY_UNITS,
    admin0_rings,
    admin1_rings,
    carve_territory_parts,
    ebird_subnational_code,
    find_traditional_names,
    territory_rings,
)

SQUARE = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
BIG_SQUARE = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
TINY_SQUARE = [[[20, 20], [20.1, 20], [20.1, 20.1], [20, 20.1], [20, 20]]]
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
    """同一代码取几何外环面积最大要素的中文名（area_sqkm 故意反向，证明未被使用）；
    无效代码记为跳过 / The geometrically largest feature names the region
    (area_sqkm deliberately reversed to prove it is not consulted); invalid
    codes are recorded as skipped."""
    feats = [
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "豪勋爵岛", "area_sqkm": 800000}, SQUARE),
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "新南威尔士州", "area_sqkm": 15}, BIG_SQUARE),
        feature({"iso_a2": "AU", "iso_3166_2": "AU-X04~", "name_zh": "阿什莫尔", "area_sqkm": 1}, SQUARE),
        feature({"iso_a2": "JP", "iso_3166_2": "JP-13", "name_zh": "东京都", "area_sqkm": 2000}, SQUARE),
    ]
    rows, names, skipped = admin1_rings(feats, valid_codes={"AU-NSW"})
    assert {r.region for r in rows} == {"AU-NSW"}
    assert [r.ring_id for r in rows] == [0, 1]
    assert all(r.level == 1 for r in rows)
    assert names == {"AU-NSW": "新南威尔士州"}
    assert skipped == ["AU-X04~"]


def test_admin1_rings_uses_geometry_area_when_area_sqkm_is_zero():
    """area_sqkm 恒为 0（真实 AU admin-1 要素场景）时按量化外环面积选中文名，
    与要素在列表中的先后顺序无关 / When area_sqkm is 0 for every feature (the
    real AU admin-1 case), the Chinese name is chosen by quantized outer-ring
    area, independent of list order."""
    feats = [
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "新南威尔士州", "area_sqkm": 0}, BIG_SQUARE),
        feature({"iso_a2": "AU", "iso_3166_2": "AU-NSW", "name_zh": "豪勋爵岛", "area_sqkm": 0}, TINY_SQUARE),
    ]
    rows, names, _ = admin1_rings(feats, valid_codes={"AU-NSW"})
    assert names == {"AU-NSW": "新南威尔士州"}


def test_admin1_marks_holes():
    """内环标为洞 / Inner rings are flagged as holes."""
    feats = [feature({"iso_a2": "US", "iso_3166_2": "US-CA", "name_zh": "加利福尼亚州", "area_sqkm": 1}, HOLED)]
    rows, _, _ = admin1_rings(feats, valid_codes={"US-CA"})
    assert [r.is_hole for r in rows] == [0, 1]
    assert (rows[0].min_lat, rows[0].max_lat, rows[0].min_lon, rows[0].max_lon) == (0.0, 4.0, 0.0, 4.0)


def test_admin0_rules():
    """用 ISO_A2_EH；无效与 -99 跳过；不在领地框内的分块不受影响 / Admin-0 rules."""
    feats = [
        feature({"ISO_A2_EH": "FR", "NAME": "France"}, [SQUARE], "MultiPolygon"),
        feature({"ISO_A2_EH": "-99", "NAME": "Somaliland"}, SQUARE),
        feature({"ISO_A2_EH": "ZZ", "NAME": "Nowhere"}, SQUARE),
    ]
    rows, skipped, carved = admin0_rings(feats, valid_codes={"FR", "AU"}, carve_boxes={})
    assert [(r.region, r.level) for r in rows] == [("FR", 0)]
    assert skipped == ["ZZ"]
    assert carved == {}


# 本土（0..1°）+ 带洞的海外分块（50..54°）+ 远处小岛（20°）/ Mainland, a holed overseas part, a far islet.
OVERSEAS_PART = [[[50, 50], [54, 50], [54, 54], [50, 54], [50, 50]], [[51, 51], [52, 51], [52, 52], [51, 52], [51, 51]]]
CARVE = {"GF": ("FR", ((49.5, 54.5, 49.5, 54.5),)), "RE": ("FR", ((19.9, 20.2, 19.9, 20.2),))}


def test_admin0_carves_territory_parts_from_parent():
    """领地框内的宗主国分块（连同内环）被整块剔除，本土保留 / Territory parts and their holes are removed."""
    feats = [feature({"ISO_A2_EH": "FR", "NAME": "France"},
                     [SQUARE, OVERSEAS_PART, TINY_SQUARE[0:1]], "MultiPolygon")]
    rows, _, carved = admin0_rings(feats, valid_codes={"FR"}, carve_boxes=CARVE)
    assert [(r.region, r.ring_id, r.is_hole, r.max_lat) for r in rows] == [("FR", 0, 0, 1.0)]
    assert carved == {("GF", 0): 1, ("RE", 0): 1}


def test_carve_only_touches_named_sovereign():
    """同一位置的其他国家要素不被剔除 / Other countries at the same place are untouched."""
    feats = [feature({"ISO_A2_EH": "BR", "NAME": "Brazil"}, [OVERSEAS_PART], "MultiPolygon")]
    rows, _, carved = admin0_rings(feats, valid_codes={"BR"}, carve_boxes=CARVE)
    assert {r.region for r in rows} == {"BR"} and len(rows) == 2
    assert carved == {}


def test_carve_requires_part_fully_inside_box():
    """只与框部分重叠的分块保留（本土不会因框略大而被误删）/ Partly overlapping parts are kept."""
    straddling = [[[49, 49], [55, 49], [55, 55], [49, 55], [49, 49]]]
    feat = feature({"ISO_A2_EH": "FR", "NAME": "France"}, straddling)
    new_feat, carved = carve_territory_parts(feat, CARVE)
    assert carved == {} and new_feat is feat


def test_territory_rings_maps_units_and_reports_missing():
    """GU_A3 映射到 eBird 代码，多个单元合并编号；表中缺失的单元报出 / Map units merge per code."""
    feats = [
        feature({"GU_A3": "MQI", "NAME": "Midway Is."}, SQUARE),
        feature({"GU_A3": "WQI", "NAME": "Wake Atoll"}, BIG_SQUARE),
        feature({"GU_A3": "FXX", "NAME": "France"}, SQUARE),
        feature({"GU_A3": "GUF", "NAME": "French Guiana"}, SQUARE),
    ]
    units = {"MQI": "UM", "WQI": "UM", "GUF": "GF", "NJM": "SJ"}
    rows, missing = territory_rings(feats, valid_codes={"UM", "GF", "SJ"}, units=units)
    assert [(r.region, r.ring_id, r.level) for r in rows] == [("UM", 0, 0), ("UM", 1, 0), ("GF", 0, 0)]
    assert missing == ["NJM"]


def test_real_territory_tables_are_consistent():
    """剔除表的每个领地都有 10m 几何来源，且框互不重叠 / Carve boxes have a geometry source and never overlap."""
    assert set(TERRITORY_CARVE_BOXES) <= set(TERRITORY_UNITS.values())
    boxes = [(code, box) for code, (_, bs) in TERRITORY_CARVE_BOXES.items() for box in bs]
    for i, (code_a, a) in enumerate(boxes):
        assert a[0] < a[1] and a[2] < a[3], code_a
        for code_b, b in boxes[i + 1:]:
            overlap = a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]
            assert not overlap, (code_a, code_b)


def test_find_traditional_names():
    """含繁体字的中文名要报出 / Names with traditional characters are reported."""
    names = {"US-NE": "內布拉斯加州", "US-TX": "得克萨斯州"}
    assert find_traditional_names(names) == {"US-NE": "內布拉斯加州"}
