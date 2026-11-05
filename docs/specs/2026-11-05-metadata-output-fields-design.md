# 元数据输出字段可选（#153）设计 / Configurable metadata output fields

日期 / Date: 2026-11-05　来源 / Source: issue #153（theggs）

## 为什么 / Why

SuperPicky 往每张照片写 12 个字段，其中 6 个借用了 Lightroom 里原本有用途的位置
（城市=锐度、省/自治区=美学分、国家/地区=对焦状态、知识产权题材=IUCN、事件=罕见度、
附加模特信息=颜值）。用这些字段筛选排序的人（维护者本人常用：城市、省/自治区、星级、旗标）
需要它们；只要星级和旗标的人每次都得用 LR 元数据预设清理，色标还清不掉。

SuperPicky writes 12 fields per photo; six borrow Lightroom fields that have real
meanings. Users who don't need them have to clean them up after every cull.

## 目标 / Goals

- 12 个字段各自一个开关，**默认全开**（升级后行为不变）。
- 「一键精简」按钮：只勾选 星级、旗标、题注、锐度（城市）、美学分（省/自治区）这 5 项，
  其余 7 项取消（结果严格等于这 5 项）。
- 「全部勾选」按钮：回到默认。
- 关掉的字段，SuperPicky 的所有自动写入路径都不再写：主流程选片、浏览器改星级（含题注）、
  改鸟种 / 标记无鸟（标题、关键字）、命令行批量写。
- **重置只清开着的字段**（维护者 2026-11-04 拍板）：关掉的字段可能放的是用户自己的真实数据。
  代价：开着时写过、后来关掉的旧值，重置不再清。

## 非目标 / Non-goals

- 不追溯清除已经写进照片的旧值。
- 不改程序自身功能：浏览器、报告、统计读 report.db，与照片里写不写无关。
- 不另存「精简模式」：按钮只是批量改勾选，配置只存 12 个开关。
- 不改 `set_metadata`（底层通用写标签接口）：LR 插件的「写标题 / 写题注」和
  `birdid_cli --write-exif` 的单文件写入是用户显式发出的写入命令，不受开关约束。
  （`birdid_cli` 的 `batch_set_metadata` 路径会自然受开关约束，这是可接受的不一致。）

## 字段表 / Field table

| id | 界面（zh_CN） | 写入的 item 键 | 写入标签 | 重置时清除 |
|---|---|---|---|---|
| rating | 星级评分（评级） | rating | Rating | Rating |
| pick | 留用 / 排除旗标（旗标） | pick | xmpDM:Pick + Good | 同左 |
| label | 颜色标记（色标） | label | XMP:Label | 同左 |
| title | 鸟种名（标题） | title | XMP:Title | 同左 |
| caption | 评分说明（题注） | caption | XMP:Description | 同左 |
| sharpness | 锐度（城市） | sharpness | XMP:City | + IPTC:City |
| aesthetic | 美学分（省/自治区） | nima_score | XMP:State | + IPTC:Province-State |
| focus | 对焦状态（国家/地区） | focus_status | XMP:Country | + IPTC:Country-PrimaryLocationName |
| keywords | 鸟种关键字（关键字） | keywords | XMP-dc:Subject | （重置本来不清） |
| iucn | IUCN 保护等级（知识产权题材） | iucn_category | IntellectualGenre | 同左（仅侧车重置原本就清） |
| rarity | 全球罕见度（事件） | gbif_rarity_100 | iptcExt:Event | 同左（同上） |
| beauty | 鸟种颜值（附加模特信息） | aesthetic_index | AdditionalModelInformation | （重置本来不清） |

前 8 项为「核心内容」，后 4 项为「鸟种识别内容」。重置清哪些标签**沿用各重置函数现状**，
只是再按开关过滤；不借机扩大或缩小重置范围。

## 方案 / Approach（实现者的判断，已在对话中说明）

- **单表**：新增无 Qt 依赖的 `core/metadata_fields.py`，定义 12 个字段的 id、配置键、
  item 键、分组，以及精简预设。界面、配置、写入器都从这一张表取，避免多处列表漂移。
- **配置（SSOT）**：`advanced_config` 新增 11 个扁平布尔键 `metadata_write_<id>`，默认 True；
  关键字**复用现有 `birdid_write_keywords`**，老用户的选择零迁移保留。不用嵌套 dict：
  `DEFAULT_CONFIG.copy()` 是浅拷贝，嵌套可变默认值会被改脏。
- **写入器统一过滤**：`ExifToolManager` 在写入入口按开关删掉 item 里被关掉的键；
  过滤后没有可写内容的条目视为「按配置跳过」，计为成功、不调用 exiftool
  （否则 exiftool 报 nothing to do 会被计为失败）。覆盖 `batch_set_metadata`（含 sidecar 模式）、
  `_write_metadata_arw` / `_write_metadata_subprocess` / `_write_metadata_xmp_sidecar`、
  `set_rating_and_pick`（顺带把其内联拼参改为复用 `_write_metadata_subprocess`）、
  `update_species_metadata` / `clear_species_metadata`（标题按 title 开关）。
- **重置**：`reset_metadata` / `batch_reset_metadata` / `_reset_xmp_sidecar` 的清除参数按开关过滤；
  全部关掉时不调用 exiftool，直接计为成功。
- **界面**：设置中心「输出」页，在「元数据写入方式」之后新增「写入哪些字段」区：说明一行、
  两个按钮、两组勾选框；写入方式为「不写入」时整区置灰；每次勾选即时保存（与该页现有模型一致）。
  识鸟页原「写入照片关键字」勾选框移除，统一到这里（同一字段只留一个入口）。
- **三语**：zh_CN / en_US / zh_TW 文案。

## 验证 / Verification

- 写入后读回：关掉若干字段后，JPG（嵌入）、NEF（侧车）、sidecar 模式三条路径读回确认被关字段不存在、
  开着的中文值（标题、题注）无乱码。
- 重置：被关字段的已有值在重置后保留，开着的字段被清。
- 全部关掉：批量写与重置都不报失败。
- 配置往返、精简按钮结果、旧 `birdid_write_keywords=False` 的用户在新界面显示为未勾选。
- `py_compile` + 相关测试 + 全量 pytest。
