# 实验室页面 + 多目录选鸟 实施计划 / Labs Page + Multi-folder Culling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在设置中心新增"实验室"页。报告、eBird 导出改为默认隐藏，由开关控制显示。实验功能"多目录选鸟"按添加顺序逐个处理多个任意位置的目录，结果浏览器按目录分标签查看，不合并。

**Architecture:**
- 开关和最近目录组存在 `advanced_config`。
- 多目录的纯逻辑放在新模块 `core/folder_queue.py`（无 Qt），负责校验、分类、队列状态和汇总。
- 主窗口不改单目录路径：
  - 把 `_start_processing` 尾部"建信号、起线程"抽成 `_launch_worker()`，单目录和队列共用。
  - 队列在主线程上用现有的完成、取消、出错、重置完成回调串起来，一个目录跑完再开始下一个。
- 结果浏览器顶部加一排目录标签，点标签就对该目录调用现有的 `open_directory`。

**Tech Stack:** Python 3.13（系统解释器，不用 `.venv`）、PySide6、pytest（`QT_QPA_PLATFORM=offscreen`，根目录 `conftest.py` 隔离配置）。

**Spec:** `docs/specs/2026-11-07-labs-and-multi-folder-design.md`

## Global Constraints

- **配置**：所有开关存放在 `advanced_config`（`DEFAULT_CONFIG` + `@property` + `set_*`），不新建 json 文件。
- **开关字段**：`lab_report_export`、`lab_ebird_export`、`lab_multi_folder`，默认值都是 `False`，不做老用户迁移。
- **最近目录组**：`recent_directory_groups`（`list[list[str]]`）加进 `PRESERVE_ON_RESET`。三个 `lab_*` 字段**不加**。
- **兼容**：不改 `recent_directories` 的格式，它仍是 `list[str]`。
- **跨盘**：任何地方都不能对两个用户目录调用 `os.path.relpath`。`os.path.commonpath` 跨盘会抛 `ValueError`，必须捕获，并按"不重叠"处理。
- **单目录零改动**：`lab_multi_folder=False`，或列表只有 1 个目录时，界面和行为与现在完全一致。
- **顺序执行**：严格按列表顺序处理，一个目录处理完（含整理、移动、元数据）才开始下一个，不并行。列表顺序就是添加顺序。
- **文案**：所有新文案在 `locales/zh_CN.json`、`zh_TW.json`、`en_US.json` 三语齐全。代码注释中英双语，用 docstring。
- **验证**：改动的 py 文件跑 `python3 -m py_compile`。界面改动要在真实窗口（cocoa）截图核对，不能只看 offscreen。

## Review Focus

1. **外接盘没插**：列表或最近目录组里的目录不存在时，灰显并跳过，队列不中断。测试见 Task 6、Task 8。
2. **Windows 跨盘**：`C:\a` 与 `D:\a` 同时添加时不能报错，也不能被判为重叠。测试见 Task 4（用 `ntpath` 模拟）。
3. **处理中途点停止**：后面的目录不能再开始；已处理完的目录要有标签和汇总；按钮恢复正常。测试见 Task 8。
4. **队列运行时关掉多目录开关**：设置中心在处理中可以打开。关掉开关后，当前队列照常跑完，列表收起留在 UI 刷新之后。测试见 Task 6。
5. **重置整组时，浏览器还开着别的目录**：每个目录重置前都要 `release_directory()`，否则 Windows 删不掉 `report.db`。测试见 Task 9。

---

### Task 1：配置字段

**Files:**
- Modify: `advanced_config.py`
  - `DEFAULT_CONFIG`（约 172–215 行附近）
  - `PRESERVE_ON_RESET`（约 360–380 行）
  - 在 `add_recent_directory`（约 1345 行）之后加组方法
- Test: `tests/test_labs_config.py`

**Interfaces:**
- Produces:
  - 属性 `AdvancedConfig.lab_report_export -> bool`、`lab_ebird_export -> bool`、`lab_multi_folder -> bool`，以及对应的 `set_lab_report_export(v: bool) -> None` 等三个 setter。
  - `AdvancedConfig.get_recent_directory_groups() -> list[list[str]]`
  - `AdvancedConfig.add_recent_directory_group(dirs: list[str]) -> None`：
    - 按目录集合判重（不管顺序），保留**这次**的顺序并插到最前；
    - 最多保留 10 组；
    - 少于 2 个目录时忽略；
    - 写完调用 `save()`。

- [ ] **Step 1: 写失败测试**

