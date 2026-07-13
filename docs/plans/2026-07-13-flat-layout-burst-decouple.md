# 检测与整理解耦（平铺模式+连拍子目录开关）实施计划 / Flat Layout + Burst Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `folder_layout` 新增第三种 `"flat"`（识别评分照写、文件全部留原地，Lightroom 友好），并为普通布局提供「连拍归入独立子文件夹」独立开关（默认开=现状）。

**Architecture:** 平铺=把 CLI 验证过的 `organize_files=False` 链路暴露给 GUI：`core/folder_layout.py` 加 `LAYOUT_FLAT`，`ui/main_window.py` 两处 `organize_files=True` 改为按布局计算，输出页下拉加第三项；主流程视频自动处理在平铺下整体跳过（视频处理的落地产物就是移动+改名，不移动则无产出）。连拍开关=`advanced_config.burst_group_folders` 参与 `photo_processor.py:558` 的 burst 整理 gate。rating_mover 对根目录照片的天然跳过（:102/:259）用回归测试钉住。

**Tech Stack:** PySide6、advanced_config SSOT、pytest（offscreen）。

**Spec:** `docs/specs/2026-07-13-flat-layout-burst-decouple-design.md`（视频条款按本计划 Task 3 修正：平铺下整体跳过视频自动处理并写日志，而非"仅跳过移动"——组织器无 no-op 模式，分析结果除归类外无落地产物）。

## Global Constraints

- 平铺语义：所有照片（含无鸟/0星）留原地；EXIF 星级/关键字/精选旗标照写；不写 manifest。
- `burst_group_folders` 默认 `True`（现状）；关闭只影响文件归档位置，连拍检测/DB burst 列/连拍 3★ 封顶不受影响。
- setter 跟随 `set_folder_layout` 惯例**不内部 save**，由所在设置页的 `_save_*` 统一 `cfg.save()`。
- 新测试文件在仓库根目录，提交须 `git add -f`；注释中英双写；`py_compile` + pytest 全绿；提交 dev。

---

### Task 1: folder_layout 加 "flat" + rating_mover 回归钉

**Files:**
- Modify: `core/folder_layout.py`（:22-31 常量区、`compute_target_folder` :34 起）
- Test: `test_flat_layout_burst.py`（新建）

**Interfaces:**
- Consumes: 现有 `compute_target_folder(rating, bird_name, layout, other_birds_label) -> str`、`normalize_layout`、`VALID_LAYOUTS`。
- Produces: `LAYOUT_FLAT = "flat"`（模块常量，Task 3 的 main_window 导入使用）；`VALID_LAYOUTS` 含 flat；`compute_target_folder(..., layout="flat")` 返回 `""`。

- [ ] **Step 1: 写失败测试（新建 test_flat_layout_burst.py）**

```python
# -*- coding: utf-8 -*-
"""
检测与整理解耦测试:平铺布局(flat)+连拍子目录开关+rating_mover 根目录跳过回归钉。

Tests for detection/organization decoupling: the flat layout, the burst
subfolder toggle, and a regression pin for rating_mover's root-photo skip.
"""
import os
import tempfile


def test_flat_layout_constant_and_target_folder():
    """
    flat 进 VALID_LAYOUTS,normalize 合法,compute_target_folder 返回空(根目录)。
    "flat" is a valid layout; compute_target_folder returns "" (stay in root).
    """
    from core.folder_layout import (
        LAYOUT_FLAT, VALID_LAYOUTS, normalize_layout, compute_target_folder,
    )

    assert LAYOUT_FLAT == "flat"
    assert LAYOUT_FLAT in VALID_LAYOUTS
    assert normalize_layout("flat") == "flat"
    assert compute_target_folder(3, "白胸鸲鹟", LAYOUT_FLAT, "其他鸟类") == ""
    assert compute_target_folder(-1, None, LAYOUT_FLAT, "其他鸟类") == ""
    # 未知布局仍回退默认(不受 flat 影响) / unknown still falls back to default
    assert normalize_layout("nonsense") != "flat"


def test_rating_mover_skips_root_photos():
    """
    回归钉:根目录照片改星不移动文件(平铺模式下浏览器改星安全的依据)。
    Regression pin: rating changes on root photos never move files — the
    guarantee that makes browser edits safe under the flat layout.
    """
    from core.rating_mover import move_photo_on_metadata_change

    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "DSC0001.NEF")
        open(raw, "wb").write(b"fake")
        photo = {"filename": "DSC0001", "current_path": "DSC0001.NEF"}
        move_photo_on_metadata_change(td, photo, new_rating=3, i18n=None, db=None)
        # 文件仍在根目录 / file stays in the root
        assert os.path.exists(raw)
        assert sorted(os.listdir(td)) == ["DSC0001.NEF"]
```

