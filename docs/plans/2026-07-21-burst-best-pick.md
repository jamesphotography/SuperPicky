# 连拍组「最佳一张」多维评分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把连拍组内"最佳一张"从"纯头部锐度"改为分层排序(对焦仲裁分档 → 层内眼清为主+头锐为辅),让组内封面/标红那张是"对焦到眼、眼睛实"的一张。

**Architecture:** 新增无 Qt 依赖的纯函数模块 `core/burst_ranking.py` 计算分层排序键;`ui/results_browser_window.py` 的唯一"最佳"计算点 `_burst_representative()` 改用该键。读时即时算,不动 DB schema、不改处理流程、旧目录立即生效、可随时回退。

**Tech Stack:** Python 3.12、pytest、PySide6(仅 Task 2 冒烟测试用 offscreen)。

## Global Constraints

- 单一事实源:评分只读 `report.db` 已有字段 `head_sharp` / `left_eye` / `right_eye` / `focus_status`;**不新增 DB 列、不改处理流程、不重跑目录**。
- `focus_status` 取值集合:`BEST` / `GOOD` / `BAD` / `WORST`(大写字符串),可能为空/None。
- 默认权重 `W_EYE = 0.7`、`W_HEAD = 0.3`;`focus_status` 缺失归中性档 `DEFAULT_TIER = 2`(GOOD)。权重为默认值,后续用真实数据 A/B 标定,不在本计划范围。
- 左右眼合成:`max(left_eye, right_eye)`(鸟多侧拍,取对焦侧那只眼)。
- UTF-8;中文+英文注释;类型注解;不动 `_burst_sort_key`(连拍展开顺序保持时间序)。
- 测试文件放仓库根目录 `test_*.py`(项目惯例,如 `test_settings_center.py`)。
- 验证命令用 `.venv/bin/python`。

---

## File Structure

- **Create** `core/burst_ranking.py` — 无 Qt 依赖的纯函数:眼清合成、对焦分档、分层排序键。唯一职责:把一张照片的字典映射成可比较的排序键。
- **Create** `test_burst_ranking.py` — 纯函数单测(不需 Qt)。
- **Modify** `ui/results_browser_window.py` — `_burst_representative()` 改用 `core.burst_ranking.burst_composite_key` 作 key。
- **Create** `test_burst_representative.py` — 接线冒烟测试(offscreen import)。

---

### Task 1: 分层排序纯函数 `core/burst_ranking.py`

**Files:**
- Create: `core/burst_ranking.py`
- Test: `test_burst_ranking.py`

**Interfaces:**
- Consumes: 无(叶子模块)。
- Produces:
  - `eye_sharp(photo: dict) -> float` — `max(left_eye, right_eye)`,缺失/非数 → 0.0
  - `focus_tier(photo: dict) -> int` — `BEST=3/GOOD=2/BAD=1/WORST=0`,缺失/未知 → 2
  - `burst_composite_key(photo: dict) -> tuple[int, float]` — `(focus_tier, W_EYE*eye_sharp + W_HEAD*head_sharp)`,供 `max(photos, key=...)` 用
  - 模块常量 `W_EYE=0.7`、`W_HEAD=0.3`、`DEFAULT_TIER=2`、`FOCUS_TIER: dict[str,int]`

- [ ] **Step 1: 写失败测试**

创建 `test_burst_ranking.py`:

```python
# -*- coding: utf-8 -*-
"""
core/burst_ranking.py 纯函数单测 — 连拍组「最佳一张」分层排序。
Pure-function tests for burst best-pick tiered ranking (no Qt).
"""
from core.burst_ranking import eye_sharp, focus_tier, burst_composite_key


def test_eye_sharp_takes_max_of_two_eyes():
    # 鸟多侧拍,只有对焦侧那只眼有效 / side profile: take the in-focus eye
    assert eye_sharp({"left_eye": 20.0, "right_eye": 88.0}) == 88.0
    assert eye_sharp({"left_eye": 88.0, "right_eye": 0.0}) == 88.0


def test_eye_sharp_missing_fields_zero():
    assert eye_sharp({}) == 0.0
    assert eye_sharp({"left_eye": None, "right_eye": None}) == 0.0


def test_focus_tier_mapping_and_default():
    assert focus_tier({"focus_status": "BEST"}) == 3
    assert focus_tier({"focus_status": "GOOD"}) == 2
    assert focus_tier({"focus_status": "BAD"}) == 1
    assert focus_tier({"focus_status": "WORST"}) == 0
    # 缺失/空/未知 → 中性档 2 / missing/unknown → neutral GOOD tier
    assert focus_tier({}) == 2
    assert focus_tier({"focus_status": ""}) == 2
    assert focus_tier({"focus_status": "weird"}) == 2


def test_composite_key_focus_tier_dominates():
    # 跨档:BEST 档一张,即使头锐更低,也胜过 BAD 档最锐那张
    best = {"head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"}
    sharp_but_bad_focus = {"head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"}
    assert burst_composite_key(best) > burst_composite_key(sharp_but_bad_focus)


def test_composite_key_same_tier_eye_leads():
    # 同档:眼清为主(头锐差距不大时,眼清高者胜)
    eye_hi = {"head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"}
    eye_lo = {"head_sharp": 99, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"}
    assert burst_composite_key(eye_hi) > burst_composite_key(eye_lo)


def test_max_over_example_group_picks_focus_best():
    # spec §问题示例组:现状(纯头锐)会选 ②(头95);新逻辑应选 ③(对焦 BEST+眼实)
    group = [
        {"filename": "1", "head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"},
        {"filename": "2", "head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"},
        {"filename": "3", "head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"},
        {"filename": "4", "head_sharp": 70, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"},
    ]
    best = max(group, key=burst_composite_key)
    assert best["filename"] == "3"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_burst_ranking.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.burst_ranking'`

