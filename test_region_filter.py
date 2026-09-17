# -*- coding: utf-8 -*-
"""
eBird 区域过滤器单测 / Unit tests for the eBird region filter.
"""
import pytest

from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL, RegionFilter
from scripts_dev.build_ebird_regions import write_database


@pytest.fixture
def region_filter(tmp_path):
    """
    AU 国家 {1,2,3}、AU-NSW {1,2}、AU-QLD 空清单、IS {9}、JP 空国家。
    """
    path = tmp_path / "regions.db"
    write_database(
        str(path),
        regions=[
            ("AU", None, "Australia", "澳大利亚", 3),
            ("AU-NSW", "AU", "New South Wales", "新南威尔士州", 2),
            ("AU-QLD", "AU", "Queensland", "昆士兰州", 0),
            ("IS", None, "Iceland", "冰岛", 1),
            ("JP", None, "Japan", "日本", 0),
        ],
        region_species={"AU": {1, 2, 3}, "AU-NSW": {1, 2}, "IS": {9}},
        rings=[],
        meta={"fetched_at": "2026-09-16"},
    )
    f = RegionFilter(str(path))
    yield f
    f.close()


def labels(items):
    """只取 (层标签, 区域) / Keep only (tier, region)."""
    return [(tier, region) for _, tier, region in items]


def test_gps_subnational_then_country(region_filter):
    """有 GPS：省州 → 国家 → 不过滤 / GPS: subnational, country, none."""
    out = list(region_filter.iter_candidates("AU", "AU-NSW", None, None))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]
    assert out[0][0] == {1, 2} and out[1][0] == {1, 2, 3} and out[2][0] is None


def test_gps_wins_over_manual(region_filter):
    """GPS 与手选同时存在时以 GPS 为准 / GPS beats the manual selection."""
    out = list(region_filter.iter_candidates("IS", None, "AU", "AU-NSW"))
    assert labels(out) == [(TIER_COUNTRY, "IS"), (TIER_NONE, None)]


def test_no_gps_uses_manual_subnational(region_filter):
    """无 GPS 用手选省州（老配置 AU-NSW 回归，spec §2.5）/ Legacy AU-NSW works again."""
    out = list(region_filter.iter_candidates(None, None, "AU", "AU-NSW"))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_manual_subnational_must_match_country(region_filter):
    """省州与国家不一致视为整个国家 / A mismatched subnational falls back to the country."""
    out = list(region_filter.iter_candidates(None, None, "IS", "AU-NSW"))
    assert labels(out) == [(TIER_COUNTRY, "IS"), (TIER_NONE, None)]


def test_manual_subnational_alone_implies_country(region_filter):
    """只给省州（CLI --region AU-NSW、迁移后国家为空）：省州 → 国家 → 不过滤 /
    A subnational alone implies its country."""
    out = list(region_filter.iter_candidates(None, None, None, "AU-NSW"))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]
    out = list(region_filter.iter_candidates(None, None, "GLOBAL", "AU-NSW"))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_gps_subnational_alone_implies_country(region_filter):
    """GPS 链只有省州时同样推出国家，并优先于手选 / GPS subnational alone implies its country too."""
    out = list(region_filter.iter_candidates(None, "AU-NSW", "IS", None))
    assert labels(out) == [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_codes_are_case_insensitive(region_filter):
    """小写与带空白的代码等同于大写 / Lowercase and padded codes behave like upper case."""
    expected = [(TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)]
    assert labels(region_filter.iter_candidates(None, None, "au", "au-nsw")) == expected
    assert labels(region_filter.iter_candidates(None, None, None, " au-nsw ")) == expected
    assert labels(region_filter.iter_candidates("au", "Au-Nsw", None, None)) == expected
    assert labels(region_filter.iter_candidates(None, None, "global", None)) == [(TIER_NONE, None)]


def test_unknown_subnational_with_valid_country_uses_country_tier(region_filter):
    """未知省州 + 有效国家：只走国家层 / An unknown subnational falls through to the country tier."""
    out = list(region_filter.iter_candidates(None, None, "AU", "AU-XYZ"))
    assert labels(out) == [(TIER_COUNTRY, "AU"), (TIER_NONE, None)]
    assert labels(region_filter.iter_candidates(None, None, None, "AU-XYZ")) == [(TIER_NONE, None)]


def test_unknown_or_global_manual_is_unfiltered(region_filter):
    """GLOBAL、未知代码都不过滤 / GLOBAL and unknown codes are unfiltered."""
    assert labels(region_filter.iter_candidates(None, None, "GLOBAL", None)) == [(TIER_NONE, None)]
    assert labels(region_filter.iter_candidates(None, None, "ZZ", None)) == [(TIER_NONE, None)]


def test_unknown_gps_country_falls_back_to_manual(region_filter):
    """GPS 国家不在库里时改用手选 / An unknown GPS country falls back to manual."""
    out = list(region_filter.iter_candidates("ZZ", None, "AU", None))
    assert labels(out) == [(TIER_COUNTRY, "AU"), (TIER_NONE, None)]


def test_empty_lists_are_skipped(region_filter):
    """空清单层被跳过，不会屏蔽全部类别 / Empty lists are skipped."""
    assert labels(region_filter.iter_candidates("AU", "AU-QLD", None, None)) == [
        (TIER_COUNTRY, "AU"), (TIER_NONE, None)
    ]
    assert labels(region_filter.iter_candidates("JP", None, None, None)) == [(TIER_NONE, None)]


def test_display_name(region_filter):
    """中英文名 / Display names."""
    assert region_filter.display_name("AU-NSW", english=False) == "新南威尔士州"
    assert region_filter.display_name("AU-NSW", english=True) == "New South Wales"
    assert region_filter.display_name("ZZ", english=True) == "ZZ"


def test_missing_db_is_unavailable(tmp_path):
    """库不存在时不可用且只产出不过滤 / Missing DB yields only the unfiltered tier."""
    f = RegionFilter(str(tmp_path / "missing.db"))
    assert f.is_available() is False
    assert labels(f.iter_candidates("AU", "AU-NSW", None, None)) == [(TIER_NONE, None)]


def test_describe_tier_covers_every_tier(monkeypatch, region_filter):
    """每层都有本地化文案 / Every tier has localized text."""
    import birdid.geo_filter as gf
    from birdid.geo_filter import describe_tier

    monkeypatch.setattr(gf, "get_geo_filter", lambda: region_filter)
    for tier, region in ((TIER_SUBNATIONAL, "AU-NSW"), (TIER_COUNTRY, "AU"), (TIER_NONE, None)):
        text = describe_tier({"tier": tier, "species_count": 42, "region_code": region})
        assert text and not text.startswith("birdid."), f"{tier}: {text}"
    assert describe_tier(None) and not describe_tier(None).startswith("birdid.")
