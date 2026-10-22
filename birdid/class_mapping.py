# -*- coding: utf-8 -*-
"""
外部识鸟模型的类别映射 / Class mapping for external bird-ID models.

SuperPicky 的鸟种库沿用 OSEA 的类别号（`BirdCountInfo.model_class_id`，0..10963）：报告库、纠错记录、
地区名录、罕见度都以它为键。外部模型（如 BirdTrace）有自己的类别表，这里把它们的输出下标映射到
这套类别号，并把概率累加过去（多对一时相加，对不上的丢弃），之后的取前 K 名、鸟名、地区过滤、
罕见度就和 OSEA 完全共用。

SuperPicky's species DB keeps OSEA's class ids (`BirdCountInfo.model_class_id`, 0..10963) as the key for
reports, corrections, region lists and rarity. External models (e.g. BirdTrace) have their own class lists;
this module maps their output indices onto those ids and sums probabilities across (many-to-one sums,
unmapped classes are dropped), so top-k, names, region filtering and rarity are shared with OSEA.
"""
import re
from typing import Dict, List, Optional, Tuple

import numpy as np

# 鸟种库的类别号上限（沿用 OSEA 输出头）。编号 ≥ 此值的新鸟种目前无法输出，见设计文档 §8。
# Upper bound of species-DB class ids (OSEA's output head). Ids at or above it cannot be output yet.
NUM_CLASSES = 10964

# eBird 新分类（拆分后的种）→ 鸟种库里拆分前那个种的学名。按 eBird 代码查，代码与学名都对不上时才用。
# New eBird splits -> scientific name of the pre-split species in our DB, keyed by eBird code.
SPLIT_OVERRIDES: Dict[str, str] = {
    "lessap2": "Charadrius mongolus",      # Siberian Sand-Plover -> 蒙古沙鸻
    "lessap1": "Charadrius mongolus",      # Tibetan Sand-Plover -> 蒙古沙鸻
    "integr2": "Ardea intermedia",         # Plumed Egret -> 中白鹭
    "souboo3": "Ninox boobook",            # Tasmanian Boobook -> 南鹰鸮
    "cresht2": "Falcunculus frontatus",    # Western Shrike-tit -> 鵙雀鹟
    "lohrai1": "Hypotaenidia sylvestris",  # Lord Howe Rail -> 豪岛秧鸡
    "norgos1": "Accipiter gentilis",       # Eurasian Goshawk -> 苍鹰
    "pacswa1": "Hirundo tahitica",         # Pacific Swallow -> 洋燕
    "olbsun2": "Cinnyris jugularis",       # Sahul Sunbird -> 黄腹花蜜鸟
    "strher1": "Butorides striata",        # Little Heron -> 绿鹭
    "pincoc1": "Lophochroa leadbeateri",   # Pink Cockatoo -> 彩冠凤头鹦鹉
    "eclpar4": "Eclectus roratus",         # Papuan Eclectus -> 红胁绿鹦鹉
    "whiter4": "Gygis alba",               # Blue-billed White-Tern -> 白燕鸥
}


def norm_name(name: Optional[str]) -> str:
    """
    英文名归一化：只保留小写字母（BirdTrace 去掉了撇号与连字符差异，Abbotts = Abbott's）。
    Normalise an English name to lower-case letters only (BirdTrace drops apostrophes).

    参数 / Parameters:
        name (Optional[str]): 英文名，可含下划线 / English name, underscores allowed.

    返回 / Returns:
        str: 小写纯字母；空值返回空串 / Letters only; "" for empty input.
    """
    return re.sub(r"[^a-z]", "", (name or "").lower())


def build_class_map(
    codes: List[str],
    taxa: Dict[str, Tuple[str, str]],
    by_code: Dict[str, int],
    by_sci: Dict[str, int],
    by_en: Optional[Dict[str, int]] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> List[int]:
    """
    外部模型输出下标 → 鸟种库类别号 / Map external-model output indices to species-DB class ids.

    顺序：拆分对照 → eBird 代码 → 学名 → 英文名（归一化后比较）；都对不上或越界记 -1。
    Order: split override, eBird code, scientific name, normalised English name; -1 when unmapped
    or out of range.

    参数 / Parameters:
        codes (list[str]): 按模型输出顺序的 eBird 代码（查不到代码时可放占位串）/ eBird codes in output order.
        taxa (dict): eBird 代码 → (英文名, 学名) / code -> (English name, scientific name).
        by_code (dict): eBird 代码 → 类别号 / eBird code -> class id.
        by_sci (dict): 学名 → 类别号 / scientific name -> class id.
        by_en (Optional[dict]): 归一化英文名 → 类别号 / normalised English name -> class id.
        overrides (Optional[dict]): eBird 代码 → 鸟种库学名；None 用 SPLIT_OVERRIDES / code -> DB sci name.

    返回 / Returns:
        list[int]: 与 codes 等长 / Same length as codes.
    """
    overrides = SPLIT_OVERRIDES if overrides is None else overrides
    by_en = by_en or {}
    out: List[int] = []
    for code in codes:
        en, sci = taxa.get(code, ("", ""))
        target = overrides.get(code)
        if target and target in by_sci:
            cid = by_sci[target]
        elif code in by_code:
            cid = by_code[code]
        elif sci in by_sci:
            cid = by_sci[sci]
        else:
            cid = by_en.get(norm_name(en), -1)
        out.append(cid if 0 <= cid < NUM_CLASSES else -1)
    return out


def aggregate_probs(model_probs: np.ndarray, class_map: List[int]) -> np.ndarray:
    """
    外部模型概率累加到鸟种库类别（多对一相加，对不上的丢弃）/ Sum probabilities onto species-DB classes.

    参数 / Parameters:
        model_probs (np.ndarray): 外部模型 softmax 概率，长度同 class_map / probabilities.
        class_map (list[int]): build_class_map 的结果 / Output of build_class_map.

    返回 / Returns:
        np.ndarray: (NUM_CLASSES,) 概率 / probabilities over species-DB classes.

    异常 / Raises:
        ValueError: 概率长度与对照表不一致 / Length mismatch.
    """
    if len(model_probs) != len(class_map):
        raise ValueError(f"类别数不一致 / length mismatch: {len(model_probs)} vs {len(class_map)}")
    cols = np.asarray(class_map)
    src = np.flatnonzero(cols >= 0)
    out = np.zeros(NUM_CLASSES, dtype=np.float64)
    np.add.at(out, cols[src], np.asarray(model_probs, dtype=np.float64)[src])
    return out
