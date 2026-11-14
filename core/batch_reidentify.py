# -*- coding: utf-8 -*-
"""
多选重新识别的纯逻辑（#163）：默认选择、连拍冲突、按新鸟种分组。

多选若干张照片后逐张重新识别，结果**先列给用户确认**再写入（用户 2026-11-14 拍板：
一次改几十张、错了很难发现，所以不直接写）。每张各出各的结果，不再做连拍统一——
用户手动多选就是要逐张核对，再统一一次又会出现 #162 那种「看不出鸟种从哪来」。

但写入时有一条既有约束：改鸟种一律以**连拍组**为单位（core.rating_mover 的
merge_bird_species 按 burst_id 去重，一组照片同在一个鸟种目录里），组内未勾选的成员
也会一起改。所以同一连拍组的几张若被选成不同鸟种，后写的会把先写的覆盖——这里把这种
情况找出来，由界面拦住、让用户先改一致。

本模块不依赖 Qt，便于单测。

Pure logic for multi-photo re-identification (#163): default choices, burst
conflicts and grouping by target species. Results are reviewed before any
write; each photo keeps its own result. Species changes apply per burst group
(merge_bird_species dedupes by burst_id), so selected frames of one burst that
end up with different species would overwrite each other — those conflicts are
detected here and blocked in the UI.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Hashable, List, Optional, Tuple


@dataclass
class ReidRow:
    """
    一张照片的重新识别状态。

    属性:
    photo (Dict[str, Any]): 浏览器里的照片记录（就地引用，不复制）
    candidates (List[Dict[str, Any]]): 候选，形如 identify_result_to_bird_data 的输出
        （chinese_name / english_name / latin_name / confidence）
    choice (Optional[int]): 选中的候选下标；None 表示保持当前鸟种不变
    status (str): "pending" 待识别 / "done" 已识别 / "none" 没识别出鸟种 / "error" 出错
    error (str): 出错时的信息

    One photo's re-identification state; choice None keeps the current species.
    """
    photo: Dict[str, Any]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    choice: Optional[int] = None
    status: str = "pending"
    error: str = ""


def _norm(name: Optional[str]) -> str:
    """鸟名比较用的规范化 / normalize a species name for comparison."""
    return (name or "").strip().casefold()


def same_species(cn_a: Optional[str], en_a: Optional[str],
                 cn_b: Optional[str], en_b: Optional[str]) -> bool:
    """
    两组鸟名是否同一种：英文名都有时比英文名（中文名有繁简与别名差异），否则比中文名。

    参数:
    cn_a, en_a: 第一组中英文名
    cn_b, en_b: 第二组中英文名

    返回:
    bool: 是否同一种；两边都没有名字时为 True（都是「无鸟种」）

    Same species? Compare English names when both exist, else Chinese names.
    """
    if _norm(en_a) and _norm(en_b):
        return _norm(en_a) == _norm(en_b)
    return _norm(cn_a) == _norm(cn_b)


def current_names(photo: Dict[str, Any]) -> Tuple[str, str]:
    """照片当前的 (中文名, 英文名) / the photo's current (cn, en) names."""
    return ((photo.get("bird_species_cn") or "").strip(),
            (photo.get("bird_species_en") or "").strip())


