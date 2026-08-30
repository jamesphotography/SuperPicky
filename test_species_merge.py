#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整种合并（merge_bird_species）的单元测试。

覆盖：跨星等目录整体搬迁、连拍组整组只搬一次、目标已存在同名文件时
必须报告失败而不是静默跳过、根目录（未整理）照片只改 DB 不移动。

Unit tests for whole-species merge. Covers cross-rating moves, burst
groups moved once as a unit, name collisions reported as failures
instead of being silently skipped, and root-level (unorganized) photos
getting a DB-only update.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from core.rating_mover import merge_bird_species


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文：本文件断言硬编码中文目录名（3星_优选 等），而目录名
    跟随全局 i18n 单例——同批先跑的测试若构造过 MainWindow 会把语言切走。

    Pin i18n to zh_CN; folder names follow the global i18n singleton and
    other tests in the same batch may switch it to en_US.
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

def _touch(path: str) -> str:
    """创建一个空文件（含父目录）。Create an empty file with its parents."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _photo(root: str, rel_current: str, rating: int, filename: str,
           burst_id=None, bird_cn: str = "白鹭") -> dict:
    """构造一条最小 photo 记录。Build a minimal photo record."""
    photo = {
        "filename": filename,
        "current_path": os.path.join(root, rel_current),
        "rating": rating,
        "bird_species_cn": bird_cn,
        "bird_species_en": "Little Egret",
    }
    if burst_id is not None:
        photo["burst_id"] = burst_id
    return photo


class FakeDB:
    """
    最小 ReportDB 替身：记录 update_photo 调用，并支持按 burst_id 查询。
    Minimal ReportDB stand-in recording update_photo calls.
    """

    def __init__(self, burst_photos: dict | None = None):
        self.updates: list = []
        self._burst_photos = burst_photos or {}

    def update_photo(self, key, data: dict) -> bool:
        self.updates.append((key, dict(data)))
        return True

    def get_photos_by_burst_id(self, burst_id: int) -> list:
        return self._burst_photos.get(burst_id, [])

    def species_of(self, key) -> dict:
        """返回该 key 最后一次写入的鸟种字段。Last species written for key."""
        result: dict = {}
        for k, data in self.updates:
            if k == key:
                result.update(data)
        return result


def _key(photo: dict) -> str:
    return photo["filename"]


# ── 测试 ─────────────────────────────────────────────────────────────────────

class TestMergeBirdSpecies:

    def test_moves_every_photo_of_species_across_rating_folders(self, tmp_path):
        """同一鸟种散落在多个星等目录，合并后全部落到新鸟种目录下的同名星等。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白鹭", "3星_优选", "A.NEF"))
        _touch(os.path.join(root, "白鹭", "2星_良好", "B.NEF"))
        photos = [
            _photo(root, os.path.join("白鹭", "3星_优选", "A.NEF"), 3, "A.NEF"),
            _photo(root, os.path.join("白鹭", "2星_良好", "B.NEF"), 2, "B.NEF"),
        ]
        db = FakeDB()

        result = merge_bird_species(
            root, photos, "中白鹭", "Intermediate Egret",
            "species-first", db, _key,
        )

        assert result["moved"] == 2
        assert result["failed"] == []
        assert os.path.exists(os.path.join(root, "中白鹭", "3星_优选", "A.NEF"))
        assert os.path.exists(os.path.join(root, "中白鹭", "2星_良好", "B.NEF"))
        assert not os.path.isdir(os.path.join(root, "白鹭"))
        assert db.species_of("A.NEF")["bird_species_cn"] == "中白鹭"
        assert db.species_of("B.NEF")["bird_species_cn"] == "中白鹭"

    def test_burst_group_moves_once_and_counts_every_member(self, tmp_path):
        """
        连拍组整组搬迁：组内两张都在 photos 列表里时，文件夹只搬一次，
        但两张都要计入 moved（否则界面会误报「失败 1 张」）。
        """
        root = str(tmp_path)
        burst_rel = os.path.join("白鹭", "3星_优选", "burst_001")
        _touch(os.path.join(root, burst_rel, "A.NEF"))
        _touch(os.path.join(root, burst_rel, "B.NEF"))
        photos = [
            _photo(root, os.path.join(burst_rel, "A.NEF"), 3, "A.NEF", burst_id=7),
            _photo(root, os.path.join(burst_rel, "B.NEF"), 3, "B.NEF", burst_id=7),
        ]
        db = FakeDB(burst_photos={7: [
            {"filename": "A.NEF", "current_path": os.path.join(burst_rel, "A.NEF")},
            {"filename": "B.NEF", "current_path": os.path.join(burst_rel, "B.NEF")},
        ]})

        result = merge_bird_species(
            root, photos, "中白鹭", "Intermediate Egret",
            "species-first", db, _key,
        )

        assert result["failed"] == []
        assert result["moved"] == 2
        new_burst = os.path.join(root, "中白鹭", "3星_优选", "burst_001")
        assert os.path.exists(os.path.join(new_burst, "A.NEF"))
        assert os.path.exists(os.path.join(new_burst, "B.NEF"))
        assert not os.path.isdir(os.path.join(root, "白鹭"))

    def test_reports_name_collision_instead_of_silently_skipping(self, tmp_path):
        """
        目标目录已有同名文件时，原实现会静默 continue（DB 改了、文件没搬，
        用户毫不知情）。合并必须把它作为失败回报，且原文件保持原位。
        """
        root = str(tmp_path)
        src = _touch(os.path.join(root, "白鹭", "3星_优选", "A.NEF"))
        _touch(os.path.join(root, "中白鹭", "3星_优选", "A.NEF"))
        photos = [_photo(root, os.path.join("白鹭", "3星_优选", "A.NEF"), 3, "A.NEF")]
        db = FakeDB()

        result = merge_bird_species(
            root, photos, "中白鹭", "Intermediate Egret",
            "species-first", db, _key,
        )

        assert result["moved"] == 0
        assert result["failed"] == [("A.NEF", "target_exists")]
        assert os.path.exists(src)
        # 文件没搬走就不该改 DB 鸟种，否则 DB 说「中白鹭」而文件还在「白鹭」目录里
        assert db.species_of("A.NEF") == {}

    def test_unorganized_root_photo_updates_db_without_moving(self, tmp_path):
        """
        根目录下（还没做过目录整理）的照片只改 DB 鸟名，不移动文件，
        并单独计入 db_only，让结果报告能说清「改了名但没搬」。
        """
        root = str(tmp_path)
        src = _touch(os.path.join(root, "A.NEF"))
        photos = [_photo(root, "A.NEF", 3, "A.NEF")]
        db = FakeDB()

        result = merge_bird_species(
            root, photos, "中白鹭", "Intermediate Egret",
            "species-first", db, _key,
        )

        assert result["moved"] == 0
        assert result["db_only"] == 1
        assert result["failed"] == []
        assert os.path.exists(src)
        assert db.species_of("A.NEF")["bird_species_cn"] == "中白鹭"

    def test_cancels_remaining_photos_when_callback_returns_false(self, tmp_path):
        """进度回调返回 False（用户点了取消）时停止处理剩余照片。"""
        root = str(tmp_path)
        _touch(os.path.join(root, "白鹭", "3星_优选", "A.NEF"))
        second = _touch(os.path.join(root, "白鹭", "2星_良好", "B.NEF"))
        photos = [
            _photo(root, os.path.join("白鹭", "3星_优选", "A.NEF"), 3, "A.NEF"),
            _photo(root, os.path.join("白鹭", "2星_良好", "B.NEF"), 2, "B.NEF"),
        ]
        db = FakeDB()

        result = merge_bird_species(
            root, photos, "中白鹭", "Intermediate Egret",
            "species-first", db, _key,
            progress_cb=lambda done, total, name: False,
        )

        assert result["cancelled"] is True
        assert result["moved"] == 1
        assert os.path.exists(second)
        assert db.species_of("B.NEF") == {}
