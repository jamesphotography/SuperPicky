# GBIF 地理分布过滤器设计 / GBIF Geo-Distribution Filter Design

日期 / Date: 2026-07-25
状态 / Status: 待评审 / Draft for review
相关 / Related: `birdid/avonet_filter.py`（被替代）、`birdid/ebird_country_filter.py`（删除）、
`birdid/bird_identifier.py:1108-1186`（调用方）、`docs/GBIF_RARITY_INDEX.md`（同源管线先例）、
`birdid/data/bird_reference.sqlite` 的 `gbif_rarity_100`（提供 `specieskey → model_class_id` 映射）

---

## 1. 问题与目标 / Problem & Goal

识鸟链路的地理过滤当前由 `AvonetFilter`（`avonet.db`，1°×1° 网格）承担，
eBird 离线清单仅作兜底。实测证明这套组合有**确定性错误**，不是精度问题：

- 在悉尼，家麻雀 / 原鸽 / 紫翅椋鸟 / 欧乌鸫等城市常见鸟被**硬屏蔽**，永远识别不出；
- 在冰岛，候选集只剩 54 类导致三层兜底全塌，最终**完全放弃过滤**，产出小企鹅、蓝脚鲣鸟等跨半球错误。

目标：用一份自建的、许可干净的、与模型分类对齐的分布数据集替换现有两套数据，
并把"硬屏蔽 + 断裂兜底"改成**分层候选集 + 逐层放宽**。

The bird-ID geo filter today relies on `AvonetFilter` (`avonet.db`, 1°x1° grid),
with offline eBird lists as fallback only. Measurements show deterministic
failures rather than accuracy loss: common urban birds are permanently masked in
Sydney, and in Iceland the candidate set collapses to 54 classes, breaking all
fallback tiers and yielding cross-hemisphere errors. This design replaces both
datasets with a self-built, license-clean, taxonomy-aligned distribution dataset,
and replaces hard masking with a layered, progressively-widening candidate set.

---

## 2. 调研事实 / Research Findings（全部为本机实测）

### 2.1 现状链路 / Current pipeline

`bird_identifier.py:1108-1186` 的优先级为：

```
① GPS 网格 (AVONET)  →  ② 手选国家/地区 (eBird 优先, AVONET 矩形回退)  →  ③ 国家兜底 (eBird)  →  ④ 无过滤
```

②的触发条件是 `if not species_class_ids and (region_code or country_code)`（`:1126`），
即**照片只要有 GPS 且网格非空，用户手选的国家/地区完全不参与**。

### 2.2 AVONET 的三类缺陷 / Three defect classes in AVONET

**(a) 排除引入种与季节性访客** — AVONET 用原生分布范围，系统性排除归化种。
悉尼 GPS 网格（-33.87, 151.21）实测：

| 学名 | 中文 | AVONET 网格 | eBird AU-NSW | GBIF 本格记录数 |
|---|---|---|---|---|
| Passer domesticus | 家麻雀 | ✗ | ✓ | 9,747 |
| Columba livia | 原鸽 | ✗ | ✓ | 45,213 |
| Sturnus vulgaris | 紫翅椋鸟 | ✗ | ✓ | 24,557 |
| Turdus merula | 欧乌鸫 | ✗ | ✓ | 3,437 |
| Acridotheres tristis | 家八哥 | ✗ | ✓ | — |
| Cacatua galerita | 葵花鹦鹉 | ✓ | ✓ | 77,684 |

**(b) 分类版本落后** — `sp_cls_map` 仅覆盖 10,573 个 class_id，模型输出 10,964 类，
**391 类在任何地点都被永久屏蔽**，含东亚石䳭、短嘴豆雁、托列斯翡翠、淡眉雀鹛等常见种。
反向地，AVONET 网格的"假阳性"（4–13%）几乎全是合并种旧分类产生的幽灵
（西方牛背鹭 / 西紫水鸡 / 西方仓鸮 / 灰斑鸠）。

| 地点 | 网格类别数 | 假阳性 | 相对国家 eBird 的漏杀 |
|---|---|---|---|
| 悉尼 | 304 | 18 (5.9%) | 597 |
| 凯恩斯 | 333 | 43 (12.9%) | 593 |
| 北京 | 163 | 6 (3.7%) | 1,209 |
| 伦敦 | 133 | 8 (6.0%) | 732 |

