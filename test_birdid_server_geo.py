# -*- coding: utf-8 -*-
"""
识鸟服务端地理过滤相关逻辑单测 / Server-side geo filter logic tests.
"""
import tempfile

from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL


def test_warning_rules():
    """
    告警条件：未过滤；或中澳美照片只落到国家级（说明省州层没用上）。
    其他国家的国家级是正常层级，不告警。
    """
    from birdid_server import geo_filter_warning

    assert geo_filter_warning({"tier": TIER_SUBNATIONAL, "region_code": "AU-NSW", "species_count": 600}) is None
    assert geo_filter_warning({"tier": TIER_COUNTRY, "region_code": "IS", "species_count": 400}) is None
    assert geo_filter_warning({"tier": TIER_COUNTRY, "region_code": "AU", "species_count": 900})
    assert geo_filter_warning({"tier": TIER_NONE, "region_code": None, "species_count": None})
    assert geo_filter_warning(None)


def test_update_gui_settings_writes_subnational(monkeypatch):
    """GPS 同步把国家与省州一起写入配置 / GPS sync writes country and subnational."""
    import advanced_config as ac
    import birdid_server

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as f:
        f.write("{}")
        path = f.name
    cfg = ac.AdvancedConfig(config_file=path)
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)

    birdid_server.update_gui_settings_from_gps("AU", "AU-NSW")
    assert cfg.birdid_country_code == "AU"
    assert cfg.birdid_region_code == "AU-NSW"

    birdid_server.update_gui_settings_from_gps("IS")
    assert cfg.birdid_country_code == "IS"
    assert cfg.birdid_region_code is None
