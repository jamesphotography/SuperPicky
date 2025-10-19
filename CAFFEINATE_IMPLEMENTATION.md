# 防休眠功能实现说明

**日期**: 2025-10-19
**版本**: V3.1.2

---

## ✅ 已完成的实现

### 功能概述

SuperPicky现在会在处理照片时**自动启动**macOS的`caffeinate`命令，防止Mac进入休眠或启动屏幕保护程序，处理完成后**自动停止**。

### 实现细节

#### 1. 修改的文件

**`main.py`**:
- 导入`subprocess`模块
- 在`WorkerThread.__init__()`中添加`self.caffeinate_process`属性
- 添加`_start_caffeinate()`方法 - 启动防休眠
- 添加`_stop_caffeinate()`方法 - 停止防休眠
- 修改`run()`方法 - 使用try-finally确保caffeinate正确启停

#### 2. caffeinate参数

```python
caffeinate -d -i
```

- `-d`: 防止显示器休眠（**同时自动阻止屏幕保护程序启动**）
- `-i`: 防止系统空闲休眠

#### 3. 生命周期管理

**启动时机**: 用户点击"开始处理"后，立即启动caffeinate

**停止时机**:
- ✅ 处理正常完成
- ✅ 用户中途取消
- ✅ 程序异常崩溃（try-finally确保）
- ✅ 用户强制关闭窗口（finally块执行）

#### 4. 用户提示

**启动时**:
```
☕ 已启动防休眠保护（处理期间Mac不会休眠或启动屏幕保护程序）
```

**停止时**:
```
☕ 已停止防休眠保护
```

**失败时**:
```
⚠️  防休眠启动失败: {错误信息}（不影响正常处理）
```

---

## 🔒 安全性保证

### 1. 异常处理

```python
try:
    self.caffeinate_process = subprocess.Popen(['caffeinate', '-d', '-i'], ...)
except Exception as e:
    # 如果启动失败，不影响正常处理
    self.caffeinate_process = None
```

### 2. 进程清理

```python
finally:
    # 无论如何都会执行停止
    self._stop_caffeinate()
```

**清理策略**:
1. 首先尝试`terminate()` - 优雅终止
2. 等待2秒超时
3. 如果失败，强制`kill()` - 强制终止
4. 确保`caffeinate_process`设为None

### 3. 不影响主流程

- caffeinate启动失败不会中断照片处理
- 只是记录警告日志
- 用户可以继续正常使用所有功能

---

## 🧪 测试场景

### 场景1: 正常完成
1. 用户选择目录并开始处理
2. 看到"☕ 已启动防休眠保护"
3. 处理过程中Mac不会休眠
4. 处理完成，看到"☕ 已停止防休眠保护"
5. Mac恢复正常的节能设置

### 场景2: 中途取消
1. 开始处理，caffeinate启动
2. 用户点击窗口关闭按钮
3. 弹出确认对话框："正在处理中，确定要退出吗？"
4. 用户点击"确定"
5. 线程停止，finally块执行，caffeinate停止
6. Mac恢复正常

### 场景3: 程序崩溃
1. 开始处理，caffeinate启动
2. 程序因某种原因崩溃
3. Python进程退出
4. caffeinate子进程自动终止（因为父进程结束）
5. Mac恢复正常

---

## 📊 技术参考

### macOS caffeinate 命令

**位置**: `/usr/bin/caffeinate`

**官方文档**: `man caffeinate`

**常用选项**:
- `-d`: Create an assertion to prevent the display from sleeping
- `-i`: Create an assertion to prevent the system from idle sleeping
- `-s`: Create an assertion to prevent the system from sleeping (valid when running on AC power)
- `-t <seconds>`: Specifies the timeout for the assertion

### Python subprocess.Popen

**优势**:
- 异步启动进程
- 不阻塞主线程
- 可以通过`terminate()`和`kill()`控制

**资源管理**:
```python
proc = subprocess.Popen(['caffeinate', '-d', '-i'],
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
# stdout和stderr重定向到DEVNULL，避免输出干扰
```

---

## 🎯 用户体验

### 优点
✅ **完全自动** - 无需用户手动操作
✅ **透明无感** - 只在日志中显示状态
✅ **安全可靠** - 多重保护确保正确清理
✅ **不需权限** - macOS系统命令，无需授权
✅ **零配置** - 不需要在设置中添加开关

### 适用场景
- 📸 处理大批量照片（几百到几千张）
- ⏰ 处理时间较长（几分钟到几小时）
- 💻 用户可能离开电脑
- 🌙 深夜批量处理

---

## 📝 代码变更总结

### 新增代码

**导入模块** (第13行):
```python
import subprocess
```

**初始化属性** (第50行):
```python
self.caffeinate_process = None  # caffeinate进程（防休眠）
```

**启动方法** (第77-90行):
```python
def _start_caffeinate(self):
    """启动caffeinate防止系统休眠和屏幕保护程序"""
    # ... 实现代码 ...
```

**停止方法** (第92-106行):
```python
def _stop_caffeinate(self):
    """停止caffeinate"""
    # ... 实现代码 ...
```

**修改run方法** (第108-123行):
```python
def run(self):
    """执行处理"""
    try:
        self._start_caffeinate()
        self.process_files()
        # ...
    finally:
        self._stop_caffeinate()
```

### 总代码量
- **新增**: 约40行
- **修改**: 约10行
- **总计**: 约50行

---

## ✅ 测试清单

准备测试时，请验证以下场景：

### 基本功能
- [ ] 点击"开始处理"后，日志显示"☕ 已启动防休眠保护"
- [ ] 处理期间，Mac不会进入休眠
- [ ] 处理期间，屏幕保护程序不会启动
- [ ] 处理完成后，日志显示"☕ 已停止防休眠保护"
- [ ] 处理完成后，Mac恢复正常的节能设置

### 异常处理
- [ ] 中途点击关闭窗口，确认后caffeinate正确停止
- [ ] 处理单张图片测试（快速完成，caffeinate正常启停）
- [ ] 处理大批量图片测试（长时间运行，防休眠持续有效）

### 系统验证
- [ ] 在终端运行`ps aux | grep caffeinate`，处理时能看到进程
- [ ] 处理完成后，caffeinate进程消失
- [ ] 系统的"节能"设置没有被修改

---

## 🚀 未来可选增强

如果需要，可以考虑以下增强（当前版本不实现）：

1. **添加高级设置开关** - 让用户选择是否启用防休眠
2. **在统计报告中显示** - 记录是否使用了防休眠
3. **添加快捷键** - Cmd+D 手动切换防休眠状态
4. **Windows兼容** - 使用pywin32实现Windows平台防休眠

---

**实现状态**: ✅ 完成
**测试状态**: ⏳ 待测试
**部署状态**: ⏳ 待部署

**作者**: SuperPicky Team
**完成日期**: 2025-10-19
