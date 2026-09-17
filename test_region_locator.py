# -*- coding: utf-8 -*-
"""
GPS 区域定位单测（合成边界）/ Region locator tests on synthetic boundaries.

AA：纬 0–10、经 0–10 的国家；CN：纬 20–30、经 100–110，省级 CN-11（经 100–105）
与 CN-12（经 105–110）；BB：纬 40–50、经 0–10，中间挖洞 44–46/4–6，洞里是 CC。

另外两组用于验证 fix round 1 的五步定位顺序（见 birdid/region_locator.py 的
_resolve 文档）：
- CN-13：纬 -3.1–-2.9、经 4.9–5.1 的省级小岛（parent=CN），距 AA 约 331.7 km
  （在 370 km 离岸容差内，足以让"国家层就近匹配"把它误判成 AA），但距 CN
  国家层边界远超 370 km——模拟国家层 admin-0 漏掉小岛、只有省州层 admin-1
  收录的场景（如真实库里的豪勋爵岛）。用于验证省州层严格包含（步骤 b）先于
  国家层离岸就近匹配（步骤 c）生效，否则会被错误就近匹配到 AA。
- CN-20/CN-21：纬 -10–-8、经 4–6 的省级区域 CN-20，中间挖洞（纬 -9.2–-8.8、
  经 4.8–5.2），洞里是省级飞地 CN-21——省州层版本的 BB/CC 洞测试，用于验证
  步骤 (b)（旁路国家层、跨国直查全部省州环）同样正确处理洞。
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
            ("CN-13", "CN", "Island", "海岛省", 1),
            ("CN-20", "CN", "EnclaveOuter", "飞地外层省", 1),
            ("CN-21", "CN", "EnclaveInner", "飞地内层省", 1),
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
            # 省级小岛，parent=CN，距 AA 约 331.7 km（<370 km 容差）、距 CN 国家层
            # 边界 >370 km，仅省州层 admin-1 收录。
            ring("CN-13", 1, 0, -3.1, -2.9, 4.9, 5.1),
            # 省州层洞测试：CN-20 外环挖洞，洞里是飞地 CN-21。
            ring("CN-20", 1, 0, -10, -8, 4, 6),
            ring("CN-20", 1, 1, -9.2, -8.8, 4.8, 5.2, is_hole=1),
            ring("CN-21", 1, 0, -9.2, -8.8, 4.8, 5.2),
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


def test_subnational_containment_beats_country_snap(locator):
    """
    省州层严格包含（步骤 b）优先于国家层离岸就近匹配（步骤 c）/ Subnational
    containment (step b) outranks country-level offshore snap (step c).

    岛屿中心距 AA 仅约 331.7 km（在 370 km 容差内），若国家层就近匹配先于省州层
    直查生效，就会被误判成 AA；正确结果应该是省州层直接包含命中 CN-13。

    The island centre is only ~331.7 km from AA (within the 370 km tolerance);
    if country-level snapping ran before the subnational-wide containment
    check, it would be misjudged as AA. The correct result is a direct
    subnational containment hit on CN-13.
    """
    assert locator.locate(-3.0, 5.0) == LocateResult("CN", "CN-13")


def test_subnational_offshore_snap_step_d(locator):
    """
    国家层与省州层严格包含都找不到时，步骤 (d) 在全部中澳美省州环里离岸就近
    匹配 / When neither country nor subnational containment finds anything,
    step (d) offshore-snaps across all CN/AU/US subnational rings.

    该点距海岛约 50 km，且距全部国家层边界（AA、CN 等）均 >370 km，因此步骤
    a/b/c 全部找不到，只能靠步骤 d 的省州层就近匹配命中 CN-13。

    This point is about 50 km off the island and more than 370 km from every
    country-level ring (AA, CN, ...), so steps a/b/c all fail; only step d's
    subnational-level snap can resolve it to CN-13.
    """
    assert locator.locate(-3.552, 5.0) == LocateResult("CN", "CN-13")


def test_subnational_hole_honoured_in_bypass(locator):
    """
    步骤 (b) 旁路国家层的跨国直查同样正确处理省州层的洞 / The country-bypassing
    cross-country direct search in step (b) also honours subnational holes.

    CN-20 挖了个洞，洞里是飞地 CN-21；两个测试点都落在国家层之外（旁路到
    步骤 b），洞外的点归 CN-20，洞内的点归洞中的飞地 CN-21。

    CN-20 has a hole with the enclave CN-21 inside it; both points fall
    outside every country-level ring (routed through step b) — the point
    outside the hole belongs to CN-20, the point inside the hole belongs to
    the enclave CN-21.
    """
    assert locator.locate(-9.5, 5.0) == LocateResult("CN", "CN-20")
    assert locator.locate(-9.0, 5.0) == LocateResult("CN", "CN-21")
