# SuperPicky - 慧眼选鸟 🦅

[![Version](https://img.shields.io/badge/version-3.1.3-blue.svg)](https://github.com/jamesphotography/SuperPicky)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**智能鸟类照片筛选工具 - 让AI帮你挑选最美的鸟类照片**

一款专门为鸟类摄影师设计的智能照片筛选软件，使用AI技术自动识别、评分和筛选鸟类照片，大幅提升后期整理效率。

---

## 🌟 主要特性

### 🤖 智能识别
- **AI鸟类检测**: 基于YOLOv11模型，精准识别照片中的鸟类
- **高置信度**: 支持自定义检测阈值，确保识别准确性
- **多鸟处理**: 自动处理包含多只鸟的照片

### ⭐ 智能评分系统
- **清晰度分析**: 自动评估照片清晰度
- **构图评分**: 分析构图质量
- **美学评估**: 基于NIMA模型的美学评分
- **综合评级**: 结合多维度指标给出1-5星评级

### 🏆 自动筛选
- **智能标记**: 自动标记高质量照片 (Pick)
- **淘汰处理**: 识别并标记低质量照片 (Reject)
- **批量处理**: 支持大批量照片的快速处理
- **EXIF写入**: 将评分信息写入照片EXIF元数据

### 📊 详细报告
- **CSV报告**: 生成详细的照片分析报告
- **多维度指标**: 包含清晰度、构图、美学等详细数据
- **处理统计**: 提供处理进度和结果统计

### ⚙️ 高级设置
- **参数调节**: 支持调整识别阈值、评分权重等参数
- **性能优化**: 可配置线程数、批处理大小等
- **防休眠**: 处理过程中自动防止系统休眠

---

## 📋 系统要求

- **操作系统**: macOS 10.15 (Catalina) 或更高版本
- **芯片**: Apple Silicon (M1/M2/M3/M4) 或 Intel 芯片
- **内存**: 建议 8GB 或更多
- **硬盘空间**: 约 2GB 可用空间
- **ExifTool**: 软件内置，无需额外安装

---

## 📥 安装

### 下载安装包

1. 从 [Releases](https://github.com/jamesphotography/SuperPicky/releases) 页面下载最新的 `SuperPicky_v3.1.2.dmg`
2. 双击 DMG 文件打开
3. 将 `SuperPicky.app` 拖动到 `Applications` 文件夹
4. 首次打开时，右键点击应用选择"打开"以绕过系统安全提示

### 从源码运行 (开发者)

```bash
# 克隆仓库
git clone https://github.com/jamesphotography/SuperPicky.git
cd SuperPicky

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 运行应用
python main.py
```

---

## 🚀 快速开始

### 基本使用流程

1. **启动软件**: 双击 `SuperPicky.app`
2. **选择文件夹**: 点击"选择文件夹"按钮，选择包含鸟类照片的文件夹
3. **配置参数** (可选): 点击"高级设置"调整识别和评分参数
4. **开始处理**: 点击"开始处理"按钮
5. **查看结果**: 处理完成后，在文件夹中查看评分结果和CSV报告

### 参数说明

#### 基本设置
- **检测阈值**: 鸟类识别的置信度阈值 (默认: 0.25)
- **选中阈值**: 标记为Pick的评分阈值 (默认: 80)
- **淘汰阈值**: 标记为Reject的评分阈值 (默认: 60)

#### 高级设置
- **YOLO置信度**: YOLO模型检测置信度 (默认: 0.25)
- **IOU阈值**: 重叠检测框过滤阈值 (默认: 0.45)
- **清晰度权重**: 清晰度在总分中的权重 (默认: 50%)
- **美学权重**: 美学评分的权重 (默认: 50%)
- **线程数**: 并行处理的线程数 (默认: 4)

---

## 📊 输出说明

### EXIF元数据
处理后的照片会包含以下EXIF信息:
- **Rating**: 1-5星评级
- **XMP:Pick**: 1 (选中) / 0 (未选中) / -1 (淘汰)
- **IPTC:City**: 综合评分 (0-100)

### CSV报告
生成的 `bird_report.csv` 包含:
- 文件名和路径
- 检测置信度
- 清晰度分数
- 美学分数
- 综合评分
- 评级和Pick状态

---

## 🏗️ 项目结构

```
SuperPicky/
├── main.py                          # 主程序入口
├── ai_model.py                      # AI模型加载和鸟类检测
├── exiftool_manager.py              # EXIF元数据管理
├── advanced_config.py               # 高级配置管理
├── advanced_settings_dialog.py      # 高级设置对话框
├── utils.py                         # 工具函数
├── core/                            # 核心模块
│   ├── bird_detector.py            # 鸟类检测核心逻辑
│   ├── config_manager.py           # 配置管理
│   └── file_manager.py             # 文件管理
├── services/                        # 服务层
│   ├── image_processing_service.py # 图像处理服务
│   └── algorithm_comparison_service.py # 算法比较服务
├── img/                            # 图像资源
├── models/                         # AI模型文件
│   ├── yolo11n.pt                 # YOLO鸟类检测模型
│   └── nima_model.pth             # NIMA美学评估模型
├── exiftool/                       # ExifTool工具
├── SuperPicky.spec                 # PyInstaller打包配置
├── build_and_notarize.sh          # 构建和公证脚本
└── USER_MANUAL_CN.md              # 中文用户手册
```

---

## 🔧 开发

### 构建应用

```bash
# 使用PyInstaller构建
pyinstaller SuperPicky.spec --clean --noconfirm

# 构建并公证 (需要Apple开发者账号)
./build_and_notarize.sh
```

### 技术栈

- **GUI框架**: Tkinter + ttkthemes
- **AI模型**:
  - YOLOv11 (鸟类检测) - [Ultralytics](https://github.com/ultralytics/ultralytics)
  - NIMA (美学评估)
- **图像处理**: OpenCV, PIL
- **深度学习**: PyTorch
- **EXIF处理**: ExifTool
- **打包工具**: PyInstaller

---

## 📝 开发日志

### v3.1.3 (2025-10-20)
- 🐛 修复 PyIQA 打包问题，解决应用在其他 Mac 上无法启动的问题
- 📦 在 SuperPicky.spec 中添加 PyIQA 完整目录结构
- 📝 更新所有文档版本号到 V3.1.3

### v3.1.2 (2025-10-19)
- ✨ 添加About窗口，显示版本和版权信息
- ⚡ 优化EXIF写入逻辑，提升处理效率
- 🔧 添加防休眠功能，处理大批量照片时保持系统活跃
- 🐛 修复多个Bug和边界情况

### v3.1.0 (2025-10-18)
- ✨ 移除预览功能，专注核心处理
- ⚡ 优化评分算法和性能
- 🔧 改进高级设置界面

### v3.0.0 (2025-10-15)
- 🎉 完整重构，全新架构
- ✨ 引入NIMA美学评估模型
- 🚀 大幅提升处理速度和准确性

---

## 🤝 贡献

欢迎提交Issue和Pull Request！

### 开发指南

1. Fork本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 提交Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 👨‍💻 作者

**James Zhang**
- GitHub: [@jamesphotography](https://github.com/jamesphotography)
- Email: james@jamesphotography.com.au

---

## 🙏 致谢

- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) - 提供强大的目标检测模型
- [ExifTool](https://exiftool.org/) - Phil Harvey的优秀EXIF处理工具
- 所有为开源项目做出贡献的开发者

---

## 📞 技术支持

如遇到问题或有建议，请:
1. 查看 [用户手册](USER_MANUAL_CN.md)
2. 提交 [Issue](https://github.com/jamesphotography/SuperPicky/issues)
3. 发送邮件至 james@jamesphotography.com.au

---

**让SuperPicky成为你鸟类摄影的得力助手！** 🦅📸
