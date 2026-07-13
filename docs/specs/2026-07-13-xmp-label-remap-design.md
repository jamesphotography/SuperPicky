# 颜色标签默认映射调整（B+ 方案）设计 / XMP Label Default Remap Design

日期：2026-07-13 ｜ 分支：dev ｜ 状态：已获用户批准（Paul 反馈 P2，方案 B+）

## 背景 / Background

现状（`core/photo_processor.py:2538-2546`）：飞鸟=绿色、头部精焦=红色。
Paul 的直觉（红=差）暴露默认映射反直觉——最好的照片被标成红色。
用户拍板 B+ 方案：只改默认映射，不开放自定义（维持设置收敛）。

## 新映射 / New Mapping（单标签字段，优先级自上而下）

| 优先级 | 条件 | 颜色 | i18n 键 | 兜底英文 |
|--------|------|------|---------|----------|
| 1 | is_flying | 蓝色/Blue | xmp_labels.flight | Blue |
| 2 | focus_status == "BEST" | 绿色/Green | xmp_labels.focus | Green |
| 3 | focus_status ∈ {"BAD","WORST"} | 红色/Red | xmp_labels.defocus | Red |
| — | GOOD / 无鸟 | 无标签 | — | — |

- 黄色不用：合焦（GOOD）是常态，全量打标是噪声；无标签本身即"普通"信号。
- 红=脱焦与平铺模式协同：文件不分目录时红标是快速识别废片的手段。
- 优先级保持飞鸟最高（稀缺信息优先，且精焦照通常已是 3★）。
- `XMP:Label` 为单值字段，一张照片只有一种颜色（方案约束，已与用户确认）。

## 实现 / Implementation

1. **抽纯函数**（`core/photo_processor.py` 模块级，便于单测）：
   `compute_xmp_label(is_flying: bool, focus_status: Optional[str], translate) -> Optional[str]`
   ——`translate` 传 `i18n.t`；沿用现有兜底逻辑（语言包缺 key 时回退英文
   色名，绝不把 key 串写进 LR——4.3.0 白框陷阱）。
2. **调用点**（:2538-2546）替换为纯函数调用；条件从
   `focus_sharpness_weight > 1.0` 改用等价且更直白的 `focus_status`
   （:2399 确认 weight>1.0 ⇔ "BEST"）。
3. **i18n**（两语言包 `xmp_labels` 段）：`flight` 绿色→蓝色/Green→Blue、
   `focus` 红色→绿色/Red→Green、新增 `defocus` 红色/Red。
4. **不改**：`set_metadata`/侧车写入链路（label 值透传）、结果浏览器
   内部 UI 颜色（与 XMP 标签无关）。

## 兼容性代价 / Compatibility Note

老用户基于「绿=飞鸟」建的 LR 智能收藏夹会失效——合并 nightly 时
ChangeLog 必须醒目注明「颜色标签含义变更」及新旧对照表。

## 测试 / Testing

- 纯函数全分支：飞鸟优先、BEST→绿、BAD/WORST→红、GOOD/None→None、
  语言包缺 key 回退英文。
- locales 断言：zh 蓝色/绿色/红色，en Blue/Green/Red。
- 变更 py 文件 `py_compile`。

## 不做 / Out of Scope

- 用户自定义颜色映射（违背设置收敛，且 LR 跨语言匹配陷阱会放大）。
- 黄色标签、旧照片标签迁移工具。
