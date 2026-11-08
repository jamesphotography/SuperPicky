# 设置中心整合为 4 页 + 脱焦封顶（#158）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** V2 评星把「确认脱焦」的照片封顶 2★（判定线可调，默认 520）；设置中心从 7 页整合为 常用 / 高级 / 实验室 / 关于 4 页，视频移入实验室。

**Architecture:** A 部分（任务 1–2）只动纯逻辑层：`advanced_config` 新字段 → `photo_processor` 读它做仲裁 → `rating_quota.assign_ratings` 封顶 → `rating_caption` 解释。B 部分（任务 3–7）先把 `SettingsCenter` 各页的构建代码拆成「往给定 layout 里加一个小节」的函数（行为不变），再用这些小节重新拼出 4 页。控件仍是 `self._xxx` 属性、仍读写原字段，所以 `done()` 的兜底保存与 `_rebuild_pages()` 不需要改逻辑，只改页键。

**Tech Stack:** Python 3.13（系统解释器，勿用 `.venv`）、PySide6、pytest。

**Spec:** `docs/specs/2026-11-08-settings-consolidation-design.md`

## Global Constraints

- 所有设置项搬家后**读写原 `advanced_config` 字段**，字段名、默认值、setter clamp 范围都不改（唯一新增字段见任务 1，唯一删除字段见任务 6）。
- 新字段 setter 的 clamp 范围必须与 UI 控件范围一致：`v2_focus_arbitration_sharpness` 为 300–800，步长 10。
- 三语（`locales/zh_CN.json`、`zh_TW.json`、`en_US.json`）同步，不写死任何界面文字。
- 注释：中文 + 英文对照；函数用 docstring（参数 / 返回）。
- 文件读写一律 UTF-8；不要用 sed/awk 改含中文的文件。
- 每个改过的 Python 文件跑 `python3 -m py_compile <file>`。
- 测试在仓库根目录运行 `python3 -m pytest`；一次只跑一轮，不并行。如果卡住，先清理 `ppid=1` 的孤儿 exiftool 进程。
- 开关样式统一用 `self._checkbox_qss()`（内部是 `checkbox_indicator_qss`）。
- 每个任务一个提交，提交说明结尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。**不要 push。**

## Review Focus

1. 「恢复默认」后 `_rebuild_pages()` 重建的是 4 个新页；关闭时的兜底保存不会把旧值写回。由任务 4 的测试覆盖。
2. 跨页联动在页面拆开后仍然生效：常用页的连拍开关控制高级页连拍速度的可用状态；常用页的档位联动高级页的配额条或 V1 滑块。由任务 4 的测试覆盖。
3. 视频开关关闭时，子选项隐藏，但值保留；重新打开后显示原值。由任务 6 的测试覆盖。
4. 还有调用方在用旧页键（`"culling"` / `"birdid"` / `"output"` / `"video"` / `"apps"`）打开设置中心时，要落到正确的新页，不能停在第一页不动。由任务 4 的测试覆盖。
5. V1 评星下，判定线控件存在但不影响 V1 结果。V1 仲裁仍跟随锐度滑块。由任务 1 的测试覆盖。

---

## A 部分：#158 脱焦封顶

### 任务 1：配置字段 `v2_focus_arbitration_sharpness` 接入仲裁

**Files:**
- Modify: `advanced_config.py`（`DEFAULT_CONFIG` 第 140–250 行附近；属性/setter 放在 `rescue_scan_enabled` 那组旁边，约 573–628 行）
- Modify: `core/photo_processor.py:2966-2969`（`_arb_thr`）
- Test: `tests/test_rating_quota.py`（改写约 299 行的 `test_v2_focus_arbitration_threshold_matches_field_review`）

**Interfaces:**
- Produces: `AdvancedConfig.v2_focus_arbitration_sharpness -> float`（property）；`AdvancedConfig.set_v2_focus_arbitration_sharpness(value: float) -> None`（clamp 300–800）。`DEFAULT_CONFIG["v2_focus_arbitration_sharpness"]` 的值引用 `core.rating_quota.FOCUS_ARBITRATION_SHARPNESS`（520.0），不要再写一个字面量 520。

- [ ] **步骤 1：写失败测试**（追加到 `tests/test_rating_quota.py`；保留原测试，只把其中 `thr` 改为读默认配置）

