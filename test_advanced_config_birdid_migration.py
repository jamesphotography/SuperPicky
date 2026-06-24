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
        assert c.birdid_use_ebird is True
        assert c.birdid_selected_country == "自动检测 (GPS)"


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
    assert c.birdid_use_ebird is False
    # Idempotent: second migration should not overwrite
    assert c.migrate_birdid_dock_settings(legacy_path=str(legacy)) is False
    assert c.birdid_selected_country == "澳大利亚"
