# -*- coding: utf-8 -*-
"""
简体 → 台湾正体（繁体 TW）转换。

背景：程序内部的中文数据一律是简体——数据库 report.db 的 bird_species_cn、
识鸟模型输出、代码里写死的档名/模板等。繁体 TW 界面只在「输出边界」转换：
界面显示、写进照片的元数据（标题/关键词/题注）、生成的文件夹名。内部数据不改，
这样筛选、改鸟种、复原等以简体为键的逻辑都不受影响。

两类转换：
- 鸟名：用识鸟参考库 bird_reference.sqlite 里的 chinese_traditional（IOC 台湾叫法，
  如 白头鹎→白頭翁、黑脸琵鹭→黑面琵鷺），**不能**逐字转换；库里查不到的
  （用户手填的名字等）才退回逐字转换。
- 其他文字：用 OpenCC s2twp 词典压缩成的 locales/zh_tw_convert.json
  （由 scripts_dev/build_zh_tw_dict.py 生成）做「词组优先、逐字兜底」的转换。

Simplified → Taiwan Traditional conversion. Internal Chinese data stays
Simplified; conversion happens only at output boundaries (UI, metadata written
to photos, folder names) when the interface language is zh_TW. Species names
use the IOC Taiwan names stored in bird_reference.sqlite; other text uses a
compact OpenCC-derived table.
"""

import functools
import json
import os
import sqlite3
import sys
import threading
from typing import Dict, List, Optional, Tuple

_TABLE = None          # 转换表缓存 / cached conversion table
_TW_NAMES = None       # 简体鸟名 → 台湾鸟名 / simplified → Taiwan species name
_LOCK = threading.Lock()


def _resource_path(*parts: str) -> str:
    """
    定位随程序打包的资源文件（开发环境与 PyInstaller 打包均可）。

    参数:
    *parts (str): 相对项目根的路径片段

    返回:
    str: 绝对路径

    Locate a bundled resource for both dev runs and frozen builds.
    """
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def _load_table() -> dict:
    """
    读取转换表（只读一次）。缺文件时返回空表，转换退化为原样返回。

    返回:
    dict: 含 chars / phrases / tw_phrases / tw_variants / post_fixes 的表，
          另附各词典的最长词长 max_*。

    Load the conversion table once; an empty table makes conversion a no-op.
    """
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    with _LOCK:
        if _TABLE is not None:
            return _TABLE
        table = {"chars": {}, "phrases": {}, "tw_phrases": {}, "tw_variants": {}, "post_fixes": []}
        try:
            with open(_resource_path("locales", "zh_tw_convert.json"), "r", encoding="utf-8") as f:
                table.update(json.load(f))
        except (OSError, ValueError):
            pass
        table["max_phrase"] = max((len(k) for k in table["phrases"]), default=1)
        table["max_tw"] = max((len(k) for k in table["tw_phrases"]), default=1)
        _TABLE = table
        return table


