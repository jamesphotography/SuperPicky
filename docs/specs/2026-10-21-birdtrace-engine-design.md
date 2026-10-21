# BirdTrace 识鸟模型接入设计 / BirdTrace Bird-ID Engine Design

- 日期 / Date：2026-10-21
- 分支 / Branch：`feat/birdtrace`（自 `dev` 6458ea54 起，不含任何 Merlin 代码）
- 状态 / Status：已与用户确认（默认对比模式、镜像到自有 HF 仓库、按本方案执行）

## 1. 背景与目标 / Background and goals

OSEA（`model20240824.pth`，ResNet，10964 类）在难认地区与新拆分种上明显落后。2026-10-21 在用户人工核对的
数据上对比了 OSEA / Merlin / BirdTrace 三个模型（评估脚本与真值在仓库外 `~/Documents/JamesAPPS/birdid-eval`）：

| 数据（只看 ≥2★） | OSEA | Merlin | BirdTrace |
|---|---|---|---|
| 维州 2026-10-20（135 张真值） | 94.1% | 96.3% | 97.0% |
| 达尔文 Lee Point 2026-09-05（265 张） | 99.6% | 100% | 99.2% |
| Opalton 锈色草鹩莺（381 张） | **0%** | 99.5% | 98.7% |
| 凯恩斯分歧·非吸蜜鸟（39 张，只核对了分歧） | 23 | 6 | 32 |
| 凯恩斯分歧·吸蜜鸟（36 张） | 26 | 17 | **1** |

Merlin 模型未获分发授权，只能留在 inside-tester。BirdTrace 为 CC BY-NC 4.0，可随 SuperPicky 分发。

目标：
1. RC 阶段：BirdTrace 与 OSEA 并存，**默认对比模式**（BirdTrace 出结果，OSEA 验证写进题注），收集分歧数据。
2. 正式版：去掉 OSEA（与 Merlin），只保留 BirdTrace。
3. 识别速度不明显变慢（基准见 §6）。

## 2. 模型事实 / Model facts

- 来源：https://huggingface.co/warden123/BirdTrace-FastViT-MA36-10K ，作者 Warden（BirdTrace，birdtrace.org），
  训练数据为 iNaturalist，作者自训；许可 CC BY-NC 4.0；架构 Apple FastViT-MA36（ICCV 2023）。
- 55,605,164 参数；`model.safetensors` 222 MB（fp32，Apple ml-fastvit 原始参数命名，带 `backbone.` 前缀）。
- 输入 384×384 RGB，ImageNet mean/std 归一化；输出 10480 类原始 logits，无地点/季节先验。
- 类别只有去掉撇号、下划线连接的英文名（eBird/Clements 新分类，`Abbotts_Babbler` = Abbott's Babbler），无学名。

## 3. 架构 / Architecture

### 3.1 推理后端
- PyTorch：`timm.create_model("fastvit_ma36")` + `timm.models.fastvit.checkpoint_filter_fn`（把 Apple 命名转成
  timm 命名，strict 全部匹配）+ `timm.utils.model.reparameterize_model`（推理融合）。
- 先在 CPU 上建模型与加载权重，再搬到 `config.get_best_device()`（Apple Silicon=MPS，Windows=CUDA，其余 CPU）；
  搬设备或首推失败时退回 CPU 并打印带 `[BirdTrace]` 的日志（CLAUDE.md 模型加载约定）。
- fp32 运行。实测 fp16 运行 1/300 第一名改变、fp16 存储 6/1000 改变，均不采用。
- 不引入新依赖：`timm` 已在 `requirements_base.txt`，`safetensors` 随 timm 安装。不使用 onnxruntime。

### 3.2 类别映射
- BirdTrace 10480 类 → SuperPicky 鸟种库类别号（沿用 OSEA 的 `model_class_id` 编号空间，报告库、纠错记录、
  地区名录、罕见度全部不用改）。多对一时概率相加（如 Siberian / Tibetan Sand-Plover → 蒙古沙鸻）。
- 对照表 `birdid/data/birdtrace_class_map.json` 由 `scripts_dev/build_birdtrace_class_map.py` 生成并**提交入库**，
  运行时只读 JSON。生成脚本只用仓库内数据（鸟种库的 eBird 代码/学名/英文名、AviList 各版英文名），
  **不依赖 Merlin**。英文名比较前去掉非字母字符（BirdTrace 去掉了撇号）。
- 原 Merlin 引擎里与模型无关的 `build_class_map` / `aggregate_probs` 移到新模块 `birdid/class_mapping.py`。
- 已知缺口：约 300 类对不上（多为 eBird 新拆分种，如北鵙雀鹟），这些类的概率被丢弃；`bird_identifier` 中
  `min(10964, ...)` 等硬编码使编号 ≥10964 的新鸟种无法输出。补新鸟种是独立后续工作（§8）。

### 3.3 识鸟模型选项（`advanced_config.birdid_engine`）

