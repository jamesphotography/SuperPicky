# -*- coding: utf-8 -*-
"""
鸟种代码搜索的集成测试（issue #119）：用真实 ioc/birdname.db 跑两处界面的搜索。
Integration tests for species-code search against the real bundled catalog.

两处界面（改鸟种弹窗、鸟名查询面板）各自拼 SQL，只是共用
tools.birdname_versions.catalog_code_search 的片段——参数顺序一旦错位，单元测试
覆盖不到，这里直接调界面的搜索方法、截下结果来断言。
Both UIs build their own SQL around the shared fragment; misplaced parameters
would slip past unit tests, so the real search methods are exercised here.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _simplified_chinese_ui():
    """钉住简体中文界面：繁体界面的台湾名搜索会改写首个精确名参数 / Pin zh_CN."""
    from tools.i18n import get_i18n

    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("zh_CN")
    yield
    i18n.switch_language(previous)


def _dialog_search(query: str) -> list:
    """
    用改鸟种弹窗的 _search 搜索，返回结果中文名列表（不含本次拍到鸟种的置顶）。

    参数:
    query (str): 搜索词

    返回:
    list: 按显示顺序的中文名

    Run the species-edit dialog's search and return Chinese names in order.
    """
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    dialog = BirdSpeciesEditDialog()
    captured = []
    dialog._show_results = lambda rows: captured.extend(r["chinese_name"] for r in rows)
    try:
        dialog._search(query)
    finally:
        dialog.close()
    return captured


def _widget_search(query: str) -> list:
    """
    用鸟名查询面板的 _perform_search 搜索，返回结果中文名列表。

    参数:
    query (str): 搜索词

    返回:
    list: 按显示顺序的中文名

    Run the bird-name panel's search and return Chinese names in order.
    """
    from tools.birdname_versions import pick_version_from_db
    from ui.birdname_search_widget import BirdNameSearchWidget

    widget = BirdNameSearchWidget()
    # 面板会恢复用户上次选的名录版本（本机设置）；代码列只在「SuperPicky 名录」里，
    # 这里固定到默认版本，不依赖本机记住的选择。
    # The panel restores the user's last catalog; pin the default master catalog.
    widget.current_version_id = pick_version_from_db(widget.db_path)
    captured = []
    widget._display_results = lambda rows: captured.extend(r["chinese_name"] for r in rows)
    try:
        widget._perform_search(query)
    finally:
        widget.close()
    return captured


SEARCHES = [_dialog_search, _widget_search]


@pytest.mark.parametrize("search", SEARCHES)
@pytest.mark.parametrize("query", ["CANG", "cang", "cangoo"])
def test_codes_put_canada_goose_first(search, query):
    """4 位代码、eBird 代码与其前缀都把加拿大黑雁排第一 / Codes rank the bird first."""
    results = search(query)
    assert results and results[0] == "加拿大黑雁", results[:5]


@pytest.mark.parametrize("search", SEARCHES)
def test_ebird_code_of_a_non_american_bird(search):
    """eBird 代码覆盖全世界：gragoo → 灰雁、livbul1 → 白头鹎 / Worldwide eBird codes."""
    assert search("gragoo")[0] == "灰雁"
    assert search("livbul1")[0] == "白头鹎"


@pytest.mark.parametrize("search", SEARCHES)
def test_pinyin_initials_and_chinese_are_unchanged(search):
    """拼音首字母与中文搜索不受代码搜索影响 / Pinyin initials and Chinese still win."""
    assert search("btb")[0] == "白头鹎"
    assert search("田鹨")[0] == "田鹨"
    assert search("理氏鹨")[0] == "田鹨"
