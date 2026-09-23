# -*- coding: utf-8 -*-
"""
鸟名名录版本的选取规则 / Picking which bird-name catalog version to use.

现网缺陷（2026-09-23 发现）：`ioc/birdname.db` 里同时装着多份名录，改鸟种弹窗
与鸟名查询面板都用「**导入时间**最新」来选，而 IOC 14.2 恰好比 IOC 15.1 晚导入
3 小时，于是更旧的分类被当成了最新的：

    v10  IOC 14.2              11276 种   导入 2026-03-05 08:59   ← 一直在用
    v8   IOC 15.1              11250 种   导入 2026-03-05 05:45   ← 更新的分类，被跳过
    v6   ChinaBirds.ZS.2026     1516 种
    v3   ChinaBirds.12.0.2024   1516 种   is_active=1（无人读取的死字段）
    v2   ChinaBirds.10.0.2022   1501 种

正确的规则是两条，顺序不能颠倒：

1. **先挑覆盖面**。ChinaBirds 只收 1500 余种中国鸟，选它会让澳洲、北美的用户在
   改鸟种时**一个鸟都搜不到**——这正是用户 2026-09-23 差点踩到的坑。
2. **再挑版本号**。覆盖面相当的几份里取版本号最大的，IOC 15.1 > IOC 14.2。

不能只按版本号：ChinaBirds.ZS.2026 的年份比 IOC 15.1 大，纯比数字会选中它。
也不能只按种数：IOC 14.2 的 11276 比 15.1 的 11250 还多 26 种（拆并所致）。

The catalog is chosen by coverage first and edition second; ordering by import
time picked an older taxonomy, and either criterion alone picks the wrong one.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

#: 本机 ioc/birdname.db 的真实版本构成 / The real versions in the shipped database.
_REAL = [
    {"version_id": 2, "version_name": "ChinaBirds.10.0.2022", "species_count": 1501},
    {"version_id": 3, "version_name": "ChinaBirds.12.0.2024", "species_count": 1516},
    {"version_id": 6, "version_name": "ChinaBirds.ZS.2026", "species_count": 1516},
    {"version_id": 8, "version_name": "IOC 15.1", "species_count": 11250},
    {"version_id": 10, "version_name": "IOC 14.2", "species_count": 11276},
]


def test_picks_the_newest_edition_of_the_broadest_catalog():
    """
    真实数据：必须选中 IOC 15.1（v8），而不是导入更晚的 IOC 14.2。

    这是缺陷本身：弹窗此前一直在用 14.2。
    """
    from tools.birdname_versions import pick_version

    assert pick_version(_REAL) == 8


def test_coverage_beats_edition_number():
    """
    覆盖面优先于版本号。

    ChinaBirds.ZS.2026 的「2026」比 IOC 的「15.1」大得多，纯比版本号会选中它，
    而它只有 1516 种——澳洲用户会一个鸟都搜不到。
    """
    from tools.birdname_versions import pick_version

    assert pick_version(_REAL) != 6


def test_edition_beats_species_count_within_the_same_breadth():
    """
    覆盖面相当时才比版本号，且不能用种数决胜。

    IOC 14.2 比 15.1 还多 26 种（拆并所致），按种数取最大会再次选错。
    """
    from tools.birdname_versions import pick_version

    assert pick_version(_REAL) == 8


def test_a_newer_ioc_would_win_when_added():
    """将来导入 IOC 16.1 时应自动切过去，不需要再改代码。"""
    from tools.birdname_versions import pick_version

    plus = _REAL + [{"version_id": 12, "version_name": "IOC 16.1",
                     "species_count": 11300}]
    assert pick_version(plus) == 12


def test_import_order_is_irrelevant():
    """
    选取结果不得受列表顺序影响。

    缺陷的根源正是「顺序即语义」，所以这条要钉死。
    """
    from tools.birdname_versions import pick_version

    assert pick_version(list(reversed(_REAL))) == 8


def test_single_version_is_returned_as_is():
    """只有一份名录时就用它，不做任何判断。"""
    from tools.birdname_versions import pick_version

    assert pick_version([_REAL[0]]) == 2


def test_no_versions_yields_none():
    """空库返回 None，由调用方按「没有名录」降级。"""
    from tools.birdname_versions import pick_version

    assert pick_version([]) is None


def test_unparseable_names_do_not_crash():
    """
    版本名解析不出版本号时不得抛异常。

    名录是外部数据，将来可能出现任何命名；解析失败的按最低版本处理，
    但仍然参与覆盖面比较——有总比没有强。
    """
    from tools.birdname_versions import pick_version

    odd = [{"version_id": 1, "version_name": "", "species_count": 9000},
           {"version_id": 2, "version_name": "自定义名录", "species_count": 9100}]
    assert pick_version(odd) in (1, 2)


def test_ordering_puts_the_chosen_version_first():
    """
    下拉列表的排序要与选取规则一致，否则查询面板默认项与弹窗用的不是同一份。

    两个入口曾各按各的顺序取第 0 项，这条钉住它们一致。
    """
    from tools.birdname_versions import order_versions, pick_version

    ordered = order_versions(_REAL)
    assert ordered[0]["version_id"] == pick_version(_REAL)
    assert len(ordered) == len(_REAL)


def test_the_shipped_database_resolves_to_ioc_15_1():
    """
    端到端：真实的 ioc/birdname.db 必须解析到 IOC 15.1。

    上面几条用的是硬编码夹具；这条读真库，防止库本身变动后规则悄悄失配。
    """
    import sqlite3
    from tools.birdname_versions import pick_version_from_db

    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "ioc", "birdname.db")
    version_id = pick_version_from_db(db)

    conn = sqlite3.connect(db)
    try:
        name = conn.execute("SELECT version_name FROM versions WHERE version_id = ?",
                            (version_id,)).fetchone()[0]
    finally:
        conn.close()
    assert name == "IOC 15.1", f"选中的是 {name}"


def test_missing_database_yields_none():
    """库文件不存在时返回 None 而不是抛异常。"""
    from tools.birdname_versions import pick_version_from_db

    assert pick_version_from_db("/nonexistent/birdname.db") is None
