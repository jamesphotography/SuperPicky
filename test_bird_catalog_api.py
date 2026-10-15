"""目录 API、只读并发与原识鸟协议回归。 / Catalog and legacy API regression tests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.bird_catalog import BirdCatalog
import birdid_server


class CatalogAPITest(unittest.TestCase):
    """使用临时数据库，无网络或模型。 / Temporary databases, no network or models."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.names, self.reference = root / 'names.db', root / 'reference.db'
        with sqlite3.connect(self.names) as c:
            c.executescript('''
                CREATE TABLE versions(version_id INTEGER, version_name TEXT, created_at TEXT);
                INSERT INTO versions VALUES(1, 'Old', '2020'), (2, 'IOC test', '2026');
                CREATE TABLE birds(bird_id INTEGER, version_id INTEGER, chinese_name TEXT,
                    english_name TEXT, latin_name TEXT, pinyin_name TEXT, abbreviation TEXT);
            ''')
            c.executemany('INSERT INTO birds VALUES(?,?,?,?,?,?,?)', [
                (1, 1, '旧鸟名', 'Old bird', 'Old species', 'jiu niao ming', 'JNM'),
                (2, 2, '白头鹎', 'Light-vented Bulbul', 'Pycnonotus sinensis', 'bai tou bei', 'BTB'),
                (3, 2, '未知鸟', 'Unknown bird', 'Unknown species', 'wei zhi niao', 'WZN'),
                (4, 2, '同名鸟', 'Duplicate A', 'Duplicate a', 'tong ming niao', 'TMN'),
                (5, 2, '同名鸟', 'Duplicate B', 'Duplicate b', 'tong ming niao', 'TMN'),
            ])
        c.close()
        with sqlite3.connect(self.reference) as c:
            c.executescript('''
                CREATE TABLE BirdCountInfo(model_class_id INTEGER, scientific_name TEXT, short_description_zh TEXT);
                CREATE TABLE gbif_rarity_100(model_class_id INTEGER, scientific_name TEXT, gbif_rarity_100 REAL);
                CREATE TABLE avilist_map(model_class_id INTEGER, iucn_category TEXT);
                INSERT INTO gbif_rarity_100 VALUES(7, 'Pycnonotus sinensis', 0);
                INSERT INTO avilist_map VALUES(7, 'LC');
            ''')
            c.execute('INSERT INTO BirdCountInfo VALUES(?,?,?)', (7, 'Pycnonotus sinensis', '中文简介'))
        c.close()
        self.catalog = BirdCatalog(self.names, self.reference)
        self.before = [p.read_bytes() for p in (self.names, self.reference)]
        mock = patch('tools.bird_catalog_routes.BirdCatalog', return_value=self.catalog)
        mock.start()
        self.addCleanup(mock.stop)
        self.client = birdid_server.app.test_client()

    def test_search_names_pinyin_pagination_and_literal_input(self):
        for query in ('白头', 'bulbul', 'Pycnonotus', 'BTB', 'baitoubei', 'bái tóu bēi'):
            with self.subTest(query=query):
                data = self.client.get('/birds/search', query_string={'q': query}).get_json()
                self.assertEqual([r['bird_id'] for r in data['results']], [2])
                self.assertEqual(data['results'][0]['pinyin_name'], 'bái tóu bēi')
        first = self.client.get('/birds/search?limit=2').get_json()
        second = self.client.get('/birds/search?limit=2&offset=2&version_id=2').get_json()
        self.assertEqual(first['total'], 4)
        self.assertFalse({b['bird_id'] for b in first['results']} & {b['bird_id'] for b in second['results']})
        for query in ("' OR 1=1 --", '%', '_'):
            self.assertEqual(self.catalog.search(query)['total'], 0)
        self.assertEqual(self.catalog.search(version_id=1)['total'], 1)

    def test_detail_identity_null_zero_and_no_heavy_import(self):
        bird = self.client.get('/birds/detail?bird_id=2&version_id=2').get_json()['bird']
        self.assertEqual(bird['gbif_rarity_100'], 0)
        self.assertEqual(bird['iucn_category'], 'LC')
        self.assertEqual(bird['description'], '中文简介')
        self.assertIsNone(bird['china_protection_level'])
        named = self.client.get('/birds/detail', query_string={'name': '白头鹎'}).get_json()['bird']
        self.assertEqual(named, bird)
        unknown = self.catalog.detail(bird_id=3)['bird']
        self.assertIsNone(unknown['gbif_rarity_100'])
        self.assertIsNone(unknown['iucn_category'])
        self.assertNotIn('birdid.bird_identifier', sys.modules)
        self.assertNotIn('torch', sys.modules)

    def test_bad_requests_and_database_failure(self):
        for query in ('limit=0', 'limit=501', 'offset=-1', 'limit=x', 'q=' + 'a' * 201):
            self.assertEqual(self.client.get('/birds/search?' + query).status_code, 400)
        self.assertEqual(self.client.get('/birds/search?version_id=999').status_code, 404)
        for query in ('', 'bird_id=x', 'bird_id=2&name=x', 'bird_id=' + '9' * 100, 'bird_id=-1'):
            self.assertEqual(self.client.get('/birds/detail?' + query).status_code, 400)
        self.assertEqual(self.client.get('/birds/detail?bird_id=999').status_code, 404)
        self.assertEqual(self.client.get('/birds/detail?bird_id=2&version_id=1').status_code, 404)
        self.assertEqual(self.client.get('/birds/detail', query_string={'name': '同名鸟'}).status_code, 409)
        self.catalog.reference_path = self.reference.with_name('missing.db')
        self.assertEqual(self.client.get('/birds/detail?bird_id=2').status_code, 503)
        self.assertFalse(self.catalog.reference_path.exists())

    def test_concurrent_read_only_queries(self):
        def query(_):
            return self.catalog.search('BTB')['results'][0]['bird_id'], self.catalog.detail(bird_id=2)['bird']['gbif_rarity_100']
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(query, range(64))), [(2, 0)] * 64)
        self.assertEqual([p.read_bytes() for p in (self.names, self.reference)], self.before)

    def test_legacy_health_and_recognition_protocol(self):
        health = self.client.get('/health')
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()['service'], 'SuperPicky BirdID API')
        self.assertEqual(self.client.get('/recognize').status_code, 405)
        image = Path(self.temp.name) / 'photo.jpg'
        image.write_bytes(b'test image')
        calls = []
        def identify(path, **kwargs):
            calls.append((path, kwargs))
            return {'success': True, 'results': [{'cn_name': '白头鹎', 'en_name': 'Bulbul',
                    'confidence': 95.5, 'gbif_rarity_100': 0, 'iucn_category': 'LC'}]}
        modules = {'birdid.bird_identifier': SimpleNamespace(identify_bird=identify),
                   'advanced_config': SimpleNamespace(get_advanced_config=lambda: SimpleNamespace(name_format='IOC'))}
        with patch.dict(sys.modules, modules), patch.object(birdid_server, 'get_gui_settings', return_value={
                'country_code': None, 'region_code': None, 'use_geo_filter': True}), patch.object(
                birdid_server, 'get_gui_language', return_value='zh_CN'), patch.object(
                birdid_server, 'geo_filter_warning', return_value=None):
            response = self.client.post('/recognize', json={'image_path': str(image), 'top_k': 3,
                                                           'use_yolo': True, 'use_gps': True})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(calls[0][1]['top_k'], 3)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['results'][0]['confidence'], 95.5)
        self.assertEqual(data['results'][0]['gbif_rarity_100'], 0)
        self.assertTrue({'yolo_info', 'gps_info', 'geo_info'} <= data.keys())


if __name__ == '__main__':
    unittest.main()
