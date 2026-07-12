# 识鸟结果写入 Lightroom 关键字 实施计划 / Bird ID → LR Keywords Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 高置信度识鸟结果在写 `XMP:Title` 的同时，把鸟名（跟随界面语言）以 merge-add 语义写入标准关键字 `XMP-dc:Subject`（Lightroom Keywords），带设置开关（默认开）。

**Architecture:** `meta_item` 新增 `'keywords': List[str]` 字段（语义=确保存在）。exiftool_manager 内：纯函数 `merge_keyword_lists` 负责合并去重（可单测），`_keywords_args` 负责「读现有列表→合并→生成 `-sep ;; -XMP-dc:Subject<=tmp` 参数」，接入三条写入路径（常驻批量/一次性 subprocess/XMP 侧车）。photo_processor 识鸟收尾在写 Title 的 meta_item 上按开关追加字段。开关走 advanced_config SSOT + 设置中心识鸟页复选框。

**Tech Stack:** ExifTool（`-sep ";;"` + `-TAG<=UTF-8临时文件` 整表赋值，已实验验证；`+=<file` 不可用）、PySide6、pytest。

**Spec:** `docs/specs/2026-07-12-birdid-lr-keywords-design.md`

## Global Constraints

- 中文元数据必须经 UTF-8 临时文件写入，禁止内联 CLI 值（CLAUDE.md 铁律）。
- merge-add 语义：绝不清除用户已有关键字；重跑幂等（无新增则跳过写入）。
- 读现有关键字：ARW/侧车路径 `.xmp` 存在读侧车，否则读文件本体。
- 开关 `birdid_write_keywords` 默认 `True`；setter 跟随 birdid_* 惯例内部调 `save()`。
- 仅主处理流程；LR 插件路径(birdid_server)不做。
- 新测试文件在仓库根目录，提交须 `git add -f`；注释中英双写；`py_compile` + pytest 全绿；提交 dev。

---

### Task 1: exiftool_manager — 关键字合并与三路写入

**Files:**
- Modify: `tools/exiftool_manager.py`（模块级纯函数 + 新私有方法；接线 `batch_set_metadata` :1055-1120 非 ARW 参数区、`_write_metadata_subprocess` :593、`_write_metadata_xmp_sidecar` :671）
- Test: `test_birdid_lr_keywords.py`（新建）

**Interfaces:**
- Consumes: `self.read_metadata(file_path, extra_args=['-XMP-dc:Subject'])`（既有，返回 dict 或 None；Subject 值可能是 str 或 list）。
- Produces:
  - 模块级 `merge_keyword_lists(existing: List[str], additions: List[str]) -> Optional[List[str]]`（None=无新增；否则返回合并后完整列表，现有在前保序）。
  - `ExifToolManager._keywords_args(self, item: Dict, read_target: str, temp_files: List[str]) -> List[str]`（item 无 `keywords` 或无新增时返回 `[]`；否则返回 `['-sep', ';;', '-XMP-dc:Subject<=<tmp>']` 并把 tmp 路径挂入 temp_files 由调用方清理）。

- [ ] **Step 1: 写失败测试（新建 test_birdid_lr_keywords.py）**

