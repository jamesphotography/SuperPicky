# SuperPicky 4.3.0 LTS

**4.3.0 is a Long-Term Support (LTS) release** — the new stable baseline that
consolidates every major improvement made since 4.1.0. If you're on an older
build, this is the recommended version to settle on.

---

## 🎬 New in 4.3.0

**Video Bird Analysis** *(headline feature)*
- Analyze birds directly in video footage, not just stills
- Automatic per-species grouping, with synchronized SRT subtitle handling
- New dedicated "Video Processing" tab in Settings

**Global Rarity Index** *(new GBIF-based scoring)*
- Every identified bird now gets a 0–100 global rarity score derived from GBIF
  occurrence data (3 billion+ records), with an IUCN-status floor
- Shown in the detail panel as a 5-tier glyph (○ ◔ ◑ ◕ ●) — Common / Occasional /
  Uncommon / Rare / Legendary — and written to a dedicated EXIF field
- Batch runs print a rarity-tier distribution summary, so the standout shots are
  easy to spot
- Replaces the previous rarity source with an open, citable GBIF-derived system

**More Reliable First Launch (rewritten download & runtime pipeline)**
- Faster, sturdier first-run setup: parallel mirror probing, multi-strategy
  downloads with automatic fallback and resume-on-interrupt
- Switched the packaged Python toolchain to `uv` for much faster, more reliable
  AI-runtime installation — a big improvement for mainland-China and slow networks

**Completion Sound**
- Optional sound when a batch finishes, so you can step away during long runs

**Streamlined Updates**
- In-app update checking is now turned off across all builds — to upgrade, download the new version from the official download page. This replaces the previous in-app auto-update and sidesteps stale-patch issues after upgrades.
- Windows installers now wipe old program files before installing; a new Uninstaller tool is provided for switching between Lite and Full builds.

**Reset & Organization**
- New **Advanced Reset**: when a folder has no manifest (older or cross-version
  directories, or the new species-first layout), Reset offers to recognize
  SuperPicky's species / rating / burst folders and move every photo back to the
  selected directory — move-only, never overwriting same-named files, and your
  own folders are left untouched.
- Reset is now strictly non-destructive: same-name conflicts are skipped (never
  overwritten) and folders are only removed once empty.
- Burst (連拍) grouping now works correctly under the new species-first layout.
- Video species names and SRT subtitles now follow the interface language
  (an English UI shows English names); video organization is reversible via Reset.

**Polish**
- Browse now opens at the currently selected directory (falls back to Pictures).
- macOS installer (.pkg in .dmg) is signed and notarized through a more reliable
  CI signing path.

**Intel Mac Is Back on the Latest (4.3.0)**
- Intel Macs now default to CPU (FP32). The legacy MPS path on old AMD dGPUs was
  actually slower — running FP16 poorly and falling back to CPU for YOLO anyway.
  Forcing CPU restores smooth performance beyond the 4.1.0 baseline, so Intel
  users can move up from 4.2.1.

---

## Highlights since 4.1.0 (the 4.2.x line)

- **Smart first-run wizard** with automatic AI-runtime selection (CUDA for NVIDIA, CPU otherwise)
- **Windows Lite installer** (~190 MB) + a separate CUDA GPU package
- **One-click in-app updates** with background download and integrity check *(replaced in 4.3.0 — updates now go through the official website; see "Streamlined Updates" above)*
- **Environment Repair** in Settings — re-run model prep without reinstalling
- **ExifTool 13.55** — better RAW support for the latest cameras
- **Smarter mirror selection** — optimized routing for China, official sources overseas
- **IOC bird-name search** — standalone CN/EN lookup
- **Keypoint model slimmed** ~283 MB → ~95 MB for faster loading
- **Recursive subfolder batch processing** in both CLI and GUI; directory switching + recent history
- **Star-rating sync** back to the original file's EXIF Rating
- **macOS**: fixed memory pressure on long batches — thousands of photos stay steady throughout
- Many stability fixes: Chinese-path compatibility, Windows console encoding, macOS packaging paths, ExifTool process cleanup

---

## Distribution Notes
- **Apple Silicon Mac**: single full installer (`.dmg`) — see Release assets
- **Intel Mac**: a dedicated **v4.3.0** full installer (`.dmg`) is now available.
  It runs on CPU (FP32); we removed the legacy MPS/AMD-dGPU path that was actually
  slower, restoring smooth performance beyond the 4.1.0 baseline. Intel users no
  longer need to stay on 4.2.1.
- **Windows** — we recommend the **Full** builds (bundled AI runtime, works out of
  the box, no first-run download):
  - **CPU Full** — runs on any PC.
  - **GPU (CUDA) Full** — for NVIDIA GPUs; distributed via Google Drive / Baidu
    Netdisk due to its large size.
  - The **Lite** installer (~190 MB) still covers all configurations, downloading
    the AI runtime on first launch — fine when your network is reliable.

---

# SuperPicky 4.3.0 LTS（中文）

**4.3.0 是长期支持（LTS）稳定版** —— 汇总了自 4.1.0 以来的所有重要改进，作为新的稳定基线。仍在旧版本的用户，建议升级到此版本长期使用。

