# -*- coding: utf-8 -*-
"""
scripts_dev/sync_from_master.py 的测试：临时目录造主库与各目标文件，不碰真实数据。
Tests for the master-database sync on throwaway files.
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from scripts_dev import sync_from_master as sync  # noqa: E402


def _master(tmp_path):
    """
    最小主库：斑胸草雀（拆分种保留整种名）、田鹨（中国鸟用 ChinaBirds 名）两个模型种，
    加塔岛鹰鸮——模型未收录种（xref 没有 superpicky_model，识鸟参考库里也没有）。
    Minimal master: two model species plus one species outside the model.
    """
    root = tmp_path / "bird-names"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "check.py").write_text("def run_checks(con):\n    return [], []\n", encoding="utf-8")
    db = root / "bird_master.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE species (id INTEGER PRIMARY KEY, scientific_name TEXT, english_name TEXT,"
        " zh_simplified TEXT, zh_traditional TEXT);"
        "CREATE TABLE xref (species_id INTEGER, system TEXT, code TEXT);"
        "CREATE TABLE name_alias (alias TEXT, species_id INTEGER, source TEXT);"
        "CREATE TABLE pinyin (chinese_name TEXT, toned TEXT, plain TEXT, plain_u TEXT, initials TEXT);"
        "CREATE TABLE meta (key TEXT, value TEXT);"
        "INSERT INTO species VALUES (1,'Taeniopygia guttata','Zebra Finch','斑胸草雀','斑胸草雀'),"
        "                           (2,'Anthus richardi','Richard''s Pipit','田鹨','大花鷚'),"
        "                           (3,'Ninox leucopsis','Tasmanian Boobook','塔岛鹰鸮','塔島鷹鴞');"
        "INSERT INTO xref VALUES (1,'ebird_code','zebfin2'),(2,'ebird_code','ricpip1'),"
        "                        (1,'superpicky_model','100'),(2,'superpicky_model','200'),"
        "                        (2,'ibp_alpha4','RIPI');"
        "INSERT INTO name_alias VALUES ('巽他斑胸草雀',1,'rename');"
        "INSERT INTO pinyin VALUES ('斑胸草雀','bān xiōng cǎo què','','','bxcq'),"
        "                          ('田鹨','tián liù','','','tl'),"
        "                          ('塔岛鹰鸮','tǎ dǎo yīng xiāo','','','tdyx');"
        "INSERT INTO meta VALUES ('names_version','v1');")
    con.commit()
    con.close()
    return root, db


def _targets(tmp_path):
    """
    造 bird_reference.sqlite（旧名）、birdname.db（IOC 15.1）与 pinyin_toned.json。
    Build stale targets: reference DB, IOC catalog and pinyin JSON.
    """
    ref = tmp_path / "bird_reference.sqlite"
    con = sqlite3.connect(ref)
    con.executescript(
        "CREATE TABLE BirdCountInfo (id INTEGER PRIMARY KEY, scientific_name TEXT, chinese_simplified TEXT,"
        " chinese_traditional TEXT, ebird_code TEXT, short_description_zh TEXT);"
        "INSERT INTO BirdCountInfo VALUES (1,'Taeniopygia guttata','斑胸草雀','斑胸草雀','ostric2','简介'),"
        "                                 (2,'Anthus richardi','理氏鹨','理氏鷚',NULL,'简介');")
    con.commit()
    con.close()

    names = tmp_path / "birdname.db"
    con = sqlite3.connect(names)
    con.executescript(
        "CREATE TABLE versions (version_id INTEGER PRIMARY KEY AUTOINCREMENT, version_name TEXT NOT NULL UNIQUE,"
        " is_active BOOLEAN DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE birds (bird_id INTEGER PRIMARY KEY AUTOINCREMENT, chinese_name TEXT, english_name TEXT,"
        " latin_name TEXT, pinyin_name TEXT, abbreviation TEXT, version_id INTEGER NOT NULL, traditional_name TEXT,"
        " order_en TEXT, family_en TEXT, genus_en TEXT, order_zh TEXT, family_zh TEXT, genus_zh TEXT);"
        "INSERT INTO versions (version_id, version_name) VALUES (8, 'IOC 15.1');"
        # 学名在主库：由主库代表，不补 / sci in master: represented by the master
        "INSERT INTO birds (chinese_name,english_name,latin_name,version_id,family_zh) VALUES"
        " ('巽他斑胸草雀','Sunda Zebra Finch','Taeniopygia guttata',8,'梅花雀科'),"
        " ('理氏鹨','Richard''s Pipit','Anthus richardi',8,'鹡鸰科'),"
        # 主库没有的 IOC 行：名录不再自行补（模型未收录种由主库统一提供）
        # IOC-only rows: no longer gap-filled here (the master provides them)
        " ('斑胸草雀','Australian Zebra Finch','Taeniopygia castanotis',8,'梅花雀科'),"
        " ('塔岛鹰鸮','Tasmanian Boobook','Ninox leucopsis',8,'鸱鸮科'),"
        " ('新几内亚鹰鸮','Papuan Boobook','Ninox theomacha',8,'鸱鸮科');")
    con.commit()
    con.close()

    py = tmp_path / "pinyin_toned.json"
    py.write_text(json.dumps({"家燕": "jiā yàn"}, ensure_ascii=False), encoding="utf-8")
    return ref, names, py


def _run(tmp_path, root, master, ref, names, py, *extra):
    return sync.main(["--master-db", str(master), "--master-root", str(root), "--ref-db", str(ref),
                      "--name-db", str(names), "--pinyin-json", str(py),
                      "--backup-dir", str(tmp_path / "bak"), *extra])


def _catalog(names):
    con = sqlite3.connect(names)
    try:
        return con.execute(
            "SELECT b.chinese_name, b.pinyin_name, b.abbreviation, b.in_model, b.search_aliases, b.family_zh "
            "FROM birds b JOIN versions v USING (version_id) WHERE v.version_name = ? ORDER BY b.chinese_name",
            (sync.MASTER_VERSION_NAME,)).fetchall()
    finally:
        con.close()


def test_plain_syllables_matches_catalog_format():
    """带调拼音转成 birdname.db 的格式：去声调、空格分隔、ü 写 v。"""
    assert sync.plain_syllables("lǜ chì yàn") == "lv chi yan"
    assert sync.plain_syllables("bān xiōng cǎo què") == "ban xiong cao que"


def test_sync_writes_all_three_targets(tmp_path):
    """三处都写对：识鸟库改名改码、名录 = 主库全部种（模型未收录的标 0）、拼音补齐；有备份。"""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    assert _run(tmp_path, root, master, ref, names, py) == 0

    con = sqlite3.connect(ref)
    assert con.execute("SELECT chinese_simplified, chinese_traditional, ebird_code, short_description_zh "
                       "FROM BirdCountInfo WHERE id=2").fetchone() == ("田鹨", "大花鷚", "ricpip1", "简介")
    assert con.execute("SELECT ebird_code FROM BirdCountInfo WHERE id=1").fetchone()[0] == "zebfin2"
    con.close()

    assert _catalog(names) == [
        ("塔岛鹰鸮", "ta dao ying xiao", "TDYX", 0, None, "鸱鸮科"),
        # IOC 15.1 英文名与显示名不同 → 作为搜索别名（issue #122）
        ("斑胸草雀", "ban xiong cao que", "BXCQ", 1, "巽他斑胸草雀|Sunda Zebra Finch", "梅花雀科"),
        ("田鹨", "tian liu", "TL", 1, "理氏鹨", "鹡鸰科"),
    ]
    table = json.loads(py.read_text(encoding="utf-8"))
    assert table["田鹨"] == "tián liù" and table["家燕"] == "jiā yàn"
    assert len(os.listdir(tmp_path / "bak")) == 3


def test_catalog_carries_species_codes(tmp_path):
    """名录带上 eBird 代码与 IBP 4 位代码（issue #119），没有的为空 / Codes are synced."""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    assert _run(tmp_path, root, master, ref, names, py) == 0

    con = sqlite3.connect(names)
    try:
        rows = con.execute(
            "SELECT b.chinese_name, b.ebird_code, b.alpha4 FROM birds b JOIN versions v USING (version_id) "
            "WHERE v.version_name = ? ORDER BY b.chinese_name", (sync.MASTER_VERSION_NAME,)).fetchall()
    finally:
        con.close()
    assert rows == [("塔岛鹰鸮", None, None), ("斑胸草雀", "zebfin2", None), ("田鹨", "ricpip1", "RIPI")]


