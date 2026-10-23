# -*- coding: utf-8 -*-
"""
#143 识鸟国家下拉的「常用国家」：默认是原来那 11 个国家（本机所在国不在其中时排最前），
用户可以调整——在「更多国家」里选的新国家自动加进来、编辑框里移除或恢复默认；
「更多国家」列出全部国家（含常用国家里的澳洲等），只排除「全球」。

Regression tests for #143 (revised): the shortlist defaults to the original
11 countries (home country first when it is not among them) and is user
adjustable; "More countries" lists every country, including shortlisted ones.
"""
import json
import os
from collections import OrderedDict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from advanced_config import AdvancedConfig
from core.country_shortlist import (
    DEFAULT_SHORTLIST,
    MAX_SHORTLIST,
    add_country,
    build_country_entries,
    effective_shortlist,
    remove_country,
)


# ---------- 纯逻辑 / pure logic ----------

def test_never_edited_uses_defaults():
    assert effective_shortlist(None, home=None) == list(DEFAULT_SHORTLIST)
    assert effective_shortlist(None, home="AU") == list(DEFAULT_SHORTLIST), "本机已在默认里不重复 / no duplicate"


def test_home_outside_defaults_goes_first():
    assert effective_shortlist(None, home="CA") == ["CA", *DEFAULT_SHORTLIST]


def test_edited_list_is_used_as_is():
    assert effective_shortlist(["JP", "DE"], home="CA") == ["JP", "DE"]
    assert effective_shortlist([], home="CA") == [], "用户删光了就是空 / emptied by the user"


def test_add_new_country_goes_first_existing_keeps_order():
    assert add_country(["AU", "JP"], "DE") == ["DE", "AU", "JP"]
    assert add_country(["AU", "JP"], "JP") == ["AU", "JP"], "已有的不挪位置 / existing keeps its place"
    big = [f"C{i}" for i in range(MAX_SHORTLIST)]
    assert len(add_country(big, "XX")) == MAX_SHORTLIST


def test_add_ignores_non_countries():
    for code in (None, "", "GLOBAL", "MORE", "SEP1", "SEP2"):
        assert add_country(["JP"], code) == ["JP"]


def test_remove_country():
    assert remove_country(["JP", "DE", "AU"], "DE") == ["JP", "AU"]


def _t(key: str) -> str:
    return {"birdid.country_auto_gps": "Auto (GPS)", "birdid.country_global": "Global",
            "birdid.country_more": "More…", "birdid.country_jp": "Japan"}.get(key, key)


REGIONS = {"countries": [{"code": "JP", "name": "Japan", "name_cn": "日本"},
                         {"code": "DE", "name": "Germany", "name_cn": "德国"},
                         {"code": "CR", "name": "Costa Rica", "name_cn": "哥斯达黎加"}]}


def test_build_entries_order_and_names():
    entries = build_country_entries(_t, is_english=True, regions_data=REGIONS, shortlist=["JP", "DE", "CR"])
    assert isinstance(entries, OrderedDict)
    codes = list(entries.values())
    assert codes[:3] == [None, "GLOBAL", "SEP1"]
    assert codes[3:6] == ["JP", "DE", "CR"]
    assert codes[-2:] == ["SEP2", "MORE"]


def test_build_entries_skips_unknown_codes():
    entries = build_country_entries(_t, is_english=False, regions_data=REGIONS, shortlist=["ZZ", "DE"])
    assert "德国" in entries and "ZZ" not in entries.values()


# ---------- 配置 / config ----------

def _cfg(tmp_path) -> AdvancedConfig:
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


def test_new_user_has_no_stored_list(tmp_path):
    assert _cfg(tmp_path).birdid_country_shortlist is None


def test_picking_a_new_country_adds_it(tmp_path, monkeypatch):
    monkeypatch.setattr("core.country_shortlist.system_country_code", lambda: None)
    c = _cfg(tmp_path)
    c.set_birdid_region(True, "DE", "Germany", None, "Entire country")
    c.set_birdid_region(True, "GLOBAL", "Global", None, "Entire country")
    assert c.birdid_country_shortlist == ["DE", *DEFAULT_SHORTLIST]
    assert _cfg(tmp_path).birdid_country_shortlist == ["DE", *DEFAULT_SHORTLIST], "要保存 / persisted"


