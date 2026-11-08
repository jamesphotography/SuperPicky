# -*- coding: utf-8 -*-
"""
多目录选鸟的纯逻辑（无 Qt 依赖）：目录校验、状态分类、顺序队列与结果汇总。

Pure logic for multi-folder culling (no Qt): folder validation, state
classification, a sequential queue and outcome aggregation.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.recursive_scanner import (
    DEFAULT_SCAN_MAX_DEPTH,
    is_dangerous_root,
    scan_directories,
)
from tools.resume_state import ResumeStateManager

REJECT_DUPLICATE = "duplicate"
REJECT_OVERLAP = "overlap"
REJECT_DANGEROUS = "dangerous"
REJECT_NO_PHOTOS = "no_photos"
REJECT_MISSING = "missing"

STATE_FRESH = "fresh"
STATE_RESUME = "resume"
STATE_PROCESSED = "processed"


def normalize_folder(path: str) -> str:
    """
    归一化目录路径（绝对化 + 去除多余分隔符/末尾分隔符）。

    参数: path (str) 原始路径。返回: str 归一化后的绝对路径。

    Normalize a folder path (absolute, no redundant/trailing separators).
    """
    return os.path.normpath(os.path.abspath(path))


def folders_overlap(a: str, b: str, pathmod=os.path) -> bool:
    """
    判断两个目录是否互相包含（相同或父子）。兄弟前缀（/p/ab 与 /p/a）不算；
    跨盘符或绝对/相对混用（commonpath 抛 ValueError）视为不重叠。

    Whether two folders contain one another (equal or parent/child). Sibling
    name prefixes do not count; ValueError from commonpath (different drives,
    mixed absolute/relative) means no overlap.

    参数: a, b (str) 路径；pathmod 路径模块（测试可传 ntpath）。
    返回: bool
    """
    na = pathmod.normcase(pathmod.normpath(a))
    nb = pathmod.normcase(pathmod.normpath(b))
    try:
        return pathmod.commonpath([na, nb]) in (na, nb)
    except ValueError:
        return False


def validate_new_folder(existing: List[str], new: str) -> Optional[str]:
    """
    校验准备加入列表的目录。顺序：不存在 -> 重复 -> 重叠 -> 危险 -> 无照片。
    扫描不到照片、但已处理过（有 report.db）或有续跑进度的目录仍可加入。

    参数: existing 已在列表中的目录；new 新目录。
    返回: None 表示可加入，否则为 REJECT_* 之一。

    Validate a folder before adding. Order: missing, duplicate, overlap,
    dangerous, no photos. A folder with no scannable photos is still accepted
    when it is processed (report.db) or resumable. Returns None if acceptable,
    else a REJECT_* code.
    """
    if not os.path.isdir(new):
        return REJECT_MISSING
    norm_new = normalize_folder(new)
    norm_existing = [normalize_folder(p) for p in existing]
    if any(os.path.normcase(norm_new) == os.path.normcase(p) for p in norm_existing):
        return REJECT_DUPLICATE
    if any(folders_overlap(norm_new, p) for p in norm_existing):
        return REJECT_OVERLAP
    dangerous, _reason = is_dangerous_root(new)
    if dangerous:
        return REJECT_DANGEROUS
    if not scan_directories(new, max_depth=DEFAULT_SCAN_MAX_DEPTH):
        # 已处理（有 report.db）或有续跑进度的目录照片多在星级/鸟种目录里，扫描会
        # 排除这些目录而得 0 张；它们仍须能加入列表，否则「已处理默认跳过 / 全部
        # 重新处理」以及放回列表看结果都走不到。
        # Processed / resumable folders keep photos in excluded rating/species
        # folders, so the scan finds none; they must still be addable.
        if classify_folder(new) != STATE_FRESH:
            return None
        return REJECT_NO_PHOTOS
    return None


def _report_db_exists(path: str) -> bool:
    """目录下是否有 .superpicky/report.db / Whether the folder has a report.db."""
    return os.path.exists(os.path.join(path, ".superpicky", "report.db"))


def result_dirs(path: str) -> List[str]:
    """
    返回保存这个目录选鸟结果的批次目录（各自带 .superpicky/report.db）。

    目录自己有 report.db → [path]（嵌在里面的批次是重复副本，结果浏览器同样只认外层）；
    否则按批次模式的落盘方式找子目录里的批次（tools.merged_report_db.find_processed_subdirs，
    与结果浏览器 open_directory 用的是同一个查找器）。父目录在批次模式下把 report.db
    写进各个子目录、从不写进自己，所以只看自己的 report.db 会漏掉它。
    读不了（目录不存在、权限不足）时返回空表，不抛异常。

    参数:
    path (str): 用户列表里的目录

    返回:
    List[str]: 批次目录绝对路径；没有结果时为空

    Batch folders holding this folder's results. Own report.db -> [path];
    otherwise the processed sub-folders a batch-mode run wrote (same finder
    the results browser uses). Never raises; unreadable -> [].

    Parameters:
    path (str): a folder from the user's list

    Return:
    List[str]: batch folder paths; empty when there are no results
    """
    if not path:
        return []
    try:
        if _report_db_exists(path):
            return [path]
        from tools.merged_report_db import find_processed_subdirs
        return list(find_processed_subdirs(path))
    except Exception:  # noqa: BLE001 — 判定结果有无不能让调用方崩溃 / must not raise
        return []


def folder_has_results(path: str) -> bool:
    """
    目录是否有选鸟结果：自己有 report.db，或子目录里有批次（见 result_dirs）。

    参数:
    path (str): 目录

    返回:
    bool: 是否有结果

    Whether the folder has results: its own report.db or processed sub-batches.
    """
    return bool(result_dirs(path))


def classify_folder(path: str) -> str:
    """
    分类目录状态：有续跑进度 -> resume；有 report.db -> processed；否则 fresh。

    Classify a folder: resume state -> resume; report.db -> processed; else fresh.
    """
    if ResumeStateManager(path).exists():
        return STATE_RESUME
    if _report_db_exists(path):
        return STATE_PROCESSED
    return STATE_FRESH


@dataclass
class FolderOutcome:
    """
    单个目录的处理结果。status: done | skipped | error | stopped | not_started。

    Result for one folder.
    """
    path: str
    status: str
    stats: dict = field(default_factory=dict)
    error: str = ""


class FolderQueue:
    """
    按顺序处理多个目录的队列。plan 为 (path, action)，
    action 取 process / resume / reprocess / skip；skip 目录在构造时即记为 skipped，
    next_item 永不返回。

    Sequential queue over folders. Action is process / resume / reprocess / skip;
    skip folders are marked "skipped" at construction and never yielded.
    """

    def __init__(self, folders: List[str], reprocess_all: bool) -> None:
        """
        参数: folders 目录列表（保持顺序）；reprocess_all 是否重处理已处理目录。

        Parameters: folders in order; reprocess_all re-runs processed folders.
        """
        self._folders: List[str] = list(folders)
        self.plan: List[Tuple[str, str]] = []
        self._outcomes = {}
        self._cursor = 0
        self._stopped = False
        for path in self._folders:
            state = classify_folder(path)
            if state == STATE_RESUME:
                action = "resume"
            elif state == STATE_PROCESSED:
                action = "reprocess" if reprocess_all else "skip"
            else:
                action = "process"
            self.plan.append((path, action))
            status = "skipped" if action == "skip" else "not_started"
            self._outcomes[path] = FolderOutcome(path, status)

    def next_item(self) -> Optional[Tuple[str, str]]:
        """返回下一个待处理 (path, action)；已停止或耗尽返回 None。
        Next (path, action) to run, or None if stopped/exhausted."""
        if self._stopped:
            return None
        while self._cursor < len(self.plan):
            item = self.plan[self._cursor]
            self._cursor += 1
            if item[1] != "skip":
                return item
        return None

    def record(self, path: str, status: str, stats: Optional[dict] = None,
               error: str = "") -> None:
        """记录某目录结果 / Record a folder's result."""
        self._outcomes[path] = FolderOutcome(path, status, dict(stats or {}), error)

    def stop(self) -> None:
        """停止队列：之后 next_item 返回 None，未开始的保持 not_started。
        Stop the queue; unstarted folders stay not_started."""
        self._stopped = True

    @property
    def outcomes(self) -> List[FolderOutcome]:
        """与输入目录同序的结果列表 / Outcomes in input order."""
        return [self._outcomes[p] for p in self._folders]

    def processed_folders(self) -> List[str]:
        """当前有结果的目录（列表顺序；判定见 folder_has_results，含结果在子目录里的父目录）。
        Folders that currently have results (own report.db or processed
        sub-batches), in list order."""
        return [p for p in self._folders if folder_has_results(p)]
