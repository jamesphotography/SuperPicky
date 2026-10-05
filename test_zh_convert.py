# -*- coding: utf-8 -*-
"""
繁体 TW（台湾正体）界面的转换与语言识别测试。

覆盖：
- 通用文字转换（OpenCC 词组 + 台湾用词修正，如 權限/選取/資料夾/偵測）
- 鸟名用 IOC 台湾叫法而非逐字转换（白头鹎 → 白頭翁）
- 目录名与显示名按语言选取
- 台湾鸟名反查简体名（鸟名搜索）
- 系统语言标记识别为繁体
- 三个语言包键一一对应，且繁体包不残留常见大陆用词

Tests for the zh_TW (Taiwan Traditional) interface: text conversion, Taiwan
species names, folder/display names, Taiwan-name search, locale detection and
language-pack parity.
"""

import json
import os

import pytest

from tools import zh_convert
from tools.i18n import SUPPORTED_LANGUAGES, _is_traditional_tag

ROOT = os.path.dirname(os.path.abspath(__file__))


def _flatten(d: dict, prefix: str = "") -> dict:
    """
    把嵌套语言包展平成「section.key → 值」。

    参数:
    d (dict): 语言包
    prefix (str): 键前缀

    返回:
    dict: 展平后的键值

    Flatten a nested language pack into dotted keys.
    """
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _load_pack(lang: str) -> dict:
    """读取一个语言包并展平 / Load and flatten one language pack."""
    with open(os.path.join(ROOT, "locales", f"{lang}.json"), "r", encoding="utf-8") as f:
        return _flatten(json.load(f))


@pytest.mark.parametrize("src, expected", [
    ("恢复文件", "恢復檔案"),          # 词组优先：不能被「复文」截走成「恢覆」
    ("当前选中的照片", "目前選取的照片"),
    ("权限", "權限"),                  # OpenCC 原样会转成「許可權」
    ("识别鸟种", "辨識鳥種"),
    ("移入回收站", "移入垃圾桶"),
    ("目录", "資料夾"),
    ("根目录", "根目錄"),
    ("检测到飞鸟", "偵測到飛鳥"),
    ("视频设置", "影片設定"),
    ("澳大利亚", "澳洲"),
])
def test_to_taiwan_phrases(src: str, expected: str) -> None:
    """通用文字按台湾用词转换 / General text uses Taiwan wording."""
    assert zh_convert.to_taiwan(src) == expected


def test_to_taiwan_passthrough() -> None:
    """空串、非字符串原样返回 / Empty and non-string inputs pass through."""
    assert zh_convert.to_taiwan("") == ""
    assert zh_convert.to_taiwan(None) is None


@pytest.mark.parametrize("cn, tw", [
    ("白头鹎", "白頭翁"),      # 台湾叫法与大陆名字形转换不同
    ("黑脸琵鹭", "黑面琵鷺"),
    ("红嘴黑鹎", "紅嘴黑鵯"),
])
def test_taiwan_species_names(cn: str, tw: str) -> None:
    """鸟名用识鸟库的台湾叫法 / Species names use the Taiwan names from the DB."""
    if not zh_convert.taiwan_species_names():
        pytest.skip("bird_reference.sqlite 不可用 / reference DB unavailable")
    assert zh_convert.tw_species_name(cn) == tw


def test_unknown_species_falls_back_to_char_conversion() -> None:
    """库里没有的名字逐字转换 / Unknown names fall back to per-char conversion."""
    assert zh_convert.tw_species_name("某某鸟") == "某某鳥"


def test_species_cn_only_converts_in_taiwan_ui() -> None:
    """只有 zh_TW 转换，简体与英文界面原样 / Only zh_TW converts."""
    assert zh_convert.species_cn("黑脸琵鹭", "zh_CN") == "黑脸琵鹭"
    assert zh_convert.species_cn("黑脸琵鹭", "en_US") == "黑脸琵鹭"
    assert zh_convert.zh_text("设置", "zh_CN") == "设置"
    assert zh_convert.zh_text("设置", "zh_TW") == "設定"


def test_species_display_by_language() -> None:
    """显示名按语言取并互相兜底 / Display names follow the language with fallback."""
    assert zh_convert.species_display("家燕", "Barn Swallow", "en_US") == "Barn Swallow"
    assert zh_convert.species_display("家燕", "Barn Swallow", "zh_CN") == "家燕"
    assert zh_convert.species_display("", "Barn Swallow", "zh_TW") == "Barn Swallow"
    assert zh_convert.species_display("家燕", "", "en_US") == "家燕"


