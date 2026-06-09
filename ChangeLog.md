## What's New in v4.3.0-RC6

**Video Bird Analysis (New)**
- Annotate bird / no-bird segments along a video timeline (YOLO detection) and generate matching SRT subtitles
- Identify species and detect birds in flight, with three recognition modes
- Automatically sort clips by species, moving the SRT subtitles along with them
- New "Video Processing" tab in Settings

**Global Rarity Index (GBIF)**
- Worldwide rarity score (0-100, with an IUCN floor) derived from GBIF data, replacing the previous rarity index
- A five-tier filled-circle rarity scale with photographer-oriented tier names
- IUCN Red List category written to a dedicated EXIF field and shown as a third metric in the detail panel

**Library & Browser**
- Folder organization: rating-first or species-first layouts; low-rated photos go to an "Other Birds" folder
- Browser: Cmd+A to select all, Cmd+Backspace to delete

**Streamlined Update Process**
- In-app update checking is now turned off across all builds — to upgrade, download the new version from the official download page. This keeps the update path simple and avoids the previous online hot-patch system's pitfall, where leftover patches could override the new version's code after an upgrade.
- The in-app "Update Center" is now a simple version-info panel with a link to the official download page.

**Cleaner Windows Installs**
- Upgrades now clear the previous program files before installing, so leftover modules/DLLs from an older version can't linger and cause conflicts.
- New Windows Uninstaller tool (SuperPickyUpdateCleaner.exe) — recommended before switching between Lite and Full installer types, or for a clean reinstall.

