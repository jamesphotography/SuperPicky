#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iRateBird 鸟种美学指数离线构建脚本（开发期一次性运行）。

数据来源 (CC-BY 4.0, Santangeli et al. 2023, Scientific Data s41597-023-02169-0)
figshare: https://figshare.com/articles/dataset/20170082
需手动下载放 scripts_dev/data_sources/ :
  - iratebirds_final_predictions_average_fullmodel_subsetmodel_151122.csv
  - iratebirds_pred_ratings_species_and_sex_level_120123.csv

把物种级 full_model 分 + 雌雄分匹配到本地 model_class_id，归一化 0–100，
写入 bird_reference.sqlite 的 iratebirds_aesthetic 表（幂等：先 DROP 再建）。

Offline builder for the iRateBird species aesthetic index (run once by a dev).
"""
import argparse
import csv
import os
import sqlite3
import sys
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from birdid.iratebirds_aesthetic import (  # noqa: E402
    normalize_score, derive_default_score, is_dimorphic,
)

_CREATE_SQL = """
CREATE TABLE iratebirds_aesthetic (
    model_class_id    INTEGER UNIQUE,
    scientific_name   TEXT,
    aesthetic_100     REAL,
    aesthetic_raw_10  REAL,
    aesthetic_male    REAL,
    aesthetic_female  REAL,
    is_dimorphic      INTEGER,
    no_of_ratings     INTEGER,
    source            TEXT
);
CREATE INDEX idx_iratebirds_class ON iratebirds_aesthetic(model_class_id);
"""


def _to_float(s: Optional[str]) -> Optional[float]:
    """
    CSV 数值转 float：空串/NA → None，支持欧洲小数逗号。

    iRateBird 的 predictions 文件用逗号作小数点（如 "6,9086"），sex-level
    文件用标准点（如 "6.6579"）。仅当字符串含逗号且不含点时按小数逗号处理，
    避免误伤千分位场景（本数据集分数 1–10、评分数为小整数，无千分位）。

    Convert a CSV cell to float; "" / NA → None. Handles the European decimal
    comma used by the predictions file ("6,9086"), while leaving dot-decimal
    values ("6.6579") untouched.
    """
    if s is None:
        return None
    s = s.strip()
    if s == "" or s.upper() in ("NA", "NAN", "NULL"):
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _sniff_delimiter(path: str) -> str:
    """
    嗅探 CSV 分隔符：predictions 文件用分号、sex-level 文件用逗号。
    读首行，分号多于逗号则判为分号，否则逗号。

    Sniff the CSV delimiter (the predictions file is ';'-separated, the
    sex-level file is ','-separated). Reads the header line only.
    """
    with open(path, encoding="utf-8-sig") as f:
        header = f.readline()
    return ";" if header.count(";") > header.count(",") else ","


def _load_sex_scores(sex_csv: str) -> Dict[str, Dict[str, float]]:
    """读 sex-level CSV → {sci_name: {'male': 0-100, 'female': 0-100}}（已归一化）。"""
    out: Dict[str, Dict[str, float]] = {}
    with open(sex_csv, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=_sniff_delimiter(sex_csv)):
            name = (row.get("sci_name") or "").strip()
            sex = (row.get("sex") or "").strip().lower()
            score = normalize_score(_to_float(row.get("predicted_attractiveness_sex_model")))
            if not name or sex not in ("male", "female") or score is None:
                continue
            out.setdefault(name, {})[sex] = score
    return out


def build_aesthetic_table(
    predictions_csv: str,
    sex_csv: str,
    class_map: Dict[str, int],
    db_path: str,
) -> dict:
    """
    构建 iratebirds_aesthetic 表（幂等）。

    参数:
    predictions_csv: 物种级 predictions CSV 路径
    sex_csv: 雌雄 sex-level CSV 路径
    class_map: {scientific_name: model_class_id} 学名→类别映射
    db_path: 目标 sqlite（bird_reference.sqlite）

    返回:
    dict: {"matched": 命中数, "total": predictions 总行数, "match_rate": 比率}
    """
    sex_scores = _load_sex_scores(sex_csv)
    matched = 0
    total = 0
    unmatched_names = []
    con = sqlite3.connect(db_path)
    try:
        con.executescript("DROP TABLE IF EXISTS iratebirds_aesthetic;")
        con.executescript(_CREATE_SQL)
        with open(predictions_csv, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=_sniff_delimiter(predictions_csv)):
                total += 1
                name = (row.get("sci_name") or "").strip()
                cid = class_map.get(name)
                if cid is None:
                    unmatched_names.append(name)
                    continue
                species_100 = normalize_score(
                    _to_float(row.get("predicted_attractiveness_full_model")))
                sx = sex_scores.get(name, {})
                male_100 = sx.get("male")
                female_100 = sx.get("female")
                # 多个 iRateBird 学名可能映射到同一 model_class_id（同物异名）。
                # 表对 model_class_id 唯一，冲突时保留评分数更多（更可信）的那条。
                # Several iRateBird names can map to one model_class_id (synonyms);
                # on conflict keep the row backed by more ratings (more reliable).
                con.execute(
                    "INSERT INTO iratebirds_aesthetic "
                    "(model_class_id, scientific_name, aesthetic_100, aesthetic_raw_10, "
                    " aesthetic_male, aesthetic_female, is_dimorphic, no_of_ratings, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(model_class_id) DO UPDATE SET "
                    " scientific_name=excluded.scientific_name, "
                    " aesthetic_100=excluded.aesthetic_100, "
                    " aesthetic_raw_10=excluded.aesthetic_raw_10, "
                    " aesthetic_male=excluded.aesthetic_male, "
                    " aesthetic_female=excluded.aesthetic_female, "
                    " is_dimorphic=excluded.is_dimorphic, "
                    " no_of_ratings=excluded.no_of_ratings "
                    "WHERE excluded.no_of_ratings > iratebirds_aesthetic.no_of_ratings",
                    (cid, name,
                     derive_default_score(species_100, male_100, female_100),
                     _to_float(row.get("predicted_attractiveness_full_model")),
                     male_100, female_100,
                     is_dimorphic(male_100, female_100),
                     int(_to_float(row.get("no_of_ratings_used")) or 0),
                     "iratebirds_2023"),
                )
                matched += 1
        con.commit()
    finally:
        con.close()
    rate = matched / total if total else 0.0
    if unmatched_names:
        out_csv = os.path.join(os.path.dirname(os.path.abspath(predictions_csv)),
                               "iratebirds_unmatched.csv")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["unmatched_sci_name"])
            for n in unmatched_names:
                w.writerow([n])
    return {"matched": matched, "total": total, "match_rate": rate}


def _load_class_map(db_path: str) -> Dict[str, int]:
    """
    构建 {scientific_name: model_class_id} 学名→类别映射。

    以 gbif_rarity_100（与罕见度同一基准）为主，并并入 avilist_map 的
    模型学名与 AviList 学名两列——iRateBird 用 eBird/Clements 2019 分类，
    与我们模型的分类存在漂移（如 Accipiter 属被拆分），多学名来源能多命中。
    先写入的（gbif）优先，setdefault 不覆盖。

    Build the scientific_name → model_class_id map. Primary source is
    gbif_rarity_100; also fold in avilist_map's model + AviList names to
    catch taxonomy drift between iRateBird (eBird/Clements 2019) and our
    model. gbif entries win (inserted first, setdefault no-overwrite).
    """
    mapping: Dict[str, int] = {}
    con = sqlite3.connect(db_path)
    try:
        queries = [
            "SELECT scientific_name, model_class_id FROM gbif_rarity_100 "
            "WHERE scientific_name IS NOT NULL AND model_class_id IS NOT NULL",
        ]
        # avilist_map 别名列（表可能不存在的旧库容错）
        # AviList alias columns (tolerate an older DB without the table)
        for col in ("scientific_name_model", "scientific_name_avilist"):
            queries.append(
                f"SELECT {col}, model_class_id FROM avilist_map "
                f"WHERE {col} IS NOT NULL AND model_class_id IS NOT NULL"
            )
        for q in queries:
            try:
                for sci, cid in con.execute(q):
                    mapping.setdefault(sci, int(cid))
            except sqlite3.OperationalError:
                continue  # 表/列不存在 → 跳过该来源
    finally:
        con.close()
    return mapping


def main() -> None:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ds = os.path.join(repo, "scripts_dev", "data_sources")
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", default=os.path.join(
        ds, "iratebirds_final_predictions_average_fullmodel_subsetmodel_151122.csv"))
    ap.add_argument("--sex", default=os.path.join(
        ds, "iratebirds_pred_ratings_species_and_sex_level_120123.csv"))
    ap.add_argument("--db", default=os.path.join(
        repo, "birdid", "data", "bird_reference.sqlite"))
    args = ap.parse_args()
    class_map = _load_class_map(args.db)
    stats = build_aesthetic_table(args.predictions, args.sex, class_map, args.db)
    print(f"matched {stats['matched']}/{stats['total']} "
          f"({stats['match_rate']*100:.1f}%) → {args.db}")


if __name__ == "__main__":
    main()
