# -*- coding: utf-8 -*-
"""
识鸟国家下拉的「常用国家」（#143）/ Bird-ID country shortlist (#143).

默认就是原来那 11 个国家；本机所在国（操作系统的地区设置，不联网）不在其中时排在最前。
用户可以调整：在「更多国家」里选的新国家自动加到最前，编辑框里可移除或恢复默认。
存储为 None 表示从没调整过、一直跟随默认（默认以后改了也会跟着变）。

The shortlist defaults to the original 11 countries, with the user's own
country (OS region, no network) first when it is not among them. Users can
adjust it: a new country picked in "More countries" is added to the front,
and the editor removes entries or restores the defaults. A stored value of
None means "never edited, follow the defaults".
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, Iterable, List, Optional

#: 默认常用国家（即原来写死的 11 个）/ Default shortlist (the original 11)
DEFAULT_SHORTLIST: tuple = ("AU", "BR", "CN", "GB", "HK", "ID", "JP", "MY", "SG", "TW", "US")

#: 常用国家最多几个 / Maximum shortlist size
MAX_SHORTLIST = 20

#: 有专门译名的国家（其余用地区数据里的名字）/ Countries with dedicated translations
COUNTRY_I18N_KEYS: Dict[str, str] = {
    "AU": "birdid.country_au", "BR": "birdid.country_br", "CN": "birdid.country_cn",
    "GB": "birdid.country_gb", "HK": "birdid.country_hk", "ID": "birdid.country_id",
    "JP": "birdid.country_jp", "MY": "birdid.country_my", "SG": "birdid.country_sg",
    "TW": "birdid.country_tw", "US": "birdid.country_us",
}

#: 下拉里不是具体国家的特殊项 / Non-country combo entries
_NON_COUNTRY = {None, "", "GLOBAL", "MORE", "SEP1", "SEP2"}


def _is_country(code: Optional[str]) -> bool:
    """是否为可记录的具体国家代码 / Whether the code is a real, recordable country."""
    return isinstance(code, str) and code not in _NON_COUNTRY


def _dedupe(codes: Iterable[Optional[str]]) -> List[str]:
    """去重、去掉非国家项，保持顺序 / Dedupe and drop non-countries, keeping order."""
    out: List[str] = []
    for code in codes:
        if _is_country(code) and code not in out:
            out.append(code)  # type: ignore[arg-type]
    return out


def effective_shortlist(stored: Optional[List[str]], home: Optional[str]) -> List[str]:
    """
    实际显示的常用国家：没调整过（stored 为 None）时用默认 11 国，本机国家不在其中则排最前；
    调整过就按用户的列表（可以是空的）。

    参数:
    stored (list[str] or None): 配置里存的常用国家，None 表示从没调整过
    home (str or None): 本机所在国（系统地区），未知为 None

    返回:
    list[str]: 去重后的国家代码

    The shortlist actually shown: the defaults (home first when not among
    them) if never edited, otherwise the user's own list (possibly empty).
    """
    if stored is None:
        return _dedupe([home, *DEFAULT_SHORTLIST])
    return _dedupe(stored)


def add_country(shortlist: List[str], code: Optional[str]) -> List[str]:
    """
    把新选的国家加到最前；已在列表里的不挪位置（免得常用顺序被打乱）；非国家项不加。
    Add a newly picked country to the front; existing entries keep their place.
    """
    if not _is_country(code) or code in shortlist:
        return list(shortlist)
    return _dedupe([code, *shortlist])[:MAX_SHORTLIST]


def remove_country(shortlist: List[str], code: str) -> List[str]:
    """从常用国家里移除一个 / Remove a country from the shortlist."""
    return [c for c in shortlist if c != code]


def country_display_name(code: str, t: Callable[[str], str], is_english: bool,
                         code_to_region: Dict[str, dict]) -> Optional[str]:
    """
    国家显示名：有专门译名用译名，否则用地区数据的英文 / 中文名；地区数据里也没有返回 None。
    Display name: dedicated translation, else the region data's name; None if unknown.
    """
    key = COUNTRY_I18N_KEYS.get(code)
    if key:
        return t(key)
    region = code_to_region.get(code)
    if not region:
        return None
    if is_english:
        return region.get("name", code)
    return region.get("name_cn") or region.get("name", code)


def build_country_entries(t: Callable[[str], str], is_english: bool, regions_data: dict,
                          shortlist: List[str]) -> "OrderedDict[str, Optional[str]]":
    """
    组装国家下拉的 {显示名 → 代码}：自动（GPS）/ 全球 / 分隔 / 短名单 / 分隔 / 更多国家。
    设置中心与识鸟面板共用，两处顺序一致。

    参数:
    t: 取译文的函数（i18n.t）
    is_english (bool): 是否英文界面
    regions_data (dict): load_regions_data() 的结果
    shortlist (list[str]): 常用国家（effective_shortlist 的结果）

    返回:
    OrderedDict: 显示名 → 国家代码（自动为 None，分隔符为 SEP1 / SEP2，更多为 MORE）

    Build the combo's {display name → code}: Auto / Global / sep / shortlist /
    sep / More. Shared by the Settings Center and the Bird-ID dock.
    """
    code_to_region = {r.get("code"): r for r in regions_data.get("countries", [])}
    entries: "OrderedDict[str, Optional[str]]" = OrderedDict()
    entries[t("birdid.country_auto_gps")] = None
    entries[t("birdid.country_global")] = "GLOBAL"
    entries["─" * 15] = "SEP1"
    for code in shortlist:
        name = country_display_name(code, t, is_english, code_to_region)
        if name and name not in entries:
            entries[name] = code
    entries["─" * 15 + " "] = "SEP2"
    entries[t("birdid.country_more")] = "MORE"
    return entries


def system_country_code() -> Optional[str]:
    """
    操作系统地区设置里的国家代码（如 AU、CN）；拿不到返回 None。不联网。
    The country code from the OS region setting (e.g. AU, CN); None if unknown. No network.
    """
    try:
        from PySide6.QtCore import QLocale
        locale = QLocale.system()
        code = QLocale.territoryToCode(locale.territory())
        return code.upper() if code and len(code) == 2 else None
    except Exception:
        return None
