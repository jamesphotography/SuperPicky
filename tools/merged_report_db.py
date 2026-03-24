#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并 ReportDB — 通过 SQLite ATTACH 实时联合查询多个目录的 report.db

用于结果浏览器的"全部"视图，无需复制数据，零额外磁盘开销。
"""

import os
import sqlite3
import threading
from typing import List, Dict, Optional, Any

from core.recursive_scanner import is_processed


class MergedReportDB:
    """
    合并多个目录的 report.db，提供与 ReportDB 兼容的查询接口。
    
    通过 SQLite ATTACH DATABASE 挂载各子目录的 DB，
    使用 UNION ALL 查询并附加 source_dir 列。
    """
    
    def __init__(self, root_dir: str, sub_dirs: List[str]):
        """
        Args:
            root_dir: 根目录（用于计算相对路径）
            sub_dirs: 包含 report.db 的子目录绝对路径列表
        """
        self.root_dir = root_dir
        self.sub_dirs = sub_dirs
        self._lock = threading.RLock()
        
        # 内存数据库作为主连接
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        
        # ATTACH 各子目录的 DB
        self._db_aliases: List[str] = []
        self._alias_to_dir: Dict[str, str] = {}
        
        for i, sub_dir in enumerate(sub_dirs):
            db_path = os.path.join(sub_dir, ".superpicky", "report.db")
            if not os.path.exists(db_path):
                continue
            alias = f"db{i}"
            try:
                self._conn.execute(f"ATTACH DATABASE ? AS {alias}", (db_path,))
                self._db_aliases.append(alias)
                self._alias_to_dir[alias] = sub_dir
            except Exception:
                pass
    
    def _build_union_sql(self, where: str = "", order: str = "ORDER BY source_dir, filename",
                         extra_params: list = None) -> tuple:
        """构建 UNION ALL 查询"""
        if not self._db_aliases:
            return "SELECT 1 WHERE 0", []
        
        parts = []
        params = list(extra_params or [])
        
        for alias in self._db_aliases:
            rel_dir = os.path.relpath(self._alias_to_dir[alias], self.root_dir)
            parts.append(f"SELECT *, '{rel_dir}' AS source_dir FROM {alias}.photos")
        
        union_sql = " UNION ALL ".join(parts)
        
        if where:
            sql = f"SELECT * FROM ({union_sql}) AS merged WHERE {where} {order}"
        else:
            sql = f"SELECT * FROM ({union_sql}) AS merged {order}"
        
        return sql, params

    def get_all_photos(self) -> List[dict]:
        """获取所有目录的照片记录"""
        with self._lock:
            sql, params = self._build_union_sql()
            cursor = self._conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _resolve_photo_targets(self, photo_key) -> List[str]:
        """将照片键解析为要更新的子数据库别名列表。"""
        if isinstance(photo_key, tuple) and len(photo_key) >= 2:
            source_dir, filename = photo_key[0], photo_key[1]
            if not filename:
                return []
            rel_dir_to_alias = {
                os.path.relpath(self._alias_to_dir[alias], self.root_dir): alias
                for alias in self._db_aliases
            }
            alias = rel_dir_to_alias.get(source_dir)
            return [alias] if alias else []

        filename = photo_key
        if not filename:
            return []

        aliases = []
        for alias in self._db_aliases:
            cursor = self._conn.execute(
                f"SELECT 1 FROM {alias}.photos WHERE filename = ? LIMIT 1",
                (filename,),
            )
            if cursor.fetchone():
                aliases.append(alias)
        return aliases if len(aliases) == 1 else []

    def update_photo(self, photo_key, data: dict) -> bool:
        """按稳定键更新记录，兼容 filename 或 (source_dir, filename)。"""
        if not data:
            return False

        from .report_db import COLUMN_NAMES, _now_iso, ReportDB

        cleaned = ReportDB._clean_data(data)
        cleaned["updated_at"] = _now_iso()
        columns = [k for k in cleaned if k in COLUMN_NAMES and k not in ("filename", "id")]
        if not columns:
            return False

        targets = self._resolve_photo_targets(photo_key)
        if not targets:
            return False

        values = [cleaned[k] for k in columns]
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        filename = photo_key[1] if isinstance(photo_key, tuple) else photo_key
        updated = False

        with self._lock:
            for alias in targets:
                sql = f"UPDATE {alias}.photos SET {set_clause} WHERE filename = ?"
                cursor = self._conn.execute(sql, values + [filename])
                updated = updated or cursor.rowcount > 0
            self._safe_commit()
        return updated

    def delete_photo(self, photo_key) -> bool:
        """按稳定键删除记录，兼容 filename 或 (source_dir, filename)。"""
        targets = self._resolve_photo_targets(photo_key)
        if not targets:
            return False

        filename = photo_key[1] if isinstance(photo_key, tuple) else photo_key
        deleted = False
        with self._lock:
            for alias in targets:
                cursor = self._conn.execute(
                    f"DELETE FROM {alias}.photos WHERE filename = ?",
                    (filename,),
                )
                deleted = deleted or cursor.rowcount > 0
            self._safe_commit()
        return deleted
    
    def update_burst_ids(self, burst_map: dict) -> int:
        """
        跨数据库批量更新 burst_id。
        因为 merged_db 是用多数据库联合挂载的，所以要分配给对应的子数据库更新。
        
        Args:
            burst_map: 字典，格式为
                {(source_dir, filename): (burst_id, burst_position)}，
                兼容旧格式 {filename: (burst_id, burst_position)}。
        """
        if not burst_map:
            return 0
            
        total_updated = 0
        from .report_db import _now_iso
        now = _now_iso()
        
        with self._lock:
            # Group updates by alias
            updates_by_alias = {alias: [] for alias in self._db_aliases}
            rel_dir_to_alias = {
                os.path.relpath(self._alias_to_dir[alias], self.root_dir): alias
                for alias in self._db_aliases
            }
            
            # 新格式：source_dir + filename，可安全定位到唯一子数据库。
            # 旧格式：仅 filename，仅在全局唯一时更新，避免误写到同名文件。
            legacy_pending = []
            for photo_key, burst_info in burst_map.items():
                if isinstance(photo_key, tuple) and len(photo_key) >= 2:
                    source_dir, filename = photo_key[0], photo_key[1]
                    alias = rel_dir_to_alias.get(source_dir)
                    if alias and filename:
                        bid, pos = burst_info
                        updates_by_alias[alias].append((bid, pos, now, filename))
                else:
                    legacy_pending.append((photo_key, burst_info))

            if legacy_pending:
                filename_to_aliases: Dict[str, List[str]] = {}
                for alias in self._db_aliases:
                    cursor = self._conn.execute(f"SELECT filename FROM {alias}.photos")
                    for row in cursor.fetchall():
                        filename_to_aliases.setdefault(row[0], []).append(alias)

                for filename, burst_info in legacy_pending:
                    aliases = filename_to_aliases.get(filename, [])
                    if len(aliases) != 1:
                        continue
                    bid, pos = burst_info
                    updates_by_alias[aliases[0]].append((bid, pos, now, filename))
            
            for alias, updates in updates_by_alias.items():
                if updates:
                    sql = f"""
                    UPDATE {alias}.photos 
                    SET burst_id = ?, burst_position = ?, updated_at = ? 
                    WHERE filename = ?
                    """
                    cursor = self._conn.executemany(sql, updates)
                    total_updated += cursor.rowcount
            
            self._safe_commit()
            
        return total_updated

    def clear_burst_ids(self) -> int:
        """清空所有附加数据库里的连拍分组字段。"""
        from .report_db import _now_iso

        total_updated = 0
        now = _now_iso()
        with self._lock:
            for alias in self._db_aliases:
                cursor = self._conn.execute(
                    f"""
                    UPDATE {alias}.photos
                    SET burst_id = NULL, burst_position = NULL, updated_at = ?
                    WHERE burst_id IS NOT NULL OR burst_position IS NOT NULL
                    """,
                    (now,),
                )
                total_updated += cursor.rowcount
            self._safe_commit()
        return total_updated

    def _safe_commit(self):
        if not self._conn:
            return
        try:
            if self._conn.in_transaction:
                self._conn.commit()
        except sqlite3.OperationalError as e:
            if "no transaction is active" in str(e).lower():
                return
            raise
    
    def get_photos_by_filters(self, filters: Optional[dict] = None) -> List[dict]:
        """按筛选条件查询（兼容 ReportDB 接口）"""
        filters = filters or {}
        
        where_clauses = []
        params: List[Any] = []
        
        # 排除无鸟
        where_clauses.append("rating != -1")
        
        ratings = filters.get("ratings")
        if isinstance(ratings, list):
            ratings = [r for r in ratings if r != -1]
            if not ratings:
                return []
            placeholders = ", ".join(["?"] * len(ratings))
            where_clauses.append(f"rating IN ({placeholders})")
            params.extend(ratings)
        
        has_low_rating = isinstance(ratings, list) and any(r <= 0 for r in ratings)
        
        focus_statuses = filters.get("focus_statuses")
        if isinstance(focus_statuses, list):
            if not focus_statuses:
                return []
            placeholders = ", ".join(["?"] * len(focus_statuses))
            condition = f"focus_status IN ({placeholders})"
            if has_low_rating:
                condition = f"({condition} OR focus_status IS NULL)"
            where_clauses.append(condition)
            params.extend(focus_statuses)
        
        is_flying = filters.get("is_flying")
        if isinstance(is_flying, list):
            if not is_flying:
                return []
            placeholders = ", ".join(["?"] * len(is_flying))
            condition = f"is_flying IN ({placeholders})"
            if has_low_rating:
                condition = f"({condition} OR is_flying IS NULL)"
            where_clauses.append(condition)
            params.extend(is_flying)
        
        species_col = None
        species_val = None
        if "bird_species_en" in filters:
            species_col = "bird_species_en"
            species_val = filters.get("bird_species_en")
        elif "bird_species_cn" in filters:
            species_col = "bird_species_cn"
            species_val = filters.get("bird_species_cn")
        
        if isinstance(species_val, str) and species_val.strip():
            where_clauses.append(f"{species_col} = ?")
            params.append(species_val.strip())
        
        where_sql = " AND ".join(where_clauses) if where_clauses else ""
        
        # 排序
        sort_by = filters.get("sort_by") or "filename"
        picked_only = filters.get("picked_only", False)
        
        if sort_by == "sharpness_desc":
            order = "ORDER BY COALESCE(adj_sharpness, head_sharp, -1e99) DESC, filename ASC"
        elif sort_by == "aesthetic_desc":
            order = "ORDER BY COALESCE(adj_topiq, nima_score, -1e99) DESC, filename ASC"
        else:
            order = "ORDER BY source_dir ASC, filename ASC"
        
        sql, base_params = self._build_union_sql(where=where_sql, order=order, extra_params=params)
        
        with self._lock:
            cursor = self._conn.execute(sql, base_params)
            results = [dict(row) for row in cursor.fetchall()]
        
        if picked_only and results:
            results.sort(key=lambda x: (
                x.get("adj_topiq", x.get("nima_score", -1e99)),
                x.get("adj_sharpness", x.get("head_sharp", -1e99)),
            ), reverse=True)
            num_to_keep = max(1, int(len(results) * 0.25))
            results = results[:num_to_keep]
            if sort_by == "sharpness_desc":
                results.sort(key=lambda x: -(x.get("adj_sharpness") or x.get("head_sharp") or -1e99))
            elif sort_by == "aesthetic_desc":
                results.sort(key=lambda x: -(x.get("adj_topiq") or x.get("nima_score") or -1e99))
            else:
                results.sort(key=lambda x: (x.get("source_dir", ""), x.get("filename", "")))
        
        return results
    
    def get_distinct_species(self, use_en: bool = False) -> List[str]:
        """获取所有目录的去重鸟种列表"""
        col = "bird_species_en" if use_en else "bird_species_cn"
        
        if not self._db_aliases:
            return []
        
        parts = []
        for alias in self._db_aliases:
            parts.append(f"SELECT DISTINCT {col} FROM {alias}.photos WHERE {col} IS NOT NULL AND {col} != ''")
        
        sql = f"SELECT DISTINCT {col} FROM ({' UNION '.join(parts)}) ORDER BY {col}"
        
        with self._lock:
            cursor = self._conn.execute(sql)
            return [row[0] for row in cursor.fetchall()]
    
    def get_statistics(self) -> dict:
        """汇总统计"""
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
    
    def close(self):
        """关闭连接"""
        try:
            for alias in self._db_aliases:
                self._conn.execute(f"DETACH DATABASE {alias}")
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass
    
    @property
    def directory(self):
        """兼容 ReportDB.directory 属性"""
        return self.root_dir


def find_processed_subdirs(root_dir: str) -> List[str]:
    """查找根目录及其子目录中所有已处理的目录"""
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
    
    return result