- [ ] **Step 3: 写实现**

创建 `core/burst_ranking.py`:

```python
# -*- coding: utf-8 -*-
"""
连拍组「最佳一张」分层排序 / Burst best-pick tiered ranking.

无 Qt 依赖的纯函数模块。把一张照片的结果字典映射成一个可比较的排序键,
供 `max(photos, key=burst_composite_key)` 选出组内"最佳"。

分层语义:先按对焦仲裁 focus_status 分档(对焦准 > 一切),同档内再按
"眼清为主 + 头锐为辅"的加权分精排。eye 与 head_sharp 同源于同一清晰度
度量、同量纲,可直接加权,无需跨量纲归一化。

Pure, Qt-free module. Maps a photo result dict to a comparable ranking key
for `max(photos, key=burst_composite_key)`. Tier by focus verdict first
(focus beats everything), then within a tier weight eye sharpness over head
sharpness. eye and head_sharp share one sharpness metric (same scale), so a
direct weighted sum needs no cross-scale normalization.
"""
from typing import Tuple

# 权重与档位(默认值;后续用真实数据 A/B 标定)/ weights & tiers (defaults; A/B-tuned later)
W_EYE: float = 0.7
W_HEAD: float = 0.3
DEFAULT_TIER: int = 2  # focus_status 缺失 → 中性 GOOD 档
FOCUS_TIER = {"BEST": 3, "GOOD": 2, "BAD": 1, "WORST": 0}


def _to_float(value) -> float:
    """安全转 float,失败返回 0.0 / safe float, 0.0 on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def eye_sharp(photo: dict) -> float:
    """
    眼睛清晰度:取左右眼的较大者(鸟多侧拍,只有对焦侧那只眼有效)。
    Eye sharpness: max of the two eyes (side profiles expose one in-focus eye).
    """
    return max(_to_float(photo.get("left_eye")), _to_float(photo.get("right_eye")))


def focus_tier(photo: dict) -> int:
    """
    对焦仲裁分档:BEST=3/GOOD=2/BAD=1/WORST=0;缺失或未知值 → 中性 GOOD 档(2),
    避免"没算出对焦"的照片一律沉底。
    Focus verdict tier; missing/unknown → neutral GOOD tier (2).
    """
    status = str(photo.get("focus_status") or "").strip().upper()
    return FOCUS_TIER.get(status, DEFAULT_TIER)


def burst_composite_key(photo: dict) -> Tuple[int, float]:
    """
    组内"最佳"分层排序键:(对焦档, 眼清为主+头锐为辅的加权分)。
    元组字典序:先比对焦档,同档再比加权分。供 max(photos, key=...) 使用。
    Tiered key (focus_tier, W_EYE*eye + W_HEAD*head) for max(...).
    """
    layer_score = W_EYE * eye_sharp(photo) + W_HEAD * _to_float(photo.get("head_sharp"))
    return (focus_tier(photo), layer_score)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_burst_ranking.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/burst_ranking.py test_burst_ranking.py
git add core/burst_ranking.py test_burst_ranking.py
git commit -m "feat(burst): 连拍组最佳分层排序纯函数(对焦档→眼清+头锐)+单测"
```

---

### Task 2: 接线 `_burst_representative` 改用分层排序键

**Files:**
- Modify: `ui/results_browser_window.py`(函数 `_burst_representative`,约在 :163)
- Test: `test_burst_representative.py`

**Interfaces:**
- Consumes: `core.burst_ranking.burst_composite_key`(Task 1)
- Produces: `_burst_representative(photos: list) -> dict` 语义变更为"分层排序最佳",签名不变。折叠封面(:1160)与展开标红 `is_burst_best`(:1157)自动跟随,无需另改。

- [ ] **Step 1: 写失败测试**

创建 `test_burst_representative.py`:

