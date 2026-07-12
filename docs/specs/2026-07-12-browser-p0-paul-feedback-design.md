# 结果浏览器 P0 三项改进（Paul 反馈）设计 / Results Browser P0 Improvements Design

日期：2026-07-12 ｜ 分支：dev ｜ 状态：已获用户批准

## 背景 / Background

外部试用用户 Paul 的书面反馈（2026-07-12），James 评估后定为 P0 的三条：
① 对焦文案左右两侧不一致；② 有识鸟结果时鸟名替换文件名（希望并显），
且右侧详情缺鸟种行；③ 希望键盘直接打星（数字键/上下键）。
P1/P2 遗留项见记忆 paul-feedback-backlog（LR 关键字、平铺模式等，4.6 排期）。

Three P0 items from external tester Paul's feedback: focus-label inconsistency,
species name replacing the filename, and keyboard star rating.

## 1. 对焦文案统一 / Consistent Focus Labels

- 现状：筛选面板（`ui/filter_panel.py` `_FOCUS_OPTIONS`）英文直接显示枚举
  mode（BEST/GOOD/BAD），中文为硬编码「精焦/合焦/失焦」；右侧详情
  （`ui/detail_panel.py`）走 `browser.focus_state_*`（Critical Focus /
  Good Focus / Soft / Out of Focus）。
- 改法：筛选面板 checkbox 文案复用 `browser.focus_state_best/good/bad`
  三键（BAD 桶合并 BAD+WORST，沿用「Soft/失焦」概括）。删除
  `_FOCUS_OPTIONS` 元组中的硬编码中文 label 字段，全部经 i18n 取值。
- 结果：英文左右两侧同词（Critical Focus / Good Focus / Soft）；中文不变。

## 2. 鸟名与文件名并显 / Species + Filename Together

- **缩略图标签**（`ui/thumbnail_grid.py` 约 :30-33 的标签取值函数）：
  有鸟名时改为两行——第一行鸟名（现有样式），第二行文件名（小一号、
  灰色 `text_tertiary`）；无鸟名时维持单行文件名。瓦片高度随之微调。
- **右侧详情面板**（`ui/detail_panel.py` rows 定义 :450 一带）：在
  `browser.meta_gbif_rarity` 行上方插入鸟种行，复用已存在的
  `self._val_species` 控件（保留其点击复制鸟名行为）；新增 i18n 键
  `browser.meta_species`（中：鸟种 / 英：Species）；更新「鸟种、文件名
  不在此显示」的过时注释。
- 全屏顶条已同时显示鸟名（居中）与文件名，不动。

## 3. 键盘打星 / Keyboard Star Rating

- 入口：`ui/results_browser_window.py` `keyPressEvent`（:1798，网格与
  全屏共用）；`ui/fullscreen_viewer.py` `keyPressEvent`（:1359）中
  Up/Down 的翻图绑定同步移除。
- **数字键 0/1/2/3**：给当前照片（网格当前选中项 / 全屏当前图）设星。
- **Up/Down**：由翻图改为星级 +1/−1（用户拍板采纳 Paul 提议；Left/Right
  翻图保留）。星级钳制在 0-3：-1★（无鸟）照片允许用数字键/加星救回，
  减星到 0 为止、不会降到 -1。星级无变化时（如 3★ 再加星）不触发写入。
- 复用现有改星链路（`results_browser_window.py` :1380 一带：内存池与
  DB 更新 + 缩略图角标刷新 + EXIF 异步 `set_rating_and_pick` +
  `rating_mover` 后台移动文件），只新增键盘入口，不新建逻辑。
- 对比（compare）模式已有数字键打星，不动。

## 测试 / Testing

- filter_panel：英文语言下三个对焦 checkbox 文本等于对应
  `browser.focus_state_*` 值（offscreen）。
- thumbnail_grid：标签取值函数在有鸟名时返回值同时含鸟名与文件名。
- detail_panel：rows 中存在 `browser.meta_species` 行且位于
  `browser.meta_gbif_rarity` 之前。
- 键盘：构造浏览器窗口（offscreen），模拟 Key_2/Key_Up/Key_Down 断言
  当前照片 rating 按预期变化且钳制边界正确。
- 变更 py 文件 `py_compile`；提交 dev。

## 不做 / Out of Scope

- 颜色标签自定义、LR 关键字、平铺模式、连拍元数据（P1/P2 另行立项）。
- 对比模式与快捷键帮助页的其他键位调整。
