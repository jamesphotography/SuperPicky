# 设置中心(Settings Center)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把分散的设置/菜单收敛为单一「设置中心」(左侧分类导航 + 右侧内容),技能等级与阈值协同、识鸟设置三处合一、数据统一到 `advanced_config`。

**Architecture:** 新建 `ui/settings_center.py` 承载 6 个分类页(左 `QListWidget` + 右 `QStackedWidget`);迁移并复用现有 `advanced_settings_dialog` 的 5 个 page 构建逻辑;`advanced_config` 作为单一事实源,补识鸟字段并一次性迁移 `birdid_dock_settings.json`;主窗口删参数面板、菜单收敛为单一「设置」入口;识鸟面板瘦身只留运行时操作。

**Tech Stack:** Python 3.x、PySide6(Qt6)、pytest(headless 用 `QT_QPA_PLATFORM=offscreen`)。

## Global Constraints

- UTF-8 安全;不得引入中文乱码;识鸟国家/区域含中文,读写与迁移须 UTF-8。
- 跨平台(Windows + macOS);路径用 `pathlib`/`os.path`,不用平台特有命令。
- 图标统一用 SVG(`ui.icon_utils.load_tinted_icon`),不使用 emoji。
- 每个改动的 Python 文件实施后跑 `.venv/bin/python -m py_compile`;locale 改动后校验 `json.load`。
- 设置项语义/默认值保持现状,仅迁移入口与存储(技能等级整合、识鸟合并、关于合并除外)。
- 提交信息中文,结尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 数据迁移须幂等:重复运行不重复搬运、不覆盖已有新值。

**现有 `advanced_config` 已有字段(直接复用,勿重复新增):** `min_confidence, min_sharpness, min_nima, flight_check, burst_check, exposure_check, burst_fps, birdid_confidence, skill_level, custom_sharpness, custom_aesthetics, folder_layout, external_apps, name_format, arw_write_mode, metadata_write_mode, video_max_frames, video_yolo_threshold, video_min_segment_frames, video_auto_process_in_main, video_species_mode, video_enable_species_id, video_enable_flight`。

**`birdid_dock_settings.json` 现有键(迁移来源):** `use_ebird, country_code, selected_country, region_code, selected_region`。

**已核准的现网 API(权威,覆盖正文任何不一致;测试/代码以此为准):**
- i18n 入口:`from tools.i18n import get_i18n`
- 主窗口类:`ui.main_window.SuperPickyMainWindow`(构造 `SuperPickyMainWindow()` 无参,headless 可构造)
- 识鸟面板类:`ui.birdid_dock.BirdIDDockWidget`(构造 `BirdIDDockWidget(parent=None)`)
- 版本常量:`from constants import APP_VERSION`(当前 "4.3.1RC1")
- 技能等级组件:`ui.skill_level_dialog.SkillLevelCard` / `SkillLevelSelector`;阈值换算 `core.skill_presets.SKILL_PRESETS` / `get_skill_level_thresholds`
- 应用配置目录:`from config import get_app_config_dir`
- headless 测试约定:`import os; os.environ.setdefault("QT_QPA_PLATFORM","offscreen")` 后 `QApplication.instance() or QApplication([])`(参考现有 `test_crop_studio.py`)

---

### Task 1: advanced_config 补识鸟字段 + 迁移 birdid_dock_settings.json

**Files:**
- Modify: `advanced_config.py`(`DEFAULT_CONFIG` 字典 20-159;尾部加属性/setter/迁移方法)
- Test: `test_advanced_config_birdid_migration.py`(项目根,与现有 `test_*.py` 同级)

