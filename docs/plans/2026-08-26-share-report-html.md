# 可分享总结报告 HTML 实施计划 / Shareable Summary Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在选鸟浏览器加一个「导出报告」入口，把 `report.db` 聚合成一个自包含、可直接分享的 HTML 文件，写入选鸟目录。

**Architecture:** 三层。`core/report_export.py` 是无 Qt 依赖的纯函数层（聚合 → 图片编码 → HTML 字符串），可用字典直接单测；`ui/report_export_dialog.py` 负责选项收集与预检；`ui/results_browser_window.py` 只加一个按钮和一个 `QThread`。图片全部以 base64 存入 JS 字符串数组，由 `IntersectionObserver` 按视口插入 `<img>`，使常驻位图与内容量脱钩。

**Tech Stack:** Python 3.12 / Pillow（已有依赖）/ PySide6（仅 UI 层）/ pytest 9.x。**不新增任何第三方依赖。**

**Spec:** `docs/specs/2026-08-26-share-report-html-design.md`

## Global Constraints

以下为全项目约束，**每个 Task 的要求都隐含包含本节**：

- **UTF-8 强制**：所有文件读写 `encoding='utf-8'`，HTML 内 `<meta charset="utf-8">`。不得用 shell 脚本（sed/awk）处理含中文的文件。
- **跨平台**：路径一律 `os.path` / `pathlib`，不硬编码分隔符。Windows 与 macOS 双端可用。
- **`core/report_export.py` 绝对不 import 任何 Qt 模块**——这是本设计可测性的基础（spec 4.1）。违反即计划失败。
- **不新增第三方依赖**：只用 Pillow（`requirements_base.txt` 已有 `Pillow>=9.0.0`）与标准库。不引入 Jinja2、reportlab、weasyprint。
- **Pillow 版本下限 9.0.0**：只用 9.0 即存在的 API。`Image.LANCZOS` 可用（Pillow 10 移除的是 `ANTIALIAS`，非 `LANCZOS`）。
- **注释规范**：UTF-8 中文注释 + 同格式英文注释；函数/类用 docstring 写明功能、参数、返回值、异常。
- **类型注解**：所有函数入参与返回值标注类型。
- **测试隔离**：测试**不得**构造任何 Qt 对象，**不得**读写用户真实 `advanced_config.json`。涉及 i18n 断言的测试须钉住 locale（见 `test_species_merge.py:25` 的 `_pin_chinese_locale` fixture 写法）。
- **测试文件被 `.gitignore` 忽略**：提交 `test_report_export.py` 必须用 `git add -f`。
- **最低验证**：改动的 Python 文件跑 `python3 -m py_compile <file>`。
- **报告内绝不出现绝对路径**（spec D6）：只显示 `os.path.basename()`。
- **报告语言跟随导出时界面语言**（spec D7），文件名同（spec D5）。

---

## File Structure

| 文件 | 职责 | Task |
|---|---|---|
| `core/report_export.py`（新建） | 纯函数层：数据模型、`aggregate()`、`collect_image_jobs()`、`encode_preview()`、`build_html()` | 1–5 |
| `ui/report_export_dialog.py`（新建） | 导出对话框：GPS 勾选、预检结果展示、体积/耗时预估 | 6 |
| `ui/results_browser_window.py`（修改） | 工具栏按钮 + `QThread` 接线 + 进度 + 打开文件 | 7 |
| `locales/zh_CN.json`、`locales/en_US.json`（修改） | 新增 `report_export.*` 段 | 8 |
| `test_report_export.py`（新建，根目录） | 纯函数层 15 条用例 | 1–5 |
| `test_report_export_entry.py`（新建，根目录） | UI 接线（按钮存在、信号连接） | 7 |

**为什么把 `build_html()` 拆到三个 Task（3/4/5）**：spec 的 T3 是一整块「模板 + CSS/JS」，太大，一个 reviewer 无法有意义地部分否决。拆成「骨架与懒插入机制」「内容区块」「打印适配」三段后，每段都有独立可断言的交付物。

---

## Task 1: 数据模型与 aggregate()

**Files:**
- Create: `core/report_export.py`
- Create: `test_report_export.py`

**Interfaces:**
- Consumes: `core.rarity_tier.gbif_score_to_tier`（已存在，`core/rarity_tier.py:125`）
- Produces: 下列 dataclass 与 `aggregate()`，Task 2–7 全部依赖它们的字段名与签名。

```python
@dataclass(frozen=True)
class PhotoRef:
    filename: str            # 仅文件名，绝不含目录（spec D6）
    path: str                # 绝对路径，仅供编码用，不进 HTML
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
    name_cn: str
    name_en: str
    count: int               # 该鸟种总张数（非展示张数）
    tier: Optional[int]      # 0..4，gbif_score_to_tier 的输出
    iucn: Optional[str]
    photos: List[PhotoRef]   # 至多 per_species 张，首张为代表作

@dataclass(frozen=True)
class GearStats:
    cameras: List[Tuple[str, int]]
    lenses: List[Tuple[str, int]]
    top_focal: Optional[int]
    iso_min: Optional[int]
    iso_max: Optional[int]

@dataclass(frozen=True)
class ReportData:
    dir_name: str
    total: int
    by_rating: Dict[int, int]      # 键 -1..5（4/5 为浏览器内手动升星）
    picked: int
    flying: int
    focus_precise: int
    bird_total: int
    species: List[SpeciesBlock]    # 罕见度降序
    cover: Optional[PhotoRef]
    shot_start: Optional[str]
    shot_end: Optional[str]
    location: str                  # 城市级，可能为 ""
    gps: Optional[Tuple[float, float, Optional[float]]]
    gear: GearStats
    burst_groups: int
    burst_avg: float
    detail: List[PhotoRef]

def aggregate(photos: List[dict], *, include_gps: bool = False,
              per_species: int = 4) -> ReportData: ...
```

- [ ] **Step 1: 写失败测试（先写 5 条覆盖 spec 用例 1–5）**

在项目根新建 `test_report_export.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.report_export'`

- [ ] **Step 3: 实现 core/report_export.py 的数据模型与 aggregate()**