```python
def test_lab_flags_default_off():
    d = AdvancedConfig.DEFAULT_CONFIG
    assert d["lab_report_export"] is False and d["lab_ebird_export"] is False and d["lab_multi_folder"] is False

def test_lab_flags_not_preserved_groups_preserved():
    p = AdvancedConfig.PRESERVE_ON_RESET
    assert "recent_directory_groups" in p
    assert not {"lab_report_export", "lab_ebird_export", "lab_multi_folder"} & p

def test_group_dedup_by_set_and_newest_first():
    cfg = get_advanced_config()
    cfg.add_recent_directory_group(["/a", "/b"])
    cfg.add_recent_directory_group(["/c", "/d"])
    cfg.add_recent_directory_group(["/b", "/a"])
    assert cfg.get_recent_directory_groups() == [["/b", "/a"], ["/c", "/d"]]

def test_group_cap_10_and_single_ignored():
    cfg = get_advanced_config()
    cfg.add_recent_directory_group(["/only"])
    for i in range(12):
        cfg.add_recent_directory_group([f"/x{i}", f"/y{i}"])
    groups = cfg.get_recent_directory_groups()
    assert len(groups) == 10 and groups[0] == ["/x11", "/y11"]

def test_reset_turns_labs_off_keeps_groups():
    cfg = get_advanced_config()
    cfg.set_lab_multi_folder(True)
    cfg.add_recent_directory_group(["/a", "/b"])
    cfg.reset_to_default()
    assert cfg.lab_multi_folder is False
    assert cfg.get_recent_directory_groups() == [["/a", "/b"]]
```

- [ ] **Step 2:** 运行 `python3 -m pytest tests/test_labs_config.py -v`，预期 FAIL（KeyError / AttributeError）。
- [ ] **Step 3:** 实现上述字段和方法。`get_recent_directory_groups` 只返回"元素是 `list[str]` 且长度 ≥ 2"的条目，用来防御手改坏的配置。
- [ ] **Step 4:** 再跑 `python3 -m pytest tests/test_labs_config.py tests/test_restore_defaults.py -v`，预期全部 PASS。
- [ ] **Step 5:** 提交 `git commit -m "feat(config): 实验室开关与最近目录组字段"`。

---

### Task 2：设置中心"实验室"页

**Files:**
- Create: `img/ico/flask-conical.svg`（Lucide 图标原文，ISC 许可，与现有图标同源）
- Modify: `ui/settings_center.py`
  - `PAGE_ORDER`（130 行）改为 `["culling", "birdid", "output", "video", "apps", "labs", "about"]`
  - `_PAGE_ICON` 加 `"labs": "flask-conical.svg"`
  - `_PAGE_TITLE_KEY` 加 `"labs": "settings.nav_labs"`
  - `_build_page` 加分支
  - 新方法 `_build_labs_page() -> QWidget`
  - `done()` 加 `_save_labs()`
- Modify: 三个 locale 的 `settings` 段
- Test: `tests/test_settings_labs_page.py`

**Interfaces:**
- Consumes: Task 1 的属性和 setter。
- Produces:
  - 控件属性 `self._lab_report_cb`、`self._lab_ebird_cb`、`self._lab_multi_cb`（`QCheckBox`）。
  - 勾选变化**立即**写配置并 `save()`，与其他页的即时保存模型一致。
  - `_save_labs()` 在 `done()` 里做兜底保存。
- 新文案 key（`settings.` 下）：
  - `nav_labs`
  - `labs_more_title`、`labs_more_hint`
  - `labs_report`、`labs_report_desc`
  - `labs_ebird`、`labs_ebird_desc`
  - `labs_exp_title`、`labs_exp_hint`
  - `labs_multi_folder`、`labs_multi_folder_desc`
  - `labs_badge`
- 中文文案：
  - 标题"实验室"
  - "更多功能"——"默认关闭，开启后对应按钮才出现"
  - "实验功能"——"还在打磨，可能变化"
  - 徽标"实验"
  - 其余描述照 spec §4.1。

- [ ] **Step 1: 写失败测试**

