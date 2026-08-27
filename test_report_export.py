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

from dataclasses import replace

from core.report_export import (
    aggregate, build_html, collect_image_jobs, encode_preview,
    preview_availability, IMG_SPECS
)
from PIL import Image


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


def _make_jpeg(tmp_path, name="p.jpg", size=(2000, 1400), orientation=None):
    """
    造一张真实 JPEG；orientation 非 None 时写入 EXIF Orientation 标签。

    Create a real JPEG on disk, optionally carrying an EXIF Orientation tag.
    """
    p = tmp_path / name
    im = Image.new("RGB", size, (120, 90, 60))
    if orientation is not None:
        exif = im.getexif()
        exif[274] = orientation          # 274 = Orientation
        im.save(p, "JPEG", exif=exif)
    else:
        im.save(p, "JPEG")
    return str(p)


def test_encode_preview_returns_data_uri_within_max_edge(tmp_path):
    """用例 8 前半：输出为 data URI，且长边不超过上限。"""
    import base64, io
    src = _make_jpeg(tmp_path, size=(2000, 1400))
    uri = encode_preview(src, 400, 78)
    assert uri.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(uri.split(",", 1)[1])
    out = Image.open(io.BytesIO(raw))
    assert max(out.size) <= 400


def test_encode_preview_applies_exif_orientation(tmp_path):
    """用例 8：Orientation=6 的横图必须被转正，宽高互换。"""
    import base64, io
    src = _make_jpeg(tmp_path, name="rot.jpg", size=(1200, 800), orientation=6)
    uri = encode_preview(src, 600, 80)
    raw = base64.b64decode(uri.split(",", 1)[1])
    out = Image.open(io.BytesIO(raw))
    assert out.height > out.width, "EXIF Orientation=6 未生效，图未被转正"


def test_encode_preview_returns_none_on_broken_file(tmp_path):
    """用例 11 基础：损坏文件返回 None，不抛异常。"""
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not a jpeg at all")
    assert encode_preview(str(bad), 400, 78) is None


def test_encode_preview_returns_none_on_missing_file(tmp_path):
    """不存在的路径同样返回 None。"""
    assert encode_preview(str(tmp_path / "nope.jpg"), 400, 78) is None


def test_collect_image_jobs_covers_every_shown_photo():
    """每张展示图都有 hd job；明细每张有 thumb job；封面有 cover job。"""
    # 每张照片需要唯一的路径，否则会在 job_id 中碰撞
    photos = [_photo(filename=f"a{i}.NEF", current_path=f"/tmp/pick/a{i}.NEF",
                     adj_topiq=float(i)) for i in range(6)]
    data = aggregate(photos)
    jobs = collect_image_jobs(data)
    kinds = {j.job_id.split(":", 1)[0] for j in jobs}
    assert {"cover", "rep", "small", "hd", "thumb"} == kinds
    assert sum(1 for j in jobs if j.job_id.startswith("thumb:")) == 6
    assert sum(1 for j in jobs if j.job_id.startswith("rep:")) == 1     # 1 个鸟种
    assert sum(1 for j in jobs if j.job_id.startswith("small:")) == 3   # 4 张展示图中 3 张小图


def test_collect_image_jobs_can_skip_detail_thumbs():
    """with_detail_thumbs=False 时不产出 thumb job（照片数 > 600 的退化路径）。"""
    data = aggregate([_photo(filename=f"a{i}.NEF") for i in range(3)])
    jobs = collect_image_jobs(data, with_detail_thumbs=False)
    assert not any(j.job_id.startswith("thumb:") for j in jobs)


def test_preview_availability_counts_existing_files(tmp_path):
    """预检只做 exists 统计，不解码。"""
    good = _make_jpeg(tmp_path, name="ok.jpg", size=(50, 50))
    rows = [
        _photo(filename="ok.NEF", temp_jpeg_path=good, current_path=good),
        _photo(filename="gone.NEF",
               temp_jpeg_path=str(tmp_path / "missing.jpg"),
               current_path=str(tmp_path / "missing.NEF")),
    ]
    available, total = preview_availability(rows)
    assert (available, total) == (1, 2)


def test_to_ref_uses_jpeg_preview_over_raw_path(tmp_path):
    """
    Critical fix: temp_jpeg_path 指向真实 JPEG、current_path 指向不存在的 RAW 时，
    PhotoRef.path 应为 JPEG 而非 RAW（否则 PIL 无法解码导致占位块）。
    """
    good_jpeg = _make_jpeg(tmp_path, name="photo.jpg", size=(100, 100))
    missing_raw = str(tmp_path / "nonexistent.NEF")
    photo = _photo(
        filename="photo.NEF",
        current_path=missing_raw,
        temp_jpeg_path=good_jpeg
    )
    data = aggregate([photo])
    # PhotoRef.path 应为可解码的 JPEG，而非不存在的 RAW
    assert data.detail[0].path == good_jpeg
    assert os.path.exists(data.detail[0].path)


