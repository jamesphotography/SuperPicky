# 鸟种美学指数（iRateBird）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给每个鸟种一个离线预计算的「物种颜值」分（0–100，来自 iRateBird 众包数据），作为与罕见度对等的纯展示+排序维度，不参与评星。

**Architecture:** 四层，各层沿用现有罕见度（`gbif_rarity_100`）的先例路径：纯变换函数（新模块）→ 开发期一次性构建脚本把两个 figshare CSV 匹配+归一化写入 `bird_reference.sqlite` 新表 → 运行时查询 API（`bird_database_manager`）→ 识鸟链路 `_build_results` 逐张查分、`photo_processor` 落 `report.db` → 详情面板展示 + 筛选面板排序。

**Tech Stack:** Python 3 (.venv)，sqlite3，pytest，PySide6（UI），i18n JSON（zh_CN/en_US）。

## Global Constraints

- **定位**：纯展示+排序，**绝不参与评星/选片加权**。评分引擎不读该字段。
- **与 TOPIQ 区分**：TOPIQ（`nima_score`/`adj_topiq`）是「这张照片画质美学」；本指数是「这个鸟种颜值」，两者正交。
- **源分**：`predicted_attractiveness_full_model`（不用 raw 平均/subset_model）。
- **归一化公式**：`round((raw - 1) / 9 * 100, 1)`，边界 raw=1→0.0、raw=10→100.0。
- **雌雄二态**：默认值 = `max(male, female)`（两者有一即用）；无雌雄分 → 用物种级分。DB 同时存 `aesthetic_male`/`aesthetic_female` 两列备用。
- **低置信度不删只标**：全量存，同存 `no_of_ratings`；不设硬阈值删除。
- **排序键名**：用 `species_beauty_desc`。**`aesthetic_desc` 已被 TOPIQ 画质美学占用**，严禁复用。
- **分类匹配**：iRateBird `sci_name`（eBird/Clements 2019）→ 本地 `model_class_id`，走学名匹配。
- **数据源不入仓**：两个 figshare CSV 放 `scripts_dev/data_sources/`（`.gitignore` 忽略），脚本注释写明下载 URL。
- **许可证**：CC-BY 4.0，About/文档署名 Santangeli et al. 2023 + 文化偏差说明。
- UTF-8 安全：locale/CSV 含中文一律 `encoding='utf-8'`，禁用 sed/awk。
- 注释：中文 + 英文双语。
- 测试在仓库根目录 `test_*.py`；`.gitignore` 忽略 `test_*.py`，提交必须 `git add -f`。
- 每个任务收尾跑 `.venv/bin/python -m py_compile <改动的py文件>`。

---

### Task 1: 纯变换函数模块

**Files:**
- Create: `birdid/iratebirds_aesthetic.py`
- Test: `test_iratebirds_aesthetic.py`（仓库根目录）

**Interfaces:**
- Consumes: 无（纯函数，只依赖标准库 typing）
- Produces:
  - `normalize_score(raw_1_10: Optional[float]) -> Optional[float]` —— 1–10 归一化到 0–100，None→None
  - `derive_default_score(species_100: Optional[float], male_100: Optional[float], female_100: Optional[float]) -> Optional[float]` —— 雌雄有值取 max，否则回退物种级
  - `is_dimorphic(male_100: Optional[float], female_100: Optional[float]) -> int` —— 雌雄均非 None 返 1 否则 0

- [ ] **Step 1: 写失败测试**

```python
"""
iRateBird 鸟种美学指数纯变换函数单测。
Unit tests for the pure transform functions of the iRateBird aesthetic index.
"""
import pytest

from birdid.iratebirds_aesthetic import (
    normalize_score,
    derive_default_score,
    is_dimorphic,
)


@pytest.mark.parametrize("raw,expected", [
    (1.0, 0.0),      # 下边界
    (10.0, 100.0),   # 上边界
    (5.5, 50.0),     # 中点
    (7.3, 70.0),     # round 到 1 位
])
def test_normalize_score_boundaries(raw, expected):
    assert normalize_score(raw) == expected


def test_normalize_score_none():
    assert normalize_score(None) is None


def test_derive_default_prefers_max_of_sexes():
    """二态种：雄 90 雌 40 → 取 max=90（该种最佳颜值）"""
    assert derive_default_score(65.0, 90.0, 40.0) == 90.0


def test_derive_default_single_sex_present():
    """只有一性有分 → 用那个"""
    assert derive_default_score(65.0, 88.0, None) == 88.0
    assert derive_default_score(65.0, None, 42.0) == 42.0


def test_derive_default_falls_back_to_species():
    """无雌雄分 → 回退物种级"""
    assert derive_default_score(65.0, None, None) == 65.0


def test_derive_default_all_none():
    assert derive_default_score(None, None, None) is None


def test_is_dimorphic():
    assert is_dimorphic(90.0, 40.0) == 1
    assert is_dimorphic(90.0, None) == 0
    assert is_dimorphic(None, None) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_iratebirds_aesthetic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'birdid.iratebirds_aesthetic'`

