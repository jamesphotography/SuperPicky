# SuperPicky V3.1.3 Release Notes

**发布日期**: 2025-10-20
**版本**: 3.1.3
**类型**: 紧急 Bug 修复版本

---

## 🚨 重要提示

**如果你正在使用 V3.1.2 或更早版本，强烈建议升级到 V3.1.3！**

V3.1.2 存在严重的打包问题，会导致应用在其他用户的 Mac 上无法启动。本次更新修复了这个关键问题。

---

## 🐛 关键 Bug 修复

### PyInstaller 打包问题修复

**问题描述**:
- V3.1.2 在其他用户的 Mac 上启动时崩溃
- 错误信息: `FileNotFoundError: [Errno 2] No such file or directory: '/Applications/SuperPicky.app/Contents/Frameworks/pyiqa/models'`
- 根本原因: PyInstaller 打包时没有正确包含 PyIQA 库的子目录结构

**修复内容**:
1. ✅ 在 `SuperPicky.spec` 中添加 PyIQA 完整目录结构
   - `pyiqa/models` - AI 模型定义
   - `pyiqa/archs` - 网络架构
   - `pyiqa/data` - 数据处理
   - `pyiqa/utils` - 工具函数
   - `pyiqa/metrics` - 评估指标
   - `pyiqa/losses` - 损失函数
   - `pyiqa/matlab_utils` - MATLAB 工具

2. ✅ 添加 PyIQA 相关的隐藏导入
   - 确保所有 PyIQA 子模块都能被正确打包

**影响范围**:
- 所有在 V3.1.2 上遇到启动崩溃的用户
- 所有需要在多台 Mac 上分发应用的用户

**修复位置**: `SuperPicky.spec:32-64`

---

## 🔄 版本更新

- 应用版本号从 V3.1.2 更新到 V3.1.3
- 所有界面和文档中的版本号已统一更新

---

## 📋 完整变更日志

```
[V3.1.3 - 2025-10-20]
🐛 修复 PyInstaller 打包问题，添加 PyIQA 完整目录结构
🔧 在 SuperPicky.spec 中添加 pyiqa 的 7 个子目录
🔧 添加 PyIQA 相关的隐藏导入
📝 更新版本号到 V3.1.3
📄 添加详细的 Bug 修复文档 (BUGFIX_V3.1.3.md)
```

---

## ⚠️ 已知问题

无。V3.1.3 已在多台 Mac 上测试通过。

---

## 🚀 升级指南

### 对于用户

1. **卸载旧版本**:
   ```bash
   # 将 SuperPicky 从 Applications 文件夹拖到废纸篓
   ```

2. **下载新版本**:
   - 下载 `SuperPicky_V3.1.3.dmg`
   - 双击挂载 DMG 文件
   - 将 SuperPicky.app 拖入 Applications 文件夹

3. **首次运行**:
   - 可能需要在"系统偏好设置 > 安全性与隐私"中允许运行

### 对于开发者

1. **拉取最新代码**:
   ```bash
   git pull origin master
   ```

2. **检查 SuperPicky.spec**:
   确保包含以下配置：
   ```python
   datas=[
       # PyIQA 完整目录结构（修复 FileNotFoundError）
       (os.path.join(venv_path, 'pyiqa/models'), 'pyiqa/models'),
       (os.path.join(venv_path, 'pyiqa/archs'), 'pyiqa/archs'),
       (os.path.join(venv_path, 'pyiqa/data'), 'pyiqa/data'),
       (os.path.join(venv_path, 'pyiqa/utils'), 'pyiqa/utils'),
       (os.path.join(venv_path, 'pyiqa/metrics'), 'pyiqa/metrics'),
       (os.path.join(venv_path, 'pyiqa/losses'), 'pyiqa/losses'),
       (os.path.join(venv_path, 'pyiqa/matlab_utils'), 'pyiqa/matlab_utils'),
   ]

   hiddenimports=[
       # PyIQA 隐藏导入（修复 FileNotFoundError）
       'pyiqa',
       'pyiqa.models',
       'pyiqa.archs',
       'pyiqa.data',
       'pyiqa.utils',
       'pyiqa.metrics',
       'pyiqa.losses',
       'pyiqa.matlab_utils',
   ]
   ```

3. **重新打包**:
   ```bash
   rm -rf build/ dist/ SuperPicky.app
   pyinstaller SuperPicky.spec --clean --noconfirm
   ```

4. **测试**:
   - 在开发机器上测试
   - 在干净的 Mac 环境中测试（推荐）

5. **发布**:
   ```bash
   ./build_and_notarize.sh
   ```

---

## 🧪 测试清单

本版本已完成以下测试：

- [x] 在开发机器上能正常运行
- [x] 打包后的应用能正常启动
- [x] PyIQA 模块能正常导入
- [x] 能正常加载 AI 模型
- [x] 能正常处理照片并写入 EXIF
- [x] 所有 PyIQA 子目录都被正确打包

**推荐测试**: 在没有开发环境的 Mac 上测试（最接近用户环境）

---

## 📊 版本对比

| 功能 | V3.1.2 | V3.1.3 |
|------|--------|--------|
| PyIQA 打包 | ❌ 不完整 | ✅ 完整 |
| 其他机器启动 | ❌ 崩溃 | ✅ 正常 |
| 关于窗口 | ✅ | ✅ |
| 防休眠功能 | ✅ | ✅ |
| EXIF 优化 | ✅ | ✅ |

---

## 📚 相关文档

- **详细修复说明**: `BUGFIX_V3.1.3.md`
- **用户手册**: `USER_MANUAL_CN.md`
- **项目 README**: `README.md`

---

## 📦 下载链接

- **macOS (Apple Silicon)**: `SuperPicky_V3.1.3_arm64.dmg`
- **macOS (Intel)**: `SuperPicky_V3.1.3_x86_64.dmg`
- **通用版本**: `SuperPicky_V3.1.3.dmg`

**文件大小**: 约 2.5GB
**MD5 校验**: 待生成
**系统要求**: macOS 10.15 或更高版本

---

## 🙏 致谢

特别感谢报告此问题的用户 @xuejiaowu，你的反馈帮助我们快速定位并修复了这个关键问题！

---

## 📞 技术支持

如果你在使用 V3.1.3 时遇到任何问题：

1. **检查日志**:
   ```bash
   /Applications/SuperPicky.app/Contents/MacOS/SuperPicky
   ```
   在终端中运行以查看详细错误信息

2. **联系开发者**:
   - **邮箱**: james@jamesphotography.com.au
   - **网站**: www.jamesphotography.com.au
   - **YouTube**: youtube.com/@JamesZhenYu

3. **提供信息**:
   - macOS 版本
   - 错误截图
   - 终端日志输出

---

## 🔮 未来计划

- [ ] Windows 平台支持
- [ ] 自动更新功能
- [ ] 更详细的错误提示
- [ ] 性能优化

---

## 📜 许可证

Copyright © 2024-2025 詹姆斯·于震 (James Yu)

SuperPicky 基于开源技术开发，仅供个人非商业使用。

---

**慧眼选鸟 - 让 AI 帮你挑选最美的瞬间 🦅📸**

---

## 📝 版本历史

- **V3.1.3** (2025-10-20): 修复 PyIQA 打包问题
- **V3.1.2** (2025-10-19): 添加关于窗口、防休眠功能
- **V3.1.1** (2025-10-18): 修复 ExifTool 重置问题
- **V3.1.0** (2025-10-15): EXIF 优化、精选旗标计算
- **V3.0.1** (2025-10-12): Bug 修复
- **V3.0.0** (2025-10-10): 完整重构版本
