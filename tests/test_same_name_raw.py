# -*- coding: utf-8 -*-
"""
回归测试：同一主文件名有多种 RAW 时（相机原片 X.CR3 + Topaz 导出的 X.dng），
只处理一个，且相机原生 RAW 优先于 DNG，与 os.listdir 的顺序无关。

此前扫描以主文件名为键直接覆盖，后扫到的赢：常见结果是 DNG 顶替了 CR3 原片，
原片不评分、不整理，同名 XMP 还被 DNG 带进星级目录。

Regression: with X.CR3 and X.dng side by side, exactly one is processed and
the camera RAW wins regardless of directory listing order.
"""
import os
from typing import List, Tuple

import pytest

from core.photo_processor import PhotoProcessor, ProcessingCallbacks, ProcessingSettings

FILES = ["_T3A9273.CR3", "_T3A9273.dng", "_T3A9274.CR3", "_T3A9274.jpg", "_T3A9316-2.dng"]


def _scan(tmp_path, monkeypatch, order) -> Tuple[dict, dict, list, List[Tuple[str, str]]]:
    """
    以指定的目录列出顺序扫描，返回扫描结果与日志。

    Scan with a forced os.listdir order; return the scan results and logs.
    """
    for name in FILES:
        (tmp_path / name).write_text("")
    real_listdir = os.listdir
    monkeypatch.setattr(
        "core.photo_processor.os.listdir",
        lambda path: order(real_listdir(path)) if os.path.samefile(path, tmp_path) else real_listdir(path),
    )
    logs: List[Tuple[str, str]] = []
    proc = PhotoProcessor(str(tmp_path), ProcessingSettings(),
                          ProcessingCallbacks(log=lambda m, level="info": logs.append((level, m))))
    raw, jpg, tbr = proc._scan_files()
    return raw, jpg, tbr, logs


@pytest.mark.parametrize("order", [sorted, lambda names: sorted(names, reverse=True)],
                         ids=["dng-last", "dng-first"])
def test_camera_raw_beats_dng_regardless_of_order(tmp_path, monkeypatch, order):
    raw, jpg, tbr, logs = _scan(tmp_path, monkeypatch, order)
    assert raw == {"_T3A9273": ".CR3", "_T3A9274": ".CR3", "_T3A9316-2": ".dng"}
    # RAW+JPG 配对不受影响 / RAW+JPG pairing unchanged
    assert jpg == {"_T3A9274": ".jpg"} and tbr == ["_T3A9274.jpg"]
    warnings = [m for level, m in logs if level == "warning"]
    assert len(warnings) == 1 and "_T3A9273.dng" in warnings[0]


def test_dng_only_stem_still_processed(tmp_path, monkeypatch):
    """只有 DNG 的照片照常处理，不会被误跳过。/ A DNG-only stem is still processed."""
    raw, _, _, logs = _scan(tmp_path, monkeypatch, sorted)
    assert raw["_T3A9316-2"] == ".dng"
    assert "_T3A9316-2.dng" not in " ".join(m for level, m in logs if level == "warning")