```python
# -*- coding: utf-8 -*-
"""
识鸟结果写入 Lightroom 关键字(XMP-dc:Subject)的测试:纯合并逻辑 +
真实 exiftool 端到端(含中文/幂等/保留用户关键字)。

Tests for writing Bird ID results into Lightroom keywords: pure merge
logic plus a real-exiftool end-to-end roundtrip (Chinese values,
idempotency, preservation of user keywords).
"""
import os
import subprocess
import tempfile

import pytest


def test_merge_keyword_lists_semantics():
    """
    合并语义:保留已有、追加缺失、去重、无新增返回 None、中文正常。
    Merge semantics: keep existing, append missing, dedup, None when
    nothing to add, Chinese values handled.
    """
    from tools.exiftool_manager import merge_keyword_lists

    assert merge_keyword_lists([], ["白胸鸲鹟"]) == ["白胸鸲鹟"]
    assert merge_keyword_lists(["UserKW"], ["白胸鸲鹟"]) == ["UserKW", "白胸鸲鹟"]
    assert merge_keyword_lists(["白胸鸲鹟"], ["白胸鸲鹟"]) is None      # 已存在
    assert merge_keyword_lists(["A", "B"], ["B", "A"]) is None          # 全存在
    assert merge_keyword_lists(["A"], ["B", "B"]) == ["A", "B"]         # 输入去重
    assert merge_keyword_lists([], []) is None                          # 空输入


def _exiftool_read_subject(path: str):
    out = subprocess.run(
        ["exiftool", "-j", "-XMP-dc:Subject", path],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout
    import json
    data = json.loads(out)[0]
    subj = data.get("Subject", [])
    return [subj] if isinstance(subj, str) else subj


@pytest.mark.skipif(
    subprocess.run(["which", "exiftool"], capture_output=True).returncode != 0,
    reason="exiftool not on PATH",
)
def test_keywords_end_to_end_merge_and_idempotent():
    """
    端到端:临时 JPG 预置用户关键字 → manager 写鸟名关键字 → 读回含两者;
    重复写第二次不产生重复(幂等)。

    End-to-end: seed a user keyword on a temp JPG, write the species
    keyword through the manager, read back both; a second write adds no
    duplicates (idempotent).
    """
    from PIL import Image
    from tools.exiftool_manager import get_exiftool_manager

    with tempfile.TemporaryDirectory() as td:
        jpg = os.path.join(td, "kw_test.jpg")
        Image.new("RGB", (8, 8), (200, 120, 40)).save(jpg, "JPEG")
        subprocess.run(
            ["exiftool", "-overwrite_original", "-XMP-dc:Subject=UserKW", jpg],
            capture_output=True, check=True,
        )

        mgr = get_exiftool_manager()
        for _ in range(2):  # 第二次验证幂等 / second pass proves idempotency
            stats = mgr.batch_set_metadata([{"file": jpg, "keywords": ["白胸鸲鹟"]}])
            assert stats["failed"] == 0

        subjects = _exiftool_read_subject(jpg)
        assert subjects == ["UserKW", "白胸鸲鹟"], subjects
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/jameszhenyu/Documents/JamesAPPS/SuperPicky2026
.venv/bin/python -m pytest test_birdid_lr_keywords.py -v
```

预期：两个测试均 FAIL/ERROR（`merge_keyword_lists` 未定义）。

- [ ] **Step 3: 实现 `tools/exiftool_manager.py`**

3a. 模块级纯函数（放在文件顶部工具函数区、类定义之前）：

```python
def merge_keyword_lists(existing: List[str], additions: List[str]) -> Optional[List[str]]:
    """
    合并关键字列表(merge-add 语义):保留 existing 全量与顺序,追加
    additions 中缺失项(输入自身去重);无新增返回 None(调用方跳过写入)。

    Merge keyword lists (merge-add): keep all of `existing` in order,
    append items from `additions` not yet present (deduping the input);
    return None when nothing new needs writing.

    参数 / Parameters:
        existing (List[str]): 文件中已有关键字 / keywords already on file.
        additions (List[str]): 需确保存在的关键字 / keywords to ensure.

    返回 / Returns:
        Optional[List[str]]: 合并后完整列表;None=无需写入。
    """
    merged = list(existing)
    seen = set(existing)
    for kw in additions:
        kw = (kw or "").strip()
        if kw and kw not in seen:
            merged.append(kw)
            seen.add(kw)
    return merged if len(merged) != len(existing) else None
```

（文件已 `from typing import ... List, Optional ...`——不足则补。）

3b. `ExifToolManager` 内新增两个私有方法（放在 `_write_metadata_xmp_sidecar` 之前）：

