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
    aggregate, build_html, build_output_path, collect_image_jobs,
    encode_preview, estimate_size, preview_availability,
    write_report_atomically, IMG_SPECS
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
    # 键必须与 collect_image_jobs 产出的 job_id 同构，故从 data 直接取
    html = build_html(data, {f"cover:{data.cover.path}": "data:image/jpeg;base64,AAA"})
    assert not re.search(r'<img[^>]*\ssrc\s*=', html), "有图片被直接写进了 src"
    assert "IntersectionObserver" in html
    # 必须断言 DOM 里真有 <img data-idx=：裸的 "data-idx" 会被 _JS_LAZY 里的
    # 选择器字符串 img[data-idx] 蒙混过关（两端键分叉时就是这么漏掉的）。
    assert "<img data-idx=" in html


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


# ── Task 4: 鸟种画廊、数据区、折叠明细 ───────────────────────────────────────

def test_species_blocks_all_rendered_no_cap():
    """
    用例 9：40 个鸟种全部出块，无一被降级为纯文字。

    spec D8 明确不对鸟种数封顶——内存问题已由 Task 3 的懒插入解决，
    不该再为它砍掉用户拍到的鸟种。
    """
    photos = [_photo(filename=f"s{i}.NEF", bird_species_cn=f"鸟种{i}",
                     bird_species_en=f"Bird{i}", gbif_rarity_100=float(i))
              for i in range(40)]
    data = aggregate(photos)
    html = build_html(data, {})
    assert len(data.species) == 40
    for i in range(40):
        assert f"鸟种{i}" in html


def test_iucn_badge_threshold():
    """用例 15：LC/NT/DD/NE 不渲染徽标；VU 及以上渲染。"""
    lc = build_html(aggregate([_photo(iucn_category="LC")]), {})
    assert 'class="iucn"' not in lc
    for cat in ("VU", "EN", "CR", "EW", "EX"):
        html = build_html(aggregate([_photo(iucn_category=cat)]), {})
        assert 'class="iucn"' in html, f"{cat} 应显示徽标"
        assert f">{cat}<" in html
    for cat in ("NT", "DD", "NE"):
        html = build_html(aggregate([_photo(iucn_category=cat)]), {})
        assert 'class="iucn"' not in html, f"{cat} 不应显示徽标"


def test_detail_table_rendered_with_thumbs_by_default():
    """明细表默认带缩略图列。"""
    data = aggregate([_photo(filename="a.NEF")])
    html = build_html(data, {f"thumb:{data.detail[0].path}": "data:image/jpeg;base64,AAA"})
    assert "<table" in html
    assert "a.NEF" in html
    assert 'class="thumbcol"' in html


def test_detail_table_degrades_to_text_when_no_thumbs():
    """
    用例 9 后半：with_detail_thumbs=False 时明细表无缩略图列。

    照片总数 > 600 时走此路径——针对文件体积而非内存（spec 6.4）。
    """
    data = aggregate([_photo(filename=f"a{i}.NEF") for i in range(3)])
    html = build_html(data, {}, with_detail_thumbs=False)
    assert "<table" in html
    assert 'class="thumbcol"' not in html


def test_species_name_follows_language():
    """is_zh=False 时英文名在前（spec D7）。"""
    data = aggregate([_photo(bird_species_cn="白腹海雕",
                             bird_species_en="White-bellied Sea Eagle")])
    en = build_html(data, {}, is_zh=False)
    assert en.index("White-bellied Sea Eagle") < en.index("白腹海雕")


def test_html_escapes_hostile_species_name():
    """
    用例 6 的鸟种名部分（从 Task 3 挪来）：鸟种名里的标签必须被转义。

    原计划把这条放在 Task 3，但鸟种名要到本 Task 的画廊才进入输出，
    在 Task 3 它会红在正确的实现上。目录名一路的转义已由 Task 3 的
    test_html_escapes_hostile_dir_name 覆盖。

    Moved from Task 3: species names only reach the output once the
    gallery lands here.
    """
    data = aggregate([_photo(bird_species_cn="<script>alert(1)</script>",
                             bird_species_en="<img onerror=x>")])
    html = build_html(data, {})
    assert "<script>alert(1)</script>" not in html
    assert "<img onerror=x>" not in html
    assert "&lt;script&gt;" in html


# ── 手动 4/5 星（浏览器内升星）纳入统计 ──────────────────────────────────────

def test_aggregate_counts_manual_high_ratings():
    """
    4/5 星是用户在浏览器里手动升出来的档位（自动评分只产 -1..3）。

    by_rating 一旦只开 -1..3，这些照片会被静默丢弃：条形图各档之和
    对不上总张数，用户越是给好片升星、统计越失真。

    4/5 stars come from manual promotion in the browser; dropping them
    would make the bars sum to less than the total.
    """
    photos = [_photo(filename="a.NEF", rating=5),
              _photo(filename="b.NEF", rating=4),
              _photo(filename="c.NEF", rating=3)]
    data = aggregate(photos)
    assert data.by_rating[5] == 1
    assert data.by_rating[4] == 1
    assert data.by_rating[3] == 1
    assert sum(data.by_rating.values()) == data.total


