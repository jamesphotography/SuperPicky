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

    用 ``SELECT *`` 而不是列清单：老目录的 report.db 可能缺少新版本才加的列
    （如 alt_species_cn），写死列名会直接抛 ``no such column``，而「看一眼」
    不该顺手去补列。

    参数 / Parameters:
        db_path (str): report.db 路径 / Path to report.db.
        stem (str): 不含扩展名的文件名 / Extension-less file name.

    返回 / Returns:
        Optional[Dict[str, Any]]: 该照片的记录；无记录或读不到时为 None /
            The photo row, or None when absent/unreadable.

    ``SELECT *`` is deliberate: legacy databases may lack columns added by later
    schema versions, and a read-only peek must not migrate them.
    """

    def _query(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
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

    return _with_read_connection(db_path, _query)


def _with_read_connection(db_path: str, query):
    """
    以只读方式连上 report.db，把连接交给 ``query`` 执行。

    只用 SQLite 的 ``mode=ro`` URI，**没有退回读写连接的路径**：读写连接一开
    一关会 checkpoint 并删掉 WAL 文件，那就等于动了用户的目录，而本模块的全部
    意义是「只是看一眼」。只读连不上时（只读卷、网络盘上建不出 ``-shm`` 等）
    直接放弃，调用方会退回实时识别——少一个便利，好过悄悄改用户的数据。

    连上之后先用一句无害的 ``sqlite_master`` 查询探连通性再跑真正的 query：
    只读连接的失败要到第一次读表时才暴露，不先探一把的话，「缺列」这类业务
    异常会与连接失败混为一谈。query 自身的异常一律不重试。

    参数 / Parameters:
        db_path (str): report.db 路径 / Path to report.db.
        query (Callable[[sqlite3.Connection], Any]): 只读查询 / A read-only query.

    返回 / Returns:
        Any: ``query`` 的返回值；连不上或查询失败时返回 None /
            The query's result, or None when the database cannot be read.

    Read-only URI with no read-write fallback: opening a WAL database
    read-write checkpoints and removes its WAL, i.e. touches the user's folder,
    which defeats the point of a look-only lookup. Connectivity is probed with a
    harmless sqlite_master query first, so a business error (e.g. a missing
    column) is not mistaken for a connection failure.
    """
    conn = None
    try:
        conn = sqlite3.connect(
            database=f"{Path(db_path).as_uri()}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        return None

    try:
        return query(conn)
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


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


def collect_species_tiers(directory: str) -> Dict[str, int]:
    """
    汇总目录里各鸟种的 GBIF 罕见度档位，供回放的完成统计给鸟名上色。

    为什么要单独跑一趟库：会话结束摘要里的鸟种名录只有「中文名/English」，没有
    罕见度；而完成面板是按罕见度给鸟名着色的（常见灰 / 能见橙 / 少见以上红）。
    档位由每张照片的 ``gbif_rarity_100`` 换算而来，只有库里有。

    同时扫本目录与其直接子目录：批量模式下用户选的是父目录，report.db 在各子
    目录里。全程只读，老库没有 ``gbif_rarity_100`` 列时安静跳过。

    参数 / Parameters:
        directory (str): 被选中的目录 / The selected directory.

    返回 / Returns:
        Dict[str, int]: 鸟名（中文与英文都建索引）→ 档位编号 /
            Species name (both languages) to tier index.

    Collect each species' GBIF rarity tier so the replayed completion panel can
    colour the names as the live one does. The session summary carries names
    only; tiers are derived from per-photo ``gbif_rarity_100`` in report.db. The
    directory itself and its immediate children are scanned, because batch mode
    keeps one database per subdirectory. Read-only; legacy databases without the
    column are skipped silently.
    """
    from core.rarity_tier import gbif_score_to_tier

    candidates = [directory]
    try:
        with os.scandir(directory) as entries:
            candidates.extend(e.path for e in entries if e.is_dir())
    except OSError:
        pass

    def _query(conn: sqlite3.Connection):
        return conn.execute(
            "SELECT bird_species_cn, bird_species_en, MAX(gbif_rarity_100) AS score "
            "FROM photos WHERE gbif_rarity_100 IS NOT NULL "
            "GROUP BY bird_species_cn, bird_species_en"
        ).fetchall()

    tiers: Dict[str, int] = {}
    for candidate in candidates:
        db_path = os.path.join(candidate, ".superpicky", "report.db")
        if not os.path.isfile(db_path):
            continue
        rows = _with_read_connection(db_path, _query)
        for row in rows or []:
            tier = gbif_score_to_tier(row["score"])
            if tier is None:
                continue
            for name in (row["bird_species_cn"], row["bird_species_en"]):
                if name:
                    tiers[name] = tier
    return tiers


def find_report_dbs(directory: str) -> List[str]:
    """
    找出目录树里属于一次处理的全部 report.db（本目录 + 批量模式处理过的子目录）。

    批量模式用 ``core.recursive_scanner.scan_directories`` 递归找照片目录，最深
    ``DEFAULT_SCAN_MAX_DEPTH`` 层，每个照片目录各建一个 ``.superpicky/report.db``。
    这里用同样的深度上限与同样的排除规则（``is_excluded``：隐藏目录、星级目录、
    burst_ 目录等）往下走，不进这些目录既省时间，也避免把整理产物当成处理目录。
    结果按路径排序，保证汇总顺序稳定。

    参数 / Parameters:
        directory (str): 被选中的目录 / The selected directory.

    返回 / Returns:
        List[str]: report.db 绝对路径列表，没有时为空 /
            Sorted report.db paths, empty when none exist.

    Find every report.db a run may have produced under ``directory``: batch
    mode processes each photo directory found by the recursive scanner, so the
    same depth limit and exclusion rules are applied here.
    """
    from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH, is_excluded

    found: List[str] = []
    stack = [(directory, 0)]
    while stack:
        current, depth = stack.pop()
        db_path = os.path.join(current, ".superpicky", "report.db")
        if os.path.isfile(db_path):
            found.append(db_path)
        if depth >= DEFAULT_SCAN_MAX_DEPTH:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and not is_excluded(entry.name):
                        stack.append((entry.path, depth + 1))
        except OSError:
            continue
    return sorted(found)


def collect_completion_stats(directory: str) -> Optional[Dict[str, Any]]:
    """
    从 report.db 现算识鸟面板「完成统计」里的结果部分（星级分布、飞版/精焦、鸟种名录）。

    为什么不用日志摘要：日志里的 ``[Session End]`` 是处理结束那一刻的快照，用户随后
    在结果浏览器里改鸟种、改星级、标记无鸟都只写 report.db，面板若照搬日志就一直
    显示改之前的结果。控制台日志保留历史原样（记录 AI 当时的判断），面板反映现状，
    所以只有这里改读库；总耗时库里没有，仍由调用方从日志取。

    口径（与 HTML 分享报告 ``core.report_export.aggregate`` 一致）：
    - 星级：-1 计「无鸟」；3 及以上计 3★——4/5★ 只能由用户在浏览器里手动升出来，
      面板没有这两档，并进 3★ 才能让各档之和等于总张数；
    - 精焦：``focus_status == "BEST"``（处理流程里与锐度权重 > 1.0 一一对应）；
    - 鸟种名录：只收至少有一张 ``FOLDERED_MIN_RATING``（2★）的鸟种，与浏览器鸟种
      下拉、HTML 报告、eBird 导出同一条线（spec D10）；按罕见度档位降序、同档张数多者
      在前，档位取该鸟种所有照片里 ``gbif_rarity_100`` 的最大值。

    库的选取：汇总 ``find_report_dbs`` 找到的全部 report.db。批量模式按递归扫描
    逐目录处理（父目录自己有照片时也算一个），每个库只记它所在目录的照片，所以
    累加不会重复计数。全程只读。

    参数 / Parameters:
        directory (str): 被选中的目录 / The selected directory.

    返回 / Returns:
        Optional[Dict[str, Any]]: 可并进 ``show_completion_message`` 的 stats
            （total/star_3/star_2/star_1/star_0/no_bird/flying/focus_precise/
            bird_species）；找不到可读的库或老库缺列时返回 None，调用方应退回
            日志摘要 / Stats to merge into the completion panel, or None when no
            database is readable, in which case callers fall back to the log.

    Recompute the result part of the completion panel from report.db, because
    edits made in the results browser (species, rating, no-bird) only reach the
    database while the log summary is a snapshot of the finished run. The console
    keeps that historical snapshot; the panel shows the current state. Scope
    matches the HTML share report: 4/5 stars fold into 3, focus = BEST, and the
    species list only includes species with at least one 2-star photo, ordered by
    rarity tier then photo count. Every database found by ``find_report_dbs`` is
    summed; each one covers only its own directory's photos, so batch runs do not
    double count. Read-only.
    """
    from core.rarity_tier import gbif_score_to_tier
    from core.report_export import FOLDERED_MIN_RATING

    db_paths = find_report_dbs(directory)
    if not db_paths:
        return None

    def _query(conn: sqlite3.Connection):
        return conn.execute(
            "SELECT rating, has_bird, is_flying, focus_status, "
            "bird_species_cn, bird_species_en, gbif_rarity_100 FROM photos"
        ).fetchall()

    rows: List[sqlite3.Row] = []
    for db_path in db_paths:
        fetched = _with_read_connection(db_path, _query)
        if fetched is None:
            # 任何一个库读不出来，现算的数字就是残缺的；宁可整体退回日志摘要
            # One unreadable database makes the totals partial; fall back wholesale.
            return None
        rows.extend(fetched)

    stats: Dict[str, Any] = {
        "total": len(rows), "star_3": 0, "star_2": 0, "star_1": 0, "star_0": 0,
        "no_bird": 0, "flying": 0, "focus_precise": 0,
    }
    # 鸟种分组键与 aggregate 一致：中文名优先，空名（未识别）不成组
    # Group key matches aggregate: Chinese name first, unnamed rows skipped.
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        rating = int(row["rating"] if row["rating"] is not None else 0)
        if rating >= 3:
            stats["star_3"] += 1
        elif rating in (0, 1, 2):
            stats[f"star_{rating}"] += 1
        else:
            stats["no_bird"] += 1
        if row["is_flying"]:
            stats["flying"] += 1
        if row["focus_status"] == "BEST":
            stats["focus_precise"] += 1

        if not row["has_bird"]:
            continue
        key = row["bird_species_cn"] or row["bird_species_en"] or ""
        if not key:
            continue
        group = groups.setdefault(key, {
            "cn_name": row["bird_species_cn"] or "",
            "en_name": row["bird_species_en"] or "",
            "count": 0, "max_rating": rating, "score": None,
        })
        group["count"] += 1
        group["max_rating"] = max(group["max_rating"], rating)
        if not group["en_name"] and row["bird_species_en"]:
            group["en_name"] = row["bird_species_en"]
        score = row["gbif_rarity_100"]
        if score is not None and (group["score"] is None or score > group["score"]):
            group["score"] = score

    species: List[Dict[str, Any]] = []
    for group in groups.values():
        if group["max_rating"] < FOLDERED_MIN_RATING:
            continue
        entry: Dict[str, Any] = {"cn_name": group["cn_name"], "en_name": group["en_name"],
                                 "count": group["count"]}
        tier = gbif_score_to_tier(group["score"])
        if tier is not None:
            entry["gbif_tier"] = tier
        species.append(entry)
    # 与 HTML 报告同序：档位降序（无档位视为最低），同档张数多者在前
    # Same order as the HTML report: tier desc (None lowest), then count desc.
    species.sort(key=lambda s: (s.get("gbif_tier", -1), s["count"]), reverse=True)
    stats["bird_species"] = species
    return stats


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


def _recorded_paths(root: str, record: Dict[str, Any]) -> List[str]:
    """
    取出记录里指向照片本体的路径，解析为绝对路径。

    参数 / Parameters:
        root (str): 已处理目录 / The processed directory.
        record (Dict[str, Any]): 照片记录 / The photo row.

    返回 / Returns:
        List[str]: current_path / original_path 解析后的绝对路径（可能为空列表）/
            Absolute paths recorded for this photo, possibly empty.

    Resolve the photo paths stored in the record; report.db keeps them relative
    to the processed root (see tools/report_db.py), but absolute values from
    older databases are tolerated.
    """
    paths: List[str] = []
    for key in ("current_path", "original_path"):
        value = record.get(key)
        if isinstance(value, str) and value:
            paths.append(value if os.path.isabs(value) else os.path.join(root, value))
    return paths


def _record_matches_file(root: str, record: Dict[str, Any], file_path: str) -> bool:
    """
    判断这条记录是否真的属于被拖入的那个文件。

    只按文件名取记录是不够的：相机文件名（``DSC00014`` 这类）在不同存储卡、
    不同机身上高度重复，而往一个处理过的目录里补放新照片是常规操作。没有这道
    校验时，拖入一张全新的同名照片会拿到另一张照片的鸟种、星级与 crop 预览图，
    而且毫无提示——给错信息比不给信息更糟。

    比的是**所在目录**而不是完整路径：RAW+JPEG 成对拍摄时两个文件同名同目录，
    PR #109 明确支持「拖 JPEG 查 RAW 的记录」，按完整路径比会把这个场景误杀。

    记录里没有任何可用路径时返回 False：宁可退回实时识别，也不拿一条无法验证
    归属的记录去冒充这张照片的历史结果。

    参数 / Parameters:
        root (str): 已处理目录 / The processed directory.
        record (Dict[str, Any]): 按文件名取到的记录 / The row matched by name.
        file_path (str): 被拖入的文件 / The dropped file.

    返回 / Returns:
        bool: 记录确实属于该文件时为 True / Whether the record is really this file's.

    Verify the record belongs to the dropped file. Directories are compared
    rather than full paths so that dropping the sibling JPEG of a recorded RAW
    still resolves, while a same-named file living elsewhere in the tree does
    not. An unverifiable record is rejected.
    """
    dropped_dir = os.path.normcase(os.path.abspath(os.path.dirname(file_path)))
    for recorded in _recorded_paths(root, record):
        if os.path.normcase(os.path.abspath(os.path.dirname(recorded))) == dropped_dir:
            return True
    return False


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

    # 文件名相同还不够，必须确认这条记录就是这个文件的（见 _record_matches_file）
    # Matching by name alone would hand another photo's result to a new file.
    if not _record_matches_file(root, record, file_path):
        return None

    return ProcessedPhoto(
        root=root,
        stem=stem,
        record=record,
        crop_path=resolve_crop_path(root, record, stem),
        log_lines=find_log_entries(root, stem),
    )
