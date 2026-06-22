# 自动修图（裁剪后 / 导出前）设计 — 降噪 + 调色

- 日期 / Date: 2026-06-22
- 状态 / Status: 已实施（降噪可用；调色受阻）/ Implemented (denoise working; color blocked)

> **实施更新 2026-06-22**：8 任务全部落地，68 passed/2 skipped。**重大发现**：SVDLUT 官方前向依赖
> **CUDA-only 自定义算子**（`bilateral2D_slicing_LUTTransform`，见上游 `cpp_ext_interface.py` +
> `kernel_code/`），**无 CPU/MPS 实现**，违反本项目跨平台 + CPU/MPS 速度硬约束。SCUNet 为纯 PyTorch，
> 已 vendor 并验证可用（17.95M 参数）。SVDLUT 调色暂以 stub + 管线优雅降级处理，需用户在三条路径间决策
> （纯 torch 重写 / 换模型 / 仅交付降噪）。详见 `docs/superpowers/plans/` 同名计划的「实施记录」。
- 范围 / Scope: 在 Crop Studio 全屏后期工作区中，对选中裁剪框在「裁剪之后、导出之前」执行自动修图（SCUNet 盲降噪 + SVDLUT 空间感知调色）。**本期不含 SwinIR 超分**。

## 1. 背景与动机 / Background

现有 Crop Studio（`ui/crop_studio.py`）已支持手动裁剪、实时 TOPIQ 评分、导出选中裁剪到全分辨率 JPEG（`core/crop_export.py:export_crop()`）。本期在导出链路中插入一个**非破坏性自动修图管线**，提升出片质量。

经第一性原理评估，三个候选模型中：

- **SVDLUT**（WontaeaeKim/SVDLUT，ICCV 2025，**Apache-2.0**）：3D LUT 经 SVD 分解的空间感知调色，查表+插值，**毫秒级、显存极小**，分辨率无关。
- **SCUNet**（cszn/SCUNet，MIR 2023）：Swin-Conv-UNet 盲降噪，无需估噪声等级，中等算力。
- **SwinIR**（超分/数毛）：Transformer 超分，**慢且吃显存，仅对极限小裁剪放大才有真实增益**——**本期移除**，以保证 CPU/MPS 速度与简洁性，后续可作为独立 spec 增量。

## 2. 目标与非目标 / Goals & Non-Goals

### 目标

1. 在裁剪后、导出前执行 `降噪 → 调色` 固定链路。
2. 全屏后期工作区**实时预览 before/after**，满意后再导出。
3. 一键默认出片（降噪开 + 调色开，保守强度）+ 可展开微调（各自开关/强度，调色可一键取消）。
4. **CPU / MPS 上保持可用速度**为首要约束。
5. 非破坏性：绝不覆盖原图；EXIF 复制逻辑不变。

### 非目标

- 不做超分（SwinIR）。
- 不引入可调序的管线（顺序写死）。
- 不修改现有「导出尺寸 + 质量」对话框语义。

## 3. 总体架构 / Architecture

新增高内聚、低耦合模块 `core/enhance/`，纯算法层、不依赖 Qt、不碰文件 I/O：

```
core/enhance/
  __init__.py
  pipeline.py     # enhance(img_rgb, opts, device, progress_cb) -> img_rgb；编排 denoise→color
  options.py      # EnhanceOptions 数据类 + 默认预设
  models/
    scunet.py     # 盲降噪封装(懒加载单例 + tiling)
    svdlut.py     # 调色封装(整图,极轻量)
  nets/           # SCUNet / SVDLUT 网络结构源码(vendoring)
```

### 接口契约

```python
@dataclass
class EnhanceOptions:
    denoise_on: bool = True          # 降噪开关
    denoise_strength: float = 0.5    # 0=短路跳过推理；保守默认
    color_on: bool = True            # 调色默认开
    color_strength: float = 0.4      # 保守默认

def enhance(
    img_rgb: "np.ndarray",
    opts: EnhanceOptions,
    device: "torch.device",
    progress_cb: "Optional[Callable[[float], None]]" = None,
) -> "np.ndarray":
    """对 RGB ndarray 按 denoise→color 链路修图，返回新 ndarray。不做 I/O。"""
```

- `crop_export.export_crop()` 与 `crop_studio` 均复用同一 `pipeline.enhance`，逻辑只有一份。
- 每个模型封装为懒加载单例，权重经 `config.get_model_path()` 定位，设备经 `config.get_best_device()` 选择。

## 4. 固定处理链路 / Fixed Pipeline

`降噪(SCUNet) → 调色(SVDLUT)`

- 先降噪再调色：在干净像素上定影调，避免对噪声调色放大色噪。
- 用户仅能开关/调强度，**不能改顺序**。
- `denoise_strength == 0` → 短路跳过 SCUNet 推理；`color_on == False` → 跳过 SVDLUT。

