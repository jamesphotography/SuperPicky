# 锐度归一化模式切换功能 - 集成指南

## ✅ 已完成的工作

### 1. sharpness.py ✅
- 已支持 `normalization=None` 选项
- 新增了原始方差模式（不归一化）
- 更新了文档字符串

### 2. config.py ✅
- 已在 `AIConfig` 中添加 `SHARPNESS_NORMALIZATION` 配置项
- 默认值设置为 `None`（原始方差模式）

### 3. main.py GUI ✅
- 已添加锐度归一化模式下拉选择器
- 位置：在"鸟锐度阈值"滑块下方
- 选项：
  - 原始方差(推荐)
  - sqrt归一化
  - linear归一化
  - log归一化
  - gentle归一化

---

## 🔧 待完成的集成步骤

### 步骤 1: 修改 main.py 中的 `start_processing` 方法

**文件**: `main.py`
**位置**: 大约第 1130 行附近

**需要修改的代码**：

找到这段代码：
```python
# 获取设置（[confidence, area, sharpness, center_threshold=15%, save_crop=True]）
ui_settings = [
    self.ai_var.get(),          # AI置信度 (0-100)
    self.ratio_var.get(),       # 鸟类占比 (0.5-10)
    self.sharp_var.get(),       # 锐度阈值 (0-300)
    15,                         # 居中阈值硬编码为15%
    True                        # 总是保存Crop图片（用于预览）
]
```

**修改为**：
```python
# 将归一化模式文本映射到代码值
norm_mapping = {
    "原始方差(推荐)": None,
    "sqrt归一化": "sqrt",
    "linear归一化": "linear",
    "log归一化": "log",
    "gentle归一化": "gentle"
}
selected_norm = norm_mapping.get(self.norm_var.get(), None)

# 获取设置（[confidence, area, sharpness, center_threshold=15%, save_crop=True, normalization]）
ui_settings = [
    self.ai_var.get(),          # AI置信度 (0-100)
    self.ratio_var.get(),       # 鸟类占比 (0.5-10)
    self.sharp_var.get(),       # 锐度阈值 (0-300)
    15,                         # 居中阈值硬编码为15%
    True,                       # 总是保存Crop图片（用于预览）
    selected_norm               # 锐度归一化模式
]
```

---

### 步骤 2: 修改 ai_model.py 中的 `detect_and_draw_birds` 函数

**文件**: `ai_model.py`
**位置**: 大约第 61-72 行

**当前代码**：
```python
def detect_and_draw_birds(image_path, model, output_path, dir, ui_settings, crop_temp_dir=None, center_threshold=None, preview_callback=None):
    """检测并标记鸟类"""
    # 从 ui_settings 获取参数
    ai_confidence = ui_settings[0] / 100  # AI置信度：0-100 -> 0.0-1.0
    area_threshold = ui_settings[1] / 100  # 鸟类占比：0.5-10 -> 0.005-0.1
    sharpness_threshold = ui_settings[2]   # 锐度阈值：0-300

    # 居中阈值：优先使用 ui_settings，否则使用参数或默认值
    if len(ui_settings) >= 4:
        center_threshold = ui_settings[3] / 100  # 5-40 -> 0.05-0.4
    elif center_threshold is None:
        center_threshold = config.ai.CENTER_THRESHOLD

    # 是否保存Crop图片（预览时总是临时保存）
    save_crop = ui_settings[4] if len(ui_settings) >= 5 else False
```

**修改为**：
```python
def detect_and_draw_birds(image_path, model, output_path, dir, ui_settings, crop_temp_dir=None, center_threshold=None, preview_callback=None):
    """检测并标记鸟类"""
    # 从 ui_settings 获取参数
    ai_confidence = ui_settings[0] / 100  # AI置信度：0-100 -> 0.0-1.0
    area_threshold = ui_settings[1] / 100  # 鸟类占比：0.5-10 -> 0.005-0.1
    sharpness_threshold = ui_settings[2]   # 锐度阈值：0-300

    # 居中阈值：优先使用 ui_settings，否则使用参数或默认值
    if len(ui_settings) >= 4:
        center_threshold = ui_settings[3] / 100  # 5-40 -> 0.05-0.4
    elif center_threshold is None:
        center_threshold = config.ai.CENTER_THRESHOLD

    # 是否保存Crop图片（预览时总是临时保存）
    save_crop = ui_settings[4] if len(ui_settings) >= 5 else False

    # 锐度归一化模式（新增）
    normalization_mode = ui_settings[5] if len(ui_settings) >= 6 else None
```

---

### 步骤 3: 修改 ai_model.py 中全局锐度计算器的初始化

**文件**: `ai_model.py`
**位置**: 大约第 47 行

**当前代码**：
```python
# 初始化全局锐度计算器（使用掩码 + 方差 + sqrt归一化）
_sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='sqrt')
```

**修改方案A（动态创建）**：

将全局变量改为函数，在 `detect_and_draw_birds` 中根据 `normalization_mode` 动态创建：

删除第47行的全局变量，改为：
```python
# 锐度计算器将根据用户选择动态创建
def _get_sharpness_calculator(normalization_mode=None):
    """
    获取锐度计算器实例

    Args:
        normalization_mode: 归一化模式 (None, 'sqrt', 'linear', 'log', 'gentle')

    Returns:
        MaskBasedSharpnessCalculator 实例
    """
    return MaskBasedSharpnessCalculator(method='variance', normalization=normalization_mode)
```

