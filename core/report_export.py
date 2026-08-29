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
import re
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
    # 鸟种颜值（iRateBird 指数 0-100），是**鸟种级**属性而非单张照片的，
    # 故挂在这里而不是 PhotoRef 上。无数据为 None。
    # Species-level beauty index (0-100); not a per-photo property.
    beauty: Optional[float] = None


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


def _dedupe_bursts(rows: List[dict]) -> List[dict]:
    """
    同一连拍组只保留一张，用于挑选鸟种画廊要展示的照片。

    参数:
        rows (List[dict]): 已按展示优先级**降序排好**的照片记录。

    返回:
        List[dict]: 每个 burst_id 只剩一条（保留输入顺序里最靠前的那条）；
        burst_id 为空的记录各自独立，全部保留。

    不去重时，一个鸟种的前 4 张极可能来自同一次连拍——画面几乎一模一样，
    版面上占了四格却只讲了一件事。入参必须是排好序的，这样每组留下的就是
    该组质量最高的一张，而不是快门顺序上的第一张。

    Keep one photo per burst so a species' gallery doesn't show four nearly
    identical frames. Input must already be sorted by display priority, so the
    survivor of each burst is its best frame rather than its first shutter.
    """
    seen: set = set()
    out: List[dict] = []
    for row in rows:
        burst_id = row.get("burst_id")
        if burst_id is not None:
            if burst_id in seen:
                continue
            seen.add(burst_id)
        out.append(row)
    return out


def format_shutter(value: object) -> str:
    """
    把快门速度显示成摄影惯例的写法。

    参数:
        value (object): report.db 的 shutter_speed，通常是秒数（0.0008），
            也可能已经是 "1/1250" 这类字符串。

    返回:
        str: 高速快门写作 `1/1250s`，慢门写作 `1.3s`；无值返回空串。

    数据库存的是秒数，直接印出来就是 `0.0008s`——对摄影师完全不可读，
    没人用小数报快门。1 秒以下取倒数写成分数，1 秒以上保留小数。

    The DB stores raw seconds; printing them verbatim yields "0.0008s", which
    no photographer reads. Sub-second values become 1/N.
    """
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if "/" in text:                 # 已是 1/1250 形式，原样带上单位
        return f"{text}s"
    try:
        seconds = float(text)
    except ValueError:
        return text                 # 无法解析就原样输出，不猜
    if seconds <= 0:
        return text
    if seconds >= 1:
        return f"{seconds:g}s"
    return f"1/{round(1 / seconds):g}s"


def format_minute(value: object) -> str:
    """
    把 EXIF 时间截到分钟，并把日期分隔符规范成连字符。

    参数:
        value (object): EXIF 的 `date_time_original`，形如
            `2026:08:28 08:29:53`。

    返回:
        str: 形如 `2026-08-28 08:29`；无法识别时原样返回。

    EXIF 用冒号分隔日期是它的内部格式，直接呈现给读者会读成时间；秒对
    一次外拍的起止时间也没有意义。

    Trim EXIF timestamps to the minute and normalize the date separators —
    EXIF's colon-separated date is an internal format, and seconds carry no
    meaning for a session's start/end.
    """
    if not value:
        return ""
    text = str(value).strip()
    parts = text.split(" ")
    if len(parts) != 2:
        return text
    date, clock = parts
    date = date.replace(":", "-")
    bits = clock.split(":")
    if len(bits) >= 2:
        clock = f"{bits[0]}:{bits[1]}"
    return f"{date} {clock}"


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
            photos=[_to_ref(r) for r in _dedupe_bursts(ordered)[:per_species]],
            beauty=head.get("aesthetic_index"),
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
    )


