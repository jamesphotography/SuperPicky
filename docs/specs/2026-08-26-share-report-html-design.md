# 可分享总结报告 HTML 设计 / Shareable Summary Report HTML Design

日期 / Date: 2026-08-26
状态 / Status: 设计已定稿，待实施 / Design approved, pending implementation
相关 / Related: `tools/report_db.py:27`（`PHOTO_COLUMNS` 数据源）、
`ui/results_browser_window.py:971`（工具栏，新增入口）、
`ui/results_browser_window.py:1264`（`_resolve_photo_paths`，上游路径解析）、
`ui/thumbnail_grid.py:205`（`_thumbnail_candidates`，预览图优先级先例）、
`ui/main_window.py:3376`（`_show_statistics_report`，现有统计报告，不被替代）、
`core/rarity_tier.py`（罕见度分级配色，复用）、
`advanced_config.py:547`（`keep_temp_files`，预览缓存开关）

---

## 1. 问题与目标 / Problem & Goal

SuperPicky 处理完一批照片后产出两样东西：主窗口日志里的一段统计报告
（`ui/main_window.py:3376`，Qt 富文本，只活在窗口里），以及选鸟浏览器里可交互的
照片网格。二者都**无法离开软件**——用户想把「这次拍到了什么」发给鸟友、发到群里，
或者半年后自己回顾，都没有载体。

目标：在选鸟浏览器里增加一个导出入口，把 `report.db` 里已有的数据聚合成**一个
自包含的 HTML 文件**，保存到选鸟目录，可直接分享给他人，也可作为自己的复盘存档。

SuperPicky currently produces a Qt rich-text summary in the main window log and an
interactive grid in the results browser. Neither can leave the application. This
design adds an export entry to the results browser that aggregates the existing
`report.db` data into a single self-contained HTML file written to the picking
directory — shareable as-is, and usable as a personal archive.

**非目标 / Non-goals**（明确不做，见第 10 节）：不做在线发布、不做 PDF 导出、
不做 CLI 入口、不引入模板引擎、不改动现有统计报告。

---

## 2. 调研事实 / Research Findings（全部为本机核实）

### 2.1 数据源盘点 / Available data

`report.db` 的 `photos` 表（`tools/report_db.py:27`，schema v9，40+ 列）已经存有
生成一份丰富报告所需的全部字段：

| 类别 | 列 |
|---|---|
| 评价 | `rating`、`picked`、`adj_sharpness`、`adj_topiq`、`focus_status`、`is_flying` |
| 鸟种 | `bird_species_cn`、`bird_species_en`、`birdid_confidence` |
| 鸟种附加数据 | `rarity_index`、`iucn_category`、`gbif_rarity_100`、`aesthetic_index` |
| 相机 | `iso`、`shutter_speed`、`aperture`、`focal_length`、`focal_length_35mm`、`camera_model`、`lens_model` |
| 地点 | `gps_latitude`、`gps_longitude`、`gps_altitude`、`city`、`state_province`、`country` |
| 时间 | `date_time_original` |
| 路径 | `original_path`、`current_path`、`temp_jpeg_path`（均为**相对路径**） |
| 连拍 | `burst_id`、`burst_position` |

### 2.2 关键发现：处理耗时从未落库 / Processing duration is never persisted

`meta` 表（`tools/report_db.py:197`）只写入两个键：`schema_version` 与
`directory_path`（`tools/report_db.py:225-233`）。处理耗时、起止墙钟时间只存在于
处理线程的内存 `stats` 字典中，传给 `_show_statistics_report()` 后即丢弃。

**结论**：从选鸟浏览器导出时**无法获得处理耗时**。本设计**不为此改动
`core/photo_processor.py` 落库**，而是改用**拍摄时段**（由 `date_time_original`
的 min/max 聚合）——对读者而言「06:12–09:47 的晨拍」远比「AI 跑了 8 分钟」有意义。

由此得到一个更好的性质：**报告完全由 `report.db` 驱动，不依赖任何处理时的运行态**，
因此任何历史目录（含旧版本处理的）今天都能导出完整报告，且结果可重放。

### 2.3 预览图链路 / Preview image sources

`ui/thumbnail_grid.py:205` 的 `_thumbnail_candidates()` 已确立优先级：
`temp_jpeg_path`（RAW→JPEG 预览，位于 `.superpicky/cache/`）→ 由 `current_path` /
`original_path` 推导的同名 JPG 边车。本设计复用同一优先级。

