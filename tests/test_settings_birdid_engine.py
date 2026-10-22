# -*- coding: utf-8 -*-
"""
设置中心「识鸟模型」下拉框与关于页 BirdTrace 署名单测 / Settings tests for the Bird-ID model dropdown.
"""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _center(tmp_path, monkeypatch, engine, ready=True):
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    import advanced_config as ac
    from advanced_config import AdvancedConfig
    from birdid import bird_identifier as bi
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    cfg = AdvancedConfig(config_file=str(tmp_path / "c.json"))
    cfg.set_birdid_engine(engine)
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)
    monkeypatch.setattr(bi, "birdtrace_ready", lambda: ready)
    w = SettingsCenter(get_i18n())
    return w, cfg


def test_dropdown_lists_choices_and_persists(tmp_path, monkeypatch):
    from advanced_config import BIRDID_ENGINE_CHOICES

    w, cfg = _center(tmp_path, monkeypatch, "compare")
    w.show_page("birdid")
    items = [w._bid_engine.itemData(i) for i in range(w._bid_engine.count())]
    assert tuple(items) == tuple(BIRDID_ENGINE_CHOICES)       # 与 setter 取值一致 / matches the setter
    assert w._bid_engine.currentData() == "compare"
    w._bid_engine.setCurrentIndex(w._bid_engine.findData("osea"))
    assert cfg.birdid_engine == "osea"
    w.close()


def test_missing_birdtrace_shows_fallback_hint(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from tools.i18n import get_i18n

    w, _cfg = _center(tmp_path, monkeypatch, "compare", ready=False)
    w.show_page("birdid")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert get_i18n().t("settings.birdid_engine_missing") in texts
    w.close()


def test_about_page_links_birdtrace(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    w, _cfg = _center(tmp_path, monkeypatch, "compare")
    w.show_page("about")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert any("huggingface.co/warden123/BirdTrace-FastViT-MA36-10K" in t for t in texts)
    assert any("Warden" in t and "BirdTrace" in t for t in texts)
    w.close()
