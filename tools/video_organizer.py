#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 3 — 视频分析结果整理器

把 VideoAnalyzer 的分析结果落地到磁盘：
    - 按主鸟种归类（有鸟视频 → 3星_有鸟/{主鸟种}/  / 无鸟视频 → 0星_无鸟/）
    - 按鸟种 + 拍摄日期重命名视频文件

设计原则：
    - 纯函数 + 单一职责，便于单测和复用
    - 不直接依赖 PySide6，调用方在 worker thread 内执行
    - 默认移动（与照片端一致），可改复制
    - 命名冲突自动加 _2/_3 后缀（不覆盖现有文件）

Video analysis result organizer for Phase 3.
Moves/copies videos into species-named subdirectories with renamed filenames.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from core.video_segment import BirdSegment


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

# 「其他鸟 / 无鸟」目录名的中英双语别名（用于跨语言幂等匹配）
# Bilingual aliases for the "other species" / "no bird" folders, so the
# idempotency guard recognizes folders created under either UI language.
OTHER_SPECIES_FOLDER_ALIASES: Tuple[str, ...] = ("其他鸟", "Other birds")
NO_BIRD_FOLDER_ALIASES: Tuple[str, ...] = ("无鸟", "No bird")


@dataclass(slots=True)
class OrganizeOptions:
    """
    整理选项 / Organize options

    视频端不用星级（与照片端不同），直接在源视频父目录创建鸟种子目录：
        - 识别到具体鸟种 → 源目录/{鸟种}/{鸟种}_{日期}_{原名}.ext
        - 有鸟但识别失败 → 源目录/{other_species_folder}/{other}_{日期}_{原名}.ext
        - 完全无鸟       → 源目录/{no_bird_folder}/{no_bird}_{日期}_{原名}.ext

    operation              : 'move'（默认）或 'copy'
    no_bird_folder         : 无鸟视频的目录名 / 命名前缀，默认 "无鸟"
    other_species_folder   : 有鸟但识别失败的目录名 / 命名前缀，默认 "其他鸟"
    use_english            : True 时鸟种文件夹名/文件名前缀用英文名（species_en），
                             False（默认）用中文名。由 UI 层按界面语言传入。

    Videos are organized directly under the source parent dir, by species.
    Localization (folder/prefix language) is decided by the UI layer and
    passed in here, keeping the organizer itself free of i18n dependency.
    """
    operation: Literal['move', 'copy'] = 'move'
    no_bird_folder: str = "无鸟"
    other_species_folder: str = "其他鸟"
    use_english: bool = False


@dataclass(slots=True)
class OrganizeResult:
    """单视频整理结果 / Result of organizing a single video"""
    source_path: str
    target_video_path: Optional[str] = None
    species_used: List[str] = field(default_factory=list)
    capture_date: Optional[datetime] = None
    success: bool = False
    skipped: bool = False  # V4.3.0: 已归类视频被幂等跳过 / already-organized, idempotent skip
    error: Optional[str] = None


# ============================================================================
# 鸟种聚合 / Species aggregation
# ============================================================================

def aggregate_species_by_duration(
    segments: List[BirdSegment],
) -> List[Tuple[str, str, float]]:
    """
    把段列表按鸟种聚合，输出 [(中文名, 英文名, 总秒数), ...] 按时长降序

    多段同鸟种合并（按中文名归并，同时保留英文名）；空 species_zh 的段忽略；无鸟段忽略。
    同时返回中英双名，便于调用方按界面语言决定文件夹名/文件名前缀。

    参数:
        segments (List[BirdSegment]): 段列表

    返回:
        List[Tuple[str, str, float]]: [(species_zh, species_en, total_sec), ...]，按时长降序

    Aggregate segments by species, returning [(zh, en, total_sec), ...] sorted desc.
    Both names are returned so the caller can pick per UI language.
    """
    totals: dict[str, float] = {}
    en_map: dict[str, str] = {}
    for s in segments:
        if not s.has_bird or not s.species_zh:
            continue
        totals[s.species_zh] = totals.get(s.species_zh, 0.0) + s.duration_sec
        # 记录该鸟种的英文名（同名段英文一致，后到不覆盖已有非空值）
        # Record English name for this species (consistent across same-species segments).
        if s.species_zh not in en_map and s.species_en:
            en_map[s.species_zh] = s.species_en
    ordered = sorted(totals.items(), key=lambda kv: -kv[1])
    return [(zh, en_map.get(zh, ""), dur) for zh, dur in ordered]


