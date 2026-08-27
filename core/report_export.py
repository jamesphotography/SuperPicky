#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 可分享总结报告生成器 / Shareable summary report generator.

本模块**不得 import 任何 Qt 模块**：它是纯函数层（字典进、字符串出），
以便脱离 QApplication 单测，并供未来的 CLI 复用。
详见 docs/specs/2026-08-26-share-report-html-design.md 第 4.1 节。

This module MUST NOT import Qt. It is a pure dict-in / string-out layer so
it can be unit-tested without a QApplication and reused by a future CLI.
"""

from __future__ import annotations

import base64
import html as _html
import io
import json
import os
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageOps

from core.rarity_tier import gbif_score_to_tier
from tools.file_utils import sibling_jpeg

# IUCN 徽标只对「易危及以上」显示，避免 LC 满屏噪音（spec 5.1 ②）。
# Only render an IUCN badge for Vulnerable-and-worse categories.
IUCN_BADGE_SHOWN = frozenset({"VU", "EN", "CR", "CR(PE)", "CR(PEW)", "EW", "EX"})

# 每个鸟种展示张数上限（spec D8）/ Max photos shown per species.
DEFAULT_PER_SPECIES = 4


@dataclass(frozen=True)
class PhotoRef:
    """
    报告中引用的单张照片。

    filename 只含文件名不含目录（spec D6：绝对路径不得进入报告）；
    path 是绝对路径，仅供图片编码使用，不会被渲染进 HTML。

    A single photo referenced by the report. `filename` is basename-only so
    no absolute path can leak into the HTML; `path` is used for encoding only.
    """
    filename: str
    path: str
    rating: int
    picked: bool
    sharpness: Optional[float]
    aesthetic: Optional[float]
    iso: Optional[int]
    shutter: Optional[str]
    aperture: Optional[str]
    focal_35mm: Optional[int]
    species_cn: str
    species_en: str
    captured_at: Optional[str]


@dataclass(frozen=True)
class SpeciesBlock:
    """
    鸟种画廊中的一个区块。

    count 是该鸟种的**总张数**（用于「34 张」这类标注），
    photos 是实际展示的至多 DEFAULT_PER_SPECIES 张，首张为代表作。

    One species block. `count` is the species' total photo count, while
    `photos` holds the (at most 4) shown photos, the first being the hero.
    """
    name_cn: str
    name_en: str
    count: int
    tier: Optional[int]
    iucn: Optional[str]
    photos: List[PhotoRef]


@dataclass(frozen=True)
class GearStats:
    """器材统计 / Camera and lens statistics."""
    cameras: List[Tuple[str, int]]
    lenses: List[Tuple[str, int]]
    top_focal: Optional[int]
    iso_min: Optional[int]
    iso_max: Optional[int]


@dataclass(frozen=True)
class ReportData:
    """报告的完整数据模型，由 aggregate() 产出、build_html() 消费。"""
    dir_name: str
    total: int
    by_rating: Dict[int, int]
    picked: int
    flying: int
    focus_precise: int
    bird_total: int
    species: List[SpeciesBlock]
    cover: Optional[PhotoRef]
    shot_start: Optional[str]
    shot_end: Optional[str]
    location: str
    gps: Optional[Tuple[float, float, Optional[float]]]
    gear: GearStats
    burst_groups: int
    burst_avg: float
    detail: List[PhotoRef]


def preview_candidates(row: dict) -> List[str]:
    """
    按优先级返回可用作预览的路径：temp_jpeg_path → 同名 JPG/JPEG 边车。

    与 ui/thumbnail_grid.py:205 的 _thumbnail_candidates 保持同一优先级，
    刻意不含任何带标注的调试图。路径需已是绝对路径。

    Return existing preview paths in priority order, mirroring the grid's
    resolver. Debug artifacts are deliberately excluded.
    """
    out: List[str] = []

    def _add(path: Optional[str]) -> None:
        if path and path not in out and os.path.exists(path):
            out.append(path)

    _add(row.get("temp_jpeg_path"))
    for key in ("current_path", "original_path"):
        val = row.get(key)
        if val:
            jpeg_sidecar = sibling_jpeg(val)
            _add(jpeg_sidecar)
    return out


def _to_ref(row: dict) -> PhotoRef:
    """
    把一条 report.db 记录转成 PhotoRef（basename 化文件名）。

    path 是可解码的预览路径：优先取 preview_candidates 的首选项，
    回退到 current_path or original_path（保持向后兼容：没有预览时走占位符）。

    Convert a report.db row to PhotoRef, extracting only the basename for filename.
    Path is set to a decodable preview if available, falling back to current/original path.
    """
    raw_name = row.get("filename") or ""
    candidates = preview_candidates(row)
    preview_path = candidates[0] if candidates else ""
    raw_path = row.get("current_path") or row.get("original_path") or ""
    # 优先用可预览的路径；没有预览则回退到原始路径（支持无预览记录走占位符）
    path = preview_path or raw_path

    return PhotoRef(
        filename=os.path.basename(raw_name) or os.path.basename(raw_path),
        path=path,
        rating=int(row.get("rating") or 0),
        picked=bool(row.get("picked")),
        sharpness=row.get("adj_sharpness"),
        aesthetic=row.get("adj_topiq"),
        iso=row.get("iso"),
        shutter=row.get("shutter_speed"),
        aperture=row.get("aperture"),
        focal_35mm=row.get("focal_length_35mm"),
        species_cn=row.get("bird_species_cn") or "",
        species_en=row.get("bird_species_en") or "",
        captured_at=row.get("date_time_original"),
    )


def _pick_key(row: dict) -> tuple:
    """
    展示图选取排序键：picked > rating > adj_topiq，均为降序。

    Sort key for choosing which photos to show: picked, then rating,
    then aesthetic score — all descending.
    """
    return (
        1 if row.get("picked") else 0,
        int(row.get("rating") or 0),
        float(row.get("adj_topiq") or 0.0),
    )


def _location_text(rows: List[dict]) -> str:
    """
    取 city/state/country 的众数，拼成城市级地点字符串（spec D3）。

    Extract city/state/country by frequency and build a location string.
    """
    parts = []
    for key in ("city", "state_province", "country"):
        vals = [r.get(key) for r in rows if r.get(key)]
        if vals:
            parts.append(Counter(vals).most_common(1)[0][0])
    return " · ".join(parts)


def _gear_stats(rows: List[dict]) -> GearStats:
    """
    统计机身/镜头分布、最常用等效焦距、ISO 区间。

    Compute camera/lens frequencies, most-used focal length, and ISO range.
    """
    cams = Counter(r["camera_model"] for r in rows if r.get("camera_model"))
    lenses = Counter(r["lens_model"] for r in rows if r.get("lens_model"))
    focals = Counter(r["focal_length_35mm"] for r in rows if r.get("focal_length_35mm"))
    isos = [r["iso"] for r in rows if r.get("iso")]
    return GearStats(
        cameras=cams.most_common(),
        lenses=lenses.most_common(),
        top_focal=focals.most_common(1)[0][0] if focals else None,
        iso_min=min(isos) if isos else None,
        iso_max=max(isos) if isos else None,
    )


def aggregate(photos: List[dict], *, include_gps: bool = False,
              per_species: int = DEFAULT_PER_SPECIES) -> ReportData:
    """
    把 report.db 记录聚合成报告数据模型。

    参数:
        photos (List[dict]): 照片记录列表。路径字段**必须已由上游解析为绝对
            路径**（见 ui/results_browser_window.py:1264 的 _resolve_photo_paths）；
            本函数不自行拼接路径。
        include_gps (bool): 是否保留精确 GPS 坐标。为 False 时坐标在**本层即被
            丢弃**，不进入返回值——渲染层隐藏等于没脱敏（spec 4.3）。
        per_species (int): 每个鸟种展示张数上限。

    返回:
        ReportData: 完整报告数据模型。

    Aggregate report.db rows into the report data model. Paths must already be
    absolute (resolved upstream). When include_gps is False the coordinates are
    dropped here, not merely hidden at render time.
    """
    total = len(photos)
    # 计数桶开到 5 星：自动评分只产 -1..3，4/5 是用户在浏览器里手动升出来的
    # （详情面板 ▲ / 对比视图 / 键盘 4、5），漏掉这两档会让各档之和小于总张数。
    # Buckets go up to 5: the auto pipeline emits -1..3, while 4/5 come from
    # manual promotion in the browser. Dropping them would break the sum.
    by_rating = {r: 0 for r in (-1, 0, 1, 2, 3, 4, 5)}
    for row in photos:
        rating = int(row.get("rating") or 0)
        if rating in by_rating:
            by_rating[rating] += 1

    bird_rows = [r for r in photos if r.get("has_bird")]

    # 鸟种分组：以中文名为分组键，空名（未识别）不成块。
    groups: Dict[str, List[dict]] = {}
    for row in bird_rows:
        key = row.get("bird_species_cn") or row.get("bird_species_en") or ""
        if key:
            groups.setdefault(key, []).append(row)

    blocks: List[SpeciesBlock] = []
    for rows in groups.values():
        ordered = sorted(rows, key=_pick_key, reverse=True)
        head = ordered[0]
        blocks.append(SpeciesBlock(
            name_cn=head.get("bird_species_cn") or "",
            name_en=head.get("bird_species_en") or "",
            count=len(rows),
            tier=gbif_score_to_tier(head.get("gbif_rarity_100")),
            iucn=head.get("iucn_category"),
            photos=[_to_ref(r) for r in ordered[:per_species]],
        ))
    # 罕见度降序；tier 为 None 视为最低，同 tier 时张数多者在前。
    blocks.sort(key=lambda b: (b.tier if b.tier is not None else -1, b.count),
                reverse=True)

    # 封面：全库 adj_topiq 最高的一张（spec 5.1 ①，非第一张）。
    cover_rows = sorted(bird_rows, key=lambda r: float(r.get("adj_topiq") or 0.0),
                        reverse=True)
    cover = _to_ref(cover_rows[0]) if cover_rows else None

    times = sorted(r["date_time_original"] for r in photos if r.get("date_time_original"))

    burst_ids = [r["burst_id"] for r in photos if r.get("burst_id") is not None]
    burst_groups = len(set(burst_ids))
    burst_avg = (len(burst_ids) / burst_groups) if burst_groups else 0.0

    gps: Optional[Tuple[float, float, Optional[float]]] = None
    if include_gps:
        for row in photos:
            if row.get("gps_latitude") is not None and row.get("gps_longitude") is not None:
                gps = (row["gps_latitude"], row["gps_longitude"], row.get("gps_altitude"))
                break

    return ReportData(
        dir_name="",
        total=total,
        by_rating=by_rating,
        picked=sum(1 for r in photos if r.get("picked")),
        flying=sum(1 for r in photos if r.get("is_flying")),
        focus_precise=sum(1 for r in photos if r.get("focus_status") == "BEST"),
        bird_total=len(bird_rows),
        species=blocks,
        cover=cover,
        shot_start=times[0] if times else None,
        shot_end=times[-1] if times else None,
        location=_location_text(photos),
        gps=gps,
        gear=_gear_stats(photos),
        burst_groups=burst_groups,
        burst_avg=burst_avg,
        detail=[_to_ref(r) for r in photos],
    )


# 分档规格（spec 6.2）：(长边, JPEG 质量)
# Size tiers: (max edge, JPEG quality)
IMG_SPECS = {
    "cover": (1800, 82),
    "rep":   (900, 80),
    "small": (400, 78),
    "hd":    (1200, 80),
    "thumb": (160, 72),
}


@dataclass(frozen=True)
class ImageJob:
    """一个待编码的图片任务 / A single image encoding job."""
    job_id: str
    path: str
    max_edge: int
    quality: int


def preview_availability(photos: List[dict]) -> Tuple[int, int]:
    """
    预检：统计有多少条记录存在可用预览。只做 os.path.exists，不解码。

    参数:
        photos (List[dict]): 照片记录（路径已解析为绝对路径）。

    返回:
        Tuple[int, int]: (可用数, 总数)。

    Cheap pre-flight check counting how many rows have a usable preview.
    Stat-only; nothing is decoded.
    """
    available = sum(1 for row in photos if preview_candidates(row))
    return available, len(photos)


def encode_preview(path: str, max_edge: int, quality: int) -> Optional[str]:
    """
    把一张图片解码、缩放、重编码为 base64 data URI。

    参数:
        path (str): 图片绝对路径。
        max_edge (int): 输出长边上限（只缩不放）。
        quality (int): JPEG 质量。

    返回:
        Optional[str]: `data:image/jpeg;base64,...`；任何失败返回 None，
        由调用方渲染占位块（spec 7.2：绝不让一张坏数据毁掉整份报告）。

    异常:
        无（不抛异常）——任何失败（文件不存在、格式错误、解码异常等）均返回 None。

    Decode, downscale and re-encode one image as a base64 data URI.
    Returns None on any failure so a single bad file cannot abort the report.

    Exceptions:
        None (never raises) — any failure (missing file, corrupt data, etc.) returns None.
    """
    try:
        with Image.open(path) as im:
            # draft() 让 libjpeg 直接按 1/2、1/4、1/8 DCT 缩放解码，是全流程性能
            # 关键（spec 6.3）；仅对 JPEG 有效，其他格式为无操作。
            # draft() decodes at reduced DCT scale for JPEG — the key optimization.
            im.draft("RGB", (max_edge, max_edge))
            im = ImageOps.exif_transpose(im)      # 等价于 QImageReader.setAutoTransform
            im = im.convert("RGB")
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def collect_image_jobs(data: ReportData, *,
                       with_detail_thumbs: bool = True) -> List[ImageJob]:
    """
    列出生成该报告所需的全部图片编码任务。

    与编码本身分离，好让 UI 层逐个调用 encode_preview 以上报进度，
    而本模块保持纯函数、不持有回调（spec 4.1）。

    参数:
        data (ReportData): 聚合结果。
        with_detail_thumbs (bool): 是否为明细表生成缩略图。照片总数 > 600 时
            由调用方传 False，明细表退化为纯文字（spec 6.4）。

    返回:
        List[ImageJob]: 去重后的任务列表。

    Enumerate every image encoding job the report needs, kept separate from
    encoding itself so the UI layer can report progress per job.
    """
    jobs: Dict[str, ImageJob] = {}

    def _add(kind: str, ref: PhotoRef) -> None:
        max_edge, quality = IMG_SPECS[kind]
        job_id = f"{kind}:{ref.path}"
        if job_id not in jobs and ref.path:
            jobs[job_id] = ImageJob(job_id, ref.path, max_edge, quality)

    if data.cover:
        _add("cover", data.cover)
    for block in data.species:
        for index, ref in enumerate(block.photos):
            _add("rep" if index == 0 else "small", ref)
            _add("hd", ref)
    if with_detail_thumbs:
        for ref in data.detail:
            _add("thumb", ref)
    return list(jobs.values())


# ── HTML 渲染 / HTML rendering ────────────────────────────────────────────────

# 深色单一主题（spec 5.0）：独立 HTML 文件没有宿主主题可跟随，
# 深色底让鸟类羽色与背景虚化显色更好。
# Single dark theme: a standalone file has no host theme to follow, and a
# dark ground renders plumage and bokeh better.
_CSS_BASE = """
:root{--bg:#0d0d0f;--card:#16161a;--text:#e8e8ea;--muted:#8a8a8e;
--line:#26262b;--gold:#ffcc00;--accent:#00d4aa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:"PingFang SC","Microsoft YaHei",-apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;line-height:1.6}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px}
.ph{background:var(--card);border:1px dashed var(--line);color:var(--muted);
display:flex;align-items:center;justify-content:center;font-size:12px;
min-height:80px;text-align:center;padding:8px;word-break:break-all}
img{display:block;width:100%;height:auto;background:var(--card)}
.cover{position:relative;margin-bottom:32px}
.cover img,.cover .ph{max-height:70vh;object-fit:cover}
.cover h1{font-size:28px;margin:16px 0 4px}
.cover .sub{color:var(--muted);font-size:14px}
.nums{display:flex;gap:28px;margin-top:16px;flex-wrap:wrap}
.nums div{text-align:center}
.nums b{display:block;font-size:30px;color:var(--accent)}
.nums span{font-size:12px;color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;
padding:40px 0;border-top:1px solid var(--line);margin-top:48px}
"""

# 区块样式（spec 5.1 ②③④）：画廊/数据条/明细表/lightbox。
# Block styles for the gallery, stat bars, detail table and lightbox.
_CSS_BASE += """
.sec{margin:48px 0}
.sec h2{font-size:20px;margin:0 0 20px;padding-bottom:10px;
border-bottom:1px solid var(--line)}
.sp{margin-bottom:40px}
.sp .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.sp .cn{font-size:18px;font-weight:600}
.sp .en{font-size:13px;color:var(--muted);font-style:italic}
.sp .cnt{font-size:12px;color:var(--muted)}
.tier{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid currentColor}
.iucn{font-size:11px;padding:1px 7px;border-radius:10px;
background:#7f1d1d;color:#fecaca;font-weight:600}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:8px}
.grid .rest{display:grid;grid-template-rows:repeat(3,1fr);gap:8px}
.cap{font-size:11px;color:var(--muted);margin-top:4px}
.bars div{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}
.bars .bar{height:10px;background:var(--gold);border-radius:5px}
.kv{display:flex;flex-wrap:wrap;gap:12px 32px;font-size:13px;color:var(--muted)}
.kv b{color:var(--text);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;
white-space:nowrap}
th{color:var(--muted);cursor:pointer;user-select:none}
td.thumbcol{width:56px}
td.thumbcol img,td.thumbcol .ph{width:48px;height:48px;object-fit:cover;min-height:0}
.tablewrap{overflow-x:auto}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;
align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
#lb img{max-width:94vw;max-height:94vh;width:auto}
"""

# 视口懒插入（spec 6.1）：图片一律不写 src，滚到视口才赋值、滚离即释放，
# 使常驻位图恒定在几屏之内，与鸟种数、照片数完全无关。
# Viewport-based lazy insertion keeps resident bitmaps constant regardless
# of how many species or photos the report contains.
_JS_LAZY = """
(function(){
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      var el=e.target,i=el.dataset.idx;
      if(e.isIntersecting){ if(!el.src&&IMGS[i]) el.src=IMGS[i]; }
      else { el.removeAttribute('src'); }
    });
  },{rootMargin:'200% 0px'});
  document.querySelectorAll('img[data-idx]').forEach(function(el){io.observe(el);});
})();
"""


# 点击放大：从 IMGS 取 hd 版本，关闭即释放位图（spec 6.1）。
# Lightbox pulls the hd variant from IMGS and releases it on close.
_JS_LIGHTBOX = """
(function(){
  var lb=document.getElementById('lb'),im=lb.querySelector('img');
  document.querySelectorAll('[data-hd]').forEach(function(el){
    el.addEventListener('click',function(){
      var i=el.dataset.hd; if(i<0||!IMGS[i])return;
      im.src=IMGS[i]; lb.style.display='flex';
    });
  });
  lb.addEventListener('click',function(){
    lb.style.display='none'; im.removeAttribute('src');
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'&&lb.style.display==='flex'){
      lb.style.display='none'; im.removeAttribute('src');
    }
  });
})();
"""

# 明细表点击表头排序。纯前端行为，不改变任何统计口径。
# Client-side table sorting; never affects the aggregated statistics.
_JS_SORT = """
(function(){
  var t=document.getElementById('detail'); if(!t)return;
  t.querySelectorAll('th').forEach(function(th,i){
    th.addEventListener('click',function(){
      var tb=t.tBodies[0],rows=[].slice.call(tb.rows);
      var dir=th.dataset.dir==='asc'?-1:1; th.dataset.dir=dir===1?'asc':'desc';
      rows.sort(function(a,b){
        var x=a.cells[i].dataset.v||a.cells[i].textContent;
        var y=b.cells[i].dataset.v||b.cells[i].textContent;
        var nx=parseFloat(x),ny=parseFloat(y);
        if(!isNaN(nx)&&!isNaN(ny))return (nx-ny)*dir;
        return String(x).localeCompare(String(y))*dir;
      });
      rows.forEach(function(r){tb.appendChild(r);});
    });
  });
})();
"""


def _esc(value: object) -> str:
    """HTML 转义。鸟种名/文件名/caption 均为外部输入，必须全部过这里。"""
    return _html.escape(str(value if value is not None else ""))


class _ImageRegistry:
    """
    收集 data URI 并分配索引，供 IMGS 数组与 data-idx 配对使用。

    Collects data URIs and hands out indices so the DOM can reference them
    by data-idx instead of embedding them in src attributes.
    """

    def __init__(self, encoded: Dict[str, str]) -> None:
        self._encoded = encoded
        self._uris: List[str] = []
        self._index: Dict[str, int] = {}

    def tag(self, job_id: str, alt: str, css_class: str = "") -> str:
        """
        返回一个 <img data-idx=..>；job_id 无对应图时返回占位块。

        Return a lazy <img>, or a placeholder block when the image is missing.
        """
        uri = self._encoded.get(job_id)
        if not uri:
            return f'<div class="ph {css_class}">{_esc(alt)}</div>'
        if job_id not in self._index:
            self._index[job_id] = len(self._uris)
            self._uris.append(uri)
        cls = f' class="{css_class}"' if css_class else ""
        return f'<img data-idx="{self._index[job_id]}" alt="{_esc(alt)}"{cls}>'

    def uri_index(self, job_id: str) -> int:
        """返回该 job 在 IMGS 中的下标；未注册返回 -1（供 lightbox 用）。"""
        return self._index.get(job_id, -1)

    def script(self) -> str:
        """产出 IMGS 数组的 <script> 内容。"""
        return "const IMGS=" + json.dumps(self._uris) + ";"


def _cover_html(data: ReportData, reg: _ImageRegistry, is_zh: bool) -> str:
    """封面：满幅大图 + 标题 + 地点 + 三个大数字（spec 5.1 ①）。"""
    lab = ("总张数", "鸟种", "精选") if is_zh else ("Photos", "Species", "Picked")
    img = reg.tag(f"cover:{data.cover.filename}", data.cover.filename) if data.cover else ""
    when = ""
    if data.shot_start:
        when = _esc(data.shot_start)
        if data.shot_end and data.shot_end != data.shot_start:
            when += " – " + _esc(data.shot_end)
    place = _esc(data.location)
    if data.gps:
        lat, lon, alt = data.gps
        place += f" · {lat:.4f}, {lon:.4f}"
        if alt is not None:
            place += f" · {alt:.0f}m"
    sub = " · ".join(x for x in (when, place) if x)
    return f"""<section class="cover">{img}