`temp_jpeg_path` 的存在取决于 `keep_temp_files`（`advanced_config.py:547`，默认
`True`）。用户关闭该开关后缓存被清理，纯 RAW 目录将**没有任何可用预览**——这是
本功能最可能发生的失败，处理方式见第 7 节。

### 2.4 依赖现状 / Dependencies

`requirements_base.txt` 已含 `Pillow>=9.0.0`、`opencv-python`、`numpy`。
**无 Jinja2**，本设计也不引入：为单一页面付出 PyInstaller hidden import 与打包
体积的代价不划算，纯 Python 字符串拼接足够。

---

## 3. 已定决策 / Settled Decisions

| # | 决策 | 理由 |
|---|---|---|
| D1 | **战报为主 + 明细可折叠** | 一份文件两用：上半给别人看，下半折叠区给自己复盘 |
| D2 | **单文件自包含**（图片 base64 内嵌） | 拖进微信/邮件即可发送，对方双击即开，永不丢图。代价约 19MB |
| D3 | **地点默认只到城市级**，导出弹窗可勾选「包含精确 GPS 坐标」（默认不勾） | 珍稀鸟点位泄露在观鸟圈是真实风险；eBird 对敏感鸟种同样强制模糊坐标 |
| D4 | **入口在选鸟浏览器工具栏，导出当前载入的全量**，不受筛选面板影响 | 统计口径必须是「这次拍的全部」，否则命中率会变成 62/62=100% 的无意义数字 |
| D5 | 输出到 `<选鸟目录>/SuperPicky报告_<目录名>_<YYYY-MM-DD>.html`（英文界面为 `SuperPicky-Report-<目录名>-<YYYY-MM-DD>.html`，**文件名与 D7 同样跟随界面语言**），**根目录非隐藏目录**；同名加 `_2` 后缀不覆盖 | 它就是要被找到并发出去的 |
| D6 | **绝对路径一律不出现在报告中**，只显示文件名 | `/Users/<用户名>/...` 属隐私，与 GPS 同理 |
| D7 | **报告语言跟随导出时的界面语言**（`zh_CN`/`en_US`），不做单独语言选择 | 复用现有 i18n，无新增决策点 |

---

## 4. 架构与数据流 / Architecture & Data Flow

```
ui/results_browser_window.py            （只管交互，依赖 Qt）
  └─ [导出报告] 按钮 → ReportExportDialog（GPS 勾选 + 体积预估 + 确认）
       └─ QThread ─────────────────────────────────┐
                                                    │ 进度回调
  core/report_export.py                （无 Qt 依赖，纯函数）
    aggregate(photos, options) -> ReportData
    encode_preview(path, max_edge, quality) -> str
    build_html(report_data, options) -> str
       └─ 复用 core/rarity_tier.py 的 tier_name_color
```

### 4.1 为什么生成器不依赖 Qt / Why the generator is Qt-free

- **可测性**：三个函数可用字典直接喂、断言输出，无需 `QApplication`。
  项目历史教训：构造 `MainWindow` 的测试会切换全局 i18n 语言，导致本地化断言假失败；
  另有测试直接写用户真实 `advanced_config.json` 污染本机设置。生成器不碰 Qt、
  不碰全局配置，天然绕开这两类问题。
- **可复用**：未来 `superpicky_cli.py` 若需 `--report` 参数可直接调用。
- **EXIF 旋转一致性**：Pillow 的 `ImageOps.exif_transpose()` 与
  `QImageReader.setAutoTransform(True)`（`ui/thumbnail_grid.py:250`）行为等价，
  不需要为此绑定 Qt。

### 4.2 职责边界 / Responsibility boundaries

| 层 | 输入 | 输出 |
|---|---|---|
| `aggregate()` | `list[dict]`（DB 行，路径已由上游解析为绝对路径） | `ReportData` |
| `encode_preview()` | 绝对路径 + 规格 | `data:image/jpeg;base64,...` |
| `build_html()` | `ReportData` + 选项 | 完整 HTML 字符串 |
| UI 层 | 用户点击 | 线程调度、进度条、预检弹窗、打开浏览器 |

**路径解析不在生成器内**：`photos` 由浏览器的 `_resolve_photo_paths()`
（`ui/results_browser_window.py:1264`）预先解析为绝对路径后传入。
`report.db` 的 `current_path` 是相对路径，此坑项目内已踩过两次，
因此生成器**不自行拼接路径**，只消费上游给定的绝对路径。

