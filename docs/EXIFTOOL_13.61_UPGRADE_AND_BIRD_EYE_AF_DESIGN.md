# SuperPicky: ExifTool 13.61 升级与相机 AI 鸟眼对焦融合设计文档

> **文档目标**：指导后续工程师在 SuperPicky 中完成 ExifTool 升级，并利用 ExifTool 13.60/13.61 新增的机内 AI 主体追踪元数据，升级打鸟场景下的“鸟眼合焦”判定与评分体系。

---

## 1. 背景与现状

### 1.1 SuperPicky 现状
* **当前内置 ExifTool 版本**：`13.55`（官方生产稳定版 Production Release，位于 `SuperPicky/exiftools_mac` 和 `SuperPicky/exiftools_win`）。
* **架构特性**：SuperPicky 采用读写分离的双常驻 `-stay_open` 进程，通过标准输入输出管道收发指令，并配合 `-j` (JSON) 进行快速元数据读取。
* **现有鸟眼/合焦判定**：依赖自研/内置视觉 AI 模型（如 YOLO 鸟类/鸟眼检测）识别目标，并与相机基础对焦点坐标进行重合度校验。

### 1.2 上游 ExifTool 版本现状（截至 2026 年 11 月）
* **稳定版 (Production)**：`13.55`
* **最新开发版 (Development)**：`13.61`（2026-11-05 发布，紧随 `13.60` 2026-10-28 发布）
* **同类竞品参考**：Lightroom 对焦可视化插件 `Focus-Points` 最新版 (v3.4.5) 已激进内置了开发版 `13.60`，主要为了支持 Ricoh GR IV、Pentax KF 以及 Nikon Z6 III 固件 2.0。

---

## 2. 为什么选择 13.61 而不是 13.60？

经过源码级对比，13.61 修复了 13.60 中存在的两个对 SuperPicky 来说非常严重的隐患：

1. **索尼 ARW 写入损坏修复（`github #465`，极其关键）**：
   * 13.60 及更早版本在修改 ARW 元数据时，因按 TagID 排序导致 `SubIFD` 排在 `ExifIFD` 前面，颠倒了索尼原有结构，导致**索尼官方 Imaging Edge 软件报错拒绝打开被修改的 ARW**。
   * 13.61 专门在 `WriteExif.pl` 中加入重排补丁，强制索尼文件 `ExifIFD` 优先，彻底解决兼容性问题。
2. **`-j` JSON 解析大二进制标签性能修复（`github #496`）**：
   * 修复了包含大块 MakerNotes / 预览图数据的 RAW 文件在 `-j` 批量输出时的性能劣化与卡顿，直接利好 SuperPicky 的常驻解析进程。
3. **佳能追焦历史补全（`github #507`）**：
   * 13.60 仅解码单帧快照，13.61 进一步解码了快门前 200ms 的环形追焦历史缓冲区（`AFTrackingHistory`）。

---

## 3. 各大相机品牌 AI 识鸟与鸟眼 EXIF 解码全景

在设计算法前，必须明确各相机在 EXIF 中暴露信息的天然差异：

