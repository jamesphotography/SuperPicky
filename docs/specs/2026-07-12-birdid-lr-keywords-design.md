# 识鸟结果写入 Lightroom 关键字 设计 / Bird ID → Lightroom Keywords Design

日期：2026-07-12 ｜ 分支：dev ｜ 状态：已获用户批准（Paul 反馈 P1-1）

## 背景 / Background

外部用户 Paul 提问「Could the bird ID be stored in a Lightroom keyword tag?」。
James 采纳：识鸟高置信度结果除写 `XMP:Title` 外，同步写入标准关键字
`XMP-dc:Subject`（Lightroom Keywords），便于 LR 按关键字筛选鸟种。

## 已审定决策 / Approved Decisions

- **内容**：跟随界面语言单写一个鸟名关键字（与 Title 行为一致；`bird_title`
  已按语言选好中/英文名）。低置信度不写（与 Title/分目录同门槛）。
- **开关**：`advanced_config.birdid_write_keywords`（默认 `True`）+
  设置中心识鸟页复选框「识别后写入照片关键字（Lightroom Keywords）」。
- **范围**：仅主处理流程（photo_processor 识鸟收尾）；LR 插件路径
  （birdid_server）列为后续跟进，不在本期。

## 写入语义 / Write Semantics（关键，已实验验证）

`meta_item` 新增字段 `'keywords': List[str]`，语义为「**确保这些关键字存在**」
（merge-add），绝不清除用户已有关键字：

1. 读取文件现有 `XMP-dc:Subject` 列表（经 ExifTool 读进程，`-j`）。
2. Python 侧合并去重（保序：现有在前，新增在后）。
3. 无新增 → 跳过写入（幂等，重跑零成本）。
4. 有新增 → 整表写回：`-sep ";;"` + `-XMP-dc:Subject<=UTF-8临时文件`
   （临时文件内容为 ";;" 连接的完整列表）。

**实验结论（2026-07-12 本机 exiftool 验证）**：
- `-XMP-dc:Subject+=<file` **不可用**——exiftool 不支持列表追加读文件，
  `<file` 会被当作字面量写入。
- `-sep ";;" -XMP-dc:Subject<=file` **可用**——UTF-8 文件整表赋值，
  中文逐值正确（`["UserKeyword","白胸鸲鹟","White-breasted Robin"]`）。
- 整表替换必须先读后写，故为 read-merge-write；同一文件的写串行经过
  写队列，无并发覆写风险。

符合 CLAUDE.md 铁律：中文元数据必须经 UTF-8 临时文件，禁止内联 CLI 值。

## 组件改动 / Components

1. **`tools/exiftool_manager.py`**：
   - 新私有方法 `_read_subject_list(file_path) -> List[str]`（经读进程
     `-j -XMP-dc:Subject`；文件不存在/无标签返回 `[]`）。
   - 新私有方法 `_merge_keywords_value(file_path, keywords) -> Optional[str]`：
     返回合并后的 ";;" 串；`None` 表示已全部存在无需写。
   - 三条写入路径支持 `keywords` 字段：常驻进程批量路径（非 ARW）、
     `_write_metadata_arw`（auto 回退 sidecar）、`_write_metadata_xmp_sidecar`。
   - **sidecar 读取优先级**：ARW/sidecar 路径读现有关键字时，`.xmp` 侧车
     存在则读侧车，否则读 RAW 本体（LR 惯例侧车优先）。
2. **`core/photo_processor.py`**（识鸟收尾 :1300 一带）：开关开启时在写
   Title 的同一个 `meta_item` 追加 `'keywords': [bird_title]`。
3. **`advanced_config.py`**：`DEFAULT_CONFIG` 加 `"birdid_write_keywords": True`
   + `@property birdid_write_keywords` + `set_birdid_write_keywords(bool)`。
4. **`ui/settings_center.py`**：识鸟页「自动识鸟」区加复选框，即改即存
   （或随 Done 保存，跟随该页现有模式）。
5. **i18n**：`settings.birdid_keywords_label` 中英两键。

## 测试 / Testing

- `_merge_keywords_value` 单测：中文合并、去重、保留用户关键字、全存在
  返回 None。
- 端到端：临时 JPG 真实 exiftool 写→读回（含中文、二次写幂等）。
- 开关落盘往返（temp AdvancedConfig）。
- 变更 py 文件 `py_compile`。

## 不做 / Out of Scope

- LR 插件路径（birdid_server）写关键字——后续跟进项。
- 层级关键字（`XMP-lr:HierarchicalSubject`）、拉丁学名——YAGNI。
- 已有照片的批量补写工具。
