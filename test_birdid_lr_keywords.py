# -*- coding: utf-8 -*-
"""
识鸟结果写入 Lightroom 关键字(XMP-dc:Subject)的测试:纯合并逻辑 +
真实 exiftool 端到端(含中文/幂等/保留用户关键字)。

Tests for writing Bird ID results into Lightroom keywords: pure merge
logic plus a real-exiftool end-to-end roundtrip (Chinese values,
idempotency, preservation of user keywords).
"""
import os
import subprocess
import tempfile

import pytest


def test_merge_keyword_lists_semantics():
    """
    合并语义:保留已有、追加缺失、去重、无新增返回 None、中文正常。
    Merge semantics: keep existing, append missing, dedup, None when
    nothing to add, Chinese values handled.
    """
    from tools.exiftool_manager import merge_keyword_lists

    assert merge_keyword_lists([], ["白胸鸲鹟"]) == ["白胸鸲鹟"]
    assert merge_keyword_lists(["UserKW"], ["白胸鸲鹟"]) == ["UserKW", "白胸鸲鹟"]
    assert merge_keyword_lists(["白胸鸲鹟"], ["白胸鸲鹟"]) is None      # 已存在
    assert merge_keyword_lists(["A", "B"], ["B", "A"]) is None          # 全存在
    assert merge_keyword_lists(["A"], ["B", "B"]) == ["A", "B"]         # 输入去重
    assert merge_keyword_lists([], []) is None                          # 空输入


def _exiftool_read_subject(path: str):
    out = subprocess.run(
        ["exiftool", "-j", "-XMP-dc:Subject", path],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    import json
    data = json.loads(out)[0]
    subj = data.get("Subject", [])
    return [subj] if isinstance(subj, str) else subj


@pytest.mark.skipif(
    subprocess.run(["which", "exiftool"], capture_output=True).returncode != 0,
    reason="exiftool not on PATH",
)
def test_keywords_end_to_end_merge_and_idempotent():
    """
    端到端:临时 JPG 预置用户关键字 → manager 写鸟名关键字 → 读回含两者;
    重复写第二次不产生重复(幂等)。

    End-to-end: seed a user keyword on a temp JPG, write the species
    keyword through the manager, read back both; a second write adds no
    duplicates (idempotent).
    """
    from PIL import Image
    from tools.exiftool_manager import get_exiftool_manager

    with tempfile.TemporaryDirectory() as td:
        jpg = os.path.join(td, "kw_test.jpg")
        Image.new("RGB", (8, 8), (200, 120, 40)).save(jpg, "JPEG")
        subprocess.run(
            ["exiftool", "-overwrite_original", "-XMP-dc:Subject=UserKW", jpg],
            capture_output=True, check=True,
        )

        mgr = get_exiftool_manager()
        for _ in range(2):  # 第二次验证幂等 / second pass proves idempotency
            stats = mgr.batch_set_metadata([{"file": jpg, "keywords": ["白胸鸲鹟"]}])
            assert stats["failed"] == 0

        subjects = _exiftool_read_subject(jpg)
        assert subjects == ["UserKW", "白胸鸲鹟"], subjects


def test_birdid_write_keywords_config_roundtrip():
    """
    开关默认 True;set 后落盘,重新加载读回 False。
    Default True; persists to disk and reloads as False after set.
    """
    from advanced_config import AdvancedConfig

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp)
        assert cfg.birdid_write_keywords is True          # 默认开 / default on
        cfg.set_birdid_write_keywords(False)
        assert AdvancedConfig(config_file=tmp).birdid_write_keywords is False
    finally:
        os.unlink(tmp)


def test_settings_center_keywords_checkbox_saves():
    """
    识鸟页「写入关键字」复选框存在,取消勾选并保存后配置为 False。
    The Bird ID page checkbox exists and unchecking + save persists False.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    _orig = _ac_mod.get_advanced_config
    try:
        cfg = AdvancedConfig(config_file=tmp)
        _ac_mod.get_advanced_config = lambda: cfg   # settings_center 均为方法内局部 import
        w = SettingsCenter(get_i18n())
        w.show_page("birdid")
        assert w._bid_keywords.isChecked() is True   # 默认开 / default on
        w._bid_keywords.setChecked(False)
        w._save_birdid()
        assert cfg.birdid_write_keywords is False
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig
        os.unlink(tmp)