| 品牌 | 机型代表 | 识鸟标记 | 鸟眼/部位标记 | 关键 EXIF 字段 | 说明 |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **富士 (Fujifilm)** | X-H2S, X-T5 等 | ✅ 精准 | ✅ 极精准区分左右眼 | `FaceElementTypes`<br>`FaceElementPositions` | `19`=鸟身, `20`=鸟头, `21`=左眼, `22`=右眼。直接带原图坐标。 |
| **奥之心 (OM System)** | OM-1, OM-1 II 等 | ✅ 精准 | ✅ 区分鸟头/鸟眼 | `AISubjectTrackingMode`<br>`SubjectDetectStatus`<br>`SubjectDetectDetail` | `0x401`=找到鸟；状态 `257`=L1(头), `258`=L2(眼)；带详细坐标框。 |
| **佳能 (Canon)**<br>*(需 13.60+)* | EOS R1, R5 II, R6 III, R8 II 等 | ⚠️ 混标 | ✅ 区分眼/头/身 | `AFDetectedSubject`<br>`AFTrackingTarget`<br>`AFDetectedParts`<br>`AFTrackingArea` | `AFDetectedSubject` 中鸟类常与猫狗混标在 `6/7/8`；但 `AFTrackingTarget` 精准给出 `Eye` / `Head` / `Body` 及跟踪坐标。 |
| **尼康 (Nikon)** | Z9, Z8, Z6 III, Zf 等 | ⚠️ 仅菜单 | ❌ 无部位标签 | `SubjectDetection`<br>`AFCoordinatesAvailable`<br>`AFAreaX/Y/Width/Height`<br>`FocusResult` | `SubjectDetection="Birds"` 仅代表菜单设置。需在自动区域/3D追踪下依据坐标非零和 `FocusResult="Focus"` 推断锁定。 |
| **索尼 (Sony)** | A1, A9 III, A7R5 等 | ❌ 未解密 | ❌ 无部位标签 | `FocusLocation`<br>`FocusFrameSize`<br>`FocusStatus` | 索尼 AI 主体识别仍在私有加密块中。仅能依靠 `FocusLocation` 配合 `FocusFrameSize` 变小（眼部框）做物理推断。 |

---

## 4. 两阶段开发执行规划

建议将后续工程拆分为独立的两个阶段：

```mermaid
flowchart TD
    A[阶段一：基础设施无损升级] --> B[下载 ExifTool 13.61 分发包]
    B --> C[替换 exiftools_mac 与 exiftools_win]
    C --> D[更新 VERSION.json 并执行回归测试]
    D --> E[阶段一上线：无损获得稳定性与新机型支持]
    
    E --> F[阶段二：AI 鸟眼对焦融合引擎开发]
    F --> G[1. 抽象多品牌 CameraAFMetadata 解析器]
    F --> H[2. 设计 EXIF 与 YOLO 视觉多源融合仲裁算法]
    F --> I[3. 接入 SuperPicky 评分与锐度加权体系]
    I --> J[阶段二上线：高精度智能挑鸟]
```

---

## 5. 阶段一：ExifTool 13.61 基础升级（无损替换）

### 5.1 目标
不动任何业务代码，仅将底层工具链无缝替换至 `13.61`，享受防挂死、索尼 ARW 写入安全、JSON 性能提升及新机型（如 Z6 III 2.0 等）解码支持。

### 5.2 实施步骤
1. **获取官方资源包**：
   * Unix/Perl: `https://sourceforge.net/projects/exiftool/files/Image-ExifTool-13.61.tar.gz/download`
   * Windows 64-bit: `https://sourceforge.net/projects/exiftool/files/exiftool-13.61_64.zip/download`
2. **资源替换路径**：
   * macOS: 解压 `Image-ExifTool-13.61.tar.gz`，将 `exiftool` 脚本和 `lib/` 目录同步覆盖至 `SuperPicky/exiftools_mac/`。
   * Windows: 解压 `exiftool-13.61_64.zip`，将 `exiftool.exe` 和 `exiftool_files/` 覆盖至 `SuperPicky/exiftools_win/`。
3. **版本元数据更新**：
   * 修改 `SuperPicky/exiftools/VERSION.json`，将版本号更新为 `13.61`，记录发布日期和 sha256 摘要。
4. **验证与回归测试**：
   * 运行现有的回归测试集：
     ```bash
     pytest SuperPicky/tests/test_exiftool_write_mode_none.py
     pytest SuperPicky/tests/test_exiftool_relative_path.py
     pytest SuperPicky/tests/test_exiftool_set_metadata.py
     ```
   * 验证 `SuperPicky/tools/exiftool_manager.py` 的常驻进程启动输出版本是否正确。

---

## 6. 阶段二：AI 鸟眼对焦融合引擎开发指南

### 6.1 数据结构抽象：统一相机 AF 表现层
新建或在现有元数据模块中扩展统一的数据契约（DTO）：