**Interfaces:**
- Produces:
  - 新增 config 键与默认值:`"birdid_auto_identify": False`、`"birdid_use_ebird": True`、`"birdid_country_code": None`、`"birdid_selected_country": "自动检测 (GPS)"`、`"birdid_region_code": None`、`"birdid_selected_region": "整个国家"`
  - 属性 getter:`birdid_auto_identify`、`birdid_use_ebird`、`birdid_country_code`、`birdid_selected_country`、`birdid_region_code`、`birdid_selected_region`(均 `@property`)
  - setter:`set_birdid_auto_identify(bool)`、`set_birdid_region(use_ebird: bool, country_code: Optional[str], selected_country: str, region_code: Optional[str], selected_region: str)`(一次性写入并 `save()`)
  - `migrate_birdid_dock_settings() -> bool`:若 `birdid_dock_settings.json` 存在且本 config 的 `birdid_selected_country` 仍为默认值,则把旧文件的 5 个键搬入对应新字段并 `save()`,返回 `True`;否则不动返回 `False`。幂等。旧文件不删。

- [ ] **Step 1: 写失败测试**

```python
# test_advanced_config_birdid_migration.py
import json, os, tempfile
from advanced_config import AdvancedConfig

def _cfg(tmp): return AdvancedConfig(config_file=os.path.join(tmp, "advanced_config.json"))

def test_defaults_present():
    with tempfile.TemporaryDirectory() as tmp:
        c = _cfg(tmp)
        assert c.birdid_auto_identify is False
        assert c.birdid_use_ebird is True
        assert c.birdid_selected_country == "自动检测 (GPS)"

def test_migration_moves_legacy_chinese_region(tmp_path):
    legacy = tmp_path / "birdid_dock_settings.json"
    legacy.write_text(json.dumps({
        "use_ebird": False, "country_code": "AU", "selected_country": "澳大利亚",
        "region_code": "AU-QLD", "selected_region": "昆士兰"
    }, ensure_ascii=False), encoding="utf-8")
    c = AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))
    moved = c.migrate_birdid_dock_settings(legacy_path=str(legacy))
    assert moved is True
    assert c.birdid_selected_country == "澳大利亚"
    assert c.birdid_region_code == "AU-QLD"
    assert c.birdid_use_ebird is False
    # 幂等:再次迁移不覆盖
    assert c.migrate_birdid_dock_settings(legacy_path=str(legacy)) is False
    assert c.birdid_selected_country == "澳大利亚"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_advanced_config_birdid_migration.py -q`
Expected: FAIL(`AttributeError: 'AdvancedConfig' object has no attribute 'birdid_auto_identify'`)

- [ ] **Step 3: 在 DEFAULT_CONFIG 末尾(159 行 `}` 前)加字段**

```python
        # V4.4: 识鸟设置统一进 advanced_config(原 birdid_dock_settings.json)
        "birdid_auto_identify": False,
        "birdid_use_ebird": True,
        "birdid_country_code": None,
        "birdid_selected_country": "自动检测 (GPS)",
        "birdid_region_code": None,
        "birdid_selected_region": "整个国家",
```

- [ ] **Step 4: 在文件尾部加属性、setter 与迁移方法**

```python
    @property
    def birdid_auto_identify(self) -> bool:
        return bool(self.config.get("birdid_auto_identify", False))

    @property
    def birdid_use_ebird(self) -> bool:
        return bool(self.config.get("birdid_use_ebird", True))

    @property
    def birdid_country_code(self):
        return self.config.get("birdid_country_code")

    @property
    def birdid_selected_country(self) -> str:
        return self.config.get("birdid_selected_country", "自动检测 (GPS)")

    @property
    def birdid_region_code(self):
        return self.config.get("birdid_region_code")

    @property
    def birdid_selected_region(self) -> str:
        return self.config.get("birdid_selected_region", "整个国家")

    def set_birdid_auto_identify(self, value: bool):
        self.config["birdid_auto_identify"] = bool(value)
        self.save()

    def set_birdid_region(self, use_ebird, country_code, selected_country,
                          region_code, selected_region):
        self.config["birdid_use_ebird"] = bool(use_ebird)
        self.config["birdid_country_code"] = country_code
        self.config["birdid_selected_country"] = selected_country
        self.config["birdid_region_code"] = region_code
        self.config["birdid_selected_region"] = selected_region
        self.save()

    def migrate_birdid_dock_settings(self, legacy_path: str = None) -> bool:
        """一次性把旧 birdid_dock_settings.json 搬入 advanced_config。幂等;旧文件保留。"""
        import json, os
        from config import get_app_config_dir
        if legacy_path is None:
            legacy_path = os.path.join(str(get_app_config_dir()), "birdid_dock_settings.json")
        # 已迁移过(country 非默认)则跳过 / skip if already customized
        if self.config.get("birdid_selected_country", "自动检测 (GPS)") != "自动检测 (GPS)":
            return False
        if not os.path.exists(legacy_path):
            return False
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            return False
        self.config["birdid_use_ebird"] = bool(old.get("use_ebird", True))
        self.config["birdid_country_code"] = old.get("country_code")
        self.config["birdid_selected_country"] = old.get("selected_country", "自动检测 (GPS)")
        self.config["birdid_region_code"] = old.get("region_code")
        self.config["birdid_selected_region"] = old.get("selected_region", "整个国家")
        self.save()
        return True
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_advanced_config_birdid_migration.py -q`
Expected: PASS(2 passed)

