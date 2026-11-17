# -*- coding: utf-8 -*-
"""
写入时保护真实地名：照片（含旁边的 .xmp 边车）已有真实地名时，不写锐度/美学/对焦。

SuperPicky 借城市/省份/国家存这三项分数；此前会冲掉带 GPS 的相机、Lightroom 里填过的真实地点。
用真实 ExifTool 写入并独立读回核对（中文地名经 UTF-8 临时文件写入）。

Writes must not overwrite a real place name in the photo or its .xmp sidecar.
Uses the real ExifTool and reads values back independently.
"""
import os
import subprocess
import tempfile

import pytest
from PIL import Image

from tools.exiftool_manager import get_exiftool_manager

SCORES = {"rating": 3, "sharpness": 612.34, "nima_score": 5.21, "focus_status": "BEST"}


@pytest.fixture
def mgr():
    m = get_exiftool_manager()
    m.drain_location_protected()
    return m


def _exiftool(mgr, *args):
    """直接调用同一个 ExifTool 可执行文件（不经常驻进程）/ run ExifTool directly."""
    return subprocess.run([mgr.exiftool_path, *args], capture_output=True, check=True).stdout


def _jpg(tmp_path, name="p.jpg"):
    path = tmp_path / name
    Image.new("RGB", (32, 32), (90, 120, 160)).save(path, quality=80)
    return str(path)


def _put(mgr, target, tag, value):
    """经 UTF-8 临时文件写一个（可能是中文的）值 / write a value through a UTF-8 temp file."""
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    try:
        _exiftool(mgr, "-charset", "utf8", "-charset", "iptc=utf8", "-codedcharacterset=utf8",
                  f"-{tag}<={tmp}", "-overwrite_original", target)
    finally:
        os.remove(tmp)


def _read(mgr, path, tag):
    """
    原样读一个标签值（纯文本 -s3，不用 -j：JSON 会把 "0612.34" 转成数值丢掉前导 0）；没有时为 None。
    Read a tag verbatim (-s3 text; -j would turn "0612.34" into a number); None if absent.
    """
    out = _exiftool(mgr, "-s3", "-charset", "utf8", f"-{tag}", path).decode("utf-8").strip()
    return out or None


def _with_city(mgr, tmp_path, city, name="p.jpg"):
    p = _jpg(tmp_path, name)
    _put(mgr, p, "XMP-photoshop:City", city)
    return p


def test_real_city_kept_scores_skipped(mgr, tmp_path):
    """已有真实城市：城市保持、国家不写入对焦，并记录这张照片。"""
    p = _with_city(mgr, tmp_path, "布里斯班")
    mgr.batch_set_metadata([{"file": p, **SCORES}])
    assert _read(mgr, p, "XMP-photoshop:City") == "布里斯班"
    assert _read(mgr, p, "XMP-photoshop:Country") is None
    assert p in mgr.drain_location_protected()


def test_protected_photo_still_gets_rating(mgr, tmp_path):
    """受保护的照片星级照写。"""
    p = _with_city(mgr, tmp_path, "布里斯班")
    mgr.batch_set_metadata([{"file": p, **SCORES}])
    assert _read(mgr, p, "XMP-xmp:Rating") == "3"


def test_tool_written_values_are_overwritten(mgr, tmp_path):
    """SuperPicky 上次写的值照常被覆盖，不记录。"""
    p = _with_city(mgr, tmp_path, "0500.00")
    mgr.batch_set_metadata([{"file": p, **SCORES}])
    assert _read(mgr, p, "XMP-photoshop:City") == "612.34"
    assert mgr.drain_location_protected() == []


def test_iptc_only_location_protected(mgr, tmp_path):
    """只有 IPTC 城市（老 JPG）也受保护。"""
    p = _jpg(tmp_path)
    _put(mgr, p, "IPTC:City", "Brisbane")
    mgr.batch_set_metadata([{"file": p, **SCORES}])
    assert _read(mgr, p, "XMP-photoshop:City") is None


def test_batch_mixes_protected_and_unprotected(mgr, tmp_path):
    """一批里只跳过受保护的那张。"""
    a = _with_city(mgr, tmp_path, "布里斯班", "a.jpg")
    b = _jpg(tmp_path, "b.jpg")
    mgr.batch_set_metadata([{"file": a, **SCORES}, {"file": b, **SCORES}])
    assert _read(mgr, a, "XMP-photoshop:City") == "布里斯班"
    assert _read(mgr, b, "XMP-photoshop:City") == "612.34"


def test_sidecar_only_location_protects_body_write(mgr, tmp_path):
    """真实地名只在旁边的 .xmp 边车里：写本体时也受保护。"""
    p = _jpg(tmp_path)
    sidecar = os.path.splitext(p)[0] + ".xmp"
    _exiftool(mgr, "-o", sidecar, p)
    _put(mgr, sidecar, "XMP-photoshop:City", "布里斯班")
    mgr.batch_set_metadata([{"file": p, **SCORES}])
    assert _read(mgr, p, "XMP-photoshop:City") is None
    assert p in mgr.drain_location_protected()
