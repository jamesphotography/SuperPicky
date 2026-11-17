# 借用地点字段存分数：保护真实地点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 照片已有真实地名时，SuperPicky 不再用锐度/美学/对焦覆盖城市/省份/国家，重置也不再清掉真实地名；设置页说明这三个字段的用途。

**Architecture:** 新增无 Qt 依赖的 `core/location_guard.py` 负责「是不是 SuperPicky 写的」的判断（与 PhotoPicky `ToolMetadata.isToolWritten` 同口径）。`ExifToolManager` 在所有写入路径的字段过滤之后、所有重置路径的清除参数过滤之后，逐张读取本体与 `.xmp` 边车的现有地点，受保护的照片去掉三项分数 / 三组地点清除参数，并像 `drain_raw_embed_fallbacks` 一样记录、由 `photo_processor` 汇总一行日志。

**Tech Stack:** Python 3.13、ExifTool 常驻读写进程（`tools/exiftool_manager.py`）、PySide6（设置中心）、pytest。

**Spec:** `docs/specs/2026-11-17-location-fields-guard-design.md`

## Global Constraints

- 判断口径：三个字段里任一是纯数字（ASCII 数字，可带一个小数点），或国家是对焦词（`BEST`/`GOOD`/`BAD`/`WORST`，大小写不敏感；或中文 `精焦`/`合焦`/`失焦`/`偏移`/`脱焦`），整组视为 SuperPicky 写的；三个全空视为可写；其余视为**真实地名**。
- 读取的标签：`XMP-photoshop:City/State/Country`、`IPTC:City`、`IPTC:Province-State`、`IPTC:Country-PrimaryLocationName`；来源 = 照片本体 + `os.path.splitext(path)[0] + '.xmp'`（存在时）。任一来源任一组是真实地名即受保护。
- 受保护时去掉的 item 键：`sharpness`、`nima_score`、`focus_status`；其余字段照写。
- 受保护时去掉的重置参数：`-XMP:City=`、`-IPTC:City=`、`-XMP:State=`、`-IPTC:Province-State=`、`-XMP:Country=`、`-IPTC:Country-PrimaryLocationName=`。
- 中文写入一律走 UTF-8 临时文件（CLAUDE.md）；新增注释中英双语；新增 i18n 键三语（zh_CN / zh_TW / en_US）同时加。
- 测试放 `tests/`，文件名 `test_*.py`；改完跑 `python3 -m py_compile` 与相关测试，最后全量 `python3 -m pytest` 看 pytest 自身退出码（不要接管道）。

## Review Focus

1. **真实地名只在边车里**（Lightroom 给 RAW 填地点、SuperPicky 又开了「写入 RAW 本体」）：必须仍受保护 → Task 2 测试 `test_sidecar_only_location_protects_body_write`。
2. **只有 IPTC、没有 XMP 的老 JPG**：IPTC 城市是真实地名也要保护 → Task 2 测试 `test_iptc_only_location_protected`。
3. **一批里有的受保护有的不受**：只跳过受保护那几张，其余照常写分数 → Task 2 测试 `test_batch_mixes_protected_and_unprotected`。
4. **受保护照片的星级、题注仍要写进去**（不能因为跳过分数整张不写）→ Task 2 测试 `test_protected_photo_still_gets_rating`。
5. **SuperPicky 自己上次写的值**（`0612.34` / `05.21` / `BEST`）在重新处理时照常覆盖、重置时照常清空 → Task 2 `test_tool_written_values_are_overwritten`、Task 3 `test_reset_clears_tool_written_location`。

---

### Task 1: 判断规则模块

**Files:**
- Create: `core/location_guard.py`
- Test: `tests/test_location_guard.py`

