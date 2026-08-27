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


class TestHighRatingFolders:
    """
    4★/5★ 手动升星的目录归属。

    详情面板 ▲ 可升到 5 星、对比视图有 1-5 星按钮，但 RATING_FOLDER_NAMES
    一度只定义到 3 星，get_rating_folder_name 的兜底 folders.get(0) 会把
    4/5 星照片静默搬进「0星_放弃」——比不移动更糟的反向操作。

    Manual 4/5-star ratings must land in their own folders; the folder-name
    table once stopped at 3 stars, so the get(0) fallback silently moved
    them into the reject pile.
    """

    def test_moves_to_five_star_folder(self, tmp_path):
        """星等从 3 改为 5 应移到「5星_杰作」，而不是回退到「0星_放弃」。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/3星_优选/DSC_1234.NEF")

        result = move_photo_on_metadata_change(
            root, photo, 5, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is True
        assert not os.path.exists(src)
        assert os.path.exists(os.path.join(root, "白腰雨燕", "5星_杰作", "DSC_1234.NEF"))
        assert not os.path.exists(os.path.join(root, "白腰雨燕", "0星_放弃"))

    def test_moves_to_four_star_folder(self, tmp_path):
        """星等从 2 改为 4 应移到「4星_精华」。"""
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白腰雨燕", "2星_良好", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/2星_良好/DSC_1234.NEF")

        result = move_photo_on_metadata_change(
            root, photo, 4, "白腰雨燕", "species-first", None, "DSC_1234"
        )

        assert result is True
        assert not os.path.exists(src)
        assert os.path.exists(os.path.join(root, "白腰雨燕", "4星_精华", "DSC_1234.NEF"))

    def test_high_rating_keeps_species_folder_in_rating_first(self, tmp_path):
        """rating-first 布局下 4 星同样按鸟种分子目录（rating >= 2 分支）。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "3星_优选", "白腰雨燕", "DSC_1234.NEF"))
        photo = _photo(root, "3星_优选/白腰雨燕/DSC_1234.NEF")

        move_photo_on_metadata_change(
            root, photo, 4, "白腰雨燕", "rating-first", None, "DSC_1234"
        )

        assert os.path.exists(os.path.join(root, "4星_精华", "白腰雨燕", "DSC_1234.NEF"))

    def test_five_star_updates_db_and_manifest(self, tmp_path):
        """升到 5 星后 DB current_path 与 manifest folder 都指向新目录。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白腰雨燕", "3星_优选", "DSC_1234.NEF"))
        photo = _photo(root, "白腰雨燕/3星_优选/DSC_1234.NEF")

        manifest_path = os.path.join(root, ".superpicky_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": "2.0",
                 "files": [{"filename": "DSC_1234.NEF", "folder": "白腰雨燕/3星_优选"}]},
                f,
            )

        calls = {}

        class FakeDB:
            def update_photo(self, key, data):
                calls[key] = data

        move_photo_on_metadata_change(
            root, photo, 5, "白腰雨燕", "species-first", FakeDB(), "DSC_1234"
        )

        assert "5星_杰作" in calls["DSC_1234"]["current_path"]
        with open(manifest_path, encoding="utf-8") as f:
            updated = json.load(f)
        assert updated["files"][0]["folder"] == os.path.join("白腰雨燕", "5星_杰作")


class TestRatingFolderNameTable:
    """
    星级 → 目录名映射表的完整性回归。

    钉死 4/5 星不再回退到 0 星目录名（get_rating_folder_name 的兜底）。
    Regression guard: every rating -1..5 must map to a distinct folder name.
    """

    def test_every_rating_has_its_own_folder_name(self):
        """-1..5 每个星级都有目录名，且 4/5 星与 0 星目录名不同（中英双语）。"""
        from constants import RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN

        for table in (RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN):
            for rating in (-1, 0, 1, 2, 3, 4, 5):
                assert rating in table, f"星级 {rating} 缺少目录名 / missing folder name"
            # -1 与 0 共用「放弃」目录是既定设计，其余星级必须各自独立
            assert table[4] != table[0]
            assert table[5] != table[0]
            assert table[4] != table[3]
            assert table[5] != table[4]

    def test_get_rating_folder_name_no_silent_fallback(self):
        """get_rating_folder_name 对 4/5 星不再静默回退到 0 星目录。"""
        from constants import get_rating_folder_name

        reject = get_rating_folder_name(0)
        assert get_rating_folder_name(4) != reject
        assert get_rating_folder_name(5) != reject
