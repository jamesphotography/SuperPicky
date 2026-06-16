# 鸟类识别算法逻辑（BirdID Recognition Pipeline）

> 文档版本：2026-06-16 ｜ 适用代码：v4.3.0-RC 系列
> 覆盖：YOLO 主体检测/裁剪 → OSEA 分类 → eBird 地区过滤 → 罕见度/IUCN 富集
> 关联文档：`BIRDID_OPTIMIZATION_GUIDE.md`、`MODEL_REPLICATION_REPORT.md`、`GBIF_RARITY_INDEX.md`

---

## 0. 为什么要读这篇

SuperPicky 里**有两条独立的鸟类识别入口**，它们共享同一个分类模型，但**前处理（怎么把鸟从原图里裁出来）完全不同**。历史上多次出现「选鸟模式认得出、识鸟面板认不出」的现象，根因几乎都落在前处理差异上。本文把整条链路拆清楚，并明确两条入口的差异点。

两条入口：

| 入口 | 代码路径 | 主体裁剪来源 |
|------|----------|--------------|
| **选鸟模式**（主处理流水线） | `core/photo_processor.py` → `ai_model.detect_and_draw_birds` | YOLO 检测 + **相机对焦点选框** + 矩形裁剪 |
| **识鸟面板 / CLI**（独立识别） | `ui/birdid_dock.py` / `birdid_cli.py` → `birdid/bird_identifier.identify_bird` | YOLO `detect_and_crop_bird` 方形裁剪 |

二者最终都汇入 **同一个分类函数 `predict_bird`**。

---

## 1. 总体数据流

```
原图(JPEG/RAW)
   │
   ├─[A] load_image() ── RAW: rawpy thumb/bitmap + EXIF 朝向校正(_auto_orient)
   │                      JPEG/HEIF: 直接解码
   │
   ├─[B] YOLO 主体检测 + 裁剪 ──────────────┐
   │      选鸟模式: detect_and_draw_birds   │  ← 两条入口在此分叉
   │      识鸟面板/CLI: detect_and_crop_bird │
   │                                          ▼
   │                                  鸟主体裁剪图(PIL RGB)
   │
   ├─[C] predict_bird() ── 共享核心分类
   │      1. 选 transform (DIRECT / Resize+CenterCrop)
   │      2. 模型前向 → logits[:10964]
   │      3. softmax(logits / T=0.9)
   │      4. top-k
   │      5. (可选) eBird 地区过滤
   │      6. DB 富集: 中英名/学名/IUCN/GBIF 罕见度/简介
   │
   └─→ 结果列表 [{class_id, cn_name, en_name, confidence, ...}]
```

---

## 2. 核心分类算法 `predict_bird`（共享）

代码：`birdid/bird_identifier.py::predict_bird`

这是两条入口共用的"大脑"，与训练/复刻脚本（外部 RL 库 `superpicky_RL.py`）**逐位对齐**，常量必须保持一致：

| 步骤 | 实现 | 关键常量 |
|------|------|----------|
| Transform 选择 | `OSEA_TRANSFORM_DIRECT` 若已被 YOLO 方形裁剪；否则 `OSEA_TRANSFORM`(Resize+CenterCrop) | 由 `is_yolo_cropped` 决定 |
| 模型前向 | EfficientNet 系列分类头，输出 11000 维 | — |
| **类别截断** | `output[:10964]`，丢弃尾部"幽灵类" | `NUM_CLASSES = 10964` |
| **温度锐化** | `softmax(output / T)` | `TEMPERATURE = 0.9` |
| Top-K | 无过滤取 `top_k`；有 eBird 过滤先取 100 再筛 | — |
| 最小置信度 | 无过滤 `1.0%`；有过滤 `0.3%` | — |
| 身份映射 | `class_id` → `BirdCountInfo.model_class_id`（**不可用旧 json 反查**，会因占位/重复码塌缩） | DB `get_bird_by_class_id` |

> ⚠️ 身份对齐铁律：模型输出索引 == `bird_reference.sqlite` 的 `model_class_id`。任何评测/打标都必须以此为准，旧的 `ebird_classid_mapping.json` 已废弃（占位码 ostric2、空码会 lossy 塌缩，导致部分鸟种永远 0%）。

---

## 3. YOLO 主体检测与裁剪（两条入口的分叉点）

整张图里鸟通常很小，背景（枯枝、落叶、芦苇）极易干扰分类器。**裁得准不准，直接决定识别成败**。这一步是两条入口最大的差异来源。

