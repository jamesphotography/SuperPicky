#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建中文鸟名的带声调拼音表 / Build the toned-pinyin table for Chinese bird names.

产物：`ioc/pinyin_toned.json`，形如 ``{"黑喉小䴙䴘": "hēi hóu xiǎo pì tī", ...}``，
约 11400 条、400KB。运行时由 `tools/pinyin_names.py` 读取，**不带任何依赖**——
pypinyin 只在本脚本（构建期）用到，不进 `requirements_runtime_*`。

为什么不直接用 `ioc/birdname.db` 自带的 `pinyin_name`：
    那一列没有声调（"hei hou xiao pi ti"），而拼音的价值在发音。

为什么不直接信 pypinyin：
    鸟名里多音字密集，两份数据**互有对错**。2026-09-19 对全库 11388 个鸟名做过
    交叉比对：分歧 655 个，但只收敛成 25 个字。下面的裁定表把两边的错都按掉，
    裁定依据是主流鸟类文献（《中国鸟类野外手册》《中国鸟类分类与分布名录》）
    与《现代汉语词典》的规范读音。

用法 / Usage:
    python3 -m scripts_dev.build_pinyin_toned            # 写 ioc/pinyin_toned.json
    python3 -m scripts_dev.build_pinyin_toned --check    # 只比对不写盘

Produces the toned-pinyin lookup shipped to the app. pypinyin is a build-time
dependency only; the runtime reads the plain JSON.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unicodedata
from typing import Dict, List, Tuple

# 带声调的元音，用于校验产物真的带了调号
# Toned vowels, used to verify the output actually carries tone marks.
TONE_MARKS = "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ"

# 鸟名的两个来源，(相对仓库的路径, 表名, 中文名列)。
#
# 必须两个都收：界面上的鸟名来自不同的库——鸟名查询面板与改鸟种弹窗读名录库，
# 详情面板与识鸟结果卡片显示的是识鸟结果（经 report.db，名字来自识鸟库）。
# 只从名录库生成时，识鸟库独有的一千多个鸟种在详情面板里拼音是空的，而且
# 不报错，没人会发现。
# Both sources are required: the two databases feed different parts of the UI,
# and building from only one left ~1000 species silently without pinyin.
NAME_SOURCES = (
    (os.path.join("ioc", "birdname.db"), "birds", "chinese_name"),
    (os.path.join("birdid", "data", "bird_reference.sqlite"),
     "BirdCountInfo", "chinese_simplified"),
)

# 识鸟库里有 19 条鸟名以 ``*`` 开头（存疑名的标记）。标记不是名字的一部分，
# 不剥掉的话界面上会显示「* běi měi wū yā」。半角全角都处理。
# Leading asterisks mark uncertain names in the reference database; they are
# not part of the name and must not reach the pinyin.
_UNCERTAIN_MARKS = "*＊"

# ----------------------------------------------------------------------
#  人工裁定表 / Hand-adjudicated overrides
#
#  改这两张表就能改读音，改完重跑本脚本即可——这正是产物选 JSON 而不是往
#  7.8MB 的 birdname.db 加列的原因：一个字的读音修正是一行 diff。
#  Edit these two tables and re-run; that is why the artifact is plain JSON.
# ----------------------------------------------------------------------

#: 词级裁定。**必须按词**的情形：单字覆盖救不了，或需要反过来压住单字覆盖。
#: pypinyin 的词组优先级高于单字，所以这张表也是压住其内置词组的唯一手段。
PHRASE_OVERRIDES: Dict[str, List[List[str]]] = {
    # 全库 196 个含「长」的鸟名里，只有「酋长鹂」这 11 个读 zhǎng，
    # 其余（长尾/长嘴/长冠/长耳/长趾/长翅/长脚/长爪）全是 cháng。
    # 所以单字压 cháng、再用本条把「酋长」捞回来。
    "酋长": [["qiú"], ["zhǎng"]],
    # 绿林戴胜是「绿色林地」的鸟，不是「绿林好汉」；pypinyin 内置词组给 lù lín。
    "绿林": [["lǜ"], ["lín"]],
    # 「南无须小霸鹟」= 南 + 无须；pypinyin 把「南无」当佛号读 nā mó。
    "南无": [["nán"], ["wú"]],
    # Chachapoyas 音译；pypinyin 内置叠词「查查」给 zhā zhā。
    "查查": [["chá"], ["chá"]],
    # 「强嘴䴕雀」指嘴强健；pypinyin 内置词组「强嘴」给 jiàng zuǐ。
    "强嘴": [["qiáng"], ["zuǐ"]],
    # Honduras 规范译音为 hóng dū lā sī；pypinyin 内置词组给 dōu。
    "洪都拉斯": [["hóng"], ["dū"], ["lā"], ["sī"]],
}

