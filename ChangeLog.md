## What's New in v4.2.6

**First-Run Onboarding & Welcome Wizard**
- New welcome flow on first launch: photographer skill-level selection, optional auto-update opt-in, environment health check
- Built-in **Initialization Manager** that probes the best download source, picks the right PyTorch runtime, and downloads required AI models with real-time byte-level progress
- Lightweight first-run experience: source probing + multi-mirror fallback + retry-with-backoff for all model downloads
- New **Environment Repair** dialog: rerun the initialization flow at any time from the Settings menu when something's off

**Windows Lite Installer (Hardware-Aware)**
- New `SuperPicky_Setup_Lite_Win64_*.exe` (182 MB, down from 746 MB Full)
- Auto-detects NVIDIA / CUDA at first launch and installs the matching PyTorch wheel (cu118 or CPU) — solves the long-standing "one installer for all GPU configs" problem
- Existing installations preserve their downloaded runtime + models under `~/AppData/Local/SuperPicky/`, so subsequent updates are tiny

**Installer-Based In-App Update**
- "Check for updates" now downloads the matching installer in the background (with progress bar, SHA-256 verification, cancellable)
- Once download finishes, a confirm dialog hands off to the OS-native installer (macOS opens the DMG, Windows launches the setup wizard)
- Replaces the old "open download page in browser" flow

**ExifTool 13.55**
- Bundled ExifTool upgraded from earlier 13.x to 13.55 (both macOS and Windows)
- New automated workflow: `sync-exiftool.yml` periodically checks the ExifTool upstream and opens a PR to refresh assets — no more manual ExifTool bumps
- New `exiftools/VERSION.json` tracks the bundled ExifTool version

**Smarter Mirror Selection**
- PyPI candidates now include Tsinghua + Aliyun + CERNET (was just CERNET)
- PyTorch wheel mirrors now include Aliyun (was just NJU)
- New 2× latency-ratio rule: prefer mirrors when they're within 2× of the official endpoint's latency; otherwise fall back to official (fixes "Hong Kong / Singapore users stuck on slow mirrors" edge case)
- Direct `urllib` raw-URL fallback for all HuggingFace model downloads: bypasses `huggingface_hub` when it stalls (sometimes happens in CI / overseas networks)

**Scanner Safety**
- Recursive scan now skips zero-byte files instead of crashing
- Directory-tree scan summaries surface file counts and skipped reasons before processing
- Protected-directory guard refuses to scan system directories or drive roots

**Performance & Stability**
- **macOS MPS memory leak fix**: keypoint and flight detectors now immediately free Metal/CUDA tensors, preventing memory growth during long batches
- BirdID classifier path-resolution fix: correctly finds the model when running under a hot-patch overlay
- TOPIQ IQA model path resolution + macOS console log encoding fixes
- huggingface_hub 1.x compatibility: runtime probe of `hf_hub_download` signature avoids `TypeError: unexpected keyword argument 'tqdm_class'`