```python
def test_v2_focus_arbitration_default_and_clamp(tmp_path):
    from advanced_config import AdvancedConfig
    from core.rating_quota import FOCUS_ARBITRATION_SHARPNESS
    cfg = AdvancedConfig(config_file=str(tmp_path / "c.json"))
    assert cfg.v2_focus_arbitration_sharpness == FOCUS_ARBITRATION_SHARPNESS == 520.0
    cfg.set_v2_focus_arbitration_sharpness(100)
    assert cfg.v2_focus_arbitration_sharpness == 300.0
    cfg.set_v2_focus_arbitration_sharpness(5000)
    assert cfg.v2_focus_arbitration_sharpness == 800.0
    cfg.set_v2_focus_arbitration_sharpness(450)
    assert cfg.v2_focus_arbitration_sharpness == 450.0
    assert "v2_focus_arbitration_sharpness" not in AdvancedConfig.PRESERVE_ON_RESET
```

- [ ] **步骤 2：运行，确认失败**：`python3 -m pytest tests/test_rating_quota.py -k arbitration -v`，预期出现 AttributeError。
- [ ] **步骤 3：实现字段、property 和 setter**（setter 写成 `max(300.0, min(800.0, float(value)))`）。然后把 `photo_processor.py` 中 V2 分支的 `V2_FOCUS_ARBITRATION_SHARPNESS` 换成 `float(self.config.v2_focus_arbitration_sharpness)`。V1 分支保持 `self.settings.sharpness_threshold` 不变。如果 1604 行那个 import 已无其他引用，一并删除。
- [ ] **步骤 4：再写一个测试**：用 monkeypatch 把配置设成 450，跑一张归一化锐度 484、对焦 WORST 的照片，断言结果被仲裁为合焦（权重 0.9）。如果 `photo_processor` 很难单独驱动，退一步：断言 `photo_processor.py` 源码中 V2 分支读的是 `v2_focus_arbitration_sharpness`（源码字符串检查，可接受），再加上 `arbitrate_focus_weights((0.5, 0.8), 484.0, 450.0)` 返回 arbitrated=True。
- [ ] **步骤 5：运行测试并编译检查**：`python3 -m pytest tests/test_rating_quota.py -v` 全部通过；`python3 -m py_compile advanced_config.py core/photo_processor.py`。
- [ ] **步骤 6：提交** `feat(rating): V2 脱焦判定锐度线可配置（默认 520）`

### 任务 2：WORST 封顶 2★ 与题注

**Files:**
- Modify: `core/rating_quota.py:305-310`（`assign_ratings` 循环中眼睛封顶之后）
- Modify: `core/rating_caption.py:41-58, 230-232`
- Modify: 三个语言包的 `rating_v2` 段（`eye_capped` 旁）和 `caption` 段（`why_eye_capped` 旁）
- Test: `tests/test_rating_quota.py`、`tests/test_rating_caption.py`

**Interfaces:**
- Produces: reason key `"rating_v2.focus_capped"` → outcome `"focus_capped"`（加入 `_OUTCOME_BY_REASON` 与 `RANKED_OUTCOMES`）；文案键 `rating_v2.focus_capped`、`caption.why_focus_capped`（参数 `{place}`）。

**文案（定稿）：**

| 键 | zh_CN | zh_TW | en_US |
|---|---|---|---|
| `rating_v2.focus_capped` | 良好（对焦点在鸟外，封顶2星） | 良好（對焦點在鳥外，封頂2星） | Good (focus point off the bird, capped at 2 stars) |
| `caption.why_focus_capped` | 为什么不是 3★：{place}，名次够 3★，但相机对焦点在鸟外、鸟头也不够清晰，最多给 2★ | 為什麼不是 3★：{place}，名次夠 3★，但相機對焦點在鳥外、鳥頭也不夠清晰，最多給 2★ | Why not 3★: {place}, ranked high enough for 3★, but the camera's focus point was off the bird and the head isn't sharp enough to overrule it, so 2★ at most |

- [ ] **步骤 1：写失败测试**（`tests/test_rating_quota.py`，复用 `_metrics`）

