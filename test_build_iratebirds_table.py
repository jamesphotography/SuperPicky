"""
iRateBird 构建脚本单测：用合成 CSV 夹具验证匹配/归一化/雌雄/幂等，
不依赖真实 figshare 数据。
Builder tests using synthetic CSV fixtures — no real figshare data needed.
"""
import csv
import os
import sqlite3

import pytest

from scripts_dev.build_iratebirds_table import build_aesthetic_table


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def fixtures(tmp_path):
    pred = os.path.join(tmp_path, "pred.csv")
    sex = os.path.join(tmp_path, "sex.csv")
    db = os.path.join(tmp_path, "ref.sqlite")
    # 物种级: 3 种。Aix galericulata(鸳鸯)=二态, Passer domesticus(麻雀)=非二态,
    # Ghost species=学名匹配不上。
    _write_csv(pred, ["sci_name", "common_name",
                      "predicted_attractiveness_full_model", "no_of_ratings_used"],
               [["Aix galericulata", "Mandarin Duck", "7.0", "40"],
                ["Passer domesticus", "House Sparrow", "4.6", "36"],
                ["Ghost species", "Ghost", "9.0", "5"]])
    # sex-level: 只有鸳鸯有雌雄分（雄 9.1 / 雌 4.0）
    _write_csv(sex, ["sci_name", "sex", "predicted_attractiveness_sex_model"],
               [["Aix galericulata", "male", "9.1"],
                ["Aix galericulata", "female", "4.0"]])
    class_map = {"Aix galericulata": 100, "Passer domesticus": 200}  # Ghost 不在
    return pred, sex, db, class_map


def test_build_matches_and_normalizes(fixtures):
    pred, sex, db, class_map = fixtures
    stats = build_aesthetic_table(pred, sex, class_map, db)
    assert stats["matched"] == 2
    assert stats["total"] == 3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = {r["model_class_id"]: r for r in
            con.execute("SELECT * FROM iratebirds_aesthetic")}
    con.close()
    # 鸳鸯: 二态, 默认=max(雄9.1→90.0, 雌4.0→33.3)=90.0
    duck = rows[100]
    assert duck["is_dimorphic"] == 1
    assert duck["aesthetic_male"] == 90.0
    assert duck["aesthetic_female"] == pytest.approx(33.3, abs=0.05)
    assert duck["aesthetic_100"] == 90.0
    assert duck["aesthetic_raw_10"] == 7.0
    assert duck["no_of_ratings"] == 40
    # 麻雀: 非二态, 默认=物种级(4.6→40.0)
    sparrow = rows[200]
    assert sparrow["is_dimorphic"] == 0
    assert sparrow["aesthetic_male"] is None
    assert sparrow["aesthetic_100"] == 40.0


def test_build_idempotent(fixtures):
    """重跑不重复：行数稳定。"""
    pred, sex, db, class_map = fixtures
    build_aesthetic_table(pred, sex, class_map, db)
    build_aesthetic_table(pred, sex, class_map, db)
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM iratebirds_aesthetic").fetchone()[0]
    con.close()
    assert n == 2  # 只有 2 个匹配上的种
