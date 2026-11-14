# -*- coding: utf-8 -*-
"""
结果浏览器按文件名搜索（#166）/ Filename search for the results browser (#166).

Al：想按文件名模式找照片，例如 ``*103*`` 找 ``_T3A0103.CR3``，``*A02*`` 找
``_T3A0200``–``_T3A0299``。

规则：
- 不区分大小写；
- 含 ``*`` 或 ``?`` 时按通配符匹配整个文件名（带或不带扩展名都试）；
- 否则按「包含」匹配——直接输 ``103`` 就能找到 ``_T3A0103.CR3``，不必非打星号；
- 空白分隔的多个词是「或」：``0103 0107`` 同时找这两张。

只筛选显示，不写数据库、不动文件。本模块不依赖 Qt，便于单测。

Case-insensitive; with * or ? the pattern is a wildcard over the whole name
(tried with and without the extension), otherwise a substring match, so
typing 103 finds _T3A0103.CR3. Whitespace-separated terms are OR-ed.
Display-only filtering; no Qt dependency.
"""

import fnmatch
import os
from typing import Any, Dict, Iterable, List


def _names(photo: Dict[str, Any]) -> List[str]:
    """
    一张照片可供匹配的名字：当前路径与原始路径的文件名、库里的主名（不带扩展名）。

    The names a photo can match: basenames of its current and original paths
    and the DB stem.
    """
    names = []
    for key in ("current_path", "original_path"):
        value = photo.get(key)
        if value:
            names.append(os.path.basename(str(value)))
    stem = photo.get("filename")
    if stem:
        names.append(str(stem))
    out = []
    for name in names:
        low = name.lower()
        if low and low not in out:
            out.append(low)
            root, ext = os.path.splitext(low)
            if ext and root not in out:
                out.append(root)
    return out


def _term_matches(term: str, names: Iterable[str]) -> bool:
    """单个词是否匹配任一名字 / whether one term matches any of the names."""
    if "*" in term or "?" in term:
        return any(fnmatch.fnmatchcase(name, term) for name in names)
    return any(term in name for name in names)


def filename_matches(pattern: str, photo: Dict[str, Any]) -> bool:
    """
    照片是否匹配搜索框内容。

    参数:
    pattern (str): 搜索框文字；空串表示不过滤
    photo (Dict[str, Any]): 照片记录

    返回:
    bool: 匹配（或没有搜索词）为 True

    Whether the photo matches the search text; an empty search matches all.
    """
    terms = [t for t in (pattern or "").lower().split() if t]
    if not terms:
        return True
    names = _names(photo)
    return any(_term_matches(term, names) for term in terms)


def filter_by_filename(photos: List[Dict[str, Any]], pattern: str) -> List[Dict[str, Any]]:
    """
    按文件名搜索过滤照片列表（保持原顺序）；没有搜索词时原样返回。

    参数:
    photos (List[Dict[str, Any]]): 照片列表
    pattern (str): 搜索框文字

    返回:
    List[Dict[str, Any]]: 匹配的照片

    Filter photos by the search text, keeping order; unchanged when empty.
    """
    if not (pattern or "").strip():
        return photos
    return [p for p in photos if filename_matches(pattern, p)]
