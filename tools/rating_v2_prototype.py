#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星级算法 V2 原型：批内相对排序 + 配额制，对已处理目录的 report.db 离线重演。

用法 / Usage:
    python scripts_dev/rating_v2_prototype.py <已处理照片目录> [--quota3 20] [--quota2 25]

设计（2026-07-09 与 James 审定）：
- 硬门槛（绝对值，保留现行语义）：无鸟→-1；置信度<50%→0；ISO归一化锐度<100→0；
  TOPIQ<3.5→0；关键点全不可见→1。
- 综合质量分 Q = 0.65×锐度批内百分位 + 0.35×TOPIQ批内百分位，
  再加小幅加减分：飞鸟+0.06、精焦(BEST)+0.04、脱焦(WORST)-0.06；
  眼睛可见度<0.5 的照片星级封顶 2★（保留现行降档精神）。
- 配额划星：3★=Q 前 20%（叠加绝对兜底：归一化锐度≥300），2★=20%-45%，其余 1★。
- 原型限制：TOPIQ 仍是主流程存的整图分；正式实现将改为鸟裁剪区打分。

输出：新旧分布、流向矩阵、三份可人工核对的清单（新3星 / 被降档的旧3星 /
被拯救的旧0星），写入目录下 rating_v2_compare/。

