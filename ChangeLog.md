# SuperPicky 4.5.0 RC8

**What's new since RC7:**

- **Best-of-burst pick, re-scored.** The frame chosen to represent a burst
  group is now selected by a tiered score — first the arbitrated focus tier,
  then eye clarity plus head sharpness — instead of head sharpness alone.
  This surfaces a sharper, better-focused keeper as the group's cover shot.
- **Species editing moved to the right-click menu.** The always-on edit
  pencil on grid tiles is gone; species correction/assignment now lives in
  each tile's right-click menu ("Edit Species…") and works for every photo,
  including ones without a name yet. Tile labels are cleaner as a result —
  a single line (species or filename), with the filename shown on hover.

---

# SuperPicky 4.5.0 RC7

**What's new since RC6:**

- **Faster, lighter full-screen browsing.** The full-screen viewer's preview
  pipeline was reworked. A resident parallel preload pool keeps held-arrow-key
  navigation on cache (0/25 → 25/25 reads), and high-resolution caching is now
  capped at the 3200px long edge and back-filled on a 250ms dwell — cutting
  resident memory by roughly 2.4 GB on large libraries. Also removed ~1050
  lines of dead results-browser code.
- **Settings Center additions.**
  - **Legacy V1 rating (opt-in).** A new "Advanced" section exposes the old
    absolute-threshold star rating (V1) for anyone who prefers it over the
    batch-relative V2 engine; the V2-only sliders hide when it is on.
  - **Bird-name display format.** Choose how species names are shown.
  - **Delete-confirmation toggle.** Turn the "confirm before deleting a photo"
    dialog on or off.
  - **Clear all preview caches.** A one-click button removes the current
    directory's AI preview/crop cache (`.superpicky/cache`) and the now-dangling
    cache paths in `report.db`, without touching your original photos.

---

# SuperPicky 4.5.0 RC6

**What's new since RC5** (the sections below are the full cumulative 4.5.0
notes):

- **No-bird rescue scan (new).** When the first detection pass finds no bird
  at the default resolution, SuperPicky rescans at 1024px with a low
  threshold and uses the Bird ID classifier as a gatekeeper — recovering
  birds that YOLO missed (small, distant, or confused with airplane/kite)
  without letting false positives through. Toggle in Settings → Picking.
- **iRateBird species aesthetic index (new).** An offline, CC-BY beauty
  score (0–100) per species, shown in the detail panel and available as a
  filter/sort key. It is display-and-sort only and independent of the
  per-photo TOPIQ aesthetic score that drives star ratings.
