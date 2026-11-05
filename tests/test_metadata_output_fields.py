#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
元数据输出字段开关（#153）/ Per-field metadata output switches (#153).

用户可以在设置中心「输出」页关掉 12 个字段中的任意几个。要守住的行为：
- 默认全开，升级后行为不变；老用户关过的「写入关键字」原样保留。
- 关掉的字段在所有写入路径上都不写：批量写本体、侧车、浏览器改星级、改鸟种。
- 重置只清开着的字段：关掉的字段里可能是用户自己的数据（比如真实城市）。
- 全部关掉时，写入与重置都不报失败。

断言一律落在「照片里读回来有没有这个字段」，并用中文样例确认没有乱码。

Users can switch off any of the 12 fields. Asserted on what reads back from the
photo, with Chinese sample values to catch mojibake.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.metadata_fields import (ALL_FIELDS, FIELD_IDS, LEAN_PRESET, filter_item,
                                  has_payload)

CN_TITLE = "白喉抚蜜鸟"
CN_CAPTION = "3★ 优选\n✓ 头部清晰：头部锐度 449"


# ── 纯逻辑 / Pure logic ──────────────────────────────────────────────────────

def test_field_table_has_twelve_unique_fields():
    """12 个字段、id 不重复；精简预设恰好是维护者定的 5 项。"""
    assert len(FIELD_IDS) == 12
    assert len(set(FIELD_IDS)) == 12
    assert LEAN_PRESET == {"rating", "pick", "caption", "sharpness", "aesthetic"}


def test_filter_item_drops_only_disabled_values():
    """关掉的字段被删；'file' 等非字段键保留；原字典不被修改。"""
    item = {"file": "a.jpg", "rating": 3, "sharpness": 95.3, "label": "Green"}
    out = filter_item(item, ALL_FIELDS - {"sharpness", "label"})
    assert out == {"file": "a.jpg", "rating": 3}
    assert item["sharpness"] == 95.3


def test_has_payload_treats_zero_as_a_value():
    """0 星、0 分是合法值；空 IUCN、空关键字不算要写的内容。"""
    assert has_payload({"file": "a", "rating": 0}) is True
    assert has_payload({"file": "a", "iucn_category": "", "keywords": []}) is False
    assert has_payload({"file": "a"}) is False


# ── 配置 / Config ────────────────────────────────────────────────────────────

def test_config_defaults_all_on_and_roundtrip(tmp_path):
    """默认全开；批量设置后重新加载仍保持；关键字就是 birdid_write_keywords。"""
    from advanced_config import AdvancedConfig

    path = str(tmp_path / "cfg.json")
    cfg = AdvancedConfig(config_file=path)
    assert cfg.enabled_metadata_fields() == ALL_FIELDS

    cfg.set_metadata_fields({"sharpness": False, "keywords": False})
    again = AdvancedConfig(config_file=path)
    assert again.metadata_field_enabled("sharpness") is False
    assert again.metadata_field_enabled("keywords") is False
    assert again.birdid_write_keywords is False
    assert again.metadata_field_enabled("rating") is True


def test_existing_keywords_choice_survives_upgrade(tmp_path):
    """升级前关过「写入关键字」的老用户，新开关里关键字仍是关的，其余默认开。"""
    import json

    from advanced_config import AdvancedConfig

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"birdid_write_keywords": False}), encoding="utf-8")
    cfg = AdvancedConfig(config_file=str(path))
    assert cfg.enabled_metadata_fields() == ALL_FIELDS - {"keywords"}


# ── 真实 exiftool 写入读回 / Real exiftool write + read-back ──────────────────

PIL = pytest.importorskip("PIL.Image")


def _manager():
    from tools.exiftool_manager import get_exiftool_manager
    return get_exiftool_manager()


def _set_mode(mode: str) -> None:
    from advanced_config import get_advanced_config
    get_advanced_config().set_metadata_write_mode(mode)


def _enable_only(fields) -> None:
    from advanced_config import get_advanced_config
    get_advanced_config().set_metadata_fields({f: f in fields for f in FIELD_IDS})


def _read(path: str) -> dict:
    return _manager().read_metadata(path, extra_args=[
        "-XMP:Title", "-XMP:Description", "-XMP:City", "-XMP:State",
        "-XMP:Label", "-XMP:Rating", "-XMP:Country"]) or {}


@pytest.fixture
def jpg():
    d = tempfile.mkdtemp(prefix="sp_fields_")
    path = os.path.join(d, "bird.jpg")
    PIL.new("RGB", (32, 32), (120, 140, 90)).save(path, "JPEG")
    return path


@pytest.fixture
def nef():
    d = tempfile.mkdtemp(prefix="sp_fields_")
    path = os.path.join(d, "DSC_0001.NEF")
    with open(path, "wb") as handle:
        handle.write(b"\x00" * 64)
    return path


