# SuperPicky Release Notes

## V4.2.0 (2026-03-11) - ONNX 引擎升级与工作流增强

### 1. 架构与模型升级
- **1.1 全新 ONNX 引擎**：移除 PyTorch 依赖，引入 ONNX Runtime，包体积更小，纯 CPU 推理速度更快。
- **1.2 轻量级关键点模型**：应用全新瘦身的关键点检测模型（体积 283MB → 95MB），加速启动并降低内存占用。

### 2. 批处理与工作流
- **2.1 子目录递归处理**：GUI 原生支持读取嵌套子目录，自动切换 Batch 模式处理所有深层图片。
- **2.2 EXIF 星级双向同步**：在结果浏览器修改的星级，将实时写入原始照片的 EXIF Rating 标签。
- **2.3 最近目录历史**：菜单栏新增历史记录堆栈，支持一键切换最近处理的文件夹。

### 3. 鸟类识别功能
- **3.1 中英鸟名词典**：整合 IOC 数据库，新增独立的鸟类名称中英双语检索工具。
- **3.2 右键快捷复制**：结果浏览器的图片卡片新增右键菜单，一键复制已识别的鸟类名称。

### 4. 修复与优化
- **4.1 中文路径解析**：彻底修复由于含中文/特殊字符路径导致的 OpenCV/RAW 解析及模型加载崩溃问题。
- **4.2 进程安全回收**：优化底层管理，确保应用退出时完全终止后台常驻的 ExifTool 服务。
- **4.3 控制台编码兼容**：修复 Windows 非 UTF-8 控制台下输出 i18n 日志导致程序崩溃的问题。
- **4.4 macOS 权限检测**：针对全屏截图模块，新增屏幕录制权限前置校验拦截。

---

## V4.1.0 LTS (2026-03-07) - Result Browser & HEIF Support / 结果浏览器与 HEIF 支持

### New Features
- **[🆕 UI] Result Browser (选鸟结果浏览器)**:
  - Three-panel layout: Filter Panel + Thumbnail Grid + Detail Panel.
  - Filter by rating, focus, exposure, flight status, and species.
  - Full-screen viewer with keyboard navigation.
  - Side-by-side comparison viewer for multi-selection.
  - Right-click to open in Lightroom/Photoshop/Finder.
  - In-browser star rating with database write-back.
- **[🆕 Format] HEIF/HEIC Support (macOS)**:
  - Native HEIF reading on macOS ARM via ImageIO.
  - Full EXIF metadata preservation.

### Improvements
- **[Perf]** Async thumbnail loading with LRU cache.
- **[UX]** Auto-launch Result Browser after processing.
- **[UX]** Thumbnail size slider with real-time adjustment.

