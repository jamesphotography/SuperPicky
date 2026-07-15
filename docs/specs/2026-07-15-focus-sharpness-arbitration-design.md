# 对焦判定锐度仲裁设计 / Focus Assessment Sharpness Arbitration Design

日期 / Date: 2026-07-15
状态 / Status: 已批准 / Approved
相关 / Related: issue #107（Unstable and unreliable focus assessment）；
docs/specs/2026-07-14-no-bird-rescue-scan-design.md（同为「像素证据优先于元数据」原则）

## 1. 问题 / Problem

对焦状态（BEST/GOOD/BAD/WORST）完全信任相机 EXIF 对焦点位置：
`verify_focus_in_bbox`（`core/focus_point_detector.py`）四层判定——头部圆 1.1 /
鸟掩码 0.9 / 框内 0.8 / **框外 0.5**——权重直接乘进锐度评分，
`photo_processor` 再按权重映射状态（>1.0=BEST / ≥0.9=GOOD / ≥0.7=BAD /
<0.7=WORST）。

但相机记录的对焦点**不是可靠的事实**。三组实测证据：

- **issue #107（Nikon Z50 II）**：两张几乎相同的清晰照片，一张 BEST 一张
  WORST——后者 EXIF 对焦点记录在画面角落的空背景上，锐度被乘 0.5
  （683→297），3 星跌 2 星。
- **Z8 全量 565 张 NEF（用户自有成片）**：有对焦数据且检出鸟的 477 张中
  **12% 对焦点落在鸟框外**（会判 WORST），其中相机自报「未合焦」的为
  **0 张**——全部是位置记录与鸟位置不符，不是真脱焦。67% 的框外样本偏离
  ≤7% 对角线（AF 框物理尺寸级别的误差，Z8 实测框宽 150~1032px）。
- **Sony A7 V 69 张 ARW**：**32% 框外**。但细查含两种性质——真实的追踪跟丢
  （连拍中 AF 框停留原地、偏离 1.6%→30.7% 递增，鸟可能真糊）与边缘含糊案例
  （小鸟剪影、对焦点在旁边树枝）。**从元数据无法区分这两种情况。**

## 2. 设计决策 / Decision

### 2.1 核心规则：鸟头实测锐度做终审

对焦几何判定结果为 BAD/WORST（`focus_sharpness_weight < 0.9`）时，
用**鸟头实测归一化锐度**仲裁：

```
若 归一化头部锐度 ≥ 用户当前锐度阈值（与评星硬门槛同一来源）:
    权重升为 (0.9, 1.0)   →  状态显示 GOOD
否则:
    维持原判（BAD/WORST 及其惩罚）
```

理由 / Rationale：
- 对焦判定的目的是「鸟拍清楚了没有」。鸟头锐度是像素级直接证据；
  EXIF 对焦点只是弱先验（上述三组数据证明它会「撒谎」）。
- **天然免疫误赦**：A7 V 追踪跟丢的照片鸟头真糊，锐度不达标，WORST 维持——
  这是单纯距离容差方案做不到的。
- 升到 GOOD 而非 BEST：诚实反映「元数据未确认对焦在头部，但锐度实证合格」；
  也避免蹭 BEST 的 1.1 锐度加成。

### 2.2 明确否决的替代方案 / Rejected Alternatives

- **纯距离容差带**（对焦点离鸟框 ≤N% 即赦免）：会把 A7 V「真跟丢」的连拍
  一并赦免，实测数据否决。
- **用户最初提议的「眼周 2× 头径同鸟身级」**：属距离容差的变体，同因否决；
  且救不了 issue #107 案例（对焦点离鸟数百像素）。距离信息仅保留在日志中
  供诊断，不参与裁决。
- **修改各品牌解析**：Z8/A7V/A1II/OM-1/X-T5 实测解析全部健康（无失败、
  坐标方向正确、DX 裁切偏移正确），不需要动。Z50 II 是否另有解析分支问题，
  等 issue #107 报告人的 NEF 到货后单独验证，与本方案不冲突。

### 2.3 触发范围与边界

- 仅当 `focus_sharpness_weight < 0.9` 触发（覆盖 0.8「框内/未合焦」与
  0.5「框外」两档）。BEST/GOOD 不受影响。
- 仲裁需要头部锐度存在：关键点检测成功且归一化锐度 > 0。关键点失败
  （鸟头不可见等）→ 不仲裁，维持原判（此时照片大概率本来就低星）。
- 锐度阈值与评星硬门槛同源（技能档换算或 custom 值），不引入新阈值。
- **不加设置开关**：行为严格单向（只减少错误惩罚、从不放大），且裁决条件
  本身就是用户自己设定的锐度标准；加开关徒增心智负担。

## 3. 代码落点 / Implementation Surface

- **`core/focus_point_detector.py`**：新增模块级纯函数
  `arbitrate_focus_weights(weights, norm_sharpness, sharpness_threshold)
  -> tuple[tuple[float, float], bool]`——输入几何判定权重与实测锐度，
  返回（可能升级的）权重与 `arbitrated` 标志。纯函数、无 Qt/IO 依赖，
  便于单测。
- **`core/photo_processor.py`**：`verify_focus_in_bbox` 调用后
  （~L2368）接一行仲裁；`arbitrated=True` 时输出 info 日志（i18n 中英
  新键，含实测锐度与阈值数值）。状态映射（~L2437）不改——权重 0.9 自然
  落 GOOD。
- **报表**：`focus_status` 照旧写 GOOD，无 schema 变更。
- **i18n**：`locales/zh_CN.json` / `en_US.json` 各加一条日志键
  （如「对焦仲裁: 元数据判 {orig} 但鸟头锐度 {sharp}≥{thr} → GOOD」）。

## 4. 预期影响 / Expected Impact

- Z8 样本：477 张中约 12%（55 张 WORST 候选）+ 13%（60 张 BAD）里所有
  鸟头达标者升 GOOD，锐度不再被错误乘 0.5/0.8；真糊的维持原判。
- issue #107 类照片：锐度实证合格 → GOOD，星级与姊妹片对齐。
- V1/V2 评星路径同时受益（权重与 focus_status 均为两者输入）。
- 无性能开销（复用已计算的归一化锐度，纯算术）。

## 5. 验证 / Verification

1. `arbitrate_focus_weights` 单测：达标升级 / 不达标维持 / 无锐度数据维持 /
   BEST·GOOD 不触碰 / 边界值（恰等于阈值）。
2. 回归：Z8 565 张分析脚本增加「仲裁后状态」列重跑，确认
   （a）WORST 占比显著下降且降幅与头部锐度达标率吻合；
   （b）A7 V 追踪跟丢连拍（DSC05403-05408）中鸟头不达标者维持 WORST。
3. GUI 真机批处理一次，确认日志出现仲裁记录、详情面板状态正确。
4. `.venv/bin/python -m py_compile` 改动文件 + 相关 pytest 全绿。