<div class="wrap"><h1>{_esc(data.dir_name)}</h1>
<div class="sub">{sub}</div>
<div class="nums">
<div><b>{data.total}</b><span>{lab[0]}</span></div>
<div><b>{len(data.species)}</b><span>{lab[1]}</span></div>
<div><b>{data.picked}</b><span>{lab[2]}</span></div>
</div></div></section>"""


def _photo_cell(ref: PhotoRef, kind: str, reg: "_ImageRegistry") -> str:
    """
    一张展示图 + 参数小字；整块可点击，点击后 lightbox 取 hd 版本。

    Render one shown photo with its EXIF caption; clicking opens the hd
    variant in the lightbox.
    """
    hd = reg.uri_index(f"hd:{ref.filename}")
    img = reg.tag(f"{kind}:{ref.filename}", ref.filename)
    bits = []
    if ref.shutter:
        bits.append(f"{ref.shutter}s")
    if ref.aperture:
        bits.append(f"f/{ref.aperture}")
    if ref.iso:
        bits.append(f"ISO {ref.iso}")
    if ref.focal_35mm:
        bits.append(f"{ref.focal_35mm}mm")
    cap = f'<div class="cap">{_esc(" · ".join(bits))}</div>' if bits else ""
    return f'<div data-hd="{hd}">{img}{cap}</div>'


def _species_html(data: ReportData, reg: "_ImageRegistry", is_zh: bool) -> str:
    """
    鸟种画廊：每种一块，区块按罕见度降序，块内 1 大 + 至多 3 小（spec 5.1 ②）。

    鸟种数不封顶——常驻位图已由懒插入与内容量脱钩（spec D8 / 6.4）。

    Species gallery: one block per species, ordered by rarity descending,
    one hero plus up to three secondary photos. Species count is never capped.
    """
    from core.rarity_tier import tier_name, tier_name_color

    if not data.species:
        return ""
    title = "本次鸟种" if is_zh else "Species"
    unit = "张" if is_zh else " photos"
    out = [f'<section class="sec wrap"><h2>{title} ({len(data.species)})</h2>']
    for block in data.species:
        primary = block.name_cn if is_zh else block.name_en
        secondary = block.name_en if is_zh else block.name_cn
        badges = ""
        if block.tier is not None:
            color = tier_name_color(block.tier, default="#8a8a8e")
            badges += (f'<span class="tier" style="color:{_esc(color)}">'
                       f'{_esc(tier_name(block.tier, is_zh))}</span>')
        if block.iucn in IUCN_BADGE_SHOWN:
            badges += f'<span class="iucn">{_esc(block.iucn)}</span>'
        hero = _photo_cell(block.photos[0], "rep", reg) if block.photos else ""
        rest = "".join(_photo_cell(r, "small", reg) for r in block.photos[1:])
        out.append(
            '<div class="sp"><div class="hd">'
            f'<span class="cn">{_esc(primary)}</span>'
            f'<span class="en">{_esc(secondary)}</span>{badges}'
            f'<span class="cnt">{block.count}{unit}</span></div>'
            f'<div class="grid">{hero}<div class="rest">{rest}</div></div></div>'
        )
    out.append("</section>")
    return "".join(out)


def _stats_html(data: ReportData, is_zh: bool) -> str:
    """
    数据区：星级分布条形图 + 命中率 + 器材 + 连拍（spec 5.1 ③）。

    条形图用纯 CSS 宽度百分比绘制，不引入任何图表库。

    Statistics section. Bars are plain CSS widths; no charting library.
    """
    title = "数据" if is_zh else "Statistics"
    labels = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★", 1: "★", 0: "0",
              -1: ("无鸟" if is_zh else "No bird")}
    top = max(data.by_rating.values()) or 1
    bars = []
    for rating in (5, 4, 3, 2, 1, 0, -1):
        count = data.by_rating.get(rating, 0)
        # 4/5 星是手动档，绝大多数批次为空——空的就不画，免得两条恒为 0
        # 的条稀释信息；其余档位即使为 0 也保留，分布形状才完整。
        # Hide unused manual tiers; keep the automatic ones even at zero so
        # the shape of the distribution stays readable.
        if rating >= 4 and count == 0:
            continue
        pct = (count / data.total * 100) if data.total else 0.0
        width = count / top * 320
        bars.append(f'<div><span style="width:52px">{labels[rating]}</span>'
                    f'<span class="bar" style="width:{width:.0f}px"></span>'
                    f'<span>{count} ({pct:.1f}%)</span></div>')
    # 命中率口径为「3 星及以上」：4/5 星是比 3 星更好的片子，若只数 3 星，
    # 用户每手动升一张，命中率反而下降——那是反直觉的错数。
    # Hit rate counts 3-and-above; counting only 3 would make promoting a
    # photo lower the number.
    keepers = sum(data.by_rating.get(r, 0) for r in (3, 4, 5))
    hit = (keepers / data.total * 100) if data.total else 0.0
    gear = data.gear
    kv = []
    if is_zh:
        kv.append(f'命中率 <b>{hit:.1f}%</b>')
        if data.flying:
            kv.append(f'飞版 <b>{data.flying}</b>')
        if data.focus_precise:
            kv.append(f'精焦 <b>{data.focus_precise}</b>')
        if data.burst_groups:
            kv.append(f'连拍 <b>{data.burst_groups}</b> 组 · 均 <b>{data.burst_avg:.1f}</b> 张')
    else:
        kv.append(f'Hit rate <b>{hit:.1f}%</b>')
        if data.flying:
            kv.append(f'In flight <b>{data.flying}</b>')
        if data.focus_precise:
            kv.append(f'Sharp focus <b>{data.focus_precise}</b>')
        if data.burst_groups:
            kv.append(f'Bursts <b>{data.burst_groups}</b> · avg <b>{data.burst_avg:.1f}</b>')
    if gear.cameras:
        kv.append(f'<b>{_esc(gear.cameras[0][0])}</b>')
    if gear.lenses:
        kv.append(f'<b>{_esc(gear.lenses[0][0])}</b>')
    if gear.top_focal:
        kv.append(f'<b>{gear.top_focal}mm</b>')
    if gear.iso_min is not None:
        kv.append(f'ISO <b>{gear.iso_min}–{gear.iso_max}</b>')
    return (f'<section class="sec wrap"><h2>{title}</h2>'
            f'<div class="bars">{"".join(bars)}</div>'
            f'<div class="kv" style="margin-top:20px">{"".join(kv)}</div></section>')


def _detail_html(data: ReportData, reg: "_ImageRegistry", is_zh: bool,
                 with_thumbs: bool) -> str:
    """
    折叠明细表（spec 5.1 ④）。with_thumbs=False 时去掉缩略图列。

    数值列写 data-v 属性供表头排序按数值而非字符串比较。

    Collapsible detail table. Numeric cells carry data-v so header sorting
    compares numbers rather than strings.
    """
    title = f"全部照片明细 ({data.total})" if is_zh else f"All photos ({data.total})"
    heads = (["", "文件名", "鸟种", "★", "精选", "锐度", "美学", "ISO",
              "快门", "光圈", "焦距", "时间"] if is_zh else
             ["", "File", "Species", "★", "Picked", "Sharp", "Aesth", "ISO",
              "Shutter", "Aperture", "Focal", "Time"])
    if not with_thumbs:
        heads = heads[1:]
    rows = []
    for ref in data.detail:
        cells = []
        if with_thumbs:
            cells.append('<td class="thumbcol">'
                         f'{reg.tag(f"thumb:{ref.filename}", ref.filename)}</td>')
        species = (ref.species_cn if is_zh else ref.species_en) or "—"
        sharp = ref.sharpness or 0.0
        aesth = ref.aesthetic or 0.0
        cells += [
            f'<td>{_esc(ref.filename)}</td>',
            f'<td>{_esc(species)}</td>',
            f'<td data-v="{ref.rating}">{ref.rating if ref.rating >= 0 else "—"}</td>',
            f'<td data-v="{1 if ref.picked else 0}">{"✓" if ref.picked else ""}</td>',
            f'<td data-v="{sharp:.0f}">{sharp:.0f}</td>',
            f'<td data-v="{aesth:.0f}">{aesth:.0f}</td>',
            f'<td data-v="{ref.iso or 0}">{_esc(ref.iso if ref.iso else "—")}</td>',
            f'<td>{_esc(ref.shutter or "—")}</td>',
            f'<td>{_esc(ref.aperture or "—")}</td>',
            f'<td data-v="{ref.focal_35mm or 0}">{_esc(ref.focal_35mm if ref.focal_35mm else "—")}</td>',
            f'<td>{_esc(ref.captured_at or "—")}</td>',
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")
    th = "".join(f"<th>{_esc(h)}</th>" for h in heads)
    return (f'<section class="sec wrap"><details><summary>'
            f'<h2 style="display:inline">{_esc(title)}</h2></summary>'
            f'<div class="tablewrap"><table id="detail"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details></section>')


def build_html(data: ReportData, encoded: Dict[str, str], *,
               is_zh: bool = True, app_version: str = "",
               generated_at: str = "", with_detail_thumbs: bool = True) -> str:
    """
    渲染完整的自包含 HTML 报告。

    参数:
        data (ReportData): aggregate() 的输出。
        encoded (Dict[str, str]): job_id → data URI。缺失项渲染为占位块，
            不中断渲染（spec 7.2）。
        is_zh (bool): 中文界面为 True，跟随导出时的界面语言（spec D7）。
        app_version (str): 写进页脚的版本号。
        generated_at (str): 写进页脚的生成时间。
        with_detail_thumbs (bool): 明细表是否带缩略图列。照片总数 > 600 时
            由调用方传 False（spec 6.4）。

    返回:
        str: 完整 HTML 文档字符串，可直接以 UTF-8 写入 .html 文件。

    Render the complete self-contained HTML report.
    """
    reg = _ImageRegistry(encoded)
    # 顺序要紧：各区块在渲染时才向 reg 注册图片，故 reg.script() 必须最后调用。
    # Order matters: blocks register images as they render, so script() comes last.
    body = (_cover_html(data, reg, is_zh)
            + _species_html(data, reg, is_zh)
            + _stats_html(data, is_zh)
            + _detail_html(data, reg, is_zh, with_detail_thumbs))
    title = _esc(data.dir_name) or "SuperPicky"
    by = (f"由 SuperPicky {_esc(app_version)} 生成" if is_zh
          else f"Generated by SuperPicky {_esc(app_version)}")
    return f"""<!DOCTYPE html>
<html lang="{'zh-Hans' if is_zh else 'en'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{_CSS_BASE}</style>
</head>
<body>
{body}
<div id="lb"><img alt=""></div>
<footer>{by} · {_esc(generated_at)}<br>https://superpicky.app</footer>
<script>{reg.script()}</script>
<script>{_JS_LAZY}</script>
<script>{_JS_LIGHTBOX}</script>
<script>{_JS_SORT}</script>
</body>
</html>"""