**Interfaces:**
- Produces:
  - `FOCUS_WORDS: frozenset[str]`（大写英文 + 中文）
  - `SCORE_ITEM_KEYS: tuple[str, ...] = ("sharpness", "nima_score", "focus_status")`
  - `LOCATION_RESET_ARGS: frozenset[str]`（Global Constraints 里那 6 个参数）
  - `LOCATION_READ_ARGS: list[str] = ["-G1", "-XMP-photoshop:City", "-XMP-photoshop:State", "-XMP-photoshop:Country", "-IPTC:City", "-IPTC:Province-State", "-IPTC:Country-PrimaryLocationName"]`
  - `is_number(s: str) -> bool`
  - `is_tool_written(city: str, state: str, country: str) -> bool`
  - `groups_from_exiftool_json(data: dict) -> list[tuple[str, str, str]]`：把 `-G1 -json` 的一条记录拆成 XMP 组与 IPTC 组（键如 `"XMP-photoshop:City"`、`"IPTC:Province-State"`；缺失视为空串，非字符串转 `str`）
  - `has_real_location(groups: Iterable[tuple[str, str, str]]) -> bool`

- [ ] **Step 1: 写失败的测试**

```python
from core.location_guard import is_tool_written, has_real_location, groups_from_exiftool_json

def test_superpicky_values_are_tool_written():
    assert is_tool_written("0612.34", "05.21", "BEST")
    assert is_tool_written("0612.34", "", "")
    assert is_tool_written("", "", "good")          # 大小写不敏感
    assert is_tool_written("", "", "偏移")           # 旧版中文

def test_real_names_are_not_tool_written():
    assert not is_tool_written("布里斯班", "昆士兰州", "澳大利亚")
    assert not is_tool_written("Brisbane", "QLD", "Australia")

def test_has_real_location():
    assert not has_real_location([("", "", ""), ("0612.34", "05.21", "BEST")])
    assert has_real_location([("0612.34", "05.21", "BEST"), ("布里斯班", "", "")])

def test_groups_from_exiftool_json():
    data = {"XMP-photoshop:City": "布里斯班", "IPTC:City": "0612.34", "IPTC:Province-State": 5.21}
    assert groups_from_exiftool_json(data) == [("布里斯班", "", ""), ("0612.34", "5.21", "")]
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_location_guard.py -q`
Expected: FAIL（`ModuleNotFoundError: core.location_guard`）

- [ ] **Step 3: 实现 `core/location_guard.py`**

`is_number` 与 PhotoPicky `ToolMetadata.isNumber` 一致（去空白后非空，按 `.` 至多切两段，各段全为 ASCII 数字，首段非空）。模块 docstring 写明与 PhotoPicky 同口径、为什么三个全空算可写。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_location_guard.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/location_guard.py tests/test_location_guard.py
git commit -m "feat(metadata): 判断地点字段是否为 SuperPicky 所写（与 PhotoPicky 同口径）"
```

---

### Task 2: 写入时保护真实地名

**Files:**
- Modify: `tools/exiftool_manager.py`（`__init__` 加记录列表与锁；新增方法；`_write_metadata_subprocess` 约 1108 行、`_write_metadata_xmp_sidecar` 约 1255 行、`_write_metadata_arw` 约 1482 行的 `_apply_field_switches` 之后；`batch_set_metadata` 入口过滤处约 2045 行）
- Modify: `core/photo_processor.py`（约 3866 行 `drain_raw_embed_fallbacks` 汇总之后；约 1443 行开批前的 drain 旁同样清一次）
- Modify: `locales/zh_CN.json`、`locales/zh_TW.json`、`locales/en_US.json`（`logs.location_protected_summary`）
- Test: `tests/test_location_guard_write.py`

**Interfaces:**
- Consumes: Task 1 的 `LOCATION_READ_ARGS`、`groups_from_exiftool_json`、`has_real_location`、`SCORE_ITEM_KEYS`
- Produces:
  - `ExifToolManager._location_protected(file_path: str) -> bool`：对本体与存在的 `.xmp` 边车各调一次 `read_metadata(path, extra_args=LOCATION_READ_ARGS)`，任一来源 `has_real_location` 即 True；读取失败按 False（不保护，与改动前行为一致）
  - `ExifToolManager._drop_scores_if_located(item: Dict[str, Any]) -> Dict[str, Any]`：item 里 `SCORE_ITEM_KEYS` 任一非 None 且 `_location_protected(item['file'])` 时，返回去掉这三键的副本并记录文件路径；否则原样返回（不读文件）
  - `ExifToolManager.drain_location_protected() -> List[str]`：取出并清空记录（同 `drain_raw_embed_fallbacks` 的锁与语义）
  - i18n `logs.location_protected_summary`，参数 `count`、`names`；zh_CN 文案：`"   {count} 张照片已有地点信息，未写入锐度/美学/对焦，以免覆盖真实地点：{names}"`

- [ ] **Step 1: 写失败的测试**（用真实 ExifTool；PIL 生成小 JPG；中文值经 UTF-8 临时文件预写入，可用 `exiftool_manager.set_metadata` 或直接 `-XMP-photoshop:City<=tmp`）

```python
def test_real_city_kept_scores_skipped(jpg_with_city):          # city="布里斯班"
    mgr.batch_set_metadata([{"file": p, "rating": 3, "sharpness": 612.34, "nima_score": 5.21, "focus_status": "BEST"}])
    assert read(p, "XMP-photoshop:City") == "布里斯班"
    assert read(p, "XMP-photoshop:Country") in ("", None)
    assert p in mgr.drain_location_protected()

