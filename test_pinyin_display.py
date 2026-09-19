# -*- coding: utf-8 -*-
"""
中文鸟名拼音的四处显示 / The four places that show pinyin.

用户 2026-09-19 拍板：拼音只在这四处显示，且**仅限简体中文界面**。

  1. 鸟名查询面板的详情标题（`BirdNameSearchWidget._show_detail`）
  2. 鸟名结果卡片（`BirdResultCard`）——改鸟种弹窗复用同一个卡片类，一处改动覆盖两个入口
  3. 浏览器右栏详情面板的鸟种行（`DetailPanel`）
  4. 识鸟面板的识别结果卡片（`birdid_dock.ResultCard`）

明确不加的地方（缩略图 caption / 鸟种筛选下拉 / HTML 报告 / 文件夹名 / EXIF /
eBird 导出）没有测试，因为「不加」由不写代码保证；但报告那条有反向断言，
见 test_report_export.py——拼音绝不能混进分享给别人的产物。

两条贯穿全文件的约束：

  - **英文界面下四处都不出现**。闸门收在 `tools.pinyin_names.pinyin_for_ui`，
    但每处仍单独断言：漏接一处闸门，英文用户就会看到中文拼音。
  - **拼音不得混进可复制的那个控件**。鸟名查询卡片与详情面板的鸟名都支持点击
    复制，把拼音并进同一个 QLabel 会让用户复制鸟名时带出一串拼音——这正是这里
    用独立 label 而不是富文本的原因。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])

_SWALLOW_PINYIN = "jiā yàn"
_GREBE = "黑喉小䴙䴘"
_GREBE_PINYIN = "hēi hóu xiǎo pì tī"


@pytest.fixture
def zh_ui():
    """把界面钉成简体中文 / Pin the UI to Simplified Chinese."""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield i18n
    if i18n.current_lang != original:
        i18n.switch_language(original)


@pytest.fixture
def en_ui():
    """把界面钉成英文 / Pin the UI to English."""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("en"):
        i18n.switch_language("en_US")
    yield i18n
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _bird_data(cn="家燕", en="Barn Swallow", latin="Hirundo rustica"):
    return {"bird_id": 1, "chinese_name": cn, "english_name": en,
            "latin_name": latin, "pinyin_name": "jia yan", "abbreviation": "JY"}


# ----------------------------------------------------------------------
#  1 + 2. 鸟名结果卡片（鸟名查询面板 / 改鸟种弹窗共用）
# ----------------------------------------------------------------------

def test_result_card_shows_pinyin_in_chinese_ui(zh_ui):
    """中文界面：卡片上要能看到带声调拼音。"""
    from ui.birdname_search_widget import BirdResultCard

    card = BirdResultCard(_bird_data())

    assert card.pinyin_label is not None
    assert card.pinyin_label.text() == _SWALLOW_PINYIN


def test_result_card_hides_pinyin_in_english_ui(en_ui):
    """英文界面：卡片主名是英文名，不得出现中文拼音。"""
    from ui.birdname_search_widget import BirdResultCard

    card = BirdResultCard(_bird_data())

    assert getattr(card, "pinyin_label", None) is None


def test_result_card_keeps_the_name_copyable_without_pinyin(zh_ui):
    """
    主名 label 的文本必须仍是纯鸟名。

    那个 label 点击即复制鸟名；拼音若并进去，用户复制到的就是
    「家燕 jiā yàn」——鸟名要拿去搜索、贴进别处，带尾巴等于不能用。
    """
    from ui.birdname_search_widget import BirdResultCard

    card = BirdResultCard(_bird_data())

    assert card.primary_label.text() == "家燕"


def test_result_card_height_is_unchanged(zh_ui):
    """
    卡片仍是固定 52px 两行。

    用户 2026-09-19 选定「拼音挂在主名行右边、不加高卡片」，这条钉住它——
    卡片一旦变高，搜索结果一屏能看到的鸟种数就少一截。
    """
    from ui.birdname_search_widget import BirdResultCard

    card = BirdResultCard(_bird_data())

    assert card.CARD_HEIGHT == 52
    assert card.height() == 52


def test_result_card_without_a_known_name_shows_no_pinyin(zh_ui):
    """查不到拼音的鸟名（历史名/手工名）不显示空标签。"""
    from ui.birdname_search_widget import BirdResultCard

    card = BirdResultCard(_bird_data(cn="查无此鸟"))

    assert getattr(card, "pinyin_label", None) is None


def test_search_detail_header_contains_pinyin(zh_ui):
    """鸟名查询面板的详情标题：中文名之后要带拼音。"""
    from ui.birdname_search_widget import BirdNameSearchWidget

    widget = BirdNameSearchWidget()
    try:
        widget._show_detail(_bird_data(cn=_GREBE, en="Little Grebe",
                                       latin="Tachybaptus ruficollis"))
        text = widget.detail_header.text()

        assert _GREBE in text
        assert _GREBE_PINYIN in text
    finally:
        widget.deleteLater()


def test_search_detail_header_has_no_pinyin_in_english_ui(en_ui):
    """英文界面的详情标题不得出现拼音。"""
    from ui.birdname_search_widget import BirdNameSearchWidget

    widget = BirdNameSearchWidget()
    try:
        widget._show_detail(_bird_data(cn=_GREBE, en="Little Grebe",
                                       latin="Tachybaptus ruficollis"))

        assert _GREBE_PINYIN not in widget.detail_header.text()
    finally:
        widget.deleteLater()


# ----------------------------------------------------------------------
#  3. 浏览器右栏详情面板
# ----------------------------------------------------------------------

def _photo(cn="家燕", en="Barn Swallow"):
    return {"filename": "DSC_0001.NEF", "current_path": "/tmp/DSC_0001.NEF",
            "bird_species_cn": cn, "bird_species_en": en,
            "rating": 3, "has_bird": 1}


def test_detail_panel_shows_pinyin_next_to_the_species(zh_ui):
    """中文界面：鸟种行旁边显示带声调拼音。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(zh_ui)
    try:
        panel.show_photo(_photo())

        assert panel._val_species.text() == "家燕"
        assert panel._val_species_pinyin.text() == _SWALLOW_PINYIN
    finally:
        panel.deleteLater()