注意：`move_photo_on_metadata_change` 的实际形参名以 `core/rating_mover.py:62` 定义为准（写测试前先读签名，i18n/db 若为必填则传最小桩）。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/jameszhenyu/Documents/JamesAPPS/SuperPicky2026
.venv/bin/python -m pytest test_flat_layout_burst.py -v
```

预期：第一个 FAIL（`ImportError: LAYOUT_FLAT`）；第二个视签名可能需按 Step 1 注意事项修正后再跑。

- [ ] **Step 3: 实现 `core/folder_layout.py`**

3a. 常量区（:22-31）：

```python
# 布局策略 / Layout strategy identifiers
LAYOUT_RATING_FIRST = "rating-first"
LAYOUT_SPECIES_FIRST = "species-first"
# V4.6(Paul P1): 平铺——识别评分但不移动文件(Lightroom 友好);
# organize 阶段整体跳过,此常量供 GUI 判断 organize_files 传值。
# V4.6 (Paul P1): flat — rate in place, no file moves (Lightroom-friendly).
# The organize stage is skipped entirely; GUI uses this to derive
# the organize_files argument.
LAYOUT_FLAT = "flat"
# V4.3 Phase 4: 默认改 species-first 以兼容视频集成
# 视频不分星，species-first 让视频和照片自然共享同一个鸟种目录
# V4.3 Phase 4: default switched to species-first so videos (no star rating)
# can naturally live alongside photos in the same species folder.
DEFAULT_LAYOUT = LAYOUT_SPECIES_FIRST

VALID_LAYOUTS = {LAYOUT_RATING_FIRST, LAYOUT_SPECIES_FIRST, LAYOUT_FLAT}
```

3b. `compute_target_folder` 函数体开头（docstring 之后第一行）加：

```python
    # 平铺:不分目录,留在根(防御性——organize gate 下正常不会走到这里)
    # Flat: no subfolders, stay in root (defensive; the organize gate
    # normally prevents this from being called at all).
    if layout == LAYOUT_FLAT:
        return ""
```

（docstring 的 Args/Returns 补一行 flat 说明。）

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py -v
```

预期：2 passed。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile core/folder_layout.py
git add core/folder_layout.py && git add -f test_flat_layout_burst.py
git commit -m "feat(organize): folder_layout 新增 flat 平铺布局 + rating_mover 根目录跳过回归钉"
```

---

### Task 2: burst_group_folders 开关 + photo_processor gate

**Files:**
- Modify: `advanced_config.py`（DEFAULT_CONFIG + property/setter，放 folder_layout 旁）
- Modify: `core/photo_processor.py`（:558 burst 整理 gate）
- Test: `test_flat_layout_burst.py`（追加）

**Interfaces:**
- Consumes: 无（独立于 Task 1）。
- Produces: `AdvancedConfig.burst_group_folders -> bool`（默认 True）、`set_burst_group_folders(value: bool)`（不内部 save，跟随 set_folder_layout 惯例）。

- [ ] **Step 1: 追加失败测试**

```python
def test_burst_group_folders_config_roundtrip():
    """
    开关默认 True;set False 后经 save 落盘,重载读回 False。
    Defaults to True; persists False after set + save.
    """
    from advanced_config import AdvancedConfig

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        cfg = AdvancedConfig(config_file=tmp)
        assert cfg.burst_group_folders is True
        cfg.set_burst_group_folders(False)
        cfg.save()   # setter 不内部 save(跟随 set_folder_layout 惯例)
        assert AdvancedConfig(config_file=tmp).burst_group_folders is False
    finally:
        os.unlink(tmp)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py::test_burst_group_folders_config_roundtrip -v
