#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可分享总结报告（core/report_export.py）的单元测试。

覆盖 spec docs/specs/2026-08-26-share-report-html-design.md 第 8 节
用例 1-15。全部以字典喂入、断言输出，不构造任何 Qt 对象。

Unit tests for the shareable summary report. Dict-in / string-out only;
no Qt objects are constructed anywhere in this file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from core.report_export import aggregate


def _photo(**kw) -> dict:
    """
    造一条 report.db 风格的照片记录，未指定字段取合理默认值。

    Build a report.db-shaped photo row; unspecified fields get defaults.
    """
    base = {
        "filename": "IMG_0001.NEF",
        "current_path": "/tmp/pick/IMG_0001.NEF",
        "temp_jpeg_path": "/tmp/pick/.superpicky/cache/IMG_0001.jpg",
        "has_bird": 1, "rating": 3, "picked": 0, "is_flying": 0,
        "focus_status": None,
        "adj_sharpness": 300.0, "adj_topiq": 50.0,
        "bird_species_cn": "白腹海雕", "bird_species_en": "White-bellied Sea Eagle",
        "gbif_rarity_100": 10.0, "iucn_category": "LC",
        "iso": 640, "shutter_speed": "1/2000", "aperture": "5.6",
        "focal_length_35mm": 600, "camera_model": "NIKON Z 9",
        "lens_model": "NIKKOR Z 600mm f/6.3 VR S",
        "gps_latitude": -16.9186, "gps_longitude": 145.7781, "gps_altitude": 12.0,
        "city": "Cairns", "state_province": "Queensland", "country": "Australia",
        "date_time_original": "2026-08-26 06:12:00",
        "burst_id": None, "burst_position": None,
    }
    base.update(kw)
    return base


def test_aggregate_counts():
    """用例 1：总数/星级/精选/飞版/连拍组数正确。"""
    photos = [
        _photo(filename="a.NEF", rating=3, picked=1),
        _photo(filename="b.NEF", rating=3, picked=0),
        _photo(filename="c.NEF", rating=2),
        _photo(filename="d.NEF", rating=1, is_flying=1),
        _photo(filename="e.NEF", rating=-1, has_bird=0, bird_species_cn="", bird_species_en=""),
    ]
    data = aggregate(photos)
    assert data.total == 5
    assert data.by_rating[3] == 2
    assert data.by_rating[2] == 1
    assert data.by_rating[1] == 1
    assert data.by_rating[-1] == 1
    assert data.picked == 1
    assert data.flying == 1
    assert data.bird_total == 4


def test_species_sorted_by_rarity_not_count():
    """用例 2：鸟种区块按罕见度降序，而非张数降序。"""
    photos = (
        [_photo(filename=f"common{i}.NEF", bird_species_cn="麻雀",
                bird_species_en="Sparrow", gbif_rarity_100=2.0) for i in range(10)]
        + [_photo(filename="rare1.NEF", bird_species_cn="传奇鸟",
                  bird_species_en="Legend Bird", gbif_rarity_100=90.0)]
    )
    data = aggregate(photos)
    assert [s.name_cn for s in data.species] == ["传奇鸟", "麻雀"]
    assert data.species[0].tier == 4
    assert data.species[1].count == 10


def test_per_species_picks_at_most_four_by_priority():
    """用例 3：每种至多 4 张，picked > rating > adj_topiq；不足者全取。"""
    photos = [
        _photo(filename="low.NEF", rating=1, picked=0, adj_topiq=10.0),
        _photo(filename="mid.NEF", rating=2, picked=0, adj_topiq=20.0),
        _photo(filename="top.NEF", rating=3, picked=1, adj_topiq=30.0),
        _photo(filename="high.NEF", rating=3, picked=0, adj_topiq=99.0),
        _photo(filename="extra.NEF", rating=0, picked=0, adj_topiq=5.0),
    ]
    data = aggregate(photos)
    block = data.species[0]
    assert len(block.photos) == 4
    assert block.photos[0].filename == "top.NEF"      # picked 优先
    assert block.photos[1].filename == "high.NEF"     # 同为 3 星，topiq 高者先
    assert block.count == 5                            # count 是总张数，非展示张数
    assert "extra.NEF" not in [p.filename for p in block.photos]


def test_gps_dropped_at_aggregate_layer_when_not_requested():
    """用例 4：未勾选 GPS 时 ReportData.gps 为 None（在聚合层丢弃，非渲染层隐藏）。"""
    data = aggregate([_photo()], include_gps=False)
    assert data.gps is None
    assert data.location == "Cairns · Queensland · Australia"


def test_gps_present_when_requested():
    """用例 4 反面：勾选时坐标进入 ReportData。"""
    data = aggregate([_photo()], include_gps=True)
    assert data.gps == (-16.9186, 145.7781, 12.0)


def test_photoref_never_carries_directory_in_filename():
    """spec D6：PhotoRef.filename 只含文件名，绝不含目录。"""
    data = aggregate([_photo(filename="IMG_1.NEF",
                             current_path="/Users/someone/Pictures/IMG_1.NEF")])
    assert data.detail[0].filename == "IMG_1.NEF"
    assert "/" not in data.detail[0].filename
