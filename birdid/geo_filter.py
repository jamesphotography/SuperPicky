# -*- coding: utf-8 -*-
"""
eBird 区域候选过滤器 / eBird region candidate filter.

基于 `birdid/data/ebird_regions.db`（eBird 清单派生的模型类别编号）按层产出候选集：
省州（仅中澳美）→ 国家 → 不过滤。有 GPS 时用 GPS 定位结果，否则用用户手选。
取代 4.6 的 GBIF 1° 网格实现（spec docs/specs/2026-09-16-ebird-region-filter-design.md）。

Yields candidate sets from ebird_regions.db in widening tiers: subnational
(CN/AU/US only), country, unfiltered. GPS location is used when available,
otherwise the user's manual selection. Replaces the 4.6 GBIF 1-degree grid.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from typing import Dict, Iterator, Optional, Set, Tuple

from tools.i18n import t as _t

TIER_SUBNATIONAL = "region_subnational"
TIER_COUNTRY = "region_country"
TIER_NONE = "none"

DB_FILENAME = "ebird_regions.db"


def default_db_path() -> str:
    """
    解析 ebird_regions.db 路径，兼容开发与打包环境 / Resolve the database path.

    返回 / Returns:
        str: 绝对路径 / Absolute path.
    """
    rel = os.path.join("birdid", "data", DB_FILENAME)
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


def _normalize_code(code: Optional[str]) -> Optional[str]:
    """
    区域代码去空白并转大写 / Strip and upper-case a region code.

    参数 / Parameters:
        code (Optional[str]): 原始代码 / Raw code.

    返回 / Returns:
        Optional[str]: 规范化后的代码；None 或空白串返回 None / Normalized code, None for None or blank.
    """
    if code is None:
        return None
    normalized = str(code).strip().upper()
    return normalized or None


class RegionFilter:
    """
    eBird 区域候选过滤器 / eBird region candidate filter.

    区域元数据在构造时全部读入（约 340 行）；物种集合按需读取并缓存，
    避免一次性把几十万个类别编号装进内存。

    Region metadata (about 340 rows) is loaded up front; species sets are read on
    demand and cached, rather than loading hundreds of thousands of ids at once.

    参数 / Parameters:
        db_path (Optional[str]): 数据库路径；None 时自动解析 / Path, auto-resolved when None.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or default_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._regions: Dict[str, Tuple[Optional[str], str, str]] = {}
        self._species: Dict[str, frozenset] = {}
        if not os.path.exists(self.db_path):
            return
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            for code, parent, name_en, name_zh in self._conn.execute(
                "SELECT code, parent, name_en, name_zh FROM regions"
            ):
                self._regions[str(code)] = (parent, str(name_en), str(name_zh))
        except sqlite3.Error as e:
            print(_t("logs.geo_db_failed", e=e))
            self._conn = None
            self._regions = {}

    def is_available(self) -> bool:
        """
        数据库是否可用 / Whether the database is usable.

        返回 / Returns:
            bool: 连接正常且有区域数据 / Connected with region data.
        """
        return self._conn is not None and bool(self._regions)

    def has_region(self, code: Optional[str]) -> bool:
        """
        区域代码是否存在 / Whether a region code exists.

        参数 / Parameters:
            code (Optional[str]): 区域代码 / Region code.

        返回 / Returns:
            bool: 存在为 True / True when present.
        """
        return bool(code) and code in self._regions

    def parent_of(self, code: str) -> Optional[str]:
        """
        省州的上级国家；国家或未知代码返回 None / Parent country of a subnational code.

        参数 / Parameters:
            code (str): 区域代码 / Region code.

        返回 / Returns:
            Optional[str]: 国家代码或 None / Country code or None.
        """
        entry = self._regions.get(code)
        return entry[0] if entry else None

    def display_name(self, code: str, english: bool) -> str:
        """
        区域显示名 / Display name of a region.

        参数 / Parameters:
            code (str): 区域代码 / Region code.
            english (bool): True 取英文名 / English when True.

        返回 / Returns:
            str: 名称；未知代码返回代码本身 / Name, or the code when unknown.
        """
        entry = self._regions.get(code)
        if not entry:
            return code
        return entry[1] if english else (entry[2] or entry[1])

    def species_for(self, code: str) -> Set[int]:
        """
        区域候选类别 / Candidate classes of a region.

        参数 / Parameters:
            code (str): 区域代码 / Region code.

        返回 / Returns:
            set[int]: 类别集合的副本；失败或未知时为空集 / A copy; empty on failure.
        """
        if self._conn is None or code not in self._regions:
            return set()
        with self._lock:
            cached = self._species.get(code)
            if cached is None:
                try:
                    rows = self._conn.execute(
                        "SELECT class_id FROM region_species WHERE region=?", (code,)
                    ).fetchall()
                except sqlite3.Error as e:
                    print(_t("logs.geo_region_failed", e=e))
                    return set()
                cached = frozenset(int(r[0]) for r in rows)
                self._species[code] = cached
        return set(cached)

    def iter_candidates(
        self,
        gps_country: Optional[str],
        gps_subnational: Optional[str],
        manual_country: Optional[str],
        manual_subnational: Optional[str],
    ) -> Iterator[Tuple[Optional[Set[int]], str, Optional[str]]]:
        """
        按层产出候选集，调用方逐层放宽直到有结果 / Yield tiers until recognition succeeds.

        规则 / Rules:
        - 代码不区分大小写，四个入参先去首尾空白并转大写（"au-nsw" 等同 "AU-NSW"）。
        - 只给省州、没给国家时，由省州推出其所属国家（CLI `--region AU-SA`、服务端只传
          region_code、迁移后国家为空的老配置都走这条）；GPS 链同理。
        - GPS 国家存在于库中时只走 GPS 链（照片可能拍于他处，手选不应覆盖实际拍摄地）；
          否则走手选链。
        - 省州必须隶属于同链的国家；未知或不一致的省州按「整个国家」处理，落到国家层
          （老配置里残留的省州代码因此不会让过滤失效，spec §2.5、§6.3）。
        - 空清单层被跳过。

        - Codes are case-insensitive: all four inputs are stripped and upper-cased
          ("au-nsw" equals "AU-NSW").
        - A subnational code alone implies its country (CLI `--region AU-SA`, the
          server receiving only region_code, migrated configs with a null
          country); the same applies to the GPS chain.
        - When the GPS country exists in the database only the GPS chain is used
          (the photo may be from elsewhere, so the manual choice must not
          override it); otherwise the manual chain is used.
        - A subnational code must belong to the chain's country; an unknown or
          mismatched one falls through to the country tier.
        - Empty tiers are skipped.

        参数 / Parameters:
            gps_country (Optional[str]): GPS 定位国家 / Country from GPS.
            gps_subnational (Optional[str]): GPS 定位省州 / Subnational from GPS.
            manual_country (Optional[str]): 手选国家，"GLOBAL" 视同未选 / Manual country.
            manual_subnational (Optional[str]): 手选省州 / Manual subnational.

        返回 / Returns:
            Iterator[tuple]: (候选集或 None, 层标签, 区域代码或 None)，最后一项恒为
                (None, TIER_NONE, None) / Last item is always the unfiltered tier.
        """
        if not self.is_available():
            yield None, TIER_NONE, None
            return

        gps_country, gps_subnational, manual_country, manual_subnational = (
            _normalize_code(code)
            for code in (gps_country, gps_subnational, manual_country, manual_subnational)
        )
        gps_country = self._country_or_implied(gps_country, gps_subnational)
        manual_country = self._country_or_implied(manual_country, manual_subnational)

        if self._is_country(gps_country):
            country, subnational = gps_country, gps_subnational
        elif self._is_country(manual_country):
            country, subnational = manual_country, manual_subnational
        else:
            country, subnational = None, None

        if country and subnational and self.parent_of(subnational) == country:
            species = self.species_for(subnational)
            if species:
                yield species, TIER_SUBNATIONAL, subnational
        if country:
            species = self.species_for(country)
            if species:
                yield species, TIER_COUNTRY, country
        yield None, TIER_NONE, None

    def _country_or_implied(self, country: Optional[str], subnational: Optional[str]) -> Optional[str]:
        """
        国家无效时由已知省州推出国家 / Derive the country from a known subnational when the country is invalid.

        参数 / Parameters:
            country (Optional[str]): 已规范化的国家代码 / Normalized country code.
            subnational (Optional[str]): 已规范化的省州代码 / Normalized subnational code.

        返回 / Returns:
            Optional[str]: 有效国家原样返回；否则省州的上级国家；都没有时原样返回 country /
                The country if valid, else the subnational's parent, else country unchanged.
        """
        if self._is_country(country) or not subnational:
            return country
        parent = self.parent_of(subnational)
        return parent if self._is_country(parent) else country

    def _is_country(self, code: Optional[str]) -> bool:
        """
        是否为库中的国家代码 / Whether a code is a known country.

        参数 / Parameters:
            code (Optional[str]): 代码 / Code.

        返回 / Returns:
            bool: 国家为 True / True for a country.
        """
        return self.has_region(code) and self.parent_of(str(code)) is None

    def close(self) -> None:
        """关闭连接 / Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "RegionFilter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False


_TIER_I18N_KEYS = {
    TIER_SUBNATIONAL: "birdid.geo_tier_subnational",
    TIER_COUNTRY: "birdid.geo_tier_country",
    TIER_NONE: "birdid.geo_tier_none",
}


def describe_tier(geo_info: Optional[dict]) -> str:
    """
    把 geo_info 渲染成一行过滤状态说明 / Render geo_info as one status line.

    参数 / Parameters:
        geo_info (Optional[dict]): identify_bird 返回的 geo_info / geo_info from identify_bird.

    返回 / Returns:
        str: 本地化文本；None 按未过滤处理 / Localized text; None means unfiltered.
    """
    info = geo_info or {}
    tier = info.get("tier", TIER_NONE)
    key = _TIER_I18N_KEYS.get(tier, _TIER_I18N_KEYS[TIER_NONE])
    code = info.get("region_code") or ""
    name = code
    if code:
        try:
            from tools.i18n import get_i18n

            english = str(get_i18n().current_lang or "").startswith("en")
            geo = get_geo_filter()
            if geo is not None:
                name = geo.display_name(code, english)
        except Exception:  # noqa: BLE001
            name = code
    return _t(key, count=info.get("species_count") or 0, region=name)


def get_geo_filter() -> Optional[RegionFilter]:
    """
    进程级单例 / Process-wide singleton.

    返回 / Returns:
        Optional[RegionFilter]: 可用时返回实例，否则 None / Instance or None.
    """
    from config import get_lazy_registry

    def _factory() -> Optional[RegionFilter]:
        try:
            f = RegionFilter()
            if f.is_available():
                return f
            print(_t("logs.geo_unavailable"))
        except Exception as e:  # noqa: BLE001
            print(_t("logs.geo_init_failed", e=e))
        return None

    return get_lazy_registry().get_or_create("birdid.geo_filter", _factory)