def test_species_folder_name_no_cross_language_fallback() -> None:
    """目录名：英文空格换下划线、不跨语言兜底 / Folder names never cross languages."""
    assert zh_convert.species_folder_name("家燕", "Barn Swallow", "en_US") == "Barn_Swallow"
    assert zh_convert.species_folder_name("家燕", "Barn Swallow", "zh_CN") == "家燕"
    assert zh_convert.species_folder_name("家燕", "", "en_US") == ""
    assert zh_convert.species_folder_name("", "Barn Swallow", "zh_TW") == ""


def test_tw_name_search_maps_back_to_simplified() -> None:
    """台湾名搜索换回库里的简体名 / Taiwan-name search maps to Simplified names."""
    if not zh_convert.taiwan_species_names():
        pytest.skip("bird_reference.sqlite 不可用 / reference DB unavailable")
    exact, hits = zh_convert.tw_name_search("白頭翁", "zh_TW")
    assert exact == "白头鹎"
    assert "白头鹎" in hits
    # 非繁体界面、或输入不含汉字时不做任何事
    assert zh_convert.tw_name_search("白頭翁", "zh_CN") == ("白頭翁", [])
    assert zh_convert.tw_name_search("bulbul", "zh_TW") == ("bulbul", [])


@pytest.mark.parametrize("tag, expected", [
    ("zh_TW.UTF-8", True),
    ("zh-Hant-TW", True),
    ("zh_HK", True),
    ("Chinese (Traditional)_Taiwan", True),
    ("zh_CN.UTF-8", False),
    ("zh-Hans-CN", False),
    ("en_US.UTF-8", False),
    ("", False),
])
def test_traditional_locale_tags(tag: str, expected: bool) -> None:
    """系统语言标记识别繁体 / Detect Traditional Chinese locale tags."""
    assert _is_traditional_tag(tag) is expected


def test_language_packs_have_identical_keys() -> None:
    """三个语言包键一一对应 / All language packs share the same keys."""
    packs = {lang: _load_pack(lang) for lang in SUPPORTED_LANGUAGES}
    base = set(packs["zh_CN"])
    for lang, pack in packs.items():
        assert set(pack) == base, f"{lang} 键不一致 / key mismatch"


def test_language_packs_keep_placeholders() -> None:
    """繁体包的占位符与简体一致，避免 format 时 KeyError / Placeholders match."""
    import string
    cn, tw = _load_pack("zh_CN"), _load_pack("zh_TW")

    def fields(text):
        try:
            # 只比 ASCII 占位符：「{鸟种}」这类字面量花括号不参与 format
            # Only ASCII fields; literal braces like「{鸟种}」are never formatted.
            return sorted(f for _, f, _, _ in string.Formatter().parse(str(text))
                          if f and f.isascii())
        except ValueError:
            return None

    bad = [k for k in cn if fields(cn[k]) != fields(tw.get(k, ""))]
    assert not bad, bad[:10]


def test_taiwan_pack_has_no_mainland_terms() -> None:
    """繁体包不残留已修正过的大陆用词 / No corrected mainland terms remain."""
    tw = _load_pack("zh_TW")
    banned = ["許可權", "回收站", "選中", "當前", "資源管理器", "後臺"]
    leftovers = {k: v for k, v in tw.items()
                 if isinstance(v, str) and any(b in v for b in banned)}
    assert not leftovers, list(leftovers.items())[:5]


def test_tw_search_clause_matches_displayed_names() -> None:
    """
    繁体界面按显示名搜：参考库台湾名（白頭翁）与逐字转换名（模型未收录的北鵙雀鶲）都搜得到；
    非繁体界面不加条件。
    In zh_TW, search matches displayed names (Taiwan names and converted ones).
    """
    import sqlite3

    if not zh_convert.taiwan_species_names():
        pytest.skip("bird_reference.sqlite 不可用 / reference DB unavailable")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE birds (chinese_name TEXT)")
    conn.executemany("INSERT INTO birds VALUES (?)", [("白头鹎",), ("北鵙雀鹟",), ("家燕",)])

    def search(q: str, lang: str) -> list:
        clause, params, _ = zh_convert.tw_search_clause(q, conn, lang)
        sql = f"SELECT chinese_name FROM birds WHERE (chinese_name LIKE ?{clause})"
        return [r[0] for r in conn.execute(sql, (f"%{q}%", *params))]

    assert search("白頭翁", "zh_TW") == ["白头鹎"]
    assert search("北鵙雀鶲", "zh_TW") == ["北鵙雀鹟"]
    assert search("北鵙雀鶲", "zh_CN") == []
    assert zh_convert.tw_search_clause("白頭翁", conn, "en_US") == ("", (), "白頭翁")
