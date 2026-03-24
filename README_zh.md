# SuperPicky - 慧眼选鸟 

[![Version](https://img.shields.io/badge/version-4.2.0-blue.svg)](https://github.com/jamesphotography/SuperPicky)
[![Platform](https://img.shields.io/badge/platform-macOS%20|%20Windows-lightgrey.svg)](https://github.com/jamesphotography/SuperPicky/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

[**English Documentation**](README.md) | [**更新日志**](RELEASE_NOTES.md)

**智能鸟类照片筛选工具 - 让AI帮你挑选最美的鸟类照片**

拍片一时爽，选片照样爽！一款专门为鸟类摄影师设计的智能照片筛选软件，使用多模型AI技术自动识别、评分和筛选鸟类照片，大幅提升后期整理效率。

---

## 🌟 核心功能

### 🤖 多模型协作
- **YOLO11 检测**: 精准识别照片中的鸟类位置和分割掩码
- **[SuperEyes 鸟眼](https://github.com/triple333sR9/SuperBirdEye)**: 检测鸟眼位置和可见度，计算头部区域锐度
- **[SuperFlier 飞鸟](https://github.com/triple333sR9/SuperFlier)**: 识别飞行姿态，给予飞版照片额外加分
- **TOPIQ 美学**: 评估整体画面美感、构图和光影

### ⭐ 智能评分系统 (0-3星)
| 星级 | 条件 | 含义 |
|------|------|------|
| ⭐⭐⭐ | 锐度达标 + 美学达标 | 优选照片，值得后期处理 |
| ⭐⭐ | 锐度达标 或 美学达标 | 良好照片，可考虑保留 |
| ⭐ | 有鸟但都未达标 | 普通照片，通常可删除 |
| 0 | 无鸟/质量太差 | 建议删除 |

### ⚙️ 摄影水平预设 (New)
根据您的拍摄经验自动设定筛选标准：
- **🐣 新手 Beginner**: 锐度>300, 美学>4.5 (保留更多)
- **📷 初级 Intermediate**: 锐度>380, 美学>4.8 (平衡)
- **👑 大师 Master**: 锐度>520, 美学>5.5 (严苛)


### 🏷️ 特殊标记
- **Pick 精选**: 3星照片中锐度+美学双排名前25%的交集
- **Flying 飞鸟**: AI检测到飞行姿态，额外加分并标记绿色
- **Exposure 曝光** (可选): 检测过曝/欠曝问题，降一星处理

### 📂 自动整理
- **按星级分类**: 自动移动到 0星/1星/2星/3星 文件夹
- **EXIF写入**: 评分、旗标、锐度/美学值写入RAW文件元数据
- **Lightroom兼容**: 导入即可按评分排序和筛选
- **可撤销**: 一键重置恢复原始状态

---

## 📋 系统要求

- **macOS**: macOS 14+ · Apple Silicon (M1/M2/M3/M4) · 1.5GB空间
- **Windows**: Windows 10+ · NVIDIA GPU (建议) · 2GB空间

---

## 📥 下载安装

### macOS
**Apple Silicon (M1/M2/M3/M4) (v4.2.0)**
- [GitHub 下载](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_arm64_c869d64.dmg) | [Google Drive](https://drive.google.com/file/d/1odYNFvtYZa8pAO_bYZZCh5FZ6v0ggxFQ) | [百度网盘](https://pan.baidu.com/s/1xzex0UrSDiZeWyLuYRSqNg?pwd=t6c4) 提取码: t6c4 | [夸克网盘](https://pan.quark.cn/s/625a2dac438a)

**Intel (2020年前 Mac) (v4.2.0)**
- [GitHub 下载](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_v4.1.0_Intel_c869d64.dmg) | [Google Drive](https://drive.google.com/file/d/1dPdCoObVLuxy9ks_sYjbfSR4bI3A-IPD) | [百度网盘](https://pan.baidu.com/s/1lNz2mBUEee8qqrd95rPJsA?pwd=3821) 提取码: 3821 | [夸克网盘](https://pan.quark.cn/s/1b5d87b74059)

1. 下载对应版本的 DMG 文件
2. 双击 DMG 文件，将应用拖入 Applications
3. 首次打开：右键点击应用选择"打开"

### Windows
**CUDA-GPU Version (v4.2.0 Beta)**
- [百度网盘](https://pan.baidu.com/s/1XBaGXPim_WzjpNBgG-altg?pwd=c2a6) 提取码: c2a6 | [Google Drive](https://drive.google.com/file/d/1IKSxB3KbQdDO7VhnsGnHjOb2EgqZIgSB/view?usp=sharing) | [夸克网盘](https://pan.quark.cn/s/d15276717367)

**CPU Version (v4.2.0)**
- [GitHub 下载](https://github.com/jamesphotography/SuperPicky/releases/download/v4.1.0/SuperPicky_Setup_Win64_4.1.0_242f4be.exe) | [百度网盘](https://pan.baidu.com/s/1dle-dGbKx5_On5cfdaaLXQ?pwd=872v) 提取码: 872v | [Google Drive](https://drive.google.com/file/d/1nTcgQdUqotu04kVkWUZqENnQtn573uzx) | [夸克网盘](https://pan.quark.cn/s/1b7016c16f79)


1. 下载并解压 ZIP 文件
2. 运行 `SuperPicky.exe`

### 从源码运行

```bash
git clone https://github.com/jamesphotography/SuperPicky.git
cd SuperPicky
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_models.py
python main.py
```

---

## 🚀 快速开始

1. **选择文件夹**: 拖入或浏览选择包含鸟类照片的文件夹
2. **调整阈值** (可选): 锐度阈值 (200-600)、美学阈值 (4.0-7.0)
3. **开关功能** (可选): 飞鸟检测、曝光检测
4. **开始处理**: 点击按钮等待AI处理完成
5. **查看结果**: 照片自动分类，导入Lightroom即可使用

---

## 📝 更新日志

### v4.0.5 (2026-02-15)
- 🚀 **架构升级**: 迁移至 SQLite 数据库，速度提升 1.9x
- 🌟 **社区贡献**: 感谢 @OscarKing888 修复 Sony ARW 及乱码问题
- 🧹 **整洁**: 统一临时文件到隐藏目录
- 🔧 **修复**: 中文路径、ExifTool 死锁及插件修复

### v4.0.4 (2026-02-09)
- 🔧 **连拍优化**: 启用识鸟但无结果时，放入"其他鸟类"子目录
- 🔧 **版本管理**: 版本号统一从 constants.py 获取
- 🔧 **UI 改进**: 确认对话框显示当前选择的国家/区域

### v4.0.3 (2026-01-30)
- ⚙️ **新增摄影水平预设**: 新手/初级/大师三种模式，一键设定最佳筛选阈值
- 🦜 **AI 鸟类识别**: 集成 11,000+ 种鸟类识别模型，自动写入元数据
- 🔌 **Lightroom 插件**: 支持在 LR 中直接调用 AI 识别
- 🌏 **eBird 集成**: 基于地理位置优化识别结果

### v3.9.0 (2026-01-09)
- 📷 **新增连拍检测**: 自动识别连拍组，选出最佳照片
  - 支持 pHash 相似度验证，提高准确率
  - 最佳照片标记紫色标签，其仙移入 burst_XXX 子目录
- 📦 CLI 新增 `burst` 命令，支持独立连拍检测
- 🔄 `reset` 和 `restar` 命令自动处理 burst 子目录
- 🎮 GUI 新增「连拍」开关（默认开启）

### v3.8.0 (2026-01-02)
- ✨ **新增曝光检测**: 检测鸟区域过曝/欠曝，可选功能默认关闭
  - 过曝判定：亮度 ≥235 的像素超过 10%
  - 欠曝判定：亮度 ≤15 的像素超过 10%
  - 有曝光问题的照片评分降一星
- 📊 新增曝光问题统计和日志标签 【曝光】
- 🎚️ 曝光阈值可在高级设置中调整 (5%-20%)

### v3.7.0 (2026-01-01)
- ✨ 重构评分逻辑，使用 TOPIQ 替代 NIMA
- 🦅 飞鸟检测加成：锐度+100，美学+0.5
- 👁️ 眼睛可见度封顶逻辑优化
- 🔧 UI 优化和 Bug 修复

### v3.6.0 (2025-12-30)
- ✨ 飞鸟照片绿色标签
- 📊 飞鸟统计计数
- 🔄 纯JPEG文件支持

---

## 👨‍💻 开发团队

| 角色 | 成员 | 贡献 |
|------|------|------|
| 开发者 | [James Yu (詹姆斯·于震)](https://github.com/jamesphotography) | 核心开发 |
| 模型训练 | [Jordan Yu (于若君)](https://github.com/triple333sR9) | SuperEyes · SuperFlier |
| Windows版 | [小平](https://github.com/thp2024) · [伯劳](https://github.com/yblpoi) | Windows移植 |

---

## 🙏 致谢

- 首位个人赞助者 - 张钧涛 2500 元人民币 （一季度开发团队 AI code 工具费用）

- [YOLO11](https://github.com/ultralytics/ultralytics) - Ultralytics 目标检测模型
- [TOPIQ](https://github.com/chaofengc/IQA-PyTorch) - Chaofeng Chen 等人的图像质量评估模型
- [SuperEyes (SuperBirdEye)](https://github.com/triple333sR9/SuperBirdEye) - [Jordan Yu (于若君)](https://github.com/triple333sR9) 鸟眼识别模型
- [SuperFlier](https://github.com/triple333sR9/SuperFlier) - [Jordan Yu (于若君)](https://github.com/triple333sR9) 飞版检测模型
- [ExifTool](https://exiftool.org/) - Phil Harvey 的 EXIF 处理工具

---

## 🐦 鸟种命名标准 (AviList 映射)

SuperPicky 通过 **AviList v2025** 映射表支持多种英文鸟种命名标准。在 **设置 > 选片标准 > 鸟种英文名格式** 中选择：

| 格式 | 来源 |
|------|------|
| 默认（OSEA 模型） | 模型训练时的原始名称 |
| AviList v2025 | AviList 统一英文名 |
| Clements / eBird v2024 | Cornell/eBird 分类法 |
| BirdLife v9 | BirdLife International |
| 仅学名 | 拉丁学名 |

**更新 AviList：** 映射表通过离线脚本从 `scripts_dev/AviList-v2025-11Jun-extended.xlsx` 构建。当新版 AviList 发布时（通常每年一次），替换 `scripts_dev/` 中的 xlsx 文件并重新运行：

```bash
pip install openpyxl  # 仅首次需要
python scripts_dev/build_avilist_mapping.py
```

---

## 📄 许可证

本软件使用 **GPL-3.0 License** 开源。

本项目使用:
- **YOLO11** by Ultralytics
- **OSEA** by Sun Jiao (github.com/sun-jiao/osea)
- **TOPIQ** by Chaofeng Chen et al.
- **AviList**: AviList Core Team. 2025. AviList: The Global Avian Checklist, v2025. https://doi.org/10.2173/avilist.v2025 — 基于 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 许可

这意味着：
1. 您可以免费下载、使用和修改本软件。
2. 如果您分发修改后的版本，必须同样开源并使用 GPL-3.0 协议。

详见 [LICENSE](LICENSE) 文件。

**让SuperPicky成为你鸟类摄影的得力助手！** 🦅📸
