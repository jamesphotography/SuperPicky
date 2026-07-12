# 结果浏览器 P0 三项改进（Paul 反馈）实施计划 / Browser P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 Paul 反馈 P0 三条——筛选面板对焦文案与右侧详情统一、缩略图鸟名+文件名两行并显+详情面板鸟种行、键盘打星（数字键 0-3 / Up/Down ±1）。

**Architecture:** 三条相互独立的浏览器 UI 改进。①filter_panel 文案改走既有 `browser.focus_state_*` i18n 键；②thumbnail_grid 标签函数改出两行 rich text + detail_panel rows 插一行（控件 `_val_species` 与 i18n 键均已存在）；③新增纯函数 `_rating_key_action` 决策键→星级，接入 `ResultsBrowserWindow` 与 `ResultsBrowserWidget` **两个近似类**的 keyPressEvent（复用各自已有的 `_on_rating_changed` 全链路），fullscreen_viewer 同步移除 Up/Down 翻图并提供轻量 `update_rating_display`。

**Tech Stack:** PySide6、tools.i18n、pytest（QT_QPA_PLATFORM=offscreen）。

**Spec:** `docs/specs/2026-07-12-browser-p0-paul-feedback-design.md`

## Global Constraints

- 中文文案不变（精焦/合焦/失焦）；英文左右两侧统一为 Critical Focus / Good Focus / Soft。
- 星级钳制 0-3：-1★ 照片可经数字键/Up 救回（Up 从 -1 → 0），Down 减到 0 为止不降到 -1；星级无变化不触发写入。
- Left/Right 翻图保留；对比（compare）模式键位不动。
- `results_browser_window.py` 有两个近似类（`ResultsBrowserWindow` :619 / `ResultsBrowserWidget` :1904），键盘改动**两处都要做**。
- 新测试文件在仓库根目录，`.gitignore` 覆盖 `test_*.py`，提交须 `git add -f`。
- 注释中英双写；验证 `.venv/bin/python -m py_compile` + pytest 全绿；提交到 dev。

---

### Task 1: filter_panel 对焦文案统一（i18n）

**Files:**
- Modify: `ui/filter_panel.py`（`_FOCUS_OPTIONS` :55-60、`_build_focus_checkboxes` :352-377、`_emit_filters` :473-479）
- Test: `test_browser_p0_paul.py`（新建）

**Interfaces:**
- Consumes: 既有 i18n 键 `browser.focus_state_best/good/bad`（zh: 精焦/合焦/失焦, en: Critical Focus/Good Focus/Soft）。
- Produces: `_FOCUS_OPTIONS` 变为 3 元组 `(mode, statuses, color)`（去掉硬编码中文 label）；`FilterPanel._focus_checks[mode]` 的 checkbox 文本 = `i18n.t(f"browser.focus_state_{mode.lower()}")`。

- [x] **Step 1: 写失败测试（新建 test_browser_p0_paul.py）**

```python
# -*- coding: utf-8 -*-
"""
Paul 反馈 P0 三项的回归测试:对焦文案统一/鸟名文件名并显/键盘打星。

Regression tests for the three P0 items from Paul's feedback: consistent
focus labels, species+filename display, and keyboard star rating.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_focus_filter_labels_match_detail_panel_terms():
    """
    筛选面板三个对焦 checkbox 的文本必须与右侧详情使用的
    browser.focus_state_* i18n 值一致(不再显示裸枚举 BEST/GOOD/BAD)。

    The three focus checkboxes must reuse the browser.focus_state_* strings
    shown in the detail panel (no more raw BEST/GOOD/BAD enums).
    """
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    panel = FilterPanel(i18n)
    for mode in ("BEST", "GOOD", "BAD"):
        cb = panel._focus_checks[mode]
        expected = i18n.t(f"browser.focus_state_{mode.lower()}")
        assert cb.text() == expected, f"{mode}: {cb.text()!r} != {expected!r}"
        assert cb.text() != mode  # 不允许裸枚举 / raw enum forbidden
    panel.close()
```

- [x] **Step 2: 跑测试确认失败**

```bash
cd /Users/jameszhenyu/Documents/JamesAPPS/SuperPicky2026
.venv/bin/python -m pytest test_browser_p0_paul.py::test_focus_filter_labels_match_detail_panel_terms -v
```

预期：FAIL（英文环境 `cb.text()=="BEST"`；中文环境硬编码值恰好相等也可能 PASS——若 PASS，改断言前先确认当前语言，本仓库默认 zh_CN 下硬编码「精焦」与 i18n 值一致会 PASS，此时直接进 Step 3 实现并保留测试作回归钉）。

