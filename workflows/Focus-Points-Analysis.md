# Focus-Points 项目深度分析报告

## 项目概述

**Focus-Points** 是一个专业的 Adobe Lightroom Classic 插件，用于可视化相机拍摄时记录的自动对焦点信息。

| 属性 | 值 |
|------|-----|
| **当前版本** | v3.1.2 |
| **开发语言** | Lua (Lightroom SDK) |
| **代码规模** | ~8,500 行 Lua 代码 (28 个 .lua 文件) |
| **支持平台** | Windows + macOS |
| **支持品牌** | Canon, Nikon, Sony, Fuji, Olympus/OM, Panasonic, Pentax, Ricoh, Apple |
| **许可证** | Apache 2.0 |

---

## 一、项目架构总览

### 1.1 目录结构

```
Focus-Points/
├── focuspoints.lrplugin/          # 主插件目录
│   ├── Info.lua                   # 插件清单和入口定义
│   ├── FocusPoint.lua             # 主功能入口 - 焦点显示对话框
│   ├── ShowMetadata.lua           # 元数据查看器
│   │
│   ├── 核心模块:
│   │   ├── DefaultPointRenderer.lua  # 焦点渲染引擎 (534行)
│   │   ├── PointsRendererFactory.lua # 渲染器工厂 (182行)
│   │   ├── DefaultDelegates.lua      # 焦点点模板定义
│   │   ├── FocusInfo.lua             # 信息面板
│   │   └── FocusPointPrefs.lua       # 偏好设置
│   │
│   ├── 相机品牌代理 (9个):
│   │   ├── CanonDelegates.lua        # 455行
│   │   ├── NikonDelegates.lua        # 642行 (最复杂)
│   │   ├── SonyDelegates.lua
│   │   ├── FujifilmDelegates.lua
│   │   ├── PentaxDelegates.lua
│   │   ├── OlympusDelegates.lua
│   │   ├── PanasonicDelegates.lua
│   │   └── AppleDelegates.lua
│   │
│   ├── 工具模块:
│   │   ├── ExifUtils.lua             # EXIF元数据读取
│   │   ├── PointsUtils.lua           # 焦点计算工具
│   │   ├── affine.lua                # 仿射变换库
│   │   ├── MogrifyUtils.lua          # ImageMagick集成(Windows)
│   │   └── Log.lua                   # 日志系统
│   │
│   ├── bin/
│   │   ├── exiftool/                 # ExifTool 可执行文件
│   │   └── ImageMagick/              # ImageMagick (仅Windows)
│   │
│   ├── focus_points/                 # 焦点坐标数据库
│   │   ├── nikon corporation/        # 31个Nikon机型
│   │   └── pentax/                   # Pentax机型
│   │
│   └── assets/imgs/                  # 焦点图标资源
│       ├── corner/{color}/           # 角标图标 (0-360°)
│       └── center/{color}/           # 中心点图标
```

### 1.2 技术栈

| 技术 | 用途 |
|------|------|
| **Lua** | 主编程语言 (Lightroom SDK 标准) |
| **ExifTool** | 读取相机 EXIF/MakerNotes 元数据 |
| **ImageMagick** | Windows 下图像绘制 |
| **AutoHotkey** | Windows 全局快捷键 |
| **Lightroom SDK** | LrApplication, LrView, LrDialogs 等 API |

### 1.3 核心设计模式

```
┌─────────────────────────────────────────────────────────────┐
│                    Lightroom Application                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                ┌────────▼────────────┐
                │   FocusPoint.lua    │  ← 用户入口
                └────────┬────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼─────┐  ┌──────▼──────┐  ┌─────▼────────┐
   │Renderer  │  │  ExifUtils  │  │  FocusInfo   │
   │ Factory  │  │ (元数据读取) │  │  (信息面板)   │
   └────┬─────┘  └─────────────┘  └──────────────┘
        │
   ┌────▼──────────────────────────────────────┐
   │         Camera Delegates (策略模式)        │
   ├───────────┬───────────┬───────────────────┤
   │  Canon    │  Nikon    │  Sony/Fuji/...   │
   └───────────┴───────────┴───────────────────┘
        │
   ┌────▼──────────────────────────────────────┐
   │    DefaultPointRenderer (坐标变换+渲染)    │
   └───────────────────────────────────────────┘
```

---

## 二、焦点位置读取算法详解

### 2.1 数据来源

焦点数据主要从 **EXIF MakerNotes** 中读取，而非通用 EXIF 标签：

```
相机RAW文件 → ExifTool解析 → MakerNotes提取 → 相机特定Delegates处理
```

