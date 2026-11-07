# -*- coding: utf-8 -*-
"""
设置中心「恢复默认设置」(用户 2026-11-07 拍板)。

- 恢复 advanced_config 里的用户设置,保留 PRESERVE_ON_RESET(运行状态、一次性
  标记、界面语言、匿名统计、最近文件夹、窗口位置);
- 恢复前备份配置文件;写盘失败时内存也还原(失败 = 什么都没改);
- 恢复后设置中心原地重建各页——关闭时 done() 会把控件值兜底写回配置,不重建
  的话旧值会被原样写回去。

Restore Defaults in the Settings Center: resets user settings, keeps the
preserved fields, backs up first, is all-or-nothing, and rebuilds the pages
so the close-time flush cannot write the old values back.
"""
import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from advanced_config import AdvancedConfig, get_advanced_config  # noqa: E402


def _non_default_values() -> dict:
    """一批与默认值不同的用户设置 / user settings differing from the defaults."""
    d = AdvancedConfig.DEFAULT_CONFIG
    return {
        "delete_confirm": not d["delete_confirm"],
        "metadata_write_title": not d["metadata_write_title"],
        "rating_algorithm": "v1" if d["rating_algorithm"] != "v1" else "v2",
        "skill_level": "custom",
        "custom_quota3": 15,
        "custom_quota2": 20,
        "min_confidence": 0.7,
        "birdid_confidence": 80,
        "keep_temp_files": not d["keep_temp_files"],
        "external_apps": [{"name": "Editor", "path": "/Applications/Editor.app"}],
        "birdid_country_shortlist": ["AU", "CN"],
    }


def test_preserved_keys_all_exist_in_defaults():
    """保留清单里不能有拼错的键 / no typos in the preserve list."""
    missing = AdvancedConfig.PRESERVE_ON_RESET - set(AdvancedConfig.DEFAULT_CONFIG)
    assert not missing, missing


def test_reset_keeps_preserved_and_resets_the_rest():
    cfg = get_advanced_config()
    cfg.config.update(_non_default_values())
    cfg.config["language"] = "en_US"
    cfg.config["telemetry_enabled"] = False
    cfg.config["birdid_dock_settings_migrated"] = True
    cfg.config["initialization_completed"] = True
    cfg.config["recent_directories"] = ["/photos/a"]
    cfg.config["merlin_app_path"] = "/old"           # 已废弃的旧键 / legacy key
    cfg.reset_to_default()
    for key, val in _non_default_values().items():
        assert cfg.config[key] == AdvancedConfig.DEFAULT_CONFIG[key], key
    assert cfg.config["language"] == "en_US"
    assert cfg.config["telemetry_enabled"] is False
    assert cfg.config["birdid_dock_settings_migrated"] is True
    assert cfg.config["initialization_completed"] is True
    assert cfg.config["recent_directories"] == ["/photos/a"]
    assert "merlin_app_path" not in cfg.config


def test_reset_deep_copies_defaults():
    """浅拷贝会让改设置顺带改掉 DEFAULT_CONFIG / no shared mutable defaults."""
    before = copy.deepcopy(AdvancedConfig.DEFAULT_CONFIG)
    cfg = get_advanced_config()
    cfg.reset_to_default()
    for key, val in cfg.config.items():
        if isinstance(val, list):
            val.append("x")
        elif isinstance(val, dict):
            val["x"] = 1
    assert AdvancedConfig.DEFAULT_CONFIG == before


def test_restore_defaults_backs_up_and_saves():
    cfg = get_advanced_config()
    cfg.config.update(_non_default_values())
    cfg.save()
    with open(cfg.config_file, encoding="utf-8") as f:
        original = json.load(f)
    backup = cfg.restore_defaults()
    assert backup and os.path.isfile(backup)
    with open(backup, encoding="utf-8") as f:
        assert json.load(f) == original
    with open(cfg.config_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["delete_confirm"] == AdvancedConfig.DEFAULT_CONFIG["delete_confirm"]


def test_restore_defaults_failed_save_changes_nothing(monkeypatch):
    cfg = get_advanced_config()
    cfg.config.update(_non_default_values())
    cfg.save()
    snapshot = copy.deepcopy(cfg.config)
    monkeypatch.setattr(cfg, "save", lambda: False)
    try:
        cfg.restore_defaults()
        raise AssertionError("expected OSError")
    except OSError:
        pass
    assert cfg.config == snapshot


def test_settings_center_restore_survives_close_flush(monkeypatch):
    """
    恢复默认后关闭设置中心:done() 的兜底保存不得把旧值写回去,
    配置文件里所有非保留项都应等于默认值。
    After restore + close, every non-preserved key on disk equals its default.
    """
    from tools.i18n import get_i18n
    from ui.custom_dialogs import DeleteConfirmDialog, StyledMessageBox
    from ui.settings_center import SettingsCenter

    cfg = get_advanced_config()
    cfg.config.update(_non_default_values())
    cfg.save()

    def _choose_reset(self):
        self.choose("reset")
        return 0

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _choose_reset)
    shown = []
    monkeypatch.setattr(StyledMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a)))

    w = SettingsCenter(get_i18n(), start_page="about")
    w._on_reset_defaults()
    assert shown, "完成提示未显示 / done message not shown"
    w.done(0)                                  # 触发兜底保存 / triggers the flush
    _app.processEvents()

    with open(cfg.config_file, encoding="utf-8") as f:
        saved = json.load(f)
    # format_filter_groups 的默认 None 表示「默认勾选」,设置页保存时写成同一份
    # 完整列表——生效值相同,按生效值比较。
    # format_filter_groups: None means "the default groups"; the page saves the
    # same list explicitly, so compare effective values.
    from core.format_filter import DEFAULT_FORMAT_GROUPS
    effective = {"format_filter_groups": list(DEFAULT_FORMAT_GROUPS)}
    wrong = {k: (saved.get(k), v) for k, v in AdvancedConfig.DEFAULT_CONFIG.items()
             if k not in AdvancedConfig.PRESERVE_ON_RESET
             and saved.get(k) != effective.get(k, v)}
    assert not wrong, wrong


def test_settings_center_cancel_changes_nothing(monkeypatch):
    from tools.i18n import get_i18n
    from ui.custom_dialogs import DeleteConfirmDialog
    from ui.settings_center import SettingsCenter

    cfg = get_advanced_config()
    cfg.config.update(_non_default_values())
    cfg.save()
    before = copy.deepcopy(cfg.config)

    def _cancel(self):
        self.choose("cancel")
        return 0

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _cancel)
    w = SettingsCenter(get_i18n(), start_page="about")
    w._on_reset_defaults()
    assert cfg.config == before
    assert not [f for f in os.listdir(os.path.dirname(cfg.config_file)) if ".bak-" in f]
    w.close()