```python
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

    注意 path 是「**可解码的预览路径**」而非原始文件路径：PIL 没有 RAW
    解码器（本机实测 .nef/.cr2/.arw 的 registered_extensions() 均为 None），
    直接把 current_path 交给 encode_preview 会让每张 RAW 都编码失败、
    报告全是占位块。故优先取 preview_candidates() 的首个候选。
    """
    raw_name = row.get("filename") or ""
    # preview_candidates 在 Task 2 定义；模块级函数运行时才解析名字，顺序无妨。
    candidates = preview_candidates(row)
    path = (candidates[0] if candidates
            else (row.get("current_path") or row.get("original_path") or ""))
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
    """取 city/state/country 的众数，拼成城市级地点字符串（spec D3）。"""
    parts = []
    for key in ("city", "state_province", "country"):
        vals = [r.get(key) for r in rows if r.get(key)]
        if vals:
            parts.append(Counter(vals).most_common(1)[0][0])
    return " · ".join(parts)


def _gear_stats(rows: List[dict]) -> GearStats:
    """统计机身/镜头分布、最常用等效焦距、ISO 区间。"""
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 6 passed

- [ ] **Step 5: py_compile 校验**

Run: `python3 -m py_compile core/report_export.py`
Expected: 无输出（成功）

- [ ] **Step 6: 提交**

```bash
git add core/report_export.py
git add -f test_report_export.py
git commit -m "feat(report): 报告数据模型与聚合层

aggregate() 把 report.db 记录聚合成 ReportData：鸟种按罕见度降序分块、
每种至多 4 张（picked > rating > topiq）、封面取全库美学最高。
未勾选 GPS 时坐标在聚合层即丢弃，不是渲染层隐藏。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: 图片编码 encode_preview() 与任务清单 collect_image_jobs()

**Files:**
- Modify: `core/report_export.py`
- Test: `test_report_export.py`

**Interfaces:**
- Consumes: Task 1 的 `ReportData`、`PhotoRef`
- Produces:

```python
@dataclass(frozen=True)
class ImageJob:
    job_id: str      # "cover:<filename>" / "rep:.." / "small:.." / "hd:.." / "thumb:.."
    path: str
    max_edge: int
    quality: int

def collect_image_jobs(data: ReportData, *, with_detail_thumbs: bool = True) -> List[ImageJob]: ...
def encode_preview(path: str, max_edge: int, quality: int) -> Optional[str]: ...
def preview_candidates(row: dict) -> List[str]: ...
def preview_availability(photos: List[dict]) -> Tuple[int, int]: ...
```

`encode_preview` 失败返回 `None`（不抛异常），由调用方渲染占位块——spec 7.2「绝不让一张坏数据毁掉整份报告」。

**为什么把 job 清单和编码分开**：编码是耗时步骤，需要在 UI 层逐个调用以上报进度；生成器保持纯函数、不持有回调。`build_html()` 接收 `job_id -> data URI` 的字典。

- [ ] **Step 1: 写失败测试**

追加到 `test_report_export.py`（顶部 import 增补 `collect_image_jobs, encode_preview, preview_availability, IMG_SPECS`）：

```python
from PIL import Image


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
    photos = [_photo(filename=f"a{i}.NEF", adj_topiq=float(i)) for i in range(6)]
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k "encode or jobs or availability"`
Expected: FAIL — `ImportError: cannot import name 'collect_image_jobs'`

- [ ] **Step 3: 实现**

在 `core/report_export.py` 顶部 import 区增补：

```python
import base64
import io

from PIL import Image, ImageOps

from tools.file_utils import sibling_jpeg
```

在文件中追加：

```python
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


def preview_candidates(row: dict) -> List[str]:
    """
    按优先级返回可用作预览的路径：temp_jpeg_path → 同名 JPG 边车。

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
    # 复用既有的 sibling_jpeg：它已覆盖 .jpg/.jpeg/.JPG/.JPEG 四种变体，
    # 自行拼后缀会漏掉 .jpeg，导致边车在缩略图网格里可见却在报告里丢失。
    # tools/file_utils.py 无 Qt 依赖，不破坏本模块的纯函数层约束。
    for key in ("current_path", "original_path"):
        _add(sibling_jpeg(row.get(key)))
    return out


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

    Decode, downscale and re-encode one image as a base64 data URI.
    Returns None on any failure so a single bad file cannot abort the report.
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
        # 用 path 而非 filename 做键：filename 按 D6 是 basename-only，
        # 跨子目录的同名文件（多张卡/多次导入的 IMG_0001.NEF，合并模式下同时
        # 入库）会撞键，第二张被静默丢弃并显示成第一张的图。
        # job_id 不进 HTML（tag() 渲染的是 IMGS 下标），故用绝对路径安全。
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 13 passed

- [ ] **Step 5: py_compile 校验**

Run: `python3 -m py_compile core/report_export.py`

- [ ] **Step 6: 提交**

```bash
git add core/report_export.py
git add -f test_report_export.py
git commit -m "feat(report): 图片编码与任务清单

encode_preview 用 Pillow draft() 走 libjpeg DCT 缩放解码（无此调用 318 张
要 2-3 分钟），exif_transpose 保证竖拍不躺倒，任何失败返回 None 而非抛异常。
collect_image_jobs 与编码分离，好让 UI 层逐个调用以上报进度。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: HTML 骨架、转义与视口懒插入

**Files:**
- Modify: `core/report_export.py`
- Test: `test_report_export.py`

**Interfaces:**
- Consumes: Task 1 的 `ReportData`、Task 2 的 `IMG_SPECS`
- Produces:

```python
def build_html(data: ReportData, encoded: Dict[str, str], *,
               is_zh: bool = True, app_version: str = "",
               generated_at: str = "") -> str: ...
```

`encoded` 是 `job_id -> data URI` 映射（Task 2 的 `ImageJob.job_id` 为键）。缺失的 job_id 渲染为占位块。

本 Task 只做骨架 + 图片机制 + 封面；鸟种画廊、数据区、明细表在 Task 4；打印样式在 Task 5。

- [ ] **Step 1: 写失败测试**

追加到 `test_report_export.py`（import 增补 `build_html`）：

