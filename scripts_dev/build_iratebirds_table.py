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
    model_class_id    INTEGER,
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
    """CSV 空串/NA → None，否则 float。"""
    if s is None:
        return None
    s = s.strip()
    if s == "" or s.upper() in ("NA", "NAN", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_sex_scores(sex_csv: str) -> Dict[str, Dict[str, float]]:
    """读 sex-level CSV → {sci_name: {'male': 0-100, 'female': 0-100}}（已归一化）。"""
    out: Dict[str, Dict[str, float]] = {}
    with open(sex_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
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
        with open(predictions_csv, encoding="utf-8") as f:
            for row in csv.DictReader(f):
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
                con.execute(
                    "INSERT INTO iratebirds_aesthetic "
                    "(model_class_id, scientific_name, aesthetic_100, aesthetic_raw_10, "
                    " aesthetic_male, aesthetic_female, is_dimorphic, no_of_ratings, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    """从 bird_reference.sqlite 的 gbif_rarity_100 表取 {scientific_name: model_class_id}
    作为学名→类别映射（与罕见度同一匹配基准）。"""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT scientific_name, model_class_id FROM gbif_rarity_100 "
            "WHERE scientific_name IS NOT NULL AND model_class_id IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: int(r[1]) for r in rows}


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
