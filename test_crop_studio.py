# -*- coding: utf-8 -*-
"""Crop Studio 全屏后期工作区冒烟测试(offscreen,headless 安全)。
   Smoke tests for the CropStudio fullscreen post-processing workspace.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_crop_studio_constructs():
    """构造不崩,且路径解析优先 temp_jpeg→current→original。"""
    from ui.crop_studio import CropStudio

    photo = {
        "filename": "a.NEF",
        "current_path": "/nonexist/a.NEF",
        "bird_species_cn": "红头鸲鹟",
        "rating": 3,
        "gbif_tier": 2,
    }
    w = CropStudio(photo, get_i18n())
    assert w is not None
    assert w._resolve_image_path(photo).endswith("a.NEF")
    # 等待后台线程结束并关闭,避免 QThread 在 GC 时仍运行触发 abort。
    # Join the worker and close so the QThread isn't destroyed while running.
    w._worker.wait(5000)
    _app.processEvents()
    w.close()


def test_resolve_prefers_temp_jpeg():
    """temp_jpeg_path 存在时应优先于 current/original。"""
    from ui.crop_studio import CropStudio

    photo = {
        "filename": "b.NEF",
        "temp_jpeg_path": "/tmp/b_preview.jpg",
        "current_path": "/nonexist/b.NEF",
        "original_path": "/nonexist/b.NEF",
    }
    w = CropStudio(photo, get_i18n())
    assert w._resolve_image_path(photo).endswith("b_preview.jpg")
    w._worker.wait(5000)
    _app.processEvents()
    w.close()