- [ ] **Step 6: py_compile + 提交**

```bash
.venv/bin/python -m py_compile advanced_config.py
git add advanced_config.py test_advanced_config_birdid_migration.py
git commit -m "feat(settings): advanced_config 补识鸟字段+迁移 birdid_dock_settings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: SettingsCenter 骨架(左导航 + stacked + SVG 图标)

**Files:**
- Create: `ui/settings_center.py`
- Test: `test_settings_center.py`

**Interfaces:**
- Consumes: `ui.icon_utils.load_tinted_icon, ICON_IDLE, ICON_ACTIVE`;`ui.styles.COLORS`。
- Produces:
  - `class SettingsCenter(QDialog)`,构造签名 `__init__(self, i18n, parent=None, start_page: str = "culling")`
  - 页 key 常量顺序:`PAGE_ORDER = ["culling", "birdid", "output", "video", "apps", "about"]`
  - `self._nav: QListWidget`(每分类一项,含 SVG 图标)、`self._stack: QStackedWidget`
  - `show_page(self, key: str)`:切到指定页(供外部 header chip / 识鸟面板跳转用)
  - 占位页方法 `_placeholder(title) -> QWidget`(后续 Task 3-6 替换为真实页)

- [ ] **Step 1: 写失败测试**

```python
# test_settings_center.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])

def test_nav_has_six_pages_and_switch():
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    w = SettingsCenter(get_i18n())
    assert PAGE_ORDER == ["culling", "birdid", "output", "video", "apps", "about"]
    assert w._nav.count() == 6
    w.show_page("about")
    assert w._stack.currentIndex() == PAGE_ORDER.index("about")
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: FAIL(`ModuleNotFoundError: ui.settings_center`)

- [ ] **Step 3: 实现骨架**