### Downloads
**macOS Apple Silicon (M1/M2/M3/M4)**:
- [GitHub](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_arm64_c869d64.dmg) | [Google Drive](https://drive.google.com/file/d/1odYNFvtYZa8pAO_bYZZCh5FZ6v0ggxFQ) | [百度网盘](https://pan.baidu.com/s/1xzex0UrSDiZeWyLuYRSqNg?pwd=t6c4) (t6c4) | [夸克网盘](https://pan.quark.cn/s/625a2dac438a)

**macOS Intel (Pre-2020 Mac)**:
- [GitHub](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_Intel_c869d64.dmg) | [Google Drive](https://drive.google.com/file/d/1dPdCoObVLuxy9ks_sYjbfSR4bI3A-IPD) | [百度网盘](https://pan.baidu.com/s/1lNz2mBUEee8qqrd95rPJsA?pwd=3821) (3821) | [夸克网盘](https://pan.quark.cn/s/1b5d87b74059)

**Windows CUDA-GPU**:
- [百度网盘](https://pan.baidu.com/s/1XBaGXPim_WzjpNBgG-altg?pwd=c2a6) (c2a6) | [Google Drive](https://drive.google.com/file/d/1IKSxB3KbQdDO7VhnsGnHjOb2EgqZIgSB/view?usp=sharing) | [夸克网盘](https://pan.quark.cn/s/d15276717367)

**Windows CPU**:
- [GitHub](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_Setup_Win64_4.1.0_242f4be.exe) | [百度网盘](https://pan.baidu.com/s/1dle-dGbKx5_On5cfdaaLXQ?pwd=872v) (872v) | [Google Drive](https://drive.google.com/file/d/1nTcgQdUqotu04kVkWUZqENnQtn573uzx) | [夸克网盘](https://pan.quark.cn/s/1b7016c16f79)

---

## V4.0.6 Beta (2026-02-18) - OSEA Model & Offline Intelligence / OSEA 模型与离线智能

### New Features
- **[AI] OSEA ResNet34 Model**: 
  - Integrated OSEA model for higher accuracy bird identification.
  - Replaces legacy birdid2024 model.
- **[Data] Offline Avonet Database**: 
  - Full offline support for species filtering using Avonet database.
  - Replaces eBird API dependency for better reliability and privacy.
- **[UI] Simplified Country Selection**:
  - Streamlined country list to 48 supported regions.
  - Smart filtering based on offline data availability.

### Improvements
- **[Perf]** Optimized country filtering performance.
- **[UX]** Updated installation guide and welcome messages.

---

## V4.0.5 (2026-02-15) - 性能跃升与架构升级 / Performance & Architecture Upgrade

This release brings a major architectural overhaul, migrating from CSV to SQLite database, and integrates key community fixes.
本次更新带来了底层的重大重构，从 CSV 迁移至 SQLite 数据库，并整合了社区贡献的多项关键修复。

### 🚀 Architecture & Performance / 架构与性能
- **[Core] 核心架构升级 (Core Architecture Upgrade)**
  - Migrated report storage from CSV to SQLite (报告存储从 CSV 迁移至 SQLite).
  - **Speed**: ~1.9x speedup (速度提升 1.9倍).
  - **Stability**: Resolved file lock conflicts (解决文件锁冲突).
- **[Core] 统一临时文件管理 (Unified Temp File Management)**
  - All cache moved to `.superpicky/cache/` (所有缓存移至隐藏目录).
  - Smart cleanup logic (智能清理逻辑).

### 🌟 Special Thanks / 特别致谢
- **@OscarKing888 (osk.ch)**: 
  - [Fix] Sony ARW compatibility (Sidecar XMP).
  - [Fix] EXIF Caption UTF-8 encoding.
  - [Dev] Windows CUDA setup script.

### 🐛 Bug Fixes
- **[Fix]** Debug Path Persistence & Ghost Paths cleanup.
- **[Fix]** Chinese Path Support (中文路径支持).
- **[Fix]** Burst Merge DB connection error.
- **[Plugin]** Metadata writing reliability.

### 📥 Downloads
**macOS Apple Silicon (M1/M2/M3/M4)**: 
- GitHub: [SuperPicky_v4.0.6.dmg](https://github.com/jamesphotography/SuperPicky/releases/download/v4.0.6/SuperPicky_v4.0.6.dmg)
- Google Drive: [SuperPicky_v4.0.6.dmg](https://drive.google.com/file/d/1vwKMcXcZQHYSalOyXg3grOV2wYFu2W8_/view?usp=sharing)
- 百度网盘: [SuperPicky_v4.0.6.dmg](https://pan.baidu.com/s/1CR1OsRRorAwC0vI5xqw7Rw?pwd=mix5) 提取码: mix5

**macOS Intel (2020年前 Mac)**:
- GitHub: [SuperPicky_v4.0.6_Intel.dmg](https://github.com/jamesphotography/SuperPicky/releases/download/v4.0.6/SuperPicky_v4.0.6_Intel.dmg)
- Google Drive: [SuperPicky_v4.0.6_Intel.dmg](https://drive.google.com/file/d/1eKw_02YlsC9Yrfi1VxOxAX6xSMzIDdQa/view?usp=drive_link)
- 百度网盘: [SuperPicky_v4.0.6_Intel.dmg](https://pan.baidu.com/s/1hMW47CCJKaKtjtqgTiep8g?pwd=6cpu) 提取码: 6cpu

**Windows (v4.0.6 Beta)**:
- **CUDA-GPU Version**: [百度网盘](https://pan.baidu.com/s/1UUfnal8rT2Mizkdcs0xpwg?pwd=igew) 提取码: igew
- **CPU Version**: [GitHub](https://github.com/jamesphotography/SuperPicky/releases/download/v4.0.6/SuperPicky_4.0.6_Win64_CPU.zip) | [Google Drive](https://drive.google.com/file/d/1m-IEASCsAa3Znertanw1NcbX3IKKi2M3/view?usp=sharing) | [百度网盘](https://pan.baidu.com/s/1VtVnNXJQYKEQw4oo_pZRlw) 提取码: xgnj

**Windows (v4.0.5)**:
- **CUDA-GPU Version**: [Google Drive](https://drive.google.com/file/d/17-dFw2pZKXn53zmYAZ7HQNHTyndCT76E/view?usp=drive_link) | [百度网盘](https://pan.baidu.com/s/14tnSXnI2LIeZf4egu4xxNg?pwd=jfuz) 提取码: jfuz

---

## V4.0.4 beta (2026-02-09) - 连拍优化与稳定性改进

### Bug Fixes
- [Fix] 启用识鸟但无结果时，照片放入"其他鸟类"子目录而非根目录
- [Fix] 版本号统一从 constants.py 获取，避免版本不一致

### Improvements
- [UI] 确认对话框中显示当前选择的国家/区域识别设置
- [Build] 新增 M3 Mac 专用打包脚本 (create_pkg_dmg_v4.0.4_m3.sh)

---



## V4.0.3 (2026-02-01) - 摄影水平预设与 AI 识鸟

### New Features
- [New] 摄影水平预设 (Photography Skill Levels)
  - 新手 (Beginner): 锐度 > 300, 美学 > 4.5 (保留更多照片)
  - 初级 (Intermediate): 锐度 > 380, 美学 > 4.8 (推荐)
  - 大师 (Master): 锐度 > 520, 美学 > 5.5 (极致严格)
  
- [New] AI 鸟类识别 (Bird Species Identification)
  - 支持全球 11,000+ 种鸟类识别
  - 自动写入照片 EXIF/IPTC 元数据
  - 中英双语结果支持
  
- [New] Lightroom 插件集成
  - 在 Adobe Lightroom Classic 中直接调用 AI 识鸟
  - 无需导出即可查看识别结果

### Improvements
- [UI] 首次启动自动弹出水平选择向导
- [UI] 主界面参数区新增当前水平标签显示
- [Fix] 修复部分翻译显示的语言错误

---

## V4.0.2 (2026-01-25) - Bug 修复

### Bug Fixes
- [Fix] Intel Mac 启动崩溃问题修复
- [Fix] 连拍检测时间阈值逻辑优化
- [Fix] 部分 RAW 文件 EXIF 写入失败问题

---

## V4.0.1 (2026-01-20) - Windows 版本与对焦检测增强

### New Features
- [New] Windows 版本发布 (支持 NVIDIA GPU 加速)
- [New] 对焦点检测增强
  - 支持 Nikon Z6-3 DX 模式
  - 对焦在头部区域 (BEST) 锐度权重 x1.1
  - 对焦在身体区域 (GOOD) 无惩罚
  - 对焦在区域外 (BAD) 锐度权重 x0.7
  - 完全脱焦 (WORST) 锐度权重 x0.5

### Improvements
- [Perf] ExifTool 常驻进程优化，EXIF 写入速度提升 50%
- [Perf] 识鸟 GPS 区域缓存，避免重复网络请求

---

## V4.0.0 (2026-01-15) - 评分引擎重构

### Breaking Changes
- [Change] TOPIQ 替代 NIMA 作为美学评分模型
  - 更准确的画面美感评估
  - 全图评估而非裁剪区域

### New Features
- [New] 对焦点验证系统
  - 从 RAW 文件提取相机对焦点位置
  - 多层验证: 头部圆/分割掩码/BBox/画面边缘
  - 支持 Nikon, Sony, Canon, Olympus, Fujifilm, Panasonic
  
- [New] ISO 锐度归一化
  - 高 ISO 噪点会虚高锐度值
  - ISO 800 以上每翻倍扣 5%

### Improvements
- [Perf] 0 星和 -1 星照片跳过对焦检测，节省 ExifTool 调用
- [UI] 调试图显示对焦点位置、头部区域、分割掩码

---

## Downloads (Latest: V4.2.0)

### macOS Apple Silicon (M1/M2/M3/M4)
- GitHub: [SuperPicky_v4.1.0_arm64_c869d64.dmg](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_arm64_c869d64.dmg)
- Google Drive: [SuperPicky_v4.1.0_arm64_c869d64.dmg](https://drive.google.com/file/d/1odYNFvtYZa8pAO_bYZZCh5FZ6v0ggxFQ)
- 百度网盘: [SuperPicky_v4.1.0_arm64_c869d64.dmg](https://pan.baidu.com/s/1xzex0UrSDiZeWyLuYRSqNg?pwd=t6c4) 提取码: t6c4
- 夸克网盘: [SuperPicky_v4.1.0_arm64_c869d64.dmg](https://pan.quark.cn/s/625a2dac438a)

### macOS Intel (Pre-2020 Mac)
- GitHub: [SuperPicky_v4.1.0_Intel_c869d64.dmg](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_Intel_c869d64.dmg)
- Google Drive: [SuperPicky_v4.1.0_Intel_c869d64.dmg](https://drive.google.com/file/d/1dPdCoObVLuxy9ks_sYjbfSR4bI3A-IPD)
- 百度网盘: [SuperPicky_v4.1.0_Intel_c869d64.dmg](https://pan.baidu.com/s/1lNz2mBUEee8qqrd95rPJsA?pwd=3821) 提取码: 3821
- 夸克网盘: [SuperPicky_v4.1.0_Intel_c869d64.dmg](https://pan.quark.cn/s/1b5d87b74059)

### Windows (v4.2.0)

**CUDA-GPU Version**
- [百度网盘](https://pan.baidu.com/s/1XBaGXPim_WzjpNBgG-altg?pwd=c2a6) 提取码: c2a6
- [Google Drive](https://drive.google.com/file/d/1IKSxB3KbQdDO7VhnsGnHjOb2EgqZIgSB/view?usp=sharing)
- [夸克网盘](https://pan.quark.cn/s/d15276717367)

**CPU Version**
- GitHub: [SuperPicky_Setup_Win64_4.1.0_242f4be.exe](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_Setup_Win64_4.1.0_242f4be.exe)
- [百度网盘](https://pan.baidu.com/s/1dle-dGbKx5_On5cfdaaLXQ?pwd=872v) 提取码: 872v
- [Google Drive](https://drive.google.com/file/d/1nTcgQdUqotu04kVkWUZqENnQtn573uzx)
- [夸克网盘](https://pan.quark.cn/s/1b7016c16f79)