### 3.1 ultralytics 输入通道约定（极易踩坑）

ultralytics YOLO 接收 numpy 数组时，**约定输入为 BGR**（OpenCV 习惯）。
- ✅ 正确：`cv2.imread()` 出来的 BGR，或 `cv2.cvtColor(rgb, COLOR_RGB2BGR)`。
- ❌ 错误：`np.array(PIL_Image)` 是 **RGB**，直接喂进去通道反了，检测质量下降、小鸟/弱对比目标会被漏检。

### 3.2 选鸟模式：`detect_and_draw_birds`（`ai_model.py`）

```
preprocess_image(path)          # cv2.imread → BGR，按 max 边 resize 到 TARGET_IMAGE_SIZE=1024
model(image, device=...)        # BGR 输入 ✅
收集 class_id==14(BIRD) 的所有框
┌ bird_count == 1            → 直接选
├ bird_count > 1 且有对焦点  → 选「bbox 包含对焦点」的框；都不含则回退最高 conf
└ bird_count > 1 无对焦点    → 选最高 conf
矩形裁剪 bbox + 15% padding  → bird_crop_bgr
```

裁出的 `bird_crop_bgr` 经 BGR→RGB→PIL，作为 `preloaded_crop` 传给 `identify_bird`（跳过其内部 YOLO 复检）。

### 3.3 识鸟面板 / CLI：`detect_and_crop_bird`（`bird_identifier.py`）

```
img_array = cv2.cvtColor(np.array(PIL_RGB), RGB2BGR)   # BGR ✅（2026-06-16 修复）
model(img_array, conf=0.25, imgsz=1024)                # imgsz 对齐主处理 ✅
收集 class_id==14 的所有框
┌ len>1 且有对焦点 → 选「bbox 含对焦点」的框（focus_point 参数）
└ 否则             → 回退最高 conf
方形裁剪: 以 bbox 中心为心，边长 = max(w,h)*(1+0.15)，不足处补黑边
```

对焦点来源：`identify_bird` / CLI 在输入是 RAW 时经
`_read_focus_point_for_path()`（内部调 `core.focus_point_detector`）自动读取，
JPEG 无对焦点则为 None、回退 conf。

### 3.4 选框策略对比（2026-06-16 已对齐）

| | 选鸟模式 | 识鸟面板 / CLI |
|---|---|---|
| YOLO 输入通道 | **BGR** ✅ | **BGR** ✅（已修复） |
| 推理分辨率 | preprocess 到 1024 | **imgsz=1024** ✅（已修复，曾用默认 640 漏检小鸟） |
| 多框选择 | **对焦点优先** → 回退 conf | **对焦点优先** → 回退 conf ✅（已补，RAW 自动读对焦点） |
| 裁剪形状 | 矩形 bbox + 15% pad | 方形 + 补黑边 |

> **典型故障**：杂乱背景里 YOLO 会把"枯叶/树皮"误检成鸟，且 conf 可能高于真鸟。识鸟面板取最高 conf → 框中枯叶 → 误判为麻鳽类。选鸟模式靠相机对焦点锁定真鸟 → 正确。

---

## 4. 对焦点引导（focus-point guided selection）

代码：`core/focus_point_detector.py`

- `FocusPointDetector.detect(raw_path)` 从 RAW EXIF 读相机自动对焦点，返回归一化坐标 `FocusPointResult(x, y ∈ [0,1], af_mode, focus_result, ...)`，已做朝向/裁切校正。
- 支持 Nikon / Sony / Canon / Olympus / Fujifilm / Panasonic。
- 选框用法：归一化坐标 × 图像宽高 → 像素坐标 →「哪个 bbox 包含它」。
- `verify_focus_in_bbox()` 另用于锐度/美学加权（头部内 1.1、SEG 掩码内 0.9、bbox 内 0.8、bbox 外 0.5）。

> 对焦点是"摄影师真正想拍的主体"的最强先验。只有 RAW 带此信息；JPEG 通常没有，退回 conf 选框。

---

## 5. eBird 地区过滤（`identify_bird`）

代码：`birdid/bird_identifier.py::identify_bird` + `species_filter`（离线 `avonet.db`）