```python
from dataclasses import dataclass
from typing import Optional, Tuple, List
from enum import Enum

class AITargetLevel(Enum):
    NONE = "none"
    BODY = "body"
    HEAD = "head"
    EYE = "eye"
    UNKNOWN = "unknown"

@dataclass
class NormalizedAFInfo:
    camera_brand: str
    has_subject_lock: bool           # 相机机内是否判定锁定主体
    detected_species_hint: Optional[str] # 如 "bird", "animal", "unknown"
    target_level: AITargetLevel      # 锁定部位级别
    eye_used: Optional[str]          # "left", "right", "unresolved", None
    box_normalized: Optional[Tuple[float, float, float, float]] # (x, y, w, h) 归一化到 0~1
    raw_focus_confirmed: bool        # FocusResult 或 FocusStatus 是否合焦
```

### 6.2 各品牌解析器实现规范

1. **佳能（Canon，机型 R1/R5m2/R6m3 等）**：
   * 读取标签：`AFDetectedSubject`, `AFTrackingTarget`, `AFDetectedParts`, `AFTrackingArea`, `AFEyeUsed`。
   * 规则：
     * 若 `AFDetectedSubject` in `['Animal (cat)', 'Animal (dog or bird)', 'Animal (horse)']`，标记 `has_subject_lock = True`, `hint = "animal/bird"`。
     * 若 `AFTrackingTarget` == `'Eye'`，`target_level = AITargetLevel.EYE`。
     * 将 `AFTrackingArea` 像素值换算为归一化比例坐标。
2. **富士（Fujifilm）**：
   * 读取标签：`FaceElementTypes`, `FaceElementPositions`。
   * 规则：
     * 若包含 `21 (Bird Left Eye)` 或 `22 (Bird Right Eye)`，直接确定 `target_level = AITargetLevel.EYE`，坐标精准提取。
     * 若包含 `20 (Bird Head)` ➔ `HEAD`；`19 (Bird Body)` ➔ `BODY`。
3. **奥之心（Olympus / OM System）**：
   * 读取标签：`AISubjectTrackingMode`, `SubjectDetectStatus`, `SubjectDetectDetail`, `SubjectDetectArea`。
   * 规则：
     * `AISubjectTrackingMode == 'Birds; Object Found'` 且状态包含 `L2 Detail` ➔ `target_level = AITargetLevel.EYE`。
4. **索尼 / 尼康降级适配**：
   * 尼康：若 `AFAreaXPosition != 0` 且 `FocusResult == "Focus"` 且菜单开了 `Birds` ➔ 标记 `has_subject_lock = True`，`target_level = AITargetLevel.UNKNOWN`。
   * 索尼：通过 `FocusLocation` 提取对焦点，若 `FocusFrameSize` 明显小于常规单点框，推测为细分跟踪框。

### 6.3 视觉 AI 与相机元数据的“双源仲裁矩阵”

SuperPicky 已有 YOLO 鸟类/鸟眼检测算法，切忌盲目相信任何单一方面。推荐采用以下仲裁矩阵进行评分赋权：

| 场景 | 相机机内 EXIF | SuperPicky 视觉 AI (YOLO) | 像素锐度检验 | 决策结论 | 评分影响 |
| :---: | :---: | :---: | :---: | :--- | :--- |
| **黄金命中** | `EYE` (鸟眼) | 检出鸟眼，且 IoU 重合度 > 0.5 | 鸟眼区域高锐度 | **极品对焦** | 给予最高锐度权重加分，打上“鸟眼精准合焦”标签。 |
| **相机假合焦** | `EYE` (鸟眼) | 判定对焦框落在树枝/背景 | 鸟眼区域低锐度 | **误报/脱焦** | 报警扣分，判定相机拉风箱或追焦脱靶，不盲信相机。 |
| **头部补偿** | `HEAD` (鸟头) | 检出鸟眼，与头框有交集 | 鸟头/眼区域锐度高 | **优质对焦** | 次高权重，视景深深度决定是否给予星级提升。 |
| **无机内支持**<br>*(索尼/旧机)*| 无眼部标签 | 检出鸟眼，与主对焦点重叠 | 鸟眼区域高锐度 | **视觉仲裁** | 自动平滑退回到现有的纯视觉重合判定逻辑。 |

