# -*- coding: utf-8 -*-
"""
结果浏览器「复制到文件夹」核心逻辑（core/photo_copy.py）的回归测试。

- 一张照片 = 本体 + 同名 .xmp 边车 + 配套相机 JPEG，三者一起复制；
- 目标已有同名文件时整组改成「主名 (N)」，大小写不敏感，绝不覆盖；
- 先写临时文件再改名：取消、出错都不留半截文件；单个失败不影响其余文件。

Regression tests for the browser's "Copy to Folder" core: companions travel
with the photo, clashes rename the whole group, nothing is overwritten, and
cancel / failure never leaves a partial file behind.
"""
import os
import threading

import pytest

import core.photo_copy as pc


def _touch(path, data: bytes = b"x") -> str:
    """写一个小文件并返回路径 / Write a small file and return its path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _names(folder) -> list:
    return sorted(os.listdir(folder))


def test_sources_include_sidecar_and_paired_jpeg(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    xmp = _touch(tmp_path / "src" / "IMG_1.xmp")
    jpg = _touch(tmp_path / "src" / "IMG_1.JPG")
    _touch(tmp_path / "src" / "IMG_2.xmp")  # 别的照片的边车不算 / another photo's sidecar
    sources = pc.photo_copy_sources(raw)
    assert sources[0] == os.path.normpath(raw)
    assert sorted(map(os.path.basename, sources)) == sorted(
        map(os.path.basename, [raw, xmp, jpg]))


def test_sources_empty_when_main_file_missing(tmp_path):
    assert pc.photo_copy_sources(str(tmp_path / "gone.CR3")) == []
    assert pc.photo_copy_sources("") == []


def test_plan_keeps_names_without_clash(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3", b"raw")
    _touch(tmp_path / "src" / "IMG_1.xmp", b"xmp")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([raw], str(dest))
    assert plan.photo_count == 1 and plan.renamed == 0
    assert sorted(os.path.basename(j.dst) for j in plan.jobs) == ["IMG_1.CR3", "IMG_1.xmp"]
    assert plan.total_bytes == 6


def test_clash_on_any_file_renames_whole_group(tmp_path):
    """目标只有 IMG_1.xmp 重名，CR3 / xmp / JPG 也要一起改成 IMG_1 (1)。"""
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    _touch(tmp_path / "src" / "IMG_1.xmp")
    _touch(tmp_path / "src" / "IMG_1.JPG")
    dest = tmp_path / "dest"
    _touch(dest / "img_1.XMP")  # 大小写不同也算重名 / case-insensitive clash
    plan = pc.plan_photo_copy([raw], str(dest))
    assert plan.renamed == 1
    assert sorted(os.path.basename(j.dst) for j in plan.jobs) == [
        "IMG_1 (1).CR3", "IMG_1 (1).JPG", "IMG_1 (1).xmp"]


def test_same_name_from_two_folders_gets_numbered(tmp_path):
    a = _touch(tmp_path / "3star" / "IMG_1.CR3")
    b = _touch(tmp_path / "2star" / "IMG_1.CR3")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([a, b], str(dest))
    assert [os.path.basename(j.dst) for j in plan.jobs] == ["IMG_1.CR3", "IMG_1 (1).CR3"]
    assert plan.photo_count == 2 and plan.renamed == 1


def test_companion_selected_separately_is_copied_once(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    jpg = _touch(tmp_path / "src" / "IMG_1.JPG")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([raw, jpg, raw], str(dest))
    assert sorted(os.path.basename(j.dst) for j in plan.jobs) == ["IMG_1.CR3", "IMG_1.JPG"]
    assert plan.photo_count == 1


def test_missing_photo_is_counted_and_skipped(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([raw, str(tmp_path / "src" / "gone.CR3")], str(dest))
    assert plan.photo_count == 1 and plan.missing == 1


def test_source_folder_conflict(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    assert pc.source_folder_conflict([raw], str(tmp_path / "src"))
    assert not pc.source_folder_conflict([raw], str(tmp_path))


def test_execute_copies_content_and_timestamps(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3", b"raw-bytes")
    xmp = _touch(tmp_path / "src" / "IMG_1.xmp", "<x:xmpmeta 鸟名=\"白鹭\"/>".encode("utf-8"))
    os.utime(raw, (1_600_000_000, 1_600_000_000))
    dest = tmp_path / "dest"
    dest.mkdir()
    seen = []
    result = pc.execute_copy_plan(pc.plan_photo_copy([raw], str(dest)),
                                  progress_cb=lambda d, t: seen.append((d, t)))
    assert result.copied_files == 2 and result.copied_photos == 1
    assert not result.failed and not result.cancelled
    assert (dest / "IMG_1.CR3").read_bytes() == b"raw-bytes"
    assert (dest / "IMG_1.xmp").read_bytes() == open(xmp, "rb").read()
    assert int(os.path.getmtime(dest / "IMG_1.CR3")) == 1_600_000_000
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1]
    assert _names(dest) == ["IMG_1.CR3", "IMG_1.xmp"]  # 没有临时文件残留 / no temp leftovers


def test_cancel_before_start_copies_nothing(tmp_path):
    raw = _touch(tmp_path / "src" / "IMG_1.CR3")
    dest = tmp_path / "dest"
    dest.mkdir()
    cancel = threading.Event()
    cancel.set()
    result = pc.execute_copy_plan(pc.plan_photo_copy([raw], str(dest)), cancel)
    assert result.cancelled and result.copied_files == 0
    assert _names(dest) == []


def test_cancel_mid_file_leaves_no_partial_file(tmp_path, monkeypatch):
    """大文件写到一半取消：正式文件不出现，临时文件被删掉。"""
    monkeypatch.setattr(pc, "_CHUNK_BYTES", 4)
    raw = _touch(tmp_path / "src" / "IMG_1.CR3", b"0123456789abcdef")
    dest = tmp_path / "dest"
    dest.mkdir()
    cancel = threading.Event()

    def _progress(done, total):
        if done >= 8:
            cancel.set()

    result = pc.execute_copy_plan(pc.plan_photo_copy([raw], str(dest)), cancel, _progress)
    assert result.cancelled and result.copied_files == 0
    assert _names(dest) == []


def test_destination_created_meanwhile_is_not_overwritten(tmp_path):
    """计划之后目标里冒出同名文件：这个文件算失败，已有文件原样保留，其余照常复制。"""
    raw1 = _touch(tmp_path / "src" / "IMG_1.CR3", b"new")
    raw2 = _touch(tmp_path / "src" / "IMG_2.CR3", b"two")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([raw1, raw2], str(dest))
    _touch(dest / "IMG_1.CR3", b"user-file")
    result = pc.execute_copy_plan(plan)
    assert (dest / "IMG_1.CR3").read_bytes() == b"user-file"
    assert (dest / "IMG_2.CR3").read_bytes() == b"two"
    assert len(result.failed) == 1 and result.copied_photos == 1
    assert _names(dest) == ["IMG_1.CR3", "IMG_2.CR3"]


def test_unreadable_source_fails_alone(tmp_path):
    raw1 = _touch(tmp_path / "src" / "IMG_1.CR3", b"one")
    raw2 = _touch(tmp_path / "src" / "IMG_2.CR3", b"two")
    dest = tmp_path / "dest"
    dest.mkdir()
    plan = pc.plan_photo_copy([raw1, raw2], str(dest))
    os.remove(raw1)  # 复制前被删掉 / removed before the copy runs
    result = pc.execute_copy_plan(plan)
    assert len(result.failed) == 1 and result.copied_photos == 1
    assert _names(dest) == ["IMG_2.CR3"]


@pytest.mark.parametrize("name", ["IMG 1 鸟.CR3", "白鹭_001.NEF"])
def test_unicode_and_space_names(tmp_path, name):
    raw = _touch(tmp_path / "src" / name, b"data")
    dest = tmp_path / "目标"
    dest.mkdir()
    result = pc.execute_copy_plan(pc.plan_photo_copy([raw], str(dest)))
    assert result.copied_photos == 1
    assert _names(dest) == [name]
