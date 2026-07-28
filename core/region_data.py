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
    从 `birdid/data/geo_distribution.db` 的 `country_species` 表生成国家列表。

    旧实现读 `ebird_regions.json`，该文件与离线数据、`REGION_BOUNDS` 三方错位：
    列出的 49 国里有 11 国无任何数据（选中即静默落空），另有 14 国有数据却选不到。
    改为与网格数据同源后，国家列表恒等于实际可用的过滤数据，错位不再可能。

    Build the country list from the `country_species` table of
    geo_distribution.db. The previous implementation read `ebird_regions.json`,
    which was out of sync with both the offline data and REGION_BOUNDS: 11 of its
    49 countries had no data at all (selecting them silently did nothing), while
    14 countries with data could not be selected. Sourcing it from the same
    dataset as the grid makes that mismatch structurally impossible.

    返回 / Returns:
        dict: 含 `"countries"` 列表的字典，每项有 `code` / `name` / `name_cn` /
              `species_count` 等字段；失败时返回 `{"countries": []}` /
              Dict with a `"countries"` list; `{"countries": []}` on failure.

    异常 / Exceptions:
        不抛出异常；所有错误以 print 记录后返回空结构。
        Does not raise; all errors are printed and an empty structure is returned.
    """
    import sqlite3

    from birdid.geo_filter import default_db_path
    from tools.country_names import country_display_names

    db_path = default_db_path()
    if not os.path.exists(db_path):
        print(f"[region_data] 地理分布库不存在 / geo DB missing: {db_path}")
        return {"countries": []}

    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT country, COUNT(*) FROM country_species "
            "GROUP BY country HAVING COUNT(*) > 0 ORDER BY country"
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[region_data] 读取国家列表失败 / Failed to read countries: {exc}")
        return {"countries": []}

    countries: list[dict[str, Any]] = []
    for code, count in rows:
        english, chinese = country_display_names(str(code))
        countries.append(
            {
                "code": str(code),
                "name": english,
                "name_cn": chinese,
                "is_continent": False,
                # 州/省级数据本设计不提供：GBIF 网格已按 GPS 精确到 1°，
                # 手选地区只在无 GPS 时作国家级回退用。
                # No subnational data by design: the GBIF grid already resolves
                # to 1 degree by GPS, and manual selection only serves as a
                # country-level fallback for photos without GPS.
                "has_regions": False,
                "regions_count": 0,
                "regions": [],
                "species_count": int(count),
            }
        )
    return {"countries": countries}