- [x] **Step 3: 实现 `ui/filter_panel.py`**

3a. `_FOCUS_OPTIONS`（:55-60）去掉中文 label 字段：

```python
# 对焦按钮配置 (mode_key, statuses_list, color_key)
# statuses_list 是传给 DB 的 focus_status 列表;显示文案统一走
# browser.focus_state_* i18n 键,与右侧详情面板同词(Paul 反馈 P0-1)。
# Focus filter config (mode_key, statuses, color). Labels come from the
# browser.focus_state_* i18n keys so both panel sides use the same terms.
_FOCUS_OPTIONS = [
    ("BEST", ["BEST"],         COLORS['focus_best']),
    ("GOOD", ["GOOD"],         COLORS['focus_good']),
    ("BAD",  ["BAD", "WORST"], COLORS['focus_bad']),   # 失焦 = BAD + WORST 合并
]
```

3b. `_build_focus_checkboxes`（:352-377）：删除 `_is_zh` 行（:354），循环改为：

```python
        for mode, statuses, color in _FOCUS_OPTIONS:
            label = self.i18n.t(f"browser.focus_state_{mode.lower()}")
            cb = QCheckBox(label)
```

（其余 checkbox 构建代码不变。）

3c. `_emit_filters`（:473-479）两处解包同步改：

```python
        for mode, statuses, color in _FOCUS_OPTIONS:
```

```python
            selected_focus = [s for _, statuses, _ in _FOCUS_OPTIONS for s in statuses]
```

- [x] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py -v
```

预期：PASS。

- [x] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/filter_panel.py
git add ui/filter_panel.py && git add -f test_browser_p0_paul.py
git commit -m "fix(browser): 对焦筛选文案统一走 focus_state_* i18n,与详情面板同词"
```

---

### Task 2: 缩略图鸟名+文件名两行并显 + 详情面板鸟种行

**Files:**
- Modify: `ui/thumbnail_grid.py`（`_display_name` :26-33、卡片标签构建 :397-402）
- Modify: `ui/detail_panel.py`（rows 定义 :446-455 一带）
- Test: `test_browser_p0_paul.py`（追加两个测试）

**Interfaces:**
- Consumes: `_display_name(photo: dict) -> str`（保持原签名不变，仍被卡片使用）；detail_panel 既有控件 `self._val_species`（:418，自带点击复制）与既有 i18n 键 `browser.meta_species`（zh 鸟种/en Species，两语言包 :1227 均已存在）。
- Produces: `thumbnail_grid._tile_label_text(photo: dict, burst_suffix: str = "") -> str`（模块级纯函数，返回 QLabel rich text：有鸟名两行、无鸟名单行）；detail_panel rows 中 `browser.meta_species` 行位于 `browser.meta_gbif_rarity` 之前。

- [x] **Step 1: 追加失败测试**

```python
def test_tile_label_shows_species_and_filename():
    """
    有鸟种时卡片标签两行并显(鸟名+文件名),无鸟种时只显示文件名。
    With a species the tile label carries both species and filename;
    without a species it falls back to the filename only.
    """
    from ui.thumbnail_grid import _tile_label_text

    photo = {"filename": "DSC01234.ARW",
             "bird_species_cn": "白胸鸲鹟", "bird_species_en": "White-breasted Robin"}
    text = _tile_label_text(photo)
    assert "DSC01234.ARW" in text
    assert ("白胸鸲鹟" in text) or ("White-breasted Robin" in text)

    no_species = {"filename": "DSC09999.NEF"}
    assert _tile_label_text(no_species) == "DSC09999.NEF"

    # 连拍后缀跟在第一行(鸟名)之后 / burst suffix stays on the first line
    text2 = _tile_label_text(photo, " (5)")
    assert "(5)" in text2.split("<br/>")[0]


def test_detail_panel_species_row_above_gbif():
    """
    详情面板 rows 中鸟种行存在且位于全球罕见度行之前(Paul 截图诉求)。
    The species row exists in the detail panel and sits above GBIF rarity.
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    keys = [k for k, _ in panel._meta_rows]
    assert "browser.meta_species" in keys
    assert keys.index("browser.meta_species") < keys.index("browser.meta_gbif_rarity")
    panel.close()
```

注意：`panel._meta_rows` 尚不存在——实现时把 rows 列表存成实例属性（`self._meta_rows = rows`），测试据此断言顺序。DetailPanel 构造函数若签名不同（先查 `class DetailPanel` 的 `__init__`），按实际调整测试构造行。

