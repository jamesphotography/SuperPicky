#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 gbif_rarity_100 表里「学名匹配回退到属级」导致的假传奇分数。

背景 / Background
-----------------
构建 gbif_rarity_100 时，用 AviList 学名调 GBIF species/match API 取 specieskey。
近年 IOC/AviList 大量属级重组（Bubulcus→Ardea、Accipiter→Tachyspiza、
Charadrius→Anarhynchus 等），这些新组合在 GBIF backbone 里尚不存在，match API
返回 ``matchType=HIGHERRANK``——只匹配到**属**甚至**纲**。构建脚本没有拒绝这种
回退，把属级 key 当成 specieskey 存下；随后按 specieskey 过滤 CC0+CC-BY 子集时
命中 0 条，count=0 被百分位算法判为「最罕见」→ 100.0 分「传奇」。

典型受害者：Ardea coromanda（东方牛背鹭）specieskey=2480922 实为属 Ardea，
count=0 → 100.0 分，而它是亚洲/澳洲最常见的鹭之一。

The rarity table stored genus-level (or even class-level) GBIF keys whenever
species/match fell back to HIGHERRANK for a recently re-combined binomial.
Those keys match zero occurrences in the CC-licensed subset, so the percentile
scorer flagged 78 common species as "Legendary".

修复策略 / Strategy
-------------------
1. 找出 specieskey 实际 rank != SPECIES 的条目。
2. 重新解析正确的 GBIF speciesKey，双重校验以杜绝同名异种误配：
   - 主路径：用 AviList 英文名在 GBIF backbone 检索；
   - 备路径：种加词按拉丁性数变位枚举检索；
   - 判定：科名必须一致，且英文名精确吻合（STRONG）或 GBIF 无英文名可比但
     种加词词干一致（MEDIUM）；英文名冲突一律拒绝（进人工清单）。
3. 用正确 key 拉实时 CC0+CC-BY 计数，按快照/实时增长率归一后，在现有
   (count, score) 曲线上插值定分——保持与库内其余 10841 条同一标尺。

用法 / Usage
------------
    python3 scripts_dev/fix_gbif_rarity_keys.py resolve   # 只解析，出报告
    python3 scripts_dev/fix_gbif_rarity_keys.py apply     # 写库（先自动备份）
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

API_ROOT = "https://api.gbif.org/v1"
GBIF_BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"
AVES_KEY = 212

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "birdid" / "data" / "bird_reference.sqlite"
WORK_DIR = REPO_ROOT / "scripts_dev" / "_gbif_rarity_fix"

# CC0+CC-BY 子集自 2026-05-01 快照以来的整体增长率，由 9 个跨量级物种实测得出
# （+0.0% ~ +7.3%，中位 +5.6%）。用于把实时 API 计数归一回快照标尺，
# 使插值定分与库内既有分数同尺。
# Growth factor from the 2026-05-01 snapshot to live API counts, measured on a
# 9-species cross-magnitude sample; used to renormalise live counts.
SNAPSHOT_GROWTH = 1.056

# 拉丁语种加词词尾，用于跨属重组时的性数变位匹配
# Latin epithet endings, for gender-agreement variants across genus moves.
_LATIN_ENDINGS = ("us", "um", "a", "is", "e", "i", "ii", "ae", "os", "on", "er")


