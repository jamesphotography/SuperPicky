# -*- coding: utf-8 -*-
"""
Paul 反馈 P0 三项的回归测试:对焦文案统一/鸟名文件名并显/键盘打星。

Regression tests for the three P0 items from Paul's feedback: consistent
focus labels, species+filename display, and keyboard star rating.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_focus_filter_labels_match_detail_panel_terms():
    """
    筛选面板三个对焦 checkbox 的文本必须与右侧详情使用的
    browser.focus_state_* i18n 值一致(不再显示裸枚举 BEST/GOOD/BAD)。

    The three focus checkboxes must reuse the browser.focus_state_* strings
    shown in the detail panel (no more raw BEST/GOOD/BAD enums).
    """
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    panel = FilterPanel(i18n)
    for mode in ("BEST", "GOOD", "BAD"):
        cb = panel._focus_checks[mode]
        expected = i18n.t(f"browser.focus_state_{mode.lower()}")
        assert cb.text() == expected, f"{mode}: {cb.text()!r} != {expected!r}"
        assert cb.text() != mode  # 不允许裸枚举 / raw enum forbidden
    panel.close()


def test_focus_filter_labels_use_i18n_in_english_ui():
    """
    用假 i18n(英文态)构造面板,证明 checkbox 文案确实经 i18n.t 取值——
    修复前英文界面显示裸枚举 BEST/GOOD/BAD(Paul 反馈的左右不一致根因)。

    Build the panel with a fake English i18n to prove labels go through
    i18n.t; before the fix the English UI showed the raw enums.
    """
    from ui.filter_panel import FilterPanel

    class _FakeI18n:
        current_lang = "en_US"

        def t(self, key, **kw):
            return f"<{key}>"

    panel = FilterPanel(_FakeI18n())
    for mode in ("BEST", "GOOD", "BAD"):
        cb = panel._focus_checks[mode]
        assert cb.text() == f"<browser.focus_state_{mode.lower()}>", cb.text()
    panel.close()


def test_tile_label_shows_species_and_filename():
    """
    有鸟种时卡片标签两行并显(鸟名+文件名),无鸟种时只显示文件名。
    With a species the tile label carries both species and filename;
    without a species it falls back to the filename only.
    """
    from ui.thumbnail_grid import _tile_label_text

    photo = {"filename": "DSC01234.ARW",
             "bird_species_cn": "白胸鸲鹟", "bird_species_en": "White-breasted Robin"}
    text = _tile_label_text(photo)
    assert "DSC01234.ARW" in text
    assert ("白胸鸲鹟" in text) or ("White-breasted Robin" in text)

    no_species = {"filename": "DSC09999.NEF"}
    assert _tile_label_text(no_species) == "DSC09999.NEF"

    # 连拍后缀跟在第一行(鸟名)之后 / burst suffix stays on the first line
    text2 = _tile_label_text(photo, " (5)")
    assert "(5)" in text2.split("<br/>")[0]


def test_detail_panel_species_row_above_gbif():
    """
    详情面板 rows 中鸟种行存在且位于全球罕见度行之前(Paul 截图诉求)。
    The species row exists in the detail panel and sits above GBIF rarity.
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    keys = [k for k, _ in panel._meta_rows]
    assert "browser.meta_species" in keys
    assert keys.index("browser.meta_species") < keys.index("browser.meta_gbif_rarity")
    panel.close()