```python
# ui/settings_center.py
# -*- coding: utf-8 -*-
"""统一设置中心:左侧分类导航 + 右侧内容页。取代旧高级设置/技能等级/关于弹窗入口。"""
from __future__ import annotations
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QWidget, QLabel, QPushButton,
)
from ui.styles import COLORS
from ui.icon_utils import load_tinted_icon, ICON_IDLE, ICON_ACTIVE

PAGE_ORDER = ["culling", "birdid", "output", "video", "apps", "about"]
_PAGE_ICON = {
    "culling": "gem.svg", "birdid": "bird.svg", "output": "download.svg",
    "video": "video.svg", "apps": "layout-grid.svg", "about": "info.svg",
}
_PAGE_TITLE_KEY = {
    "culling": "settings.nav_culling", "birdid": "settings.nav_birdid",
    "output": "settings.nav_output", "video": "settings.nav_video",
    "apps": "settings.nav_apps", "about": "settings.nav_about",
}


class SettingsCenter(QDialog):
    def __init__(self, i18n, parent=None, start_page: str = "culling"):
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.t("settings.window_title"))
        self.resize(760, 560)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav = QListWidget()
        self._nav.setFixedWidth(160)
        for key in PAGE_ORDER:
            item = QListWidgetItem(load_tinted_icon(_PAGE_ICON[key], ICON_IDLE, 18),
                                   "  " + i18n.t(_PAGE_TITLE_KEY[key]))
            item.setData(Qt.UserRole, key)
            self._nav.addItem(item)
        root.addWidget(self._nav)

        right = QVBoxLayout()
        self._stack = QStackedWidget()
        for key in PAGE_ORDER:
            self._stack.addWidget(self._build_page(key))
        right.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        done = QPushButton(i18n.t("settings.done"))
        done.clicked.connect(self.accept)
        footer.addWidget(done)
        right.addLayout(footer)
        root.addLayout(right, 1)

        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self.show_page(start_page)

    def _build_page(self, key: str) -> QWidget:
        # Task 3-6 用真实页替换各分支;此处先占位
        return self._placeholder(self.i18n.t(_PAGE_TITLE_KEY[key]))

    def _placeholder(self, title: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(title))
        lay.addStretch(1)
        return page

    def show_page(self, key: str):
        if key in PAGE_ORDER:
            self._nav.setCurrentRow(PAGE_ORDER.index(key))
```

- [ ] **Step 4: 加 locale 键(导航/标题/按钮)**

在 `locales/zh_CN.json` 与 `locales/en_US.json` 的合适命名空间加 `settings` 段:

```json
"settings": {
  "window_title": "设置",
  "done": "完成",
  "nav_culling": "精选", "nav_birdid": "识鸟", "nav_output": "输出",
  "nav_video": "视频", "nav_apps": "外部应用", "nav_about": "关于"
}
```

英文对应:`"Settings" / "Done" / "Culling" / "Bird ID" / "Output" / "Video" / "External Apps" / "About"`。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: PASS

- [ ] **Step 6: py_compile + JSON 校验 + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
.venv/bin/python -c "import json;[json.load(open(f,encoding='utf-8')) for f in ('locales/zh_CN.json','locales/en_US.json')]"
git add ui/settings_center.py test_settings_center.py locales/zh_CN.json locales/en_US.json
git commit -m "feat(settings): 设置中心骨架(左导航+stacked+SVG图标)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 精选页 + 技能等级↔阈值协同

**Files:**
- Modify: `ui/settings_center.py`(新增 `_build_culling_page`,替换 `_build_page` 的 `culling` 分支)
- Test: `test_settings_center.py`(追加协同逻辑测试)

**Interfaces:**
- Consumes: `core.skill_presets.SKILL_PRESETS, get_skill_level_thresholds`;`advanced_config.get_advanced_config`。
- Produces:
  - 精选页含:技能等级单选(含"自定义")、`self._cull_ai`(QSlider 0-100)、`self._cull_sharp`(QSlider 200-600)、`self._cull_nima`(QSlider 0-100,值/10=NIMA)、`self._cull_flight`/`self._cull_burst`(QCheckBox)、`self._cull_burst_fps`(QSpinBox)
  - `_on_skill_preset_selected(level_key: str)`:用 `get_skill_level_thresholds(level_key)` 填 sharp/nima/ai 滑块(填充时设 `self._suppress=True` 避免回环)
  - `_on_cull_threshold_changed()`:任一阈值被用户改 → 技能等级单选切到"自定义"(`self._suppress` 为真时跳过)
  - `_save_culling()`:写回 `advanced_config`(`set_min_confidence/set_min_sharpness/set_min_nima` + `flight_check/burst_check/burst_fps/skill_level`),`accept()` 时调用

- [ ] **Step 1: 写失败测试(协同逻辑)**

