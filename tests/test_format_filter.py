# -*- coding: utf-8 -*-
"""
#126 文件格式筛选的回归测试。

规则（用户拍板）：
- 设置放在设置中心「精选」页，默认「全部」；
- 「只处理勾选的格式」时，与 RAW 同名的 JPG 跟随 RAW；没有同名 RAW 的
  「单独的 JPEG」默认不勾；
- 被筛掉的文件原地保留，并在日志中列出；全部被筛掉时明确提示。

Regression tests for the #126 file-format filter.
"""
import os
import types
from typing import List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.format_filter import (
    DEFAULT_FORMAT_GROUPS,
    FORMAT_GROUP_IDS,
    allowed_extensions,
    normalize_groups,
)

FILES = [
    "A.CR3", "A.jpg",          # RAW+JPG 配对 / RAW+JPG pair
    "B.dng",                   # 只有 DNG / DNG only
    "C.jpg",                   # 单独的 JPEG / standalone JPEG
    "D.NEF", "D.jpg",          # 尼康配对 / Nikon pair
    "E.CR3", "E.dng",          # 同名两种 RAW / two RAWs, same stem
]


def test_allowed_extensions_and_defaults():
    assert allowed_extensions("all", ["canon"]) is None
    assert "jpeg" not in DEFAULT_FORMAT_GROUPS
    assert set(DEFAULT_FORMAT_GROUPS) == set(FORMAT_GROUP_IDS) - {"jpeg"}
    assert allowed_extensions("selected", ["canon", "bogus"]) == frozenset({".cr2", ".cr3"})
    assert normalize_groups(["jpeg", "canon", "canon", "x"]) == ["canon", "jpeg"]


def test_config_defaults_and_validation(tmp_path):
    from advanced_config import AdvancedConfig
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    assert cfg.format_filter_mode == "all"
    assert cfg.format_filter_groups == list(DEFAULT_FORMAT_GROUPS)
    assert cfg.get_allowed_extensions() is None

    cfg.set_format_filter_mode("weird")
    assert cfg.format_filter_mode == "all"
    cfg.set_format_filter_mode("selected")
    cfg.set_format_filter_groups(["dng", "nope"])
    cfg.save()
    again = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    assert again.format_filter_mode == "selected"
    assert again.format_filter_groups == ["dng"]
    assert again.get_allowed_extensions() == frozenset({".dng"})


def _scan(tmp_path, monkeypatch, mode: str, groups) -> Tuple[dict, list, List[Tuple[str, str]]]:
    """
    用给定筛选设置扫描 FILES，返回 (raw_dict, files_tbr, 日志)。

    Scan FILES under the given filter; return (raw_dict, files_tbr, logs).
    """
    from advanced_config import AdvancedConfig
    from core.photo_processor import PhotoProcessor, ProcessingCallbacks, ProcessingSettings

    for name in FILES:
        (tmp_path / name).write_text("")
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    cfg.set_format_filter_mode(mode)
    cfg.set_format_filter_groups(groups)
    monkeypatch.setattr("core.photo_processor.get_advanced_config", lambda: cfg)
    logs: List[Tuple[str, str]] = []
    proc = PhotoProcessor(str(tmp_path), ProcessingSettings(),
                          ProcessingCallbacks(log=lambda m, level="info": logs.append((level, m))))
    raw, _jpg, tbr = proc._scan_files()
    return raw, sorted(tbr), logs


def _warnings(logs) -> str:
    return "\n".join(m for level, m in logs if level == "warning")


def test_all_mode_unchanged(tmp_path, monkeypatch):
    raw, tbr, logs = _scan(tmp_path, monkeypatch, "all", [])
    assert raw == {"A": ".CR3", "B": ".dng", "D": ".NEF", "E": ".CR3"}
    assert tbr == ["A.jpg", "C.jpg", "D.jpg"]
    assert "×" not in _warnings(logs)          # 没有筛选提示 / no filter summary


def test_canon_only_keeps_paired_jpg_and_skips_the_rest(tmp_path, monkeypatch):
    """只勾 Canon：CR3 与同名 JPG 处理；DNG、NEF 组、单独 JPEG 原地跳过。"""
    raw, tbr, logs = _scan(tmp_path, monkeypatch, "selected", ["canon"])
    assert raw == {"A": ".CR3", "E": ".CR3"}
    assert tbr == ["A.jpg"]
    w = _warnings(logs)
    assert ".dng ×2" in w and ".jpg ×2" in w and ".nef ×1" in w
    for name in FILES:                          # 文件都还在原地 / nothing moved
        assert (tmp_path / name).exists()


def test_standalone_jpeg_only_when_checked(tmp_path, monkeypatch):
    raw, tbr, _ = _scan(tmp_path, monkeypatch, "selected", ["canon", "jpeg"])
    assert tbr == ["A.jpg", "C.jpg"]            # D.jpg 跟随被筛掉的 NEF / follows the NEF


def test_dng_only_makes_dng_the_main_file(tmp_path, monkeypatch):
    """只勾 DNG：同名的 CR3 被筛掉，E.dng 成为主文件；CR3 的配对 JPG 不处理。"""
    raw, tbr, _ = _scan(tmp_path, monkeypatch, "selected", ["dng"])
    assert raw == {"B": ".dng", "E": ".dng"}
    assert tbr == []


def test_nothing_checked_warns_explicitly(tmp_path, monkeypatch):
    from tools.i18n import get_i18n
    raw, tbr, logs = _scan(tmp_path, monkeypatch, "selected", [])
    assert raw == {} and tbr == []
    assert get_i18n().t("logs.format_filter_nothing_left") in _warnings(logs)


@pytest.fixture
def center(monkeypatch, tmp_path):
    """
    全新配置的设置中心，停在精选页 / Settings Center with a fresh config.
    """
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from advanced_config import AdvancedConfig
    import advanced_config as _ac_mod
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))
    monkeypatch.setattr(_ac_mod, "get_advanced_config", lambda: cfg)
    w = SettingsCenter(get_i18n())
    w.show_page("culling")
    yield w, cfg
    w.close()


def test_settings_center_persists_mode_and_groups(center):
    w, cfg = center
    assert w._format_mode_combo.currentData() == "all"
    assert w._format_groups_box.isHidden()
    assert not w._format_checks["jpeg"].isChecked()          # 默认不勾 / unchecked by default

    w._format_mode_combo.setCurrentIndex(w._format_mode_combo.findData("selected"))
    assert cfg.format_filter_mode == "selected"
    assert not w._format_groups_box.isHidden()

    w._format_checks["nikon"].setChecked(False)
    assert "nikon" not in cfg.format_filter_groups
    assert "canon" in cfg.format_filter_groups


def test_start_dialog_mentions_active_filter(tmp_path):
    from advanced_config import AdvancedConfig
    from tools.i18n import get_i18n
    from ui.main_window import SuperPickyMainWindow
    fake = types.SimpleNamespace(i18n=get_i18n())
    cfg = AdvancedConfig(config_file=str(tmp_path / "cfg.json"))

    assert SuperPickyMainWindow._format_filter_summary(fake, cfg) == ""
    cfg.set_format_filter_mode("selected")
    cfg.set_format_filter_groups(["canon", "dng"])
    text = SuperPickyMainWindow._format_filter_summary(fake, cfg)
    assert "Canon" in text and "DNG" in text
    cfg.set_format_filter_groups([])
    assert SuperPickyMainWindow._format_filter_summary(fake, cfg) == \
        get_i18n().t("dialogs.note_format_filter_none")
