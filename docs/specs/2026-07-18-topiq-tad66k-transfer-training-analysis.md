# TOPIQ-IAA 迁移训练到 TAD66K 的评估与决策 / TOPIQ-IAA Transfer Training on TAD66K — Analysis & Decision

日期 / Date: 2026-07-18
状态 / Status: **已放弃 / Rejected**（现有证据不支持，非永久否决）
相关 / Related: `iqa_scorer.py`（App 现用 TOPIQ-IAA）、`topiq_model.py`（CFANet res50）、
`core/rating_quota.py`（V2 评星，消费 TOPIQ 百分位）、
`scripts_dev/aesthetic_topiq_diagnostic.py`（本次诊断脚本，可复现）

---

## 0. 一句话结论 / TL;DR

评估「把 TOPIQ-IAA 从 AVA 迁移训练到 TAD66K，以提升鸟片美学评分」。**诊断显示当前
模型没坏、Swin 变体换了也没用、所谓『分数窄』对评星无影响**——现有证据不支持这项
周级投入，**放弃**。若未来重启，真正的前置不是训练，而是先建鸟类审美人工标注基准集。

We evaluated fine-tuning TOPIQ-IAA from AVA to TAD66K to improve bird-photo
aesthetic scoring. The diagnostic showed the current model ranks sensibly, the
Swin variant adds nothing, and the "narrow score range" doesn't affect rating.
Evidence does not justify the weeks-scale effort — **rejected**. If revisited,
the real prerequisite is a human-labeled bird-aesthetic benchmark, not training.

## 1. 背景 / Context

- **现用模型**：`cfanet_iaa_ava_res50`（TOPIQ-IAA，ResNet50 骨干，AVA 数据集训练，
  输出 1–10 美学分，输入 384×384），打在**鸟裁剪区**上（V4.6 起，非整图）。
- **提议动机**：属于「B3 + C」——凭印象觉得区分度不够 + 探索性想试 TAD66K
  （听说标注更密）。**没有具体失败案例，没有量化证据**。
- **TAD66K 事实**（IJCAI 2022, TANet；核实自官方仓库/HuggingFace）：66K 图 / 47 主题 /
  MOS 分 1.13–9.46 / 每图 ≥1200 人标注（比 AVA 密集）；核心立论是「美学是**主题
  依赖**的」，其基线 TANet **要吃主题标签**。**pyiqa 无现成 TAD66K 权重**——`topiq_iaa`
  系列只有两个变体,都是 AVA 训练（res50 与 swin）,故换权重白嫖不成立,必须自训。
- **数据许可未定**：代码 Apache-2.0,但数据集商用条款未确认（SuperPicky 是商业 App）。

## 2. 第一性原理的两个拦截点 / Two first-principles objections

1. **V2 吃的是排序不是绝对分**。评星 V2 用 TOPIQ 的**批内百分位**（锐度百分位 +
   美学百分位）。分数挤在窄区间只要单调排序正确就对 V2 零影响。所以「分数窄」
   本身不是问题——除非**排序**错了。
2. **窄是输入分布决定的**。能走到 TOPIQ 打分的照片都已过硬门槛（有鸟、置信度/锐度
   达标、眼睛可见）——本就是一批同质候选。任何美学模型打同质输入分数都会挤在窄带。
   换数据集未必能拉开。

## 3. 诊断方法 / Diagnostic method

在已处理目录（`~/Desktop/Test-Superpicky`，453 张有鸟裁剪区）上，对**同一批裁剪区**
用 res50（App 现用）和 Swin 变体各打一遍，比分布 + 算秩相关；再把 res50 的极值端
（最美/最丑各 8 张）拼图供人工核对。脚本：`scripts_dev/aesthetic_topiq_diagnostic.py`。
流水线已验证为完全确定性（见 perf 审计），故单轮结果即可信。

## 4. 结果 / Results

| 指标 | res50（App 现用） | Swin 变体 |
|------|------------------|-----------|
| 范围 range | 3.77–6.54 | 2.95–6.32 |
| mean / std | 5.20 / 0.62 | 4.73 / 0.70 |
| p10–p90（80% 落区） | 4.31–5.96（跨度 1.65） | 3.78–5.49（跨度 1.71） |

**res50 vs Swin 秩相关 Spearman ρ = 0.898**（453 张）。

人工核对拼图（res50 极值端）：
- **最美 8 张**（6.37–6.54）：艳红鸟配黄背景、细腻正面仙鹩、红耳鹎配红喂食器——
  锐、色彩好、背景干净、构图完整。**确实好看**。
- **最丑 8 张**（3.77–3.94）：蓝天飞版猛禽（主体小、背景空、逆光）、欠曝剪影、平淡灰鸟。
  **确实审美较弱**。极值端 Swin 与 res50 判断一致。

## 5. 结论与理由 / Conclusion & reasoning

| 发现 | 含义 |
|------|------|
| 两模型都窄（p10–p90 跨度 ~1.7） | 输入预筛同质所致；V2 用百分位,**不影响评星**（伪问题） |
| Swin ρ=0.898、极值端全一致 | **不是白嫖机会**;仅分值下移 ~0.5,零新排序信息,换它=没换 |
| res50 极值端排序合理（拼图证实） | **模型没坏**;审美排序符合直觉,top=好片 bottom=弱片 |
| 唯一争议：飞版被打低分 | 审美上站得住（构图空）+ **已被评星引擎的飞版乘法加成补偿**,未伤星级 |

动机是「凭印象 + 探索」,诊断显示无真问题;TAD66K 训练是周级投入（数据许可未定、
训练、跨平台推理验证、打包新模型）,且**缺验证标尺**（无鸟类审美 ground truth）。
投入产出严重不成比例——**放弃**。

## 6. 若未来重启:真正的前置 / If revisited: the real prerequisite

无论用 AVA/Swin/TAD66K,都缺**鸟类审美人工标注基准集**（建议对 ~200 张裁剪区做
两两「哪张更美」判断,成本约一两小时）。没有它无法验证任何模型好坏。这个基准集
才是一切的前置,也是判断 TAD66K 到底值不值的唯一客观办法——**先建基准,再谈训练**。
届时先算「当前 res50 与人工判断的秩相关」作为要超越的基线。

## 7. 复现 / Reproduce

```bash
# 目录须先被 SuperPicky 处理过(有 .superpicky/report.db)
.venv/bin/python scripts_dev/aesthetic_topiq_diagnostic.py ~/Desktop/Test-Superpicky
# 输出:res50/Swin 分布 + Spearman ρ,明细写入 <目录>/.superpicky/aesthetic_diag.csv
```