```python
def test_worst_focus_capped_at_two_stars_without_backfill():
    from core.rating_quota import assign_ratings
    photos = [_metrics(key="a", norm_sharpness=484.0, focus_status="WORST", is_flying=True),
              _metrics(key="b", norm_sharpness=400.0, focus_status="GOOD"),
              _metrics(key="c", norm_sharpness=350.0, focus_status="GOOD")]
    r = assign_ratings(photos, quota3=33, quota2=33, species_floor=False)
    assert r["a"].rating == 2 and r["a"].reason_key == "rating_v2.focus_capped"
    assert r["b"].rating == 2 and r["b"].reason_key == "rating_v2.mid_quota"   # 不递补 / no backfill
    assert r["c"].rating == 1

def test_bad_focus_not_capped():
    from core.rating_quota import assign_ratings
    r = assign_ratings([_metrics(key="a", focus_status="BAD")], quota3=100, quota2=0, species_floor=False)
    assert r["a"].rating == 3

def test_focus_cap_keeps_species_floor():
    from core.rating_quota import assign_ratings
    r = assign_ratings([_metrics(key="a", focus_status="WORST", species="X", species_confirmed=True)],
                       quota3=100, quota2=0)
    assert r["a"].rating == 2
```

（quota3=33 → 3★ 名额 `ceil(3×0.33)=1`，只给排第一的 a；a 被封顶后名额不顺延，b 仍是 2★ 中段。）

- [ ] **步骤 2：写题注测试**（`tests/test_rating_caption.py`）：在 `test_caps_explain_why_not_three_stars` 中追加：

```python
    foc = build_caption(_ranked(rating=2, outcome="focus_capped", focus_status="WORST", focus_weight=0.5), ZH)
    assert "名次够 3★，但相机对焦点在鸟外、鸟头也不够清晰，最多给 2★" in foc.split("\n")[1]
    assert outcome_from_reason_key("rating_v2.focus_capped") == "focus_capped"
```

- [ ] **步骤 3：运行，确认失败。**
- [ ] **步骤 4：实现**：在眼睛封顶之后加一条判断：`if p.focus_status == "WORST" and star > 2:` 降到 2★，并设置 `reason_key`。题注中 `o == "focus_capped"` 返回 `t("caption.why_focus_capped", place=place)`，分支放在 `eye_capped` 之后。补齐三语文案。更新 `rating_quota.py` 模块 docstring 中「配额定星」那一段，补一句「对焦点在鸟外（仲裁后仍为 WORST）→ 封顶 2★」。
- [ ] **步骤 5：运行** `python3 -m pytest tests/test_rating_quota.py tests/test_rating_caption.py -v`，全部通过；对改过的两个文件跑 py_compile。另外检查三个语言包的 JSON 能正常解析：`python3 -c "import json;[json.load(open(f'locales/{l}.json',encoding='utf-8')) for l in ('zh_CN','zh_TW','en_US')]"`。
- [ ] **步骤 6：提交** `fix(rating): V2 确认脱焦的照片最多 2★（#158）`

> A 部分完成后暂停，向用户汇报。**回复 #158 必须先由用户过目回复稿**（对外操作）。回复稿要点：原因（边缘反差让糊片得高分）、本次修复（脱焦封顶）、请 Al 提供原 RAW 文件作为非 Z 9 样本。

---

## B 部分：设置中心 4 页

### 任务 3：把各页拆成小节函数（只重构，行为不变）

**Files:**
- Modify: `ui/settings_center.py`（`_build_culling_page` 298 行、`_build_birdid_page` 632 行、`_build_output_page` 1761 行、`_build_video_page` 2500 行、`_build_apps_page` 2729 行）

**Interfaces:**
- Produces（全部是 `SettingsCenter` 的方法，签名统一为 `(self, lay: QVBoxLayout, cfg) -> None`，把控件加进 `lay`，控件仍赋值给原来的 `self._xxx` 属性）：
  - `_sec_skill`（摄影水平），`_sec_rating`（V1 回退开关 + 配额条 / V1 滑块 + 算法可见性），`_sec_detect_basic`（飞鸟、连拍），`_sec_detect_extra`（连拍速度、补救扫描），`_sec_ai_confidence`，`_build_format_filter_section`（已有）
  - `_sec_birdid_basic`（自动识鸟、国家/地区、鸟名显示格式），`_sec_birdid_extra`（识别置信度、数据源、英文命名标准、自定义罕见指数）
  - `_sec_output_basic`（分类目录布局、完成提示音、删除前确认），`_sec_output_metadata`（写入方式、专有 RAW、写入字段、连拍子文件夹、0 星补详情数值），`_sec_storage`（保留预览、清理缓存）
  - `_sec_video`（视频总开关 + 全部子选项），`_sec_apps`（应用列表 + 关闭主窗口时）
  - `_scroll_page(fill: Callable[[QVBoxLayout], None]) -> QWidget`：统一的 QScrollArea 包装（含 macOS viewport 透明样式、`_PAGE_BODY_OBJECT_NAME`、边距 24/20、末尾 `addStretch`），取代各页里重复的包装代码。
