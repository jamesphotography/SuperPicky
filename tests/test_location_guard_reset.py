# -*- coding: utf-8 -*-
"""
重置时保护真实地名：只清 SuperPicky 写的城市/省份/国家，真实地名保留。

此前重置会把照片里的真实地点（带 GPS 的相机、Lightroom 里填过的）一起清空。
用真实 ExifTool 写入并独立读回核对。

Reset clears only SuperPicky's City/State/Country values and keeps a real
place name. Uses the real ExifTool and reads values back independently.
"""
import os
import subprocess
import tempfile

import pytest
from PIL import Image

from tools.exiftool_manager import get_exiftool_manager


@pytest.fixture
def mgr():
    return get_exiftool_manager()


def _exiftool(mgr, *args):
    return subprocess.run([mgr.exiftool_path, *args], capture_output=True, check=True).stdout


def _photo(mgr, tmp_path, name, city):
    """带城市与 3 星的小 JPG（城市经 UTF-8 临时文件写入）/ a small JPG with a city and 3 stars."""
    path = str(tmp_path / name)
    Image.new("RGB", (32, 32), (90, 120, 160)).save(path, quality=80)
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(city)
    try:
        _exiftool(mgr, "-charset", "utf8", f"-XMP-photoshop:City<={tmp}", "-XMP:Rating=3",
                  "-overwrite_original", path)
    finally:
        os.remove(tmp)
    return path


def _read(mgr, path, tag):
    """原样读一个标签值（-s3）；没有时为 None / read a tag verbatim, None if absent."""
    out = _exiftool(mgr, "-s3", "-charset", "utf8", f"-{tag}", path).decode("utf-8").strip()
    return out or None


def test_reset_keeps_real_city(mgr, tmp_path):
    """真实城市保留，星级照常清掉。"""
    p = _photo(mgr, tmp_path, "a.jpg", "布里斯班")
    mgr.reset_metadata(p)
    assert _read(mgr, p, "XMP-photoshop:City") == "布里斯班"
    assert _read(mgr, p, "XMP-xmp:Rating") in (None, "0")


def test_reset_clears_tool_written_location(mgr, tmp_path):
    """SuperPicky 写的城市照常清空。"""
    p = _photo(mgr, tmp_path, "b.jpg", "612.34")
    mgr.reset_metadata(p)
    assert _read(mgr, p, "XMP-photoshop:City") is None


def test_batch_reset_mixed(mgr, tmp_path):
    """批量重置：受保护的那张城市保留，另一张清空，两张星级都清掉。"""
    a = _photo(mgr, tmp_path, "a.jpg", "布里斯班")
    b = _photo(mgr, tmp_path, "b.jpg", "612.34")
    mgr.batch_reset_metadata([a, b])
    assert _read(mgr, a, "XMP-photoshop:City") == "布里斯班"
    assert _read(mgr, b, "XMP-photoshop:City") is None
    assert _read(mgr, a, "XMP-xmp:Rating") in (None, "0")
    assert _read(mgr, b, "XMP-xmp:Rating") in (None, "0")


def test_reset_clears_stale_xmp_keeps_real_iptc(mgr, tmp_path):
    """XMP 是我们的旧分数、IPTC 是真实城市：只清 XMP，IPTC 保留（终审 I1）。"""
    p = _photo(mgr, tmp_path, "m.jpg", "095.30")
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("布里斯班")
    try:
        _exiftool(mgr, "-charset", "iptc=utf8", "-codedcharacterset=utf8", f"-IPTC:City<={tmp}",
                  "-overwrite_original", p)
    finally:
        os.remove(tmp)
    mgr.reset_metadata(p)
    assert _read(mgr, p, "XMP-photoshop:City") is None
    assert _read(mgr, p, "IPTC:City") == "布里斯班"


def test_reset_with_failed_read_keeps_location(mgr, tmp_path, monkeypatch):
    """读不到现有地点时重置不清地点字段（终审 I2）。"""
    p = _photo(mgr, tmp_path, "f.jpg", "布里斯班")
    monkeypatch.setattr(mgr, "read_metadata", lambda *a, **k: None)
    mgr.reset_metadata(p)
    monkeypatch.undo()
    assert _read(mgr, p, "XMP-photoshop:City") == "布里斯班"