```python
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


def test_html_escapes_hostile_species_name():
    """用例 6：鸟种名里的标签必须被转义，不能撕烂页面。"""
    data = aggregate([_photo(bird_species_cn="<script>alert(1)</script>",
                             bird_species_en="<img onerror=x>")])
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
    """用例 12：中文写入读回逐字一致，且声明了 charset。"""
    data = aggregate([_photo(bird_species_cn="白腹海雕")])
    html = build_html(data, {})
    assert '<meta charset="utf-8">' in html
    f = tmp_path / "报告_测试.html"
    f.write_text(html, encoding="utf-8")
    assert f.read_text(encoding="utf-8") == html
    assert "白腹海雕" in f.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k "html or gps or escape"`
Expected: FAIL — `ImportError: cannot import name 'build_html'`

- [ ] **Step 3: 实现骨架**

在 `core/report_export.py` 顶部 import 区增补 `import html as _html`、`import json`。追加：

```python
# 深色单一主题（spec 5.0）：独立 HTML 文件没有宿主主题可跟随，
# 深色底让鸟类羽色与背景虚化显色更好。
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


def build_html(data: ReportData, encoded: Dict[str, str], *,
               is_zh: bool = True, app_version: str = "",
               generated_at: str = "") -> str:
    """
    渲染完整的自包含 HTML 报告。

    参数:
        data (ReportData): aggregate() 的输出。
        encoded (Dict[str, str]): job_id → data URI。缺失项渲染为占位块。
        is_zh (bool): 中文界面为 True，跟随导出时的界面语言（spec D7）。
        app_version (str): 写进页脚的版本号。
        generated_at (str): 写进页脚的生成时间。

    返回:
        str: 完整 HTML 文档字符串。

    Render the complete self-contained HTML report. Missing images degrade to
    placeholder blocks rather than aborting the render.
    """
    reg = _ImageRegistry(encoded)
    body = _cover_html(data, reg, is_zh)
    title = _esc(data.dir_name) or "SuperPicky"
    gen = _esc(generated_at)
    ver = _esc(app_version)
    by = f"由 SuperPicky {ver} 生成" if is_zh else f"Generated by SuperPicky {ver}"
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
<footer>{by} · {gen}<br>https://superpicky.app</footer>
<script>{reg.script()}</script>
<script>{_JS_LAZY}</script>
</body>
</html>"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 19 passed

- [ ] **Step 5: py_compile 校验**

Run: `python3 -m py_compile core/report_export.py`

- [ ] **Step 6: 提交**

```bash
git add core/report_export.py
git add -f test_report_export.py
git commit -m "feat(report): HTML 骨架、转义与视口懒插入

图片一律不写 src，全部存 JS 数组由 IntersectionObserver 按视口插入、
滚离即释放——常驻位图恒定几屏，与鸟种数无关，40 种也不会到 178MB。
测试钉死「输出中不存在任何 <img src=\"data:」这条不变量。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: 鸟种画廊、数据区、折叠明细

**Files:**
- Modify: `core/report_export.py`
- Test: `test_report_export.py`

**Interfaces:**
- Consumes: Task 3 的 `_ImageRegistry`、`_esc`、`build_html`
- Produces: `build_html()` 新增关键字参数 `with_detail_thumbs: bool = True`；输出中新增鸟种画廊、数据区、折叠明细三个区块。

- [ ] **Step 1: 写失败测试**

追加到 `test_report_export.py`：

```python
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
    html = build_html(data, {"thumb:a.NEF": "data:image/jpeg;base64,AAA"})
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k "species_blocks or iucn or detail"`
Expected: FAIL — `TypeError: build_html() got an unexpected keyword argument 'with_detail_thumbs'`

- [ ] **Step 3: 追加区块样式**

在 `core/report_export.py` 中 `_CSS_BASE` 定义之后追加：

```python
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
```

- [ ] **Step 4: 追加 lightbox 与排序脚本**

在 `_JS_LAZY` 定义之后追加：

```python
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
```

- [ ] **Step 5: 实现鸟种画廊**

```python
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
```

- [ ] **Step 6: 实现数据区**

```python
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
        if rating >= 4 and count == 0:
            continue          # 手动档没用过就不画空条
        pct = (count / data.total * 100) if data.total else 0.0
        width = count / top * 320
        bars.append(f'<div><span style="width:52px">{labels[rating]}</span>'
                    f'<span class="bar" style="width:{width:.0f}px"></span>'
                    f'<span>{count} ({pct:.1f}%)</span></div>')
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
```

- [ ] **Step 7: 实现折叠明细**

```python
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
```

- [ ] **Step 8: 改写 build_html() 接入三个区块**

用下列版本整体替换 Task 3 写下的 `build_html()`：

```python
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
```

- [ ] **Step 9: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 24 passed

- [ ] **Step 10: 目视检查一次真实输出**

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from core.report_export import aggregate, build_html

rows = [dict(filename=f"a{i}.NEF", current_path=f"/tmp/a{i}.NEF", has_bird=1,
             rating=3 if i % 3 == 0 else 2, picked=1 if i == 0 else 0,
             adj_sharpness=300.0 + i, adj_topiq=50.0 + i,
             bird_species_cn=f"鸟{i % 5}", bird_species_en=f"Bird{i % 5}",
             gbif_rarity_100=float(i % 5) * 20,
             iucn_category="VU" if i % 5 == 0 else "LC",
             iso=640, shutter_speed="1/2000", aperture="5.6",
             focal_length_35mm=600, camera_model="NIKON Z 9",
             lens_model="NIKKOR Z 600mm", city="Cairns",
             state_province="Queensland", country="Australia",
             date_time_original=f"2026-08-26 0{6 + i % 4}:12:00")
        for i in range(20)]
data = aggregate(rows)
html = build_html(data, {}, app_version="4.6.0RC2", generated_at="2026-08-26 14:32")
with open('/tmp/preview_report.html', 'w', encoding='utf-8') as fh:
    fh.write(html)
print("OK -> /tmp/preview_report.html")
EOF
open /tmp/preview_report.html      # macOS；Windows 用 start
```

Expected: 页面可见封面、5 个鸟种块（`鸟4` 罕见度最高排最前、带 VU 徽标）、星级条形图、可折叠可点表头排序的明细表。所有图片位置都是虚线占位块——本步骤未编码任何图片，这是预期结果。

- [ ] **Step 11: 提交**

```bash
git add core/report_export.py
git add -f test_report_export.py
git commit -m "feat(report): 鸟种画廊、数据区与折叠明细