- 旧的 `_build_*_page` 暂时保留，改为 `_scroll_page` 加上按原顺序调用各小节，界面效果与改动前一致。

- [ ] **步骤 1：跑基线**：`python3 -m pytest tests/test_settings_center.py tests/test_settings_labs_page.py tests/test_restore_defaults.py tests/test_format_filter.py tests/test_raw_embed_sub_option.py tests/test_metadata_output_fields.py tests/test_country_shortlist.py tests/test_birdid_country_more_stuck.py tests/test_settings_birdid_engine.py tests/test_settings_center_combo_dark.py -q`，记录通过数。
- [ ] **步骤 2：拆分**。小节标题、分隔线跟随小节走。原精选页的「高级选项」折叠按钮在此任务里仍包住 `_sec_ai_confidence`。
- [ ] **步骤 3：重跑步骤 1 的命令**，通过数必须与基线一致，失败数为 0；对改过的文件跑 py_compile；用 PyCharm `get_file_problems` 检查 `ui/settings_center.py`，确认没有未定义名。
- [ ] **步骤 4：提交** `refactor(settings): 设置页拆为可复用小节（行为不变）`

### 任务 4：导航改为 4 页并重新拼装

**Files:**
- Modify: `ui/settings_center.py`（`PAGE_ORDER` 130 行、`_PAGE_ICON`、`_PAGE_TITLE_KEY`、`__init__` 默认 `start_page`、`_build_page`、`show_page`、`_rebuild_pages`、`done()`；删除 `_build_language_picker` 和 `_on_language_changed`；删除已无用的旧 `_build_*_page` 以及 `_toggle_culling_advanced`）
- Modify: `ui/main_window.py:1236, 1894, 3139, 3191, 3567`（调用改为新页键）
- Modify: 三个语言包（新增导航和小节标题）
- Test: `tests/test_settings_center.py`（新增）；`tests/test_language_picker.py`（删除只测设置中心语言选择器的用例，菜单切换语言的用例保留）；其他引用旧页键的测试同步修改

**Interfaces:**
- Consumes: 任务 3 的小节函数。
- Produces: `PAGE_ORDER = ["common", "advanced", "labs", "about"]`；`_LEGACY_PAGE_KEY = {"culling": "common", "birdid": "common", "output": "common", "video": "labs", "apps": "advanced"}`，由 `show_page` 先查这张表做映射；`_collapsible(lay: QVBoxLayout, title: str, expanded: bool) -> QVBoxLayout`，返回小节内容所用的 layout。

**页面组成**（顺序即显示顺序）：
- common：小节「选片」= `_sec_skill` + `_sec_detect_basic`；「识鸟」= `_sec_birdid_basic`；「整理与提示」= `_sec_output_basic`
- advanced：可折叠小节「评星」（默认展开）= `_sec_rating` + `_sec_ai_confidence` + `_sec_detect_extra` + `_build_format_filter_section`；「识鸟」= `_sec_birdid_extra`；「输出与元数据」= `_sec_output_metadata`；「外部应用」= `_sec_apps`；「存储」= `_sec_storage`。除「评星」外默认收起。
- labs：现有内容不变（任务 6 再加入视频）
- about：不变
- 图标：common=`gem.svg`，advanced=`layout-grid.svg`，labs 和 about 不变。

**文案：**

| 键 | zh_CN | zh_TW | en_US |
|---|---|---|---|
| `settings.nav_common` | 常用 | 常用 | Common |
| `settings.nav_advanced` | 高级 | 進階 | Advanced |
| `settings.sec_culling` | 选片 | 選片 | Culling |
| `settings.sec_birdid` | 识鸟 | 識鳥 | Bird ID |
| `settings.sec_organize` | 整理与提示 | 整理與提示 | Organizing & alerts |
| `settings.sec_rating` | 评星 | 評星 | Rating |
| `settings.sec_output` | 输出与元数据 | 輸出與中繼資料 | Output & metadata |
| `settings.sec_apps` | 外部应用 | 外部應用程式 | External apps |
| `settings.sec_storage` | 存储 | 儲存空間 | Storage |

不再使用的 `settings.nav_culling/birdid/output/video/apps` 键从三语中删除（删除前先 grep 确认无其他引用）。

