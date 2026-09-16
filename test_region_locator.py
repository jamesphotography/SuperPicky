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
