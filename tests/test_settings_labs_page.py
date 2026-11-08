# -*- coding: utf-8 -*-
"""
设置中心「实验室」页测试。
Tests for the Settings Center "Labs" page.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from advanced_config import get_advanced_config
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_labs_page_in_order_before_about():
    """实验室页紧挨在关于页之前 / labs sits right before about."""
    from ui.settings_center import PAGE_ORDER
    assert PAGE_ORDER.index("labs") == PAGE_ORDER.index("about") - 1


def test_labs_checkboxes_reflect_and_save():
    """勾选立即写配置 / toggling saves immediately."""
    from ui.settings_center import SettingsCenter
    cfg = get_advanced_config()
    w = SettingsCenter(get_i18n(), start_page="labs")
    assert not w._lab_report_cb.isChecked()
    w._lab_multi_cb.setChecked(True)
    assert cfg.lab_multi_folder is True
    w._lab_report_cb.setChecked(True)
    assert cfg.lab_report_export is True
    w._lab_ebird_cb.setChecked(True)
    assert cfg.lab_ebird_export is True
    w.done(0)
    assert cfg.lab_multi_folder is True


def test_restore_defaults_rebuild_unchecks_labs(monkeypatch):
    """恢复默认后重建的实验室页全是未勾选 / rebuilt page shows all off."""
    from ui.custom_dialogs import DeleteConfirmDialog, StyledMessageBox
    from ui.settings_center import SettingsCenter
    cfg = get_advanced_config()
    cfg.set_lab_report_export(True)
    cfg.set_lab_multi_folder(True)
    cfg.save()
    monkeypatch.setattr(DeleteConfirmDialog, "exec", lambda self: (self.choose("reset"), 0)[1])
    monkeypatch.setattr(StyledMessageBox, "information", staticmethod(lambda *a, **k: None))
    w = SettingsCenter(get_i18n(), start_page="labs")
    w._on_reset_defaults()
    assert not any(cb.isChecked() for cb in (w._lab_report_cb, w._lab_ebird_cb, w._lab_multi_cb))
    w.done(0)
    assert cfg.lab_report_export is False and cfg.lab_multi_folder is False


def test_labs_locale_keys_present():
    """三个语言包都有全部新 key / all new keys exist in every locale."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    keys = ["nav_labs", "labs_more_title", "labs_more_hint", "labs_report",
            "labs_report_desc", "labs_ebird", "labs_ebird_desc", "labs_exp_title",
            "labs_exp_hint", "labs_multi_folder", "labs_multi_folder_desc", "labs_badge"]
    for name in ("zh_CN", "zh_TW", "en_US"):
        with open(root / "locales" / f"{name}.json", "r", encoding="utf-8") as f:
            s = json.load(f)["settings"]
        for k in keys:
            assert s.get(k), f"{name} missing settings.{k}"


# ── 视频处理移入实验室 / Video moved into Labs ──────────────────────────────

import pytest


@pytest.mark.parametrize("initial", [True, False])
def test_labs_video_switch_reflects_config(initial):
    """视频开关初值等于配置 / switch mirrors video_auto_process_in_main."""
    from ui.settings_center import SettingsCenter
    cfg = get_advanced_config()
    cfg.config["video_auto_process_in_main"] = initial
    w = SettingsCenter(get_i18n(), start_page="labs")
    try:
        assert w._video_auto_check.isChecked() is initial
        assert w._video_opts_box.isHidden() is (not initial)
    finally:
        w.done(0)


def test_labs_video_suboptions_follow_switch_and_keep_values():
    """关闭隐藏子选项、值保留、勾选写配置 / hide on off, keep values, save on check."""
    from ui.settings_center import SettingsCenter
    cfg = get_advanced_config()
    cfg.config["video_auto_process_in_main"] = False
    w = SettingsCenter(get_i18n(), start_page="labs")
    try:
        assert w._video_opts_box.isHidden()
        w._video_auto_check.setChecked(True)
        assert not w._video_opts_box.isHidden()
        assert cfg.config["video_auto_process_in_main"] is True
        w._video_max_frames.setValue(120)
        w._video_auto_check.setChecked(False)
        assert w._video_opts_box.isHidden()
        assert cfg.config["video_auto_process_in_main"] is False
        w._video_auto_check.setChecked(True)
        assert w._video_max_frames.value() == 120
    finally:
        w.done(0)


def test_video_first_run_prompt_removed():
    """首次视频提示已删除 / first-run video prompt is gone."""
    from advanced_config import AdvancedConfig
    from ui.main_window import SuperPickyMainWindow
    assert "video_first_run_prompted" not in AdvancedConfig.DEFAULT_CONFIG
    assert "video_first_run_prompted" not in AdvancedConfig.PRESERVE_ON_RESET
    assert not hasattr(SuperPickyMainWindow, "_maybe_prompt_video_first_run")


def test_video_locale_keys():
    """新文案存在、旧提示文案已删 / new copy present, old prompt copy removed."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("zh_CN", "zh_TW", "en_US"):
        with open(root / "locales" / f"{name}.json", "r", encoding="utf-8") as f:
            d = json.load(f)
        assert d["settings"].get("labs_video_desc")
        assert "video_first_tip_title" not in d["dialogs"]
        assert "video_first_tip_body" not in d["dialogs"]