def test_protected_photo_still_gets_rating(jpg_with_city):
    ...  # 同上写入后 XMP:Rating == 3

def test_tool_written_values_are_overwritten(jpg_with_city_0500):  # city="0500.00"
    ...  # 写入后 City == "0612.34"，drain 为空

def test_iptc_only_location_protected(jpg_with_iptc_city):     # 只有 IPTC:City="Brisbane"
    ...  # 写入后 XMP City 未出现 0612.34

def test_batch_mixes_protected_and_unprotected(jpg_with_city, plain_jpg):
    ...  # 一批两张：前者 City 不变，后者 City == "0612.34"

def test_sidecar_only_location_protects_body_write(tmp_path):
    ...  # p.jpg 无地点；旁边 p.xmp 里 City="布里斯班" → 写 p.jpg 后其 XMP City 未被写成 0612.34
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_location_guard_write.py -q`
Expected: FAIL（真实城市被写成 `0612.34`；`drain_location_protected` 不存在）

- [ ] **Step 3: 实现上面 Interfaces 里的三个方法，并在四处写入路径调用 `_drop_scores_if_located`**（单张三处紧跟 `_apply_field_switches`，批量在 `filter_item` 之后、`has_payload` 判断之前对每个 item 调用）

- [ ] **Step 4: `photo_processor` 汇总日志**：在 `raw_fallbacks` 汇总之后 drain 并按文件名去重，列前 5 个（超出加 ` …`），`self._log(..., "info")`；开批前的 `drain_raw_embed_fallbacks()` 旁加一次 `drain_location_protected()` 清空残留。三语 i18n 键同步加。

- [ ] **Step 5: 运行确认通过**

Run: `python3 -m pytest tests/test_location_guard_write.py tests/test_location_guard.py -q`
Expected: PASS

- [ ] **Step 6: 回归**

Run: `python3 -m pytest -q $(ls tests | grep -iE "exiftool|metadata|sidecar|raw_embed" | sed 's#^#tests/#')`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add tools/exiftool_manager.py core/photo_processor.py locales/*.json tests/test_location_guard_write.py
git commit -m "feat(metadata): 照片已有真实地名时不写锐度/美学/对焦"
```

---

### Task 3: 重置时保护真实地名

**Files:**
- Modify: `tools/exiftool_manager.py`（`_reset_raw_body` 约 1407 行、`_reset_xmp_sidecar` 约 1449 行、`reset_metadata` 约 2458 行、`batch_reset_metadata` 约 2557 行）
- Test: `tests/test_location_guard_reset.py`

