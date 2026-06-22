# -*- coding: utf-8 -*-
"""自动修图(降噪+调色 合一)对比模式:选项携带两者(P3b/简化)。"""
import os

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.i18n import get_i18n  # noqa: E402
from ui import crop_studio  # noqa: E402

_app = QApplication.instance() or QApplication([])
_SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "docs", "Promotion", "wechat", "articles",
                       "v4.3.0-rarity", "06.jpg")


def _studio(monkeypatch):
    monkeypatch.setattr(crop_studio, "advise_crops",
                        lambda p: crop_studio.CropAdviceResult(status="no_bird", bird_count=0))
    return crop_studio.CropStudio(
        {"filename": "x", "temp_jpeg_path": _SAMPLE,
         "current_path": _SAMPLE, "original_path": _SAMPLE}, get_i18n())


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="样片缺失")
def test_enhance_mode_carries_denoise_and_color(monkeypatch):
    w = _studio(monkeypatch)
    w._enter_compare_mode()
    assert w._enhance_active is True
    assert w._center_stack.currentWidget() is w._compare_view
    # 降噪、调色两个滑块都在条上 / both sliders present
    assert w._color_slider.isVisibleTo(w._enhance_panel)
    assert w._denoise_slider.isVisibleTo(w._enhance_panel)
    opts = w._current_enhance_opts()
    assert opts is not None and opts.denoise_on is True and opts.color_on is True
    assert abs(opts.color_strength - 0.40) < 1e-6   # 调色默认 40%
    # 预览选项 == 导出选项(都是降噪+调色) / preview == export
    p = w._preview_opts()
    assert p is not None and p.denoise_on and p.color_on
    # 退出后仍对导出生效(engaged 持久) / persists after exit
    w._exit_compare_mode()
    assert w._current_enhance_opts() is not None
    # 两个滑块都拉 0 → 不修图 / both 0 -> None
    w._denoise_slider.setValue(0)
    w._color_slider.setValue(0)
    assert w._current_enhance_opts() is None
    w.close()