```python
# -*- coding: utf-8 -*-
"""
_burst_representative 接线冒烟测试:验证组内"最佳"用分层排序,而非纯头锐。
Wiring smoke test: representative uses tiered ranking, not raw head sharpness.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.results_browser_window import _burst_representative


def test_representative_prefers_focus_best_over_sharpest():
    # 现状(纯 head_sharp)会选 ②(头95);接线后应选 ③(对焦 BEST+眼实)
    group = [
        {"filename": "1", "head_sharp": 90, "left_eye": 85, "right_eye": 0, "focus_status": "GOOD"},
        {"filename": "2", "head_sharp": 95, "left_eye": 60, "right_eye": 0, "focus_status": "BAD"},
        {"filename": "3", "head_sharp": 80, "left_eye": 88, "right_eye": 0, "focus_status": "BEST"},
        {"filename": "4", "head_sharp": 70, "left_eye": 70, "right_eye": 0, "focus_status": "GOOD"},
    ]
    assert _burst_representative(group)["filename"] == "3"


def test_representative_all_fields_missing_does_not_crash():
    # 退化保护:字段全缺失时不抛异常,仍返回组内某一张
    group = [{"filename": "a"}, {"filename": "b"}]
    assert _burst_representative(group)["filename"] in {"a", "b"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_burst_representative.py -q`
Expected: FAIL — 第一个测试断言 `"3"`,但现状 `_burst_representative` 用 `head_sharp` 会选 `"2"`(头95)。

- [ ] **Step 3: 改实现**

> ⚠️ **读取污染提示:** `ui/results_browser_window.py:150-166` 区域此前用 grep/Read 反复读到被污染的内容(重复行/悬空 return/注入文本)。做本步 Edit 前,**先用 Read 干净读到 `_burst_representative` 的真实源码**确认 `old_string`;若仍被污染,改用下方 Python 脚本按函数体替换,不要凭被污染的文本硬匹配。

在 `ui/results_browser_window.py` 顶部 import 区加入:

```python
from core.burst_ranking import burst_composite_key
```

把 `_burst_representative` 函数体改为:

```python
def _burst_representative(photos: list) -> dict:
    """
    组内"最佳"代表:分层排序(对焦仲裁档 → 层内眼清为主+头锐为辅)。
    折叠封面与展开标红均取此结果。
    Pick the burst representative via tiered ranking (focus tier, then
    eye-led + head sharpness). Used for the collapsed cover and the
    highlighted "best" member.
    """
    return max(photos, key=burst_composite_key)
```

若 Edit 因污染无法精确匹配,用此脚本替换(按行定位函数,不依赖被污染文本):

```python
# scratchpad 一次性脚本 / one-off replace, run with .venv/bin/python
import re
p = "ui/results_browser_window.py"
s = open(p, encoding="utf-8").read()
if "from core.burst_ranking import burst_composite_key" not in s:
    s = s.replace("def _burst_representative(",
                  "from core.burst_ranking import burst_composite_key\n\n\ndef _burst_representative(", 1)
s = re.sub(r"def _burst_representative\(photos: list\) -> dict:.*?\n(?=\S)",
           'def _burst_representative(photos: list) -> dict:\n'
           '    """组内最佳:分层排序(对焦档→眼清+头锐) / tiered burst representative."""\n'
           '    return max(photos, key=burst_composite_key)\n\n',
           s, count=1, flags=re.DOTALL)
open(p, "w", encoding="utf-8").write(s)
```

> 注:上面 import 建议移到文件既有 import 区(顶部),脚本为兜底把它就近插在函数前也可工作;实现者优先手动 Edit 到 import 区,保持整洁。

- [ ] **Step 4: 跑测试确认通过 + 无回归**

```bash
.venv/bin/python -m py_compile ui/results_browser_window.py
.venv/bin/python -m pytest test_burst_representative.py test_burst_ranking.py -q
```
Expected: PASS(全部)。

- [ ] **Step 5: 提交**

```bash
git add ui/results_browser_window.py test_burst_representative.py
git commit -m "feat(burst): 组内最佳接入分层排序键(替换纯头锐 _burst_representative)"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 读时算不落库 → Task 2 改 key,零 schema 改动 ✅
- §3.2 分层排序键(focus 档 + w_eye·max(眼)+w_head·头锐)→ Task 1 `burst_composite_key` ✅
- §3.3 唯一接入点 `_burst_representative` → Task 2 ✅(纯函数改抽到 `core/burst_ranking.py`,较 spec 原文更可测,已在计划开头说明)
- §4.1 睁眼未必可测 → 属验证阶段(§6),非代码任务;计划不实现"睁眼检测",eye 语义为眼区清晰度,符合 spec 降级约定 ✅
- §4.3 字段缺失兜底 → Task 1 `test_*_missing_*` + Task 2 `all_fields_missing` ✅
- §4.4 单眼可见 → Task 1 `eye_sharp_takes_max` ✅
- §5 测试计划 → Task 1/2 测试逐条覆盖(跨档/同档/eye 合成/缺失/退化)✅
- §6 A/B 验证、§7 成本、§8 备选 → 非编码项,不产生任务 ✅

**2. Placeholder scan:** 无 TBD/TODO/"handle edge cases";所有代码步含完整代码 ✅

**3. Type consistency:** `eye_sharp/focus_tier/burst_composite_key` 签名在 Task 1 定义、Task 2 仅用 `burst_composite_key`,名称一致;返回 `tuple[int,float]` 与 `max(key=...)` 用法一致 ✅

无遗漏,无需回改。