- [ ] **步骤 1：写失败测试**（`tests/test_settings_center.py`）：
  - `PAGE_ORDER == ["common", "advanced", "labs", "about"]`，导航有 4 项。
  - 每个旧页键经 `show_page` 打开后，`_stack.currentIndex()` 等于映射目标的索引。
  - 构建后关键控件都存在且只存在一份：`_cull_flight`、`_cull_burst`、`_cull_burst_fps`、`_cull_rescue`、`_cull_ai`、`_bid_auto`、`_bid_conf`、`_bid_country`、`_folder_layout_combo`、`_raw_embed`、`_keep_temp_files`、`_completion_sound`、`_delete_confirm`、`_apps_list`、`_close_action_combo`。
  - 跨页联动：取消勾选 `_cull_burst` 后 `_burst_fps_row_widgets` 中每个控件 `isEnabled()` 为 False。
  - 恢复默认：先改 `_completion_sound`，调用 `_rebuild_pages("about")`，再对配置调用 `restore_defaults` 和 `done(0)`，断言配置为默认值、当前页是 about、导航仍为 4 项（参照 `tests/test_restore_defaults.py` 的现有写法）。
  - 设置中心里已没有 `_lang_combo` 属性。
- [ ] **步骤 2：运行，确认失败。**
- [ ] **步骤 3：实现**：页面组装、`_collapsible`（按钮样式复用原精选页「高级选项」那个扁平按钮 QSS，▸/▾ 前缀）、`show_page` 映射、`__init__` 默认值改为 `"common"`、`done()` 的 `hasattr` 判据保持不变（控件属性名没变）、删除语言选择器，以及 `main_window` 中 5 处调用改为新页键（`"culling"`/`"birdid"` → `"common"`）。
- [ ] **步骤 4：运行**任务 3 步骤 1 的那组测试，加上 `tests/test_language_picker.py`、`tests/test_main_window_queue.py`、`tests/test_main_window_launch_worker.py`、`tests/test_about_version_check.py`、`tests/test_geo_config_migration.py`、`tests/test_birdid_lr_keywords.py`，全部通过；py_compile；跑 JSON 解析检查。
- [ ] **步骤 5：提交** `feat(settings): 设置中心整合为 常用/高级/实验室/关于 四页`

### 任务 5：高级页「评星」小节加入「脱焦判定锐度线」

**Files:**
- Modify: `ui/settings_center.py`（`_sec_rating` 内，配额条之后）
- Modify: 三个语言包
- Test: `tests/test_settings_center.py`

**Interfaces:**
- Consumes: 任务 1 的 `v2_focus_arbitration_sharpness` / `set_v2_focus_arbitration_sharpness`。
- Produces: 控件 `self._focus_arb_spin`（QSpinBox，范围 300–800，步长 10）。值变化时立即写入配置并 `save()`；`_save_culling()` 里加一行兜底保存。V1 模式下控件置灰（`setEnabled(False)`），由 `_apply_algo_visibility` 统一切换。

**文案：**

| 键 | zh_CN | zh_TW | en_US |
|---|---|---|---|
| `settings.focus_arb_label` | 脱焦判定锐度线 | 脫焦判定銳利度線 | Out-of-focus sharpness line |
| `settings.focus_arb_hint` | 相机记录的对焦点在鸟外时：鸟头锐度达到此值，视为合焦、不扣分；不到此值，视为脱焦，最多 2★。默认 520，仅 V2 评星生效。 | 相機記錄的對焦點在鳥外時：鳥頭銳利度達到此值，視為合焦、不扣分；不到此值，視為脫焦，最多 2★。預設 520，僅 V2 評星生效。 | When the camera's focus point is off the bird: a head sharpness at or above this line counts as in focus (no penalty); below it the shot counts as out of focus and gets 2★ at most. Default 520; V2 rating only. |

- [ ] **步骤 1：写失败测试**：`_focus_arb_spin` 的 minimum 和 maximum 分别等于 300 和 800，singleStep 为 10，初值等于配置值；`setValue(450)` 后配置为 450.0；切换到 V1 后 `isEnabled()` 为 False。
- [ ] **步骤 2：运行，确认失败。**
- [ ] **步骤 3：实现。**
- [ ] **步骤 4：运行** `tests/test_settings_center.py` 并跑 py_compile，全部通过。
- [ ] **步骤 5：提交** `feat(settings): 高级页可调脱焦判定锐度线`

### 任务 6：视频移入实验室，删除首次视频提示