```
use_gps → extract_gps_from_exif → 反查国家代码 (reverse_geocoder)
use_ebird:
   GPS 优先 get_species_by_gps(lat,lon)
   否则 region_code/country_code → get_species_by_region_ebird
   得到 species_class_ids 白名单
predict_bird(species_class_ids=白名单)  # 只在白名单内取 top-k，置信度按白名单重归一化
回退链: 区域无结果 → 国家级 → 全球(无过滤)，并打 country_fallback / gps_fallback 标记
```

> **地区过滤是破"姊妹种歧义"的关键**。例：欧金翅雀(eurgre1,欧洲) vs 金翅雀(origre,东亚) 视觉几乎相同，纯模型 top-1/top-2 仅差 1%；带欧洲地区过滤后，东亚种被剔除，欧金翅雀升为 top-1。
>
> 注意：外部评测脚本 `compare_models` **不做地区过滤**，因此会低估这类姊妹种准确率（report 出 0% 实为假阴性）。

---

## 6. RAW 朝向处理

代码：`birdid/bird_identifier.py::_auto_orient`

- 竖拍 RAW 的 thumb/传感器数据常是横向的，不旋转会让鸟"横躺"送入 YOLO+分类器 → 漏检/低置信。
- 优先用 EXIF Orientation(tag 274)，无则回退 libraw `flip`。
- 必须在 `convert("RGB")` 之前调用（convert 会丢 EXIF）。

---

## 7. 两条入口端到端对比（速查）

| 维度 | 选鸟模式 | 识鸟面板 / CLI |
|------|----------|----------------|
| 入口 | `photo_processor` 批处理 | `birdid_dock` 拖拽 / `birdid_cli` 命令行 |
| 跑 YOLO | ✅ `detect_and_draw_birds` | ✅ `identify_bird` 内 `detect_and_crop_bird` |
| YOLO 通道 | BGR | BGR ✅ |
| 推理分辨率 | 1024 | imgsz=1024 ✅ |
| 对焦点选框 | ✅ | ✅（RAW 自动读） |
| 裁剪 | 矩形 + 15% pad | 方形 + 补黑边 |
| eBird 过滤 | ✅（按 UI 设置） | birdid2024 ✅ / OSEA-CLI ❌ |
| 分类核心 | `predict_bird`（共享） | `predict_bird`（共享） |

---

## 8. 已知历史问题与对齐要点

### 8.1 识鸟面板/CLI 与选鸟模式的检测偏差（2026-06-16 已修复）

典型案例：白痣吸蜜鸟（小鸟趴在杂乱枯枝/落叶地面，相机对焦点压在鸟上）。
选鸟模式 100% 认对，识鸟面板/CLI 却识别成麻鳽类。三个**叠加**的根因：

1. **通道顺序**：`detect_and_crop_bird` 把 `np.array(PIL_RGB)`（RGB）喂给 ultralytics（约定 BGR）。通道反转 → 杂背景小鸟**完全漏检**，只检出酷似麻鳽的枯叶。
   → 修复：`cv2.cvtColor(..., RGB2BGR)`。
2. **推理分辨率**：默认 `imgsz=640` 把高像素原图直接降采样，远距小鸟被抹掉。
   → 修复：`imgsz=1024`，对齐主处理 `TARGET_IMAGE_SIZE`。
3. **缺对焦点选框**：多目标时盲取最高 conf，会锁定 conf 比真鸟高的枯叶误检框。
   → 修复：RAW 经 `_read_focus_point_for_path` 自动读对焦点，选「含对焦点的框」，回退 conf。

实测（同一张 `_Z9W7012.NEF`）：修复前 → 麻鳽 3.8~14%；修复后 → 白痣吸蜜鸟 **99.3%**（OSEA 与 birdid2024 一致）。

涉及文件：`birdid/bird_identifier.py`（`detect_and_crop_bird` + `identify_bird` +
`_read_focus_point_for_path`）、`birdid_cli.py`（OSEA 路径）。dock 经 `identify_bird`
自动获益，无需改动。

### 8.2 长期对齐铁律

- **train/serve 对齐**（见外部 RL 库）：class14 / 整图喂 YOLO / 方形补黑边 / DIRECT transform / `[:10964]` / 温度 0.9，六处必须与生产 `predict_bird` 逐位一致。
- **身份映射**：一律用 `model_class_id`，禁用旧 `ebird_classid_mapping.json` 反查。
- **评测脚本缺地区过滤**：外部 `compare_models` 不做 eBird 过滤，会把姊妹种（如欧金翅雀）误报 0%，属假阴性，非生产链路问题。
