# -*- coding: utf-8 -*-
"""
scripts_dev/sync_from_master.py 的测试：临时目录造主库与各目标文件，不碰真实数据。
Tests for the master-database sync on throwaway files.
"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from scripts_dev import sync_from_master as sync  # noqa: E402


def _master(tmp_path):
    """
    最小主库：斑胸草雀（拆分种保留整种名）、田鹨（中国鸟用 ChinaBirds 名）。
    Minimal master: a split species kept lumped and a China bird.
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
        "                           (2,'Anthus richardi','Richard''s Pipit','田鹨','大花鷚');"
        "INSERT INTO xref VALUES (1,'ebird_code','zebfin2'),(2,'ebird_code','ricpip1');"
        "INSERT INTO name_alias VALUES ('巽他斑胸草雀',1,'rename');"
        "INSERT INTO pinyin VALUES ('斑胸草雀','bān xiōng cǎo què','','','bxcq'),"
        "                          ('田鹨','tián liù','','','tl');"
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
        # 中文名已被主库占用：不补，避免两个「斑胸草雀」/ name taken: skipped
        " ('斑胸草雀','Australian Zebra Finch','Taeniopygia castanotis',8,'梅花雀科'),"
        # 主库没有：补漏 / not in master: gap-filled
        " ('塔岛鹰鸮','Tasmanian Boobook','Ninox leucopsis',8,'鸱鸮科');")
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
    """三处都写对：识鸟库改名改码、名录版本主库种 + 补漏、拼音补齐；有备份。"""
    root, master = _master(tmp_path)
    ref, names, py = _targets(tmp_path)
    assert _run(tmp_path, root, master, ref, names, py) == 0

    con = sqlite3.connect(ref)
    assert con.execute("SELECT chinese_simplified, chinese_traditional, ebird_code, short_description_zh "
                       "FROM BirdCountInfo WHERE id=2").fetchone() == ("田鹨", "大花鷚", "ricpip1", "简介")
    assert con.execute("SELECT ebird_code FROM BirdCountInfo WHERE id=1").fetchone()[0] == "zebfin2"
    con.close()

    assert _catalog(names) == [
        ("塔岛鹰鸮", None, None, 0, None, "鸱鸮科"),
        ("斑胸草雀", "ban xiong cao que", "BXCQ", 1, "巽他斑胸草雀", "梅花雀科"),
        ("田鹨", "tian liu", "TL", 1, "理氏鹨", "鹡鸰科"),
    ]
    table = json.loads(py.read_text(encoding="utf-8"))
    assert table["田鹨"] == "tián liù" and table["家燕"] == "jiā yàn"
    assert len(os.listdir(tmp_path / "bak")) == 3


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
