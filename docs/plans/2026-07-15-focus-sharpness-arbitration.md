# 对焦判定锐度仲裁实施方案 / Focus Sharpness Arbitration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对焦几何判定为 BAD/WORST 时，用鸟头实测归一化锐度做终审——达标者升为 GOOD（权重 0.9/1.0），像素证据优先于会撒谎的 EXIF 对焦点元数据（issue #107）。

**Architecture:** 在 `core/focus_point_detector.py` 加一个模块级纯函数 `arbitrate_focus_weights`（无 Qt/IO 依赖），`core/photo_processor.py` 在对焦权重计算块之后接一行仲裁调用；状态映射（weight≥0.9→GOOD）与报表 schema 均不改。设计依据：`docs/specs/2026-07-15-focus-sharpness-arbitration-design.md`（已批准）。

**Tech Stack:** Python 3 (.venv)，pytest，i18n JSON（zh_CN/en_US）。

## Global Constraints

- 锐度阈值与评星硬门槛同源：`self.settings.sharpness_threshold`（`ProcessingSettings`，`core/photo_processor.py:56`），不引入新阈值、不加设置开关。
- 仅当 `focus_sharpness_weight < 0.9` 触发仲裁；BEST(1.1)/GOOD(0.9) 不受影响。升级目标固定为 `(0.9, 1.0)`（GOOD），绝不升到 BEST。
- 关键点失败/无锐度数据（`norm_sharpness` 为 None 或 ≤0）→ 不仲裁，维持原判。
- **触发范围说明**：仲裁挂在整个对焦权重块之后（含 `verify_focus_in_bbox` 的 0.8/0.5 两档、`focus.is_focused=False` 的 0.8 档，以及「RAW 支持但读不到对焦数据且非手动对焦」的 0.7 档）——与 spec 2.3 的「weight < 0.9 即触发」权重规则一致：这些惩罚全部源于元数据（缺失或不可信），像素证据同样应能推翻。
- UTF-8 安全：locale JSON 中文用 `open(..., encoding='utf-8')` 读写；不用 sed/awk 碰中文文件。
- 注释：中文 + 英文双语（仓库注释规范）。
- 测试文件在仓库根目录 `test_*.py`；**`.gitignore` 忽略 `test_*.py`，提交必须 `git add -f`**。
- 每个任务收尾跑 `.venv/bin/python -m py_compile <改动的py文件>`。

---

### Task 1: `arbitrate_focus_weights` 纯函数（TDD）

**Files:**
- Modify: `core/focus_point_detector.py`（在 `verify_focus_in_bbox` 之后、`# 全局单例` 注释之前，约 L857 处新增函数）
- Test: `test_focus_arbitration.py`（仓库根目录，新建）

**Interfaces:**
- Consumes: 无（纯函数，只依赖标准库）
- Produces: `arbitrate_focus_weights(weights: Tuple[float, float], norm_sharpness: Optional[float], sharpness_threshold: float) -> Tuple[Tuple[float, float], bool]` —— 返回（可能升级的）`(锐度权重, 美学权重)` 与 `arbitrated` 标志。Task 2 的 photo_processor 接线依赖这个精确签名。

- [ ] **Step 1: 写失败测试**

