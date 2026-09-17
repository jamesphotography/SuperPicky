# -*- coding: utf-8 -*-
"""
真实 ebird_regions.db 定位验收（spec §8 验收 3）/ Acceptance on the real database.
"""
import os

import pytest

from birdid.region_geometry import point_in_ring
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
        # fix round 1：国家层 admin-0（50m）漏掉/误判的小块领土，只有省州层
        # admin-1（10m）能正确收录，用于验证步骤 (b) 的跨国直查兜底
        # （见 birdid/region_locator.py 的 _resolve 文档）。
        # fix round 1: territory the country-level admin-0 (50m) layer drops
        # or misattributes, correctly captured only by the subnational-level
        # admin-1 (10m) layer — exercises step (b)'s cross-country direct
        # search fallback (see the _resolve docstring in
        # birdid/region_locator.py).
        ("塞拜岛 Saibai", -9.425, 142.645, LocateResult("AU", "AU-QLD")),
        ("博伊古岛 Boigu", -9.286, 142.204, LocateResult("AU", "AU-QLD")),
        # 万山群岛：万山主岛中心点在国家层被划入 HK（50m 精度），但省州层
        # admin-1 正确收录为 CN-44（广东）；步骤 (b) 优先于步骤 (a) 之后的
        # 国家层就近匹配生效。
        # Wanshan Archipelago: the island centre is swallowed into HK at the
        # country level (50m), but the subnational admin-1 layer correctly
        # attributes it to CN-44 (Guangdong); step (b) resolves it directly.
        ("万山群岛 Wanshan", 21.97, 113.75, LocateResult("CN", "CN-44")),
        # 罗伯茨角（Point Roberts, WA）：整块半岛在国家层 50m 精度下大部分被并入
        # 加拿大（美加边界在此处被粗化拉直），省州层 US-WA 的更高精度多边形
        # 正确收录了它；审校给出的原始坐标 (48.97, -123.09) 恰好落在该省州环
        # 量化后的一个顶点上（点在多边形算法在顶点处结果不确定），故换成同一
        # 半岛上明确落在环内部、且 ±0.002°（约 220 m）扰动下结果稳定的坐标
        # （已用 RegionLocator._resolve 逐点验证，步骤 a 在此点两个国家层都
        # 找不到、步骤 b 直接命中 US-WA）。
        # Point Roberts, WA: most of this tiny peninsula is swallowed into
        # Canada by the coarse 50m country-level layer (the US-Canada border
        # is straightened across it there); the higher-resolution US-WA
        # subnational polygon correctly attributes it to Washington. The
        # reviewer's original coordinate (48.97, -123.09) happens to sit
        # exactly on a vertex of that quantized subnational ring (point-in-
        # polygon is ill-defined exactly on a vertex), so this uses a nearby
        # coordinate on the same peninsula that is unambiguously interior and
        # stable under +-0.002 degrees (~220 m) of perturbation (verified
        # point-by-point via RegionLocator._resolve: step a finds neither
        # country there, step b hits US-WA directly).
        ("罗伯茨角 Point Roberts", 48.975, -123.08, LocateResult("US", "US-WA")),
        ("豪勋爵岛附近海域", -31.60, 159.20, LocateResult("AU", "AU-NSW")),
        # 海外领地（终审修复）：eBird 把它们当独立国家，有自己的清单；以前被并进宗主国
        # （卡宴→FR 等），会用宗主国清单过滤。几何取自 10m map_units，并从 50m 宗主国剔除。
        # Overseas territories (final-review fix): eBird treats them as countries
        # with their own lists; they used to resolve to the sovereign (Cayenne -> FR).
        ("卡宴 Cayenne", 4.93, -52.33, LocateResult("GF", None)),
        ("圣但尼 Saint-Denis, Réunion", -20.88, 55.45, LocateResult("RE", None)),
        ("皮特尔角城 Pointe-à-Pitre", 16.24, -61.53, LocateResult("GP", None)),
        ("法兰西堡 Fort-de-France", 14.60, -61.07, LocateResult("MQ", None)),
        ("马穆楚 Mamoudzou", -12.78, 45.23, LocateResult("YT", None)),
        ("克拉伦代克 Kralendijk", 12.15, -68.27, LocateResult("BQ", None)),
        ("飞鱼湾 Flying Fish Cove", -10.42, 105.68, LocateResult("CX", None)),
        ("科科斯西岛 West Island", -12.19, 96.83, LocateResult("CC", None)),
        ("中途岛 Midway", 28.21, -177.38, LocateResult("UM", None)),
        ("朗伊尔城 Longyearbyen", 78.22, 15.65, LocateResult("SJ", None)),
        ("阿姆斯特丹 Amsterdam", 52.37, 4.90, LocateResult("NL", None)),
        ("麦夸里岛 Macquarie", -54.50, 158.94, LocateResult("AU", "AU-TAS")),
    ],
)
def test_known_locations(locator, name, lat, lon, expected):
    """已知坐标定位正确 / Known coordinates resolve correctly."""
    assert locator.locate(lat, lon) == expected, name


@pytest.mark.parametrize(
    "name,lat,lon",
    [
        ("卡宴 Cayenne", 4.93, -52.33),
        ("圣但尼 Saint-Denis", -20.88, 55.45),
        ("皮特尔角城 Pointe-à-Pitre", 16.24, -61.53),
        ("克拉伦代克 Kralendijk", 12.15, -68.27),
        ("中途岛 Midway", 28.21, -177.38),
        ("熊岛 Bjørnøya", 74.43, 19.00),
        ("巴黎", 48.86, 2.35),
    ],
)
def test_territory_not_also_contained_by_sovereign(locator, name, lat, lon):
    """
    领地点最多被一个国家外环严格包含 / At most one country's outer rings contain a territory point.

    「外包框面积小者优先」会掩盖宗主国分块未剔除的问题，所以直接数包含它的国家。
    The smaller-bbox tie-break would hide an uncarved sovereign part, so count
    the containing countries directly.
    """
    containing = {
        ring.region
        for ring in locator._groups[(0, None)]
        if not ring.is_hole
        and ring.bbox[0] <= lat <= ring.bbox[1]
        and ring.bbox[2] <= lon <= ring.bbox[3]
        and point_in_ring(lat, lon, locator._points(ring))
    }
    assert len(containing) == 1, (name, sorted(containing))