def _longest_match(text: str, mapping: Dict[str, str], max_len: int,
                   fallback: Optional[Dict[str, str]] = None) -> str:
    """
    正向最长匹配替换：每个位置先找 mapping 里最长的词，找不到就按 fallback 逐字转。

    参数:
    text (str): 输入文字
    mapping (dict): 词组表
    max_len (int): 词组表最长词长
    fallback (dict | None): 单字表；None 表示单字原样保留

    返回:
    str: 转换结果

    Forward maximum matching over ``mapping``; unmatched characters go through
    ``fallback`` (or are kept as-is).
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        for size in range(min(max_len, n - i), 1, -1):
            hit = mapping.get(text[i:i + size])
            if hit is not None:
                out.append(hit)
                i += size
                break
        else:
            ch = text[i]
            hit = mapping.get(ch)
            if hit is None and fallback is not None:
                hit = fallback.get(ch)
            out.append(hit if hit is not None else ch)
            i += 1
    return "".join(out)


def to_taiwan(text: str) -> str:
    """
    把简体中文转成台湾正体（含台湾惯用词，如 信息→資訊、文件夹→資料夾）。

    参数:
    text (str): 简体文字；非字符串或空串原样返回

    返回:
    str: 台湾正体文字

    Convert Simplified Chinese text to Taiwan Traditional (with Taiwan phrasing).
    """
    if not text or not isinstance(text, str):
        return text
    table = _load_table()
    out = _longest_match(text, table["phrases"], table["max_phrase"], table["chars"])
    out = _longest_match(out, table["tw_phrases"], table["max_tw"])
    variants = table["tw_variants"]
    if variants:
        out = "".join(variants.get(c, c) for c in out)
    for old, new in table["post_fixes"]:
        out = out.replace(old, new)
    return out


def current_language() -> str:
    """
    当前界面语言代码（zh_CN / zh_TW / en_US）。取不到时按简体中文处理。

    Current interface language code; defaults to zh_CN when unavailable.
    """
    try:
        from tools.i18n import get_i18n
        return str(get_i18n().current_lang or "zh_CN")
    except Exception:
        return "zh_CN"


def is_taiwan(lang: Optional[str] = None) -> bool:
    """
    是否为繁体 TW 界面。

    参数:
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    bool: zh_TW 时为 True

    Whether the (given or current) language is Taiwan Traditional.
    """
    return (lang if lang is not None else current_language()) == "zh_TW"


def zh_text(text: str, lang: Optional[str] = None) -> str:
    """
    代码里写死的中文在繁体 TW 界面下转成台湾正体，其他语言原样返回。

    参数:
    text (str): 简体中文文字
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    str: 适合当前界面的中文

    Localize a hard-coded Simplified string for the zh_TW interface.
    """
    return to_taiwan(text) if is_taiwan(lang) else text


def _load_tw_names() -> Dict[str, str]:
    """
    从 bird_reference.sqlite 读取「简体鸟名 → 台湾鸟名」对照（只读一次）。

    返回:
    dict: 简体名 → 繁体名；库缺失时为空表

    Load the simplified → Taiwan species-name map from bird_reference.sqlite.
    """
    global _TW_NAMES
    if _TW_NAMES is not None:
        return _TW_NAMES
    with _LOCK:
        if _TW_NAMES is not None:
            return _TW_NAMES
        names: Dict[str, str] = {}
        db_path = _resource_path("birdid", "data", "bird_reference.sqlite")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    for cn, tw in conn.execute(
                            "SELECT chinese_simplified, chinese_traditional FROM BirdCountInfo"):
                        if cn and tw:
                            names[str(cn).strip()] = str(tw).strip()
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        _TW_NAMES = names
        return names


def taiwan_species_names() -> Dict[str, str]:
    """
    全部「简体鸟名 → 台湾鸟名」对照（只读视图，供目录识别等批量用途）。

    Full simplified → Taiwan species-name map (for folder recognizers etc.).
    """
    return dict(_load_tw_names())


def tw_species_name(cn_name: str) -> str:
    """
    简体鸟名 → 台湾鸟名。库里有的用 IOC 台湾叫法，没有的逐字转换。

    参数:
    cn_name (str): 简体中文鸟名

    返回:
    str: 台湾鸟名

    Simplified species name → Taiwan name (IOC Taiwan name, else converted).
    """
    if not cn_name or not isinstance(cn_name, str):
        return cn_name
    key = cn_name.strip()
    hit = _load_tw_names().get(key)
    return hit if hit else to_taiwan(key)


def species_cn(cn_name: str, lang: Optional[str] = None) -> str:
    """
    中文鸟名按界面语言输出：繁体 TW 界面给台湾鸟名，其他原样。

    参数:
    cn_name (str): 简体中文鸟名
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    str: 适合当前界面的中文鸟名

    Localize a Simplified species name for the (given or current) language.
    """
    return tw_species_name(cn_name) if is_taiwan(lang) else cn_name


def species_display(cn_name: Optional[str], en_name: Optional[str],
                    lang: Optional[str] = None) -> str:
    """
    按界面语言选鸟名：英文界面优先英文名，中文界面优先中文名（繁体 TW 用台湾鸟名），
    缺哪个就用另一个兜底。

    参数:
    cn_name (str | None): 简体中文鸟名
    en_name (str | None): 英文鸟名
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    str: 显示用鸟名；两者都缺时为空串

    Pick the species name for the interface language, falling back to the other.
    """
    lang = lang if lang is not None else current_language()
    if lang.startswith("en"):
        return en_name or (species_cn(cn_name, lang) if cn_name else "") or ""
    if cn_name:
        return species_cn(cn_name, lang)
    return en_name or ""


def species_folder_name(cn_name: Optional[str], en_name: Optional[str],
                        lang: Optional[str] = None) -> str:
    """
    鸟种目录名：英文界面用英文名（空格换下划线），中文界面用中文名（繁体 TW 用台湾鸟名）。
    不跨语言兜底，缺名时返回空串，由调用方决定退路。

    参数:
    cn_name (str | None): 简体中文鸟名
    en_name (str | None): 英文鸟名
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    str: 目录名；缺名时为空串

    Species folder name for the UI language (English uses underscores, zh_TW
    uses the Taiwan name). No cross-language fallback; empty when missing.
    """
    lang = lang if lang is not None else current_language()
    if lang.startswith("en"):
        return (en_name or "").strip().replace(" ", "_")
    return species_cn(cn_name.strip(), lang) if cn_name else ""


def tw_name_search(query: str, lang: Optional[str] = None) -> Tuple[str, List[str]]:
    """
    繁体 TW 界面下按台湾鸟名搜索：名称库只存简体名，用户却会输入台湾叫法
    （白頭翁、黑面琵鷺），这里把台湾名命中的鸟种换回简体名交给 SQL。

    参数:
    query (str): 用户输入
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    tuple: (精确命中时对应的简体名，否则原样的 query；台湾名包含 query 的全部简体名)。
           非繁体 TW 界面或输入不含汉字时为 (query, [])。

    Search by Taiwan species names in the zh_TW UI: the catalog stores only
    Simplified names, so Taiwan-name hits are mapped back to Simplified.
    Returns (exact Simplified match or the query itself, all Simplified names
    whose Taiwan name contains the query).
    """
    q = (query or "").strip()
    if not q or not is_taiwan(lang) or not any("一" <= c <= "鿿" for c in q):
        return query, []
    exact = query
    hits: List[str] = []
    for cn, tw in _load_tw_names().items():
        if q in tw:
            hits.append(cn)
            if tw == q:
                exact = cn
    return exact, hits



@functools.lru_cache(maxsize=None)
def _tw_display_name(cn_name: Optional[str]) -> str:
    """
    简体鸟名在繁体 TW 界面上显示成的名字（与 species_cn 同一规则，带缓存供 SQL 逐行调用）。

    Display name of a Simplified species name in the zh_TW UI (cached for SQL).
    """
    return tw_species_name(cn_name.strip()) if cn_name else ""


def tw_search_clause(query: str, conn: sqlite3.Connection,
                     lang: Optional[str] = None) -> Tuple[str, Tuple[str, ...], str]:
    """
    鸟名搜索在繁体 TW 界面下附加的 SQL 条件（改鸟种弹窗与鸟名查询共用）。

    按「界面上显示的名字」搜：把 简体名 → 繁体 TW 显示名 的转换注册成 SQLite 函数，
    条件为 sp_tw_name(chinese_name) LIKE %输入%。显示名的来源有两种——识鸟参考库的
    台湾鸟名（白头鹎→白頭翁），以及参考库没有时的逐字转换（识鸟模型未收录的
    北鵙雀鹟→北鵙雀鶲）。用同一个函数做显示和搜索，看到什么就能搜到什么，与名录
    版本、名录里有没有繁体列都无关（此前只认参考库台湾名，「北鵙雀鶲」搜不到）。

    参数:
    query (str): 用户输入
    conn (sqlite3.Connection): 执行搜索的连接（函数注册在它上面）
    lang (str | None): 语言代码；None 表示取当前界面语言

    返回:
    tuple: (追加到 WHERE 括号内的 SQL 片段，以 " OR " 开头；对应参数；
            精确命中台湾名时用于排序的简体名，否则为原 query)。非繁体界面为 ("", (), query)。

    Extra WHERE terms for species search in the zh_TW UI: match the displayed
    Taiwan name via a registered SQLite function, so whatever is shown can be
    searched regardless of catalog version.
    """
    if not is_taiwan(lang):
        return "", (), query
    q = (query or "").strip()
    if not q:
        return "", (), query
    conn.create_function("sp_tw_name", 1, _tw_display_name, deterministic=True)
    exact_cn, _ = tw_name_search(query, lang)
    return " OR sp_tw_name(chinese_name) LIKE ?", (f"%{q}%",), exact_cn
