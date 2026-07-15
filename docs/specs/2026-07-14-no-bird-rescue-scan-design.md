# 无鸟补救扫描（两段式检测）设计 / No-Bird Rescue Scan (Two-Stage Detection) Design

日期 / Date: 2026-07-14
状态 / Status: 已批准 / Approved
相关 / Related: 用户反馈「明明有鸟却被判无鸟」；39 张 Sony A1 漏检样本 + 1063 张全量 A/B 实测

## 1. 问题 / Problem

用户持续反馈 YOLO11「莫名其妙识别不到鸟」。用 39 张确认有鸟但被判无鸟的
ARW 样本 + 其来源目录全量 1063 张做诊断与 A/B 测试，定位到三个叠加根因：

1. **双重降分辨率**：`preprocess_image` 先把长边缩到 1024，`model(image)` 又按
   ultralytics 默认 `imgsz=640` 二次缩放。45MP 原图里的远距离小鸟到推理时只剩
   几个像素。39 张样本在生产配置下 0 检出；仅把 `imgsz` 提到 1024，27 张直接
   检出且置信度跳到 0.6~0.9。
2. **COCO 类别混淆**：高调天空下的小体积飞版，YOLO 高置信度地认成
   `airplane`（class 4）或 `kite`（class 33）——鸟没有消失，是被认错了类。
   39 张样本中 24 张存在此现象。
3. **两道置信度门槛静默丢弃**：YOLO 内部默认 `conf=0.25` 丢一轮；
   `photo_processor.py`（`rejected_by_detection`）再按 UI「AI 置信度」滑块
   （默认 0.5）拒一轮。落在 0.25~0.5 区间的真鸟被静默判 0 星低置信度。

对选片软件而言「有鸟判无鸟」的代价（好片进放弃文件夹）远大于「无鸟判有鸟」
（多看一张片），因此值得为疑难照片增加一次更贵但更准的复查。

## 2. A/B 实测依据 / Evidence

测试集：`2026年7月6日住处鸟片1`，1063 张 ARW（含全部 39 张已知漏检）。

- A 组（生产复现，640/0.25）：75 张判无鸟，全部落在 0星_放弃。
- B 组（补救：1024/conf=0.05 重扫 + kite/airplane 候选 + BirdID 分类器确认
  ≥10%）：救回 34/75；已知 39 张漏检救回 36 张（92%），剩 3 张为白天空中仅
  数像素的极端小点。
- 误报核验：未救回的 41 张经人工拼图核验绝大多数为真无鸟空镜（草地/屋檐/
  山景/墙面），补救通道未将其错误救回。
- 成本：补救仅对判无鸟照片触发（本批 7%），单张约 336ms（YOLO@1024）
  + 41ms（BirdID 确认）；1063 张全程额外约 28 秒（+0.4%）。

## 3. 方案 / Design

### 3.1 总体流程

快速路径完全不变；只在「即将拒绝」时触发一次补救扫描：

```
JPEG(长边1024) → YOLO imgsz=640, conf=0.25          ── 快速路径，现状不变
    ├─ 最佳 bird 置信度 ≥ UI阈值 → 接受（现状）
    └─ 无 bird 框，或最佳 bird 置信度 < UI阈值：
         补救扫描（rescue_scan_enabled 时）:
         同一张已解码图 → YOLO imgsz=1024, conf=0.05
             ├─ 最佳 bird 置信度 ≥ UI阈值 → 接受（rescued，天然过下游门槛）
             ├─ 否则取最佳候选框 = bird(≥0.05) 或 kite/airplane 框
             │    → BirdID 分类器裁框确认，top1 置信度 ≥ rescue_birdid_gate(默认10%)
             │    → 接受（rescued，需豁免下游置信度门槛）
             └─ 都不满足 → 维持无鸟/低置信度拒绝（现状）
```

要点：
- 补救触发条件覆盖两类漏检：**判无鸟** 与 **检出但低于 UI 置信度阈值**。
- 补救复用同一 YOLO 模型实例与同一张已解码图（在 `yolo_infer_lock` 内），
  无额外 IO/解码。
- BirdID 分类器是「守门员」：YOLO 说像风筝，分类器说是红脚鹬 81%，即判鸟。
  分类器不可用（模型缺失/加载失败）时优雅降级：仅接受 1024 重扫
  bird ≥ UI 阈值的救回，弱候选路径自动关闭。

