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
        if country is None:
            # 国家层用 admin-0（50m）会漏掉极小海岛（如豪勋爵岛），而该岛的
            # 省州层 admin-1（10m）分辨率更高、确实收录了它；国家层完全找不到
            # 结果时，直接在全部省州环里兜底查一次。
            #
            # The country level uses admin-0 (50m), which can drop tiny
            # islands (e.g. Lord Howe Island); its admin-1 (10m) subnational
            # boundary is higher resolution and does include them. Only when
            # the country level finds nothing at all do we fall back to a
            # direct search across all subnational rings.
            direct = self._find_subnational_direct(float(lat), float(lon))
            if direct is not None:
                country, subnational = direct
        if subnational is None and country in SUBNATIONAL_COUNTRIES:
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

    def _find_subnational_direct(self, lat: float, lon: float) -> Optional[Tuple[str, str]]:
        """
        直接在所有省州环中查找包含该点的区域，旁路国家层 / Search all subnational
        rings directly, bypassing the country level.

        仅作为国家层完全找不到结果时的兜底（见 locate 中的调用处）；不做离岸
        容差匹配，只做严格的点在多边形内判断。

        Only called as a fallback when the country level finds nothing (see
        the call site in locate); performs a strict point-in-polygon test
        only, no offshore tolerance.

        参数 / Parameters:
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            Optional[tuple[str, str]]: (国家代码, 省州代码) 或 None /
                (country, subnational) or None.
        """
        best_area = math.inf
        best: Optional[Tuple[str, str]] = None
        for (level, parent), rings in self._groups.items():
            if level != 1 or parent is None:
                continue
            for ring in rings:
                if ring.is_hole:
                    continue
                a, b, c, d = ring.bbox
                if not (a <= lat <= b and c <= lon <= d):
                    continue
                if ring.area < best_area and point_in_ring(lat, lon, self._points(ring)):
                    best_area = ring.area
                    best = (parent, ring.region)
        return best

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
