# -*- coding: utf-8 -*-
"""
GPS → eBird 国家/省州离线定位 / Offline GPS to eBird country/subnational lookup.

取代 reverse_geocoder：后者从未进入打包版（不在任何 requirements 中），且按最近城市判定，
在省界与海岸附近会出错（spec §2.4）。这里用 ebird_regions.db 中的 Natural Earth 边界做
点在多边形判断；落在海上时在 200 海里（约 370 km）内就近归属。

定位分五步，严格按此顺序（见 RegionLocator._resolve）：
    a. 国家层严格包含（admin-0）。命中则直接返回该国家，省州用 (a) 内嵌逻辑
       （先包含、找不到再离岸就近）在该国家范围内查一次。
    b. 全部中澳美省州环严格包含，旁路国家层（admin-1，仅当 a 完全找不到时才查）。
    c. 国家层离岸容差就近匹配（仅当 a、b 都找不到时才查）。
    d. 全部中澳美省州环离岸容差就近匹配（仅当 a、b、c 都找不到时才查）。
    e. 以上全部找不到，返回 (None, None)。

之所以把 (b) 放在 (c) 之前：admin-0（50m，国家层）比 admin-1（10m，省州层）分
辨率粗得多，会整体漏掉像豪勋爵岛这样的小岛——国家层严格包含判断不到、离岸
容差匹配又因为最近的国家层多边形太远（>370 km）而落空；但该岛在更精细的省
州层 admin-1 里是完整收录的。因此"包含"永远优先于"就近匹配"：只要在任一层
级能严格包含，就不该退而求其次去做距离匹配。已经在国家层被严格包含的点
（大陆内部的省界分歧点）不受这条兜底逻辑影响，始终按 (a) 就地在该国家内解析，
不会被 (b)/(c)/(d) 的跨国兜底改判。

Replaces reverse_geocoder, which never shipped in packaged builds and judges by
nearest city, failing near borders and coasts (spec section 2.4). This uses the
Natural Earth boundaries stored in ebird_regions.db for point-in-polygon tests,
snapping offshore points to the nearest region within 200 nautical miles.

Location resolves in five steps, strictly in this order (see
RegionLocator._resolve):
    a. Country-level strict containment (admin-0). On a hit, the subnational
       unit is looked up within that same country (containment first, then
       offshore snap as a fallback), and the result is returned immediately.
    b. Strict containment across every CN/AU/US subnational ring, bypassing
       the country level (admin-1; only tried when (a) found nothing).
    c. Country-level offshore snap within tolerance (only tried when (a) and
       (b) both found nothing).
    d. Offshore snap across every CN/AU/US subnational ring (only tried when
       (a), (b) and (c) all found nothing).
    e. None of the above matched: return (None, None).

Why (b) comes before (c): admin-0 (50m, country level) is far coarser than
admin-1 (10m, subnational level) and can drop small islands entirely (e.g.
Lord Howe Island) — the country-level containment test misses them, and the
offshore snap also fails because the nearest country-level polygon is too far
away (>370 km). The finer subnational level does contain them. Containment
must always win over distance-based snapping: whenever a point is strictly
inside some polygon at either level, matching by distance instead would be
wrong. Points already resolved by strict country-level containment (i.e.
ordinary land-border disagreements) are unaffected by this fallback — they
are always resolved within that country at step (a) and never reconsidered by
the (b)/(c)/(d) cross-country fallbacks.
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
        # _groups[(level, parent)]：level=0 时 parent 恒为 None（全部国家一组）；
        # level=1 时按上级国家分组，用于"已知国家，只在其省州内查"的场景（步骤 a/c）。
        # _level1_all：全部省州环拼成一份平铺列表，用于"旁路国家层，跨国直查"的
        # 场景（步骤 b/d）；region 代码全局唯一（如 CN-11、AU-NSW），跨国合并不会混淆。
        # _parent_of：省州代码 → 所属国家代码，把 (b)/(d) 的直查结果换算回国家。
        #
        # _groups[(level, parent)]: parent is always None at level 0 (one group
        # for all countries); at level 1 it is grouped by parent country, used
        # when the country is already known and we only search within it
        # (steps a/c). _level1_all: every subnational ring flattened into one
        # list, used to search across all countries directly, bypassing the
        # country level (steps b/d); region codes are globally unique (e.g.
        # CN-11, AU-NSW), so merging countries cannot collide. _parent_of:
        # subnational code -> owning country code, to translate a (b)/(d) hit
        # back into a country.
        self._groups: Dict[Tuple[int, Optional[str]], List[_Ring]] = defaultdict(list)
        self._level1_all: List[_Ring] = []
        self._parent_of: Dict[str, str] = {}
        self._cache: "OrderedDict[Tuple[float, float], LocateResult]" = OrderedDict()
        self._lock = threading.Lock()
        conn = sqlite3.connect(db_path)
        try:
            parents = dict(conn.execute("SELECT code, parent FROM regions").fetchall())
            for region, level, is_hole, a, b, c, d, blob in conn.execute(
                "SELECT region, level, is_hole, min_lat, max_lat, min_lon, max_lon, coords "
                "FROM boundaries ORDER BY region, level, ring_id"
            ):
                level = int(level)
                parent = parents.get(region) if level == 1 else None
                ring = _Ring(region, bool(is_hole), (a, b, c, d), bytes(blob))
                self._groups[(level, parent)].append(ring)
                if level == 1:
                    self._level1_all.append(ring)
                    if parent is not None:
                        self._parent_of[region] = parent
        finally:
            conn.close()

    def locate(self, lat: float, lon: float) -> LocateResult:
        """
        定位坐标所在的国家与省州 / Locate the country and subnational unit.

        结果按 0.01° 取整缓存：同一批照片多拍于同一地点。实际的五步查找逻辑见
        _resolve（含缓存未命中时的完整判定顺序说明）。

        Results are cached at 0.01 degree: a batch of photos usually shares a
        location. The actual five-step lookup lives in _resolve (see its
        docstring for the full ordering rationale on a cache miss).

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
        result = self._resolve(float(lat), float(lon))
        with self._lock:
            self._cache[key] = result
            if len(self._cache) > _CACHE_LIMIT:
                self._cache.popitem(last=False)
        return result

    def _resolve(self, lat: float, lon: float) -> LocateResult:
        """
        五步定位流程，严格按顺序执行 / Five-step location flow, executed strictly in order.

        a. 国家层严格包含；命中则连同该国省州（先包含后离岸就近）一并返回。
        b. 找不到则旁路国家层，直接在全部中澳美省州环里严格包含查找（分辨率更
           高的 admin-1 收录了国家层 admin-0 漏掉的小岛，例如豪勋爵岛）。
        c. 还找不到则退回国家层离岸容差就近匹配，连同该国省州一并返回。
        d. 依然找不到则在全部中澳美省州环里离岸容差就近匹配。
        e. 全部失败，返回 (None, None)。

        "包含"永远先于"就近匹配"尝试：(b) 在 (c) 之前，避免国家层的粗分辨率把
        本该属于某国省州的点误判成需要跨国就近匹配、甚至匹配错国家。已经在
        (a) 被国家层严格包含的点不会进入 (b)/(c)/(d)，行为与旧版一致。

        a. Country-level strict containment; on a hit, its subnational unit
           (containment, then offshore snap) is resolved and returned together.
        b. If not found, bypass the country level and search all CN/AU/US
           subnational rings directly by strict containment (the higher-
           resolution admin-1 layer includes small islands admin-0 drops,
           e.g. Lord Howe Island).
        c. If still not found, fall back to country-level offshore snap,
           together with that country's subnational unit.
        d. If still not found, offshore-snap across all CN/AU/US subnational
           rings.
        e. All of the above failed: return (None, None).

        Containment is always attempted before snapping: (b) precedes (c) so
        the coarse country layer cannot force a point that truly belongs to a
        CN/AU/US subnational polygon into a cross-country distance match (or
        the wrong country entirely). A point already resolved by strict
        country-level containment at (a) never reaches (b)/(c)/(d), matching
        prior behavior for ordinary land-border points.

        参数 / Parameters:
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            LocateResult: 定位结果 / The result.
        """
        country_rings = self._groups.get((0, None), [])

        # a. 国家层严格包含 / (a) Country-level strict containment.
        country = self._containing(country_rings, lat, lon)
        if country is not None:
            return LocateResult(country, self._subnational_in(country, lat, lon))

        # b. 旁路国家层，全部中澳美省州环严格包含 / (b) Bypass the country level,
        # strict containment across all CN/AU/US subnational rings.
        sub = self._containing(self._level1_all, lat, lon)
        if sub is not None:
            return LocateResult(self._parent_of.get(sub), sub)

        # c. 国家层离岸容差就近匹配 / (c) Country-level offshore snap.
        country = self._nearest_within(country_rings, lat, lon)
        if country is not None:
            return LocateResult(country, self._subnational_in(country, lat, lon))

        # d. 全部中澳美省州环离岸容差就近匹配 / (d) Offshore snap across all
        # CN/AU/US subnational rings.
        sub = self._nearest_within(self._level1_all, lat, lon)
        if sub is not None:
            return LocateResult(self._parent_of.get(sub), sub)

        # e. 彻底无法定位 / (e) Completely unlocated.
        return LocateResult(None, None)

    def _subnational_in(self, country: str, lat: float, lon: float) -> Optional[str]:
        """
        在已知国家范围内查省州：先严格包含，找不到再离岸就近匹配 /
        Subnational lookup within a known country: strict containment first,
        then an offshore snap fallback.

        仅中澳美有省州数据；其余国家恒返回 None。

        Only CN/AU/US have subnational data; every other country returns None.

        参数 / Parameters:
            country (str): 已确定的国家代码 / The already-resolved country code.
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            Optional[str]: 省州代码或 None / Subnational code or None.
        """
        if country not in SUBNATIONAL_COUNTRIES:
            return None
        rings = self._groups.get((1, country), [])
        sub = self._containing(rings, lat, lon)
        if sub is not None:
            return sub
        return self._nearest_within(rings, lat, lon)

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

    def _containing(self, rings: List[_Ring], lat: float, lon: float) -> Optional[str]:
        """
        严格点在多边形内判断（同一层级、同一批环共用）/ Strict point-in-polygon
        test, shared by every level and every ring group.

        洞（is_hole=1）命中时排除其所属区域（该区域在洞内不算包含该点，通常是
        因为洞里是另一块飞地，由飞地自身的外环单独命中）；多个外环同时命中时
        取外包框面积最小者（更具体的区域优先）。

        A hit inside a hole (is_hole=1) excludes that hole's owning region
        (it does not count as containing the point there — typically because
        the hole is an enclave, matched separately by the enclave's own outer
        ring); when multiple outer rings hit, the smallest bbox area wins
        (the more specific region takes priority).

        参数 / Parameters:
            rings (list[_Ring]): 待查找的环集合 / Rings to search.
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            Optional[str]: 区域代码或 None / Region code or None.
        """
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
        return inside[0][1] if inside else None

    def _nearest_within(self, rings: List[_Ring], lat: float, lon: float) -> Optional[str]:
        """
        离岸容差内就近匹配（同一层级、同一批环共用）/ Offshore snap to the
        nearest ring within tolerance, shared by every level and ring group.

        只考虑外环（洞不参与就近匹配，一个点不会因为"离某个洞很近"而被判给
        那个洞所属的区域）。

        Only outer rings are considered (holes never participate in snapping —
        a point is never assigned to a hole's owning region merely for being
        near that hole).

        参数 / Parameters:
            rings (list[_Ring]): 待查找的环集合 / Rings to search.
            lat (float): 纬度 / Latitude.
            lon (float): 经度 / Longitude.

        返回 / Returns:
            Optional[str]: OFFSHORE_TOLERANCE_KM 内最近的区域代码，否则 None /
                The nearest region code within OFFSHORE_TOLERANCE_KM, or None.
        """
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