- [x] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py -v -k "tile_label or species_row"
```

预期：FAIL（`_tile_label_text` 未定义 / `_meta_rows` 无此属性）。

- [x] **Step 3: 实现 `ui/thumbnail_grid.py`**

3a. 文件头 import 区加 `import html as _html`（放在现有 import 之后）。

3b. `_display_name`（:26-33）之后新增：

```python
def _tile_label_text(photo: dict, burst_suffix: str = "") -> str:
    """
    卡片底部标签文本:有鸟种时两行 rich text(第一行鸟名+连拍后缀,第二行
    文件名小一号灰字),无鸟种时返回纯文件名单行(Paul 反馈 P0-2)。

    Tile label text: with a species, two rich-text lines (species + burst
    suffix, then the filename in smaller muted type); otherwise just the
    plain filename.

    参数 / Parameters:
        photo (dict): 照片记录 / photo record.
        burst_suffix (str): 连拍数量后缀,如 " (5)" / burst-count suffix.

    返回 / Returns:
        str: QLabel 文本(含 HTML 时 QLabel 自动按 rich text 渲染)。
    """
    primary = _display_name(photo)
    filename = photo.get("filename", "")
    if primary and primary != filename:
        return (
            f"{_html.escape(primary + burst_suffix)}<br/>"
            f"<span style='font-size:9px;color:{COLORS['text_muted']};'>"
            f"{_html.escape(filename)}</span>"
        )
    return primary + burst_suffix
```

3c. 卡片标签构建（:397-402）改为：

```python
        # 卡片底部:鸟种+文件名两行并显(无鸟种时单行文件名);悬停显示文件名
        # Tile footer: species + filename on two lines (filename only when
        # no species); the tooltip still shows the filename.
        burst_suffix = f" ({self.burst_count})" if (self.is_burst_group and self.burst_count > 1) else ""
        self.setToolTip(photo.get("filename", ""))
        self.name_label = QLabel(_tile_label_text(photo, burst_suffix))