```python
"""
issue #107 对焦锐度仲裁纯函数单测。
Unit tests for the focus sharpness arbitration pure function (issue #107).
"""
import pytest

from core.focus_point_detector import arbitrate_focus_weights


def test_worst_upgrades_when_sharpness_meets_threshold():
    """框外0.5档、锐度683≥阈值400 → 升(0.9,1.0)并标记仲裁（issue#107本案）"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 683.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_bad_in_bbox_upgrades_when_sharpness_meets_threshold():
    """框内0.8档同样可仲裁升级"""
    weights, arbitrated = arbitrate_focus_weights((0.8, 0.9), 500.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_no_focus_data_penalty_upgrades_when_sharp():
    """读不到对焦数据的0.7档也属元数据惩罚，锐度达标应升级"""
    weights, arbitrated = arbitrate_focus_weights((0.7, 0.9), 450.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_worst_maintained_when_sharpness_below_threshold():
    """A7V真跟丢场景：鸟头真糊 → 维持WORST原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 250.0, 400.0)
    assert weights == (0.5, 0.8)
    assert arbitrated is False


def test_boundary_exactly_at_threshold_upgrades():
    """恰等于阈值 → 达标（≥语义）"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 400.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_best_and_good_untouched():
    """BEST(1.1)/GOOD(0.9) 不触碰，即使锐度极高"""
    for w in [(1.1, 1.0), (0.9, 1.0), (1.0, 1.0)]:
        weights, arbitrated = arbitrate_focus_weights(w, 9999.0, 400.0)
        assert weights == w
        assert arbitrated is False


@pytest.mark.parametrize("bad_sharpness", [None, 0.0, -5.0])
def test_missing_sharpness_maintains_verdict(bad_sharpness):
    """关键点失败/无锐度数据 → 不仲裁维持原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), bad_sharpness, 400.0)
    assert weights == (0.5, 0.8)
    assert arbitrated is False


@pytest.mark.parametrize("bad_threshold", [None, 0.0, -1.0])
def test_invalid_threshold_maintains_verdict(bad_threshold):
    """阈值异常（未配置/非法）→ 保守维持原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 683.0, bad_threshold)
    assert weights == (0.5, 0.8)
    assert arbitrated is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py -v`
Expected: FAIL — `ImportError: cannot import name 'arbitrate_focus_weights'`

- [ ] **Step 3: 最小实现**

在 `core/focus_point_detector.py` 的 `verify_focus_in_bbox` 函数之后（`# 全局单例` 之前）插入：

```python
def arbitrate_focus_weights(
    weights: Tuple[float, float],
    norm_sharpness: Optional[float],
    sharpness_threshold: Optional[float],
) -> Tuple[Tuple[float, float], bool]:
    """
    对焦锐度仲裁 (issue #107)：像素证据优先于 EXIF 元数据。

    几何判定结果为 BAD/WORST（锐度权重 < 0.9，元数据惩罚）时，
    若鸟头实测归一化锐度达到用户锐度阈值（与评星硬门槛同源），
    则升级为 GOOD 档权重 (0.9, 1.0)。实测数据表明 EXIF 对焦点
    会「撒谎」（Z8 12% 框外全为记录偏差而非真脱焦），而真跟丢
    的照片鸟头锐度不达标，天然免疫误赦。

    参数:
    weights (Tuple[float, float]): 几何判定的 (锐度权重, 美学权重)
    norm_sharpness (Optional[float]): ISO 归一化后的鸟头实测锐度；
        None 或 ≤0 表示无有效数据（如关键点检测失败）
    sharpness_threshold (Optional[float]): 用户锐度达标阈值

    返回:
    Tuple[Tuple[float, float], bool]: (可能升级的权重, 是否发生仲裁)

    Focus sharpness arbitration (issue #107): pixel evidence beats EXIF
    metadata. When the geometric verdict is BAD/WORST (sharpness weight
    < 0.9, a metadata-driven penalty), upgrade to GOOD-tier weights
    (0.9, 1.0) if the measured normalized head sharpness reaches the
    user's sharpness threshold (same source as the rating hard gate).
    Truly-blurred shots fail the threshold, so they keep their verdict.

    Parameters:
    weights: (sharpness_weight, topiq_weight) from the geometric check
    norm_sharpness: ISO-normalized measured head sharpness; None or <=0
        means no valid data (e.g. keypoint detection failed)
    sharpness_threshold: the user's sharpness pass threshold

    Return:
    ((possibly upgraded weights), arbitrated flag)
    """
    sharp_w, _ = weights
    # 仅仲裁元数据惩罚档（<0.9）；BEST/GOOD 不触碰
    # Only arbitrate metadata-penalty tiers (<0.9); BEST/GOOD untouched
    if sharp_w >= 0.9:
        return weights, False
    # 无有效锐度数据或阈值异常 → 保守维持原判
    # No valid sharpness data or invalid threshold -> keep the verdict
    if norm_sharpness is None or norm_sharpness <= 0:
        return weights, False
    if sharpness_threshold is None or sharpness_threshold <= 0:
        return weights, False
    if norm_sharpness >= sharpness_threshold:
        return (0.9, 1.0), True
    return weights, False
```

