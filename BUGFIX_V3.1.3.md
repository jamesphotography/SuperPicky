# SuperPicky V3.1.3 - 打包问题修复说明

## 🐛 问题描述

**错误类型**: `FileNotFoundError`
**错误信息**: `[Errno 2] No such file or directory: '/Applications/SuperPicky.app/Contents/Frameworks/pyiqa/models'`

**发生场景**: 在其他用户的 Mac 机器上安装并运行 SuperPicky V3.1.2 时，应用启动失败。

**根本原因**: PyInstaller 打包时没有正确包含 PyIQA 库的子目录结构，导致运行时找不到 `pyiqa/models` 等目录。

---

## ✅ 修复方案

### 1. 修改 `SuperPicky.spec` 文件

在 `datas` 列表中添加 PyIQA 的完整目录结构：

```python
datas=[
    # ... 其他配置 ...

    # PyIQA 完整目录结构（修复 FileNotFoundError）
    (os.path.join(venv_path, 'pyiqa/models'), 'pyiqa/models'),
    (os.path.join(venv_path, 'pyiqa/archs'), 'pyiqa/archs'),
    (os.path.join(venv_path, 'pyiqa/data'), 'pyiqa/data'),
    (os.path.join(venv_path, 'pyiqa/utils'), 'pyiqa/utils'),
    (os.path.join(venv_path, 'pyiqa/metrics'), 'pyiqa/metrics'),
    (os.path.join(venv_path, 'pyiqa/losses'), 'pyiqa/losses'),
    (os.path.join(venv_path, 'pyiqa/matlab_utils'), 'pyiqa/matlab_utils'),
],
```

### 2. 添加 PyIQA 隐藏导入

在 `hiddenimports` 列表中添加 PyIQA 模块：

```python
hiddenimports=[
    # ... 其他配置 ...

    # PyIQA 隐藏导入（修复 FileNotFoundError）
    'pyiqa',
    'pyiqa.models',
    'pyiqa.archs',
    'pyiqa.data',
    'pyiqa.utils',
    'pyiqa.metrics',
    'pyiqa.losses',
    'pyiqa.matlab_utils',
],
```

---

## 🔧 重新打包步骤

### 步骤 1: 清理旧的构建文件

```bash
cd /Users/jameszhenyu/PycharmProjects/SuperPicky_SandBox
rm -rf build/ dist/ SuperPicky.app
```

### 步骤 2: 重新打包应用

```bash
pyinstaller SuperPicky.spec --clean --noconfirm
```

### 步骤 3: 验证打包结果

检查打包后的应用是否包含 PyIQA 目录：

```bash
ls -la dist/SuperPicky.app/Contents/Resources/pyiqa/
```

应该看到以下目录：
- `models/`
- `archs/`
- `data/`
- `utils/`
- `metrics/`
- `losses/`
- `matlab_utils/`

### 步骤 4: 本地测试

```bash
open dist/SuperPicky.app
```

检查应用是否能正常启动，不再出现 `FileNotFoundError`。

### 步骤 5: 代码签名和公证（可选）

如果需要分发给其他用户：

```bash
./build_and_notarize.sh
```

---

## 📦 技术细节

### PyIQA 库的目录结构

PyIQA（Python Image Quality Assessment）库使用动态扫描来加载模型文件：

```python
# pyiqa/utils/misc.py:84
def _scandir(dir_path):
    return os.scandir(dir_path)  # 需要实际目录存在
```

这意味着 PyIQA 在运行时会扫描 `pyiqa/models` 目录以发现可用的模型。如果该目录不存在，就会抛出 `FileNotFoundError`。

### PyInstaller 打包机制

PyInstaller 默认只打包 Python 模块的 `.py` 和 `.pyc` 文件，不会自动包含：
1. 子目录结构
2. 非 Python 文件（如 YAML 配置、模型文件等）
3. 动态加载的模块

因此需要在 `.spec` 文件中显式指定这些资源。

---

## 🧪 测试清单

在发布新版本前，请确保：

- [ ] 在开发机器上能正常运行
- [ ] 打包后的应用能正常启动
- [ ] 在另一台 Mac 上测试（无开发环境）
- [ ] 能正常加载 AI 模型
- [ ] 能正常处理照片并写入 EXIF
- [ ] 检查应用大小是否合理（预期 ~2-3GB）

---

## 📊 受影响的版本

- **问题版本**: V3.1.2 及之前的版本
- **修复版本**: V3.1.3

---

## 🔄 升级说明

### 对于开发者

1. 拉取最新代码
2. 检查 `SuperPicky.spec` 文件是否包含 PyIQA 配置
3. 重新打包应用

### 对于用户

1. 卸载旧版本 SuperPicky
2. 下载并安装 V3.1.3 或更高版本
3. 首次运行可能需要在"系统偏好设置 > 安全性与隐私"中允许

---

## 📝 相关文件

- **修复文件**: `SuperPicky.spec:32-39` (datas), `SuperPicky.spec:56-64` (hiddenimports)
- **错误来源**: `pyiqa/utils/misc.py:84` (_scandir 函数)
- **影响模块**: `iqa_scorer.py`, `ai_model.py`, `main.py`

---

## 💡 预防措施

为了避免类似问题，建议：

1. **在干净的环境中测试**: 每次发布前，在没有开发环境的 Mac 上测试
2. **使用虚拟机**: 使用 macOS 虚拟机进行打包测试
3. **详细的打包日志**: 启用 PyInstaller 的详细日志，检查哪些文件被包含
4. **自动化测试**: 编写脚本自动检查打包后的应用结构

---

## 📞 联系方式

如果遇到其他打包或运行问题，请联系：

- **开发者**: 詹姆斯·于震 (James Zhen Yu)
- **邮箱**: james@jamesphotography.com.au

---

**修复日期**: 2025-10-20
**修复版本**: V3.1.3
