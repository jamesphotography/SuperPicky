# SuperPicky V3.1.3 - 快速修复指南

## 问题症状

在其他 Mac 上运行 SuperPicky 时出现以下错误：

```
FileNotFoundError: [Errno 2] No such file or directory:
'/Applications/SuperPicky.app/Contents/Frameworks/pyiqa/models'
```

---

## 快速修复（3 步骤）

### 步骤 1: 更新 SuperPicky.spec

在 `datas` 列表末尾添加：

```python
# PyIQA 完整目录结构（修复 FileNotFoundError）
(os.path.join(venv_path, 'pyiqa/models'), 'pyiqa/models'),
(os.path.join(venv_path, 'pyiqa/archs'), 'pyiqa/archs'),
(os.path.join(venv_path, 'pyiqa/data'), 'pyiqa/data'),
(os.path.join(venv_path, 'pyiqa/utils'), 'pyiqa/utils'),
(os.path.join(venv_path, 'pyiqa/metrics'), 'pyiqa/metrics'),
(os.path.join(venv_path, 'pyiqa/losses'), 'pyiqa/losses'),
(os.path.join(venv_path, 'pyiqa/matlab_utils'), 'pyiqa/matlab_utils'),
```

在 `hiddenimports` 列表末尾添加：

```python
# PyIQA 隐藏导入（修复 FileNotFoundError）
'pyiqa',
'pyiqa.models',
'pyiqa.archs',
'pyiqa.data',
'pyiqa.utils',
'pyiqa.metrics',
'pyiqa.losses',
'pyiqa.matlab_utils',
```

### 步骤 2: 清理并重新打包

```bash
cd /path/to/SuperPicky_SandBox
rm -rf build/ dist/ SuperPicky.app
pyinstaller SuperPicky.spec --clean --noconfirm
```

### 步骤 3: 测试

```bash
open dist/SuperPicky.app
```

检查应用是否能正常启动。

---

## 验证修复

运行以下命令检查打包是否成功：

```bash
# 检查 pyiqa 目录是否存在
ls -la dist/SuperPicky.app/Contents/Resources/pyiqa/

# 应该看到这些目录：
# models/
# archs/
# data/
# utils/
# metrics/
# losses/
# matlab_utils/
```

---

## 如果仍然失败

1. **检查虚拟环境路径**:
   确保 `SuperPicky.spec` 中的 `venv_path` 指向正确的虚拟环境。

2. **检查 PyIQA 安装**:
   ```bash
   python3 -c "import pyiqa; print(pyiqa.__file__)"
   ```

3. **查看详细日志**:
   在终端中直接运行应用以查看错误详情：
   ```bash
   /Applications/SuperPicky.app/Contents/MacOS/SuperPicky
   ```

---

## 常见问题

**Q: 为什么需要手动添加这些目录？**
A: PyInstaller 只自动打包 Python 模块文件，不会自动包含子目录结构。PyIQA 使用 `os.scandir()` 动态扫描目录，因此必须包含实际目录。

**Q: 会增加应用大小吗？**
A: 是的，大约增加 50-100MB。这是确保应用正常运行的必要代价。

**Q: 其他依赖会有类似问题吗？**
A: 可能。如果添加新的依赖库，建议在干净环境中测试打包结果。

---

## 联系支持

- **邮箱**: james@jamesphotography.com.au
- **文档**: 参见 `BUGFIX_V3.1.3.md` 获取详细技术说明

---

**最后更新**: 2025-10-20
**适用版本**: V3.1.3 及更高版本