def test_rerun_keeps_version_id_and_changes_nothing(tmp_path):
    """重跑：版本 id 不变、内容不重复、识鸟库零改动。"""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    _run(tmp_path, root, master, ref, names, py)
    con = sqlite3.connect(names)
    vid = con.execute("SELECT version_id FROM versions WHERE version_name = ?",
                      (sync.MASTER_VERSION_NAME,)).fetchone()[0]
    con.close()

    _run(tmp_path, root, master, ref, names, py)
    con = sqlite3.connect(names)
    assert con.execute("SELECT version_id FROM versions WHERE version_name = ?",
                       (sync.MASTER_VERSION_NAME,)).fetchone()[0] == vid
    con.close()
    assert len(_catalog(names)) == 3
    assert sync.plan_reference(str(ref), sync.load_master(str(master))["species"])[0] == []


def test_dry_run_and_id_drift_write_nothing(tmp_path):
    """演练不写；同一 id 学名对不上时中止且不写。"""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    before = (ref.read_bytes(), names.read_bytes(), py.read_bytes())
    assert _run(tmp_path, root, master, ref, names, py, "--dry-run") == 0
    assert (ref.read_bytes(), names.read_bytes(), py.read_bytes()) == before

    con = sqlite3.connect(ref)
    con.execute("UPDATE BirdCountInfo SET scientific_name = 'Anthus novaeseelandiae' WHERE id = 2")
    con.commit()
    con.close()
    before = (ref.read_bytes(), names.read_bytes())
    assert _run(tmp_path, root, master, ref, names, py) == 1
    assert (ref.read_bytes(), names.read_bytes()) == before