**(c) 高纬度稀疏导致兜底链断裂** — 真实案例来自用户 433 张法罗群岛/冰岛照片
（Nikon Z 8，80.4% 带 GPS，`.superpicky_backup_JJ2TB_20260723-215817/report.db`）：

| 层 | 冰岛 (63.404, -19.103) | 结果 |
|---|---|---|
| ① AVONET 网格 | 54 类 | 模型 top-100 无一命中 |
| ③ 国家兜底 | IS/FO 既不在 `REGION_BOUNDS`，也无离线 eBird 数据 | 失效 |
| ④ 全局兜底 (`:1176`) | `species_class_ids=None` | 输出小企鹅、蓝脚鲣鸟、角海鹦 |

### 2.3 三份清单互不对齐 / Three-way list mismatch

- UI (`ebird_regions.json`) 列 49 国，其中 **11 国无离线数据**（TW/NP/NL/UA/EG/GR/MA/LK/CH/PT/MN），选中后静默落空；
- **14 国有离线数据但 UI 选不到**（BO/BZ/CM/ET/GH/GT/HN/NG/NI/PA/RO/SV/UG/VE），且这些国家也不在 `REGION_BOUNDS`；
- 州级数据仅 AU(8)/US(50)/CN(31) 三国。

### 2.4 Lite 版当前完全没有地理过滤 / Lite build has no geo filter at all

`SuperPicky_lite_win.spec:65-68` 逐个列出打包文件，**不含 `avonet.db`**。
后果链：`AvonetFilter._find_database()` 返回 None → `is_available()` 为 False →
`bird_identifier.py:318-340` 的 `get_species_filter()` 返回 None →
`:1112` 的 `if species_filter:` 跳过整个过滤块。

由于 eBird 路径（`get_species_by_region_ebird`）也是 `AvonetFilter` 的方法，
Lite 版打包的 `offline_ebird_data/`(1.5 MB) + `ebird_classid_mapping.json`(224 KB) +
`ebird_regions.json`(24 KB) 共 **1.8 MB 是纯死重，一行都不会被读取**。

新方案的 `geo_distribution.db`（~16 MB）单文件即可让 Lite 版**首次真正获得地理过滤能力**，
同时删除这 1.8 MB 死重。**已确认纳入 Lite 版**（见 §3.4、§7）。

### 2.5 附带 bug / Incidental bug

`avonet_filter.py:388` `_detect_country_from_gps` 文档声称"仅返回国家级"，但 `_SKIP`
只排除 6 个大洲代码，州级代码仍参与"面积最小优先"匹配，导致悉尼返回 `AU-NSW`、
阿拉斯加返回 `US-AK`。后果是本应扩大候选集的国家兜底反而加载了更窄的州级清单。
矩形判国本身也不可靠：20 点抽测中巴拿马城/乌干达判为 `None`，博茨瓦纳/纳米比亚被南非矩形吞掉。

### 2.6 许可 / Licensing

项目为**纯开源、纯免费、非商业**软件。

- **AVONET**：CC BY 4.0，署名即可。
- **GBIF**：CC0 / CC-BY 4.0，署名即可，已有 `gbif_rarity_100` 的署名先例。
- **eBird API**：条款为「will not publish or publicly distribute eBird data in their
  **original format**, either whole or in part, in any media」。该禁令**不因非商业而豁免**
  （管的是格式，不是商业性）。现仓库打包分发的 428 个 `species_list_*.json` 存储的是
  原始 speciesCode 数组，处于该禁令覆盖范围。条款允许派生数据集在附带同样条款的前提下传递。
  本设计移除这些文件，从根本上规避该问题。

