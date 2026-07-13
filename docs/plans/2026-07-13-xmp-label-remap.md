# 颜色标签默认映射 B+ 实施计划 / XMP Label Remap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 颜色标签默认映射改为 蓝=飞鸟 / 绿=精焦(BEST) / 红=脱焦(BAD/WORST) / GOOD 无标签（B+ 方案）。

**Architecture:** 从 `photo_processor` 内联 label 逻辑（:2538-2546）抽出模块级纯函数 `compute_xmp_label(is_flying, focus_status, translate)`（可单测），沿用「i18n 缺 key 回退英文色名」的 4.3.0 白框防御；i18n `xmp_labels` 段改两键加一键。

**Tech Stack:** 纯 Python + i18n JSON + pytest。

**Spec:** `docs/specs/2026-07-13-xmp-label-remap-design.md`

## Global Constraints

- 优先级：飞鸟(蓝) > BEST(绿) > BAD/WORST(红)；GOOD/无鸟返回 None。
- 语言包缺 key 时回退英文色名，绝不把 key 串写进 LR。
- 不开放自定义映射；ChangeLog（合并 nightly 时）注明含义变更与新旧对照。
- 新测试文件根目录 `git add -f`；注释中英双写；py_compile + pytest 全绿；提交 dev。

---

### Task 1: compute_xmp_label 纯函数 + i18n 重映射

**Files:**
- Modify: `core/photo_processor.py`（模块级新增纯函数；调用点 :2538-2546 替换）
- Modify: `locales/zh_CN.json` / `locales/en_US.json`（`xmp_labels` 段 :7-10）
- Test: `test_xmp_label_remap.py`（新建）

**Interfaces:**
- Produces: `compute_xmp_label(is_flying: bool, focus_status: Optional[str], translate) -> Optional[str]`（`translate` 为 `i18n.t` 同签名可调用）。

- [ ] **Step 1: 写失败测试（新建 test_xmp_label_remap.py）**

```python
# -*- coding: utf-8 -*-
"""
颜色标签默认映射 B+ 的测试:蓝=飞鸟/绿=精焦/红=脱焦/GOOD 无标签,
i18n 缺 key 回退英文,locales 色名断言。

Tests for the B+ XMP label remap: blue=flying, green=critical focus,
red=defocused, no label for GOOD; English fallback on missing i18n keys;
locale color-name assertions.
"""
import json


def _t_zh(key, **kw):
    return {"xmp_labels.flight": "蓝色", "xmp_labels.focus": "绿色",
            "xmp_labels.defocus": "红色"}.get(key, key)


def _t_missing(key, **kw):
    return key  # 模拟语言包缺 key / simulate missing keys


def test_compute_xmp_label_mapping_and_priority():
    """飞鸟优先于对焦;BEST→绿,BAD/WORST→红,GOOD/None→无标签。"""
    from core.photo_processor import compute_xmp_label as f

    assert f(True, "BEST", _t_zh) == "蓝色"      # 飞鸟优先 / flying wins
    assert f(True, None, _t_zh) == "蓝色"
    assert f(False, "BEST", _t_zh) == "绿色"
    assert f(False, "BAD", _t_zh) == "红色"
    assert f(False, "WORST", _t_zh) == "红色"
    assert f(False, "GOOD", _t_zh) is None       # 常态无标签 / no label
    assert f(False, None, _t_zh) is None


def test_compute_xmp_label_english_fallback():
    """语言包缺 key → 回退英文色名,绝不返回 key 串(4.3.0 白框防御)。"""
    from core.photo_processor import compute_xmp_label as f

    assert f(True, None, _t_missing) == "Blue"
    assert f(False, "BEST", _t_missing) == "Green"
    assert f(False, "WORST", _t_missing) == "Red"


def test_locale_label_color_names():
    """两语言包的 xmp_labels 值符合 B+ 映射(蓝/绿/红,Blue/Green/Red)。"""
    zh = json.load(open("locales/zh_CN.json", encoding="utf-8"))["xmp_labels"]
    en = json.load(open("locales/en_US.json", encoding="utf-8"))["xmp_labels"]
    assert zh == {"flight": "蓝色", "focus": "绿色", "defocus": "红色"}
    assert en == {"flight": "Blue", "focus": "Green", "defocus": "Red"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/jameszhenyu/Documents/JamesAPPS/SuperPicky2026
.venv/bin/python -m pytest test_xmp_label_remap.py -v
```

