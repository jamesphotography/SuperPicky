# 连拍组「最佳一张」多维评分设计 / Burst Best-Pick Multi-Signal Design

- 日期 / Date: 2026-07-21
- 状态 / Status: 待审 (Draft, pending review)
- 相关 / Related: `core/burst_detector.py`、`ui/results_browser_window.py`、issue #107 对焦仲裁 [[issue-107-focus-assessment]]

---

## 1. 背景与问题 / Background & Problem

结果浏览器已能把连拍聚成组(折叠/展开、组内标红一张为"最佳")。但用户反馈**组内挑出的"最佳"那张经常不对**。

根因(代码实证):

- 组内"最佳"的**唯一计算点**是 `ui/results_browser_window.py:1127`
  `best_photo = _burst_representative(photos)`,而
  `_burst_representative(photos) = max(photos, key=_burst_sharpness)`,
  `_burst_sharpness` 只取 **`head_sharp`(头部锐度)** 优先、`adj_sharpness` 回退。
- 即:**组内最佳 = 头部最锐的一张,单一维度**。它忽略了每张都已算好、存在 `report.db` 的其他信号——眼睛清晰度、对焦仲裁等级。
- 结果:一张"头最锐、但对焦没到眼 / 眼睛糊 / 背朝镜头"的照片会被选成组内封面。
- 附带事实:`composite_score` 字段在 `results_browser_window.py:1254/1305` 被 `max(key=(rating, composite_score))` 引用,但**全仓库从未赋值**,恒为 `0.0`,是预留但未实现的死字段——正好是本设计的天然接入点。

> 注:连拍**分组本身**(哪些照片算一组)不在本设计范围。当前分组为纯时间戳(<150ms);
> `verify_groups_with_phash` / `select_best_in_groups` 为 dead code。分组质量另案处理,
> 本设计只解决"**已经成组之后,组内选哪张为最佳**"。

## 2. 目标与非目标 / Goals & Non-Goals

**目标:**

- 组内"最佳一张"从"纯头部锐度"改为**多维评分**,纳入用户认可的维度:
  **对焦仲裁 `focus_status` + 眼睛清晰 `left_eye`/`right_eye` + 头部锐度 `head_sharp`**。
- 不新增模型、不重算、不改处理流程、不动 DB schema;旧目录**立即生效**。

**非目标(YAGNI):**

- 不纳入美学/构图(`nima_score`)、不纳入曝光(`exposure_status`)——用户明确只要"技术过硬(对焦实、眼睛清)"这一维度,美学与曝光留给用户后期把控。
- 不改连拍**分组**算法。
- 不改连拍**展开时的排列顺序**(`_burst_sort_key`,保持时间序)。

## 3. 设计 / Design

### 3.1 架构决策:读时即时算,不持久化

组内"最佳"是一个**排序键**,不需要落库:

- 改 `_burst_representative()` 的排序 key,从 `_burst_sharpness` 换成新的**分层排序键** `_burst_composite_key`。
- 现状本就是读时 `max(photos, key=...)`,所以**架构零变化、DB schema 零变化、无需重跑目录、旧库立即受益**。
- (可选第二阶段)若 CLI / 其他路径也需要该分,再考虑把分写入 `composite_score` 列复用;本期不做。

### 3.2 分层排序键 / Tiered ranking key

对组内每张照片计算一个可比较的 key,`max(photos, key=key)` 即为最佳:

```
key(photo) = (focus_tier, layer_score)

focus_tier:  BEST=3, GOOD=2, BAD=1, WORST=0
             缺失/None → 归中性档 2(GOOD),避免"没算出对焦"的一律沉底
             (实现时确认 focus_status 何时可能为空)

layer_score = w_eye · eye_sharp + w_head · head_sharp     # 同量纲,直接加权,无需归一化
  eye_sharp  = max(left_eye, right_eye)   # 鸟多侧拍,取对焦侧那只可见的眼
  head_sharp = head_sharp
  默认权重 w_eye = 0.7, w_head = 0.3      # "眼清为主 + 头锐为辅";真实数据标定
```

