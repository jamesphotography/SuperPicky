# -*- coding: utf-8 -*-
"""
识鸟国家下拉的「常用国家」短名单（#143）/ Bird-ID country shortlist (#143).

此前设置中心与识鸟面板各自写死 11 个默认国家。现在短名单 = 本机所在国（操作系统的地区设置）
+ 最近选过的国家（最新在前、最多 MAX_RECENT 个）；两者都没有时才回退 DEFAULT_SHORTLIST。
本机所在国用系统地区而不是按 IP 查：不联网、不把 IP 交给第三方，各地网络状况也不影响。

Previously the Settings Center and the Bird-ID dock each hard-coded 11
countries. The shortlist is now: the user's own country (OS region setting)
plus recently picked countries (newest first, at most MAX_RECENT), falling
back to DEFAULT_SHORTLIST only when both are empty. The OS region is used
instead of IP geolocation: no network call, no IP sent to a third party.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, Iterable, List, Optional

#: 旧版写死的默认国家，仅在既无本机国家也无最近记录时使用 / Legacy defaults, fallback only
DEFAULT_SHORTLIST: tuple = ("AU", "BR", "CN", "GB", "HK", "ID", "JP", "MY", "SG", "TW", "US")

#: 最近选过的国家最多记几个 / How many recent picks to keep
MAX_RECENT = 10

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


def shortlist_codes(recent: List[str], home: Optional[str]) -> List[str]:
    """
    短名单的国家代码顺序：本机国家在前，其后最近选过的；都没有时回退默认。

    参数:
    recent (list[str]): 最近选过的国家（最新在前）
    home (str or None): 本机所在国（系统地区），未知为 None

    返回:
    list[str]: 去重后的国家代码

    Home country first, then recent picks; defaults only when both are empty.
    """
    codes = _dedupe([home, *recent])
    return codes if codes else list(DEFAULT_SHORTLIST)


def record_country(recent: List[str], code: Optional[str]) -> List[str]:
    """
    记下一次选择：移到最前、超出 MAX_RECENT 的丢掉；非国家项（自动 / 全球等）不记。
    Record a pick: move it to the front, cap at MAX_RECENT; non-countries are ignored.
    """
    if not _is_country(code):
        return list(recent)
    return _dedupe([code, *recent])[:MAX_RECENT]


def remove_country(recent: List[str], code: str) -> List[str]:
    """从最近记录里移除一个国家 / Remove a country from the recent list."""
    return [c for c in recent if c != code]


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
                          recent: List[str], home: Optional[str]) -> "OrderedDict[str, Optional[str]]":
    """
    组装国家下拉的 {显示名 → 代码}：自动（GPS）/ 全球 / 分隔 / 短名单 / 分隔 / 更多国家。
    设置中心与识鸟面板共用，两处顺序一致。

    参数:
    t: 取译文的函数（i18n.t）
    is_english (bool): 是否英文界面
    regions_data (dict): load_regions_data() 的结果
    recent (list[str]): 最近选过的国家
    home (str or None): 本机所在国

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
    for code in shortlist_codes(recent, home):
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
