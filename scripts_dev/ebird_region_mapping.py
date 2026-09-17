# -*- coding: utf-8 -*-
"""
eBird 物种代码 → 模型类别映射 / Map eBird species codes to model class ids.

旧版 ebird_classid_mapping.json 有 42 个类别被误映射为 ostric2、大量旧代码失配，
导致本地常见鸟被永久屏蔽（spec §2.2）。本模块按五个步骤逐级映射，并提供卡口函数，
让同类错误在构建期暴露而不是在用户手里静默发生。

The legacy mapping mis-mapped 42 classes to ostric2 and carried stale codes,
permanently masking common local birds (spec section 2.2). This module maps in
five ordered steps and exposes gate helpers so that class of error surfaces at
build time instead of silently in users' hands.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

# 不代表单一物种的记录类型：杂交、属级、「A/B」二选一、过渡型
# Record types that do not denote a single species.
EXCLUDED_CATEGORIES = frozenset({"hybrid", "spuh", "slash", "intergrade"})

# 词尾阴阳性变化：属名调整后种加词会随属的性改变词尾（concinna → concinnus）
# Gender endings: moving a species to a new genus can change the epithet ending.
_EPITHET_SUFFIXES = ("us", "um", "is", "ae", "a", "e", "i")
_MIN_STEM = 4


def normalize_to_species(codes: Iterable[str], taxonomy: Dict[str, dict]) -> Set[str]:
    """
    把区域清单归并到物种级 / Reduce a region list to species-level codes.

    参数 / Parameters:
        codes (Iterable[str]): 清单中的 speciesCode / Codes from a region list.
        taxonomy (dict): speciesCode → 分类条目 / Taxonomy entries by code.

    返回 / Returns:
        set[str]: 物种级 speciesCode；分类表里查不到的代码被忽略 /
            Species-level codes; codes missing from the taxonomy are ignored.
    """
    out: Set[str] = set()
    for code in codes:
        entry = taxonomy.get(code)
        if entry is None:
            continue
        category = entry.get("category")
        if category == "species":
            out.add(code)
            continue
        if category in EXCLUDED_CATEGORIES:
            continue
        target = entry.get("reportAs")
        if target and taxonomy.get(target, {}).get("category") == "species":
            out.add(target)
    return out


def epithet_stem(sci_name: str) -> str:
    """
    种加词去掉阴阳性词尾后的词干 / Epithet stem without gender endings.

    参数 / Parameters:
        sci_name (str): 学名 / Scientific name.

    返回 / Returns:
        str: 小写词干 / Lower-case stem.
    """
    epithet = sci_name.strip().split()[-1].lower()
    for suffix in _EPITHET_SUFFIXES:
        if epithet.endswith(suffix) and len(epithet) - len(suffix) >= _MIN_STEM:
            return epithet[: -len(suffix)]
    return epithet


@dataclass
class ModelIndex:
    """
    模型类别的各种查找索引 / Lookup indexes over model classes.

    属性 / Attributes:
        by_cornell: cornell_code → 类别集合 / cornell code to classes.
        by_sci: 模型学名 → 类别集合 / model scientific name to classes.
        by_gbif_key: GBIF specieskey → 类别集合 / GBIF key to classes.
        class_sci: 类别 → 模型学名 / class to model scientific name.
        genus_family: IOC 属 → 科，作 eBird 属科表的兜底 / IOC genus to family fallback.
    """

    by_cornell: Dict[str, Set[int]] = field(default_factory=dict)
    by_sci: Dict[str, Set[int]] = field(default_factory=dict)
    by_gbif_key: Dict[int, Set[int]] = field(default_factory=dict)
    class_sci: Dict[int, str] = field(default_factory=dict)
    genus_family: Dict[str, str] = field(default_factory=dict)


def load_model_index(ref_db_path: str) -> ModelIndex:
    """
    从 bird_reference.sqlite 读取映射所需索引 / Load indexes from bird_reference.sqlite.

    参数 / Parameters:
        ref_db_path (str): 参考库路径 / Reference database path.

    返回 / Returns:
        ModelIndex: 索引集合 / The indexes.

    异常 / Exceptions:
        sqlite3.Error: 表缺失或库损坏时抛出 / Raised when tables are missing.
    """
    index = ModelIndex()
    conn = sqlite3.connect(ref_db_path)
    try:
        by_cornell: Dict[str, Set[int]] = defaultdict(set)
        for code, cls in conn.execute(
            "SELECT cornell_code, model_class_id FROM avilist_map "
            "WHERE model_class_id IS NOT NULL AND cornell_code IS NOT NULL AND cornell_code != ''"
        ):
            by_cornell[str(code)].add(int(cls))
        by_sci: Dict[str, Set[int]] = defaultdict(set)
        for sci, cls in conn.execute(
            "SELECT scientific_name, model_class_id FROM BirdCountInfo "
            "WHERE model_class_id IS NOT NULL AND scientific_name IS NOT NULL"
        ):
            by_sci[str(sci)].add(int(cls))
            index.class_sci[int(cls)] = str(sci)
        by_key: Dict[int, Set[int]] = defaultdict(set)
        for key, cls in conn.execute(
            "SELECT specieskey, model_class_id FROM gbif_rarity_100 "
            "WHERE specieskey IS NOT NULL AND model_class_id IS NOT NULL"
        ):
            by_key[int(key)].add(int(cls))
        for genus, family in conn.execute(
            "SELECT genus, family_scientific FROM bird_ioc "
            "WHERE genus IS NOT NULL AND family_scientific IS NOT NULL"
        ):
            index.genus_family.setdefault(str(genus), str(family))
    finally:
        conn.close()
    index.by_cornell = dict(by_cornell)
    index.by_sci = dict(by_sci)
    index.by_gbif_key = dict(by_key)
    return index


def parse_gbif_v2_match(payload: dict) -> Optional[int]:
    """
    从 GBIF v2 species match 响应取可用的物种 key / Extract a usable species key.

    只接受物种级的精确/模糊匹配；同物异名返回其 accepted key。

    Only species-rank exact/fuzzy/variant matches are accepted; synonyms return
    their accepted key.

    参数 / Parameters:
        payload (dict): `GET /v2/species/match` 的 JSON / Response JSON.

    返回 / Returns:
        Optional[int]: 物种 key；不可用时 None / Species key or None.
    """
    diagnostics = payload.get("diagnostics") or {}
    if diagnostics.get("matchType") not in ("EXACT", "FUZZY", "VARIANT"):
        return None
    usage = payload.get("usage") or {}
    if str(usage.get("rank", "")).upper() != "SPECIES":
        return None
    accepted = payload.get("acceptedUsage") or {}
    key = accepted.get("key") or usage.get("key")
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


@dataclass
class SpeciesMapping:
    """
    映射结果 / Mapping result.

    属性 / Attributes:
        classes: 已映射的 speciesCode → 类别集合 / Mapped code to classes.
        steps: speciesCode → 命中步骤名 / Code to the step that mapped it.
        unmapped: 未映射的物种代码（已排序）/ Sorted unmapped codes.
    """

    classes: Dict[str, Set[int]]
    steps: Dict[str, str]
    unmapped: List[str]


def build_species_mapping(
    taxonomy: Dict[str, dict],
    index: ModelIndex,
    resolve_gbif: Callable[[str], Optional[int]],
    overrides: Dict[str, List[int]],
) -> SpeciesMapping:
    """
    对分类表中全部物种执行五步映射 / Run the five-step mapping over all species.

    映射对象是整个分类表而非某个清单：第五步需要知道哪些模型类别没被任何物种占用，
    只看单个清单会把别处已占用的类别误当成空闲。

    The whole taxonomy is mapped rather than one list: step five needs to know
    which model classes no species has claimed, and a single list would mistake
    classes claimed elsewhere for free ones.

    参数 / Parameters:
        taxonomy (dict): speciesCode → 分类条目 / Taxonomy by code.
        index (ModelIndex): 模型索引 / Model indexes.
        resolve_gbif (Callable): 学名 → GBIF 物种 key 或 None / Name to key or None.
        overrides (dict): 人工映射 speciesCode → 类别列表 / Manual mappings.

    返回 / Returns:
        SpeciesMapping: 映射结果 / The mapping.
    """
    classes: Dict[str, Set[int]] = {}
    steps: Dict[str, str] = {}
    species = sorted(c for c, e in taxonomy.items() if e.get("category") == "species")

    pending: List[str] = []
    for code in species:
        sci = taxonomy[code].get("sciName", "")
        if code in overrides:
            classes[code] = {int(c) for c in overrides[code]}
            steps[code] = "override"
            continue
        hit = index.by_cornell.get(code)
        step = "cornell"
        if not hit:
            hit = index.by_sci.get(sci)
            step = "sci"
        if hit:
            classes[code] = set(hit)
            steps[code] = step
        else:
            pending.append(code)

    still: List[str] = []
    for code in pending:
        key = resolve_gbif(taxonomy[code].get("sciName", ""))
        hit = index.by_gbif_key.get(key) if key is not None else None
        if hit:
            classes[code] = set(hit)
            steps[code] = "gbif"
        else:
            still.append(code)

    genus_family: Dict[str, str] = {}
    for entry in taxonomy.values():
        family = entry.get("familySciName")
        sci = entry.get("sciName") or ""
        if family and sci:
            genus_family.setdefault(sci.split()[0], family)

    def model_family(cls: int) -> Optional[str]:
        genus = index.class_sci[cls].split()[0]
        return genus_family.get(genus) or index.genus_family.get(genus)

    used: Set[int] = set()
    for cs in classes.values():
        used |= cs
    orphan_index: Dict[tuple, Set[int]] = defaultdict(set)
    for cls, sci in index.class_sci.items():
        if cls in used:
            continue
        family = model_family(cls)
        if family:
            orphan_index[(epithet_stem(sci), family)].add(cls)

    keys = {
        code: (epithet_stem(taxonomy[code].get("sciName", "")), taxonomy[code].get("familySciName"))
        for code in still
    }
    demand = Counter(keys.values())
    unmapped: List[str] = []
    for code in still:
        key = keys[code]
        candidates = orphan_index.get(key, set())
        if key[1] and len(candidates) == 1 and demand[key] == 1:
            classes[code] = set(candidates)
            steps[code] = "epithet"
        else:
            unmapped.append(code)
    return SpeciesMapping(classes=classes, steps=steps, unmapped=sorted(unmapped))


def classes_for_region(species_codes: Iterable[str], mapping: SpeciesMapping) -> Set[int]:
    """
    区域物种代码 → 模型类别并集 / Union of model classes for a region.

    参数 / Parameters:
        species_codes (Iterable[str]): 物种级代码 / Species-level codes.
        mapping (SpeciesMapping): 映射结果 / The mapping.

    返回 / Returns:
        set[int]: 类别集合 / Class ids.
    """
    out: Set[int] = set()
    for code in species_codes:
        out |= mapping.classes.get(code, set())
    return out


def find_overloaded_classes(
    mapping: SpeciesMapping, allow: Set[int], limit: int = 3
) -> Dict[int, List[str]]:
    """
    找出被映射到过多物种的类别 / Find classes mapped from too many species.

    旧版 42 个类别都被映射成 ostric2 这种「默认值污染」，在反方向表现为一个值被
    大量复用；这里检查类别侧的同类异常。

    The legacy ostric2 pollution shows up as one value reused many times; this
    checks for the same anomaly on the class side.

    参数 / Parameters:
        mapping (SpeciesMapping): 映射结果 / The mapping.
        allow (set[int]): 确认合理的类别白名单 / Classes confirmed as legitimate.
        limit (int): 允许的最大物种数 / Maximum species per class.

    返回 / Returns:
        dict[int, list[str]]: 超限类别 → 物种代码 / Offending class to codes.
    """
    by_class: Dict[int, List[str]] = defaultdict(list)
    for code, cs in mapping.classes.items():
        for cls in cs:
            by_class[cls].append(code)
    return {
        cls: sorted(codes)
        for cls, codes in by_class.items()
        if len(codes) > limit and cls not in allow
    }