def test_stats_bars_render_high_ratings_when_present():
    """有 4/5 星时条形图出现对应档，且各档计数之和等于总张数。"""
    import re
    photos = [_photo(filename="a.NEF", rating=5),
              _photo(filename="b.NEF", rating=4),
              _photo(filename="c.NEF", rating=2)]
    html = build_html(aggregate(photos), {})
    assert "★★★★★" in html
    assert "★★★★" in html
    counts = [int(m) for m in re.findall(r'<span>(\d+) \(', html)]
    assert sum(counts) == 3


def test_stats_bars_hide_empty_high_ratings():
    """
    没有 4/5 星时不画这两条空条——绝大多数批次都没手动升过星，
    多两条恒为 0 的条只会稀释信息。

    Hide the 4/5 bars when unused; most batches never promote by hand.
    """
    html = build_html(aggregate([_photo(rating=3)]), {})
    assert "★★★★" not in html


def test_hit_rate_counts_manual_high_ratings():
    """
    命中率是「能用的片子占比」，4/5 星比 3 星更好，必须算进去，
    否则用户每升一张星、命中率反而下降（spec 5.1 ③ 的口径修订）。

    Hit rate counts 3★ and above; otherwise promoting a photo would
    paradoxically lower it.
    """
    photos = [_photo(filename="a.NEF", rating=5),
              _photo(filename="b.NEF", rating=4),
              _photo(filename="c.NEF", rating=3),
              _photo(filename="d.NEF", rating=1)]
    html = build_html(aggregate(photos), {})
    assert "75.0%" in html


# ── Task 5: 打印适配与「存为 PDF」 ───────────────────────────────────────────

def test_print_stylesheet_has_all_four_requirements():
    """
    用例 14：@media print 必须同时满足 spec 5.2 的四项，缺一则打印结果不可用。

      1. 白底（深色底会打出整页黑）
      2. page-break-inside: avoid（图文不被跨页拦腰截断）
      3. 展开 <details>（否则明细区根本印不出来）
      4. 隐藏交互控件（按钮、lightbox）
    """
    html = build_html(aggregate([_photo()]), {})
    assert "@media print" in html
    block = html.split("@media print", 1)[1]
    assert "#fff" in block
    assert "page-break-inside" in block
    assert "details" in block
    assert "no-print" in block


def test_print_keeps_detail_heading():
    """
    展开明细区时标题必须留着：<h2> 长在 <summary> 里，整块 display:none
    会让打印版多出一张没有名字的表。只去掉折叠箭头即可。

    The detail table's heading lives inside <summary>; hiding the whole
    summary would print an unlabeled table. Drop only the disclosure marker.
    """
    html = build_html(aggregate([_photo()]), {})
    block = html.split("@media print", 1)[1].split("</style>", 1)[0]
    assert "summary{display:none" not in block.replace(" ", "")
    assert "list-style:none" in block


def test_save_as_pdf_button_present():
    """页顶「存为 PDF」按钮存在，且点击调用 window.print()。"""
    html = build_html(aggregate([_photo()]), {})
    assert 'id="pdfbtn"' in html
    assert "window.print()" in html


def test_pdf_button_is_hidden_when_printing():
    """按钮自身必须带 no-print，否则会被印进 PDF。"""
    html = build_html(aggregate([_photo()]), {})
    assert 'id="pdfbtn" class="no-print"' in html


def test_print_forces_lazy_images_to_materialize():
    """
    懒插入的代价：未进过视口的图 src 是空的，直接打印会得到大片空白。
    因此按钮必须先把 IMGS 全部落位再 print()。
    """
    html = build_html(aggregate([_photo()]), {})
    assert "img[data-idx]" in html
    assert "IMGS[el.dataset.idx]" in html
    # 落位必须发生在 print() 之前
    assert html.index("IMGS[el.dataset.idx]") < html.rindex("window.print()")


def test_save_as_pdf_button_localized():
    """按钮文案跟随语言（spec D7）。"""
    assert "存为 PDF" in build_html(aggregate([_photo()]), {}, is_zh=True)
    assert "Save as PDF" in build_html(aggregate([_photo()]), {}, is_zh=False)


def test_print_expands_details_via_js_not_css():
    """
    CSS 改不了 <details> 的 open（那是属性不是样式，display:block 只让
    元素本身是块级，折叠内容依旧不渲染），所以打印样式再全，明细区在纸上
    也是不存在的。必须在打印前用 JS 补 open、打印后恢复。

    Safari 长期不支持 beforeprint/afterprint，故同时挂 matchMedia('print')
    兜底；按钮路径直接调用，不依赖任何事件。

    CSS cannot open a <details> (open is an attribute, not a style), so the
    detail section must be expanded by JS before printing and restored after.
    """
    html = build_html(aggregate([_photo()]), {})
    assert "beforeprint" in html
    assert "afterprint" in html
    assert "matchMedia" in html          # Safari 兜底
    assert ".open=true" in html
    assert ".open=false" in html         # 打印后恢复原折叠状态


