# -*- coding: utf-8 -*-
"""降噪对比模式流程 / denoise compare-mode flow (headless)."""
import os

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
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

    w._enter_compare_mode()
    # 进入「自动修图」对比:激活、切到对比视图、选项含降噪+调色
    assert w._enhance_active is True
    assert w._center_stack.currentWidget() is w._compare_view
    opts = w._current_enhance_opts()
    assert opts is not None and opts.denoise_on is True and opts.color_on is True
    assert abs(opts.denoise_strength - 0.70) < 1e-6  # 降噪默认 70
    assert abs(opts.color_strength - 0.40) < 1e-6    # 调色默认 40

    # 防抖 400ms + 后台 worker → after 应被替换为常数 7
    _spin_until(lambda: w._compare_view._after is not None
                and not np.array_equal(
                    _qpix_first_px(w._compare_view._after),
                    _qpix_first_px(w._compare_view._before)))
    assert w._compare_view._after is not None

    # 退出对比模式:回裁剪页,但降噪已启用 → 导出仍生效(选项不变 None)
    w._exit_compare_mode()
    assert w._enhance_active is False
    assert w._center_stack.currentWidget() is w._canvas
    assert w._current_enhance_opts() is not None  # 完成后导出仍降噪 / persists

    # 两个滑块都拉 0 → 不修图导出 / both sliders 0 means export unchanged
    w._denoise_slider.setValue(0)
    w._color_slider.setValue(0)
    assert w._current_enhance_opts() is None

    w.close()  # 清理后台线程 / stop background threads


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="样片缺失 / sample missing")
def test_preview_uses_crop_region(monkeypatch):
    """预览源应是裁剪区(分析图坐标),而非整帧。"""
    monkeypatch.setattr(
        crop_studio, "advise_crops",
        lambda p: crop_studio.CropAdviceResult(status="no_bird", bird_count=0))
    photo = {"filename": "06.jpg", "temp_jpeg_path": _SAMPLE,
             "current_path": _SAMPLE, "original_path": _SAMPLE}
    w = crop_studio.CropStudio(photo, get_i18n())
    # 注入已知尺寸分析图 + 裁剪框 / inject known analysis image + crop box
    w._analysis_bgr = np.zeros((400, 600, 3), np.uint8)
    w._current_box = (100, 50, 300, 250)  # 200x200 区域 / 200x200 region
    crop = w._current_crop_bgr()
    assert crop.shape[0] == 200 and crop.shape[1] == 200
    # 无裁剪框 → 整图 / no box -> whole frame
    w._current_box = None
    full = w._current_crop_bgr()
    assert full.shape[0] == 400 and full.shape[1] == 600
    w.close()


def test_divider_moves_only_when_grabbed():
    """分割线只在按住线附近时才进入拖动;远处按下不拖动线(适应模式不动)。"""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    v = crop_studio._BeforeAfterView()
    v.resize(400, 400)
    v.set_images(np.zeros((400, 400, 3), np.uint8),
                 np.full((400, 400, 3), 200, np.uint8))
    v._split = 0.5  # 分割线在 x≈200 / divider at x≈200
    assert abs(v._split_x() - 200) <= 2

    def press(x):
        e = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 200),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        v.mousePressEvent(e)

    press(200)                     # 按在线上 → 拖动分割线
    assert v._drag_mode == "divider"
    v.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(200, 200),
                                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

    press(40)                      # 远离线,适应模式 → 不拖动(None)
    assert v._drag_mode is None


def _qpix_first_px(pm):
    """取 QPixmap 左上角像素 RGB,用于判断 after 是否已更新。"""
    img = pm.toImage()
    c = img.pixelColor(0, 0)
    return (c.red(), c.green(), c.blue())
