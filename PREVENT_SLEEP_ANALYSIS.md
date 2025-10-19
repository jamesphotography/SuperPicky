# macOS防止休眠和屏幕保护程序 - 实现分析

**日期**: 2025-10-19
**目的**: 在SuperPicky处理照片时防止Mac进入休眠和启动屏幕保护程序

---

## 📊 技术方案分析

### 方案1: 使用macOS内置的`caffeinate`命令 ⭐ **推荐**

#### 优点
✅ **无需特殊权限** - `caffeinate`是macOS系统自带命令，普通用户权限即可运行
✅ **实现简单** - 通过Python的subprocess调用即可
✅ **稳定可靠** - Apple官方提供的工具，稳定性有保证
✅ **灵活控制** - 可以通过进程控制随时启动/停止

#### 缺点
❌ 需要保持caffeinate进程运行
❌ 如果进程异常退出，保护会失效

#### 实现方式

```python
import subprocess

# 启动caffeinate（防止休眠）
caffeinate_process = subprocess.Popen(['caffeinate', '-d', '-i'])

# 处理照片...
# ... SuperPicky的主要逻辑 ...

# 完成后终止caffeinate
caffeinate_process.terminate()
```

#### caffeinate参数说明

- **无参数**: 防止空闲休眠（idle sleep）
- **`-d`**: 防止显示器休眠（prevent display sleep）
- **`-i`**: 防止系统空闲休眠（prevent system idle sleep）
- **`-s`**: 防止系统休眠（只在接入电源时有效）
- **`-t 秒数`**: 指定持续时间

**推荐组合**: `-d -i`
- 既防止显示器休眠（屏幕不黑屏）
- 又防止系统空闲休眠
- **自动阻止屏幕保护程序启动**

---

### 方案2: 使用PyObjC库直接调用macOS API

#### 优点
✅ 更底层的控制
✅ 不依赖外部进程

#### 缺点
❌ 需要安装额外依赖（PyObjC）
❌ 代码复杂度高
❌ 可能在macOS版本更新后失效

#### 实现示例

```python
import objc
from Cocoa import NSProcessInfo

# 创建activity
activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
    0x00FFFFFF,  # NSActivityUserInitiated | NSActivityIdleSystemSleepDisabled
    "SuperPicky正在处理照片"
)

# 处理照片...

# 结束activity
NSProcessInfo.processInfo().endActivity_(activity)
```

---

## 🎯 权限要求分析

### ✅ caffeinate不需要特殊权限

**结论**: `caffeinate`命令**不需要用户授予特殊权限**

**原因**:
1. `caffeinate`是macOS系统自带工具（位于`/usr/bin/caffeinate`）
2. 普通用户权限即可运行
3. 它只是向系统发送"保持唤醒"的断言（assertion），不修改系统设置
4. 与需要"完整磁盘访问权限"的工具不同，caffeinate属于用户级别的命令

**验证方法**:
```bash
# 直接在终端运行（无需sudo）
caffeinate -d -i -t 60  # 保持唤醒60秒
```

### ❌ 不需要在高级设置中添加权限选项

因为caffeinate不需要特殊权限，所以：
- ❌ 不需要在高级设置中添加"授权"选项
- ❌ 不需要引导用户去"系统设置 > 隐私与安全性"授权
- ✅ 只需添加一个开关让用户选择是否启用防休眠功能即可

---

## 💡 推荐实现方案

### 在高级设置中添加开关

**高级配置选项**:
```json
{
    "prevent_sleep": true,  // 默认启用
    "prevent_sleep_description": "处理照片时防止Mac休眠和启动屏幕保护程序"
}
```

### 实现步骤

#### 1. 修改 `advanced_config.py`

添加配置项：
```python
class AdvancedConfig:
    def __init__(self):
        # ... 现有配置 ...

        # 防止休眠设置
        self.prevent_sleep = True  # 默认启用
```

#### 2. 修改 `advanced_settings_dialog.py`

在高级设置对话框中添加选项：
```python
# 防止休眠选项
prevent_sleep_var = tk.BooleanVar(value=config.prevent_sleep)
prevent_sleep_check = ttk.Checkbutton(
    frame,
    text="处理时防止系统休眠和屏幕保护程序",
    variable=prevent_sleep_var
)
```

#### 3. 修改 `main.py` 的 `ProcessWorker`类

在处理开始和结束时控制caffeinate：

