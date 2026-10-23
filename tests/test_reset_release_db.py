# -*- coding: utf-8 -*-
"""
#146 重置后 .superpicky 里残留 3 个报告文件的回归测试。

根因：结果浏览器「返回主窗口」只隐藏窗口、不关 report.db 连接；Windows 上开着的文件删不掉，
rmtree 删到 report.db / -wal / -shm 就中断，兜底的 `rm -rf` 在 Windows 上又不存在。
修法：重置前让浏览器释放数据库；删除改用跨平台的重试 + 去只读属性。

Regression tests for #146: the hidden results browser kept report.db open,
so on Windows the reset could not delete it (and the `rm -rf` fallback does
not exist there). The browser now releases the DB before a reset, and the
.superpicky folder is removed with a cross-platform retry helper.
"""
import os
import stat
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def test_remove_superpicky_dir_missing_is_ok(tmp_path):
    """目录不存在算成功 / A missing folder counts as removed."""
    from core.superpicky_dir import remove_superpicky_dir

    assert remove_superpicky_dir(str(tmp_path)) is True


def test_remove_superpicky_dir_removes_report_files(tmp_path):
    """report.db 及其 -wal / -shm 一并删掉 / Report DB and its WAL files go too."""
    from core.superpicky_dir import remove_superpicky_dir

    sp = tmp_path / ".superpicky"
    (sp / "cache" / "temp_preview").mkdir(parents=True)
    for name in ("report.db", "report.db-wal", "report.db-shm"):
        (sp / name).write_text("x", encoding="utf-8")

    assert remove_superpicky_dir(str(tmp_path)) is True
    assert not sp.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 只读目录语义 / POSIX read-only dir semantics")
def test_remove_superpicky_dir_clears_readonly(tmp_path):
    """
    只读子目录里的文件 shutil.rmtree 删不掉；帮手去掉只读后重试能删干净。
    A read-only subfolder defeats plain rmtree; the helper clears it and retries.
    """
    from core.superpicky_dir import remove_superpicky_dir

    locked = tmp_path / ".superpicky" / "cache"
    locked.mkdir(parents=True)
    (locked / "preview.jpg").write_text("x", encoding="utf-8")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x：不能在里面删文件 / cannot unlink inside
    try:
        assert remove_superpicky_dir(str(tmp_path)) is True
        assert not (tmp_path / ".superpicky").exists()
    finally:
        if locked.exists():
            locked.chmod(stat.S_IRWXU)


def _wait_grid_built(win, timeout: float = 5.0) -> None:
    """等网格建完 / Pump events until the grid is built."""
    grid = win._thumb_grid
    deadline = time.time() + timeout
    while time.time() < deadline:
        _app.processEvents()
        if grid._photos and grid._current_batch_idx >= len(grid._photos) and not grid._pending_photos:
            return
        time.sleep(0.01)
    raise AssertionError("grid not built")


def test_browser_release_directory_closes_db(tmp_path):
    """
    重置前浏览器要能释放 report.db（隐藏状态下也不再占着连接）。
    The browser releases report.db before a reset, even while hidden.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    open(os.path.join(root, "IMG_0.CR3"), "w").close()
    db.insert_photo({"filename": "IMG_0", "current_path": "IMG_0.CR3", "original_path": "IMG_0.CR3",
                     "rating": 3, "has_bird": 1})
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    _wait_grid_built(win)
    held = win._db
    assert held is not None

    win._go_back_to_main()  # 与用户操作一致：先返回主窗口（只是隐藏）/ hide, like the user did
    win.release_directory()

    assert win._db is None
    assert held._conn is None, "数据库连接应已关闭 / connection must be closed"
    win.close()