#: 单字裁定。左列是字，右列是在鸟名语境中的唯一读音。
#: 每条都已在全库里核对过没有反例（例如「秘」不在表内，因为 37 个「秘鲁」读 bì
#: 而「隐秘树鹃」读 mì，pypinyin 两者本来就都对）。
SINGLE_OVERRIDES: Dict[str, str] = {
    # —— 库错、本表跟随 pypinyin（显式钉住，防 pypinyin 版本漂移） ——
    "卡": "kǎ",     # 音译用字：阿卡拉、卡纳灶鸟、尤卡坦（库作 qiǎ）
    "鹱": "hù",     # 鹱科 Procellariidae（库作 huò）
    "鹨": "liù",    # 鹨属 Anthus、岩鹨（库作 liáo）
    "爪": "zhǎo",   # 长爪/铁爪/直爪，爪哇亦同（库作 zhuǎ）
    "鸱": "chī",    # 蟆口鸱、鸱鸮（库作 zhī）
    "颤": "chàn",   # 颤声/颤音/颤鸣（库作 zhàn）
    "什": "shí",    # 克什米尔、安卡什（库作 shén）
    "桔": "jú",     # 桔红/桔黄，桔为橘的俗字（库作 jié）
    "血": "xuè",    # 血雉/血雀，书面复合词（库作 xiě）
    "娜": "nà",     # 安娜（库作 nuó）
    # —— pypinyin 错、本表跟随库 ——
    "长": "cháng",  # 长尾/长嘴…（例外「酋长」见 PHRASE_OVERRIDES）
    "靓": "liàng",  # 靓唐纳雀 Tangara，取「漂亮」义
    "佛": "fó",     # 佛得角、佛法僧目
    "勒": "lè",     # 加勒比、圣赫勒拿、勒氏
    "藏": "zàng",   # 藏雀/藏鹀/藏雪鸡，指西藏
    "都": "dū",     # 史都华岛、民都洛、洪都拉斯（后者另有词条兜住）
    "绿": "lǜ",     # 全库 461 个「绿」均为颜色义
    "查": "chá",    # 查岛、查氏、查科、查塔
    "泊": "bó",     # 尼泊尔、漂泊信天翁
    "强": "qiáng",  # 强健/强嘴/强脚/强霸
    "伽": "qié",    # 那伽（Nāga）
    "杓": "sháo",   # 杓鹬 Numenius
}

_loaded = False


def _ensure_overrides_loaded() -> None:
    """
    把裁定表灌进 pypinyin（只做一次）。

    返回 / Return:
    None

    异常 / Raises:
    ImportError: 未安装 pypinyin。它是**构建期**依赖，运行时用不到；
        用 `pip install pypinyin` 安装后再跑本脚本。

    Load the override tables into pypinyin once.
    """
    global _loaded
    if _loaded:
        return
    from pypinyin import load_phrases_dict, load_single_dict

    # 顺序无关紧要，但词组优先级本就高于单字，两张表可以共存：
    # 单字把「长」全压成 cháng，词组再把「酋长」单独捞成 qiú zhǎng。
    load_single_dict({ord(ch): py for ch, py in SINGLE_OVERRIDES.items()})
    load_phrases_dict(PHRASE_OVERRIDES)
    _loaded = True


def normalize_name(raw: Optional[str]) -> str:
    """
    把库里读到的鸟名归一化：去首尾空白、剥掉存疑名的 ``*`` 标记。

    参数 / Parameters:
    raw (Optional[str]): 库里的原始值 / The raw value from a database.

    返回 / Returns:
    str: 归一化后的鸟名，无内容时为空串 / The normalised name, or "".

    Normalise a name read from a database: trim, then strip the leading
    uncertainty marker so it never reaches the pinyin.
    """
    return (raw or "").strip().lstrip(_UNCERTAIN_MARKS).strip()


def toned_pinyin(chinese_name: str) -> str:
    """
    把一个中文鸟名转成空格分隔的带声调拼音。

    参数 / Parameters:
    chinese_name (str): 中文鸟名，如 "黑喉小䴙䴘"。空串或纯空白返回空串。

    返回 / Returns:
    str: 带声调拼音，如 "hēi hóu xiǎo pì tī"。

    Convert one Chinese bird name into space-separated toned pinyin.
    """
    name = normalize_name(chinese_name)
    if not name:
        return ""
    _ensure_overrides_loaded()
    from pypinyin import Style, lazy_pinyin

    return " ".join(lazy_pinyin(name, style=Style.TONE))


def _strip_tone(syllable: str) -> str:
    """
    去掉一个音节的调号，用于与库里的无声调 `pinyin_name` 对账。

    参数 / Parameters:
    syllable (str): 带调音节，如 "lǜ"。

    返回 / Returns:
    str: 无调写法，如 "lv"。ü 统一写作 v，与 `ioc/birdname.db` 的记法一致。

    Strip tone marks so the result can be diffed against the shipped
    toneless column; ü is written as v to match that column's convention.
    """
    decomposed = unicodedata.normalize("NFD", syllable)
    has_diaeresis = any(ord(ch) == 0x308 for ch in decomposed)
    base = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    if has_diaeresis and "u" in base:
        base = base.replace("u", "v", 1)
    return base


