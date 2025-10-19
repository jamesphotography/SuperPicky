# SuperPicky V3.1 - 高级设置功能集成指南

## 📋 功能概述

新增高级设置功能，允许用户配置以下硬编码参数：

### 可配置参数

#### 1. 评分阈值
- **AI置信度最低阈值** (0.3-0.7, 默认0.5)
  - 低于此值 → -1星（已拒绝）

- **锐度最低阈值** (2000-6000, 默认4000)
  - 低于此值 → 0星（技术质量差）

- **摄影美学最低阈值** (3.0-5.0, 默认4.0)
  - 低于此值 → 0星（技术质量差）

- **画面噪点最高阈值** (20-50, 默认30)
  - 高于此值 → 0星（技术质量差）

#### 2. 输出设置
- **保存CSV报告** (默认开启)
- **日志详细程度** (详细/简单)

#### 3. 语言设置（后续实现）
- 中文/English

## 🗂️ 文件结构

```
SuperPicky_SandBox/
├── advanced_config.py              # 配置管理类
├── advanced_settings_dialog.py     # 设置对话框UI
├── advanced_config.json            # 配置文件（自动生成）
└── main.py                         # 需要修改的主文件
```

## 🔧 集成步骤

### 步骤1: 在main.py中导入模块

```python
# 在文件顶部添加
from advanced_config import get_advanced_config
from advanced_settings_dialog import AdvancedSettingsDialog
```

### 步骤2: 在SuperPickyApp.__init__中初始化配置

```python
def __init__(self, root):
    self.root = root
    self.config = get_advanced_config()  # 添加这行
    # ... 其他初始化代码
```

### 步骤3: 添加菜单栏

```python
def __init__(self, root):
    # ... 现有代码

    # 创建菜单栏
    menubar = tk.Menu(self.root)
    self.root.config(menu=menubar)

    # 设置菜单
    settings_menu = tk.Menu(menubar, tearoff=0)
    menubar.add_cascade(label="设置", menu=settings_menu)
    settings_menu.add_command(label="高级设置...", command=self.show_advanced_settings)

    # ... 其他初始化代码
```

### 步骤4: 添加打开高级设置的方法

```python
def show_advanced_settings(self):
    """显示高级设置对话框"""
    dialog = AdvancedSettingsDialog(self.root)
    dialog.show()
```

### 步骤5: 修改评分逻辑使用配置

#### 在WorkerThread.process_files中 (main.py 约第224-240行)

**修改前**:
```python
if not detected:
    rating_value = -1
    if confidence < 0.5:  # 硬编码
        reject_reason = "置信度太低"
    else:
        reject_reason = "完全没鸟"
elif selected:
    rating_value = 3
else:
    # 检查0星的具体原因
    if brisque is not None and brisque > 30:  # 硬编码
        rating_value = 0
        quality_issue = f"噪点过高({brisque:.1f}>30)"
    elif nima is not None and nima < 4.0:  # 硬编码
        rating_value = 0
        quality_issue = f"美学太差({nima:.1f}<4.0)"
    elif sharpness < 4000:  # 硬编码
        rating_value = 0
        quality_issue = f"锐度太低({sharpness:.0f}<4000)"
```

**修改后**:
```python
# 获取配置
config = get_advanced_config()

if not detected:
    rating_value = -1
    if confidence < config.min_confidence:  # 使用配置
        reject_reason = f"置信度太低(<{config.min_confidence:.0%})"
    else:
        reject_reason = "完全没鸟"
elif selected:
    rating_value = 3
else:
    # 检查0星的具体原因
    if brisque is not None and brisque > config.max_brisque:  # 使用配置
        rating_value = 0
        quality_issue = f"噪点过高({brisque:.1f}>{config.max_brisque})"
    elif nima is not None and nima < config.min_nima:  # 使用配置
        rating_value = 0
        quality_issue = f"美学太差({nima:.1f}<{config.min_nima:.1f})"
    elif sharpness < config.min_sharpness:  # 使用配置
        rating_value = 0
        quality_issue = f"锐度太低({sharpness:.0f}<{config.min_sharpness})"
```

### 步骤6: 同样修改ai_model.py中的评分逻辑 (约第320-326行)

**修改前**:
```python
if conf < 0.5:
    rating_value = -1
    rating_stars = "❌"
    rating_reason = "置信度太低"
elif (brisque_score is not None and brisque_score > 30) or \
     (nima_score is not None and nima_score < 4.0) or \
     sharpness < 4000:
```

**修改后**:
```python
from advanced_config import get_advanced_config
config = get_advanced_config()

if conf < config.min_confidence:
    rating_value = -1
    rating_stars = "❌"
    rating_reason = "置信度太低"
elif (brisque_score is not None and brisque_score > config.max_brisque) or \
     (nima_score is not None and nima_score < config.min_nima) or \
     sharpness < config.min_sharpness:
```

### 步骤7: 修改UI文本

#### main.py 约第367行（"选择照片目录"改为"选择照片目录"）
```python
# 保持不变
```

#### main.py 约第379行（"优选照片设置"改为"优选参数"）
```python
# 修改前
settings_frame = ttk.LabelFrame(parent, text="优选照片设置", padding=10)

# 修改后
settings_frame = ttk.LabelFrame(parent, text="优选参数", padding=10)
```

#### 移除欢迎界面中的"V3.1新特性"部分 (main.py 约第692-696行)

**修改前**:
```python
💡 V3.1新特性：
  • 对数压缩锐度 - 大小鸟公平评分
  • 摄影美学评分 - 全面评估照片质量
  • 默认锐度8000 - 更适合鸟类摄影
  • 移除预览功能 - 处理速度更快
```

**修改后**（直接删除这部分）:
```python
# 删除此部分，直接进入"使用步骤"
```

## 📊 配置文件示例

配置会自动保存到 `advanced_config.json`:

```json
{
  "min_confidence": 0.5,
  "min_sharpness": 4000,
  "min_nima": 4.0,
  "max_brisque": 30,
  "save_csv": true,
  "log_level": "detailed",
  "language": "zh_CN"
}
```

## 🎯 用户使用流程

1. 打开SuperPicky
2. 点击菜单栏 "设置" → "高级设置..."
3. 在对话框中调整参数
4. 点击"保存"
5. 重新处理照片时自动使用新设置

## ✅ 测试清单

- [ ] advanced_config.py 语法检查
- [ ] advanced_settings_dialog.py 语法检查
- [ ] main.py 集成后语法检查
- [ ] ai_model.py 修改后语法检查
- [ ] 打开高级设置对话框
- [ ] 修改参数并保存
- [ ] 验证配置文件生成
- [ ] 处理照片验证新阈值生效
- [ ] 恢复默认值功能测试

## 🔍 注意事项

1. **配置持久化**: 配置保存在JSON文件中，程序重启后自动加载
2. **参数验证**: 所有参数都有范围限制，防止用户输入无效值
3. **向后兼容**: 如果配置文件不存在，自动使用默认值
4. **即时生效**: 保存后立即生效，无需重启程序

## 📝 后续扩展

可以考虑添加的功能：
- [ ] 导出/导入配置文件
- [ ] 预设配置方案（保守/平衡/宽松）
- [ ] 配置历史记录
- [ ] 多语言支持
- [ ] EXIF字段映射自定义

---

**版本**: V3.1.0
**日期**: 2025-10-19
**作者**: SuperPicky Team