- **Species correction entry (#106).** An edit pencil on the grid and detail
  cards lets you fix a misidentified species; candidate cards now follow the
  interface language.
- **Focus sharpness arbitration (#107).** When the EXIF focus-point verdict
  says a shot is soft but the measured bird-head sharpness clears your
  threshold, pixel evidence wins and the verdict is upgraded — fewer sharp
  keepers wrongly demoted.
- **Processing ~30% faster** (measured: 495 ARW, 135s → 95s). Proprietary
  RAW metadata now writes to XMP sidecars instead of rewriting the RAW body;
  fixed a bug that fully rewrote cached preview JPEGs on every photo, and a
  silent sidecar temp-file write failure.
- **Fixes.** English filter panel no longer clips the 0★ chip; the species
  aesthetic score is shown without a "/100" suffix.

---

# SuperPicky 4.5.0

**4.5.0 is a focus release.** Building on the 4.3.0 LTS baseline, it
concentrates on five things: a brand-new batch-relative star rating engine,
a Lightroom-friendly flat workflow, making the core culling pipeline faster
and more dependable, unifying all settings into a brand-new Settings Center,
and streamlining the interface around the core workflow.

---

## ⭐ Batch-Relative Star Rating (new)

- Star ratings are now assigned **relative to the current batch** instead of
  fixed absolute thresholds: photos that pass the hard gates (bird present,
  minimum confidence / sharpness, visible keypoints) are ranked by a combined
  quality score (sharpness + aesthetics percentiles, small bonuses for flight
  and precise focus), and the best N% get 3 stars.
- The 3-star quota is adjustable (5–50%, default 20%) and mapped to the skill
  levels (Beginner 25% / Intermediate 20% / Master 8%); a single quota slider
  replaces the sharpness / aesthetics threshold sliders on the home panel and
  in the Settings Center.
- With Bird ID enabled the quota is applied **per species** — every species
  keeps its own best shots (a rare species keeps at least its single best
  photo), so a long burst of one common bird no longer crowds out the others.
  Note: the more species in a batch, the further the effective 3-star share
  can round up above the quota value.
- Aesthetic scoring (TOPIQ) now evaluates the bird crop instead of the whole
  frame, so backgrounds no longer dominate the score.
- Absolute floors remain: 3 stars still require a minimum normalized
  sharpness, low eye visibility caps a photo at 2 stars, and burst groups
  keep only a limited number of 3-star photos.
- While processing, the log and preview show metrics only; final stars are
  assigned in a single pass at the end — no more ratings jumping around
  mid-run.
- The legacy absolute-threshold algorithm is still available: Settings →
  Culling → "Rating Algorithm" cards let you switch back to V1 (default V2).

## 🗂 Flat Layout & Burst Control (new)

- **New "Flat" folder layout — rate in place, no file moves.** Settings →
  Output → Folder layout now offers a third option: photos are detected,
  rated and tagged as usual (EXIF ratings, keywords, picks, XMP sidecars for
  Sony RAW), but **no files are moved** — your Lightroom folder references
  stay intact. Browsing and filtering by stars / species / focus / burst in
  the Results Browser works exactly the same (it reads the app's own
  database, not the folder structure). Rating or species changes made in the
  browser also leave files in place under this layout.
- **Burst detection decoupled from burst subfolders.** A new toggle
  (Settings → Culling → "Group bursts into subfolders", default on) lets you
  keep burst detection — grouping in the browser, per-burst 3-star cap —
  while filing burst shots like normal photos instead of `burst_NNN`
  subfolders.

## 🏷 Color Labels — New Defaults (please note)

The default XMP color-label mapping is now intuitive — green means good,
red means bad:

| Condition | Old label | New label |
|---|---|---|
| Bird in flight | Green | **Blue** |
| Critical focus | Red | **Green** |
| Soft / out of focus | (none) | **Red** |
| Good focus / no bird | (none) | (none) |

A photo carries one label; flight takes priority. **If you built Lightroom
smart collections on "green = flying", update them to blue.**

## 🔑 Species Keywords (new)

- High-confidence Bird ID results are now written to the photo's standard
  keywords (`XMP-dc:Subject`) as well as the Title, so you can filter by
  species in Lightroom's keyword panel. Writes are merge-add: your own
  keywords are never touched, and re-running a folder never duplicates.
  Toggle: Settings → Bird ID → "Write species to photo keywords" (default
  on).

## ⚡ Performance

- **ExifTool now runs as two dedicated persistent processes (read / write).**
  Batch metadata writes no longer block reads, eliminating a recurring
  ~3-second stall every ~30 photos (real-world test, 152 photos: total time
  75.5 s → 62.1 s).
- Aesthetic scoring (TOPIQ) uses a new two-stage downscale — noticeably faster
  on large images.
- Fixed a side effect where importing the detection stack limited OpenCV to a
  single thread; 45 MP image decoding is back to full speed.
- Reduced repeated image decoding and debug I/O in the main pipeline.
- The Bird ID resident service now lazy-loads its model stack — the service
  port is ready within seconds of app launch, so the Lightroom plugin no
  longer waits 10+ seconds to connect.

## 🐦 Core Culling Pipeline

- Fixed a batch of stability/accuracy issues in the main culling flow and
  hardened model-file integrity checks.
- A photo that fails processing is no longer mistakenly marked as completed —
  it is counted as failed and will be retried on resume.
- Fixed aesthetic-score pre-scaling on extreme aspect ratios (downscale only,
  never upscale).
- Intel Macs now default to CPU, sidestepping MPS pitfalls on older AMD GPUs.
- The completion report uses a consistent timing definition and now shows
  start and finish wall-clock times, so the total can be verified against a
  clock.
- Main-window position and state persist across sessions, including after
  restoring from the system tray (community PRs #104 / #105 — thank you!).

## 🔎 Bird ID & Lightroom Plugin

- GPS coordinates of exactly 0.0 (equator / prime meridian) are no longer
  treated as "no GPS".
- GPS / RAW preview extraction goes through the shared ExifTool persistent
  process — fixes console-window flashing on Windows.
- Closing the main window now hides it to the tray as a true resident app,
  fixing "Lightroom plugin can't reach the resident service".
- Fixed the plugin's write-bird-name / write-caption to EXIF, which had been
  silently broken; also fixed an IPTC encoding trap where Chinese text could
  be stored or read back as Latin-1 garbage.
- Bird ID panel: restored the data-source / country / region quick controls;
  unified rarity-icon sizing and cleaner region display.

## ⚙️ Settings Center (new)

- All scattered settings dialogs are unified into a single Settings Center
  with left-side navigation: Culling / Bird ID / Output / External Apps /
  About.
- One single source of truth for every setting; the home-page Quick Adjust
  panel (sharpness & aesthetics sliders + flight / burst / Bird ID toggles)
  stays in two-way sync with the Settings Center.
- Skill level and thresholds are linked — manually editing a threshold
  switches to "Custom" automatically.
- The Settings entry now lives in the Settings menu, with a new shortcut:
  Cmd+, (macOS) / Ctrl+, (Windows).
- Fixed radio-button selection being invisible in Windows dark mode.
- Fixed a light-mode background glitch in the Settings Center; the aesthetics
  threshold is now correctly labeled TOPIQ.

## 🖼 Results Browser

- Change a photo's species directly in the browser — files move to the
  matching species folder automatically.
- Changing a star rating moves the files to the matching rating folder.
- "Picked" is now a persistent flag with a crown badge on thumbnails.
- Full SVG iconography (stars, filter chips, toolbars, focus status), and the
  full-screen view gains a Photoshop-style left toolbar.
- Fixed thumbnails not refreshing their badges after pick/rating changes;
  burst badges are merged into a single indicator.
- **Keyboard rating**: press 0–3 to set a photo's stars directly, Up/Down to
  step the rating by one (Left/Right still navigate). Works in grid and
  full-screen view.
- Thumbnails show the species name **and** the filename (two lines) instead
  of replacing one with the other; the detail panel gains a species row just
  above GBIF Rarity (click to copy the name).
- Focus terms are now consistent on both sides of the browser: Critical
  Focus / Good Focus / Soft (the filter chips previously showed raw
  BEST/GOOD/BAD in the English UI).

## 🧹 Reset & Organization

- Reset ignores hidden files and OS metadata leftovers.
- Fixed burst-folder members being left behind under the species-first
  layout; Reset now ends with an unconditional flatten pass as a safety net.

## ✂️ Streamlined Interface

To keep the app focused on the core culling workflow, this release removes
the UI entry points for several non-core features: in-app update checks,
smart enhance, crop suggestions, video analysis, and correction submission.
If you used Video Bird Analysis in 4.3.0, its menu is intentionally absent in
4.5.0 — this is not a bug. The underlying implementations remain in the
codebase and may return in a future release.

---

## Distribution Notes

- **Apple Silicon Mac**: single full installer (`.dmg`) — see Release assets.
- **Intel Mac**: dedicated full installer (`.dmg`), running on CPU (FP32).
- **Windows** — we recommend the **Full** builds (bundled AI runtime, works
  out of the box, no first-run download):
  - **CPU Full** — runs on any PC.
  - **GPU (CUDA) Full** — for NVIDIA GPUs; distributed via Google Drive /
    Baidu Netdisk due to its large size.
  - The **Lite** installer (~190 MB) still covers all configurations,
    downloading the AI runtime on first launch — fine when your network is
    reliable.

---

# SuperPicky 4.5.0 RC6（中文）

**RC5 以来的新增**（下方为 4.5.0 累积完整说明）：

- **无鸟补救扫描（全新）。** 第一遍默认分辨率检测无鸟时，用 1024px 低阈值
  重扫、并以识鸟分类器守门——救回被 YOLO 漏检的鸟（小、远、或与飞机/风筝
  混淆），同时挡住误检。开关在 设置 → 精选。
- **iRateBird 鸟种颜值指数（全新）。** 离线 CC-BY 的鸟种颜值分（0–100），
  在详情面板展示、并可作为筛选/排序键。它仅用于展示与排序，与驱动评星的
  单张 TOPIQ 美学分相互独立。
- **鸟种纠错入口（#106）。** 网格卡与详情卡上新增编辑铅笔，可修正识别错误
  的鸟种；候选卡片跟随界面语言。
- **对焦锐度仲裁（#107）。** 当 EXIF 对焦点判定为脱焦、但实测鸟头锐度已过
  阈值时，以像素证据为准升级判定——减少清晰好片被误降级。
- **处理提速约 30%**（实测：495 张 ARW，135 秒 → 95 秒）。专有 RAW 元数据
  改写 XMP 侧车而非重写 RAW 本体；修复了每张照片都完整重写缓存预览 JPEG
  的 bug，以及侧车临时文件导致的静默写入失败。
- **修复。** 英文筛选面板不再裁掉 0★ 筹码；鸟种颜值分去掉「/100」后缀。

---

# SuperPicky 4.5.0（中文）

**4.5.0 是一个聚焦版本。** 在 4.3.0 LTS 的基础上，集中做了五件事：全新的
批内相对评星引擎、Lightroom 友好的平铺工作流、让选鸟主流程更快更稳、
把所有设置统一进全新的设置中心、并围绕核心工作流精简界面。

---

## ⭐ 批内相对评星（全新）

- 星级改为**批内相对**分配，不再依赖固定绝对阈值：通过硬门槛（有鸟、
  最低置信度/锐度、关键点可见）的照片按综合质量分排序（锐度+美学百分位，
  飞行/精准对焦小幅加分），排名最好的前 N% 获得 3 星。
- 3 星配额可调（5–50%，默认 20%），与摄影水平档位联动（新手 25% /
  进阶 20% / 大师 8%）；首页与设置中心用单一「3星配额」滑块取代原来的
  锐度/美学阈值双滑块。
- 开启识鸟时配额**按鸟种分组**执行——每个鸟种都保住自己最好的照片
  （罕见鸟至少保底 1 张），常见鸟的大连拍不再挤占其他鸟种的 3 星名额。
  注意：一批照片鸟种越多，向上取整后的实际 3 星占比会越高于配额面值。
- 美学评分（TOPIQ）改为针对鸟体裁剪区打分，背景不再干扰分数。
- 绝对底线保留：3 星仍要求最低归一化锐度，眼睛不可见封顶 2 星，
  连拍组内 3 星限量。
- 处理过程中日志与预览只显示指标，星级在收尾阶段一次性统一分配，
  不再出现处理中星级跳动。
- 旧版绝对阈值算法仍可切换：设置中心 → 精选 →「评星算法」卡片可
  切回 V1（默认 V2）。

## 🗂 平铺布局与连拍控制（全新）

- **新增「平铺」目录布局——识别评分但不移动文件。** 设置中心 → 输出 →
  分类目录布局新增第三项：照片照常检测、评星、打标签（EXIF 星级、关键字、
  精选旗标，索尼 RAW 走 XMP 侧车），但**所有文件留在原地**——Lightroom
  已导入目录的引用不受影响。结果浏览器按星级/鸟种/对焦/连拍的浏览筛选
  完全不变（它读应用自己的数据库，不依赖目录结构）；平铺下在浏览器里
  改星/改鸟种同样不移动文件。
- **连拍检测与子目录归档解耦。** 精选页新增开关「连拍归入独立子文件夹」
  （默认开）：关闭后保留连拍检测——浏览器分组、连拍组内 3 星限量照常——
  但连拍照片按各自星级/鸟种正常归档，不再产生 `burst_NNN` 子目录。

## 🏷 颜色标签——新默认映射（请注意）

XMP 色标默认映射改为符合直觉——绿=好、红=差：

| 条件 | 旧色标 | 新色标 |
|---|---|---|
| 飞鸟 | 绿色 | **蓝色** |
| 精准合焦 | 红色 | **绿色** |
| 脱焦/失焦 | （无） | **红色** |
| 普通合焦 / 无鸟 | （无） | （无） |

一张照片只有一种色标，飞鸟优先。**如果你在 Lightroom 建过「绿色=飞鸟」
的智能收藏夹，请改为蓝色。**

## 🔑 鸟种关键字（全新）

- 高置信度识鸟结果除写 Title 外，同步写入标准关键字（`XMP-dc:Subject`），
  Lightroom 关键字面板可直接按鸟种筛选。写入为合并追加：你自己打的关键字
  绝不会被动，重跑同一目录也不会产生重复。开关：设置中心 → 识鸟 →
  「识别后写入照片关键字」（默认开）。

## ⚡ 性能

- **ExifTool 读写分离为两个专属常驻进程。** 批量写元数据不再阻塞读取，
  消除了每约 30 张照片卡顿 3 秒的问题（152 张实测：总耗时 75.5 秒 →
  62.1 秒）。
- 美学评分（TOPIQ）接入两段式缩放预降，大图打分明显提速。
- 修复检测组件导入后 OpenCV 被限制为单线程的副作用，4500 万像素大图解码
  速度恢复。
- 减少主流程中重复的图像解码与调试 I/O。
- 识鸟驻留服务改为懒加载，应用启动后端口秒级就绪，Lightroom 插件不再等待
  十几秒。

## 🐦 选鸟主流程

- 修复选鸟主流程一批稳定性/准确性问题，并加固模型文件完整性校验。
- 单张照片处理异常不再被误标为「已完成」：计入失败统计，续跑可重试。
- 修复极端长宽比图片美学评分预缩放方向错误（只降不升）。
- Intel Mac 强制走 CPU，绕开老 AMD 显卡 MPS 的兼容陷阱。
- 完成报告计时口径统一，并显示开始/结束时间，总耗时可对表自验。
- 主窗口位置与状态持久化，从托盘重新显示后位置保存正常
  （社区 PR #104 / #105，特此致谢）。

## 🔎 识鸟 / Lightroom 插件

- 修复 GPS 坐标恰好为 0.0（赤道/本初子午线）被误判为无 GPS。
- GPS / RAW 预览提取统一走 ExifTool 常驻进程，修复 Windows 下控制台窗口
  闪烁。
- 关闭主窗口改为隐藏到托盘真驻留，修复 Lightroom 插件连不上驻留服务。
- 修复插件写鸟名/描述到 EXIF 长期静默失效的问题；并修复 IPTC 中文被按
  Latin-1 存储/误读的编码陷阱。
- 识鸟面板恢复数据源/国家/区域快速控件；罕见度图标大小统一、地区显示优化。

## ⚙️ 设置中心（全新）

- 分散的设置窗口统一为设置中心：精选 / 识鸟 / 输出 / 外部应用 / 关于
  五页，左侧导航。
- 所有设置单一存储源；首页「快速调整」面板（锐度/美学滑块 + 飞行/连拍/识鸟
  开关）与设置中心双向同步。
- 技能等级与阈值联动，手动改阈值自动切换为「自定义」。
- 设置入口固定在设置菜单，新增 Cmd+,（macOS）/ Ctrl+,（Windows）快捷键。
- 修复 Windows 深色界面下单选按钮选中状态不可见。
- 修复浅色模式下设置中心背景露灰；美学阈值标注更正为 TOPIQ。

## 🖼 选鸟结果浏览器

- 支持直接修改鸟种，文件联动移动到对应鸟种目录。
- 修改星级后文件自动移动到对应星级目录。
- 「精选」改为持久化旗标，缩略图显示皇冠角标。
- 界面图标全面 SVG 化（星级/筛选筹码/工具栏/对焦状态），大图模式改为
  PS 风格左侧工具栏。
- 修复精选/改星后缩略图角标不刷新；连拍角标合并显示。
- **键盘打星**：数字键 0-3 直接设星，Up/Down 星级 ±1（Left/Right 仍为
  翻图）；网格与大图模式均可用。
- 缩略图**同时显示鸟名与文件名**（两行），不再互相替换；右侧详情面板
  在全球罕见度上方新增鸟种行（点击可复制鸟名）。
- 浏览器左右两侧对焦用语统一：精焦 / 合焦 / 失焦（英文界面此前左侧
  显示的是原始枚举 BEST/GOOD/BAD）。

## 🧹 重置与整理

- 重置时忽略系统隐藏文件与 OS 元数据残留。
- 修复「按鸟种优先」布局下连拍目录成员残留，重置末尾补摊平兜底。

## ✂️ 界面精简

为聚焦核心选鸟工作流，本版本移除了若干非核心功能的界面入口：应用内更新
检查、智能修图、裁剪建议、视频分析、纠错提交。如果你在 4.3.0 用过
「视频选鸟」，其菜单在 4.5.0 中是有意移除的，并非 bug。相关功能实现仍
保留在代码中，未来版本可能重新开放。

---

## 分发说明

- **Apple Silicon Mac**：单一完整安装包（`.dmg`），见 Release 资产。
- **Intel Mac**：独立完整安装包（`.dmg`），走 CPU（FP32）运行。
- **Windows**：推荐下载**完整版（Full）**（内置 AI 运行时，开箱即用，无需
  首启下载）：
  - **CPU 完整版** —— 适用于所有电脑。
  - **GPU（CUDA）完整版** —— 面向 NVIDIA 显卡；因体积较大，通过
    Google Drive / 百度网盘分发。
  - **Lite** 安装包（约 190 MB）仍覆盖所有配置，首次启动时在线下载 AI
    运行时，网络良好时适用。
