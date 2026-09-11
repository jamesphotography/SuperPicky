"""
GBIF 罕见度「属级 key 误匹配」修复的回归测试。

背景：构建 gbif_rarity_100 时，AviList 的新属组合（Ardea coromanda、
Tachyspiza spp.、Anarhynchus spp. 等）在 GBIF backbone 里不存在，
species/match 回退到 ``matchType=HIGHERRANK`` 只匹配到属甚至纲。构建脚本
未拒绝该回退，把属级 key 当 specieskey 存下，按 specieskey 过滤 CC 子集时
命中 0 条 → 百分位算法判为最罕见 → 78 个常见种被打成 100 分「传奇」。

Regression tests for the genus-level key mis-match repair: rows whose stored
specieskey was a genus/class key matched zero occurrences and were pinned at
100.0 "Legendary".
"""
import sqlite3
from pathlib import Path

import pytest

from core.rarity_tier import (
    HARDCODE_OVERRIDES,
    gbif_score_to_tier,
    get_score_override,
    tier_name,
)

DB_PATH = Path(__file__).parent / "birdid" / "data" / "bird_reference.sqlite"

# 修复覆盖的代表性物种：学名 → (英文名, 该给的最高档位索引)
# 这些都是各自分布区的常见/易见鸟，绝不该出现在「传奇」档(4)。
# Representative repaired species; none may sit in the "Legendary" tier.
REPAIRED_SAMPLES = [
    ("Ardea coromanda", "Eastern Cattle Egret", 0),
    ("Aerospiza tachiro", "African Goshawk", 2),
    ("Tachyspiza novaehollandiae", "Grey Goshawk", 2),
    ("Dicrurus divaricatus", "Glossy-backed Drongo", 0),
]


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.skip(f"数据库不存在 / DB missing: {DB_PATH}")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def _effective_score(conn, scientific_name):
    """
    取某学名的「生效罕见度」——先查 HARDCODE_OVERRIDES，未命中再读库。
    这条链路与 BirdDatabaseManager.get_gbif_rarity_by_scientific_name 一致。

    Return the effective rarity score, mirroring the production lookup order
    (manual override first, then the gbif_rarity_100 table).
    """
    override = get_score_override(scientific_name)
    if override is not None:
        return override
    row = conn.execute(
        "SELECT gbif_rarity_100 FROM gbif_rarity_100 WHERE scientific_name = ?",
        (scientific_name,),
    ).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("sci,en,max_tier", REPAIRED_SAMPLES)
def test_repaired_species_not_legendary(conn, sci, en, max_tier):
    """修复过的常见鸟不得再落在「传奇」档，且不得超过各自的合理档位上限。"""
    score = _effective_score(conn, sci)
    assert score is not None, f"{sci} ({en}) 在 gbif_rarity_100 表里查不到"
    tier = gbif_score_to_tier(score)
    assert tier is not None
    assert tier <= max_tier, (
        f"{sci} ({en}) 得分 {score} → 「{tier_name(tier)}」档，"
        f"超出合理上限「{tier_name(max_tier)}」"
    )


def test_cattle_egret_matches_its_split_sibling(conn):
    """
    东方牛背鹭与西方牛背鹭是同一合并种拆分而来，观察难度相当，
    档位必须一致——GBIF 记录滞留在旧合并名下不应让前者显得罕见。

    The two cattle egrets split from one lumped species; their tiers must agree.
    """
    eastern = _effective_score(conn, "Ardea coromanda")
    western = _effective_score(conn, "Ardea ibis")
    assert eastern is not None and western is not None
    assert gbif_score_to_tier(eastern) == gbif_score_to_tier(western), (
        f"东方牛背鹭 {eastern} 分 vs 西方牛背鹭 {western} 分，档位不一致"
    )


def test_repaired_rows_carry_counts_and_provenance(conn):
    """
    修复过的行必须同时满足：CC 计数 > 0、分数不再是满分 100、source 留有修复来源。
    三者缺一即说明修复被回退或数据被重建覆盖。

    Repaired rows must carry a positive count, a non-pinned score, and a
    provenance tag in `source`; a violation means the fix was reverted.
    """
    rows = conn.execute(
        "SELECT scientific_name, cc_combined_count, gbif_rarity_100, source "
        "FROM gbif_rarity_100 WHERE source LIKE 'GBIF live API%'"
    ).fetchall()
    assert len(rows) >= 74, f"修复记录只剩 {len(rows)} 条，疑似被覆盖"
    for sci, count, score, source in rows:
        assert count and count > 0, f"{sci} 修复后 CC 计数仍为 {count}"
        assert source, f"{sci} 缺 source 来源标记"
        # 满分 100 只有在计数确实极低时才合法（如 Tachyspiza princeps 仅 3 条）；
        # 若计数可观却仍是 100，说明分数没跟着新计数重算 = 修复只做了一半。
        # A 100.0 score is legitimate only alongside a genuinely tiny count.
        if score == 100.0:
            assert count < 500, (
                f"{sci} 计数 {count} 却仍是满分 100，分数未随新计数重算"
            )


def test_genus_key_no_longer_used_for_cattle_egret(conn):
    """
    属 Ardea 的 key 2480922 不得再被任何物种行当作 specieskey——
    它是本次 bug 的原始指纹（属级 key 冒充种级 key）。

    The genus-level key that triggered this bug must no longer appear as a
    specieskey on any row.
    """
    rows = conn.execute(
        "SELECT scientific_name FROM gbif_rarity_100 WHERE specieskey = 2480922"
    ).fetchall()
    assert not rows, f"属级 key 2480922 仍被使用: {[r[0] for r in rows]}"


def test_override_entries_are_well_formed():
    """HARDCODE_OVERRIDES 每条必须是 (0-100 分, 非空理由)，理由需含提议人+日期。"""
    for name, entry in HARDCODE_OVERRIDES.items():
        assert isinstance(entry, tuple) and len(entry) == 2, f"{name} 结构不对"
        score, reason = entry
        assert 0.0 <= float(score) <= 100.0, f"{name} 分数越界: {score}"
        assert reason and len(reason) > 20, f"{name} 缺少充分理由"
        assert "[" in reason and "]" in reason, f"{name} 理由缺提议人/日期署名"
