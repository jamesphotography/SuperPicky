# -*- coding: utf-8 -*-
"""
#120 回归测试：平铺布局下，各确认弹窗不得再声称「会移动文件 / 整理到文件夹」。

覆盖三处：
1. 开始处理前的「文件整理」说明（MainWindow._file_organization_text）；
2. 整种合并 / 批量改鸟种确认框的目标目录渲染（_merge_folders_display）
   与「不移动」文案 key 在三种语言里都存在；
3. 改鸟种弹窗的提示与「无鸟」按钮说明有平铺版本。

#120 regression: under the flat layout, confirmation dialogs must not claim
that files will be moved/organized into folders.
"""

import types

import pytest

from tools.i18n import get_i18n

LANGS = ["en_US", "zh_CN", "zh_TW"]


@pytest.fixture
def i18n():
    """
    切换界面语言的夹具，测试结束后恢复原语言，避免影响其他测试。

    Fixture yielding the I18n singleton; restores the original language after.
    """
    inst = get_i18n()
    original = inst.current_lang
    yield inst
    inst.switch_language(original)


def _org_text(i18n, layout: str, birdid_on: bool = True) -> str:
    """
    不构造主窗口，直接以假 self 调用文案生成方法。

    Call the text builder with a stand-in self instead of building the window.
    """
    from ui.main_window import SuperPickyMainWindow
    fake = types.SimpleNamespace(i18n=i18n)
    return SuperPickyMainWindow._file_organization_text(fake, layout, birdid_on, "@@N@@")


@pytest.mark.parametrize("lang", LANGS)
def test_flat_text_does_not_list_folders(i18n, lang):
    """平铺文案不含任何星级目录名，且保留附注占位。/ Flat text lists no folders."""
    from constants import ALL_RATING_FOLDER_NAMES
    i18n.switch_language(lang)
    text = _org_text(i18n, "flat")
    assert "@@N@@" in text
    assert not any(name in text for name in ALL_RATING_FOLDER_NAMES)


@pytest.mark.parametrize("lang", LANGS)
def test_folder_layouts_use_real_folder_names(i18n, lang):
    """非平铺布局列出的目录与 compute_target_folder 实际结果一致。/ Matches real names."""
    from constants import get_rating_folder_name
    i18n.switch_language(lang)
    other = i18n.t("logs.folder_other_birds")
    species = i18n.t("dialogs.file_organization_species")

    sf = _org_text(i18n, "species-first")
    assert f"{species}/{get_rating_folder_name(3)}" in sf
    assert f"{other}/{get_rating_folder_name(1)}" in sf

    rf = _org_text(i18n, "rating-first")
    assert f"{get_rating_folder_name(3)}/{species}" in rf

    # 关识鸟：2 星以上也进「其他鸟类」/ Bird ID off: everything goes to Other Birds
    off = _org_text(i18n, "species-first", birdid_on=False)
    assert species not in off
    assert f"{other}/{get_rating_folder_name(3)}" in off


@pytest.mark.parametrize("lang", LANGS)
def test_merge_folders_display_and_no_move_keys(i18n, lang):
    """空串目录显示为根目录说明而非空行；不移动文案 key 三语齐全。/ Root label + keys."""
    from ui.results_browser_window import _merge_folders_display
    i18n.switch_language(lang)

    shown = _merge_folders_display(["", "A/B"], i18n)
    assert shown.splitlines()[0] == i18n.t("browser.merge_folder_root")
    assert shown.splitlines()[0].strip()
    assert _merge_folders_display([], i18n) == "—"

    for key in (
        "browser.merge_confirm_body_no_move",
        "browser.batch_species_confirm_body_no_move",
        "bird_species_edit.hint_flat",
        "bird_species_edit.not_a_bird_tip_flat",
    ):
        assert i18n.t(key) != key, f"{lang} missing {key}"

    body = i18n.t("browser.merge_confirm_body_no_move").format(
        old="A", count=1, burst_note="", batch_note="", new="B",
        folders_label="", folders="—")
    assert "{" not in body
