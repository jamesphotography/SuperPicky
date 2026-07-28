import inspect
import json
import os
import tempfile
from advanced_config import AdvancedConfig


def _cfg(tmp):
    """Helper to create AdvancedConfig with custom path."""
    return AdvancedConfig(config_file=os.path.join(tmp, "advanced_config.json"))


def test_defaults_present():
    """Test that all birdid default fields are present."""
    with tempfile.TemporaryDirectory() as tmp:
        c = _cfg(tmp)
        assert c.birdid_auto_identify is False
        assert c.birdid_use_geo_filter is True
        assert c.birdid_selected_country == "自动检测 (GPS)"
        assert c.birdid_country_code is None
        assert c.birdid_region_code is None
        assert c.birdid_selected_region == "整个国家"


def test_migration_moves_legacy_chinese_region(tmp_path):
    """Test that legacy birdid_dock_settings.json is migrated correctly."""
    legacy = tmp_path / "birdid_dock_settings.json"
    legacy.write_text(
        json.dumps(
            {
                "use_ebird": False,
                "country_code": "AU",
                "selected_country": "澳大利亚",
                "region_code": "AU-QLD",
                "selected_region": "昆士兰",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c = AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))
    moved = c.migrate_birdid_dock_settings(legacy_path=str(legacy))
    assert moved is True
    assert c.birdid_selected_country == "澳大利亚"
    assert c.birdid_region_code == "AU-QLD"
    assert c.birdid_use_geo_filter is False
    assert c.birdid_selected_region == "昆士兰"
    assert c.birdid_country_code == "AU"
    # Idempotent: second migration should not overwrite
    assert c.migrate_birdid_dock_settings(legacy_path=str(legacy)) is False
    assert c.birdid_selected_country == "澳大利亚"


def test_migration_idempotent_even_when_country_is_default(tmp_path):
    """Test idempotency when legacy file has default country value."""
    legacy = tmp_path / "birdid_dock_settings.json"
    legacy.write_text(
        json.dumps(
            {
                "use_ebird": True,
                "country_code": None,
                "selected_country": "自动检测 (GPS)",
                "region_code": None,
                "selected_region": "整个国家",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    c = AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))
    assert c.migrate_birdid_dock_settings(legacy_path=str(legacy)) is True
    assert c.migrate_birdid_dock_settings(legacy_path=str(legacy)) is False


def test_c1_migration_wired_in_main_startup():
    """
    C1 回归测试：验证 main.py 的启动序列中确实调用了 migrate_birdid_dock_settings。

    通过 inspect 读取 main.py 源码来断言，无需真正启动 QApplication。
    这确保了迁移调用不会因重构而悄悄丢失。

    C1 regression test: verify that main.py startup sequence actually calls
    migrate_birdid_dock_settings.

    Uses inspect to read main.py source — no need to launch QApplication.
    Ensures the migration call cannot silently disappear during refactoring.
    """
    import importlib.util
    import pathlib

    # 读取 main.py 源码 / Read main.py source
    main_path = pathlib.Path(__file__).parent / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "migrate_birdid_dock_settings" in source, (
        "C1 defect still present: migrate_birdid_dock_settings is NOT called in main.py. "
        "Existing users will lose their birdid country/region on upgrade."
    )