注意：文件顶部已 `from typing import Optional, Tuple`（若无 `Tuple` 则补上）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py -v`
Expected: 10 passed

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/focus_point_detector.py
git add core/focus_point_detector.py
git add -f test_focus_arbitration.py
git commit -m "feat(focus): 锐度仲裁纯函数arbitrate_focus_weights(issue #107)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: photo_processor 接线 + i18n 日志键

**Files:**
- Modify: `core/photo_processor.py:44`（import 行）与 `core/photo_processor.py:2406-2407`（权重块结尾、`add_photo_stage('focus', ...)` 之前插入仲裁）
- Modify: `locales/zh_CN.json:297` 附近（`logs.caption_factors` 之后加键）
- Modify: `locales/en_US.json:297` 附近（同位置）
- Test: `test_focus_arbitration.py`（追加 i18n 键测试）

**Interfaces:**
- Consumes: Task 1 的 `arbitrate_focus_weights(weights, norm_sharpness, sharpness_threshold) -> ((float, float), bool)`
- Produces: 新 i18n 键 `logs.focus_arbitrated`（参数：`orig`/`sharp`/`thr`）；`focus_sharpness_weight` 仲裁后值 0.9 自然流入下游（最终评分 L2413、状态映射 L2437→GOOD、V2 池 `focus_status` 输入、caption/CSV 的 adj 锐度），**下游零改动**。

- [ ] **Step 1: 追加失败测试（i18n 键存在且可格式化）**

在 `test_focus_arbitration.py` 末尾追加：

```python
import json
from pathlib import Path


@pytest.mark.parametrize("locale_file", ["locales/zh_CN.json", "locales/en_US.json"])
def test_focus_arbitrated_log_key_exists_and_formats(locale_file):
    """仲裁日志键在中英locale都存在，且能用orig/sharp/thr格式化"""
    data = json.loads(Path(locale_file).read_text(encoding="utf-8"))
    template = data["logs"]["focus_arbitrated"]
    rendered = template.format(orig=0.5, sharp=683.0, thr=400)
    assert "0.5" in rendered
    assert "683" in rendered
    assert "400" in rendered
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py -v -k focus_arbitrated_log`
Expected: FAIL — `KeyError: 'focus_arbitrated'`

- [ ] **Step 3: 加 i18n 键**

用 Python（勿用 sed）或编辑器在两个 locale 的 `logs` 段、`"caption_factors"` 行之后各加一行。

`locales/zh_CN.json`（L297 `caption_factors` 之后）：

```json
    "focus_arbitrated": "[对焦仲裁] 元数据判权重 {orig:.2f}，但鸟头实测锐度 {sharp:.0f} ≥ 阈值 {thr:.0f} → 升为 GOOD",
```

`locales/en_US.json`（L297 `caption_factors` 之后）：

```json
    "focus_arbitrated": "[Focus Arbitration] metadata weight {orig:.2f}, but measured head sharpness {sharp:.0f} >= threshold {thr:.0f} -> upgraded to GOOD",
```

- [ ] **Step 4: 跑 i18n 测试确认通过**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py -v`
Expected: 12 passed

- [ ] **Step 5: photo_processor 接线**

修改 `core/photo_processor.py:44` 的 import：

```python
from core.focus_point_detector import get_focus_detector, verify_focus_in_bbox, arbitrate_focus_weights
```

在权重计算块结尾与 `add_photo_stage('focus', (time.time() - focus_start) * 1000)`（原 L2407）之间插入（缩进与 `if preliminary_result.rating >= 1 ...` 块体内语句对齐，即仍在该 if 块内、与 `if focus_data_available ...` 同级）：

```python
                    # V4.7(issue#107): 锐度仲裁——BAD/WORST(权重<0.9)且鸟头实测锐度
                    # 达标(≥用户阈值,与评星硬门槛同源)时升为GOOD(0.9/1.0)。
                    # 像素证据优先于EXIF对焦点元数据;真糊照片锐度不达标维持原判。
                    # V4.7 (issue #107): sharpness arbitration — when the verdict
                    # is BAD/WORST (weight < 0.9) and the measured head sharpness
                    # meets the user threshold (same source as the rating hard
                    # gate), upgrade to GOOD (0.9/1.0). Pixel evidence beats EXIF
                    # focus-point metadata; truly-blurred shots keep the verdict.
                    _orig_focus_w = focus_sharpness_weight
                    (focus_sharpness_weight, focus_topiq_weight), _focus_arbitrated = arbitrate_focus_weights(
                        (focus_sharpness_weight, focus_topiq_weight),
                        normalized_sharpness,
                        float(self.settings.sharpness_threshold),
                    )
                    if _focus_arbitrated:
                        self._log(self.i18n.t(
                            "logs.focus_arbitrated",
                            orig=_orig_focus_w,
                            sharp=normalized_sharpness,
                            thr=float(self.settings.sharpness_threshold),
                        ))
```