```python
def test_labs_page_in_order_before_about():
    assert PAGE_ORDER.index("labs") == PAGE_ORDER.index("about") - 1

def test_labs_checkboxes_reflect_and_save(monkeypatch):
    cfg = get_advanced_config()
    w = SettingsCenter(get_i18n(), start_page="labs")
    assert not w._lab_report_cb.isChecked()
    w._lab_multi_cb.setChecked(True)
    assert cfg.lab_multi_folder is True          # 即时保存 / immediate save
    w.done(0)

def test_restore_defaults_rebuild_unchecks_labs(monkeypatch):
    """恢复默认后重建的实验室页全是未勾选 / rebuilt page shows all off."""
    cfg = get_advanced_config()
    cfg.set_lab_report_export(True); cfg.set_lab_multi_folder(True); cfg.save()
    monkeypatch.setattr(DeleteConfirmDialog, "exec", lambda self: (self.choose("reset"), 0)[1])
    monkeypatch.setattr(StyledMessageBox, "information", staticmethod(lambda *a, **k: None))
    w = SettingsCenter(get_i18n(), start_page="labs")
    w._on_reset_defaults()
    assert not any(cb.isChecked() for cb in (w._lab_report_cb, w._lab_ebird_cb, w._lab_multi_cb))
    w.done(0)
    assert cfg.lab_report_export is False and cfg.lab_multi_folder is False
```

- [ ] **Step 2:** 运行 `python3 -m pytest tests/test_settings_labs_page.py -v`，预期 FAIL。
- [ ] **Step 3:** 实现页面：
  - 两段之间用 `self._divider()`；
  - 勾选框用 `self._checkbox_qss()`；
  - "实验"徽标用一个小 `QLabel`（`COLORS['warning']` 系的半透明底）。
- [ ] **Step 4:** 跑上面的测试和 `tests/test_restore_defaults.py`，预期 PASS。
- [ ] **Step 5:** 真窗口验证：脚本打开 `SettingsCenter(start_page="labs")`，用真实平台截图，确认左侧多了"实验室"、勾选框不重叠。
- [ ] **Step 6:** 提交 `feat(settings): 实验室页`。

---

### Task 3：报告和 eBird 入口受开关控制

**Files:**
- Modify: `ui/results_browser_window.py`
  - 导出菜单构建处（约 2435–2475 行）
  - `changeEvent` / `showEvent`
- Test: `tests/test_browser_lab_entries.py`

**Interfaces:**
- Produces: `ResultsBrowserWindow._refresh_lab_entries() -> None`。行为：
  - 按 `lab_report_export` / `lab_ebird_export` 设置 `self._report_action` / `self._ebird_action` 的 `setVisible`；
  - 如果导出菜单里一个可见项都没有，隐藏 `self._export_menu_btn`。
  - 调用时机：构建完、`showEvent`、窗口激活（`QEvent.ActivationChange` 且 `isActiveWindow()`）。设置中心从主窗口打开，关掉后浏览器重新激活时会刷新。
- 不改 `_export_report` / `_export_ebird` 的实现。

- [ ] **Step 1: 写失败测试**

```python
def test_entries_hidden_by_default(browser):
    browser._refresh_lab_entries()
    assert not browser._report_action.isVisible() and not browser._ebird_action.isVisible()

def test_entries_follow_flags(browser):
    get_advanced_config().set_lab_ebird_export(True)
    browser._refresh_lab_entries()
    assert browser._ebird_action.isVisible() and not browser._report_action.isVisible()

def test_export_button_hidden_when_menu_empty(browser):
    """没有任何可见导出项时整个按钮隐藏 / hide the button when no item is visible."""
    if getattr(browser, "_apple_photos_action", None) is not None:
        browser._apple_photos_action.setVisible(False)   # 模拟 Windows：本来就没有这一项
    browser._refresh_lab_entries()
    assert browser._export_menu_btn.isHidden()
    get_advanced_config().set_lab_report_export(True)
    browser._refresh_lab_entries()
    assert not browser._export_menu_btn.isHidden()
```

注意：`_refresh_lab_entries` 判断"是否还有可见项"时，要看菜单里所有 action 的 `isVisible()`，不能只看报告和 eBird 两项，这样上面的模拟才成立。

（`browser` fixture 参照 `tests/test_browser_delete_sync.py` 构造 `ResultsBrowserWindow` 的方式。）

- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现 `_refresh_lab_entries`，并在上述三个时机调用。
- [ ] **Step 4:** 测试 PASS。
- [ ] **Step 5:** 提交 `feat(browser): 报告/eBird 导出改由实验室开关控制`。

---

### Task 4：多目录纯逻辑 `core/folder_queue.py`

**Files:**
- Create: `core/folder_queue.py`（无 Qt 依赖）
- Test: `tests/test_folder_queue.py`

**Interfaces（后续任务都依赖这里的名字）:**

