#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国国家重点保护野生动物等级查询（鸟类，2021 版名录）。

数据出处：国家林业和草原局、农业农村部 2021 年第 3 号公告公布的《国家重点保护
野生动物名录》（2021 年 2 月 1 日施行）。鸟名主库（bird-names 的
species_conservation 表）把名录中的鸟类对应到主库鸟种，再由
scripts_dev/sync_from_master.py 写进参考库
birdid/data/bird_reference.sqlite 的 china_protection 表：每个受保护鸟种按学名、
英文名、简体名、繁体名各一行，等级 1=国家一级、2=国家二级。

为什么按名字查而不是存进照片库：
- report.db 只存中英文鸟名、不存学名，按名字查正好对得上；
- 等级是**鸟种**属性，显示时现查，改鸟种后不会残留上一个鸟种的等级，
  也不用给老照片库加字段、做迁移。

只在中文界面（简体 / 繁体）显示：这是中国的保护名录，对英文用户意义不大
（2026-10-31 与用户确认）。暂不写入照片元数据。

本模块**不得 import 任何 Qt 模块**：详情面板、鸟名查询、后期工作区与报告导出
（纯 IO 层）都会调用它，必须能脱离 QApplication 单测。

China's national key protection class for birds, from the List of Wildlife
under Special State Protection published by the National Forestry and Grassland
Administration and the Ministry of Agriculture and Rural Affairs (Announcement
No. 3 of 2021), mapped to master species in bird-names and synced into the
reference DB's china_protection table (one row per
scientific / English / Simplified / Traditional name; 1 = Class I, 2 = Class II).
Looked up by name at display time, so a species change never leaves a stale
class behind and no report.db migration is needed. Shown in Chinese UIs only.
Must not import Qt.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Dict, Iterable, Optional

#: 参考库里的表名，与 scripts_dev/sync_from_master.CHINA_TABLE 保持一致
#: Table name in the reference DB; must match sync_from_master.CHINA_TABLE.
CHINA_TABLE = "china_protection"

#: 等级 → (简体文字, 繁体文字)。繁体按台湾用字。
#: Class → (Simplified label, Traditional label).
_LABELS: Dict[int, tuple] = {
    1: ("国家一级", "國家一級"),
    2: ("国家二级", "國家二級"),
}

#: 等级 → 显示色。一级用红、二级用橙，与 IUCN 的 CR/EN 色系相近，一眼看出分量。
#: Display colours: Class I red, Class II orange.
_COLORS: Dict[int, str] = {
    1: "#ef4444",
    2: "#f59e0b",
}

# 名字 → 等级的内存表（约 1500 行）。首次查询时整表读入，之后不再访问数据库。
# 读不到（缺库、旧库没有这张表）时缓存空表，不再重试——这是环境问题，运行中不会变好。
# Name → class map (~1500 rows), loaded once; a miss caches an empty map.
_table: Optional[Dict[str, int]] = None
_table_lock = threading.Lock()


def _default_db_path() -> Optional[str]:
    """
    参考库路径（开发环境与打包环境统一走 config 的资源路径解析）。

    返回 / Returns:
        Optional[str]: 路径；config 不可用时返回 None。

    Resolve the reference DB path via config (dev and packaged builds alike).
    """
    try:
        from config import get_install_scoped_resource_path
        return str(get_install_scoped_resource_path(
            os.path.join("birdid", "data", "bird_reference.sqlite")))
    except Exception:
        return None


def load_table(db_path: Optional[str] = None) -> Dict[str, int]:
    """
    读入名字 → 等级表；已读过则直接返回缓存。

    参数 / Parameters:
        db_path (Optional[str]): 参考库路径，仅供测试注入；默认用 config 解析的路径。
            传入时总是重新读取并替换缓存。

    返回 / Returns:
        Dict[str, int]: 名字 → 等级（1/2）。缺库或缺表时为空字典。

    异常 / Raises:
        无。任何读取错误都降级为空表——查不到等级只是不显示，不能影响界面。

    Load (or return the cached) name → class map; never raises.
    """
    global _table
    if _table is not None and db_path is None:
        return _table
    with _table_lock:
        if _table is not None and db_path is None:
            return _table
        path = db_path or _default_db_path()
        table: Dict[str, int] = {}
        if path and os.path.exists(path):
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    for name, level in con.execute(f"SELECT name, level FROM {CHINA_TABLE}"):
                        if name and level in _LABELS:
                            table[name] = int(level)
                finally:
                    con.close()
            except sqlite3.Error:
                table = {}
        _table = table
        return _table


def reset_cache() -> None:
    """清空缓存（测试用）/ Drop the cache (tests)."""
    global _table
    with _table_lock:
        _table = None


def get_level(*names: Optional[str]) -> Optional[int]:
    """
    按鸟名查国家保护等级，依次尝试传入的每个名字，命中即返回。

    参数 / Parameters:
        *names: 鸟种名字，可混传中文名、英文名、学名；空值自动跳过。
            调用方通常传 (中文名, 英文名) 或 (学名, 中文名, 英文名)。

    返回 / Returns:
        Optional[int]: 1=国家一级，2=国家二级；不在名录里或查不到为 None。

    Look up the class by any of the given names (first hit wins).
    """
    table = load_table()
    if not table:
        return None
    for name in _clean(names):
        level = table.get(name)
        if level is not None:
            return level
    return None


def _clean(names: Iterable[Optional[str]]):
    """去空白、跳过空值 / strip and skip blanks."""
    for name in names:
        name = (name or "").strip()
        if name:
            yield name


def is_shown_for(lang: Optional[str]) -> bool:
    """
    当前界面语言是否显示国家保护等级：只在中文（简体 / 繁体）界面显示。

    参数 / Parameters:
        lang (Optional[str]): 界面语言代码，如 zh_CN / zh_TW / en_US。

    返回 / Returns:
        bool: 中文界面为 True。

    Shown in Chinese (Simplified / Traditional) UIs only.
    """
    return str(lang or "").startswith("zh")


def label(level: Optional[int], lang: Optional[str]) -> Optional[str]:
    """
    把等级转成当前界面的显示文字；非中文界面或无等级时返回 None。

    参数 / Parameters:
        level (Optional[int]): 1 或 2
        lang (Optional[str]): 界面语言代码

    返回 / Returns:
        Optional[str]: 「国家一级」/「國家二級」等；不显示时为 None。

    Display label for the class in the current UI language, or None.
    """
    if level not in _LABELS or not is_shown_for(lang):
        return None
    simplified, traditional = _LABELS[int(level)]
    return traditional if str(lang).startswith("zh_TW") else simplified


def color(level: Optional[int]) -> Optional[str]:
    """
    等级对应的显示色；无等级为 None。

    参数 / Parameters:
        level (Optional[int]): 1 或 2

    返回 / Returns:
        Optional[str]: 十六进制颜色。

    Display colour for the class, or None.
    """
    return _COLORS.get(level) if level is not None else None