```python
    def _read_subject_list(self, file_path: str) -> List[str]:
        """
        读取文件现有 XMP-dc:Subject 关键字列表(经常驻读进程)。
        文件不存在/无标签/读取失败均返回空列表。

        Read the current XMP-dc:Subject list via the resident read
        process; returns [] when missing/absent/unreadable.
        """
        if not file_path or not os.path.exists(file_path):
            return []
        try:
            data = self.read_metadata(file_path, extra_args=['-XMP-dc:Subject']) or {}
        except Exception:
            return []
        subj = data.get('Subject')
        if subj is None:
            return []
        return [subj] if isinstance(subj, str) else [str(s) for s in subj]

    def _keywords_args(self, item: Dict[str, any], read_target: str,
                       temp_files: List[str]) -> List[str]:
        """
        为 item['keywords'](若有)生成 merge-add 写入参数。

        读 read_target 现有关键字 → merge_keyword_lists 合并 → 无新增返回 [];
        有新增则把完整列表以 ";;" 连接写入 UTF-8 临时文件,返回
        ['-sep', ';;', '-XMP-dc:Subject<=<tmp>'](临时文件挂入 temp_files
        由调用方统一清理)。整表赋值经临时文件,符合中文 UTF-8 铁律;
        exiftool 不支持 '+=<file' 追加(2026-07-12 实验验证),故用
        读-合并-整表回写实现幂等追加。

        Build merge-add write args for item['keywords']: read the current
        list from read_target, merge, and when something new is needed
        write the full ";;"-joined list through a UTF-8 temp file
        (-sep ';;' -XMP-dc:Subject<=tmp). exiftool has no '+=<file'
        append form (verified 2026-07-12), hence read-merge-rewrite.
        """
        keywords = item.get('keywords')
        if not keywords:
            return []
        merged = merge_keyword_lists(self._read_subject_list(read_target), keywords)
        if merged is None:
            return []
        try:
            fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='sp_keywords_')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(';;'.join(merged))
            temp_files.append(tmp_path)
            return ['-sep', ';;', f'-XMP-dc:Subject<={tmp_path}']
        except Exception as e:
            print(f"⚠️ Keywords temp file failed: {e}, skip keywords write")
            return []
```

3c. 接线一：`_write_metadata_subprocess`（:593，ARW auto 路径在临时副本上调用它）——在 `title = item.get('title')` 之前加：

```python
        # 鸟名关键字(merge-add,Paul P1-1) / species keywords (merge-add)
        args.extend(self._keywords_args(item, file_path, temp_files))
```

注意：该方法中 `temp_files: List[str] = []` 的声明在 gbif 之后（:625 一带）——把 `temp_files` 声明**上移**到 `args = []` 之后，保证关键字参数可用。

3d. 接线二：`_write_metadata_xmp_sidecar`（:671）——同样把 `temp_files` 声明上移到 `args = []` 之后，然后在 Title 块之前加（侧车存在读侧车，否则读本体）：

```python
        # 鸟名关键字:侧车存在读侧车,否则读文件本体(LR 惯例侧车优先)
        # Species keywords: read the sidecar when present, else the file
        # itself (LR gives sidecars precedence for proprietary RAW).
        kw_read_target = xmp_path if os.path.exists(xmp_path) else file_path
        args.extend(self._keywords_args(item, kw_read_target, temp_files))
```

3e. 接线三：`batch_set_metadata` 非 ARW 常驻批量路径（:1055-1120 逐 item 构建 `args_list` 处）——在 Title 块之前加：

```python
            # 鸟名关键字(merge-add,Paul P1-1) / species keywords (merge-add)
            args_list.extend(self._keywords_args(item, file_path, caption_temp_files))
```

（复用该路径既有的 `caption_temp_files` 清理列表。）

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py -v
```

预期：2 passed（端到端用真实 exiftool + 常驻进程）。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile tools/exiftool_manager.py
git add tools/exiftool_manager.py && git add -f test_birdid_lr_keywords.py
git commit -m "feat(birdid): exiftool_manager 支持 keywords merge-add 写入(XMP-dc:Subject,三路)"
```

