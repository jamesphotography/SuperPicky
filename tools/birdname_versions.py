#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸟名名录版本的选取 / Choosing which bird-name catalog version to use.

`ioc/birdname.db` 里同时装着多份名录（不同来源、不同年份），改鸟种弹窗与鸟名
查询面板各自都要挑一份来搜。此前两处都按「**导入时间**最新」挑，而 IOC 14.2
恰好比 IOC 15.1 晚导入 3 小时，于是更旧的分类一直被当成最新的在用。

选取规则两条，顺序不能颠倒：

1. **先挑覆盖面。** ChinaBirds 只收 1500 余种中国鸟；选中它会让澳洲、北美的
   用户在改鸟种时一个鸟都搜不到。
2. **再挑版本号。** 覆盖面相当的几份里取版本号最大的（IOC 15.1 > IOC 14.2）。

两条都不能单独用：ChinaBirds.ZS.2026 的「2026」按数字比 IOC 的「15.1」大；
而 IOC 14.2 的种数（11276）又比 15.1（11250）多——拆并所致，种数多不等于更新。

本模块**不依赖 Qt**，两个 UI 入口共用它，避免各挑各的导致默认项不一致。

The catalog is picked by coverage first and edition second; ordering by import
time selected an older taxonomy, and either criterion alone picks the wrong one.
Qt-free so both UI entry points can share one rule.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 覆盖面「相当」的判定：种数达到最大值的这个比例即视为同一档。
#: IOC 两版相差 0.2%，ChinaBirds 只有 IOC 的 13%，这个阈值把它们干净地分开。
#: Versions within this fraction of the largest count count as equally broad.
BREADTH_RATIO = 0.9

#: 从版本名里抓版本号，如 "IOC 15.1" → (15, 1)、"ChinaBirds.12.0.2024" → (12, 0, 2024)
_NUMBERS_RE = re.compile(r"\d+")


def parse_edition(version_name: Optional[str]) -> Tuple[int, ...]:
    """
    从版本名解析出可比较的版本号。

    参数 / Parameters:
    version_name (Optional[str]): 版本名，如 "IOC 15.1"。

    返回 / Returns:
    Tuple[int, ...]: 名字里出现的数字序列；解析不出时为空元组（视作最低版本）。
        名录是外部数据，将来可能出现任何命名，解析失败不得让选取失败。

    Extract a comparable edition tuple; an unparseable name sorts lowest.
    """
    return tuple(int(x) for x in _NUMBERS_RE.findall(version_name or ""))


def order_versions(versions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按选取规则排序：最该用的排在最前。

    参数 / Parameters:
    versions (Sequence[Dict]): 版本记录，需含 `version_name` 与 `species_count`。

    返回 / Returns:
    List[Dict]: 排序后的副本；第一项即 `pick_version` 会选中的那个。

    下拉列表用它排序，保证「查询面板的默认项」与「弹窗实际使用的版本」是同一份
    ——两处曾各按各的顺序取第 0 项。

    Sorted best-first; the dropdown uses this so its default matches the dialog.
    """
    if not versions:
        return []
    largest = max(int(v.get("species_count") or 0) for v in versions)
    threshold = largest * BREADTH_RATIO

    def key(v: Dict[str, Any]):
        broad = 1 if int(v.get("species_count") or 0) >= threshold else 0
        return (broad, parse_edition(v.get("version_name")),
                int(v.get("species_count") or 0))

    return sorted(versions, key=key, reverse=True)


def pick_version(versions: Sequence[Dict[str, Any]]) -> Optional[int]:
    """
    选出该用的名录版本 id。

    参数 / Parameters:
    versions (Sequence[Dict]): 版本记录，需含 `version_id` / `version_name` /
        `species_count`。

    返回 / Returns:
    Optional[int]: 版本 id；`versions` 为空时返回 None。

    结果与传入顺序无关——缺陷的根源正是「顺序即语义」。

    Pick the catalog version id; independent of the input ordering.
    """
    ordered = order_versions(versions)
    return ordered[0]["version_id"] if ordered else None


def load_versions(db_path: str) -> List[Dict[str, Any]]:
    """
    从 birdname.db 读出版本清单（含每版的鸟种数）。

    参数 / Parameters:
    db_path (str): `ioc/birdname.db` 的路径。

    返回 / Returns:
    List[Dict]: 版本记录；库不存在或读失败时为空列表。

    以只读方式打开：这是随包发布的库，查询不该改动它。

    Read the version list; returns [] when the database is unavailable.
    """
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT v.version_id, v.version_name, "
                "       (SELECT COUNT(*) FROM birds b "
                "        WHERE b.version_id = v.version_id) AS species_count "
                "FROM versions v"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def pick_version_from_db(db_path: str) -> Optional[int]:
    """
    直接从库里选出该用的版本 id。

    参数 / Parameters:
    db_path (str): `ioc/birdname.db` 的路径。

    返回 / Returns:
    Optional[int]: 版本 id；库不存在、读失败或没有版本时为 None。

    Convenience wrapper over load_versions + pick_version.
    """
    return pick_version(load_versions(db_path))
