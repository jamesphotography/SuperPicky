# -*- coding: utf-8 -*-
"""
整理目录的空目录修剪 / Pruning empty folders left behind by organizing.

照片在鸟种 / 星级 / 连拍目录之间搬动后，搬空的目录应当删掉。此前的做法是
「目录里一样东西都没有才删」，实测（2026-10-03，exFAT 外接盘）有两种情况会挡住它：

1. 同级的空目录：处理时照片先按单张星级放进「鸟种/2星_良好」，连拍整理再把整组
   搬进「鸟种/3星_优选/burst_014」，2星_良好 就此空着；之后整种合并把 burst 搬走，
   往上清理到「鸟种」时，里面还剩那个空的 2星_良好，于是判定非空、停下。
2. 系统伴生文件：macOS 在 exFAT 等非原生文件系统上给每个文件 / 目录配一个
   「._名字」AppleDouble 文件（一个目录里实测 321 个）；Finder 浏览过会留 .DS_Store；
   Windows 留 Thumbs.db / desktop.ini。

本模块把「空」定义为：只剩系统垃圾文件、孤儿 ._ 伴生文件（对应的本体已不在）、
以及同样「空」的子目录。只删这几类文件，其它任何文件一概不碰；目录一律用
os.rmdir 删除——它本身拒绝删非空目录，是最后一道保险。处理根目录与以点开头的
目录（.superpicky 等）永远不删，符号链接不跟随。

Folders are "empty" when they hold only OS junk, orphan AppleDouble files and
equally empty subfolders. Only those files are ever deleted, directories go
through os.rmdir (which refuses non-empty ones), and the root and dot-folders
are never touched.
"""
from __future__ import annotations

import os
from typing import Iterable, List, Optional, Set

#: 可以随目录一起删掉的系统垃圾文件 / OS junk files safe to delete with a folder
JUNK_FILES: Set[str] = {".DS_Store", "Thumbs.db", "desktop.ini"}

#: macOS 在非原生文件系统上为每个条目生成的伴生文件前缀 / AppleDouble prefix
APPLEDOUBLE_PREFIX = "._"


def _is_disposable(name: str, siblings: Set[str]) -> bool:
    """
    这个文件能否随目录一起删：系统垃圾文件，或本体已不存在的 ._ 伴生文件。

    参数:
    name (str): 文件名
    siblings (Set[str]): 同目录下现存的全部条目名

    返回:
    bool: 可删时为 True

    Whether a file is junk or an orphan AppleDouble companion.
    """
    if name in JUNK_FILES:
        return True
    if name.startswith(APPLEDOUBLE_PREFIX) and len(name) > len(APPLEDOUBLE_PREFIX):
        return name[len(APPLEDOUBLE_PREFIX):] not in siblings
    return False


def _remove_companion(path: str) -> None:
    """
    目录删掉后，顺手删父目录里它的 ._ 伴生文件（只删这一个，删不掉就算了）。

    参数:
    path (str): 刚删掉的目录

    Remove the deleted folder's own AppleDouble companion in its parent.
    """
    companion = os.path.join(os.path.dirname(path),
                             APPLEDOUBLE_PREFIX + os.path.basename(path))
    try:
        if os.path.isfile(companion) and not os.path.islink(companion):
            os.remove(companion)
    except OSError:
        pass


def prune_tree(path: str) -> bool:
    """
    自底向上修剪 path：先修剪子目录，再看 path 自己是否已「空」，空则删掉。

    参数:
    path (str): 要修剪的目录

    返回:
    bool: path 本身被删掉时为 True

    Prune ``path`` bottom-up; returns True when ``path`` itself was removed.
    """
    if os.path.islink(path) or not os.path.isdir(path):
        return False
    if os.path.basename(path).startswith("."):
        return False                      # .superpicky 等隐藏目录永远保留
    try:
        entries = os.listdir(path)
    except OSError:
        return False

    for name in entries:
        child = os.path.join(path, name)
        if os.path.isdir(child) and not os.path.islink(child):
            prune_tree(child)

    try:
        remaining = os.listdir(path)
    except OSError:
        return False
    names = set(remaining)
    for name in remaining:
        child = os.path.join(path, name)
        if os.path.isdir(child) or os.path.islink(child) or not _is_disposable(name, names):
            return False                  # 还有真东西：保留 / real content remains
    for name in remaining:
        try:
            os.remove(os.path.join(path, name))
        except OSError:
            return False
    try:
        os.rmdir(path)
    except OSError:
        return False
    _remove_companion(path)
    return True


def prune_upwards(root: str, start: str) -> None:
    """
    从 start 开始逐级向上修剪，直到 root（不含）或遇到删不掉的目录为止。

    每一级都整棵修剪：上一级里同级的空目录（如被搬空的 2星_良好）也会一并清掉，
    不会再因为它把整条链挡住。

    参数:
    root (str): 处理根目录，永远不删
    start (str): 刚搬走文件的目录

    Prune from ``start`` up to (not including) ``root``, sweeping empty siblings
    at every level so they no longer block the chain.
    """
    root_n = os.path.normpath(os.path.abspath(root))
    current = os.path.normpath(os.path.abspath(start))
    while current != root_n and current.startswith(root_n + os.sep):
        if not prune_tree(current):
            break
        current = os.path.dirname(current)


def prune_organized_folders(root: str, folder_names: Iterable[str],
                            max_depth: int = 3) -> int:
    """
    处理收尾时扫一遍整理出来的目录：星级目录与 burst_ 目录及其上级，空了就删。

    只从「我们自己建的目录」（星级目录名、burst_ 前缀）出发向上修剪，用户自己在
    根目录下建的空文件夹不会被碰到。

    参数:
    root (str): 处理根目录
    folder_names (Iterable[str]): 星级目录名（中英文都要传）
    max_depth (int): 向下找星级 / 连拍目录的最大层数（鸟种/星级/burst 共三层）

    返回:
    int: 删掉的目录数

    End-of-run sweep starting only from folders the organizer creates (rating
    and burst_ folders), so user-made empty folders are never touched.
    """
    names = set(folder_names)
    root_n = os.path.normpath(os.path.abspath(root))
    candidates: List[str] = []
    for dirpath, dirnames, _files in os.walk(root_n):
        depth = 0 if dirpath == root_n else os.path.relpath(dirpath, root_n).count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if depth >= max_depth:
            dirnames[:] = []
        for d in dirnames:
            if d in names or d.startswith("burst_"):
                candidates.append(os.path.join(dirpath, d))

    before = _count_dirs(root_n)
    # 深的先处理，免得父目录先被判断 / deepest first
    for path in sorted(candidates, key=lambda p: p.count(os.sep), reverse=True):
        if os.path.isdir(path):
            prune_upwards(root_n, path)
    return before - _count_dirs(root_n)


def _count_dirs(root: str) -> int:
    """数 root 下的目录数（不含隐藏目录）/ Count non-hidden folders under root."""
    total = 0
    for _dirpath, dirnames, _files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        total += len(dirnames)
    return total
