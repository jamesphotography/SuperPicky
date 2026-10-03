#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 bird-names 鸟名主库同步名字数据到 SuperPicky。
Sync name data from the bird-names master database into SuperPicky.

鸟名主库（JamesAPPS/bird-names/bird_master.sqlite）是 SuperPicky、慧眼、OZBirds 与
eBird 插件共用的唯一鸟名来源。改一只鸟的名字只改主库，再跑本脚本。

写三处，各自只动名字相关的数据：
1. birdid/data/bird_reference.sqlite：BirdCountInfo 的简繁中文名与 eBird 代码
   （按 id 对齐，先核对学名逐条一致）。识鸟结果、详情、卡片、LR 插件、CLI 都读它。
2. ioc/pinyin_toned.json：补齐 / 更新主库现用名的带声调读音（其余条目不动）。
3. ioc/birdname.db：写入一个名为「SuperPicky 名录」的版本，供改鸟种弹窗与鸟名查询使用：
   - 主库全部鸟种（in_model=1），名字与识鸟结果一致；
   - IOC 15.1 里主库没有的种（in_model=0，多为模型训练后才拆出的新种），用 IOC 名，
     界面标「识鸟模型未收录」。中文名或英文名已被主库占用的 IOC 行不补，避免重复；
   - search_aliases：主库别名 + 对应 IOC 中文名，搜旧名 / IOC 名也能找到现用名。

写库前跑主库校验（有错误即中止），三个文件都先备份到 ~/Library/Caches。可重复运行。

用法 / Usage:
    python3 scripts_dev/sync_from_master.py --dry-run
    python3 scripts_dev/sync_from_master.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_ROOT = os.path.join(os.path.dirname(ROOT), "bird-names")
MASTER_DB = os.path.join(MASTER_ROOT, "bird_master.sqlite")
REF_DB = os.path.join(ROOT, "birdid", "data", "bird_reference.sqlite")
NAME_DB = os.path.join(ROOT, "ioc", "birdname.db")
PINYIN_JSON = os.path.join(ROOT, "ioc", "pinyin_toned.json")
BACKUP_DIR = os.path.expanduser("~/Library/Caches/SuperPicky-db-backups")

#: 写进 birdname.db 的主库版本名；tools.birdname_versions 据此把它排在最前
#: Version name of the master catalog in birdname.db (preferred by birdname_versions).
MASTER_VERSION_NAME = "SuperPicky 名录"
IOC_VERSION_ID = 8


def norm_sci(sci: str) -> str:
    """规范化学名（去 † 并合并空白）/ Normalise a scientific name."""
    return re.sub(r"\s+", " ", (sci or "").replace("†", " ")).strip()


def norm_en(name: str) -> str:
    """规范化英文名（Gray/Grey、大小写、标点）/ Normalise an English name."""
    s = (name or "").lower().replace("’", "'").replace("gray", "grey")
    return re.sub(r"[^a-z]", "", s)


def plain_syllables(toned: str) -> str:
    """
    带调拼音 → birdname.db 的 pinyin_name 格式（去声调、空格分隔、ü 写作 v）。

    参数:
    toned (str): 如 "lǜ chì yàn"

    返回:
    str: 如 "lv chi yan"

    Toned pinyin to birdname.db's pinyin_name format.
    """
    out = []
    for syl in toned.split():
        syl = syl.replace("ü", "v").replace("ǖ", "v").replace("ǘ", "v").replace("ǚ", "v").replace("ǜ", "v")
        syl = "".join(c for c in unicodedata.normalize("NFD", syl) if unicodedata.category(c) != "Mn")
        out.append(syl.lower())
    return " ".join(out)


