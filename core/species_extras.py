#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸟种级属性查询 / Species-level attribute lookup.

罕见度（`gbif_rarity_100`）、IUCN 等级（`iucn_category`）与鸟种颜值
（`aesthetic_index`）三者都是**鸟种**的属性而不是照片的属性：同一个鸟种的每
一张照片取值都相同。处理流程在识鸟时写过一次，此后只要鸟种变了，这三个值就
必须跟着重查——否则改完鸟种的照片会顶着上一个鸟种的罕见度与濒危徽标进报告，
而报告的鸟种清单正是按罕见度档位排序的。

本模块**不得 import 任何 Qt 模块**：它被 `core.rating_mover`（纯 IO/DB 层）与
结果浏览器同时调用，必须能脱离 QApplication 单测。

This module MUST NOT import Qt. Rarity / IUCN / beauty are species-level, not
per-photo, so any species change has to re-resolve them; leaving the previous
species' values behind puts wrong badges and wrong ordering into the report.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, Optional

# 三个鸟种级字段的统一空值。查不到时返回它的副本——**键必须齐全**：
# 调用方会把整个字典并进 DB 更新，键缺席等于「保留旧值」，而旧值属于上一个
# 鸟种，是确定错的。宁可清空不显示，也不能显示错的徽标。
# All three keys are always present: a missing key would mean "keep the old
# value", and the old value belongs to the previous species.
EMPTY_SPECIES_EXTRAS: Dict[str, None] = {
    "gbif_rarity_100": None,
    "iucn_category": None,
    "aesthetic_index": None,
}

# 参考库管理器单例：库 36MB，每次改鸟种都新建一个实例会白付连接与自检的开销。
# 取不到库时缓存 None 并不再重试（缺库是环境问题，不会在运行中变好）。
# Cached manager; the reference DB is 36MB and a miss is an environment issue.
_manager = None
_manager_resolved = False
_manager_lock = threading.Lock()


def _get_manager():
    """
    懒加载并缓存 BirdDatabaseManager；缺库或异常时返回 None（静默降级）。

    返回 / Returns:
        Optional[BirdDatabaseManager]: 参考库管理器，不可用时为 None。

    Lazily build and cache the reference-DB manager, degrading to None.
    """
    global _manager, _manager_resolved
    if _manager_resolved:
        return _manager
    with _manager_lock:
        if _manager_resolved:
            return _manager
        try:
            from config import get_install_scoped_resource_path
            path = str(get_install_scoped_resource_path(
                os.path.join("birdid", "data", "bird_reference.sqlite")
            ))
            if os.path.exists(path):
                # 延迟导入：BirdDatabaseManager 只依赖 sqlite3 + i18n，不引入 torch。
                # Lazy import; the manager pulls in sqlite3 + i18n only.
                from birdid.bird_database_manager import BirdDatabaseManager
                _manager = BirdDatabaseManager(path)
        except Exception:
            _manager = None
        _manager_resolved = True
        return _manager


def lookup_species_extras(latin_name: Optional[str], db_manager=None) -> Dict:
    """
    按学名查鸟种级属性：罕见度、IUCN 等级、鸟种颜值。

    参数 / Parameters:
    latin_name (Optional[str]): 鸟种学名（拉丁名）。空值或查不到都不是错误——
        老批次、用户自定义鸟名都可能没有学名。
    db_manager: 可选的 BirdDatabaseManager 替身，仅供测试注入；默认用进程内
        缓存的参考库实例。

    返回 / Returns:
    Dict: `{"gbif_rarity_100": Optional[float], "iucn_category": Optional[str],
        "aesthetic_index": Optional[float]}`。**三个键恒在**，查不到的为 None，
        调用方可整体并进 DB 更新以清掉上一个鸟种的残值。

    异常 / Raises:
    无。参考库缺失、损坏或查询异常一律降级为全 None——改鸟种本身必须成功，
    不能因为查不到徽标数据就失败。

    Look up a species' rarity / IUCN / beauty by scientific name. Always returns
    all three keys (None on a miss) so callers can clear the previous species'
    values in one update. Never raises: a species change must not fail just
    because the badge data is unavailable.
    """
    extras: Dict = dict(EMPTY_SPECIES_EXTRAS)
    name = (latin_name or "").strip()
    if not name:
        return extras

    manager = db_manager if db_manager is not None else _get_manager()
    if manager is None:
        return extras

    try:
        info = manager.get_extra_info_by_scientific_name(name)
    except Exception:
        return extras
    if not info:
        return extras

    extras["gbif_rarity_100"] = info.get("gbif_rarity_100")
    extras["iucn_category"] = info.get("iucn_category")

    # 颜值挂在 model_class_id 上（iRateBird 表按模型类别索引），
    # 学名对不上 BirdCountInfo 时拿不到 class_id，颜值只能留空。
    # Beauty is keyed by model_class_id, unavailable when the name misses.
    class_id = info.get("model_class_id")
    if class_id is not None:
        try:
            extras["aesthetic_index"] = manager.get_aesthetic_by_class_id(class_id)
        except Exception:
            extras["aesthetic_index"] = None
    return extras
