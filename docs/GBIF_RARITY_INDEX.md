# GBIF 全球罕见度指数 / GBIF Global Rarity Index

> v4.3.0 起，SuperPicky 引入了一套基于 **GBIF 全球观察数据** 的罕见度
> 系统：每只鸟一个 0-100 分，配五级图标，帮你在一堆相似照片里一眼挑出
> 真正难得的那张。本文说明分数从哪里来、怎么算、怎么读。
>
> v4.3.0 introduces a rarity scoring system built on **GBIF
> occurrence data**: every bird gets a 0-100 score plus a 5-tier
> glyph, so you can pick out the genuinely rare shot from a stack of
> lookalikes at a glance. This document explains where the score
> comes from, how it is computed, and how to read it.

---

## 1. 为什么是 GBIF / Why GBIF

我们需要一份**可溯源、可商用、可开源**的全球罕见度数据。
评估了几个候选数据源之后，GBIF（Global Biodiversity Information
Facility）是唯一同时满足规模、许可、可引用性三项的：

We needed **citable, license-clean, fully open** rarity data with
global coverage. Among the candidates we evaluated, the Global
Biodiversity Information Facility (GBIF) was the only one that
checked all three boxes — scale, license, citability:

- **数据规模 / Volume**: 30 亿+ 物种观察记录，覆盖全球 / 3B+ occurrence records, global coverage.
- **开放许可 / License**: 我们只用 CC0 / CC-BY 两个最宽松的许可下的数据 / We
  filter down to CC0 / CC-BY records only.
- **AWS Open Data**: GBIF 的月度快照以 Parquet 格式发布在 S3 上，匿名可读 /
  Monthly GBIF snapshots are published as Parquet on AWS S3 with anonymous access.
- **学术可引用 / Citable**: 每个 GBIF 数据集都有 DOI，可作正式引用 / Each
  GBIF dataset has a DOI suitable for academic citation.

---

## 2. 五级视觉分级 / 5-tier visualization

为了让"100 分罕见度"易读，我们映射到 5 个层级，每级配一个圆形充填图标
和颜色。

To make a "0-100" raw score legible, we bin it into 5 tiers, each with a
filled-circle glyph and a color.

| 图标 | 中文 | English | 分数区间 | 大致语义 |
|:---:|:---:|:---:|:---:|---|
| `○` | 常见 | Common | 0 – 7 | 后院常客、城市鸟、广分布 |
| `◔` | 能见 | Occasional | 8 – 24 | 季节性或局部常见，野外能见到 |
| `◑` | 少见 | Uncommon | 25 – 49 | 需要特意寻找，不是天天能见 |
| `◕` | 罕见 | Rare | 50 – 74 | 多数鸟人一年看不到几次 |
| `●` | 传奇 | Legendary | 75 – 100 | 一辈子能拍到一次就值得记入鸟生 |

阈值刻意设成**非均匀**（8/25/50/75），原因是真实分布是**右偏长尾**——
大多数常见鸟挤在 0-15 分，把 0-100 平均切五段会让 90% 的鸟变成"常见"。
现在的阈值能让每一档都有足够多的鸟落入。

The thresholds are intentionally **non-uniform** (8/25/50/75) because
the real-world distribution is **right-skewed long-tail** — most common
birds cluster in 0-15. A uniform split would collapse 90% of birds into
"Common." The current cuts give each tier a meaningful population.

---

## 3. 分数怎么算 / How the score is computed

### 3.1 数据源 / Data source

- **来源 / Source**: `s3://gbif-open-data-us-east-1/occurrence/<YYYY-MM-DD>/occurrence.parquet/*`
- **数据周期 / Snapshot**: 截至发布前 2 周内的最新月度快照 / Latest
  monthly snapshot prior to release (within 2 weeks).
- **查询引擎 / Query engine**: DuckDB（直接读 S3 Parquet，无需下载整库）/
  DuckDB queries the S3 Parquet directly.
- **许可过滤 / License filter**: 仅保留 `license` ∈ {`CC0_1_0`, `CC_BY_4_0`} /
  Only `CC0_1_0` and `CC_BY_4_0` records are counted.
