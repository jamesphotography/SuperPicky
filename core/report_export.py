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

import os
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.rarity_tier import gbif_score_to_tier

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


def _to_ref(row: dict) -> PhotoRef:
    """
    把一条 report.db 记录转成 PhotoRef（basename 化文件名）。

    Convert a report.db row to PhotoRef, extracting only the basename for filename.
    """
    raw_name = row.get("filename") or ""
    path = row.get("current_path") or row.get("original_path") or ""
    return PhotoRef(
        filename=os.path.basename(raw_name) or os.path.basename(path),
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
    by_rating = {r: 0 for r in (-1, 0, 1, 2, 3)}
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