**ExifTool 调用命令** (`ExifUtils.lua`):
```bash
exiftool -config ExifTool.config -a -u -sort -XMP-crs:all photo.NEF > metadata.txt
```

### 2.2 各品牌焦点数据标签

| 品牌 | 关键 MakerNotes 标签 |
|------|---------------------|
| **Canon** | `AF Points In Focus`, `AF Area X/Y Positions`, `AF Area Width/Height` |
| **Nikon** | `AF Info 2 Version`, `AF Points Used/Selected/In Focus`, `AF Area X/Y Position` |
| **Sony** | `Focus Location`, `Focus Frame Size`, `Focal Plane AF Point Location` |
| **Fujifilm** | `Focus Pixel`, `AF Area Point Size` |
| **Pentax** | `AF Points Selected/In Focus`, `CAF Points Selected` |
| **Olympus** | `AF Point Details`, `AF Point Selected`, `Subject Detect Frame Size` |

### 2.3 焦点提取算法 - Canon 示例

```lua
function CanonDelegates.getAfPoints(photo, metaData)
  -- 1. 读取AF区域参考尺寸
  local imageWidth = ExifUtils.findValue(metaData, "AF Image Width")
  local imageHeight = ExifUtils.findValue(metaData, "AF Image Height")

  -- 2. 读取焦点相对位置（相对于图像中心）
  local afAreaXPositions = split(ExifUtils.findValue(metaData, "AF Area X Positions"), " ")
  local afAreaYPositions = split(ExifUtils.findValue(metaData, "AF Area Y Positions"), " ")

  -- 3. 转换为绝对坐标
  for key, _ in pairs(afAreaXPositions) do
    -- Canon 焦点坐标以图像中心为原点
    local x = (imageWidth/2 + afAreaXPositions[key]) * xScale
    local y = (imageHeight/2 + (afAreaYPositions[key] * yDirection)) * yScale

    -- 注意：紧凑相机的Y轴方向反向
    local yDirection = (cameraType == "compact") and 1 or -1
  end
end
```

### 2.4 焦点提取算法 - Nikon 示例

Nikon 支持两种对焦系统，按优先级处理：

```lua
function NikonDelegates.getAfPoints(_photo, metaData)
  -- 1. 优先尝试对比度AF（用于Z系列无反相机和实时取景）
  local result = NikonDelegates.getCAfPoints(metaData)

  if not result then
    -- 2. 降级到相位检测AF（用于传统DSLR光学取景器）
    result = NikonDelegates.getPDAfPoints(metaData)
  end
  return result
end

-- CAF处理：直接从EXIF读取焦点框坐标
function NikonDelegates.getCAfPoints(metaData)
  local x = ExifUtils.findValue(metaData, "AF Area X Position")
  local y = ExifUtils.findValue(metaData, "AF Area Y Position")
  local w = ExifUtils.findValue(metaData, "AF Area Width")
  local h = ExifUtils.findValue(metaData, "AF Area Height")
  return { points = {{ x=x, y=y, width=w, height=h }} }
end

-- PDAF处理：使用焦点名称查表获取坐标
function NikonDelegates.getPDAfPoints(metaData)
  -- 加载焦点映射表（如 "nikon d5.txt"）
  local focusPointsMap = PointsUtils.readIntoTable("nikon corporation", "nikon d5.txt")

  -- 读取EXIF中的焦点名称
  local afPointsUsed = ExifUtils.findValue(metaData, "AF Points Used")  -- 如 "C6, D5"

  -- 查表获取坐标
  for _, pointName in pairs(split(afPointsUsed, ",")) do
    local coords = focusPointsMap[pointName]  -- {x, y, width, height}
  end
end
```

**焦点映射表格式** (`focus_points/nikon corporation/nikon d5.txt`):
```
-- Nikon D5, FX, 5568 × 3712, AF-System: 55 points
A1 = {1237, 1326, 107, 92}
C1 = {1237, 1591, 107, 92}
E1 = {1237, 1856, 107, 92}
-- ... 55个焦点定义
```

### 2.5 焦点提取算法 - Sony 示例

Sony 需要处理非原生宽高比的坐标偏移：

```lua
function SonyDelegates.getAfPoints(photo, metaData)
  -- 1. 读取焦点位置（格式: "imageWidth imageHeight pointX pointY"）
  local focusPoint = ExifUtils.findValue(metaData, "Focus Location")
  local values = split(focusPoint, " ")
  local fpW, fpH, fpX, fpY = values[1], values[2], values[3], values[4]

  -- 2. 关键补偿：处理非原生宽高比（如16:9裁剪）导致的坐标偏移
  local orgWidth, orgHeight = DefaultPointRenderer.getNormalizedDimensions(photo)
  local x = fpX + (orgWidth - fpW) / 2
  local y = fpY + (orgHeight - fpH) / 2

  -- 3. 如果有PDAF数据，需要额外的缩放转换
  local pdafWidth, pdafHeight = ...  -- 从 "Focal Plane AF Point Area" 获取
  local xScale = exifImageWidth / pdafWidth
  local yScale = exifImageHeight / pdafHeight
end
```

