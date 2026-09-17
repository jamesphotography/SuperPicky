# eBird 区域过滤设计 / eBird Region Filter Design

日期 / Date: 2026-09-16
状态 / Status: 设计已确认，待写实施计划 / Design approved, implementation plan pending
取代 / Supersedes: `docs/specs/2026-07-25-gbif-geo-filter-design.md`（GBIF 1° 网格分层过滤）
相关 / Related: `birdid/geo_filter.py`、`birdid/bird_identifier.py:1003-1195`、`core/region_data.py`、
`ui/settings_center.py`（识鸟页地区行）、`ui/birdid_dock.py`（国家/区域下拉）

---

## 1. 问题与目标 / Problem & Goal

4.6 把识鸟地理过滤从「AVONET 网格 + 离线 eBird 清单」换成了 GBIF 1° 网格分层过滤，
并删除了中国/澳洲/美国的省州级选择。用户反馈识别错误变多：

- **主要是无 GPS 照片**：只能手选国家，候选集是整国 GBIF 清单（中国 1512 / 澳洲 1017 / 美国 2184 类），
  远宽于旧版 eBird 省州清单（多为 300–700 类）。
- **部分有 GPS 照片**也出错（尚无具体案例，本设计不针对其做推测性修改，见 §9）。

目标：恢复并优化基于 eBird 的区域过滤——中澳美三国省州级 + 全球国家级，全部改用 eBird 清单；
彻底移除 GBIF 网格数据（35 MB）。

After 4.6 replaced AVONET + offline eBird with a GBIF 1-degree grid and dropped
subnational selection, users report more misidentifications, mostly on photos
without GPS (country-wide candidate sets are far wider than the old eBird
state lists). This design restores eBird-based filtering — subnational for
CN/AU/US and national worldwide — and removes the 35 MB GBIF grid dataset.

---

## 2. 调研事实 / Research Findings（全部为本机实测，2026-09-16）

### 2.1 eBird 清单边界比 GBIF 紧

同一省州映射到模型类别后的候选数（GBIF 用 `gadmLevel1Gid` facet，仅 CC0/CC-BY）：

| 省州 | 旧 eBird 清单 | GBIF 省州 | GBIF 多出的种中记录数 ≤5 |
|---|---|---|---|
| 新南威尔士 | 603 | 788 | 117/227 |
| 加州 | 886 | 1151 | 239/305 |
| 北京 | 510 | 497 | 9/26 |
| 云南 | 883 | 902 | 32/65 |

澳美两国 GBIF 的长尾主要是 iNaturalist 零星记录/误鉴定与逃逸个体；eBird 清单经地区审核员把关。

### 2.2 旧版 eBird 映射表一直在误杀本地鸟

旧 `ebird_classid_mapping.json`（`6f342a3d^`）存在两类确定性错误：

- **42 个模型类别被映射为 `ostric2`（鸵鸟）**——疑为生成时的默认值；反查字典后它们永远进不了候选集。
- 大量 eBird 分类更新前的旧代码与当前清单代码不一致（如 `yelwar` vs `yelwar1`）。

按当前 eBird 分类表（`/v2/ref/taxonomy/ebird`）归并到物种级后的丢失数：

| 省州 | 物种数 | 旧映射丢失 | 用 `avilist_map.cornell_code` 重映射后丢失 |
|---|---|---|---|
| 新南威尔士 | 619 | 31（褐刺嘴莺、紫冠吸蜜鹦鹉、条纹刺嘴莺…） | 16 |
| 昆士兰 | 699 | 38 | 22 |
| 加州 | 897 | 22 | 11 |
| 北京 | 522 | 17（黄腹山雀、蓑羽鹤、金眶鸻…） | 12 |
| 云南 | 903 | 26 | 20 |

重映射后仍丢的，根因是**模型用旧学名**而 `avilist_map` 该行 `model_class_id` 为空，例如：
`Thinornis dubius`（eBird）↔ 模型 `Charadrius dubius`（金眶鸻）、`Anarhynchus mongolus`、
`Periparus venustulus`、`Glareola isabella`、`Butorides atricapilla`。需要同物异名解析（§4.2）。

