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
    count = build_pinyin_file(db, out)

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
        build_pinyin_file(db, str(tmp_path / "out.json"))