def _full_item(path: str) -> dict:
    return {"file": path, "rating": 3, "pick": 1, "sharpness": 449.0,
            "nima_score": 4.5, "label": "Green", "focus_status": "BEST",
            "title": CN_TITLE, "caption": CN_CAPTION}


def test_batch_embedded_skips_disabled_fields(jpg):
    """嵌入写入（JPG）：关掉的字段读不回来，开着的中文值无乱码。"""
    _set_mode("embedded")
    _enable_only(ALL_FIELDS - {"sharpness", "label", "title"})

    stats = _manager().batch_set_metadata([_full_item(jpg)])

    assert stats["failed"] == 0
    data = _read(jpg)
    assert "City" not in data and "Label" not in data and "Title" not in data
    assert data.get("Description") == CN_CAPTION
    assert float(data.get("State")) == 4.5


def test_sidecar_skips_disabled_fields(nef):
    """专有 RAW 走侧车：关掉的字段不进 .xmp。"""
    _set_mode("embedded")
    _enable_only(LEAN_PRESET)

    stats = _manager().batch_set_metadata([_full_item(nef)])

    assert stats["failed"] == 0
    xmp = os.path.splitext(nef)[0] + ".xmp"
    data = _read(xmp)
    assert float(data.get("City")) == 449.0
    assert data.get("Description") == CN_CAPTION
    assert "Title" not in data and "Label" not in data and "Country" not in data


def test_all_off_writes_nothing_and_does_not_fail(nef):
    """全部关掉：批量写不报失败，也不生成侧车。"""
    _set_mode("embedded")
    _enable_only(set())

    stats = _manager().batch_set_metadata([_full_item(nef)])

    assert stats == {"success": 1, "failed": 0}
    assert not os.path.exists(os.path.splitext(nef)[0] + ".xmp")


def test_rating_change_respects_caption_switch(jpg):
    """浏览器改星级（set_rating_and_pick）：题注关掉时不写题注，星级照写。"""
    _set_mode("embedded")
    _enable_only(ALL_FIELDS - {"caption"})

    assert _manager().set_rating_and_pick(jpg, 2, caption=CN_CAPTION) is True

    data = _read(jpg)
    assert int(data.get("Rating")) == 2
    assert "Description" not in data


def test_species_update_respects_title_switch(jpg):
    """改鸟种：标题关掉时不碰 Title（用户自己的标题保留）。"""
    _set_mode("embedded")
    mgr = _manager()
    assert mgr.set_metadata(jpg, {"XMP:Title": "我自己的标题"}) is True
    _enable_only(ALL_FIELDS - {"title"})

    assert mgr.update_species_metadata(jpg, CN_TITLE, write_keywords=False) is True

    assert _read(jpg).get("Title") == "我自己的标题"


def test_reset_keeps_disabled_fields(jpg):
    """重置只清开着的字段：锐度（城市）关掉时，城市里原有的值保留。"""
    _set_mode("embedded")
    mgr = _manager()
    _enable_only(ALL_FIELDS)
    mgr.batch_set_metadata([_full_item(jpg)])
    _enable_only(ALL_FIELDS - {"sharpness"})

    assert mgr.reset_metadata(jpg) is True

    data = _read(jpg)
    assert float(data.get("City")) == 449.0
    assert "Rating" not in data and "State" not in data


def test_batch_reset_all_off_is_a_noop(jpg):
    """全部关掉时批量重置不动文件，也不报失败。"""
    _set_mode("embedded")
    mgr = _manager()
    _enable_only(ALL_FIELDS)
    mgr.batch_set_metadata([_full_item(jpg)])
    _enable_only(set())

    stats = mgr.batch_reset_metadata([jpg], log_callback=lambda _m: None)

    assert stats == {"success": 1, "failed": 0}
    assert _read(jpg).get("Title") == CN_TITLE


# ── 界面 / UI ───────────────────────────────────────────────────────────────

def test_output_page_lean_and_select_all_buttons():
    """「一键精简」恰好勾 5 项并保存；「全部勾选」恢复 12 项；「不写入」时整区置灰。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    from advanced_config import get_advanced_config
    from tools.i18n import get_i18n
    from ui.settings_center import SettingsCenter

    cfg = get_advanced_config()
    w = SettingsCenter(get_i18n())
    try:
        w.show_page("output")
        assert set(w._meta_field_checks) == set(FIELD_IDS)

        w._on_metadata_fields_lean()
        checked = {f for f, c in w._meta_field_checks.items() if c.isChecked()}
        assert checked == LEAN_PRESET
        assert cfg.enabled_metadata_fields() == LEAN_PRESET

        w._on_metadata_fields_all()
        assert cfg.enabled_metadata_fields() == ALL_FIELDS

        w._xmp_none.setChecked(True)
        assert w._meta_fields_box.isEnabled() is False
        w._xmp_embedded.setChecked(True)
        assert w._meta_fields_box.isEnabled() is True
    finally:
        w.close()
