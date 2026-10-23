# -*- coding: utf-8 -*-
"""
#143 识鸟国家下拉不再写死 11 个默认国家：本机所在国在前，其后是最近选过的（最新在前、最多 10 个），
两者都没有时才回退默认；任何入口选过的国家都记下来，可以移除。

Regression tests for #143: the Bird-ID country shortlist puts the user's own
country first, then recently picked countries (newest first, at most 10),
and only falls back to the old defaults when both are empty. Picks from any
entry point are remembered and can be removed.
"""
import json
import os
from collections import OrderedDict

from advanced_config import AdvancedConfig
from core.country_shortlist import (
    DEFAULT_SHORTLIST,
    MAX_RECENT,
    build_country_entries,
    record_country,
    remove_country,
    shortlist_codes,
)


# ---------- 纯逻辑 / pure logic ----------

def test_home_first_then_recent_without_duplicates():
    assert shortlist_codes(["JP", "DE", "AU"], home="AU") == ["AU", "JP", "DE"]


def test_home_only_when_nothing_picked_yet():
    assert shortlist_codes([], home="CA") == ["CA"]


def test_defaults_only_when_no_home_and_no_recent():
    assert shortlist_codes([], home=None) == list(DEFAULT_SHORTLIST)


def test_record_moves_to_front_and_caps():
    recent = [f"C{i}" for i in range(MAX_RECENT)]
    out = record_country(recent, "XX")
    assert out[0] == "XX" and len(out) == MAX_RECENT and "C9" not in out
    assert record_country(["JP", "DE", "AU"], "AU") == ["AU", "JP", "DE"]


def test_record_ignores_non_countries():
    for code in (None, "", "GLOBAL", "MORE", "SEP1", "SEP2"):
        assert record_country(["JP"], code) == ["JP"]


def test_remove_country():
    assert remove_country(["JP", "DE", "AU"], "DE") == ["JP", "AU"]
    assert remove_country(["JP"], "XX") == ["JP"]


def _t(key: str) -> str:
    return {"birdid.country_auto_gps": "Auto (GPS)", "birdid.country_global": "Global",
            "birdid.country_more": "More…", "birdid.country_jp": "Japan"}.get(key, key)


REGIONS = {"countries": [{"code": "JP", "name": "Japan", "name_cn": "日本"},
                         {"code": "DE", "name": "Germany", "name_cn": "德国"},
                         {"code": "CR", "name": "Costa Rica", "name_cn": "哥斯达黎加"}]}


def test_build_entries_order_and_names():
    entries = build_country_entries(_t, is_english=True, regions_data=REGIONS,
                                    recent=["DE", "CR"], home="JP")
    assert isinstance(entries, OrderedDict)
    codes = list(entries.values())
    assert codes[:3] == [None, "GLOBAL", "SEP1"]
    assert codes[3:6] == ["JP", "DE", "CR"]
    assert codes[-2:] == ["SEP2", "MORE"]
    assert "Costa Rica" in entries and entries["Japan"] == "JP"


def test_build_entries_chinese_names_and_unknown_codes_skipped():
    entries = build_country_entries(_t, is_english=False, regions_data=REGIONS,
                                    recent=["ZZ", "DE"], home=None)
    assert "德国" in entries
    assert "ZZ" not in entries.values(), "地区数据里没有的代码不显示 / unknown codes are skipped"


# ---------- 配置 / config ----------

def _cfg(tmp_path) -> AdvancedConfig:
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


def test_picking_a_country_records_it(tmp_path):
    c = _cfg(tmp_path)
    c.set_birdid_region(True, "JP", "Japan", None, "Entire country")
    c.set_birdid_region(True, "DE", "Germany", None, "Entire country")
    c.set_birdid_region(True, "GLOBAL", "Global", None, "Entire country")
    assert c.birdid_recent_countries == ["DE", "JP"]
    assert _cfg(tmp_path).birdid_recent_countries == ["DE", "JP"], "要保存到文件 / persisted"


def test_existing_user_seeded_with_current_country(tmp_path):
    path = tmp_path / "advanced_config.json"
    path.write_text(json.dumps({"birdid_country_code": "AU", "birdid_selected_country": "Australia"}),
                    encoding="utf-8")
    assert AdvancedConfig(config_file=str(path)).birdid_recent_countries == ["AU"]


def test_new_user_starts_empty(tmp_path):
    assert _cfg(tmp_path).birdid_recent_countries == []


def test_set_recent_countries_cleans_input(tmp_path):
    c = _cfg(tmp_path)
    c.set_birdid_recent_countries(["JP", "JP", "GLOBAL", None] + [f"C{i}" for i in range(20)])
    assert c.birdid_recent_countries[0] == "JP"
    assert len(c.birdid_recent_countries) == MAX_RECENT
    assert "GLOBAL" not in c.birdid_recent_countries


# ---------- 界面 / UI ----------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_removes_selected_country():
    _qapp()
    from tools.i18n import get_i18n
    from ui.country_shortlist_dialog import CountryShortlistDialog

    dlg = CountryShortlistDialog(get_i18n(), ["JP", "DE", "CR"],
                                 {"JP": "Japan", "DE": "Germany", "CR": "Costa Rica"})
    dlg._list.setCurrentRow(1)
    dlg.remove_selected()
    assert dlg.result_codes() == ["JP", "CR"]


def test_settings_page_lists_recent_and_edit_removes(monkeypatch, tmp_path):
    """
    设置中心识鸟页：下拉里有最近选过的国家；编辑框移除后下拉随即刷新，当前选中保持。
    The Bird-ID page lists recent picks; removing one refreshes the combo and
    keeps the current selection.
    """
    _qapp()
    import advanced_config as _ac_mod
    import core.country_shortlist as shortlist
    import ui.country_shortlist_dialog as dialog_mod
    from PySide6.QtWidgets import QDialog
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    i18n = get_i18n()
    original = i18n.current_lang
    i18n.switch_language("en_US")
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    cfg.set_birdid_region(True, "DE", "Germany", None, "Entire country")
    cfg.set_birdid_region(True, "JP", "Japan", None, "Entire country")
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    monkeypatch.setattr(shortlist, "system_country_code", lambda: None)
    try:
        w = SettingsCenter(i18n)
        w.show_page("birdid")
        codes = [w._bid_country_list.get(w._bid_country.itemText(i)) for i in range(w._bid_country.count())]
        assert codes[3:5] == ["JP", "DE"], "最近选过的排在前面 / recent picks come first"
        assert "BR" not in codes, "有最近记录就不再塞默认国家 / no hard-coded defaults"

        # 编辑框里移除德国 / remove Germany in the editor
        monkeypatch.setattr(dialog_mod.CountryShortlistDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(dialog_mod.CountryShortlistDialog, "result_codes", lambda self: ["JP"])
        w._edit_country_shortlist()
        codes = [w._bid_country_list.get(w._bid_country.itemText(i)) for i in range(w._bid_country.count())]
        assert "DE" not in codes and "JP" in codes
        assert w._bid_country_list.get(w._bid_country.currentText()) == "JP", "当前选中保持 / selection kept"
        assert cfg.birdid_recent_countries == ["JP"]
        w.close()
    finally:
        i18n.switch_language(original)