### 4.3 `ReportData` 聚合内容 / Aggregated fields

全部来自第 2.1 节已有列：

- **总体统计**：总数、各星级数、`picked` 数、命中率（3★/总数）、飞版数、精焦数
- **鸟种榜**：按 `bird_species_cn/en` 分组 → 张数、最高星、`gbif_rarity_100` 分级、
  `iucn_category`、`aesthetic_index`
- **每种代表作**：组内优先级 `picked=1` > `rating` 最高 > `adj_sharpness` 最高
- **器材榜**：`camera_model`/`lens_model`/`focal_length_35mm` 分布，ISO 与快门区间
- **拍摄时段**：`date_time_original` 的 min/max
- **地点**：`city`/`state_province`/`country` 众数；
  **GPS 坐标仅在勾选时进入 `ReportData`，未勾选时在聚合层即丢弃**
  （不是渲染层隐藏——渲染层隐藏意味着坐标仍在 HTML 源码中，查看源代码即可挖出，等于未脱敏）
- **连拍**：`burst_id` 分组数与每组平均张数

---

## 5. 页面结构与内容 / Page Structure

### 5.0 视觉基调 / Visual baseline

- **深色底（`#0d0d0f`）单一主题**，不做明暗切换：这是独立 HTML 文件而非 Artifact，
  没有宿主主题可跟随；深色底让鸟类羽色与背景虚化显色更好。
- **系统字体栈**（`PingFang SC` / `Microsoft YaHei` / `-apple-system`），
  **不引 Google Fonts**：文件必须能在离线手机上双击打开。
- 附 `@media print` 转白底样式，便于用户自行存 PDF。

### 5.1 逐屏内容 / Section by section

**① 封面**

| 元素 | 数据来源 |
|---|---|
| 满幅大图 | 精选中 `adj_topiq` 最高的一张（非第一张——第一张往往只是时间最早） |
| 标题 | 目录名 |
| 副标 | 拍摄日期 + `city · state_province · country` |
| 三个大数字 | 总张数 / 鸟种数 / 精选数 |

**② 本次鸟种**

每种一张卡片：代表作方图 + 中英文名 + 张数 + 罕见度徽标
（`gbif_rarity_100` 经 `core/rarity_tier.py` 的 `tier_name_color` 着色）
+ IUCN 徽标（**仅 `VU`/`EN`/`CR`/`CR(PE)`/`CR(PEW)`/`EW`/`EX` 显示；
`LC`/`NT`/`DD`/`NE` 不显示**以免满屏噪音）。

**排序按罕见度降序，非张数降序**：战报的价值在「拍到了罕见的」，
而非「对着常见鸟按了 200 张」。

**③ 精选画廊**

CSS `columns` 瀑布流（比 grid 更适合横竖构图混排）。点击放大用原生 JS lightbox
（约 30 行，无外部依赖）。每张底部一行小字：
`白腹海雕 · 1/2000s · f/5.6 · ISO 640 · 600mm`。

**④ 数据区**

星级分布条形图用纯 CSS 宽度百分比绘制（不引图表库）。含：命中率、飞版数、
精焦数、连拍 N 组均 M 张、器材榜（机身/镜头/最常用焦距/ISO 区间）。

**⑤ 折叠明细**（原生 `<details>`）

表格 12 列：缩略图 / 文件名 / 鸟种 / 星 / 精选 / 锐度 / 美学 / ISO / 快门 /
光圈 / 焦距 / 拍摄时间。表头点击排序（原生 JS 约 20 行）。

**⑥ 页脚**

`由 SuperPicky v<版本> 生成 · <生成时间>` + 项目地址。
报告被转发时，这是工具本身唯一的传播入口。

---

## 6. 体积控制与图片编码 / Size Budget & Image Encoding

### 6.1 关键约束：data URI 无法延迟加载 / data URIs cannot be lazy-loaded

`loading="lazy"` 对 `data:` URI 无效（内容已在文档中，无网络请求可推迟），
`<details>` 折叠与 `display:none` 也不阻止解码。因此页面打开时**所有 `<img>` 会
同时展开为内存位图**：

```
41 张 × 1400×930 × 4B  = 213 MB
318 张 × 160×160 × 4B  =  32 MB
                        ────────
                          245 MB   ← 接近 iOS Safari 单页上限，有被系统终止的风险
```

