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
    从 `birdid/data/ebird_regions.json` 加载国家/地区数据，失败时返回空结构。

    Load country/region data from `birdid/data/ebird_regions.json`;
    returns an empty structure on any failure.

    返回 / Returns:
        dict: 含 `"countries"` 列表的字典；失败时返回 `{"countries": []}` /
              Dict with a `"countries"` list; returns `{"countries": []}` on failure.

    异常 / Exceptions:
        不抛出异常；所有错误以 print 记录后返回空结构。
        Does not raise; all errors are printed and an empty structure is returned.
    """
    regions_path = _get_birdid_data_path("ebird_regions.json")
    if os.path.exists(regions_path):
        try:
            with open(regions_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[region_data] 加载区域数据失败 / Failed to load region data: {exc}")
    return {"countries": []}