def test_preview_candidates_finds_jpeg_extension(tmp_path):
    """Important fix: preview_candidates 应能找到 .jpeg 变体（除 .jpg/.JPG/.JPEG）。"""
    import os
    # 创建 .jpeg 版本
    jpeg_ext = _make_jpeg(tmp_path, name="photo.jpeg", size=(100, 100))
    photo = _photo(
        filename="photo.NEF",
        current_path=str(tmp_path / "photo.NEF"),
        original_path=str(tmp_path / "photo.NEF"),
        temp_jpeg_path=str(tmp_path / "missing.jpg")  # temp_jpeg_path 不存在
    )
    data = aggregate([photo])
    # .jpeg 小写应被找到
    assert data.detail[0].path == jpeg_ext


def test_job_id_includes_path_to_avoid_collisions(tmp_path):
    """
    Important fix: job_id 用 path 而非 filename，避免不同目录同名文件碰撞。
    两张同名照片来自不同目录应各自拿到独立的 job。
    """
    # 创建两个目录，各放一张同名照片
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    photo1_path = _make_jpeg(dir1, name="IMG_0001.jpg", size=(100, 100))
    photo2_path = _make_jpeg(dir2, name="IMG_0001.jpg", size=(100, 100))

    photos = [
        _photo(
            filename="IMG_0001.NEF",
            current_path=photo1_path,
            temp_jpeg_path=photo1_path
        ),
        _photo(
            filename="IMG_0001.NEF",  # 同名
            current_path=photo2_path,
            temp_jpeg_path=photo2_path
        ),
    ]
    data = aggregate(photos)
    jobs = collect_image_jobs(data)

    # 应有两个独立的 hd job（对应两个不同的文件）
    hd_jobs = [j for j in jobs if j.job_id.startswith("hd:")]
    assert len(hd_jobs) == 2
    # 两个 job 应指向不同的路径
    paths = {j.path for j in hd_jobs}
    assert len(paths) == 2
    assert photo1_path in paths
    assert photo2_path in paths


# ── Task 3: HTML 骨架、转义与视口懒插入 ──────────────────────────────────────

def test_no_image_src_in_dom():
    """
    用例 7（本设计最关键的一条不变量）：输出中不存在任何 <img ... src="data:。

    所有 data URI 只能出现在 JS 数组里，<img> 一律靠 data-idx 由
    IntersectionObserver 按视口插入。这条一旦被破坏，40 个鸟种的报告
    常驻位图会飙到 178MB，手机必崩（spec 6.1）。
    """
    import re
    data = aggregate([_photo(filename=f"a{i}.NEF") for i in range(3)])
    html = build_html(data, {"cover:a0.NEF": "data:image/jpeg;base64,AAA"})
    assert not re.search(r'<img[^>]*\ssrc\s*=', html), "有图片被直接写进了 src"
    assert "IntersectionObserver" in html
    assert "data-idx" in html


def test_html_escapes_hostile_dir_name():
    """
    用例 6：目录名里的标签必须被转义，不能撕烂页面。

    目录名是 Task 3 就渲染的外部输入（<h1> 与 <title> 各一处）。鸟种名
    的同类转义在鸟种画廊落地后由 Task 4 覆盖。

    Directory names are user-supplied and rendered by this task; species
    names get the same treatment once the gallery lands in Task 4.
    """
    data = replace(aggregate([_photo()]),
                   dir_name="<script>alert(1)</script>")
    html = build_html(data, {})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escapes_hostile_filename():
    """文件名同样来自外部，必须转义。"""
    data = aggregate([_photo(filename="a<b>.NEF")])
    html = build_html(data, {})
    assert "a<b>.NEF" not in html
    assert "a&lt;b&gt;.NEF" in html


def test_gps_never_leaks_into_html_when_not_requested():
    """
    用例 5：未勾选 GPS 时，坐标数值不得出现在 HTML 全文任何位置
    （包括注释、JS 字符串、data-* 属性）。
    """
    data = aggregate([_photo()], include_gps=False)
    html = build_html(data, {})
    assert "16.9186" not in html
    assert "145.7781" not in html
    assert "Cairns" in html          # 城市级地点仍应显示


def test_gps_rendered_when_requested():
    """勾选时坐标出现。"""
    data = aggregate([_photo()], include_gps=True)
    html = build_html(data, {}, is_zh=True)
    assert "16.9186" in html


def test_html_is_utf8_roundtrip(tmp_path):
    """
    用例 12：中文写入读回逐字一致，且声明了 charset。

    中文取自目录名（Task 3 渲染的唯一中文来源）；鸟种名的中文在
    Task 4 的画廊测试里覆盖。
    """
    data = replace(aggregate([_photo()]), dir_name="白腹海雕_凯恩斯")
    html = build_html(data, {})
    assert '<meta charset="utf-8">' in html
    f = tmp_path / "报告_测试.html"
    f.write_text(html, encoding="utf-8")
    assert f.read_text(encoding="utf-8") == html
    assert "白腹海雕_凯恩斯" in f.read_text(encoding="utf-8")