预期：3 个全 FAIL（函数未定义 / locales 仍旧值）。

- [ ] **Step 3: 实现**

3a. `core/photo_processor.py` 模块级（import 区之后、类定义之前的工具函数区）：

```python
def compute_xmp_label(is_flying: bool, focus_status: Optional[str], translate) -> Optional[str]:
    """
    计算 XMP:Label 颜色名(B+ 默认映射,Paul P2):
    飞鸟=蓝(优先) > 精焦 BEST=绿 > 脱焦 BAD/WORST=红;GOOD/无鸟不打标签。
    Lightroom 按本地化字符串匹配标签色,语言包缺 key 时回退英文色名,
    绝不把 key 串写进 LR(4.3.0 白框陷阱防御)。

    Compute the XMP:Label color name (B+ default mapping): flying=Blue
    (highest priority) > BEST=Green > BAD/WORST=Red; GOOD or no bird gets
    no label. Falls back to English color names when the language pack
    lacks a key (LR matches labels by localized string).

    参数 / Parameters:
        is_flying (bool): 是否飞鸟 / whether the bird is flying.
        focus_status (Optional[str]): BEST/GOOD/BAD/WORST 或 None。
        translate: i18n.t 同签名的翻译函数 / i18n.t-compatible callable.

    返回 / Returns:
        Optional[str]: 本地化颜色名;None=不写标签。
    """
    if is_flying:
        label = translate("xmp_labels.flight")
        return "Blue" if label == "xmp_labels.flight" else label
    if focus_status == "BEST":
        label = translate("xmp_labels.focus")
        return "Green" if label == "xmp_labels.focus" else label
    if focus_status in ("BAD", "WORST"):
        label = translate("xmp_labels.defocus")
        return "Red" if label == "xmp_labels.defocus" else label
    return None
```

3b. 调用点（:2538-2546）替换（保留其上 V4.3.0 白框注释块，注释中
「飞鸟=绿色/Green，头部精焦=红色/Red」一句更新为「蓝=飞鸟/绿=精焦/红=脱焦」）：

```python
                # V4.6(Paul P2/B+): 蓝=飞鸟 > 绿=精焦 > 红=脱焦,GOOD 无标签
                # V4.6 (Paul P2/B+): Blue=flying > Green=BEST > Red=defocused.
                label = compute_xmp_label(is_flying, focus_status, self.i18n.t)
```

3c. locales 两文件 `xmp_labels` 段（:7-10）：

zh_CN.json：

```json
  "xmp_labels": {
    "flight": "蓝色",
    "focus": "绿色",
    "defocus": "红色"
  },
```

en_US.json：

```json
  "xmp_labels": {
    "flight": "Blue",
    "focus": "Green",
    "defocus": "Red"
  },
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

```bash
.venv/bin/python -m pytest test_xmp_label_remap.py test_rating_quota.py -v
.venv/bin/python -m py_compile core/photo_processor.py
.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]"
```

预期：全 PASS。注意调用点原位置在 `focus_status` 赋值（:2396-2410）之后——替换前确认变量已就绪（是，:2538 在 :2410 之后）。

- [ ] **Step 5: 提交**

```bash
git add core/photo_processor.py locales/zh_CN.json locales/en_US.json && git add -f test_xmp_label_remap.py
git commit -m "feat(labels): 颜色标签默认映射B+——蓝=飞鸟/绿=精焦/红=脱焦(Paul P2)"
```