来源 / Sources:
[eBird API Terms of Use](https://www.birds.cornell.edu/home/ebird-api-terms-of-use/)、
[eBird Data Access Terms of Use](https://www.birds.cornell.edu/home/ebird-data-access-terms-of-use)、
[GBIF Occurrence Snapshots](https://www.gbif.org/occurrence-snapshots)

### 2.7 GBIF 小样本验证 / GBIF small-sample validation

用 GBIF Occurrence API 的 `speciesKey` facet（`classKey=212`，
`license IN (CC0_1_0, CC_BY_4_0)`，`hasGeospatialIssue=false`）逐格拉取，单格耗时 0.5–0.8 s。

**关键种命中 / Key species check:**

| 地点 | 物种 | 期望 | GBIF | AVONET |
|---|---|---|---|---|
| 冰岛 | 北极海鹦 | 应有 | 1,540 ✓ | ✓ |
| 冰岛 | 欧绒鸭 | 应有 | 903 ✓ | ✓ |
| 冰岛 | 矛隼 | 应有 | 3 ✓ | ✓ |
| 冰岛 | **角海鹦** | **应无** | **✗ 正确排除** | **✓ 错误包含** |
| 冰岛 | 蓝脚鲣鸟 / 小企鹅 | 应无 | ✗ ✓ | ✗ |
| 悉尼 | 家麻雀 / 原鸽 / 紫翅椋鸟 / 欧乌鸫 | 应有 | 全部命中 | **全部屏蔽** |

角海鹦（*Fratercula corniculata*，北太平洋种）正是用户那批照片中 5 张误识别的来源——
AVONET 网格包含它因而拦不住，GBIF 正确排除。

**覆盖度 / Coverage**（映射到 model_class_id 后的类别数）：

| 地点 | CC0/CC-BY | 不过滤许可 | | 地点 | CC0/CC-BY | 不过滤许可 |
|---|---|---|---|---|---|---|
| 北京 | 430 | 456 | | 香港 | 658 | 678 |
| 上海 | 427 | 511 | | 台北 | 634 | 650 |
| 广州 | 415 | 446 | | 东京 | 424 | 441 |
| 成都 | 446 | 465 | | 首尔 | 396 | 426 |
| 西双版纳 | 404 | 416 | | 德里 | 428 | 444 |
| 青海 | 188 | 194 | | 黑龙江某格 | **0** | 2 |

许可过滤仅损失约 6%。中国主要区域覆盖充分；偏远网格存在稀疏/空网格，
这是**分层回退必须存在**的直接依据。

**体积外推 / Volume**（14 个随机陆地网格采样，总体为 AVONET 有鸟网格 18,709 个）：

| 阈值 | 均值类别/格 | 全球行数 | 未压缩 | gzip |
|---|---|---|---|---|
| n≥1 | 198 | 3,711,064 | 21.2 MB | 7.4 MB |
| n≥2 | 150 | 2,801,005 | 16.0 MB | 5.6 MB |
| n≥5 | 109 | 2,031,263 | 11.6 MB | 4.1 MB |

对照 `avonet.db` = **107.2 MB**（3,373,379 行，学名字符串做键）。新数据集用
`int16` class_id，体积小 5–10 倍，同时可删除 428 个 eBird json。

---

## 3. 设计决策 / Decisions（已与用户确认）

### 3.1 数据源：GBIF 自建，替换 AVONET 与 eBird 两套

不采用"AVONET ∪ eBird 并集"的最小改动方案。理由：并集会同时继承 AVONET 的 391 类缺失、
eBird 的再分发条款问题，以及冰岛这类"两边都无数据"的空白区；而 GBIF 一份数据同时解决四者，
且复用现有管线的边际成本接近于零。

### 3.2 过滤方式：分层候选集 + 逐层放宽（硬屏蔽）

保留硬屏蔽语义（`predict_bird` 中的 `continue`），但候选集分层，调用方逐层放宽直到有结果。
不采用软加权方案——软加权会改变现有置信度语义和 `birdid_confidence_threshold` 的含义，
波及面远大于本设计目标。

### 3.3 保留手选国家/地区设置

无 GPS 照片仍需要手选（用户样本中 19.6% 无 GPS）。但候选国家列表改为从
`country_species` 表动态生成，与网格数据同源，三方错位问题随之消失。

### 3.4 Lite 版纳入 `geo_distribution.db`

Lite 版当前完全没有地理过滤（见 §2.4），且携带 1.8 MB 从不读取的死重。
纳入新库后 Lite 首次具备与完整版一致的地理过滤能力，体积净增约 14 MB
（+16 MB 新库 −1.8 MB 死重）。用户已确认接受该体积代价。

---

## 4. 数据集设计 / Dataset Schema

新文件 `birdid/data/geo_distribution.db`：

```sql
CREATE TABLE cell_species (
    cell_id  INTEGER NOT NULL,   -- (lat_bin+90)*360 + (lon_bin+180)
    class_id INTEGER NOT NULL,   -- OSEA 模型类别 / model class, 0-10963
    n        INTEGER NOT NULL    -- CC0/CC-BY 观察记录数 / occurrence count
);
CREATE INDEX idx_cell ON cell_species(cell_id);

CREATE TABLE country_species (
    country  TEXT NOT NULL,      -- ISO 3166-1 alpha-2
    class_id INTEGER NOT NULL,
    n        INTEGER NOT NULL
);
CREATE INDEX idx_country ON country_species(country);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- snapshot_date, gbif_doi, license, attribution, builder_version, tier1_threshold
```

`cell_id` 用整数编码而非存储四个浮点边界，查询直接命中索引；
现有 `avonet_filter.get_species_by_gps` 每次要在 19,561 个矩形上做四次浮点 `BETWEEN` 比较。

`country_species` 由同一份网格数据聚合：每个 `cell_id` 用 `reverse_geocoder` 反查
ISO 国家代码后按国家汇总。这保证国家清单与网格数据永远一致。

---

## 5. 分层候选集 / Layered Candidates

新模块 `birdid/geo_filter.py`，替代 `birdid/avonet_filter.py`。

```python
def get_candidates(
    lat: Optional[float],
    lon: Optional[float],
    country_code: Optional[str] = None,
) -> Iterator[Tuple[Set[int], str]]:
    """按层产出候选集，调用方逐层放宽直到 predict 有结果。"""
```

| 层 | 来源 | 冰岛实测 | 悉尼实测 |
|---|---|---|---|
| L1 | 本格 `cumulative` 累积覆盖 99.9%（§5.1 已标定） | 481 | 370 |
| L2 | 本格全部（`n ≥ 1`） | 497 | 624 |
| L3 | 邻域 3×3 格合并 | — | — |
| L4 | 国家级（`reverse_geocoder` 判国；无 GPS 时用手选值） | — | — |
| L5 | 无过滤（`None`） | 10,964 | 10,964 |

L3/L4 标 "—" 表示本轮小样本未实测（邻域合并与国家级聚合都依赖尚未生成的
`geo_distribution.db`），实施时与 L1 阈值一并标定。

调用方 `bird_identifier.identify_bird` 改为遍历该迭代器，命中即停，并把层级标签写入
返回结果的 `geo_info.tier`，供日志与 UI 显示（替代现有的 `ebird_info.country_fallback` /
`gps_fallback` 布尔标记）。

无 GPS 时从 L4 起步；`country_code` 也缺失时直接 L5。

### 5.1 L1 阈值 T（已标定）/ L1 threshold T (calibrated)

**结论：采用 `cumulative` 方案，`cover=0.999`**（按记录数降序累加，保留累积占该格
总记录 99.9% 的物种）。`meta.tier1_threshold` 写入字符串 `"cumulative:0.999"`。

**标定方法**：`scripts_dev/calibrate_geo_threshold.py` 用固定种子（2026）从
`avonet.db.places`（18,709 个有分布数据的 1°网格，即 `WHERE EXISTS (... distributions ...)`
过滤后的数量，与 §2.7"体积外推"一致；`places` 表未过滤总行数为 19,561，
是 §4 提到的矩形总数，与本节采样池不同）随机抽样 60 格，逐格调用 GBIF
Occurrence API（`classKey=212`，CC0/CC-BY 许可）取 `speciesKey` facet，映射到
`model_class_id` 后分别用三种候选方案计算 L1 候选集，并检验每格记录数 top-30
的物种（"当地常见种"）是否被排除。60 格中 11 格返回零物种（偏远海洋/沙漠网格，
符合预期，未计入统计）。

**第一轮实测（`cumulative` 用初始 `cover=0.995`）：**

| 方案 | 中位数 | 均值 | P10 | P90 | top30 违规格数 |
|---|---|---|---|---|---|
| `absolute`（`n≥5`） | 90 | 137 | 0 | 356 | 15 |
| `cumulative`（`cover=0.995`） | 165 | 160 | 4 | 304 | 1 |
| `hybrid`（`max(2, 0.0001×total)`） | 157 | 142 | 3 | 273 | 10 |

三个方案在硬要求（top30 违规格数 == 0）上全部未过。按 Step 3 判定流程，
`cumulative` 放宽到 `cover=0.999` 重跑：

| 方案 | 中位数 | 均值 | P10 | P90 | top30 违规格数 |
|---|---|---|---|---|---|
| `cumulative`（`cover=0.999`） | 186 | 186 | 4 | 392 | **0** |

**为什么选 `cumulative`（`cover=0.999`）**：

- **硬要求**：唯一在两轮实测中达到 top30 违规格数 = 0 的方案。原因可从算法结构
  解释——当某格总物种数少于 30（稀疏格的常态）时，"top-30 常见种"实际上覆盖了
  该格几乎全部物种，此时任何固定或相对 `n` 下限（`absolute`/`hybrid`）都会因为
  排除低计数的稀有观测而误伤这些"稀疏格里也算常见"的物种；`cumulative` 按记录数
  贡献比例累加，稀疏格总记录少，覆盖到 99.9% 需要保留几乎全部物种，天然不会
  产生这种误伤。
- **规模要求**：中位数 186，落在 100–400 区间内。
- **稳定性（部分满足）**：P90=392 ≤ 600 达标；但 P10=4，低于 ≥50 的目标。
  这不是阈值过严导致的人为收窄，而是采样到的偏远网格本身观测记录稀少
  （如 11 个空网格之外，仍有若干格总记录数仅个位数到几十），`cumulative` 在
  这类格上保留的物种数等于该格几乎全部真实物种——即"P10 低"反映的是数据稀疏
  这一客观事实，而非过滤器的缺陷。这正是 §5 设计 L2/L3/L4 逐层回退的原因：
  当 L1 因为格子本身稀疏而很窄时，L2（本格全部）、L3（3×3 邻域）、L4（国家级）
  提供递进的宽松候选集，不依赖 L1 单层兜底。

**为什么淘汰 `absolute`（`n≥5`）**：15/49 个非空采样格违反硬要求，且行为方向与
需求相反——在悉尼这种高观测密度地区几乎不筛（624→502，占位初值时已知的问题），
在观测量小的格里却把当地真实常见种一并排除。

**为什么淘汰 `hybrid`（`max(2, 0.0001×cell_total)`）**：10/49 个非空采样格违反
硬要求。虽然相对项能适应总量差异，但绝对下限 `2` 在物种数少于 30 的稀疏格中
同样会排除那些计数为 1 的真实常见种（该格里"常见"只需要 1 次记录）。

**冰岛 / 悉尼实测**（`cumulative`, `cover=0.999`，直接查询 63.404,-19.103 与
-33.87,151.21 两格得出，已写入 §5 表格）：

| 地点 | 本格总记录数 | L2（`n≥1`） | L1（`cumulative@0.999`） |
|---|---|---|---|
| 冰岛 | 28,793 | 497 | 481 |
| 悉尼 | 3,304,911 | 624 | 370 |

冰岛记录总量小，L1 相对 L2 几乎不收窄（497→481），符合"稀疏格不应被 L1 进一步
压缩"的设计意图；悉尼记录总量大，L1 有效收窄长尾稀有记录（624→370），且验证
家麻雀（9,747）/ 原鸽（45,213）/ 紫翅椋鸟（24,557）/ 欧乌鸫（3,437）四个 §2.2
提到的引入种全部保留在 L1 内——记录数与 §2.2 表格完全一致，佐证了取数管线可复现。

---

## 6. 生成脚本 / Build Script

`scripts_dev/build_geo_distribution.py`，对每个 1°网格调用 GBIF Occurrence API 的
`speciesKey` facet，服务端直接返回「该格每个物种的观察记录数」，无需下载原始记录：

```
GET https://api.gbif.org/v1/occurrence/search
    ?classKey=212
    &decimalLatitude=<lat_bin>,<lat_bin+1>
    &decimalLongitude=<lon_bin>,<lon_bin+1>
    &hasCoordinate=true&hasGeospatialIssue=false
    &license=CC0_1_0&license=CC_BY_4_0
    &facet=speciesKey&facetLimit=1200&limit=0
```

随后用 `bird_reference.sqlite` 的 `gbif_rarity_100.specieskey → model_class_id`
（覆盖 10,963/10,964）映射，聚合出两张表，写入 SQLite，并在 `meta` 记录构建日期与许可。

### 6.1 为什么不走 S3 Parquet（实测否决）/ Why not S3 Parquet (measured, rejected)

设计初稿计划复用 `docs/GBIF_RARITY_INDEX.md` 记载的 DuckDB 直读 S3 管线。
实施时实测否决，三个原因：

1. **列名与初稿假设不符**：GBIF Parquet 没有 `classkey`（只有 `class VARCHAR`，值 `'Aves'`）、
   没有 `hasgeospatialissues`（只有 `issue VARCHAR[]`），`specieskey` 是 `VARCHAR` 而非整数。
2. **规模不可行**：`2026-07-01` 快照有 **8,515 个分片 / 265 GB**；单分片聚合实测 19.4 s，
   串行外推 46 小时。首次尝试以 `IOException: Timeout ... occurrence.parquet/000018` 失败。
3. **预筛选下载同样不现实**：鸟类 + 有坐标 + CC0/CC-BY 共 **21 亿条**记录
   （eBird 观察数据集在 GBIF 上是 CC-BY-4.0，不被许可过滤排除），SIMPLE_PARQUET 仍有 20–30 GB。

API facet 方案的实测吞吐（60 格随机采样）：

| 模式 | 等效单格 | 18,709 格外推 | 错误 |
|---|---|---|---|
| 串行 | 0.54 s | 2.8 小时 | 0 |
| 并发 4 | 0.172 s | 0.89 小时 | 2× HTTP 429 |
| **并发 8** | **0.079 s** | **0.41 小时** | 0 |
| 并发 16 | 0.311 s | 1.62 小时 | 1× 超时（过载反降） |

采用并发 8，配 429 指数退避与断点续传（已处理网格记入 `_build_progress`，构建完成后删表）。
该方法与 §5.1 的阈值标定同源，`cumulative:0.999` 直接适用；换用 S3 精确 count 反而需要
重新验证标定值是否仍成立。

**网格枚举**：用 `avonet.db` 中有分布记录的 18,709 个网格作枚举源。该依赖是一次性的——
数据落地后 §7 删除 `avonet.db` 不影响本库。

**数据升级路径**：重跑一次脚本即可（约 30–60 分钟），无需 API key、无需逐国抓取、
无需人工维护国家清单。这是本设计相对现状（428 个手工抓取的 json + 一份来源不明的
107 MB db + 无任何生成脚本）的核心改进。

---

## 7. 迁移与清理 / Migration & Cleanup

| 删除 / Remove | 行数 | 理由 |
|---|---|---|
| `birdid/ebird_country_filter.py` | 831 | 已 DEPRECATED、零调用点、含硬编码 API key (`:813`) |
| `birdid/avonet_filter.py` | 501 | 由 `geo_filter.py` 取代（含 `REGION_BOUNDS` 矩形表与 `_detect_country_from_gps` bug） |
| `birdid/data/avonet.db` | 107 MB | 由 `geo_distribution.db` (~16 MB) 取代 |
| `birdid/data/offline_ebird_data/` | 428 文件 | 国家级数据并入新库，同时规避 eBird 再分发条款 |
| `birdid/data/ebird_regions.json` | — | 国家/地区列表改由 `country_species` 动态生成 |
| `birdid/data/ebird_classid_mapping.json` | — | 仅服务于上述 eBird 清单，一并移除 |

**保留并调整 / Keep with changes:**

- `advanced_config` 的 `birdid_country_code` / `birdid_region_code`：保留（无 GPS 路径仍需要）。
- `birdid_use_ebird` → 改名 `birdid_use_geo_filter`（该字段实际控制的是整个地理过滤，
  而非 eBird；`bird_identifier.py:1146` 的 `data_source` 已硬写为 `"avonet.db (offline)"`）。
  旧键保留读取以完成一次性迁移，遵循 `migrate_birdid_dock_settings()` 的既有模式。
- `core/region_data.py`：改为从 `geo_distribution.db` 读取国家列表。
- `ui/settings_center.py:890-1000` 的国家/地区下拉：数据源切换，交互不变。
- UI 文案补充说明：手选地区仅在照片无 GPS 时生效（`locales/zh_CN.json:1028` 已有类似表述，
  但设置中心的下拉旁没有提示）。

**打包配置 / Packaging specs:**

| 文件 | 现状 | 改动 |
|---|---|---|
| `SuperPicky.spec:57` | 打包整个 `birdid/data` 目录 | 无需改动，新库自动包含 |
| `SuperPicky_win64.spec:58` | 同上 | 无需改动 |
| `SuperPicky_lite_win.spec:65-68` | 逐文件列出，含 3 个 eBird 死重文件、不含 `avonet.db` | 删除 `:66-68`，新增 `geo_distribution.db`（已确认纳入，见 §3.4） |
| `SuperPicky_full.spec` | 未列 birdid/data | 实施时确认其数据来源路径 |

**按 CLAUDE.md 要求，`.spec` 改动后必须做打包启动冒烟测试。**

**署名 / Attribution:** `meta` 表记录 GBIF 快照 DOI 与 CC-BY 署名，关于页同步展示，
沿用 iRateBird 的做法。

---

## 8. 验收标准 / Acceptance Criteria

1. **冰岛回归**：用 `.superpicky_backup_JJ2TB_20260723-215817/report.db` 中 433 张照片的
   GPS 与识别结果建回归集，验证小企鹅、蓝脚鲣鸟、角海鹦不再出现，北极海鹦等真实种保持命中。
2. **悉尼引入种**：家麻雀 / 原鸽 / 紫翅椋鸟 / 欧乌鸫 / 家八哥在悉尼 GPS 下进入候选集。
3. **空网格不崩**：黑龙江空网格 (46.41, 127.5) 能平滑降到 L3/L4，不落到 L5。
4. **391 类不再永久缺失**：随机抽取原 AVONET 缺失类，在其真实分布区能进入候选集。
5. **体积**：`geo_distribution.db` ≤ 40 MB（实测 **35.2 MB**）。

   设计初稿写 ≤25 MB，是按「6 字节/行」估的，忽略了 SQLite 每行的 rowid 与页开销；
   普通 rowid 表实测 86.3 MB。改用 `WITHOUT ROWID` + 复合主键（主键 B 树即表本身，
   省掉隐藏 rowid 和一份独立索引）后降到 35.2 MB（−59%），故把上限修正为 40 MB。

   **不再进一步裁剪长尾**：裁掉 `n=1` 的行只能降到 29.8 MB，却会让 **1,131 个网格
   整格清空**——那些正是稀疏网格，而稀疏网格候选集塌陷恰恰是本次改造要修的冰岛缺陷
   （§2.2c）。为 5 MB 牺牲稀疏地区的数据完整性与本设计目标直接冲突。

   **安装包影响**：完整版删除 `avonet.db`(102 MB) 与 `offline_ebird_data/`(1.5 MB)、
   新增 35.2 MB，**净减约 68 MB**；Lite 版删除 1.8 MB 死重、新增 35.2 MB，
   净增约 33 MB，换来首次具备地理过滤能力（§3.4）。
6. **性能**：单次 `get_candidates` L1 查询 ≤ 现有 `get_species_by_gps` 的耗时。
7. **清理彻底**：全仓库无 `avonet` / `ebird_country_filter` 残留引用；
   `.venv/bin/python -m py_compile` 通过所有改动文件。
8. **打包冒烟**：四个 `.spec` 改动后各做一次打包启动冒烟测试，确认新库路径在
   打包环境（`get_install_scoped_resource_path` / `_MEIPASS` 两条分支）下都能解析。

---

## 9. 风险 / Risks

| 风险 | 缓解 |
|---|---|
| L1 阈值标定不当，过严则退化为 AVONET 的窄候选问题 | 实施第一任务即标定；分层回退保证即使 L1 过严也不会崩到 L5 |
| GBIF 观察数据存在误标记录（错误坐标、圈养个体） | `hasGeospatialIssue=false` 过滤 + `n≥T` 阈值天然抑制单次误标 |
| 偏远地区网格稀疏 | L3 邻域 + L4 国家级两层回退；已用黑龙江空网格验证该路径必要性 |
| GBIF 快照重跑需数十 GB 扫描 | 沿用现有 DuckDB+S3 管线，列存只读 4 列；小样本验证可用 REST API（单格 0.5 s） |
| 观察密度偏差（欧美记录远多于其他地区） | 影响的是 `n` 的绝对值而非物种是否存在；相对阈值方案对此免疫 |