```python
REJECT_DUPLICATE = "duplicate"; REJECT_OVERLAP = "overlap"
REJECT_DANGEROUS = "dangerous"; REJECT_NO_PHOTOS = "no_photos"; REJECT_MISSING = "missing"

def normalize_folder(path: str) -> str            # os.path.normpath(os.path.abspath(path))
def folders_overlap(a: str, b: str, pathmod=os.path) -> bool
def validate_new_folder(existing: list[str], new: str) -> Optional[str]   # None=可加，否则 REJECT_*

STATE_FRESH = "fresh"; STATE_RESUME = "resume"; STATE_PROCESSED = "processed"
def classify_folder(path: str) -> str
    # ResumeStateManager(path).exists() → resume；否则 .superpicky/report.db 存在 → processed；否则 fresh

@dataclass
class FolderOutcome:
    path: str
    status: str            # "done" | "skipped" | "error" | "stopped" | "not_started"
    stats: dict = field(default_factory=dict)
    error: str = ""

class FolderQueue:
    def __init__(self, folders: list[str], reprocess_all: bool): ...
    plan: list[tuple[str, str]]   # (path, action)，action ∈ {"process", "resume", "reprocess", "skip"}
    def next_item(self) -> Optional[tuple[str, str]]   # 跳过 skip，按顺序；无则 None
    def record(self, path: str, status: str, stats: Optional[dict] = None, error: str = "") -> None
    def stop(self) -> None          # 之后 next_item 返回 None，未开始的记 not_started
    @property
    def outcomes(self) -> list[FolderOutcome]   # 与 folders 同序
    def processed_folders(self) -> list[str]    # 有 report.db 的目录，按列表顺序（给浏览器标签用）
```

**规则：**
- `classify_folder` 的结果决定动作：
  - `resume` → `"resume"`；
  - `processed` 在 `reprocess_all=True` 时为 `"reprocess"`，否则为 `"skip"`；
  - `fresh` → `"process"`。
- `validate_new_folder` 的检查顺序：
  1. 不存在 → `missing`
  2. 归一化后已在列表中 → `duplicate`
  3. 与任一已有目录互相包含 → `overlap`
  4. `is_dangerous_root` → `dangerous`
  5. `scan_directories(new, max_depth=DEFAULT_SCAN_MAX_DEPTH)` 为空 → `no_photos`
- `folders_overlap` 在比较前做 `normcase`，用 `pathmod.commonpath([a, b]) in (a, b)` 判断；遇到 `ValueError` 返回 `False`。

- [ ] **Step 1: 写失败测试**

```python
import ntpath
def test_overlap_parent_child():          assert folders_overlap("/p", "/p/c")
def test_no_overlap_siblings_prefix():    assert not folders_overlap("/p/ab", "/p/a")
def test_cross_drive_windows_not_overlap(): assert not folders_overlap(r"C:\a", r"D:\a", pathmod=ntpath)
def test_validate_rejections(tmp_path, monkeypatch):
    a = tmp_path / "a"; (a / "c").mkdir(parents=True)
    monkeypatch.setattr(fq, "scan_directories", lambda p, max_depth=0: [object()])
    assert fq.validate_new_folder([], str(tmp_path / "nope")) == fq.REJECT_MISSING
    assert fq.validate_new_folder([str(a)], str(a) + os.sep) == fq.REJECT_DUPLICATE
    assert fq.validate_new_folder([str(a)], str(a / "c")) == fq.REJECT_OVERLAP
    monkeypatch.setattr(fq, "scan_directories", lambda p, max_depth=0: [])
    assert fq.validate_new_folder([], str(a / "c")) == fq.REJECT_NO_PHOTOS
def test_plan_actions(monkeypatch):
    states = {"/f": "fresh", "/r": "resume", "/p": "processed"}
    monkeypatch.setattr(fq, "classify_folder", states.get)
    assert fq.FolderQueue(["/f", "/r", "/p"], False).plan == [("/f","process"),("/r","resume"),("/p","skip")]
    assert fq.FolderQueue(["/p"], True).plan == [("/p", "reprocess")]
def test_queue_order_stop_and_outcomes(monkeypatch):
    monkeypatch.setattr(fq, "classify_folder", lambda p: "fresh")
    q = fq.FolderQueue(["/1", "/2", "/3"], False)
    assert q.next_item() == ("/1", "process"); q.record("/1", "done", {"total": 5})
    assert q.next_item() == ("/2", "process"); q.stop(); q.record("/2", "stopped")
    assert q.next_item() is None
    assert [o.status for o in q.outcomes] == ["done", "stopped", "not_started"]
```

- [ ] **Step 2:** 运行 `python3 -m pytest tests/test_folder_queue.py -v`，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS，并对新文件跑 `python3 -m py_compile`。
- [ ] **Step 5:** 提交 `feat(core): 多目录队列纯逻辑`。

---

### Task 5：目录列表控件 `ui/folder_list_widget.py`