鸟种块按罕见度降序、每种 1 大 3 小、数量不封顶；IUCN 徽标只对 VU 及以上
显示，LC/NT/DD/NE 不显示以免满屏噪音。明细表可点表头排序，数值列写 data-v
让排序按数值而非字符串比较；> 600 张时去掉缩略图列。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: 打印适配与「存为 PDF」按钮

**Files:**
- Modify: `core/report_export.py`
- Test: `test_report_export.py`

**Interfaces:**
- Consumes: Task 4 的 `build_html()`
- Produces: 输出中新增 `@media print` 段、`#pdfbtn` 按钮、`_JS_PRINT` 脚本。无新增公开函数签名。

**背景**（spec D9 / 3.1）：代码级 PDF 需 QtWebEngine，要付 200~300MB 打包体积 + macOS `QtWebEngineProcess` helper 单独签名带 JIT entitlement，故改为 `window.print()`。spec 5.2 规定打印样式必须同时做到四件事，缺一则打印结果不可用。

- [ ] **Step 1: 写失败测试**

追加到 `test_report_export.py`：

```python
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


def test_print_keeps_detail_heading():
    """明细表标题长在 <summary> 里，整块隐藏会印出一张没名字的表。"""
    html = build_html(aggregate([_photo()]), {})
    block = html.split("@media print", 1)[1].split("</style>", 1)[0]
    assert "summary{display:none" not in block.replace(" ", "")
    assert "list-style:none" in block


def test_print_expands_details_via_js_not_css():
    """
    CSS 改不了 <details> 的 open（属性不是样式），打印样式再全，明细区
    在纸上也不存在。必须在打印前用 JS 补 open、打印后恢复；Safari 不支持
    beforeprint/afterprint，故加 matchMedia('print') 兜底。
    """
    html = build_html(aggregate([_photo()]), {})
    assert "beforeprint" in html
    assert "afterprint" in html
    assert "matchMedia" in html
    assert ".open=true" in html
    assert ".open=false" in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k "print or pdf"`
Expected: FAIL — `assert "@media print" in html`

- [ ] **Step 3: 追加按钮样式与打印样式**

在 `core/report_export.py` 中，`_CSS_BASE` 的最后一次 `+=` 之后追加：

```python
_CSS_BASE += """
#pdfbtn{position:fixed;top:16px;right:16px;z-index:50;cursor:pointer;
background:var(--card);color:var(--text);border:1px solid var(--line);
border-radius:6px;padding:8px 14px;font-size:13px;font-family:inherit}
#pdfbtn:hover{border-color:var(--accent);color:var(--accent)}
"""

# 打印适配（spec 5.2）。四项缺一则打印结果不可用：白底、分页控制、
# 展开折叠区、隐藏交互控件。
# Print support: white background, page-break control, expanded <details>,
# and hidden interactive chrome. Missing any one makes printouts unusable.
# 注意：展开折叠区不能靠 CSS——open 是属性不是样式，display:block 只让
# details 元素本身是块级，折叠内容依旧不渲染。展开在 _JS_PRINT 里做。
# summary 也不能整块隐藏：明细表的 <h2> 标题就长在里面，隐藏了打印版会
# 多出一张没有名字的表；只去掉折叠箭头即可。
_CSS_PRINT = """
@media print{
  :root{--bg:#fff;--card:#fff;--text:#111;--muted:#555;--line:#ccc}
  body{background:#fff;color:#111}
  .no-print,#lb{display:none !important}
  th{cursor:default}
  details>summary{list-style:none}
  details>summary::-webkit-details-marker{display:none}
  .sp,.cover,tr,.grid{page-break-inside:avoid;break-inside:avoid}
  img{max-height:none}
  .cover img,.cover .ph{max-height:12cm}
}
"""
```

- [ ] **Step 4: 追加打印前落位脚本**

在 `_JS_SORT` 之后追加：

```python
# 打印前把懒插入的图片全部落位——这是懒插入方案的必要代价：未进过视口的
# <img> 没有 src，直接打印会得到大片空白。300ms 留给浏览器解码。
# Materialize every lazy image before printing; images that never entered the
# viewport have no src and would otherwise print blank.
_JS_PRINT = """
(function(){
  var reopened=[];
  function materialize(){
    document.querySelectorAll('img[data-idx]').forEach(function(el){
      if(!el.src&&IMGS[el.dataset.idx]) el.src=IMGS[el.dataset.idx];
    });
  }
  function expand(){
    materialize();
    reopened=[];
    document.querySelectorAll('details:not([open])').forEach(function(d){
      d.open=true; reopened.push(d);
    });
  }
  function restore(){
    reopened.forEach(function(d){d.open=false;});
    reopened=[];
  }
  window.addEventListener('beforeprint',expand);
  window.addEventListener('afterprint',restore);
  if(window.matchMedia){
    var mq=window.matchMedia('print');
    var onchange=function(e){ e.matches?expand():restore(); };
    if(mq.addEventListener) mq.addEventListener('change',onchange);
    else if(mq.addListener) mq.addListener(onchange);
  }
  var btn=document.getElementById('pdfbtn'); if(!btn)return;
  btn.addEventListener('click',function(){
    expand();
    setTimeout(function(){window.print();},300);
  });
})();
"""
```

- [ ] **Step 5: 把按钮与打印样式接进 build_html()**

在 `build_html()` 的 `by = ...` 之后增加一行：

```python
    pdf_label = "存为 PDF" if is_zh else "Save as PDF"
```

并把 `return` 的 f-string 改成下列版本（三处变化：`<style>` 接上 `_CSS_PRINT`、`<body>` 后加按钮、脚本列表末尾加 `_JS_PRINT`）：

```python
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
<script>{reg.script()}</script>
<script>{_JS_LAZY}</script>
<script>{_JS_LIGHTBOX}</script>
<script>{_JS_SORT}</script>
<script>{_JS_PRINT}</script>
</body>
</html>"""
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 29 passed

- [ ] **Step 7: 目视验证打印预览**

重跑 Task 4 Step 10 的脚本生成 `/tmp/preview_report.html`，打开后按 `Cmd+P`（Windows `Ctrl+P`）。

Expected: 预览为白底黑字；明细表已自动展开；右上角「存为 PDF」按钮不出现在预览中；鸟种块没有被跨页拦腰截断。

- [ ] **Step 8: py_compile 校验并提交**

```bash
python3 -m py_compile core/report_export.py
git add core/report_export.py
git add -f test_report_export.py
git commit -m "feat(report): 打印适配与「存为 PDF」按钮

