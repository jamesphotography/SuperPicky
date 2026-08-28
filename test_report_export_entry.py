#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出报告入口的接线测试：只验按钮存在与信号连接，不执行真实导出。

参照 test_species_merge_entry.py 的写法。

Wiring test for the export entry: verifies the button exists and is
connected, without running a real export.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


def test_export_button_wired():
    """工具栏存在导出按钮，且已连接 _export_report。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow

    window = ResultsBrowserWindow()
    try:
        assert hasattr(window, "_export_btn")
        assert callable(getattr(window, "_export_report", None))
        # isSignalConnected 是 QObject 的公开 API，比 receivers() 可靠。
        meta = window._export_btn.metaObject()
        index = meta.indexOfSignal("clicked(bool)")
        assert window._export_btn.isSignalConnected(meta.method(index))
    finally:
        window.deleteLater()


def test_export_report_guards_empty_directory():
    """未载入任何照片时，导出应安全返回而不抛异常。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.results_browser_window import ResultsBrowserWindow
    from ui import custom_dialogs

    window = ResultsBrowserWindow()
    calls = []
    original = custom_dialogs.StyledMessageBox.warning
    custom_dialogs.StyledMessageBox.warning = staticmethod(
        lambda *a, **k: calls.append(a))
    try:
        window._all_photos = []
        window._directory = ""
        window._export_report()
        assert calls, "空目录应给出提示"
    finally:
        custom_dialogs.StyledMessageBox.warning = original
        window.deleteLater()