### 3.2 代码落点

- **`ai_model.detect_and_draw_birds`**（唯一检测入口，GUI/CLI 共用）：
  内部实现 `_rescue_scan()`。触发点两处：现有「bird_idx == -1 → 返回无鸟」
  分支，以及「有 bird 但最佳置信度 < ai_confidence（ui_settings[0]/100，
  函数内已有）」时。补救成功则按救回候选框继续走原有的裁剪/画框/入库逻辑。
  返回元组由 9 元扩展为 10 元，追加 `rescued: bool`。
- **`core/photo_processor.py`**：两处解构（主路径与多鸟 focus refine 路径）
  改为接收 10 元；置信度二次门槛改为
  `not detected or (detected and not rescued and confidence < threshold)`
  ——救回照片已经过两因子核验（YOLO 候选 + 鸟种分类器），豁免该门槛，
  否则弱候选救回（conf≈0.3）会被默认 0.5 阈值全部再杀一遍，功能失效。
- **BirdID 确认调用**：`ai_model` 内经 `core.birdid_adapter.BirdIDAdapter`
  懒加载单例调用（与批处理鸟种识别共享底层懒加载模型，无重复显存）。
- **线程安全**：批处理时 BirdID executor（MPS/CUDA 下单 worker）与补救确认
  可能并发调用分类器。在 `birdid/bird_identifier.py` 增加模块级分类器推理锁，
  两条路径的模型 forward 全部串行化（现状仅靠 executor 单 worker 约束自身，
  无跨路径保护）。
- **常量**（`config.py` `AIConfig`）：`RESCUE_IMGSZ=1024`、`RESCUE_CONF=0.05`、
  `RESCUE_CONFUSABLE_CLASS_IDS={4, 33}`（airplane/kite）。

### 3.3 设置（遵循 advanced_config SSOT 约定）

- `rescue_scan_enabled: bool = True` — DEFAULT_CONFIG + property/setter；
  设置中心「精选」页新增一个开关（`checkbox_indicator_qss` 统一样式）：
  「无鸟补救扫描（更慢但更少漏检）」。
- `rescue_birdid_gate: int = 10` — 分类器确认门槛（百分比），clamp 0~100；
  暂不出 UI（JSON 可调），避免给用户过多旋钮。

### 3.4 日志与可追溯

- 救回时输出 info 日志（i18n 中英双语新键）：
  例「补救扫描: 1024px 重扫检出鸟 (conf=0.71)」/
  「补救扫描: kite 候选经识鸟确认 红脚鹬 81% → 判有鸟」。
- report_db 照片记录若能低成本携带（现有 dict 插入即可）则加 `rescued` 字段，
  否则仅日志，不做 schema 迁移。

### 3.5 明确不做 / Out of Scope

- 不全局提高 `imgsz`（正常照片零损失原则）。
- 不更换/微调检测模型（yolo26 A/B 已证无收益；专用检测器是另一工程）。
- 不做 SAHI 切片推理（成本高，收益被本方案覆盖）。
- 不改评星逻辑：救回照片正常进入关键点/锐度/评星流程，拍得太小太糊自然
  得低星——本功能只保证「它被当作鸟照片对待」。

## 4. 风险与对策 / Risks

- **真飞机/风筝照片被救回**：经分类器 ≥10% 才收，且救回后仍要过关键点/
  锐度门槛，最坏结果是 0 星而非无鸟，代价可接受（选片场景不对称代价原则）。
- **MPS 并发**：见 3.2 推理锁。
- **性能**：补救仅对拒绝路径触发；鸟片占比高的正常批次几乎无感；
  空镜比例高的批次每张 +0.4s，属可解释成本（且有开关）。
- **Windows 打包**：无新依赖、无新模型文件，仅代码路径变化；按惯例做打包
  冒烟即可。

## 5. 验证 / Verification

1. `.venv/bin/python -m py_compile` 全部改动文件。
2. 用本次 A/B 脚本在 1063 张测试集重跑：判无鸟数应从 75 降到 ≤41，
   已知 39 张漏检救回 ≥32；1星/2星/3星文件夹 604 张检出结果不得变化。
3. 多线程压测：预取线程 + BirdID executor + 补救确认并发跑一小批，
   无崩溃/无事务错误（CLAUDE.md 最低验证要求）。
4. 设置开关关闭时行为与现状逐位一致（回归保障）。
