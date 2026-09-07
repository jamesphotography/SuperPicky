# -*- coding: utf-8 -*-
"""
自定义罕见指数（可选数据源）/ Custom rarity index (optional data source).

本模块只提供一个**通用接口**：用户自行准备一份 0-10 的鸟种罕见度数据，
放进来即可在详情页与内置的 GBIF 全球罕见度并排显示。应用不附带、不预设
任何具体数据源——数据从哪来、依据什么标准，由提供数据的人决定。

之所以做成外部可选而非内置：不同数据源的授权条款各不相同，多数不允许随
应用再分发；把它做成接口，应用本身就不牵涉任何数据集的分发问题。

This module is just an interface: bring your own 0-10 rarity dataset and it
shows up next to the built-in GBIF global rarity. Nothing ships with the app —
which keeps the app clear of any dataset's redistribution terms.

数据文件格式 / Data file format:
    SQLite，含表 ``custom_rarity``，至少这几列：
      chinese_simplified TEXT   中文鸟名（查询键之一）
      english_name       TEXT   英文鸟名（查询键之一）
      rarity_index       REAL   0-10，越大越罕见
    照片记录里只有中英文鸟名，所以按名字匹配。

位置 / Location:
    用户配置目录下的 ``custom_rarity.db``。刻意不放在 birdid/data/ —— 三个
    .spec 都以整目录方式打包那里，任何放进去的文件都会被塞进安装包。
    配置目录不被任何 .spec 收集，用户也能自行放入或经设置页导入。

文件缺席是常态（绝大多数用户没有这份数据），所有入口都必须安全降级，
不得报错、更不得影响详情页的其它内容。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from typing import Dict, Optional, Tuple

# 数据表名 / Table name expected inside the user-supplied database.
TABLE_NAME = "custom_rarity"

# 懒加载的查表缓存：(中文名→指数, 英文名→指数)；None 表示尚未尝试加载
# Lazily built lookup tables; None means "not loaded yet".
_TABLES: Optional[Tuple[Dict[str, float], Dict[str, float]]] = None
_LOCK = threading.Lock()


def _db_path() -> str:
    """
    返回数据库路径：**用户配置目录**下的 custom_rarity.db。

    刻意不放在 birdid/data/ —— 三个 .spec 都以整目录方式打包该目录，
    .gitignore 拦得住 git 却拦不住 PyInstaller：只要开发机上存在，
    文件就会被打进安装包。配置目录则打包永远碰不到。

    Deliberately NOT under birdid/data: every .spec bundles that whole
    directory, so a file there would ship in the installer.

    测试会 monkeypatch 本函数以指向临时库。
    Patched by tests to point at a temp file.

    返回 / Returns:
        str: 绝对路径，文件不一定存在
    """
    from config import get_app_config_dir
    return str(get_app_config_dir() / "custom_rarity.db")


def reset_cache() -> None:
    """
    清空内存缓存，下次查询重新加载。

    导入新数据后由 install_database 自动调用；测试切换数据源时也用它。

    Drop the in-memory cache; called after an import and by tests.
    """
    global _TABLES
    with _LOCK:
        _TABLES = None


def _load() -> Tuple[Dict[str, float], Dict[str, float]]:
    """
    加载并缓存查表。文件缺席或损坏时返回两个空字典（不抛异常）。

    整表通常只有一万余行、一两 MB，一次读进内存即可；浏览时每张照片都要查
    一次，走缓存才不会被磁盘 IO 拖慢。

    Load and cache the lookup tables, returning empty dicts when the file is
    absent or unreadable. The browser queries this once per photo, so it stays
    in memory.

    返回 / Returns:
        Tuple[Dict[str, float], Dict[str, float]]: (中文名表, 英文名表)
    """
    global _TABLES
    with _LOCK:
        if _TABLES is not None:
            return _TABLES

        by_cn: Dict[str, float] = {}
        by_en: Dict[str, float] = {}
        path = _db_path()
        if os.path.exists(path):
            try:
                con = sqlite3.connect(path)
                try:
                    rows = con.execute(
                        "SELECT chinese_simplified, english_name, rarity_index "
                        f"FROM {TABLE_NAME} WHERE rarity_index IS NOT NULL"
                    ).fetchall()
                finally:
                    con.close()
                for cn, en, idx in rows:
                    if idx is None:
                        continue
                    # 同名种以先到者为准：这是一份参考值，极少数重名不影响用途
                    # First writer wins; this is a reference figure.
                    if cn:
                        by_cn.setdefault(str(cn).strip(), float(idx))
                    if en:
                        by_en.setdefault(str(en).strip(), float(idx))
            except (sqlite3.Error, OSError, ValueError):
                # 数据坏了就当没有——一个可选的参考值绝不能拖垮详情页
                # A broken optional dataset must never break the detail panel.
                by_cn, by_en = {}, {}

        _TABLES = (by_cn, by_en)
        return _TABLES


def is_available() -> bool:
    """
    本机是否有可用的自定义罕见指数数据。

    Whether the optional dataset is present and non-empty.

    返回 / Returns:
        bool: True 表示可以显示
    """
    by_cn, by_en = _load()
    return bool(by_cn or by_en)


def species_count() -> int:
    """
    已加载的鸟种数，供设置页显示启用状态。

    Number of loaded species, for the settings page status line.

    返回 / Returns:
        int: 鸟种数；数据缺席时为 0
    """
    by_cn, by_en = _load()
    return max(len(by_cn), len(by_en))


def lookup_index(cn_name: Optional[str], en_name: Optional[str]) -> Optional[float]:
    """
    按鸟名查自定义罕见指数。

    照片记录里只有中英文鸟名（没有物种编号），所以按名字查：中文名优先，
    取不到再用英文名兜底（英文环境处理的批次可能没有中文名）。

    Look up by species name: Chinese first, English as fallback, since photo
    records carry only names.

    参数 / Args:
        cn_name: 中文鸟名，可为 None/空
        en_name: 英文鸟名，可为 None/空

    返回 / Returns:
        Optional[float]: 0-10 的指数；查不到或数据缺席时为 None
    """
    by_cn, by_en = _load()
    if not by_cn and not by_en:
        return None

    key_cn = (cn_name or "").strip()
    if key_cn and key_cn in by_cn:
        return by_cn[key_cn]

    key_en = (en_name or "").strip()
    if key_en and key_en in by_en:
        return by_en[key_en]

    return None


def validate_database(path: str) -> Tuple[bool, str, int]:
    """
    校验一个文件是否为可用的罕见指数库，不做任何改动。

    用户是自行准备/获取这份数据的，选错文件很正常，所以要在复制之前就把话
    说清楚：不是数据库、结构不对、还是空表。

    Validate a candidate file without touching anything; users bring this data
    themselves and picking the wrong file is expected.

    参数 / Args:
        path: 待校验文件路径

    返回 / Returns:
        Tuple[bool, str, int]: (是否可用, 原因码, 鸟种数)
        原因码：ok / not_a_database / missing_table / empty
        原因码保持稳定，由 UI 层翻译成本地化文案。
    """
    if not path or not os.path.isfile(path):
        return False, "not_a_database", 0
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            has_table = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (TABLE_NAME,),
            ).fetchone()
            if not has_table:
                return False, "missing_table", 0
            n = con.execute(
                f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE rarity_index IS NOT NULL"
            ).fetchone()[0]
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return False, "not_a_database", 0

    if not n:
        return False, "empty", 0
    return True, "ok", int(n)


def install_database(src_path: str) -> Tuple[bool, str, int]:
    """
    校验并把用户给定的数据库安装到配置目录，成功后立即生效。

    先校验再复制：校验不过绝不动现有文件——用户可能已经装好了一份能用的
    数据，弄丢了未必方便再要一份。复制走临时文件 + 原子替换，中途失败也不会
    留下半个损坏的库。

    Validate first, then install atomically. A failed import must never damage
    an already-working dataset.

    参数 / Args:
        src_path: 用户选择的数据库文件

    返回 / Returns:
        Tuple[bool, str, int]: (是否成功, 原因码, 鸟种数)
        原因码：ok / not_a_database / missing_table / empty / copy_failed
    """
    ok, reason, count = validate_database(src_path)
    if not ok:
        return False, reason, 0

    dst = _db_path()
    tmp = dst + ".importing"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dst)          # 同目录内原子替换 / atomic within a dir
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, "copy_failed", 0

    reset_cache()
    return True, "ok", count