---

## 🎬 4.3.0 全新功能

**视频鸟类分析**（核心新功能）
- 不再局限于静态照片，可直接分析视频中的鸟类
- 自动按鸟种归类，并同步处理 SRT 字幕
- 设置中新增独立的「视频处理」标签页

**全球罕见度指数**（全新 GBIF 评分）
- 每只识别出的鸟现在都有一个 0–100 的全球罕见度分数，基于 GBIF 全球观察数据（30 亿+ 记录），并以 IUCN 濒危等级兜底
- 详情面板以 5 级图标呈现（○ ◔ ◑ ◕ ●）：常见 / 能见 / 少见 / 罕见 / 传奇，并写入独立的 EXIF 字段
- 跑批结束输出罕见度分级分布统计，一眼挑出最难得的那张
- 从旧的罕见度来源全面切换到开放、可引用的 GBIF 派生体系

**更可靠的首次启动（下载与运行时链路重写）**
- 首启准备更快更稳：并行镜像探测、多策略下载、自动回退与中断续传
- 打包的 Python 工具链改用 `uv`，AI 运行时安装显著更快更稳 —— 对中国大陆与慢速网络改善明显

**完成提示音**
- 批量处理完成时可选播放提示音，长任务期间可放心离开

**简化的升级方式**
- 所有版本均已关闭应用内更新检测 —— 升级请前往官网下载页获取新版本。这取代了原先的应用内自动更新，并规避了升级后旧补丁覆盖新代码的问题。
- Windows 安装包升级时会先清空旧程序文件；并新增卸载工具，用于在 Lite 与 Full 之间切换。

**重置与整理**
- 全新**高级重置**：当文件夹没有 manifest（较旧或跨版本目录，或新的鸟种优先布局）
  时，重置会尝试识别 SuperPicky 的鸟种 / 评级 / 连拍文件夹，并把每张照片移回所选
  目录 —— 仅移动、绝不覆盖同名文件，你自己的文件夹保持不动。
- 重置现严格非破坏性：同名冲突一律跳过（绝不覆盖），文件夹仅在清空后才删除。
- 连拍（連拍）分组在新的鸟种优先布局下也能正确工作。
- 视频鸟种名称与 SRT 字幕现跟随界面语言（英文界面显示英文名）；视频整理可通过
  重置可逆复原。

**细节打磨**
- 浏览现在会定位到当前所选目录（无则回退到「图片」文件夹）。
- macOS 安装器（.dmg 内的 .pkg）通过更可靠的 CI 签名链路完成签名与公证。

**Intel Mac 重回最新版（4.3.0）**
- Intel Mac 现默认走 CPU（FP32）运行。此前在老款 AMD 独显上误用 MPS 反而更慢
  （FP16 表现差、YOLO 还得回退 CPU 重算），改为强制 CPU 后性能恢复并超过 4.1.0
  水平，Intel 用户可从 4.2.1 升级上来。

---

## 自 4.1.0 以来的重点更新（4.2.x 系列）

- **智能首启向导**，自动选择 AI 运行引擎（NVIDIA 走 CUDA，其余走 CPU）
- **Windows Lite 安装包**（约 190 MB）+ 独立的 CUDA GPU 包
- **一键应用内升级**：后台下载 + 完整性校验 *（4.3.0 起已改为前往官网手动更新，详见上方「简化的升级方式」）*
- **环境修复**：设置内一键重跑模型准备，无需重装
- **ExifTool 13.55**：更好支持最新相机的 RAW
- **更智能的镜像选择**：大陆优化路由，海外走官方源
- **IOC 鸟名检索**：独立的中英文鸟名查询
- **关键点模型瘦身** 约 283 MB → 95 MB，加载更快
- **子目录递归批处理**（CLI 与 GUI 均支持）；浏览器支持目录切换与最近目录历史
- **星级同步**：评分修改写回原始文件的 EXIF Rating
- **macOS**：修复长批量处理的内存压力 —— 数千张照片全程稳定
- 大量稳定性修复：中文路径兼容、Windows 控制台编码、macOS 打包路径、ExifTool 进程清理

---

## 分发说明
- **Apple Silicon Mac**：单一完整安装包（`.dmg`），见 Release 资产
- **Intel Mac**：现已提供 **v4.3.0** 完整安装包（`.dmg`）。该版本走 CPU（FP32）
  运行，我们移除了反而更慢的老款 MPS/AMD 独显路径，性能恢复并超过 4.1.0 水平。
  Intel 用户无需再停留在 4.2.1。
- **Windows**：推荐下载**完整版（Full）**（内置 AI 运行时，开箱即用，无需首启下载）：
  - **CPU 完整版** —— 适用于所有电脑。
  - **GPU（CUDA）完整版** —— 面向 NVIDIA 显卡；因体积较大，通过 Google Drive /
    百度网盘分发。
  - **Lite** 安装包（约 190 MB）仍覆盖所有配置，首次启动时在线下载 AI 运行时，
    网络良好时适用。