**排序语义:** 先按对焦仲裁分档(对焦准 > 一切,符合鸟摄铁律);**同档内**再按
"眼清为主 + 头锐为辅"的加权分精排。因 `eye_sharp` 与 `head_sharp` 同源于同一
`_calculate_sharpness` 度量、**同量纲**,可直接加权,无需跨量纲归一化——这正是选
"分层排序"而非"全局加权综合分"的关键好处(见备选方案)。

### 3.3 接入点与改动面 / Touch points

| 文件 | 改动 | 说明 |
|---|---|---|
| `ui/results_browser_window.py` | 新增 `_eye_sharp()`、`_focus_tier()`、`_burst_composite_key()` | 纯函数,便于单测 |
| `ui/results_browser_window.py:163` `_burst_representative` | key 由 `_burst_sharpness` → `_burst_composite_key` | 唯一"最佳"计算点,一处改动即覆盖折叠封面(:1160)+展开标红(:1157) |
| `_burst_sharpness` | 保留 | 可能仍被 `_burst_sort_key`(展开顺序)使用;不动 |

预估:**新增约 30–50 行纯函数 + 改 1 行 key 引用**。不碰处理流程/DB/打包。

## 4. 边界与风险 / Edge Cases & Risks

1. **"睁眼"未必可测(需实测验证)。** 现有 `left_eye`/`right_eye` 是**眼区清晰度**;
   闭眼时眼睑纹理可能仍有一定锐度。若实测无法区分睁/闭眼,则本维度语义降级为
   "眼区清晰度"(仍优于纯头锐)。**实现前用真实照片验证。**
2. **档边界硬。** BEST 档里一张眼睛略糊者,仍会胜过 GOOD 档里眼睛很实者。用户已认可
   "靠 `focus_status` 已足够准(#107 实测 jo-2 Z8 29/29)化解"。A/B 阶段重点观察此类跨档反例。
3. **字段缺失。** `head_sharp`/`eye` 缺失 → 视为 0;`focus_status` 缺失 → 中性档 2。
   全部缺失时退化为原行为(不劣化)。
4. **单眼可见。** 侧拍常只有一只眼有效,`max(left,right)` 天然取到那只;两只都缺 → 0。

## 5. 测试计划 / Test Plan

新增纯函数单测(pytest),覆盖:

- **跨档优先**:BEST 档眼80 胜过 BAD 档头锐95眼60(即 §问题示例组,现状会选错的那张)。
- **同档精排**:同为 GOOD 档,眼85头90 胜过 眼70头99(验证眼为主、头为辅)。
- **eye 合成**:`left=20,right=88 → eye_sharp=88`(取对焦侧)。
- **缺失兜底**:`focus_status=None → 档2`;`head_sharp/eye 缺失 → 0`,不抛异常。
- **退化保护**:全字段缺失时不劣于原纯锐度行为。

## 6. 验证方法 / Validation

参照既有 A/B 方法(如评星 495 张实测):

- 取一批**真实连拍目录**,对每组分别用"旧(纯头锐)"与"新(分层)"选最佳,导出对照缩略图。
- 人工判读:新方案选中的"最佳"是否更常是"对焦到眼、眼睛实"的那张。
- 特别抽查:①睁眼/闭眼样本(验证风险 1)②跨档反例(验证风险 2)。

## 7. 落地成本 / Cost Estimate

- **核心改动**:`results_browser_window.py` 约 30–50 行纯函数 + 1 行 key 引用,半天内。
- **测试**:单测 5–6 例,数小时。
- **验证**:A/B 对照 + 睁眼抽查,约 1 天(依赖准备真实连拍样本)。
- **零风险项**:不动 DB schema、不改处理流程、不重跑目录、不影响打包;旧库即时生效,可随时回退(改回 key 即可)。

## 8. 备选方案(已否决) / Rejected Alternatives

- **全局加权综合分**(三信号归一化后加权求和):`focus_status` 是离散级,塞进加权和不自然,
  且跨量纲归一化难标定、对焦 BAD 可能被高锐度拉回。分层排序用字典序天然处理离散优先级,更稳。
- **门槛+综合分**(对焦 WORST/BAD 重罚):与分层排序效果接近,但"惩罚系数"又是一个需标定的自由度;
  分层排序无此额外参数。
- **落库 composite_score**:本期"最佳"仅浏览器用,读时算即可,落库徒增 schema 迁移成本。留待第二阶段按需。