---

### Task 2: 开关字段 + 识鸟收尾接线

**Files:**
- Modify: `advanced_config.py`（DEFAULT_CONFIG birdid 段 + property/setter，仿 :753/:842 的 birdid_auto_identify 模式）
- Modify: `core/photo_processor.py`（识鸟收尾 meta_item :1300-1312）
- Test: `test_birdid_lr_keywords.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `keywords` 字段语义。
- Produces: `AdvancedConfig.birdid_write_keywords -> bool`（默认 True）、`set_birdid_write_keywords(value: bool)`（内部调 save，跟随 birdid_* 惯例）。

- [ ] **Step 1: 追加失败测试**

```python
def test_birdid_write_keywords_config_roundtrip():
    """
    开关默认 True;set 后落盘,重新加载读回 False。
    Default True; persists to disk and reloads as False after set.
    """
    from advanced_config import AdvancedConfig

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp)
        assert cfg.birdid_write_keywords is True          # 默认开 / default on
        cfg.set_birdid_write_keywords(False)
        assert AdvancedConfig(config_file=tmp).birdid_write_keywords is False
    finally:
        os.unlink(tmp)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py::test_birdid_write_keywords_config_roundtrip -v
```

预期：FAIL（`AttributeError: birdid_write_keywords`）。

- [ ] **Step 3: 实现 `advanced_config.py`**

3a. `DEFAULT_CONFIG` birdid 段（`"birdid_auto_identify"` 行附近）加：

```python
        "birdid_write_keywords": True,  # 识别后写鸟名到 XMP-dc:Subject(LR 关键字,Paul P1-1)
```

3b. property/setter（放在 birdid_auto_identify 的 property/setter 旁边）：

```python
    @property
    def birdid_write_keywords(self) -> bool:
        """
        识别成功后是否把鸟名写入照片关键字(XMP-dc:Subject,Lightroom Keywords)。

        返回:
        bool: 默认 True / Whether to write the species name into the photo's
        keywords (XMP-dc:Subject) after identification. Defaults to True.
        """
        return bool(self.config.get("birdid_write_keywords", True))

    def set_birdid_write_keywords(self, value: bool):
        """
        设置「识别后写入关键字」开关并保存。

        参数:
        value (bool): 是否写入关键字

        Set the write-keywords toggle and save.

        Parameters:
        value (bool): Whether to write species keywords.
        """
        self.config["birdid_write_keywords"] = bool(value)
        self.save()
```

- [ ] **Step 4: 接线 `core/photo_processor.py`（:1300-1312 高置信度写 Title 处）**

`meta_item` 构建后、`queue_metadata(meta_item)` 之前加：

```python
                        # 鸟名关键字(Paul P1-1):开关开启时随 Title 一起 merge-add
                        # 写入 XMP-dc:Subject(bird_title 已按界面语言选名)。
                        # Species keyword (Paul P1-1): when enabled, merge-add
                        # into XMP-dc:Subject alongside the Title write.
                        if self.config.birdid_write_keywords:
                            meta_item['keywords'] = [bird_title]
```

- [ ] **Step 5: 跑测试 + py_compile + 提交**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py -v
.venv/bin/python -m py_compile advanced_config.py core/photo_processor.py
git add advanced_config.py core/photo_processor.py && git add -f test_birdid_lr_keywords.py
git commit -m "feat(birdid): 识鸟收尾按开关写鸟名关键字(birdid_write_keywords,默认开)"
```

预期：3 passed。

---

### Task 3: 设置中心识鸟页开关 + i18n