def run_master_checks(master_root: str, master_db: str) -> List[str]:
    """
    跑主库自带校验（bird-names/tools/check.py），返回错误列表（提醒不算）。

    参数:
    master_root (str): bird-names 仓库目录
    master_db (str): 主库路径

    返回:
    List[str]: 错误描述；空列表表示通过

    Run the master's own checks and return its errors.
    """
    spec = importlib.util.spec_from_file_location(
        "bird_names_check", os.path.join(master_root, "tools", "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    con = sqlite3.connect(f"file:{master_db}?mode=ro", uri=True)
    try:
        return module.run_checks(con)[0]
    finally:
        con.close()


def load_master(master_db: str) -> dict:
    """
    读主库（只读）/ Read the master database read-only.

    参数:
    master_db (str): 主库路径

    返回:
    dict: species（id → 字段）、aliases（id → [旧名]）、pinyin（中文名 → 带调拼音）、
          initials（中文名 → 首字母）、version
    """
    con = sqlite3.connect(f"file:{master_db}?mode=ro", uri=True)
    try:
        species = {}
        for sid, sci, en, zh, tc, code in con.execute(
                "SELECT s.id, s.scientific_name, s.english_name, s.zh_simplified, s.zh_traditional, "
                "(SELECT code FROM xref WHERE species_id = s.id AND system = 'ebird_code') FROM species s"):
            species[sid] = {"sci": sci, "en": en, "zh": zh, "tc": tc or zh, "code": code}
        aliases: Dict[int, List[str]] = {}
        for alias, sid in con.execute("SELECT alias, species_id FROM name_alias ORDER BY alias"):
            aliases.setdefault(sid, []).append(alias)
        pinyin, initials = {}, {}
        for name, toned, ini in con.execute("SELECT chinese_name, toned, initials FROM pinyin"):
            pinyin[name] = toned
            initials[name] = ini
        version = con.execute("SELECT value FROM meta WHERE key = 'names_version'").fetchone()[0]
    finally:
        con.close()
    return {"species": species, "aliases": aliases, "pinyin": pinyin, "initials": initials,
            "version": version}


def plan_reference(ref_db: str, species: dict) -> Tuple[List[tuple], List[str]]:
    """
    对比 bird_reference.sqlite 的 BirdCountInfo，算出要改的行。

    参数:
    ref_db (str): bird_reference.sqlite 路径
    species (dict): load_master()["species"]

    返回:
    Tuple[List[tuple], List[str]]: (更新行 [(简体, 繁体, 代码, id)], 致命问题)

    Diff BirdCountInfo against the master.
    """
    con = sqlite3.connect(f"file:{ref_db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, scientific_name, chinese_simplified, chinese_traditional, ebird_code "
                           "FROM BirdCountInfo").fetchall()
    finally:
        con.close()
    fatal, updates, seen = [], [], set()
    for sid, sci, zh, tc, code in rows:
        seen.add(sid)
        m = species.get(sid)
        if m is None:
            fatal.append(f"id={sid} {sci} 主库里没有")
        elif m["sci"] != sci:
            fatal.append(f"id={sid} 学名不一致：SuperPicky {sci} / 主库 {m['sci']}")
        elif (zh, tc, code) != (m["zh"], m["tc"], m["code"]):
            updates.append((m["zh"], m["tc"], m["code"], sid))
    if set(species) - seen:
        fatal.append(f"主库有 {len(set(species) - seen)} 个鸟种 BirdCountInfo 里没有")
    return updates, fatal


def build_catalog(name_db: str, master: dict) -> Tuple[List[tuple], dict]:
    """
    组装「SuperPicky 名录」版本的全部行（主库种 + IOC 补漏种）。

    参数:
    name_db (str): birdname.db 路径（读 IOC 15.1 取分类与补漏）
    master (dict): load_master() 结果

    返回:
    Tuple[List[tuple], dict]: (行，列顺序同 _CATALOG_COLUMNS；统计)

    Assemble the master catalog rows: master species plus IOC-only species.
    """
    con = sqlite3.connect(f"file:{name_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        ioc = [dict(r) for r in con.execute("SELECT * FROM birds WHERE version_id = ?", (IOC_VERSION_ID,))]
    finally:
        con.close()
    ioc_by_sci = {}
    for r in ioc:
        ioc_by_sci.setdefault(norm_sci(r["latin_name"]), r)

    species = master["species"]
    current = {m["zh"].lstrip("*") for m in species.values()}
    master_sci = {norm_sci(m["sci"]) for m in species.values()}
    master_en = {norm_en(m["en"]) for m in species.values()}
    rows: List[tuple] = []
    for sid, m in sorted(species.items()):
        if m["zh"].startswith("*"):
            continue                      # 同物异名旧条目不进搜索 / legacy synonyms stay out
        hit = ioc_by_sci.get(norm_sci(m["sci"])) or {}
        toned = master["pinyin"].get(m["zh"], "")
        alias = list(master["aliases"].get(sid, []))
        ioc_zh = (hit.get("chinese_name") or "").strip()
        if ioc_zh and ioc_zh != m["zh"] and ioc_zh not in alias:
            alias.append(ioc_zh)
        rows.append((m["zh"], m["en"], m["sci"], plain_syllables(toned),
                     master["initials"].get(m["zh"], "").upper(), m["tc"],
                     hit.get("order_en"), hit.get("family_en"), hit.get("genus_en"),
                     hit.get("order_zh"), hit.get("family_zh"), hit.get("genus_zh"),
                     1, "|".join(alias) or None))
    gap = 0
    for r in ioc:
        zh, en = (r["chinese_name"] or "").strip(), (r["english_name"] or "").strip()
        if not zh or norm_sci(r["latin_name"]) in master_sci or norm_en(en) in master_en or zh in current:
            continue
        gap += 1
        rows.append((zh, en, r["latin_name"], r["pinyin_name"], r["abbreviation"], r["traditional_name"],
                     r["order_en"], r["family_en"], r["genus_en"], r["order_zh"], r["family_zh"], r["genus_zh"],
                     0, None))
    return rows, {"master": len(rows) - gap, "ioc_gap": gap}


_CATALOG_COLUMNS = ("chinese_name", "english_name", "latin_name", "pinyin_name", "abbreviation",
                    "traditional_name", "order_en", "family_en", "genus_en", "order_zh", "family_zh",
                    "genus_zh", "in_model", "search_aliases")


def write_catalog(name_db: str, rows: List[tuple]) -> None:
    """
    把「SuperPicky 名录」写进 birdname.db：必要时加列，版本 id 保持不变，整版替换内容。

    参数:
    name_db (str): birdname.db 路径
    rows (List[tuple]): build_catalog 的行

    Write the master catalog version into birdname.db (adds columns if needed).
    """
    con = sqlite3.connect(name_db)
    try:
        with con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(birds)")}
            if "in_model" not in cols:
                con.execute("ALTER TABLE birds ADD COLUMN in_model INTEGER")
            if "search_aliases" not in cols:
                con.execute("ALTER TABLE birds ADD COLUMN search_aliases TEXT")
            row = con.execute("SELECT version_id FROM versions WHERE version_name = ?",
                              (MASTER_VERSION_NAME,)).fetchone()
            if row is None:
                vid = con.execute("INSERT INTO versions (version_name, is_active) VALUES (?, 0)",
                                  (MASTER_VERSION_NAME,)).lastrowid
            else:
                vid = row[0]
            con.execute("DELETE FROM birds WHERE version_id = ?", (vid,))
            con.executemany(
                f"INSERT INTO birds ({', '.join(_CATALOG_COLUMNS)}, version_id) "
                f"VALUES ({', '.join('?' * len(_CATALOG_COLUMNS))}, ?)",
                [(*r, vid) for r in rows])
        con.execute("VACUUM")
    finally:
        con.close()


def merge_pinyin(json_path: str, master: dict) -> Tuple[dict, int]:
    """
    把主库现用名的读音并进 pinyin_toned.json（只增改主库现用名，其余不动）。

    参数:
    json_path (str): pinyin_toned.json 路径
    master (dict): load_master() 结果

    返回:
    Tuple[dict, int]: (合并后的表, 变动条数)

    Merge the master's readings for current names into the JSON table.
    """
    with open(json_path, "r", encoding="utf-8") as fh:
        table = json.load(fh)
    changed = 0
    for m in master["species"].values():
        name = m["zh"].lstrip("*")
        toned = master["pinyin"].get(name)
        if toned and table.get(name) != toned:
            table[name] = toned
            changed += 1
    return table, changed


def main(argv: List[str]) -> int:
    """命令行入口；校验失败或数据对不齐时返回 1 / CLI entry point."""
    parser = argparse.ArgumentParser(description="从鸟名主库同步名字到 SuperPicky")
    parser.add_argument("--master-db", default=MASTER_DB)
    parser.add_argument("--master-root", default=MASTER_ROOT)
    parser.add_argument("--ref-db", default=REF_DB)
    parser.add_argument("--name-db", default=NAME_DB)
    parser.add_argument("--pinyin-json", default=PINYIN_JSON)
    parser.add_argument("--backup-dir", default=BACKUP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    errors = run_master_checks(args.master_root, args.master_db)
    if errors:
        print(f"主库校验失败 {len(errors)} 处，中止：")
        for e in errors[:20]:
            print("  " + e)
        return 1
    master = load_master(args.master_db)
    updates, fatal = plan_reference(args.ref_db, master["species"])
    if fatal:
        print("bird_reference 与主库对不上，中止：")
        for f in fatal[:20]:
            print("  " + f)
        return 1
    catalog, stats = build_catalog(args.name_db, master)
    table, py_changed = merge_pinyin(args.pinyin_json, master)

    print(f"主库 names_version={master['version']}")
    print(f"  bird_reference：BirdCountInfo 更新 {len(updates)} 行")
    print(f"  birdname.db「{MASTER_VERSION_NAME}」：主库种 {stats['master']}，IOC 补漏 {stats['ioc_gap']}")
    print(f"  pinyin_toned.json：增改 {py_changed} 条")
    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    os.makedirs(args.backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path in (args.ref_db, args.name_db, args.pinyin_json):
        shutil.copy2(path, os.path.join(args.backup_dir, f"{stamp}.{os.path.basename(path)}"))

    con = sqlite3.connect(args.ref_db)
    try:
        with con:
            con.executemany("UPDATE BirdCountInfo SET chinese_simplified = ?, chinese_traditional = ?, "
                            "ebird_code = ? WHERE id = ?", updates)
    finally:
        con.close()
    write_catalog(args.name_db, catalog)
    with open(args.pinyin_json, "w", encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False, indent=0, sort_keys=True)
        fh.write("\n")
    print(f"已写入；备份目录 {args.backup_dir}（前缀 {stamp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
