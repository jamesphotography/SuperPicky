"""
地理分层候选过滤器单测 / Unit tests for the layered geo candidate filter.

用临时 SQLite 构造已知分布，验证 cell_id 编码、分层顺序与逐层放宽行为，
不依赖真实的 geo_distribution.db。

Builds a temporary SQLite with a known distribution to verify cell-id encoding,
tier ordering, and progressive widening, without touching the real database.
"""
import sqlite3

import pytest

from birdid.geo_filter import (
    GeoFilter,
    TIER_CELL_ALL,
    TIER_CELL_STRONG,
    TIER_COUNTRY,
    TIER_NEIGHBORHOOD,
    TIER_NONE,
    cell_id_for,
)


def test_cell_id_encoding_origin():
    """(0,0) 落在 lat_bin=0, lon_bin=0 → (0+90)*360 + (0+180)"""
    assert cell_id_for(0.5, 0.5) == 90 * 360 + 180


def test_cell_id_encoding_negative():
    """悉尼 (-33.87, 151.21) → lat_bin=-34, lon_bin=151"""
    assert cell_id_for(-33.87, 151.21) == (-34 + 90) * 360 + (151 + 180)


def test_cell_id_clamps_poles_and_dateline():
    """lat=90 / lon=180 必须 clamp，不得越界"""
    assert cell_id_for(90.0, 180.0) == (89 + 90) * 360 + (179 + 180)
    assert 0 <= cell_id_for(-90.0, -180.0) <= 64799


@pytest.fixture
def db_path(tmp_path):
    """
    构造测试库：悉尼格 3 个种、邻格 1 个种、AU 国家级 5 个种。

    悉尼格的计数刻意用真实量级（10000/50/1，合计 10051）：`cumulative:0.999`
    的截断点在 10040，因此 n=1 的长尾种落在 L1 之外、L2 之内。若用几十条记录
    的玩具数据，0.999 会保留全部物种、L1 与 L2 相同，分层行为就测不出来。

    Sydney's counts deliberately use a realistic magnitude (10000/50/1, total
    10051): the `cumulative:0.999` cutoff lands at 10040, so the n=1 long-tail
    species falls outside L1 but inside L2. With toy counts of a few dozen
    records, 0.999 would keep every species and L1 would equal L2, making the
    tier behaviour untestable.
    """
    p = tmp_path / "geo.db"
    db = sqlite3.connect(str(p))
    db.executescript(
        """
        CREATE TABLE cell_species (cell_id INTEGER, class_id INTEGER, n INTEGER);
        CREATE TABLE country_species (country TEXT, class_id INTEGER, n INTEGER);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX idx_cell ON cell_species(cell_id);
        CREATE INDEX idx_country ON country_species(country);
        """
    )
    sydney = (-34 + 90) * 360 + (151 + 180)
    neighbour = (-33 + 90) * 360 + (151 + 180)
    db.executemany(
        "INSERT INTO cell_species VALUES (?,?,?)",
        [(sydney, 1, 10000), (sydney, 2, 50), (sydney, 3, 1), (neighbour, 4, 500)],
    )
    db.executemany(
        "INSERT INTO country_species VALUES (?,?,?)",
        [("AU", i, 10) for i in range(1, 6)],
    )
    db.execute("INSERT INTO meta VALUES ('tier1_threshold','cumulative:0.999')")
    db.commit()
    db.close()
    return str(p)


def test_tier_order_and_widening(db_path):
    """分层必须按 L1→L2→L3→L4→L5 顺序产出，且逐层变宽"""
    f = GeoFilter(db_path)
    tiers = list(f.iter_candidates(-33.87, 151.21, "AU"))
    labels = [t for _, t in tiers]
    assert labels == [
        TIER_CELL_STRONG, TIER_CELL_ALL, TIER_NEIGHBORHOOD, TIER_COUNTRY, TIER_NONE
    ]
    l1, l2, l3 = tiers[0][0], tiers[1][0], tiers[2][0]
    assert l1 <= l2 <= l3          # 逐层包含
    assert 3 in l2 and 3 not in l1  # n=1 的种被 L1 排除、L2 保留
    assert 4 in l3                  # 邻格的种只在 L3 出现
    assert tiers[4][0] is None      # L5 表示不过滤
    f.close()


def test_sparse_cell_keeps_every_species(tmp_path):
    """
    记录极少的网格：L1 应保留全部物种，不因阈值把稀疏格切空。

    这是与 test_tier_order_and_widening 互补的一面——cumulative 是相对阈值，
    在总记录数很小时本就该退化为「全保留」，稀疏地区不会被过度收窄。
    """
    p = tmp_path / "sparse.db"
    db = sqlite3.connect(str(p))
    db.executescript(
        """
        CREATE TABLE cell_species (cell_id INTEGER, class_id INTEGER, n INTEGER);
        CREATE TABLE country_species (country TEXT, class_id INTEGER, n INTEGER);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    iceland = (63 + 90) * 360 + (-20 + 180)
    db.executemany(
        "INSERT INTO cell_species VALUES (?,?,?)",
        [(iceland, 11, 40), (iceland, 12, 3), (iceland, 13, 1)],
    )
    db.execute("INSERT INTO meta VALUES ('tier1_threshold','cumulative:0.999')")
    db.commit()
    db.close()

    f = GeoFilter(str(p))
    first, label = next(iter(f.iter_candidates(63.4, -19.1, None)))
    assert label == TIER_CELL_STRONG
    assert first == {11, 12, 13}
    f.close()


def test_no_gps_starts_at_country(db_path):
    """无 GPS 时从 L4 起步"""
    f = GeoFilter(db_path)
    labels = [t for _, t in f.iter_candidates(None, None, "AU")]
    assert labels == [TIER_COUNTRY, TIER_NONE]
    f.close()


def test_no_gps_no_country_is_unfiltered(db_path):
    """无 GPS 且无国家 → 只有 L5"""
    f = GeoFilter(db_path)
    out = list(f.iter_candidates(None, None, None))
    assert out == [(None, TIER_NONE)]
    f.close()


def test_empty_cell_falls_through_to_neighbourhood(db_path):
    """空网格不产出空候选集，直接降到有内容的层"""
    f = GeoFilter(db_path)
    # 选一个 cell_species 里没有的格（南太平洋）
    tiers = list(f.iter_candidates(-20.5, -150.5, None))
    for cand, label in tiers:
        assert cand is None or len(cand) > 0, f"{label} 产出了空候选集"
    f.close()


def test_unavailable_db_yields_only_none(tmp_path):
    """库缺失时只产出 L5，不抛异常"""
    f = GeoFilter(str(tmp_path / "missing.db"))
    assert f.is_available() is False
    assert list(f.iter_candidates(-33.87, 151.21, "AU")) == [(None, TIER_NONE)]
    f.close()
