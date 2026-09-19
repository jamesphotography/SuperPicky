# -*- coding: utf-8 -*-
"""
已处理照片的历史结果查询 / Lookup of a processed photo's recorded result.

用途：把一张已经处理过的照片（多半已被移进 ``鸟种/星级/`` 子目录）拖进识鸟面板时，
不重跑模型，直接把上次处理留下的结果取回来显示——鸟种、置信度、星级、对焦状态，
以及 ``.superpicky/cache/crop_debug/`` 里那张裁切预览图。

三条事实源，按可靠性排序：
1. ``.superpicky/report.db``：结构化、带列名，是处理流程的落库结果，本模块的主源。
2. ``.superpicky/cache/crop_debug/<文件名>.jpg``：裁切+mask 预览图。
3. 根目录 ``superpicky.log``：当时控制台的原文，作为补充展示（用户能看到原始那几行）。

只读约定：本模块**绝不写**用户目录。report.db 一律以 SQLite 只读 URI 打开，
不走 ``tools.report_db.ReportDB``——后者构造时会建表/补列（``_reconcile_photo_columns``），
对着一个只是想看看的旧目录做 schema 迁移属于意料之外的副作用。

Lookup of the result a previous processing run recorded for one photo, so that
dropping an already-processed file into the BirdID dock can show species,
confidence, rating, focus status and the stored crop preview without re-running
any model. Strictly read-only: report.db is opened through a read-only SQLite
URI rather than ``tools.report_db.ReportDB``, whose constructor would migrate
the schema of a directory the user merely wanted to look at.
"""
from __future__ import annotations

import os
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.log_restore import LOG_FILENAME, parse_log_line

# 从被拖入的文件向上找已处理目录的最大层数。
# 处理后的照片最深是 ``<根>/鸟种/星级/burst_xxx/照片``（3 层），留出余量到 6 层。
# Maximum number of parent levels to walk when locating the processed root.
# A processed photo sits at most 3 levels deep (species/rating/burst_xxx).
MAX_PARENT_LEVELS = 6

# 单张照片最多回显的原始日志行数（同一文件可能有逐张结果行 + Bird ID 行 + 补救扫描行）。
# Maximum raw log lines echoed for one photo.
MAX_LOG_ENTRIES = 8

# 日志文件超过该大小就不再回查单张照片的原文。这段扫描跑在 UI 线程上（拖入即
# 出结果，不值得为补充信息再开线程），所以宁可放弃原文也不能让面板卡住；
# 16MB 约合十几万行日志，远超正常一次处理的规模。结构化结果仍来自 report.db。
# Skip the per-photo log scan above this size: it runs on the UI thread, so a
# huge log must not stall the panel. 16MB is well beyond a normal run's log; the
# structured result from report.db is unaffected either way.
MAX_LOG_SCAN_BYTES = 16 * 1024 * 1024


@dataclass
class ProcessedPhoto:
    """
    一张已处理照片的历史记录 / The recorded result of one processed photo.

    属性 / Attributes:
        root (str): 该照片所属的已处理目录（含 .superpicky/report.db）/
            The processed directory owning the photo.
        stem (str): 不含扩展名的文件名，即 report.db 的 filename 列 /
            Extension-less file name, i.e. report.db's ``filename`` column.
        record (Dict[str, Any]): report.db 中该照片的整行记录 / The photo row.
        crop_path (Optional[str]): crop_debug 预览图绝对路径，不存在时为 None /
            Absolute path to the stored crop preview, or None.
        log_lines (List[str]): superpicky.log 中提到该文件的原始行 /
            Raw log lines mentioning this file.
    """

    root: str
    stem: str
    record: Dict[str, Any] = field(default_factory=dict)
    crop_path: Optional[str] = None
    log_lines: List[str] = field(default_factory=list)


