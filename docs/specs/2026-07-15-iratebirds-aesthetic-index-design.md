# 鸟种美学指数（iRateBird）设计 / Species Aesthetic Index (iRateBird) Design

日期 / Date: 2026-07-15
状态 / Status: 待评审 / Draft for review
相关 / Related: 罕见度指标（`gbif_rarity_100`，同架构先例）；
`birdid/data/bird_reference.sqlite`（承载库）；`tools/report_db.py`（逐张字段）

## 1. 问题与目标 / Problem & Goal

给每个鸟种一个**物种颜值分**（这个鸟种本身好不好看），作为独立的展示与排序维度，
与罕见度指标完全对等——**不参与评星**。

关键区分（务必厘清，避免与现有指标混淆）：
- **TOPIQ 美学分**：这**张照片**拍得好不好（构图/清晰/曝光的画质美学，逐张不同）。
- **本设计的美学指数**：这个**鸟种**本身好不好看（物种颜值，与具体照片无关；
  同一只麻雀拍得再好，物种颜值不变）。两者正交，**绝不混入同一评分**。

数据来源：iRateBird 公民科学数据集（Santangeli et al. 2023, *Scientific Data*,
Nature, `s41597-023-02169-0`）。**许可证 CC-BY 4.0**——可离线打包，需署名。
figshare 提供 CSV/XLSX 直接下载，**全流程离线，无需联网**。

## 2. 数据集事实 / Dataset Facts（调研确认）

- 覆盖 **11,319 个物种 + 亚种**，40 万+ 评分，6,212 名用户。
- 评分尺度 **1–10**（1=最不好看，10=最好看），基于 Macaulay Library 真实照片。
- 分类口径 **eBird/Clements 2019 checklist** 学名（列 `sci_name`）。
- 打分非简单平均：贝叶斯序数回归（brms），控制照片质量、用户语言、鸟科随机效应。
- 用到的两个文件（raw ratings 文件本设计不用）：

| 文件 | 关键列 | 用途 |
|---|---|---|
| `iratebirds_final_predictions_*` | `sci_name`, `common_name`, `predicted_attractiveness_full_model`, `sd_full_model`, `no_of_ratings_used` | 物种级颜值分（主表） |
| `iratebirds_pred_ratings_species_and_sex_level_*` | `sci_name`, `sex`(male/female/unknown), `predicted_attractiveness_sex_model`, `sd_sex_model` | 雌雄分开的颜值分 |

**已知偏差（须在 About/文档注明）**：评分人以芬兰（17.8 万）与英语用户（7.9 万）为主，
「好不好看」带西方审美视角。作者建议按国籍本地化，但那需从 raw 数据重跑贝叶斯模型，
**属研究级工作量，明确不在本设计范围**；本设计只用全球 full_model 分。

## 3. 设计决策 / Decisions（均已与用户确认）

### 3.1 定位：纯展示 + 排序，不碰评星
逐张照片存一个鸟种美学分，详情面板展示、可按它排序/筛选。评分引擎完全不读它。
（与罕见度定位一致；理由：物种颜值不该决定「这一张拍得算不算成功」。）

### 3.2 雌雄二态：取 max 打底 + 雌雄两列都存
模型 `model_class_id` 是物种级、不知照片中个体性别，无信号选用雌/雄分。因此：
- **默认值** = `max(male_score, female_score)`（该种「最佳颜值」；拍鸟人拍到艳丽公鸟
  的概率与意愿更高，用上限更贴近「这张可能有多好看」）。
- 同时**存 `aesthetic_male` / `aesthetic_female` 两列**备用，给未来（若引入性别信号）留口子。
- 非二态种（sex-level 文件无该种，或只有 unknown）：默认值 = 物种级 full_model 分。
- **明确否决**：在识鸟链路加性别检测（独立大项目、多数种单张判不出性别，ROI 存疑）。

### 3.3 存储的数值口径
- **源分** = `predicted_attractiveness_full_model`（干净物种颜值分；raw 平均噪声大、
  subset_model 依赖用户协变量只对研究有意义，均否决）。
- **尺度归一化到 0–100**：`round((score - 1) / 9 * 100, 1)`，与罕见度 UI 同量纲。
  原始 1–10 值保留一列备查。
- **低置信度不删只标**：全量存，同时存 `no_of_ratings_used`；UI 可对评分数过少
  （数据集有 1.7% 的种仅 1 张照片、0.03% 仅 1 人评）的种打「数据少」标记或过滤，
  **不设硬阈值删除**（与罕见度全量存一致）。

## 4. 架构与数据流 / Architecture & Data Flow

四层，各层边界清晰、可独立测试：

```
[离线一次性构建]  figshare CSV ──build_iratebirds_table.py──▶ bird_reference.sqlite
                    (2文件)         学名匹配+max+归一化         新表 iratebirds_aesthetic
                                                                     │
[运行时查询]  bird_database_manager.get_aesthetic_by_class_id(class_id) ─┐
                                                                     │
[逐张落库]  photo_processor 处理时按 model_class_id 查分 ──▶ report.db.photos.aesthetic_index
                                                                     │
[展示/排序]  detail_panel 显示 + filter_panel/report_db 排序 sort_by="aesthetic_desc"
```

### 4.1 离线构建脚本 / Offline builder（开发期一次性，不打包）
- 新增 `scripts_dev/build_iratebirds_table.py`。输入：两个 figshare CSV（放
  `scripts_dev/data_sources/`，不入仓，脚本注释写明下载 URL 与文件名）。
