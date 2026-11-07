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
