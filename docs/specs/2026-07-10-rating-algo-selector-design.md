# 评星算法选择卡片（V1/V2）设计 / Rating Algorithm Selector Design

日期：2026-07-10 ｜ 分支：dev ｜ 状态：已获用户批准（方案 B 卡片式）

## 背景 / Background

评星 V2（批内相对+配额，见 `docs/plans/2026-07-09-rating-v2-quota.md`）落地后，
`advanced_config.rating_algorithm`（`"v1"`/`"v2"`，默认 `"v2"`）仅为隐藏回滚开关，
只能手改 json。用户决定将其暴露为设置中心 UI，让用户可自选新旧分星方式，**默认仍为 V2**。

The `rating_algorithm` rollback switch is currently json-only. Expose it in the
Settings Center so users can choose the legacy absolute-threshold rating (v1) or the
batch-relative quota rating (v2, default).

## UI 结构 / UI Structure

- 位置：设置中心精选页（`ui/settings_center.py` `_build_culling_page`），
  「技能等级」区与「阈值」区之间，新增「评星算法」区。
- 组件：复用 `ui/skill_level_dialog.py` 的 `SkillLevelCard`（标题+描述+选中态），横排两张：
  - **V2 · 批内配额（推荐）**：同批照片相对排序，按配额取最好的前 N% 为 3 星。
  - **V1 · 绝对阈值（旧版）**：按固定锐度/美学阈值定星，星级数量不受配额控制。
- 初始选中态读 `cfg.rating_algorithm`；默认值 `"v2"` 不变（`advanced_config.py:55`）。

## 交互 / Interaction

- 点卡片 → `cfg.set_rating_algorithm(...)` 落盘 → 精选页**立刻**切换下方滑块可见性：
  v2 显示「3星配额」滑块，v1 显示锐度/美学滑块。
- 前置改造：精选页配额滑块目前为 `if self._rating_v2:` 条件构建
  （`settings_center.py:358`），改为**无条件构建**、按当前算法 show/hide
  （与首页 `main_window.py:3015-3021` 的做法对齐）。行内控件引用需保存以便切换。
- 技能等级卡逻辑不动：预设本来就同时映射阈值（v1 用）与配额（v2 用），两种算法下都有效。

## 首页同步 / Home Panel Sync

- 首页快速面板两套滑块均已构建（`main_window.py:2986-3021`），仅按启动时算法隐藏一套。
- `main_window._refresh_param_panel`（`main_window.py:3088`）增加：按最新
  `cfg.rating_algorithm` 更新 `self._rating_v2_ui` 并切换两组滑块可见性。
- 需把目前是局部变量的行标签（sharp_label/nima_label/quota_label）存为实例引用
  （如 `self._sharp_row_widgets` / `self._nima_row_widgets` / `self._quota_row_widgets`）。

## 生效方式 / Effectivity

- 切换即写 `advanced_config.json`；下次跑批生效（`photo_processor.py:1167`
  每次运行读 `rating_algorithm`），无需重启。

## i18n

- 新增键（中英各一份）：`settings.culling_algo_section`、
  `settings.culling_algo_v2_title` / `culling_algo_v2_desc`、
  `settings.culling_algo_v1_title` / `culling_algo_v1_desc`。

## 测试 / Testing

- 设置中心单测：点 v1 卡 → 配置落盘为 `"v1"` + 旧滑块可见/配额滑块隐藏；点 v2 卡反向。
- 首页刷新单测：`_refresh_param_panel` 在 v1/v2 下各自的可见性切换。
- 变更文件 `py_compile`。

## 不做 / Out of Scope

- 不改 `rating_algorithm` 默认值、不改 v1/v2 任一评星链路逻辑。
- 不在首页加算法选择入口（只在设置中心）。
- 不加切换弹窗确认（卡片描述已说明语义差异）。
