# -*- coding: utf-8 -*-
"""
检测与整理解耦测试:平铺布局(flat)+连拍子目录开关+rating_mover 根目录跳过回归钉。

Tests for detection/organization decoupling: the flat layout, the burst
subfolder toggle, and a regression pin for rating_mover's root-photo skip.
"""
import os
import tempfile


def test_flat_layout_constant_and_target_folder():
    """
    flat 进 VALID_LAYOUTS,normalize 合法,compute_target_folder 返回空(根目录)。
    "flat" is a valid layout; compute_target_folder returns "" (stay in root).
    """
    from core.folder_layout import (
        LAYOUT_FLAT, VALID_LAYOUTS, normalize_layout, compute_target_folder,
    )

    assert LAYOUT_FLAT == "flat"
    assert LAYOUT_FLAT in VALID_LAYOUTS
    assert normalize_layout("flat") == "flat"
    assert compute_target_folder(3, "白胸鸲鹟", LAYOUT_FLAT, "其他鸟类") == ""
    assert compute_target_folder(-1, None, LAYOUT_FLAT, "其他鸟类") == ""
    # 未知布局仍回退默认(不受 flat 影响) / unknown still falls back to default
    assert normalize_layout("nonsense") != "flat"


def test_rating_mover_skips_root_photos():
    """
    回归钉:根目录照片改星不移动文件(平铺模式下浏览器改星安全的依据)。
    Regression pin: rating changes on root photos never move files — the
    guarantee that makes browser edits safe under the flat layout.
    """
    from core.rating_mover import move_photo_on_metadata_change

    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "DSC0001.NEF")
        with open(raw, "wb") as f:
            f.write(b"fake")
        photo = {"filename": "DSC0001", "current_path": raw}
        moved = move_photo_on_metadata_change(
            td, photo, new_rating=3, new_bird_name=None,
            layout="species-first", report_db=None, db_key="DSC0001",
        )
        assert moved is False                     # 根目录 → 跳过 / root → skip
        assert os.path.exists(raw)                # 文件未动 / file untouched
        assert sorted(os.listdir(td)) == ["DSC0001.NEF"]