放置校验（实现者必读）：
- 必须在 `if preliminary_result.rating >= 1 or should_read_focus_for_detail:` 块**内部末尾**（覆盖 `verify_focus_in_bbox` 结果和 0.7 无数据分支），且在 `add_photo_stage('focus', ...)` 之前。
- 必须在最终评分 `rating_result = self.rating_engine.calculate(...)`（原 L2413）与状态映射（原 L2437）**之前**——仲裁后的 0.9 权重要流入两者。

- [ ] **Step 6: 全链路复读 + 编译检查**

重读插入点前后 60 行，确认：变量 `normalized_sharpness` 在此作用域已定义（L2283 赋值）、无重名冲突、缩进正确。

Run: `.venv/bin/python -m py_compile core/photo_processor.py core/focus_point_detector.py`
Expected: 无输出（成功）

- [ ] **Step 7: 跑全部相关测试**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py test_photo_processor_failure_handling.py test_rating_quota.py -v`
Expected: 全绿（后两个是回归保险：processor 失败路径与 V2 配额不受影响）

- [ ] **Step 8: 提交**

```bash
git add core/photo_processor.py locales/zh_CN.json locales/en_US.json
git add -f test_focus_arbitration.py
git commit -m "feat(focus): photo_processor接线锐度仲裁+中英日志(issue #107)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 验证（spec §5 逐条落实）

**Files:**
- 无新代码；跑验证并记录结果。

**Interfaces:**
- Consumes: Task 1/2 全部产出。
- Produces: 验证结论（贴到 issue #107 / 记入 memory）。

- [ ] **Step 1: 单测全量**

Run: `.venv/bin/python -m pytest test_focus_arbitration.py -v`
Expected: 12 passed（spec §5.1：达标升级/不达标维持/无数据维持/BEST·GOOD 不触碰/边界值 全覆盖）

- [ ] **Step 2: Z8/A7V 样本回归（需用户本地照片，交互确认）**

spec §5.2 的 565 张 Z8 分析脚本在上一会话 scratchpad，未入仓。执行到此步时**向用户确认样本目录位置**后，用 GUI 或 CLI 对 Z8 样本目录整批重跑一次，核对：
- 日志中出现 `[对焦仲裁]` 记录，数量级与「12% 框外 + 13% 框内惩罚中头部锐度达标者」吻合；
- A7V 真跟丢连拍（DSC05403-05408）鸟头不达标者维持 WORST/失焦。

若用户样本不在手边，此步可延后，不阻塞合并（单测+GUI smoke 已覆盖逻辑正确性）。

- [ ] **Step 3: GUI 真机 smoke**

启动应用对任一含 RAW 的小目录跑一次批处理，确认：
- 处理日志出现仲裁记录（若样本触发）；
- 详情面板对焦状态显示正确（升级片显示「合焦/GOOD」）；
- 中英界面语言切换后日志键均正常渲染（无 KeyError 回退）。

- [ ] **Step 4: 收尾**

- issue #107 回复补充：仲裁已实装（注明 nightly/版本），Z50 II 解析验证仍等报告人 NEF，到货后单独验证（与本方案不冲突，spec §2.2）。
- 更新 memory `issue-107-focus-assessment.md`：方案已实施，剩余事项仅 NEF 验证。

---

## 显式不做 / Out of Scope（spec §2.2 已否决）

- 纯距离容差带、「眼周 2× 头径」中间层——会误赦 A7V 真跟丢，仅日志保留距离信息（现状已有，不新增代码）。
- 修改各品牌 EXIF 解析（Z8/A7V/A1II/OM-1/X-T5 实测健康）；Z50 II 解析问题等 NEF 到货单独立项。
- 设置开关——行为严格单向（只减少错误惩罚），裁决条件即用户自设锐度标准。
- 报表 schema 变更——`focus_status` 照旧写 GOOD。