**Files:**
- Modify: `ui/settings_center.py`（labs 页在「多目录选鸟」之后调用 `_sec_video`；`_sec_video` 改用实验室页同款 `_toggle` 开关样式；子选项放进一个容器 QWidget，`setVisible(self._video_auto_check.isChecked())`，随开关切换显示/隐藏）
- Modify: `ui/main_window.py`（删除 `_maybe_prompt_video_first_run`（约 4684–4733 行）及 3199、3573 两处调用）
- Modify: `advanced_config.py`（从 `DEFAULT_CONFIG`（约 248 行）和 `PRESERVE_ON_RESET`（约 374 行）中删除 `video_first_run_prompted`，并更新 238–242 行附近的注释）
- Modify: 三个语言包：`logs.video_skipped_disabled` 改为指向实验室；先 grep 确认无其他引用，再删除 `dialogs.video_first_tip_title` 和 `dialogs.video_first_tip_body`
- Modify: `ui/settings_center.py:118-130` 关于 4.6 视频页的注释，改为说明现状
- Test: `tests/test_settings_labs_page.py`；以及用 grep 找出的所有测 `video_first_run` 的测试（删除或改写）

**文案：**

| 键 | zh_CN | zh_TW | en_US |
|---|---|---|---|
| `logs.video_skipped_disabled` | ⏭ 检测到 {count} 个视频，但视频处理未开启（可在「设置 → 实验室」中开启） | ⏭ 偵測到 {count} 個影片，但影片處理未開啟（可在「設定 → 實驗室」中開啟） | ⏭ Found {count} videos, but video processing is off (enable it in Settings → Labs) |
| `settings.labs_video_desc` | 选鸟时一并分析目录里的视频，按鸟种归类。仍不稳定。 | 選鳥時一併分析目錄裡的影片，依鳥種歸類。仍不穩定。 | Analyze videos in the folder while culling and sort them by species. Still unstable. |

（zh_TW、en_US 现有译文措辞如与上表不同，以现有术语为准，只改「入口位置」那部分。）

- [ ] **步骤 1：写失败测试**（`tests/test_settings_labs_page.py`）：
  - 实验室页里有 `_video_auto_check`，初值等于 `cfg.video_auto_process_in_main`，分别在 True 和 False 两种初值下验证。
  - 关闭时子选项容器 `isHidden()`；打开后可见。关闭再打开后，`_video_max_frames.value()` 保持原值。
  - 勾选后配置 `video_auto_process_in_main` 为 True。
  - `"video_first_run_prompted" not in AdvancedConfig.DEFAULT_CONFIG`；`MainWindow` 已没有 `_maybe_prompt_video_first_run` 属性。
- [ ] **步骤 2：运行，确认失败。**
- [ ] **步骤 3：实现。**
- [ ] **步骤 4：运行** `tests/test_settings_labs_page.py tests/test_labs_config.py tests/test_restore_defaults.py tests/test_main_window_queue.py tests/test_main_window_launch_worker.py`，再加上 `grep -rln video_first tests` 找到的文件，全部通过；py_compile；跑 JSON 解析检查。
- [ ] **步骤 5：提交** `feat(labs): 视频处理移入实验室；删除首次视频提示`

### 任务 7：文档、全量测试与真实窗口检查

**Files:**
- Modify: `CLAUDE.md`「设置中心架构」一节：7 页改为 4 页（常用/高级/实验室/关于）、`PAGE_ORDER` 新值、旧页键映射 `_LEGACY_PAGE_KEY`、语言只在菜单、高级页按小节折叠、视频开关复用 `video_auto_process_in_main` 是 `lab_*` 约定的例外（理由：已有字段守卫主流程，再加一个开关需迁移且两字段须同判）。
- Modify: `docs/specs/2026-11-08-settings-consolidation-design.md` 状态改为「已实施」。

- [ ] **步骤 1：更新文档。**
- [ ] **步骤 2：全量测试**：`python3 -m pytest -q > <scratchpad>/full.txt 2>&1`（不要用管道），预期 0 失败，通过数约为 1666 加上新增用例。
- [ ] **步骤 3：在 macOS 真实窗口（cocoa，不加 offscreen）下运行** `python3 main.py`，打开设置中心，逐页截图：常用、高级（全部展开）、实验室（视频开和关）、关于。检查控件没有重叠、高级页能滚动、窗口没有被撑大。截图存到 scratchpad，交给用户过目。
- [ ] **步骤 4：提交** `docs: CLAUDE.md 设置中心改为四页`
- [ ] **步骤 5：向用户汇报**。说明 Windows 实机排版检查尚未做，需要用户在 Windows 机器上看一遍。