**Available Builds**
- **macOS**: Apple Silicon (arm64, signed & notarized); a separate Intel build is also available.
- **Windows**: Lite (≈196 MB, downloads the AI engine + models on first launch), Full CPU (≈749 MB, install-and-run, fully offline), and Full CUDA (≈2 GB, NVIDIA GPU acceleration — distributed via netdisk due to GitHub's 2 GiB single-file cap).

---

## v4.3.0-RC6 更新内容

**视频鸟类分析（全新）**
- 在视频时间轴上标注有鸟 / 无鸟片段（YOLO 检测），并生成对应的 SRT 字幕
- 识别鸟种 + 飞行检测，提供三档识别模式
- 按鸟种自动归类片段，SRT 字幕同步移动
- 参数设置新增「视频处理」标签页

**全球罕见度（GBIF）**
- 基于 GBIF 数据的全球罕见度评分（0-100，含 IUCN 下限），取代原有的罕见度指数
- 五档圆形充填的罕见度等级 + 摄影师视角的分级名称
- IUCN 红色名录等级写入独立 EXIF 字段，并在详情面板作为第三项指标展示

**图库与浏览**
- 文件夹整理：按评分优先或按鸟种优先两种布局；低星照片归入「其他鸟类」文件夹
- 浏览器：Cmd+A 全选，Cmd+Backspace 删除

**简化的升级流程**
- 所有版本均已关闭应用内更新检测 —— 升级请前往官网下载页获取新版本。这让更新路径更简单，并规避了原在线热补丁机制的问题：升级后残留的旧补丁可能覆盖新版本代码。
- 应用内"更新中心"改为简洁的版本信息面板，并提供官网下载入口。

**更干净的 Windows 安装**
- 升级时会先清空上一版的程序文件，避免旧版本残留的模块/DLL 干扰新版本引发冲突。
- 新增 Windows 卸载工具（SuperPickyUpdateCleaner.exe）—— 在 Lite 与 Full 安装类型之间切换、或需要全新重装时推荐使用。

**提供的版本**
- **macOS**：Apple Silicon（arm64，已签名公证）；另单独提供 Intel 版本。
- **Windows**：Lite（约 196 MB，首启下载 AI 引擎与模型）、Full CPU（约 749 MB，装完即用、完全离线）、Full CUDA（约 2 GB，NVIDIA 显卡加速 —— 因 GitHub 单文件 2 GiB 上限走网盘分发）。

---

## What's New in v4.2.6

**Smarter First-Run Experience**
- Brand-new welcome wizard guides you through skill-level setup, optional auto-update preferences, and AI model preparation — all visualized with real-time progress
- The app now auto-picks the right AI runtime for your hardware: NVIDIA GPU users get CUDA acceleration, others stay on CPU — no manual choice needed

**Windows: One Smarter Installer**
- New 182 MB lightweight installer replaces the previous 750 MB "CPU only" build
- First launch automatically downloads the matching AI engine for your hardware (CUDA for NVIDIA cards, CPU otherwise)
- Subsequent updates keep your downloaded models, so you only re-download the app shell

**One-Click In-App Updates**
- "Check for updates" now downloads the new installer in the background and prompts you to install when ready — no more hunting on the download page
- Built-in integrity check ensures the installer arrived intact before you run it

**Environment Repair**
- New "Environment Repair" entry in Settings: rerun model preparation any time something feels off — no need to reinstall the whole app

**ExifTool 13.55**
- The bundled metadata engine is upgraded to ExifTool 13.55, with significantly better RAW support for the latest cameras (Canon, DJI, etc.) and improved tag coverage

**Faster, More Reliable Downloads**
- Improved automatic mirror selection: mainland China users get optimized routing through faster mirrors; overseas users automatically use official sources
- Hardened recovery: AI model downloads now fall back through multiple mirrors with progress display and retry — slow networks no longer mean stuck setup

**Mac: Smooth Long-Batch Processing**
- Fixed a memory pressure issue on Apple Silicon: processing thousands of photos in one batch now stays steady throughout, no slowdown toward the end

**Smarter Folder Scanning**
- Corrupted (zero-byte) photo files are automatically skipped instead of stopping the batch
- Recursive scan now refuses to walk into protected system folders by mistake
- Folder summaries show file counts and skipped reasons before processing begins

---

**Distribution Changes**

- **Apple Silicon Mac users**: Same single full installer as before.
- **Intel Mac users**: Sorry — Apple Silicon has been the focus since 2020, and PyTorch (the underlying AI engine) no longer publishes updates for Intel Macs. Please continue using v4.2.1, which remains the last Intel-supported release.
- **Windows users**: We've consolidated to a single Lite installer (182 MB). The previous "Full" 750 MB CPU-only build is discontinued because the Lite installer covers all Windows configurations more efficiently.

---

## v4.2.6 更新内容

**更聪明的首次运行体验**
- 全新欢迎向导引导设置摄影水平、自动更新偏好与 AI 模型准备，全程可视化进度
- 应用会根据你的硬件自动选择最合适的 AI 运行引擎：NVIDIA 显卡用户获得 CUDA 加速，其他用户用 CPU —— 无需手动选择

**Windows：一个更智能的安装包**
- 新的 182 MB 轻量安装包，替代原有 750 MB 的 "CPU only" 完整安装包
- 首次启动时自动按你的硬件下载匹配的 AI 引擎（NVIDIA 显卡走 CUDA，其他走 CPU）
- 后续升级会保留你已下载的模型，只更新应用主体

**一键应用内升级**
- "检查更新"现在会后台下载新版安装包，下载完成后弹窗提示安装 —— 不再需要自己去下载页找文件
- 内置完整性校验，确保安装包没有传输损坏才让你执行

**环境修复**
- 设置菜单新增"环境修复"入口：当 AI 模型或运行环境出问题时，一键重新走一遍准备流程 —— 不用重装整个应用

**ExifTool 13.55**
- 包内元数据引擎升级到 ExifTool 13.55，显著增强对最新相机 RAW 文件的支持（佳能、大疆等品牌），覆盖更多元数据标签

**更快、更稳定的下载**
- 改进自动镜像选择：大陆用户自动走最快的镜像源，海外用户自动走官方源
- 强化容错：AI 模型下载会在多个镜像之间智能切换 + 显示进度 + 自动重试，慢网不再卡死

**Mac：长批次处理更流畅**
- 修复了 Apple Silicon 上的内存压力问题：批量处理数千张照片时全程保持稳定，不再在后期变慢

**更聪明的目录扫描**
- 自动跳过损坏（零字节）的照片文件，不再中断整个批次
- 递归扫描拒绝误入系统目录
- 扫描前显示文件计数和跳过原因预览

---

**发行版调整**

- **Apple Silicon Mac 用户**：跟以前一样，单一完整安装包。
- **Intel Mac 用户**：抱歉 —— Apple Silicon 从 2020 年起一直是主流，PyTorch（底层 AI 引擎）也已经停止为 Intel Mac 发布新版本。请继续使用 v4.2.1，它仍然是最后一个支持 Intel Mac 的版本。
- **Windows 用户**：我们将发布版本简化为单一轻量安装包（182 MB）。之前 750 MB 的"完整版"已经停发，因为新的轻量包配合首次启动的自动适配，覆盖所有 Windows 配置更高效。

---

## 致谢 / Acknowledgements

感谢 **张钧涛 (Juntao Zhang)** 赞助本项目 AI 编程工具使用费。
Thanks to **张钧涛 (Juntao Zhang)** for sponsoring AI coding tools for this project.

本版本也包含来自 **@yblpoi** 的代码贡献（首次启动初始化框架、扫描安全、ExifTool 自动同步）。
This release also includes code contributions from **@yblpoi** (first-run initialization framework, scanner safety, ExifTool sync automation).

---

> 本版本仍处于 RC 测试阶段（当前 v4.2.6-RC13）。如需稳定生产环境，推荐继续使用 v4.1.0 LTS 直至 v4.2.6 正式版发布。
> This release is still in RC testing (currently v4.2.6-RC13). For a stable production environment, please continue using v4.1.0 LTS until v4.2.6 GA.