def _read_names(db_path: str, table: str, column: str) -> List[str]:
    """
    读出一个源库里全部（归一化后的）中文鸟名。

    参数 / Parameters:
    db_path (str): 源库路径 / Path to the source database.
    table (str): 表名 / Table holding the names.
    column (str): 中文名所在列 / Column holding the Chinese name.

    返回 / Returns:
    List[str]: 归一化后的鸟名，保持库内顺序、可能重复 / Normalised names.

    表名与列名来自模块内的 NAME_SOURCES 常量而非外部输入，故直接内插；
    值一律走参数化以外的只读查询，不接受调用方传入的过滤条件。
    Table and column names come from the module-level NAME_SOURCES constant,
    never from user input.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL AND TRIM("{column}") != \'\''
        ).fetchall()
    finally:
        conn.close()
    return [normalize_name(row[0]) for row in rows]


def _read_shipped_pinyin(db_path: str) -> Dict[str, set]:
    """
    读出名录库自带的无声调拼音，供 ``--check`` 对账。

    参数 / Parameters:
    db_path (str): `ioc/birdname.db` 的路径 / Path to the catalog database.

    返回 / Returns:
    Dict[str, set]: {中文名: {无声调拼音, ...}}。同一个名字在多个鸟名版本里
        重复出现是常态，故按名字去重。

    Read the catalog's own toneless pinyin column for the --check diff.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT DISTINCT chinese_name, pinyin_name FROM birds "
            "WHERE chinese_name IS NOT NULL AND TRIM(chinese_name) != ''"
        ).fetchall()
    finally:
        conn.close()
    names: Dict[str, set] = {}
    for row in rows:
        names.setdefault(normalize_name(row["chinese_name"]), set()).add(
            (row["pinyin_name"] or "").strip())
    return names


def compare_with_shipped(db_path: str) -> List[Tuple[str, str, str]]:
    """
    把本脚本的结果与库自带的无声调拼音逐条对账。

    参数 / Parameters:
    db_path (str): `ioc/birdname.db` 的路径。

    返回 / Returns:
    List[Tuple[str, str, str]]: 仍有分歧的 (中文名, 库读音, 本脚本读音)。
        分歧**不等于错**——剩下的都是已裁定为「库错」的那些字（卡/鹱/鹨/爪/
        秘/酋长/鸱/颤/什/桔/血/娜）。本函数供 `--check` 人工复核用。

    Diff against the shipped toneless column; remaining rows are the ones the
    override table deliberately decided against the shipped data.
    """
    diffs: List[Tuple[str, str, str]] = []
    for name, shipped in _read_shipped_pinyin(db_path).items():
        toned = toned_pinyin(name)
        stripped = [_strip_tone(s) for s in toned.split()]
        if any(p.split() == stripped for p in shipped):
            continue
        diffs.append((name, sorted(shipped)[0], toned))
    return diffs


def build_pinyin_file(sources, out_path: str) -> int:
    """
    生成 `pinyin_toned.json`，取所有源库鸟名的并集。

    参数 / Parameters:
    sources (Iterable[Tuple[str, str, str]]): 源库清单，每项为
        (库路径, 表名, 中文名列)，通常直接传 ``NAME_SOURCES``。
    out_path (str): 产物路径。

    返回 / Returns:
    int: 写入的鸟名条数（并集去重后）。

    异常 / Raises:
    ValueError: 有中文名无法转写（转写结果为空，或没有任何调号）。
        这种情况必须整个构建失败，不能静默写一条空值——那条鸟名的拼音会就此
        永远缺失，而界面上只是少显示一行，没有人会发现。

    Build the JSON artifact from the union of every source's names; refuses to
    emit a silently empty entry.
    """
    names: List[str] = []
    for db_path, source_table, column in sources:
        names.extend(_read_names(db_path, source_table, column))

    table: Dict[str, str] = {}
    for name in names:
        if not name or name in table:
            continue
        toned = toned_pinyin(name)
        if not toned or not any(ch in TONE_MARKS for ch in toned):
            raise ValueError(
                f"无法转写鸟名 / cannot romanize: {name!r} -> {toned!r}")
        table[name] = toned

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # sort_keys 让产物可复现、diff 可读；ensure_ascii=False 保留中文键。
    # Sorted + non-ASCII keys keep the artifact reproducible and diffable.
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False, sort_keys=True, indent=0)
        fh.write("\n")
    return len(table)


def main(argv: List[str]) -> int:
    """命令行入口 / CLI entry point."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    catalog_path = os.path.join(repo, "ioc", "birdname.db")
    out_path = os.path.join(repo, "ioc", "pinyin_toned.json")
    sources = [(os.path.join(repo, rel), tbl, col)
               for rel, tbl, col in NAME_SOURCES]

    for path, _tbl, _col in sources:
        if not os.path.exists(path):
            print(f"[ERROR] missing {path}")
            return 1

    if "--check" in argv:
        diffs = compare_with_shipped(catalog_path)
        print(f"[check] names with a reading different from the shipped "
              f"toneless column: {len(diffs)}")
        for name, shipped, toned in diffs[:40]:
            print(f"  {name}  shipped={shipped}  built={toned}")
        if len(diffs) > 40:
            print(f"  ... and {len(diffs) - 40} more")
        return 0

    count = build_pinyin_file(sources, out_path)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"[ok] wrote {count} names -> {out_path} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