**Files:**
- Create: `ui/folder_list_widget.py`
- Modify: 三个 locale 的 `labels` 段
- Test: `tests/test_folder_list_widget.py`

**Interfaces:**
- Consumes: `core.folder_queue.validate_new_folder`、`normalize_folder`，以及 `REJECT_*`。
- Produces: `class FolderListWidget(QWidget)`：
  - `foldersChanged = Signal(list)`
  - `folders() -> list[str]`
  - `set_folders(paths: list[str]) -> list[str]`：返回因不存在而跳过的目录。
  - `try_add(path: str) -> Optional[str]`：返回 `REJECT_*` 或 `None`；成功时发出 `foldersChanged`。
  - `set_enabled_editing(on: bool) -> None`：处理中禁用添加和移除。
- 外观：
  - 每行显示"📁 路径 · N 张"（N 取 `scan_directories` 各项 `photo_count` 之和）和 ✕ 按钮；
  - 下方有"＋ 添加目录…"按钮和"共 N 个目录 · M 张"合计；
  - 添加按钮弹 `QFileDialog.getExistingDirectory`；
  - 支持拖放目录（参照 `DropLineEdit`）。
- 被拒时，用 `StyledMessageBox.warning` 显示 `labels.folder_reject_<code>`。
- 新文案 key：
  - `labels.add_folder`
  - `labels.folder_total`（含 `{count}`、`{photos}`）
  - `labels.folder_photos`（含 `{n}`）
  - `labels.folder_reject_duplicate`、`_overlap`、`_dangerous`、`_no_photos`、`_missing`

- [ ] **Step 1: 写失败测试**：`try_add` 成功会追加并发信号；被拒时不追加，并且弹窗被调用（monkeypatch `StyledMessageBox.warning`）；`set_folders` 跳过不存在的目录并返回它们；移除按钮会删掉对应行并发信号；`folders()` 保持添加顺序。
- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS。
- [ ] **Step 5:** 提交 `feat(ui): 多目录列表控件`。

---

### Task 6：主窗口切换多目录界面 + 最近目录组

**Files:**
- Modify: `ui/main_window.py`
  - 目录栏构建处（约 1727–1735 行）
  - `_handle_directory_selection`（约 1917 行）
  - `_refresh_recent_menu`（约 1221 行）
  - `_open_settings_center`（约 3289 行）
- Test: `tests/test_main_window_multi_folder.py`

**Interfaces:**
- Consumes: Task 1、Task 5。
- Produces:
  - `self._folder_list: FolderListWidget`：与 `self.dir_input` 放在同一个父布局里，二者只显示一个。
  - `MainWindow._apply_lab_multi_folder() -> None`：在 `__init__` 末尾和 `_open_settings_center` 关闭后调用。
    - 开启时：显示列表；如果 `directory_path` 非空且列表为空，把它作为第一项。
    - 关闭时：显示 `dir_input`。如果列表有多个目录，只保留第一个，并对它走 `_handle_directory_selection`。**处理中（worker 存活或队列在跑）时只记下 pending，等 `_queue_finish` 或 `_on_finished`/`_on_cancelled` 之后再应用。**
  - `MainWindow._multi_folders() -> list[str]`：多目录开启时返回列表内容，否则返回 `[directory_path]` 或 `[]`。
  - `MainWindow._is_multi_mode() -> bool`：`lab_multi_folder` 为真且列表长度 ≥ 2。
  - `foldersChanged` 的处理：
    - 列表长度为 1 时，对该目录调用 `_handle_directory_selection`，完全等同单目录；
    - 长度 ≥ 2 时，`directory_path` 设为第一个，状态条显示"已选 N 个目录"（`labels.multi_selected`），按钮状态为：任一目录有 `report.db` 时 `has_results`，否则 `ready`。
- 最近目录菜单：
  - 多目录开启时，在单目录条目之前列出目录组，标签用 `menu.recent_group`（含 `{count}`、`{names}`；`names` 取各目录 basename，用"、"或", "连接，超过 60 字符截断加"…"）。
  - 点击目录组：`set_folders(group)`，跳过的目录写一条 warning 日志。
  - 多目录开启时，点击单个目录：`set_folders([d])`（替换整个列表）。
  - 多目录关闭时，不显示目录组。

- [ ] **Step 1: 写失败测试**（构造主窗口时参照 `tests/test_main_window_settings_wiring.py`）：
  - 开关关闭时 `_folder_list` 不可见，`dir_input` 可见；
  - 开启后可见性反过来；
  - 列表有两个目录时关掉开关，只剩第一个，且 `directory_path` 等于它；
  - 队列运行中关掉开关，列表不变，`_on_cancelled` 之后才收起；
  - 最近目录组的菜单项在开关关闭时不存在；
  - 点组：会调用 `set_folders`，不存在的目录被跳过。
- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS，并跑 `python3 -m pytest tests/test_main_window_settings_wiring.py -v` 确认单目录没变。
- [ ] **Step 5:** 真窗口截图核对目录列表布局。
- [ ] **Step 6:** 提交 `feat(main): 多目录列表与最近目录组`。

---

### Task 7：抽出 `_launch_worker`（单目录行为不变）

**Files:**
- Modify: `ui/main_window.py`：`_start_processing`（约 2566–2745 行）
- Test: `tests/test_main_window_launch_worker.py`

**Interfaces:**
- Produces:
  - `MainWindow._build_ui_settings() -> list`：原 `ui_settings = [...]` 那 9 项，原样搬出。
  - `MainWindow._launch_worker(directory: str, resume: bool, scan_results: Optional[list]) -> None`：
    - 原"创建信号、连接槽、`_update_action_buttons("processing")`、`_update_status_banner("processing")`、`WorkerThread(...)`.start()"这一段；
    - 外加清日志和进度、`processing_start` 日志（原样搬入）。
  - `MainWindow._prescan(directory: str) -> Optional[list]`：原危险目录检查和预扫描段。被阻断时返回 `None`（已弹窗），扫描异常时返回 `[]` 的哨兵会让行为改变，所以**用独立的 `_SCAN_FAILED` 哨兵对象**，保持原来"扫描失败不阻断、传 `None` 给 worker"的语义。
- `_start_processing` 改为调用以上三个方法，弹窗的顺序和条件一字不改。

- [ ] **Step 1: 写测试**（先在当前代码上跑，应 PASS，作为行为基线）：
  - monkeypatch `WorkerThread`，记录构造参数；
  - 对一个含照片的 tmp 目录，调用 `_start_processing`（所有弹窗打桩为"是"）；
  - 断言 `WorkerThread` 收到 `(directory_path, ui_settings, signals, i18n, resume=False, scan_results=<列表>)`，且 `ui_settings` 等于按当前配置算出的 9 项。
- [ ] **Step 2:** 重构。
- [ ] **Step 3:** 基线测试仍然 PASS；再跑一遍全量 `python3 -m pytest -q`，确认没有回归。
- [ ] **Step 4:** 提交 `refactor(main): 抽出 _launch_worker，单目录行为不变`。

---

### Task 8：多目录队列执行

**Files:**
- Modify: `ui/main_window.py`
  - `_on_start_btn_clicked`（2401 行）
  - `_on_finished`（约 2768 行）
  - `_on_cancelled`（2443 行）
  - `_on_error`（约 2817 行）
  - `_is_removable_source`（改为接收参数）
  - 新增 `_start_queue`、`_queue_advance`、`_queue_finish`
- Modify: 三个 locale（`dialogs.queue_*`、`logs.queue_*`、`messages.queue_*`）
- Test: `tests/test_main_window_queue.py`

**Interfaces:**
- Consumes:
  - Task 4 的 `FolderQueue`、`classify_folder`；
  - Task 7 的 `_build_ui_settings`、`_launch_worker`、`_prescan`；
  - Task 6 的 `_is_multi_mode`、`_multi_folders`。
- Produces:
  - `self._queue: Optional[FolderQueue]`
  - `_is_removable_source(path: Optional[str] = None) -> bool`：不传参时用 `directory_path`，单目录调用方不变。
  - `_start_queue() -> None`：`_on_start_btn_clicked` 在"非处理中且 `_is_multi_mode()`"时调用它，否则走原 `_start_processing`。
  - `_queue_advance() -> None`：取下一个目录并启动；没有下一个就调用 `_queue_finish()`。
  - `_queue_finish() -> None`：收尾，并保存最近目录组。

**`_start_queue` 的流程**（所有询问在第一个目录开始前一次问完）：
1. `_require_initialization_for_processing()`，再做 ExifTool 健康检查，各一次。
2. 存储卡警告：取 `[f for f in folders if self._is_removable_source(f)]`，非空时弹一次，用 `messages.queue_removable_message`，列出这些目录。
3. 识鸟国家提示、`_confirm_start()`、`_maybe_prompt_video_first_run()`：各一次，条件与单目录相同。
4. 预检确认框：先对每个目录 `classify_folder`，存在 `resume` 或 `processed` 时，弹一个 `DeleteConfirmDialog`（复用 `ui/custom_dialogs.py`）：
   - 正文分两组列出"上次没处理完（将接着处理）"和"已处理过（将跳过）"；
   - 带勾选框"全部重新处理"（`checkbox_text`），对应 `reprocess_all`；
   - 按钮：`("start", 开始, "primary")`、`("cancel", 取消, "secondary")`。
