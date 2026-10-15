# -*- coding: utf-8 -*-
"""
浏览器排序偏好的保存回归测试。

修复前 set_browser_sort 的白名单只有 4 项，漏了排序下拉里的「鸟种颜值」
(species_beauty_desc)：用户选了它，下次打开浏览器又回到默认排序。
这里直接从筛选面板的排序下拉读出全部选项，逐个确认都能被保存——以后再加排序项
而忘了改白名单，本测试会失败。

Regression test: every option in the browser's sort combo must be persistable.
species_beauty_desc used to be silently dropped by set_browser_sort.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from advanced_config import AdvancedConfig
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def _sort_options() -> list:
    """
    读出筛选面板排序下拉的全部选项值。

    返回:
    list: 例如 ["rarity_desc", "filename", ...]

    Read every value offered by the filter panel's sort combo.
    """
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(get_i18n())
    try:
        combo = panel._sort_combo
        return [combo.itemData(i) for i in range(combo.count())]
    finally:
        panel.close()


def test_every_sort_option_is_persisted():
    """排序下拉里的每一项都能存进配置并读回（含鸟种颜值）。"""
    options = _sort_options()
    assert "species_beauty_desc" in options
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "advanced_config.json")
        for value in options:
            cfg = AdvancedConfig(config_file=path)
            cfg.set_browser_sort(value)
            cfg.save()
            assert AdvancedConfig(config_file=path).get_browser_sort() == value, value


def test_unknown_sort_value_is_ignored():
    """白名单外的值不写入，保留原值。"""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = AdvancedConfig(config_file=os.path.join(tmp, "advanced_config.json"))
        cfg.set_browser_sort("species_beauty_desc")
        cfg.set_browser_sort("bogus_desc")
        assert cfg.get_browser_sort() == "species_beauty_desc"
