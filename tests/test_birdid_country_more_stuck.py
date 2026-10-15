# -*- coding: utf-8 -*-
"""
回归测试：识鸟页国家下拉不能停在「── More Countries ──」并被当成国家存下来（#122 跟进）。

用户截图里处理前弹窗显示的国家是「── More Countries ──」——那是国家下拉里打开
完整列表的入口项。两条路径会让下拉停在它上面：
1. 「更多国家」对话框点 OK 但列表里没有选中项（例如在搜索框输入后直接回车）；
2. 点取消，而要恢复的显示名不在下拉里（默认值是写死的中文「自动检测 (GPS)」，
   英文界面的下拉里没有这一项，setCurrentText 静默失败）。
之后页面上任一字段变化触发 _save_birdid，就把入口项文字存成国家名。

Regression: the Bird-ID country combo must never rest on the "More countries"
entry and persist it as the country name.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def english_center(monkeypatch, tmp_path):
    """
    英文界面 + 全新默认配置的设置中心，停在识鸟页。

    Settings Center in English with a fresh default config, on the Bird-ID page.
    """
    from advanced_config import AdvancedConfig
    import advanced_config as _ac_mod
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    i18n = get_i18n()
    original = i18n.current_lang
    i18n.switch_language("en_US")
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    w = SettingsCenter(i18n)
    w.show_page("birdid")
    yield w, cfg, i18n
    w.close()
    i18n.switch_language(original)


def _choose_more(w, i18n, monkeypatch, dialog_result) -> None:
    """
    在国家下拉里选「更多国家」，让弹出的对话框直接返回 dialog_result（不选列表项）。

    Pick "More countries" and make the popup return dialog_result with no list
    item selected.
    """
    monkeypatch.setattr(QDialog, "exec", lambda self: dialog_result)
    more = i18n.t("birdid.country_more")
    w._bid_country.setCurrentIndex(w._bid_country.findText(more))


@pytest.mark.parametrize("dialog_result", [
    QDialog.DialogCode.Rejected,     # 取消 / cancel
    QDialog.DialogCode.Accepted,     # OK 但没选中 / OK with nothing selected
])
def test_more_countries_never_persisted_as_country(english_center, monkeypatch, dialog_result):
    w, cfg, i18n = english_center
    more = i18n.t("birdid.country_more")
    before_code = cfg.birdid_country_code

    _choose_more(w, i18n, monkeypatch, dialog_result)
    # 下拉不能停在入口项上 / combo must not rest on the entry
    assert w._bid_country.currentText() != more

    # 随后改动其他字段触发保存 / another field change triggers a save
    w._bid_conf.setValue(w._bid_conf.value() + 1)
    assert cfg.birdid_selected_country != more
    assert cfg.birdid_country_code == before_code


def test_cancel_keeps_previously_saved_country(english_center, monkeypatch):
    """
    已选美国后点「更多国家」再取消：仍是美国，代码不变。

    With the US saved, opening "More countries" and cancelling keeps the US.
    """
    w, cfg, i18n = english_center
    us = i18n.t("birdid.country_us")
    w._bid_country.setCurrentIndex(w._bid_country.findText(us))
    assert cfg.birdid_country_code == "US"

    _choose_more(w, i18n, monkeypatch, QDialog.DialogCode.Rejected)
    assert w._bid_country.currentText() == us
    w._bid_conf.setValue(w._bid_conf.value() + 1)
    assert cfg.birdid_country_code == "US"
    assert cfg.birdid_selected_country == us


def test_enter_after_search_with_single_match_selects_it(english_center, monkeypatch):
    """
    在搜索框输入 Canada 后直接回车（列表无选中项、只剩一项）：选中加拿大并保存。

    Typing "Canada" and pressing Enter (no selection, one match left) picks it.
    """
    from PySide6.QtWidgets import QLineEdit
    w, cfg, i18n = english_center

    def _exec(dialog):
        dialog.findChild(QLineEdit).setText("Canada")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", _exec)
    w._bid_country.setCurrentIndex(w._bid_country.findText(i18n.t("birdid.country_more")))
    assert cfg.birdid_country_code == "CA"
    assert cfg.birdid_selected_country == i18n.t("birdid.country_ca")
