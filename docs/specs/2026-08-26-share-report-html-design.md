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
| D2 | **单文件自包含**（图片 base64 内嵌） | 拖进微信/邮件即可发送，对方双击即开，永不丢图。代价约 18.5MB（见 6.2） |
| D3 | **地点默认只到城市级**，导出弹窗可勾选「包含精确 GPS 坐标」（默认不勾） | 珍稀鸟点位泄露在观鸟圈是真实风险；eBird 对敏感鸟种同样强制模糊坐标 |
| D4 | **入口在选鸟浏览器工具栏，导出当前载入的全量**，不受筛选面板影响 | 统计口径必须是「这次拍的全部」，否则命中率会变成 62/62=100% 的无意义数字 |
| D5 | 输出到 `<选鸟目录>/SuperPicky报告_<目录名>_<YYYY-MM-DD>.html`（英文界面为 `SuperPicky-Report-<目录名>-<YYYY-MM-DD>.html`，**文件名与 D7 同样跟随界面语言**），**根目录非隐藏目录**；同名加 `_2` 后缀不覆盖 | 它就是要被找到并发出去的 |
| D6 | **绝对路径一律不出现在报告中**，只显示文件名 | `/Users/<用户名>/...` 属隐私，与 GPS 同理 |
| D7 | **报告语言跟随导出时的界面语言**（`zh_CN`/`en_US`），不做单独语言选择 | 复用现有 i18n，无新增决策点 |
| D8 | **画廊按鸟种分组，每种最多 4 张**（1 张大代表作 + 3 张小图），区块按罕见度降序；**鸟种数不封顶**，多少种就出多少种 | 读者的问题是「拍到了哪些鸟、每种长什么样」，不是「有多少张好片」；原「鸟种屏 + 精选画廊」两屏本就重复展示同一批代表作 |
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

- **总体统计**：总数、各星级数、`picked` 数、命中率（3★/总数）、飞版数、精焦数
- **鸟种榜**：按 `bird_species_cn/en` 分组 → 张数、最高星、`gbif_rarity_100` 分级、
  `iucn_category`、`aesthetic_index`
- **每种展示图**：组内按 `picked=1` > `rating` 最高 > `adj_topiq` 最高排序，
  **取前 4 张**（首张为代表作，见 D8）；不足 4 张者全取
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

**② 鸟种画廊**（原「鸟种屏」与「精选画廊」合并为一屏，见 D8）

每种鸟一个区块，区块按**罕见度降序**排列——战报的价值在「拍到了罕见的」，
而非「对着常见鸟按了 200 张」。

区块头：中英文名 + 张数 + 罕见度徽标（`gbif_rarity_100` 经
`core/rarity_tier.py` 的 `tier_name_color` 着色）+ IUCN 徽标
（**仅 `VU`/`EN`/`CR`/`CR(PE)`/`CR(PEW)`/`EW`/`EX` 显示；
`LC`/`NT`/`DD`/`NE` 不显示**以免满屏噪音）。

区块体：**最多 4 张**——1 张大代表作 + 3 张小图，不足 4 张者全出。
选取优先级同 4.3：`picked=1` > `rating` 最高 > `adj_topiq` 最高，
代表作取该序列首张。

点击任意图放大，用原生 JS lightbox（约 30 行，无外部依赖）。
每张底部一行小字：`白腹海雕 · 1/2000s · f/5.6 · ISO 640 · 600mm`
——参数是鸟友之间真正会讨论的东西。

**③ 数据区**

星级分布条形图用纯 CSS 宽度百分比绘制（不引图表库）。含：命中率、飞版数、
精焦数、连拍 N 组均 M 张、器材榜（机身/镜头/最常用焦距/ISO 区间）。

**④ 折叠明细**（原生 `<details>`）

表格 12 列：缩略图 / 文件名 / 鸟种 / 星 / 精选 / 锐度 / 美学 / ISO / 快门 /
光圈 / 焦距 / 拍摄时间。表头点击排序（原生 JS 约 20 行）。

**⑤ 页脚**

`由 SuperPicky v<版本> 生成 · <生成时间>` + 项目地址。
报告被转发时，这是工具本身唯一的传播入口。

### 5.2 打印适配 / Print support（D9）

页顶固定一个「存为 PDF」按钮，`onclick` 调 `window.print()`。
配套 `@media print` 需做到四件事，缺一则打印结果不可用：

1. 转白底黑字（深色底打印会输出整页黑）
2. `page-break-inside: avoid` 施加于鸟种区块与明细表行，避免图文被拦腰截断
3. 自动展开 `<details>` 折叠区（`details { display: block } summary { display: none }`）
4. 隐藏交互控件（「存为 PDF」按钮本身、lightbox 容器、明细表排序箭头）

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

**解法：所有图片一律不写进 `src`，改为 JS 字符串数组 + 视口懒插入。**

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

