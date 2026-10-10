# 鸟名目录 API

现有 BirdID 服务同一地址/端口新增两个 GET 路由。`POST /recognize`、健康检查及 EXIF 接口的参数、返回协议保持不变。无需识鸟模型或 Qt，也不上传照片；目录及参考库按每请求独立只读连接访问。

## 列表与搜索

`GET /birds/search?q=白头鹎&limit=100&offset=0`

- `q` 可省略/留空，分页列出鸟名；支持中文、英文、拉丁学名、带/不带声调拼音和缩写，不区分英文字母大小写，拼音可连写。
- `limit` 默认 100，范围 1–500；`offset` 默认 0，必须非负；查询最长 200 字符。
- `version_id` 可选；默认沿用鸟名面板的最新导入版本（`created_at` 降序，同时间按 ID 降序），并非按 IOC 版本数字大小。当前库默认为 IOC 14.2。
- 后续分页应传回响应里的 `version_id`；鸟种选择应保存 `bird_id` 和 `version_id`，避免同名或不同分类版本混淆。
- `%`、`_` 等输入按文字搜索，不作为 SQL 通配符。完全匹配名字优先，随后按中文名和 ID 排序。

```json
{
  "success": true,
  "version_id": 10,
  "version_name": "IOC 14.2",
  "total": 1,
  "offset": 0,
  "limit": 100,
  "results": [{
    "bird_id": 126247,
    "version_id": 10,
    "cn_name": "白头鹎",
    "en_name": "Light-vented Bulbul",
    "scientific_name": "Pycnonotus sinensis",
    "pinyin_name": "bái tóu bēi",
    "pinyin_plain": "bai tou bei",
    "abbreviation": "BTB"
  }]
}
```

## 详情

`GET /birds/detail?bird_id=126247&version_id=10`

也支持精确名字：`GET /birds/detail?name=白头鹎&version_id=10`。`name` 匹配中文、英文或学名；与 `bird_id` 二选一。同一版本存在多个同名记录时返回 409，需从列表选择 ID，不能随意取第一只。

响应为 `{"success": true, "bird": {...}}`；`bird` 包含列表字段、`version_name`，以及：

| 字段 | 含义 |
| --- | --- |
| `gbif_rarity_100` | 全球 GBIF 稀有度，0–100，越高越稀有，0 有效；缺失为 null |
| `rarity_scope` | 固定 `global`；与按照片国家过滤的识鸟分数可能不同 |
| `rarity_label_zh` / `rarity_label_en` | SuperPicky 稀有度分档名称，缺失为 null |
| `iucn_category` | IUCN 编码，例如 LC、NT、VU、EN、CR；缺失为 null |
| `china_protection_level` | 当前资料库没有中国国家保护等级数据，返回 null；不可由 IUCN 推算 |
| `description` | 参考库的中文简介，无资料为空字符串 |

稀有度按学名直查，未命中经模型类别 ID 回退，并沿用 `core.rarity_tier` 的学名覆盖规则。IUCN 与简介按同一学名对应的参考库记录获取。名称来自 `ioc/birdname.db`，声调来自 `ioc/pinyin_toned.json`，补充信息来自 `birdid/data/bird_reference.sqlite`。未找到鸟种时不模糊猜测详情；参考库缺失或损坏时明确报错，不将故障当成全空字段。

## 错误与验证

错误统一为 `{"success": false, "error": "..."}`：400 参数错误、404 鸟种/版本不存在、409 鸟名有歧义、503 数据库不可用。搜索无匹配仍返回 200、`total: 0` 和空列表。

实现：[BirdCatalog](../tools/bird_catalog.py)、[路由](../tools/bird_catalog_routes.py)。现有 [birdid_server.py](../birdid_server.py) 注册蓝图，源码和打包服务共用。验证：项目虚拟环境运行 `python -m unittest test_bird_catalog_api -v`，覆盖并发只读、中文/拼音搜索、分页、空值/零值、缺库及原识鸟协议；`test_birdid_server_light_import.py` 验证原服务轻量启动。
