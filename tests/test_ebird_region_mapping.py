# -*- coding: utf-8 -*-
"""
eBird 物种映射单测 / Unit tests for eBird species mapping.

用临时 SQLite 模拟 bird_reference.sqlite 的四张表，覆盖五个映射步骤与卡口。
Builds a temporary stand-in for bird_reference.sqlite covering all five
mapping steps and the gates.
"""
import sqlite3

import pytest

from scripts_dev.ebird_region_mapping import (
    build_species_mapping,
    classes_for_region,
    epithet_stem,
    find_overloaded_classes,
    load_model_index,
    normalize_to_species,
    parse_gbif_v2_match,
)

TAXONOMY = {
    "brotho1": {"speciesCode": "brotho1", "sciName": "Acanthiza pusilla", "category": "species", "familySciName": "Acanthizidae"},
    "grucra1": {"speciesCode": "grucra1", "sciName": "Grus virgo", "category": "species", "familySciName": "Gruidae"},
    "auspra1": {"speciesCode": "auspra1", "sciName": "Glareola isabella", "category": "species", "familySciName": "Glareolidae"},
    "lirplo": {"speciesCode": "lirplo", "sciName": "Thinornis dubius", "category": "species", "familySciName": "Charadriidae"},
    "kilplo": {"speciesCode": "kilplo", "sciName": "Charadrius vociferus", "category": "species", "familySciName": "Charadriidae"},
    "nomodel1": {"speciesCode": "nomodel1", "sciName": "Nullus absentis", "category": "species", "familySciName": "Nullidae"},
    "brotho1x": {"speciesCode": "brotho1x", "sciName": "Acanthiza pusilla diemenensis", "category": "issf", "reportAs": "brotho1"},
    "hybr1": {"speciesCode": "hybr1", "sciName": "A x B", "category": "hybrid"},
    "thorn1": {"speciesCode": "thorn1", "sciName": "Acanthiza sp.", "category": "spuh"},
}


@pytest.fixture
def ref_db(tmp_path):
    """
    构造最小参考库 / Build a minimal reference database.

    类别 1 褐刺嘴莺（cornell 命中）、2 蓑羽鹤（学名命中）、3 澳洲燕鸻（GBIF 命中）、
    4 金眶鸻（种加词命中，模型仍用旧属名 Charadrius）、5 双领鸻（学名命中，
    用来提供 Charadrius → Charadriidae 的属科关系）。
    """
    p = tmp_path / "ref.sqlite"
    db = sqlite3.connect(str(p))
    db.executescript(
        """
        CREATE TABLE avilist_map (model_class_id INTEGER, cornell_code TEXT, family TEXT);
        CREATE TABLE BirdCountInfo (model_class_id INTEGER, scientific_name TEXT);
        CREATE TABLE gbif_rarity_100 (model_class_id INTEGER, specieskey INTEGER);
        CREATE TABLE bird_ioc (genus TEXT, family_scientific TEXT);
        """
    )
    db.executemany("INSERT INTO avilist_map VALUES (?,?,?)", [(1, "brotho1", "Acanthizidae"), (None, "lirplo", None)])
    db.executemany(
        "INSERT INTO BirdCountInfo VALUES (?,?)",
        [(1, "Acanthiza pusilla"), (2, "Grus virgo"), (3, "Stiltia isabella"),
         (4, "Charadrius dubius"), (5, "Charadrius vociferus")],
    )
    db.executemany("INSERT INTO gbif_rarity_100 VALUES (?,?)", [(3, 2480749)])
    db.commit()
    db.close()
    return str(p)


def fake_gbif(sci_name):
    """只有澳洲燕鸻能解析到 accepted key / Only the pratincole resolves."""
    return 2480749 if sci_name == "Glareola isabella" else None


def test_normalize_rolls_up_and_excludes():
    """亚种归并到物种，杂交与属级记录剔除 / Roll issf up; drop hybrid and spuh."""
    out = normalize_to_species(["brotho1x", "hybr1", "thorn1", "grucra1", "unknown"], TAXONOMY)
    assert out == {"brotho1", "grucra1"}


@pytest.mark.parametrize(
    "a,b",
    [("Trichoglossus concinnus", "Glossopsitta concinna"),
     ("Emblema modestum", "Aidemosyne modesta"),
     ("Idiopsar speculifer", "Idiopsar speculiferus")],
)
def test_epithet_stem_ignores_gender_endings(a, b):
    """词尾阴阳性不同也视为同一词干 / Gender endings map to the same stem."""
    assert epithet_stem(a) == epithet_stem(b)


def test_parse_gbif_synonym_returns_accepted_key():
    """同物异名返回 accepted key / A synonym yields the accepted key."""
    payload = {
        "usage": {"key": "111", "rank": "SPECIES", "status": "SYNONYM"},
        "acceptedUsage": {"key": "2480749"},
        "diagnostics": {"matchType": "EXACT"},
    }
    assert parse_gbif_v2_match(payload) == 2480749


def test_parse_gbif_rejects_higherrank():
    """只匹配到属时不可用 / A genus-level match is rejected."""
    payload = {"usage": {"key": "2480339", "rank": "GENUS"}, "diagnostics": {"matchType": "HIGHERRANK"}}
    assert parse_gbif_v2_match(payload) is None


def test_mapping_steps(ref_db):
    """五个步骤各自命中 / Each of the five steps hits."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    assert m.classes["brotho1"] == {1} and m.steps["brotho1"] == "cornell"
    assert m.classes["grucra1"] == {2} and m.steps["grucra1"] == "sci"
    assert m.classes["auspra1"] == {3} and m.steps["auspra1"] == "gbif"
    assert m.classes["lirplo"] == {4} and m.steps["lirplo"] == "epithet"
    assert m.steps["kilplo"] == "sci"
    assert m.unmapped == ["nomodel1"]


def test_override_wins(ref_db):
    """人工覆盖优先于自动结果 / Overrides beat automatic steps."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={"grucra1": [5]})
    assert m.classes["grucra1"] == {5} and m.steps["grucra1"] == "override"


def test_classes_for_region(ref_db):
    """区域清单合并为类别集合，未映射的忽略 / Region lists union to classes."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    assert classes_for_region(["brotho1", "lirplo", "nomodel1"], m) == {1, 4}


def test_overloaded_classes(ref_db):
    """一个类别被映射到超过 3 个物种要报出，白名单除外 / Flag >3 species per class."""
    index = load_model_index(ref_db)
    m = build_species_mapping(TAXONOMY, index, fake_gbif, overrides={})
    for i in range(4):
        m.classes[f"x{i}"] = {9}
    assert set(find_overloaded_classes(m, allow=set())) == {9}
    assert find_overloaded_classes(m, allow={9}) == {}