def test_unchanged_run_writes_nothing(tmp_path):
    """主库没变时重跑：三个文件逐字节不变，也不产生备份（避免 git 里的假改动）。
    A no-op rerun leaves every file byte-identical and makes no backup."""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    _run(tmp_path, root, master, ref, names, py)
    before = (ref.read_bytes(), names.read_bytes(), py.read_bytes())
    backups = len(os.listdir(tmp_path / "bak"))

    assert _run(tmp_path, root, master, ref, names, py) == 0
    assert (ref.read_bytes(), names.read_bytes(), py.read_bytes()) == before
    assert len(os.listdir(tmp_path / "bak")) == backups



def test_model_species_missing_from_reference_is_fatal(tmp_path):
    """
    模型未收录种不在识鸟参考库属正常；模型种缺失仍要中止且不写。
    Non-model species may be absent; a missing model species still aborts.
    """
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    _, fatal, ref_ids = sync.plan_reference(str(ref), sync.load_master(str(master))["species"])
    assert fatal == [] and ref_ids == {1, 2}

    con = sqlite3.connect(ref)
    con.execute("DELETE FROM BirdCountInfo WHERE id = 2")
    con.commit()
    con.close()
    before = (ref.read_bytes(), names.read_bytes())
    assert _run(tmp_path, root, master, ref, names, py) == 1
    assert (ref.read_bytes(), names.read_bytes()) == before


def test_catalog_aliases_include_ebird_english_names(tmp_path):
    """
    主库 xref.ebird_en 进搜索别名（issue #122）：与显示名只差大小写的不加；
    美式拼写（Gray）即使与英式（Grey）等价也要加，因为搜索是子串匹配。

    eBird English names become search aliases; case-only differences are skipped,
    US spellings are kept because search is substring-based.
    """
    root, master = _master(tmp_path)
    con = sqlite3.connect(master)
    con.executescript(
        "INSERT INTO xref VALUES (1,'ebird_en','zebra finch'),"          # 只差大小写 / case only
        "                        (2,'ebird_en','Richard''s Pipit (Gray)'),"
        "                        (3,'ebird_en','Tasmanian Owl');")
    con.commit()
    con.close()
    ref, names, py = _targets(tmp_path)
    assert _run(tmp_path, root, master, ref, names, py) == 0

    aliases = {row[0]: row[4] for row in _catalog(names)}
    assert aliases == {
        "塔岛鹰鸮": "Tasmanian Owl",
        "斑胸草雀": "巽他斑胸草雀|Sunda Zebra Finch",
        "田鹨": "理氏鹨|Richard's Pipit (Gray)",
    }