---

## 三、坐标变换系统

### 3.1 变换流程

焦点坐标从原始相机坐标到显示坐标需要经过 4 步变换：

```
原始相机坐标 (EXIF)
       ↓
  ① 裁剪变换 (用户在Lightroom中的裁剪)
       ↓
  ② 显示缩放 (适应窗口大小)
       ↓
  ③ 用户旋转 (90°/-90°/180°)
       ↓
  ④ 用户镜像 (水平翻转)
       ↓
最终显示坐标
```

### 3.2 仿射变换矩阵 (`affine.lua`)

使用 3x3 仿射矩阵进行坐标变换：

```lua
-- 基本变换操作
affine.trans(dx, dy)   -- 平移: [1 0 dx; 0 1 dy; 0 0 1]
affine.rotate(theta)   -- 旋转: [cos -sin 0; sin cos 0; 0 0 1]
affine.scale(sx, sy)   -- 缩放: [sx 0 0; 0 sy 0; 0 0 1]

-- 矩阵乘法支持链式操作
resultingTransformation = userMirroringTransformation *
  (userRotationTransformation *
    (displayScalingTransformation * cropTransformation))
```

### 3.3 核心变换代码 (`DefaultPointRenderer.lua`)

```lua
function DefaultPointRenderer.prepareRendering(photo, displayWidth, displayHeight)
  -- 获取原始尺寸和裁剪信息
  local originalWidth, originalHeight, cropWidth, cropHeight =
    DefaultPointRenderer.getNormalizedDimensions(photo)
  local developSettings = photo:getDevelopSettings()
  local cropRotation = developSettings["CropAngle"]
  local cropLeft = developSettings["CropLeft"]
  local cropTop = developSettings["CropTop"]

  -- ① 裁剪变换
  local cropTransformation = a.rotate(math.rad(-cropRotation)) *
    a.trans(-cropLeft * originalWidth, -cropTop * originalHeight)

  -- ② 显示缩放变换
  local displayScalingTransformation =
    a.scale(displayWidth / cropWidth, displayHeight / cropHeight)

  -- ③ 用户旋转变换 (处理90°/-90°/180°)
  local userRotation = DefaultPointRenderer.getUserRotationAndMirroring(photo)
  if userRotation == 90 then
    userRotationTransformation = a.trans(0, displayHeight) * a.rotate(math.rad(-90))
  elseif userRotation == -90 then
    userRotationTransformation = a.trans(displayWidth, 0) * a.rotate(math.rad(90))
  end

  -- ④ 镜像变换
  if userMirroring == -1 then
    userMirroringTransformation = a.trans(displayWidth, 0) * a.scale(-1, 1)
  end

  -- 组合所有变换
  return userMirroringTransformation * userRotationTransformation *
         displayScalingTransformation * cropTransformation
end
```

### 3.4 焦点框四角坐标计算

```lua
-- 应用变换到焦点中心
local x, y = resultingTransformation(point.x, point.y)

-- 计算焦点框的四个角点
local tlX, tlY = resultingTransformation(point.x - w/2, point.y - h/2)  -- 左上
local trX, trY = resultingTransformation(point.x + w/2, point.y - h/2)  -- 右上
local brX, brY = resultingTransformation(point.x + w/2, point.y + h/2)  -- 右下
local blX, blY = resultingTransformation(point.x - w/2, point.y + h/2)  -- 左下
```

---

## 四、焦点可视化系统

### 4.1 焦点点类型

| 类型 | 常量名 | 颜色 | 用途 |
|------|--------|------|------|
| `af_focus_pixel` | POINTTYPE_AF_FOCUS_PIXEL | 用户设置 | 单个焦点像素 |
| `af_focus_box` | POINTTYPE_AF_FOCUS_BOX | 用户设置 | EXIF定义的焦点区域 |
| `af_focus_box_dot` | POINTTYPE_AF_FOCUS_BOX_DOT | 用户设置 | 带中心点的焦点框 |
| `af_selected` | POINTTYPE_AF_SELECTED | 白色 | 选中但未对焦 |
| `af_inactive` | POINTTYPE_AF_INACTIVE | 灰色 | 非活跃焦点 |
| `face` | POINTTYPE_FACE | 黄色 | 面部检测 |
| `crop` | POINTTYPE_CROP | 黑色 | 裁剪区域 |

