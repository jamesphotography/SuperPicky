# -*- coding: utf-8 -*-
"""
GBIF 缺席时仍要显示自定义罕见指数 / The custom index must show without GBIF.

用户反馈（2026-09-23）：装了自定义罕见指数后，「东鵙雀鹟」能看到指数，而
「北鵙雀鹟」「西鵙雀鹟」什么都没有——尽管数据文件里三者都有。

根因：两处显示代码都把 `lookup_custom_rarity` **嵌在 GBIF 的 if 分支里**
（`ui/birdname_search_widget.py` 的 `_show_detail`、`ui/detail_panel.py` 的
罕见度行），GBIF 取不到值就走 else 直接置「—」，自定义指数根本不会被查询。

而这两件事本是**互相独立的数据源**：

    Falcunculus frontatus  (东鵙雀鹟)  bird_reference 有 → GBIF 18.41 → 显示
    Falcunculus whitei     (北鵙雀鹟)  bird_reference 没有 → GBIF None → 整行「—」
    Falcunculus leucogaster(西鵙雀鹟)  同上

偏偏这些「内置库没有的鸟种」正是自定义指数最该补位的地方：它们多是新拆分出的
物种，模型与内置库都还没跟上，用户只能手工改鸟名——改完却什么参照都看不到。

The custom index is an independent optional source but was nested inside the
GBIF branch, so species missing from the bundled reference showed nothing —
exactly the species the custom dataset exists to cover.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


# ----------------------------------------------------------------------
#  纯函数：两个数据源各自可缺席
# ----------------------------------------------------------------------

def test_both_sources_render_side_by_side():
    """两者都有时并排显示，保持原有格式。"""
    from core.rarity_tier import format_rarity_values

    assert format_rarity_values(18.41, 9.8) == "18.4 - 9.80"


def test_gbif_alone_renders_unchanged():
    """只有 GBIF 时与改动前逐字一致——绝大多数鸟种走这条，不能动。"""
    from core.rarity_tier import format_rarity_values

    assert format_rarity_values(18.41, None) == "18.4"


def test_custom_alone_still_renders():
    """
    只有自定义指数时也要显示，并保住「GBIF - 自定义」的位置语义。

    用占位符而不是直接把数字提到前面：否则 9.80 会被误读成 GBIF 分数
    （两者尺度不同，GBIF 是 0-100，自定义是 0-10）。
    """
    from core.rarity_tier import format_rarity_values

    assert format_rarity_values(None, 9.8) == "— - 9.80"


def test_neither_source_yields_empty():
    """两者都没有时返回空串，由调用方显示占位。"""
    from core.rarity_tier import format_rarity_values

    assert format_rarity_values(None, None) == ""


def test_custom_keeps_two_decimals():
    """
    自定义指数保留两位小数。

    这类 0-10 的评分取值高度集中，截成一位会把大部分区分度抹掉
    （实测万余种数据 572 个取值压成 87 档）。
    """
    from core.rarity_tier import format_rarity_values

    assert format_rarity_values(None, 9.0) == "— - 9.00"
    assert format_rarity_values(50.0, 7.05) == "50.0 - 7.05"


# ----------------------------------------------------------------------
#  两个显示入口
# ----------------------------------------------------------------------

@pytest.fixture
def custom_rarity(tmp_path, monkeypatch):
    """装一份只含「北鵙雀鹟」的自定义指数，且该鸟种在内置库里没有。"""
    import sqlite3
    import core.custom_rarity as mod

    path = tmp_path / "custom_rarity.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE custom_rarity (chinese_simplified TEXT, "
                 "english_name TEXT, rarity_index REAL)")
    conn.execute("INSERT INTO custom_rarity VALUES (?, ?, ?)",
                 ("北鵙雀鹟", "Northern Shriketit", 9.8))
    conn.commit()
    conn.close()

    monkeypatch.setattr(mod, "_db_path", lambda: str(path))
    mod.reset_cache()
    yield
    mod.reset_cache()


@pytest.fixture
def zh_ui():
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield i18n
    if i18n.current_lang != original:
        i18n.switch_language(original)


def test_search_panel_shows_custom_index_without_gbif(custom_rarity, zh_ui):
    """
    鸟名查询面板：内置库没有这个鸟种时，仍要显示自定义指数。

    这是用户报的症状本身——他在鸟名搜索里搜「北鵙雀鹟」，罕见度一栏是空的。
    """
    from ui.birdname_search_widget import BirdNameSearchWidget

    widget = BirdNameSearchWidget()
    try:
        widget._show_detail({
            "chinese_name": "北鵙雀鹟", "english_name": "Northern Shriketit",
            "latin_name": "Falcunculus whitei",
        })

        assert "9.80" in widget.detail_rarity_label.text(), (
            f"罕见度一栏是 {widget.detail_rarity_label.text()!r}"
        )
    finally:
        widget.deleteLater()


def test_detail_panel_shows_custom_index_without_gbif(custom_rarity, zh_ui):
    """选片详情页同样：没有 GBIF 也要显示自定义指数。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(zh_ui)
    try:
        panel.show_photo({
            "filename": "DSC_0001.NEF", "current_path": "/tmp/DSC_0001.NEF",
            "bird_species_cn": "北鵙雀鹟", "bird_species_en": "Northern Shriketit",
            "rating": 3, "has_bird": 1, "gbif_rarity_100": None,
        })

        assert "9.80" in panel._val_gbif_rarity.text(), (
            f"罕见度一栏是 {panel._val_gbif_rarity.text()!r}"
        )
    finally:
        panel.deleteLater()


def test_species_with_neither_source_still_shows_placeholder(zh_ui):
    """
    两个数据源都没有时保持占位符，不能因为改动而变成空白或报错。
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(zh_ui)
    try:
        panel.show_photo({
            "filename": "DSC_0002.NEF", "current_path": "/tmp/DSC_0002.NEF",
            "bird_species_cn": "查无此鸟", "bird_species_en": "No Such Bird",
            "rating": 3, "has_bird": 1, "gbif_rarity_100": None,
        })

        assert panel._val_gbif_rarity.text().strip() != ""
    finally:
        panel.deleteLater()