def _add_conservation(master, rows):
    """
    给最小主库补上 species_conservation 表（国家重点保护等级等）。
    Add the master's species_conservation table with the given rows.
    """
    con = sqlite3.connect(master)
    con.executescript(
        "CREATE TABLE species_conservation (species_id INTEGER PRIMARY KEY, iucn_category TEXT,"
        " china_category TEXT, cites_appendix TEXT);")
    con.executemany("INSERT INTO species_conservation VALUES (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _china_rows(ref):
    con = sqlite3.connect(ref)
    try:
        return con.execute(
            f"SELECT name, scientific_name, level FROM {sync.CHINA_TABLE} ORDER BY name").fetchall()
    finally:
        con.close()


def test_china_protection_written_under_every_name(tmp_path):
    """
    国家保护等级写进参考库：每个受保护种按学名、英文名、简繁中文名各一行；
    非保护种（含只有 IUCN 的）不写。重跑不再改动参考库。
    Each protected species gets one row per name; unlisted ones get none;
    a rerun leaves the reference DB alone.
    """
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    _add_conservation(master, [(2, "LC", "国家二级", None),     # 田鹨（测试用）
                               (3, "VU", None, "附录II")])      # 只有 IUCN / CITES
    assert _run(tmp_path, root, master, ref, names, py) == 0
    assert _china_rows(ref) == [
        ("Anthus richardi", "Anthus richardi", 2),
        ("Richard's Pipit", "Anthus richardi", 2),
        ("大花鷚", "Anthus richardi", 2),
        ("田鹨", "Anthus richardi", 2),
    ]

    before = os.path.getmtime(ref)
    backups = len(os.listdir(tmp_path / "bak"))
    assert _run(tmp_path, root, master, ref, names, py) == 0
    assert os.path.getmtime(ref) == before
    assert len(os.listdir(tmp_path / "bak")) == backups


def test_china_protection_level_change_rewrites_table(tmp_path):
    """主库等级变了（二级→一级）就整表重写 / A changed class rewrites the table."""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    _add_conservation(master, [(2, "LC", "国家二级", None)])
    assert _run(tmp_path, root, master, ref, names, py) == 0

    con = sqlite3.connect(master)
    con.execute("UPDATE species_conservation SET china_category = '国家一级' WHERE species_id = 2")
    con.commit()
    con.close()
    assert _run(tmp_path, root, master, ref, names, py) == 0
    assert {level for _, _, level in _china_rows(ref)} == {1}


def test_master_without_conservation_creates_no_table(tmp_path):
    """
    旧主库没有 species_conservation：不报错，也不为一张空表去改参考库。
    An older master without the table neither fails nor adds an empty table.
    """
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    assert sync.load_master(str(master))["china"] == {}
    rows, ambiguous, same = sync.plan_china_protection(str(ref), sync.load_master(str(master)))
    assert (rows, ambiguous, same) == ([], [], True)


def test_ambiguous_name_is_dropped(tmp_path):
    """
    同一个名字对应两个等级不同的鸟种时，这个名字不写（宁可不显示也不显示错）。
    A name shared by species of different classes is dropped.
    """
    root, master = _master(tmp_path)
    ref, _, _ = _targets(tmp_path)
    data = sync.load_master(str(master))
    data["species"][1]["zh"] = "田鹨"          # 人为制造同名 / force a shared name
    data["china"] = {1: 1, 2: 2}
    rows, ambiguous, _ = sync.plan_china_protection(str(ref), data)
    assert ambiguous == ["田鹨"]
    assert all(name != "田鹨" for name, _, _ in rows)
