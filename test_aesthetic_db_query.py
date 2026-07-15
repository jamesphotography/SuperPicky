"""
鸟种美学指数运行时查询单测：用临时库建表塞数据，验证命中/未命中/缺表容错。
Runtime aesthetic-lookup tests against a temp DB.
"""
import os
import sqlite3

import pytest

from birdid.bird_database_manager import BirdDatabaseManager


@pytest.fixture
def db_with_aesthetic(tmp_path):
    db = os.path.join(tmp_path, "ref.sqlite")
    con = sqlite3.connect(db)
    # BirdDatabaseManager.__init__ 会 SELECT COUNT(*) FROM BirdCountInfo，
    # 需最小可用库（实测确认表名，非 brief 草稿里的 bird_ioc）。
    # __init__ runs SELECT COUNT(*) FROM BirdCountInfo; provide a minimal
    # usable table (verified actual name, not the bird_ioc placeholder).
    con.execute("CREATE TABLE BirdCountInfo (model_class_id INTEGER)")
    con.execute("INSERT INTO BirdCountInfo VALUES (1)")
    con.execute(
        "CREATE TABLE iratebirds_aesthetic (model_class_id INTEGER, aesthetic_100 REAL)")
    con.execute("INSERT INTO iratebirds_aesthetic VALUES (100, 90.0)")
    con.commit()
    con.close()
    return db


def test_get_aesthetic_hit(db_with_aesthetic):
    mgr = BirdDatabaseManager(db_path=db_with_aesthetic)
    assert mgr.get_aesthetic_by_class_id(100) == 90.0


def test_get_aesthetic_miss(db_with_aesthetic):
    mgr = BirdDatabaseManager(db_path=db_with_aesthetic)
    assert mgr.get_aesthetic_by_class_id(999) is None


def test_get_aesthetic_missing_table(tmp_path):
    """库无 iratebirds_aesthetic 表 → 容错返 None，不抛。"""
    db = os.path.join(tmp_path, "ref.sqlite")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE BirdCountInfo (model_class_id INTEGER)")
    con.execute("INSERT INTO BirdCountInfo VALUES (1)")
    con.commit()
    con.close()
    mgr = BirdDatabaseManager(db_path=db)
    assert mgr.get_aesthetic_by_class_id(100) is None