# ============================================================================
# 拍摄日期提取 / Capture date extraction
# ============================================================================

# 多个候选的 ExifTool tag，按优先级排列
# Ordered ExifTool tags by priority for video capture date
_DATE_TAGS = (
    'CreationDate',
    'CreateDate',
    'MediaCreateDate',
    'DateTimeOriginal',
)


def get_video_capture_date(video_path: str,
                           exiftool_path: Optional[str] = None) -> datetime:
    """
    提取视频拍摄日期

    优先级：
        1. ExifTool 读 CreationDate / CreateDate / MediaCreateDate / DateTimeOriginal
        2. 文件修改时间 (mtime)
        3. 当前时间（兜底）

    参数:
        video_path (str): 视频文件绝对路径
        exiftool_path (Optional[str]): ExifTool 二进制路径；None 自动定位

    返回:
        datetime: 拍摄日期

    Extract video capture date with ExifTool → mtime → now fallback chain.
    """
    if exiftool_path is None:
        exiftool_path = _locate_exiftool()

    if exiftool_path and os.path.exists(exiftool_path):
        dt = _read_date_with_exiftool(exiftool_path, video_path)
        if dt is not None:
            return dt

    # Fallback 1: 文件修改时间 / mtime
    try:
        return datetime.fromtimestamp(os.path.getmtime(video_path))
    except Exception:
        pass

    # Fallback 2: 当前时间 / now
    return datetime.now()


