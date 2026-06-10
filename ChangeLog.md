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
- **Intel Mac**: please stay on **v4.2.1**, the last Intel-supported release (PyTorch no longer ships for Intel Macs)
- **Windows**: the **Lite** installer covers all configurations; the **CUDA/GPU** build is distributed separately (large file) — ask if you need it

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
- **Intel Mac**：请继续使用 **v4.2.1**（最后一个支持 Intel 的版本，PyTorch 已不再为 Intel Mac 发布更新）
- **Windows**：**Lite** 安装包覆盖所有配置；**CUDA/GPU** 版本因体积较大单独分发，需要可联系获取
