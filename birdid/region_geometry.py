# -*- coding: utf-8 -*-
"""
区域边界几何工具 / Geometry helpers for region boundaries.

边界环以 0.01° 精度量化为整数点 (lon*100, lat*100)，zlib 压缩后存入 ebird_regions.db。
本模块只做纯计算，构建脚本与运行时定位器共用，保证编码与解码永远一致。

重要前置条件 / Critical Precondition:
环不跨越反子午线（Natural Earth 在 ±180° 处裁剪多边形，因此每个环的连续顶点经度差 < 180°）。
±180° 附近的点会对每个裁剪后的半环分别求值；跨越 ±180° 接缝的距离不会被包装（179.9° 处的点与 -179.9° 处的环无法判定为"接近"）。

Boundary rings are quantized to integer points (lon*100, lat*100) at 0.01
degree precision and stored zlib-compressed in ebird_regions.db. This module is
pure computation shared by the build script and the runtime locator, so the
encoder and decoder can never drift apart.

Critical Precondition:
Rings must not cross the antimeridian (Natural Earth clips polygons at ±180°,
so every ring's consecutive vertices differ by <180° longitude). A point near
±180° is evaluated against each clipped half separately; distance across the
±180° seam is not wrapped (a point at 179.9° is not "near" a ring at -179.9°).
"""
from __future__ import annotations

import math
import struct
import zlib
from typing import List, Sequence, Tuple

SCALE = 100
_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LON_EQUATOR = 111.320

Point = Tuple[int, int]


def quantize_ring(coords: Sequence[Sequence[float]]) -> List[Point]:
    """
    把 GeoJSON 环量化为整数点 / Quantize a GeoJSON ring into integer points.

    去掉量化后连续重复的点，以及与首点相同的闭合尾点。

    Removes points that become consecutive duplicates after quantization, and
    the closing point that repeats the first one.

    参数 / Parameters:
        coords (Sequence[Sequence[float]]): GeoJSON 的 [lon, lat] 序列 / [lon, lat] pairs.

    返回 / Returns:
        list[tuple[int, int]]: (lon*100, lat*100) 点列，可能少于 3 点 /
            Quantized points; may contain fewer than three points.
    """
    out: List[Point] = []
    prev = None
    for pair in coords:
        p = (int(round(float(pair[0]) * SCALE)), int(round(float(pair[1]) * SCALE)))
        if p != prev:
            out.append(p)
            prev = p
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def pack_ring(points: Sequence[Point]) -> bytes:
    """
    打包为 zlib 压缩的小端 int32 序列 / Pack as zlib-compressed little-endian int32.

    参数 / Parameters:
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        bytes: 压缩后的字节串 / Compressed bytes.
    """
    flat = [v for p in points for v in p]
    return zlib.compress(struct.pack(f"<{len(flat)}i", *flat), 9)


def unpack_ring(blob: bytes) -> List[Point]:
    """
    解包 pack_ring 的输出 / Unpack the output of pack_ring.

    参数 / Parameters:
        blob (bytes): 压缩字节串 / Compressed bytes.

    返回 / Returns:
        list[tuple[int, int]]: 量化点列 / Quantized points.

    异常 / Exceptions:
        zlib.error / struct.error: 数据损坏时抛出 / Raised on corrupt data.
    """
    raw = zlib.decompress(blob)
    flat = struct.unpack(f"<{len(raw) // 4}i", raw)
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


def ring_bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    """
    环的外包框（度）/ Bounding box of a ring in degrees.

    参数 / Parameters:
        points (Sequence[tuple[int, int]]): 量化点列（非空）/ Non-empty quantized points.

    返回 / Returns:
        tuple: (min_lat, max_lat, min_lon, max_lon)
    """
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lats) / SCALE, max(lats) / SCALE, min(lons) / SCALE, max(lons) / SCALE)


def point_in_ring(lat: float, lon: float, points: Sequence[Point]) -> bool:
    """
    射线法判断点是否在环内 / Ray-casting point-in-ring test.

    前置条件：环不跨越反子午线（Natural Earth 在 ±180° 处裁剪）。
    ±180° 附近的点会对每个裁剪后的半环分别求值。

    Precondition: Ring must not cross the antimeridian (Natural Earth clips at
    ±180°). A point near ±180° is evaluated against each clipped half separately.

    参数 / Parameters:
        lat (float): 纬度 / Latitude.
        lon (float): 经度 / Longitude.
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        bool: 在环内为 True / True when inside.
    """
    x = lon * SCALE
    y = lat * SCALE
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def distance_to_ring_km(lat: float, lon: float, points: Sequence[Point]) -> float:
    """
    点到环边界的最短距离（km，局部等距投影近似）/ Shortest distance to a ring edge.

    在点所在纬度做等距矩形投影，数百公里内误差可忽略，足以判断离岸容差。

    前置条件：环不跨越反子午线（Natural Earth 在 ±180° 处裁剪）。
    跨越 ±180° 接缝的距离不会被包装；±180° 处的点与对侧环的接近度不可比较。

    Uses an equirectangular projection at the point's latitude; the error is
    negligible within a few hundred kilometres, which is all the offshore
    tolerance needs.

    Precondition: Ring must not cross the antimeridian (Natural Earth clips at
    ±180°). Distance across the ±180° seam is not wrapped; a point at ±180°
    is not considered "near" a ring on the opposite side of the seam.

    参数 / Parameters:
        lat (float): 纬度 / Latitude.
        lon (float): 经度 / Longitude.
        points (Sequence[tuple[int, int]]): 量化点列 / Quantized points.

    返回 / Returns:
        float: 距离 km；空环返回 inf / Distance in km; inf for an empty ring.
    """
    if not points:
        return math.inf
    kx = _KM_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)) / SCALE
    ky = _KM_PER_DEG_LAT / SCALE
    px = lon * SCALE * kx
    py = lat * SCALE * ky
    best = math.inf
    n = len(points)
    for i in range(n):
        ax, ay = points[i][0] * kx, points[i][1] * ky
        bx, by = points[(i + 1) % n][0] * kx, points[(i + 1) % n][1] * ky
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
        cx, cy = ax + t * dx, ay + t * dy
        d = math.hypot(px - cx, py - cy)
        if d < best:
            best = d
    return best
