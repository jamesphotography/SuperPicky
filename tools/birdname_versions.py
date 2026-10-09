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
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

#: 覆盖面「相当」的判定：种数达到最大值的这个比例即视为同一档。
#: IOC 两版相差 0.2%，ChinaBirds 只有 IOC 的 13%，这个阈值把它们干净地分开。
#: Versions within this fraction of the largest count count as equally broad.
BREADTH_RATIO = 0.9

#: 鸟名主库导出的版本名（scripts_dev/sync_from_master.py 写入）。它与识鸟结果同名同源，
#: 永远排在最前：改鸟种弹窗选它，手动改出的名字才与识鸟给出的名字一致。
#: The bird-names master catalog; always preferred so manual edits match ID results.
MASTER_VERSION_NAME = "SuperPicky 名录"

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
        is_master = 1 if v.get("version_name") == MASTER_VERSION_NAME else 0
        broad = 1 if int(v.get("species_count") or 0) >= threshold else 0
        return (is_master, broad, parse_edition(v.get("version_name")),
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


def catalog_search_extras(conn: sqlite3.Connection) -> Tuple[str, str]:
    """
    鸟名搜索 SQL 的附加片段：别名搜索与「识鸟模型收录的种优先」排序。

    「SuperPicky 名录」版本多两列：`search_aliases`（主库旧名 + IOC 中文名，用 | 分隔，
    搜「理氏鹨」也能找到现用名「田鹨」）与 `in_model`（0 = IOC 补漏、识鸟模型未收录）。
    其它版本与老库没有这两列，这时返回空片段，查询照旧。改鸟种弹窗与鸟名查询面板
    共用本函数，两处的搜索行为保持一致。

    参数 / Parameters:
    conn (sqlite3.Connection): 已打开的 birdname.db 连接。

    返回 / Returns:
    Tuple[str, str]: (追加在 WHERE 的 OR 条件，含一个 `?` 占位，参数为 `%词%`；
        追加在 ORDER BY 末尾、chinese_name 之前的排序项)。没有对应列时为空串。

    Extra SQL for name search: alias matching and model-first ordering, or empty
    strings when the database lacks the master-catalog columns.
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(birds)")}
    except sqlite3.Error:
        return "", ""
    where = " OR search_aliases LIKE ?" if "search_aliases" in cols else ""
    order = "COALESCE(in_model, 1) DESC, " if "in_model" in cols else ""
    return where, order


# 看起来像鸟种代码的输入：3～7 个英文字母或数字（eBird 代码如 cangoo / livbul1，
# 北美 4 位代码如 CANG）。中文、带空格的英文名、拼音全拼都不会命中。
# Code-like input: 3-7 ASCII letters/digits (eBird "cangoo", IBP "CANG").
_CODE_LIKE_RE = re.compile(r"^[A-Za-z0-9]{3,7}$")


class CodeSearch(NamedTuple):
    """
    鸟种代码搜索的 SQL 片段与参数（见 catalog_code_search）。

    属性 / Attributes:
    where (str): 追加在 WHERE 括号内的 OR 条件
    where_params (tuple): where 的参数
    rank (str): 插进排序 CASE 的 WHEN 子句（放在「精确名字」之后、「部分名字」之前）
    rank_params (tuple): rank 的参数

    SQL fragments and parameters for species-code search.
    """

    where: str
    where_params: tuple
    rank: str
    rank_params: tuple


_NO_CODE_SEARCH = CodeSearch("", (), "", ())


def catalog_code_search(conn: sqlite3.Connection, query: str) -> CodeSearch:
    """
    鸟种代码搜索（issue #119）：北美 4 位代码（IBP，如 CANG）与 eBird 代码（如 cangoo）。

    「SuperPicky 名录」由 sync_from_master 写入 `alpha4` 与 `ebird_code` 两列；老库没有
    这两列，或输入不像代码（中文、含空格、长度不在 3～7）时返回空片段，查询照旧。

    匹配：4 位代码精确（不分大小写）；eBird 代码精确或前缀。
    排序：rank 子句给「代码精确命中」4.5 分——排在中文名 / 英文名 / 拼音首字母精确命中
    （1～4）之后、部分名字匹配（5～6）之前；只靠 eBird 前缀命中的落到 ELSE（最后）。
    这样中文用户输入拼音首字母 btb 时，白头鹎仍排在 eBird 代码以 btb 开头的鸟之前。

    参数 / Parameters:
    conn (sqlite3.Connection): 已打开的 birdname.db 连接
    query (str): 用户输入

    返回 / Returns:
    CodeSearch: SQL 片段与参数；调用方把 where 拼进 WHERE 的 OR 列表，把 rank 插进
        排序 CASE 中「LOWER(abbreviation) = ? THEN 4」之后。

    Species-code search for IBP four-letter and eBird codes. Exact code hits rank
    right after exact name/pinyin-initial hits; prefix-only eBird hits rank last,
    so pinyin initials such as "btb" keep their bird on top.
    """
    q = (query or "").strip()
    if not _CODE_LIKE_RE.match(q):
        return _NO_CODE_SEARCH
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(birds)")}
    except sqlite3.Error:
        return _NO_CODE_SEARCH
    has_alpha, has_ebird = "alpha4" in cols, "ebird_code" in cols
    if not (has_alpha or has_ebird):
        return _NO_CODE_SEARCH

    upper, lower = q.upper(), q.lower()
    where, where_params, rank, rank_params = "", [], "", []
    if has_alpha:
        where += " OR alpha4 = ?"
        where_params.append(upper)
        rank += " WHEN alpha4 = ? THEN 4.5"
        rank_params.append(upper)
    if has_ebird:
        # 代码只含字母数字（已由 _CODE_LIKE_RE 保证），LIKE 无需转义
        # Codes are alphanumeric, so the LIKE pattern needs no escaping.
        where += " OR ebird_code LIKE ?"
        where_params.append(lower + "%")
        rank += " WHEN ebird_code = ? THEN 4.5"
        rank_params.append(lower)
    return CodeSearch(where, tuple(where_params), rank, tuple(rank_params))
