# -*- coding: utf-8 -*-
"""
连拍鸟种统一单测 / Unit tests for burst species unification.

规则（用户 2026-09-17 拍板）：同一连拍组内，以达到置信度阈值且置信度最高的那张的鸟种
为整组鸟种；组内有鸟但未达阈值或未识别的帧也继承该鸟种；组内无一达阈值则不改。
前四个用例取自用户硬盘上的真实连拍（report.db 只读统计）。

Rule (decided by the user on 2026-09-17): within a burst, the species of the
highest-confidence frame at or above the threshold becomes the burst's species;
frames with a bird but below threshold or unidentified inherit it; bursts with
no frame at the threshold are left untouched. The first four cases come from
real bursts on the user's drive.
"""
from core.burst_species import BirdIdOutcome, reconcile_burst_species

THRESHOLD = 60.0


def outcome(cn: str, conf: float, en: str = "") -> BirdIdOutcome:
    """构造识鸟结果 / Build an outcome."""
    return BirdIdOutcome(cn_name=cn, en_name=en or cn, confidence=conf)


def names(final):
    """只取中文名 / Keep Chinese names only."""
    return {k: v.cn_name for k, v in final.items()}


def test_real_burst_63_highest_single_frame_wins():
    """09-12 #63：尖尾滨鹬 81% 胜过两张阔嘴鹬 79%/75%，无鸟种帧也继承。"""
    burst = {"_Z9W1665": 63, "_Z9W1666": 63, "_Z9W1667": 63, "_Z9W1668": 63}
    outcomes = {
        "_Z9W1665": outcome("尖尾滨鹬", 81),
        "_Z9W1667": outcome("阔嘴鹬", 79),
        "_Z9W1668": outcome("阔嘴鹬", 75),
    }
    final, summaries = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {k: "尖尾滨鹬" for k in burst}
    assert all(v.confidence == 81 for v in final.values())
    assert len(summaries) == 1
    s = summaries[0]
    assert (s.group_id, s.winner_prefix, s.winner.cn_name) == (63, "_Z9W1665", "尖尾滨鹬")
    assert sorted(s.changed) == ["_Z9W1666", "_Z9W1667", "_Z9W1668"]
    assert s.previous["_Z9W1667"].cn_name == "阔嘴鹬" and s.previous["_Z9W1666"] is None


def test_real_burst_182():
    """08-31 #182：双斑草雀 99% 胜过七彩文鸟 76%/87%。"""
    burst = {"_Z9W0910": 182, "_Z9W0911": 182, "_Z9W0912": 182, "_Z9W0913": 182}
    outcomes = {
        "_Z9W0910": outcome("双斑草雀", 99),
        "_Z9W0911": outcome("七彩文鸟", 76),
        "_Z9W0912": outcome("七彩文鸟", 87),
    }
    final, _ = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {k: "双斑草雀" for k in burst}


def test_real_burst_183():
    """08-31 #183：双斑草雀 95% 胜过三张七彩文鸟 83–91%。"""
    burst = {"_Z9W0934": 183, "_Z9W0935": 183, "_Z9W0936": 183, "_Z9W0937": 183}
    outcomes = {
        "_Z9W0934": outcome("双斑草雀", 95),
        "_Z9W0935": outcome("七彩文鸟", 83),
        "_Z9W0936": outcome("七彩文鸟", 91),
        "_Z9W0937": outcome("七彩文鸟", 88),
    }
    final, _ = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {k: "双斑草雀" for k in burst}


def test_real_burst_178():
    """08-31 #178：双斑草雀 99% 胜过三张白耳草雀 94–97%。"""
    burst = {"_Z9W0981": 178, "_Z9W0982": 178, "_Z9W0983": 178, "_Z9W0984": 178}
    outcomes = {
        "_Z9W0981": outcome("白耳草雀", 94),
        "_Z9W0982": outcome("白耳草雀", 96),
        "_Z9W0983": outcome("白耳草雀", 97),
        "_Z9W0984": outcome("双斑草雀", 99),
    }
    final, _ = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {k: "双斑草雀" for k in burst}


