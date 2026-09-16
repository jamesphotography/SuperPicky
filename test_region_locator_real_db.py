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
