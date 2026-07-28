# -*- coding: utf-8 -*-
"""
ExifTool 相对路径回归测试 / Regression test for relative paths in ExifToolManager.

常驻 exiftool 进程的 cwd 被设为 exiftool 二进制所在目录（Windows 需在此找 DLL），
与 Python 进程的 cwd 不同。相对路径能通过 os.path.exists 检查（用 Python 的 cwd
判断），却在 exiftool 侧解析失败并静默返回空——CLI 传相对目录时，整条 GPS 与
地理过滤链路会因此失效且无任何报错。

The persistent exiftool process runs with the binary's directory as cwd (needed
on Windows to find the DLLs), which differs from the Python process cwd. A
relative path passes the os.path.exists check (evaluated with Python's cwd) but
silently resolves to nothing on the exiftool side — when the CLI was given a
relative directory, the entire GPS / geo-filter chain failed with no error.
"""
import os

import pytest


@pytest.fixture
def sample_image():
    """仓库内自带 GPS 的样张 / A repo image that carries GPS."""
    rel = os.path.join("img", "_Z9W0960.jpg")
    if not os.path.exists(rel):
        pytest.skip(f"样张缺失 / sample missing: {rel}")
    return rel


def test_read_metadata_accepts_relative_path(sample_image):
    """相对路径与绝对路径必须读到同一份元数据"""
    from tools.exiftool_manager import get_exiftool_manager

    m = get_exiftool_manager()
    tags = ["GPSLatitude", "GPSLongitude"]
    rel = m.read_metadata(sample_image, tags)
    abs_ = m.read_metadata(os.path.abspath(sample_image), tags)
    assert rel, "相对路径读取返回空——常驻进程 cwd 问题复发"
    assert rel.get("GPSLatitude") == abs_.get("GPSLatitude")
    assert rel.get("GPSLongitude") == abs_.get("GPSLongitude")


def test_extract_gps_accepts_relative_path(sample_image):
    """
    GPS 提取对相对路径必须有效。

    这是地理过滤的入口：GPS 读不到就退化为无过滤（L5），而 CLI 用相对目录是
    常见用法，失效时没有任何提示。
    """
    from birdid.bird_identifier import extract_gps_from_exif

    lat_rel, lon_rel, _ = extract_gps_from_exif(sample_image)
    lat_abs, lon_abs, _ = extract_gps_from_exif(os.path.abspath(sample_image))
    assert lat_rel is not None and lon_rel is not None, "相对路径读不到 GPS"
    assert lat_rel == lat_abs and lon_rel == lon_abs
