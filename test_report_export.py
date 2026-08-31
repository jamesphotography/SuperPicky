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
    format_minute, format_shutter, probe_jpeg_ratio,
    write_report_atomically, IMG_SPECS, _tier_color_on_dark
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
    assert data.cover.filename == "IMG_1.NEF"
    assert "/" not in data.cover.filename


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
    """每张展示图都有 hd job；封面有 cover job；不再产出任何 thumb job。"""
    # 每张照片需要唯一的路径，否则会在 job_id 中碰撞；burst_id 各不相同，
    # 免得被连拍去重合并掉（去重逻辑另有专门用例覆盖）。
    photos = [_photo(filename=f"a{i}.NEF", current_path=f"/tmp/pick/a{i}.NEF",
                     burst_id=i, adj_topiq=float(i)) for i in range(6)]
    data = aggregate(photos)
    jobs = collect_image_jobs(data)
    kinds = {j.job_id.split(":", 1)[0] for j in jobs}
    assert {"cover", "shot"} == kinds, "只应有封面与展示图两档"
    assert not any(j.job_id.startswith("thumb:") for j in jobs)
    # 每张照片只编码一次：4 张展示图 = 4 个 shot job，没有第二份「放大专用」的
    assert sum(1 for j in jobs if j.job_id.startswith("shot:")) == 4
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
    assert data.cover.path == good_jpeg
    assert os.path.exists(data.cover.path)


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
    assert data.cover.path == jpeg_ext


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

    # 应有两个独立的 shot job（对应两个不同的文件）
    hd_jobs = [j for j in jobs if j.job_id.startswith("shot:")]
    assert len(hd_jobs) == 2
    # 两个 job 应指向不同的路径
    paths = {j.path for j in hd_jobs}
    assert len(paths) == 2
    assert photo1_path in paths
    assert photo2_path in paths


# ── Task 3: HTML 骨架、转义与视口懒插入 ──────────────────────────────────────

def test_images_render_without_javascript():
    """
    用例 7（已反转的一条不变量）：每张展示图都必须把 data URI 写在 src 上。

    原先的约定正好相反——DOM 里绝不写 src、全靠 IntersectionObserver 按视口
    插入——目的是压常驻位图。代价直到把报告分享出去才暴露：**任何不执行
    JavaScript 的查看环境都只能看到一份没有照片的报告**。macOS 的快速查看
    （Finder 按空格）、iOS「文件」App 预览、邮件客户端与部分 IM 的内置预览
    都不跑脚本，而收到文件先按空格看一眼恰恰是最常见的动作。

    改法不牺牲内存控制：脚本一跑就把 src 收走交给 observer，滚出视口照样
    卸载，行为与之前完全一致（见下面两条断言）。体积也没有代价，base64 仍
    只存一份，只是从数组挪进了 src。

    The original invariant was the reverse and made the report invisible in
    every JS-free viewer. Images now ship with src; the observer takes them
    over when scripts run, so the memory behavior is unchanged.
    """
    import re
    data = aggregate([_photo(filename=f"a{i}.NEF") for i in range(3)])
    # 键必须与 collect_image_jobs 产出的 job_id 同构，故从 data 直接取
    html = build_html(data, {f"cover:{data.cover.path}": "data:image/jpeg;base64,AAA"})
    assert re.search(r'<img[^>]*\ssrc="data:image', html), "图片没有直接写进 src"
    assert "<img data-lazy decoding=" in html
    # 内存控制必须仍然在位：observer 依旧接管、依旧卸载屏幕外的位图
    assert "IntersectionObserver" in html
    assert "removeAttribute('src')" in html


def test_images_are_not_hidden_by_css_without_javascript():
    """
    光有 src 还不够：任何让图片默认不可见的样式（淡入的 opacity:0 是典型）
    都会把「看得见」重新绑回脚本，无 JS 时图片有 src 却是透明的——比根本
    没有 src 更难排查。

    A src alone is not enough: any default-hidden style (a fade-in's
    opacity:0) would re-bind visibility to JavaScript.
    """
    html = build_html(aggregate([_photo()]), {})
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "opacity:0" not in style.replace(" ", ""), "有样式让图片默认不可见"


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