```python
class ProcessWorker:
    def __init__(self, ...):
        # ... 现有代码 ...
        self.caffeinate_process = None

    def run(self):
        try:
            # 获取配置
            config = get_advanced_config()

            # 如果启用防休眠，启动caffeinate
            if config.prevent_sleep:
                self._start_caffeinate()

            # 现有的处理逻辑
            # ... 扫描、转换、AI检测等 ...

        finally:
            # 确保停止caffeinate（即使出错也要停止）
            if self.caffeinate_process:
                self._stop_caffeinate()

    def _start_caffeinate(self):
        """启动caffeinate防止休眠"""
        try:
            self.caffeinate_process = subprocess.Popen(
                ['caffeinate', '-d', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.log_callback("☕ 已启动防休眠保护（处理期间Mac不会休眠）")
        except Exception as e:
            self.log_callback(f"⚠️  防休眠启动失败: {e}（不影响正常处理）")

    def _stop_caffeinate(self):
        """停止caffeinate"""
        try:
            if self.caffeinate_process:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=2)
                self.log_callback("☕ 已停止防休眠保护")
        except Exception as e:
            # 即使终止失败也不影响主流程
            try:
                self.caffeinate_process.kill()
            except:
                pass
```

---

## ⚠️ 注意事项

### 1. 进程管理

**重要**: 必须确保caffeinate进程在以下情况都能正确终止：
- ✅ 正常处理完成
- ✅ 用户中途取消
- ✅ 程序异常崩溃
- ✅ 用户强制关闭程序

**解决方案**: 使用try-finally块确保清理

### 2. 用户体验

**建议显示状态**:
```
开始处理时:
☕ 已启动防休眠保护（处理期间Mac不会休眠）

处理完成时:
☕ 已停止防休眠保护
```

### 3. 默认值建议

**推荐默认启用**:
- 照片处理往往需要较长时间（几分钟到几小时）
- 如果中途休眠会中断处理
- 用户期望处理过程不被打断

### 4. 兼容性

**测试环境**:
- ✅ macOS 10.14+ (Mojave及以上)
- ✅ Apple Silicon (M1/M2/M3)
- ✅ Intel Mac

caffeinate是macOS的标准组件，所有现代macOS版本都支持。

---

## 🧪 测试方法

### 功能测试

```python
# 测试脚本
import subprocess
import time

# 启动caffeinate
proc = subprocess.Popen(['caffeinate', '-d', '-i'])
print("防休眠已启动，PID:", proc.pid)

# 模拟长时间处理
print("等待60秒，期间Mac不应休眠...")
time.sleep(60)

# 停止caffeinate
proc.terminate()
proc.wait()
print("防休眠已停止")
```

### 验证方法

1. 在"系统设置 > 锁定屏幕"中设置较短的休眠时间（如1分钟）
2. 运行SuperPicky处理照片
3. 观察Mac是否在处理期间保持唤醒
4. 处理完成后，系统应恢复正常的休眠设置

---

## 📋 实现清单

### 必须实现
- [ ] 在`advanced_config.py`添加`prevent_sleep`配置项
- [ ] 在`advanced_settings_dialog.py`添加UI开关
- [ ] 在`ProcessWorker.run()`中实现caffeinate启动/停止
- [ ] 添加异常处理确保caffeinate正确清理

### 可选实现
- [ ] 在日志中显示防休眠状态
- [ ] 在统计报告中说明是否启用了防休眠
- [ ] 添加单元测试验证caffeinate控制逻辑

---

## 🎯 总结

### 最佳方案: caffeinate + 高级设置开关

**为什么选择这个方案**:
1. ✅ **无需权限** - 不需要用户手动授权
2. ✅ **实现简单** - 只需调用系统命令
3. ✅ **用户友好** - 提供开关让用户选择
4. ✅ **稳定可靠** - Apple官方工具
5. ✅ **自动阻止屏幕保护程序** - `-d`参数自动处理

### 不推荐的做法
❌ 要求用户在系统设置中授权
❌ 修改系统的节能设置
❌ 使用第三方库（增加依赖）

---

**建议**: 立即实现此功能，默认启用，用户可在高级设置中关闭。

**预期效果**:
- 用户开始处理照片后，即使离开电脑，Mac也不会休眠或启动屏幕保护程序
- 处理完成后，Mac恢复正常的节能设置
- 完全透明，无需用户干预

---

**作者**: SuperPicky Team
**日期**: 2025-10-19