```

预期：FAIL（AttributeError）。

- [ ] **Step 3: 实现 `advanced_config.py`**

3a. `DEFAULT_CONFIG`（`"folder_layout": "species-first"` 行 :88 附近）加：

```python
        "burst_group_folders": True,  # 连拍归入 burst_NNN 子目录(关=按星级/鸟种常规归档,Paul P1)
```

3b. `set_folder_layout`（:454）之后加：

```python
    @property
    def burst_group_folders(self) -> bool:
        """
        连拍照片是否归入独立 burst_NNN 子目录(默认 True=现状)。
        关闭后连拍照片按各自星级/鸟种走常规归档;连拍检测、DB burst 列、
        评分阶段连拍 3★ 封顶均不受影响。

        Whether burst groups get their own burst_NNN subfolder (default
        True). When off, burst shots are filed like normal photos; burst
        detection, DB columns and the 3-star burst cap are unaffected.
        """
        return bool(self.config.get("burst_group_folders", True))

    def set_burst_group_folders(self, value: bool) -> None:
        """
        设置「连拍归入独立子文件夹」开关(不内部 save,由设置页统一保存)。

        参数:
        value (bool): 是否分子文件夹

        Set the burst-subfolder toggle (no internal save; the settings
        page persists via its own cfg.save()).

        Parameters:
        value (bool): Whether to group bursts into subfolders.
        """
        self.config["burst_group_folders"] = bool(value)
```

- [ ] **Step 4: 实现 `core/photo_processor.py` gate（:558）**

原：

```python
            if self.settings.detect_burst and self.burst_map and organize_files:
```

改为：

```python
            # V4.6(Paul P1): burst_group_folders 关闭时连拍不聚子目录,
            # 照片按星级/鸟种常规归档(检测与整理解耦)。
            # V4.6 (Paul P1): with burst_group_folders off, burst shots are
            # filed normally instead of into burst_NNN subfolders.
            if (self.settings.detect_burst and self.burst_map and organize_files
                    and self.config.burst_group_folders):
```

- [ ] **Step 5: 跑测试 + py_compile + 提交**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py -v
.venv/bin/python -m py_compile advanced_config.py core/photo_processor.py
git add advanced_config.py core/photo_processor.py && git add -f test_flat_layout_burst.py
git commit -m "feat(organize): burst_group_folders 开关——连拍检测与子目录归档解耦"
```

预期：3 passed。

---

### Task 3: GUI 接线——organize_files 按布局 + 平铺跳过视频归类

**Files:**
- Modify: `ui/main_window.py`（:519、:607 两处 `organize_files=True`；`_process_videos` :185 起的 gate 区 :212 附近）
- Test: `test_flat_layout_burst.py`（追加轻量源检查测试）

**Interfaces:**
- Consumes: Task 1 的 `LAYOUT_FLAT`。
- Produces: 无新接口（行为变更）。

- [ ] **Step 1: 追加失败测试（源检查——两处调用点不再写死 True）**

```python
def test_main_window_organize_files_follows_layout():
    """
    main_window 不再写死 organize_files=True,而是按 folder_layout 计算
    (源检查:offscreen 全构造跑批不可行,以调用点文本断言钉住接线)。

    main_window must derive organize_files from folder_layout instead of
    hard-coding True (source-level pin; a full offscreen batch run is not
    feasible in unit tests).
    """
    import io
    src = io.open("ui/main_window.py", encoding="utf-8").read()
    assert "organize_files=True" not in src, "organize_files 仍有写死 True 的调用点"
    assert src.count("organize_files=_organize_enabled") == 2, \
        "两处 processor.process 调用点都应使用 _organize_enabled"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py::test_main_window_organize_files_follows_layout -v
```

预期：FAIL（存在 organize_files=True）。