def test_print_stylesheet_has_all_requirements():
    """
    用例 14：@media print 必须同时满足三项，缺一则打印结果不可用。

      1. 白底（深色底会打出整页黑）
      2. page-break-inside: avoid（图文不被跨页拦腰截断）
      3. 隐藏交互控件（按钮、lightbox）

    原第 3 项「展开 <details>」随「全部照片明细」一并移除，报告里已无折叠区。
    """
    html = build_html(aggregate([_photo()]), {})
    assert "@media print" in html
    block = html.split("@media print", 1)[1]
    assert "#fff" in block
    assert "page-break-inside" in block
    assert "no-print" in block
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
    懒插入的代价：滚出过视口的图 src 被卸掉了，直接打印那几页会是空白。
    因此按钮必须先把 src 全部恢复再 print()。
    """
    html = build_html(aggregate([_photo()]), {})
    assert "window.__spRestore" in html
    # 恢复必须发生在 print() 之前
    assert html.index("__spRestore") < html.rindex("window.print()")


def test_save_as_pdf_button_localized():
    """按钮文案跟随语言（spec D7）。"""
    assert "存为 PDF" in build_html(aggregate([_photo()]), {}, is_zh=True)
    assert "Save as PDF" in build_html(aggregate([_photo()]), {}, is_zh=False)

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
    small = estimate_size({"cover": 1, "shot": 4})
    big = estimate_size({"cover": 1, "shot": 160})
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
    jobs = collect_image_jobs(data)
    assert jobs, "应至少有封面与鸟种图任务"

    encoded = {}
    for job in jobs:
        uri = encode_preview(job.path, job.max_edge, job.quality)
        assert uri, f"{job.job_id} 编码失败"
        encoded[job.job_id] = uri

    html = build_html(data, encoded)
    assert "<img data-lazy decoding=" in html, "编码好的图一张都没进 DOM"
    assert 'class="ph' not in html, "仍有占位块，说明有 job_id 对不上"
    # IMGS 数组里的条目数应与实际用到的图片数一致
    assert html.count("<img data-lazy decoding=") >= len(data.species) + 1


# ── 版式改版（等高零裁切画廊 / 无彩色主题）新增用例 ──────────────────────
# Cases added with the equal-height zero-crop gallery and achromatic theme.


def test_render_job_ids_match_collect_image_jobs(tmp_path):
    """
    渲染层查的 job_id 必须全部被 collect_image_jobs 产出过。

    这是本模块最危险的失败模式——两端对不上不会报错，只会让那一格静默退化
    成虚线占位块，而各自的单元测试仍然全绿。（曾经的 rep/small/hd 三档正是
    因此出过 hd 全失效的问题；现在只剩 cover 与 shot 两档，风险小了但不为零。）

    Every job_id the renderer looks up must have been produced; a mismatch
    degrades cells to placeholders silently.
    """
    for n in range(1, 5):
        rows = []
        for i in range(n):
            jpg = tmp_path / f"j{n}_{i}.jpg"
            Image.new("RGB", (900, 600), (70, 90, 70)).save(jpg, quality=80)
            rows.append(_photo(filename=f"j{n}_{i}.NEF",
                               current_path=str(tmp_path / f"j{n}_{i}.NEF"),
                               temp_jpeg_path=str(jpg), burst_id=i,
                               bird_species_cn="同一种", bird_species_en="Same",
                               adj_topiq=float(n - i)))
        data = aggregate(rows)
        assert len(data.species) == 1 and len(data.species[0].photos) == n

        jobs = collect_image_jobs(data)
        produced = {j.job_id for j in jobs}
        for ref in data.species[0].photos:
            assert f"shot:{ref.path}" in produced
        assert f"cover:{data.cover.path}" in produced

        # 真渲染一遍：占位块为 0 才说明两端确实对上了
        encoded = {j.job_id: encode_preview(j.path, j.max_edge, j.quality)
                   for j in jobs}
        html = build_html(data, {k: v for k, v in encoded.items() if v})
        assert 'class="ph' not in html, f"{n} 张时出现占位块，job_id 对不上"


def test_gallery_layout_adapts_to_photo_count(tmp_path):
    """
    版式随张数变化：1 张只有满幅；2 张只有一排（不设满幅）；3/4 张为满幅 + 一排。

    Layout adapts: 1 = hero only, 2 = a single row (no hero), 3/4 = hero + row.
    """
    def render(n: int) -> str:
        rows = []
        for i in range(n):
            jpg = tmp_path / f"g{n}_{i}.jpg"
            Image.new("RGB", (900, 600), (60, 90, 60)).save(jpg, quality=80)
            rows.append(_photo(filename=f"g{n}_{i}.NEF",
                               current_path=str(tmp_path / f"g{n}_{i}.NEF"),
                               temp_jpeg_path=str(jpg),
                               bird_species_cn="测试种", bird_species_en="Test",
                               adj_topiq=float(n - i)))
        data = aggregate(rows)
        jobs = collect_image_jobs(data)
        enc = {j.job_id: encode_preview(j.path, j.max_edge, j.quality) for j in jobs}
        return build_html(data, {k: v for k, v in enc.items() if v})

    one = render(1)
    assert one.count('class="hero"') == 2      # 封面 + 该鸟种的满幅
    assert 'class="row"' not in one

    two = render(2)
    assert two.count('class="hero"') == 1      # 只有封面，鸟种块不设满幅
    assert two.count('class="row"') == 1

    for n in (3, 4):
        html = render(n)
        assert html.count('class="hero"') == 2
        assert html.count('class="row"') == 1
        # 一排里的每张图都必须带 flex-grow，否则等高布局根本没生效
        assert html.count("flex-grow:") == n - 1


def test_probe_jpeg_ratio_reads_real_dimensions(tmp_path):
    """
    宽高比要能从编码后的 JPEG 头部读回来，横/竖/方/极端比例都成立。

    Aspect ratios must be recoverable from the encoded JPEG header.
    """
    for w, h in ((3000, 2000), (2000, 3000), (2400, 2400), (1000, 3000), (3600, 1200)):
        src = tmp_path / f"r{w}x{h}.jpg"
        Image.new("RGB", (w, h), (80, 80, 80)).save(src, quality=88)
        max_edge, quality = IMG_SPECS["shot"]
        uri = encode_preview(str(src), max_edge, quality)
        got = probe_jpeg_ratio(uri)
        assert got is not None
        # 以缩放取整后的实际像素为真值（缩放会引入 ±1px 的取整）
        im = Image.open(src)
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        assert abs(got - im.width / im.height) < 1e-6


def test_probe_jpeg_ratio_falls_back_without_raising():
    """畸形输入一律返回 None，绝不抛异常——一张坏图不能毁掉整份报告。"""
    for bad in ("", "garbage", "data:image/jpeg;base64,", "data:image/jpeg;base64,!!!!"):
        assert probe_jpeg_ratio(bad) is None


def test_row_widths_resolve_to_equal_heights():
    """
    等高布局的数学前提：flex-basis 为 0 时，可用宽度按宽高比分配，
    于是一排里每张图的高度必然相等——横、竖、全景混排也成立。

    The invariant behind the layout: with flex-basis 0, widths land
    proportional to each ratio, so all heights in a row resolve equal.
    """
    ratios = [1.5, 2 / 3, 3.0, 1.0]
    available = 1060 - 8 * (len(ratios) - 1)      # 版心 1060，gap 8px
    total = sum(ratios)
    heights = [(available * r / total) / r for r in ratios]
    assert max(heights) - min(heights) < 1e-9


def test_dark_theme_has_no_leftover_accent_tokens():
    """
    「美术馆」配色：界面不得再出现原来的薄荷绿 / 纯黄强调色，
    颜色只允许留在罕见度与 IUCN 徽章上。

    The achromatic theme must not reintroduce the old accent colors.
    """
    data = aggregate([_photo()])
    html = build_html(data, {})
    for banned in ("#00d4aa", "#ffcc00", "var(--accent)", "var(--gold)"):
        assert banned not in html, f"配色里仍残留 {banned}"


def test_tier_color_is_brightened_for_dark_ground():
    """
    罕见度红在深色底上对比度不足（约 4.3:1），报告端必须提亮后再用；
    共用的 core.rarity_tier 不受影响。

    The shared rarity red fails contrast on the dark ground and must be
    brightened for the report only.
    """
    from core.rarity_tier import tier_name_color
    assert tier_name_color(3) == "#D81E05"          # 共用模块未被改动
    assert _tier_color_on_dark("#D81E05") == "#FF4A32"
    assert _tier_color_on_dark("#FC7F3F") == "#FC7F3F"   # 橙本就达标，原样
    assert _tier_color_on_dark(None) == "#8f8a82"       # 常见 → muted


def test_print_guards_bar_contrast():
    """
    星级条的屏幕用浅灰在白纸上不可见，必须被 !important 压成深灰。
    （原先还有一条「淡入 opacity 复位」，随淡入一并移除。）

    The star bars' light greys are invisible on paper and must be overridden.
    """
    html = build_html(aggregate([_photo()]), {})
    assert ".bars .bar{background:#444 !important}" in html


def test_sections_stay_centered():
    """
    .sec 的左右外边距必须是 auto：它定义在 .wrap 之后，写死 `margin:64px 0`
    会覆盖掉 `margin:0 auto`，整个版心贴左、右边空一条。

    .sec must keep auto side margins or it overrides .wrap's centering.
    """
    html = build_html(aggregate([_photo()]), {})
    assert ".sec{margin:64px auto}" in html
    assert ".sec{margin:64px 0}" not in html


# ── 第二轮细节调整（时间/快门/连拍去重/明细表移除/代表作参数）─────────────
# Second round: minute-precision time, shutter notation, burst dedupe,
# detail-table removal, and pick metrics on the hero.


def test_format_shutter_uses_photographic_notation():
    """
    DB 存的是秒数，直接印就是 `0.0008s`——没人这么报快门。1 秒以下取倒数。

    Sub-second shutter speeds must render as 1/N, not as raw decimals.
    """
    assert format_shutter(0.0008) == "1/1250s"
    assert format_shutter("0.004") == "1/250s"
    assert format_shutter(1) == "1s"
    assert format_shutter(1.3) == "1.3s"
    assert format_shutter("1/500") == "1/500s"      # 已是分数就原样带单位
    assert format_shutter(None) == ""
    assert format_shutter("") == ""
    assert format_shutter("abc") == "abc"           # 解析不了就不猜


def test_format_minute_trims_seconds_and_normalizes_date():
    """EXIF 的 `2026:08:28 08:29:53` → `2026-08-28 08:29`。"""
    assert format_minute("2026:08:28 08:29:53") == "2026-08-28 08:29"
    assert format_minute("2026:08:28 08:29") == "2026-08-28 08:29"
    assert format_minute("") == ""
    assert format_minute("乱七八糟") == "乱七八糟"    # 认不出就原样


def test_cover_time_range_omits_repeated_date_and_seconds():
    """
    封面起止时间截到分钟；同一天只写一次日期。

    The cover's time range is minute-precision and prints the date once.
    """
    rows = [_photo(filename="a.NEF", current_path="/tmp/pick/a.NEF",
                   date_time_original="2026:08:28 08:29:53"),
            _photo(filename="b.NEF", current_path="/tmp/pick/b.NEF",
                   date_time_original="2026:08:28 11:51:21")]
    html = build_html(aggregate(rows), {})
    assert "2026-08-28 08:29 – 11:51" in html
    assert "08:29:53" not in html and "11:51:21" not in html


def test_burst_dedupe_keeps_one_frame_per_group():
    """
    同一连拍组只上一张，否则一个鸟种的四格全是几乎相同的画面。
    留下的必须是该组质量最高的一张，而非快门顺序上的第一张。

    One frame per burst, and it must be the group's best rather than its
    first shutter.
    """
    rows = []
    for i in range(8):
        rows.append(_photo(filename=f"burst{i}.NEF",
                           current_path=f"/tmp/pick/burst{i}.NEF",
                           bird_species_cn="西大亭鸟", bird_species_en="Bowerbird",
                           burst_id=i // 4,          # 8 张分属 2 个连拍组
                           rating=3, adj_topiq=float(i)))
    data = aggregate(rows)
    shown = data.species[0].photos
    assert data.species[0].count == 8, "标注的总张数仍是全部 8 张"
    assert len(shown) == 2, "两个连拍组只应各出一张"
    # 每组取 adj_topiq 最高的：组 0 的最高是 burst3，组 1 的最高是 burst7
    assert {p.filename for p in shown} == {"burst3.NEF", "burst7.NEF"}


def test_photos_without_burst_id_are_all_kept():
    """burst_id 为空（非连拍）的照片各自独立，不能被去重误伤。"""
    rows = [_photo(filename=f"solo{i}.NEF", current_path=f"/tmp/pick/solo{i}.NEF",
                   bird_species_cn="鹊鹩", bird_species_en="Wren",
                   burst_id=None, adj_topiq=float(i)) for i in range(4)]
    data = aggregate(rows)
    assert len(data.species[0].photos) == 4


def test_detail_table_is_gone():
    """
    「全部照片明细」整块已移除：不再有表格、不再有折叠区、不再产缩略图任务。

    The all-photos detail table is gone: no table, no <details>, no thumbs.
    """
    rows = [_photo(filename=f"d{i}.NEF", current_path=f"/tmp/pick/d{i}.NEF",
                   burst_id=i) for i in range(5)]
    data = aggregate(rows)
    jobs = collect_image_jobs(data)
    assert not any(j.job_id.startswith("thumb:") for j in jobs)
    assert "thumb" not in IMG_SPECS

    html = build_html(data, {})
    for gone in ("<table", "<details>", "<summary>", 'id="detail"', "全部照片明细"):
        assert gone not in html, f"明细表残留: {gone}"


def test_hero_carries_pick_metrics_and_secondaries_do_not():
    """
    锐度/美学/颜值只挂在代表作上；副图仍然只有曝光组合。

    Pick metrics ride on the hero only; secondaries keep the exposure line.
    """
    rows = []
    for i in range(4):
        rows.append(_photo(filename=f"m{i}.NEF", current_path=f"/tmp/pick/m{i}.NEF",
                           bird_species_cn="冠鸠", bird_species_en="Bronzewing",
                           burst_id=i, adj_sharpness=418.6, adj_topiq=5.74,
                           aesthetic_index=63.2, rating=3))
    html = build_html(aggregate(rows), {})
    assert html.count("锐度 419") == 1, "锐度只应出现在代表作上"
    # 美学量程只有 3–6.5，必须留一位小数，取整会把整批压成清一色的 5 和 6
    assert html.count("美学 5.7") == 1
    assert html.count("颜值 63") == 1
    # 位置：必须排在曝光组合之后
    cap = html.split('class="cap">', 1)[1].split("</div>", 1)[0]
    assert cap.index("ISO") < cap.index("锐度")


def test_pick_metrics_localized_and_skip_missing_values():
    """英文界面用英文标签；缺失的项不出现，不留空占位。"""
    rows = [_photo(filename="e.NEF", current_path="/tmp/pick/e.NEF",
                   bird_species_cn="鹊鹩", bird_species_en="Wren",
                   adj_sharpness=300.0, adj_topiq=None, aesthetic_index=None)]
    html = build_html(aggregate(rows), {}, is_zh=False)
    assert "Sharp 300" in html
    assert "Aesth" not in html and "Beauty" not in html


def test_aesthetic_keeps_one_decimal_to_stay_discriminative():
    """
    美学分实际只在 3–6.5 之间（本机真实批次 min 3.32 / max 6.39），取整会让
    一整批鸟种清一色显示 5 和 6，等于没显示。必须保留一位小数。

    同时这也是技能等级阈值的口径（core/skill_presets.py 的 4.5/4.8/5.5），
    报告与设置里的数对得上才不会互相打架。

    Aesthetics spans only ~3-6.5, so rounding flattens an entire batch.
    """
    def cap_of(topiq: float) -> str:
        rows = [_photo(filename="x.NEF", current_path="/tmp/pick/x.NEF",
                       bird_species_cn="冠鸠", bird_species_en="Bronzewing",
                       adj_topiq=topiq, adj_sharpness=500.0)]
        html = build_html(aggregate(rows), {})
        return html.split('class="cap">', 1)[1].split("</div>", 1)[0]

    assert "美学 5.1" in cap_of(5.09)
    assert "美学 6.4" in cap_of(6.39)
    # 两个相差 0.4 的分必须显示成不同的数，取整就都是 5 了
    assert cap_of(5.1) != cap_of(5.5)


def test_lightbox_zooms_the_image_already_on_the_page(tmp_path):
    """
    点击放大用的是页面上那一张，不再有第二份副本。

    旧实现为每张图额外编码一份 1200px 的 hd，占掉整个报告的 74%，而画面与
    页面上那张完全相同。更糟的是它一直是坏的：hd 图没有 DOM 节点、从没被
    注册过，每个 data-hd 都是 -1，点击毫无反应——体积照付，功能不存在。

    Zoom reuses the on-page image; the old hd duplicates cost 74% of the file
    and never worked (every index resolved to -1).
    """
    rows = []
    for i in range(3):
        jpg = tmp_path / f"lb{i}.jpg"
        Image.new("RGB", (1600, 1067), (50, 90, 120)).save(jpg, quality=85)
        rows.append(_photo(filename=f"lb{i}.NEF",
                           current_path=str(tmp_path / f"lb{i}.NEF"),
                           temp_jpeg_path=str(jpg), burst_id=i,
                           bird_species_cn="冠鸠", bird_species_en="Bronzewing"))
    data = aggregate(rows)
    jobs = collect_image_jobs(data)
    encoded = {j.job_id: encode_preview(j.path, j.max_edge, j.quality) for j in jobs}
    html = build_html(data, {k: v for k, v in encoded.items() if v})

    # 放大机制不再依赖任何数组或索引属性
    assert "const HD=" not in html
    assert "data-hd" not in html
    # 点击目标是 .shot 整块，取的是它自己的 <img>
    assert "querySelectorAll('.shot')" in html
    assert "el.querySelector('img')" in html


def test_each_photo_is_encoded_exactly_once():
    """
    同一张照片不得产出两个编码任务——重复副本正是旧版 74% 体积的来源。

    No photo may yield two encoding jobs; duplicates were the old bloat.
    """
    rows = [_photo(filename=f"v{i}.NEF", current_path=f"/tmp/pick/v{i}.NEF",
                   burst_id=i, bird_species_cn="鹊鹩", bird_species_en="Wren")
            for i in range(4)]
    data = aggregate(rows)
    jobs = collect_image_jobs(data)
    paths = [j.path for j in jobs if j.job_id.startswith("shot:")]
    assert len(paths) == len(set(paths)), "同一张照片被编码了不止一次"
    # 封面与某张展示图可能是同一个文件，但那是两个不同尺寸的档位，允许
    assert len(jobs) == len(set(j.job_id for j in jobs))
