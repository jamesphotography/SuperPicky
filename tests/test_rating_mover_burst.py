#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
issue #173：结果浏览器改星级后，连拍组（burst_xxx）里的照片不随星级换目录。

处理流程把整组连拍放进「组内最高星级」对应的目录（photo_processor 连拍整理段：
compute_target_folder(highest_rating, ...)）。改星级时 rating_mover 原先对 burst_
子目录一律跳过，于是用户把 3★ 全部改成 1★ 后，3星_优选 下的 burst_xxx 原封不动。

修复后的约定：连拍组整组移动，目标目录仍按「组内最高星级」计算（与处理流程同一规则）；
组内最高星级不变就不动。

Issue #173: rating changes never moved photos inside burst_xxx folders. A burst
folder lives under the group's highest rating (same rule as processing), so after a
change the whole folder follows the new highest rating; unchanged highest = no move.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rating_mover import move_photo_on_metadata_change


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住中文界面：断言里写死了中文目录名（3星_优选、其他鸟类）。
    Pin zh_CN: assertions hard-code Chinese folder names.
    """
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if original != "zh_CN":
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _touch(path: str) -> str:
    """建空文件（连同父目录）/ Create an empty file with its parents."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _make_burst(root: str, folder_rel: str, ratings: list, burst_id: int = 7):
    """
    在 root/folder_rel/burst_007 下建一组连拍，并写入真实 ReportDB。

    参数 / Parameters:
        root (str): 批处理根目录 / Batch root.
        folder_rel (str): 连拍目录的父目录（相对路径）/ Parent of the burst folder.
        ratings (list): 每张照片的星级 / Rating of each member.
        burst_id (int): 连拍组 ID / Burst group id.

    返回 / Returns:
        tuple: (ReportDB, [photo dict]) —— photo 与浏览器缓存里的形态一致（绝对路径）。
    """
    from tools.report_db import ReportDB
    db = ReportDB(root)
    photos = []
    for i, rating in enumerate(ratings):
        name = f"Z8B_{2870 + i}"
        rel = os.path.join(folder_rel, f"burst_{burst_id:03d}", name + ".NEF")
        _touch(os.path.join(root, rel))
        _touch(os.path.join(root, folder_rel, f"burst_{burst_id:03d}", name + ".xmp"))
        db.insert_photo({
            "filename": name, "current_path": rel, "original_path": name + ".NEF",
            "bird_species_cn": "红隼", "bird_species_en": "Common Kestrel",
            "rating": rating, "burst_id": burst_id, "burst_position": i,
        })
        photos.append({
            "filename": name, "current_path": os.path.join(root, rel),
            "bird_species_cn": "红隼", "bird_species_en": "Common Kestrel",
            "rating": rating, "burst_id": burst_id,
        })
    return db, photos


def _batch_set(root: str, db, photos: list, new_rating: int) -> None:
    """
    模拟浏览器批量改星：先逐张写 DB，再逐张调用移动（与 _apply_batch_rating 同序）。
    Mimic the browser batch: DB writes first, then one mover call per photo.
    """
    for p in photos:
        db.update_photo(p["filename"], {"rating": new_rating})
        p["rating"] = new_rating
    for p in photos:
        move_photo_on_metadata_change(
            root, p, new_rating, "红隼", "species-first", db, p["filename"])


def test_whole_burst_downgraded_moves_folder_to_new_rating(tmp_path):
    """整组 3★ 改 1★：burst_007 整体搬到 其他鸟类/1星_普通，DB 与内存路径同步。"""
    root = str(tmp_path)
    db, photos = _make_burst(root, os.path.join("红隼", "3星_优选"), [3, 3, 3])

    _batch_set(root, db, photos, 1)

    new_dir = os.path.join(root, "其他鸟类", "1星_普通", "burst_007")
    assert sorted(os.listdir(new_dir)) == sorted(
        [p["filename"] + ext for p in photos for ext in (".NEF", ".xmp")])
    # 搬空的旧目录要清掉 / emptied old folders are pruned
    assert not os.path.exists(os.path.join(root, "红隼"))
    for p in photos:
        expected = os.path.join("其他鸟类", "1星_普通", "burst_007", p["filename"] + ".NEF")
        assert db.get_photo(p["filename"])["current_path"] == expected
        # 内存路径必须指向新位置：随后的元数据写入用的就是它
        # In-memory path must follow, the metadata write uses it next
        assert p["current_path"] == os.path.join(root, expected)
    db.close()


def test_burst_follows_highest_remaining_rating(tmp_path):
    """组内 3★+2★，把 3★ 改成 1★：最高星级变 2，整组进 红隼/2星_良好。"""
    root = str(tmp_path)
    db, photos = _make_burst(root, os.path.join("红隼", "3星_优选"), [3, 2, 2])

    _batch_set(root, db, photos[:1], 1)

    assert os.path.isdir(os.path.join(root, "红隼", "2星_良好", "burst_007"))
    assert not os.path.exists(os.path.join(root, "红隼", "3星_优选"))
    db.close()


def test_burst_stays_when_highest_rating_unchanged(tmp_path):
    """组内仍有 3★：不搬，返回 False。"""
    root = str(tmp_path)
    db, photos = _make_burst(root, os.path.join("红隼", "3星_优选"), [3, 3])

    db.update_photo(photos[0]["filename"], {"rating": 1})
    moved = move_photo_on_metadata_change(
        root, photos[0], 1, "红隼", "species-first", db, photos[0]["filename"])

    assert moved is False
    assert os.path.isdir(os.path.join(root, "红隼", "3星_优选", "burst_007"))
    db.close()


def test_burst_upgrade_rating_first_layout(tmp_path):
    """rating-first 布局下升星：2星_良好/红隼/burst_007 → 3星_优选/红隼/burst_007。"""
    root = str(tmp_path)
    db, photos = _make_burst(root, os.path.join("2星_良好", "红隼"), [2, 2])

    db.update_photo(photos[1]["filename"], {"rating": 3})
    moved = move_photo_on_metadata_change(
        root, photos[1], 3, "红隼", "rating-first", db, photos[1]["filename"])

    assert moved is True
    assert os.path.isdir(os.path.join(root, "3星_优选", "红隼", "burst_007"))
    db.close()


def test_burst_target_exists_is_left_alone(tmp_path):
    """目标位置已有同名 burst 目录：整组不动，不覆盖。"""
    root = str(tmp_path)
    db, photos = _make_burst(root, os.path.join("红隼", "3星_优选"), [3])
    occupant = _touch(os.path.join(root, "其他鸟类", "1星_普通", "burst_007", "OTHER.NEF"))

    db.update_photo(photos[0]["filename"], {"rating": 1})
    moved = move_photo_on_metadata_change(
        root, photos[0], 1, "红隼", "species-first", db, photos[0]["filename"])

    assert moved is False
    assert os.path.exists(occupant)
    assert os.path.exists(photos[0]["current_path"])
    db.close()
