# -*- coding: utf-8 -*-
"""调色对比模式:独立入口 + 选项携带调色(P3b)。"""
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
def test_color_mode_carries_color_opts(monkeypatch):
    w = _studio(monkeypatch)
    w._enter_compare_mode("color")
    assert w._enhance_active is True and w._compare_kind == "color"
    assert w._center_stack.currentWidget() is w._compare_view
    # 调色滑块可见 / color slider shown in color mode
    assert w._color_slider.isVisibleTo(w._enhance_panel)
    opts = w._current_enhance_opts()
    assert opts is not None and opts.color_on is True
    assert abs(opts.color_strength - 0.40) < 1e-6   # 默认 40%
    # 退出后调色仍对导出生效(engaged 持久) / persists after exit
    w._exit_compare_mode()
    assert w._current_enhance_opts() is not None
    # 调色滑块拉 0 → 不调色;且未进降噪 → None
    w._color_slider.setValue(0)
    assert w._current_enhance_opts() is None
    w.close()


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="样片缺失")
def test_denoise_mode_excludes_color(monkeypatch):
    """降噪模式预览只降噪(color_on=False),即便调色从未启用。"""
    w = _studio(monkeypatch)
    w._enter_compare_mode("denoise")
    popts = w._preview_opts()
    assert popts is not None and popts.color_on is False
    assert not w._color_slider.isVisibleTo(w._enhance_panel)  # 降噪模式隐藏调色滑块
    w.close()
