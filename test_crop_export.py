# -*- coding: utf-8 -*-
"""core/crop_export.py 单元测试:全分辨率裁剪导出 + 路径推导。"""
import os
import tempfile

import cv2
import numpy as np

from core.crop_export import export_crop, default_out_path


def _make_img(path: str, w: int = 200, h: int = 120) -> None:
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (40, 80, 160)
    cv2.imwrite(path, img)


def test_default_out_path():
    assert default_out_path("/x/y/Z9W1.NEF").replace("\\", "/") == "/x/y/Z9W1_crop.jpg"


def test_export_crop_writes_cropped_jpeg():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "a.png")
    _make_img(src)
    out = os.path.join(d, "a_crop.jpg")
    res = export_crop(src, (50, 30, 150, 90), out, copy_exif=False)
    assert res == out and os.path.exists(out)
    got = cv2.imread(out)
    assert got.shape[1] == 100 and got.shape[0] == 60  # 裁剪尺寸正确


def test_export_crop_none_box_is_full_copy():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "a.png")
    _make_img(src, 200, 120)
    out = os.path.join(d, "full.jpg")
    export_crop(src, None, out, copy_exif=False)
    got = cv2.imread(out)
    assert got.shape[1] == 200 and got.shape[0] == 120


def test_export_crop_clamps_out_of_bounds_box():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "a.png")
    _make_img(src, 200, 120)
    out = os.path.join(d, "clamp.jpg")
    # box 超出右下边界 → 应被夹到图像范围内,不报错
    export_crop(src, (150, 80, 9999, 9999), out, copy_exif=False)
    got = cv2.imread(out)
    assert got.shape[1] == 50 and got.shape[0] == 40
