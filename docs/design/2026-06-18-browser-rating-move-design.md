# 结果浏览器改星等联动文件移动 — 设计文档

**日期**: 2026-06-18  
**状态**: 待实现

---

## 背景

SuperPicky 结果浏览器支持手动修改照片星等，但修改后只写 DB + EXIF，不移动文件。
整理阶段（organize）按 `compute_target_folder(rating, bird_name, layout)` 把照片分配到
`鸟种/星级`（species-first，默认）或 `星级/鸟种`（rating-first）目录。
改完星等后文件仍停在旧目录，和目录结构语义不符。

同样的问题在未来开放"修改鸟种"后也会出现，因此设计必须同时覆盖两种触发场景。

---

## 功能范围

| 触发场景 | 是否移动 |
|---|---|
| 浏览器改星等 | ✅ |
| 浏览器改鸟种（未来功能） | ✅（同一套机制） |
| 文件在根目录（未整理） | ❌ 不移动，仅改 DB/EXIF |
| 文件在 `burst_xxx/` 子目录 | ❌ 不移动，burst 组整体性不可拆 |
| 新旧目标目录相同 | ❌ no-op |
| 目标已有同名文件 | ❌ 跳过（不覆盖） |

---

## 架构

### 新文件：`core/rating_mover.py`

封装"因 rating 或 bird_name 变化移动文件"的全部逻辑，与 UI 解耦。

```python
def move_photo_on_metadata_change(
    dir_path: str,          # 批处理根目录（绝对路径）
    photo: dict,            # 来自 DB 的 photo 字典
    new_rating: int,        # 新星等
    new_bird_name: str,     # 新鸟种名（改星等时传原值，改鸟种时传新值）
    layout: str,            # "species-first" | "rating-first"
    report_db,              # ReportDB 实例
) -> bool:                  # 是否实际发生了移动
```

**执行步骤**：

1. **路径校验**：从 `photo["current_path"]` 解析绝对路径，文件不存在则返回 `False`
2. **Burst 检测**：若路径任一段以 `burst_` 开头，跳过移动返回 `False`
3. **根目录检测**：若文件直接在 `dir_path` 下（无子目录层），跳过移动返回 `False`
4. **计算新目录**：`compute_target_folder(new_rating, new_bird_name, layout)` → `new_rel_folder`
5. **比较旧目录**：从当前路径提取相对目录，与 `new_rel_folder` 一致则 no-op
6. **收集配套文件**：
   - RAW：来自 `photo["current_path"]`
   - JPEG：来自 `photo["temp_jpeg_path"]`（若存在且路径有效）
   - XMP sidecar：与 RAW 同目录、同 stem、`.xmp` 后缀
7. **移动文件**：逐个移动，目标目录不存在则 `os.makedirs`；目标已有同名则跳过
8. **更新 DB**：
   - `current_path` → RAW 新相对路径
   - `temp_jpeg_path` → JPEG 新相对路径（若移动了 JPEG）
9. **更新 manifest**：读取 `dir_path/.superpicky_manifest.json`，更新 `files` 列表中
   对应条目的 `folder` 字段，写回；manifest 不存在则跳过

**Manifest 锁**：模块级 `threading.Lock`，防止并发写坏 JSON。

---

### 修改：`ui/results_browser_window.py`

**`_on_rating_changed`** 末尾追加后台线程调用：

```python
from core.rating_mover import move_photo_on_metadata_change
from advanced_config import get_advanced_config

threading.Thread(
    target=_do_move_safe,   # 包 try/except 的 wrapper，失败只 log 不抛
    args=(
        self._get_base_dir(current_photo),   # 处理 merged 多目录的情况
        current_photo,
        new_rating,
        _photo_bird_name(current_photo, self.i18n),  # 按语言取 CN/EN 名
        get_advanced_config().folder_layout,
        self._db,
    ),
    daemon=True,
).start()
```

移动成功后，在主线程同步更新内存中 `current_photo["current_path"]`（通过回调或
`QMetaObject.invokeMethod`，避免跨线程直接写 dict）。

**辅助函数**（文件顶部）：

```python
def _photo_bird_name(photo: dict, i18n) -> str:
    """按界面语言取鸟种名，用于 compute_target_folder。"""
    use_en = i18n.current_lang.startswith('en')
    key = "bird_species_en" if use_en else "bird_species_cn"
    return (photo.get(key) or "").strip()

def _do_move_safe(dir_path, photo, new_rating, bird_name, layout, db):
    """后台移动的 try/except wrapper。"""
    try:
        move_photo_on_metadata_change(dir_path, photo, new_rating, bird_name, layout, db)
    except Exception as e:
        from tools.utils import log_message
        log_message(f"[rating_mover] move failed: {e}")
```

---

### 未来：鸟种修改联动（接口预留）

`move_photo_on_metadata_change` 的 `new_bird_name` 参数即为预留接口。
未来开放鸟种编辑后，在对应的 `_on_bird_name_changed` 回调里传新鸟种名即可，
文件移动逻辑完全复用，无需修改 `rating_mover.py`。

---

## 边界与容错

| 情况 | 行为 |
|---|---|
| `current_path` 为空或文件不存在 | 跳过，`False` |
| 路径含 `burst_` 段 | 跳过，`False` |
| 文件在根目录 | 跳过，`False` |
| 新旧目录相同 | no-op，`False` |
| 目标已有同名文件 | 跳过该文件，继续其他配套文件 |
| 移动失败（权限/IO） | log warning，已移动部分不回滚 |
| manifest 不存在 | 仅移文件 + 更新 DB，跳过 manifest |
| manifest JSON 损坏 | 捕获异常，跳过 manifest 更新 |
| `_is_merged` 多目录 browser | 用 `photo["_base_dir"]` 替代 `self._directory` |

---

## 涉及文件

| 文件 | 变更类型 |
|---|---|
| `core/rating_mover.py` | 新建 |
| `ui/results_browser_window.py` | 修改 `_on_rating_changed`，新增辅助函数 |

`EmbeddedResultsBrowser`（`results_browser_window.py` 内的嵌入式变体）有自己的
`_on_rating_changed`，需同步修改。

---

## 不在范围内

- 改星等后清理变空的旧目录（留给 Reset 统一处理）
- 批量改星等的进度 UI（本次是单张静默移动）
- 移动结果的 Toast 通知（静默后台）