- [ ] **Step 3: 最小实现**

Create `birdid/iratebirds_aesthetic.py`:

```python
# -*- coding: utf-8 -*-
"""
iRateBird 鸟种美学指数纯变换函数（无 IO/Qt 依赖，便于单测）。

数据来源: Santangeli et al. 2023, Scientific Data (s41597-023-02169-0),
CC-BY 4.0。物种颜值 1–10 众包评分，本模块负责归一化与雌雄二态取值。

Pure transforms for the iRateBird species aesthetic index (no IO/Qt deps).
"""
from typing import Optional


def normalize_score(raw_1_10: Optional[float]) -> Optional[float]:
    """
    把 1–10 原始颜值分归一化到 0–100（与罕见度 UI 同量纲）。

    参数:
    raw_1_10 (Optional[float]): iRateBird full_model 原始分（1–10）

    返回:
    Optional[float]: 0–100 分，保留 1 位小数；输入 None 返 None

    Normalize a 1–10 raw score to 0–100 (same scale as the rarity UI).
    """
    if raw_1_10 is None:
        return None
    return round((raw_1_10 - 1.0) / 9.0 * 100.0, 1)


def derive_default_score(
    species_100: Optional[float],
    male_100: Optional[float],
    female_100: Optional[float],
) -> Optional[float]:
    """
    计算展示用默认颜值分：二态种取 max(雄,雌)（该种最佳颜值），
    无雌雄分则回退物种级分。

    参数:
    species_100 (Optional[float]): 物种级归一化分（0–100）
    male_100 (Optional[float]): 雄鸟归一化分，无则 None
    female_100 (Optional[float]): 雌鸟归一化分，无则 None

    返回:
    Optional[float]: 默认展示分（0–100）；全 None 返 None

    Default display score: max(male, female) for dichromatic species (the
    species' best-case beauty), else fall back to the species-level score.
    """
    sexes = [s for s in (male_100, female_100) if s is not None]
    if sexes:
        return max(sexes)
    return species_100


def is_dimorphic(male_100: Optional[float], female_100: Optional[float]) -> int:
    """
    是否雌雄二态（sex-level 数据同时含雄与雌分）。

    参数:
    male_100 (Optional[float]): 雄鸟分
    female_100 (Optional[float]): 雌鸟分

    返回:
    int: 1=雌雄均有分, 0=否

    Whether the species is sexually dichromatic (both male and female
    scores present in the sex-level data).
    """
    return 1 if (male_100 is not None and female_100 is not None) else 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_iratebirds_aesthetic.py -v`
Expected: 11 passed

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile birdid/iratebirds_aesthetic.py
git add birdid/iratebirds_aesthetic.py
git add -f test_iratebirds_aesthetic.py
git commit -m "feat(aesthetic): iRateBird颜值指数纯变换函数(归一化+雌雄取max)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 离线构建脚本 + 参考表

**Files:**
- Create: `scripts_dev/build_iratebirds_table.py`
- Modify: `.gitignore`（加 `scripts_dev/data_sources/`）
- Test: `test_build_iratebirds_table.py`（仓库根目录）

**Interfaces:**
- Consumes: Task 1 的 `normalize_score` / `derive_default_score` / `is_dimorphic`
- Produces:
  - `build_aesthetic_table(predictions_csv: str, sex_csv: str, class_map: dict[str, int], db_path: str) -> dict` —— 幂等建 `iratebirds_aesthetic` 表并返回统计 `{"matched": int, "total": int, "match_rate": float}`
  - 表 `iratebirds_aesthetic`（列见下），供 Task 3 查询

