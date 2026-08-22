#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测开关的 i18n 与文案测试。

不构造 MainWindow——构造它会切换全局 i18n 语言，导致本地化断言假失败
（既往教训）。这里只校验键存在且两种语言都有。

i18n coverage for the telemetry toggle. Deliberately avoids constructing
MainWindow, which switches the global i18n language.
"""
import json
from pathlib import Path

import pytest

LOCALES = Path(__file__).parent / "locales"


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_keys_exist(filename: str) -> None:
    """两种语言都必须有开关文案，缺一个就是界面上的空标签。/ Both locales."""
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    assert "telemetry_label" in data["settings"]
    assert "telemetry_desc" in data["settings"]


@pytest.mark.parametrize("filename", ["zh_CN.json", "en_US.json"])
def test_telemetry_desc_states_what_is_not_sent(filename: str) -> None:
    """
    说明文案必须写明「不含照片/路径/个人信息」。

    opt-out 默认开启的前提是说明必须到位；这条测试守住它不被简化掉。

    The description must state what is NOT collected — the premise of opt-out.
    """
    data = json.loads((LOCALES / filename).read_text(encoding="utf-8"))
    desc = data["settings"]["telemetry_desc"].lower()
    assert ("照片" in desc or "photo" in desc)
    assert ("个人信息" in desc or "personal information" in desc)
