# 评星算法选择卡片（V1/V2）实施计划 / Rating Algorithm Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在设置中心精选页新增「评星算法」两张选择卡（V2 批内配额=默认推荐 / V1 绝对阈值=旧版），点卡即落盘并实时切换设置中心与首页两处滑块的可见性。

**Architecture:** `rating_algorithm` 已是 `advanced_config` 的 SSOT 字段（默认 `"v2"`，setter 只收 `"v1"/"v2"`），处理链 `photo_processor.py:1167` 每次运行读取——本计划**只做 UI 暴露**，不碰任何评星逻辑。两处 UI（设置中心精选页、首页快速面板）均已同时构建两套滑块，改造点是：精选页配额行从条件构建改为无条件构建、新增卡片区与可见性切换方法、首页 `_refresh_param_panel` 增加按算法切换可见性。

**Tech Stack:** PySide6（QFrame 卡片 + Signal）、advanced_config SSOT、locales/*.json i18n、pytest（QT_QPA_PLATFORM=offscreen）。

**Spec:** `docs/specs/2026-07-10-rating-algo-selector-design.md`（已获用户批准；其中「复用 SkillLevelCard」调整为同款视觉的私有 `_AlgoCard`——SkillLevelCard 的内容与 SKILL_PRESETS 硬绑定，改共享组件风险大于收益）。

## Global Constraints

- 默认算法保持 `"v2"`，不改 `advanced_config.py` 任何默认值或 setter。
- 不改 v1/v2 评星链路逻辑（core/、photo_processor 不动）。
- i18n 中英双语必须成对新增；注释按仓库规范中英双写。
- 测试不得污染真实用户配置：设置中心测试用临时 `AdvancedConfig` + monkeypatch `advanced_config.get_advanced_config`；首页测试只改内存 dict 不调 `save()`。
- 离屏测试断言可见性用 `isHidden()`（父窗口未 show 时 `isVisible()` 恒 False，不可用）。
- 验证：`.venv/bin/python -m py_compile` 变更 py 文件；`.venv/bin/python -m pytest` 相关测试文件全绿。

---

### Task 1: 设置中心精选页 — 算法卡片 + 滑块运行时切换 + i18n

**Files:**
- Modify: `ui/settings_center.py`（import 区 :19、`_build_culling_page` :317-436、新增 `_AlgoCard` 类与两个方法）
- Modify: `locales/zh_CN.json`（settings 段，`culling_skill_section` 附近 :579）
- Modify: `locales/en_US.json`（同位置）
- Test: `test_settings_center.py`（追加一个测试）

**Interfaces:**
- Consumes: `advanced_config.get_advanced_config().rating_algorithm` / `set_rating_algorithm(str)` / `save()`（均已存在）。
- Produces: `SettingsCenter._algo_cards: dict[str, _AlgoCard]`、`SettingsCenter._on_algo_selected(algo_key: str) -> None`、`SettingsCenter._apply_algo_visibility() -> None`、行控件元组 `self._quota_row_widgets` / `self._sharp_row_widgets` / `self._nima_row_widgets`（各为 `(QLabel, QSlider, QLabel)`）。`self._cull_quota` 从「v1 下为 None」变为**恒非 None**（`_on_skill_preset_selected`/`_save_culling` 现有 `is not None` 守卫兼容，不必改）。

- [ ] **Step 1: 写失败测试**

在 `test_settings_center.py` 末尾追加：

```python
def test_algo_cards_switch_config_and_slider_visibility():
    """
    验证评星算法卡片:v2 初始态配额行可见/旧滑块隐藏;点 v1 卡后配置落盘为
    v1、旧滑块可见/配额行隐藏、卡片选中态切换;点 v2 卡恢复。

    Verify the rating-algorithm cards: under v2 the quota row is visible and the
    legacy sliders are hidden; clicking the v1 card persists "v1", swaps slider
    visibility and card selection; clicking v2 restores everything.
    """
    import tempfile
    import advanced_config as _ac_mod
    from advanced_config import AdvancedConfig
    from ui.settings_center import SettingsCenter
    from tools.i18n import get_i18n

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    _orig_get = _ac_mod.get_advanced_config
    try:
        cfg = AdvancedConfig(config_file=tmp_path)
        assert cfg.rating_algorithm == "v2"  # 默认 v2 / default stays v2
        # settings_center 内部均为方法内局部 import,调用时解析到补丁后的符号
        # settings_center uses in-method imports, resolved at call time
        _ac_mod.get_advanced_config = lambda: cfg

        w = SettingsCenter(get_i18n())
        w.show_page("culling")

        # v2 初始:配额行可见,旧阈值滑块隐藏 / v2 initial state
        assert not w._cull_quota.isHidden()
        assert w._cull_sharp.isHidden() and w._cull_nima.isHidden()
        assert w._algo_cards["v2"]._selected and not w._algo_cards["v1"]._selected

        # 点 v1 卡 → 落盘 + 可见性互换 / click v1 card
        w._on_algo_selected("v1")
        assert cfg.rating_algorithm == "v1"
        assert AdvancedConfig(config_file=tmp_path).rating_algorithm == "v1"  # 已写盘
        assert w._cull_quota.isHidden()
        assert not w._cull_sharp.isHidden() and not w._cull_nima.isHidden()
        assert w._algo_cards["v1"]._selected and not w._algo_cards["v2"]._selected

        # 点 v2 卡 → 恢复 / click v2 card restores
        w._on_algo_selected("v2")
        assert cfg.rating_algorithm == "v2"
        assert not w._cull_quota.isHidden()
        assert w._cull_sharp.isHidden() and w._cull_nima.isHidden()
        w.close()
    finally:
        _ac_mod.get_advanced_config = _orig_get
        os.unlink(tmp_path)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/jameszhenyu/Documents/JamesAPPS/SuperPicky2026
.venv/bin/python -m pytest test_settings_center.py::test_algo_cards_switch_config_and_slider_visibility -v
```

预期：FAIL（`AttributeError: ... has no attribute '_algo_cards'`）。

- [ ] **Step 3: i18n 键（中英各 5 个）**

`locales/zh_CN.json` settings 段（`"culling_skill_section"` 行之后）插入：

```json
    "culling_algo_section": "评星算法",
    "culling_algo_v2_title": "V2 · 批内配额（推荐）",
    "culling_algo_v2_desc": "同批照片相对排序，按配额取最好的前 N% 为 3 星",
    "culling_algo_v1_title": "V1 · 绝对阈值（旧版）",
    "culling_algo_v1_desc": "按固定锐度/美学阈值定星，星级数量不受配额控制",
```

`locales/en_US.json` 同位置插入：

```json
    "culling_algo_section": "Rating Algorithm",
    "culling_algo_v2_title": "V2 · Batch Quota (Recommended)",
    "culling_algo_v2_desc": "Photos are ranked within each batch; the best N% get 3 stars",
    "culling_algo_v1_title": "V1 · Fixed Thresholds (Legacy)",
    "culling_algo_v1_desc": "Stars come from fixed sharpness/aesthetic thresholds, not capped by quota",
```

注意：两文件插入后跑 `python -c "import json; json.load(open('locales/zh_CN.json', encoding='utf-8'))"` 确认合法（逗号）。

- [ ] **Step 4: 实现 `ui/settings_center.py`**

4a. import 区（:19）加 `Signal`：

```python
from PySide6.QtCore import Qt, Signal
```

4b. 在 `SettingsCenter` 类定义之前新增模块级私有卡片类：

```python
class _AlgoCard(QFrame):
    """
    评星算法选择卡片（标题+描述+选中态），视觉样式与 SkillLevelCard 一致。
    内容为任意标题/描述文本，不绑定 SKILL_PRESETS（故不复用 SkillLevelCard）。

    Rating-algorithm selector card (title + description + selected state),
    visually consistent with SkillLevelCard but content-agnostic (hence a
    dedicated class instead of reusing the preset-bound SkillLevelCard).
    """

    clicked = Signal(str)  # 发射算法 key("v1"/"v2") / emits the algorithm key

    def __init__(self, algo_key: str, title: str, desc: str,
                 parent: QWidget | None = None) -> None:
        """
        参数 / Parameters:
            algo_key (str): 算法 key，"v1" 或 "v2" / algorithm key.
            title (str): 卡片标题 / card title.
            desc (str): 卡片描述（自动换行）/ card description (word-wrapped).
        """
        super().__init__(parent)
        self.algo_key = algo_key
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(100)
        self.setMinimumWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        name_label = QLabel(title)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:14px;font-weight:600;"
            "background:transparent;border:none;"
        )
        layout.addWidget(name_label)

        desc_label = QLabel(desc)
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(
            f"color:{COLORS['text_tertiary']};font-size:11px;"
            "background:transparent;border:none;"
        )
        layout.addWidget(desc_label)

        self._update_style()

    def set_selected(self, selected: bool) -> None:
        """设置选中态并刷新样式 / Set selection state and refresh style."""
        self._selected = selected
        self._update_style()

    def _update_style(self) -> None:
        """按选中态应用边框/底色（与 SkillLevelCard 同款）。/ Apply style."""
        if self._selected:
            self.setStyleSheet(
                f"QFrame {{background-color:{COLORS['accent']}20;"
                f"border:2px solid {COLORS['accent']};border-radius:8px;}}"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{background-color:{COLORS['bg_elevated']};"
                f"border:2px solid {COLORS['border']};border-radius:8px;}}"
                f"QFrame:hover {{border-color:{COLORS['accent']};}}"
            )

    def mousePressEvent(self, event) -> None:
        """点击发射 clicked(algo_key) / Emit clicked(algo_key) on press."""
        self.clicked.emit(self.algo_key)
        super().mousePressEvent(event)
```

4c. `_build_culling_page`：在 `self._select_skill_radio(self._current_skill_key)`（:317）之后、「阈值区」标题（:320）之前插入算法区（`self._rating_v2` 的赋值**上移到这里**，原 :354 处删除）：

```python
        # ── 评星算法区 / Rating-algorithm section ────────────────────────────
        # V4.6(rating-v2/UI): 暴露 rating_algorithm 为两张选择卡,默认 v2;
        # 点卡即写盘、下次跑批生效,下方滑块可见性随算法实时切换。
        # V4.6 (rating-v2/UI): expose rating_algorithm as two selector cards
        # (default v2). Clicking persists immediately (takes effect next run)
        # and swaps the slider rows below in real time.
        self._rating_v2 = cfg.rating_algorithm == "v2"
        algo_title = QLabel(self.i18n.t("settings.culling_algo_section"))
        algo_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(algo_title)

        algo_row = QHBoxLayout()
        algo_row.setSpacing(8)
        self._algo_cards: dict[str, _AlgoCard] = {}
        for algo_key in ("v2", "v1"):
            card = _AlgoCard(
                algo_key,
                self.i18n.t(f"settings.culling_algo_{algo_key}_title"),
                self.i18n.t(f"settings.culling_algo_{algo_key}_desc"),
            )
            card.clicked.connect(self._on_algo_selected)
            self._algo_cards[algo_key] = card
            algo_row.addWidget(card)
        algo_row.addStretch(1)
        lay.addLayout(algo_row)
        self._algo_cards["v2" if self._rating_v2 else "v1"].set_selected(True)
```

4d. 配额行（:354-379）改**无条件构建**：删除 `self._rating_v2 = ...`（已上移）与 `if self._rating_v2:` 守卫（`from core.rating_quota import get_quota3_for_skill` 及行内全部代码保留、反缩进一级），行尾追加：

```python
        self._quota_row_widgets = (quota_label, self._cull_quota, self._cull_quota_value_label)
```

（`self._cull_quota = None` 的占位赋值 :355 一并删除；:356-357 的 `_cull_sharp/_cull_nima = None` 占位同删——三者现在都无条件构建。）

4e. 锐度行（:383-402）与美学行（:406-425）末尾各追加：

```python
        self._sharp_row_widgets = (sharp_label, self._cull_sharp, self._cull_sharp_value_label)
```

```python
        self._nima_row_widgets = (nima_label, self._cull_nima, self._cull_nima_value_label)
```

4f. 原「v2 下隐藏旧滑块」块（:427-435 的注释+`if self._rating_v2: ... w.hide()`）整体替换为：

```python
        # 按当前算法应用滑块行可见性 / Apply slider-row visibility per algorithm
        self._apply_algo_visibility()
```

4g. 在 `_select_skill_radio`（:901）之前新增两个方法：

```python
    def _apply_algo_visibility(self) -> None:
        """
        按当前评星算法切换滑块行可见性：v2 显示「3星配额」行，v1 显示
        锐度/美学阈值行（两套控件均常驻构建，只切显示）。

        Toggle slider-row visibility by the current rating algorithm: the
        quota row under v2, the legacy sharpness/aesthetics rows under v1
        (both sets stay constructed; only visibility changes).
        """
        for w in self._quota_row_widgets:
            w.setVisible(self._rating_v2)
        for w in self._sharp_row_widgets + self._nima_row_widgets:
            w.setVisible(not self._rating_v2)

    def _on_algo_selected(self, algo_key: str) -> None:
        """
        评星算法卡片点击回调：立即持久化 rating_algorithm（下次跑批生效），
        刷新卡片选中态并切换滑块行可见性。

        Card click callback: persist rating_algorithm immediately (takes
        effect on the next run), refresh card selection and slider rows.

        参数 / Parameters:
            algo_key (str): 被点击的算法 key（"v1"/"v2"）/ clicked key.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_rating_algorithm(algo_key)
        cfg.save()
        self._rating_v2 = algo_key == "v2"
        for key, card in self._algo_cards.items():
            card.set_selected(key == algo_key)
        self._apply_algo_visibility()
```

兼容性核查（不改动）：`_on_skill_preset_selected` :943 与 `_save_culling` :995 的 `getattr(self, "_rating_v2", False) and self._cull_quota is not None` 守卫在新状态下语义不变（`_cull_quota` 恒存在，`_rating_v2` 跟随卡片实时更新——v1 下预设只填旧阈值、Done 只回写旧阈值，正确）。

- [ ] **Step 5: 跑测试确认通过 + 该文件全量回归**

```bash
.venv/bin/python -m pytest test_settings_center.py -v
```

预期：全部 PASS（含既有测试——`test_skill_preset_fills_thresholds...` 访问的 `_cull_sharp/_cull_nima` 仍存在）。

- [ ] **Step 6: py_compile + JSON 校验 + 提交**

```bash
.venv/bin/python -m py_compile ui/settings_center.py
.venv/bin/python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('locales/zh_CN.json','locales/en_US.json')]"
git add ui/settings_center.py locales/zh_CN.json locales/en_US.json test_settings_center.py
git commit -m "feat(rating-v2): 设置中心精选页评星算法(v1/v2)选择卡片,默认v2"
```

---

### Task 2: 首页快速面板 — 设置中心关闭后同步滑块可见性

**Files:**
- Modify: `ui/main_window.py`（`_create_parameters_section` :3013-3021、`_refresh_param_panel` :3088-3110、新增 `_apply_algo_visibility` 方法）
- Test: `test_main_window_settings_wiring.py`（追加一个测试）

**Interfaces:**
- Consumes: `self.config.rating_algorithm`（advanced_config property，已存在）。
- Produces: `SuperPickyMainWindow._apply_algo_visibility() -> None`、行控件元组 `self._sharp_row_widgets` / `self._nima_row_widgets` / `self._quota_row_widgets`（各为 `(QLabel, QSlider, QLabel)`）；`self._rating_v2_ui` 由启动时一次性赋值变为随 `_apply_algo_visibility()` 刷新。

- [ ] **Step 1: 写失败测试**

在 `test_main_window_settings_wiring.py` 末尾追加：

```python
def test_refresh_param_panel_switches_algo_slider_visibility():
    """
    验证设置中心改完评星算法后,首页 _refresh_param_panel 同步切换两组
    滑块可见性(v1 显示锐度/美学,v2 显示配额)。只改内存配置不落盘。

    Verify _refresh_param_panel swaps the two slider groups after the
    Settings Center changes the rating algorithm (memory-only, no save).
    """
    from ui.main_window import SuperPickyMainWindow

    w = SuperPickyMainWindow()
    cfg = w.config
    original = cfg.config.get("rating_algorithm", "v2")
    try:
        cfg.config["rating_algorithm"] = "v1"
        w._refresh_param_panel()
        assert w.quota_slider.isHidden()
        assert not w.sharp_slider.isHidden() and not w.nima_slider.isHidden()
        assert w._rating_v2_ui is False

        cfg.config["rating_algorithm"] = "v2"
        w._refresh_param_panel()
        assert not w.quota_slider.isHidden()
        assert w.sharp_slider.isHidden() and w.nima_slider.isHidden()
        assert w._rating_v2_ui is True
    finally:
        cfg.config["rating_algorithm"] = original
        w.close()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest test_main_window_settings_wiring.py::test_refresh_param_panel_switches_algo_slider_visibility -v
```

预期：FAIL（`_refresh_param_panel` 后 `quota_slider.isHidden()` 仍为 False——现状不切换）。

- [ ] **Step 3: 实现 `ui/main_window.py`**

3a. `_create_parameters_section` 中（:3013 `sliders_layout.addLayout(quota_layout)` 之后），把原 :3015-3021 的 if/else hide 块整体替换为：

```python
        # V4.6(rating-v2/UI): 行控件存实例引用,供设置中心改算法后运行时切换
        # V4.6 (rating-v2/UI): keep row-widget refs so visibility can be
        # re-applied after the Settings Center changes the algorithm.
        self._sharp_row_widgets = (sharp_label, self.sharp_slider, self.sharp_value)
        self._nima_row_widgets = (nima_label, self.nima_slider, self.nima_value)
        self._quota_row_widgets = (quota_label, self.quota_slider, self.quota_value)
        self._apply_algo_visibility()
```

（原 `self._rating_v2_ui = cfg.rating_algorithm == "v2"` :2994 保留也可删除——`_apply_algo_visibility` 会重新赋值；按「删」处理，避免双份真相。）

3b. 在 `_refresh_param_panel`（:3088）之前新增方法：

```python
    def _apply_algo_visibility(self):
        """
        按 advanced_config.rating_algorithm 切换首页两组滑块可见性：
        v2 显示「3星配额」行，v1 显示锐度/美学行，并同步 _rating_v2_ui。

        Toggle the home quick-panel slider groups by rating_algorithm:
        quota row under v2, legacy rows under v1; refresh _rating_v2_ui.
        """
        self._rating_v2_ui = self.config.rating_algorithm == "v2"
        for w in self._quota_row_widgets:
            w.setVisible(self._rating_v2_ui)
        for w in self._sharp_row_widgets + self._nima_row_widgets:
            w.setVisible(not self._rating_v2_ui)
```

3c. `_refresh_param_panel` 的 `try:` 块末尾（:3108 `self.quota_value.setText(...)` 之后、`finally` 之前）追加：

```python
            # V4.6(rating-v2/UI): 设置中心可能改了评星算法 → 重应用滑块可见性
            # V4.6 (rating-v2/UI): the Settings Center may have switched the
            # rating algorithm — re-apply slider-row visibility.
            if hasattr(self, "_quota_row_widgets"):
                self._apply_algo_visibility()
```

- [ ] **Step 4: 跑测试确认通过 + 两文件全量回归**

```bash
.venv/bin/python -m pytest test_main_window_settings_wiring.py test_settings_center.py -v
```

预期：全部 PASS。

- [ ] **Step 5: py_compile + 提交**

```bash
.venv/bin/python -m py_compile ui/main_window.py
git add ui/main_window.py test_main_window_settings_wiring.py
git commit -m "feat(rating-v2): 首页快速面板随评星算法切换同步滑块可见性"
```

---

### Task 3: 端到端核查 + 收尾

**Files:**
- Modify: 无（只跑验证；如发现问题回到对应 Task 修复）

- [ ] **Step 1: 相关测试全量回归**

```bash
.venv/bin/python -m pytest test_settings_center.py test_main_window_settings_wiring.py test_rating_quota.py test_settings_menu_action_role.py -v
```

预期：全部 PASS。

- [ ] **Step 2: GUI 冒烟（offscreen 全链路）**

```bash
.venv/bin/python - <<'EOF'
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from tools.i18n import get_i18n
from ui.settings_center import SettingsCenter
w = SettingsCenter(get_i18n())
w.show_page("culling")
w._on_algo_selected("v1")
w._on_algo_selected("v2")   # 恢复默认,净效果为零 / restore default, net-zero
w.close()
print("smoke OK")
EOF
```

预期：输出 `smoke OK`，无异常。注意此冒烟会真实写两次用户 `advanced_config.json`（v1 再 v2），净效果为零。

- [ ] **Step 3: 更新评星 V2 主计划的任务清单**

`docs/plans/2026-07-09-rating-v2-quota.md` 任务分解处追加一行（T7 之前）：

```markdown
- [x] T10 评星算法选择 UI(用户提议):设置中心精选页两张卡(V2 批内配额=默认推荐/
      V1 绝对阈值=旧版),点卡即落盘+两处滑块可见性实时切换;
      spec: docs/specs/2026-07-10-rating-algo-selector-design.md
```

- [ ] **Step 4: 提交收尾**

```bash
git add docs/plans/2026-07-09-rating-v2-quota.md docs/plans/2026-07-10-rating-algo-selector.md
git commit -m "docs(rating-v2): T10 算法选择UI计划与主计划勾选"
```