**解法：把大图从 DOM 移入 JS 字符串数组。**

```html
<img src="data:...">                            <!-- 画廊图 640px，会解码 -->
<script>const HD = ["data:...", ...]</script>   <!-- 1400px，仅字符串，不解码 -->
```

字符串不是图像，浏览器不会解码。仅当点击 lightbox 时执行
`new Image().src = HD[i]` 解开一张，关闭后释放。常驻位图逐档重算：

```
封面    1 张 × 1800×1200 × 4B =  8.6 MB
画廊   41 张 ×  640×425  × 4B = 44.6 MB
明细  318 张 ×  160×160  × 4B = 32.6 MB
                              ─────────
                                85.8 MB   ← 安全区
```

### 6.2 三档规格 / Three tiers

| 用途 | 长边 | JPEG 质量 | 典型数量 | 小计 | 位置 |
|---|---|---|---|---|---|
| 封面 | 1800 | 82 | 1 | 0.4 MB | DOM |
| 画廊图 | 640 | 78 | ~41 | 2.5 MB | DOM |
| 大图 | 1400 | 82 | ~41 | 8.0 MB | JS 数组 |
| 明细缩略图 | 160 | 72 | ~318 | 3.8 MB | DOM |
| | | | base64 ×1.33 | **≈ 19 MB** | |

### 6.3 编码流水线 / Encoding pipeline

```python
im = Image.open(path)
im.draft('RGB', (max_edge, max_edge))   # JPEG 走 libjpeg DCT 1/2·1/4·1/8 缩放
im = ImageOps.exif_transpose(im)        # 等价于 QImageReader.setAutoTransform(True)
im.thumbnail((max_edge, max_edge), Image.LANCZOS)
```

- `draft()` 是性能关键，**仅对 JPEG 有效**（预览图正好都是 JPEG）。
  无此调用时 318 张全尺寸解码需 2~3 分钟，有此调用约 15~25 秒。
  与 `ui/thumbnail_grid.py:247` 注释中 `QImageReader.setScaledSize` 是同一技巧。
- `exif_transpose()` **不可省略**：项目曾因 `load_image` 缺 EXIF 旋转导致竖拍 RAW
  识别错误（长嘴捕蛛鸟 1% → 黄腹花蜜鸟 99.8%），同类坑不再踩。
- `Image.LANCZOS` 在 Pillow 9/10/11 均可用（Pillow 10 移除的是 `ANTIALIAS`，
  非 `LANCZOS`），与 `requirements_base.txt` 的 `Pillow>=9.0.0` 兼容。
  显式传入，不依赖 `thumbnail()` 的默认重采样。

### 6.4 规模保护 / Scale guards

防止大目录生成数百 MB 文件：

- **精选画廊封顶 120 张**，按 `adj_topiq` 降序取，页面标注「显示 120 / 共 N 张精选」
- **照片总数 > 600 时，明细表退化为纯文字**（去掉缩略图列），页面标注原因
- **导出弹窗先估算再确认**：显示「预计生成约 19 MB，用时约 20 秒」

---

## 7. 错误处理与边界 / Error Handling & Edge Cases

### 7.1 首要失败：预览缓存已被清理 / Missing preview cache

见第 2.3 节。**导出前预检**：对全部记录执行 `os.path.exists()`
（不解码，318 次 stat 为毫秒级），计算预览可用率：

| 可用率 | 行为 |
|---|---|
| ≥ 90% | 直接导出 |
| 50~90% | 导出，弹窗提示「N 张预览不可用，将以占位块显示」 |
| < 50% | **拦截**，说明原因（预览缓存已清理），给出两条路径：重新处理该目录 / 仍生成纯文字版报告 |

### 7.2 其余边界 / Other edge cases

原则：**绝不让一张坏数据毁掉整份报告**。

- **单张解码失败**（文件损坏、异常格式）→ `try/except` 兜住，渲染 CSS 灰色占位块 +
  文件名，继续处理下一张；报告末尾汇总「N 张预览不可用」。
- **HTML 转义**：鸟种名、文件名、城市名、`title`、`caption` **一律 `html.escape()`**。
  文件名中的 `<` 即可破坏页面，而 `caption` 是 exiftool 从照片读回的外部输入。
  项目此前已修复过 AppleScript 注入类问题，同类不再犯。
- **中途取消 / 写入失败**：先写 `<name>.html.tmp`，成功后 `os.replace()` 原子重命名，
  永不留下半截文件。