| 值 | 含义 |
|---|---|
| `compare`（RC 默认） | BirdTrace 出主结果；同一裁剪图、同一地区名录再跑 OSEA，作为验证写进题注，并逐张追加对比 CSV |
| `birdtrace` | 只用 BirdTrace |
| `osea` | 只用 OSEA |

- 环境变量 `SUPERPICKY_BIRDID_ENGINE` 优先于设置（测试与评估用）。
- 无效值读作默认值 `compare`。setter 的取值范围与设置页下拉框一致（CLAUDE.md SSOT 约定）。
- 模型文件缺失时（BirdTrace 未下载）：`compare` / `birdtrace` 退回 OSEA，并打印带 `[BirdTrace]` 的日志，
  不让识鸟整体失败。

### 3.4 对比模式的通用部件（在 inside-tester 上做过 Merlin 版，这里按模型无关重写）
- `identify_bird` 返回 `engine` / `primary_engine` / `compare`（验证模型的结果）。
- 题注：主模型名 + 验证模型的第一名（`core/rating_caption.py`）。
- 报告库 `photos` 新增 `verify_model / verify_species_cn / verify_species_en / verify_confidence`（`tools/report_db.py`
  迁移，老库自动加列）。
- 结果浏览器筛选面板「只看分歧」：当前鸟种（或待确定候选）与验证模型不同才入选。
- 对比 CSV：用户数据目录 `birdid_compare.csv`。

### 3.5 署名 / Attribution
- 设置中心「关于」页：BirdTrace 识鸟模型 · 作者 Warden · 链接上述 HF 页面 · CC BY-NC 4.0；FastViT 架构署名（Apple）。
- 安装包附 `THIRD_PARTY_NOTICES.txt`（BirdTrace 原文件）。
- 官网（superpicky-site 私有仓库）的致谢/关于加同一链接，单独提交。

### 3.6 模型分发
- 镜像到 `jamesphotography/SuperPicky-models`（`birdtrace/model.safetensors`、`classes.json`、`THIRD_PARTY_NOTICES.txt`），
  在 `scripts/download_models.py` 的 `MODELS_TO_DOWNLOAD` 登记并锁定 SHA256；记录上游 commit。
- 上游 SHA256：`model.safetensors` 81d698a8457a9c40d67e6d20d18ebe24f9d164b7083717b9f18a08a99f9f0ca6，
  `classes.json` db040636f828b4d676d2cd7754ca02a56784f58fa4108309fa039dde46394daa。

## 4. 分支流转 / Branch flow

```
dev ──► feat/birdtrace ──► dev ──► nightly / master ──► RC（BirdTrace + OSEA，默认对比）
                      └──► inside-tester（Merlin 保留为单独可选模型，不参与对比模式）
正式版：dev 上去掉 OSEA，只留 BirdTrace
```

## 5. 正式版（本分支不做，记录待办）
1. 删除 OSEA 模型下载、代码与设置项。
2. 配置迁移：`advanced_config.json` 里残留的 `osea` / `compare` / `merlin` 一律读作并存回 `birdtrace`
   （避免 4.5.0 下架视频时残留配置把用户分成两拨的问题）；设置页下拉框整体移除。
3. 置信度默认门槛按 RC 收集的数据重定；对易混鸟种（吸蜜鸟、绣眼鸟等）评估更高门槛或「待确定」。

## 6. 验证 / Verification
- 单元测试：类别映射（撇号归一化、多对一相加、对不上记 -1）、模型选项与环境变量优先级、缺模型时退回、
  对比模式结果结构、题注、报告库迁移、「只看分歧」筛选。
- 全量 `python3 -m pytest`。
- 准确率回归：用新代码重跑 `eval_engines.py` 五批数据，与 2026-10-21 的结果一致（PyTorch 与 ONNX 结果已验证一致）。
- 速度回归：端到端 200 张 NEF（Lee Point 09-05，`-i --birdid-region AU-NT --no-organize`），
  基准 OSEA 40.4 s / BirdTrace 42.4 s；对比模式另测。单张推理：OSEA 9 ms、BirdTrace 22 ms（MPS，融合后）。
- CPU 模式（Intel Mac / Windows 普通版）端到端测速。
- macOS 打包后启动冒烟测试（`.spec` 有改动）。

## 7. 已知风险 / Known risks
1. Windows CUDA 安装包：RC 同时带两个模型会超过 GitHub 2 GiB 上限约 80 MB，CI 体积卡口会拦下 RC 发版。
   用户决定暂不处理，不影响开发，RC 发版前必须解决。
2. 对比模式内存约多 230 MB（两个模型同时加载）。
3. Lightroom 插件识鸟服务在对比模式下会识别两次，需确认是否只需要主结果。
4. BirdTrace 对吸蜜鸟、绣眼鸟会以高置信度认错；置信度分布与 OSEA 不同（维州数据 50–70% 段只有 67% 正确）。

## 8. 后续独立工作
- 鸟种库补 eBird 新拆分种（bird-names 主库），并去掉编号 ≥10964 的硬编码，使 BirdTrace 认得的新种能显示。
- Windows 改用 ONNX Runtime + DirectML 去掉 CUDA 包（另立项评估）。
