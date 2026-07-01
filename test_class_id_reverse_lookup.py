# -*- coding: utf-8 -*-
"""birdid/bird_database_manager.py 名字→model_class_id 反查单测。"""
from birdid.bird_database_manager import BirdDatabaseManager


def test_lookup_by_scientific_name():
    mgr = BirdDatabaseManager()
    # Abroscopus albogularis = 棕脸鹟莺 model_class_id=7447（见 BirdCountInfo）
    assert mgr.get_class_id_by_scientific_name("Abroscopus albogularis") == 7447


def test_lookup_falls_back_to_english_name():
    mgr = BirdDatabaseManager()
    # 给个不存在的学名，靠英文名回退命中
    cid = mgr.get_class_id_by_scientific_name(
        "Nonexistent latinicus", english_name="Rufous-faced Warbler"
    )
    assert cid == 7447


def test_lookup_miss_returns_none():
    mgr = BirdDatabaseManager()
    assert mgr.get_class_id_by_scientific_name("Zzz nonexistent",
                                               english_name="Zzz Nobird") is None
