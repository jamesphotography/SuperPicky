# -*- coding: utf-8 -*-
"""
分层地理候选过滤器 / Layered geographic candidate filter.

基于 `birdid/data/geo_distribution.db`（GBIF CC0/CC-BY 观察记录派生）按层产出
候选物种集合：本格强候选 → 本格全部 → 邻域 3x3 → 国家级 → 不过滤。
调用方逐层放宽直到识别有结果，避免旧实现中候选集过窄时直接崩到无过滤。

Yields candidate species sets in widening tiers from geo_distribution.db
(derived from GBIF CC0/CC-BY occurrence data): strong in-cell, all in-cell, 3x3
neighbourhood, country, unfiltered. Callers widen until recognition returns a
result, avoiding the old implementation's collapse straight to no filtering.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from typing import Dict, Iterator, List, Optional, Set, Tuple

from tools.i18n import t as _t

TIER_CELL_STRONG = "L1_cell_strong"
TIER_CELL_ALL = "L2_cell_all"
TIER_NEIGHBORHOOD = "L3_neighborhood"
TIER_COUNTRY = "L4_country"
TIER_NONE = "L5_none"

# L1 默认策略：保留累积覆盖该格 99.9% 观察记录的物种（Task 1 标定值）
# Default L1 strategy: keep species covering 99.9% of the cell's records.
_DEFAULT_TIER1 = "cumulative:0.999"


def cell_id_for(lat: float, lon: float) -> int:
    """
    把经纬度编码成 1°网格编号 / Encode a coordinate into a 1-degree cell id.

    参数 / Parameters:
        lat (float): 纬度 / Latitude, -90..90.
        lon (float): 经度 / Longitude, -180..180.

    返回 / Returns:
        int: 0..64799 的网格编号 / Cell id in 0..64799.
    """
    lat_bin = max(-90, min(89, _floor_int(lat)))
    lon_bin = max(-180, min(179, _floor_int(lon)))
    return (lat_bin + 90) * 360 + (lon_bin + 180)


def _floor_int(value: float) -> int:
    """
    向下取整为 int / Floor a float to int.

    参数 / Parameters:
        value (float): 输入值 / Input value.

    返回 / Returns:
        int: 向下取整结果 / Floored value.
    """
    return int(value // 1)


def _neighbour_cells(lat: float, lon: float) -> List[int]:
    """
    3x3 邻域的网格编号 / The cell ids of the 3x3 neighbourhood.

    跨日期变更线时经度回绕；越过极点的纬度直接跳过。

    Longitude wraps across the dateline; latitudes beyond the poles are skipped.

    参数 / Parameters:
        lat (float): 中心纬度 / Centre latitude.
        lon (float): 中心经度 / Centre longitude.

    返回 / Returns:
        list[int]: 最多 9 个网格编号 / Up to nine cell ids.
    """
    lat_bin = max(-90, min(89, _floor_int(lat)))
    lon_bin = max(-180, min(179, _floor_int(lon)))
    out: List[int] = []
    for dlat in (-1, 0, 1):
        la = lat_bin + dlat
        if la < -90 or la > 89:
            continue
        for dlon in (-1, 0, 1):
            lo = lon_bin + dlon
            if lo > 179:
                lo -= 360
            elif lo < -180:
                lo += 360
            out.append((la + 90) * 360 + (lo + 180))
    return out


def default_db_path() -> str:
    """
    解析 geo_distribution.db 路径，兼容开发与打包环境。

    Resolve geo_distribution.db, covering development and packaged builds.

    返回 / Returns:
        str: 绝对路径 / Absolute path.
    """
    rel = os.path.join("birdid", "data", "geo_distribution.db")
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


class GeoFilter:
    """
    分层地理候选过滤器 / Layered geographic candidate filter.

    参数 / Parameters:
        db_path (Optional[str]): 数据库路径；None 时自动解析 /
            Database path; auto-resolved when None.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._tier1_strategy = _DEFAULT_TIER1
        if os.path.exists(self.db_path):
            try:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                row = self._conn.execute(
                    "SELECT value FROM meta WHERE key='tier1_threshold'"
                ).fetchone()
                if row and row[0]:
                    self._tier1_strategy = str(row[0])
            except sqlite3.Error as e:
                print(_t("logs.geo_db_failed", e=e))
                self._conn = None

    def is_available(self) -> bool:
        """
        数据库是否可用 / Whether the database is usable.

        返回 / Returns:
            bool: 连接正常且 cell_species 非空时为 True /
                True when connected and cell_species is non-empty.
        """
        if self._conn is None:
            return False
        try:
            row = self._conn.execute("SELECT 1 FROM cell_species LIMIT 1").fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def _cell_counts(self, cell_ids: List[int]) -> Dict[int, int]:
        """
        查询若干网格合并后的物种观察数 / Merged per-species counts for cells.

        参数 / Parameters:
            cell_ids (list[int]): 网格编号 / Cell ids.

        返回 / Returns:
            dict[int, int]: {class_id: 观察记录数} / {class_id: occurrence count}.
        """
        if self._conn is None or not cell_ids:
            return {}
        try:
            marks = ",".join("?" * len(cell_ids))
            rows = self._conn.execute(
                f"SELECT class_id, SUM(n) FROM cell_species "
                f"WHERE cell_id IN ({marks}) GROUP BY class_id",
                cell_ids,
            ).fetchall()
            return {int(c): int(n) for c, n in rows}
        except sqlite3.Error as e:
            print(_t("logs.geo_cell_failed", e=e))
            return {}

    def _tier1_filter(self, counts: Dict[int, int]) -> Set[int]:
        """
        按标定的策略裁剪出 L1 强候选 / Apply the calibrated L1 strategy.

        支持三种策略，由 `meta.tier1_threshold` 决定：
        `cumulative:<cover>` 保留累积覆盖 cover 比例记录的物种（Task 1 标定为 0.999）；
        `absolute` 保留 n>=5；`hybrid` 保留 n>=max(2, 0.0001*总数)。

        Three strategies selected by `meta.tier1_threshold`: `cumulative:<cover>`
        keeps species covering that fraction of records (calibrated to 0.999);
        `absolute` keeps n>=5; `hybrid` keeps n>=max(2, 0.0001*total).

        参数 / Parameters:
            counts (dict[int, int]): {class_id: 观察记录数} / per-class counts.

        返回 / Returns:
            set[int]: L1 候选 class_id 集合 / L1 candidate class ids.
        """
        if not counts:
            return set()

        strategy = self._tier1_strategy
        if strategy == "absolute":
            return {c for c, n in counts.items() if n >= 5}
        if strategy == "hybrid":
            total = sum(counts.values())
            thr = max(2, int(total * 0.0001))
            return {c for c, n in counts.items() if n >= thr}

        cover = 0.999
        if strategy.startswith("cumulative:"):
            try:
                cover = float(strategy.split(":", 1)[1])
            except ValueError:
                cover = 0.999

        total = sum(counts.values())
        kept: Set[int] = set()
        acc = 0
        for c, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
            kept.add(c)
            acc += n
            if acc >= total * cover:
                break
        return kept

    def _country_species(self, country_code: str) -> Set[int]:
        """
        国家级候选 / Country-level candidates.

        参数 / Parameters:
            country_code (str): ISO 3166-1 alpha-2 代码 / ISO country code.

        返回 / Returns:
            set[int]: 该国候选 class_id 集合 / Candidate class ids.
        """
        if self._conn is None or not country_code:
            return set()
        try:
            rows = self._conn.execute(
                "SELECT class_id FROM country_species WHERE country=?",
                (country_code.upper(),),
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.Error as e:
            print(_t("logs.geo_country_failed", e=e))
            return set()

    def iter_candidates(
        self,
        lat: Optional[float],
        lon: Optional[float],
        country_code: Optional[str] = None,
    ) -> Iterator[Tuple[Optional[Set[int]], str]]:
        """
        按层产出候选集，调用方逐层放宽直到有结果。

        空层会被跳过，稀疏网格因此不会产出空候选集（那会屏蔽掉所有类别）。

        Yield candidate sets tier by tier; the caller widens until recognition
        succeeds. Empty tiers are skipped so a sparse cell never produces an
        empty candidate set, which would mask every class.

        参数 / Parameters:
            lat (Optional[float]): 纬度，无 GPS 时为 None / Latitude or None.
            lon (Optional[float]): 经度，无 GPS 时为 None / Longitude or None.
            country_code (Optional[str]): 国家代码，用于 L4 / Country code for L4.

        返回 / Returns:
            Iterator[tuple]: (候选集或 None, 层标签)；最后一项恒为
                (None, TIER_NONE) 表示不过滤 / (candidates or None, tier label);
                the last item is always (None, TIER_NONE), meaning unfiltered.
        """
        if not self.is_available():
            yield None, TIER_NONE
            return

        has_gps = lat is not None and lon is not None
        if has_gps:
            counts = self._cell_counts([cell_id_for(float(lat), float(lon))])
            l1 = self._tier1_filter(counts)
            if l1:
                yield l1, TIER_CELL_STRONG
            l2 = set(counts)
            if l2 and l2 != l1:
                yield l2, TIER_CELL_ALL
            l3 = set(self._cell_counts(_neighbour_cells(float(lat), float(lon))))
            if l3 and l3 != l2:
                yield l3, TIER_NEIGHBORHOOD

        if country_code:
            l4 = self._country_species(country_code)
            if l4:
                yield l4, TIER_COUNTRY

        yield None, TIER_NONE

    def close(self) -> None:
        """关闭连接 / Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "GeoFilter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False


_TIER_I18N_KEYS = {
    TIER_CELL_STRONG: "birdid.geo_tier_cell_strong",
    TIER_CELL_ALL: "birdid.geo_tier_cell_all",
    TIER_NEIGHBORHOOD: "birdid.geo_tier_neighborhood",
    TIER_COUNTRY: "birdid.geo_tier_country",
    TIER_NONE: "birdid.geo_tier_none",
}


def describe_tier(geo_info: Optional[dict]) -> str:
    """
    把 geo_info 渲染成一行可读的过滤状态说明。

    Render geo_info into a single human-readable filter-status line.

    参数 / Parameters:
        geo_info (Optional[dict]): identify_bird 返回的 geo_info /
            The geo_info dict returned by identify_bird.

    返回 / Returns:
        str: 已本地化的说明文本；geo_info 为空时按未过滤处理 /
            Localized description; treated as unfiltered when geo_info is None.
    """
    info = geo_info or {}
    tier = info.get("tier", TIER_NONE)
    key = _TIER_I18N_KEYS.get(tier, _TIER_I18N_KEYS[TIER_NONE])
    return _t(
        key,
        count=info.get("species_count") or 0,
        country=info.get("country_code") or "?",
    )


def get_geo_filter() -> Optional["GeoFilter"]:
    """
    进程级单例 / Process-wide singleton.

    返回 / Returns:
        Optional[GeoFilter]: 可用时返回实例，否则 None / Instance or None.
    """
    from config import get_lazy_registry

    def _factory() -> Optional["GeoFilter"]:
        try:
            f = GeoFilter()
            if f.is_available():
                return f
            print(_t("logs.geo_unavailable"))
        except Exception as e:  # noqa: BLE001
            print(_t("logs.geo_init_failed", e=e))
        return None

    return get_lazy_registry().get_or_create("birdid.geo_filter", _factory)
