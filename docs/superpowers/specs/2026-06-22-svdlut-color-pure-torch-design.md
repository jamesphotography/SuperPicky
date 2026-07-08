# SVDLUT 调色（纯 PyTorch 重写）设计 / SVDLUT Color (pure-torch) Design

- 日期 / Date: 2026-06-22
- 状态 / Status: **已实施 / Implemented**（P0–P4 完成，纯 torch 与官方逐位一致，CPU/MPS 全通，已进 GUI）

> **实施完成 2026-06-22**：61 测试绿。**纯 torch SVDLUT 与官方 CPU 算子真图调色平均像素差 0.000**
> （切片算子对参考 max 2.4e-7；整网 PSNR≥50）。两个关键认知：① 官方内核 `x = x_/(width-1)` 为
> **整数除法**（空间坐标恒 0、仅末列=1），必须复刻才对齐；② **MPS 不支持 grid_sample 的 border
> padding** → 坐标 clamp+zeros 等价绕过。GUI：左栏「调色」独立入口，after=降噪+调色，复用降噪的
> 对比/100%/状态/平均像素差框架。实现见 `core/enhance/nets/{svdlut_net,svdlut_slicing}.py`；
> P0 参考工具见 `scripts/svdlut_reference/`（仅本机/CI，不进打包）。
- 前置 / Depends on: 降噪+调色总设计 [[2026-06-22-auto-enhance-denoise-color-design]];
  本 spec 专攻"调色"那一步（pipeline 链路 `降噪 → 调色` 的后半段）。
- 决策 / Decisions（用户已定）: 实现路线 **A=纯 PyTorch 重写**；调色权重 **FiveK sRGB**。

## 1. 背景与动机 / Background

自动修图链路为 `降噪(SCUNet) → 调色(SVDLUT)`。降噪已实装并跑通；调色此前因官方 SVDLUT
前向依赖**编译的 CUDA-only 自定义算子** `bilateral2D_slicing_LUTTransform` 而受阻
（违反跨平台 + CPU/MPS 速度硬约束）。

经核实，该算子的官方 `kernel_code/` 同时带 **`*_cpu.cpp`(CPU) 与 `*_cuda.cu`(CUDA)** 两套实现，
但 setup 用 `CUDAExtension` 需 CUDA 工具链编译，且 MPS 无原生内核。其**数学本质**（双边网格
三线性切片 + 3D LUT 三线性插值）可用 PyTorch 的 `F.grid_sample` 等价实现，无需任何编译、全平台通用。
故采用 **A：纯 PyTorch 重写切片算子**。

权重 `svdlut.pth`（= 官方 `pretrained/fiveK_sRGB.pth`，639KB，Apache-2.0）已上传
`jamesphotography/SuperPicky-models`，下载清单已含该条目。

## 2. 目标 / 非目标 / Goals & Non-Goals

### 目标
1. 用纯 PyTorch 实现 SVDLUT 前向，**数值与官方实现一致**（容差内），CPU/MPS/CUDA 全通。
2. 加载官方 FiveK sRGB 权重（state_dict 键名须与重写模块匹配）。
3. 接入 `core/enhance/pipeline.py` 的 color 步，替换现有 stub。
4. Crop Studio 增加调色对比预览（复用降噪的 `_BeforeAfterView` / 状态 / 平均像素差 / 10% 档）。
5. 非破坏性、跨平台、打包零额外负担（不引入编译扩展）。

### 非目标
- 不做训练，仅推理。
- 不改降噪链路与既有 UI 框架（只在其上增加"调色"开关/滑块）。
- 本期不做 SwinIR 超分。

## 3. 算法（待重写）/ Algorithm

```
SVDLUT.forward(img):
  feat = backbone(img)                              # 小 CNN(coef=8) → 256 维特征
  g3d_lut, lut_w      = gen_2d_lut(feat)            # 生成 3D LUT(SVD 分解的基组合)
  lut_pw, lut_pb      = gen_2d_lut_weight_bias(feat)# LUT 的空间 weight/bias
  gbilateral, grid_w  = gen_2d_bilateral(feat)      # 生成双边网格
  grid_pw, grid_pb    = gen_2d_grid_weight_bias(feat)
  out = slicing_transform(gbilateral, img, grid_pw, grid_pb,
                          g3d_lut, lut_pw, lut_pb)   # ← 唯一需重写的自定义算子
  return relu(out)
```

- **backbone + 4 个 Gen 模块**：标准 Conv/Linear/InstanceNorm，**直接照搬上游 `models.py`**
  （纯 torch，无自定义算子），保持模块/参数命名以便加载 state_dict。
