# -*- coding: utf-8 -*-
"""
增量选片支持模块（#161）：扫描根目录未处理文件、构建历史 V2 参考池、偏移连拍编号与合并清单。

Incremental culling helpers (#161): root photo/delta scanning, historical V2
quota/species-floor context reconstruction, burst ID/folder offset calculation,
and manifest merging.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from constants import HEIF_EXTENSIONS, JPG_EXTENSIONS, RAW_EXTENSIONS
from core.folder_layout import LAYOUT_FLAT
from core.iso_sharpness import display_aesthetic, display_head_sharpness
from core.processed_lookup import _with_read_connection
from core.rating_quota import PhotoMetricsV2, SPECIES_FLOOR_RATING
from tools.resume_state import ResumeStateManager

_BURST_DIR_RE = re.compile(r"^burst_(\d+)$")


@dataclass
class RootScanResult:
    """
    目录首层可处理照片的扫描结果（与 PhotoProcessor._scan_files 口径完全一致）。

    Result of scanning the top level of a directory using the active format filter.
    """

    raw_dict: Dict[str, str] = field(default_factory=dict)
    jpg_dict: Dict[str, str] = field(default_factory=dict)
    files_tbr: List[str] = field(default_factory=list)
    heif_processed_as_raw: Set[str] = field(default_factory=set)
    filtered_out: List[str] = field(default_factory=list)
    skipped_same_name: List[str] = field(default_factory=list)


@dataclass
class IncrementalDelta:
    """
    目录的增量状态快照。

    Snapshot comparing top-level unprocessed media against `.superpicky/report.db`.
    """

    has_report_db: bool = False
    has_valid_db: bool = False
    existing_count: int = 0
    existing_prefixes: Set[str] = field(default_factory=set)
    new_raw_dict: Dict[str, str] = field(default_factory=dict)
    new_jpg_dict: Dict[str, str] = field(default_factory=dict)
    new_files_tbr: List[str] = field(default_factory=list)
    new_count: int = 0
    new_video_files: List[str] = field(default_factory=list)
    skipped_existing_count: int = 0
    conflict_same_stem: List[str] = field(default_factory=list)
    has_resume_state: bool = False

    @property
    def has_new_work(self) -> bool:
        """是否有未处理的新照片或新视频 / Whether new photos or videos await."""
        return self.new_count > 0 or bool(self.new_video_files)


@dataclass
class HistoricalV2Context:
    """
    从历史 report.db 读取的 V2 定星与精选参考上下文（只读，不修改历史记录）。

    Read-only historical reference context extracted from report.db for V2
    incremental rating, species floor, and picked calculation.
    """

    reference_photos: List[PhotoMetricsV2] = field(default_factory=list)
    existing_floor_species: Set[str] = field(default_factory=set)
    existing_star_3_scores: List[Tuple[float, float]] = field(default_factory=list)


def scan_root_photos(
    dir_path: str,
    allowed_exts: Optional[FrozenSet[str]] = None,
) -> RootScanResult:
    """
    按 #126 格式筛选规则扫描目录顶层照片，分离 RAW/HEIF、配对 JPG 与独立 JPG。

    参数:
        dir_path (str): 目标目录路径
        allowed_exts (Optional[FrozenSet[str]]): 允许的小写扩展名集合；None 表示全部允许

    返回:
        RootScanResult: 扫描与筛选后的字典、待读列表及跳过记录

    Scan top-level photo files under ``dir_path``, applying the #126 format
    filter and camera-native RAW precedence.
    """
    result = RootScanResult()
    if not dir_path or not os.path.isdir(dir_path):
        return result

    heif_dict: Dict[str, str] = {}
    raw_candidates: Dict[str, List[str]] = {}

    try:
        entries = os.listdir(dir_path)
    except OSError:
        return result

    for filename in entries:
        if filename.startswith("."):
            continue
        lower_name = filename.lower()
        if lower_name.startswith(("tmp_", "temp_")):
            continue
        if lower_name in ("desktop.ini", "thumbs.db"):
            continue
        full_path = os.path.join(dir_path, filename)
        if not os.path.isfile(full_path):
            continue

        file_prefix, file_ext = os.path.splitext(filename)
        ext_lower = file_ext.lower()
        if ext_lower in RAW_EXTENSIONS:
            raw_candidates.setdefault(file_prefix, []).append(file_ext)
        elif ext_lower in HEIF_EXTENSIONS:
            heif_dict[file_prefix] = file_ext
        if ext_lower in JPG_EXTENSIONS:
            result.jpg_dict[file_prefix] = file_ext

    raw_candidates_all = set(raw_candidates)
    heif_all = set(heif_dict)

    def _allowed(ext: str) -> bool:
        return allowed_exts is None or ext.lower() in allowed_exts

    for prefix in list(raw_candidates):
        kept = [e for e in raw_candidates[prefix] if _allowed(e)]
        result.filtered_out.extend(
            prefix + e for e in raw_candidates[prefix] if not _allowed(e)
        )
        if kept:
            raw_candidates[prefix] = kept
        else:
            del raw_candidates[prefix]

    for prefix in list(heif_dict):
        if not _allowed(heif_dict[prefix]):
            result.filtered_out.append(prefix + heif_dict.pop(prefix))

    for prefix, exts in raw_candidates.items():
        ordered = sorted(exts, key=lambda e: (e.lower() == ".dng", e.lower(), e))
        result.raw_dict[prefix] = ordered[0]
        result.skipped_same_name.extend(prefix + e for e in ordered[1:])

    for prefix, ext in heif_dict.items():
        if prefix not in result.raw_dict:
            result.raw_dict[prefix] = ext
            result.heif_processed_as_raw.add(prefix)

    main_stems = raw_candidates_all | heif_all
    for prefix in list(result.jpg_dict):
        ext = result.jpg_dict[prefix]
        if prefix in result.raw_dict:
            keep = True
        elif prefix in main_stems:
            keep = False
        else:
            keep = _allowed(ext)
        if keep:
            result.files_tbr.append(prefix + ext)
        else:
            result.filtered_out.append(prefix + result.jpg_dict.pop(prefix))

    return result


def inspect_directory_delta(
    dir_path: str,
    allowed_exts: Optional[FrozenSet[str]] = None,
    folder_layout: Optional[str] = None,
    video_auto_process: Optional[bool] = None,
) -> IncrementalDelta:
    """
    以只读方式比对目录顶层文件与 `.superpicky/report.db`，判断是否有新增未处理照片/视频。

    绝不在检测阶段创建或修改 report.db（测试中的 0 字节占位库返回 has_valid_db=False）。

    参数:
        dir_path (str): 目录路径
        allowed_exts (Optional[FrozenSet[str]]): #126 格式筛选集合；None 时从高级配置读取
        folder_layout (Optional[str]): 目录布局；None 时从高级配置读取
        video_auto_process (Optional[bool]): 是否主流程自动处理视频；None 时从高级配置读取

    返回:
        IncrementalDelta: 增量状态描述

    Read-only inspection of ``dir_path`` against ``.superpicky/report.db`` to
    discover newly added unprocessed photos and videos.
    """
    delta = IncrementalDelta()
    if not dir_path or not os.path.isdir(dir_path):
        return delta

    try:
        delta.has_resume_state = ResumeStateManager(dir_path).exists()
    except Exception:
        delta.has_resume_state = False

    db_path = os.path.join(dir_path, ".superpicky", "report.db")
    delta.has_report_db = os.path.isfile(db_path)

    if allowed_exts is None or folder_layout is None or video_auto_process is None:
        try:
            from advanced_config import get_advanced_config
            cfg = get_advanced_config()
            if allowed_exts is None:
                allowed_exts = cfg.get_allowed_extensions()
            if folder_layout is None:
                folder_layout = cfg.folder_layout
            if video_auto_process is None:
                video_auto_process = bool(cfg.video_auto_process_in_main)
        except Exception:
            if folder_layout is None:
                folder_layout = LAYOUT_FLAT
            if video_auto_process is None:
                video_auto_process = False

    db_rows_by_prefix: Dict[str, Tuple[str, str]] = {}
    if delta.has_report_db:
        def _query_existing(conn: sqlite3.Connection):
            return conn.execute(
                "SELECT filename, original_path, current_path FROM photos"
            ).fetchall()

        fetched = _with_read_connection(db_path, _query_existing)
        if fetched is not None:
            delta.has_valid_db = True
            delta.existing_count = len(fetched)
            for row in fetched:
                stem = row["filename"]
                if stem:
                    delta.existing_prefixes.add(stem)
                    db_rows_by_prefix[stem] = (
                        row["original_path"] or "",
                        row["current_path"] or "",
                    )

    scan = scan_root_photos(dir_path, allowed_exts=allowed_exts)
    all_stems = set(scan.raw_dict) | set(scan.jpg_dict)
    new_stems: Set[str] = set()

    if not delta.has_valid_db:
        delta.new_raw_dict = dict(scan.raw_dict)
        delta.new_jpg_dict = dict(scan.jpg_dict)
        delta.new_files_tbr = list(scan.files_tbr)
        new_stems = set(all_stems)
    else:
        for prefix in all_stems:
            if prefix not in delta.existing_prefixes:
                new_stems.add(prefix)
                continue

            delta.skipped_existing_count += 1
            orig_rel, curr_rel = db_rows_by_prefix.get(prefix, ("", ""))
            root_ext = scan.raw_dict.get(prefix) or scan.jpg_dict.get(prefix) or ""
            root_name = prefix + root_ext
            curr_parent = os.path.dirname(curr_rel.replace("\\", "/"))
            known_basenames = {
                os.path.basename(p.replace("\\", "/")).lower()
                for p in (orig_rel, curr_rel)
                if p
            }
            # 非 Flat 布局下旧文件已迁入子目录，或者根目录出现同前缀不同扩展名的新文件，
            # 视为同前缀冲突（保留旧记录、不覆盖），供日志明示提醒。
            if curr_parent or (known_basenames and root_name.lower() not in known_basenames):
                delta.conflict_same_stem.append(root_name)

        delta.new_raw_dict = {
            k: v for k, v in scan.raw_dict.items() if k in new_stems
        }
        delta.new_jpg_dict = {
            k: v for k, v in scan.jpg_dict.items() if k in new_stems
        }
        tbr_set = set(scan.files_tbr)
        delta.new_files_tbr = [
            prefix + ext
            for prefix, ext in delta.new_jpg_dict.items()
            if (prefix + ext) in tbr_set
        ]

    delta.new_count = len(new_stems)

    if delta.has_valid_db and video_auto_process and folder_layout != LAYOUT_FLAT:
        try:
            from core.recursive_scanner import _scan_directory_once
            _p_cnt, root_videos, _children = _scan_directory_once(dir_path)
            delta.new_video_files = list(root_videos)
        except Exception:
            delta.new_video_files = []

    return delta


def find_max_burst_id(dir_path: str, report_db=None) -> int:
    """
    查找目录内现有 ``burst_NNN`` 文件夹及 report.db 中已使用的最大连拍组编号。

    参数:
        dir_path (str): 工作目录
        report_db: 可选的 ReportDB 实例

    返回:
        int: 最大编号（无既有连拍时返回 0）

    Find the highest existing burst group number across ``burst_NNN`` folders
    under ``dir_path`` and ``report_db`` so incremental runs offset safely.
    """
    max_id = 0
    if dir_path and os.path.isdir(dir_path):
        try:
            for root, dirs, _files in os.walk(dir_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for d in dirs:
                    m = _BURST_DIR_RE.match(d)
                    if m:
                        try:
                            max_id = max(max_id, int(m.group(1)))
                        except ValueError:
                            pass
        except OSError:
            pass

    if report_db is not None:
        try:
            with report_db._lock:
                row = report_db._conn.execute(
                    "SELECT MAX(burst_id) FROM photos WHERE burst_id IS NOT NULL"
                ).fetchone()
                if row and row[0] is not None:
                    max_id = max(max_id, int(row[0]))
        except Exception:
            pass
    elif dir_path:
        db_path = os.path.join(dir_path, ".superpicky", "report.db")
        if os.path.isfile(db_path):
            rows = _with_read_connection(
                db_path,
                lambda conn: conn.execute(
                    "SELECT MAX(burst_id) FROM photos WHERE burst_id IS NOT NULL"
                ).fetchone(),
            )
            if rows and rows[0] is not None:
                try:
                    max_id = max(max_id, int(rows[0]))
                except (TypeError, ValueError):
                    pass

    return max_id


def load_historical_v2_context(
    report_db,
    exclude_prefixes: Optional[Set[str]] = None,
) -> HistoricalV2Context:
    """
    从 report.db 重建只读历史参考池，供增量 V2 定星与精选（Picked）阈值参照。

    参数:
        report_db: ReportDB 实例
        exclude_prefixes (Optional[Set[str]]): 需要排除的前缀集合（例如当批新增照片，防止重复计入）

    返回:
        HistoricalV2Context: 历史参考照片指标、已有 ≥2★ 的鸟种集合、历史 3★ 分数对

    Build read-only V2 reference metrics, existing floored species, and
    historical 3-star scores from ``report.db``, excluding ``exclude_prefixes``.
    """
    ctx = HistoricalV2Context()
    if report_db is None:
        return ctx

    try:
        rows = report_db.get_all_photos()
    except Exception:
        return ctx

    excluded = exclude_prefixes or set()

    for row in rows:
        prefix = row.get("filename")
        if not prefix or prefix in excluded:
            continue

        species_key = (
            (row.get("bird_species_en") or row.get("bird_species_cn") or "").strip()
            or None
        )
        rating = row.get("rating")
        if species_key and rating is not None:
            try:
                if int(rating) >= SPECIES_FLOOR_RATING:
                    ctx.existing_floor_species.add(species_key)
            except (TypeError, ValueError):
                pass

        has_bird = bool(row.get("has_bird"))
        head_sharp = row.get("head_sharp")
        if has_bird and head_sharp is not None:
            norm_sharp = display_head_sharpness(row)
            if norm_sharp is None:
                continue
            topiq = display_aesthetic(row)
            best_eye = max(
                float(row.get("left_eye") or 0.0),
                float(row.get("right_eye") or 0.0),
            )
            beak_vis = float(row.get("beak") or 0.0)
            exp_status = str(row.get("exposure_status") or "").strip().lower()
            has_exp = exp_status not in ("", "normal", "ok", "none")

            ctx.reference_photos.append(
                PhotoMetricsV2(
                    key=f"__hist__:{prefix}",
                    detected=True,
                    confidence=float(row.get("confidence") or 0.0),
                    norm_sharpness=float(norm_sharp),
                    topiq=topiq,
                    best_eye=best_eye,
                    beak_vis=beak_vis,
                    is_flying=bool(row.get("is_flying")),
                    focus_status=str(row.get("focus_status") or ""),
                    has_exposure_issue=has_exp,
                    burst_id=None,
                    species=species_key,
                    species_confirmed=bool(species_key),
                )
            )

            try:
                if rating is not None and int(rating) >= 3:
                    adj_topiq = row.get("adj_topiq")
                    adj_sharp = row.get("adj_sharpness")
                    nima_val = float(adj_topiq) if adj_topiq is not None else (
                        float(topiq) if topiq is not None else None
                    )
                    sharp_val = (
                        float(adj_sharp)
                        if adj_sharp is not None
                        else float(norm_sharp)
                    )
                    if nima_val is not None:
                        ctx.existing_star_3_scores.append((nima_val, sharp_val))
            except (TypeError, ValueError):
                pass

    return ctx


def merge_incremental_manifest(
    dir_path: str,
    new_files: List[dict],
    new_temp_jpegs: List[str],
    folder_structure: List[str],
    moved_delta: int,
) -> dict:
    """
    增量模式下将新移动的文件条目合并写入 ``.superpicky_manifest.json``，保留历史条目。

    参数:
        dir_path (str): 目录路径
        new_files (List[dict]): 本轮移动文件清单
        new_temp_jpegs (List[str]): 本轮转换的临时 JPEG 相对路径列表
        folder_structure (List[str]): 星级目录名列表
        moved_delta (int): 本轮实际移动的文件数

    返回:
        dict: 合并后的 manifest 字典

    Merge newly organized file entries into an existing ``.superpicky_manifest.json``
    without dropping entries from earlier batches.
    """
    manifest_path = os.path.join(dir_path, ".superpicky_manifest.json")
    existing: dict = {}
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}

    merged_by_name: Dict[str, dict] = {}
    ordered_names: List[str] = []
    for entry in existing.get("files") or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("filename")
        if name:
            if name not in merged_by_name:
                ordered_names.append(name)
            merged_by_name[name] = entry

    for entry in new_files:
        name = entry.get("filename")
        if not name:
            continue
        if name not in merged_by_name:
            ordered_names.append(name)
        merged_by_name[name] = entry

    merged_files = [merged_by_name[n] for n in ordered_names]

    merged_temps: List[str] = []
    seen_temps: Set[str] = set()
    for p in list(existing.get("temp_jpegs") or []) + list(new_temp_jpegs or []):
        if p and p not in seen_temps:
            seen_temps.add(p)
            merged_temps.append(p)

    prev_moved = int((existing.get("stats") or {}).get("total_moved") or 0)
    manifest = {
        "version": "2.0",
        "created": existing.get("created") or datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "app_version": "V4.0.5",
        "original_dir": dir_path,
        "folder_structure": folder_structure,
        "bird_species_dirs": True,
        "files": merged_files,
        "temp_jpegs": merged_temps,
        "stats": {"total_moved": prev_moved + moved_delta},
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest
