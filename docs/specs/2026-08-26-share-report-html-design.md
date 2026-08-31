# 可分享总结报告 HTML 设计 / Shareable Summary Report HTML Design

日期 / Date: 2026-08-26
状态 / Status: 已实施；2026-08-29 修订 / Implemented, revised 2026-08-29
相关 / Related: `tools/report_db.py:27`（`PHOTO_COLUMNS` 数据源）、
`ui/results_browser_window.py:971`（工具栏，新增入口）、
`ui/results_browser_window.py:1264`（`_resolve_photo_paths`，上游路径解析）、
`ui/thumbnail_grid.py:205`（`_thumbnail_candidates`，预览图优先级先例）、
`ui/main_window.py:3376`（`_show_statistics_report`，现有统计报告，不被替代）、
`core/rarity_tier.py`（罕见度分级配色，复用）、
`advanced_config.py:547`（`keep_temp_files`，预览缓存开关）

---

## 0. 修订记录 / Revision Log

### 2026-08-29（提交 `37878f9c`）

首版实施后用真实批次（284 张 / 12 鸟种）验收，改动如下。**被推翻的决策在原
节内保留了原推理**——那些计算大多是对的，变的是权衡前提，删掉只会让人日后
重新提出同一个方案。

| 节 | 原设计 | 现状 | 推翻的理由 |
|---|---|---|---|
| 6.1 | 图片一律不写 `src`，全部靠 JS 数组 + 视口懒插入 | 图片**直接写进 `src`**，脚本再接管卸载 | 任何不执行 JS 的环境（macOS 快速查看、iOS 文件预览、邮件与 IM 内置预览）只能看到一份没有照片的报告。而收到文件先按空格看一眼恰是最常见的动作 |
| 6.2 | 五档（封面/代表作/小图/放大/缩略图） | **两档**（封面 + 展示图 1000px） | 「放大专用」的 hd 副本与页面上那张是同一个画面，却占掉整份报告 74% 的体积 |
| 5.1 ② | 固定 1 大 + 3 小 | 按张数自适应，且**按连拍组去重** | 固定 2:1 列宽在数学上无法等高；不去重则四格常是同一次连拍的雷同画面 |
| 5.1 ④ | 折叠明细表（12 列 + 缩略图） | **移除** | 用户决策：分享用的报告不需要全量清单，去掉后省 284 张缩略图 |
| 5.0 | 双强调色（青绿 + 纯黄） | 界面无彩色，颜色只留给徽章 | 高饱和界面色与照片争夺视线 |

实测：**7.93 MB → 4.04 MB，图片编码任务 377 → 42 个。**

同时修掉三个既有缺陷（均非本次引入）：`.sec` 的 `margin` 覆盖了 `.wrap` 的
居中、封面是全页唯一被裁切的图、**lightbox 从未工作过**（hd 图无 DOM 节点、
从未被注册，`data-hd` 恒为 `-1`，点击无反应而字节照付）。

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
| D1 | ~~战报为主 + 明细可折叠~~ → **只做战报**（2026-08-29 修订） | 原意是「一份文件两用：上半给别人看，下半折叠区给自己复盘」。实际使用后由用户决定去掉明细区——分享用的报告不需要全量清单，复盘留在软件里做 |
| D2 | **单文件自包含**（图片 base64 内嵌，**且写进 `src`**） | 拖进微信/邮件即可发送，对方双击即开，永不丢图。代价约 4MB（见 6.2；首版估 18.5MB，去掉明细表与 hd 副本后大幅下降）。**内嵌还不够——图片必须写在 `src` 上**，否则不执行 JS 的预览环境（快速查看等）看到的是一份没有照片的报告，见 6.1 |
| D3 | **地点默认只到城市级**，导出弹窗可勾选「包含精确 GPS 坐标」（默认不勾） | 珍稀鸟点位泄露在观鸟圈是真实风险；eBird 对敏感鸟种同样强制模糊坐标 |
| D4 | **入口在选鸟浏览器工具栏，导出当前载入的全量**，不受筛选面板影响 | 统计口径必须是「这次拍的全部」，否则命中率会变成 62/62=100% 的无意义数字 |
| D5 | 输出到 `<选鸟目录>/SuperPicky报告_<目录名>_<YYYY-MM-DD>.html`（英文界面为 `SuperPicky-Report-<目录名>-<YYYY-MM-DD>.html`，**文件名与 D7 同样跟随界面语言**），**根目录非隐藏目录**；同名加 `_2` 后缀不覆盖 | 它就是要被找到并发出去的 |
| D6 | **绝对路径一律不出现在报告中**，只显示文件名 | `/Users/<用户名>/...` 属隐私，与 GPS 同理 |
| D7 | **报告语言跟随导出时的界面语言**（`zh_CN`/`en_US`），不做单独语言选择 | 复用现有 i18n，无新增决策点 |
| D8 | **画廊按鸟种分组，每种最多 4 张**（版式按张数自适应，非固定的 1 大 3 小；**且按连拍组去重**），区块按罕见度降序；**鸟种数不封顶**，多少种就出多少种 | 读者的问题是「拍到了哪些鸟、每种长什么样」，不是「有多少张好片」；原「鸟种屏 + 精选画廊」两屏本就重复展示同一批代表作。「最多 4 张」这个上限未变，变的是版式与选片方式，见 5.1 ② |
| D9 | **PDF 不写代码**：做扎实 `@media print` + 页顶「存为 PDF」按钮调 `window.print()` | 见第 3.1 节，唯一能复用本 HTML 的 QtWebEngine 路线要付 200~300MB 打包体积与 macOS helper 签名改造 |

### 3.1 为什么 PDF 与地图不按直觉做 / Why PDF and maps are not built the obvious way

**PDF**：本机 `PySide6.QtWebEngineWidgets` 可用，但当前代码无一处 import，
PyInstaller 的 PySide6 hook 因此未将其打入产物（`SuperPicky.spec:137` 的 `excludes`
也未显式排除，纯粹靠"没有 import"）。一旦 import，整个 Chromium 进包：
`QtWebEngineCore.framework` + `.pak` 资源 + ICU 数据 + `QtWebEngineProcess` helper，
**macOS 产物预计增加 200~300MB**（估算，非实测）；且该 helper 是独立 app，
**必须单独签名并带 JIT entitlement**，会改动现有 rcodesign CI 签名链路。
替代路线 `QTextDocument` + `QtPrintSupport` 无体积代价，但只认 Qt 的 HTML 子集
（CSS `columns`、`<details>`、JS 全不支持），等于为 PDF 另写一套布局，双倍维护。
故取 D9：`window.print()` 交给系统打印对话框，体验差别仅一次点击，成本接近零。

**地图**：离线无瓦片，但内嵌 Natural Earth 简化轮廓 SVG（CC0，约 100~300KB）+
等距圆柱投影打点技术上可行。否决它的不是技术而是内容：

1. **一个选鸟目录通常就是一次外拍 = 一个地点 = 地图上一个点。**
   一张世界地图画一个点，信息量不如 `Cairns · Queensland` 一行字。
2. **与 D3 冲突**：默认不带 GPS 就无坐标可画；勾选 GPS 才有图，
   而那恰是隐私风险最大的那份文件，再配一张点位图等于放大风险。
   退到"国家级高亮"则需新增地名坐标数据——`tools/country_names.py` 只有名称表，无坐标。

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

- **总体统计**：总数、各星级数（含用户手动升出的 4★/5★）、`picked` 数、
  命中率（**3★ 及以上**/总数）、飞版数、精焦数
  - 命中率口径为「3★ 及以上」而非仅 3★：4/5★ 是比 3★ 更好的片子，只数 3★
    会让用户每手动升一张星、命中率反而下降。
- **鸟种榜**：按 `bird_species_cn/en` 分组 → 张数、最高星、`gbif_rarity_100` 分级、
  `iucn_category`、`aesthetic_index`
- **每种展示图**：组内按 `picked=1` > `rating` 最高 > `adj_topiq` 最高排序，
  **按 `burst_id` 去重后**取前 4 张（首张为代表作，见 D8）；不足 4 张者全取。
  去重在排序之后，故每个连拍组留下的是质量最高的一张（2026-08-29 新增）
- **鸟种颜值**：`aesthetic_index` 挂在 `SpeciesBlock.beauty` 上——它是**鸟种级**
  属性而非单张照片的，不放 `PhotoRef`（2026-08-29 新增）
- **器材榜**：`camera_model`/`lens_model`/`focal_length_35mm` 分布，ISO 与快门区间
- **拍摄时段**：`date_time_original` 的 min/max
- **地点**：`city`/`state_province`/`country` 众数；
  **GPS 坐标仅在勾选时进入 `ReportData`，未勾选时在聚合层即丢弃**
  （不是渲染层隐藏——渲染层隐藏意味着坐标仍在 HTML 源码中，查看源代码即可挖出，等于未脱敏）
- **连拍**：`burst_id` 分组数与每组平均张数

---

## 5. 页面结构与内容 / Page Structure

### 5.0 视觉基调 / Visual baseline

- **深色底单一主题**，不做明暗切换：这是独立 HTML 文件而非 Artifact，没有宿主
  主题可跟随；深色底让鸟类羽色与背景虚化显色更好。底色为 `#0f0e0d`——**中性微暖**，
  首版的 `#0d0d0f` 蓝分量高于红，会把暖调晨昏光的片子衬发青。
- **界面无彩色**（2026-08-29 修订）。首版有两个高饱和强调色（`--accent:#00d4aa`
  薄荷绿、`--gold:#ffcc00` 纯黄），二者同屏打架，且都在跟画面里的鸟争夺视线。
  现在**颜色只允许出现在两处有信息含义的地方**：罕见度徽章与 IUCN 徽章；照片
  是页面上唯一的颜色来源。星级条改用**亮度分级**承载高低（越高星越亮），比原先
  一律涂成同一个纯黄多传达了一维信息。
  - 罕见度红 `#D81E05` 在此底色上对比度仅约 4.3:1（低于 WCAG AA），报告端提亮至
    `#FF4A32`（约 6.1:1）。**不改共用的 `core/rarity_tier.py`**——那份配色由浅色底
    的 GUI 共用。
- **系统字体三栈**，**不引 Google Fonts**：文件必须能在离线手机上双击打开，
  内嵌一套中文字体会让文件涨到 10MB+。
  - `--sans`：`-apple-system` → `Segoe UI` → `PingFang SC` → `Microsoft YaHei`。
    **西文字体必须排在中文之前**，否则西文与数字被 CJK 字面接管（雅黑的西文数字
    字重不匀），首版正是如此。
  - `--serif`：`Iowan Old Style` / `Palatino` / `Georgia`，用于**学名**与封面大数字。
    学名必须走真 italic——PingFang 没有 italic 字形，浏览器只能做倾斜合成，
    首版的学名是「歪的」而不是「斜的」。
  - `--mono`：EXIF 参数与数值，配 `tabular-nums` 保证数字对齐。
  - 字号收敛为 1.25 阶梯（11 / 12 / 14 / 17 / 21 / 27 / 34），首版的八个字号
    是逐次加需求加出来的，彼此无比例关系。
- 附 `@media print` 转白底样式，便于用户自行存 PDF。

### 5.1 逐屏内容 / Section by section

**① 封面**

| 元素 | 数据来源 |
|---|---|
| 满幅大图 | 全库有鸟的照片中 `adj_topiq` 最高的一张（非第一张——第一张往往只是时间最早） |
| 标题 | 目录名 |
| 副标 | 拍摄时段 + `city · state_province · country` |
| 三个大数字 | 总张数 / 鸟种数 / 精选数 |

封面**不裁切**（2026-08-29 修订）。首版用 `object-fit:cover` + `max-height:70vh`，
使得全页最该完整呈现的一张（全库美学分最高）反倒成了唯一被裁的。现按方向分流：

- **横构图 / 方构图 → 满宽出血**。封面的职责是第一眼的冲击力；若套用鸟种代表作
  那套 `max-width = 宽高比 × vh`，900px 高的窗口里 3:2 封面只有 1026px 宽，两侧
  留黑边，看着像图没加载完。
- **竖构图 → 限高 80vh 居中**。竖图满宽会撑到近两屏（1280 宽的 2:3 是 1920px）。

时间显示截到**分钟**，日期分隔符由 EXIF 的冒号规范为连字符，同一天只写一次日期
（`2026-08-28 08:29 – 11:51`）。秒对一次外拍的起止毫无意义，而 `2026:08:28` 这种
EXIF 内部格式会被读成时间。

**② 鸟种画廊**（原「鸟种屏」与「精选画廊」合并为一屏，见 D8）

每种鸟一个区块，区块按**罕见度降序**排列——战报的价值在「拍到了罕见的」，
而非「对着常见鸟按了 200 张」。

区块头：中英文名 + 张数 + 罕见度徽标（`gbif_rarity_100` 经
`core/rarity_tier.py` 的 `tier_name_color` 着色）+ IUCN 徽标
（**仅 `VU`/`EN`/`CR`/`CR(PE)`/`CR(PEW)`/`EW`/`EX` 显示；
`LC`/`NT`/`DD`/`NE` 不显示**以免满屏噪音）。

区块体：**最多 4 张**，选取优先级同 4.3（`picked=1` > `rating` 最高 >
`adj_topiq` 最高），首张为代表作。

**按连拍组去重**（2026-08-29 新增）：同一 `burst_id` 只留一张。不去重时一个鸟种
的前 4 张极可能来自同一次连拍——画面几乎一模一样，占了四格却只讲了一件事
（真实批次里「西大亭鸟」8 张仅来自 2 个连拍组）。去重发生在**排序之后**，所以
每组留下的是该组质量最高的一张，而非快门顺序上的第一张。`burst_id` 为空的
非连拍照片各自独立，不受影响。

**版式按张数自适应**（2026-08-29 修订，取代固定的「1 大 + 3 小」）：

| 张数 | 版式 |
|---|---|
| 1 | 满幅一张 |
| 2 | 左右对开，不设代表作位 |
| 3 | 满幅代表作 + 一排 2 张 |
| 4 | 满幅代表作 + 一排 3 张 |

2 张时不排成「满幅 + 下方一张同宽的图」：两张一样大地上下堆着，读起来是重复而
不是主次。

**每一排是等高零裁切的 flex**，这是版式的核心机制：

```css
.row{display:flex;gap:8px}
.row>.shot{flex-grow:<该图宽高比>;flex-basis:0;min-width:0}
```

`flex-basis:0` 时 gap 已从可用空间扣除，剩余宽度**严格按 grow 比例**分配，于是
每张图的宽度正比于自身宽高比、高度 = 宽 ÷ 比 = 恒等。整排顶底严丝合缝，且不需
`object-fit` 裁切、不需任何 JS（实测横/竖/全景混排高度极差 0.0000000000 px）。
`min-width:0` 不可省——flex item 默认 `min-width:auto`，图片的 min-content 宽度
会阻止收缩，窄视口下比例失真、整排随即不再等高。

> 首版的 `grid-template-columns:2fr 1fr` + `grid-template-rows:repeat(3,1fr)`
> 从数学上就不可能对齐：行高由 `1fr` 均分容器，图片高度却由自身宽高比决定，
> 两者从不相等。且 1 大 3 小要天然对齐，列宽比必须是 **3:1** 而非 2:1——
> 三张小图叠起来的高度才等于一张 3 倍宽的大图。

满幅代表作限高 `85vh`（`max-width = 宽高比 × 85vh`，不能直接给 `<img>` 设
`max-height`，那会让宽高脱钩而变形）。85 是权衡出来的：代表作限宽后居中，而它
下方那排始终满版心宽，一旦限高让**横构图**代表作窄于版心，就读作排版失误。
版心 1060、3:2 横图满宽需 `1.5 × H × vh ≥ 1060`，取 85 时窗口内容高 ≥832px 即
满宽；竖构图仍被有效限制（2:3 若不限高，满宽会撑到 1590px）。

每张底部一行小字：`1/1250s · f/4 · ISO 1250 · 600mm`。快门按摄影惯例显示——
数据库存的是秒数，直接印出来是 `0.0008s`，没人这么报快门。

**代表作额外附选鸟参数**（2026-08-29 新增）：`锐度 550 · 美学 5.2 · 颜值 57`，
接在曝光组合之后。只挂代表作——副图各带一串数字会盖过画面。小数位按各自量程定：

- 锐度（`adj_sharpness`，量程 ~0–800）取整
- 美学（`adj_topiq`，量程仅 **3–6.5**）**必须留一位小数**，取整会把整批压成清一色
  的 5 和 6。这个尺度也正是用户在技能等级里看到的阈值口径（`core/skill_presets.py`
  的 4.5 / 4.8 / 5.5），两处对得上才不会打架
- 颜值（`aesthetic_index`，鸟种级的 iRateBird 指数 0–100）取整；无数据则整项不出现

点击任意图放大，用原生 JS lightbox（无外部依赖）。**放大的就是页面上那一张**，
不存在第二份副本（见 6.2）。

**③ 数据区**

星级分布条形图用纯 CSS 宽度百分比绘制（不引图表库）。含：命中率、飞版数、
精焦数、连拍 N 组均 M 张、器材榜（机身/镜头/最常用焦距/ISO 区间）。

**④ 折叠明细** —— **已移除**（2026-08-29）

~~表格 12 列：缩略图 / 文件名 / 鸟种 / 星 / 精选 / 锐度 / 美学 / ISO / 快门 /
光圈 / 焦距 / 拍摄时间。表头点击排序。~~

用户决策：分享用的报告不需要全量清单。一并去掉的还有 `thumb` 档位（284 张
缩略图约占 0.9MB）、表头排序脚本、`<details>` 折叠区、`ReportData.detail` 字段，
以及 `with_detail_thumbs` 参数与 `DETAIL_THUMB_LIMIT`（含 UI 层的接线）。
报告结构因此简化为 **封面 → 鸟种画廊 → 数据区 → 页脚**。

**⑤ 页脚**

`由 SuperPicky v<版本> 生成 · <生成时间>` + 项目地址。
报告被转发时，这是工具本身唯一的传播入口。

### 5.2 打印适配 / Print support（D9）

页顶固定一个「存为 PDF」按钮，`onclick` 调 `window.print()`。
配套 `@media print` 需做到三件事，缺一则打印结果不可用：

1. 转白底黑字（深色底打印会输出整页黑）
2. `page-break-inside: avoid` 施加于鸟种区块与图片排，避免图文被拦腰截断
3. 隐藏交互控件（「存为 PDF」按钮本身、lightbox 容器）

> 首版还有第 4 项「自动展开 `<details>`」，随明细表一并移除。它也**不能靠 CSS**
> 完成——`open` 是属性不是样式，`display:block` 只让 `<details>` 本身是块级，
> 折叠内容依旧不渲染，当时是在打印前用 JS 补 `open`、打印后恢复。

另有一条**打印专属的兜底**（2026-08-29 新增）：星级条的屏幕配色是亮度分级，那套
浅灰印在白纸上等于消失，`@media print` 里统一压成深灰
（`.bars .bar{background:#444 !important}`）。条色是内联 style 写死的，
必须 `!important` 才盖得住。条的**长度**已完整承载数量信息，白底不必再做亮度编码。

打印前仍需把滚出过视口的 `src` 恢复（见 6.1），否则那几页是空白。三条触发路径
都要覆盖：按钮（直接调用，留 300ms 给浏览器解码）、`beforeprint` 事件、以及
`matchMedia('print')`——Safari 长期不支持 `beforeprint`。

---

## 6. 体积控制与图片编码 / Size Budget & Image Encoding

### 6.1 关键约束：data URI 无法延迟加载 / data URIs cannot be lazy-loaded

`loading="lazy"` 对 `data:` URI 无效（内容已在文档中，无网络请求可推迟），
`<details>` 折叠与 `display:none` 也不阻止解码。因此只要图片写进 `src`，
页面打开时**全部会同时展开为内存位图**。以 12 鸟种 / 48 展示图 / 318 明细为例：

```
放大图  48 张 × 1200×800 × 4B = 184.3 MB
封面     1 张 × 1800×1200× 4B =   8.6 MB
代表作  12 张 ×  900×600 × 4B =  25.9 MB
小图    36 张 ×  400×267 × 4B =  15.4 MB
明细   318 张 ×  160×160 × 4B =  32.6 MB
                              ──────────
                                266.8 MB   ← 超出 iOS Safari 单页承受范围，会被系统终止
```

鸟种数越多越糟：40 个鸟种约 178MB（不含放大图），100 个鸟种约 344MB。

**首版解法（已推翻）：所有图片一律不写进 `src`，改为 JS 字符串数组 + 视口懒插入。**

```html
<img data-idx="7" alt="...">                    <!-- 无 src，不解码 -->
<script>
const IMGS = ["data:...", ...];                 <!-- 仅字符串，不解码 -->
new IntersectionObserver(es => es.forEach(e => {
  const el = e.target;
  if (e.isIntersecting) el.src = IMGS[el.dataset.idx];
  else el.removeAttribute('src');               // 滚离视口即释放位图
}), {rootMargin: '200% 0px'}).observe(...);
</script>
```

上面的内存计算没有错，错的是**把「看得见」也一起绑在了脚本上**。

#### 推翻的理由：不执行 JS 的环境看到的是一份没有照片的报告

图片一旦只存在于 JS 数组里，任何不跑脚本的查看环境都是一片空白——**macOS 的
快速查看（Finder 里按空格）、iOS「文件」App 的预览、邮件客户端与部分 IM 的
内置预览**都不执行 JavaScript。而这份文件的整个存在意义就是**发给别人**，
收到后先按空格看一眼恰恰是最常见的动作，对方看到空白只会以为文件传坏了。

本机用 `qlmanage` 复现过：文字、鸟种名、徽章、EXIF 全在，图片一张都没有。

#### 现方案：`src` 直出，脚本再接管

```html
<img data-lazy decoding="async" src="data:image/jpeg;base64,...">
```

```js
var els = document.querySelectorAll('img[data-lazy]');
var SRC = [];
els.forEach(function(el, i){ SRC[i] = el.src; el.dataset.i = i; });  // 收走
// 之后交给同一个 IntersectionObserver：滚离视口 removeAttribute，滚回来从 SRC 恢复
```

- **无 JS**：图片全部直接显示，报告完整可读
- **有 JS**：脚本收走 `src` 后接管，滚离视口照样卸载，**内存行为与首版完全一致**
- **体积无代价**：base64 仍只存一份，只是从数组挪进了 `src`
- `data-lazy` 只是给脚本认领的标记，脚本不跑时它是个无副作用的属性

**同时不做滚动淡入。** 淡入要靠初始 `opacity:0`，那等于把「看得见」重新绑回脚本
——无 JS 时图片虽有 `src` 却是透明的，比没有 `src` 更难排查。这条已固化为测试
（断言样式表中不含 `opacity:0`）。

#### 首版的内存计算为何不再是障碍

上表 266.8MB 里，明细缩略图（32.6MB）与放大图（184.3MB）合计占 81%，而这两项
**都已不存在**：明细表整块移除，放大图不再有独立副本。现在 DOM 中只剩 42 张
1000px 图，全部解码约 **47MB**——而且浏览器对屏幕外图片本就延迟解码，实际峰值
更低，脚本一跑起来 observer 立刻卸载视口外的。

代价仅存在于「无 JS + 超大批次」这一个组合：那时没有 observer 卸载，图片全部
常驻。但预览场景本就是看几眼就关，且 40 鸟种量级下也只有约 160 张展示图。
**「常驻位图与内容量脱钩」这条性质在有 JS 时依然成立**，D8 的「鸟种数不封顶」
不受影响。

文件体积仍随内容线性增长（这是自包含单文件绕不开的代价），
由 6.4 的导出前预估告知用户，而非静默产出一个发不出去的文件。

### 6.2 分档规格 / Size tiers

**现为两档**（2026-08-29 修订）。基准样本：284 张照片、12 个鸟种、41 张展示图。

| 用途 | 长边 | JPEG 质量 | 典型数量 | 小计 |
|---|---|---|---|---|
| 封面 | 1800 | 82 | 1 | 0.17 MB |
| 展示图（页面显示 + 点击放大共用） | 1000 | 75 | 41 | 3.85 MB |
| | | | **合计** | **≈ 4.04 MB** |

上表的"数量"随内容线性变化，是估算的输入而非固定值。

#### 为什么从五档砍到两档：同一张照片不该存两份

首版为每张展示图额外编码一份 1200px 的 `hd` 副本专供 lightbox。真实批次拆解
下来，**这些副本占掉整份报告体积的 74%**（7.93MB 中的 5.85MB），而它们与页面上
那张是**同一个画面**。

| 方案 | 总计 | 放大质量 |
|---|---|---|
| 原：900/400px 展示图 + 1200px hd 副本 | 7.93 MB | 1200px |
| 只降 hd 到 1000px（仍存两份） | 5.96 MB | 1000px |
| **一张图两用，统一 1000px** | **4.04 MB** | 1000px |
| （对照）一张图两用，统一 1200px | 6.04 MB | 1200px |

看第 2 行与第 4 行：**体积几乎相同，但放大质量差一档**——降规格是在为重复存储
付钱。现在每张照片只编码一次，页面上缩着显示（代表作约 1060px 宽、副图约 348px），
点击时按原尺寸铺开。副图因此从 400px 升到 1000px，**在页面上反而更锐**。

> 首版此处写道「体积大头是 lightbox 放大图与明细缩略图，不是展示图。若日后确需
> 压缩体积，有效手段是降低放大图规格或关闭明细缩略图」——前半句的诊断是准的，
> 后半句给错了药方：真正的手段是**消除重复**，不是降质量。

这一并删掉了 `hd_index()`、`HD` 数组、`data-hd` 属性与三档分配逻辑
（`species_photo_kinds`）。**同一张照片不得产出两个编码任务**已固化为测试。

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

**内存侧（有 JS 时）无需保护**：6.1 的 observer 仍在卸载视口外的位图，常驻量
与内容量脱钩。因此 D8 明确不对鸟种数封顶——多少种就出多少种，不把任何一种降级
为纯文字。无 JS 的预览环境下没有卸载，但那是看几眼就关的场景，见 6.1 末段。

**仅剩文件体积需要管理**，且手段是告知而非砍内容：

- **导出弹窗先估算再确认**：按 6.2 的单张均值 × 实际数量估算，
  显示「预计生成约 N MB，用时约 M 秒」。用户看到 113MB 可自行决定是否拆分目录，
  而不是静默产出一个发不出去的文件
- **超过 80MB 时弹窗额外提示**：常见 IM 与邮件附件上限在 100MB 附近，
  提示文案给出「仍然导出 / 取消」两个选择，不代替用户决策
- ~~**照片总数 > 600 时，明细表退化为纯文字**~~ —— 随明细表一并移除
  （2026-08-29）。`DETAIL_THUMB_LIMIT` 与 `with_detail_thumbs` 参数、以及 UI 层
  对应的接线均已删除；导出对话框现在只剩 GPS 一个选项

去掉明细表与 hd 副本后，图片编码任务数从 377 降到 42，**导出耗时也大幅缩短**。

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
| 2 | 鸟种区块排序 | 按罕见度降序而非张数降序 |
| 3 | 每种取图 | `picked` > `rating` > `adj_topiq` 优先级，每种至多 4 张，不足者全取 |
| 4 | GPS 未勾选 | `ReportData` 中**不存在坐标字段** |
| 5 | 坐标不泄漏 | 未勾选时坐标数值字符串**不出现在 HTML 全文** |
| 6 | HTML 转义 | 鸟种名喂 `<script>alert(1)</script>`，断言被转义 |
| 7 | ~~无图写进 DOM~~ → **图片必须写进 DOM**（2026-08-29 反转）| 每张图都带 `src="data:image`，且样式表中不含 `opacity:0`（否则「看得见」又被绑回脚本）；同时断言 observer 仍在接管（`IntersectionObserver` + `removeAttribute('src')`），确保内存行为未退化 |
| 8 | EXIF 旋转 | 造 `Orientation=6` 的图，断言输出宽高互换 |
| 9 | 规模保护 | 40 个鸟种 → 40 个区块全部出图、无一降级为纯文字 |
| 10 | 优雅降级 | 全 `no_bird` / 无时间 / 无 GPS，各自不留空标题 |
| 11 | 单张失败不中断 | 混入损坏文件，报告仍完整生成 |
| 12 | 中文往返 | 目录名 + 鸟种名含中文，UTF-8 写入读回逐字一致 |
| 13 | 原子写 | 成功后无 `.tmp` 残留；模拟中途失败不产出成品文件 |
| 14 | 打印样式 | 输出含 `@media print`，且其中有白底、`page-break-inside: avoid`、隐藏交互控件三项；另断言星级条被 `!important` 压成深灰（浅灰在白纸上不可见） |
| 15 | IUCN 徽标门槛 | `LC`/`NT`/`DD`/`NE` 不渲染徽标；`VU` 及以上渲染 |

**2026-08-29 新增用例：**