def _api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    调用 GBIF REST API 并返回解析后的 JSON。

    参数:
        path (str): API 路径，如 "/species/match"
        params (Optional[Dict[str, Any]]): 查询参数

    返回:
        Dict[str, Any]: 解析后的响应体

    异常:
        urllib.error.URLError / json.JSONDecodeError: 网络或解析失败时抛出

    Call the GBIF REST API and return the decoded JSON body.
    """
    url = API_ROOT + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def epithet_stem(scientific_name: str) -> str:
    """
    取学名的种加词并剥掉拉丁性数词尾，用于跨属变位比对。

    参数:
        scientific_name (str): 双名法学名，如 "Ardea coromanda"

    返回:
        str: 词干，如 "coromand"

    Strip the Latin gender ending off the specific epithet so that
    "coromanda" (Ardea) and "coromandus" (Bubulcus) compare equal.
    """
    parts = scientific_name.strip().split()
    if not parts:
        return ""
    epithet = re.sub(r"[^a-z]", "", parts[-1].lower())
    for ending in sorted(_LATIN_ENDINGS, key=len, reverse=True):
        if epithet.endswith(ending) and len(epithet) - len(ending) >= 4:
            return epithet[: -len(ending)]
    return epithet


def epithet_variants(stem: str) -> List[str]:
    """
    由词干枚举常见拉丁性数变位形式，供 GBIF 全文检索逐个试。

    参数:
        stem (str): epithet_stem() 产出的词干

    返回:
        List[str]: 候选种加词拼写列表

    Enumerate gender-agreement spellings of an epithet stem for lookup.
    """
    return [stem + e for e in ("us", "a", "um", "is", "e", "i", "or", "")]


def normalize_en(name: Optional[str]) -> str:
    """
    英文鸟名归一化：小写、连字符与空格等价、去非字母字符。

    参数:
        name (Optional[str]): 英文名，如 "Eastern Cattle-Egret"

    返回:
        str: 归一化结果，如 "easterncattleegret"；输入为空时返回 ""

    Normalise an English bird name so "Cattle-Egret" == "Cattle Egret".
    """
    if not name:
        return ""
    return re.sub(r"[^a-z]", "", name.lower())


def gbif_rank(key: int) -> Optional[str]:
    """
    查询某个 GBIF usage key 的分类阶元。

    参数:
        key (int): GBIF usage key

    返回:
        Optional[str]: 如 "SPECIES"/"GENUS"；查询失败返回 None

    Return the taxonomic rank of a GBIF usage key, or None on failure.
    """
    try:
        return _api_get(f"/species/{key}").get("rank")
    except Exception:
        return None


def gbif_english_names(key: int) -> List[str]:
    """
    取某个 GBIF 物种的英文俗名列表。

    参数:
        key (int): GBIF species key

    返回:
        List[str]: 英文俗名；无数据或失败时返回空列表

    Fetch the English vernacular names recorded for a GBIF species.
    """
    try:
        data = _api_get(f"/species/{key}/vernacularNames", {"limit": 100})
    except Exception:
        return []
    return [
        r["vernacularName"]
        for r in data.get("results", [])
        if r.get("language") == "eng" and r.get("vernacularName")
    ]


def _search_backbone(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    在 GBIF backbone 的鸟纲范围内检索 accepted 物种。

    参数:
        query (str): 检索词（学名片段或英文俗名）
        limit (int): 返回条数上限

    返回:
        List[Dict[str, Any]]: 候选物种记录；失败时返回空列表

    Search accepted bird species within the GBIF backbone dataset.
    """
    try:
        data = _api_get(
            "/species/search",
            {
                "q": query,
                "rank": "SPECIES",
                "status": "ACCEPTED",
                "highertaxonKey": AVES_KEY,
                "datasetKey": GBIF_BACKBONE,
                "limit": limit,
            },
        )
    except Exception:
        return []
    return data.get("results", [])