代码级 PDF 需 QtWebEngine（+200~300MB 打包体积、macOS helper 需单独签名
带 JIT entitlement），改为 window.print() 交给系统打印对话框，体验差别
只有一次点击。按钮点击时先把懒插入的图片全部落位，否则未进过视口的图
会打印成空白——这是懒插入方案的必要代价。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: 导出对话框与预检

**Files:**
- Create: `ui/report_export_dialog.py`
- Modify: `core/report_export.py`（新增 `estimate_size`、`build_output_path`）
- Test: `test_report_export.py`

**Interfaces:**
- Consumes: Task 2 的 `preview_availability`、`collect_image_jobs`、`IMG_SPECS`
- Produces:

```python
# core/report_export.py
def estimate_size(job_count_by_kind: Dict[str, int]) -> int: ...        # 返回预估字节数
def build_output_path(directory: str, dir_name: str, is_zh: bool,
                      today: str) -> str: ...                            # 返回不冲突的绝对路径

# ui/report_export_dialog.py
class ReportExportDialog(QDialog):
    def get_options(self) -> dict: ...   # {"include_gps": bool, "with_detail_thumbs": bool}
```

- [ ] **Step 1: 写失败测试（纯函数部分）**

追加到 `test_report_export.py`：

```python
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

    open(zh, "w", encoding="utf-8").write("x")
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k "output_path or estimate"`
Expected: FAIL — `ImportError: cannot import name 'build_output_path'`

- [ ] **Step 3: 实现纯函数部分**

在 `core/report_export.py` 追加（import 区增补 `import re`）：

```python
# 各档单张编码后的经验均值（字节），用于导出前预估（spec 6.2 / 6.4）。
# Empirical per-image byte averages used for the pre-export size estimate.
_EST_BYTES = {"cover": 400_000, "rep": 110_000, "small": 32_000,
              "hd": 150_000, "thumb": 12_000}

# base64 编码膨胀系数 / base64 inflation factor.
_BASE64_FACTOR = 1.33

# 明细表带缩略图的照片数上限（spec 6.4，针对文件体积而非内存）。
# Above this photo count the detail table drops its thumbnail column.
DETAIL_THUMB_LIMIT = 600

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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 32 passed

- [ ] **Step 5: 实现对话框**

新建 `ui/report_export_dialog.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 导出报告对话框 / Report export dialog.

收集导出选项（GPS 勾选）、展示预检结果与体积/耗时预估。
真正的生成工作在 ui/results_browser_window.py 的工作线程里进行。