def _read_date_with_exiftool(exiftool_path: str, video_path: str) -> Optional[datetime]:
    """
    用 ExifTool 读视频的日期 tag，返回第一个能 parse 的 datetime

    Return the first parseable datetime among candidate ExifTool date tags.
    """
    tag_args: List[str] = [f"-{t}" for t in _DATE_TAGS]
    cmd = [exiftool_path, '-s3', '-d', '%Y-%m-%d %H:%M:%S', *tag_args, video_path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, encoding='utf-8'
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return datetime.strptime(line, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return None


def _locate_exiftool() -> Optional[str]:
    """
    定位 ExifTool 二进制路径

    优先复用项目里的 ExifToolManager 单例，避免重复探测。

    Locate ExifTool binary (preferring the project's ExifToolManager singleton).
    """
    try:
        from tools.exiftool_manager import get_exiftool_manager
        mgr = get_exiftool_manager()
        if mgr.exiftool_path and os.path.exists(mgr.exiftool_path):
            return mgr.exiftool_path
    except Exception:
        pass
    # Fallback: 系统 PATH 中找 / Fall back to PATH lookup
    for candidate in ('exiftool', 'exiftool.exe'):
        path = shutil.which(candidate)
        if path:
            return path
    return None


# ============================================================================
# 文件名 / Filename builder
# ============================================================================

# 文件系统不安全字符（macOS / Windows 通用保守集合）
# Reserved filesystem characters (conservative cross-platform set)
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_path_component(name: str, max_len: int = 60) -> str:
    """
    清理文件名/目录名中的非法字符，限制长度

    Sanitize a single path component (filename or dirname).
    """
    cleaned = _UNSAFE_CHARS.sub('_', name).strip()
    cleaned = cleaned.replace('\n', ' ').replace('\r', ' ')
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or '_'


def build_target_filename(source_path: str, species_list: List[str],
                          has_bird: bool,
                          capture_date: Optional[datetime],
                          no_bird_name: str = "无鸟",
                          other_species_name: str = "其他鸟") -> str:
    """
    构建目标文件名（不含目录，含扩展名）

    格式：
        {鸟种前缀}_{拍摄日期8位}_{原始文件名}.{扩展名}

    鸟种前缀按情况：
        - 识别到鸟种         → 主鸟种_次鸟种_...（最多 5 个）
        - 有鸟但识别失败     → other_species_name（默认「其他鸟」）
        - 完全无鸟           → no_bird_name（默认「无鸟」）

    Build target filename based on species / has_bird state.
    """
    base = os.path.basename(source_path)
    stem, ext = os.path.splitext(base)

    if species_list:
        species_part = "_".join(sanitize_path_component(s) for s in species_list[:5])
    elif has_bird:
        # 检测到鸟但鸟种识别失败 / Bird detected but species ID failed
        species_part = sanitize_path_component(other_species_name)
    else:
        species_part = sanitize_path_component(no_bird_name)

    if capture_date is not None:
        date_part = capture_date.strftime('%Y%m%d')
    else:
        date_part = ''

    parts = [species_part]
    if date_part:
        parts.append(date_part)
    parts.append(stem)
    return f"{'_'.join(parts)}{ext}"


def build_target_directory(source_path: str, options: OrganizeOptions,
                           species_list: List[str],
                           has_bird: bool) -> Path:
    """
    构建目标目录路径（直接在源视频父目录创建子目录）

    路径结构（视频端无星级、无中间目录，直接按鸟种分子目录）：
        - 识别到鸟种    → {源目录}/{主鸟种}/
        - 有鸟无种类    → {源目录}/{other_species_folder}/
        - 完全无鸟      → {源目录}/{no_bird_folder}/

    Build target directory directly under the source parent (no wrapper subdir).
    """
    source_parent = Path(source_path).parent

    if species_list:
        return source_parent / sanitize_path_component(species_list[0])
    if has_bird:
        return source_parent / options.other_species_folder
    return source_parent / options.no_bird_folder


def resolve_name_conflict(target_dir: Path, filename: str) -> Path:
    """
    解决同名冲突：若 target_dir/filename 已存在，自动加 _2 / _3 ... 后缀

    Return a non-conflicting path, suffixing _2, _3, ... if needed.
    """
    target_path = target_dir / filename
    if not target_path.exists():
        return target_path
    stem, ext = os.path.splitext(filename)
    counter = 2
    while True:
        candidate = target_dir / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


# ============================================================================
# 主整理流程 / Main organize flow
# ============================================================================

class VideoOrganizer:
    """
    视频整理引擎：把 VideoAnalysisResult 落地到磁盘

    Video result organizer: persists VideoAnalysisResult to disk.
    """

    def __init__(self, options: Optional[OrganizeOptions] = None,
                 exiftool_path: Optional[str] = None):
        """
        参数:
            options (Optional[OrganizeOptions]): 整理选项；None 使用默认
            exiftool_path (Optional[str]): ExifTool 二进制路径；None 自动定位

        Parameters:
            options: organize options; None uses defaults
            exiftool_path: ExifTool binary path; None auto-locates
        """
        self.options = options or OrganizeOptions()
        self.exiftool_path = exiftool_path or _locate_exiftool()

    def organize(self, video_path: str,
                 segments: List[BirdSegment]) -> OrganizeResult:
        """
        整理单个视频：聚合鸟种 → 提取日期 → 构建路径 → 移动

        参数:
            video_path (str): 源视频路径
            segments (List[BirdSegment]): VideoAnalyzer 输出的段列表

        返回:
            OrganizeResult: 整理结果（含错误信息，便于 UI 显示）

        Organize one video: aggregate species → extract date → build path
        → move/copy.
        """
        result = OrganizeResult(source_path=video_path)
        try:
            # 1. 聚合鸟种（中英双名），按界面语言决定落地用的鸟种名
            # Aggregate species (zh + en); pick display name per UI language.
            species_with_dur = aggregate_species_by_duration(segments)
            use_en = self.options.use_english
            # 落地用的鸟种名列表（英文模式优先英文，缺失回退中文；中文模式反之）
            # Names used for folder/filename (en-first in English mode, zh-first otherwise).
            species_list = [
                (en or zh) if use_en else (zh or en)
                for zh, en, _ in species_with_dur
            ]
            result.species_used = species_list
            has_bird = any(s.has_bird for s in segments)

            # V4.3.0: 幂等守卫——若视频已归类（已在对应鸟种目录里，且文件名已带该前缀），
            # 直接跳过，避免反复归类导致目录逐层嵌套 + 文件名前缀叠加（见 Bird-Video-P3 事故）。
            # 关键：文件夹名/前缀跟随界面语言，故守卫需同时匹配中、英两种命名，
            # 否则「中文下归类、英文下重跑」会匹配不上而再次嵌套。
            # V4.3.0: Idempotency guard — skip already-organized videos. Since folder/prefix
            # names follow the UI language, the guard must match BOTH the zh and en variants,
            # or a video organized under one language would be re-nested under the other.
            if species_with_dur:
                top_zh, top_en, _ = species_with_dur[0]
                folder_candidates = {
                    sanitize_path_component(top_zh),
                    sanitize_path_component(top_en or top_zh),
                }
            elif has_bird:
                folder_candidates = {
                    sanitize_path_component(a) for a in OTHER_SPECIES_FOLDER_ALIASES
                }
            else:
                folder_candidates = {
                    sanitize_path_component(a) for a in NO_BIRD_FOLDER_ALIASES
                }
            source_p = Path(video_path)
            if source_p.parent.name in folder_candidates and any(
                source_p.name.startswith(fc + "_") for fc in folder_candidates
            ):
                result.target_video_path = video_path
                result.skipped = True
                result.success = True
                return result

            # 2. 提取拍摄日期
            capture_date = get_video_capture_date(video_path, self.exiftool_path)
            result.capture_date = capture_date

            # 3. 构建目标路径（无星级，按鸟种 / 其他鸟 / 无鸟 分目录）
            target_dir = build_target_directory(
                video_path, self.options, species_list, has_bird)
            target_filename = build_target_filename(
                video_path, species_list, has_bird, capture_date,
                no_bird_name=self.options.no_bird_folder,
                other_species_name=self.options.other_species_folder,
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            final_target = resolve_name_conflict(target_dir, target_filename)

            # 4. 移动 / 复制视频
            source_p = Path(video_path)
            if self.options.operation == 'move':
                shutil.move(str(source_p), str(final_target))
            else:
                shutil.copy2(str(source_p), str(final_target))
            result.target_video_path = str(final_target)

            result.success = True
            return result

        except Exception as e:
            result.success = False
            result.error = str(e)
            return result


# ============================================================================
# 归类清单 / Organize manifest（用于「复原」可逆地撤销整理）
# Organize manifest — records each move so a reset can undo the organization.
# ============================================================================

# 清单文件名（隐藏文件，与照片端 .superpicky_* 命名风格一致）
# Manifest filename (hidden file, consistent with the photo-side .superpicky_* naming).
VIDEO_MANIFEST_NAME = ".superpicky_video_manifest.json"
_VIDEO_MANIFEST_VERSION = 1


def record_organized_results(results: List[OrganizeResult]) -> List[str]:
    """
    把一次整理（move）的结果写成「归类清单」，供后续「复原」精确撤销。

    清单按【原始视频所在目录】分组写入，每个目录一个 VIDEO_MANIFEST_NAME 文件，
    记录「原始路径 → 落地视频路径」。已存在清单则合并去重（按原始路径）。

    仅记录真正发生移动的条目（source_path 与落地路径不同、未被幂等跳过、成功）。
    copy 模式不应调用本函数（原文件仍在，复原会重复）。

    参数:
        results (List[OrganizeResult]): VideoOrganizer.organize 的结果列表

    返回:
        List[str]: 实际写入/更新的清单文件路径列表

    Persist a move-organize batch as a manifest so a later reset can undo it.
    Manifests are grouped by the original directory of each source video.
    """
    by_dir: dict[str, List[dict]] = {}
    for r in results:
        if not r.success or r.skipped or not r.target_video_path:
            continue
        if os.path.abspath(r.source_path) == os.path.abspath(r.target_video_path):
            continue  # 没有实际移动 / no real move
        src_dir = os.path.dirname(os.path.abspath(r.source_path))
        by_dir.setdefault(src_dir, []).append({
            "original": os.path.abspath(r.source_path),
            "video": os.path.abspath(r.target_video_path),
        })

    written: List[str] = []
    for src_dir, entries in by_dir.items():
        manifest_path = os.path.join(src_dir, VIDEO_MANIFEST_NAME)
        existing: List[dict] = []
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                existing = data.get("entries", []) if isinstance(data, dict) else []
            except Exception:
                existing = []
        # 按 original 去重合并（新条目覆盖旧）/ merge-dedupe by original (new wins)
        merged: dict[str, dict] = {e.get("original"): e for e in existing if e.get("original")}
        for e in entries:
            merged[e["original"]] = e
        payload = {
            "version": _VIDEO_MANIFEST_VERSION,
            "entries": list(merged.values()),
        }
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            written.append(manifest_path)
        except Exception:
            # 清单写失败不影响整理本身 / manifest failure must not break organizing
            pass
    return written


def restore_organized_videos(directory: str, log=None) -> dict:
    """
    根据「归类清单」把视频复原到整理前的位置（撤销 organize）。

    步骤：
        1. 读取 directory 下的 VIDEO_MANIFEST_NAME
        2. 逐条把落地视频移回原始路径（原始已存在则跳过、不覆盖）
        3. 删除对应 SRT 字幕
        4. 删除因此清空的鸟种子目录
        5. 删除清单文件

    参数:
        directory (str): 包含归类清单的目录（即整理前视频所在目录）
        log (callable | None): 可选日志回调

    返回:
        dict: {'manifest': bool, 'restored': int, 'missing': int, 'srt_removed': int,
               'dirs_removed': int}

    Restore organized videos to their pre-organize locations using the manifest.
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    stats = {"manifest": False, "restored": 0, "missing": 0,
             "srt_removed": 0, "dirs_removed": 0}

    manifest_path = os.path.join(directory, VIDEO_MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        return stats
    stats["manifest"] = True

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries", []) if isinstance(data, dict) else []
    except Exception as e:
        _log(f"  ❌ 读取视频归类清单失败 / failed to read video manifest: {e}")
        return stats

    target_dirs: set[str] = set()
    for entry in entries:
        original = entry.get("original")
        video = entry.get("video")
        srt = entry.get("srt")
        if not original or not video:
            continue
        if video:
            target_dirs.add(os.path.dirname(video))
        # 1. 移回原位 / move back
        if os.path.exists(video):
            try:
                os.makedirs(os.path.dirname(original), exist_ok=True)
                if os.path.exists(original):
                    # 原始路径已被占用，保守跳过不覆盖 / don't overwrite an existing original
                    _log(f"  ⚠️  原位已存在，跳过 / original exists, skipped: {original}")
                else:
                    shutil.move(video, original)
                    stats["restored"] += 1
            except Exception as e:
                _log(f"  ❌ 复原失败 / restore failed {os.path.basename(video)}: {e}")
        else:
            stats["missing"] += 1
        # 2. 删 SRT / delete SRT
        if srt and os.path.exists(srt):
            try:
                os.remove(srt)
                stats["srt_removed"] += 1
            except Exception:
                pass

    # 3. 删除清空的鸟种子目录 / remove now-empty species folders
    for d in sorted(target_dirs, key=len, reverse=True):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                stats["dirs_removed"] += 1
        except Exception:
            pass

    # 4. 删除清单 / remove the manifest
    try:
        os.remove(manifest_path)
    except Exception:
        pass

    return stats
