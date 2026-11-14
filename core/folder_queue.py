# -*- coding: utf-8 -*-
"""
多目录选鸟的纯逻辑（无 Qt 依赖）：目录校验、状态分类、顺序队列与结果汇总。

Pure logic for multi-folder culling (no Qt): folder validation, state
classification, a sequential queue and outcome aggregation.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from constants import HEIF_EXTENSIONS, JPG_EXTENSIONS, RAW_EXTENSIONS
from core.recursive_scanner import (
    DEFAULT_SCAN_MAX_DEPTH,
    is_dangerous_root,
    scan_directories,
)
from tools.resume_state import ResumeStateManager

# 照片扩展名，与 core.recursive_scanner 扫描用的同源 / same sources as the scanner
_PHOTO_EXTENSIONS = frozenset(RAW_EXTENSIONS + JPG_EXTENSIONS + HEIF_EXTENSIONS)

REJECT_DUPLICATE = "duplicate"
REJECT_OVERLAP = "overlap"
REJECT_DANGEROUS = "dangerous"
REJECT_NO_PHOTOS = "no_photos"
REJECT_MISSING = "missing"

STATE_FRESH = "fresh"
STATE_RESUME = "resume"
STATE_PROCESSED = "processed"
STATE_INCREMENTAL = "incremental"


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
    扫描不到照片、但已处理过（有 report.db）、有续跑进度，或是批次模式处理过的父目录
    （report.db 只在子目录里，见 folder_has_results）仍可加入。

    参数: existing 已在列表中的目录；new 新目录。
    返回: None 表示可加入，否则为 REJECT_* 之一。

    Validate a folder before adding. Order: missing, duplicate, overlap,
    dangerous, no photos. A folder with no scannable photos is still accepted
    when it is processed (report.db), resumable, or a batch-mode parent whose
    results live in sub-folders (folder_has_results). Returns None if
    acceptable, else a REJECT_* code.
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
        # 批次模式处理过的父目录：report.db 只写在各子目录里，classify_folder 只看自己
        # 的 report.db 会判成 fresh，所以还要看 folder_has_results。
        # Processed / resumable folders keep photos in excluded rating/species
        # folders, so the scan finds none; they must still be addable. A
        # batch-mode parent has report.db only in its sub-folders, which
        # classify_folder does not see, hence folder_has_results as well.
        if (classify_folder(new) != STATE_FRESH or folder_has_results(new)
                or has_leftover_output(new)):
            return None
        return REJECT_NO_PHOTOS
    return None


def _has_photo_anywhere(path: str) -> bool:
    """
    目录（含所有子目录，跳过 . 开头的隐藏项如 .superpicky、._*）里是否至少有一张照片。
    找到第一张就返回；读不了的子目录跳过。

    参数:
    path (str): 目录

    返回:
    bool: 有照片为 True

    Whether the folder or any sub-folder holds at least one photo, skipping
    hidden entries (.superpicky, ._*). Returns on the first hit; unreadable
    sub-folders are skipped.
    """
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if (not name.startswith(".")
                    and os.path.splitext(name)[1].lower() in _PHOTO_EXTENSIONS):
                return True
    return False


def has_leftover_output(path: str) -> bool:
    """
    目录是否是「处理过、report.db 已不在、照片还在上次整理出的子目录里」：
    留有 .superpicky 文件夹，并且子目录里确实还有照片。

    report.db 可能因老版本、手动清理等原因不在了，而照片仍在星级/鸟种子目录里，
    扫描得 0 张。单目录下这种目录照样能选中、点「重置」把照片移回来；多目录也要能
    加入列表，按「已处理」对待（默认跳过；全部重新处理 = 先重置再处理），否则只能
    先关掉实验室开关、切回单目录重置。
    只剩空的鸟种/星级文件夹、照片早已移走的目录不算——重置不出任何照片，仍按
    「没有照片」拒绝。

    参数:
    path (str): 目录

    返回:
    bool: 有 .superpicky 且子目录里有照片为 True

    Whether the folder was processed, lost its report.db, and still keeps
    photos in last run's sub-folders: .superpicky exists and some sub-folder
    holds a photo. Such a folder must be addable (single-folder mode can
    select and reset it) and is treated as processed. A folder left with
    only empty rating/species folders does not count and is still rejected
    as having no photos.

    Parameters:
    path (str): the folder

    Return:
    bool: True if .superpicky exists and photos remain in sub-folders
    """
    return (os.path.isdir(os.path.join(path, ".superpicky"))
            and _has_photo_anywhere(path))


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
    分类目录状态：有续跑进度 -> resume；有 report.db 且含未处理新片 -> incremental；
    仅有 report.db -> processed；否则 fresh。

    Classify a folder: resume state -> resume; report.db + new unprocessed media ->
    incremental; report.db only -> processed; else fresh.
    """
    if ResumeStateManager(path).exists():
        return STATE_RESUME
    if _report_db_exists(path):
        try:
            from advanced_config import get_advanced_config
            if get_advanced_config().lab_incremental_cull:
                from core.incremental_cull import inspect_directory_delta
                delta = inspect_directory_delta(path)
                if delta.has_valid_db and delta.has_new_work:
                    return STATE_INCREMENTAL
        except Exception:
            pass
        return STATE_PROCESSED
    return STATE_FRESH