```python
def test_skill_preset_fills_thresholds_and_manual_edit_switches_custom():
    from ui.settings_center import SettingsCenter
    from core.skill_presets import get_skill_level_thresholds
    w = SettingsCenter(get_i18n())
    w.show_page("culling")
    # 选某预设档 → 阈值被自动填为该档值
    th = get_skill_level_thresholds("advanced")
    w._on_skill_preset_selected("advanced")
    assert w._cull_sharp.value() == int(th["sharpness"])
    # 手动改阈值 → 档位切到自定义
    w._cull_sharp.setValue(w._cull_sharp.value() + 30)
    assert w._current_skill_key == "custom"
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_settings_center.py::test_skill_preset_fills_thresholds_and_manual_edit_switches_custom -q`
Expected: FAIL(`AttributeError: _on_skill_preset_selected`)

- [ ] **Step 3: 实现精选页**

实现要点(在 `ui/settings_center.py` 新增方法并把 `_build_page` 的 `culling` 分支指向它):
- 顶部技能等级:复用 `core.skill_presets.SKILL_PRESETS` 渲染单选(可复用 `ui.skill_level_dialog` 的 `SkillLevelCard`/`SkillLevelSelector`,若耦合过重则用 `QRadioButton` 行)。维护 `self._current_skill_key`。
- 三个阈值滑块 + 两个检测开关 + 连拍 fps `QSpinBox`,初值读 `get_advanced_config()` 现值。
- `self._suppress = False` 守卫:`_on_skill_preset_selected` 内置 True 再填值再置 False;阈值 `valueChanged` 接 `_on_cull_threshold_changed`,内部 `if self._suppress: return`,否则 `self._current_skill_key="custom"` 并刷新单选选中。
- `get_skill_level_thresholds(level_key)` 返回含 `sharpness`/`aesthetics`(NIMA)键(见 `core/skill_presets.py`);AI 置信度若该档未定义则保持当前值。

完整守卫骨架:

