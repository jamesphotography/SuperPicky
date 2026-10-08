# -*- coding: utf-8 -*-
"""
识鸟面板「粘贴识别」与 Windows 截图收尾测试 / BirdID dock paste & Windows snip tests.

动因（issue #113）：Windows 上点「截图识别」能调出系统截图工具，截完却不识别——
程序靠轮询剪贴板等图片，而那条路径既没有日志也没有任何退路。这里锁住三件事：
1. 面板支持 Ctrl+V（macOS 为 ⌘V）粘贴剪贴板里的图片或复制的图片文件来识别，
   自动识别没触发时用户仍有手动出口；
2. Windows 截图等待期间，剪贴板变化信号与轮询两条路都能收尾，但同一张截图只识别一次；
3. 超时后停止等待，之后再出现的剪贴板变化不会突然触发识别。

Motivation (issue #113): on Windows the snipping tool opens but the capture is
never identified. These tests pin a manual paste fallback, a single-shot
completion shared by the clipboard signal and the poll, and a clean timeout.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _make_dock(calls: list):
    """
    建面板，并把 on_file_dropped 换成只记录路径的替身。

    参数 / Parameters:
    calls (list): 收集被识别文件路径的列表 / Collects the identified paths.

    返回 / Returns:
    BirdIDDockWidget: 已替换识别入口的面板 / The dock with a recording stub.
    """
    from ui.birdid_dock import BirdIDDockWidget

    dock = BirdIDDockWidget()
    dock.on_file_dropped = lambda path, force_identify=False: calls.append(path)
    return dock


def _image() -> QImage:
    """一张 8×8 的纯色图 / An 8x8 solid image."""
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0xFF3366)
    return img


# ── 1. 手动粘贴 / Manual paste ────────────────────────────────────────────────

def test_paste_image_from_clipboard_identifies_it():
    """剪贴板里是图片：存成临时 PNG 并交给识别。"""
    calls = []
    dock = _make_dock(calls)
    try:
        QApplication.clipboard().setImage(_image())
        assert dock._paste_from_clipboard() is True
        assert len(calls) == 1
        assert calls[0].endswith(".png") and os.path.getsize(calls[0]) > 0
    finally:
        dock.close()


def test_paste_copied_image_file_identifies_that_file(tmp_path):
    """在资源管理器 / Finder 里复制的图片文件：直接识别原文件，不另存。"""
    photo = tmp_path / "鸟.jpg"
    assert _image().save(str(photo), "JPEG")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(photo))])

    calls = []
    dock = _make_dock(calls)
    try:
        QApplication.clipboard().setMimeData(mime)
        assert dock._paste_from_clipboard() is True
        assert calls == [str(photo)]
    finally:
        dock.close()


def test_paste_without_image_does_nothing_but_says_so():
    """剪贴板里只有文字：不识别，并在状态栏说明原因。"""
    calls = []
    dock = _make_dock(calls)
    try:
        QApplication.clipboard().setText("not an image")
        assert dock._paste_from_clipboard() is False
        assert calls == []
        assert dock.status_label.text() == dock.i18n.t("birdid.paste_no_image")
    finally:
        dock.close()


def test_paste_switches_back_to_identify_tab():
    """在「查询鸟名」页粘贴图片，应切回识鸟页再识别。"""
    calls = []
    dock = _make_dock(calls)
    try:
        dock._switch_tab(1)
        QApplication.clipboard().setImage(_image())
        dock._paste_from_clipboard()
        assert dock.stacked_widget.currentIndex() == 0
        assert len(calls) == 1
    finally:
        dock.close()


def test_paste_shortcut_is_registered():
    """面板注册了标准粘贴快捷键（Windows Ctrl+V / macOS ⌘V）。"""
    dock = _make_dock([])
    try:
        keys = [s.key() for s in dock.findChildren(QShortcut)]
        assert QKeySequence(QKeySequence.StandardKey.Paste) in keys
    finally:
        dock.close()


# ── 2. Windows 截图收尾 / Windows snip completion ─────────────────────────────

def test_win_snip_completes_once_even_if_signal_and_poll_both_fire():
    """剪贴板信号与轮询都可能看到同一张截图，只能识别一次。"""
    calls = []
    dock = _make_dock(calls)
    try:
        dock._begin_win_screenshot_wait()
        QApplication.clipboard().setImage(_image())
        dock._on_win_clipboard_changed()
        dock._poll_clipboard_for_screenshot()
        QTest.qWait(300)
        assert len(calls) == 1
        assert dock._sc_waiting is False
    finally:
        dock.close()


def test_win_snip_timeout_stops_waiting_and_ignores_late_clipboard():
    """超时后不再等待；之后剪贴板里出现图片也不会突然开始识别。"""
    calls = []
    dock = _make_dock(calls)
    try:
        dock._begin_win_screenshot_wait()
        dock._screenshot_poll_count = dock.WIN_SNIP_MAX_POLLS
        dock._poll_clipboard_for_screenshot()
        assert dock._sc_waiting is False
        assert dock.status_label.text() == dock.i18n.t("birdid.sc_timeout")

        QApplication.clipboard().setImage(_image())
        dock._on_win_clipboard_changed()
        QTest.qWait(300)
        assert calls == []
    finally:
        dock.close()