Prototype for the batch-relative + quota rating scheme, replayed offline
against an already-processed directory's report.db. Emits old-vs-new
distributions, a transition matrix, and reviewable file lists.
"""

import argparse
import bisect
import math
import os
import sqlite3
import sys
from collections import Counter
from typing import Dict, List, Optional

# 与 photo_processor 相同的 ISO 归一化参数 / same ISO normalization as production
ISO_BASE = 800
ISO_PENALTY_FACTOR = 0.05
ISO_MIN_FACTOR = 0.5

# 硬门槛 / absolute gates
MIN_CONFIDENCE = 0.50
MIN_SHARPNESS = 100.0
MIN_TOPIQ = 3.5

# Q 分构成 / Q composition
W_SHARP = 0.65
W_TOPIQ = 0.35
BONUS_FLYING = 0.06
BONUS_FOCUS_BEST = 0.04
PENALTY_FOCUS_WORST = -0.06
EYE_CAP_THRESHOLD = 0.5   # best_eye < 0.5 → 星级封顶 2★
QUOTA3_SHARP_FLOOR = 300.0  # 3★ 绝对兜底：归一化锐度下限


def iso_factor(iso: Optional[int]) -> float:
    """ISO 锐度归一化系数（与生产一致）/ ISO sharpness factor (matches production)."""
    if iso is None or iso <= ISO_BASE:
        return 1.0
    penalty = ISO_PENALTY_FACTOR * math.log2(iso / ISO_BASE)
    return max(ISO_MIN_FACTOR, 1.0 - penalty)


def percentile_fn(values: List[float]):
    """返回 v→批内百分位(0-1) 的函数 / batch percentile function."""
    s = sorted(values)
    n = len(s)

    def pct(v: float) -> float:
        return bisect.bisect_left(s, v) / n if n else 0.0

    return pct


def rate_v2(rows: List[sqlite3.Row], quota3: float, quota2: float) -> Dict[str, dict]:
    """
    对一批照片计算 V2 星级。

    参数:
        rows: photos 表记录（需含 head_sharp/nima_score/iso 等字段）
        quota3: 3★ 配额（百分比，如 20）
        quota2: 2★ 配额（百分比，接在 3★ 之后，如 25 → 20%-45% 区间）

    返回:
        {filename: {'new': 星级, 'q': Q分, 'norm_sharp': 归一化锐度, ...}}
    """
    result: Dict[str, dict] = {}
    scored: List[dict] = []

    for r in rows:
        fn = r["filename"]
        info = {"old": r["rating"], "new": None, "q": None,
                "norm_sharp": None, "topiq": r["nima_score"], "fly": bool(r["is_flying"])}
        result[fn] = info

        # 硬门槛链（与现行 RatingEngine 顺序一致）
        if not r["has_bird"]:
            info["new"] = -1
            continue
        if (r["confidence"] or 0) < MIN_CONFIDENCE:
            info["new"] = 0
            continue
        best_eye = max(r["left_eye"] or 0.0, r["right_eye"] or 0.0)
        beak = r["beak"] or 0.0
        if best_eye < 0.3 and beak < 0.3:
            info["new"] = 1  # 关键点全不可见 → 角度不佳
            continue
        norm_sharp = (r["head_sharp"] or 0.0) * iso_factor(r["iso"])
        info["norm_sharp"] = norm_sharp
        if norm_sharp < MIN_SHARPNESS:
            info["new"] = 0
            continue
        if r["nima_score"] is not None and r["nima_score"] < MIN_TOPIQ:
            info["new"] = 0
            continue

        scored.append({"fn": fn, "sharp": norm_sharp,
                       "topiq": r["nima_score"] or 0.0,
                       "fly": bool(r["is_flying"]),
                       "focus": r["focus_status"] or "",
                       "eye": best_eye})

    if not scored:
        return result

    pct_sharp = percentile_fn([s["sharp"] for s in scored])
    pct_topiq = percentile_fn([s["topiq"] for s in scored])

    for s in scored:
        q = W_SHARP * pct_sharp(s["sharp"]) + W_TOPIQ * pct_topiq(s["topiq"])
        if s["fly"]:
            q += BONUS_FLYING
        if s["focus"] == "BEST":
            q += BONUS_FOCUS_BEST
        elif s["focus"] == "WORST":
            q += PENALTY_FOCUS_WORST
        s["q"] = q
        result[s["fn"]]["q"] = q

    scored.sort(key=lambda s: -s["q"])
    n = len(scored)
    cut3, cut2 = quota3 / 100.0, (quota3 + quota2) / 100.0
    for idx, s in enumerate(scored):
        frac = idx / n
        if frac < cut3 and s["sharp"] >= QUOTA3_SHARP_FLOOR:
            star = 3
        elif frac < cut2:
            star = 2
        else:
            star = 1
        if s["eye"] < EYE_CAP_THRESHOLD:
            star = min(star, 2)
        result[s["fn"]]["new"] = star

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="星级算法 V2 原型对比")
    ap.add_argument("directory", help="已处理照片目录（含 .superpicky/report.db）")
    ap.add_argument("--quota3", type=float, default=20.0, help="3星配额%%（默认20）")
    ap.add_argument("--quota2", type=float, default=25.0, help="2星配额%%（默认25，即3星线后再取25%%）")
    args = ap.parse_args()

    db_path = os.path.join(args.directory, ".superpicky", "report.db")
    if not os.path.exists(db_path):
        print(f"❌ 未找到 report.db: {db_path}")
        return 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT filename, rating, has_bird, confidence, head_sharp, nima_score, "
        "left_eye, right_eye, beak, iso, is_flying, focus_status, current_path "
        "FROM photos"))
    print(f"样本: {len(rows)} 张（{os.path.basename(args.directory)}）")

    res = rate_v2(rows, args.quota3, args.quota2)

    old_c = Counter(v["old"] for v in res.values())
    new_c = Counter(v["new"] for v in res.values())
    print(f"\n{'星级':>4} {'现行':>6} {'V2':>6}")
    for star in (3, 2, 1, 0, -1):
        print(f"{star:>4}★ {old_c.get(star, 0):>6} {new_c.get(star, 0):>6}")

    flows = Counter((v["old"], v["new"]) for v in res.values() if v["old"] != v["new"])
    print("\n主要流向(现行→V2):")
    for (o, nw), cnt in sorted(flows.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {o}★→{nw}★: {cnt}")

    # 可人工核对的清单 / reviewable lists
    out_dir = os.path.join(args.directory, "rating_v2_compare")
    os.makedirs(out_dir, exist_ok=True)
    lists = {
        "new_3star.txt": sorted(
            (v["q"], fn) for fn, v in res.items() if v["new"] == 3),
        "demoted_3to1.txt": sorted(
            (v["q"] or 0, fn) for fn, v in res.items()
            if v["old"] == 3 and v["new"] == 1),
        "rescued_0star.txt": sorted(
            (v["q"] or 0, fn) for fn, v in res.items()
            if v["old"] == 0 and (v["new"] or 0) >= 2),
    }
    for name, items in lists.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            for q, fn in sorted(items, key=lambda x: -(x[0] or 0)):
                v = res[fn]
                f.write(f"{fn}\tQ={q:.3f}\t原{v['old']}★→新{v['new']}★\t"
                        f"锐度={v['norm_sharp'] and round(v['norm_sharp']) or '-'}\t"
                        f"TOPIQ={v['topiq'] and round(v['topiq'], 2) or '-'}\t"
                        f"{'飞' if v['fly'] else ''}\n")
        print(f"清单已写出: {path} ({len(items)} 条)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
