# -*- coding: utf-8 -*-
"""
带声调拼音构建脚本单测 / Toned-pinyin builder tests.

鸟名里的多音字是这份数据唯一的风险点。`ioc/birdname.db` 自带的 `pinyin_name`
（无声调）与 pypinyin 各有对错，实测 11388 个鸟名中两者分歧 655 个，但只收敛成
25 个字——所以构建脚本靠一张人工裁定表把两边的错都按掉，本文件逐条钉住裁定结果。

裁定依据：主流鸟类文献（《中国鸟类野外手册》《中国鸟类分类与分布名录》）与
《现代汉语词典》的规范读音，用户 2026-09-19 拍板按此口径。

Polyphonic characters are the only real risk in this dataset: the shipped
toneless `pinyin_name` and pypinyin are each wrong in different places, so the
builder applies a hand-adjudicated override table. These tests pin every entry.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


# ----------------------------------------------------------------------
#  单字裁定：库错、pypinyin 对
# ----------------------------------------------------------------------

#: (鸟名, 期望拼音, 说明) —— 说明写进断言消息，失败时不用回头翻文献。
_LIBRARY_WAS_WRONG = [
    ("东非阿卡拉鸲", "dōng fēi ā kǎ lā qú", "卡 为音译用字读 kǎ，库作 qiǎ"),
    ("亚南极鹱", "yà nán jí hù", "鹱科 hù，库作 huò"),
    ("东方田鹨", "dōng fāng tián liù", "鹨 liù，库作 liáo"),
    ("长爪鹡鸰", "cháng zhǎo jí líng", "爪 书面复合词读 zhǎo，库作 zhuǎ"),
    ("秘鲁企鹅", "bì lǔ qǐ é", "秘鲁 Bìlǔ，库作 mì"),
    ("巴拉望蟆口鸱", "bā lā wàng má kǒu chī", "鸱鸮之鸱读 chī，库作 zhī"),
    ("颤声扇尾莺", "chàn shēng shàn wěi yīng", "颤 chàn，库作 zhàn"),
    ("克什米尔䴓", "kè shí mǐ ěr shī", "克什米尔 shí，库作 shén"),
    ("桔红长爪鹡鸰", "jú hóng cháng zhǎo jí líng", "桔为橘的俗字读 jú，库作 jié"),
    ("血雉", "xuè zhì", "血雉 xuè，库作 xiě"),
    ("安娜黑领雀", "ān nà hēi lǐng què", "安娜 nà，库作 nuó"),
]

# ----------------------------------------------------------------------
#  单字裁定：pypinyin 错、库对
# ----------------------------------------------------------------------

_PYPINYIN_WAS_WRONG = [
    ("长尾风鸟", "cháng wěi fēng niǎo", "长尾 cháng，pypinyin 默认 zhǎng"),
    ("七彩靓唐纳雀", "qī cǎi liàng táng nà què", "靓（漂亮）liàng，pypinyin 作 jìng"),
    ("佛得角鹱", "fó dé jiǎo hù", "佛得角 fó，pypinyin 作 fú"),
    ("加勒比崖燕", "jiā lè bǐ yá yàn", "加勒比 lè，pypinyin 作 lēi"),
    ("藏雪鸡", "zàng xuě jī", "藏（西藏）zàng，pypinyin 作 cáng"),
    ("尼泊尔鹪鹛", "ní bó ěr jiāo méi", "尼泊尔 bó，pypinyin 作 pō"),
    ("那伽鹩鹛", "nà qié liáo méi", "那伽（Nāga）qié，pypinyin 作 gā"),
    ("中杓鹬", "zhōng sháo yù", "杓鹬 sháo，pypinyin 作 biāo"),
]

# ----------------------------------------------------------------------
#  词级裁定：单字覆盖救不了，必须按词
# ----------------------------------------------------------------------

_PHRASE_CASES = [
    ("亚热带酋长鹂", "yà rè dài qiú zhǎng lí",
     "酋长 qiú zhǎng——全库 196 个含「长」的鸟名里只有这 11 个读 zhǎng"),
    ("绿林戴胜", "lǜ lín dài shèng",
     "绿林是绿色林地不是「绿林好汉」，pypinyin 内置词组给 lù lín"),
    ("南无须小霸鹟", "nán wú xū xiǎo bà wēng",
     "南+无须，pypinyin 把「南无」当佛号读 nā mó"),
    ("查查波亚蚁鸫", "chá chá bō yà yǐ dōng",
     "Chachapoyas 音译，pypinyin 内置叠词「查查」给 zhā zhā"),
    ("强嘴䴕雀", "qiáng zuǐ liè què",
     "强嘴指嘴强健，pypinyin 内置词组「强嘴」给 jiàng zuǐ"),
    ("洪都拉斯蜂鸟", "hóng dū lā sī fēng niǎo",
     "Honduras 应作 hóng dū lā sī，pypinyin 内置词组给 dōu"),
]

# ----------------------------------------------------------------------
#  反例：不能一刀切按字强制，这些必须保持原读音
# ----------------------------------------------------------------------

_MUST_NOT_OVERREACH = [
    ("隐秘树鹃", "yǐn mì shù juān", "秘 只有「秘鲁」读 bì，此处仍是 mì"),
    ("北无须小霸鹟", "běi wú xū xiǎo bà wēng", "无须 本来就对，别被「南无」规则波及"),
    ("爪哇翠鸟", "zhǎo wā cuì niǎo", "爪哇 Zhǎowā，与长爪同音无冲突"),
    ("漂泊信天翁", "piāo bó xìn tiān wēng", "泊 与尼泊尔同为 bó"),
    ("佛法僧", "fó fǎ sēng", "佛法僧目同样读 fó"),
]


@pytest.mark.parametrize("name,expected,why",
                         _LIBRARY_WAS_WRONG + _PYPINYIN_WAS_WRONG
                         + _PHRASE_CASES + _MUST_NOT_OVERREACH)
def test_adjudicated_reading(name, expected, why):
    """逐条钉住人工裁定的读音 / Pin every adjudicated reading."""
    from scripts_dev.build_pinyin_toned import toned_pinyin

    assert toned_pinyin(name) == expected, f"{name}：{why}"


def test_plain_names_need_no_adjudication():
    """绝大多数鸟名没有争议，裁定表不能把它们改坏。"""
    from scripts_dev.build_pinyin_toned import toned_pinyin

    assert toned_pinyin("家燕") == "jiā yàn"
    assert toned_pinyin("黑喉小䴙䴘") == "hēi hóu xiǎo pì tī"
    assert toned_pinyin("白腹海雕") == "bái fù hǎi diāo"


def test_empty_name_yields_empty_string():
    """空名不抛异常——库里理论上不该有，但构建脚本不该因一条脏数据整个挂掉。"""
    from scripts_dev.build_pinyin_toned import toned_pinyin

    assert toned_pinyin("") == ""
    assert toned_pinyin("   ") == ""


def test_build_writes_every_name_with_tone_marks(tmp_path):
    """
    构建产物：每个中文名一条带声调拼音，且**必须真的带声调**。

    只断言「有内容」挡不住最可能的事故——某次改动让脚本退回无声调输出，
    json 看起来照样是满的。
    """
    import sqlite3
    from scripts_dev.build_pinyin_toned import build_pinyin_file

    db = str(tmp_path / "birdname.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE birds (chinese_name TEXT, pinyin_name TEXT)")
    conn.executemany("INSERT INTO birds VALUES (?, ?)", [
        ("家燕", "jia yan"),
        ("中杓鹬", "zhong shao yu"),
        ("家燕", "jia yan"),          # 同名多版本，产物里只该有一条
    ])
    conn.commit()
    conn.close()

    out = str(tmp_path / "pinyin_toned.json")
    count = build_pinyin_file([(db, "birds", "chinese_name")], out)

    data = json.loads(open(out, encoding="utf-8").read())
    assert count == 2
    assert data == {"家燕": "jiā yàn", "中杓鹬": "zhōng sháo yù"}
    assert any(ch in data["家燕"] for ch in "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ"), (
        "产物退回了无声调拼音"
    )


def test_build_rejects_a_name_it_cannot_romanize(tmp_path):
    """
    出现无法转写的名字时必须报错退出，不能静默写一条空值。

    静默写空的话，界面上那条鸟名的拼音就此永远缺失，而没有任何人会发现。
    """
    import sqlite3
    from scripts_dev.build_pinyin_toned import build_pinyin_file

    db = str(tmp_path / "birdname.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE birds (chinese_name TEXT, pinyin_name TEXT)")
    conn.execute("INSERT INTO birds VALUES (?, ?)", ("???", ""))
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="无法转写|cannot romanize"):
        build_pinyin_file([(db, "birds", "chinese_name")], str(tmp_path / "out.json"))


# ======================================================================
#  多源：拼音表必须覆盖**两个**库里的鸟名
#
#  界面上的鸟名来自两个不同的库：
#    ioc/birdname.db（名录）        —— 鸟名查询面板、改鸟种弹窗
#    bird_reference.sqlite（识鸟库）—— 详情面板、识鸟结果卡片（经 report.db）
#
#  拼音表最初只从名录库生成，于是识鸟库独有的 1000 多个鸟种在详情面板里拼音
#  是空的——静默缺失，不报错。表必须取两个库的并集。
#
#  The displayed names come from two databases; building the table from only
#  one left ~1000 species silently without pinyin.
# ======================================================================


def test_build_merges_names_from_every_source(tmp_path):
    """两个源库各自独有的鸟名都要进产物，重复的只留一条。"""
    import json as _json
    import sqlite3 as _sqlite3
    from scripts_dev.build_pinyin_toned import build_pinyin_file

    catalog = str(tmp_path / "birdname.db")
    conn = _sqlite3.connect(catalog)
    conn.execute("CREATE TABLE birds (chinese_name TEXT)")
    conn.executemany("INSERT INTO birds VALUES (?)", [("家燕",), ("中杓鹬",)])
    conn.commit(); conn.close()

    reference = str(tmp_path / "bird_reference.sqlite")
    conn = _sqlite3.connect(reference)
    conn.execute("CREATE TABLE BirdCountInfo (chinese_simplified TEXT)")
    conn.executemany("INSERT INTO BirdCountInfo VALUES (?)",
                     [("家燕",), ("东方鹗",)])          # 家燕重复，东方鹗是识鸟库独有
    conn.commit(); conn.close()

    out = str(tmp_path / "pinyin_toned.json")
    count = build_pinyin_file(
        [(catalog, "birds", "chinese_name"),
         (reference, "BirdCountInfo", "chinese_simplified")], out)

    data = _json.loads(open(out, encoding="utf-8").read())
    assert count == 3
    assert set(data) == {"家燕", "中杓鹬", "东方鹗"}


def test_uncertain_name_marker_is_stripped():
    """
    识鸟库里 19 条鸟名带 ``*`` 前缀（存疑名标记），星号不得进拼音。

    不剥的话界面上会显示「* běi měi wū yā」。
    """
    from scripts_dev.build_pinyin_toned import toned_pinyin

    assert toned_pinyin("*北美乌鸦") == "běi měi wū yā"
    assert toned_pinyin("＊北美乌鸦") == "běi měi wū yā"


def test_shipped_table_covers_every_name_in_both_databases():
    """
    防脱节守卫：两个源库里的每一个中文名都必须能查到拼音。

    拼音表是**派生数据**。任何一个源库变动（例如 #110 那次 60 个改名）之后忘了
    重跑构建脚本，界面上就会有鸟种悄悄没有拼音——不报错、没人发现。靠这条测试
    盯住，比靠记性可靠。

    Drift guard: the table is derived data, so a source database changing
    without a rebuild would silently drop pinyin for some species.
    """
    import sqlite3 as _sqlite3
    from tools.pinyin_names import pinyin_for
    from scripts_dev.build_pinyin_toned import NAME_SOURCES, normalize_name

    repo = os.path.dirname(os.path.abspath(__file__))
    missing = []
    for rel_path, table, column in NAME_SOURCES:
        # 按仓库根解析，不依赖 pytest 的当前工作目录
        # Resolve against the repo root rather than the process CWD.
        conn = _sqlite3.connect(os.path.join(repo, rel_path))
        try:
            rows = conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND TRIM("{column}") != ""'
            ).fetchall()
        finally:
            conn.close()
        for (raw,) in rows:
            name = normalize_name(raw)
            if name and not pinyin_for(name):
                missing.append((rel_path, name))

    assert not missing, (
        f"{len(missing)} 个鸟名查不到拼音，源库变动后需重跑 "
        f"scripts_dev/build_pinyin_toned.py；例：{missing[:5]}"
    )