```python
    def _on_skill_preset_selected(self, level_key: str):
        from core.skill_presets import get_skill_level_thresholds
        self._current_skill_key = level_key
        if level_key == "custom":
            return
        th = get_skill_level_thresholds(level_key)
        self._suppress = True
        try:
            self._cull_sharp.setValue(int(th["sharpness"]))
            self._cull_nima.setValue(int(round(th["aesthetics"] * 10)))
        finally:
            self._suppress = False

    def _on_cull_threshold_changed(self, *_):
        if getattr(self, "_suppress", False):
            return
        self._current_skill_key = "custom"
        self._select_skill_radio("custom")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: PASS

- [ ] **Step 5: 加技能等级相关 locale(若复用 skill_level 文案则沿用其键)+ py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
git add ui/settings_center.py test_settings_center.py locales/zh_CN.json locales/en_US.json
git commit -m "feat(settings): 精选页+技能等级与阈值协同

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 识鸟页(三处合一)

**Files:**
- Modify: `ui/settings_center.py`(`_build_birdid_page`)
- Test: `test_settings_center.py`(追加)

**Interfaces:**
- Consumes: `advanced_config`(Task 1 新字段);识鸟国家/区域数据源沿用 `ui/birdid_dock.py` 的 `_load_regions_data`(可抽到 `core` 或直接复用静态 json 路径 `birdid/data` 下区域表)。
- Produces:
  - `self._bid_auto`(QCheckBox 自动识鸟)、`self._bid_conf`(QSlider/QDoubleSpinBox 置信度)、`self._bid_ebird`/`self._bid_gbif`(数据源单选)、`self._bid_country`(QComboBox)、`self._bid_region`(QComboBox)
  - `_save_birdid()`:`set_birdid_auto_identify` + `set_birdid_confidence` + `set_birdid_region(...)`

- [ ] **Step 1: 写失败测试**

```python
def test_birdid_page_reads_and_writes_config(tmp_path, monkeypatch):
    from ui.settings_center import SettingsCenter
    from advanced_config import get_advanced_config
    w = SettingsCenter(get_i18n()); w.show_page("birdid")
    w._bid_auto.setChecked(True)
    w._save_birdid()
    assert get_advanced_config().birdid_auto_identify is True
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_settings_center.py::test_birdid_page_reads_and_writes_config -q`
Expected: FAIL(`AttributeError: _bid_auto`)

- [ ] **Step 3: 实现识鸟页**

要点:国家/区域下拉数据从 `birdid_dock` 现有 `_load_regions_data()` 逻辑迁移(把该方法抽成 `core` 级函数 `load_regions_data()` 复用,避免与 dock 重复);初值读 `advanced_config` 的 `birdid_*` 字段;`_save_birdid()` 写回。自动识鸟开关读写 `birdid_auto_identify`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: PASS

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
git add ui/settings_center.py test_settings_center.py locales/zh_CN.json locales/en_US.json
git commit -m "feat(settings): 识鸟页三处合一(开关/置信度/国家区域)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 输出页 + 视频页 + 外部应用页(迁移现有)

**Files:**
- Modify: `ui/settings_center.py`(`_build_output_page` / `_build_video_page` / `_build_apps_page`)
- Modify: `ui/advanced_settings_dialog.py`(把可复用的 page 构建逻辑抽为可被设置中心调用的函数;或将其方法搬入设置中心后删除旧文件——见 Task 9)
- Test: `test_settings_center.py`(追加构造性测试)

**Interfaces:**
- Consumes: `advanced_config`(输出/视频字段均已存在;外部应用用 `get_external_apps`/`add/remove` 现有 API)。
- Produces: 三个页方法,均读写 `advanced_config`;`_save_output()/_save_video()/_save_apps()`。

- [ ] **Step 1: 写失败测试(三页可构造且含关键控件)**

```python
def test_output_video_apps_pages_build():
    from ui.settings_center import SettingsCenter
    w = SettingsCenter(get_i18n())
    for key in ("output", "video", "apps"):
        w.show_page(key)
    assert hasattr(w, "_apps_list")   # 外部应用列表存在
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_settings_center.py::test_output_video_apps_pages_build -q`
Expected: FAIL

- [ ] **Step 3: 迁移三页**

把 `ui/advanced_settings_dialog.py` 的 `_create_output_page`(291-501)、`_create_video_page`(771-860)、`_create_apps_page`(861+)的构建逻辑迁入设置中心对应方法,控件命名加 `self._` 前缀以便测试与保存;读写改为 `advanced_config`。保存逻辑沿用原 `_on_save`/各 setter。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: PASS

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
git add ui/settings_center.py test_settings_center.py
git commit -m "feat(settings): 迁移输出/视频/外部应用页到设置中心

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 关于页(合并 about_dialog)

**Files:**
- Modify: `ui/settings_center.py`(`_build_about_page`)
- Test: `test_settings_center.py`(追加)

**Interfaces:**
- Consumes: `ui/about_dialog.py` 现有版本/致谢/链接内容(抽为可复用函数或直接迁移其 widget 构建)。
- Produces: `_build_about_page` 返回含版本号 `QLabel` 的页。

- [ ] **Step 1: 写失败测试**

```python
def test_about_page_shows_version():
    from ui.settings_center import SettingsCenter
    from constants import APP_VERSION
    w = SettingsCenter(get_i18n()); w.show_page("about")
    texts = [c.text() for c in w.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)]
    assert any(str(APP_VERSION) in t for t in texts)
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_settings_center.py::test_about_page_shows_version -q`
Expected: FAIL

- [ ] **Step 3: 实现关于页**

迁移 `about_dialog` 的内容(版本、致谢"YOLO11/Ultralytics"等、官网链接)到 `_build_about_page`。若 `APP_VERSION` 常量名不同,先 `grep -rn "version" config.py` 确认实际来源后引用。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_settings_center.py -q`
Expected: PASS

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
git add ui/settings_center.py test_settings_center.py
git commit -m "feat(settings): 关于页合并进设置中心

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 主窗口接线(删参数面板/菜单收敛/header chip/取值改 advanced_config)

**Files:**
- Modify: `ui/main_window.py`(参数面板 `_create_parameters_section`;菜单 858-901;`ui_settings` 构建 2228-2238;技能等级 chip 1397-1410;`_show_advanced_settings` 2780 / `_show_skill_level_dialog` / `_show_about` 2820)
- Test: `test_main_window_settings_wiring.py`