### 2.3 许可 / Licensing

eBird API 条款禁止「以**原始格式**公开再分发 eBird 数据」，该禁令与是否商业无关（管的是格式）；
条款允许传递派生数据集，前提是附带同样的条款。旧版打包原样的 `species_list_*.json`（speciesCode 数组）
落在禁令内。本设计**只存储映射后的模型类别编号**，不存 eBird 原始代码，属派生数据；关于页附 eBird 署名与条款说明。

来源：[eBird API Terms of Use](https://www.birds.cornell.edu/home/ebird-api-terms-of-use/)

### 2.4 `reverse_geocoder` 在打包版中从未存在

`bird_identifier.py:65` 的 `_resolve_country_code_from_gps` 依赖 `reverse_geocoder`，但它**不在任何
requirements 文件中**，`/Applications/SuperPicky.app` 内也找不到。导入失败被静默吞掉 → 打包版
`photo_country_code` 恒为 None：GPS 照片的国家级回退实际用的是手选国家。

该库最后发布于 2016 年，只有源码包，原理是「最近的千人以上城市」，在省界与海岸附近会判错。
新设计要求 GPS → 省州，必须换成可打包、离线、按真实边界判断的实现（§5）。

附带核实：`bird_database_manager.get_gbif_rarity_by_class_id` 按国家优先查的
`gbif_rarity_by_country` 表在 `bird_reference.sqlite` 中**不存在**，始终回退全球分数。
因此修好 GPS 判国**不会**改变罕见度数值。

### 2.5 老配置残留的省州代码导致无过滤

4.6 保留了 `birdid_region_code`，而 `bird_identifier.py:1153` 以
`region_code or country_code or photo_country_code` 作为 L4 国家代码。老用户存的 `AU-NSW` 在
`country_species` 表查不到 → L4 被跳过 → **无 GPS 照片直接落到不过滤**。本设计让这类值重新有效（§6.3）。

### 2.6 边界数据 / Boundary data

Natural Earth（公有领域）`ne_10m_admin_1_states_provinces` 与 `ne_50m_admin_0_countries` 实测：

- admin-1 中 CN 32 条 / AU 12 条 / US 51 条，含 `iso_3166_2`、`name_zh`。
- **中国用 ISO 新式字母码**（`CN-BJ`），eBird 用数字码（旧数据 `CN-11`）→ 需对照表（GB/T 2260）。
- `name_zh` 夹杂繁体（「內布拉斯加州」「傑維斯灣地區」）→ 需中文名覆盖表。
- AU 有多条非州级要素：`AU-X02~` 杰维斯湾、`AU-X03~` 麦夸里岛、`AU-X04~` 阿什莫尔；豪勋爵岛标为 `AU-NSW`。
  CN 有 `CN-X01~` 西沙群岛。这些要素的省州归属需显式规定（§5.2）。
- admin-0 50m 中法国、挪威的 `ISO_A2` 为 `-99`，须改用 `ISO_A2_EH`。
- 按 0.01°（约 1 km）量化并去除连续重复点：三国 admin-1 11.2 万点、全球 admin-0 9.9 万点，
  原始坐标合计约 1.7 MB（zlib 后约 0.68 MB）。

---

## 3. 设计决策 / Decisions（已与用户确认）

| # | 决策 | 备注 |
|---|---|---|
| D1 | 数据源全部改为 eBird，重新拉取最新清单 | 用户提供自己的 API key，构建时经环境变量传入 |
| D2 | 省州级覆盖中国、澳洲、美国；国家级覆盖全球 | |
| D3 | **有 GPS 的照片只按 GPS 所在的 eBird 省州/国家过滤**，不再使用网格 | 见下方取舍记录 |
| D4 | 三国以外有 GPS 的照片只按 eBird 国家级过滤 | |
| D5 | 删除 GBIF 网格库及其脚本 | 安装包净减约 31–33 MB |

**D3 取舍记录**：设计阶段实测，有 GPS 时按省州过滤比现有网格 L1 宽 1.5–2.5 倍
（悉尼 370→610、西双版纳 394→884、洛杉矶 365→896）；「网格 ∩ eBird 清单」方案可在保留局部精度的同时
剔除每格 4–30 个 GBIF 噪声种。用户知情后选择口径统一为 eBird 省州。实施后如 GPS 照片误识别
仍集中出现，「网格 ∩ 清单」是首个备选改进方向（需恢复网格数据）。

---

## 4. 数据构建 / Data Build

脚本 `scripts_dev/build_ebird_regions.py`，在开发者本机运行，产物提交入库。

### 4.1 抓取 / Fetch

| 用途 | eBird API | 是否需要 key |
|---|---|---|
| 分类表 | `GET /v2/ref/taxonomy/ebird?fmt=json` | 否（已实测 200） |
| 国家列表 | `GET /v2/ref/region/list/country/world` | 是 |
| 省州列表 | `GET /v2/ref/region/list/subnational1/{CN,AU,US}` | 是 |
| 物种清单 | `GET /v2/product/spplist/{regionCode}` | 是（无 key 实测 403） |

- key 仅从环境变量 `EBIRD_API_KEY` 读取，缺失时立即报错退出；不写入任何文件与日志。
- 请求量约 250（国家）+ 约 90（省州），串行 + 失败重试即可；原始响应只缓存在
  `scripts_dev/.ebird_cache/`（加入 `.gitignore`），不进仓库、不进安装包。
- 已用 key 实测（2026-09-16）：国家 253 个；中国省级 31 个，数字码 `CN-11`…`CN-65`；
  台湾/香港/澳门为独立国家代码 `TW/HK/MO`；澳洲 8 个；美国 51 个（含 `US-DC`）；
  `AU-NSW` 清单 644 项（2025-09 旧数据 637 项）。
- key 沿用旧版 `ebird_country_filter.py` 中的那一个（用户决定），运行时经环境变量传入，仓库中不出现。

### 4.2 归并与映射 / Normalize & map

对每个区域清单中的每个 speciesCode：

1. **归并到物种级**：分类表 `category == "species"` 直接保留；`issf` / `form` / `domestic` 等若有
   `reportAs` 且目标为物种，则归并到该物种；`hybrid` / `spuh` / `slash` / `intergrade` 剔除。
2. **映射到模型类别编号**（得到集合，允许一对多），对**整个分类表**的物种按顺序取第一个非空结果：
   1. 人工覆盖 `scripts_dev/ebird_overrides.json` 的 `map`（人工结论优先于自动结果）；
   2. `avilist_map.cornell_code == code` 且 `model_class_id` 非空；
   3. eBird `sciName` 精确匹配 `BirdCountInfo.scientific_name`；
   4. GBIF v2 species match 把 eBird `sciName` 解析为物种级 accepted key，命中 `gbif_rarity_100.specieskey`；
   5. **同科种加词**：在前四步未占用的模型类别中，找「同科 + 种加词词干（去阴阳性词尾）相同」且双方唯一的配对。
      科名取 eBird 分类表的属→科关系，缺失时用 `bird_ioc`。设计阶段实测 111 例全部为属名调整且配对正确；
      不加同科约束会出现 `Crithagra striatipectus` ↔ `Nystalus striatipectus` 这类跨科错配。
3. 未命中的物种记入报告。

### 4.3 构建卡口 / Build gate

模型 10,964 类、eBird 物种 11,167 个，天然存在模型没有的物种，无法自动区分「模型没有」与「漏接」，
因此卡口改为可判定的规则：

- **硬失败**：哨兵物种缺失——`SENTINELS` 中列出的（区域, 模型学名）必须出现在该区域候选集中
  （取自 §2.2 旧版误杀案例：褐刺嘴莺、红耳绿鹦鹉、澳洲燕鸻、黄腹山雀、蓑羽鹤、金眶鸻、蒙古沙鸻、黄林莺）。
- **硬失败**：任一模型类别被映射到超过 3 个 eBird 物种，且不在 `allow_multi` 白名单中。
- **硬失败**：任一区域物种数为 0；任一中澳美省州没有边界；`CN`/`AU`/`US` 没有国家边界。
- **硬失败**：国家中文名缺失（`tools/country_names.py` 未收录），或省州中文名含繁体字。
- **只报告**：未映射物种清单（代码、学名、英文名、出现在多少个区域）与第 5 步配对清单，写入
  `scripts_dev/.ebird_cache/mapping_report.txt` 供人工复核，发现错配就加进 `map` 覆盖。

### 4.4 产物 / Output — `birdid/data/ebird_regions.db`

```sql
-- 区域：国家与三国省州 / Regions: countries and CN/AU/US subnational1
CREATE TABLE regions (
    code          TEXT PRIMARY KEY,  -- eBird 区域代码，如 AU、AU-NSW、CN-11
    parent        TEXT,              -- 省州的上级国家代码；国家为 NULL
    name_en       TEXT NOT NULL,
    name_zh       TEXT NOT NULL,
    species_count INTEGER NOT NULL   -- 映射后的模型类别数
);

-- 区域物种：只存模型类别编号（派生数据，不含 eBird 原始代码）
-- Region species: model class ids only (derived data, no raw eBird codes)
CREATE TABLE region_species (
    region   TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    PRIMARY KEY (region, class_id)
) WITHOUT ROWID;

-- 边界：量化后的多边形环，供离线点定位（§5）
-- Boundaries: quantized polygon rings for offline point location (see §5)
CREATE TABLE boundaries (
    region  TEXT NOT NULL,        -- 与 regions.code 对应
    level   INTEGER NOT NULL,     -- 0 = 国家，1 = 省州
    ring_id INTEGER NOT NULL,
    min_lat REAL NOT NULL, max_lat REAL NOT NULL,   -- 外包框，用于预筛
    min_lon REAL NOT NULL, max_lon REAL NOT NULL,
    is_hole INTEGER NOT NULL,     -- 1 = 内环（洞）
    coords  BLOB NOT NULL,        -- zlib(int32 小端 [lon1e2, lat1e2, ...])
    PRIMARY KEY (region, ring_id)
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
-- taxonomy_version, fetched_at, ebird_terms_url, attribution,
-- boundaries_source, builder_version
```

预计体积 2–4 MB（边界约 1.7 MB 原始坐标，经 zlib 约 0.7 MB；清单约 250×平均数百行）。
验收上限 6 MB（§8）。

---

## 5. 离线定位 / Offline Location — `birdid/region_locator.py`

替换 `reverse_geocoder`（§2.4）。纯 Python，不引入 shapely 等新依赖。

### 5.1 接口

```python
def locate(lat: float, lon: float) -> LocateResult:
    """返回 (country_code, subnational_code)，两者都可能为 None。"""
```

算法：

1. 取外包框包含该点的环（SQL 预筛），做射线法点在多边形内判断（含洞）。
   先查 level 0 得国家；国家属于 CN/AU/US 时再查 level 1 得省州。
2. 点不落在任何国家多边形内（海上/岸边/量化误差）时：取外包框外扩后的候选环，
   计算点到线段的最短大圆近似距离，**200 海里（约 370 km）内**取最近的国家，省州同理。
   选 200 海里依据：专属经济区宽度；实施时用一条已知离岸观鸟样本（如澳洲东岸远洋）核实
   eBird 将其归入相邻省州，若 eBird 实际做法不同则按实测调整该常量。
3. 超出容差 → 两者均为 None，调用方按「无 GPS」处理（§6.1）。
4. 结果按坐标四舍五入到 0.01° 做 LRU 缓存（同一批照片多在同一地点）。
5. 边界数据加载为进程级单例，沿用 `config.get_lazy_registry()`；数据库路径解析沿用
   现 `geo_filter.default_db_path()` 的三分支（开发 / frozen+`_MEIPASS` / frozen+Windows install-scoped）。

### 5.2 代码对照 / Code reconciliation（构建期完成，运行时只读 eBird 代码）

- **中国**：Natural Earth `CN-BJ` 等字母码按 GB/T 2260 对照到 eBird 数字码（`CN-11` 北京…`CN-65` 新疆）。
  对照表写在构建脚本中，构建时校验 31 个省级单位两侧一一对应。
- **台湾/香港/澳门**：以 admin-0 的 `TW/HK/MO` 作为国家级处理，不作为中国省级（eBird 同为独立国家代码，§4.1 已实测）。
- **西沙群岛** `CN-X01~`：构建期不单独生成省级边界（该要素不对应任何 eBird 省级代码）；运行时定位器
  先在国家层离岸就近归 `CN`，再在中国省级环里离岸就近归 `CN-46`（海南）。这与 eBird 一致：
  三沙市隶属海南省，eBird 的西沙记录归在 `CN-46`。
- **澳洲**：豪勋爵岛 → `AU-NSW`（Natural Earth 本已如此）；杰维斯湾 `AU-X02~` → `AU-ACT`；
  麦夸里岛 `AU-X03~` → `AU-TAS`；阿什莫尔 `AU-X04~` 不生成澳洲省级边界（国家级归 `AC`，见下条）。
  实施时逐条用 eBird 区域核实，以 eBird 的实际归属为准。
- **海外领地**（终审修复）：eBird 把法属圭亚那 `GF`、瓜德罗普 `GP`、马提尼克 `MQ`、留尼汪 `RE`、马约特 `YT`、
  克利珀顿 `CP`、荷兰加勒比区 `BQ`、斯瓦尔巴（含扬马延、熊岛）`SJ`、布韦岛 `BV`、托克劳 `TK`、圣诞岛 `CX`、
  科科斯 `CC`、阿什莫尔和卡捷 `AC`、直布罗陀 `GI`、美国本土外小岛屿 `UM` 当作独立国家并有独立清单，
  但 admin-0 50m 把它们并进宗主国或根本没有（如中途岛）。处理：领地几何统一取自 Natural Earth
  `ne_10m_admin_0_map_units`（按 `GU_A3` 显式对照），并用一张小外包框表把 50m 宗主国要素中落在领地内的
  多边形分块整块剔除，保证领地点不会同时被宗主国包含。构建卡口：有物种清单的区域必须有边界环，
  仅允许 `XX`（公海，非地理区域）与 `CS`（珊瑚海群岛，Natural Earth 多边形在 0.01° 量化下塌成单点）例外；
  剔除框未命中任何分块或 map_units 缺单元也报错。
- **美国**：51 条一一对应，`US-DC` 在 eBird 中为独立代码（§4.1 已实测）。
- 国家级用 `ISO_A2_EH`（§2.6）。
- 中文名：国家沿用 `tools/country_names.py`（补齐 eBird 有而现表缺的代码）；三国省州用 Natural Earth `name_zh`
  加覆盖表修正繁体与不规范名称；构建卡口要求 `name_zh` 全部非空。

---

## 6. 过滤链路 / Filter Pipeline

### 6.1 候选层

`birdid/geo_filter.py` 重写为 eBird 区域过滤，保留模块名以减少调用方改动；
`iter_candidates` 签名改为：

```python
def iter_candidates(
    self,
    gps_region: LocateResult | None,     # 由 region_locator.locate 得到；无 GPS 或无法定位为 None
    manual_country: str | None,
    manual_subnational: str | None,
) -> Iterator[tuple[set[int] | None, str]]:
```

| 情形 | 层序 |
|---|---|
| 有 GPS 且可定位 | GPS 省州（仅 CN/AU/US）→ GPS 国家 → 不过滤 |
| 无 GPS，或 GPS 无法定位 | 手选省州 → 手选国家 → 不过滤 |
| 两者都缺 | 不过滤 |

- 层标签：`region_subnational` / `region_country` / `none`，替换现有 L1–L5 五个常量。
- 空集合层跳过（与现实现一致，避免屏蔽全部类别）。
- GPS 与手选同时存在时 GPS 优先（照片可能拍于他处）。
- `_identify_with_tiers`（`bird_identifier.py:1003`）逐层放宽的机制不变。注意：`predict_bird` 只有在 top-100 中
  **无一**落在候选集内时才返回空，因此放宽基本只在候选集完全不含相近种时发生；候选集的准确性而非放宽机制决定效果。

### 6.2 `identify_bird` 改动

- `_resolve_country_code_from_gps` 改为调用 `region_locator.locate`，`gps_info` 同时写入 `country_code` 与
  `subnational_code`。`photo_country_code`（供罕见度）取 `locate` 的国家结果。
- 删除 `effective_region = region_code or country_code or photo_country_code` 的混用写法（§2.5 根因），
  改为把 GPS 定位结果与手选国家/省州分开传给 `iter_candidates`。
- `geo_info` 字段：`enabled` / `tier` / `species_count` / `region_code`（实际命中层所用区域代码）。
  消费方 `birdid_cli.py:154`、`birdid_server.py:379-411` 同步改键名与告警判断
  （告警条件：命中层为 `region_country` 且该国家属于 CN/AU/US，或 `none`）。

### 6.3 配置 / Config

- 键不变：`birdid_use_geo_filter`、`birdid_country_code`、`birdid_region_code`、`birdid_selected_country`、
  `birdid_selected_region`。
- 读取时校验：`birdid_region_code` 不在 `regions` 表中，或其 `parent` 与 `birdid_country_code` 不一致 → 视为「整个国家」。
  不写回配置（避免启动时静默改用户文件），只影响本次运行语义。
- 老配置中的 `AU-NSW` / `US-CA` / `CN-11` 等值与 eBird 代码同构，升级后自动恢复生效。

---

## 7. 界面、署名与清理 / UI, Attribution & Cleanup

### 7.1 界面

- `core/region_data.load_regions_data()` 改读 `ebird_regions.db`：国家列表来自 `regions WHERE parent IS NULL`，
  CN/AU/US 的 `has_regions=True` 且 `regions` 填省州（`code`/`name`/`name_cn`/`species_count`），
  沿用函数现有返回结构，UI 读取逻辑不需重写。
- `ui/settings_center.py` 识鸟页：地区行在选中 CN/AU/US 时显示并填充省州，其他国家隐藏（现有
  `_populate_bid_regions` 显隐机制保留）。提示文案改为「有 GPS 的照片按拍摄地自动判断省州/国家；手选只对无 GPS 照片生效」。
- `ui/birdid_dock.py`：区域下拉同上；「数据源」复选框文案去掉 GBIF 字样。
- 过滤状态文案（`birdid.geo_tier_*`）改为三条：按省州 / 按国家 / 未过滤，中英文同步。

### 7.2 署名

- 关于页 `settings_center.py:2737` 的 `_geo_attribution_text()` 改读 `ebird_regions.db.meta`：
  「鸟类区域清单 - eBird（康奈尔鸟类学实验室），获取于 {fetched_at}，依 eBird API 使用条款提供；
  行政区边界 - Natural Earth（公有领域）」。GBIF 罕见度署名保持不变。

### 7.3 删除 / Remove

| 项 | 说明 |
|---|---|
| `birdid/data/geo_distribution.db` | 35 MB，git 跟踪文件 |
| `birdid/data/land_cells.json` | 网格枚举源 |
| `scripts_dev/build_geo_distribution.py` / `calibrate_geo_threshold.py` / `validate_geo_filter.py` | GBIF 网格管线 |
| `birdid/geo_filter.py` 中网格相关代码 | `cell_id_for`、`_neighbour_cells`、`_tier1_filter` 等 |
| `bird_identifier.py` 的 `reverse_geocoder` 分支与 `_RG_*` 全局量 | 由 `region_locator` 取代 |
| `locales` 中 `logs.geo_cell_failed` 等网格专用键 | |
| `test_geo_filter.py` / `test_geo_filter_wiring.py` | 改写为 eBird 区域版本 |

`tools/country_names.py` 保留并按 eBird 国家列表补齐。

### 7.4 打包

- `SuperPicky.spec:57`、`SuperPicky_win64.spec:58` 整目录打包 `birdid/data`，新库自动包含，无需改动。
- `hiddenimports` 新增 `birdid.region_locator`（`birdid.geo_filter` 保留）。
- `SuperPicky_full.spec` 实施时确认其数据来源。
- 按 CLAUDE.md，`.spec` 改动后做打包启动冒烟。

---

## 8. 验收标准 / Acceptance Criteria

1. **映射完整**：构建卡口全部通过；新南威尔士、昆士兰、北京、云南、加州 §2.2 所列误杀种
   （褐刺嘴莺、紫冠吸蜜鹦鹉、黄腹山雀、蓑羽鹤、金眶鸻、蒙古沙鸻、澳洲燕鸻…）全部出现在对应省州候选集中。
2. **无 `ostric2` 式污染**：没有模型类别映射到超过 3 个 eBird 物种。
3. **定位正确**：单测覆盖以下坐标得到预期代码——北京、上海、乌鲁木齐、拉萨、香港（HK）、台北（TW）、
   堪培拉（AU-ACT）、霍巴特（AU-TAS）、豪勋爵岛（AU-NSW）、洛杉矶（US-CA）、华盛顿特区、安克雷奇（US-AK）、
   檀香山（US-HI）、冰岛、法国、挪威；省界两侧各 5 km 的点对（至少 3 组）；
   离岸 50 km 点归入相邻省州；离岸 500 km 点返回 None。
4. **老配置生效**：`birdid_region_code="AU-NSW"`、无 GPS 的照片命中 `region_subnational` 层（§2.5 回归）。
5. **冰岛回归**：433 张法罗群岛/冰岛照片重跑，与 4.6 结果逐张比对；角海鹦、小企鹅、蓝脚鲣鸟仍为 0，
   有鸟种识别数不低于 4.6（283/433）。如不达标，停下与用户讨论，不自行调整口径。
6. **用户案例**：用户提供的无 GPS 误识别照片复测，逐张记录前后结果。
7. **体积**：`ebird_regions.db` ≤ 6 MB；安装包相对 4.6.3 净减 ≥ 29 MB。
8. **性能**：`locate` 冷启动（含加载边界）≤ 500 ms；缓存命中后单次 ≤ 1 ms；未命中单次中位 ≤ 20 ms。
9. **清理彻底**：全仓库无 `geo_distribution`、`land_cells`、`reverse_geocoder`、`cell_id_for` 的功能性引用；
   改动 Python 文件均通过 `py_compile`；全量测试无新增失败。
10. **打包冒烟**：macOS 打包产物含 `ebird_regions.db`、不含 `geo_distribution.db`；启动至主窗口，
    带 GPS 的 JPEG 拖入识鸟面板，日志显示命中 `region_subnational` 且省州代码正确。

---

## 9. 风险与未决 / Risks & Open Items

| 风险 | 缓解 |
|---|---|
| 有 GPS 照片候选比网格宽 1.5–2.5 倍，误识别可能不降反升 | 用户已知情选择（D3）；验收 5/6 实测；「网格 ∩ 清单」为既定备选 |
| 有 GPS 照片出错的根因未知 | 本设计不做推测性修改；收集案例后单独诊断 |
| eBird 全时段清单含已审核迷鸟，无记录数，无法区分常见/偶见 | 与旧版行为一致；如需分层再议（可叠加 GBIF 省州记录数） |
| eBird 离岸归属规则与 200 海里假设不符 | §5.1 实施时用样本核实后再定常量 |
| 中国照片 GPS 可能为 GCJ-02 偏移（约数百米） | 仅在省界数百米内有影响，可忽略 |
| eBird 分类每年更新，代码变化导致映射漂移 | 构建卡口兜底；每年重跑脚本一次 |
| `reverse_geocoder` 移除后，其他依赖 `gps_info.country_code` 的地方 | 实施时全仓库 grep 消费点并改读 `locate` 结果 |
