# -*- coding: utf-8 -*-
"""
裁剪导出 / Crop export.

把选中的裁剪框应用到「全分辨率原图」(RAW/JPEG/HEIF 经 EXIF-aware loader 解码),
保存为 JPEG,可选用 ExifTool 把原图 EXIF 复制到导出图。非破坏性——绝不覆盖原图。

Apply the chosen crop box to the full-resolution source image, save as JPEG, and
optionally copy the source EXIF onto the exported file. Never overwrites the source.
"""
from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

import cv2

Box = Tuple[int, int, int, int]


def default_out_path(src_path: str, suffix: str = "_crop") -> str:
    """
    由原图路径推导导出路径,输出恒为 .jpg。
    例:/a/b/IMG.NEF → /a/b/IMG_crop.jpg

    Derive an export path from the source; output is always .jpg.
    """
    d = os.path.dirname(src_path)
    stem = os.path.splitext(os.path.basename(src_path))[0]
    return os.path.join(d, f"{stem}{suffix}.jpg")


def export_crop(src_path: str, box: Optional[Box], out_path: str, *,
                jpeg_quality: int = 95, copy_exif: bool = True,
                exif_src: Optional[str] = None,
                _image_loader: Optional[Callable[[str], "object"]] = None) -> str:
    """
    读取源图(可解码的 JPEG/预览),按 box 裁剪后写 JPEG;box=None 表示导出整图副本。
    copy_exif=True 时用 ExifTool 把 EXIF 复制到导出图(失败不影响主流程);EXIF 来源默认
    为 src_path,可用 exif_src 指定为另一文件(如从原始 RAW 复制完整元数据,而像素取自
    可解码的预览)。返回写出的 out_path;源图无法解码时抛 ValueError。

    参数 / Parameters:
        src_path (str): 像素来源路径(须可被 loader 解码,如 JPEG / 嵌入预览)。
        box (Optional[Box]): (x1, y1, x2, y2) 像素坐标(src_path 坐标系);None=不裁剪。
        out_path (str): 导出文件路径(JPEG)。
        jpeg_quality (int): JPEG 质量 0-100。
        copy_exif (bool): 是否复制 EXIF 到导出图。
        exif_src (Optional[str]): EXIF 来源文件;None 时用 src_path。
                                  ExifTool 可从 RAW 读元数据,故可传原始 RAW 以保全元数据。
        _image_loader (Callable): 可注入的解码函数(测试用);默认 EXIF-aware loader。

    返回 / Returns:
        str: out_path。

    异常 / Raises:
        ValueError: 源图无法解码。
    """
    loader = _image_loader
    if loader is None:
        from core.crop_advisor import _load_image_exif_aware  # 复用 RAW/EXIF 方向解码
        loader = _load_image_exif_aware

    img = loader(src_path)
    if img is None:
        raise ValueError(f"无法解码图片 / cannot decode: {src_path}")

    h, w = img.shape[:2]
    if box is not None:
        x1, y1, x2, y2 = box
        # 夹到图像范围,保证裁剪框有效(至少 1px)
        x1 = max(0, min(int(x1), w - 1))
        x2 = max(x1 + 1, min(int(x2), w))
        y1 = max(0, min(int(y1), h - 1))
        y2 = max(y1 + 1, min(int(y2), h))
        img = img[y1:y2, x1:x2]

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    cv2.imwrite(out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])

    if copy_exif:
        try:
            from tools.exiftool_manager import get_exiftool_manager
            get_exiftool_manager().copy_exif(exif_src or src_path, out_path)
        except Exception:
            pass  # EXIF 复制失败不影响导出主流程

    return out_path