**Interfaces:**
- Consumes: `ui.settings_center.SettingsCenter`;`advanced_config`(Task 1/3/4 字段)。
- Produces:
  - `_open_settings_center(self, start_page="culling")`:构造并 `exec()` `SettingsCenter`,关闭后刷新依赖值(技能 chip、识鸟面板状态)。
  - `ui_settings` 取值改为读 `advanced_config`(`min_confidence/min_sharpness/min_nima/flight_check/burst_check/birdid_auto_identify`),不再读参数面板控件。

- [ ] **Step 1: 写失败测试**

```python
# test_main_window_settings_wiring.py
import os; os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

def test_main_window_has_settings_entry_and_no_param_panel():
    from ui.main_window import SuperPickyMainWindow
    w = SuperPickyMainWindow()
    assert hasattr(w, "_open_settings_center")
    assert not hasattr(w, "sharp_slider")  # 参数面板已移除
    w.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_main_window_settings_wiring.py -q`
Expected: FAIL

- [ ] **Step 3: 改主窗口**

- 删除 `_create_parameters_section` 及其调用;主界面布局只保留目录选择/开始/结果。
- 菜单 858-901:移除"高级设置/技能等级/关于"分散项,改为单一「设置」`QAction`(图标 `gem.svg` 或齿轮)→ `_open_settings_center()`;关于入口并入(设置中心已含关于页)。
- header 技能 chip(1397-1410):保留只读 chip,`mousePressEvent`/包一层可点 → `_open_settings_center("culling")`。
- `ui_settings`(2228-2238)改为:

```python
        _adv = get_advanced_config()
        ui_settings = [
            int(_adv.min_confidence * 100),
            int(_adv.min_sharpness),
            _adv.min_nima,
            True,
            "log_compression",
            bool(_adv.flight_check),
            False,
            bool(_adv.burst_check),
            bool(_adv.birdid_auto_identify),
        ]
```

- 新增 `_open_settings_center`:

```python
    def _open_settings_center(self, start_page: str = "culling"):
        from ui.settings_center import SettingsCenter
        dlg = SettingsCenter(self.i18n, parent=self, start_page=start_page)
        dlg.exec()
        self._refresh_skill_chip()           # 刷新技能等级显示
        if getattr(self, "birdid_dock", None):
            self.birdid_dock.reload_from_config()  # Task 8 提供
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_main_window_settings_wiring.py -q`
Expected: PASS

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/main_window.py
git add ui/main_window.py test_main_window_settings_wiring.py
git commit -m "feat(settings): 主窗口删参数面板+菜单收敛为设置中心入口

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 识鸟面板瘦身 + 设置链接 + 从 config 读

**Files:**
- Modify: `ui/birdid_dock.py`(移除国家/区域/开关配置控件;`_load_settings/_save_settings` 改读写 `advanced_config`;新增 `reload_from_config()` 与"设置"跳转链接)
- Test: `test_birdid_dock_config.py`

**Interfaces:**
- Consumes: `advanced_config`(`birdid_*`)。
- Produces:
  - `reload_from_config(self)`:从 `advanced_config` 重新载入国家/区域/自动开关并刷新运行时使用的字段。
  - "设置"链接 `clicked` → 调用父窗口 `_open_settings_center("birdid")`(通过 signal 或 parent 引用)。

- [ ] **Step 1: 写失败测试**

```python
# test_birdid_dock_config.py
import os; os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

def test_birdid_dock_reads_region_from_config():
    from ui.birdid_dock import BirdIDDockWidget
    from advanced_config import get_advanced_config
    get_advanced_config().set_birdid_region(True, "AU", "澳大利亚", "AU-QLD", "昆士兰")
    dock = BirdIDDockWidget()
    dock.reload_from_config()
    assert dock.settings.get("selected_country") == "澳大利亚"
    dock.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest test_birdid_dock_config.py -q`
Expected: FAIL

- [ ] **Step 3: 改识鸟面板**