## 5. 双分辨率策略 / Two-Resolution Strategy

| 阶段 | 输入图 | 线程 | 说明 |
|---|---|---|---|
| 调参预览 | 裁剪框降采样到长边 ~1280px | 后台 `_EnhanceWorker`（仿 `_ExportWorker`），滑块防抖 ~300ms | 保证 MPS/CPU 调参秒级响应 |
| 导出渲染 | 全分辨率裁剪 | 后台 worker + 进度条，可取消 | 最终成品 |

**保真度**：调色(LUT)与分辨率无关，预览与成品**完全一致**；仅 SCUNet 降噪分辨率相关，预览与全分辨率高度近似。基本做到所见即所得。

## 6. CPU / MPS 速度保证 / Performance

唯一重算力为 SCUNet（SVDLUT 可忽略）。措施：

1. 设备：`get_best_device()`（CUDA>MPS>CPU）；CUDA 半精度，MPS/CPU fp32。
2. **tiling**：大图按 256–512 瓦片 + 32 重叠拼接，界定显存与峰值耗时，瓦片粒度回报进度。
3. **预览限长边 1280px**，调参阶段保持秒级。
4. **懒加载 + 单例常驻**，避免每次重载权重。
5. 兜底：降噪强度滑块到 0 即短路跳过 SCUNet，调色照常（极快）。

## 7. UI / 交互（全屏后期工作区内）

- 工具栏 **「智能修图」** 主按钮：点亮即按默认预设出片（降噪开 + 调色开，保守强度）。
- 可折叠微调面板两段：
  - `降噪强度` 滑块（0 = 关）。
  - `调色强度` 滑块 + **「取消自动调色」** 快捷（一键关调色）。
- 画布复用现有 hand-pan / 缩放 + **before/after 对比**（按住对比键或分屏）。
- 导出沿用现有「尺寸 + 质量」对话框，**原封不动**（无超分故无尺寸冲突）；修图状态随导出请求下传，导出时全分辨率重跑。

## 8. 打包与交付 / Packaging

- 权重加入 `scripts/download_models.py` 的 `MODELS_TO_DOWNLOAD`、`build_release_win.py` / `build_release_mac.py` 模型清单、`core/initialization_manager.py` 下载列表，落到 `models/`，运行时 `config.get_model_path()` 加载。
- 增量体积：**SVDLUT 极小 + SCUNet 权重数十 MB ≈ 70–110MB**。
- 许可证：**SVDLUT = Apache-2.0 已确认无碍**；落盘前**核验 SCUNet（cszn）许可**（前置确认项）。
- 网络结构源码 **vendoring** 进 `core/enhance/nets/`（两者非 pip 包）。
- 非破坏性不变：仍输出 `*_crop.jpg`，EXIF 复制逻辑不动。

## 9. 数据流接线 / Data-Flow Wiring

1. `EnhanceOptions` 由 UI 面板状态构造。
2. 预览：`crop_studio` 在裁剪框/滑块变更（防抖）后启 `_EnhanceWorker`，对降采样裁剪调 `pipeline.enhance`，回传 before/after 显示。
3. 导出：`_on_export_clicked` → 现有尺寸/质量对话框 → `export_crop(...)` 新增可选参数 `enhance_opts`；`export_crop` 在 `img = img[y1:y2, x1:x2]` 之后、`cv2.imwrite` 之前调用 `pipeline.enhance`（全分辨率），其余流程不变。

## 10. 测试 / Testing（满足 CLAUDE.md 最低验证）

- 单测：
  - 链路顺序（denoise 先于 color）。
  - 开关 gating：`denoise_strength==0` 短路、`color_on==False` 跳过。
  - tiling 无缝拼接（瓦片结果与整图结果一致/近似）。
  - 设备回退（CUDA→MPS→CPU 选择正确）。
  - 模型逻辑用 mock 隔离。
- 集成：一个真权重小图冒烟测试（skip-marked，视体积）。
- 改动 Python 文件跑 `.venv*/bin/python -m py_compile`。
- 导出读回校验：中文 EXIF 经修图导出后不被破坏（UTF-8 安全）。

## 11. 风险 / Risks

1. SCUNet 在低端 CPU 上仍可能偏慢 → §6 tiling + 预览限尺寸 + 强度 0 短路兜底。
2. SVDLUT 调色对野生鸟摄可能偏激 → 默认保守强度 + 一键取消。
3. 包体积 +~100MB（可接受，叠加现有 CUDA 包压力需留意）。
4. 跨平台（Windows + macOS）torch 推理一致性 → 设备/精度分支需各平台冒烟。

## 12. 后续可选增量 / Future

- SwinIR 超分「数毛」作为独立 spec：按需补到目标长边、tiling、与导出尺寸对话框接入。
