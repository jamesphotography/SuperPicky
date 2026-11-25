#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯 JPEG 照片（无 RAW）改鸟种 / 整种合并 / 改星级时的移动回归测试（issue #171）。

纯 JPEG 照片的 temp_jpeg_path 与 current_path 是同一个文件。原实现把它当成
「RAW + 配套 JPEG」搬两次：第一次成功，第二次发现目标目录已有「它自己」，
误报「目标目录已有同名文件」，且 temp_jpeg_path 留着已不存在的旧路径。

Regression tests for JPEG-only photos (issue #171): the JPEG is both the image
body and temp_jpeg_path, so it must be moved once, reported as moved with no
failure, and temp_jpeg_path must follow it to the new folder.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rating_mover import merge_bird_species, move_photo_on_metadata_change


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文：目录名跟随全局 i18n 单例，其他测试可能切走语言。

    Pin i18n to zh_CN; folder names follow the global i18n singleton.
    """
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


class FakeDB:
    """
    最小 ReportDB 替身：只记录 update_photo 调用。
    Minimal ReportDB stand-in recording update_photo calls.
    """

    def __init__(self) -> None:
        self.updates: list = []

    def update_photo(self, key, data: dict) -> bool:
        """记录一次更新 / Record one update."""
        self.updates.append((key, dict(data)))
        return True


def _make_jpeg_only(root: str, rel_folder: str, name: str,
                    jpeg_name: str | None = None) -> dict:
    """
    在 root/rel_folder 下建一张纯 JPEG 照片，返回与浏览器一致的 photo 记录。

    参数 / Args:
        root:       批处理根目录
        rel_folder: 照片所在的相对目录
        name:       磁盘上的文件名
        jpeg_name:  temp_jpeg_path 使用的文件名（默认同 name；可传大小写不同的写法）

    返回 / Returns:
        dict: current_path / temp_jpeg_path 均为绝对路径的照片记录

    Create a JPEG-only photo and return its record (absolute paths).
    """
    folder = os.path.join(root, rel_folder)
    os.makedirs(folder, exist_ok=True)
    body = os.path.join(folder, name)
    with open(body, "wb") as f:
        f.write(b"jpeg")
    return {
        "filename": os.path.splitext(name)[0],
        "current_path": body,
        "original_path": body,
        "temp_jpeg_path": os.path.join(folder, jpeg_name or name),
        "rating": 3,
        "bird_species_cn": "其他鸟类",
        "bird_species_en": "Other birds",
    }


def _files_under(root: str) -> list:
    """列出 root 下所有文件的相对路径 / List every file under root (relative)."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), root))
    return sorted(out)


def test_merge_jpeg_only_moves_once_without_false_failure(tmp_path):
    """整种合并纯 JPEG：算移动 1 张、无失败、temp_jpeg_path 跟到新目录。"""
    root = str(tmp_path)
    photo = _make_jpeg_only(root, os.path.join("其他鸟类", "3星_优选"), "PB221518.jpg")
    db = FakeDB()

    result = merge_bird_species(
        root, [photo], "白头鹎", "Light-vented Bulbul", "species-first",
        db, lambda p: p["filename"],
    )

    assert result["failed"] == []
    assert result["moved"] == 1
    assert _files_under(root) == [os.path.join("白头鹎", "3星_优选", "PB221518.jpg")]
    new_rel = os.path.join("白头鹎", "3星_优选", "PB221518.jpg")
    _key, data = db.updates[-1]
    assert data["current_path"] == new_rel
    assert data["temp_jpeg_path"] == new_rel
    assert photo["temp_jpeg_path"] == photo["current_path"]
    assert os.path.exists(photo["temp_jpeg_path"])


def test_merge_jpeg_only_case_insensitive_alias(tmp_path):
    """
    temp_jpeg_path 与 current_path 只差扩展名大小写（.JPG vs .jpg）：在不区分
    大小写的文件系统（macOS/Windows 默认）上是同一文件，也只能搬一次。

    On case-insensitive file systems X.JPG and X.jpg are one file.
    """
    probe = tmp_path / "Probe.JPG"
    probe.write_bytes(b"x")
    if not (tmp_path / "probe.jpg").exists():
        pytest.skip("文件系统区分大小写 / case-sensitive file system")
    probe.unlink()

    root = str(tmp_path)
    photo = _make_jpeg_only(root, os.path.join("其他鸟类", "3星_优选"),
                            "PB221518.JPG", jpeg_name="PB221518.jpg")
    db = FakeDB()

    result = merge_bird_species(
        root, [photo], "白头鹎", "Light-vented Bulbul", "species-first",
        db, lambda p: p["filename"],
    )

    assert result["failed"] == []
    assert result["moved"] == 1
    assert os.path.exists(photo["temp_jpeg_path"])


def test_rating_change_jpeg_only_updates_temp_jpeg_path(tmp_path):
    """改星级纯 JPEG：文件只搬一次，DB 的 temp_jpeg_path 也指向新位置。"""
    root = str(tmp_path)
    photo = _make_jpeg_only(root, os.path.join("白头鹎", "3星_优选"), "PB221518.jpg")
    db = FakeDB()

    moved = move_photo_on_metadata_change(
        root, photo, 2, "白头鹎", "species-first", db, photo["filename"],
    )

    assert moved is True
    files = _files_under(root)
    assert len(files) == 1 and files[0].endswith("PB221518.jpg")
    new_rel = files[0]
    _key, data = db.updates[-1]
    assert data["current_path"] == new_rel
    assert data["temp_jpeg_path"] == new_rel
    assert photo["temp_jpeg_path"] == photo["current_path"]
