# -*- coding: utf-8 -*-
"""
判断照片的城市/省份/国家是 SuperPicky 写的分数，还是用户的真实地名（无 Qt 依赖）。

SuperPicky 把锐度、美学、对焦借用 XMP/IPTC 的城市、省份、国家存放（Lightroom 能按城市排序、
DxO 能在 IPTC 信息里显示）。照片里已有真实地名（带 GPS 的相机、在 Lightroom 里填过地点）时，
写入不能覆盖、重置不能清掉——所以先要分清哪些值是我们写的。

口径与 PhotoPicky ``ToolMetadata.isToolWritten`` 完全一致，两个软件对同一张照片的判断相同：
三个字段任一是纯数字，或国家是对焦词（BEST/GOOD/BAD/WORST，或旧版中文 精焦/合焦/失焦/偏移/脱焦），
整组就是工具写的。三个全空也视为可写——没有地名可保护。

Decide whether a photo's City/State/Country hold SuperPicky's scores or the
user's real place name (no Qt). SuperPicky stores sharpness, aesthetics and
focus there (Lightroom can sort by City, DxO shows it); a real place name must
never be overwritten on write or cleared on reset. Same rule as PhotoPicky's
``ToolMetadata.isToolWritten``: any numeric field, or a focus word as the
country, marks the whole group as tool-written; an all-empty group is writable.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

# 对焦状态写进「国家」的值：新版英文，旧版中文 / focus values written as Country (new English, legacy Chinese)
FOCUS_WORDS: frozenset = frozenset({
    "BEST", "GOOD", "BAD", "WORST",
    "精焦", "合焦", "失焦", "偏移", "脱焦",
})

# 照片已有真实地名时不写的 item 键（对应城市/省份/国家）/ item keys skipped for a real place name
SCORE_ITEM_KEYS: Tuple[str, ...] = ("sharpness", "nima_score", "focus_status")

# 照片已有真实地名时，重置不清的参数 / reset arguments kept off a photo with a real place name
LOCATION_RESET_ARGS: frozenset = frozenset({
    "-XMP:City=", "-IPTC:City=",
    "-XMP:State=", "-IPTC:Province-State=",
    "-XMP:Country=", "-IPTC:Country-PrimaryLocationName=",
})

# 读取现有地点的 ExifTool 参数；-G1 让 XMP 与 IPTC 的同名字段分开 / -G1 keeps XMP and IPTC apart
LOCATION_READ_ARGS: List[str] = [
    "-G1",
    "-XMP-photoshop:City", "-XMP-photoshop:State", "-XMP-photoshop:Country",
    "-IPTC:City", "-IPTC:Province-State", "-IPTC:Country-PrimaryLocationName",
]

_GROUP_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("XMP-photoshop:City", "XMP-photoshop:State", "XMP-photoshop:Country"),
    ("IPTC:City", "IPTC:Province-State", "IPTC:Country-PrimaryLocationName"),
)


def is_number(s: str) -> bool:
    """
    是否为纯数字（ASCII 数字，至多一个小数点，整数部分非空），与 PhotoPicky ``isNumber`` 一致。

    参数:
    s (str): 字段值

    返回:
    bool: 纯数字为 True

    Whether s is a plain number (ASCII digits, at most one dot, non-empty
    integer part), matching PhotoPicky's ``isNumber``.
    """
    t = (s or "").strip()
    if not t:
        return False
    parts = t.split(".")
    if len(parts) > 2 or not parts[0]:
        return False
    return all(p.isascii() and (p.isdigit() or (p == "" and len(parts) == 1)) for p in parts)


def is_tool_written(city: str, state: str, country: str) -> bool:
    """
    这一组城市/省份/国家是不是工具（SuperPicky）写的。

    参数:
    city (str): 城市
    state (str): 省份
    country (str): 国家

    返回:
    bool: 任一字段是纯数字，或国家是对焦词时为 True

    Whether this City/State/Country group was written by a tool (SuperPicky).
    """
    return (is_number(city) or is_number(state) or is_number(country)
            or (country or "").strip().upper() in FOCUS_WORDS
            or (country or "").strip() in FOCUS_WORDS)


def groups_from_exiftool_json(data: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """
    把 ``-G1 -json`` 读出的一条记录拆成 [XMP 组, IPTC 组]；缺失为空串，非字符串转 str。

    参数:
    data (Dict[str, Any]): ExifTool JSON 的一条记录（键如 "XMP-photoshop:City"）

    返回:
    List[Tuple[str, str, str]]: [(城市, 省份, 国家) × 2]

    Split one ``-G1 -json`` record into [XMP group, IPTC group].
    """
    groups = []
    for keys in _GROUP_KEYS:
        groups.append(tuple("" if data.get(k) is None else str(data.get(k)) for k in keys))
    return groups


def has_real_location(groups: Iterable[Tuple[str, str, str]]) -> bool:
    """
    是否有任一组是真实地名（非空，且不是工具写的）。

    参数:
    groups (Iterable[Tuple[str, str, str]]): 各来源的 (城市, 省份, 国家)

    返回:
    bool: 有真实地名为 True

    Whether any group holds a real place name (non-empty, not tool-written).
    """
    for city, state, country in groups:
        if not any((v or "").strip() for v in (city, state, country)):
            continue
        if not is_tool_written(city, state, country):
            return True
    return False