5. 建 `FolderQueue(folders, reprocess_all)`，调用 `config.add_recent_directory_group(folders)`，然后 `_queue_advance()`。

**`_queue_advance` 对每一项的处理：**
1. `self.directory_path = path`。
2. `_prescan(path)`：
   - 返回 `None`（危险目录或无照片）时记 `error` 并继续下一项；
   - 返回 `_SCAN_FAILED` 时按单目录语义，把 `scan_results=None` 传下去。
3. 打日志 `logs.queue_folder_start`：`"[{i}/{n}] {path}"`。
4. 按动作启动：
   - `"resume"`：`_launch_worker(path, True, scan)`。
   - `"process"`：`_launch_worker(path, False, scan)`。
   - `"reprocess"`：先 `_queue_pending_launch = (path, scan)`，再调 `self._do_reset_directory(skip_exif_reset=True, skip_confirm=True)`（与单目录"重来"同一路径）。`_on_reset_complete` 发现队列在跑且有 pending 时，不弹提示、不走 `_check_report_csv`，直接 `_launch_worker(path, False, scan)`。

**完成、取消、出错时的回调（队列在跑时）：**
- `_on_finished`：`record(path, "done", stats)`，**跳过**统计报告、提示音、识鸟面板、打开结果，然后 `QTimer.singleShot(0, self._queue_advance)`。
- `_on_cancelled`：`record(path, "stopped", stats)`，调 `stop()`，再调 `_queue_finish()`。
- `_on_error`：写日志 `logs.queue_folder_error`（含 `{path}`、`{error}`），`record(path, "error", error=msg)`，然后 `QTimer.singleShot(0, self._queue_advance)`。

**`_queue_finish`：**
- 控制台写汇总，每个目录一行：路径、状态、张数和 3/2/1/0 星分布；用 `messages.queue_summary_*` 文案。
- 提示音响一次；识鸟面板只用最后一个"done"目录的结果刷新一次。
- 打开结果：
  - `keep_temp_files` 为真时，`_auto_open_results()`，由 Task 10 负责带标签；
  - 否则只打开 `processed_folders()[0]` 的文件夹。
- `self._queue = None`；按钮状态和单目录收尾一致（`_update_action_buttons("has_results")`）。
- 应用 Task 6 中 pending 的开关变化。

**进度条：** 用已完成张数加当前目录进度换算成总百分比。总张数在 `_start_queue` 里用各目录 `photo_count` 之和算出。`_on_progress` 在队列模式下做这一步换算。

- [ ] **Step 1: 写失败测试**：monkeypatch `_launch_worker` 和 `_do_reset_directory` 记录调用，用手动 emit 的方式模拟完成。
  - 三个 fresh 目录：`_launch_worker` 依次被调用三次，参数为 `(dir_i, False, scan_i)`，第 i+1 次只在第 i 次 finished 之后发生；汇总只出现一次，提示音只响一次。
  - **等价性**：同一目录在单目录 `_start_processing` 中和在队列中，传给 `_launch_worker` 的 `resume` 和 `scan_results` 完全相等。
  - 预检：processed 目录默认被跳过（不调用 `_launch_worker`），勾选"全部重新处理"后先调用 `_do_reset_directory(skip_exif_reset=True, skip_confirm=True)`，`_on_reset_complete` 之后再 launch；resume 目录用 `resume=True`。
  - 第 2 个目录 error：第 3 个仍然执行，汇总里第 2 个是 error。
  - 第 2 个目录处理中 cancel：第 3 个不执行，状态为 `not_started`，按钮恢复。
  - 存储卡警告、`_confirm_start` 各只调用一次。
  - 目录不存在（外接盘拔了）：记 error，继续下一个。
- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS，并跑全量测试。
- [ ] **Step 5:** 真机端到端：用 3 个小目录（其中一个放在外接盘或另一个父目录下），各复制 5–10 张实拍照片，用多目录模式跑一遍。核对每个目录的 `report.db` 星级和 XMP，与单独处理同一份拷贝的结果逐张比较，必须一致。
- [ ] **Step 6:** 提交 `feat(main): 多目录按顺序逐个处理`。

---

### Task 9：整组重置

**Files:**
- Modify: `ui/main_window.py`
  - `_reset_directory`（约 2832 行）
  - `_on_reset_complete`（约 3153 行）
