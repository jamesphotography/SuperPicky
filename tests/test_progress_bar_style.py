# -*- coding: utf-8 -*-
"""
全局样式里的进度条高度:只有主窗口那条是 3px 细线,其它进度条要容得下百分比。

此前 GLOBAL_STYLE 对所有 QProgressBar 锁死 3px,结果浏览器的进度对话框(删除 /
复制 / 改鸟种 / 导出报告 / 导入照片)和视频窗口的进度条都被压成一条绿线,
百分比文字看不到。

Progress bar height in GLOBAL_STYLE: only the main window's bar is a 3px
line; every other bar must fit its percentage text. A blanket 3px cap used
to squash all browser progress dialogs and the video window's bar.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QProgressBar, QProgressDialog,
                               QVBoxLayout, QWidget)

_app = QApplication.instance() or QApplication([])

from ui.styles import GLOBAL_STYLE  # noqa: E402


def _styled_root() -> QWidget:
    root = QWidget()
    root.setStyleSheet(GLOBAL_STYLE)
    root.resize(460, 200)
    QVBoxLayout(root)
    return root


def test_generic_bar_fits_percentage_text():
    root = _styled_root()
    bar = QProgressBar()
    bar.setValue(40)
    root.layout().addWidget(bar)
    root.show()
    _app.processEvents()
    assert bar.height() >= 14
    root.close()


def test_progress_dialog_bar_fits_percentage_text():
    root = _styled_root()
    root.show()
    dlg = QProgressDialog("…", "取消", 0, 10, root)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(4)
    dlg.show()
    _app.processEvents()
    bar = dlg.findChild(QProgressBar)
    assert bar is not None and bar.height() >= 14
    dlg.close()
    root.close()


def test_main_window_bar_stays_thin_line():
    root = _styled_root()
    bar = QProgressBar()
    bar.setObjectName("mainProgressBar")
    bar.setTextVisible(False)
    root.layout().addWidget(bar)
    root.show()
    _app.processEvents()
    assert bar.height() <= 4
    root.close()


def test_main_window_uses_thin_bar_object_name():
    """主窗口进度条必须带 mainProgressBar,否则会变成粗条 / keep the objectName."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert 'setObjectName("mainProgressBar")' in src


def test_styled_progress_dialog_children_do_not_overlap():
    """
    结果浏览器的进度框:文字 / 进度条 / 取消按钮自上而下不重叠,改文字后亦然。

    QProgressDialog 自带布局在 macOS 上间距为 -1、高度没算进 44px 的按钮,
    三者重叠且进度条被压扁(只剩一条绿线、百分比被按钮盖住)。

    The browser's progress dialogs: label / bar / button stacked without
    overlap, also after setLabelText (Qt's own layout overlapped on macOS).
    """
    from ui.custom_dialogs import StyledProgressDialog
    from PySide6.QtWidgets import QLabel, QPushButton
    root = _styled_root()
    root.show()
    dlg = StyledProgressDialog("正在把 45 张照片移到回收站…", "取消", 0, 100, root)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(44)
    dlg.setLabelText("正在处理 纹翅食蜜鸟 Bar-winged Honeyeater (Western) 很长很长的名字")
    _app.processEvents()
    label, bar, button = (dlg.findChild(QLabel), dlg.findChild(QProgressBar),
                          dlg.findChild(QPushButton))
    gl, gb, gc = label.geometry(), bar.geometry(), button.geometry()
    assert gl.bottom() < gb.top() and gb.bottom() < gc.top()
    assert bar.height() >= 14
    assert gc.bottom() < dlg.height() and gl.right() < dlg.width()
    dlg.close()
    root.close()


def test_browser_uses_styled_progress_dialog():
    """结果浏览器不得再直接用 QProgressDialog / no raw QProgressDialog(...)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "ui" / "results_browser_window.py").read_text(encoding="utf-8")
    assert "QProgressDialog(" not in src