- [ ] **Step 3: 实现 `ui/main_window.py` 两处照片跑批调用点**

两处（:519 与 :607 各自的 `processor.process(` 之前）——每处在既有
`adv_config = get_advanced_config()`（第一处 :515 已有；第二处若无则同样取一次）之后加：

```python
            # V4.6(Paul P1): 平铺布局 → 识别评分但不移动文件(Lightroom 友好)
            # V4.6 (Paul P1): flat layout — rate in place, no file moves.
            from core.folder_layout import LAYOUT_FLAT
            _organize_enabled = adv_config.folder_layout != LAYOUT_FLAT
```

调用参数改为：

```python
                    organize_files=_organize_enabled,
```

（两处都改；第二处上下文变量名以实际为准——若该作用域用 `cfg`/`adv_config` 命名不同，跟随现名。）

- [ ] **Step 4: 实现 `_process_videos` 平铺跳过（:212 `if not cfg.video_auto_process_in_main:` gate 之后）**

```python
        # V4.6(Paul P1): 平铺布局下跳过视频自动归类——视频处理的落地产物
        # 就是移动+改名(组织器无 no-op 模式),不移动则无产出,整体跳过并留日志。
        # V4.6 (Paul P1): under the flat layout skip video auto-organize —
        # its only durable output is the move+rename, so skip entirely.
        from core.folder_layout import LAYOUT_FLAT
        if cfg.folder_layout == LAYOUT_FLAT:
            self.signals.log.emit(
                get_i18n().t("logs.video_skip_flat"), "info")
            return
```

（`get_i18n` 若该作用域未导入则局部导入；i18n 键在 Step 5 添加。）

- [ ] **Step 5: i18n 键（logs 段，两语言包）**

`locales/zh_CN.json`（logs 段）：

```json
    "video_skip_flat": "📁 平铺模式：跳过视频自动归类（文件不移动）",
```

`locales/en_US.json`：

```json
    "video_skip_flat": "📁 Flat layout: skipping video auto-organize (files stay in place)",
```

