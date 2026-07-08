#!/usr/bin/env python3
"""
鸟类数据库管理器
使用SQLite数据库替代JSON文件，提供完整的鸟类信息和eBird映射
"""
import sqlite3
import os
from typing import List, Dict, Optional, Tuple, Set
import json
from tools.i18n import t as _t

class BirdDatabaseManager:
    def __init__(self, db_path: str = None):
        """
        初始化鸟类数据库管理器

        Args:
            db_path: SQLite数据库路径，默认为 birdid/data/bird_reference.sqlite
        """
        if db_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(script_dir, 'data', 'bird_reference.sqlite')
        
        self.db_path = db_path
        
        # 检查数据库文件是否存在
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"数据库文件不存在: {db_path}")
        
        # 测试数据库连接
        self._test_connection()

        # AviList 名称映射缓存（懒加载，首次调用时填充）
        self._avilist_cache: Optional[Dict] = None

        print(f"BirdDatabaseManager initialized with database: {db_path}")
    
    def _test_connection(self):
        """测试数据库连接"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM BirdCountInfo")
                count = cursor.fetchone()[0]
                print(_t("logs.db_connected", count=count))
        except Exception as e:
            raise ConnectionError(f"数据库连接失败: {e}")
    
    def get_bird_by_class_id(self, class_id: int) -> Optional[Dict]:
        """
        根据模型类别ID获取鸟类信息

        Args:
            class_id: 鸟类类别ID（对应模型输出的索引）

        Returns:
            包含鸟类信息的字典，如果未找到返回None
        """
        # class_id对应数据库中的model_class_id字段
        query = """
        SELECT id, english_name, chinese_simplified, chinese_traditional,
               scientific_name, ebird_code, short_description_zh
        FROM BirdCountInfo
        WHERE model_class_id = ?
        """

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, (class_id,))
                result = cursor.fetchone()

                if result:
                    return {
                        'id': result[0],
                        'english_name': result[1],
                        'chinese_simplified': result[2],
                        'chinese_traditional': result[3],
                        'scientific_name': result[4],
                        'ebird_code': result[5],
                        'short_description_zh': result[6]
                    }
                return None
        except Exception as e:
            print(_t("logs.db_query_failed", id=class_id, e=e))
            return None

    def get_class_id_by_scientific_name(
        self, scientific_name: str, english_name: Optional[str] = None
    ) -> Optional[int]:
        """
        名字 → model_class_id 反查（改鸟种打标用）。

        优先按学名精确匹配（IOC 学名 = BirdCountInfo.scientific_name 最稳），
        未命中时回退英文名精确匹配；仍无返回 None（调用方标黄跳过，不瞎猜）。

        参数 / Args:
            scientific_name: 学名（IOC latin_name）。
            english_name: 英文名，学名未命中时的回退键。

        返回 / Returns:
            model_class_id（int）或 None。
        """
        def _query(col: str, value: str) -> Optional[int]:
            if not value or not value.strip():
                return None
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        f"SELECT model_class_id FROM BirdCountInfo "
                        f"WHERE {col} = ? AND model_class_id IS NOT NULL LIMIT 1",
                        (value.strip(),),
                    )
                    row = cur.fetchone()
                    return int(row[0]) if row and row[0] is not None else None
            except Exception as e:
                print(_t("logs.db_query_failed", id=value, e=e))
                return None

        cid = _query("scientific_name", scientific_name)
        if cid is not None:
            return cid
        if english_name:
            return _query("english_name", english_name)
        return None

    def get_gbif_rarity_by_class_id(
        self, class_id: int, country_code: Optional[str] = None
    ) -> Optional[float]:
        """
        根据模型类别ID获取 GBIF 罕见度（0-100 分）。

        Args:
            class_id: 鸟类类别ID（model_class_id）
            country_code: ISO 3166-1 alpha-2 国家代码（如 "CN"/"AU"）。
                          提供时优先查该国家的 rarity，未命中再回退到全球。

        Returns:
            GBIF 罕见度 (0-100, 越大越罕见; None=数据库未匹配)

        Fetch GBIF-derived rarity score (0-100). When country_code is given,
        prefer the country-specific score (table gbif_rarity_by_country);
        fall back to the global table on miss.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # 1) 优先查 country-specific 分数（gbif_rarity_by_country 表）
                # 注意 inner try/except：该表可能不存在（未启用 country-aware 时），
                # 不能让 OperationalError 把整个方法短路掉，否则全球 fallback 不会被执行。
                # Country-specific lookup with an inner try/except so a missing
                # gbif_rarity_by_country table does not short-circuit the
                # function before the global fallback runs.
                if country_code:
                    try:
                        cursor.execute(
                            "SELECT gbif_rarity_100 FROM gbif_rarity_by_country "
                            "WHERE model_class_id = ? AND countrycode = ? "
                            "AND gbif_rarity_100 IS NOT NULL LIMIT 1",
                            (class_id, country_code),
                        )
                        row = cursor.fetchone()
                        if row and row[0] is not None:
                            return float(row[0])
                    except sqlite3.OperationalError:
                        # 表不存在 → 走全球 fallback / Missing table → global fallback
                        pass

                # 2) Fallback 全球 + 手动 override 检查
                # Global fallback + manual override check
                cursor.execute(
                    "SELECT gbif_rarity_100, scientific_name FROM gbif_rarity_100 "
                    "WHERE model_class_id = ? AND gbif_rarity_100 IS NOT NULL LIMIT 1",
                    (class_id,),
                )
                row = cursor.fetchone()
                if row and row[0] is not None:
                    # V4.2.7: 检查 core.rarity_tier 的 HARDCODE_OVERRIDES 表，
                    # 对极少数 GBIF 算法明显失真的鸟做学名级硬编码降级。
                    # V4.2.7: Consult core.rarity_tier.HARDCODE_OVERRIDES first
                    # so manual corrections for taxonomic splits / data gaps
                    # take precedence over the raw GBIF score.
                    try:
                        from core.rarity_tier import get_score_override
                        ov = get_score_override(row[1])
                        if ov is not None:
                            return ov
                    except Exception:
                        pass
                    return float(row[0])
                return None
        except Exception:
            # gbif_rarity_100 表也不存在（旧版数据库），静默降级
            return None

    def get_iucn_by_class_id(self, class_id: int) -> Optional[str]:
        """
        根据模型类别ID获取 IUCN 红色名录保护级别

        Args:
            class_id: 鸟类类别ID（对应模型输出的索引，即 model_class_id）

        Returns:
            IUCN 等级缩写（LC/NT/VU/EN/CR/CR(PE)/CR(PEW)/EW/EX/DD/NE），
            未找到或 avilist_map 无 IUCN 数据时返回 None

        Fetch IUCN Red List category for a model class id.
        Returns one of LC/NT/VU/EN/CR/CR(PE)/CR(PEW)/EW/EX/DD/NE, or None when
        the row is missing or the column is empty.
        """
        query = (
            "SELECT iucn_category FROM avilist_map "
            "WHERE model_class_id = ? AND iucn_category IS NOT NULL "
            "AND iucn_category != '' LIMIT 1"
        )

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, (class_id,))
                result = cursor.fetchone()

                if result and result[0]:
                    return str(result[0])
                return None
        except Exception:
            # avilist_map 表可能不存在（旧版数据库），静默降级
            return None

    def get_gbif_rarity_by_scientific_name(
        self, scientific_name: str
    ) -> Optional[float]:
        """
        直接按学名查询「全球 GBIF 罕见度」（0-100 分），不经 model_class_id。

        gbif_rarity_100 表自带 scientific_name 列，其学名取自 GBIF backbone，
        与 BirdCountInfo.scientific_name（eBird/Clements 体系）并不完全一致。
        因此「按学名直查 gbif」能命中一批「经 BirdCountInfo 跳转会丢」的种
        （实测对 IOC 名覆盖 95.3% vs 经 BirdCountInfo 的 94.7%）。命中后同样
        应用 core.rarity_tier.HARDCODE_OVERRIDES 学名级硬编码降级。

        Args:
            scientific_name: 学名/拉丁名，按库内存储原样匹配（区分大小写）。

        Returns:
            全球罕见度 (0-100, 越大越罕见)；未命中或无分数返回 None。

        Fetch the global GBIF rarity score directly from gbif_rarity_100 by its
        own scientific_name column, bypassing the BirdCountInfo → model_class_id
        hop. Returns None on miss.
        """
        if not scientific_name:
            return None
        name = scientific_name.strip()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT gbif_rarity_100, scientific_name FROM gbif_rarity_100 "
                    "WHERE scientific_name = ? AND gbif_rarity_100 IS NOT NULL LIMIT 1",
                    (name,),
                )
                row = cursor.fetchone()
            if row and row[0] is not None:
                try:
                    from core.rarity_tier import get_score_override
                    override = get_score_override(row[1])
                    if override is not None:
                        return override
                except Exception:
                    pass
                return float(row[0])
            return None
        except Exception:
            # gbif_rarity_100 表不存在（旧库）→ 静默降级 / Missing table → None.
            return None

    def get_extra_info_by_scientific_name(
        self, scientific_name: str
    ) -> Optional[Dict]:
        """
        按学名（拉丁名）查询鸟种的「罕见度 + IUCN + 简介」补充信息。

        供「查询鸟名」面板使用：IOC 鸟名库与本库通过学名关联。
        罕见度采用「直查 gbif 学名优先 + 经 model_class_id 回退」的并集策略
        （实测 IOC 名覆盖 95.65%，高于任一单路径），IUCN 与简介仍依赖
        BirdCountInfo 命中（经 model_class_id → avilist_map / short_description_zh）。

        Args:
            scientific_name: 学名/拉丁名，按库内存储原样匹配（区分大小写）。

        Returns:
            命中（罕见度或 BirdCountInfo 任一可得）返回字典：{
                'model_class_id': Optional[int],        # BirdCountInfo 未命中时为 None
                'gbif_rarity_100': Optional[float],     # 全球罕见度 0-100，越大越罕见
                'iucn_category':   Optional[str],       # LC/NT/VU/EN/CR/...
                'short_description_zh': Optional[str],  # 中文简介
            }
            罕见度与 BirdCountInfo 均未命中时返回 None（调用方显示「—」）。

        Look up supplementary rarity / IUCN / intro info for a species by its
        scientific name, taking the union of the direct-gbif and the
        BirdCountInfo→model_class_id paths for rarity.
        """
        if not scientific_name:
            return None
        name = scientific_name.strip()

        # 1) BirdCountInfo 命中拿 class_id + 简介（约 5% 学名差异会落空，不致命）
        class_id: Optional[int] = None
        description: Optional[str] = None
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT model_class_id, short_description_zh "
                    "FROM BirdCountInfo WHERE scientific_name = ? LIMIT 1",
                    (name,),
                )
                row = cursor.fetchone()
            if row:
                class_id, description = row[0], row[1]
        except Exception as e:
            # 不直接 return：仍尝试按学名直查罕见度 / Keep going for direct rarity.
            print(_t("logs.db_query_failed", id=name, e=e))

        # 2) 罕见度：直查 gbif 学名优先，未命中再经 model_class_id 回退
        rarity = self.get_gbif_rarity_by_scientific_name(name)
        if rarity is None and class_id is not None:
            rarity = self.get_gbif_rarity_by_class_id(class_id)

        # 3) IUCN：仅 BirdCountInfo 命中（拿到 class_id）时可查
        iucn = self.get_iucn_by_class_id(class_id) if class_id is not None else None

        # 罕见度与 BirdCountInfo 均落空 → 整体无补充信息
        if class_id is None and rarity is None:
            return None

        return {
            'model_class_id': class_id,
            'gbif_rarity_100': rarity,
            'iucn_category': iucn,
            'short_description_zh': description,
        }

    def get_gbif_scores_by_scientific_names(
        self, scientific_names: List[str]
    ) -> Dict[str, float]:
        """
        批量按学名查询「全球 GBIF 罕见度」（0-100 分）。

        供「查询鸟名」结果列表一次性给每行算罕见度图标用，避免逐行开连接。
        覆盖策略与 get_extra_info_by_scientific_name 一致——直查 gbif 学名优先，
        未命中的再经 BirdCountInfo.model_class_id 回退，取两者并集（实测对
        IOC 名覆盖 95.65%）。仅返回成功匹配且有分数的项；同样应用
        core.rarity_tier.HARDCODE_OVERRIDES 学名级硬编码降级。

        Args:
            scientific_names: 学名列表（自动去空白、去空项）。

        Returns:
            {scientific_name: gbif_rarity_100}，键为去空白后的学名，仅含命中项。

        Batch-fetch global GBIF rarity for a list of scientific names in one
        connection, taking the union of the direct-gbif and BirdCountInfo
        fallback paths and applying the manual override table.
        """
        result: Dict[str, float] = {}
        names = [n.strip() for n in scientific_names if n and n.strip()]
        if not names:
            return result

        try:
            from core.rarity_tier import get_score_override
        except Exception:
            def get_score_override(_n):  # type: ignore[misc]
                return None

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # 1) 直查 gbif_rarity_100.scientific_name（覆盖更广）
                placeholders = ",".join("?" * len(names))
                cursor.execute(
                    "SELECT scientific_name, gbif_rarity_100 FROM gbif_rarity_100 "
                    f"WHERE scientific_name IN ({placeholders}) "
                    "AND gbif_rarity_100 IS NOT NULL",
                    names,
                )
                for sci, score in cursor.fetchall():
                    if sci is None or score is None:
                        continue
                    override = get_score_override(sci)
                    result[sci] = (
                        float(override) if override is not None else float(score)
                    )

                # 2) 对直查未命中的学名，经 BirdCountInfo.model_class_id 回退
                remaining = [n for n in names if n not in result]
                if remaining:
                    ph2 = ",".join("?" * len(remaining))
                    cursor.execute(
                        "SELECT b.scientific_name, g.scientific_name, g.gbif_rarity_100 "
                        "FROM BirdCountInfo b "
                        "JOIN gbif_rarity_100 g ON g.model_class_id = b.model_class_id "
                        f"WHERE b.scientific_name IN ({ph2}) "
                        "AND g.gbif_rarity_100 IS NOT NULL",
                        remaining,
                    )
                    for query_name, gbif_name, score in cursor.fetchall():
                        if query_name is None or score is None or query_name in result:
                            continue
                        override = get_score_override(gbif_name)
                        result[query_name] = (
                            float(override) if override is not None else float(score)
                        )
        except Exception:
            # gbif_rarity_100 表不存在（旧库）→ 返回已有结果（可能为空）
            # Missing gbif_rarity_100 table (legacy DB) → return what we have.
            return result
        return result

    def get_ebird_code_by_english_name(self, english_name: str) -> Optional[str]:
        """
        根据英文名获取eBird代码
        
        Args:
            english_name: 英文鸟类名称
            
        Returns:
            eBird代码，如果未找到返回None
        """
        query = """
        SELECT ebird_code 
        FROM BirdCountInfo 
        WHERE english_name = ? AND ebird_code IS NOT NULL
        """
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, (english_name,))
                result = cursor.fetchone()
                return result[0] if result else None
        except Exception as e:
            print(_t("logs.db_ebird_query_failed", name=english_name, e=e))
            return None
    
    def get_birds_by_ebird_codes(self, ebird_codes: Set[str]) -> List[Dict]:
        """
        根据eBird代码集合获取鸟类信息
        
        Args:
            ebird_codes: eBird代码集合
            
        Returns:
            匹配的鸟类信息列表
        """
        if not ebird_codes:
            return []
        
        # 构建IN查询的占位符
        placeholders = ','.join('?' * len(ebird_codes))
        query = f"""
        SELECT id, english_name, chinese_simplified, ebird_code
        FROM BirdCountInfo 
        WHERE ebird_code IN ({placeholders})
        """
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, list(ebird_codes))
                results = cursor.fetchall()
                
                return [
                    {
                        'id': row[0],
                        'english_name': row[1],
                        'chinese_simplified': row[2],
                        'ebird_code': row[3]
                    }
                    for row in results
                ]
        except Exception as e:
            print(_t("logs.db_batch_ebird_failed", e=e))
            return []
    
    def search_birds(self, query: str, limit: int = 10) -> List[Dict]:
        """
        搜索鸟类（支持中英文名称和学名）
        
        Args:
            query: 搜索查询
            limit: 返回结果数量限制
            
        Returns:
            匹配的鸟类信息列表
        """
        search_query = """
        SELECT id, english_name, chinese_simplified, ebird_code, scientific_name
        FROM BirdCountInfo 
        WHERE english_name LIKE ? 
           OR chinese_simplified LIKE ? 
           OR scientific_name LIKE ?
        LIMIT ?
        """
        
        search_term = f"%{query}%"
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(search_query, (search_term, search_term, search_term, limit))
                results = cursor.fetchall()
                
                return [
                    {
                        'id': row[0],
                        'english_name': row[1],
                        'chinese_simplified': row[2],
                        'ebird_code': row[3],
                        'scientific_name': row[4]
                    }
                    for row in results
                ]
        except Exception as e:
            print(_t("logs.db_search_failed", e=e))
            return []
    
    def get_all_ebird_codes(self) -> Set[str]:
        """
        获取所有eBird代码
        
        Returns:
            所有eBird代码的集合
        """
        query = """
        SELECT DISTINCT ebird_code 
        FROM BirdCountInfo 
        WHERE ebird_code IS NOT NULL AND ebird_code != ''
        """
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                results = cursor.fetchall()
                return {row[0] for row in results}
        except Exception as e:
            print(_t("logs.db_all_ebird_failed", e=e))
            return set()
    
    def get_bird_data_for_model(self) -> List[List[str]]:
        """
        获取模型所需的鸟类数据格式（兼容原有的birdinfo.json格式）
        
        Returns:
            List[List[str]]: [[中文名, 英文名], ...] 格式的数据
        """
        query = """
        SELECT chinese_simplified, english_name
        FROM BirdCountInfo 
        ORDER BY id
        """
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                results = cursor.fetchall()
                return [[row[0], row[1]] for row in results]
        except Exception as e:
            print(_t("logs.db_model_data_failed", e=e))
            return []
    
    def get_statistics(self) -> Dict:
        """
        获取数据库统计信息
        
        Returns:
            统计信息字典
        """
        queries = {
            'total_birds': "SELECT COUNT(*) FROM BirdCountInfo",
            'birds_with_ebird_codes': "SELECT COUNT(*) FROM BirdCountInfo WHERE ebird_code IS NOT NULL AND ebird_code != ''",
            'unique_ebird_codes': "SELECT COUNT(DISTINCT ebird_code) FROM BirdCountInfo WHERE ebird_code IS NOT NULL AND ebird_code != ''",
            'birds_with_descriptions': "SELECT COUNT(*) FROM BirdCountInfo WHERE short_description_zh IS NOT NULL",
        }
        
        stats = {}
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                for key, query in queries.items():
                    cursor.execute(query)
                    stats[key] = cursor.fetchone()[0]
        except Exception as e:
            print(_t("logs.db_stats_failed", e=e))
            return {}
        
        return stats
    
    def check_species_in_region(self, scientific_name: str, region: str = None) -> bool:
        """
        检查物种是否在指定区域出现（简化版本，仅检查物种是否存在于数据库）

        Args:
            scientific_name: 物种学名
            region: 地理区域（暂未使用，保留用于未来扩展）

        Returns:
            True如果物种存在于数据库，否则False
        """
        query = """
        SELECT COUNT(*)
        FROM BirdCountInfo
        WHERE scientific_name = ?
        """

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, (scientific_name,))
                count = cursor.fetchone()[0]
                return count > 0
        except Exception as e:
            print(_t("logs.db_region_check_failed", name=scientific_name, e=e))
            return False

    def _load_avilist_cache(self):
        """一次性加载 avilist_map 表到内存字典，键为 model_class_id。"""
        query = """
        SELECT model_class_id, en_name_avilist, en_name_clements, en_name_birdlife,
               scientific_name_avilist, match_type, cornell_code,
               family_english, iucn_category
        FROM avilist_map
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
            self._avilist_cache = {}
            for row in rows:
                if row[0] is not None:
                    self._avilist_cache[row[0]] = {
                        'en_name_avilist': row[1],
                        'en_name_clements': row[2],
                        'en_name_birdlife': row[3],
                        'scientific_name_avilist': row[4],
                        'match_type': row[5],
                        'cornell_code': row[6],
                        'family_english': row[7],
                        'iucn_category': row[8],
                    }
            print(f"AviList cache loaded: {len(self._avilist_cache)} entries")
        except Exception as e:
            # avilist_map 表不存在时为正常情况（build 脚本未运行）
            if "no such table" not in str(e).lower():
                print(f"⚠️ AviList cache load failed: {e}")
            self._avilist_cache = {}

    def get_avilist_names_by_class_id(self, class_id: int) -> Optional[Dict]:
        """
        Query avilist_map table for alternative English names by model class ID.

        Returns dict with en_name_avilist, en_name_clements, en_name_birdlife,
        scientific_name_avilist, match_type, etc.  Returns None if no mapping
        or if the avilist_map table does not exist.

        Uses in-memory cache loaded on first call to avoid per-identification
        SQLite round-trips.
        """
        if self._avilist_cache is None:
            self._load_avilist_cache()
        return self._avilist_cache.get(class_id)

    def validate_ebird_codes_with_country(self, ebird_species_set: Set[str]) -> Dict:
        """
        验证eBird代码与国家物种列表的匹配情况

        Args:
            ebird_species_set: 国家物种eBird代码集合

        Returns:
            验证结果字典
        """
        if not ebird_species_set:
            return {"error": "Empty eBird species set"}

        # 获取数据库中的所有eBird代码
        db_codes = self.get_all_ebird_codes()

        # 计算匹配情况
        matched_codes = ebird_species_set.intersection(db_codes)
        unmatched_in_country = ebird_species_set - db_codes
        unmatched_in_db = db_codes - ebird_species_set

        return {
            "country_species_total": len(ebird_species_set),
            "database_species_total": len(db_codes),
            "matched_species": len(matched_codes),
            "match_rate": len(matched_codes) / len(ebird_species_set) * 100 if ebird_species_set else 0,
            "unmatched_in_country": len(unmatched_in_country),
            "unmatched_in_database": len(unmatched_in_db),
            "sample_matched": list(matched_codes)[:10],
            "sample_unmatched_country": list(unmatched_in_country)[:10]
        }


def test_bird_database_manager():
    """测试鸟类数据库管理器"""
    try:
        # 初始化管理器
        db_manager = BirdDatabaseManager()
        
        print("\n=== 数据库统计信息 ===")
        stats = db_manager.get_statistics()
        for key, value in stats.items():
            print(f"{key}: {value}")
        
        print("\n=== 测试根据英文名获取eBird代码 ===")
        test_names = ["Australian Magpie", "House Sparrow", "Laughing Kookaburra", "Galah"]
        for name in test_names:
            code = db_manager.get_ebird_code_by_english_name(name)
            print(f"{name}: {code}")
        
        print("\n=== 测试搜索功能 ===")
        search_results = db_manager.search_birds("magpie", limit=5)
        for bird in search_results:
            print(f"ID: {bird['id']}, 中文: {bird['chinese_simplified']}, 英文: {bird['english_name']}, eBird: {bird['ebird_code']}")
        
        print("\n=== 测试根据类别ID获取鸟类信息 ===")
        bird_info = db_manager.get_bird_by_class_id(1)
        if bird_info:
            print(f"类别ID 1: {bird_info['chinese_simplified']} ({bird_info['english_name']}) - {bird_info['ebird_code']}")
        
        print("\n=== 测试模型数据格式 ===")
        model_data = db_manager.get_bird_data_for_model()
        print(f"模型数据样例 (前5条): {model_data[:5]}")
        
    except Exception as e:
        print(f"测试失败: {e}")


if __name__ == "__main__":
    test_bird_database_manager()