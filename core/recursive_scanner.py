#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
递归目录扫描器
扫描根目录下的所有子目录，识别出包含照片的"原子目录"，
排除星级目录、隐藏目录、连拍目录等 SuperPicky 产物。
"""

import os
import sqlite3
from typing import List, Set

from constants import RAW_EXTENSIONS, JPG_EXTENSIONS, HEIF_EXTENSIONS, RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN

# 所有照片扩展名（小写）
_PHOTO_EXTENSIONS: Set[str] = set(RAW_EXTENSIONS + JPG_EXTENSIONS + HEIF_EXTENSIONS)

# 星级目录名（中 + 英）
_RATING_DIR_NAMES: Set[str] = set(RATING_FOLDER_NAMES.values()) | set(RATING_FOLDER_NAMES_EN.values())


def is_excluded(dirname: str) -> bool:
    """判断目录是否应被排除（非用户照片目录）"""
    if dirname.startswith('.'):
        return True
    if dirname.startswith('burst_'):
        return True
    if dirname in _RATING_DIR_NAMES:
        return True
    if dirname in ('__pycache__', 'node_modules'):
        return True
    return False


def has_photos(dir_path: str) -> bool:
    """判断目录是否直接包含至少 1 个照片文件"""
    try:
        for entry in os.scandir(dir_path):
            if entry.is_file(follow_symlinks=False):
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in _PHOTO_EXTENSIONS:
                    return True
    except PermissionError:
        pass
    return False


def is_processed(dir_path: str) -> bool:
    """判断目录是否已被 SuperPicky 处理过（存在 report.db）"""
    db_path = os.path.join(dir_path, '.superpicky', 'report.db')
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'job_status'").fetchone()
            if row and row[0] == 'running':
                return False
        finally:
            conn.close()
    except Exception:
        pass
    return True


def scan_recursive(root: str, max_depth: int = 10) -> List[str]:
    """
    递归扫描根目录，返回所有原子目录（包含照片的非排除目录）的绝对路径列表。
    
    Args:
        root: 根目录路径
        max_depth: 最大递归深度（默认 10）
        
    Returns:
        原子目录绝对路径列表，按字母排序
    """
    result: List[str] = []
    
    # 根目录本身如果包含照片，也加入列表
    if has_photos(root):
        result.append(root)
    
    def _scan(dir_path: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: e.name)
        except PermissionError:
            return
        
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            if is_excluded(entry.name):
                continue
            
            if has_photos(entry.path):
                result.append(entry.path)
            
            # 即使当前目录有照片，也继续扫描子目录
            _scan(entry.path, depth + 1)
    
    _scan(root, 0)
    return result


def count_photos(dir_path: str) -> int:
    """统计目录中直接包含的照片文件数量"""
    count = 0
    try:
        for entry in os.scandir(dir_path):
            if entry.is_file(follow_symlinks=False):
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in _PHOTO_EXTENSIONS:
                    count += 1
    except PermissionError:
        pass
    return count
