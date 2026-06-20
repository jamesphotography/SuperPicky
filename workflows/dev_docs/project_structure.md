# SuperPicky 项目目录结构

> 最后更新：2026-01-27

## 📁 目录概览

```
SuperPicky/
├── 📂 核心代码
├── 📂 模块目录
├── 📂 资源文件
├── 📂 构建配置
└── 📂 文档
```

---

## 🔧 核心代码

| 文件 | 说明 |
|------|------|
| `main.py` | 应用入口 |
| `config.py` | 全局配置 |
| `constants.py` | 常量定义 |
| `advanced_config.py` | 高级配置管理 |

### AI 模型相关
| 文件 | 说明 |
|------|------|
| `ai_model.py` | YOLO 检测 + 分割模型 |
| `iqa_scorer.py` | IQA 图像质量评估 |
| `nima_model.py` | NIMA 美学评分 |
| `topiq_model.py` | TOPIQ 技术质量评估 |
| `post_adjustment_engine.py` | 后处理调整引擎 |

### 服务端
| 文件 | 说明 |
|------|------|
| `birdid_server.py` | BirdID HTTP 服务 |
| `server_manager.py` | 服务进程管理 |

### CLI 工具
| 文件 | 说明 |
|------|------|
| `superpicky_cli.py` | 照片筛选 CLI |
| `birdid_cli.py` | 鸟类识别 CLI |

---

## 📂 模块目录

### `core/` - 核心算法
| 文件 | 说明 |
|------|------|
| `burst_detector.py` | 连拍检测器 |
| `focus_point_detector.py` | 对焦点检测（多品牌支持） |
| `photo_processor.py` | 照片处理流水线 |
| `rating_engine.py` | 评分引擎 |
| `scorer.py` | 综合评分计算 |
| `seg_evaluator.py` | 分割质量评估 |
| `sharpness_evaluator.py` | 锐度评估 |

### `ui/` - 图形界面
| 文件 | 说明 |
|------|------|
| `main_window.py` | 主窗口 |
| `birdid_dock.py` | 鸟类识别 Dock 面板 |
| `tray_icon.py` | 系统托盘图标 |

### `birdid/` - 鸟类识别模块
| 目录/文件 | 说明 |
|------|------|
| `bird_identifier.py` | 鸟类识别核心 |
| `ebird_country_filter.py` | eBird 地区过滤 |
| `data/` | 物种数据库 |
| `models/` | 鸟类分类模型 |

### `tools/` - 工具模块
| 文件 | 说明 |
|------|------|
| `exiftool_manager.py` | ExifTool 封装 |
| `i18n.py` | 国际化支持 |
| `safe_logger.py` | 线程安全日志 |

---

## 📦 资源文件

### `models/` - AI 模型
| 文件 | 说明 | 大小 |
|------|------|------|
| `yolo11l-seg.pt` | YOLO 分割模型 | ~56MB |
| `cfanet_iaa_ava_res50.pth` | 美学评分模型 | ~294MB |
| `cub200_keypoint_resnet50.pth` | 关键点检测模型 | ~297MB |
| `superFlier_efficientnet.pth` | 飞鸟检测模型 | ~43MB |

### `locales/` - 国际化
| 文件/目录 | 说明 |
|------|------|
| `en_US.json` | 英文 UI 翻译 |
| `zh_CN.json` | 中文 UI 翻译 |
| `en.lproj/` | macOS 英文应用名 |
| `zh-Hans.lproj/` | macOS 中文应用名 |

### `img/` - 图片资源
应用图标和界面图片。

### `exiftools_mac/` & `exiftools_win/`
ExifTool 二进制文件（macOS 和 Windows）。

---

## 🔨 构建配置

| 文件 | 说明 |
|------|------|
| `SuperPicky.spec` | PyInstaller 打包配置 |
| `entitlements.plist` | macOS 权限声明 |
| `create_pkg_dmg_v4.0.0.sh` | PKG/DMG 构建脚本 |
| `build_release_mac.py` / `build_release_win.py` | 发布构建主脚本 |
| `requirements.txt` | Python 依赖 |

---

## 📚 文档

### `docs/` - 网站 & 用户文档
| 文件 | 说明 |
|------|------|
| `index.html` | 官网首页 |
| `faq.html` | 常见问题 |
| `tutorial.html` | 使用教程 |
| `css/`, `img/` | 网站资源 |
| `wechat/` | 微信公众号文章 |

### `workflows/` - 开发文档
| 文件 | 说明 |
|------|------|
| `Focus-Points-Analysis.md` | 对焦点分析 |
| `intel-build.md` | Intel 构建指南 |
| `upload-gdrive.md` | Google Drive 上传 |

---

## 🚫 不纳入版本控制

以下目录/文件在 `.gitignore` 中排除：

| 类型 | 内容 |
|------|------|
| 构建产物 | `dist/`, `build/`, `*.dmg`, `*.pkg` |
| Python 缓存 | `__pycache__/`, `*.pyc` |
| 虚拟环境 | `.venv/`, `venv/` |
| IDE 配置 | `.idea/`, `.vscode/` |
| AI 工具 | `.agent/`, `.claude/` |
| 临时文件 | `temp/`, `.superpicky_temp/` |
