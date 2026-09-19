#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中文鸟名的汉语拼音查询 / Hanyu Pinyin lookup for Chinese bird names.

数据来自 `ioc/pinyin_toned.json`（由 `scripts_dev/build_pinyin_toned.py` 构建，
约 11400 条带声调拼音）。本模块**只读文件、无任何第三方依赖、不 import Qt**：
生成拼音要用的 pypinyin 是构建期依赖，运行时用不到。

拼音是纯展示信息，且**仅在简体中文界面显示**（用户 2026-09-19 决定）。因此
本模块的所有失败路径一律安静降级为空串：鸟名出现在缩略图、详情面板、识鸟结果
等处，任何一处因为查拼音失败而抛异常，代价都远大于少显示一行拼音。

Display-only, Chinese-UI-only lookup. Every failure path degrades to "" rather
than raising, because the species name it annotates is rendered everywhere.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional

#: 相对仓库/安装目录的数据文件位置。`ioc` 整个目录已在打包清单里
#: （SuperPicky.spec 的 `(base_path/'ioc', 'ioc')`），新增文件自动随包。
#: The whole `ioc` directory is already packaged, so this file ships with it.
_RELATIVE_PATH = os.path.join("ioc", "pinyin_toned.json")

_table: Optional[Dict[str, str]] = None
_lock = threading.Lock()


def _data_path() -> str:
    """
    解析数据文件路径（源码目录 / PyInstaller bundle / Windows 安装目录）。

    返回 / Returns:
    str: `pinyin_toned.json` 的绝对路径；解析失败时返回一个不存在的路径，
        由调用方按「文件缺失」降级处理。

    Resolve the data file across source, frozen and install-scoped layouts.
    """
    try:
        from config import get_install_scoped_resource_path
        return str(get_install_scoped_resource_path(_RELATIVE_PATH))
    except Exception:
        # config 不可用时退回相对本文件的仓库布局，仍然不抛异常。
        # Fall back to the repo layout; never raise from a path lookup.
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(here, _RELATIVE_PATH)


def _load_table() -> Dict[str, str]:
    """
    从磁盘读入拼音表。

    返回 / Returns:
    Dict[str, str]: {中文鸟名: 带声调拼音}；文件缺失或损坏时返回空字典。

    Read the lookup table; a missing or corrupt file yields an empty table.
    """
    path = _data_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def reset_cache() -> None:
    """
    丢弃已缓存的拼音表，下次查询重新读盘。

    返回 / Return:
    None

    供测试隔离用；正常运行期间不需要调用——数据文件随包发布，运行中不会变。

    Drop the cached table; used by tests, never needed at runtime.
    """
    global _table
    with _lock:
        _table = None


def pinyin_for(chinese_name: Optional[str]) -> str:
    """
    查一个中文鸟名的带声调拼音。

    参数 / Parameters:
    chinese_name (Optional[str]): 中文鸟名。None、空串、纯空白都合法。

    返回 / Returns:
    str: 带声调拼音如 "hēi hóu xiǎo pì tī"；查不到返回空串。
        查不到是常态而非错误——老批次里的历史鸟名、用户手工填的名字都不在表内。

    异常 / Raises:
    无。见模块说明：这是展示信息，任何失败都降级为空串。

    Look up a Chinese bird name's toned pinyin; "" on any miss or failure.
    """
    name = (chinese_name or "").strip()
    if not name:
        return ""

    global _table
    if _table is None:
        with _lock:
            if _table is None:
                _table = _load_table()
    return _table.get(name, "")


def pinyin_for_ui(chinese_name: Optional[str], is_zh: bool) -> str:
    """
    界面用的拼音取值：非简体中文界面一律返回空串。

    参数 / Parameters:
    chinese_name (Optional[str]): 中文鸟名。
    is_zh (bool): 当前界面是否为简体中文。

    返回 / Returns:
    str: 中文界面下的带声调拼音，其余情况为空串。

    语言闸门收在这一个函数里，四个显示点（鸟名查询详情、改鸟种候选卡片、
    浏览器详情面板、识鸟结果卡片）都走它——各自写 if 迟早漏掉一处。

    The single language gate used by all four display sites.
    """
    if not is_zh:
        return ""
    return pinyin_for(chinese_name)
