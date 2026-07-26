"""
配置键迁移与国家列表数据源测试 / Config-key migration and country-list source tests.

覆盖 birdid_use_ebird → birdid_use_geo_filter 的一次性迁移，以及国家列表
从 geo_distribution.db 生成。

Covers the one-shot migration from birdid_use_ebird to birdid_use_geo_filter,
and generating the country list from geo_distribution.db.
"""
import json

import pytest


def test_old_key_migrates_to_new(tmp_path):
    """旧键 birdid_use_ebird=False 应迁移为 birdid_use_geo_filter=False"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text(json.dumps({"birdid_use_ebird": False}), encoding="utf-8")

    from advanced_config import AdvancedConfig

    cfg = AdvancedConfig(str(cfg_file))
    assert cfg.birdid_use_geo_filter is False


def test_new_key_wins_over_old(tmp_path):
    """新旧键并存时以新键为准"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text(
        json.dumps({"birdid_use_ebird": False, "birdid_use_geo_filter": True}),
        encoding="utf-8",
    )
    from advanced_config import AdvancedConfig

    assert AdvancedConfig(str(cfg_file)).birdid_use_geo_filter is True


def test_default_is_true(tmp_path):
    """两个键都没有 → 默认开启"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    from advanced_config import AdvancedConfig

    assert AdvancedConfig(str(cfg_file)).birdid_use_geo_filter is True


def test_migration_survives_default_merge(tmp_path):
    """
    迁移判断必须基于磁盘上读到的 loaded_config，而非合并后的 self.config。

    __init__ 先 DEFAULT_CONFIG.copy() 再 update(loaded_config)，新键在合并后
    恒存在；若用 `"birdid_use_geo_filter" in self.config` 判断，迁移永远不触发，
    老用户关闭过的开关会被静默重置为默认的 True。本用例锁死这个行为。
    """
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text(json.dumps({"birdid_use_ebird": False}), encoding="utf-8")

    from advanced_config import AdvancedConfig, AdvancedConfig as AC

    assert "birdid_use_geo_filter" in AC.DEFAULT_CONFIG, "新键应在 DEFAULT_CONFIG 中"
    assert AC.DEFAULT_CONFIG["birdid_use_geo_filter"] is True, "默认值应为 True"
    # 默认 True 与旧键 False 冲突时，必须以旧键为准（迁移生效）
    assert AdvancedConfig(str(cfg_file)).birdid_use_geo_filter is False


def test_set_birdid_region_writes_new_key(tmp_path):
    """set_birdid_region 应写入新键，不写旧键"""
    cfg_file = tmp_path / "advanced_config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    from advanced_config import AdvancedConfig

    cfg = AdvancedConfig(str(cfg_file))
    cfg.set_birdid_region(False, "AU", "澳大利亚", None, "整个国家")
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["birdid_use_geo_filter"] is False
    assert "birdid_use_ebird" not in saved
    assert saved["birdid_country_code"] == "AU"


def test_region_data_from_geo_db():
    """国家列表来自 geo_distribution.db，且不含 species_count=0 的空壳国家"""
    from core.region_data import load_regions_data

    data = load_regions_data()
    countries = data["countries"]
    assert len(countries) >= 200, "GBIF 覆盖应远多于旧的 49 国"
    assert all(c["species_count"] > 0 for c in countries), "不应有空壳国家"
    codes = {c["code"] for c in countries}
    # 旧版三方错位的两类受害者都应出现
    assert {"IS"} <= codes, "冰岛缺失（旧版空白区，正是误识别案例的拍摄地）"
    assert {"PA", "UG", "BO"} <= codes, "旧版有数据但 UI 选不到的国家缺失"


def test_region_data_has_display_names():
    """每个国家都要有可显示的中英文名，不能只有代码"""
    from core.region_data import load_regions_data

    for c in load_regions_data()["countries"][:50]:
        assert c["name"], f"{c['code']} 缺英文名"
        assert c["name_cn"], f"{c['code']} 缺中文名"
