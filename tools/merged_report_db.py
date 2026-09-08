#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并 ReportDB — 跨多个目录的 report.db 联合查询

用于结果浏览器的「全部」视图与跨目录汇总（例如十天合计拍到多少种鸟），
不复制数据、零额外磁盘开销。

实现方式：持有每个子目录各自的 ReportDB 实例，查询时逐个调用再在 Python 侧
合并。**不再使用 SQLite 的 ATTACH**——SQLite 编译期上限 MAX_ATTACHED=10，
第 11 个库起必然失败，而原实现把失败静默吞掉，用户看到的照片数与鸟种数只含
前 10 个目录却毫无提示（2026-09-08 实测：25 个目录只有 10 个的数据进来，
统计数字看起来完全正常）。实际使用中一天可能分上下午两个目录，十天就是
20 个，这个上限远远不够。

逐库查询还顺带解决了 schema 漂移：各目录的 report.db 列数未必一致（旧版本
引入又废弃的字段仍留在老库里），原先用 SELECT * 做 UNION ALL，一个库多几列
就整个失败。现在每个库由自己的 ReportDB 负责补齐缺失列，合并层再按
PHOTO_COLUMNS 对齐字段集，多出来的一律不外露。

Holds one ReportDB per directory and merges results in Python rather than
using SQLite ATTACH, which caps at 10 databases and silently dropped the rest.
"""

import os
import threading
from typing import Any, Dict, List, Optional

from core.recursive_scanner import is_processed


def _source_key(sub_dir: str, root_dir: str) -> str:
    """
    算出一个子目录对外的 ``source_dir`` 标识。

    正常情况用相对 root_dir 的路径，短且可读。但参与合并的目录不再保证有共同
    父目录——用户可以把移动盘和内置盘上的批次凑在一起，Windows 上跨盘
    ``os.path.relpath`` 直接抛 ValueError，整个合并就失败了。这种情况退回用
    绝对路径：不好看，但唯一且稳定，而 source_dir 的职责只是唯一定位。

    Fall back to the absolute path when the directory shares no root with
    root_dir (relpath raises across Windows drives); source_dir only needs to
    identify a batch, not to look nice.

    参数 / Args:
        sub_dir:  子目录绝对路径
        root_dir: 合并根

    返回 / Returns:
        str: 唯一标识该子目录的字符串
    """
    try:
        return os.path.relpath(sub_dir, root_dir)
    except ValueError:
        return os.path.abspath(sub_dir)


class MergedReportDB:
    """
    多目录合并视图，对外暴露与 ReportDB 相同的查询/更新接口。

    每条返回的记录都带 ``source_dir`` 字段（相对 root_dir 的路径），供调用方
    定位照片属于哪个批次；写入接口接受 ``filename`` 或 ``(source_dir,
    filename)`` 作为稳定键。

    Presents the same interface as ReportDB over several directories; every row
    carries a ``source_dir`` field and writes accept either key form.
    """

    def __init__(self, root_dir: str, sub_dirs: List[str]):
        """
        参数 / Args:
            root_dir: 根目录（用于计算 source_dir 相对路径）
            sub_dirs: 含 report.db 的子目录绝对路径列表

        说明：逐个实例化 ReportDB，这也会触发各库的 schema 升级与缺列补齐。
        打不开的库跳过并记入 skipped，不阻断其余目录。
        Opening each ReportDB also migrates that database's schema.
        """
        from .report_db import ReportDB

        self.root_dir = root_dir
        self.sub_dirs = list(sub_dirs)
        self._lock = threading.RLock()

        # rel_dir -> ReportDB，rel_dir 即对外的 source_dir
        self._dbs: Dict[str, "ReportDB"] = {}
        # 打不开的目录及原因，供上层提示用户（不再静默丢弃）
        self.skipped: List[tuple] = []

        for sub_dir in sub_dirs:
            db_path = os.path.join(sub_dir, ".superpicky", "report.db")
            if not os.path.exists(db_path):
                self.skipped.append((sub_dir, "no_database"))
                continue
            rel = _source_key(sub_dir, root_dir)
            try:
                self._dbs[rel] = ReportDB(sub_dir)
            except Exception as e:
                self.skipped.append((sub_dir, f"open_failed:{e.__class__.__name__}"))

    # ── 内部工具 / Internals ────────────────────────────────────────────────

    def _tag(self, rows: List[dict], rel: str) -> List[dict]:
        """
        把一个子库的查询结果规范化：字段按 PHOTO_COLUMNS 对齐，并打上 source_dir。

        字段集必须固定，不能随参与合并的目录组合而变——老库里残留的废弃字段
        （analysis_state / session_id 等）若原样带出，下游按列名取值的代码就会
        时灵时不灵。缺的补 None，多的丢弃。

        Normalize one sub-database's rows to a fixed PHOTO_COLUMNS-aligned field
        set plus source_dir, so downstream code sees the same keys regardless of
        which directories happen to be merged.
        """
        from .report_db import PHOTO_COLUMNS

        names = [c[0] for c in PHOTO_COLUMNS]
        return [
            {**{n: row.get(n) for n in names}, "source_dir": rel}
            for row in rows
        ]

    def _each(self):
        """按目录名排序遍历 (rel_dir, ReportDB)，保证结果顺序稳定。"""
        for rel in sorted(self._dbs):
            yield rel, self._dbs[rel]

    def _targets(self, photo_key) -> List[str]:
        """
        把照片键解析为要操作的子库 rel_dir 列表。

        传 (source_dir, filename) 时精确定位；只传 filename 时全库查找，
        命中多个则返回空——跨目录同名照片很常见（相机计数器循环），
        写错目标比不写更糟。

        Resolve a photo key to sub-databases; an ambiguous bare filename
        resolves to nothing, since writing to the wrong batch is worse than
        not writing at all.
        """
        if isinstance(photo_key, tuple) and len(photo_key) >= 2:
            source_dir, filename = photo_key[0], photo_key[1]
            if not filename:
                return []
            return [source_dir] if source_dir in self._dbs else []

        filename = photo_key
        if not filename:
            return []
        hits = [rel for rel, db in self._each() if db.get_photo(filename)]
        return hits if len(hits) == 1 else []

    # ── 查询 / Queries ──────────────────────────────────────────────────────

    def get_all_photos(self) -> List[dict]:
        """
        返回所有目录的照片记录，按 (source_dir, filename) 排序。

        Return every photo across all directories.
        """
        with self._lock:
            out: List[dict] = []
            for rel, db in self._each():
                out.extend(self._tag(db.get_all_photos(), rel))
            return sorted(out, key=lambda p: (p.get("source_dir") or "",
                                              p.get("filename") or ""))

    def get_photo(self, filename: str) -> Optional[dict]:
        """
        按文件名查一条记录；跨目录重名时返回第一个命中。

        Look up one photo by filename across directories.
        """
        with self._lock:
            for rel, db in self._each():
                row = db.get_photo(filename)
                if row:
                    row["source_dir"] = rel
                    return row
            return None

    def get_photos_by_filters(self, filters: Optional[dict] = None) -> List[dict]:
        """
        跨目录按筛选条件查询。

        绝大多数条件可以逐库委托，唯独「其他鸟类」兜底项必须跨库判定：单库的
        判据是「**本库**里没有 2★以上照片的鸟种」，逐库委托会把「在 A 批次有
        目录、在 B 批次只拍到低星」的鸟种，在 B 里错划进兜底项——于是同一个
        鸟种既出现在自己的条目下，又出现在兜底项里，划分不再互斥。

        所以这里先取跨库的「有目录鸟种」并集，再把兜底项定义为「鸟名为空、或
        不属于该并集」的照片，与 get_distinct_species(foldered_only=True) 严格
        对齐，保证不重不漏。

        Most filters delegate per-database, but the "other birds" catch-all must
        be decided across directories: a species foldered in one batch would
        otherwise be double-counted as unfoldered in another.
        """
        from .report_db import SPECIES_FILTER_OTHER

        filters = filters or {}
        with self._lock:
            species_col = None
            if "bird_species_en" in filters:
                species_col = "bird_species_en"
            elif "bird_species_cn" in filters:
                species_col = "bird_species_cn"
            species_val = filters.get(species_col) if species_col else None

            is_other = (isinstance(species_val, str)
                        and species_val.strip() == SPECIES_FILTER_OTHER)

            if not is_other:
                out: List[dict] = []
                for rel, db in self._each():
                    out.extend(self._tag(db.get_photos_by_filters(filters), rel))
                return sorted(out, key=lambda p: (p.get("source_dir") or "",
                                                  p.get("filename") or ""))

            # 兜底项：跨库算出有目录的鸟种，再取补集
            # Catch-all: compute foldered species across all directories first.
            rest = {k: v for k, v in filters.items() if k != species_col}
            foldered = set(self.get_distinct_species(
                use_en=(species_col == "bird_species_en"),
                ratings=filters.get("ratings"),
                foldered_only=True,
            ))
            out = []
            for rel, db in self._each():
                for row in self._tag(db.get_photos_by_filters(rest), rel):
                    name = (row.get(species_col) or "").strip()
                    if not name or name not in foldered:
                        out.append(row)
            return sorted(out, key=lambda p: (p.get("source_dir") or "",
                                              p.get("filename") or ""))

    def get_photos_by_burst_id(self, burst_id: int,
                               abs_source_dir: Optional[str] = None) -> List[dict]:
        """
        按 burst_id 查同组连拍。

        连拍组是 per-directory 的，不同目录的 burst_id 会撞号，因此给了
        abs_source_dir 就只查该目录；没给则跨目录查（调用方需自行区分）。

        Burst ids are per-directory and collide across batches, so a source
        directory narrows the search to just that one.

        参数 / Args:
            burst_id:       连拍组 ID
            abs_source_dir: 照片所在目录绝对路径，可选

        返回 / Returns:
            List[dict]: 该组照片，含 source_dir
        """
        with self._lock:
            if abs_source_dir:
                try:
                    rel = os.path.relpath(abs_source_dir, self.root_dir)
                except ValueError:
                    return []
                db = self._dbs.get(rel)
                if db is None:
                    return []
                return self._tag(db.get_photos_by_burst_id(burst_id), rel)

            out: List[dict] = []
            for rel, db in self._each():
                out.extend(self._tag(db.get_photos_by_burst_id(burst_id), rel))
            return out

    def get_distinct_species(self, use_en: bool = False, ratings: list = None,
                             foldered_only: bool = False) -> List[str]:
        """
        跨目录的去重鸟种列表。

        foldered_only 取**并集**：某鸟种只要在任一批次里够格建目录，它在别的
        批次的低星照片也归到该鸟种条目下，不落进「其他鸟类」兜底项——这与
        get_photos_by_filters 的哨兵判据必须一致，否则划分会重叠或漏照片。

        Union across directories: a species owning a folder in ANY batch counts
        as foldered, matching the sentinel rule used when filtering.
        """
        with self._lock:
            names: set = set()
            for _rel, db in self._each():
                names.update(db.get_distinct_species(
                    use_en=use_en, ratings=ratings, foldered_only=foldered_only))
            return sorted(n for n in names if n)

    def get_statistics(self) -> dict:
        """汇总统计（跨全部目录）。"""
        photos = self.get_all_photos()
        stats = {'total': len(photos), 'has_bird': 0, 'flying': 0, 'by_rating': {}}
        for p in photos:
            if p.get('has_bird'):
                stats['has_bird'] += 1
            if p.get('is_flying'):
                stats['flying'] += 1
            r = p.get('rating', 0)
            stats['by_rating'][r] = stats['by_rating'].get(r, 0) + 1
        return stats

    def get_corrections(self) -> List[dict]:
        """返回所有子目录的纠错记录，按时间升序。"""
        with self._lock:
            out: List[dict] = []
            for _rel, db in self._each():
                try:
                    out.extend(db.get_corrections())
                except Exception:
                    # 个别老库可能还没有 corrections 表
                    # A legacy sub-DB may lack the table.
                    pass
            return sorted(out, key=lambda r: (r.get("created_at") or "",
                                              r.get("id") or 0))

    # ── 写入 / Writes ───────────────────────────────────────────────────────

    def update_photo(self, photo_key, data: dict) -> bool:
        """按稳定键更新记录，兼容 filename 或 (source_dir, filename)。"""
        if not data:
            return False
        with self._lock:
            filename = photo_key[1] if isinstance(photo_key, tuple) else photo_key
            updated = False
            for rel in self._targets(photo_key):
                if self._dbs[rel].update_photo(filename, data):
                    updated = True
            return updated

    def delete_photo(self, photo_key) -> bool:
        """按稳定键删除记录。"""
        with self._lock:
            filename = photo_key[1] if isinstance(photo_key, tuple) else photo_key
            deleted = False
            for rel in self._targets(photo_key):
                if self._dbs[rel].delete_photo(filename):
                    deleted = True
            return deleted

    def insert_correction(self, data: dict) -> None:
        """
        写入一条纠错记录，落到该照片所属的子库。

        接受 data['photo_key'] 为 (source_dir, filename) 以精确定位——合并库里
        跨目录重名很常见，只给 filename 可能定位不到唯一子库。

        Insert a correction into the owning sub-database; photo_key
        disambiguates same-named photos across directories.
        """
        with self._lock:
            key = data.get("photo_key") or data.get("filename")
            for rel in self._targets(key):
                self._dbs[rel].insert_correction(data)

    def update_burst_ids(self, burst_map: dict) -> int:
        """
        批量写连拍分组。burst_map 的键可以是 filename 或 (source_dir, filename)。

        Bulk-write burst ids; keys may be filenames or (source_dir, filename).
        """
        with self._lock:
            per_db: Dict[str, dict] = {}
            for key, burst_id in burst_map.items():
                filename = key[1] if isinstance(key, tuple) else key
                for rel in self._targets(key):
                    per_db.setdefault(rel, {})[filename] = burst_id
            total = 0
            for rel, sub_map in per_db.items():
                total += self._dbs[rel].update_burst_ids(sub_map)
            return total

    def clear_burst_ids(self) -> int:
        """清空所有子库的连拍分组。"""
        with self._lock:
            return sum(db.clear_burst_ids() for _rel, db in self._each())

    # ── 生命周期 / Lifecycle ────────────────────────────────────────────────

    def close(self):
        """关闭所有子库连接。"""
        with self._lock:
            for db in self._dbs.values():
                try:
                    db.close()
                except Exception:
                    pass
            self._dbs.clear()

    @property
    def directory(self):
        """兼容 ReportDB.directory 属性"""
        return self.root_dir


def summarize_directories(sub_dirs: List[str]) -> List[Dict[str, Any]]:
    """
    为每个批次目录给出概览：照片数与鸟种数，供用户在合并前挑选。

    刻意用只读连接直查，**不经 ReportDB**——后者会触发 schema 升级、补列与
    建索引。用户可能只是打开父目录看一眼、最后一个都不选，为了列表上的两个
    数字就去改人家几十个库是不可接受的。

    某个目录读不出来（库损坏、权限不足、老到没有 photos 表）时，该目录的
    计数为 None 而不是 0——0 会让人以为「这天没拍到」，None 才如实表示
    「读不出来」。其余目录不受影响。

    Summarize each batch for the pre-merge picker using a read-only query;
    opening a full ReportDB would migrate schemas for directories the user may
    never open. Unreadable directories report None rather than 0.

    参数 / Args:
        sub_dirs: 批次目录绝对路径列表

    返回 / Returns:
        List[Dict[str, Any]]: 每项含 path / name / photos / species，
        顺序与入参一致；photos、species 读不出时为 None。
    """
    import sqlite3

    out: List[Dict[str, Any]] = []
    for sub_dir in sub_dirs:
        entry: Dict[str, Any] = {
            "path": sub_dir,
            "name": os.path.basename(sub_dir.rstrip(os.sep)) or sub_dir,
            "photos": None,
            "species": None,
        }
        db_path = os.path.join(sub_dir, ".superpicky", "report.db")
        if os.path.exists(db_path):
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    entry["photos"] = con.execute(
                        "SELECT COUNT(*) FROM photos").fetchone()[0]
                    entry["species"] = con.execute(
                        "SELECT COUNT(DISTINCT bird_species_cn) FROM photos "
                        "WHERE bird_species_cn IS NOT NULL "
                        "AND TRIM(bird_species_cn) != ''").fetchone()[0]
                finally:
                    con.close()
            except (sqlite3.Error, OSError):
                pass          # 保持 None：读不出就如实说读不出
        out.append(entry)
    return out


def find_processed_subdirs(root_dir: str) -> List[str]:
    """
    查找根目录及其子目录中所有已处理的批次目录。

    已处理目录内部再嵌一个已处理目录时**只保留外层**：内层几乎总是同一批
    照片的重复副本（现网实例 2026-03-07 与 2026-03-07/2026-03-07 是同一批
    195 张，文件名逐个相同），两个都算会让合计张数与鸟种数虚高一倍。

    但外层若没被处理过，内层就是真正的批次，必须保留——相机卡目录或分类
    文件夹里放着一个批次是常见情形（2026-06-08/115JMSZ9）。

    Find processed batch directories. A processed directory nested inside
    another is dropped as a duplicate copy; one nested in an *unprocessed*
    folder is kept, since that folder is just a container.

    参数 / Args:
        root_dir: 要扫描的根目录

    返回 / Returns:
        List[str]: 批次目录绝对路径，按发现顺序
    """
    result = []

    if is_processed(root_dir):
        result.append(root_dir)

    for root, subdirs, files in os.walk(root_dir):
        subdirs[:] = [d for d in subdirs if not d.startswith('.') and not d.startswith('burst_')]
        from constants import RATING_FOLDER_NAMES, RATING_FOLDER_NAMES_EN
        star_names = set(RATING_FOLDER_NAMES.values()) | set(RATING_FOLDER_NAMES_EN.values())
        subdirs[:] = [d for d in subdirs if d not in star_names]

        for d in subdirs:
            full = os.path.join(root, d)
            if is_processed(full):
                result.append(full)

    # 落在已收录批次内部的目录一律剔除（见上文的重复副本说明）
    # Drop any batch that lives inside another already-collected batch.
    kept: List[str] = []
    for path in result:
        parent_of_path = os.path.dirname(os.path.abspath(path))
        if any(os.path.abspath(other) == parent_of_path
               or parent_of_path.startswith(os.path.abspath(other) + os.sep)
               for other in result if os.path.abspath(other) != os.path.abspath(path)):
            continue
        kept.append(path)
    return kept


def merge_root(sub_dirs: List[str]) -> str:
    """
    为一组要合并的目录选出合并根。

    合并根只用来算各批次的 source_dir 相对路径，以及给导出文件挑个默认落点。
    目录多半在同一个父目录下，取共同父目录即可；但用户可以自由添加任意位置的
    目录，跨盘时根本没有共同路径（Windows 上 commonpath 抛 ValueError），
    此时退回第一个目录的父目录——总得有个可写的落点。

    Pick a root for a set of batches: their common parent when there is one,
    otherwise the first directory's parent (paths may span volumes).

    参数 / Args:
        sub_dirs: 批次目录绝对路径

    返回 / Returns:
        str: 合并根；sub_dirs 为空时返回空串
    """
    paths = [os.path.abspath(d) for d in sub_dirs if d]
    if not paths:
        return ""
    if len(paths) == 1:
        return os.path.dirname(paths[0]) or paths[0]
    try:
        common = os.path.commonpath(paths)
    except ValueError:
        common = ""
    if common and common not in paths:
        return common
    if common in paths:
        # 有目录本身就是其他目录的父目录，用它当根会让自己的 source_dir 变成
        # "."，仍然唯一，可接受。
        return common
    return os.path.dirname(paths[0]) or paths[0]


def merged_span_label(sub_dirs: List[str]) -> str:
    """
    合并视图的一句话标识：首尾目录名，如 ``2026-08-23 ~ 2026-09-07``。

    合并根的名字说明不了用户在看哪几天（多半是「2026」这种年份文件夹，跨盘时
    甚至是「/」）。批次目录以日期命名，名字序即时间序，取排序后的首尾即可。

    参数 / Args:
        sub_dirs: 批次目录绝对路径

    返回 / Returns:
        str: 首尾目录名；只有一个目录时就是它的名字，无目录时为空串
    """
    names = sorted({os.path.basename(os.path.abspath(d).rstrip(os.sep)) or d
                    for d in sub_dirs if d})
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} ~ {names[-1]}"