**Files:**
- Modify: `ui/settings_center.py`（识鸟页自动识鸟区 :636 一带加复选框；`_save_birdid` :957 一带加保存）
- Modify: `locales/zh_CN.json` / `locales/en_US.json`（settings 段 `birdid_auto_label` 之后）
- Test: `test_birdid_lr_keywords.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `birdid_write_keywords` property / `set_birdid_write_keywords`。
- Produces: `SettingsCenter._bid_keywords: QCheckBox`。

- [ ] **Step 1: 追加失败测试**

```python
def test_settings_center_keywords_checkbox_saves():
    """
    识鸟页「写入关键字」复选框存在,取消勾选并保存后配置为 False。
    The Bird ID page checkbox exists and unchecking + save persists False.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    _orig = _ac_mod.get_advanced_config
    try:
        cfg = AdvancedConfig(config_file=tmp)
        _ac_mod.get_advanced_config = lambda: cfg   # settings_center 均为方法内局部 import
        w = SettingsCenter(get_i18n())
        w.show_page("birdid")
        assert w._bid_keywords.isChecked() is True   # 默认开 / default on
        w._bid_keywords.setChecked(False)
        w._save_birdid()
        assert cfg.birdid_write_keywords is False
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig
        os.unlink(tmp)
```

注意：`_save_birdid` 若实际方法名不同（grep `def _save_birdid` 确认；:957 上下文所在方法），按实际名调整测试与实现。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py::test_settings_center_keywords_checkbox_saves -v
```

预期：FAIL（`_bid_keywords` 不存在）。

- [ ] **Step 3: i18n 键（`birdid_auto_label` 行之后插入，两文件同位置）**

`locales/zh_CN.json`：

```json
    "birdid_keywords_label": "识别后写入照片关键字（Lightroom Keywords）",
```

`locales/en_US.json`：

```json
    "birdid_keywords_label": "Write species to photo keywords (Lightroom)",
```

（插入后 `python -c "import json; ..."` 校验两文件合法。）

- [ ] **Step 4: 实现 `ui/settings_center.py`**

4a. 识鸟页 `self._bid_auto` 之后（:638 `lay.addWidget(self._bid_auto)` 后）加：

```python
        # 写入关键字开关(Paul P1-1) / write-keywords toggle
        self._bid_keywords = QCheckBox(self.i18n.t("settings.birdid_keywords_label"))
        self._bid_keywords.setChecked(cfg.birdid_write_keywords)
        self._bid_keywords.setStyleSheet(self._checkbox_qss())
        lay.addWidget(self._bid_keywords)
```

4b. `_save_birdid`（:957 `cfg.set_birdid_auto_identify(...)` 之后）加：

```python
        # 写入关键字开关 / write-keywords toggle
        cfg.set_birdid_write_keywords(self._bid_keywords.isChecked())
```

- [ ] **Step 5: 跑全部测试 + py_compile + JSON 校验 + 提交**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py -v
.venv/bin/python -m py_compile ui/settings_center.py
.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]"
git add ui/settings_center.py locales/zh_CN.json locales/en_US.json && git add -f test_birdid_lr_keywords.py
git commit -m "feat(birdid): 设置中心识鸟页「写入关键字」开关(默认开)+i18n"
```

预期：4 passed。

---

### Task 4: 回归 + 收尾

**Files:**
- Modify: 无新改动（验证与文档）

- [ ] **Step 1: 相关测试全量回归**

```bash
.venv/bin/python -m pytest test_birdid_lr_keywords.py test_settings_center.py test_exiftool_set_metadata.py test_browser_p0_paul.py -v
```

预期：全部 PASS（`test_exiftool_set_metadata.py` 覆盖既有元数据写入不被 keywords 改动破坏）。

- [ ] **Step 2: 用户验收提示**

提醒用户：跑一次带识鸟的小目录，在 Lightroom 里确认关键字面板出现鸟名、
用户已有关键字未丢失；重跑同目录关键字不重复。ARW 验证 .xmp 侧车中
`<dc:subject>` 列表。

- [ ] **Step 3: 计划勾选 + 提交**

```bash
git add docs/plans/2026-07-12-birdid-lr-keywords.md
git commit -m "docs(birdid): LR 关键字计划执行完毕勾选"
```
