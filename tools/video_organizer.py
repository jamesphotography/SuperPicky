#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 3 — 视频分析结果整理器

把 VideoAnalyzer 的分析结果落地到磁盘：
    - 按主鸟种归类（有鸟视频 → 3星_有鸟/{主鸟种}/  / 无鸟视频 → 0星_无鸟/）
    - 按鸟种 + 拍摄日期重命名视频文件
    - 同时把 SRT 字幕文件写到同目录同名

设计原则：
    - 纯函数 + 单一职责，便于单测和复用
    - 不直接依赖 PySide6，调用方在 worker thread 内执行
    - 默认移动（与照片端一致），可改复制
    - 命名冲突自动加 _2/_3 后缀（不覆盖现有文件）

Video analysis result organizer for Phase 3.
Moves/copies videos into species-named subdirectories with renamed filenames,
plus SRT subtitles alongside.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from core.video_segment import BirdSegment, write_srt


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass(slots=True)
class OrganizeOptions:
    """
    整理选项 / Organize options

    视频端不用星级（与照片端不同），直接在源视频父目录创建鸟种子目录：
        - 识别到具体鸟种 → 源目录/{鸟种}/{鸟种}_{日期}_{原名}.ext
        - 有鸟但识别失败 → 源目录/{other_species_folder}/其他鸟_{日期}_{原名}.ext
        - 完全无鸟       → 源目录/{no_bird_folder}/无鸟_{日期}_{原名}.ext

    operation              : 'move'（默认）或 'copy'
    no_bird_folder         : 无鸟视频的目录名 / 命名前缀，默认 "无鸟"
    other_species_folder   : 有鸟但识别失败的目录名 / 命名前缀，默认 "其他鸟"

    Videos are organized directly under the source parent dir, by species.
    """
    operation: Literal['move', 'copy'] = 'move'
    no_bird_folder: str = "无鸟"
    other_species_folder: str = "其他鸟"


@dataclass(slots=True)
class OrganizeResult:
    """单视频整理结果 / Result of organizing a single video"""
    source_path: str
    target_video_path: Optional[str] = None
    target_srt_path: Optional[str] = None
    species_used: List[str] = field(default_factory=list)
    capture_date: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None


# ============================================================================
# 鸟种聚合 / Species aggregation
# ============================================================================

def aggregate_species_by_duration(segments: List[BirdSegment]) -> List[Tuple[str, float]]:
    """
    把段列表按鸟种聚合，输出 [(中文名, 总秒数), ...] 按时长降序

    多段同鸟种合并；空 species_zh 的段忽略；无鸟段忽略。

    参数:
        segments (List[BirdSegment]): 段列表

    返回:
        List[Tuple[str, float]]: [(species_zh, total_sec), ...]，降序

    Aggregate segments by species, returning [(zh_name, total_sec), ...] sorted desc.
    """
    totals: dict[str, float] = {}
    for s in segments:
        if not s.has_bird or not s.species_zh:
            continue
        totals[s.species_zh] = totals.get(s.species_zh, 0.0) + s.duration_sec
    return sorted(totals.items(), key=lambda kv: -kv[1])


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
        整理单个视频：聚合鸟种 → 提取日期 → 构建路径 → 移动 → 写 SRT

        参数:
            video_path (str): 源视频路径
            segments (List[BirdSegment]): VideoAnalyzer 输出的段列表

        返回:
            OrganizeResult: 整理结果（含错误信息，便于 UI 显示）

        Organize one video: aggregate species → extract date → build path
        → move/copy → write SRT.
        """
        result = OrganizeResult(source_path=video_path)
        try:
            # 1. 聚合鸟种
            species_with_dur = aggregate_species_by_duration(segments)
            species_list = [name for name, _ in species_with_dur]
            result.species_used = species_list
            has_bird = any(s.has_bird for s in segments)

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

            # 5. 写 SRT 到同目录同名（用 stem 替换扩展名）
            srt_path = final_target.with_suffix('.srt')
            try:
                write_srt(segments, srt_path)
                result.target_srt_path = str(srt_path)
            except Exception as e:
                # SRT 失败不影响视频整理本身 / SRT failure does not fail the whole op
                result.error = f"SRT 写入失败: {e}"

            result.success = True
            return result

        except Exception as e:
            result.success = False
            result.error = str(e)
            return result