- **GPS 国家解析 / Country resolution**: 照片 GPS 用 `reverse_geocoder` 离线解析为
  ISO 国家代码，再查国别罕见度 / Photo GPS is reverse-geocoded offline to an
  ISO country code, used for country-scoped lookups.

### 3.2 归一化 / Normalization

直接用 GBIF 的 `count` 当排名会让"麻雀级"鸟把 99% 的分数挤掉。我们用
log-normal 归一化把分布拉平到 0-100：

Raw GBIF `count` is heavily right-skewed (city sparrows dwarf rare
endemics). We apply log-normal normalization to spread the score
across 0-100:

```
score = 100 × (1 - (log10(count + 1) - log10(min_count + 1)) /
                  (log10(max_count + 1) - log10(min_count + 1)))
```

即：观察记录越**少**，分数越**高**。

Fewer observations → higher rarity score.

### 3.3 IUCN 红色名录下限 / IUCN Red List floor

GBIF 观察次数低不一定意味着真的"濒危"——也可能是冷门科研对象。我们用
IUCN 红色名录给"应该罕见"的鸟设下限：

A low GBIF count doesn't always mean a species is *actually* endangered —
sometimes it's just understudied. We use the IUCN Red List status as a
floor to ensure threatened species are scored as rare:

| IUCN 等级 | 下限分数 |
|:---:|:---:|
| CR (Critically Endangered) | 90 |
| EN (Endangered) | 75 |
| VU (Vulnerable) | 60 |
| NT (Near Threatened) | 45 |

实际 GBIF 分数若高于 IUCN 下限则保留；若低于则提到下限。

The IUCN floor only applies when GBIF score is below it.

### 3.4 count=0 异常修复 / count=0 anomaly fix

某些常见鸟在 GBIF 上 `count = 0`（地区性数据采集差异、学名同义词未合并
等原因），原始算法会给 100 分——明显错误。我们对这 47 个异常物种，用
**同科属邻居 GBIF 中位数**作 proxy 重打分，写进离线预计算表里。

Some common birds have `count = 0` on GBIF due to regional sampling
gaps or unmerged synonyms; the naive algorithm scores them 100 — clearly
wrong. For these 47 species we substitute the **median GBIF score of
same-family neighbors** as a proxy and bake the corrected value into
the offline precomputed table.

### 3.5 手动 override / Manual overrides

4 个物种因为分布特殊（岛屿特有 / 单点遗存 / 中国大陆稀客等），算法分
数明显违反鸟圈直觉，单独 hardcode 修正：

4 species where algorithmic scores clash with field-birder intuition
have explicit hardcoded overrides:

| 学名 / Scientific name | 分数 / Score | 备注 / Note |
|---|:---:|---|
| *Quoyornis georgianus* (White-breasted Robin) | 40 | 澳洲西南端特有 |
| *Butorides sundevalli* (Galapagos Heron) | 12 | Galapagos 特有但常见 |
| *Pyrocephalus obscurus* (Vermilion Flycatcher - Galapagos clade) | 18 | 亚种分级争议 |
| *Pteroglossus erythropygius* (Toucanet sp.) | 20 | 局部常见 |

---

## 4. 在 SuperPicky 里怎么用 / How to use it

### 4.1 详情面板 / Detail panel

照片选中后，右侧详情面板会显示三行连续的鸟类信息：

The right-side detail panel shows three adjacent rows when a photo is
selected:

```
鸟种 / Species:   Black-capped Chickadee  ◔  (点击复制学名)
全球罕见度:        Occasional (12/100)
IUCN:             LC (Least Concern)
```

**点击鸟种行可一键复制学名到剪贴板。** / Click the species row to copy the
scientific name to clipboard.

### 4.2 结果浏览器排序 / Results browser sort

排序下拉框新增 **"罕见度↓ / Rarity↓"** 选项，并设为默认排序。
照片按 GBIF 罕见度从高到低排列，让最难得的鸟一眼可见。

A new **"Rarity↓"** option in the sort dropdown is now the default —
photos are ordered by GBIF rarity score, putting your rarest finds at
the top of the queue.

### 4.3 控制台输出 / Console output

每只识别到的鸟，控制台日志会跟一个 tier 图标：