| # | 用例 | 断言 |
|---|---|---|
| 16 | 版式随张数自适应 | 1 张只有满幅、2 张只有一排（无满幅位）、3/4 张为满幅 + 一排；排内每张都带 `flex-grow` |
| 17 | 等高布局的数学前提 | `flex-basis:0` 下按宽高比分配宽度后，一排内各图高度极差 < 1e-9（横/竖/全景混排） |
| 18 | 宽高比探测 | 横/竖/方/极端比例编码后都能从 JPEG 头读回；畸形输入返回 `None` 且不抛异常 |
| 19 | 连拍去重 | 8 张分属 2 组 → 只展示 2 张，且各组留下的是 `adj_topiq` 最高那张；`burst_id` 为空者全保留 |
| 20 | 每张只编码一次 | 同一照片不得产出两个编码任务（重复副本正是旧版 74% 体积的来源） |
| 21 | 放大用页面图 | 输出中无 `const HD=`、无 `data-hd`；点击目标是 `.shot`，取的是它自己的 `<img>` |
| 22 | 明细表确已移除 | 无 `<table>`／`<details>`／`thumb` 任务；`IMG_SPECS` 中无 `thumb` 档 |
| 23 | 快门显示 | `0.0008` → `1/1250s`；`1.3` → `1.3s`；已是 `1/500` 则原样；无法解析不猜 |
| 24 | 时间截到分钟 | `2026:08:28 08:29:53` → `2026-08-28 08:29`；同日只出现一次日期 |
| 25 | 美学精度 | 保留一位小数——量程仅 3–6.5，取整会把整批压成清一色的 5 和 6 |
| 26 | 选鸟参数只在代表作 | 锐度/美学/颜值各出现一次，且排在曝光组合之后；缺数据的项不出现 |
| 27 | 版心居中 | `.sec` 的左右外边距必须是 `auto`（它定义在 `.wrap` 之后，写死 `0` 会覆盖居中） |
| 28 | 无残留强调色 | 输出中不含 `#00d4aa` / `#ffcc00` / `var(--accent)` / `var(--gold)` |

UI 接线另置一个测试，参照现有 `test_species_merge_entry.py` 写法：
仅验证按钮存在与信号连接，不执行真实导出。

**最低验证**（依 CLAUDE.md）：改动的 Python 文件执行 `py_compile`；
中文样本往返验证；真机手机浏览器打开一次对账体积估算；在真机上走一遍
「存为 PDF」。

**2026-08-29 补充的两项人工验证**，二者都是自动化测试抓不到的：

1. **用 `qlmanage -t` 生成快速查看预览**，确认图片可见——这是「无 JS 环境可读」
   唯一的硬证据，也是首版 bug 的复现手段
2. **实际点击一张图**确认 lightbox 弹出、关闭后 `src` 被释放——旧测试只断言了
   `data-hd` 属性存在、没验证取值，所以「索引恒为 -1、点击毫无反应」这个 bug
   一直绿着

---

## 9. 任务拆分 / Task Breakdown

| # | 任务 | 性质 | 覆盖用例 |
|---|---|---|---|
| T1 | `ReportData` 数据模型 + `aggregate()` | 纯函数 | 1–5 |
| T3.5 | 页顶「存为 PDF」按钮（`window.print()`） | 字符串 | 14 |
| T2 | `encode_preview()` | Pillow | 8、11 |
| T3 | `build_html()` 模板 + CSS/JS + `@media print` | 字符串 | 6–7、9–10、12、14–15 |
| T4 | 导出对话框 + 预检 + `QThread` 接线 | Qt | UI 接线测试 |
| T5 | i18n key（`zh_CN` + `en_US`） | — | 跟随导出语言 |
| T6 | 真实目录端到端 + 手机端打开验证 | 手工 | 体积/耗时实测对账 |

---

## 10. 明确不做 / Explicit Non-goals

| 不做 | 理由 |
|---|---|
| 在线发布 / 上传分享链接 | 用户要的是本地文件；上传涉及隐私与托管成本 |
| 代码级 PDF 导出（`printToPdf`） | 见 3.1：QtWebEngine 要付 200~300MB 打包体积 + macOS helper 签名改造。改为 D9 的页内「存为 PDF」按钮，体验差别仅一次点击 |
| CLI 入口（`--report`） | 当前无需求；架构已预留（生成器无 Qt 依赖），需要时再加 |
| 引入 Jinja2 等模板引擎 | 见第 2.4 节 |
| 报告内嵌地图 | 见 3.1：一个目录通常就一个地点，地图退化为单点，信息量不如一行地名；且与 D3 默认不带 GPS 直接冲突 |
| 改动 `_show_statistics_report()` | 现有主窗口统计报告继续保留，二者用途不同 |
| 为报告落库处理耗时 | 见第 2.2 节，改用拍摄时段，且换来「纯 DB 驱动」的更好性质 |
| 导出跟随筛选面板 | 见 D4，会破坏统计口径 |
