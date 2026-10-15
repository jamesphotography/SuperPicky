# -*- coding: utf-8 -*-
"""
区域几何工具单测 / Unit tests for region geometry helpers.
"""
import pytest

from birdid.region_geometry import (
    distance_to_ring_km,
    pack_ring,
    point_in_ring,
    quantize_ring,
    ring_bbox,
    unpack_ring,
)

SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]


def test_quantize_drops_duplicates_and_closing_point():
    """连续重复点与首尾闭合点都应去掉 / Drop consecutive dupes and the closing point."""
    pts = quantize_ring([[0.001, 0.001], [0.002, 0.002], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]])
    assert pts == [(0, 0), (100, 0), (100, 100)]


def test_pack_roundtrip():
    """打包再解包得到原点列 / Pack then unpack returns the same points."""
    pts = quantize_ring(SQUARE)
    assert unpack_ring(pack_ring(pts)) == pts


def test_bbox_in_degrees():
    """外包框以度为单位 / Bounding box is expressed in degrees."""
    assert ring_bbox(quantize_ring(SQUARE)) == (0.0, 10.0, 0.0, 10.0)


def test_point_in_ring():
    """内部为真、外部为假 / Inside is True, outside is False."""
    pts = quantize_ring(SQUARE)
    assert point_in_ring(5.0, 5.0, pts) is True
    assert point_in_ring(11.0, 5.0, pts) is False
    assert point_in_ring(5.0, -0.5, pts) is False


def test_distance_one_degree_south():
    """正南 1° 距离约 110.57 km / One degree due south is about 110.57 km."""
    pts = quantize_ring(SQUARE)
    assert distance_to_ring_km(-1.0, 5.0, pts) == pytest.approx(110.574, abs=0.5)


def test_distance_inside_point_is_to_nearest_edge():
    """内部点到最近边的距离 / Inside point measures to the nearest edge."""
    pts = quantize_ring(SQUARE)
    assert distance_to_ring_km(9.0, 5.0, pts) == pytest.approx(110.574, abs=0.5)


def test_antimeridian_clipped_halves():
    """反子午线裁剪后的两个半环被独立处理 / Antimeridian-clipped halves are evaluated independently."""
    # 西半环：lon 170..180, lat 50..60 / Western half: lon 170..180, lat 50..60
    square_west = [[170.0, 50.0], [180.0, 50.0], [180.0, 60.0], [170.0, 60.0], [170.0, 50.0]]
    pts_west = quantize_ring(square_west)

    # 东半环：lon -180..-170, lat 50..60 / Eastern half: lon -180..-170, lat 50..60
    square_east = [[-180.0, 50.0], [-170.0, 50.0], [-170.0, 60.0], [-180.0, 60.0], [-180.0, 50.0]]
    pts_east = quantize_ring(square_east)

    # 点 (55, 175) 在西半环内，不在东半环内
    # Point (55, 175) is inside western half, outside eastern half
    assert point_in_ring(55.0, 175.0, pts_west) is True
    assert point_in_ring(55.0, 175.0, pts_east) is False

    # 点 (55, -175) 在东半环内，不在西半环内
    # Point (55, -175) is inside eastern half, outside western half
    assert point_in_ring(55.0, -175.0, pts_east) is True
    assert point_in_ring(55.0, -175.0, pts_west) is False
