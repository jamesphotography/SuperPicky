# -*- coding: utf-8 -*-
"""
地点字段「是不是 SuperPicky 写的」的判断（与 PhotoPicky ToolMetadata.isToolWritten 同口径）。

SuperPicky 借城市/省份/国家存锐度/美学/对焦；照片里已有真实地名时不能覆盖、重置也不能清掉，
所以先得分清哪些值是我们写的。

Which City/State/Country values SuperPicky wrote (same rule as PhotoPicky).
"""
from core.location_guard import (
    LOCATION_RESET_ARGS,
    SCORE_ITEM_KEYS,
    groups_from_exiftool_json,
    has_real_location,
    is_number,
    is_tool_written,
)


def test_superpicky_values_are_tool_written():
    """SuperPicky 的数字、对焦词（含旧版中文）都算工具写的。"""
    assert is_tool_written("0612.34", "05.21", "BEST")
    assert is_tool_written("0612.34", "", "")
    assert is_tool_written("", "", "good")          # 大小写不敏感 / case-insensitive
    assert is_tool_written("", "", "偏移")           # 旧版中文 / legacy Chinese


def test_real_names_are_not_tool_written():
    """中英文真实地名都不算。"""
    assert not is_tool_written("布里斯班", "昆士兰州", "澳大利亚")
    assert not is_tool_written("Brisbane", "QLD", "Australia")
    assert not is_tool_written("", "", "")


def test_is_number_rules():
    """与 PhotoPicky isNumber 一致：ASCII 数字，至多一个小数点，首段非空。"""
    assert is_number(" 612.34 ") and is_number("612")
    assert not is_number("") and not is_number(".5") and not is_number("1.2.3")
    assert not is_number("６１２")                   # 全角数字不算 / full-width digits don't count


def test_has_real_location():
    """任一组是真实地名即受保护；全空或全是工具值不受保护。"""
    assert not has_real_location([("", "", ""), ("0612.34", "05.21", "BEST")])
    assert has_real_location([("0612.34", "05.21", "BEST"), ("布里斯班", "", "")])
    assert not has_real_location([])


def test_groups_from_exiftool_json():
    """-G1 -json 的一条记录拆成 XMP 组与 IPTC 组；缺失为空串，非字符串转 str。"""
    data = {"XMP-photoshop:City": "布里斯班", "IPTC:City": "0612.34", "IPTC:Province-State": 5.21}
    assert groups_from_exiftool_json(data) == [("布里斯班", "", ""), ("0612.34", "5.21", "")]


def test_constants():
    """受保护时去掉的分数键与重置参数。"""
    assert SCORE_ITEM_KEYS == ("sharpness", "nima_score", "focus_status")
    assert LOCATION_RESET_ARGS == frozenset({
        "-XMP:City=", "-IPTC:City=", "-XMP:State=", "-IPTC:Province-State=",
        "-XMP:Country=", "-IPTC:Country-PrimaryLocationName=",
    })