def resolve_species_key(
    scientific_name: str,
    family: Optional[str],
    english_names: Sequence[Optional[str]],
) -> Tuple[Optional[int], Optional[str], str, str]:
    """
    为一个 AviList 学名解析出正确的 GBIF speciesKey，带双重校验。

    校验规则（防同名异种误配）：
      - 科名必须一致，否则一律拒绝；
      - STRONG：英文名与 GBIF 俗名精确吻合 → 接受；
      - MEDIUM：GBIF 无英文俗名可比，但种加词词干一致 → 接受并标记；
      - CONFLICT：双方都有英文名但对不上 → 拒绝，交人工。

    参数:
        scientific_name (str): AviList 学名，如 "Ardea coromanda"
        family (Optional[str]): AviList 科名，如 "Ardeidae"
        english_names (Sequence[Optional[str]]): AviList/Clements/BirdLife 英文名

    返回:
        Tuple[Optional[int], Optional[str], str, str]:
            (speciesKey, GBIF 学名, 置信度标签, 说明)
            解析失败时 speciesKey 与学名为 None。

    Resolve the correct GBIF speciesKey for an AviList binomial, rejecting
    same-epithet different-species collisions via family + vernacular checks.
    """
    stem = epithet_stem(scientific_name)
    want_en = {normalize_en(n) for n in english_names if n}
    candidates: Dict[int, Dict[str, Any]] = {}

    # 主路径：英文俗名检索 / Primary: search by English vernacular name.
    for en in [n for n in english_names if n]:
        for rec in _search_backbone(en, limit=10):
            if rec.get("key"):
                candidates.setdefault(rec["key"], rec)

    # 备路径：种加词变位检索 / Fallback: epithet gender variants.
    for variant in epithet_variants(stem):
        if not variant:
            continue
        for rec in _search_backbone(variant, limit=30):
            if rec.get("key"):
                candidates.setdefault(rec["key"], rec)

    scored: List[Tuple[int, int, str, str]] = []
    conflicts: List[str] = []
    for key, rec in candidates.items():
        canonical = rec.get("canonicalName") or ""
        if len(canonical.split()) != 2:
            continue
        if family and (rec.get("family") or "").lower() != family.lower():
            continue
        stem_ok = epithet_stem(canonical) == stem
        gbif_en = {normalize_en(n) for n in gbif_english_names(key)}
        en_hit = bool(want_en & gbif_en)

        if en_hit:
            scored.append((100 if stem_ok else 80, key, canonical, "STRONG"))
        elif stem_ok and not gbif_en and family:
            # MEDIUM 必须有科名做过滤器。AviList 缺 family 时，仅凭种加词词干
            # 会把 Pezoporus flaviventris（鹦鹉）配到 Cyclarhis flaviventris
            # （绿鵙）这种跨目错配上——实测踩到过，故此处强制要求 family。
            # MEDIUM requires a family filter: without it, epithet-only matching
            # produced cross-order collisions in testing.
            scored.append((50, key, canonical, "MEDIUM"))
        elif stem_ok and gbif_en and want_en:
            conflicts.append(f"{canonical}(en={sorted(gbif_en)[:2]})")

    if not scored:
        note = "英文名冲突: " + "; ".join(conflicts) if conflicts else "无候选"
        return None, None, "UNRESOLVED", note

    scored.sort(reverse=True)
    _, key, canonical, level = scored[0]
    return key, canonical, level, f"候选 {len(scored)} 个"


def cc_count(species_key: int) -> Optional[int]:
    """
    取某物种在 GBIF CC0 + CC-BY 4.0 子集下的实时记录数。

    参数:
        species_key (int): GBIF species key

    返回:
        Optional[int]: 记录数；查询失败返回 None

    Live CC0 + CC-BY 4.0 occurrence count for a GBIF species.
    """
    total = 0
    for lic in ("CC0_1_0", "CC_BY_4_0"):
        try:
            data = _api_get(
                "/occurrence/search",
                {"speciesKey": species_key, "license": lic, "limit": 0},
            )
        except Exception:
            return None
        total += int(data.get("count", 0))
    return total


def build_score_curve(conn: sqlite3.Connection) -> List[Tuple[int, float]]:
    """
    从库内既有条目提取 (count, score) 曲线，作为插值定分的标尺。

    参数:
        conn (sqlite3.Connection): 已打开的数据库连接

    返回:
        List[Tuple[int, float]]: 按 count 升序排列的 (count, score) 对

    Extract the existing (count, score) curve used as the interpolation ruler.
    """
    rows = conn.execute(
        "SELECT cc_combined_count, gbif_rarity_100 FROM gbif_rarity_100 "
        "WHERE cc_combined_count > 0 AND gbif_rarity_100 IS NOT NULL "
        "ORDER BY cc_combined_count"
    ).fetchall()
    return [(int(c), float(s)) for c, s in rows]


