# -*- coding: utf-8 -*-
"""
连拍鸟种统一 / Burst species unification.

同一连拍组（前后帧间隔 ≤ 1000/FPS 毫秒）拍的是同一只鸟，但逐帧识鸟会出现
「3 张阔嘴鹬 + 1 张尖尾滨鹬」这类结论不一致，导致同一连拍被拆到两个鸟种目录、报告里
算成两种鸟。本模块在全部识鸟结果到齐后、按鸟种分配星级配额之前做一次统一。

规则（用户 2026-09-17 拍板）：
1. 组内达到置信度阈值、置信度最高的那一帧的鸟种作为整组鸟种（置信度并列时取文件名
   排序靠前者，保证结果可复现）；
2. 组内其余「有鸟」的帧——无论原来是别的鸟种、低于阈值还是没有识别结果——都统一为该
   鸟种，置信度记为胜出帧的置信度；
3. 组内没有任何一帧达到阈值时，整组保持原样。

选「单张最高」而非加权投票的依据：用户 1372 组真实连拍中规则分歧仅 4 组，且置信度
最高的帧往往也是星级最高、画面最清楚的帧；多鸟画面的拍摄角度被选中概率很小。

本模块不依赖 Qt / torch，便于单测。

Frames in one burst (consecutive shots ≤ 1000/FPS ms apart) show the same bird,
yet per-frame identification can disagree (e.g. three Broad-billed Sandpipers
and one Sharp-tailed Sandpiper), splitting a burst across species folders and
counting it as two species. This module unifies species once all Bird ID
results are in, before per-species star quotas are applied.

Rule (decided by the user on 2026-09-17): the species of the highest-confidence
frame at or above the threshold becomes the burst's species (ties resolve by
prefix order for reproducibility); every other frame with a bird — a different
species, below threshold, or unidentified — takes that species and the winner's
confidence; bursts with no frame at the threshold are left untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Set, Tuple


@dataclass(frozen=True)
class BirdIdOutcome:
    """
    单张照片的识鸟结论 / One photo's Bird ID outcome.

    属性 / Attributes:
        cn_name: 中文鸟名 / Chinese name.
        en_name: 英文鸟名 / English name.
        confidence: 置信度 0-100 / Confidence 0-100.
        iucn_category: IUCN 等级，可能为 None / IUCN category or None.
        gbif_rarity_100: GBIF 罕见度 0-100，可能为 None / GBIF rarity or None.
        aesthetic_index: 鸟种颜值 0-100，可能为 None / Species aesthetic index or None.
    """

    cn_name: str
    en_name: str
    confidence: float
    iucn_category: Optional[str] = None
    gbif_rarity_100: Optional[float] = None
    aesthetic_index: Optional[float] = None

    def same_species(self, other: "BirdIdOutcome") -> bool:
        """
        是否同一鸟种 / Whether both outcomes name the same species.

        参数 / Parameters:
            other (BirdIdOutcome): 另一结论 / The other outcome.

        返回 / Returns:
            bool: 中英文名都相同时为 True / True when both names match.
        """
        return self.cn_name == other.cn_name and self.en_name == other.en_name


@dataclass
class BurstUnification:
    """
    一个连拍组的统一记录，供日志与说明文字使用 / Record of one unified burst.

    属性 / Attributes:
        group_id: 连拍组编号 / Burst group id.
        winner_prefix: 胜出帧文件名前缀 / Winning frame prefix.
        winner: 胜出帧结论 / Winning outcome.
        changed: 被改写的帧前缀（已排序）/ Sorted prefixes that were rewritten.
        previous: 被改写帧的原结论，无结论为 None / Previous outcomes (None if absent).
    """

    group_id: int
    winner_prefix: str
    winner: BirdIdOutcome
    changed: List[str] = field(default_factory=list)
    previous: Dict[str, Optional[BirdIdOutcome]] = field(default_factory=dict)


def reconcile_burst_species(
    burst_of: Dict[str, int],
    outcomes: Dict[str, BirdIdOutcome],
    bird_prefixes: Iterable[str],
    threshold: float,
) -> Tuple[Dict[str, BirdIdOutcome], List[BurstUnification]]:
    """
    按连拍组统一鸟种 / Unify species per burst group.

    参数 / Parameters:
        burst_of (dict[str, int]): 文件名前缀 → 连拍组编号（≤0 视为不属于连拍）/
            Prefix to burst id (≤ 0 means not in a burst).
        outcomes (dict[str, BirdIdOutcome]): 前缀 → 识鸟结论（含低于阈值的）/
            Prefix to outcome, including below-threshold ones.
        bird_prefixes (Iterable[str]): 检测到鸟的照片前缀 / Prefixes where a bird was detected.
        threshold (float): 置信度阈值 0-100 / Confidence threshold 0-100.

    返回 / Returns:
        tuple: (最终结论字典——包含原有结论与继承得到的结论, 发生改写的连拍组记录列表) /
            (final outcomes, including inherited ones; list of rewritten bursts).
            输入对象不会被修改 / Inputs are not mutated.
    """
    final: Dict[str, BirdIdOutcome] = dict(outcomes)
    birds: Set[str] = set(bird_prefixes) | set(outcomes)

    groups: Dict[int, List[str]] = {}
    for prefix, group_id in burst_of.items():
        if group_id is not None and group_id > 0:
            groups.setdefault(int(group_id), []).append(prefix)

    summaries: List[BurstUnification] = []
    for group_id in sorted(groups):
        members = sorted(groups[group_id])
        confident = [p for p in members if p in outcomes and outcomes[p].confidence >= threshold]
        if not confident:
            continue
        # max() 在并列时返回首个元素；confident 按前缀排序，故并列取文件名靠前者
        # max() returns the first maximal item; confident is prefix-sorted, so ties go to the earliest prefix
        winner_prefix = max(confident, key=lambda p: outcomes[p].confidence)
        winner = outcomes[winner_prefix]

        record = BurstUnification(group_id=group_id, winner_prefix=winner_prefix, winner=winner)
        for prefix in members:
            if prefix == winner_prefix or prefix not in birds:
                continue
            current = outcomes.get(prefix)
            if (current is not None and current.same_species(winner)
                    and current.confidence >= threshold):
                continue
            final[prefix] = replace(winner)
            record.changed.append(prefix)
            record.previous[prefix] = current
        if record.changed:
            summaries.append(record)
    return final, summaries