- **slicing_transform（唯一难点）**：逐像素以 (空间 x,y) + (颜色 r,g,b) 对双边网格做
  **三线性切片**得每像素仿射系数(weight/bias)，叠加 **3D LUT 三线性插值**。
  CPU 内核 `trilinear2D_slice_LUTTransform_cpu.cpp` 给出确切索引/归一化/插值公式，
  用 `F.grid_sample(mode='bilinear', align_corners=...)` 在 3D 体上等价实现。

## 4. 验证策略（A 成立的关键）/ Validation

1. **建参考基准 P0**：在本机把官方 `bilateral_slicing_LUTTransform` 编为 **CPU 扩展**
   （`CppExtension`、剔除 `.cu`、改 setup），用官方 `models.py` + FiveK sRGB 权重对若干样图
   产出参考输出 `out_ref`。若本机编译受阻，退化为"严格逐行移植 CPU 内核到 numpy"作参考。
2. **逐位对齐 P2**：纯 torch 实现对同样输入产出 `out_torch`，要求
   `max|out_torch - out_ref| ≤ 1e-3`（归一化域）且整图 PSNR ≥ 50dB。先在随机小图、再在真实样图验证。
3. 失败时定位到具体子步（grid 切片 / LUT 插值 / weight-bias 合成）逐个比对中间张量。

## 5. 软件设计 / Software Design

```
core/enhance/nets/svdlut_net.py     # 替换 stub:backbone + 4 Gen + 纯torch slicing,导出 SVDLUTNet
core/enhance/nets/svdlut_slicing.py # 纯 torch 的 bilateral 切片 + 3D LUT 变换(F.grid_sample)
core/enhance/models/svdlut.py       # 现有封装无需大改:_load_model 载权重,colorize 强度混合
```

- `core/enhance/models/svdlut.py` 已有懒加载单例 + 强度线性混合 + `weights_only=True` 加载；
  仅需确认输入归一化/通道序与重写网络一致。
- pipeline 的 color 步已接 `colorize`；net 就绪后优雅降级自动失效、调色生效。

### UI（复用降噪框架）
- 左栏新增/扩展"调色"入口；进入后用同一 `_BeforeAfterView`（竖线对比、100%、平移、状态、平均像素差）。
- 链路顺序写死 `降噪 → 调色`；预览组合方式见 §7。

## 6. 权重 / Packaging

- `svdlut.pth`（FiveK sRGB）已在 HF + 下载清单 + build fallback。net 重写后 state_dict 直接加载。
- 零编译扩展，打包不变。

## 7. 已定决策 / Resolved Decisions

1. **预览组合**：调色对比模式的 after = **降噪+调色 端到端成品**，before = 原图（所见即导出）。
   即进入"调色"对比时按当前降噪强度先降噪、再调色，与原图对比。
2. **UI 形态**：**降噪与调色分开入口**——左栏两个按钮，各自进入自己的 `_BeforeAfterView` 对比：
   - 「修图/降噪」对比：after = 仅降噪，before = 原图（现状不变）。
   - 「调色」对比：after = 降噪+调色，before = 原图。
   导出选项 `_current_enhance_opts` 须同时携带降噪与调色状态（各自 0=短路）。
3. **backbone 类型**：FiveK sRGB 对应 `backbone_type='cnn'`（state_dict 键 `backbone.model.*` 印证）；
   构造参数（lut_n_vertices/ranks 等）P1 据 state_dict 形状反推确认。

## 8. 测试 / Testing

- 单测：slicing 子算子 vs 参考（随机张量）；net 加载 FiveK 权重 missing/unexpected=0；
  pipeline color 步 gating；强度混合；设备回退。
- 集成：真权重端到端冒烟（本机 MPS/CPU，shape/类型/PSNR）。
- `py_compile` + 既有降噪测试不回归。

## 9. 风险 / Risks

1. **切片算子逆向不一致**（最大风险）：用 §4 的参考基准 + 子步比对控制；实在难收敛则退路
   B（编译 CPU 扩展打包）或 C（换纯 torch 调色模型）。
2. backbone/构造参数与权重不匹配 → 据 state_dict 形状反推（P1）。
3. 调色对野生鸟摄可能偏激 → 默认保守强度 + 可关（沿用降噪的强度/开关与 0=短路）。

## 10. 分阶段 / Phases

- **P0** 参考基准（编 CPU 扩展或逐行移植）。
- **P1** 纯 torch 重写 `svdlut_net` + `svdlut_slicing`，加载 FiveK 权重。
- **P2** 数值验证逐位对齐。
- **P3** 接线 pipeline + 调色对比 UI。
- **P4** 真权重端到端 + 文档/回归。
