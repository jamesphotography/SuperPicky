"""
配置键迁移与国家列表数据源测试 / Config-key migration and country-list source tests.

覆盖 birdid_use_ebird → birdid_use_geo_filter 的一次性迁移，以及国家与省州列表
从 ebird_regions.db 生成。

Covers the one-shot migration from birdid_use_ebird to birdid_use_geo_filter,
and generating the country and subnational lists from ebird_regions.db.
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


def test_region_data_from_ebird_db():
    """国家列表来自 ebird_regions.db，且不含空清单国家 / Countries come from ebird_regions.db."""
    from core.region_data import load_regions_data

    countries = load_regions_data()["countries"]
    assert len(countries) >= 200, "eBird 国家级应覆盖 200+ 国家/地区"
    assert all(c["species_count"] > 0 for c in countries), "不应有空清单国家"
    codes = {c["code"] for c in countries}
    assert {"IS", "PA", "UG", "BO", "TW", "HK", "MO"} <= codes


def test_region_data_subnational_for_cn_au_us():
    """只有中澳美带省州，数量与 eBird 一致 / Only CN/AU/US carry subnational lists."""
    from core.region_data import load_regions_data

    by_code = {c["code"]: c for c in load_regions_data()["countries"]}
    assert (by_code["CN"]["regions_count"], by_code["AU"]["regions_count"], by_code["US"]["regions_count"]) == (31, 8, 51)
    assert by_code["IS"]["has_regions"] is False and by_code["IS"]["regions"] == []
    nsw = next(r for r in by_code["AU"]["regions"] if r["code"] == "AU-NSW")
    assert nsw["name_cn"] == "新南威尔士州" and nsw["species_count"] > 0

    # 省州按 eBird code 排序：中国省份遵循 GB/T 2260 行政区划顺序，首项应为
    # CN-11（北京）/ Subnational regions are ordered by eBird code: China's
    # follow the GB/T 2260 administrative order, so the first entry is CN-11
    # (Beijing).
    cn_codes = [r["code"] for r in by_code["CN"]["regions"]]
    assert cn_codes == sorted(cn_codes), f"CN 省州应按 code 升序: {cn_codes}"
    assert cn_codes[0] == "CN-11", f"CN 首项应为 CN-11（北京）, 实际 {cn_codes[0]}"


def test_region_data_has_display_names():
    """每个国家与省州都有中英文名 / Every country and subnational unit has both names."""
    from core.region_data import load_regions_data

    for c in load_regions_data()["countries"]:
        assert c["name"] and c["name_cn"], f"{c['code']} 缺名称"
        for r in c["regions"]:
            assert r["name"] and r["name_cn"], f"{r['code']} 缺名称"


def _isolate_advanced_config(monkeypatch):
    """
    把 get_advanced_config 指向临时文件，隔离本机真实配置。

    构造 SettingsCenter 会读取、并在某些路径写回 advanced_config；若直连真实
    配置，跑一次测试就可能改掉用户的国家选择等设置（本仓库有过此类事故记录）。

    Point get_advanced_config at a temp file to isolate the developer's real
    config: constructing SettingsCenter reads it and can write it back on some
    paths, so running the suite could silently change real user settings.

    参数 / Parameters:
        monkeypatch: pytest fixture.
    """
    import tempfile

    import advanced_config as _ac_mod

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w",
                                     encoding="utf-8") as f:
        f.write("{}")
        tmp_path = f.name
    cfg = _ac_mod.AdvancedConfig(config_file=tmp_path)
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    return cfg


def test_settings_center_shows_gps_scope_hint(monkeypatch):
    """
    识鸟页必须展示「手选地区仅无 GPS 时生效」的提示。

    有 GPS 的照片按拍摄地自动判断省州/国家、手选不参与，这一点若不明说，用户会
    以为选了没生效。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    _isolate_advanced_config(monkeypatch)
    i18n = get_i18n()
    expected = i18n.t("settings.birdid_region_hint")
    assert expected and not expected.startswith("settings."), "提示文案键缺失"

    w = SettingsCenter(i18n)
    w.show_page("birdid")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert expected in texts, f"识鸟页未展示 GPS 生效提示；现有标签: {texts[:12]}"
    w.close()


def test_about_page_shows_ebird_attribution(monkeypatch):
    """
    关于页展示 eBird 与 Natural Earth 署名，且带真实获取日期。

    获取日期从 ebird_regions.db 的 meta 表动态读取，不能并入静态 about.content。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    _isolate_advanced_config(monkeypatch)
    w = SettingsCenter(get_i18n())
    attr = w._geo_attribution_text()
    assert attr, "署名文本为空（ebird_regions.db 应可用）"
    assert "eBird" in attr and "Natural Earth" in attr, f"署名不完整: {attr}"
    assert "GBIF" not in attr, f"地理数据已不来自 GBIF: {attr}"

    w.show_page("about")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert attr in texts, "关于页未展示署名行"
    w.close()
