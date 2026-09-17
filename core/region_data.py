# -*- coding: utf-8 -*-
"""
识鸟地区数据加载工具 — 核心层可复用函数。
Bird-ID region data loader — reusable core-layer function.

原始逻辑来自 `ui/birdid_dock.BirdIDDockPanel._load_regions_data`；
抽到此处使 SettingsCenter（Task 4）与 Dock（Task 8 重构后）共享同一实现，
避免重复维护两份路径解析代码。

The original logic lives in `ui/birdid_dock.BirdIDDockPanel._load_regions_data`;
extracting it here lets SettingsCenter (Task 4) and Dock (Task 8 refactor) share
a single implementation, preventing duplicated path-resolution code.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def _get_birdid_data_path(relative_path: str) -> str:
    """
    解析 `birdid/data/<relative_path>` 的绝对路径，兼容开发和打包环境。

    Resolve the absolute path for `birdid/data/<relative_path>`,
    compatible with development and packaged (PyInstaller) environments.

    参数 / Parameters:
        relative_path (str): 相对于 `birdid/data/` 的文件名 / Filename relative to `birdid/data/`.

    返回 / Returns:
        str: 绝对路径 / Absolute path.
    """
    # 打包 Windows 环境 — 使用安装作用域资源路径
    # Packaged Windows environment — use install-scoped resource path
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        from config import get_install_scoped_resource_path
        return str(get_install_scoped_resource_path(
            os.path.join("birdid", "data", relative_path)
        ))

    # 打包非 Windows 环境 — 使用 _MEIPASS（PyInstaller bundle 根）
    # Packaged non-Windows environment — use _MEIPASS (PyInstaller bundle root)
    if getattr(sys, "frozen", False):
        from config import get_runtime_meipass
        meipass = get_runtime_meipass()
        if meipass is not None:
            return os.path.join(meipass, "birdid", "data", relative_path)

    # 开发环境 — 从本文件向上两层到项目根
    # Development environment — two levels up from this file to the project root
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "birdid", "data", relative_path)


def load_regions_data() -> dict[str, Any]:
    """
    从 `birdid/data/ebird_regions.db` 生成国家与中澳美省州列表。

    国家与省州都来自 eBird 区域清单（与过滤用的是同一份数据），列表中出现的每个区域
    都保证有候选物种；只有中国、澳洲、美国带省州。

    Build the country list, with subnational units for CN/AU/US, from
    ebird_regions.db. Both come from the same eBird region lists the filter
    uses, so every listed region has candidate species.

    返回 / Returns:
        dict: 含 `"countries"` 列表；每个国家含 `code` / `name` / `name_cn` /
              `has_regions` / `regions_count` / `regions` / `species_count`；
              失败时返回 `{"countries": []}` /
              Dict with a `"countries"` list; `{"countries": []}` on failure.

    异常 / Exceptions:
        不抛出异常；错误以 print 记录后返回空结构。
        Does not raise; errors are printed and an empty structure is returned.
    """
    import sqlite3

    from birdid.geo_filter import default_db_path

    db_path = default_db_path()
    if not os.path.exists(db_path):
        print(f"[region_data] region DB missing: {db_path}")
        return {"countries": []}

    try:
        conn = sqlite3.connect(db_path)
        try:
            countries_rows = conn.execute(
                "SELECT code, name_en, name_zh, species_count FROM regions "
                "WHERE parent IS NULL AND species_count > 0 ORDER BY code"
            ).fetchall()
            # 省州按 eBird code 排序（非按英文名字母序）：中国省份的 code 遵循
            # GB/T 2260 行政区划顺序（CN-11 北京、CN-12 天津、CN-13 河北……），
            # 这是中文用户熟悉的顺序；按 name_en 排会把它打乱成拼音字母序。
            # AU/US 的 code 本身就是缩写序，两种排序对它们没有实质差别，用同一
            # 排序键即可，不必按国家分支判断。
            # Order subnational regions by eBird code, not the English name:
            # China's codes follow the GB/T 2260 administrative order (CN-11
            # Beijing, CN-12 Tianjin, CN-13 Hebei, ...), which is the order
            # Chinese users expect; sorting by name_en would scramble it into
            # English-alphabetical order. AU/US codes are already
            # abbreviation-ordered, so code order works for them too — no
            # per-country branching needed.
            region_rows = conn.execute(
                "SELECT code, parent, name_en, name_zh, species_count FROM regions "
                "WHERE parent IS NOT NULL AND species_count > 0 ORDER BY parent, code"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[region_data] failed to read regions: {exc}")
        return {"countries": []}

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for code, parent, name_en, name_zh, count in region_rows:
        by_parent.setdefault(str(parent), []).append(
            {"code": str(code), "name": str(name_en), "name_cn": str(name_zh), "species_count": int(count)}
        )

    countries: list[dict[str, Any]] = []
    for code, name_en, name_zh, count in countries_rows:
        regions = by_parent.get(str(code), [])
        countries.append(
            {
                "code": str(code),
                "name": str(name_en),
                "name_cn": str(name_zh),
                "is_continent": False,
                "has_regions": bool(regions),
                "regions_count": len(regions),
                "regions": regions,
                "species_count": int(count),
            }
        )
    return {"countries": countries}
