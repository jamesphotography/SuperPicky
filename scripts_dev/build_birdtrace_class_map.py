# -*- coding: utf-8 -*-
"""
生成 BirdTrace → 鸟种库类别号对照表 / Build the BirdTrace-to-species-DB class map.

BirdTrace 的 classes.json 只有去掉撇号、下划线连接的英文名（eBird/Clements 新分类），没有学名与代码。
这里用 eBird 公开的分类表（英文名 → eBird 代码 + 学名）把英文名换成代码，再用
birdid.class_mapping.build_class_map（拆分对照 → eBird 代码 → 学名 → 鸟种库/AviList 英文名）对到鸟种库类别号。
输出 birdid/data/birdtrace_class_map.json，运行时只读这个文件。

BirdTrace's classes.json only has apostrophe-free, underscore-joined English names (new eBird/Clements
taxonomy). eBird's public taxonomy CSV turns names into codes and scientific names; class_mapping then
maps them onto species-DB class ids. The output JSON is all the runtime reads.

用法 / Usage:
    curl -o /tmp/ebird_taxonomy.csv "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv&locale=en"
    python3 scripts_dev/build_birdtrace_class_map.py --classes models/birdtrace/classes.json \
        --taxonomy /tmp/ebird_taxonomy.csv
"""
import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
from typing import Dict, List, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from birdid.class_mapping import build_class_map, norm_name  # noqa: E402

DB = os.path.join(REPO, "birdid", "data", "bird_reference.sqlite")
REGIONS = os.path.join(REPO, "birdid", "data", "ebird_regions.db")
OUT = os.path.join(REPO, "birdid", "data", "birdtrace_class_map.json")


def load_db_indexes() -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """
    鸟种库的 eBird 代码 / 学名 / 英文名（含 AviList 各版）→ 类别号 / Species-DB lookups.

    返回 / Returns:
        tuple: (by_code, by_sci, by_en)
    """
    by_code: Dict[str, int] = {}
    by_sci: Dict[str, int] = {}
    by_en: Dict[str, int] = {}
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
        for code, sci, en, cid in db.execute(
                "SELECT ebird_code, scientific_name, english_name, model_class_id FROM BirdCountInfo "
                "WHERE model_class_id IS NOT NULL"):
            if code:
                by_code.setdefault(code, int(cid))
            if sci:
                by_sci.setdefault(sci, int(cid))
            if en:
                by_en.setdefault(norm_name(en), int(cid))
        cols = [r[1] for r in db.execute("PRAGMA table_info(avilist_map)") if r[1].startswith("en_name")]
        for row in db.execute(f"SELECT model_class_id, {', '.join(cols)} FROM avilist_map "
                              "WHERE model_class_id IS NOT NULL"):
            for en in row[1:]:
                if en:
                    by_en.setdefault(norm_name(en), int(row[0]))
    by_en.pop("", None)
    return by_code, by_sci, by_en


def main() -> None:
    """
    入口：生成对照表并打印覆盖情况 / Entry: write the class map and report coverage.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", required=True, help="BirdTrace classes.json")
    ap.add_argument("--taxonomy", required=True, help="eBird 分类表 CSV / eBird taxonomy CSV")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    with open(args.classes, encoding="utf-8") as f:
        classes = sorted(json.load(f), key=lambda c: c["index"])
    names = [c["species_name"].replace("_", " ") for c in classes]

    taxa: Dict[str, Tuple[str, str]] = {}
    code_by_en: Dict[str, str] = {}
    with open(args.taxonomy, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        taxa[r["SPECIES_CODE"]] = (r["COMMON_NAME"], r["SCIENTIFIC_NAME"])
        if r["CATEGORY"] == "species":
            code_by_en.setdefault(norm_name(r["COMMON_NAME"]), r["SPECIES_CODE"])

    codes: List[str] = []
    for i, en in enumerate(names):
        code = code_by_en.get(norm_name(en))
        if code is None:  # 不在 eBird 分类表：只能靠英文名兜底 / not in eBird: English-name fallback only
            code = f"_bt{i}"
            taxa[code] = (en, "")
        codes.append(code)

    by_code, by_sci, by_en = load_db_indexes()
    class_map = build_class_map(codes, taxa, by_code, by_sci, by_en)

    with sqlite3.connect(f"file:{REGIONS}?mode=ro", uri=True) as rg:
        vic = {int(c) for (c,) in rg.execute("SELECT class_id FROM region_species WHERE region='AU-VIC'")}
    mapped = [c for c in class_map if c >= 0]
    misses = [names[i] for i, c in enumerate(class_map) if c < 0]
    meta = {
        "source": "https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K classes.json",
        "taxonomy": f"eBird taxonomy CSV ({len(rows)} rows)",
        "generated": datetime.date.today().isoformat(),
        "classes": len(names),
        "mapped": len(mapped),
        "not_in_ebird_taxonomy": sum(1 for c in codes if c.startswith("_bt")),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "class_map": class_map}, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(meta, ensure_ascii=False))
    print(f"去重后覆盖鸟种库类别 {len(set(mapped))} 个；AU-VIC 名录覆盖 {len(vic & set(mapped))}/{len(vic)}")
    print(f"对不上 {len(misses)} 类，示例 / misses: {misses[:30]}")
    print(f"→ {args.out}")


if __name__ == "__main__":
    main()
