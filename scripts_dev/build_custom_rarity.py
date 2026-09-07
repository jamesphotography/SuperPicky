#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一份 CSV 罕见度数据转成应用能读的「自定义罕见指数」库。

应用只提供接口、不附带任何数据源；本脚本是开发侧的转换工具，数据从哪来由
使用者决定。生成的 db 放在用户配置目录，那里不会被 .spec 打包
（birdid/data 会被整目录打包，所以刻意避开）。详情页装了该文件就并排显示
GBIF 与这份自定义指数，没有则只显示 GBIF。

⚠️ 无论数据来自哪里，**都不要**把它提交进仓库，也不要写进随应用分发的
bird_reference.sqlite——多数数据源的授权条款不允许再分发，而这两处都会
随应用发出去。

Convert a CSV of rarity scores into the app's optional "custom rarity"
database. The app bundles no dataset; where the data comes from is the
operator's call. Never commit such data, and never merge it into
bird_reference.sqlite — both would ship with the app.

CSV 需含列 / Required CSV columns:
    chinese_simplified  中文鸟名
    english_name        英文鸟名
    rarity_index        0-10，越大越罕见
    （model_class_id / scientific_name / matched_by 可选，有则一并存入）

用法 / Usage:
    python scripts_dev/build_custom_rarity.py            # 用默认路径
    python scripts_dev/build_custom_rarity.py --csv <路径> --out <路径>
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_CSV = os.path.join("birdid", "data", "rarity_match_report.csv")


def _default_out() -> str:
    """
    默认输出到用户配置目录——刻意避开 birdid/data，那里会被 .spec 整目录
    打包进安装包，而这类数据多半不允许随应用分发。
    Default to the config dir; birdid/data would be bundled by the .spec.
    """
    from config import get_app_config_dir
    return str(get_app_config_dir() / "custom_rarity.db")


def read_rows(csv_path: str) -> Tuple[List[Dict[str, str]], int]:
    """
    读取 CSV，返回有 rarity_index 的行与被跳过的行数。

    CSV 由 Excel 导出，带 UTF-8 BOM，必须用 utf-8-sig 读，否则首列列名会
    多出 \\ufeff 前缀导致取不到值。

    Read the match report (Excel-exported, UTF-8 BOM — utf-8-sig is required).

    参数 / Args:
        csv_path: CSV 路径

    返回 / Returns:
        Tuple[List[Dict[str, str]], int]: (有效行, 跳过行数)

    异常 / Raises:
        FileNotFoundError: CSV 不存在
    """
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    kept: List[Dict[str, str]] = []
    skipped = 0
    for r in rows:
        idx = (r.get("rarity_index") or "").strip()
        if not idx:
            skipped += 1
            continue
        try:
            float(idx)
        except ValueError:
            skipped += 1
            continue
        kept.append(r)
    return kept, skipped


def build(csv_path: str, out_path: str) -> Dict[str, int]:
    """
    生成 sqlite 库（覆盖已有文件），并对查询用的两个鸟名列建索引。

    Build the sqlite DB, replacing any existing file.

    参数 / Args:
        csv_path: 源 CSV
        out_path: 目标 db 路径

    返回 / Returns:
        Dict[str, int]: {"written": 写入行数, "skipped": 跳过行数}
    """
    kept, skipped = read_rows(csv_path)

    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute(
            "CREATE TABLE custom_rarity ("
            "  model_class_id     INTEGER,"
            "  scientific_name    TEXT,"
            "  english_name       TEXT,"
            "  chinese_simplified TEXT,"
            "  rarity_index       REAL,"
            "  matched_by         TEXT"
            ")"
        )
        con.executemany(
            "INSERT INTO custom_rarity VALUES (?,?,?,?,?,?)",
            [
                (
                    int(r["model_class_id"]) if (r.get("model_class_id") or "").strip()
                    else None,
                    (r.get("scientific_name") or "").strip() or None,
                    (r.get("english_name") or "").strip() or None,
                    (r.get("chinese_simplified") or "").strip() or None,
                    float(r["rarity_index"]),
                    (r.get("matched_by") or "").strip() or None,
                )
                for r in kept
            ],
        )
        # 运行时按鸟名查（照片记录里没有 model_class_id），两列都建索引
        # Runtime lookups go by species name, so index both name columns.
        con.execute("CREATE INDEX idx_cr_cn ON custom_rarity(chinese_simplified)")
        con.execute("CREATE INDEX idx_cr_en ON custom_rarity(english_name)")
        con.commit()
    finally:
        con.close()

    os.replace(tmp, out_path)
    return {"written": len(kept), "skipped": skipped}


def main() -> int:
    """命令行入口。"""
    ap = argparse.ArgumentParser(
        description="把 CSV 罕见度数据转成自定义罕见指数库（数据不得随应用分发）"
    )
    ap.add_argument("--csv", default=DEFAULT_CSV, help=f"源 CSV（默认 {DEFAULT_CSV}）")
    ap.add_argument("--out", default=_default_out(),
                    help=f"输出 db（默认 {_default_out()}）")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        print(f"❌ 找不到源 CSV: {args.csv}")
        return 1

    stats = build(args.csv, args.out)
    size_kb = os.path.getsize(args.out) / 1024
    print(f"✅ 已生成 {args.out}")
    print(f"   写入 {stats['written']} 行，跳过无值 {stats['skipped']} 行，"
          f"体积 {size_kb:.0f} KB")
    print("   ⚠️ 该文件不得提交进仓库或随应用分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
