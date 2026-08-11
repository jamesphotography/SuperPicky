#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky ReportDB - SQLite 报告数据库封装
替代原有的 CSV 报告存储，提供更高效的查询和更新操作。

Usage:
    db = ReportDB("/path/to/photos")
    db.insert_photo({"filename": "IMG_1234", "has_bird": 1, ...})
    photo = db.get_photo("IMG_1234")
    db.close()
"""

import os
import sqlite3
import time
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from .file_utils import ensure_hidden_directory


# Schema 版本，用于未来升级
SCHEMA_VERSION = "9"

# 所有列定义（有序），用于 CREATE TABLE 和数据验证
PHOTO_COLUMNS = [
    # (列名, SQLite 类型, 默认值)
    ("filename",      "TEXT NOT NULL UNIQUE", None),
    ("has_bird",      "INTEGER", 0),          # 0=no, 1=yes
    ("confidence",    "REAL", 0.0),
    ("head_sharp",    "REAL", None),
    ("left_eye",      "REAL", None),
    ("right_eye",     "REAL", None),
    ("beak",          "REAL", None),
    ("nima_score",    "REAL", None),
    ("is_flying",     "INTEGER", 0),          # 0=no, 1=yes
    ("flight_conf",   "REAL", None),
    ("rating",        "INTEGER", 0),          # -1/0/1/2/3
    ("picked",        "INTEGER", 0),          # 精选旗标:选鸟时 3★ 中美学∩锐度 top% 的交集(0/1)
    ("focus_status",  "TEXT", None),           # BEST/GOOD/BAD/WORST
    ("focus_x",       "REAL", None),
    ("focus_y",       "REAL", None),
    ("adj_sharpness", "REAL", None),
    ("adj_topiq",     "REAL", None),
    
    # V2: 相机设置
    ("iso",              "INTEGER", None),
    ("shutter_speed",    "TEXT", None),
    ("aperture",         "TEXT", None),
    ("focal_length",     "REAL", None),
    ("focal_length_35mm","INTEGER", None),
    ("camera_model",     "TEXT", None),
    ("lens_model",       "TEXT", None),
    
    # V2: GPS
    ("gps_latitude",     "REAL", None),
    ("gps_longitude",    "REAL", None),
    ("gps_altitude",     "REAL", None),
    
    # V2: IPTC 元数据
    ("title",            "TEXT", None),
    ("caption",          "TEXT", None),
    ("city",             "TEXT", None),
    ("state_province",   "TEXT", None),
    ("country",          "TEXT", None),
    
    # V2: 时间
    ("date_time_original", "TEXT", None),
    
    # V2: 鸟种识别
    ("bird_species_cn",  "TEXT", None),
    ("bird_species_en",  "TEXT", None),
    ("birdid_confidence","REAL", None),
    
    # V2: 曝光状态
    ("exposure_status",  "TEXT", None),
    
    # V3: 文件路径（相对路径）
    ("original_path",    "TEXT", None),
    ("current_path",     "TEXT", None),
    ("temp_jpeg_path",   "TEXT", None),
    ("debug_crop_path",  "TEXT", None),   # 裁切鸟+mask (crop_debug/)
    ("yolo_debug_path",  "TEXT", None),   # 全图+YOLO框 (yolo_debug/)
    
    # V5: 连拍分组
    ("burst_id",         "INTEGER", None),
    ("burst_position",   "INTEGER", None),

    # V6: 懂鸟罕见指数 (0-10，越大越罕见)
    # V6: BirdID rarity index (0-10, higher = rarer)
    ("rarity_index",     "REAL", None),

    # V7: IUCN 红色名录保护级别 (LC/NT/VU/EN/CR/CR(PE)/CR(PEW)/EW/EX/DD/NE)
    # V7: IUCN Red List category
    ("iucn_category",    "TEXT", None),

    # V8: GBIF 全球罕见度 (0-100 分制，越大越罕见，CC0+CC-BY 4.0 子集派生)
    # V8: GBIF-derived global rarity score (0-100, higher = rarer)
    ("gbif_rarity_100",  "REAL", None),

    # V9: iRateBird 鸟种美学(颜值)指数 (0-100，越大越好看，CC-BY 4.0 派生)
    # V9: iRateBird species aesthetic score (0-100, higher = prettier)
    ("aesthetic_index",  "REAL", None),

    ("created_at",    "TEXT", None),
    ("updated_at",    "TEXT", None),
]

# 列名集合，用于快速查找
COLUMN_NAMES = {col[0] for col in PHOTO_COLUMNS}


class ReportDB:
    """SQLite 报告数据库封装。

    每个照片处理目录拥有一个独立的数据库文件：
        <directory>/.superpicky/report.db

    线程安全：设置 check_same_thread=False，支持工作线程写入。
    WAL 模式：支持读写并发。
    """

    DB_FILENAME = "report.db"

    def __init__(self, directory: str):
        """
        初始化数据库连接。

        Args:
            directory: 照片目录路径（数据库存储在 .superpicky/ 子目录下）
        """
        self.directory = directory
        self._superpicky_dir = os.path.join(directory, ".superpicky")
        self.db_path = os.path.join(self._superpicky_dir, self.DB_FILENAME)
        # 同一连接会被主线程和后台线程复用，需要串行化访问避免事务冲突
        self._lock = threading.RLock()

        # 确保 .superpicky 目录存在并隐藏（Windows 下设置 Hidden 属性）
        ensure_hidden_directory(self._superpicky_dir)

        # 连接数据库
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row  # 支持按列名访问

        # 启用 WAL 模式和外键
        self._conn.execute("PRAGMA journal_mode=WAL")
        # WAL 搭配 NORMAL:每次 commit 不再单独 fsync(默认 FULL 每 commit 一次),
        # 仅 checkpoint 时同步。主处理循环每张照片 2-6 次 commit,在 SD 卡/ExFAT/
        # HDD 上每次 fsync 10-30ms;NORMAL 断电最多丢最后一批事务,库不会损坏。
        # WAL + NORMAL: commits no longer fsync individually (default FULL
        # syncs every commit); only checkpoints do. The main loop commits 2-6
        # times per photo, and on SD/ExFAT/HDD each fsync costs 10-30ms.
        # NORMAL may lose the last batch on power loss but never corrupts.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # 初始化 Schema
        self._init_schema()

    def _init_schema(self):
        """创建表和索引（如果不存在）。"""
        # 构建 CREATE TABLE 语句
        col_defs = []
        for name, type_def, _ in PHOTO_COLUMNS:
            col_defs.append(f"    {name} {type_def}")

        create_sql = (
            "CREATE TABLE IF NOT EXISTS photos (\n"
            "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            + ",\n".join(col_defs)
            + "\n)"
        )

        with self._lock:
            with self._conn:
                self._conn.execute(create_sql)

                # 索引
                self._conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_photos_filename "
                    "ON photos(filename)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photos_rating "
                    "ON photos(rating)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_photos_has_bird "
                    "ON photos(has_bird)"
                )

                # 元数据表
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)

                # 纠错样本表（correction submission）：随项目持久化。
                # 记录一次「改鸟种」事件的原预测(wrong_*)与改正结果(corrected_*)。
                # Corrections table for the correction-submission feature.
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        wrong_cn TEXT,
                        wrong_en TEXT,
                        corrected_model_class_id INTEGER,
                        corrected_cn TEXT,
                        corrected_en TEXT,
                        birdid_confidence REAL,
                        created_at TEXT
                    )
                """)
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_corrections_filename "
                    "ON corrections(filename)"
                )

                # 初始化元数据
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    ("schema_version", SCHEMA_VERSION)
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                    ("directory_path", self.directory)
                )

        # Schema 升级在独立事务中执行，避免嵌套 commit 冲突
        self._upgrade_schema_if_needed()
        # 逐版本迁移之后再按 PHOTO_COLUMNS 兜底补列（见该方法说明）
        self._reconcile_photo_columns()

    def _reconcile_photo_columns(self):
        """
        以 PHOTO_COLUMNS 为准补齐 photos 表缺失的列。

        为什么需要这一步：建表走的是 ``CREATE TABLE IF NOT EXISTS``，对已存在
        的旧表不做任何改动；而逐版本迁移只补各自版本显式列出的列。一旦有人往
        PHOTO_COLUMNS 加了列却忘记同步写 ALTER(picked 列正是如此)，旧目录的
        report.db 就永远拿不到该列，读取时抛
        ``sqlite3.OperationalError: no such column``。本方法作为最终兜底，
        使「PHOTO_COLUMNS 是唯一事实源」这一约定对新旧库都成立。

        Reconcile the photos table against PHOTO_COLUMNS. Table creation uses
        CREATE TABLE IF NOT EXISTS (a no-op on existing tables) and the
        versioned migrations only add the columns each version enumerates, so a
        column added to PHOTO_COLUMNS without a matching ALTER (exactly what
        happened to `picked`) never reaches legacy databases and later raises
        "no such column". This final pass makes PHOTO_COLUMNS the single source
        of truth for both new and legacy databases.

        返回 / Returns:
            list[str]: 本次补齐的列名，便于日志与测试断言。
        """

        added = []
        with self._lock:
            existing = {
                row[1] for row in self._conn.execute("PRAGMA table_info(photos)")
            }
            missing = [c for c in PHOTO_COLUMNS if c[0] not in existing]
            if not missing:
                return added

            with self._conn:
                for name, type_def, default in missing:
                    upper = type_def.upper()
                    # SQLite 的 ALTER TABLE ADD COLUMN 不支持 UNIQUE/PRIMARY KEY，
                    # 也不允许在无默认值时添加 NOT NULL 列。这类列只可能出现在
                    # 建表定义里(如 filename)，正常不会缺失；真缺失时只能重建表，
                    # 这里明确告警而不是抛异常，避免整个目录打不开。
                    # SQLite cannot ADD COLUMN with UNIQUE/PRIMARY KEY, nor a
                    # NOT NULL column without a default. Such columns exist only
                    # in the initial definition and should never be missing;
                    # warn instead of raising so the directory still opens.
                    if "UNIQUE" in upper or "PRIMARY KEY" in upper or (
                        "NOT NULL" in upper and default is None
                    ):
                        print(
                            f"⚠️ report.db 缺少列 {name}，但其约束({type_def})"
                            f"无法通过 ALTER 添加，需重建数据库"
                        )
                        continue

                    statement = f"ALTER TABLE photos ADD COLUMN {name} {type_def}"
                    if default is not None:
                        statement += f" DEFAULT {self._sql_literal(default)}"
                    self._conn.execute(statement)
                    added.append(name)

        if added:
            print(f"✅ report.db 补齐缺失列: {', '.join(added)}")
        return added

    @staticmethod
    def _sql_literal(value):
        """
        把默认值渲染成 DDL 字面量。/ Render a default value as a DDL literal.

        DDL 不支持参数占位，只能拼接，因此字符串需转义单引号。
        DDL cannot use placeholders, so strings are quoted and escaped here.
        """

        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def _upgrade_schema_if_needed(self):
        """检查并升级数据库 Schema（支持连续升级 v1 -> v2 -> v3 -> v4）"""
        with self._lock:
            # 获取当前 schema 版本
            cursor = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            )
            row = cursor.fetchone()
            current_version = row[0] if row else "1"

            # ----------------------------------------------------------------------
            #  Upgrade: v1 -> v2 (EXIF metadata)
            # ----------------------------------------------------------------------
            if current_version == "1":
                print("🔄 Upgrading database schema from v1 to v2...")
                new_columns = [
                    ("iso", "INTEGER"),
                    ("shutter_speed", "TEXT"),
                    ("aperture", "TEXT"),
                    ("focal_length", "REAL"),
                    ("focal_length_35mm", "INTEGER"),
                    ("camera_model", "TEXT"),
                    ("lens_model", "TEXT"),
                    ("gps_latitude", "REAL"),
                    ("gps_longitude", "REAL"),
                    ("gps_altitude", "REAL"),
                    ("title", "TEXT"),
                    ("caption", "TEXT"),
                    ("city", "TEXT"),
                    ("state_province", "TEXT"),
                    ("country", "TEXT"),
                    ("date_time_original", "TEXT"),
                    ("bird_species_cn", "TEXT"),
                    ("bird_species_en", "TEXT"),
                    ("birdid_confidence", "REAL"),
                    ("exposure_status", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("2")
                current_version = "2"
                print("✅ Database schema upgraded to v2")

            # ----------------------------------------------------------------------
            #  Upgrade: v2 -> v3 (File paths)
            # ----------------------------------------------------------------------
            if current_version == "2":
                print("🔄 Upgrading database schema from v2 to v3...")
                new_columns_v3 = [
                    ("original_path", "TEXT"),
                    ("current_path", "TEXT"),
                    ("temp_jpeg_path", "TEXT"),
                    ("debug_crop_path", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v3:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("3")
                current_version = "3"
                print("✅ Database schema upgraded to v3")

            # ----------------------------------------------------------------------
            #  Upgrade: v3 -> v4 (Check debug images)
            # ----------------------------------------------------------------------
            if current_version == "3":
                print("🔄 Upgrading database schema from v3 to v4...")
                new_columns_v4 = [
                    ("yolo_debug_path", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v4:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("4")
                current_version = "4"
                print("✅ Database schema upgraded to v4")

            # ----------------------------------------------------------------------
            #  Upgrade: v4 -> v5 (Burst id and position)
            # ----------------------------------------------------------------------
            if current_version == "4":
                print("🔄 Upgrading database schema from v4 to v5...")
                new_columns_v5 = [
                    ("burst_id", "INTEGER"),
                    ("burst_position", "INTEGER"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v5:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("5")
                current_version = "5"
                print("✅ Database schema upgraded to v5")

            # ----------------------------------------------------------------------
            #  Upgrade: v5 -> v6 (BirdID rarity index)
            # ----------------------------------------------------------------------
            if current_version == "5":
                print("🔄 Upgrading database schema from v5 to v6...")
                new_columns_v6 = [
                    ("rarity_index", "REAL"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v6:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("6")
                current_version = "6"
                print("✅ Database schema upgraded to v6")

            # ----------------------------------------------------------------------
            #  Upgrade: v6 -> v7 (IUCN Red List category)
            # ----------------------------------------------------------------------
            if current_version == "6":
                print("🔄 Upgrading database schema from v6 to v7...")
                new_columns_v7 = [
                    ("iucn_category", "TEXT"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v7:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("7")
                current_version = "7"
                print("✅ Database schema upgraded to v7")

            # ----------------------------------------------------------------------
            #  Upgrade: v7 -> v8 (GBIF 0-100 rarity score)
            # ----------------------------------------------------------------------
            if current_version == "7":
                print("🔄 Upgrading database schema from v7 to v8...")
                new_columns_v8 = [
                    ("gbif_rarity_100", "REAL"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v8:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("8")
                current_version = "8"
                print("✅ Database schema upgraded to v8")

            # ----------------------------------------------------------------------
            #  Upgrade: v8 -> v9 (iRateBird species aesthetic index)
            # ----------------------------------------------------------------------
            if current_version == "8":
                print("🔄 Upgrading database schema from v8 to v9...")
                new_columns_v9 = [
                    ("aesthetic_index", "REAL"),
                ]
                with self._conn:
                    for col_name, col_type in new_columns_v9:
                        try:
                            self._conn.execute(
                                f"ALTER TABLE photos ADD COLUMN {col_name} {col_type}"
                            )
                        except sqlite3.OperationalError:
                            pass  # 列已存在，跳过
                    self._update_schema_version("9")
                current_version = "9"
                print("✅ Database schema upgraded to v9")

    def _update_schema_version(self, version):
        """更新数据库中的版本号（由调用方负责提交事务）"""
        with self._lock:
            self._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (version,)
            )

    # ==========================================================================
    #  写入操作
    # ==========================================================================

    def insert_photo(self, data: dict) -> None:
        """
        插入或更新一条照片记录。

        如果 filename 已存在则更新，否则插入新记录。
        自动处理 CSV 兼容的数据格式转换（如 "yes"/"no" → 1/0）。

        Args:
            data: 照片数据字典，键为列名
        """
        cleaned = self._clean_data(data)
        now = _now_iso()
        cleaned.setdefault("created_at", now)
        cleaned["updated_at"] = now

        # 仅保留合法列
        columns = [k for k in cleaned if k in COLUMN_NAMES]
        values = [cleaned[k] for k in columns]

        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(columns)

        # INSERT OR REPLACE
        update_clause = ", ".join(
            f"{c} = excluded.{c}" for c in columns if c != "filename"
        )

        sql = (
            f"INSERT INTO photos ({col_str}) VALUES ({placeholders}) "
            f"ON CONFLICT(filename) DO UPDATE SET {update_clause}"
        )

        with self._lock:
            self._conn.execute(sql, values)
            self._safe_commit()

    def insert_photos_batch(self, photos: List[dict]) -> int:
        """
        批量插入或更新照片记录。

        使用事务包裹，性能优于逐条插入。

        Args:
            photos: 照片数据字典列表

        Returns:
            成功插入/更新的记录数
        """
        if not photos:
            return 0

        now = _now_iso()
        count = 0

        with self._lock:
            with self._conn:
                for data in photos:
                    cleaned = self._clean_data(data)
                    cleaned.setdefault("created_at", now)
                    cleaned["updated_at"] = now

                    columns = [k for k in cleaned if k in COLUMN_NAMES]
                    values = [cleaned[k] for k in columns]

                    placeholders = ", ".join(["?"] * len(columns))
                    col_str = ", ".join(columns)

                    update_clause = ", ".join(
                        f"{c} = excluded.{c}" for c in columns if c != "filename"
                    )

                    sql = (
                        f"INSERT INTO photos ({col_str}) VALUES ({placeholders}) "
                        f"ON CONFLICT(filename) DO UPDATE SET {update_clause}"
                    )

                    self._conn.execute(sql, values)
                    count += 1

        return count

    # ==========================================================================
    #  查询操作
    # ==========================================================================

    def get_photo(self, filename: str) -> Optional[dict]:
        """
        按 filename 查询单条记录。

        Args:
            filename: 照片文件名（不含扩展名）

        Returns:
            照片数据字典，未找到返回 None
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE filename = ?", (filename,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_photos(self) -> List[dict]:
        """
        获取所有照片记录。

        Returns:
            照片数据字典列表
        """
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM photos ORDER BY filename")
            return [dict(row) for row in cursor.fetchall()]

    def get_bird_photos(self) -> List[dict]:
        """
        获取所有有鸟的照片记录（has_bird=1）。

        Returns:
            有鸟照片数据字典列表
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE has_bird = 1 ORDER BY filename"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_photos_by_rating(self, rating: int) -> List[dict]:
        """
        按评分查询照片。

        Args:
            rating: 评分 (-1/0/1/2/3)

        Returns:
            照片数据字典列表
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE rating = ? ORDER BY filename",
                (rating,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_photos_by_burst_id(self, burst_id: int, **kwargs) -> List[dict]:
        """
        按 burst_id 查询同组所有连拍照片。
        **kwargs 仅用于与 MergedReportDB 签名兼容，在此忽略。

        Args:
            burst_id: 连拍组 ID

        Returns:
            该组所有照片的数据字典列表，按 burst_position / filename 排序
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE burst_id = ? ORDER BY burst_position, filename",
                (burst_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_distinct_species(self, use_en: bool = False, ratings: list = None) -> List[str]:
        """
        获取数据库中去重后的鸟种名称列表（用于结果浏览器筛选下拉框）。

        Args:
            use_en: True 使用英文鸟种列，False 使用中文鸟种列
            ratings: 若提供，只返回在这些星级下有照片的鸟种

        Returns:
            鸟种名称列表（已去重、去空值）
        """
        column = "bird_species_en" if use_en else "bird_species_cn"
        assert column in {"bird_species_en", "bird_species_cn"}, f"Invalid column: {column}"
        order_clause = f"{column} COLLATE NOCASE" if use_en else column

        where_clauses = [
            f"{column} IS NOT NULL",
            f"TRIM({column}) != ''",
            "rating != -1",
        ]
        params: List[Any] = []

        if isinstance(ratings, list):
            valid = [r for r in ratings if r != -1]
            if valid:
                placeholders = ", ".join(["?"] * len(valid))
                where_clauses.append(f"rating IN ({placeholders})")
                params.extend(valid)

        where_sql = " AND ".join(where_clauses)
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT DISTINCT {column} FROM photos WHERE {where_sql} ORDER BY {order_clause}",
                params
            )
            return [row[0] for row in cursor.fetchall()]

    def get_photos_by_filters(self, filters: Optional[dict] = None) -> List[dict]:
        """
        按结果浏览器筛选条件查询照片。

        支持的 filters 键：
            - ratings: List[int]
            - focus_statuses: List[str]
            - is_flying: List[int]
            - bird_species_cn / bird_species_en: str
            - sort_by: filename | sharpness_desc | aesthetic_desc
            - picked_only: bool (如果为 True，则在结果集中筛选出 adj_topiq 和 adj_sharpness 均排名前 25% 的照片)
        """
        filters = filters or {}

        where_clauses = []
        params: List[Any] = []

        ratings = filters.get("ratings")

        if isinstance(ratings, list):
            if not ratings:
                return []
            placeholders = ", ".join(["?"] * len(ratings))
            where_clauses.append(f"rating IN ({placeholders})")
            params.extend(ratings)

        # 是否包含低评分（0 星），这类照片 focus_status/is_flying 可能是 NULL
        has_low_rating = ratings is None or (isinstance(ratings, list) and any(r <= 0 for r in ratings))

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
            assert species_col in {"bird_species_en", "bird_species_cn"}, f"Invalid column: {species_col}"
            where_clauses.append(f"{species_col} = ?")
            params.append(species_val.strip())

        # 精选(picked):直接用选鸟时写入的持久旗标列(3★ 中美学∩锐度 top% 的交集)。
        # 旧目录(未重跑选鸟)该列全为 0,需重跑后才有结果。
        if filters.get("picked_only", False):
            where_clauses.append("picked = 1")

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        # Determine sort order
        sort_by = filters.get("sort_by") or "filename"
        if sort_by == "sharpness_desc":
            order_sql = "ORDER BY COALESCE(adj_sharpness, head_sharp, -1e99) DESC, filename ASC"
        elif sort_by == "aesthetic_desc":
            order_sql = "ORDER BY COALESCE(adj_topiq, nima_score, -1e99) DESC, filename ASC"
        elif sort_by == "rarity_desc":
            # V4.2.7: 按 GBIF 罕见度降序（最罕见在前）— 无 GBIF 数据的排最后
            order_sql = "ORDER BY COALESCE(gbif_rarity_100, -1e99) DESC, filename ASC"
        elif sort_by == "species_beauty_desc":
            # V9: 按鸟种颜值(iRateBird)降序 — 无数据排最后
            # V9: sort by species beauty (iRateBird) desc — missing data last
            order_sql = "ORDER BY COALESCE(aesthetic_index, -1e99) DESC, filename ASC"
        else:
            order_sql = "ORDER BY filename ASC"

        sql = f"SELECT * FROM photos {where_sql} {order_sql}"

        with self._lock:
            cursor = self._conn.execute(sql, params)
            results = [dict(row) for row in cursor.fetchall()]

        return results

    # ==========================================================================
    #  纠错样本（correction submission）
    # ==========================================================================

    def insert_correction(self, data: dict) -> None:
        """
        插入一条纠错记录（每次「改鸟种」事件一条）。

        参数 / Args:
            data: 键含 filename, wrong_cn, wrong_en, corrected_model_class_id,
                  corrected_cn, corrected_en, birdid_confidence。created_at 自动填。
        """
        cols = ("filename", "wrong_cn", "wrong_en", "corrected_model_class_id",
                "corrected_cn", "corrected_en", "birdid_confidence", "created_at")
        row = {k: data.get(k) for k in cols}
        row["created_at"] = _now_iso()
        placeholders = ", ".join(["?"] * len(cols))
        col_str = ", ".join(cols)
        sql = f"INSERT INTO corrections ({col_str}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute(sql, [row[c] for c in cols])
            self._safe_commit()

    def get_corrections(self) -> List[dict]:
        """返回全部纠错记录，按 created_at 升序。"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM corrections ORDER BY created_at, id"
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_photos_by_species(
        self,
        cn: Optional[str] = None,
        en: Optional[str] = None,
        exclude_filename: Optional[str] = None,
    ) -> List[dict]:
        """
        查当前项目内同鸟种、可当正样本的照片（has_bird=1 且 rating!=-1）。

        cn 优先；cn 为空时用 en。exclude_filename 排除被改正图自身。
        """
        col = None
        val = None
        if cn and cn.strip():
            col, val = "bird_species_cn", cn.strip()
        elif en and en.strip():
            col, val = "bird_species_en", en.strip()
        if col is None:
            return []
        assert col in {"bird_species_cn", "bird_species_en"}
        sql = (
            f"SELECT * FROM photos WHERE {col} = ? AND has_bird = 1 "
            "AND rating != -1"
        )
        params: List[Any] = [val]
        if exclude_filename:
            sql += " AND filename != ?"
            params.append(exclude_filename)
        sql += " ORDER BY filename"
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_statistics(self) -> dict:
        """
        获取评分统计信息。

        Returns:
            包含统计数据的字典，如:
            {
                "total": 217,
                "has_bird": 180,
                "flying": 15,
                "by_rating": {0: 50, 1: 60, 2: 45, 3: 25}
            }
        """
        stats = {}

        with self._lock:
            # 总数
            row = self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()
            stats["total"] = row[0]

            # 有鸟数
            row = self._conn.execute(
                "SELECT COUNT(*) FROM photos WHERE has_bird = 1"
            ).fetchone()
            stats["has_bird"] = row[0]

            # 飞行数
            row = self._conn.execute(
                "SELECT COUNT(*) FROM photos WHERE is_flying = 1"
            ).fetchone()
            stats["flying"] = row[0]

            # 按评分统计
            cursor = self._conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM photos GROUP BY rating ORDER BY rating"
            )
            stats["by_rating"] = {row[0]: row[1] for row in cursor.fetchall()}

        return stats

    def count(self) -> int:
        """返回总记录数。"""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM photos").fetchone()
            return row[0]

    def exists(self) -> bool:
        """数据库文件是否存在。"""
        return os.path.exists(self.db_path)

    # ==========================================================================
    #  更新操作
    # ==========================================================================

    def update_photo(self, filename: str, data: dict) -> bool:
        """
        按 filename 更新指定字段。

        Args:
            filename: 照片文件名
            data: 要更新的字段字典（仅包含需要更新的字段）

        Returns:
            是否成功更新
        """
        cleaned = self._clean_data(data)
        cleaned["updated_at"] = _now_iso()

        # 仅保留合法列，排除 filename 和 id
        columns = [k for k in cleaned if k in COLUMN_NAMES and k not in ("filename", "id")]
        if not columns:
            return False

        values = [cleaned[k] for k in columns]
        set_clause = ", ".join(f"{c} = ?" for c in columns)

        sql = f"UPDATE photos SET {set_clause} WHERE filename = ?"
        values.append(filename)

        with self._lock:
            cursor = self._conn.execute(sql, values)
            self._safe_commit()
            return cursor.rowcount > 0

    def update_burst_ids(self, burst_map: dict) -> int:
        """
        批量更新照片的 burst_id 和 burst_position。
        
        Args:
            burst_map: 字典，格式为
                {filename: (burst_id, burst_position)} 或
                {(source_dir, filename): (burst_id, burst_position)}
            
        Returns:
            成功更新的记录数
        """
        if not burst_map:
            return 0
            
        updates = []
        now = _now_iso()
        for photo_key, (bid, pos) in burst_map.items():
            if isinstance(photo_key, tuple):
                filename = photo_key[-1]
            else:
                filename = photo_key
            if not filename:
                continue
            updates.append((bid, pos, now, filename))
            
        sql = """
        UPDATE photos 
        SET burst_id = ?, burst_position = ?, updated_at = ? 
        WHERE filename = ?
        """
        
        with self._lock:
            cursor = self._conn.executemany(sql, updates)
            self._safe_commit()
            return cursor.rowcount

    def clear_burst_ids(self) -> int:
        """清空全部连拍分组字段。"""
        sql = """
        UPDATE photos
        SET burst_id = NULL, burst_position = NULL, updated_at = ?
        WHERE burst_id IS NOT NULL OR burst_position IS NOT NULL
        """
        with self._lock:
            cursor = self._conn.execute(sql, [_now_iso()])
            self._safe_commit()
            return cursor.rowcount

    def delete_photo(self, filename: str) -> bool:
        """从 photos 表中删除指定文件名的记录。

        Args:
            filename: 照片文件名

        Returns:
            是否成功删除
        """
        sql = "DELETE FROM photos WHERE filename = ?"
        with self._lock:
            cursor = self._conn.execute(sql, [filename])
            self._safe_commit()
            return cursor.rowcount > 0

    def clear_cache_paths(self) -> int:
        """清空缓存相关路径字段（临时 JPG、调试裁切、YOLO 调试图）。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE photos SET debug_crop_path = NULL, temp_jpeg_path = NULL, yolo_debug_path = NULL"
            )
            self._safe_commit()
            return cursor.rowcount

    # ==========================================================================
    #  元数据操作
    # ==========================================================================

    def get_meta(self, key: str) -> Optional[str]:
        """获取元数据值。"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """设置元数据值。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value)
            )
            self._safe_commit()

    # ==========================================================================
    #  同步预留
    # ==========================================================================

    def get_updated_since(self, since: str) -> List[dict]:
        """
        获取指定时间之后更新的记录（增量同步用）。

        Args:
            since: ISO 8601 时间字符串

        Returns:
            更新记录列表
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM photos WHERE updated_at > ? ORDER BY updated_at",
                (since,)
            )
            return [dict(row) for row in cursor.fetchall()]

    # ==========================================================================
    #  连接管理
    # ==========================================================================

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ==========================================================================
    #  内部方法
    # ==========================================================================

    def _safe_commit(self) -> None:
        """仅在存在活动事务时提交，兼容 autocommit 场景。"""
        if not self._conn:
            return
        try:
            if self._conn.in_transaction:
                self._conn.commit()
        except sqlite3.OperationalError as e:
            # 某些运行时在 autocommit 下会抛 "no transaction is active"
            if "no transaction is active" in str(e).lower():
                return
            raise

    @staticmethod
    def _clean_data(data: dict) -> dict:
        """
        清洗输入数据，处理 CSV 兼容格式转换。

        转换规则：
        - "yes"/"no" → 1/0（仅对 has_bird, is_flying 字段）
        - "-" 或空字符串 → None
        - 数值字符串 → 对应的 float/int
        """
        cleaned = {}
        for key, value in data.items():
            # 跳过非法列名
            if key not in COLUMN_NAMES:
                continue

            # 布尔/yes-no 字段（优先处理，"-"/None/空 → 0）
            if key in ("has_bird", "is_flying"):
                if value is None or value == "-" or value == "":
                    cleaned[key] = 0
                elif isinstance(value, str):
                    cleaned[key] = 1 if value.lower() in ("yes", "1", "true") else 0
                else:
                    cleaned[key] = 1 if value else 0
                continue

            # 处理 None 和占位符
            if value is None or value == "-" or value == "":
                cleaned[key] = None
                continue

            # 数值字段
            if key in ("confidence", "head_sharp", "left_eye", "right_eye",
                        "beak", "nima_score", "flight_conf", "focus_x",
                        "focus_y", "adj_sharpness", "adj_topiq",
                        # V2: 新增数值字段
                        "focal_length", "gps_latitude", "gps_longitude",
                        "gps_altitude", "birdid_confidence"):
                try:
                    cleaned[key] = float(value)
                except (ValueError, TypeError):
                    cleaned[key] = None
                continue

            # 整数字段
            if key in ("rating", "iso", "focal_length_35mm"):
                try:
                    cleaned[key] = int(float(value))
                except (ValueError, TypeError):
                    cleaned[key] = 0 if key == "rating" else None
                continue

            # 文本字段直接使用（包括 V2 新增的文本字段）
            # shutter_speed, aperture, camera_model, lens_model,
            # title, caption, city, state_province, country,
            # date_time_original, bird_species_cn, bird_species_en, exposure_status
            cleaned[key] = value

        return cleaned


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
