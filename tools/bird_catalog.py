"""只读鸟名目录与详情，无模型或 Qt 依赖。 / Read-only, model-free bird catalog."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import unicodedata

from config import get_install_scoped_resource_path
from core.rarity_tier import get_score_override, gbif_score_to_tier, TIER_NAMES_ZH, TIER_NAMES_EN
from tools.pinyin_names import pinyin_for


class CatalogNotFound(LookupError):
    """鸟种或版本不存在。 / Unknown species or version."""


class CatalogAmbiguous(ValueError):
    """同名记录需要 bird_id 消歧。 / Select a bird_id for ambiguous names."""


class BirdCatalog:
    """每次调用独立只读连接，线程间不共享游标。 / Per-call read-only connections."""

    def __init__(self, names_path=None, reference_path=None):
        self.names_path = Path(names_path or get_install_scoped_resource_path('ioc/birdname.db'))
        self.reference_path = Path(reference_path or get_install_scoped_resource_path('birdid/data/bird_reference.sqlite'))

    @staticmethod
    def _connect(path):
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    @staticmethod
    def _version(conn, version_id):
        if version_id is None:
            row = conn.execute('SELECT * FROM versions ORDER BY created_at DESC, version_id DESC LIMIT 1').fetchone()
        else:
            row = conn.execute('SELECT * FROM versions WHERE version_id=?', (version_id,)).fetchone()
        if row is None:
            raise CatalogNotFound('鸟名库版本不存在 / Unknown catalog version')
        return row

    @staticmethod
    def _record(row):
        return {
            'bird_id': row['bird_id'], 'version_id': row['version_id'],
            'cn_name': row['chinese_name'] or '', 'en_name': row['english_name'] or '',
            'scientific_name': row['latin_name'] or '',
            'pinyin_name': pinyin_for(row['chinese_name']),
            'pinyin_plain': row['pinyin_name'] or '', 'abbreviation': row['abbreviation'] or '',
        }

    def search(self, query='', *, version_id=None, limit=100, offset=0):
        """分页名称列表；空查询列出全部。 / Paginated names; empty query lists all."""
        if not 1 <= limit <= 500 or offset < 0 or len(query) > 200:
            raise ValueError('limit 必须为 1–500，offset ≥ 0，q 最长 200 字符')
        with self._connect(self.names_path) as conn:
            version = self._version(conn, version_id)
            # 将声调及空格规范化，只把参数作为文字匹配。 / Literal, tone-insensitive search.
            q = ''.join(c for c in unicodedata.normalize('NFD', query.strip()) if not unicodedata.combining(c))
            q = ''.join(q.lower().split())
            pattern = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            columns = ('chinese_name', 'english_name', 'latin_name', 'pinyin_name', 'abbreviation')
            where = 'version_id=? AND (' + ' OR '.join(
                f"LOWER(REPLACE(COALESCE({col}, ''), ' ', '')) LIKE ? ESCAPE '\\'" for col in columns) + ')'
            params = (version['version_id'], *([pattern] * len(columns)))
            total = conn.execute('SELECT COUNT(*) FROM birds WHERE ' + where, params).fetchone()[0]
            rows = conn.execute('SELECT * FROM birds WHERE ' + where +
                                ' ORDER BY CASE WHEN chinese_name=? OR english_name=? COLLATE NOCASE THEN 0 ELSE 1 END, chinese_name, bird_id LIMIT ? OFFSET ?',
                                (*params, query.strip(), query.strip(), limit, offset)).fetchall()
        return {'success': True, 'version_id': version['version_id'], 'version_name': version['version_name'],
                'total': total, 'offset': offset, 'limit': limit, 'results': [self._record(r) for r in rows]}

    def detail(self, *, bird_id=None, name=None, version_id=None):
        """按稳定 ID 或精确鸟名获取详情；缺失字段为 null。 / Exact identity lookup."""
        if (bird_id is None) == (not name):
            raise ValueError('请提供 bird_id 或 name（二选一）')
        if name is not None and len(name) > 200:
            raise ValueError('name 最长 200 字符 / Name too long')
        with self._connect(self.names_path) as conn:
            if bird_id is not None:
                rows = conn.execute('SELECT * FROM birds WHERE bird_id=?', (bird_id,)).fetchall()
                if rows and version_id is not None and rows[0]['version_id'] != version_id:
                    rows = []
            else:
                version = self._version(conn, version_id)
                rows = conn.execute('SELECT * FROM birds WHERE version_id=? AND '
                                    '(chinese_name=? OR english_name=? COLLATE NOCASE OR latin_name=? COLLATE NOCASE)',
                                    (version['version_id'], name, name, name)).fetchall()
            if not rows:
                raise CatalogNotFound('没有找到该鸟种 / Species not found')
            if len(rows) != 1:
                raise CatalogAmbiguous('鸟名对应多个记录，请从列表选择 bird_id / Ambiguous name')
            result = self._record(rows[0])
            version = self._version(conn, result['version_id'])
            result['version_name'] = version['version_name']
        result.update(self._extra(result['scientific_name']))
        return {'success': True, 'bird': result}

    def _extra(self, scientific_name):
        """与鸟名面板相同的全球稀有度口径。 / Same global rarity policy as the name panel."""
        # 不导入 birdid 包：其 __init__ 会启动模型栈。 / Avoid birdid's eager model imports.
        with self._connect(self.reference_path) as conn:
            info = conn.execute('SELECT model_class_id, short_description_zh FROM BirdCountInfo '
                                'WHERE scientific_name=? LIMIT 1', (scientific_name,)).fetchone()
            score = conn.execute('SELECT gbif_rarity_100, scientific_name FROM gbif_rarity_100 '
                                 'WHERE scientific_name=? AND gbif_rarity_100 IS NOT NULL LIMIT 1',
                                 (scientific_name,)).fetchone()
            if score is None and info is not None and info['model_class_id'] is not None:
                score = conn.execute('SELECT gbif_rarity_100, scientific_name FROM gbif_rarity_100 '
                                     'WHERE model_class_id=? AND gbif_rarity_100 IS NOT NULL LIMIT 1',
                                     (info['model_class_id'],)).fetchone()
            category = None
            if info is not None and info['model_class_id'] is not None:
                category = conn.execute("SELECT iucn_category FROM avilist_map WHERE model_class_id=? "
                                        "AND iucn_category IS NOT NULL AND iucn_category!='' LIMIT 1",
                                        (info['model_class_id'],)).fetchone()
        rarity = None
        if score is not None:
            override = get_score_override(score['scientific_name'])
            rarity = override if override is not None else score['gbif_rarity_100']
        tier = gbif_score_to_tier(rarity) if rarity is not None else None
        return {'gbif_rarity_100': rarity, 'rarity_scope': 'global',
                'rarity_label_zh': TIER_NAMES_ZH[tier] if tier is not None else None,
                'rarity_label_en': TIER_NAMES_EN[tier] if tier is not None else None,
                'iucn_category': category[0] if category else None,
                'description': (info['short_description_zh'] or '') if info else '',
                'china_protection_level': None}