def interpolate_score(curve: List[Tuple[int, float]], count: int) -> float:
    """
    在既有 (count, score) 曲线上线性插值，为新 count 定分。

    参数:
        curve (List[Tuple[int, float]]): build_score_curve() 的产出
        count (int): 归一化后的 CC 记录数

    返回:
        float: 0-100 罕见度分数，保留两位小数

    Linearly interpolate a rarity score for a count against the existing curve,
    keeping new scores on the same scale as the other 10841 rows.
    """
    if count <= curve[0][0]:
        return curve[0][1]
    if count >= curve[-1][0]:
        return curve[-1][1]
    lo, hi = 0, len(curve) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if curve[mid][0] <= count:
            lo = mid
        else:
            hi = mid
    c0, s0 = curve[lo]
    c1, s1 = curve[hi]
    if c1 == c0:
        return round(s0, 2)
    ratio = (count - c0) / (c1 - c0)
    return round(s0 + (s1 - s0) * ratio, 2)


def load_broken_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    找出 specieskey 实际不是种级、且因此被打成满分 100 的条目。

    只取 gbif_rarity_100 = 100.0 的行。另有 44 行同样是属级 key + count=0，
    但最终分数合理（来源是构建期另一条未回写 count 的路径）——重算会把
    Charadrius falklandicus 这类从 25.73 抬到 80.88，反而更差，故一律不动。

    参数:
        conn (sqlite3.Connection): 已打开的数据库连接

    返回:
        List[Dict[str, Any]]: 每条含 model_class_id / 学名 / 旧 key / 旧分 /
                              科名 / 三套英文名 / GBIF 实际 rank

    Collect rows whose stored specieskey is not species-rank AND which ended up
    pinned at 100.0. Rows with a plausible score are left untouched: recomputing
    them measurably degrades their scores.
    """
    # avilist_map 对少数条目缺英文名与科名（7 个受害者全部如此），故再从
    # BirdCountInfo 取英文名、从 bird_ioc 取科名兜底，否则它们无从解析。
    # avilist_map lacks vernacular/family for a handful of rows; fall back to
    # BirdCountInfo and bird_ioc so those can still be resolved.
    rows = conn.execute(
        """
        SELECT g.model_class_id, g.scientific_name, g.specieskey,
               g.cc_combined_count, g.gbif_rarity_100,
               COALESCE(a.family, i.family_scientific),
               a.en_name_avilist, a.en_name_clements, a.en_name_birdlife,
               b.english_name
        FROM gbif_rarity_100 g
        LEFT JOIN avilist_map a ON a.model_class_id = g.model_class_id
        LEFT JOIN BirdCountInfo b ON b.model_class_id = g.model_class_id
        LEFT JOIN bird_ioc i ON i.scientific_name = g.scientific_name
        WHERE g.cc_combined_count = 0 AND g.gbif_rarity_100 = 100.0
        """
    ).fetchall()

    keys = sorted({r[2] for r in rows if r[2] is not None})
    with ThreadPoolExecutor(max_workers=10) as pool:
        ranks = dict(zip(keys, pool.map(gbif_rank, keys)))

    out: List[Dict[str, Any]] = []
    for (cid, sci, key, cnt, score, fam, en_a, en_c, en_b, en_bci) in rows:
        rank = ranks.get(key)
        if rank == "SPECIES":
            continue
        names = [n for n in (en_a, en_c, en_b, en_bci) if n]
        out.append(
            {
                "model_class_id": cid,
                "scientific_name": sci,
                "old_key": key,
                "old_rank": rank,
                "old_count": cnt,
                "old_score": score,
                "family": fam,
                "english_names": list(dict.fromkeys(names)),
            }
        )
    return out


def stage_resolve() -> None:
    """
    解析阶段：重查正确 key、拉计数、算新分，输出报告 JSON，不写库。

    Resolve stage — re-match keys, fetch counts, compute scores, write a
    report for human review without touching the database.
    """
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    curve = build_score_curve(conn)

    broken = load_broken_rows(conn)
    print(f"发现 specieskey 非种级的条目: {len(broken)} 个", flush=True)

    def work(item: Dict[str, Any]) -> Dict[str, Any]:
        key, canonical, level, note = resolve_species_key(
            item["scientific_name"], item["family"], item["english_names"]
        )
        item["new_key"] = key
        item["new_name"] = canonical
        item["confidence"] = level
        item["note"] = note
        if key is not None:
            live = cc_count(key)
            item["live_count"] = live
            if live is not None:
                normalized = int(round(live / SNAPSHOT_GROWTH))
                item["normalized_count"] = normalized
                item["new_score"] = interpolate_score(curve, normalized)
            else:
                item["new_score"] = None
        else:
            item["live_count"] = None
            item["new_score"] = None
        return item

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(work, broken))

    conn.close()
    out_path = WORK_DIR / "resolve_report.json"
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    from collections import Counter

    print("置信度分布:", Counter(r["confidence"] for r in results))
    resolved = [r for r in results if r.get("new_score") is not None]
    print(f"可自动定分: {len(resolved)}/{len(results)}")
    print(f"报告写入: {out_path}")


def stage_apply() -> None:
    """
    写库阶段：把 STRONG/MEDIUM 置信度的解析结果写回 gbif_rarity_100，
    写前自动备份数据库文件。

    Apply stage — write STRONG/MEDIUM resolutions back into gbif_rarity_100
    after taking a timestamped backup of the database file.
    """
    report_path = WORK_DIR / "resolve_report.json"
    if not report_path.exists():
        sys.exit("请先运行 resolve 阶段 / Run the resolve stage first")
    results = json.loads(report_path.read_text(encoding="utf-8"))

    writable = [
        r
        for r in results
        if r.get("new_key") and r.get("new_score") is not None
        and r.get("confidence") in ("STRONG", "MEDIUM")
    ]
    if not writable:
        sys.exit("没有可写入的条目 / Nothing to write")

    # 备份写到 WORK_DIR（scripts_dev/ 已在 .gitignore 中），避免 36MB 二进制
    # 落在 birdid/data/ 下被误提交进版本库。
    # Back up under WORK_DIR — scripts_dev/ is gitignored, so the 36 MB blob
    # cannot be accidentally staged alongside the repaired database.
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = WORK_DIR / f"bird_reference.sqlite.bak-{stamp}"
    shutil.copy2(DB_PATH, backup)
    print(f"数据库已备份到 / Backed up to: {backup}")

    conn = sqlite3.connect(DB_PATH)
    proxy_count = 0
    with conn:
        for r in writable:
            # 种加词不一致 = 英文名命中的是拆分母种（如 Dicrurus divaricatus
            # 借 D. adsimilis 的合并计数），或纯学名变更（Apteryx maxima →
            # haastii）。两者分数都可用，但来源要在 source 里留痕以便审计。
            # A differing epithet means the vernacular matched a split parent or
            # a renamed binomial; flag it in `source` so the row stays auditable.
            is_proxy = epithet_stem(r["scientific_name"]) != epithet_stem(r["new_name"])
            proxy_count += int(is_proxy)
            source = (
                f"GBIF live API via {r['new_name']} (split/rename proxy)"
                if is_proxy
                else "GBIF live API (higherrank key repair)"
            )
            conn.execute(
                "UPDATE gbif_rarity_100 SET specieskey = ?, cc_combined_count = ?, "
                "gbif_rarity_100 = ?, source = ? WHERE model_class_id = ?",
                (
                    r["new_key"],
                    r["normalized_count"],
                    r["new_score"],
                    source,
                    r["model_class_id"],
                ),
            )
    conn.close()
    print(f"已更新 {len(writable)} 条 / Updated {len(writable)} rows"
          f"（其中 {proxy_count} 条为拆分/改名代理 / split-rename proxies）")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "resolve"
    if stage == "resolve":
        stage_resolve()
    elif stage == "apply":
        stage_apply()
    else:
        sys.exit(f"未知阶段 / Unknown stage: {stage}")