**说明（实现者必读）：** 真实 figshare 两个 CSV **不在仓库里**，需开发者手动下载放 `scripts_dev/data_sources/`。本任务只实现并单测构建逻辑（用合成小 CSV 夹具）；用真数据跑一次填充 `bird_reference.sqlite` 是收尾的手动步骤（见 Step 6），不由 subagent 执行。

- [ ] **Step 1: 写失败测试**

```python
"""
iRateBird 构建脚本单测：用合成 CSV 夹具验证匹配/归一化/雌雄/幂等，
不依赖真实 figshare 数据。
Builder tests using synthetic CSV fixtures — no real figshare data needed.
"""
import csv
import os
import sqlite3
import tempfile

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_build_iratebirds_table.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts_dev.build_iratebirds_table'`（或 `scripts_dev` 无 `__init__` 时 ImportError）

- [ ] **Step 3: 确保 scripts_dev 可导入**

若 `scripts_dev/__init__.py` 不存在则创建空文件：

```bash
test -f scripts_dev/__init__.py || touch scripts_dev/__init__.py
```

- [ ] **Step 4: 写构建脚本**

Create `scripts_dev/build_iratebirds_table.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iRateBird 鸟种美学指数离线构建脚本（开发期一次性运行）。

数据来源 (CC-BY 4.0, Santangeli et al. 2023, Scientific Data s41597-023-02169-0)
figshare: https://figshare.com/articles/dataset/20170082
需手动下载放 scripts_dev/data_sources/ :
  - iratebirds_final_predictions_average_fullmodel_subsetmodel_151122.csv
  - iratebirds_pred_ratings_species_and_sex_level_120123.csv

把物种级 full_model 分 + 雌雄分匹配到本地 model_class_id，归一化 0–100，
写入 bird_reference.sqlite 的 iratebirds_aesthetic 表（幂等：先 DROP 再建）。

Offline builder for the iRateBird species aesthetic index (run once by a dev).
"""
import argparse
import csv
import os
import sqlite3
import sys
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from birdid.iratebirds_aesthetic import (  # noqa: E402
    normalize_score, derive_default_score, is_dimorphic,
)

_CREATE_SQL = """
CREATE TABLE iratebirds_aesthetic (
    model_class_id    INTEGER,
    scientific_name   TEXT,
    aesthetic_100     REAL,
    aesthetic_raw_10  REAL,
    aesthetic_male    REAL,
    aesthetic_female  REAL,
    is_dimorphic      INTEGER,
    no_of_ratings     INTEGER,
    source            TEXT
);
CREATE INDEX idx_iratebirds_class ON iratebirds_aesthetic(model_class_id);
"""


def _to_float(s: Optional[str]) -> Optional[float]:
    """CSV 空串/NA → None，否则 float。"""
    if s is None:
        return None
    s = s.strip()
    if s == "" or s.upper() in ("NA", "NAN", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_sex_scores(sex_csv: str) -> Dict[str, Dict[str, float]]:
    """读 sex-level CSV → {sci_name: {'male': 0-100, 'female': 0-100}}（已归一化）。"""
    out: Dict[str, Dict[str, float]] = {}
    with open(sex_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("sci_name") or "").strip()
            sex = (row.get("sex") or "").strip().lower()
            score = normalize_score(_to_float(row.get("predicted_attractiveness_sex_model")))
            if not name or sex not in ("male", "female") or score is None:
                continue
            out.setdefault(name, {})[sex] = score
    return out


def build_aesthetic_table(
    predictions_csv: str,
    sex_csv: str,
    class_map: Dict[str, int],
    db_path: str,
) -> dict:
    """
    构建 iratebirds_aesthetic 表（幂等）。

    参数:
    predictions_csv: 物种级 predictions CSV 路径
    sex_csv: 雌雄 sex-level CSV 路径
    class_map: {scientific_name: model_class_id} 学名→类别映射
    db_path: 目标 sqlite（bird_reference.sqlite）

    返回:
    dict: {"matched": 命中数, "total": predictions 总行数, "match_rate": 比率}
    """
    sex_scores = _load_sex_scores(sex_csv)
    matched = 0
    total = 0
    unmatched_names = []
    con = sqlite3.connect(db_path)
    try:
        con.executescript("DROP TABLE IF EXISTS iratebirds_aesthetic;")
        con.executescript(_CREATE_SQL)
        with open(predictions_csv, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += 1
                name = (row.get("sci_name") or "").strip()
                cid = class_map.get(name)
                if cid is None:
                    unmatched_names.append(name)
                    continue
                species_100 = normalize_score(
                    _to_float(row.get("predicted_attractiveness_full_model")))
                sx = sex_scores.get(name, {})
                male_100 = sx.get("male")
                female_100 = sx.get("female")
                con.execute(
                    "INSERT INTO iratebirds_aesthetic "
                    "(model_class_id, scientific_name, aesthetic_100, aesthetic_raw_10, "
                    " aesthetic_male, aesthetic_female, is_dimorphic, no_of_ratings, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, name,
                     derive_default_score(species_100, male_100, female_100),
                     _to_float(row.get("predicted_attractiveness_full_model")),
                     male_100, female_100,
                     is_dimorphic(male_100, female_100),
                     int(_to_float(row.get("no_of_ratings_used")) or 0),
                     "iratebirds_2023"),
                )
                matched += 1
        con.commit()
    finally:
        con.close()
    rate = matched / total if total else 0.0
    if unmatched_names:
        out_csv = os.path.join(os.path.dirname(os.path.abspath(predictions_csv)),
                               "iratebirds_unmatched.csv")
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["unmatched_sci_name"])
            for n in unmatched_names:
                w.writerow([n])
    return {"matched": matched, "total": total, "match_rate": rate}


def _load_class_map(db_path: str) -> Dict[str, int]:
    """从 bird_reference.sqlite 的 gbif_rarity_100 表取 {scientific_name: model_class_id}
    作为学名→类别映射（与罕见度同一匹配基准）。"""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT scientific_name, model_class_id FROM gbif_rarity_100 "
            "WHERE scientific_name IS NOT NULL AND model_class_id IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: int(r[1]) for r in rows}


def main() -> None:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ds = os.path.join(repo, "scripts_dev", "data_sources")
    ap = argparse.ArgumentParser()
    ap.add_argument("--predictions", default=os.path.join(
        ds, "iratebirds_final_predictions_average_fullmodel_subsetmodel_151122.csv"))
    ap.add_argument("--sex", default=os.path.join(
        ds, "iratebirds_pred_ratings_species_and_sex_level_120123.csv"))
    ap.add_argument("--db", default=os.path.join(
        repo, "birdid", "data", "bird_reference.sqlite"))
    args = ap.parse_args()
    class_map = _load_class_map(args.db)
    stats = build_aesthetic_table(args.predictions, args.sex, class_map, args.db)
    print(f"matched {stats['matched']}/{stats['total']} "
          f"({stats['match_rate']*100:.1f}%) → {args.db}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_build_iratebirds_table.py -v`
