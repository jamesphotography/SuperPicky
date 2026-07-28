# -*- coding: utf-8 -*-
"""
地理过滤回归验证 / Regression validation for the geo filter.

对照 spec §8 的验收标准逐条验证。核心用例来自一批真实照片：433 张法罗群岛/
冰岛拍摄的 Nikon Z 8 素材，旧的 AVONET 实现在其上产出过小企鹅、蓝脚鲣鸟、
角海鹦等跨半球错误。

Validates each acceptance criterion in spec section 8. The core cases come from
a real batch: 433 Faroe/Iceland photos shot on a Nikon Z 8, on which the old
AVONET implementation produced cross-hemisphere errors (Little Penguin,
Blue-footed Booby, Horned Puffin).

用法 / Usage:
    .venv/bin/python scripts_dev/validate_geo_filter.py
    .venv/bin/python scripts_dev/validate_geo_filter.py --report-db <路径>

不带参数时只跑不依赖照片的检查；给出处理结果库（report.db）后追加逐张比对。
Without arguments only photo-independent checks run; passing a processed
report.db adds the per-photo comparison.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import List, Optional, Tuple

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from birdid.geo_filter import (  # noqa: E402
    TIER_NONE,
    GeoFilter,
    cell_id_for,
)

# 仓库内自带的旧结果快照，用作对照基线 / Bundled old-result snapshot as baseline
LEGACY_DB = os.path.join(PROJ, ".superpicky_backup_JJ2TB_20260723-215817", "report.db")

# 旧实现在冰岛/法罗产出的跨半球错误种 / Cross-hemisphere errors the old build produced
CROSS_HEMISPHERE_ERRORS = ["小企鹅", "蓝脚鲣鸟", "角海鹦", "弗氏燕鸥"]

ICELAND = (63.404, -19.103)
SYDNEY = (-33.87, 151.21)
HEILONGJIANG_SPARSE = (46.41, 127.5)


class Checks:
    """收集检查结果并汇总 / Collects check results and summarizes."""

    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, bool]] = []

    def add(self, name: str, detail: str, ok: bool) -> None:
        self.rows.append((name, detail, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    def note(self, name: str, detail: str) -> None:
        """仅记录、不参与判定 / Informational only."""
        print(f"  [info] {name}: {detail}")

    @property
    def ok(self) -> bool:
        return all(o for _, _, o in self.rows)


def sci_to_class(ref: sqlite3.Connection, sci: str) -> Optional[int]:
    """学名 → model_class_id / Scientific name to model class id."""
    r = ref.execute(
        "SELECT model_class_id FROM BirdCountInfo WHERE scientific_name=?", (sci,)
    ).fetchone()
    return r[0] if r else None


def first_tier(f: GeoFilter, lat: float, lon: float, cc: Optional[str] = None):
    """取第一层候选 / Take the first candidate tier."""
    for cand, tier in f.iter_candidates(lat, lon, cc):
        return cand, tier
    return None, TIER_NONE


def check_iceland_excludes_errors(f: GeoFilter, ref: sqlite3.Connection, ck: Checks) -> None:
    """§8.1 冰岛：跨半球错误种必须被排除，本地真实种必须保留。"""
    cand, tier = first_tier(f, *ICELAND, "IS")
    must_out = {
        "Eudyptula minor": "小企鹅",
        "Sula nebouxii": "蓝脚鲣鸟",
        "Fratercula corniculata": "角海鹦",
    }
    must_in = {
        "Fratercula arctica": "北极海鹦",
        "Somateria mollissima": "欧绒鸭",
        "Numenius phaeopus": "中杓鹬",
    }
    bad = [cn for sci, cn in must_out.items()
           if (c := sci_to_class(ref, sci)) is not None and c in cand]
    miss = [cn for sci, cn in must_in.items()
            if (c := sci_to_class(ref, sci)) is not None and c not in cand]
    ck.add("冰岛候选集", f"{tier} / {len(cand)} 类；误含={bad or '无'}；漏={miss or '无'}",
           not bad and not miss)


def check_sydney_introduced(f: GeoFilter, ref: sqlite3.Connection, ck: Checks) -> None:
    """§8.2 悉尼：AVONET 曾硬屏蔽的引入种必须进入候选集。"""
    cand, tier = first_tier(f, *SYDNEY, "AU")
    intro = {
        "Passer domesticus": "家麻雀",
        "Columba livia": "原鸽",
        "Sturnus vulgaris": "紫翅椋鸟",
        "Turdus merula": "欧乌鸫",
        "Acridotheres tristis": "家八哥",
    }
    miss = [cn for sci, cn in intro.items()
            if (c := sci_to_class(ref, sci)) is not None and c not in cand]
    ck.add("悉尼引入种", f"{tier} / {len(cand)} 类；漏={miss or '无'}", not miss)


def check_sparse_cell_degrades(f: GeoFilter, ck: Checks) -> None:
    """§8.3 稀疏网格必须平滑降级，不得直接落到无过滤。"""
    cand, tier = first_tier(f, *HEILONGJIANG_SPARSE, "CN")
    ck.add("稀疏网格降级", f"{tier} / {len(cand) if cand else 0} 类（不得为 {TIER_NONE}）",
           tier != TIER_NONE)


def check_formerly_missing_classes(f: GeoFilter, ref: sqlite3.Connection, ck: Checks) -> None:
    """§8.4 旧实现永久缺失的 391 类，在其分布区必须可进入候选集。"""
    cases = [
        ("Saxicola stejnegeri", "东亚石䳭", 39.90, 116.40, "CN"),
        ("Anser serrirostris", "短嘴豆雁", 39.90, 116.40, "CN"),
        ("Todiramphus sordidus", "托列斯翡翠", -16.92, 145.77, "AU"),
    ]
    missing = []
    for sci, cn, lat, lon, cc in cases:
        cid = sci_to_class(ref, sci)
        if cid is None:
            missing.append(f"{cn}(参考库无)")
            continue
        hit = any(cand is not None and cid in cand
                  for cand, _ in f.iter_candidates(lat, lon, cc))
        if not hit:
            missing.append(cn)
    ck.add("旧版永久缺失种", f"未覆盖={missing or '无'}", not missing)


def check_volume(f: GeoFilter, ck: Checks) -> None:
    """§8.5 体积上限 40 MB。"""
    mb = os.path.getsize(f.db_path) / 1024 / 1024
    ck.add("数据库体积", f"{mb:.1f} MB（上限 40）", mb <= 40)


def check_no_legacy_refs(ck: Checks) -> None:
    """§8.7 全仓库不得残留旧实现的功能性引用。"""
    import subprocess

    pat = r"avonet_filter|AvonetFilter|ebird_country_filter|get_species_filter|ebird_info"
    out = subprocess.run(
        ["grep", "-rnE", pat, "--include=*.py", "--include=*.spec", PROJ],
        capture_output=True, text=True,
    ).stdout
    hits = []
    for ln in out.splitlines():
        if ("/.venv/" in ln or "/.worktrees/" in ln or "/docs/" in ln
                or "/scripts_dev/validate_geo_filter.py" in ln):
            continue
        # 只看功能性引用：解释「旧实现为何如此」的注释是有价值的历史说明，
        # 不应被当成残留。grep 输出格式为 path:lineno:content。
        # Only functional references count: comments explaining why the old
        # implementation behaved a certain way are worthwhile history, not
        # leftovers. grep output is path:lineno:content.
        content = ln.split(":", 2)[-1].lstrip()
        if content.startswith("#"):
            continue
        hits.append(ln)
    ck.add("无旧实现残留", f"{len(hits)} 处功能性引用", not hits)


def compare_with_legacy(report_db: str, ck: Checks) -> None:
    """
    与旧结果逐张比对 / Per-photo comparison against the legacy run.

    参数 / Parameters:
        report_db (str): 新一轮处理产生的 report.db 路径 / Path to the new report.db.
    """
    if not os.path.exists(LEGACY_DB):
        ck.note("旧结果对照", f"基线库缺失，跳过: {LEGACY_DB}")
        return
    if not os.path.exists(report_db):
        ck.note("旧结果对照", f"新结果库缺失，跳过: {report_db}")
        return

    old = sqlite3.connect(LEGACY_DB)
    new = sqlite3.connect(report_db)

    def species_counts(db: sqlite3.Connection) -> dict:
        return {
            r[0]: r[1] for r in db.execute(
                "SELECT bird_species_cn, COUNT(*) FROM photos "
                "WHERE bird_species_cn IS NOT NULL AND bird_species_cn!='' GROUP BY 1"
            )
        }

    def named(db: sqlite3.Connection) -> Tuple[int, int, float]:
        tot = db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        n = db.execute(
            "SELECT COUNT(*) FROM photos WHERE bird_species_cn IS NOT NULL "
            "AND bird_species_cn!=''"
        ).fetchone()[0]
        avg = db.execute(
            "SELECT AVG(birdid_confidence) FROM photos WHERE birdid_confidence>0"
        ).fetchone()[0] or 0.0
        return tot, n, avg

    old_sp, new_sp = species_counts(old), species_counts(new)
    残留 = {k: new_sp[k] for k in CROSS_HEMISPHERE_ERRORS if new_sp.get(k)}
    消除 = {k: old_sp[k] for k in CROSS_HEMISPHERE_ERRORS if old_sp.get(k)}
    ck.add("跨半球错误消除", f"旧={消除 or '无'} → 新={残留 or '无'}", not 残留)

    o_tot, o_named, o_avg = named(old)
    n_tot, n_named, n_avg = named(new)
    # 识别率不得因收紧过滤而下降——只避错不认鸟不算修好
    # Recognition rate must not drop: avoiding errors by refusing to identify is not a fix
    ck.add(
        "识别率未下降",
        f"{o_named}/{o_tot} ({o_named/o_tot*100:.1f}%) → {n_named}/{n_tot} "
        f"({n_named/n_tot*100:.1f}%)，平均置信 {o_avg:.1f}% → {n_avg:.1f}%",
        n_named >= o_named,
    )

    gps = new.execute(
        "SELECT COUNT(*) FROM photos WHERE gps_latitude IS NOT NULL"
    ).fetchone()[0]
    ck.note("GPS 覆盖", f"{gps}/{n_tot} ({gps/n_tot*100:.1f}%)，"
                        f"其余 {n_tot-gps} 张依赖手选国家走 L4")
    old.close()
    new.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Validate the geo filter against spec section 8")
    p.add_argument("--report-db", default="",
                   help="新一轮处理产生的 report.db；给出后追加逐张比对")
    a = p.parse_args()

    f = GeoFilter()
    if not f.is_available():
        print(f"FAIL: geo_distribution.db 不可用 ({f.db_path})")
        sys.exit(1)
    ref = sqlite3.connect(os.path.join(PROJ, "birdid", "data", "bird_reference.sqlite"))
    ck = Checks()

    print("\n── 候选集行为 / Candidate behaviour ──")
    check_iceland_excludes_errors(f, ref, ck)
    check_sydney_introduced(f, ref, ck)
    check_sparse_cell_degrades(f, ck)
    check_formerly_missing_classes(f, ref, ck)

    print("\n── 产物与清理 / Artifact & cleanup ──")
    check_volume(f, ck)
    check_no_legacy_refs(ck)

    if a.report_db:
        print("\n── 与旧结果逐张比对 / Per-photo comparison ──")
        compare_with_legacy(a.report_db, ck)

    ref.close()
    f.close()
    print(f"\n{'全部通过 / ALL PASS' if ck.ok else '存在失败 / FAILURES PRESENT'}")
    sys.exit(0 if ck.ok else 1)


if __name__ == "__main__":
    main()
