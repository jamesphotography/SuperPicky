# -*- coding: utf-8 -*-
"""
颜色标签默认映射 B+ 的测试:蓝=飞鸟/绿=精焦/红=脱焦/GOOD 无标签,
i18n 缺 key 回退英文,locales 色名断言。

Tests for the B+ XMP label remap: blue=flying, green=critical focus,
red=defocused, no label for GOOD; English fallback on missing i18n keys;
locale color-name assertions.
"""
import json


def _t_zh(key, **kw):
    return {"xmp_labels.flight": "蓝色", "xmp_labels.focus": "绿色",
            "xmp_labels.defocus": "红色"}.get(key, key)


def _t_missing(key, **kw):
    return key  # 模拟语言包缺 key / simulate missing keys


def test_compute_xmp_label_mapping_and_priority():
    """飞鸟优先于对焦;BEST→绿,BAD/WORST→红,GOOD/None→无标签。"""
    from core.photo_processor import compute_xmp_label as f

    assert f(True, "BEST", _t_zh) == "蓝色"      # 飞鸟优先 / flying wins
    assert f(True, None, _t_zh) == "蓝色"
    assert f(False, "BEST", _t_zh) == "绿色"
    assert f(False, "BAD", _t_zh) == "红色"
    assert f(False, "WORST", _t_zh) == "红色"
    assert f(False, "GOOD", _t_zh) is None       # 常态无标签 / no label
    assert f(False, None, _t_zh) is None


def test_compute_xmp_label_english_fallback():
    """语言包缺 key → 回退英文色名,绝不返回 key 串(4.3.0 白框防御)。"""
    from core.photo_processor import compute_xmp_label as f

    assert f(True, None, _t_missing) == "Blue"
    assert f(False, "BEST", _t_missing) == "Green"
    assert f(False, "WORST", _t_missing) == "Red"


def test_locale_label_color_names():
    """两语言包的 xmp_labels 值符合 B+ 映射(蓝/绿/红,Blue/Green/Red)。"""
    zh = json.load(open("locales/zh_CN.json", encoding="utf-8"))["xmp_labels"]
    en = json.load(open("locales/en_US.json", encoding="utf-8"))["xmp_labels"]
    assert zh == {"flight": "蓝色", "focus": "绿色", "defocus": "红色"}
    assert en == {"flight": "Blue", "focus": "Green", "defocus": "Red"}