---

## 7. 交付产物与验收标准

1. **阶段一产物**：
   * `exiftools_mac/` 和 `exiftools_win/` 升级到 13.61。
   * 运行测试用例全部通过，无崩溃或挂死现象。
   * 索尼 ARW 写元数据后可在官方 Imaging Edge 中正常加载。
2. **阶段二产物**：
   * 新增/完善多品牌机内 AF 元数据解析器。
   * 单元测试：针对佳能、富士、奥之心样本照片能准确输出 `NormalizedAFInfo`。
   * 评分联动：照片评分详情弹窗或元数据面板中能清晰展示机内对焦目标状态（如 `[机内识别] 鸟眼锁定 (Eye-AF Confirmed)`）。

---

## 8. 附录：核心第三方依赖组件最新动态与演进建议（2026 年下半年）

为方便后续对 SuperPicky 整体技术栈的演进与调优评估，特对工程主要依赖进行最新版本现状与重大突破分析：

| 依赖库 | 当前 SuperPicky 使用版本 | PyPI 最新版本 (2026-11) | 重大突破 / 核心变动 | 对 SuperPicky 的建议 |
| :--- | :--- | :--- | :--- | :--- |
| **`ultralytics`** | `8.4.147` *(锁版本)* | `8.4.207` | **1. CPU 推理大幅提速 (8.4.193)**：ONNX Runtime 与 OpenVINO 在 CPU 端预测提速高达 2x；<br>**2. YOLO-World 体积骤降 (8.4.176)**：瘦身 328MB → 26MB；<br>**3. 推进 YOLO26 架构体系**。 | **谨慎评估升级**：之前版本曾因全局 monkey patch `Image.open` 导致 NEF 打开弹窗 Bug，若想吸收 2x CPU 推理红利，必须在独立分支中先做完整回归测试。 |
| **`rawpy` / `LibRaw`** | `>=0.18.0` | `0.28.0` | **1. 升级底层 LibRaw 0.22.2/0.22.3**：修复佳能 CR3 零宽度异常、富士 X-Trans 绿色插值内存越界、松下编码切片防崩溃；<br>**2. 原生暴露结构体**：Python 层开放 `libraw_lensinfo_t` 镜头数据；<br>**3. Windows ARM64 原生轮子**。 | **推荐升级**：纯粹的解码底层稳定性提升与防崩补丁，API 向下兼容良好。 |
| **`pillow-heif`** | `>=0.10.0` | `1.8.1` | **完美适配 10-bit HEIF (HIF)**：修复了索尼与佳能新机型拍摄的 HLG/Rec.2020 色彩空间解析偏灰/失真的兼容性 Bug。 | **推荐升级**：有利于提升微单直出 HIF 照片在预览窗与缩略图渲染时的色彩准确度。 |
| **`PySide6`** | `>=6.6.0` | `6.12.0` | **1. 高分屏 (HiDPI) 渲染修复**：消除 macOS Retina 与 Windows 缩放下的 1px 绘制抖动与虚化；<br>**2. 纹理异步载入性能优化**：多图翻页更平滑。 | **打包环境建议跟进**：对 SuperPicky 画面对焦框精细贴合与 100% 裁切画质呈现有正向收益。 |
| **`torch` / `torchvision`** | `2.8.0` (macOS MPS) | `2.15.0` | **1. Apple Silicon Metal (MPS) 算子库补全**；<br>**2. MPS 显存分配器优化**：降低大批量挑图爆统一内存风险。 | **保持观望**：目前 2.8.0 运行稳定，升级需重新评估打包后体积，暂无紧迫性。 |

---

## 9. 审阅意见（Claude，2026-11-08）

> 以下为对本文档的事实核对与代码对照结果，供原作者复核并提出意见。核对来源：ExifTool 官方更新日志 `https://exiftool.org/history.html`、ExifTool GitHub PR #400/#464/#465/#488/#490/#496/#505/#506/#507、PyPI（`pip index versions`）、本仓库代码（dev `56007975`）、RC3 打包日志（GitHub Actions run 42364959134）。

