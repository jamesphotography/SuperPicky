# -*- coding: utf-8 -*-
"""
识鸟地区的存储：没选省/州时，配置里存固定默认值，不存当前语言的显示文字。

此前存的是下拉框显示文字，而开始确认框 / 识鸟面板拿它和当前语言的「整个国家」比较：
英文界面会显示「Australia - 整个国家」，中文措辞改成「整个国家/地区」后中文界面也会。
现在是否选了省/州一律看 region_code。

Bird-ID region storage: with no state/province chosen the config keeps one
fixed default instead of the active translation; region_code decides.
"""
import pytest

from advanced_config import AdvancedConfig


@pytest.fixture
def cfg(tmp_path):
    """独立配置文件 / an isolated config file."""
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


@pytest.mark.parametrize("label", ["整个国家/地区", "Entire country", "整個國家/地區", "整个国家"])
def test_entire_country_stores_fixed_default(cfg, label):
    """任何语言的「整个国家/地区」都存成同一个默认值。"""
    cfg.set_birdid_region(use_geo_filter=True, country_code="AU", selected_country="澳大利亚",
                          region_code=None, selected_region=label)
    assert cfg.birdid_region_code is None
    assert cfg.birdid_selected_region == AdvancedConfig.DEFAULT_CONFIG["birdid_selected_region"]


def test_chosen_region_keeps_display_name(cfg):
    """选了省/州时照常存它的显示名。"""
    cfg.set_birdid_region(use_geo_filter=True, country_code="AU", selected_country="澳大利亚",
                          region_code="AU-QLD", selected_region="昆士兰州")
    assert cfg.birdid_region_code == "AU-QLD"
    assert cfg.birdid_selected_region == "昆士兰州"