def test_picking_a_default_country_keeps_list_untouched(tmp_path):
    c = _cfg(tmp_path)
    c.set_birdid_region(True, "AU", "Australia", None, "Entire country")
    assert c.birdid_country_shortlist is None, "仍跟随默认 / still following the defaults"


def test_existing_user_with_country_outside_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("core.country_shortlist.system_country_code", lambda: None)
    path = tmp_path / "advanced_config.json"
    path.write_text(json.dumps({"birdid_country_code": "DE", "birdid_selected_country": "Germany"}), encoding="utf-8")
    assert AdvancedConfig(config_file=str(path)).birdid_country_shortlist == ["DE", *DEFAULT_SHORTLIST]


def test_reset_to_defaults(tmp_path):
    c = _cfg(tmp_path)
    c.set_birdid_country_shortlist(["JP"])
    assert c.birdid_country_shortlist == ["JP"]
    c.set_birdid_country_shortlist(None)
    assert c.birdid_country_shortlist is None


# ---------- 界面 / UI ----------

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_removes_and_resets():
    _qapp()
    from tools.i18n import get_i18n
    from ui.country_shortlist_dialog import CountryShortlistDialog

    dlg = CountryShortlistDialog(get_i18n(), ["JP", "DE", "CR"],
                                 {"JP": "Japan", "DE": "Germany", "CR": "Costa Rica"})
    dlg._list.setCurrentRow(1)
    dlg.remove_selected()
    assert dlg.result_codes() == ["JP", "CR"]
    dlg.reset_to_defaults()
    assert dlg.result_codes() is None, "恢复默认 / back to defaults"


def _settings_center(monkeypatch, tmp_path, cfg):
    import advanced_config as _ac_mod
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    i18n = get_i18n()
    i18n.switch_language("en_US")
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    monkeypatch.setattr("core.country_shortlist.system_country_code", lambda: None)
    w = SettingsCenter(i18n)
    w.show_page("birdid")
    return w, i18n


def _combo_codes(w) -> list:
    return [w._bid_country_list.get(w._bid_country.itemText(i)) for i in range(w._bid_country.count())]


def test_settings_page_defaults_then_edit(monkeypatch, tmp_path):
    _qapp()
    import ui.country_shortlist_dialog as dialog_mod
    from PySide6.QtWidgets import QDialog
    from tools.i18n import get_i18n

    original = get_i18n().current_lang
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    try:
        w, _ = _settings_center(monkeypatch, tmp_path, cfg)
        assert _combo_codes(w)[3:3 + len(DEFAULT_SHORTLIST)] == list(DEFAULT_SHORTLIST), "默认 11 国 / defaults"

        monkeypatch.setattr(dialog_mod.CountryShortlistDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(dialog_mod.CountryShortlistDialog, "result_codes", lambda self: ["JP", "AU"])
        w._edit_country_shortlist()
        assert _combo_codes(w)[3:5] == ["JP", "AU"] and "BR" not in _combo_codes(w)
        assert cfg.birdid_country_shortlist == ["JP", "AU"]
        w.close()
    finally:
        get_i18n().switch_language(original)


def test_more_countries_lists_shortlisted_countries_too(monkeypatch, tmp_path):
    """
    「更多国家」要能找到常用国家里的澳洲等（此前写死排除原 11 国，从常用里删掉后两边都找不到）。
    "More countries" must include shortlisted countries such as Australia.
    """
    _qapp()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QListWidget
    from tools.i18n import get_i18n

    original = get_i18n().current_lang
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    cfg.set_birdid_country_shortlist(["JP"])
    try:
        w, i18n = _settings_center(monkeypatch, tmp_path, cfg)
        listed = []

        def fake_exec(dialog):
            lists = dialog.findChildren(QListWidget)
            for lw in lists:
                listed.extend(lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count()))
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, "exec", fake_exec)
        w._bid_country.setCurrentIndex(w._bid_country.findText(i18n.t("birdid.country_more")))
        assert "AU" in listed and "JP" in listed and "US" in listed
        assert "GLOBAL" not in listed
        w.close()
    finally:
        get_i18n().switch_language(original)