# ── Task 6: 输出路径与体积预估 ───────────────────────────────────────────────

def test_build_output_path_language_and_collision(tmp_path):
    """
    spec D5：输出到目录根、非隐藏目录；文件名跟随界面语言；同名加 _2 不覆盖。
    """
    d = str(tmp_path)
    zh = build_output_path(d, "晨拍", is_zh=True, today="2026-08-26")
    assert os.path.dirname(zh) == d
    assert os.path.basename(zh) == "SuperPicky报告_晨拍_2026-08-26.html"
    assert ".superpicky" not in zh

    en = build_output_path(d, "morning", is_zh=False, today="2026-08-26")
    assert os.path.basename(en) == "SuperPicky-Report-morning-2026-08-26.html"

    with open(zh, "w", encoding="utf-8") as fh:
        fh.write("x")
    second = build_output_path(d, "晨拍", is_zh=True, today="2026-08-26")
    assert os.path.basename(second) == "SuperPicky报告_晨拍_2026-08-26_2.html"


def test_build_output_path_sanitizes_separators(tmp_path):
    """目录名含路径分隔符时不得穿出目标目录。"""
    out = build_output_path(str(tmp_path), "a/b\\c", is_zh=True, today="2026-08-26")
    assert os.path.dirname(out) == str(tmp_path)


def test_estimate_size_scales_with_job_counts():
    """预估随任务数线性增长，且含 base64 膨胀。"""
    small = estimate_size({"cover": 1, "rep": 1, "small": 3, "hd": 4, "thumb": 10})
    big = estimate_size({"cover": 1, "rep": 40, "small": 120, "hd": 160, "thumb": 600})
    assert big > small * 5
    assert small > 0


# ── Task 7: 原子写 ───────────────────────────────────────────────────────────

def test_write_report_atomically_leaves_no_tmp(tmp_path):
    """用例 13：成功后无 .tmp 残留，内容为 UTF-8。"""
    target = str(tmp_path / "r.html")
    write_report_atomically(target, "<html>白腹海雕</html>")
    assert os.path.exists(target)
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())
    with open(target, encoding="utf-8") as fh:
        assert "白腹海雕" in fh.read()


def test_write_report_atomically_no_partial_file_on_failure(tmp_path, monkeypatch):
    """用例 13 后半：写入中途失败时不得产出成品文件。"""
    import core.report_export as mod
    target = str(tmp_path / "r.html")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "replace", boom)
    with pytest.raises(OSError):
        write_report_atomically(target, "<html></html>")
    assert not os.path.exists(target)
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


# ── 端到端：编码结果必须真的进到 HTML 里 ─────────────────────────────────────

def test_encoded_images_actually_reach_the_html(tmp_path):
    """
    端到端回归钉：collect_image_jobs 产出的 job_id 必须与渲染端查表所用的键
    完全一致，否则每一张图都静默退化成占位块——报告表面生成成功、实际一张
    图都没有，而各自为政的单元测试全是绿的。

    两端确实分叉过：任务侧用 f"{kind}:{ref.path}"（绝对路径），渲染侧用
    f"{kind}:{ref.filename}"（文件名），encoded.get() 永远取不到值。键统一
    用 path 而非 filename——合并报告里不同子目录的同名文件会互相顶替，
    渲染出张冠李戴的图。

    End-to-end pin: the job_id produced by collect_image_jobs must match the
    key the renderer looks up, or every image silently degrades to a
    placeholder while every isolated unit test still passes.
    """
    rows = []
    for i in range(3):
        jpg = tmp_path / f"DSC_{i}.jpg"
        Image.new("RGB", (900, 600), (40 + i * 40, 90, 140)).save(jpg, quality=85)
        rows.append(_photo(filename=f"DSC_{i}.NEF",
                           current_path=str(tmp_path / f"DSC_{i}.NEF"),
                           temp_jpeg_path=str(jpg),
                           bird_species_cn=f"鸟种{i}", bird_species_en=f"Bird{i}"))

    data = aggregate(rows)
    jobs = collect_image_jobs(data, with_detail_thumbs=True)
    assert jobs, "应至少有封面与鸟种图任务"

    encoded = {}
    for job in jobs:
        uri = encode_preview(job.path, job.max_edge, job.quality)
        assert uri, f"{job.job_id} 编码失败"
        encoded[job.job_id] = uri

    html = build_html(data, encoded)
    assert "<img data-idx=" in html, "编码好的图一张都没进 DOM"
    assert 'class="ph' not in html, "仍有占位块，说明有 job_id 对不上"
    # IMGS 数组里的条目数应与实际用到的图片数一致
    assert html.count("<img data-idx=") >= len(data.species) + 1
