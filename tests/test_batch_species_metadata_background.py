# -*- coding: utf-8 -*-
"""
#147 回归测试：批量改鸟种后的元数据写入在后台线程执行，状态栏显示进度。

此前移动文件的进度框关掉之后，主线程逐张调用 ExifTool 写鸟名，界面卡住且没有
任何反馈；照片在外接盘上时要卡很久。

#147: after a batch species change, metadata is written on a worker thread
with status-bar progress instead of freezing the UI thread.
"""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _pump_until(cond, timeout: float = 3.0) -> bool:
    """处理事件直到 cond() 为真或超时 / Pump events until cond() or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window():
    import ui.results_browser_window as rbw
    win = rbw.ResultsBrowserWindow()
    yield win
    win.close()


def test_metadata_written_off_ui_thread_with_progress(window, monkeypatch):
    import ui.results_browser_window as rbw

    release = threading.Event()
    seen = {}

    def fake_write(files, new_cn, new_en, old_cn, old_en, cfg,
                   metadata_writer=None, progress_cb=None):
        seen["thread"] = threading.current_thread()
        release.wait(3)
        for i, _ in enumerate(files, 1):
            progress_cb(i, len(files))
        seen["files"] = list(files)

    monkeypatch.setattr(rbw, "_write_species_metadata", fake_write)
    progress = []
    window.species_metadata_progress.connect(lambda d, t: progress.append((d, t)))

    files = ["/x/a.CR3", "/x/b.CR3", "/x/c.CR3"]
    start = time.time()
    window._write_species_metadata_in_background(files, "灰鹤", "Common Crane")
    # 写入还卡着时调用方已经返回 / returns while the write is still blocked
    assert time.time() - start < 1.0
    assert "files" not in seen

    _pump_until(lambda: progress)
    assert window._status_bar.currentMessage() == window.i18n.t(
        "browser.species_metadata_progress").format(done=0, total=3)

    release.set()
    assert _pump_until(lambda: (3, 3) in progress)
    assert seen["thread"] is not threading.main_thread()
    assert seen["files"] == files
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    # 写完后状态栏恢复为张数统计 / status bar back to the counts
    assert "/" not in window._status_bar.currentMessage()


def test_failed_write_still_restores_status(window, monkeypatch):
    """写入抛异常也要发「写完」，状态栏不能一直停在进度上。"""
    import ui.results_browser_window as rbw

    def boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(rbw, "_write_species_metadata", boom)
    progress = []
    window.species_metadata_progress.connect(lambda d, t: progress.append((d, t)))
    window._write_species_metadata_in_background(["/x/a.CR3"], "灰鹤", "Common Crane")
    assert _pump_until(lambda: (1, 1) in progress)