def find_processed_root(path: str, max_levels: int = MAX_PARENT_LEVELS) -> Optional[str]:
    """
    从一个文件或目录向上找到最近的已处理目录（含 ``.superpicky/report.db``）。

    参数 / Parameters:
        path (str): 文件或目录路径 / A file or directory path.
        max_levels (int): 最多向上查找的层数 / Maximum parent levels to walk.

    返回 / Returns:
        Optional[str]: 已处理目录的绝对路径；找不到时返回 None /
            Absolute path of the processed directory, or None.

    Walk upwards from a file (or directory) to the nearest directory holding
    ``.superpicky/report.db``.
    """
    if not path:
        return None

    current = os.path.abspath(path)
    if os.path.isfile(current):
        current = os.path.dirname(current)

    for _ in range(max_levels + 1):
        if os.path.isfile(os.path.join(current, ".superpicky", "report.db")):
            return current
        parent = os.path.dirname(current)
        if parent == current:      # 到达文件系统根 / Reached the filesystem root
            break
        current = parent
    return None


def _fetch_photo_row(db_path: str, stem: str) -> Optional[Dict[str, Any]]:
    """
    连接 report.db 并取出一张照片的整行记录。

    先用 SQLite 的 ``mode=ro`` URI 只读打开；WAL 模式的库若无法建立 ``-shm``
    （只读卷、网络盘等）会在首次查询时失败，因此失败后退回普通连接重试一次
    ——本函数只发 SELECT，不会改动数据。

    用 ``SELECT *`` 而不是列清单：老目录的 report.db 可能缺少新版本才加的列
    （如 alt_species_cn），写死列名会直接抛 ``no such column``，而「看一眼」
    不该顺手去补列。

    参数 / Parameters:
        db_path (str): report.db 路径 / Path to report.db.
        stem (str): 不含扩展名的文件名 / Extension-less file name.

    返回 / Returns:
        Optional[Dict[str, Any]]: 该照片的记录；无记录或两种方式都读不到时为 None /
            The photo row, or None when absent/unreadable.

    Open read-only first, retrying with a normal connection when a WAL database
    cannot create its ``-shm`` file. ``SELECT *`` is deliberate: legacy
    databases may lack columns added by later schema versions, and a read-only
    peek must not migrate them.
    """
    attempts = (
        {"database": f"{Path(db_path).as_uri()}?mode=ro", "uri": True},
        {"database": db_path, "uri": False},
    )
    for kwargs in attempts:
        conn = None
        try:
            conn = sqlite3.connect(timeout=5.0, **kwargs)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM photos WHERE filename = ? LIMIT 1", (stem,)
            ).fetchone()
            if row is None:
                # 大小写不敏感兜底：部分相机/文件系统会改写文件名大小写
                # Case-insensitive fallback: some cameras/filesystems change case.
                row = conn.execute(
                    "SELECT * FROM photos WHERE filename = ? COLLATE NOCASE LIMIT 1",
                    (stem,),
                ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    return None


def load_photo_record(root: str, stem: str) -> Optional[Dict[str, Any]]:
    """
    从已处理目录的 report.db 读取一张照片的整行记录。

    参数 / Parameters:
        root (str): 已处理目录 / The processed directory.
        stem (str): 不含扩展名的文件名 / Extension-less file name.

    返回 / Returns:
        Optional[Dict[str, Any]]: 该照片的记录；无记录或读取失败时返回 None /
            The photo row, or None when absent/unreadable.

    Read one photo row from the directory's report.db.
    """
    db_path = os.path.join(root, ".superpicky", "report.db")
    if not os.path.isfile(db_path):
        return None
    return _fetch_photo_row(db_path, stem)


def resolve_crop_path(root: str, record: Optional[Dict[str, Any]], stem: str) -> Optional[str]:
    """
    定位 crop_debug 预览图。

    优先用 report.db 里记录的相对路径；该列可能为空（老库、或裁切图写库失败），
    此时按约定路径 ``.superpicky/cache/crop_debug/<stem>.jpg`` 兜底。

    参数 / Parameters:
        root (str): 已处理目录 / The processed directory.
        record (Optional[Dict[str, Any]]): 照片记录，可为 None / The photo row.
        stem (str): 不含扩展名的文件名 / Extension-less file name.

    返回 / Returns:
        Optional[str]: 预览图绝对路径；文件不存在时返回 None /
            Absolute path of the crop preview, or None.

    Locate the stored crop preview, preferring the path recorded in report.db
    and falling back to the conventional location.
    """
    candidates: List[str] = []
    if record:
        rel = record.get("debug_crop_path")
        if isinstance(rel, str) and rel:
            candidates.append(rel if os.path.isabs(rel) else os.path.join(root, rel))
    candidates.append(
        os.path.join(root, ".superpicky", "cache", "crop_debug", f"{stem}.jpg")
    )

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def _scan_log_for_stem(log_path: str, stem: str, limit: int) -> List[str]:
    """
    扫描单个日志文件，取出提到该文件名的行。

    参数 / Parameters:
        log_path (str): 日志文件路径 / Path to a superpicky.log.
        stem (str): 不含扩展名的文件名 / Extension-less file name.
        limit (int): 最多返回的行数 / Maximum number of lines returned.

    返回 / Returns:
        List[str]: 命中的行（已去掉时间戳前缀），按出现顺序 / Matching lines.

    Scan one log file for lines mentioning the given file name.
    """
    try:
        if os.path.getsize(log_path) > MAX_LOG_SCAN_BYTES:
            return []
    except OSError:
        return []

    matched: deque = deque(maxlen=max(0, limit))
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if stem in raw:
                    matched.append(parse_log_line(raw.rstrip("\n")).message.strip())
    except OSError:
        return []
    return list(matched)


def find_log_entries(root: str, stem: str, limit: int = MAX_LOG_ENTRIES) -> List[str]:
    """
    取出 ``superpicky.log`` 里提到该文件的原始日志行。

    为什么要向上找：控制台内容写在**用户当初选中的那个目录**的日志里。批量模式
    下用户选的是父目录，逐张结果行就落在父目录的 superpicky.log，而照片所属的
    已处理子目录里那份只有 photo_processor 直接写的调试细节。因此从已处理目录
    起逐级向上找，命中即止。

    只作为面板上的补充展示（让用户看到当时控制台的原文），结构化结果来自
    report.db。取最后 ``limit`` 条——重复处理过的目录里同一文件会有多轮记录，
    最近那轮才是当前状态。

    参数 / Parameters:
        root (str): 已处理目录 / The processed directory.
        stem (str): 不含扩展名的文件名 / Extension-less file name.
        limit (int): 最多返回的行数 / Maximum number of lines returned.

    返回 / Returns:
        List[str]: 去掉时间戳前缀的日志行，按时间先后排列；没找到时为空 /
            Matching log lines (timestamp prefix stripped), oldest first.

    Walk upwards from the processed directory looking for the console log: in
    batch mode the user selected the parent, so the per-photo lines live in the
    parent's superpicky.log while the subdirectory's copy holds only
    photo_processor's file-only debug output. First log with a hit wins.
    """
    if not stem or not root:
        return []

    current = os.path.abspath(root)
    for _ in range(MAX_PARENT_LEVELS + 1):
        log_path = os.path.join(current, LOG_FILENAME)
        if os.path.isfile(log_path):
            matched = _scan_log_for_stem(log_path, stem, limit)
            if matched:
                return matched
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return []


def lookup_processed_photo(file_path: str) -> Optional[ProcessedPhoto]:
    """
    查询一张照片的历史处理结果。

    参数 / Parameters:
        file_path (str): 被拖入/选中的照片路径（RAW 或 JPEG 均可）/
            The dropped or selected photo path (RAW or JPEG).

    返回 / Returns:
        Optional[ProcessedPhoto]: 找到记录时返回历史结果；该文件不属于任何
            已处理目录、或库里没有它时返回 None /
            The recorded result, or None when the file does not belong to a
            processed directory or has no row.

    Look up everything a previous run recorded for one photo.
    """
    if not file_path:
        return None

    root = find_processed_root(file_path)
    if not root:
        return None

    stem = os.path.splitext(os.path.basename(file_path))[0]
    record = load_photo_record(root, stem)
    if record is None:
        return None

    return ProcessedPhoto(
        root=root,
        stem=stem,
        record=record,
        crop_path=resolve_crop_path(root, record, stem),
        log_lines=find_log_entries(root, stem),
    )