### 9.1 事实核对

| 文档说法 | 核对结果 |
| :--- | :--- |
| 13.55 为正式版，13.60/13.61 为开发版 | ✅ 正确。13.55 = 2026-04-07 production release；13.60 = 2026-10-28、13.61 = 2026-11-05，均为开发版。13.50 也是 production。 |
| 13.61 修复索尼 ARW 写入后 Imaging Edge 打不开（#465） | ✅ 正确。PR #465「Write ExifIFD before SubIFD for Sony RAW images」，作者在 ILCE-1/7M4/7CR/6400 上验证。适用范围见 9.3。 |
| 13.61 修复 `-j` 读大二进制标签性能（#496） | ✅ 正确。但 SuperPicky 只按名读取少量指定标签并配合 `-n`，不读大块二进制标签，预计收益很小。 |
| 佳能 `AFDetectedSubject` / `AFTrackingTarget` / `AFDetectedParts` / `AFTrackingArea` / `AFEyeUsed` 需 13.60+ | ✅ 来自 PR #488（R5 Mark II 等），随 13.60 发布。 |
| 13.61 解码佳能「快门前 200ms」追焦历史 | ⚠️ 需修正。PR #507 新增 `AFTrackingHistory`（11 款 DIGIC X 机身），原文为环形记录约 **0.2–0.5 秒**、约 60Hz 采样，并非固定 200ms。 |
| 富士 `FaceElementTypes` 鸟眼、奥之心 `SubjectDetect*` / `AISubjectTrackingMode` 依赖 13.6x | ❌ 13.5x–13.61 更新日志中均未出现这些字段；可能在更早版本已支持，需用样片实测确认，**不应作为升级 13.61 的理由**。表格中富士 19–22、奥之心 257/258 等取值也需样片验证。 |
| 尼康 Z6III 固件 2.0 支持 | ✅ 13.60 更新日志「Update for Nikon Z6iii firmware 2.0」。 |
| §5.2 路径 `SuperPicky/exiftools/VERSION.json`、`SuperPicky/tests/...` | ⚠️ 仓库内路径无 `SuperPicky/` 前缀：`exiftools/VERSION.json`、`tests/test_exiftool_*.py`（三个测试文件均存在）。 |

### 9.2 文档未覆盖的现有实现

1. **对焦点读取与仲裁已存在。** `core/focus_point_detector.py` 已支持尼康、索尼、佳能、奥之心、富士、松下 6 个品牌的对焦点坐标读取（经常驻 ExifTool 进程，`-n`）；issue #107 已实现「对焦点是否落在鸟框内」校验与锐度/美学权重仲裁（`verify_focus_in_bbox` / `arbitrate_focus_weights`，在 `core/photo_processor.py` 中调用）。
   - 建议：阶段二**不要**新建平行的 `NormalizedAFInfo` 体系与仲裁矩阵，而是在现有各品牌 `_detect_*` 中增加读取「机内识别部位（眼/头/身）」，作为现有仲裁的一个新输入。
2. **ExifTool 同步策略是「只跟正式版」。** `scripts/sync_exiftool.py` + `.github/workflows/sync-exiftool.yml`（每周一自动运行）只同步 production release 并开 PR。换用开发版 13.61 等于改变这条既定策略。
3. **鸟眼位置来自关键点模型**（非 YOLO 检测鸟眼）。§1.1「YOLO 鸟类/鸟眼检测」表述不准确：YOLO 只检测鸟，眼/喙由关键点模型给出。

### 9.3 索尼 ARW 的实际影响（对 #465 的补充）