### 4.2 图标系统

```
assets/imgs/
├── corner/{color}/                 # 角标图标
│   ├── normal_fat_0.png           # 0°旋转
│   ├── normal_fat_5.png           # 5°旋转
│   ├── ...                        # (0-360°，步长5°)
│   ├── small_fat_0.png            # 小版本
│   └── ...
└── center/{color}/                 # 中心点图标
```

### 4.3 平台差异实现

**macOS**：使用 Lightroom 原生叠加视图
```lua
local imageView = viewFactory:catalog_photo { photo = photo }
local overlayViews = DefaultPointRenderer.createOverlayViews(fpTable)
photoView = viewFactory:view {
  imageView, overlayViews,
  place = 'overlapping',  -- 关键：重叠显示
}
```

**Windows**：使用 ImageMagick 直接在图像上绘制
```lua
local fileName = MogrifyUtils.createDiskImage(photo)
MogrifyUtils.drawFocusPoints(photo, fpTable)
photoView = viewFactory:picture { value = fileName }
```

---

## 五、数据流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                          用户点击 "Show Focus Point"              │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  FocusPoint.lua         │
                    │  catalog:getTargetPhoto()│
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────▼────────┐   ┌─────────▼─────────┐   ┌────────▼────────┐
│  ExifUtils.lua  │   │PointsRenderer     │   │  FocusInfo.lua  │
│  读取EXIF元数据  │   │    Factory        │   │  构建信息面板    │
└────────┬────────┘   └─────────┬─────────┘   └─────────────────┘
         │                      │
         │           ┌──────────▼──────────┐
         │           │ 识别相机品牌/型号    │
         │           │ 选择对应Delegates   │
         │           └──────────┬──────────┘
         │                      │
         │    ┌─────────────────┼─────────────────┐
         │    │                 │                 │
         │ ┌──▼───┐  ┌─────────▼───────┐  ┌─────▼─────┐
         │ │Canon │  │     Nikon       │  │  Sony/... │
         │ │Deleg.│  │(CAF优先→PDAF)   │  │           │
         │ └──┬───┘  └─────────┬───────┘  └─────┬─────┘
         │    │                │                │
         │    └────────────────┼────────────────┘
         │                     │
         │          ┌──────────▼──────────┐
         └─────────►│ 归一化焦点数据结构   │
                    │ {type, x, y, w, h}  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ DefaultPointRenderer │
                    │ 坐标变换 (affine.lua)│
                    │ 裁剪→缩放→旋转→镜像  │
                    └──────────┬──────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
    ┌────▼────┐         ┌──────▼──────┐       ┌─────▼─────┐
    │ macOS   │         │  Windows    │       │ 计算四角   │
    │ 叠加视图 │         │ ImageMagick │       │ 坐标+旋转  │
    └────┬────┘         └──────┬──────┘       └───────────┘
         │                     │
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │  LrDialogs 显示     │
         │  模态对话框         │
         └─────────────────────┘
```

---

## 六、关键算法总结

### 6.1 焦点数据读取

1. **数据来源**：主要从 EXIF MakerNotes，不同品牌标签完全不同
2. **读取方式**：通过 ExifTool 命令行工具提取
3. **品牌差异**：
   - Canon：相对于图像中心的坐标
   - Nikon：CAF 优先，PDAF 使用焦点名称查表
   - Sony：需要处理非原生宽高比偏移
   - 其他品牌：各有特殊处理逻辑

### 6.2 坐标变换

1. **变换链**：裁剪 → 缩放 → 旋转 → 镜像
2. **数学基础**：3x3 仿射变换矩阵
3. **关键处理**：
   - 用户裁剪角度的反向旋转
   - 90°旋转时交换宽高
   - 镜像时X坐标反向

### 6.3 可视化渲染

1. **跨平台**：macOS 使用原生视图叠加，Windows 使用 ImageMagick
2. **图标系统**：预生成 0-360° 旋转图标，5° 步长
3. **自适应缩放**：焦点框小于阈值时切换小图标

---

## 七、项目亮点

1. **模块化架构**：工厂模式 + 策略模式，易于扩展新相机支持
2. **跨平台兼容**：针对 macOS 和 Windows 采用不同的渲染方案
3. **精确坐标变换**：完整的仿射变换系统处理所有图像操作
4. **完善的相机支持**：9 大品牌，数十种机型的焦点数据提取
5. **外部工具集成**：ExifTool 提供强大的元数据解析能力
6. **用户体验**：详细的状态反馈、键盘快捷键、偏好设置

---

这份报告涵盖了 Focus-Points 项目的完整实现思路和核心算法，希望对您的研究有所帮助！
