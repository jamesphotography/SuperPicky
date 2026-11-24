# 借用地点字段存分数：保护真实地点（设计）

日期：2026-11-17　状态：已批准（2026-11-17）

## 为什么

SuperPicky 把锐度、美学、对焦写进 XMP 的城市、省份、国家（`photoshop:City/State/Country`）。
这是用户在 Lightroom 里按分数排序、在 DxO PhotoLab 的「IPTC 图像信息」里看到分数的唯一办法，
但它会冲掉照片里的真实地名（带 GPS 的相机、在 Lightroom 里填过地点的照片），一直被用户吐槽；
重置时还会把真实地名一起清空。

## 目标

1. 写入时：照片里已有真实地名，就不写锐度、美学、对焦这三项（其余字段照写），日志里说明。
2. 重置时：只清空 SuperPicky 写的那种格式的值，真实地名保留。
3. 设置页把「城市 = 锐度、省份 = 美学、国家 = 对焦状态」写清楚，告诉用户要保留真实地点可以关掉。

## 非目标

- 不做 Lightroom 插件、不写自有 XMP 命名空间、不加分档关键字（见「考虑过的做法」）。
- 不改默认开关；没有真实地名的照片行为完全不变。
- PhotoPicky 不用改：`Sources/PhotoPicky/Library/ToolMetadata.swift` 已能识别并剥离 SuperPicky 的值。

## 考虑过的做法

| 做法 | 结论 |
|---|---|
| 保留城市/省份/国家，只保护真实地名（**选定**） | Lightroom 能排序、DxO 能显示；代价是有真实地名的照片无法按锐度排序 |
| 自有 XMP 命名空间 + Lightroom 插件 | DxO、Bridge 都看不到（实测 DxO PhotoLab 9）；插件字段在 Lightroom 网格里也不能排序 |
| 分档关键字（如 `SuperPicky-锐度-600+`） | DxO 能搜到（实测），但不能排序，且往用户的关键字里塞词，会招来另一种吐槽 |

## 怎么判断「是不是 SuperPicky 写的」

与 PhotoPicky 的 `ToolMetadata.isToolWritten` 保持同一口径：三个字段里只要有一个是纯数字，
或国家是对焦状态（BEST/GOOD/BAD/WORST，或旧版中文 精焦/合焦/失焦/偏移/脱焦），这一组就是工具写的。
三个都为空也视为可写。其余（有任何非空、且不是上述格式）都算**真实地名**。

## 读取范围

写入或重置前，按批读取每张照片现有的地点：`XMP-photoshop:City/State/Country` 与
`IPTC:City/Province-State/Country-PrimaryLocationName`；RAW 走边车写入、或旁边已有 `.xmp` 边车时，
边车里的同名字段也要读（Lightroom 给 RAW 填的地点在边车里）。任一来源是真实地名即受保护。

## 落点

- `tools/exiftool_manager.py`：所有写入都经过的字段过滤处（`_apply_field_switches` 的三处单张写入 +
  `batch_set_metadata` 入口）对受保护的照片去掉 `sharpness` / `nima_score` / `focus_status`；
  重置清除参数对受保护的照片去掉 City/State/Country 三项。判断规则放进一个无 Qt 依赖的小模块，便于测试。
- 日志：每批汇总一行「N 张照片已有地点信息，未写入锐度/美学/对焦」（三语）。
- 设置中心元数据字段那一节加说明文字（三语）。

## 验证

- 判断规则单测（数字、对焦词、中文旧值、空值、真实中英文地名）。
- 用 ExifTool 在临时文件上做写入 + 读回：中文真实地名（如「布里斯班」）写入与重置后都原样保留；
  SuperPicky 的旧值照常被覆盖、被清空；JPG 内嵌与 RAW 边车两种情况都覆盖。

## 留给实现者

- 批量读取用写进程还是读进程（项目已拆分读写两个常驻 ExifTool，见 exiftool-rw-split）由实现决定，
  以不阻塞写入为准。
- 受保护照片的题注里是否提示「未写入分数」：不做，日志汇总即可。
