# -*- coding: utf-8 -*-
"""
设置中心识鸟页与关于页单测：识鸟模型下拉框已随 OSEA 删除，关于页保留 BirdTrace 署名。
Settings tests: the Bird-ID model dropdown is gone with OSEA; the about page keeps BirdTrace credit.
"""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _center(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    import advanced_config as ac
    from advanced_config import AdvancedConfig
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    cfg = AdvancedConfig(config_file=str(tmp_path / "c.json"))
    monkeypatch.setattr(ac, "get_advanced_config", lambda: cfg)
    return SettingsCenter(get_i18n())


def test_birdid_page_has_no_engine_dropdown(tmp_path, monkeypatch):
    w = _center(tmp_path, monkeypatch)
    w.show_page("birdid")
    assert not hasattr(w, "_bid_engine")
    w.close()


def test_about_page_links_birdtrace(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel

    w = _center(tmp_path, monkeypatch)
    w.show_page("about")
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert any("huggingface.co/warden123/BirdTrace-FastViT-MA36-10K" in t for t in texts)
    assert any("Warden" in t and "BirdTrace" in t for t in texts)
    w.close()