def default_choice(candidates: List[Dict[str, Any]], photo: Dict[str, Any],
                   threshold: float) -> Optional[int]:
    """
    识别完成后的默认选择：第一候选达到识鸟阈值、且与当前鸟种不同时选它，否则保持不变。

    与选片阶段同一个阈值：低于阈值的结论选片时也不会被采用，这里同样不替用户做主；
    候选仍全部列出，用户可以自己选。

    参数:
    candidates (List[Dict[str, Any]]): 候选（按置信度降序）
    photo (Dict[str, Any]): 照片记录
    threshold (float): 识鸟置信度阈值（%）

    返回:
    Optional[int]: 默认选中的候选下标；None 表示保持不变

    Preselect the top candidate only when it clears the Bird ID threshold and
    differs from the current species; otherwise keep the current species.
    """
    if not candidates:
        return None
    top = candidates[0]
    try:
        confidence = float(top.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < float(threshold):
        return None
    cur_cn, cur_en = current_names(photo)
    if same_species(top.get("chinese_name"), top.get("english_name"), cur_cn, cur_en):
        return None
    return 0


def target_names(row: ReidRow) -> Tuple[str, str, str]:
    """
    这一行最终要写成的 (中文名, 英文名, 学名)；保持不变时是当前鸟种（学名为空）。

    The (cn, en, latin) this row ends up with; the current species when kept.
    """
    if row.choice is None or not (0 <= row.choice < len(row.candidates)):
        cn, en = current_names(row.photo)
        return cn, en, ""
    cand = row.candidates[row.choice]
    return ((cand.get("chinese_name") or "").strip(),
            (cand.get("english_name") or "").strip(),
            (cand.get("latin_name") or "").strip())


def is_change(row: ReidRow) -> bool:
    """这一行是否真的会改鸟种 / whether this row actually changes the species."""
    if row.choice is None:
        return False
    cn, en, _ = target_names(row)
    if not (cn or en):
        return False
    cur_cn, cur_en = current_names(row.photo)
    return not same_species(cn, en, cur_cn, cur_en)


def burst_key(photo: Dict[str, Any]) -> Optional[Hashable]:
    """
    连拍组键：(批次目录, burst_id)；不在连拍组里为 None。

    合并库里不同批次目录的 burst_id 各自从 1 编号，必须带上目录才不会串组。
    Burst key (batch dir, burst_id); burst ids restart per batch directory.
    """
    bid = photo.get("burst_id")
    if not bid:
        return None
    return (photo.get("_base_dir") or "", bid)


def find_burst_conflicts(rows: List[ReidRow]) -> List[List[ReidRow]]:
    """
    找出「同一连拍组里被选成不同鸟种」的行。

    只要组里有一张要改，整组都会被改成它（含保持不变的那几张和未勾选的成员），
    所以比较的是每行**最终鸟种**（保持不变 = 当前鸟种），而不只是要改的那几行。

    参数:
    rows (List[ReidRow]): 全部行

    返回:
    List[List[ReidRow]]: 每个冲突连拍组的行列表；没有冲突时为空

    Rows of one burst whose final species differ. Any change rewrites the
    whole burst, so kept rows count with their current species.
    """
    groups: Dict[Hashable, List[ReidRow]] = {}
    for row in rows:
        key = burst_key(row.photo)
        if key is not None:
            groups.setdefault(key, []).append(row)
    conflicts: List[List[ReidRow]] = []
    for members in groups.values():
        if len(members) < 2 or not any(is_change(r) for r in members):
            continue
        first_cn, first_en, _ = target_names(members[0])
        if any(not same_species(first_cn, first_en, *target_names(r)[:2])
               for r in members[1:]):
            conflicts.append(members)
    return conflicts


def build_plans(rows: List[ReidRow]) -> List[Tuple[List[Dict[str, Any]], str, str, str]]:
    """
    把要改的行按新鸟种分组，交给浏览器的 _execute_species_plans。

    参数:
    rows (List[ReidRow]): 全部行（保持不变的会被跳过）

    返回:
    List[Tuple[list, str, str, str]]: [(照片列表, 新中文名, 新英文名, 新学名)]，
        按首次出现的顺序

    Group the changing rows by target species for _execute_species_plans,
    in first-seen order.
    """
    plans: List[Tuple[List[Dict[str, Any]], str, str, str]] = []
    for row in rows:
        if not is_change(row):
            continue
        cn, en, latin = target_names(row)
        for photos, p_cn, p_en, _latin in plans:
            if same_species(cn, en, p_cn, p_en):
                photos.append(row.photo)
                break
        else:
            plans.append(([row.photo], cn, en, latin))
    return plans