- **目录只读 / 空间不足**：写入前试探，失败则弹出「另存为」让用户选择位置，不抛异常。
- **UTF-8**：`open(..., encoding='utf-8')` + `<meta charset="utf-8">`。
  目录名含中文时文件名亦含中文，路径全程走 `pathlib`，不硬编码分隔符（Windows/macOS 双端）。
- **优雅降级**：全部 `no_bird` → 不渲染鸟种屏；`date_time_original` 缺失 → 不渲染
  拍摄时段；无 GPS → 不渲染地点。每屏可独立缺席，不留空标题。

---

## 8. 测试策略 / Testing Strategy

新增 `test_report_export.py`。**注意 `.gitignore` 忽略 `test_*.py`，提交需 `git add -f`**
（此坑项目内已踩过两次）。

三个纯函数全部以字典喂入、断言输出，**不构造任何 Qt 对象、不读写全局配置**。

| # | 用例 | 断言 |
|---|---|---|
| 1 | 聚合统计 | 总数/星级/命中率/连拍组数正确 |
| 2 | 鸟种榜排序 | 按罕见度降序而非张数降序 |
| 3 | 代表作选取 | `picked` > `rating` > `adj_sharpness` 优先级 |
| 4 | GPS 未勾选 | `ReportData` 中**不存在坐标字段** |
| 5 | 坐标不泄漏 | 未勾选时坐标数值字符串**不出现在 HTML 全文** |
| 6 | HTML 转义 | 鸟种名喂 `<script>alert(1)</script>`，断言被转义 |
| 7 | 大图不在 DOM | 1400px data URI 只出现在 JS 数组，`<img src=` 中没有 |
| 8 | EXIF 旋转 | 造 `Orientation=6` 的图，断言输出宽高互换 |
| 9 | 规模保护 | 200 张精选 → 画廊只出 120；700 张 → 明细表无缩略图列 |
| 10 | 优雅降级 | 全 `no_bird` / 无时间 / 无 GPS，各自不留空标题 |
| 11 | 单张失败不中断 | 混入损坏文件，报告仍完整生成 |
| 12 | 中文往返 | 目录名 + 鸟种名含中文，UTF-8 写入读回逐字一致 |
| 13 | 原子写 | 成功后无 `.tmp` 残留；模拟中途失败不产出成品文件 |

UI 接线另置一个测试，参照现有 `test_species_merge_entry.py` 写法：
仅验证按钮存在与信号连接，不执行真实导出。

**最低验证**（依 CLAUDE.md）：改动的 Python 文件执行 `py_compile`；
中文样本往返验证；T6 需在真机手机浏览器打开一次，对账第 6 节的
19MB 文件体积与 85.8MB 常驻位图估算。

---

## 9. 任务拆分 / Task Breakdown

| # | 任务 | 性质 | 覆盖用例 |
|---|---|---|---|
| T1 | `ReportData` 数据模型 + `aggregate()` | 纯函数 | 1–5 |
| T2 | `encode_preview()` | Pillow | 8、11 |
| T3 | `build_html()` 模板 + CSS/JS | 字符串 | 6–7、9–10、12 |
| T4 | 导出对话框 + 预检 + `QThread` 接线 | Qt | UI 接线测试 |
| T5 | i18n key（`zh_CN` + `en_US`） | — | 跟随导出语言 |
| T6 | 真实目录端到端 + 手机端打开验证 | 手工 | 体积/耗时实测对账 |

---

## 10. 明确不做 / Explicit Non-goals

| 不做 | 理由 |
|---|---|
| 在线发布 / 上传分享链接 | 用户要的是本地文件；上传涉及隐私与托管成本 |
| PDF 导出 | `@media print` 已让用户可自行「打印为 PDF」，不值得引入 PDF 库 |
| CLI 入口（`--report`） | 当前无需求；架构已预留（生成器无 Qt 依赖），需要时再加 |
| 引入 Jinja2 等模板引擎 | 见第 2.4 节 |
| 报告内嵌地图 | CSP/离线限制下无法加载地图瓦片，无底图散点图实用价值低 |
| 改动 `_show_statistics_report()` | 现有主窗口统计报告继续保留，二者用途不同 |
| 为报告落库处理耗时 | 见第 2.2 节，改用拍摄时段，且换来「纯 DB 驱动」的更好性质 |
| 导出跟随筛选面板 | 见 D4，会破坏统计口径 |