字符串不是图像，浏览器不会解码。约 20 行原生 JS，`IntersectionObserver` 是标准
API，离线可用、无外部依赖。lightbox 放大图同理，点击时才 `new Image().src = HD[i]`。

**关键性质：常驻位图恒定为视口内的几屏（约 20~40MB），与鸟种数、照片数完全无关。**
这正是 D8 得以「鸟种数不封顶」的前提——没有这一条，40 个鸟种即达 178MB，
100 个鸟种达 344MB，手机必然崩溃。

文件体积仍随内容线性增长（这是自包含单文件绕不开的代价），
由 6.4 的导出前预估告知用户，而非静默产出一个发不出去的文件。

### 6.2 分档规格 / Size tiers

基准样本：318 张照片、12 个鸟种、48 张展示图（12×4）。

| 用途 | 长边 | JPEG 质量 | 典型数量 | 单张 | 小计 |
|---|---|---|---|---|---|
| 封面 | 1800 | 82 | 1 | 400 KB | 0.4 MB |
| 鸟种代表作 | 900 | 80 | 12 | 110 KB | 1.3 MB |
| 鸟种小图 | 400 | 78 | 36 | 32 KB | 1.2 MB |
| lightbox 放大 | 1200 | 80 | 48 | 150 KB | 7.2 MB |
| 明细缩略图 | 160 | 72 | 318 | 12 KB | 3.8 MB |
| | | | | 小计 | 13.9 MB |
| | | | | base64 ×1.33 | **≈ 18.5 MB** |

按 6.1，**全部五档一律存放于 JS 字符串数组**，DOM 中不含任何 `src`。
上表的"数量"随内容线性变化，是估算的输入而非固定值。

**体积大头是 lightbox 放大图（7.2MB）与明细缩略图（3.8MB），不是展示图。**
因此 D8 把每种张数从 6 降到 4 只省约 2MB——该决策的依据是信息架构（报告该短该精），
不是体积。若日后确需压缩体积，有效手段是降低放大图规格或关闭明细缩略图，
而非继续削减每种张数。

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

**内存侧无需保护**：6.1 的懒插入已让常驻位图与内容量脱钩，恒定在 20~40MB。
因此 D8 明确不对鸟种数封顶——多少种就出多少种，不把任何一种降级为纯文字。

**仅剩文件体积需要管理**，且手段是告知而非砍内容：

- **导出弹窗先估算再确认**：按 6.2 的单张均值 × 实际数量估算，
  显示「预计生成约 N MB，用时约 M 秒」。用户看到 113MB 可自行决定是否拆分目录，
  而不是静默产出一个发不出去的文件
- **超过 80MB 时弹窗额外提示**：常见 IM 与邮件附件上限在 100MB 附近，
  提示文案给出「仍然导出 / 取消」两个选择，不代替用户决策
- **照片总数 > 600 时，明细表退化为纯文字**（去掉缩略图列），页面标注原因。
  此项针对的是**文件体积**而非内存：3000 张明细缩略图即 36MB，
  而明细区是自己复盘用的，不值得为它把分享用的文件撑大一倍

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
| 7 | 无图写进 DOM | 输出中**不存在任何** `<img ... src="data:`；所有 data URI 只出现在 JS 数组，且 `<img>` 均带 `data-idx` |
| 8 | EXIF 旋转 | 造 `Orientation=6` 的图，断言输出宽高互换 |
| 9 | 规模保护 | 40 个鸟种 → 40 个区块全部出图、无一降级为纯文字；700 张 → 明细表无缩略图列 |
| 10 | 优雅降级 | 全 `no_bird` / 无时间 / 无 GPS，各自不留空标题 |
| 11 | 单张失败不中断 | 混入损坏文件，报告仍完整生成 |
| 12 | 中文往返 | 目录名 + 鸟种名含中文，UTF-8 写入读回逐字一致 |
| 13 | 原子写 | 成功后无 `.tmp` 残留；模拟中途失败不产出成品文件 |
| 14 | 打印样式 | 输出含 `@media print`，且其中有白底、`page-break-inside: avoid`、展开 `details`、隐藏交互控件四项 |
| 15 | IUCN 徽标门槛 | `LC`/`NT`/`DD`/`NE` 不渲染徽标；`VU` 及以上渲染 |

UI 接线另置一个测试，参照现有 `test_species_merge_entry.py` 写法：
仅验证按钮存在与信号连接，不执行真实导出。

**最低验证**（依 CLAUDE.md）：改动的 Python 文件执行 `py_compile`；
中文样本往返验证；T6 需在真机手机浏览器打开一次，对账第 6 节的
18.5MB 文件体积估算；并用开发者工具确认滚动全程常驻位图稳定在 20~40MB
（这是 6.1 懒插入是否真正生效的唯一硬证据），最后在真机上走一遍「存为 PDF」。

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