然后在 `detect_and_draw_birds` 函数开始处（第86-90行附近）添加：
```python
    # 根据用户选择的归一化模式创建锐度计算器
    sharpness_calculator = _get_sharpness_calculator(normalization_mode)
```

同时，将所有使用 `_sharpness_calculator` 的地方改为 `sharpness_calculator`：
- 第 245 行附近：`sharpness_result = sharpness_calculator.calculate(crop_img, mask_crop)`
- 第 256 行附近：`sharpness_result = sharpness_calculator.calculate(crop_img, full_mask)`

---

## 📝 完整修改清单

### 文件 1: main.py

**位置 1**: 第 1130 行附近（`start_processing` 方法）

添加归一化模式映射和传递：
```python
# 将归一化模式文本映射到代码值
norm_mapping = {
    "原始方差(推荐)": None,
    "sqrt归一化": "sqrt",
    "linear归一化": "linear",
    "log归一化": "log",
    "gentle归一化": "gentle"
}
selected_norm = norm_mapping.get(self.norm_var.get(), None)

# 获取设置（添加 normalization 参数）
ui_settings = [
    self.ai_var.get(),          # AI置信度 (0-100)
    self.ratio_var.get(),       # 鸟类占比 (0.5-10)
    self.sharp_var.get(),       # 锐度阈值 (0-300)
    15,                         # 居中阈值硬编码为15%
    True,                       # 总是保存Crop图片（用于预览）
    selected_norm               # 锐度归一化模式 (新增)
]
```

### 文件 2: ai_model.py

**修改 1**: 删除第 47 行的全局变量，替换为函数：
```python
# 删除这行：
# _sharpness_calculator = MaskBasedSharpnessCalculator(method='variance', normalization='sqrt')

# 替换为：
def _get_sharpness_calculator(normalization_mode=None):
    """
    获取锐度计算器实例

    Args:
        normalization_mode: 归一化模式 (None, 'sqrt', 'linear', 'log', 'gentle')

    Returns:
        MaskBasedSharpnessCalculator 实例
    """
    return MaskBasedSharpnessCalculator(method='variance', normalization=normalization_mode)
```

**修改 2**: 第 61-86 行附近，添加归一化模式参数解析：
```python
    # 锐度归一化模式（新增）
    normalization_mode = ui_settings[5] if len(ui_settings) >= 6 else None

    # 根据用户选择的归一化模式创建锐度计算器
    sharpness_calculator = _get_sharpness_calculator(normalization_mode)
```

**修改 3**: 替换所有 `_sharpness_calculator` 为 `sharpness_calculator`：
- 搜索： `_sharpness_calculator`
- 替换为：`sharpness_calculator`
- 预计有 2 处需要替换（第 245 行和第 256 行附近）

---

## ✅ 测试步骤

1. **启动应用**：
   ```bash
   python main.py
   ```

2. **验证GUI**：
   - 检查"锐度归一化"下拉框是否显示
   - 默认值应为"原始方差(推荐)"

3. **测试原始方差模式**：
   - 选择"原始方差(推荐)"
   - 处理一批照片
   - 检查CSV中的锐度值（应该是较大的数值，如 5000-30000）

4. **测试sqrt归一化模式**：
   - 选择"sqrt归一化"
   - 处理同一批照片
   - 检查CSV中的锐度值（应该是较小的数值，如 20-150）

5. **对比结果**：
   - 原始方差模式的锐度值应该比sqrt归一化大很多
   - 原始方差模式选出的Top 10应该与分析报告一致

---

## 📊 预期效果

### 原始方差模式（推荐）
- 锐度值范围：100 - 35,000
- 与NIMA相关性：**+0.319**（强）
- 与BRISQUE相关性：**-0.281**（强）
- 对鸟大小偏差：**-0.161**（小）

### sqrt归一化模式
- 锐度值范围：10 - 200
- 与NIMA相关性：+0.024（极弱）
- 与BRISQUE相关性：-0.131（弱）
- 对鸟大小偏差：-0.346（大，低估大鸟）

---

## 🎯 完成标志

当你完成以上所有修改后，应该能够：

1. ✅ 在GUI中看到"锐度归一化"下拉框
2. ✅ 切换不同的归一化模式
3. ✅ 处理照片后，CSV中的锐度值会根据选择的模式变化
4. ✅ 原始方差模式选出的优秀照片质量更高（NIMA和BRISQUE更优）

---

## 💡 调试提示

如果遇到问题：

1. **GUI显示不正常**：
   - 检查 main.py 第 453-466 行是否正确添加
   - 重启应用

2. **归一化模式不生效**：
   - 在 ai_model.py 中添加日志：
     ```python
     log_message(f"使用归一化模式: {normalization_mode}", dir)
     ```
   - 检查传递的参数是否正确

3. **锐度值异常**：
   - 检查 sharpness_calculator 是否正确创建
   - 验证 normalization_mode 的值是否为预期

---

## 📌 注意事项

1. **CSV兼容性**：原始方差和归一化模式的锐度值范围不同，不要混合使用
2. **阈值设置**：
   - 原始方差模式：建议阈值 5000-15000
   - sqrt归一化模式：建议阈值 50-150
3. **历史数据**：切换模式后，建议重置目录重新处理

---

**创建日期**: 2025-10-18
**版本**: SuperPicky V3.0
**功能**: 锐度归一化模式切换