- [ ] **Step 6: 跑测试 + py_compile + JSON 校验 + 提交**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py -v
.venv/bin/python -m py_compile ui/main_window.py
.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]"
git add ui/main_window.py locales/zh_CN.json locales/en_US.json && git add -f test_flat_layout_burst.py
git commit -m "feat(organize): GUI 接线平铺布局——organize_files 按布局计算,平铺跳过视频归类"
```

预期：4 passed。

---

### Task 4: 设置中心 UI——输出页第三项 + 精选页连拍复选框 + i18n

**Files:**
- Modify: `ui/settings_center.py`（输出页 combo :1200-1209、`_save_output` :1374 附近；精选页检测开关区连拍检测 `self._cull_burst` 之后、`_save_culling` :985 附近）
- Modify: `locales/zh_CN.json` / `locales/en_US.json`
- Test: `test_flat_layout_burst.py`（追加）

**Interfaces:**
- Consumes: Task 1 `LAYOUT_FLAT`、Task 2 `burst_group_folders`/`set_burst_group_folders`。
- Produces: `SettingsCenter._cull_burst_folders: QCheckBox`；输出页下拉含 data=`"flat"` 的第三项。

- [ ] **Step 1: 追加失败测试**

```python
def test_settings_center_flat_option_and_burst_checkbox():
    """
    输出页布局下拉含 flat 第三项且保存往返;精选页连拍子目录复选框保存往返。
    Output page combo carries the flat option and round-trips; the culling
    page burst-subfolder checkbox round-trips too.
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
        _ac_mod.get_advanced_config = lambda: cfg
        w = SettingsCenter(get_i18n())

        # 输出页:第三项 flat / output page: third option is flat
        idx = w._folder_layout_combo.findData("flat")
        assert idx >= 0, "combo 缺 flat 项"
        w._folder_layout_combo.setCurrentIndex(idx)
        w._save_output()
        assert cfg.folder_layout == "flat"

        # 精选页:连拍子目录复选框 / culling page: burst-subfolder checkbox
        assert w._cull_burst_folders.isChecked() is True   # 默认开
        w._cull_burst_folders.setChecked(False)
        w._save_culling()
        assert cfg.burst_group_folders is False
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig
        os.unlink(tmp)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py::test_settings_center_flat_option_and_burst_checkbox -v
```

预期：FAIL（combo 无 flat 项）。

- [ ] **Step 3: i18n 键**

`locales/zh_CN.json` `advanced_settings` 段（`folder_layout_species_first` :711 之后）：

```json
    "folder_layout_flat": "平铺——识别评分但不移动文件（Lightroom 友好）",
```

同段 `settings`（`culling_burst_fps_label` 附近）：

```json
    "culling_burst_folders_label": "连拍归入独立子文件夹",
```

`locales/en_US.json` 同位置：

```json
    "folder_layout_flat": "Flat — rate in place, no file moves (Lightroom-friendly)",
```

```json
    "culling_burst_folders_label": "Group bursts into subfolders",
```

- [ ] **Step 4: 实现 `ui/settings_center.py`**

4a. 输出页 combo（:1205 `species-first` addItem 之后）：

```python
        self._folder_layout_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_flat"), "flat"
        )
```

（`_save_output` :1374 已用 `currentData()` 写回 `set_folder_layout`——Task 1 已把 flat 加入 VALID_LAYOUTS，无需再改。）

4b. 精选页检测开关区，连拍检测 `self._cull_burst` 构建之后（连拍速度行之前）加：

```python
        # 连拍子目录开关(Paul P1) / burst-subfolder toggle
        self._cull_burst_folders = QCheckBox(self.i18n.t("settings.culling_burst_folders_label"))
        self._cull_burst_folders.setChecked(cfg.burst_group_folders)
        self._cull_burst_folders.setStyleSheet(
            checkbox_indicator_qss(15, COLORS['text_muted'], COLORS['accent'])
        )
        lay.addWidget(self._cull_burst_folders)
```

（样式写法跟随该区 `self._cull_burst` 的现有写法——若其用别的辅助函数，保持一致。）

4c. `_save_culling`（`cfg.set_burst_check(...)` 之后、`cfg.save()` 之前）加：

```python
        cfg.set_burst_group_folders(self._cull_burst_folders.isChecked())
```

- [ ] **Step 5: 跑全部测试 + py_compile + JSON 校验 + 提交**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py test_settings_center.py -v
.venv/bin/python -m py_compile ui/settings_center.py
.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]"
git add ui/settings_center.py locales/zh_CN.json locales/en_US.json && git add -f test_flat_layout_burst.py
git commit -m "feat(organize): 设置中心——输出页平铺布局第三项 + 精选页连拍子目录开关"
```

预期：全绿。

---

### Task 5: 回归 + 收尾

**Files:**
- Modify: `docs/specs/2026-07-13-flat-layout-burst-decouple-design.md`（视频条款按 Task 3 实现修正）

- [ ] **Step 1: 相关测试全量回归**

```bash
.venv/bin/python -m pytest test_flat_layout_burst.py test_settings_center.py test_rating_mover.py test_rating_quota.py test_browser_p0_paul.py -v
```

预期：全部 PASS。

- [ ] **Step 2: spec 视频条款修正**

spec「A.5 主界面视频自动归类」改为：平铺模式下整体跳过视频自动处理并写日志
`logs.video_skip_flat`（组织器无 no-op 模式，分析结果除归类外无落地产物）。

- [ ] **Step 3: 用户验收提示**

提醒用户实测：①设 flat 跑一个小目录——文件全留原地、EXIF/侧车照写、结果
浏览器筛选/改星正常且不移动文件；②普通布局关掉「连拍归入独立子文件夹」
跑连拍目录——无 burst_NNN 子目录、连拍照片按星级归档、浏览器连拍角标仍在。

- [ ] **Step 4: 提交收尾**

```bash
git add docs/specs/2026-07-13-flat-layout-burst-decouple-design.md docs/plans/2026-07-13-flat-layout-burst-decouple.md
git commit -m "docs(organize): 平铺+连拍解耦计划勾选与 spec 视频条款修正"
```