- 移除国家/区域下拉与识鸟开关等配置控件(只留选图/截图/结果运行时 UI)。
- `_load_settings` 改为从 `advanced_config` 读 `birdid_*` 组装 `self.settings` dict(保持其余运行时代码对 `self.settings` 的依赖不变);`_save_settings` 写 `advanced_config`。
- 新增 `reload_from_config()`;新增"设置"链接(`load_tinted_icon("gem.svg")` 或文字),点击发 signal/调用父 `_open_settings_center("birdid")`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest test_birdid_dock_config.py -q`
Expected: PASS

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/birdid_dock.py
git add ui/birdid_dock.py test_birdid_dock_config.py
git commit -m "feat(settings): 识鸟面板瘦身+配置改读 advanced_config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 清理旧入口 + locale + 跨平台冒烟

**Files:**
- Delete/Modify: `ui/advanced_settings_dialog.py`、`ui/skill_level_dialog.py`(保留 `SkillCard` 等被复用组件)、`ui/about_dialog.py` —— 移除不再使用的对话框入口与死代码
- Modify: `locales/zh_CN.json` / `locales/en_US.json` —— 删除废弃入口文案,确认 `settings.*` 与迁移文案齐全
- Test: 全量回归

**Interfaces:**
- Consumes: 全链路;确认无对已删除符号的引用。

- [ ] **Step 1: 全仓库引用扫描(应只剩组件复用,无对话框入口)**

Run:
```bash
grep -rn "AdvancedSettingsDialog\|_show_advanced_settings\|_show_skill_level_dialog\|SkillLevelDialog(\|AboutDialog(\|_show_about" --include="*.py" ui/ | grep -v "settings_center"
```
Expected: 无残留入口调用(若有,改为 `_open_settings_center`)。

- [ ] **Step 2: 删除/精简废弃对话框与死代码**

移除三个旧对话框中不再被引用的入口类/方法;保留被设置中心复用的组件(如 `SkillCard`、`get_skill_level_thresholds` re-export)。

- [ ] **Step 3: 全量编译 + JSON + 测试**

Run:
```bash
.venv/bin/python -m py_compile ui/*.py advanced_config.py
.venv/bin/python -c "import json;[json.load(open(f,encoding='utf-8')) for f in ('locales/zh_CN.json','locales/en_US.json')]"
.venv/bin/python -m pytest test_settings_center.py test_advanced_config_birdid_migration.py test_main_window_settings_wiring.py test_birdid_dock_config.py -q
```
Expected: 全绿。

- [ ] **Step 4: headless 启动冒烟(确认无旧入口残留报错)**

Run:
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from PySide6.QtWidgets import QApplication; from ui.main_window import SuperPickyMainWindow; app=QApplication([]); w=SuperPickyMainWindow(); w._open_settings_center(); print('OK')"
```
Expected: 打印 OK,无异常。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(settings): 清理旧设置/技能/关于入口+locale收尾

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** 设置中心骨架(T2)、6 分类(T2-6)、精选技能协同(T3)、识鸟三合一(T4)、输出/视频/应用迁移(T5)、关于(T6)、主窗口删面板+菜单收敛+header chip+取值改 config(T7)、识鸟面板瘦身(T8)、数据层 SSOT+迁移(T1)、清理旧入口+locale(T9)、SVG 图标(T2)。spec 各节均有对应任务。
- **Placeholder scan:** 无 TBD/TODO;测试与新逻辑均给出代码;迁移类步骤给出精确源行号引用(非占位)。少数构造签名(`MainWindow()`/`BirdIDDock()`/`APP_VERSION`)标注"按实际确认",因实施者需以现网代码为准——已在步骤中明确指出核对方式。
- **Type consistency:** `advanced_config` 新字段/属性/setter 名在 T1 定义,T4/T7/T8 一致引用;`SettingsCenter(i18n, parent, start_page)`、`show_page`、`PAGE_ORDER` 在 T2 定义,T3-7 一致使用;`reload_from_config` 在 T8 定义、T7 调用一致。