**Interfaces:**
- Consumes: Task 1 `LOCATION_RESET_ARGS`；Task 2 `_location_protected`
- Produces: `ExifToolManager._drop_location_reset_args(args: List[str], file_path: str) -> List[str]`：`args` 与 `LOCATION_RESET_ARGS` 有交集且 `_location_protected(file_path)` 时去掉这几项，否则原样返回（不读文件）

- [ ] **Step 1: 写失败的测试**

```python
def test_reset_keeps_real_city(jpg_with_city):            # City="布里斯班", Rating=3
    mgr.reset_metadata(p)
    assert read(p, "XMP-photoshop:City") == "布里斯班"
    assert read(p, "XMP:Rating") in (None, 0, "0")          # 其余字段照常清

def test_reset_clears_tool_written_location(jpg_with_city_0612):
    mgr.reset_metadata(p)
    assert read(p, "XMP-photoshop:City") in (None, "")

def test_batch_reset_mixed(jpg_with_city, jpg_with_city_0612):
    mgr.batch_reset_metadata([p1, p2])
    ...  # p1 城市保留，p2 城市清空
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_location_guard_reset.py -q`
Expected: FAIL（真实城市被清空）

- [ ] **Step 3: 三个逐张重置在 `_filter_reset_args` 之后调用 `_drop_location_reset_args`；`batch_reset_metadata` 先把本批文件按 `_location_protected` 分成两组，各用自己的参数集执行，受保护的组用去掉地点参数后的那套**（参数集为空就跳过该组）。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_location_guard_reset.py tests/test_directory_reset_safety.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tools/exiftool_manager.py tests/test_location_guard_reset.py
git commit -m "feat(metadata): 重置不再清掉照片里的真实地名"
```

---

### Task 4: 设置页说明 + 全量验证

**Files:**
- Modify: `ui/settings_center.py`（`_sec_output_metadata` 内，核心字段组循环里「对焦状态」复选框之后加一行说明）
- Modify: `locales/zh_CN.json`、`locales/zh_TW.json`、`locales/en_US.json`（`advanced_settings.metadata_location_hint`）
- Test: `tests/test_settings_center.py`（加一条）

**Interfaces:**
- Produces: i18n `advanced_settings.metadata_location_hint`；zh_CN 文案：`"锐度、美学、对焦借用城市、省份、国家字段，方便在 Lightroom 里按分数排序。照片里已有真实地名时不会写入这三项，重置也不会清掉真实地名；不想用这三个字段，取消勾选即可。"`；en_US：`"Sharpness, aesthetics and focus use the City, State and Country fields so you can sort by score in Lightroom. Photos that already have a real place name keep it: these three are not written, and Reset leaves the place name alone. Untick them if you don't want these fields used."`；zh_TW 用繁体对应文案（城市、省份、國家；Lightroom）。

- [ ] **Step 1: 写失败的测试**：构建 `SettingsCenter` 后，在元数据字段框内能找到文本等于 `i18n.t("advanced_settings.metadata_location_hint")` 的 `QLabel`，且三语 locale 都有该键。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_settings_center.py -q -k location_hint`
Expected: FAIL

- [ ] **Step 3: 加 `QLabel`（自动换行，样式同 `lean_hint`：`text_muted`、11px），放在 `field.field_id == "focus"` 的复选框之后；三语键同步加。**

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_settings_center.py -q`
Expected: PASS

- [ ] **Step 5: 全量**

Run: `python3 -m pytest -q -p no:cacheprovider > /tmp/full.txt 2>&1; echo "exit=$?"; tail -1 /tmp/full.txt`
Expected: `exit=0`，末行 `N passed`

- [ ] **Step 6: 提交**

```bash
git add ui/settings_center.py locales/*.json tests/test_settings_center.py
git commit -m "feat(settings): 说明锐度/美学/对焦借用地点字段及真实地名保护"
```