Expected: 2 passed

- [ ] **Step 6: 加 .gitignore + py_compile + 提交**

`.gitignore` 追加一行（用 Edit 工具，勿破坏现有内容）：
```
scripts_dev/data_sources/
```

```bash
.venv/bin/python -m py_compile scripts_dev/build_iratebirds_table.py
git add scripts_dev/build_iratebirds_table.py scripts_dev/__init__.py .gitignore
git add -f test_build_iratebirds_table.py
git commit -m "feat(aesthetic): iRateBird离线构建脚本+参考表(学名匹配+幂等)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

**手动收尾（开发者执行，非 subagent）：** 下载两个 figshare CSV 放 `scripts_dev/data_sources/`，跑 `.venv/bin/python scripts_dev/build_iratebirds_table.py`，确认匹配率 ≥90%，然后 `git add birdid/data/bird_reference.sqlite` 提交更新后的库。

---

### Task 3: 运行时查询 API

**Files:**
- Modify: `birdid/bird_database_manager.py`（在 `get_gbif_rarity_by_class_id` 之后新增方法）
- Test: `test_aesthetic_db_query.py`（仓库根目录）

**Interfaces:**
- Consumes: Task 2 的 `iratebirds_aesthetic` 表
- Produces: `BirdDatabaseManager.get_aesthetic_by_class_id(class_id: int) -> Optional[float]` —— 返回默认颜值分（0–100），未匹配/缺表返 None

- [ ] **Step 1: 写失败测试**

```python
"""
鸟种美学指数运行时查询单测：用临时库建表塞数据，验证命中/未命中/缺表容错。
Runtime aesthetic-lookup tests against a temp DB.
"""
import os
import sqlite3
import tempfile

import pytest

from birdid.bird_database_manager import BirdDatabaseManager