def test_detail_panel_hides_pinyin_in_english_ui(en_ui):
    """英文界面：鸟种行显示英文名，拼音标签为空。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(en_ui)
    try:
        panel.show_photo(_photo())

        assert panel._val_species_pinyin.text() == ""
    finally:
        panel.deleteLater()


def test_detail_panel_clears_pinyin_when_switching_to_an_unknown_species(zh_ui):
    """
    换到一张查不到拼音的照片时，上一张的拼音必须被清掉。

    标签是复用的：只在「查到了」时 setText，切换照片就会留下前一只鸟的拼音，
    挂在一个完全不相干的鸟名旁边。
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(zh_ui)
    try:
        panel.show_photo(_photo())
        assert panel._val_species_pinyin.text() == _SWALLOW_PINYIN

        panel.show_photo(_photo(cn="查无此鸟"))

        assert panel._val_species_pinyin.text() == ""
    finally:
        panel.deleteLater()


# ----------------------------------------------------------------------
#  4. 识鸟面板的识别结果卡片
# ----------------------------------------------------------------------

def test_birdid_result_card_shows_pinyin(zh_ui):
    """中文界面：识别结果卡片显示拼音。"""
    from ui.birdid_dock import ResultCard

    card = ResultCard(1, "家燕", "Barn Swallow", 98.7)

    assert card.pinyin_label is not None
    assert card.pinyin_label.text() == _SWALLOW_PINYIN


def test_birdid_result_card_hides_pinyin_in_english_ui(en_ui):
    """英文界面：识别结果卡片不显示拼音。"""
    from ui.birdid_dock import ResultCard

    card = ResultCard(1, "家燕", "Barn Swallow", 98.7)

    assert getattr(card, "pinyin_label", None) is None


def test_birdid_result_card_keeps_the_name_label_pure(zh_ui):
    """识别结果卡片的鸟名 label 同样不得混入拼音（它也支持点击复制）。"""
    from ui.birdid_dock import ResultCard

    card = ResultCard(1, "家燕", "Barn Swallow", 98.7)

    assert card.name_label.text() == "家燕"