Collects export options, shows the pre-flight result and the size estimate.
The actual generation runs in a worker thread owned by the browser window.
"""

from typing import Dict

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                               QVBoxLayout)

from core.report_export import DETAIL_THUMB_LIMIT, SIZE_WARN_BYTES
from ui.icon_utils import checkbox_indicator_qss


class ReportExportDialog(QDialog):
    """
    导出报告的选项对话框。

    参数:
        i18n: 全局 i18n 实例，用于文案本地化。
        available (int): 预检得到的可用预览数。
        total (int): 照片总数。
        est_bytes (int): 预估文件字节数。
        est_seconds (int): 预估耗时秒数。
        parent: 父窗口。

    Option dialog shown before exporting a report.
    """

    def __init__(self, i18n, available: int, total: int, est_bytes: int,
                 est_seconds: int, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.t("report_export.title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        ratio = (available / total) if total else 0.0
        est_mb = est_bytes / (1024 * 1024)
        layout.addWidget(QLabel(i18n.t("report_export.estimate",
                                       size=f"{est_mb:.0f}", secs=est_seconds)))

        # 预览可用率 50%~90%：可以导出，但要让用户知道会有占位块（spec 7.1）。
        # 低于 50% 的情况由调用方拦截，不会走到这个对话框。
        if ratio < 0.9:
            warn = QLabel(i18n.t("report_export.missing_previews",
                                 count=total - available))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#ffcc00")
            layout.addWidget(warn)

        if est_bytes >= SIZE_WARN_BYTES:      # 阈值只在 core 定义一处
            big = QLabel(i18n.t("report_export.too_big"))
            big.setWordWrap(True)
            big.setStyleSheet("color:#ffcc00")
            layout.addWidget(big)

        # GPS 默认不勾（spec D3）：珍稀鸟点位泄露是真实风险。
        # GPS off by default; leaking rare-bird locations is a real risk.
        self._gps = QCheckBox(i18n.t("report_export.include_gps"))
        self._gps.setChecked(False)
        self._gps.setToolTip(i18n.t("report_export.include_gps_tip"))
        # 全局默认是方框指示器，本项目统一用圆圈/带勾圆圈（见 CLAUDE.md）。
        self._gps.setStyleSheet(checkbox_indicator_qss())
        layout.addWidget(self._gps)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._total = total

    def get_options(self) -> Dict[str, bool]:
        """
        返回用户选择的导出选项。

        Returns:
            Dict[str, bool]: include_gps 与 with_detail_thumbs。

        Return the chosen export options.
        """
        return {
            "include_gps": self._gps.isChecked(),
            "with_detail_thumbs": self._total <= DETAIL_THUMB_LIMIT,
        }
```

- [ ] **Step 6: py_compile 校验**

Run: `python3 -m py_compile core/report_export.py ui/report_export_dialog.py`
Expected: 无输出

- [ ] **Step 7: 提交**

```bash
git add core/report_export.py ui/report_export_dialog.py
git add -f test_report_export.py
git commit -m "feat(report): 导出对话框、预检与体积预估

build_output_path 写目录根而非 .superpicky 隐藏目录（报告就是要被找到并
发出去的），文件名跟随界面语言，同名加 _2 不覆盖，并净化路径分隔符防穿目录。
对话框在导出前告知预估体积与耗时；GPS 默认不勾。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: 工具栏入口、工作线程与原子写

**Files:**
- Modify: `ui/results_browser_window.py`（工具栏见 `:971`，路径解析见 `:1264`）
- Create: `test_report_export_entry.py`
- Modify: `core/report_export.py`（新增 `write_report_atomically`）

**Interfaces:**
- Consumes: Task 1–6 全部公开函数、`ReportExportDialog`
- Produces:

```python
# core/report_export.py
def write_report_atomically(path: str, html: str) -> None: ...

# ui/results_browser_window.py
class ResultsBrowserWindow:
    def _export_report(self) -> None: ...     # 按钮槽函数
```

**为什么要原子写**（spec 7.2）：导出耗时十几秒，中途取消或崩溃若直接写目标文件，用户会得到一个半截的 HTML，双击打开是残页。先写 `.tmp` 再 `os.replace()` 可杜绝。

- [ ] **Step 1: 写失败测试（原子写）**

追加到 `test_report_export.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export.py -v -k atomically`
Expected: FAIL — `ImportError: cannot import name 'write_report_atomically'`

- [ ] **Step 3: 实现原子写**

在 `core/report_export.py` 追加：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export.py -v`
Expected: 34 passed

- [ ] **Step 5: 在工具栏加按钮**

在 `ui/results_browser_window.py` 的 `_build_toolbar()` 中，`self._compare_btn` 之后、Apple Photos 导入之前插入：

```python
        # 导出报告：把当前载入的全量照片聚合成一个可分享的 HTML（spec D4）。
        # 刻意**不受筛选面板影响**——报告的统计口径必须是「这次拍的全部」，
        # 跟随筛选会让命中率变成 62/62=100% 这种无意义的数字。
        # Export report over the full loaded set, never the filtered view.
        self._export_btn = QPushButton(self.i18n.t("report_export.button"))
        self._export_btn.setObjectName("secondary")
        self._export_btn.setFixedHeight(32)
        self._export_btn.setToolTip(self.i18n.t("report_export.button_tip"))
        self._export_btn.clicked.connect(self._export_report)
        layout.addWidget(self._export_btn)
```

- [ ] **Step 6: 实现导出槽函数**

在 `ui/results_browser_window.py` 中 `_resolve_photo_paths()`（`:1264`）附近追加：

```python
    @Slot()
    def _export_report(self) -> None:
        """
        导出可分享的 HTML 报告。

        口径为当前载入的**全量**照片（`self._all_photos`），不跟随筛选面板
        （spec D4）。路径先经 _resolve_photo_paths 解析为绝对路径再交给生成器
        ——report.db 存的是相对路径，生成器不自行拼接（spec 4.2）。

        Export the shareable HTML report over the full loaded photo set.
        """
        import datetime
        import os

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from constants import APP_VERSION
        from core.report_export import (aggregate, build_html, collect_image_jobs,
                                        encode_preview, estimate_size,
                                        build_output_path, preview_availability,
                                        write_report_atomically,
                                        DETAIL_THUMB_LIMIT)
        from ui.report_export_dialog import ReportExportDialog
        from ui.custom_dialogs import StyledMessageBox

        if not self._all_photos or not self._directory:
            StyledMessageBox.warning(self, self.i18n.t("messages.hint"),
                                     self.i18n.t("report_export.no_photos"))
            return

        rows = [self._resolve_photo_paths(p) for p in self._all_photos]
        available, total = preview_availability(rows)

        # 预检 < 50%：拦住并说明原因（spec 7.1）。预览缓存被 keep_temp_files
        # 关掉后清理，是本功能最可能发生的失败。
        if total and available / total < 0.5:
            reply = StyledMessageBox.question(
                self, self.i18n.t("report_export.title"),
                self.i18n.t("report_export.previews_gone", count=total - available),
                yes_text=self.i18n.t("report_export.text_only"),
                no_text=self.i18n.t("labels.no"))
            if reply != StyledMessageBox.Yes:
                return

        with_thumbs = total <= DETAIL_THUMB_LIMIT
        probe = aggregate(rows, include_gps=False)
        jobs = collect_image_jobs(probe, with_detail_thumbs=with_thumbs)
        counts = {}
        for job in jobs:
            kind = job.job_id.split(":", 1)[0]
            counts[kind] = counts.get(kind, 0) + 1
        est_bytes = estimate_size(counts)
        est_secs = max(1, int(len(jobs) * 0.06))

        dialog = ReportExportDialog(self.i18n, available, total, est_bytes,
                                    est_secs, self)
        if dialog.exec() != ReportExportDialog.Accepted:
            return
        options = dialog.get_options()

        data = aggregate(rows, include_gps=options["include_gps"])
        data = replace(data, dir_name=os.path.basename(self._directory) or self._directory)
        jobs = collect_image_jobs(data,
                                  with_detail_thumbs=options["with_detail_thumbs"])

        progress = QProgressDialog(self.i18n.t("report_export.working"),
                                   self.i18n.t("report_export.cancel"),
                                   0, len(jobs), self)
        progress.setWindowModality(Qt.WindowModal)
        encoded = {}
        for index, job in enumerate(jobs):
            if progress.wasCanceled():
                return
            uri = encode_preview(job.path, job.max_edge, job.quality)
            if uri:
                encoded[job.job_id] = uri
            progress.setValue(index + 1)
        progress.close()

        is_zh = self.i18n.current_lang.startswith("zh")
        html = build_html(
            data, encoded, is_zh=is_zh, app_version=APP_VERSION,
            generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            with_detail_thumbs=options["with_detail_thumbs"])
        out = build_output_path(self._directory, data.dir_name, is_zh,
                                datetime.date.today().isoformat())
        try:
            write_report_atomically(out, html)
        except OSError as exc:
            StyledMessageBox.warning(self, self.i18n.t("errors.error_title"),
                                     self.i18n.t("report_export.write_failed",
                                                 error=str(exc)))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(out))
```

在 `ui/results_browser_window.py` 顶部 import 区增补 `from dataclasses import replace`。

**已有 import 无需重复添加**（`ui/results_browser_window.py:20-27` 已包含）：
`QPushButton`、`QProgressDialog`、`Qt`、`Slot`。只有 `QUrl` 与 `QDesktopServices`
需要新增，且已在槽函数内局部 import。

**注意**：上面的编码循环跑在主线程里，靠 `QProgressDialog` 保持界面响应。若在 Task 9 的真实目录验证中发现界面卡顿明显（318 张预计 15~25 秒），再改为 `QThread` + `Signal` 上报进度；先用简单版本，避免为未证实的问题引入线程复杂度。

- [ ] **Step 7: 写 UI 接线测试**

新建 `test_report_export_entry.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出报告入口的接线测试：只验按钮存在与信号连接，不执行真实导出。

参照 test_species_merge_entry.py 的写法。

Wiring test for the export entry: verifies the button exists and is
connected, without running a real export.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def test_export_button_wired():
    """工具栏存在导出按钮，且已连接 _export_report。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow

    window = ResultsBrowserWindow()
    try:
        assert hasattr(window, "_export_btn")
        assert callable(getattr(window, "_export_report", None))
        # isSignalConnected 是 QObject 的公开 API，比 receivers() 可靠。
        from PySide6.QtCore import QMetaMethod
        meta = window._export_btn.metaObject()
        index = meta.indexOfSignal("clicked(bool)")
        assert window._export_btn.isSignalConnected(meta.method(index))
    finally:
        window.deleteLater()


