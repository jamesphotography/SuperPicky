# -*- coding: utf-8 -*-
"""GPS 坐标 0.0 是合法值（赤道/本初子午线），不能用真值判断当作缺失。

Zero GPS coordinates (equator / prime meridian) are legal values and must
not be treated as "missing" by truthiness checks.

背景 / Background:
identify_bird() 里曾用 `if lat and lon:` 判断 GPS 是否存在——在赤道
（lat=0.0）或本初子午线（lon=0.0）附近拍摄的照片会被当作无 GPS，
gps_info 不写入、拍摄国家不解析，GBIF 按国家归一化 rarity 静默失效；
而同函数几行之下的 eBird 过滤用的却是正确的 `is not None` 写法。
这组测试锁定统一的判断辅助函数及 identify_bird 不再使用真值判断。

identify_bird() used `if lat and lon:` to detect GPS presence — photos taken
near the equator (lat=0.0) or prime meridian (lon=0.0) were treated as
GPS-less: gps_info was dropped and the shooting country never resolved,
silently disabling country-aware GBIF rarity. A few lines below, the eBird
filter already used the correct `is not None` form. These tests pin the
shared predicate and that identify_bird no longer uses the truthy check.
"""
import inspect

from birdid import bird_identifier


def test_zero_coordinates_are_valid_gps():
    """0.0 纬度/经度是合法坐标，必须判定为「存在」。"""
    assert bird_identifier._gps_coords_present(0.0, 25.3) is True
    assert bird_identifier._gps_coords_present(-33.8, 0.0) is True
    assert bird_identifier._gps_coords_present(0.0, 0.0) is True
    assert bird_identifier._gps_coords_present(-33.8, 151.2) is True


def test_missing_coordinates_are_not_present():
    """任一坐标为 None 即视为缺失。"""
    assert bird_identifier._gps_coords_present(None, 25.3) is False
    assert bird_identifier._gps_coords_present(-33.8, None) is False
    assert bird_identifier._gps_coords_present(None, None) is False


def test_identify_bird_source_has_no_truthy_gps_check():
    """identify_bird 内不得再出现 `if lat and lon` 真值判断。

    identify_bird must no longer contain the truthy `if lat and lon` check.
    """
    src = inspect.getsource(bird_identifier.identify_bird)
    assert "if lat and lon" not in src, "GPS 存在性判断必须用 is not None 语义"