```

（原 `fn = _display_name(photo)` / `if self.is_burst_group ...` 两行删除；`self.name_label` 后续 setAlignment/样式/MaxWidth 代码不变。）

- [x] **Step 4: 实现 `ui/detail_panel.py`**

4a. rows 注释与定义（:446-455）改为：

```python
        # 文件名仍只在大图顶条显示;鸟种行按用户反馈(Paul P0-2)加回详情面板,
        # 置于全球罕见度上方,点击可复制鸟名(复用 _val_species 既有行为)。
        # The filename stays in the big-image top strip only; the species row
        # returns to the panel (above GBIF rarity) per user feedback, keeping
        # the existing click-to-copy behavior of _val_species.
        rows = [
            ("browser.meta_species",    self._val_species),
            ("browser.meta_gbif_rarity", self._val_gbif_rarity),
            ("browser.meta_iucn",       self._val_iucn),
```

（其余行保持原顺序不动。）

4b. rows 列表构建完成处（`rows = [...]` 之后第一行）追加实例属性：

```python
        # 供测试断言行序 / kept for tests asserting row order
        self._meta_rows = rows
```

- [x] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py -v
```

预期：PASS（全部 3 个测试）。

- [x] **Step 6: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/thumbnail_grid.py ui/detail_panel.py
git add ui/thumbnail_grid.py ui/detail_panel.py && git add -f test_browser_p0_paul.py
git commit -m "feat(browser): 缩略图鸟名+文件名两行并显;详情面板罕见度上方加鸟种行"
```

---

### Task 3: 键盘打星（数字键 0-3 / Up/Down ±1）

**Files:**
- Modify: `ui/results_browser_window.py`（模块级新增 `_rating_key_action`；`ResultsBrowserWindow.keyPressEvent` :1798；`ResultsBrowserWidget.keyPressEvent` :2897）
- Modify: `ui/fullscreen_viewer.py`（`keyPressEvent` :1359 移除 Up/Down 翻图；新增 `update_rating_display`；`show_photo` :1165-1173 星级块抽取复用）
- Test: `test_browser_p0_paul.py`（追加纯函数测试）

**Interfaces:**
- Consumes: 两个浏览器类各自已有的 `_on_rating_changed(photo_or_filename, new_rating)`（:1368 / :2463，全链路：DB+缩略图+EXIF+移动）；当前照片来源 `self._fullscreen._current_photo`（全屏）/ `self._detail_panel._current_photo`（网格）。
- Produces: 模块级 `_rating_key_action(key: int, current_rating) -> Optional[int]`（None=与打星无关或星级无变化）；`FullscreenViewer.update_rating_display(photo: dict) -> None`（只刷新顶条星级/皇冠，不重载图片）。

- [x] **Step 1: 追加失败测试（纯函数）**

```python
def test_rating_key_action_digits_and_arrows():
    """
    键盘打星决策:数字键 0-3 直设,Up/Down ±1 钳制 0-3;-1 可经 Up/数字键
    救回(Up 从 -1 → 0);星级无变化返回 None;无关键返回 None。

    Keyboard rating decisions: digits set directly, Up/Down step within
    0-3, -1 recovers via Up (to 0) or digits, no-op returns None.
    """
    from PySide6.QtCore import Qt
    from ui.results_browser_window import _rating_key_action

    assert _rating_key_action(Qt.Key_2, 0) == 2
    assert _rating_key_action(Qt.Key_0, 3) == 0
    assert _rating_key_action(Qt.Key_3, 3) is None          # 无变化 / no-op
    assert _rating_key_action(Qt.Key_Up, 1) == 2
    assert _rating_key_action(Qt.Key_Up, 3) is None          # 顶格 / ceiling
    assert _rating_key_action(Qt.Key_Up, -1) == 0            # 救回 / recover
    assert _rating_key_action(Qt.Key_Down, 2) == 1
    assert _rating_key_action(Qt.Key_Down, 0) is None        # 到 0 为止 / floor
    assert _rating_key_action(Qt.Key_Down, -1) is None       # -1 不再降 / stays
    assert _rating_key_action(Qt.Key_1, -1) == 1             # 数字键救回
    assert _rating_key_action(Qt.Key_F, 2) is None           # 无关键 / unrelated
    assert _rating_key_action(Qt.Key_2, None) == 2           # rating 缺失按 0 处理
```

- [x] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py::test_rating_key_action_digits_and_arrows -v
```

预期：FAIL（`_rating_key_action` 未定义）。

- [x] **Step 3: 实现 `ui/results_browser_window.py` 模块级纯函数**

放在 `_coerce_photo`（:56）之后：

```python
# 键盘打星键集(数字 0-3 + 上下箭头) / keys handled by keyboard rating
_RATING_KEYS = (Qt.Key_0, Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_Up, Qt.Key_Down)


def _rating_key_action(key: int, current_rating) -> Optional[int]:
    """
    键盘打星决策(Paul 反馈 P0-3):数字键 0-3 直接设星;Up/Down 星级 ±1,
    钳制 0-3——-1★(无鸟)可经 Up(→0)或数字键救回,Down 减到 0 为止。

    Decide the new star rating for a key press: digits 0-3 set directly;
    Up/Down step by one within 0-3 (-1 recovers via Up→0 or digits; Down
    never goes below 0).

    参数 / Parameters:
        key (int): Qt 键码 / Qt key code.
        current_rating: 当前星级(可能为 None/-1..3) / current rating.

    返回 / Returns:
        Optional[int]: 新星级;None 表示与打星无关或星级无变化。
    """
    digit_map = {Qt.Key_0: 0, Qt.Key_1: 1, Qt.Key_2: 2, Qt.Key_3: 3}
    try:
        cur = int(current_rating) if current_rating is not None else 0
    except (TypeError, ValueError):
        cur = 0
    if key in digit_map:
        new = digit_map[key]
    elif key == Qt.Key_Up:
        new = 0 if cur < 0 else min(3, cur + 1)
    elif key == Qt.Key_Down:
        if cur <= 0:
            return None
        new = cur - 1
    else:
        return None
    return new if new != cur else None
```

（文件头已有 `from typing import Optional` 则复用；没有则在 import 区补。）

- [x] **Step 4: 跑纯函数测试确认通过**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py::test_rating_key_action_digits_and_arrows -v
```

预期：PASS。

- [x] **Step 5: 接线 `ResultsBrowserWindow.keyPressEvent`（:1798）**

原 Up/Down 与 Left/Right 合并的两个分支拆开，并新增打星分支（方法整体结构保持）：

```python
        if key == Qt.Key_Left:
            if in_fullscreen:
                self._fullscreen_prev()
            else:
                self._prev_photo()
        elif key == Qt.Key_Right:
            if in_fullscreen:
                self._fullscreen_next()
            else:
                self._next_photo()
        elif key in _RATING_KEYS:
            # 键盘打星(Paul P0-3):Up/Down 由翻图改为星级±1,数字键 0-3 直设。
            # Keyboard rating: Up/Down now step the rating; digits set it.
            photo = (getattr(self._fullscreen, "_current_photo", None) if in_fullscreen
                     else getattr(self._detail_panel, "_current_photo", None))
            if photo:
                new_rating = _rating_key_action(key, photo.get("rating"))
                if new_rating is not None:
                    self._on_rating_changed(photo, new_rating)
                    if in_fullscreen:
                        self._fullscreen.update_rating_display(photo)
                    else:
                        self._detail_panel.show_photo(photo)
        elif key == Qt.Key_Tab:
```

（`elif key == Qt.Key_Tab:` 起既有分支不动。`_on_rating_changed` 已把新星级写回 `photo["rating"]`——它更新的是 `self._filtered_photos` 中的同一 dict 对象。）

- [x] **Step 6: 接线 `ResultsBrowserWidget.keyPressEvent`（:2897）**

该类结构与 Window 类同构（自查 :2897-2945 分支）；对 Up/Down/数字键做**完全相同**的改动：Left/Right 拆开保留翻图，`elif key in _RATING_KEYS:` 分支代码与 Step 5 相同（该类同样有 `self._fullscreen`、`self._detail_panel`、`self._on_rating_changed`，实现前用 grep 确认属性名一致，不一致则按该类实际属性调整）。

- [x] **Step 7: `ui/fullscreen_viewer.py` 移除 Up/Down 翻图 + 星级刷新方法**

7a. `keyPressEvent`（:1359）：

```python
        if key == _Qt.Key_Left:
            self.prev_requested.emit()
        elif key == _Qt.Key_Right:
            self.next_requested.emit()
        elif key in (_Qt.Key_Up, _Qt.Key_Down) or key in (
            _Qt.Key_0, _Qt.Key_1, _Qt.Key_2, _Qt.Key_3
        ):
            # 键盘打星交给宿主窗口处理(Paul P0-3) / bubble to host for rating
            event.ignore()
            super().keyPressEvent(event)
```

（其余 F/Z/Escape/Delete 分支不动。注意:全屏 viewer 是宿主 stack 的子页,未处理的键事件会冒泡到 `ResultsBrowserWindow/Widget.keyPressEvent`;实现后须在 offscreen 冒烟中确认冒泡路径成立,若 viewer 为独立顶层窗口则改为在 viewer 内直接发既有信号处理。）

7b. `show_photo` 的星级块（:1165-1173）抽成方法并调用：

```python
    def update_rating_display(self, photo: dict) -> None:
        """
        仅刷新顶条星级/皇冠显示,不重载图片(外部键盘改星后调用)。
        Refresh only the top-strip rating/crown without reloading the image.
        """
        rating = photo.get("rating", 0)
        if photo.get("picked"):
            self._rating_label.setPixmap(
                load_tinted_icon("crown.svg", COLORS['star_gold'], 18).pixmap(QSize(18, 18))
            )
        elif isinstance(rating, int) and rating >= 1:
            self._rating_label.setPixmap(stars_pixmap(rating, COLORS['star_gold'], size=16))
        else:
            self._rating_label.setText("")
```

`show_photo` 内原星级块（:1165-1173）替换为：

```python
        self.update_rating_display(photo)
```

- [x] **Step 8: 全量测试 + py_compile + GUI 冒烟**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py -v
.venv/bin/python -m py_compile ui/results_browser_window.py ui/fullscreen_viewer.py
```

预期：4 测试全 PASS、编译通过。

- [x] **Step 9: 提交**

```bash
git add ui/results_browser_window.py ui/fullscreen_viewer.py && git add -f test_browser_p0_paul.py
git commit -m "feat(browser): 键盘打星——数字键0-3直设,Up/Down星级±1(翻图保留Left/Right)"
```

---

### Task 4: 回归 + 收尾

**Files:**
- Modify: 无新改动（验证与文档）

- [x] **Step 1: 相关测试全量回归**

```bash
.venv/bin/python -m pytest test_browser_p0_paul.py test_settings_center.py test_main_window_settings_wiring.py test_rating_mover.py -v
```

预期：全部 PASS。

- [x] **Step 2: 真实 GUI 冒烟（用户目录只读验证不可行时跳过移动验证）**

用 Test-Superpicky 目录打开结果浏览器人工冒烟（用户执行）：英文界面对焦文案、缩略图两行标签、详情鸟种行、数字键/上下键改星并观察文件是否移动到对应星级目录。此步为用户验收项，代码侧完成后提醒用户。

- [x] **Step 3: 提交计划勾选状态**

```bash
git add docs/plans/2026-07-12-browser-p0-paul.md
git commit -m "docs(browser): P0 三项计划执行完毕勾选"
```