- 学名匹配：iRateBird `sci_name`（eBird/Clements 2019）→ 本地 `model_class_id`，
  复用罕见度同款学名匹配路径（`gbif_rarity_100` 当初学名匹配覆盖 95.65%）。
  未匹配的种记入 `scripts_dev/data_sources/iratebirds_unmatched.csv` 供诊断。
- 输出：在 `bird_reference.sqlite` 建表 `iratebirds_aesthetic`（幂等：先 DROP 再建），
  列见 4.2。附署名/许可证元数据写入现有 `meta`/`versions` 机制或表注释。

### 4.2 数据库 schema
承载库 `birdid/data/bird_reference.sqlite` 新增表：

```sql
CREATE TABLE iratebirds_aesthetic (
    model_class_id    INTEGER,   -- 与 gbif_rarity_100 同键
    scientific_name   TEXT,
    aesthetic_100     REAL,      -- 默认展示值(0-100, max(m,f)或物种级归一化)
    aesthetic_raw_10  REAL,      -- 原始 full_model 1-10 备查
    aesthetic_male    REAL,      -- 0-100, 无则 NULL
    aesthetic_female  REAL,      -- 0-100, 无则 NULL
    is_dimorphic      INTEGER,   -- 1=sex-level 文件含雌雄两分, 0=否
    no_of_ratings     INTEGER,   -- 置信度指示(评分数)
    source            TEXT       -- 'iratebirds_2023'
);
CREATE INDEX idx_iratebirds_class ON iratebirds_aesthetic(model_class_id);
```

`tools/report_db.py` 逐张表 `photos` 新增列（沿 `gbif_rarity_100` 迁移套路，
加一个 schema 版本升级块）：
```
("aesthetic_index", "REAL", None)   -- 0-100, 该张照片鸟种的美学默认分
```

### 4.3 运行时查询 API
`birdid/bird_database_manager.py` 新增方法，签名与
`get_gbif_rarity_by_class_id` 对称：
```python
def get_aesthetic_by_class_id(self, class_id: int) -> Optional[float]:
    """按 model_class_id 取鸟种美学默认分(0-100)。None=未匹配。"""
```
（本期不做 country_code 参数——无本地化模型，见 §2 偏差说明。）

### 4.4 逐张落库
`core/photo_processor.py` 在已确定 `model_class_id`（识鸟成功）后，查一次
`get_aesthetic_by_class_id` 写入 `aesthetic_index`。查询失败/未匹配 → 存 NULL，
不影响处理（与罕见度同样容错）。**不参与任何权重或评分计算。**

### 4.5 展示与排序
- `ui/detail_panel.py`：在罕见度指标旁增一行「鸟种颜值 XX/100」（i18n 中英键）；
  `no_of_ratings` 过少时附「数据少」提示。
- `tools/report_db.py`：`sort_by` 增 `"aesthetic_desc"`（`ORDER BY
  COALESCE(aesthetic_index, -1e99) DESC, filename ASC`，套罕见度 `rarity_desc` 写法）。
- `ui/filter_panel.py`：排序下拉增「鸟种颜值」项。

## 5. 组件边界 / Unit Boundaries

| 单元 | 职责 | 依赖 | 可独立测什么 |
|---|---|---|---|
| `build_iratebirds_table.py` | CSV→匹配→归一化→建表 | 两 CSV、bird_reference.sqlite | max 取值、归一化公式、匹配率、幂等 |
| `get_aesthetic_by_class_id` | class_id→分数查询 | bird_reference.sqlite | 命中/未命中/NULL 容错 |
| report_db 迁移+排序 | 加列、aesthetic_desc | report.db | 迁移幂等、NULL 排序末位 |
| detail/filter UI | 展示+排序项 | 上述数据 | i18n 键、低置信度标记 |

## 6. 验证 / Verification

1. 构建脚本单测：max(m,f) 取值正确、归一化 `(s-1)/9*100` 边界（1→0, 10→100）、
   非二态种回退物种级、匹配率打印且 ≥90%、重跑幂等（表行数稳定）。
2. `get_aesthetic_by_class_id`：已知种命中、未匹配 class_id 返回 None、库缺表容错。
3. report_db：迁移幂等（连跑两次不重复加列）、`aesthetic_desc` 排序 NULL 落末位。
4. 端到端：小目录批处理后 `photos.aesthetic_index` 有值且与库中该种分一致；
   详情面板显示、按颜值排序可用。
5. `.venv/bin/python -m py_compile` 改动文件 + 相关 pytest 全绿。

## 7. 显式不做 / Out of Scope

- 性别检测（识鸟链路加 male/female 分类）——独立大项目。
- 按国籍/文化本地化颜值分——需从 raw 数据重跑贝叶斯模型，研究级工作量。
- 美学指数参与评星/选片加权——定位为纯展示+排序（§3.1）。
- 打包 raw ratings 文件——只用两个 predictions 文件构建离线表。

## 8. 许可证与署名 / License & Attribution

CC-BY 4.0。需在 About 页或文档署名：Santangeli, A. et al. *The iratebirds Citizen
Science Project: a Dataset on Birds' Visual Aesthetic Attractiveness to Humans.*
Scientific Data 10, (2023). 并注明数据的文化偏差来源（§2）。