def test_no_frame_at_threshold_leaves_burst_untouched():
    """组内无一达阈值：保持原样，不产生汇总 / No confident frame: untouched."""
    burst = {"a": 1, "b": 1, "c": 1}
    outcomes = {"a": outcome("甲", 55), "b": outcome("乙", 40)}
    final, summaries = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {"a": "甲", "b": "乙"}
    assert final["a"].confidence == 55
    assert summaries == []


def test_uniform_burst_produces_no_summary():
    """整组已一致且都达阈值：不改、不汇总 / Already consistent: no summary."""
    burst = {"a": 2, "b": 2}
    outcomes = {"a": outcome("甲", 90), "b": outcome("甲", 70)}
    final, summaries = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert final["b"].confidence == 70
    assert summaries == []


def test_low_confidence_same_species_is_promoted():
    """低于阈值但鸟种相同的帧升级为整组置信度 / Same species below threshold is promoted."""
    burst = {"a": 3, "b": 3}
    outcomes = {"a": outcome("甲", 90), "b": outcome("甲", 45)}
    final, summaries = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert final["b"].confidence == 90
    assert summaries[0].changed == ["b"]


def test_frames_without_bird_are_not_assigned():
    """不在有鸟集合里的帧不继承 / Frames without a bird do not inherit."""
    burst = {"a": 4, "nobird": 4}
    outcomes = {"a": outcome("甲", 90)}
    final, _ = reconcile_burst_species(burst, outcomes, {"a"}, THRESHOLD)
    assert names(final) == {"a": "甲"}


def test_non_burst_photos_unchanged_and_bursts_isolated():
    """非连拍照片不动；不同连拍组互不影响 / Non-burst untouched; groups isolated."""
    burst = {"a": 5, "b": 5, "c": 6, "d": 6}
    outcomes = {
        "solo": outcome("丙", 30),
        "a": outcome("甲", 90),
        "b": outcome("乙", 70),
        "c": outcome("乙", 65),
        "d": outcome("甲", 88),
    }
    final, summaries = reconcile_burst_species(burst, outcomes, set(burst) | {"solo"}, THRESHOLD)
    assert names(final) == {"solo": "丙", "a": "甲", "b": "甲", "c": "甲", "d": "甲"}
    assert final["solo"].confidence == 30
    assert sorted(s.group_id for s in summaries) == [5, 6]


def test_tie_breaks_on_prefix_order():
    """置信度并列时取文件名排序靠前者，结果可复现 / Ties resolve by prefix order."""
    burst = {"b": 7, "a": 7}
    outcomes = {"b": outcome("乙", 90), "a": outcome("甲", 90)}
    final, _ = reconcile_burst_species(burst, outcomes, set(burst), THRESHOLD)
    assert names(final) == {"a": "甲", "b": "甲"}


def test_species_level_fields_come_from_winner():
    """IUCN/罕见度/颜值取自胜出帧 / Species-level fields follow the winner."""
    burst = {"a": 8, "b": 8}
    win = BirdIdOutcome("甲", "A", 95, iucn_category="VU", gbif_rarity_100=80, aesthetic_index=70)
    lose = BirdIdOutcome("乙", "B", 70, iucn_category="LC", gbif_rarity_100=10, aesthetic_index=20)
    final, _ = reconcile_burst_species(burst, {"a": win, "b": lose}, set(burst), THRESHOLD)
    assert (final["b"].iucn_category, final["b"].gbif_rarity_100, final["b"].aesthetic_index) == ("VU", 80, 70)
    assert final["b"].en_name == "A"


def test_inputs_are_not_mutated():
    """不修改传入的结果对象 / Input outcomes are not mutated."""
    burst = {"a": 9, "b": 9}
    lose = outcome("乙", 70)
    reconcile_burst_species(burst, {"a": outcome("甲", 90), "b": lose}, set(burst), THRESHOLD)
    assert lose.cn_name == "乙" and lose.confidence == 70
