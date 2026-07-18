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


def test_build_dedups_class_id_keeping_more_ratings(tmp_path):
    """
    两个 iRateBird 学名映射到同一 model_class_id（同物异名）→ 表对该 class_id
    只留一行，且保留评分数更多的那条。
    Two names → one class_id: keep a single row, the one with more ratings.
    """
    pred = os.path.join(tmp_path, "pred.csv")
    sex = os.path.join(tmp_path, "sex.csv")
    db = os.path.join(tmp_path, "ref.sqlite")
    # 两个学名, 都映射到 class_id 500; "Accipiter gentilis" 评分 60 > "Astur gentilis" 20
    _write_csv(pred, ["sci_name", "common_name",
                      "predicted_attractiveness_full_model", "no_of_ratings_used"],
               [["Accipiter gentilis", "Goshawk", "5.0", "20"],
                ["Astur gentilis", "Goshawk", "8.0", "60"]])
    _write_csv(sex, ["sci_name", "sex", "predicted_attractiveness_sex_model"], [])
    class_map = {"Accipiter gentilis": 500, "Astur gentilis": 500}
    build_aesthetic_table(pred, sex, class_map, db)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT * FROM iratebirds_aesthetic WHERE model_class_id=500"))
    con.close()
    assert len(rows) == 1                       # class_id 唯一
    assert rows[0]["no_of_ratings"] == 60       # 保留评分数更多的那条
    assert rows[0]["aesthetic_raw_10"] == 8.0


def test_build_real_format_semicolon_and_comma_decimal(tmp_path):
    """
    真实 figshare 格式：predictions 用分号分隔 + 逗号小数（"7,0"），
    sex-level 用逗号分隔 + 点小数（"9.1"）。构建须正确解析两种格式。
    Real figshare formats: predictions is ';'-sep with comma decimals,
    sex-level is ','-sep with dot decimals — both must parse.
    """
    pred = os.path.join(tmp_path, "pred.csv")
    sex = os.path.join(tmp_path, "sex.csv")
    db = os.path.join(tmp_path, "ref.sqlite")
    # 分号分隔 + 逗号小数（含前导 BOM，模拟 utf-8-sig）
    with open(pred, "w", newline="", encoding="utf-8-sig") as f:
        f.write("number;sci_name;common_name;predicted_attractiveness_full_model;no_of_ratings_used\n")
        f.write("1;Aix galericulata;Mandarin Duck;7,0;40\n")
        f.write("2;Passer domesticus;House Sparrow;4,6;36\n")
    # 逗号分隔 + 点小数
    _write_csv(sex, ["sci_name", "sex", "predicted_attractiveness_sex_model"],
               [["Aix galericulata", "male", "9.1"],
                ["Aix galericulata", "female", "4.0"]])
    class_map = {"Aix galericulata": 100, "Passer domesticus": 200}
    stats = build_aesthetic_table(pred, sex, class_map, db)
    assert stats["matched"] == 2
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = {r["model_class_id"]: r for r in
            con.execute("SELECT * FROM iratebirds_aesthetic")}
    con.close()
    # 逗号小数 "7,0" 正确解析为 7.0 → raw_10；默认取 max(雄9.1→90.0,雌4.0)=90.0
    assert rows[100]["aesthetic_raw_10"] == 7.0
    assert rows[100]["aesthetic_100"] == 90.0
    # "4,6" → 4.6 → 40.0
    assert rows[200]["aesthetic_100"] == 40.0