@pytest.fixture
def db_with_aesthetic(tmp_path):
    db = os.path.join(tmp_path, "ref.sqlite")
    con = sqlite3.connect(db)
    # BirdDatabaseManager.__init__ 会 SELECT COUNT(*) FROM ... 需最小可用库
    con.execute("CREATE TABLE bird_ioc (model_class_id INTEGER)")
    con.execute("INSERT INTO bird_ioc VALUES (1)")
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
    con.execute("CREATE TABLE bird_ioc (model_class_id INTEGER)")
    con.execute("INSERT INTO bird_ioc VALUES (1)")
    con.commit()
    con.close()
    mgr = BirdDatabaseManager(db_path=db)
    assert mgr.get_aesthetic_by_class_id(100) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_aesthetic_db_query.py -v`
Expected: FAIL — `AttributeError: 'BirdDatabaseManager' object has no attribute 'get_aesthetic_by_class_id'`

（若 `BirdDatabaseManager.__init__` 的 COUNT 查询针对的表名与夹具不符导致别的错误，先读 `birdid/bird_database_manager.py` 的 `__init__`/`_check_*`，把夹具里的 `bird_ioc` 建表语句改成 init 实际 SELECT 的表名，再继续。）

- [ ] **Step 3: 实现方法**

在 `birdid/bird_database_manager.py` 的 `get_gbif_rarity_by_class_id` 方法之后插入：

```python
    def get_aesthetic_by_class_id(self, class_id: int) -> Optional[float]:
        """
        根据模型类别ID获取鸟种美学（颜值）默认分（0–100）。

        Args:
            class_id: 鸟类类别ID（model_class_id）

        Returns:
            iRateBird 颜值分 (0–100, 越大越好看; None=未匹配或缺数据)

        Fetch the iRateBird species aesthetic score (0–100). Returns None on
        miss or when the iratebirds_aesthetic table is absent (older DB).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        "SELECT aesthetic_100 FROM iratebirds_aesthetic "
                        "WHERE model_class_id = ? AND aesthetic_100 IS NOT NULL LIMIT 1",
                        (class_id,),
                    )
                    row = cursor.fetchone()
                    if row and row[0] is not None:
                        return float(row[0])
                except sqlite3.OperationalError:
                    # 表不存在（旧库）→ 容错返 None
                    # Missing table (older DB) → tolerate, return None
                    return None
        except sqlite3.Error:
            return None
        return None
```

确认 `birdid/bird_database_manager.py` 顶部已 `from typing import Optional`（罕见度方法已用，通常已有；缺则补）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_aesthetic_db_query.py -v`
Expected: 3 passed

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile birdid/bird_database_manager.py
git add birdid/bird_database_manager.py
git add -f test_aesthetic_db_query.py
git commit -m "feat(aesthetic): bird_database_manager新增颜值查询API(缺表容错)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: report_db 加列 + 排序键

**Files:**
- Modify: `tools/report_db.py`（DB_COLUMNS 列表 ~L100 加字段；schema 迁移 v8→v9 ~L396 后；`sort_by` 分支 ~L690）
- Test: `test_report_db_aesthetic.py`（仓库根目录）

**Interfaces:**
- Consumes: 无（DB 层）
- Produces:
  - `photos` 表新列 `aesthetic_index REAL`
  - 排序键 `"species_beauty_desc"`（**不是** `aesthetic_desc`）

- [ ] **Step 1: 写失败测试**

```python
"""
report_db 颜值列迁移 + 排序单测。
report_db aesthetic-column migration + sort tests.
"""
import os
import tempfile

import pytest

from tools.report_db import ReportDB


def test_aesthetic_column_exists(tmp_path):
    db = ReportDB(str(tmp_path / "r.db"))
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(photos)")]
    assert "aesthetic_index" in cols
    db.close()


def test_migration_idempotent(tmp_path):
    """同目录二次打开（触发迁移路径）不报错、列仍只有一个。"""
    p = str(tmp_path / "r.db")
    ReportDB(p).close()
    db = ReportDB(p)  # 二次打开走已迁移分支
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(photos)")]
    assert cols.count("aesthetic_index") == 1
    db.close()


def test_species_beauty_sort_nulls_last(tmp_path):
    db = ReportDB(str(tmp_path / "r.db"))
    db.upsert_photo({"filename": "a.jpg", "aesthetic_index": 20.0})
    db.upsert_photo({"filename": "b.jpg", "aesthetic_index": 80.0})
    db.upsert_photo({"filename": "c.jpg", "aesthetic_index": None})
    rows = db.query_photos({"sort_by": "species_beauty_desc"})
    names = [r["filename"] for r in rows]
    assert names == ["b.jpg", "a.jpg", "c.jpg"]  # 高分在前, NULL 末位
    db.close()
```

**说明：** Step 2 前先读 `tools/report_db.py` 确认 `ReportDB` 的构造签名、`upsert_photo`/`query_photos`/`close` 的真实方法名与入参（本测试按常见命名假设）。若方法名不同，按实际改测试与断言，保持「列存在 / 迁移幂等 / 排序 NULL 末位」三个断言意图不变。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_report_db_aesthetic.py -v`
Expected: FAIL — `aesthetic_index` 不在列里 / `species_beauty_desc` 未识别导致顺序错

- [ ] **Step 3: 加 DB_COLUMNS 字段**

在 `tools/report_db.py` 的 DB_COLUMNS 列表，`("gbif_rarity_100", "REAL", None)` 之后插入：

```python
    # V9: iRateBird 鸟种美学(颜值)指数 (0-100，越大越好看，CC-BY 4.0 派生)
    # V9: iRateBird species aesthetic score (0-100, higher = prettier)
    ("aesthetic_index",  "REAL", None),
```

- [ ] **Step 4: 加 schema 迁移 v8→v9**

在 `tools/report_db.py` 的 v7→v8 升级块（`_update_schema_version("8")` / `current_version = "8"`）之后插入：

```python
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
```

**同时**：找到全新库初始化时写入的 schema 版本号（搜索 `_update_schema_version("8")` 或初始建库处的版本常量），把新库的初始版本升到 `"9"`，确保新库不再走迁移即为最新。若初始版本由 DB_COLUMNS 直接建表 + 单独写版本号，将该处 `"8"` 改 `"9"`。

- [ ] **Step 5: 加排序分支**

在 `tools/report_db.py` 的 `sort_by` 分支，`elif sort_by == "rarity_desc":` 块之后插入（**注意用新键 `species_beauty_desc`**）：

```python
        elif sort_by == "species_beauty_desc":
            # V9: 按鸟种颜值(iRateBird)降序 — 无数据排最后
            # V9: sort by species beauty (iRateBird) desc — missing data last
            order_sql = "ORDER BY COALESCE(aesthetic_index, -1e99) DESC, filename ASC"
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_report_db_aesthetic.py -v`
Expected: 3 passed

- [ ] **Step 7: py_compile + 提交**

```bash
.venv/bin/python -m py_compile tools/report_db.py
git add tools/report_db.py
git add -f test_report_db_aesthetic.py
git commit -m "feat(aesthetic): report_db加aesthetic_index列(v8→v9)+species_beauty_desc排序

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 识鸟链路查分 + photo_processor 落库

**Files:**
- Modify: `birdid/bird_identifier.py`（`_build_results` ~L948-967，罕见度查询旁加颜值查询 + result dict 加键）
- Modify: `core/photo_processor.py`（~L1265 取值、~L1311 db_updates、~L1347 meta_item，镜像 gbif_rarity_100 三处）
- Test: `test_aesthetic_wiring.py`（仓库根目录）

**Interfaces:**
- Consumes: Task 3 的 `get_aesthetic_by_class_id`
- Produces: 识别结果 dict 新增键 `"aesthetic_index"`；`report.db` 逐张 `aesthetic_index` 有值

- [ ] **Step 1: 写失败测试（验证 _build_results 注入颜值键）**

```python
"""
识鸟结果注入颜值键单测：用假 db_manager 验证 _build_results 输出含 aesthetic_index。
Verify _build_results injects the aesthetic_index key from db_manager.
"""
import pytest

import birdid.bird_identifier as bi


class _FakeDB:
    def get_gbif_rarity_by_class_id(self, cid, cc=None): return 50.0
    def get_iucn_by_class_id(self, cid): return None
    def get_aesthetic_by_class_id(self, cid): return 88.5
    def get_avilist_names_by_class_id(self, cid): return None
    # 依据 _build_results 实际调用补齐其余被调方法（读源码确认）


def test_build_results_has_aesthetic(monkeypatch):
    """_build_results 每条结果应带 aesthetic_index。"""
    # 读 birdid/bird_identifier.py 的 _build_results 签名后，构造最小入参调用。
    # 断言：返回的每个 dict 均含键 'aesthetic_index' 且值来自 db_manager。
    ...  # 见 Step 2 说明：先读源码补全调用
```

**说明（重要）：** `_build_results` 是内部函数，入参较多（class 分数、name_format、species_class_ids、photo_country_code、db_manager 等）。Step 2 前**先读 `birdid/bird_identifier.py:863-972` 完整签名与循环体**，据此把上面的 `test_build_results_has_aesthetic` 补成可运行的最小调用（构造一两个候选 class_id、传 `_FakeDB()`），断言结果含 `aesthetic_index == 88.5`。`_FakeDB` 按实际被调方法补齐。

- [ ] **Step 2: 补全并跑测试确认失败**

读源码补全测试后：
Run: `.venv/bin/python -m pytest test_aesthetic_wiring.py -v`
Expected: FAIL — 结果 dict 无 `aesthetic_index` 键（KeyError 或断言失败）

- [ ] **Step 3: bird_identifier 注入颜值**

在 `birdid/bird_identifier.py` 的 `_build_results`，紧接 `gbif_rarity_100 = (...)` 赋值块（~L948-952）之后加：

```python
        # iRateBird 鸟种美学(颜值)分（0–100，与照片无关的物种级指标）
        # iRateBird species aesthetic score (0–100, species-level, photo-agnostic)
        aesthetic_index = (
            db_manager.get_aesthetic_by_class_id(class_id) if db_manager else None
        )
```

并在 `results.append({...})` 的字典里，`"gbif_rarity_100": gbif_rarity_100,` 之后加一行：

```python
                "aesthetic_index": aesthetic_index,
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_aesthetic_wiring.py -v`
Expected: PASS

- [ ] **Step 5: photo_processor 落库（镜像 gbif_rarity_100 三处）**

读 `core/photo_processor.py:1260-1350`，按 `gbif_rarity_100` 的三处写法各加一处平行代码：

(a) ~L1265 取值旁：
```python
            aesthetic_index = top_result.get('aesthetic_index')  # iRateBird 颜值 (0-100)，可能为 None
```

(b) ~L1311-1312 `db_updates` 块，`db_updates['gbif_rarity_100'] = gbif_rarity_100` 旁：
```python
                        if aesthetic_index is not None:
                            db_updates['aesthetic_index'] = aesthetic_index
```

(c) ~L1347-1348 `meta_item` 块，`meta_item['gbif_rarity_100'] = gbif_rarity_100` 旁：
```python
                        if aesthetic_index is not None:
                            meta_item['aesthetic_index'] = aesthetic_index
```

放置校验：`aesthetic_index` 取值必须在 `top_result` 已定义之后、且与 `gbif_rarity_100` 同作用域；(b)(c) 的缩进/条件层级与相邻 `gbif_rarity_100` 写入完全一致。**不得**把 `aesthetic_index` 传入任何评分/权重计算。

- [ ] **Step 6: 全链路复读 + 编译检查**

重读 `core/photo_processor.py:1260-1350` 确认三处缩进与条件正确、无重名。

Run: `.venv/bin/python -m py_compile birdid/bird_identifier.py core/photo_processor.py`
Expected: 无输出（成功）

- [ ] **Step 7: 跑相关测试**

Run: `.venv/bin/python -m pytest test_aesthetic_wiring.py test_iratebirds_aesthetic.py -v`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add birdid/bird_identifier.py core/photo_processor.py
git add -f test_aesthetic_wiring.py
git commit -m "feat(aesthetic): 识鸟链路查颜值+photo_processor落库(镜像罕见度)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: UI 展示 + 排序项 + i18n

**Files:**
- Modify: `locales/zh_CN.json` / `locales/en_US.json`（加 3 个 browser 键）
- Modify: `ui/detail_panel.py`（建标签 ~L413、rows 元组 ~L481、更新 ~L890 后）
- Modify: `ui/filter_panel.py`（排序下拉 ~L200 后加项）
- Test: `test_aesthetic_i18n.py`（仓库根目录）

**Interfaces:**
- Consumes: Task 4 排序键 `species_beauty_desc`；`report.db` 的 `aesthetic_index`
- Produces: i18n 键 `browser.meta_species_beauty` / `browser.sort_species_beauty`；详情面板颜值行；筛选面板「鸟种颜值」排序项

- [ ] **Step 1: 写失败测试（i18n 键存在）**

```python
"""
鸟种颜值 UI i18n 键存在性单测。
Species-beauty UI i18n key presence tests.
"""
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("locale", ["locales/zh_CN.json", "locales/en_US.json"])
@pytest.mark.parametrize("key", ["meta_species_beauty", "sort_species_beauty"])
def test_browser_keys_exist(locale, key):
    data = json.loads(Path(locale).read_text(encoding="utf-8"))
    assert key in data["browser"]
    assert data["browser"][key].strip() != ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_aesthetic_i18n.py -v`
Expected: FAIL — `KeyError` / assert 失败（键不存在）

- [ ] **Step 3: 加 i18n 键**

`locales/zh_CN.json` 的 `browser` 段（`"meta_gbif_rarity"` 旁与 `"sort_rarity"` 旁）加：
```json
    "meta_species_beauty": "鸟种颜值",
    "sort_species_beauty": "鸟种颜值",
```

`locales/en_US.json` 对应位置加：
```json
    "meta_species_beauty": "Species Beauty",
    "sort_species_beauty": "Species Beauty",
```

- [ ] **Step 4: 跑 i18n 测试确认通过**

Run: `.venv/bin/python -m pytest test_aesthetic_i18n.py -v`
Expected: 4 passed

- [ ] **Step 5: detail_panel 加颜值行**

(a) 建标签（`ui/detail_panel.py` ~L413 `self._val_gbif_rarity = _make_value_label()` 旁）：
```python
        self._val_species_beauty = _make_value_label()
```

(b) rows 元组（~L481 `("browser.meta_gbif_rarity", self._val_gbif_rarity),` 之后）：
```python
            ("browser.meta_species_beauty", self._val_species_beauty),
```

(c) 更新逻辑（`_refresh_metadata` 内，~L901 GBIF 罕见度 else 块之后）加：
```python
        # iRateBird 鸟种颜值（0–100，无数据显示占位）
        # iRateBird species beauty (0–100, placeholder when missing)
        beauty = p.get("aesthetic_index")
        if beauty is not None:
            self._val_species_beauty.setText(f"{beauty:.0f}/100")
            self._val_species_beauty.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;"
            )
        else:
            self._val_species_beauty.setText(_unknown)
            self._val_species_beauty.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;"
            )
```

放置校验：`_unknown` 变量在 `_refresh_metadata` 作用域内已定义（罕见度 else 分支用了同名变量）；若不在同一作用域，读该方法确认其占位文案来源并复用。

- [ ] **Step 6: filter_panel 加排序项**

`ui/filter_panel.py` ~L200 `self._sort_combo.addItem(self.i18n.t("browser.sort_aesthetic"), "aesthetic_desc")` 之后加（**新键、新排序值**）：
```python
        self._sort_combo.addItem(self.i18n.t("browser.sort_species_beauty"), "species_beauty_desc")
```

- [ ] **Step 7: 编译检查 + GUI 冒烟**

Run: `.venv/bin/python -m py_compile ui/detail_panel.py ui/filter_panel.py`
Expected: 无输出

GUI 冒烟（手动/描述）：对一个已处理、含识鸟结果的小目录打开浏览器视图，确认：详情面板出现「鸟种颜值 XX/100」行；筛选面板排序下拉出现「鸟种颜值」并可切换排序；中英切换文案正常。

- [ ] **Step 8: 提交**

```bash
git add locales/zh_CN.json locales/en_US.json ui/detail_panel.py ui/filter_panel.py
git add -f test_aesthetic_i18n.py
git commit -m "feat(aesthetic): 详情面板颜值行+筛选排序项+中英键

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 手动收尾清单 / Manual Wrap-up（开发者，非 subagent）

1. 下载两个 figshare CSV → `scripts_dev/data_sources/`（见 Task 2 脚本头注释）。
2. 跑 `.venv/bin/python scripts_dev/build_iratebirds_table.py`，确认匹配率 ≥90%，查 `iratebirds_unmatched.csv` 抽检未匹配是否为真缺失。
3. `git add birdid/data/bird_reference.sqlite` 提交更新后的库（35MB 二进制）。
4. About 页/文档加 CC-BY 署名 + 文化偏差说明（spec §8）。

## 显式不做 / Out of Scope

- 性别检测、按国籍本地化颜值分、颜值参与评星、打包 raw ratings 文件（spec §7）。