# 分档规格（spec 6.2）：(长边, JPEG 质量)
# Size tiers: (max edge, JPEG quality)
IMG_SPECS = {
    "cover": (1800, 82),
    "shot":  (1000, 75),
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


DEFAULT_RATIO = 1.5          # 3:2，探测失败时的回退值 / fallback aspect ratio
RATIO_PROBE_CHARS = 4000     # 只解这么多 base64 字符就够读到 SOF


def probe_jpeg_ratio(data_uri: str) -> Optional[float]:
    """
    从已编码的 JPEG data URI 里读出宽高比，只解码头部若干字节。

    参数:
        data_uri (str): `data:image/jpeg;base64,...`。

    返回:
        Optional[float]: 宽 / 高；无法解析时返回 None（调用方回退
        DEFAULT_RATIO）。

    异常:
        无（不抛异常）——任何畸形输入都返回 None。

    画廊的等高布局需要每张图的宽高比（写进 flex-grow）。与其改
    encode_preview 的签名、连带改动 UI 层的进度上报循环，不如在这里就地
    从已经拿到的 data URI 反查：只 base64 解码前 RATIO_PROBE_CHARS 个字符
    （约 3KB），扫 JPEG 的 SOF marker 读出尺寸。

    这样够用的前提是 encode_preview 用 PIL 保存且**不写 EXIF**，头部只有
    JFIF APP0 与量化表，SOF 稳定落在前 1KB 内。若哪天给保存加上 exif=，
    大块 EXIF 会把 SOF 推到探测窗口之外——那时该调大 RATIO_PROBE_CHARS，
    而不是接受静默回退成 3:2（版面会歪，但不报错，很难察觉）。

    Read an encoded JPEG's aspect ratio by decoding only its first few KB.
    Keeps encode_preview's signature (and the UI progress loop) untouched.
    Valid because encode_preview writes no EXIF, so the SOF marker sits well
    within the probe window — if EXIF is ever added, raise RATIO_PROBE_CHARS
    rather than accept the silent 3:2 fallback.
    """
    try:
        b64 = data_uri.split(",", 1)[1][:RATIO_PROBE_CHARS]
        head = base64.b64decode(b64[:len(b64) // 4 * 4])
    except Exception:
        return None
    i, n = 2, len(head)
    while i + 9 < n:
        if head[i] != 0xFF:
            i += 1
            continue
        marker = head[i + 1]
        # SOF0/1/2/3：payload 为 [精度 1][高 2][宽 2]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            height = int.from_bytes(head[i + 5:i + 7], "big")
            width = int.from_bytes(head[i + 7:i + 9], "big")
            return (width / height) if height else None
        # 无长度字段的 marker：填充 FF、SOI/EOI、RSTn
        if marker == 0xFF or marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + int.from_bytes(head[i + 2:i + 4], "big")
    return None


def collect_image_jobs(data: ReportData) -> List[ImageJob]:
    """
    列出生成该报告所需的全部图片编码任务。

    与编码本身分离，好让 UI 层逐个调用 encode_preview 以上报进度，
    而本模块保持纯函数、不持有回调（spec 4.1）。

    参数:
        data (ReportData): 聚合结果。
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
        for ref in block.photos:
            _add("shot", ref)
    return list(jobs.values())


# ── HTML 渲染 / HTML rendering ────────────────────────────────────────────────

# 深色单一主题（spec 5.0）：独立 HTML 文件没有宿主主题可跟随，
# 深色底让鸟类羽色与背景虚化显色更好。
#
# 「美术馆」配色原则：这是一份照片作品集，界面必须让位给照片——中性灰阶
# 全部无彩色，**颜色只允许出现在有信息含义的两处**（罕见度徽章、IUCN
# 徽章）。原先的 --accent(#00d4aa 亮青绿) 与 --gold(#ffcc00 纯黄) 是两个
# 高饱和色同屏打架，且会跟画面里的鸟争夺视线，已删除。
# 底色也从偏蓝紫的 #0d0d0f 校正为中性微暖，免得把暖调晨昏光的片子衬发青。
#
# 字体三栈的顺序是要点：**西文字体必须排在中文字体之前**，否则西文与数字
# 会被 PingFang / 雅黑接管（雅黑的西文数字字重不匀）。学名走 --serif 的
# 真 italic——PingFang 没有 italic 字形，浏览器只能做倾斜合成，学名会是
# 「歪的」而不是「斜的」。全部为系统字体：本文件要能离线打开，不可引入
# webfont（内嵌一套中文字体会让文件从 ~4MB 涨到 10MB+）。
#
# Single dark theme: a standalone file has no host theme to follow, and a
# dark ground renders plumage and bokeh better. Gallery principle: the UI is
# achromatic so the photos are the only source of color on the page; hue is
# reserved for the two badges that actually carry information. Latin font
# families MUST precede the CJK ones or Latin text and digits get rendered by
# the CJK face. System fonts only — the file must work offline.
_CSS_BASE = """
:root{--bg:#0f0e0d;--card:#191715;--text:#f2efe9;--muted:#8f8a82;
--line:#2a2724;--iucn-bg:#7f1d1d;--iucn-fg:#fecaca;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Microsoft YaHei",sans-serif;
--serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,
"Songti SC",serif;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:var(--sans);font-size:15px;line-height:1.6}
.wrap{max-width:1100px;margin:0 auto;padding:0 20px}
.ph{background:var(--card);border:1px dashed var(--line);color:var(--muted);
display:flex;align-items:center;justify-content:center;font-size:12px;
min-height:80px;text-align:center;padding:8px;word-break:break-all}
img{display:block;width:100%;height:auto;background:var(--card)}
.cover{position:relative;margin-bottom:32px}
.cover h1{font-size:34px;line-height:1.25;margin:16px 0 4px}
.cover .sub{color:var(--muted);font-size:14px}
.nums{display:flex;gap:32px;margin-top:16px;flex-wrap:wrap}
.nums div{text-align:center}
.nums b{display:block;font-family:var(--serif);font-size:34px;
font-weight:400;line-height:1.2;font-variant-numeric:tabular-nums}
.nums span{font-size:12px;color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;
padding:40px 0;border-top:1px solid var(--line);margin-top:48px}
"""

# 区块样式（spec 5.1 ②③④）：画廊/数据条/明细表/lightbox。
#
# 画廊布局的核心是 .row 这一行「等高零裁切」的 flex：
#   .row>.shot{flex-grow:<该图宽高比>;flex-basis:0}
# flex-basis 为 0 时 gap 已从可用空间里扣除，剩余宽度**严格按 grow 比例**
# 分配，于是每张图的宽度正比于自身宽高比、高度 = 宽 ÷ 比 = 恒等。整排顶
# 底严丝合缝，且不需要 object-fit 裁切、不需要任何 JS。这是 Flickr 式
# justified gallery 的做法。宽高比由 _probe_jpeg_ratio() 从已编码的 JPEG
# 头部读出，写进 inline style。
#   min-width:0 不可省：flex item 默认 min-width:auto，图片的 min-content
#   宽度会阻止收缩，窄视口下比例会失真、整排随即不再等高。
#
# 照片边框用 outline 而不是 inset box-shadow：inset 阴影绘制在背景之上、
# 替换内容之下，会被 <img> 的图像本身完全盖住，什么也看不见。
# outline + outline-offset:-1px 画在元素之上且不占布局空间。
#
# The .row flex is what makes a row of photos equal-height with zero cropping:
# with flex-basis:0 the gap is already deducted, so widths land exactly
# proportional to each image's aspect ratio and every height resolves equal.
# min-width:0 is mandatory (default min-width:auto would block shrinking).
# Borders use outline, not inset box-shadow — an inset shadow paints beneath
# the replaced content and is entirely hidden by the image.
_CSS_BASE += """
/* 左右必须写 auto：.sec 定义在 .wrap 之后，写 `margin:64px 0` 会把
   .wrap 的 `margin:0 auto` 覆盖掉，<section class="sec wrap"> 整块就不再
   居中——版心贴着左边、右边空一大条。
   Must be auto: .sec comes after .wrap, so `margin:64px 0` would override
   `margin:0 auto` and knock every section off-center. */
.sec{margin:64px auto}
.sec h2{font-size:21px;margin:0 0 24px;padding-bottom:10px;
border-bottom:1px solid var(--line);font-weight:600}
.sp{margin-bottom:48px}
.sp .hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.sp .cn{font-size:21px;font-weight:600;letter-spacing:.01em}
.sp .en{font-family:var(--serif);font-size:14px;color:var(--muted);
font-style:italic}
.sp .cnt{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.tier{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid currentColor}
.iucn{font-size:11px;padding:1px 7px;border-radius:10px;
background:var(--iucn-bg);color:var(--iucn-fg);font-weight:600}
.hero{margin:0 auto}
.row{display:flex;gap:8px;margin-top:8px}
.row>.shot{flex-basis:0;min-width:0}
.shot img{outline:1px solid rgba(255,255,255,.07);outline-offset:-1px}
.shot{cursor:zoom-in}
.shot img{transition:filter .2s ease}
.shot:hover img{filter:brightness(1.08)}
.cap{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:5px;
font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;
text-overflow:ellipsis}
.bars div{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px;
font-variant-numeric:tabular-nums}
.bars .bar{height:10px;border-radius:5px}
.kv{display:flex;flex-wrap:wrap;gap:12px 32px;font-size:13px;color:var(--muted)}
.kv b{color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;
align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
#lb img{max-width:94vw;max-height:94vh;width:auto}
"""

_CSS_BASE += """
#pdfbtn{position:fixed;top:16px;right:16px;z-index:50;cursor:pointer;
background:var(--card);color:var(--muted);border:1px solid var(--line);
border-radius:6px;padding:8px 14px;font-size:13px;font-family:inherit;
transition:color .2s ease,border-color .2s ease}
#pdfbtn:hover{border-color:var(--muted);color:var(--text)}
"""

# 打印适配（spec 5.2）。三项缺一则打印结果不可用：白底、分页控制、
# 隐藏交互控件。（原先还有第四项「展开折叠区」，随明细表一并去掉了。）
#
# Print support: white background, page-break control, and hidden interactive
# chrome. Missing any one makes printouts unusable.
#
# 另外一条是深色主题改版后新引入的，缺了打印件就是废纸：
#   `.bars .bar` 的灰阶必须推翻 —— 屏幕上星级条按「越亮=越高星」编码，
#      那套浅灰印在白纸上等于消失。打印时统一压成深灰：条的**长度**已经
#      完整承载了数量信息，亮度编码在白底属于冗余，不必反向重排一遍。
#      条色是内联 style 写死的，故这里必须 !important 才盖得住。
#
# The rule below is specific to the achromatic redesign: the star bars'
# on-screen lightness ramp is invisible on white paper — flattened to one dark grey, since bar
# length already carries the count. Bar colors are inline, hence !important.
_CSS_PRINT = """
@media print{
  :root{--bg:#fff;--card:#fff;--text:#111;--muted:#555;--line:#ccc;
        --iucn-bg:#fee;--iucn-fg:#900}
  body{background:#fff;color:#111}
  .no-print,#lb{display:none !important}
  .sp,.cover,.row,.hero{page-break-inside:avoid;break-inside:avoid}
  img{max-height:none}
  .shot img{outline-color:#ddd}
  .hero{max-width:none !important}
  .cover .hero img,.cover .ph{max-height:12cm;width:auto;margin:0 auto}
  .bars .bar{background:#444 !important}
}
"""

# 视口懒插入（spec 6.1）：滚离视口即释放位图、滚回来再从 SRC 恢复，使常驻
# 位图恒定在几屏之内，与鸟种数无关。
#
# 与最初版本的关键差别：图片**先带着 src 出现在 HTML 里**，这段脚本只是把
# src 收走后接管。原先是反过来的——DOM 里不写 src、全靠脚本填——那样任何
# 不执行 JS 的环境（macOS 快速查看、iOS 文件预览、邮件内置预览）看到的都是
# 一份没有照片的报告。收走 src 前先存进 SRC，卸载与恢复的行为和以前一样。
#
# 也不再做淡入：淡入要靠初始 opacity:0，那等于把「看得见」重新绑回脚本，
# 无 JS 时图片虽有 src 却是透明的——比没有 src 更难排查。
#
# Images ship with their src already set; this script takes them over rather
# than supplying them, so the report still renders without JavaScript. The
# fade-in was dropped: its initial opacity:0 would re-bind visibility to JS.
_JS_LAZY = """
(function(){
  var els=document.querySelectorAll('img[data-lazy]');
  var SRC=[];
  els.forEach(function(el,i){ SRC[i]=el.src; el.dataset.i=i; });
  window.__spSrc=SRC;
  window.__spRestore=function(){
    els.forEach(function(el){ if(!el.getAttribute('src')) el.src=SRC[el.dataset.i]; });
  };
  if(!('IntersectionObserver' in window)) return;   // 老浏览器：保持全部已加载
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      var el=e.target,i=el.dataset.i;
      if(e.isIntersecting){ if(!el.getAttribute('src')&&SRC[i]) el.src=SRC[i]; }
      else { el.removeAttribute('src'); }
    });
  },{rootMargin:'200% 0px'});
  els.forEach(function(el){io.observe(el);});
})();
"""


# 点击放大：直接放大页面上那一张，关闭即释放位图（spec 6.1）。
#
# 不再有「放大专用」的 hd 副本——图片按 1000px 编码，页面上缩着显示、点击时
# 按原尺寸铺开，同一份数据两用。旧版为每张图额外存一份 1200px，占掉整个报告
# 的 74%，而画面与页面上那张完全相同。
#
# Zooms the very image already on the page; no separate hd copy exists. The
# old duplicates cost 74% of the file to show the identical frame.
_JS_LIGHTBOX = """
(function(){
  var lb=document.getElementById('lb'),im=lb.querySelector('img');
  document.querySelectorAll('.shot').forEach(function(el){
    el.addEventListener('click',function(){
      var img=el.querySelector('img'); if(!img)return;
      // 能被点到的图必然在视口内、必然带着 src；SRC 只是滚动边界上的兜底
      var src=img.getAttribute('src')||(window.__spSrc||[])[img.dataset.i];
      if(!src)return;
      im.src=src; lb.style.display='flex';
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

# 打印前必须把懒插入的图片全部落位：未进过视口的 <img> 没有 src，直接打印
# 就是大片空白——而且版面高度是对的，比「图没加载」更难察觉。
#
# 三条触发路径都覆盖：按钮（直接调用，留 300ms 给浏览器解码）、beforeprint
# 事件、以及 matchMedia('print')——Safari 长期不支持 beforeprint/afterprint。
#
# Before printing, materialize every lazy image (no src = blank page).
# Covered via the button, beforeprint, and matchMedia for Safari.
_JS_PRINT = """
(function(){
  function materialize(){
    // 把滚离视口时卸掉的 src 全部恢复；未卸载过的本来就带着 src
    if(window.__spRestore) window.__spRestore();
  }
  window.addEventListener('beforeprint',materialize);
  if(window.matchMedia){
    var mq=window.matchMedia('print');
    var onchange=function(e){ if(e.matches) materialize(); };
    if(mq.addEventListener) mq.addEventListener('change',onchange);
    else if(mq.addListener) mq.addListener(onchange);
  }
  var btn=document.getElementById('pdfbtn'); if(!btn)return;
  btn.addEventListener('click',function(){
    materialize();
    setTimeout(function(){window.print();},300);
  });
})();
"""


def _esc(value: object) -> str:
    """HTML 转义。鸟种名/文件名/caption 均为外部输入，必须全部过这里。"""
    return _html.escape(str(value if value is not None else ""))


class _ImageRegistry:
    """
    把编码好的 data URI 发放给 DOM，并单独收集 lightbox 用的 hd 图。

    图片**直接写进 <img src>**，不经过任何数组。这一条是刻意的：图片一旦只
    存在于 JS 数组里，任何不执行脚本的查看环境就是一片空白——macOS 的快速
    查看（Finder 里按空格）、iOS「文件」App 的预览、邮件客户端与部分 IM 的
    内置预览都不跑 JS。一份用来分享的报告，对方按空格看到空白只会以为文件
    传坏了。

    也不再有第二份「放大专用」的图：每张照片只编码一次，页面上缩着显示、
    点击时按原尺寸放大，同一份数据两用。曾经那份 1200px 的 hd 副本占掉整个
    报告的 74%，而它与页面上那张是同一个画面。

    Images carry their data URI directly in `src`, so the report renders
    without JavaScript. There is no separate "zoom" copy either — each photo
    is encoded once and serves both roles; the old hd duplicates accounted for
    74% of the file while showing the very same frame.
    """

    def __init__(self, encoded: Dict[str, str]) -> None:
        self._encoded = encoded
        self._ratios: Dict[str, float] = {}

    def ratio(self, job_id: str) -> float:
        """
        返回该 job 图片的宽高比，供画廊的等高 flex 布局使用。

        参数:
            job_id (str): 与 collect_image_jobs 同构的 `f"{kind}:{path}"`。

        返回:
            float: 宽 / 高；图片缺失或头部无法解析时返回 DEFAULT_RATIO。

        结果按 job_id 缓存：同一张图会被 _photo_cell 与 _species_html 各查
        一次，重复解 base64 没有意义。
        """
        if job_id not in self._ratios:
            uri = self._encoded.get(job_id)
            self._ratios[job_id] = (probe_jpeg_ratio(uri) if uri else None) or DEFAULT_RATIO
        return self._ratios[job_id]

    def tag(self, job_id: str, alt: str, css_class: str = "") -> str:
        """
        返回一个 data URI 直挂在 src 上的 <img>；job_id 无对应图时返回占位块。

        参数:
            job_id (str): 与 collect_image_jobs 同构的 `f"{kind}:{path}"`。
            alt (str): 替代文本（用文件名）。
            css_class (str): 追加的样式类。

        返回:
            str: `<img data-lazy src="data:...">`，或缺图时的占位块。

        job_id 必须与 collect_image_jobs 产出的一致，对不上会静默退化成占位
        块——报告看着生成成功、实际一张图都没有。

        `data-lazy` 只是给 JS 认领的标记：脚本跑得起来时，_JS_LAZY 会把这些
        src 收走交给 IntersectionObserver 管理（滚出视口即卸载）；跑不起来
        时它就是个无副作用的属性，图片照常显示。

        The data URI rides directly on `src`. `data-lazy` merely marks the node
        for the observer to adopt when scripts run; without them it is inert
        and the image still shows.
        """
        uri = self._encoded.get(job_id)
        if not uri:
            return f'<div class="ph {css_class}">{_esc(alt)}</div>'
        cls = f' class="{css_class}"' if css_class else ""
        # decoding="async" 让屏幕外的图不阻塞首屏；无 JS 环境下这是唯一还能
        # 起作用的减负手段（IntersectionObserver 那套此时并不运行）。
        # decoding="async" keeps offscreen images off the critical path — the
        # only mitigation still active when scripts don't run.
        return (f'<img data-lazy decoding="async" src="{uri}" '
                f'alt="{_esc(alt)}"{cls}>')

def _cover_html(data: ReportData, reg: _ImageRegistry, is_zh: bool) -> str:
    """封面：满幅大图 + 标题 + 地点 + 三个大数字（spec 5.1 ①）。"""
    lab = ("总张数", "鸟种", "精选") if is_zh else ("Photos", "Species", "Picked")
    # 键用 path 不用 filename：必须与 collect_image_jobs 的 job_id 同构，
    # 且合并报告里不同子目录可能有同名文件，用文件名会互相顶替。
    # Key by path (not filename) to match collect_image_jobs and to keep
    # same-named files from different sub-dirs apart in merged reports.
    # 封面是全库 adj_topiq 最高的一张，旧版用 object-fit:cover + max-height:70vh
    # 把它拦腰裁了——最该完整呈现的一张反倒是全页唯一被裁的。现在不裁，但
    # **按方向分流**，不能照搬鸟种代表作那套限高：
    #
    #   横构图 / 方构图 → 满宽出血。封面的职责是第一眼的冲击力，套上
    #     `max-width = 比 × vh` 后，900px 高的窗口里 3:2 封面只有 1026px 宽，
    #     两侧留出黑边，看着像图没加载完，封面就白当了。满宽时高度 = 宽 ÷ 比，
    #     3:2 在常见宽度下约 0.55–0.67 屏，本来就不会过高。
    #   竖构图 → 限高居中。竖图满宽会撑到近两屏高（1280 宽的 2:3 是 1920px），
    #     滚半天出不了封面，这时收窄居中才是对的。
    #
    # The cover used to be the only cropped image on the page. It now keeps its
    # aspect ratio, but splits by orientation: landscape bleeds full-width (a
    # vh-capped cover would sit in a letterbox and read as a loading failure),
    # while portrait is capped and centered (full-width would run ~2 screens).
    img = ""
    if data.cover:
        job = f"cover:{data.cover.path}"
        cell = reg.tag(job, data.cover.filename)
        ratio = reg.ratio(job)
        style = ("" if ratio >= 1.0
                 else f' style="max-width:calc({ratio:.4f} * {COVER_PORTRAIT_MAX_VH}vh)"')
        img = f'<div class="hero"{style}>{cell}</div>'
    # 起止时间截到分钟。同一天时只写一次日期（外拍绝大多数是当天往返，
    # 「2026-08-28 08:29 – 2026-08-28 11:51」把同一个日期报两遍纯属噪音）。
    # Trim to the minute; when both ends fall on the same day, print the date
    # once — repeating it is pure noise for a single-day outing.
    when = ""
    if data.shot_start:
        start, end = format_minute(data.shot_start), format_minute(data.shot_end)
        when = _esc(start)
        if end and end != start:
            same_day = start[:10] == end[:10]
            when += " – " + _esc(end[11:] if same_day else end)
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


def _pick_bits(ref: PhotoRef, beauty: Optional[float], is_zh: bool) -> List[str]:
    """
    代表作的选鸟参数：锐度、美学、颜值。

    参数:
        ref (PhotoRef): 代表作。
        beauty (Optional[float]): 该鸟种的颜值指数（0-100），无数据传 None。
        is_zh (bool): 中文界面为 True。

    返回:
        List[str]: 已格式化的短语，缺数据的项不出现。

    锐度与美学是**这张照片**的评分（adj_sharpness / adj_topiq），颜值是
    **这个鸟种**的 iRateBird 指数。只挂在代表作上：副图各带一串数字会盖过画面。

    小数位数按各自的实际量程定，不能统一：
      锐度 0–800 上下 → 取整，小数位没有意义；
      美学 3–6.5 之间 → **必须留一位小数**，取整会把整批压成清一色的 5 和 6，
        等于没显示。这个尺度也正是用户在技能等级里看到的阈值口径
        （core/skill_presets.py 的 4.5 / 4.8 / 5.5），两处对得上才不会打架；
      颜值 0–100 → 取整。

    Decimals follow each metric's actual range: sharpness spans ~0-800 and
    rounds to an integer, aesthetics only spans ~3-6.5 and MUST keep one
    decimal (rounding flattens a whole batch to 5s and 6s) — matching the
    threshold scale users already see in the skill-level presets.
    """
    labels = ("锐度", "美学", "颜值") if is_zh else ("Sharp", "Aesth", "Beauty")
    out: List[str] = []
    if ref.sharpness:
        out.append(f"{labels[0]} {ref.sharpness:.0f}")
    if ref.aesthetic:
        out.append(f"{labels[1]} {ref.aesthetic:.1f}")
    if beauty:
        out.append(f"{labels[2]} {beauty:.0f}")
    return out


def _photo_cell(ref: PhotoRef, reg: "_ImageRegistry",
                grow: bool = False, extra: Optional[List[str]] = None) -> str:
    """
    一张展示图 + 参数小字；整块可点击，点击后 lightbox 取 hd 版本。

    参数:
        ref (PhotoRef): 照片。
        reg (_ImageRegistry): 图片注册表。
        grow (bool): 是否作为 .row 的 flex 成员输出。为 True 时写入
            `flex-grow:<宽高比>`——一排里每张图的宽度按此比例分配，高度
            于是自动相等（见 _CSS_BASE 中 .row 的说明）。
        extra (Optional[List[str]]): 追加在曝光组合之后的短语（代表作用来
            挂锐度/美学/颜值），为空则只有曝光组合。

    返回:
        str: 一个 `<div class="shot">` 片段（整块可点击放大）。

    Render one shown photo with its EXIF caption; clicking opens the hd
    variant in the lightbox. With grow=True the cell carries the flex-grow
    that makes a row equal-height without cropping.
    """
    job = f"shot:{ref.path}"
    img = reg.tag(job, ref.filename)
    style = f' style="flex-grow:{reg.ratio(job):.4f}"' if grow else ""
    bits = []
    shutter = format_shutter(ref.shutter)
    if shutter:
        bits.append(shutter)
    if ref.aperture:
        bits.append(f"f/{ref.aperture}")
    if ref.iso:
        bits.append(f"ISO {ref.iso}")
    if ref.focal_35mm:
        bits.append(f"{ref.focal_35mm}mm")
    bits += extra or []
    cap = f'<div class="cap">{_esc(" · ".join(bits))}</div>' if bits else ""
    return f'<div class="shot"{style}>{img}{cap}</div>'


# 满幅代表作的最大高度（视窗高百分比）。
#
# 这个数是权衡出来的，不能随手调小：代表作限宽后是居中的，而它下面那排副图
# 始终满版心宽——一旦限高让**横构图**代表作窄于版心，就成了「上面一张缩进
# 的图 + 下面一排满宽的图」，读起来像排版失误而不是设计。
# 版心 1060、3:2 横图要满宽需要 1.5 × H × vh ≥ 1060，取 85 时窗口内容高
# ≥832px 即满宽，覆盖绝大多数窗口；而竖构图仍被有效限制（2:3 若不限高，
# 满宽 1060 会撑到 1590px，一屏放不下一张）。
#
# Tuned, not arbitrary: the hero is centered while the row below it always
# spans the full column, so a capped *landscape* hero reads as a mistake.
# 85 keeps 3:2 heroes full-width for any viewport ≥832px tall, while still
# reining in portrait shots (unbounded, a 2:3 hero would run 1590px tall).
HERO_MAX_VH = 85

# 竖构图封面的高度上限（视窗高百分比）。横构图封面不限高，见 _cover_html。
# Height cap for portrait covers only; landscape covers bleed. See _cover_html.
COVER_PORTRAIT_MAX_VH = 80

# 罕见度徽章在深色底上的替换色 / Rarity badge colors remapped for dark ground.
#
# core.rarity_tier 的配色是给 GUI（浅色底）定的，其中「少见及以上」的
# #D81E05 压在本报告的 #0f0e0d 底上对比度只有约 4.3:1，低于 WCAG AA 的
# 4.5。界面改成无彩色之后这两个徽章是整页仅存的颜色，读不清就失去意义，
# 故在报告端提亮到约 6:1。
#
# 不改 core/rarity_tier.py：那份配色由 GUI 共用，为了一个深色底的导出产物
# 去动它会连带改变主窗口的观感。
#
# The shared GUI palette is tuned for a light ground; #D81E05 only reaches
# ~4.3:1 on this report's dark background. These badges are the page's only
# color, so they are brightened here to ~6:1 — without touching the shared
# module, which the light-themed GUI also renders from.
_TIER_COLOR_ON_DARK = {
    "#FC7F3F": "#FC7F3F",   # 能见（橙）：约 6.5:1，本就达标，原样保留
    "#D81E05": "#FF4A32",   # 少见及以上（红）：4.3:1 → 约 6.1:1
}


def _tier_color_on_dark(color: Optional[str]) -> str:
    """
    把 GUI 的罕见度色映射成深色底可读的版本。

    参数:
        color (Optional[str]): tier_name_color() 的返回值，None 表示常见/无数据。

    返回:
        str: 深色底上使用的颜色；常见/无数据回落到 --muted 的取值。
    """
    if not color:
        return "#8f8a82"
    return _TIER_COLOR_ON_DARK.get(color.upper(), color)


def _hero_html(ref: PhotoRef, reg: "_ImageRegistry",
               extra: Optional[List[str]] = None) -> str:
    """
    满幅代表作：横向占满版心，但高度不超过 HERO_MAX_VH，且**不裁切**。

    参数:
        ref (PhotoRef): 代表作。
        reg (_ImageRegistry): 图片注册表。

    返回:
        str: 居中的 `<div class="hero">` 片段。

    限高只能加在容器的 max-width 上，不能直接给 <img> 设 max-height：
    图片是 width:100%;height:auto，给它 max-height 会让宽高脱钩而变形，
    要么就得 object-fit:cover 裁掉——两者都违背「零裁切」这条原则。
    改成 `max-width: 宽高比 × 62vh` 后，高度天然被限制在 62vh，图片始终
    保持原比例，横构图与竖构图都成立（竖构图会自动变窄并居中）。

    Cap the hero's height via the container's max-width, never via the image's
    max-height (which would distort it) nor object-fit (which would crop it).
    max-width = ratio × 62vh bounds the height while preserving the aspect
    ratio, and works for portrait shots too — they simply become narrower.
    """
    ratio = reg.ratio(f"shot:{ref.path}")
    cell = _photo_cell(ref, reg, extra=extra)
    return f'<div class="hero" style="max-width:calc({ratio:.4f} * {HERO_MAX_VH}vh)">{cell}</div>'


def _gallery_html(refs: List[PhotoRef], reg: "_ImageRegistry",
                  extra: Optional[List[str]] = None) -> str:
    """
    按张数自适应的版式：1 张满幅 / 2 张对开 / 3 张=满幅+2 / 4 张=满幅+3。

    参数:
        refs (List[PhotoRef]): 该鸟种要展示的照片，首张为代表作。
        reg (_ImageRegistry): 图片注册表。
        extra (Optional[List[str]]): 只挂到代表作上的选鸟参数（锐度/美学/
            颜值）。2 张对开时虽无满幅位，首张仍是代表作，参数照挂。

    返回:
        str: 该鸟种的图片区 HTML。

    为什么不是所有张数都「一大 + 其余一排」：只有 2 张时若排成满幅 + 下方
    一张同宽的图，两张一样大地上下堆着，读起来是重复而不是主次，所以 2 张
    走左右对开、不设代表作。3 张和 4 张则代表作分量足够，用满幅 + 一排。

    每一排都是等高零裁切的 flex（见 _CSS_BASE 里 .row 的说明），所以「不足
    一排」的情况不会留下空洞——两张就把整排均分掉，没有第三个空格子。这正
    是旧版 `grid-template-rows:repeat(3,1fr)` 做不到的：行数写死为 3，只有
    2 张时右列必然空出一格。

    Layout adapts to the photo count. Two photos are shown side by side rather
    than hero-plus-one, which would read as repetition instead of hierarchy.
    Every row is an equal-height zero-crop flex, so a short row simply fills
    the width — unlike the old fixed three-row grid, which left empty cells.
    """
    if not refs:
        return ""
    if len(refs) == 2:
        cells = "".join(_photo_cell(r, reg, grow=True,
                                    extra=extra if i == 0 else None)
                        for i, r in enumerate(refs))
        return f'<div class="row">{cells}</div>'
    out = _hero_html(refs[0], reg, extra=extra)
    if len(refs) > 1:
        rest = "".join(_photo_cell(r, reg, grow=True) for r in refs[1:])
        out += f'<div class="row">{rest}</div>'
    return out


def _species_html(data: ReportData, reg: "_ImageRegistry", is_zh: bool) -> str:
    """
    鸟种画廊：每种一块，区块按罕见度降序，块内版式随张数自适应（spec 5.1 ②）。

    鸟种数不封顶——常驻位图已由懒插入与内容量脱钩（spec D8 / 6.4）。

    Species gallery: one block per species, ordered by rarity descending, with
    the in-block layout adapting to the photo count. Species count never capped.
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
            color = _tier_color_on_dark(tier_name_color(block.tier))
            badges += (f'<span class="tier" style="color:{_esc(color)}">'
                       f'{_esc(tier_name(block.tier, is_zh))}</span>')
        if block.iucn in IUCN_BADGE_SHOWN:
            badges += f'<span class="iucn">{_esc(block.iucn)}</span>'
        # 选鸟参数只算一次，且只喂给代表作（_gallery_html 内部决定挂哪一张）
        pick_bits = (_pick_bits(block.photos[0], block.beauty, is_zh)
                     if block.photos else None)
        out.append(
            '<div class="sp"><div class="hd">'
            f'<span class="cn">{_esc(primary)}</span>'
            f'<span class="en">{_esc(secondary)}</span>{badges}'
            f'<span class="cnt">{block.count}{unit}</span></div>'
            f'{_gallery_html(block.photos, reg, pick_bits)}</div>'
        )
    out.append("</section>")
    return "".join(out)


# 星级条的灰阶：越高星越亮。界面已无彩色，条形图不能再靠色相区分档位，
# 改用亮度承载「高低」这层语义——亮度递减本身就读作从优到劣，比原先一律
# 涂成同一个纯黄 (#ffcc00) 多传达了一维信息。
#
# 打印时这套浅灰在白纸上会消失，_CSS_PRINT 里统一压成深灰覆盖，见那里的说明。
#
# Star-bar greyscale: brighter = higher rating. With an achromatic UI the bars
# can no longer use hue, so lightness carries the ranking — which reads as
# better-to-worse on its own, unlike the old single flat yellow. Print CSS
# overrides these (light grey is invisible on white paper).
_BAR_SHADE = {
    5: "#f2efe9", 4: "#d8d4cd", 3: "#b8b3ab", 2: "#8f8a82",
    1: "#6b6660", 0: "#4a4640", -1: "#332f2b",
}


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
                    f'<span class="bar" style="width:{width:.0f}px;'
                    f'background:{_BAR_SHADE[rating]}"></span>'
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


def build_html(data: ReportData, encoded: Dict[str, str], *,
               is_zh: bool = True, app_version: str = "",
               generated_at: str = "") -> str:
    """
    渲染完整的自包含 HTML 报告。

    参数:
        data (ReportData): aggregate() 的输出。
        encoded (Dict[str, str]): job_id → data URI。缺失项渲染为占位块，
            不中断渲染（spec 7.2）。
        is_zh (bool): 中文界面为 True，跟随导出时的界面语言（spec D7）。
        app_version (str): 写进页脚的版本号。
        generated_at (str): 写进页脚的生成时间。

    返回:
        str: 完整 HTML 文档字符串，可直接以 UTF-8 写入 .html 文件。

    Render the complete self-contained HTML report.
    """
    reg = _ImageRegistry(encoded)
    body = (_cover_html(data, reg, is_zh)
            + _species_html(data, reg, is_zh)
            + _stats_html(data, is_zh))
    title = _esc(data.dir_name) or "SuperPicky"
    by = (f"由 SuperPicky {_esc(app_version)} 生成" if is_zh
          else f"Generated by SuperPicky {_esc(app_version)}")
    pdf_label = "存为 PDF" if is_zh else "Save as PDF"
    return f"""<!DOCTYPE html>
<html lang="{'zh-Hans' if is_zh else 'en'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{_CSS_BASE}{_CSS_PRINT}</style>
</head>
<body>
<button id="pdfbtn" class="no-print">{_esc(pdf_label)}</button>
{body}
<div id="lb"><img alt=""></div>
<footer>{by} · {_esc(generated_at)}<br>https://superpicky.app</footer>
<script>{_JS_LAZY}</script>
<script>{_JS_LIGHTBOX}</script>
<script>{_JS_PRINT}</script>
</body>
</html>"""


# ── 导出前预检 / Pre-export estimates ────────────────────────────────────────

# 各档单张编码后的经验均值（字节），用于导出前预估（spec 6.2 / 6.4）。
# Empirical per-image byte averages used for the pre-export size estimate.
_EST_BYTES = {"cover": 400_000, "shot": 95_000}

# base64 编码膨胀系数 / base64 inflation factor.
_BASE64_FACTOR = 1.33

# 提示用户体积偏大的阈值（字节）：常见 IM 与邮件附件上限在 100MB 附近。
# Warn above this size; common IM and email attachment limits sit near 100MB.
SIZE_WARN_BYTES = 80 * 1024 * 1024


def estimate_size(job_count_by_kind: Dict[str, int]) -> int:
    """
    按各档任务数预估最终 HTML 文件字节数。

    参数:
        job_count_by_kind (Dict[str, int]): 档位名 → 该档任务数。

    返回:
        int: 预估字节数（已计入 base64 膨胀）。

    Estimate the final HTML size in bytes from per-tier job counts.
    """
    raw = sum(_EST_BYTES.get(kind, 0) * count
              for kind, count in job_count_by_kind.items())
    return int(raw * _BASE64_FACTOR)


def build_output_path(directory: str, dir_name: str, is_zh: bool,
                      today: str) -> str:
    """
    构造报告输出路径：目录根、非隐藏目录，文件名跟随界面语言（spec D5）。

    参数:
        directory (str): 选鸟目录绝对路径。
        dir_name (str): 用于文件名的目录显示名。
        is_zh (bool): 中文界面为 True。
        today (str): YYYY-MM-DD。

    返回:
        str: 不与现有文件冲突的绝对路径（冲突时依次加 _2、_3…）。

    Build the report output path in the picking directory's root. The filename
    follows the UI language; collisions get a numeric suffix instead of
    overwriting an existing report.
    """
    # 目录名可能含路径分隔符或非法字符，须净化，否则会穿出目标目录。
    # Sanitize: a name containing separators must not escape the target dir.
    safe = re.sub(r'[\\/:*?"<>|]', "_", dir_name).strip() or "report"
    stem = (f"SuperPicky报告_{safe}_{today}" if is_zh
            else f"SuperPicky-Report-{safe}-{today}")
    candidate = os.path.join(directory, stem + ".html")
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{index}.html")
        index += 1
    return candidate


def write_report_atomically(path: str, html: str) -> None:
    """
    原子写入报告文件：先写 .tmp，成功后 os.replace 重命名。

    导出耗时十几秒，中途取消或异常若直接写目标文件会留下半截 HTML，
    用户双击打开就是残页（spec 7.2）。

    参数:
        path (str): 目标绝对路径。
        html (str): 完整 HTML 文本。

    异常:
        OSError: 磁盘空间不足、目录只读等；此时不会留下任何残留文件。

    Write the report atomically: temp file first, then os.replace. Prevents a
    half-written HTML from ever appearing at the target path.
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(html)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