Each identified bird now appears with a tier glyph in the log:

```
[BirdID] Common Crane (Grus grus) — confidence 84%  ◑
[BirdID] House Sparrow (Passer domesticus) — confidence 91%  ○
```

跑批结束时还会打印整批次的 tier 分布摘要：

At the end of a batch, a tier breakdown summary is printed:

```
[Rarity Summary] ○ Common: 124 | ◔ Occasional: 38 | ◑ Uncommon: 12 | ◕ Rare: 3 | ● Legendary: 0
```

### 4.4 文件夹布局 / Folder layout

新增「按评级优先 / 按物种优先」二选一开关（高级设置 → 输出设置）：

A new toggle under Advanced Settings → Output picks between two
organization strategies:

- **Rating-first** (默认 / default): `3 Star/Black-capped Chickadee/...`
- **Species-first**: `Black-capped Chickadee/3 Star/...`

不管选哪种，**1 星 / 0 星 / -1 星** 的照片都会被归到 `Other Birds/` 分
支，避免主目录被低质量副本污染。

Regardless of which is selected, **1★/0★/-1★** photos always go to the
`Other Birds/` branch to keep the main tree free of low-quality copies.

### 4.5 EXIF / XMP 元数据 / Metadata write

GBIF 罕见度分数会写入 XMP 字段：

The GBIF rarity score is written to:

- **`XMP-iptcExt:Event`** — `GBIF Rarity 18/100`（示例 / example）
- **`XMP-iptcCore:IntellectualGenre`** — `IUCN: LC`（IUCN 等级 / IUCN status）

Lightroom / Bridge / digiKam / ExifTool 都能直接读到这两个字段。

These fields round-trip through Lightroom / Bridge / digiKam / ExifTool.

---

## 5. 怎么解读分数 / How to read the score

GBIF 罕见度反映的是**全球观察密度**，不直接等于"物种保护级别"：

The GBIF score reflects **global observation density** — it is not a
substitute for conservation status:

| 场景 | GBIF 高分 ≠ IUCN 濒危 |
|---|---|
| 极偏远岛屿特有种 | 全球观察少 → GBIF 高分；本地可能并不少 |
| 难以拍照的夜行性 / 林冠层鸟 | 观察少 → GBIF 高分；种群可能稳定 |
| 学名最近被拆分 / 合并 | 数据库未同步 → 分数可能偏差 |

**最佳实践 / Best practice**: GBIF tier 当作"拍到这张照片的稀有度"——它
帮你快速识别值得打星的瞬间。要评估保护意义，再去查 IUCN 等级（已经显示
在详情面板里）。

Treat the GBIF tier as **"how rare it is to capture this shot"** — it's
optimized to surface the photos worth starring. For conservation
significance, cross-reference the IUCN row in the same panel.

---

## 6. 引用 / Citation

如果你在科研、媒体或博客中使用 SuperPicky 的罕见度结果，建议同时引用
GBIF 的数据快照 DOI（每次构建时记录在 `birdid/data/bird_reference.sqlite`
的 `gbif_rarity_index` 表 metadata 行）以及 IUCN Red List。

If you use SuperPicky's rarity output in research, media, or blog
posts, please cite the GBIF snapshot DOI (recorded in the
`gbif_rarity_index` table metadata row of
`birdid/data/bird_reference.sqlite` at build time) together with the
IUCN Red List.

- GBIF Occurrence Snapshot: https://www.gbif.org/occurrence-snapshots
- IUCN Red List: https://www.iucnredlist.org
- SuperPicky: https://github.com/jamesphotography/SuperPicky

---

## 7. 反馈 / Feedback

如果你发现某只鸟的 tier 明显违反直觉（比如你家后院常客被标成"传奇"），
欢迎提 Issue 附上学名 + 你的地区 + GBIF 链接，我们会评估加进 4.x 的手
动 override 列表。

If you spot a tier that strongly disagrees with field intuition (e.g. a
backyard regular flagged as "Legendary"), open an Issue with the
scientific name, your region, and the GBIF taxon link — we'll review it
for the 4.x manual override list.

Issues: https://github.com/jamesphotography/SuperPicky/issues