def queue_state(path: str) -> str:
    """
    多目录队列用的目录状态：在 classify_folder 基础上认出批次模式处理过的父目录。

    批次模式下父目录的 report.db 只写在各子目录里、从不写进自己，classify_folder 只看
    自己的 report.db 会把它判成 fresh——队列就不会默认跳过它，「全部重新处理」也不先
    复原，扫描还会因为照片都在被排除的星级/鸟种目录里而记一条「无照片」错误（spec §4.3）。
    所以：classify_folder 为 fresh、目录有结果（folder_has_results，含子目录批次）、并且
    扫描不到任何可处理的照片时，算 processed。还有能扫到照片的父目录（部分子目录没
    处理）仍为 fresh，照常处理（批次模式会跳过已处理的子目录）。
    「扫不到」与开始前预扫描 / 加入列表时的判定相同：scan_directories 返回空表。
    列表缓存的张数不能用来判断：扫描为 0 时它换成了各 report.db 的计数
    （ui.folder_list_widget.folder_row_count），所以这里重新扫描；只有 fresh 且有结果的
    目录（即批次父目录）才会走到扫描，普通目录不多花时间。扫描出错时保守地返回 fresh。

    参数:
    path (str): 目录

    返回:
    str: STATE_FRESH / STATE_RESUME / STATE_PROCESSED

    Folder state for the multi-folder queue: classify_folder plus batch-mode
    processed parents. A batch parent keeps report.db only in its
    sub-folders, so classify_folder calls it fresh and the queue would
    neither skip it by default nor restore it before "reprocess all", and
    the scan would log a "no photos" error. Hence a fresh folder that has
    results (folder_has_results) and no scannable photos (scan_directories
    returns nothing, the same test as the pre-scan and list validation) is
    processed. A parent that still has scannable photos stays fresh. The
    list's cached counts cannot decide this (they fall back to report.db
    counts when the scan is 0), so the folder is rescanned — only fresh
    folders with results get that far. A failing scan conservatively yields
    fresh.

    Parameters:
    path (str): the folder

    Return:
    str: STATE_FRESH / STATE_RESUME / STATE_PROCESSED
    """
    state = classify_folder(path)
    if state != STATE_FRESH:
        return state
    # 没有 report.db 但留有 .superpicky 的目录（照片还在上次整理的子目录里）同样按
    # 「扫不到照片 → 已处理」判定，见 has_leftover_output。
    # A folder with .superpicky but no report.db (photos still in last run's
    # sub-folders) follows the same "nothing to scan -> processed" rule.
    if not (folder_has_results(path) or has_leftover_output(path)):
        return state
    try:
        if scan_directories(path, max_depth=DEFAULT_SCAN_MAX_DEPTH):
            return STATE_FRESH
    except Exception:  # noqa: BLE001 — 扫描失败按 fresh 处理 / treat a failed scan as fresh
        return STATE_FRESH
    return STATE_PROCESSED


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

    def __init__(self, folders: List[str], reprocess_all: bool,
                 states: Optional[Dict[str, str]] = None) -> None:
        """
        参数:
        folders (List[str]): 目录列表（保持顺序）
        reprocess_all (bool): 是否重处理已处理目录
        states (Optional[Dict[str, str]]): 开始前预检已经算好的状态（queue_state 的结果）；
            给了就直接采用，保证预检框列出的与队列实际执行的一致，也免得批次父目录再扫一遍。
            缺的目录（例如不存在、没进预检）用 queue_state 现算。

        Parameters:
        folders (List[str]): folders in order
        reprocess_all (bool): re-run processed folders
        states (Optional[Dict[str, str]]): states already computed by the
            pre-check (queue_state); used as-is so the dialog and the plan
            agree and batch parents are not rescanned. Missing entries are
            computed with queue_state.
        """
        self._folders: List[str] = list(folders)
        self.plan: List[Tuple[str, str]] = []
        self._outcomes = {}
        self._cursor = 0
        self._stopped = False
        states = states or {}
        for path in self._folders:
            state = states[path] if path in states else queue_state(path)
            if state == STATE_RESUME:
                action = "resume"
            elif state == STATE_INCREMENTAL:
                action = "reprocess" if reprocess_all else "incremental"
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