- Test: `tests/test_main_window_queue_reset.py`

**Interfaces:**
- Produces:
  - `_reset_directory()`：`_is_multi_mode()` 时改为调用 `_reset_group()`，否则走原逻辑。
  - `_reset_group() -> None`：
    - 一次确认，正文 `messages.reset_group_confirm` 列出所有目录；
    - 然后按顺序对每个目录设置 `directory_path`，调用 `_do_reset_directory(skip_exif_reset=False, skip_confirm=True)`；
    - 用 `self._reset_queue: list[str]` 在 `_on_reset_complete` 里串起下一个；
    - 每个目录的完成提示都抑制，最后弹**一次** `messages.reset_group_done`（含 `{count}`）；
    - 某个目录失败时写日志并继续，最后的提示里列出失败的目录。
  - `_do_reset_directory` 自己已经在每次开头调用 `self._results_browser.release_directory()`，不需要改。

- [ ] **Step 1: 写失败测试**：
  - 两个目录：只确认一次，`_do_reset_directory` 依次以 `skip_confirm=True` 调用两次，第二次在第一次 complete 之后；只弹一次完成提示；
  - 取消确认：一次都不调用；
  - 每次重置前，浏览器桩的 `release_directory` 都被调用。
- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS。
- [ ] **Step 5:** 提交 `feat(main): 多目录整组重置`。

---

### Task 10：结果浏览器目录标签

**Files:**
- Modify: `ui/results_browser_window.py`
  - 顶部工具栏区域
  - `open_directory`（2533 行）
- Modify: `ui/main_window.py`：`_auto_open_results`（2044 行）
- Test: `tests/test_browser_folder_tabs.py`

**Interfaces:**
- Produces:
  - `ResultsBrowserWindow.set_folder_tabs(folders: list[str]) -> None`：
    - 只为存在 `.superpicky/report.db` 的目录建标签；
    - 少于 2 个标签时隐藏整排；
    - 标签文字是"目录名 · N"，N 用 `tools.merged_report_db.summarize_directories` 的只读计数，不升级 schema；
    - 样式参照浏览器现有筹码，选中的用 `COLORS['accent']` 底。
  - `ResultsBrowserWindow.open_directory(directory: str, ask_batches: bool = True)`：`ask_batches=False` 时，如果目录下有多个批次，直接全部载入，不弹 `_ask_which_batches`。单目录调用方不传参，行为不变。
  - 点标签：`open_directory(path, ask_batches=False)`，然后高亮该标签。
- 主窗口 `_auto_open_results`：
  - `_is_multi_mode()` 时，先 `set_folder_tabs(self._multi_folders())`，再打开第一个有结果的目录（`ask_batches=False`）；
  - 否则调用 `set_folder_tabs([])`，再按原来的方式打开。

- [ ] **Step 1: 写失败测试**：
  - 三个目录中两个有 `report.db`：只出现 2 个标签；
  - 只有 1 个有结果时，标签排隐藏；
  - 点第二个标签：会以 `ask_batches=False` 调用 `open_directory(第二个目录)`；
  - 单目录 `_auto_open_results`：标签排隐藏，`open_directory` 调用参数与原来相同。
- [ ] **Step 2:** 运行测试，预期 FAIL。
- [ ] **Step 3:** 实现。
- [ ] **Step 4:** 测试 PASS。
- [ ] **Step 5:** 真窗口截图核对标签排在 14 寸宽度下不挤压工具栏；标签多时可横向滚动。
- [ ] **Step 6:** 提交 `feat(browser): 多目录结果按目录分标签`。

---

### Task 11：收尾

**Files:**
- Modify: `CLAUDE.md`：设置中心架构段加一条"实验室"约定。内容：
  - 新的先锋功能用 `lab_*` 开关，默认关闭；
  - 入口必须按开关隐藏；
  - 开关不进 `PRESERVE_ON_RESET`。
- Modify: `ChangeLog.md`：等下次发版时再写，本任务不改。

- [ ] **Step 1:** 对所有改动的 py 文件跑 `python3 -m py_compile`，用 PyCharm `get_file_problems` 复查。
- [ ] **Step 2:** 跑全量 `python3 -m pytest -q`（一次只跑一轮，结束后清理孤儿 exiftool），预期在 1537 的基础上加上新测试，全绿。
- [ ] **Step 3:** 三语切换，核对实验室页、目录列表、预检框、汇总的文案没有缺 key（缺 key 时界面会显示 key 名）。
- [ ] **Step 4:** 提交 `docs: CLAUDE.md 实验室约定`。
