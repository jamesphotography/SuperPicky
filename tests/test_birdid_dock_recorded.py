# -*- coding: utf-8 -*-
"""
识鸟面板「历史结果回显」测试 / BirdID dock recorded-result tests.

约定：把一张已处理过的照片拖进面板时，直接回显上次处理的结果与 crop_debug
预览图，不重跑模型；未处理过的照片仍走实时识别；结果区始终留「重新识别」的出口。

Contract: dropping an already-processed photo replays the recorded result and
its crop_debug preview without re-running any model; unknown photos still go to
live identification; the results area always offers a re-identify escape hatch.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QPushButton

_app = QApplication.instance() or QApplication([])

from test_processed_lookup import _make_processed_dir


def _write_real_jpeg(path):
    """写一张真 JPEG，让 QPixmap 能加载（fixture 默认写的是占位字节）。
    Write a real JPEG so QPixmap can load it (the fixture writes placeholders)."""
    pixmap = QPixmap(8, 8)
    pixmap.fill()
    assert pixmap.save(str(path), "JPEG")


def _make_dock(monkeypatched_identify):
    """建面板并把实时识别替换成记录调用的替身。
    Build the dock with live identification replaced by a recording stub."""
    from ui.birdid_dock import BirdIDDockWidget

    dock = BirdIDDockWidget()
    dock._start_identify = monkeypatched_identify
    return dock


def _result_widgets(dock):
    """取出结果区当前的控件列表 / Current widgets in the results area."""
    layout = dock.results_layout
    return [layout.itemAt(i).widget() for i in range(layout.count())]


def test_dropping_processed_photo_replays_record_without_identifying(tmp_path):
    """已处理照片：显示历史鸟种，且不得启动识别线程。
    A processed photo shows its recorded species and starts no identification."""
    from ui.birdid_dock import ResultCard

    photo = _make_processed_dir(tmp_path)
    _write_real_jpeg(tmp_path / ".superpicky" / "cache" / "crop_debug" / "DSC00014.jpg")

    calls = []
    dock = _make_dock(lambda path: calls.append(path))
    try:
        dock.on_file_dropped(photo)

        assert calls == [], "历史结果已足够，不应再跑模型 / must not re-run the model"
        cards = [w for w in _result_widgets(dock) if isinstance(w, ResultCard)]
        assert len(cards) == 1
        assert cards[0].cn_name == "青脚鹬"
        assert round(cards[0].confidence) == 90
        assert dock.identify_results[0]["en_name"] == "Common Greenshank"
    finally:
        dock.close()


def test_recorded_result_shows_crop_debug_preview(tmp_path):
    """图片区显示的是 crop_debug 里的裁切图，而不是原 RAW。
    The preview area shows the stored crop_debug image, not the RAW."""
    photo = _make_processed_dir(tmp_path)
    crop = tmp_path / ".superpicky" / "cache" / "crop_debug" / "DSC00014.jpg"
    _write_real_jpeg(crop)

    dock = _make_dock(lambda path: None)
    try:
        dock.on_file_dropped(photo)
        assert dock.preview_label.isVisible() or dock._current_pixmap is not None
        assert dock._current_pixmap is not None
        assert dock._current_pixmap.size() == QPixmap(str(crop)).size()
    finally:
        dock.close()


def test_recorded_result_offers_reidentify(tmp_path):
    """结果区必须留「重新识别」按钮，否则已处理照片再也无法实时识别。
    A re-identify button must remain, or processed photos could never be
    analysed live from this panel again."""
    photo = _make_processed_dir(tmp_path)
    _write_real_jpeg(tmp_path / ".superpicky" / "cache" / "crop_debug" / "DSC00014.jpg")

    calls = []
    dock = _make_dock(lambda path: calls.append(path))
    try:
        dock.on_file_dropped(photo)
        buttons = [w for w in _result_widgets(dock) if isinstance(w, QPushButton)]
        assert buttons, "results area should offer a re-identify button"

        buttons[-1].click()
        assert calls == [photo], "点「重新识别」应真正跑一次实时识别 / should run live ID"
    finally:
        dock.close()


def test_unprocessed_photo_still_runs_live_identification(tmp_path):
    """没有历史记录的照片照常走实时识别，行为不变。
    A photo with no record still goes through live identification."""
    loose = tmp_path / "DSC12345.jpg"
    _write_real_jpeg(loose)

    calls = []
    dock = _make_dock(lambda path: calls.append(path))
    try:
        dock.on_file_dropped(str(loose))
        assert calls == [str(loose)]
    finally:
        dock.close()
