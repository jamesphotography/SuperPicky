#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
递归目录扫描器
扫描根目录下的所有子目录，识别出包含照片的"原子目录"，
排除星级目录、隐藏目录、连拍目录等 SuperPicky 产物。
"""

import os
import platform
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import List, Optional, Set, Tuple

from constants import (
    HEIF_EXTENSIONS,
    JPG_EXTENSIONS,
    RATING_FOLDER_NAMES,
    RATING_FOLDER_NAMES_EN,
    RAW_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

# 所有照片扩展名（小写）
_PHOTO_EXTENSIONS: Set[str] = set(RAW_EXTENSIONS + JPG_EXTENSIONS + HEIF_EXTENSIONS)

# V4.3 Phase 4: 所有视频扩展名（小写）
# V4.3 Phase 4: All video extensions (lowercase)
_VIDEO_EXTENSIONS: Set[str] = set(VIDEO_EXTENSIONS)

# 星级目录名（中 + 英）
_RATING_DIR_NAMES: Set[str] = set(RATING_FOLDER_NAMES.values()) | set(RATING_FOLDER_NAMES_EN.values())

# V4.3 Phase 4: 视频归类后产生的目录名（防止扫描进入避免循环扫到新生成视频）
# V4.3 Phase 4: Video sub-folder names produced by Phase 3 organizer.
_VIDEO_SUBFOLDER_NAMES: Set[str] = {"其他鸟", "无鸟"}

DEFAULT_SCAN_MAX_DEPTH = 16


@dataclass(frozen=True)
class ScannedDirectory:
    """
    扫描结果条目

    V4.3 Phase 4: 新增 video_count + video_files 字段以支持视频处理。
    """

    path: str
    depth: int
    photo_count: int
    # V4.3 Phase 4: 视频文件数和路径列表（默认 0/空，保持向后兼容）
    # V4.3 Phase 4: Video file count + absolute paths (defaults preserve compatibility)
    video_count: int = 0
    video_files: Tuple[str, ...] = ()


def is_excluded(dirname: str) -> bool:
    """
    判断目录是否应被排除（非用户照片/视频目录）

    V4.3 Phase 4: 扩展排除 Phase 3 视频整理产生的「其他鸟」「无鸟」目录，
    避免再次扫描时把已归类的视频又当成原始素材处理。
    """
    if dirname.startswith('.'):
        return True
    if dirname.startswith('burst_'):
        return True
    if dirname in _RATING_DIR_NAMES:
        return True
    if dirname in _VIDEO_SUBFOLDER_NAMES:
        return True
    if dirname in ('__pycache__', 'node_modules'):
        return True
    return False


def _scan_directory_once(dir_path: str) -> Tuple[int, List[str], List[str]]:
    """
    单次扫描目录

    V4.3 Phase 4: 同时返回照片数量、视频文件路径列表、子目录列表

    Returns:
        (photo_count, video_files, child_dirs)
        - photo_count: 直接照片数量
        - video_files: 直接视频文件绝对路径列表
        - child_dirs: 可继续扫描的子目录绝对路径列表
    """
    photo_count = 0
    video_files: List[str] = []
    child_dirs: List[str] = []

    try:
        with os.scandir(dir_path) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in _PHOTO_EXTENSIONS:
                        photo_count += 1
                    elif ext in _VIDEO_EXTENSIONS:
                        video_files.append(entry.path)
                    continue

                if not entry.is_dir(follow_symlinks=False):
                    continue
                if is_excluded(entry.name):
                    continue

                child_dirs.append(entry.path)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return 0, [], []

    video_files.sort(key=lambda value: os.path.basename(value).casefold())
    child_dirs.sort(key=lambda value: os.path.basename(value).casefold())
    return photo_count, video_files, child_dirs


def _scan_directories_dfs(root: str, max_depth: int) -> List[ScannedDirectory]:
    """
    V4.3 Phase 4: 同时收集照片和视频。
    保留原有逻辑「只把有照片的目录加进结果」，但额外附带该目录下的视频。
    目录如果只有视频没照片也会被加进结果（让用户处理纯视频目录）。
    """
    root = os.path.abspath(root)
    if max_depth < 0:
        return []

    result: List[ScannedDirectory] = []
    stack: List[Tuple[str, int]] = [(root, 0)]
    while stack:
        dir_path, depth = stack.pop()
        photo_count, video_files, child_dirs = _scan_directory_once(dir_path)
        # 只要有照片或视频之一，就计入结果 / Include if has photos OR videos
        if photo_count > 0 or video_files:
            result.append(ScannedDirectory(
                path=dir_path,
                depth=depth,
                photo_count=photo_count,
                video_count=len(video_files),
                video_files=tuple(video_files),
            ))
        if depth >= max_depth:
            continue
        for child_dir in reversed(child_dirs):
            stack.append((child_dir, depth + 1))
    result.sort(key=lambda item: item.path.casefold())
    return result


def _is_windows_path(path: str) -> bool:
    drive, _ = os.path.splitdrive(path)
    return bool(drive) or "\\" in path


def _is_subpath(candidate_parts: Tuple[str, ...], protected_parts: Tuple[str, ...]) -> bool:
    if len(candidate_parts) < len(protected_parts):
        return False
    return candidate_parts[:len(protected_parts)] == protected_parts


def is_dangerous_root(
    root: str,
    platform_name: Optional[str] = None,
    home_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    """判断根目录是否属于危险目录。"""
    platform_name = (platform_name or platform.system()).lower()
    home_dir = os.path.expanduser(home_dir or "~")

    if platform_name.startswith("win") or _is_windows_path(root):
        normalized = str(PureWindowsPath(os.path.realpath(os.path.abspath(root))))
        root_path = PureWindowsPath(normalized)
        anchor = root_path.anchor.rstrip("\\/")
        current = normalized.rstrip("\\/")
        if anchor and current.lower() == anchor.lower():
            return True, "磁盘根目录 / Drive root"

        protected_paths = [
            PureWindowsPath(os.path.realpath(os.environ.get("SystemRoot", "C:\\Windows"))),
            PureWindowsPath(os.path.realpath("C:\\Program Files")),
            PureWindowsPath(os.path.realpath("C:\\Program Files (x86)")),
            PureWindowsPath(os.path.realpath(os.path.join(home_dir, "AppData"))),
        ]
        root_parts = tuple(part.casefold() for part in root_path.parts)
        for protected in protected_paths:
            protected_parts = tuple(part.casefold() for part in protected.parts)
            if _is_subpath(root_parts, protected_parts):
                return True, f"受保护的系统或设置目录 / Protected path: {protected}"
        return False, ""

    normalized = str(PurePosixPath(os.path.realpath(os.path.abspath(root))))
    root_path = PurePosixPath(normalized)
    root_parts = tuple(root_path.parts)

    # 严格相等判断：仅当路径恰好是文件系统根目录时才阻断。
    # 不能将 "/" 放入 protected_paths 用 _is_subpath() 做前缀匹配，
    # 因为所有绝对路径的第一个 part 均为 "/"，会导致误判一切目录。
    # Strict equality check: only block when the path is exactly the filesystem root.
    # Do NOT put "/" into protected_paths for _is_subpath() prefix matching,
    # because every absolute path starts with "/" as its first part,
    # which would cause all directories to be falsely flagged.
    if normalized == "/":
        return True, "文件系统根目录 / Filesystem root"

    # 对受保护路径同样执行 realpath 解析，以处理符号链接。
    # 例如 macOS 上 /etc -> /private/etc，/var -> /private/var，
    # 若不解析则与已 realpath 处理的 normalized 无法匹配。
    # Resolve protected paths with realpath too, to handle symlinks.
    # e.g. on macOS: /etc -> /private/etc, /var -> /private/var.
    _raw_protected = [
        "/usr",
        "/etc",
        "/var",
        "/System",
        "/Library",
        os.path.join(home_dir, "Library"),
    ]
    protected_paths = [
        PurePosixPath(os.path.realpath(p)) for p in _raw_protected
    ]
    for protected in protected_paths:
        protected_parts = tuple(protected.parts)
        if _is_subpath(root_parts, protected_parts):
            return True, f"受保护的系统或设置目录 / Protected path: {protected}"
    if normalized in ("/home", os.path.realpath("/home")):
        return True, "系统用户根目录 / System user root"
    return False, ""


def has_photos(dir_path: str) -> bool:
    """判断目录是否直接包含至少 1 个照片文件"""
    photo_count, _video_files, _child_dirs = _scan_directory_once(dir_path)
    return photo_count > 0


def has_videos(dir_path: str) -> bool:
    """
    V4.3 Phase 4: 判断目录是否直接包含至少 1 个视频文件

    Phase 4: check if a directory directly contains at least one video file.
    """
    _photo_count, video_files, _child_dirs = _scan_directory_once(dir_path)
    return bool(video_files)


def is_processed(dir_path: str) -> bool:
    """判断目录是否已被 SuperPicky 处理过（存在 report.db）"""
    return os.path.exists(os.path.join(dir_path, '.superpicky', 'report.db'))


def scan_directories(
    root: str,
    max_depth: int = DEFAULT_SCAN_MAX_DEPTH,
) -> List[ScannedDirectory]:
    """扫描根目录，返回包含照片的目录摘要列表。"""
    return _scan_directories_dfs(root, max_depth)


def scan_dfs(root: str, max_depth: int = DEFAULT_SCAN_MAX_DEPTH) -> List[ScannedDirectory]:
    """使用 DFS 扫描根目录。"""
    return scan_directories(root, max_depth=max_depth)


def scan_recursive(root: str, max_depth: int = DEFAULT_SCAN_MAX_DEPTH) -> List[str]:
    """
    递归扫描根目录，返回所有原子目录（包含照片的非排除目录）的绝对路径列表。
    
    Args:
        root: 根目录路径
        max_depth: 最大递归深度（默认 16）
        
    Returns:
        原子目录绝对路径列表，按字母排序
    """
    return [item.path for item in scan_dfs(root, max_depth=max_depth)]


def count_photos(dir_path: str) -> int:
    """统计目录中直接包含的照片文件数量"""
    count, _video_files, _child_dirs = _scan_directory_once(dir_path)
    return count


def count_videos(dir_path: str) -> int:
    """V4.3 Phase 4: 统计目录中直接包含的视频文件数量"""
    _photo_count, video_files, _child_dirs = _scan_directory_once(dir_path)
    return len(video_files)