def test_export_report_guards_empty_directory():
    """未载入任何照片时，导出应安全返回而不抛异常。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow
    from ui import custom_dialogs

    window = ResultsBrowserWindow()
    calls = []
    original = custom_dialogs.StyledMessageBox.warning
    custom_dialogs.StyledMessageBox.warning = staticmethod(
        lambda *a, **k: calls.append(a))
    try:
        window._all_photos = []
        window._directory = ""
        window._export_report()
        assert calls, "空目录应给出提示"
    finally:
        custom_dialogs.StyledMessageBox.warning = original
        window.deleteLater()
```

- [ ] **Step 8: 运行接线测试**

Run: `python3 -m pytest test_report_export_entry.py -v`
Expected: 2 passed

- [ ] **Step 9: py_compile 校验并提交**

```bash
python3 -m py_compile core/report_export.py ui/results_browser_window.py ui/report_export_dialog.py
git add core/report_export.py ui/results_browser_window.py
git add -f test_report_export.py test_report_export_entry.py
git commit -m "feat(report): 选鸟浏览器导出入口与原子写

按钮导出当前载入的全量照片，刻意不跟随筛选面板——跟随筛选会让命中率
变成 62/62=100% 这种无意义数字。路径先经 _resolve_photo_paths 解析为
绝对路径再交给生成器（report.db 存的是相对路径）。
写入先落 .tmp 再 os.replace，杜绝中途取消留下半截 HTML。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: i18n 文案

**Files:**
- Modify: `locales/zh_CN.json`、`locales/en_US.json`
- Test: `test_report_export_entry.py`

**Interfaces:**
- Consumes: Task 6、7 中用到的全部 `i18n.t()` 键
- Produces: `report_export.*` 段，两个语言文件键集必须完全一致

- [ ] **Step 1: 写失败测试**

追加到 `test_report_export_entry.py`：

```python
def test_report_export_locale_keys_match():
    """
    中英文 locale 的 report_export 段键集必须完全一致。

    缺键会让界面显示原始 key（如 "report_export.title"），
    这类问题在中文环境下测不出来。

    The two locale files must expose an identical key set; a missing key
    renders as the raw key string and would go unnoticed in one language.
    """
    import json
    zh = json.load(open("locales/zh_CN.json", encoding="utf-8"))
    en = json.load(open("locales/en_US.json", encoding="utf-8"))
    assert "report_export" in zh and "report_export" in en
    assert set(zh["report_export"]) == set(en["report_export"])


def test_report_export_keys_cover_all_usages():
    """代码中用到的每个 report_export.* 键都必须在 locale 中存在。"""
    import json
    import re
    zh = json.load(open("locales/zh_CN.json", encoding="utf-8"))["report_export"]
    used = set()
    for path in ("ui/results_browser_window.py", "ui/report_export_dialog.py"):
        with open(path, encoding="utf-8") as fh:
            used |= set(re.findall(r'report_export\.(\w+)', fh.read()))
    missing = used - set(zh)
    assert not missing, f"locale 缺少这些键: {sorted(missing)}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest test_report_export_entry.py -v -k locale`
Expected: FAIL — `assert "report_export" in zh`

- [ ] **Step 3: 写入 locale（用 Python，勿用 sed——文件含中文）**

```bash
python3 - <<'EOF'
import json
from collections import OrderedDict

ZH = {
    "button": "导出报告",
    "cancel": "取消",
    "button_tip": "把这次选片的结果导出成一个可分享的 HTML 文件",
    "title": "导出报告",
    "estimate": "预计生成约 {size} MB，用时约 {secs} 秒",
    "include_gps": "包含精确 GPS 坐标",
    "include_gps_tip": "分享给他人时建议不勾选——珍稀鸟种的点位可能被扩散",
    "missing_previews": "有 {count} 张照片的预览不可用，报告中将显示为占位块",
    "previews_gone": "有 {count} 张照片找不到预览图（临时文件可能已被清理）。\n可以生成一份不含图片的纯文字报告，或先重新处理该目录。",
    "text_only": "生成纯文字版",
    "too_big": "文件偏大，部分聊天工具与邮箱的附件上限在 100MB 左右",
    "working": "正在生成报告…",
    "no_photos": "当前没有可导出的照片",
    "write_failed": "报告写入失败：{error}",
}

EN = {
    "button": "Export Report",
    "cancel": "Cancel",
    "button_tip": "Export this session's results as a shareable HTML file",
    "title": "Export Report",
    "estimate": "About {size} MB, roughly {secs} seconds",
    "include_gps": "Include exact GPS coordinates",
    "include_gps_tip": "Leave unchecked when sharing — rare bird locations can spread",
    "missing_previews": "{count} photos have no usable preview and will show as placeholders",
    "previews_gone": "No preview found for {count} photos (temporary files may have been cleaned up).\nYou can generate a text-only report, or re-process the directory first.",
    "text_only": "Text-only report",
    "too_big": "Large file — some chat apps and mail servers cap attachments near 100MB",
    "working": "Generating report…",
    "no_photos": "No photos available to export",
    "write_failed": "Failed to write report: {error}",
}

for path, block in (("locales/zh_CN.json", ZH), ("locales/en_US.json", EN)):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh, object_pairs_hook=OrderedDict)
    data["report_export"] = OrderedDict(sorted(block.items()))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("updated", path)
EOF
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest test_report_export_entry.py -v`
Expected: 4 passed

- [ ] **Step 5: 确认没破坏既有 locale**

Run: `python3 -m pytest test_aesthetic_i18n.py test_settings_center.py -v`
Expected: 全部通过（确认 JSON 重写没破坏其他段）

- [ ] **Step 6: 提交**

```bash
git add locales/zh_CN.json locales/en_US.json
git add -f test_report_export_entry.py
git commit -m "i18n(report): 导出报告的中英文案

GPS 勾选项的提示直说「珍稀鸟种的点位可能被扩散」，而不是泛泛的隐私提醒——
用户需要知道具体风险才会做对选择。
测试钉住两个 locale 键集一致，并校验代码里用到的每个键都存在。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: 真实目录端到端验证（手工）

**Files:** 无代码改动。本 Task 的交付物是一份验证记录。

**前置**：Task 1–8 全部完成且测试通过。

- [ ] **Step 1: 全量测试**

Run: `python3 -m pytest test_report_export.py test_report_export_entry.py -v`
Expected: 36 passed

- [ ] **Step 2: 回归既有测试**

Run: `python3 -m pytest -x -q`
Expected: 无新增失败。若有失败，先确认是否为已知的历史失败（对比改动前的基线）。

- [ ] **Step 3: 真实目录导出**

启动应用 → 打开一个**已处理过、预览缓存完整**的真实目录 → 选鸟浏览器 → 点「导出报告」。

记录以下实测值，与 spec 第 6 节估算对账：

| 指标 | spec 估算 | 实测 |
|---|---|---|
| 照片数 / 鸟种数 | 318 / 12 | |
| 文件体积 | ≈18.5 MB | |
| 生成耗时 | 15~25 秒 | |

- [ ] **Step 4: 验证懒插入真的生效（本 Task 最关键的一步）**

在 Chrome 打开生成的报告 → DevTools → Performance monitor（或 Memory 面板）→ 从头滚到尾。

Expected: JS heap 与 `Documents`/`Nodes` 之外，**图片占用的内存全程稳定在 20~40MB**，不随滚动单调累积。

这是 spec 6.1 懒插入是否真正生效的**唯一硬证据**。若观察到内存随滚动持续上涨，说明 `removeAttribute('src')` 没生效或 `rootMargin` 太大，必须回到 Task 3 修复——否则 40 个鸟种的报告在手机上会被系统杀掉。

- [ ] **Step 5: 手机端验证**

把 HTML 传到手机（AirDrop / 微信文件传输），用手机浏览器打开。

Expected: 能正常打开并滚动到底，不崩溃、不白屏；点图能放大；折叠区能展开。

- [ ] **Step 6: 打印验证**

在桌面浏览器点「存为 PDF」→ 系统打印对话框 → 另存为 PDF。

Expected: 白底；图片**全部出现**（验证 `_JS_PRINT` 的落位逻辑生效，未进过视口的图没有印成空白）；明细表已展开；按钮本身不在 PDF 里。

- [ ] **Step 7: GPS 脱敏验证**

不勾 GPS 导出一份，用文本编辑器打开 HTML，搜索该目录照片的实际纬度数值（如 `16.91`）。

Expected: **搜不到**。这验证了坐标是在聚合层丢弃而非渲染层隐藏——渲染层隐藏的话，查看源代码就能挖出来（spec 4.3）。

- [ ] **Step 8: 中文与跨平台验证**

用一个**中文目录名**的目录导出一次。

Expected: 文件名为 `SuperPicky报告_<中文目录名>_<日期>.html`，无乱码；双击能打开；报告内鸟种中文名显示正常。

若有 Windows 环境，在 Windows 上重复 Step 3 与本步骤。

- [ ] **Step 9: 预览缓存缺失路径验证**

在设置中心关掉「保留临时文件」→ 重新处理一个小目录（或手工删掉 `.superpicky/cache/`）→ 导出。

Expected: 弹出「有 N 张照片找不到预览图」的拦截提示，可选择生成纯文字版；生成的报告图片位置是虚线占位块，页面结构完整不塌。

- [ ] **Step 10: 记录验证结果并提交**

把 Step 3 的实测值填进 spec 第 6 节（若与估算偏差超过 30%，同时修正估算值与 `_EST_BYTES` 常量）。

```bash
git add docs/specs/2026-08-26-share-report-html-design.md
git commit -m "docs(spec): 补记报告导出的真实目录实测值

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 附录：与 spec 的对应关系 / Spec Coverage

| spec 章节 | 由哪个 Task 实现 |
|---|---|
| 2.2 拍摄时段替代处理耗时 | Task 1（`shot_start`/`shot_end`） |
| 4.2 路径解析不在生成器内 | Task 7（`_resolve_photo_paths` 后再交付） |
| 4.3 GPS 在聚合层丢弃 | Task 1（用例 4）+ Task 9 Step 7（真实验证） |
| 5.0 深色单一主题 + 系统字体 | Task 3（`_CSS_BASE`） |
| 5.1 ① 封面 | Task 3（`_cover_html`） |
| 5.1 ② 鸟种画廊 | Task 4（`_species_html`） |
| 5.1 ③ 数据区 | Task 4（`_stats_html`） |
| 5.1 ④ 折叠明细 | Task 4（`_detail_html`） |
| 5.1 ⑤ 页脚 | Task 3 |
| 5.2 打印适配四项 | Task 5（用例 14） |
| 6.1 懒插入 | Task 3（用例 7）+ Task 9 Step 4（硬证据） |
| 6.2 分档规格 | Task 2（`IMG_SPECS`） |
| 6.3 编码流水线 | Task 2（`encode_preview`） |
| 6.4 规模保护与预估 | Task 6（`estimate_size`、`DETAIL_THUMB_LIMIT`） |
| 7.1 预览缺失预检 | Task 6（`preview_availability`）+ Task 7（拦截）+ Task 9 Step 9 |
| 7.2 单张失败不中断 | Task 2（`encode_preview` 返回 None）+ Task 3（占位块） |
| 7.2 HTML 转义 | Task 3（用例 6） |
| 7.2 原子写 | Task 7（用例 13） |
| 7.2 UTF-8 | Task 3（用例 12）+ Task 9 Step 8 |
| 7.2 优雅降级 | Task 3/4（各区块空数据时返回 ""） |
| D5 输出路径 | Task 6（`build_output_path`） |
| D6 无绝对路径 | Task 1（`PhotoRef.filename` basename 化） |
| D7 语言跟随 | Task 3/4（`is_zh`）+ Task 8 |
| D9 PDF | Task 5 |