- SuperPicky 对所有专有 RAW（NEF/CR3/ARW/ORF/RAF…，DNG 除外）**规则一致、不区分品牌**：默认只写 XMP 侧车，不改 RAW 本体（`tools/exiftool_manager.py` `_is_sidecar_raw`；`advanced_config.get_arw_write_mode_for_file`）。
- 用户在「元数据写入方式 → 写入文件」下勾选子选项「也写入 RAW 本体」（`raw_embed_metadata`，默认关）后，走带校验的写法：复制 → 写副本 → 比对关键结构标签（`_read_arw_structure`：PreviewImageStart、ThumbnailOffset、JpgFromRawStart、StripOffsets、HiddenDataOffset、SR2SubIFDOffset、FileSize）→ 一致才替换，否则回退侧车。
- 因此 #465 **对默认设置无影响**；但对勾选了「写入 RAW 本体」的索尼用户有影响：13.55 下 SubIFD 会写在 ExifIFD 之前，**现有结构比对是否能识别这种 IFD 顺序变化尚未验证**。若识别不了，可能用改坏的副本替换原 ARW。
- 待验证：用真实 ARW + Imaging Edge 实测「也写入 RAW 本体」。能拦住 → 维持现状等下一个正式版；拦不住 → 临时让 ARW 即使勾选也只写侧车。
- **当前决定（项目作者 2026-11-08）：暂不修改，保持现状。**

### 9.4 第三方依赖的实际情况（对 §8 的补充）

- §8 的 PyPI 最新版本号均核对无误（ultralytics 8.4.207、rawpy 0.28.0、pillow-heif 1.8.1、PySide6 6.12.0、torch 2.15.0）。各条「重大突破」的具体内容**未逐条核实**。
- 「当前使用版本」列有误导：`rawpy`、`pillow-heif`、`PySide6` 在 `requirements_base.txt` 中只写最低版本，CI 为全新安装，**打包时已自动是最新版**。RC3 打包日志实际安装：rawpy 0.28.0、pillow-heif 1.8.1、PySide6 6.12.0、Pillow 13.0.0、numpy 2.5.4、opencv-python 5.0.0.93。§8 中这三项的「推荐升级」已自动满足。
- 真正锁定版本的只有 `ultralytics==8.4.147`（曾因全局替换 `Image.open` 导致打开 NEF 弹窗）和 Mac 的 `torch==2.8.0`（影响打包体积），均维持不动。
- 本机开发环境曾落后于打包版本（rawpy 0.26.1、PySide6 6.10.2、ultralytics 8.3.239 等），已于 2026-11-08 按 `requirements_mac.txt` 与 RC3 打包版本对齐。

### 9.5 结论与建议

1. **阶段一（升级 13.61）：暂不做。** 主要收益（#465）只影响主动勾选「写入 RAW 本体」的索尼用户；#496 收益小；富士/奥之心字段与 13.6x 无关。等下一个 ExifTool 正式版由每周自动同步更新即可。仅当决定做佳能 R5 Mark II / R1 等机型的机内部位识别时，才需要 13.60+。
2. **阶段二（机内鸟眼识别融合）：方向可取，先摸底再决定。**
   - 先收集各品牌开启鸟/鸟眼识别对焦的真实 RAW 样片（佳能 R5 Mark II / R1、富士 X-H2S/X-T5、OM-1），用 `exiftool -a -G1 -s` 导出全部 AF 相关字段，确认实际可用字段与取值，再修订 §3 表格与 §6.2 规则。
   - 实施时在 `core/focus_point_detector.py` 上扩展，接入现有 `arbitrate_focus_weights`，不另起体系。
   - 收益主要在佳能/富士/奥之心用户；尼康、索尼按 §3 表格没有部位标记，收益有限。

### 9.6 请原作者重点回应

1. 富士 `FaceElementTypes` 取值 19/20/21/22、奥之心 `SubjectDetectStatus` 257/258 的出处（是否有样片或 ExifTool 标签文档依据）？
2. 若阶段二只扩展现有 `focus_point_detector`，§6.1 的 `NormalizedAFInfo` 是否还有必要？
3. §6.3 仲裁矩阵中「相机假合焦 → 报警扣分」：现有 #107 仲裁的做法是「对焦点不在鸟框内 → 降低锐度/美学权重」，二者是否需要合并成一套规则？
4. §8 中 ultralytics「CPU 推理 2x」、PySide6「HiDPI 1px 抖动修复」等说法的出处链接。

