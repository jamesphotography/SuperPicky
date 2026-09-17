# -*- coding: utf-8 -*-
"""
eBird 区域库构建脚本单测（不联网）/ Offline unit tests for the region DB builder.
"""
import json
import sqlite3

from birdid.region_geometry import pack_ring, quantize_ring
from scripts_dev.build_ebird_regions import (
    CachedFetcher,
    check_sentinels,
    country_rows,
    load_overrides,
    missing_boundary_errors,
    publish_database,
    write_database,
)
from scripts_dev.ebird_region_boundaries import RingRow


def square_row(region, level, lat0, lon0, size=1.0):
    """构造一个正方形边界行 / Build a square boundary row."""
    pts = quantize_ring([[lon0, lat0], [lon0 + size, lat0], [lon0 + size, lat0 + size], [lon0, lat0 + size]])
    return RingRow(region, level, 0, lat0, lat0 + size, lon0, lon0 + size, 0, pack_ring(pts))


def test_write_database_roundtrip(tmp_path):
    """写入后四张表内容可读回 / All four tables read back."""
    path = tmp_path / "out.db"
    write_database(
        str(path),
        regions=[("AU", None, "Australia", "澳大利亚", 2), ("AU-NSW", "AU", "New South Wales", "新南威尔士州", 1)],
        region_species={"AU": {1, 2}, "AU-NSW": {1}},
        rings=[square_row("AU", 0, -40, 110, 40), square_row("AU-NSW", 1, -37, 141, 8)],
        meta={"fetched_at": "2026-09-16"},
    )
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT code, parent FROM regions ORDER BY code").fetchall() == [("AU", None), ("AU-NSW", "AU")]
    assert db.execute("SELECT class_id FROM region_species WHERE region='AU' ORDER BY 1").fetchall() == [(1,), (2,)]
    assert db.execute("SELECT COUNT(*) FROM boundaries").fetchone()[0] == 2
    assert db.execute("SELECT value FROM meta WHERE key='fetched_at'").fetchone()[0] == "2026-09-16"
    db.close()


def test_write_database_replaces_existing(tmp_path):
    """已有文件被整体替换而不是追加 / An existing file is replaced, not appended."""
    path = tmp_path / "out.db"
    for count in (1, 1):
        write_database(str(path), [("IS", None, "Iceland", "冰岛", count)], {"IS": {7}}, [], {})
    db = sqlite3.connect(str(path))
    assert db.execute("SELECT COUNT(*) FROM regions").fetchone()[0] == 1
    db.close()


def test_check_sentinels():
    """缺失的哨兵逐条报出 / Each missing sentinel is reported."""
    errors = check_sentinels(
        region_species={"AU-NSW": {6065}, "CN-11": set()},
        class_by_sci={"Acanthiza pusilla": {6065}, "Grus virgo": {1418}},
        sentinels=[("AU-NSW", "Acanthiza pusilla"), ("CN-11", "Grus virgo"), ("CN-11", "Nullus absentis")],
    )
    assert len(errors) == 2
    assert "Grus virgo" in errors[0] and "Nullus absentis" in errors[1]


def test_country_rows_reports_missing_chinese_names():
    """未收录中文名的国家要报出 / Countries without a Chinese name are reported."""
    rows, missing = country_rows(
        [{"code": "AU", "name": "Australia"}, {"code": "QQ", "name": "Q Land"}],
        {"AU": {1, 2}, "QQ": {3}},
    )
    assert rows[0] == ("AU", None, "Australia", "澳大利亚", 2)
    assert missing == ["QQ"]


def test_fetcher_uses_cache_without_network(tmp_path):
    """缓存命中时不调用网络 / A cache hit never touches the network."""
    (tmp_path / "a.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("不应联网 / must not hit the network")

    fetcher = CachedFetcher(str(tmp_path), headers={}, opener=boom)
    assert fetcher.get_json("https://example.invalid/a", "a.json") == {"x": 1}


def test_fetcher_writes_cache(tmp_path):
    """网络结果写入缓存 / Network results are cached."""
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps([1, 2]).encode("utf-8")

    fetcher = CachedFetcher(str(tmp_path), headers={}, opener=lambda req, timeout: Resp())
    assert fetcher.get_json("https://example.invalid/b", "sub/b.json") == [1, 2]
    assert json.loads((tmp_path / "sub" / "b.json").read_text(encoding="utf-8")) == [1, 2]


def test_load_overrides(tmp_path):
    """覆盖文件的两个字段 / Both override fields load."""
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"map": {"lirplo": [1513]}, "allow_multi": [42]}), encoding="utf-8")
    mapping, allow = load_overrides(str(p))
    assert mapping == {"lirplo": [1513]} and allow == {42}


def test_publish_database_within_limit(tmp_path):
    """大小在限制内时发布并删除临时文件 / Publishes and removes staging when within limit."""
    staging = tmp_path / "staging.db"
    output = tmp_path / "final.db"
    write_database(str(staging), [("AU", None, "Australia", "澳大利亚", 1)], {"AU": {1}}, [], {})
    assert staging.exists()
    assert not output.exists()

    success = publish_database(str(staging), str(output), 100 * 1024 * 1024)  # 100 MB limit
    assert success
    assert output.exists()
    assert not staging.exists()  # Staging file should be gone


def test_publish_database_oversized(tmp_path):
    """大小超限时删除临时文件、保留现有输出 / Deletes staging and preserves existing output when oversized."""
    staging = tmp_path / "staging.db"
    output = tmp_path / "final.db"

    # Create an existing output file
    write_database(str(output), [("IS", None, "Iceland", "冰岛", 1)], {"IS": {1}}, [], {})
    original_content = output.read_bytes()

    # Create an oversized staging file
    write_database(str(staging), [("AU", None, "Australia", "澳大利亚", 1)], {"AU": {1}}, [], {})

    # Attempt to publish with a very small limit
    success = publish_database(str(staging), str(output), 1)  # 1 byte limit (too small)
    assert not success
    assert not staging.exists()  # Staging should be cleaned up
    assert output.exists()  # Output should still exist
    assert output.read_bytes() == original_content  # Output should be unchanged


def test_missing_boundary_errors_flags_regions_with_species_but_no_rings():
    """有物种但无环的区域报错；无物种或在允许表中的不报 / Species without rings is a gate error."""
    regions = [
        ("FR", None, "France", "法国", 700),
        ("GF", None, "French Guiana", "法属圭亚那", 707),
        ("XX", None, "High Seas", "公海", 372),
        ("ZZ", None, "Empty", "空", 0),
        ("AU-NSW", "AU", "New South Wales", "新南威尔士州", 5),
    ]
    rings = [square_row("FR", 0, 42, -4), square_row("AU-NSW", 1, -37, 141)]
    errors = missing_boundary_errors(regions, rings, allow={"XX": "High Seas"})
    assert errors == ["no boundary ring for region with species: GF"]
    assert all(msg.isascii() for msg in errors)


def test_unmappable_allow_list_is_minimal():
    """允许表只含已核实无法制图的代码 / The allow-list holds only verified unmappable codes."""
    from scripts_dev.build_ebird_regions import UNMAPPABLE_REGIONS

    assert set(UNMAPPABLE_REGIONS) == {"XX", "CS"}
    assert missing_boundary_errors([("XX", None, "High Seas", "公海", 372)], []) == []
