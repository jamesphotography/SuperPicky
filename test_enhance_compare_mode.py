# -*- coding: utf-8 -*-
"""降噪对比模式流程 / denoise compare-mode flow (headless)."""
import os

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tools.i18n import get_i18n  # noqa: E402
from ui import crop_studio  # noqa: E402

_app = QApplication.instance() or QApplication([])  # QWidget 需 QApplication

_SAMPLE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs", "Promotion", "wechat", "articles", "v4.3.0-rarity", "06.jpg")


def _spin_until(pred, timeout_ms=4000):
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: pred() and loop.quit())
    t.start(20)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    t.stop()


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="样片缺失 / sample missing")
def test_enter_compare_mode_updates_after(monkeypatch):
    # 桩管线避免真模型/加速;after 设为可辨识常数
    monkeypatch.setattr(crop_studio, "_pipeline_enhance",
                        lambda rgb, opts, **kw: np.full_like(rgb, 7),
                        raising=False)
    # 桩掉重型鸟检测候选(真图会加载 YOLO,在 headless 下崩);只测修图链路
    monkeypatch.setattr(
        crop_studio, "advise_crops",
        lambda p: crop_studio.CropAdviceResult(status="no_bird", bird_count=0))
    photo = {"filename": "06.jpg", "temp_jpeg_path": _SAMPLE,
             "current_path": _SAMPLE, "original_path": _SAMPLE}
    w = crop_studio.CropStudio(photo, get_i18n())

    w._enter_enhance_mode()
    # 进入对比模式:激活、切到对比视图、选项为降噪-only
    assert w._enhance_active is True
    assert w._center_stack.currentWidget() is w._compare_view
    opts = w._current_enhance_opts()
    assert opts is not None and opts.color_on is False
    assert abs(opts.denoise_strength - 0.5) < 1e-6

    # 防抖 400ms + 后台 worker → after 应被替换为常数 7
    _spin_until(lambda: w._compare_view._after is not None
                and not np.array_equal(
                    _qpix_first_px(w._compare_view._after),
                    _qpix_first_px(w._compare_view._before)))
    assert w._compare_view._after is not None

    # 退出对比模式:回裁剪页、选项变 None
    w._exit_enhance_mode()
    assert w._enhance_active is False
    assert w._center_stack.currentWidget() is w._canvas
    assert w._current_enhance_opts() is None

    w.close()  # 清理后台线程 / stop background threads


def _qpix_first_px(pm):
    """取 QPixmap 左上角像素 RGB,用于判断 after 是否已更新。"""
    img = pm.toImage()
    c = img.pixelColor(0, 0)
    return (c.red(), c.green(), c.blue())