**Build & Release Pipeline**
- macOS code signing fix: temporary keychain is now added to the user-domain search list, so `codesign` reliably finds the Developer ID Application identity
- `scripts/download_models.py` works as a standalone script (auto-adds project root to `sys.path`)
- `yolo11l-seg.pt` now downloaded from the official `Ultralytics/YOLO11` HuggingFace repo (was missing from the project's own model repo)

**Release Matrix Simplification**
- **Removed Mac Lite** (provided no real benefit — macOS doesn't need hardware-aware PyTorch dispatch, and the model files still had to be downloaded on first launch)
- **Removed Windows Full** (CPU-only build was strictly worse than Lite for NVIDIA users; Lite + onboarding now covers everyone)
- **Removed macOS Intel build** (PyTorch stopped publishing macOS x86_64 wheels at 2.2.2, March 2024)
- New shape: **macOS arm64 + Windows x64 Lite + code patch zip**

**Website**
- Download page modernized: now reads `downloads_github.json`, supports the new installer matrix
- China mirror simplified: links now route through `gh-proxy.com` instead of the separate GitCode JSON manifest (one mirror config instead of two)

---

## 更新内容 (v4.2.6)

**首次运行向导（Onboarding）**
- 新增欢迎流程：摄影水平预设、自动更新开关、环境健康检查
- 内置**初始化管理器**：自动探测最佳下载源、按硬件选择 PyTorch 运行时、显示字节级真实下载进度
- 轻量初装体验：源探测 + 多镜像 fallback + 退避重试，覆盖所有模型下载
- 新增**环境修复**对话框：在设置菜单中可随时重新运行初始化流程修复异常环境

**Windows 轻量版安装包（按硬件适配）**
- 新增 `SuperPicky_Setup_Lite_Win64_*.exe`（182 MB，对比完整版 746 MB）
- 首次启动时自动检测 NVIDIA / CUDA，安装匹配的 PyTorch wheel（cu118 或 CPU）—— 一个安装包通吃所有 GPU 配置
- 已装机的运行时和模型保留在 `~/AppData/Local/SuperPicky/`，后续升级体积很小

**应用内安装包式更新**
- "检查更新"现在会后台下载对应平台安装包（进度条 + SHA-256 校验 + 可取消）
- 下载完成后弹出确认对话框，移交给系统原生安装器（macOS 打开 DMG，Windows 启动安装向导）
- 替代之前"打开下载页让用户手动找"的体验

**ExifTool 13.55**
- macOS 与 Windows 包内的 ExifTool 全部升级至 13.55
- 新增自动同步工作流 `sync-exiftool.yml`：定期检查 ExifTool 上游版本并自动开 PR 同步资产 —— 不再需要人工跟版
- 新增 `exiftools/VERSION.json` 记录当前打包的 ExifTool 版本

**更智能的下载源选择**
- PyPI 镜像新增清华 + 阿里（之前只有 CERNET）
- PyTorch wheel 镜像新增阿里（之前只有南大）
- 新增 2× 延迟比率规则：镜像延迟在官方 2 倍以内时优先用镜像，否则退到官方（修复"香港/新加坡用户被慢镜像锁定"的边缘场景）
- 所有 HuggingFace 模型下载新增 `urllib` 直拉兜底：绕过 `huggingface_hub` 在 CI / 海外网络下偶发的失败

**扫描安全**
- 递归扫描自动跳过零字节文件，不再因损坏文件中断
- 目录树扫描预览显示文件计数和跳过原因
- 受保护目录守卫：拒绝扫描系统目录或盘根

**性能与稳定性**
- **macOS MPS 显存泄漏修复**：关键点 / 飞行检测器在长批次推理时立即释放 Metal/CUDA 张量
- BirdID 模型路径修复：热补丁覆盖层场景下能正确定位模型
- TOPIQ 美学评分模型路径修复 + macOS 控制台日志编码修复
- huggingface_hub 1.x 兼容：运行时探测 `hf_hub_download` 函数签名，避免 `TypeError: unexpected keyword argument 'tqdm_class'`

**构建与发布流水线**
- macOS 代码签名修复：临时 keychain 加入用户域搜索列表，`codesign` 可靠地找到 Developer ID Application 证书
- `scripts/download_models.py` 支持独立运行（自动把项目根加入 `sys.path`）
- `yolo11l-seg.pt` 改从 Ultralytics 官方 HuggingFace 仓库下载（项目仓库不再托管 .pt 权重）

**发布矩阵简化**
- **移除 Mac Lite**（macOS 不需要按硬件分发 PyTorch，且模型仍需在首次启动时下载，与 Full 没有实际差异）
- **移除 Windows Full**（CPU-only 包对 NVIDIA 用户严格不如 Lite + onboarding 自动选择）
- **移除 macOS Intel**（PyTorch 自 2.2.2 起停止发布 macOS x86_64 wheel，2024 年 3 月）
- 新形态：**macOS arm64 + Windows x64 Lite + code patch zip**

**网站**
- 下载页改为基于 `downloads_github.json` 渲染，适配新的安装包矩阵
- 大陆镜像简化：链接统一走 `gh-proxy.com`，替代独立的 GitCode 清单

---

## Acknowledgements

Thanks to **张钧涛 (Juntao Zhang)** for sponsoring AI coding tools for this project.

This release also includes contributions from **@yblpoi** (upstream migration, scanner safety, ExifTool 13.55 sync automation).

---

> 本版本仍在 RC 测试阶段（v4.2.6-RC13）。正式生产环境请使用 v4.1.0 LTS 直至 v4.2.6 正式版发布。
> This release is still in RC testing (v4.2.6-RC13). For production use, please stick to v4.1.0 LTS until v4.2.6 GA.
