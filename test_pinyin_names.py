# -*- coding: utf-8 -*-
"""
中文鸟名拼音查询（tools/pinyin_names.py）单测 / Pinyin lookup tests.

拼音只给简体中文界面看，是纯展示信息：查不到、数据文件缺失、界面是英文——
这三种情况都必须安静地返回空串，绝不能抛异常。鸟名显示在缩略图、详情面板、
识鸟结果等处，任何一处因为查拼音失败而崩，代价远大于少显示一行拼音。

Pinyin is display-only and Chinese-UI-only: a miss, a missing data file, or an
English UI must all yield "" rather than raise.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def test_known_bird_name_returns_toned_pinyin():
    """随库发布的真实数据必须查得到，且带声调。"""
    from tools.pinyin_names import pinyin_for

    assert pinyin_for("家燕") == "jiā yàn"
    assert pinyin_for("黑喉小䴙䴘") == "hēi hóu xiǎo pì tī"


def test_adjudicated_readings_survive_into_the_shipped_data():
    """
    人工裁定的读音必须真的进了产物。

    构建脚本的测试只验证函数，这里验证**发布出去的那份 json**——漏跑一次构建
    脚本，界面上就会悄悄退回未裁定的读音。
    """
    from tools.pinyin_names import pinyin_for

    assert pinyin_for("亚热带酋长鹂") == "yà rè dài qiú zhǎng lí"
    assert pinyin_for("绿林戴胜") == "lǜ lín dài shèng"
    assert pinyin_for("中杓鹬") == "zhōng sháo yù"
    assert pinyin_for("秘鲁企鹅") == "bì lǔ qǐ é"


def test_unknown_name_returns_empty_string():
    """
    查不到返回空串而不是抛异常。

    用户改鸟种时可能留下历史遗留名、手工名；老批次的 report.db 里也有当年的
    旧称。这些都查不到，属于常态。
    """
    from tools.pinyin_names import pinyin_for

    assert pinyin_for("这不是一个鸟名") == ""


def test_blank_and_none_are_tolerated():
    """空名 / None 不得抛异常——调用点直接把 DB 字段递进来，可能是 None。"""
    from tools.pinyin_names import pinyin_for

    assert pinyin_for("") == ""
    assert pinyin_for("   ") == ""
    assert pinyin_for(None) == ""


def test_english_name_is_not_looked_up():
    """英文鸟名不该匹配到任何东西（表的键全是中文名）。"""
    from tools.pinyin_names import pinyin_for

    assert pinyin_for("Barn Swallow") == ""


def test_missing_data_file_degrades_to_empty(tmp_path, monkeypatch):
    """
    数据文件缺失（打包遗漏、用户删文件）时整体降级为不显示拼音。

    这是唯一一条「宁可不显示也不能崩」的硬要求：拼音挂在鸟名旁边，鸟名出现在
    缩略图、详情面板、识鸟结果里，抛异常会连带整片界面。
    """
    import tools.pinyin_names as mod

    mod.reset_cache()
    monkeypatch.setattr(mod, "_data_path", lambda: str(tmp_path / "nope.json"))
    assert mod.pinyin_for("家燕") == ""
    mod.reset_cache()


def test_corrupt_data_file_degrades_to_empty(tmp_path, monkeypatch):
    """数据文件损坏同样降级，不抛异常。"""
    import tools.pinyin_names as mod

    broken = tmp_path / "pinyin_toned.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    mod.reset_cache()
    monkeypatch.setattr(mod, "_data_path", lambda: str(broken))
    assert mod.pinyin_for("家燕") == ""
    mod.reset_cache()


def test_custom_data_file_is_read(tmp_path, monkeypatch):
    """能读到替换过的数据文件——证明查的是文件而不是硬编码。"""
    import tools.pinyin_names as mod

    custom = tmp_path / "pinyin_toned.json"
    custom.write_text(json.dumps({"测试鸟": "cè shì niǎo"}, ensure_ascii=False),
                      encoding="utf-8")
    mod.reset_cache()
    monkeypatch.setattr(mod, "_data_path", lambda: str(custom))
    assert mod.pinyin_for("测试鸟") == "cè shì niǎo"
    assert mod.pinyin_for("家燕") == ""
    mod.reset_cache()


# ----------------------------------------------------------------------
#  界面语言闸门
# ----------------------------------------------------------------------

def test_ui_helper_returns_pinyin_for_chinese_ui():
    """中文界面照常返回拼音。"""
    from tools.pinyin_names import pinyin_for_ui

    assert pinyin_for_ui("家燕", is_zh=True) == "jiā yàn"


def test_ui_helper_is_silent_on_english_ui():
    """
    英文界面一律不显示拼音（用户 2026-09-19 要求：仅限简体中文版）。

    闸门收在这一个函数里，四个显示点各自写 if 迟早会漏掉一处。
    """
    from tools.pinyin_names import pinyin_for_ui

    assert pinyin_for_ui("家燕", is_zh=False) == ""
    assert pinyin_for_ui("黑喉小䴙䴘", is_zh=False) == ""


def test_lookup_is_cached(monkeypatch):
    """
    数据只读一次盘。

    这张表 477KB / 11388 条，而拼音要挂在缩略图这种一屏几十个的地方，
    每次查都读盘会把滚动拖垮。
    """
    import tools.pinyin_names as mod

    mod.reset_cache()
    calls = {"n": 0}
    real = mod._load_table

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(mod, "_load_table", counting)
    mod.pinyin_for("家燕")
    mod.pinyin_for("白腹海雕")
    mod.pinyin_for("黑鸢")
    assert calls["n"] == 1
    mod.reset_cache()


# ----------------------------------------------------------------------
#  发布链路：Windows Lite 也要装到这份数据
# ----------------------------------------------------------------------

def test_pinyin_data_is_in_the_installable_resource_plan():
    """
    `ioc/pinyin_toned.json` 必须进 Windows Lite 的资源安装清单。

    Lite 版的资源装在安装目录而不是随包，清单里漏掉这一条，Lite 用户的拼音会
    整体消失——而且是**静音**消失：查询模块按设计降级为空串，界面只是少一行，
    没有任何报错。所以必须由测试盯住，不能指望运行时发现。

    断言的是「运行时要读的那个相对路径」与「清单里声明的落地路径」一致，
    而不是复读常量本身。

    The Lite build installs resources into the install directory; a missing
    entry makes pinyin vanish silently, since the lookup degrades to "".
    """
    import os
    from scripts.download_models import resolve_download_plan
    from tools.pinyin_names import _RELATIVE_PATH

    plan = resolve_download_plan(["birdid"])
    landed = {os.path.join(item.get("dest_dir", ""), item["filename"])
              for item in plan}

    assert _RELATIVE_PATH in landed, (
        f"{_RELATIVE_PATH} 不在 birdid 的资源安装清单里；"
        f"清单现有：{sorted(landed)}"
    )


@pytest.mark.parametrize("features", [None, [], ["birdid"], ["yolo"], ["nope"]])
def test_pinyin_data_ships_exactly_when_the_name_database_does(features):
    """
    拼音表与 `ioc/birdname.db` 必须**同进同出**。

    两者是同一份东西的两半：名字和它的读音。任何一种功能勾选组合下只装其一，
    都会得到一个「有鸟名没拼音」或「装了拼音却没有鸟名库」的半截安装。

    断言的是两者的关系而不是某个具体的 feature_tag——将来标签怎么调整，
    这条不变量都该成立。（注意本模块「空选 = 全选」，所以不能用空列表来
    表达「没选识鸟」。）

    The pinyin table and the name database are two halves of one thing and must
    be planned together under every feature selection.
    """
    import os
    from scripts.download_models import resolve_download_plan
    from tools.pinyin_names import _RELATIVE_PATH

    plan = resolve_download_plan(features)
    landed = {os.path.join(item.get("dest_dir", ""), item["filename"])
              for item in plan}

    assert (_RELATIVE_PATH in landed) == (os.path.join("ioc", "birdname.db") in landed)
