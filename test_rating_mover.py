#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/rating_mover.py 的单元测试。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from core.rating_mover import move_photo_on_metadata_change


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文:本文件断言硬编码中文目录名(3星_优选 等),而
    get_rating_folder_name 跟随全局 i18n 单例——同批先跑的测试若构造过
    MainWindow(加载用户真实配置,可能是 en_US)会把语言切走,导致文件被
    移到英文目录名下(2026-07-13 nightly 合并回归时实际踩到)。

    Pin the i18n singleton to zh_CN: assertions here hardcode Chinese
    folder names while get_rating_folder_name follows the global i18n —
    tests that construct MainWindow earlier in the batch (loading the
    user's real config, possibly en_US) would switch the language and move
    files under English folder names (bitten for real on 2026-07-13).
    """
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _photo(root, rel_current, rel_jpeg=None, filename="DSC_1234", bird_cn="白腰雨燕"):
    photo = {
        "filename": filename,
        "current_path": os.path.join(root, rel_current),
        "bird_species_cn": bird_cn,
        "bird_species_en": "Pacific Swift",
    }
    if rel_jpeg:
        photo["temp_jpeg_path"] = os.path.join(root, rel_jpeg)
    return photo


# ── 测试 ─────────────────────────────────────────────────────────────────────

class TestMovePhotoOnMetadataChange:

    def test_moves_raw_to_new_rating_folder(self, tmp_path):
        """星等从 2 改为 3 应将 RAW 移到新目录。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        result = move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is True
        assert not os.path.exists(src)
        assert os.path.exists(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.NEF"))

    def test_no_move_when_same_folder(self, tmp_path):
        """新旧目录相同时返回 False，不移动文件。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/3星_优选/DSC_1234.NEF")

        result = move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is False
        assert os.path.exists(src)

    def test_skips_burst_subfolder(self, tmp_path):
        """burst_ 子目录内的照片不移动。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "burst_001", "DSC_1234.NEF"))
        photo = _photo(root, os.path.join("白腰雨燕", "2星_良好", "burst_001", "DSC_1234.NEF"))

        result = move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is False
        assert os.path.exists(src)

    def test_skips_root_level_file(self, tmp_path):
        """文件直接在根目录（未整理）时不移动。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "DSC_1234.NEF"))
        photo = _photo(root, "DSC_1234.NEF")

        result = move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is False
        assert os.path.exists(src)

    def test_moves_jpeg_sidecar_together(self, tmp_path):
        """配套 JPEG 随 RAW 一起移动。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        jpeg_src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.JPG"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF",
                       rel_jpeg="白腰雨燕/2星_良好/DSC_1234.JPG")

        move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert not os.path.exists(jpeg_src)
        assert os.path.exists(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.JPG"))

    def test_moves_xmp_sidecar_together(self, tmp_path):
        """XMP sidecar 随 RAW 一起移动。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        xmp_src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.xmp"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert not os.path.exists(xmp_src)
        assert os.path.exists(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.xmp"))

    def test_updates_manifest(self, tmp_path):
        """移动后 manifest 的 folder 字段应更新。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        manifest = {
            "version": "2.0",
            "files": [{"filename": "DSC_1234.NEF", "folder": "白腰雨燕/2星_良好"}],
        }
        manifest_path = os.path.join(root, ".superpicky_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        with open(manifest_path, encoding="utf-8") as f:
            updated = json.load(f)
        assert updated["files"][0]["folder"] == os.path.join("白腰雨燕", "3星_优选")

    def test_skips_move_when_target_exists(self, tmp_path):
        """目标位置已有同名文件时跳过，不覆盖。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        existing = _touch(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert os.path.exists(src)
        assert os.path.exists(existing)

    def test_updates_db_current_path(self, tmp_path):
        """移动后 DB update_photo 应被调用，含新 current_path。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        calls = {}

        class FakeDB:
            def update_photo(self, key, data):
                calls[key] = data

        move_photo_on_metadata_change(
            root, photo, 3, "白腰雨燕", "species-first", FakeDB(), "DSC_1234"
        )

        assert "DSC_1234" in calls
        assert "current_path" in calls["DSC_1234"]
        assert "3星_优选" in calls["DSC_1234"]["current_path"]
