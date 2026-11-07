# -*- coding: utf-8 -*-
"""
删除方式可选:移到回收站(默认,可找回)/ 永久删除(快,不可恢复)。

安全约定(用户 2026-11-07 拍板):
- 确认框三选一,默认按钮是「移到回收站」;
- 「以后不再询问」只在选回收站时生效,永久删除永远要确认;
- 永久删除快捷键 ⌘⌥⌫ / Shift+Delete 与右键「永久删除」总会先确认,
  即使「删除前确认」已关闭。

Delete mode is selectable: Trash (default, recoverable) or permanent (fast,
irreversible). "Don't ask again" only applies to Trash; permanent deletes
always ask, even with confirmation turned off.
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.results_browser_window as rbw  # noqa: E402
from ui.fullscreen_viewer import is_permanent_delete_shortcut  # noqa: E402


# ── 永久删除执行 / permanent removal ─────────────────────────────────────


def test_remove_many_deletes_files_and_refuses_dirs(tmp_path):
    a = tmp_path / "a.NEF"
    a.write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    res = rbw._remove_many([str(a), str(sub), str(tmp_path / "missing.NEF"), ""])
    assert res == [True, False, False, False]
    assert not a.exists() and sub.is_dir()


def test_permanent_jobs_never_touch_trash(tmp_path, monkeypatch):
    """永久删除不得经回收站 / permanent never goes through the trash."""
    def _no_trash(paths):
        raise AssertionError("trash used for a permanent delete")

    monkeypatch.setattr(rbw, "_move_many_to_trash", _no_trash)
    main, xmp, cache = (tmp_path / n for n in ("IMG.NEF", "IMG.xmp", "c.jpg"))
    for p in (main, xmp, cache):
        p.write_bytes(b"x")
    out = rbw._trash_photo_jobs([(str(main), [str(xmp)], [str(cache)])], permanent=True)
    assert out == [True]
    assert os.listdir(tmp_path) == []


# ── 快捷键 / shortcut ─────────────────────────────────────────────────────


def _key(key, mods) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, mods)


def test_permanent_shortcut_detection():
    if sys.platform == "darwin":
        assert is_permanent_delete_shortcut(_key(Qt.Key_Backspace, Qt.ControlModifier | Qt.AltModifier))
        assert not is_permanent_delete_shortcut(_key(Qt.Key_Backspace, Qt.ControlModifier))
    else:
        assert is_permanent_delete_shortcut(_key(Qt.Key_Delete, Qt.ShiftModifier))
    assert not is_permanent_delete_shortcut(_key(Qt.Key_Delete, Qt.NoModifier))


# ── 确认框 / confirmation dialog ──────────────────────────────────────────


@pytest.fixture
def win():
    w = rbw.ResultsBrowserWindow()
    yield w
    w.close()


@pytest.fixture
def cfg():
    from advanced_config import get_advanced_config
    c = get_advanced_config()
    c.set_delete_confirm(True)
    return c


def _answer(monkeypatch, key, check: bool = False, seen: dict = None):
    """
    模拟用户在删除确认框里选某项(可同时勾选「不再询问」);key=None 为按 Esc。
    Simulate choosing an option (optionally ticking the checkbox); None = Esc.
    """
    from ui.custom_dialogs import DeleteConfirmDialog

    def _exec(self):
        if seen is not None:
            seen["default"] = self.default_key()
            seen["keys"] = self.button_keys()
            seen["has_checkbox"] = self.has_checkbox()
        if check and self._checkbox is not None:
            self._checkbox.setChecked(True)
        if key is None:
            self.reject()
        else:
            self.choose(key)
        return 0

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _exec)


def test_three_way_dialog_defaults_to_trash(win, cfg, monkeypatch):
    seen = {}
    _answer(monkeypatch, "trash", seen=seen)
    assert win._confirm_delete([{}], [("", [], [])]) == "trash"
    assert seen["default"] == "trash"
    assert set(seen["keys"]) == {"trash", "permanent", "cancel"}
    assert seen["has_checkbox"] is True


def test_dont_ask_again_only_applies_to_trash(win, cfg, monkeypatch):
    _answer(monkeypatch, "permanent", check=True)
    assert win._confirm_delete([{}], [("", [], [])]) == "permanent"
    assert cfg.delete_confirm is True          # 永久删除不能关掉确认 / never disables

    _answer(monkeypatch, "trash", check=True)
    assert win._confirm_delete([{}], [("", [], [])]) == "trash"
    assert cfg.delete_confirm is False


def test_confirm_off_trashes_silently_but_permanent_still_asks(win, cfg, monkeypatch):
    from ui.custom_dialogs import DeleteConfirmDialog
    cfg.set_delete_confirm(False)

    def _boom(self):
        raise AssertionError("dialog shown")

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _boom)
    assert win._confirm_delete([{}], [("", [], [])]) == "trash"

    seen = {}
    _answer(monkeypatch, "cancel", seen=seen)
    assert win._confirm_delete([{}], [("", [], [])], permanent=True) is None
    # 永久删除确认的默认按钮是「取消」,且没有「不再询问」/ default Cancel, no checkbox
    assert seen["default"] == "cancel"
    assert seen["has_checkbox"] is False


def test_cancel_and_escape_return_none(win, cfg, monkeypatch):
    _answer(monkeypatch, "cancel")
    assert win._confirm_delete([{}], [("", [], [])]) is None
    _answer(monkeypatch, None)
    assert win._confirm_delete([{}], [("", [], [])]) is None


def test_summary_counts_files_and_size(win, tmp_path):
    main, xmp = tmp_path / "IMG.NEF", tmp_path / "IMG.xmp"
    main.write_bytes(b"x" * 2048)
    xmp.write_bytes(b"x" * 1024)
    text = win._delete_summary([(str(main), [str(xmp)], [])])
    assert "2" in text and "1" in text      # 2 个文件,1 张照片 / 2 files, 1 photo


# ── 窗口层:永久删除走 os.remove / window: permanent path ─────────────────


def test_window_permanent_delete_removes_files(tmp_path, monkeypatch):
    from tools.report_db import ReportDB
    root = str(tmp_path)
    db = ReportDB(root)
    for i in range(3):
        open(os.path.join(root, f"IMG_{i}.CR3"), "w").close()
        open(os.path.join(root, f"IMG_{i}.xmp"), "w").close()
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.CR3",
                         "original_path": f"IMG_{i}.CR3", "rating": 3, "has_bird": 1})
    db.close()

    def _no_trash(paths):
        raise AssertionError("trash used for a permanent delete")

    monkeypatch.setattr(rbw, "_move_many_to_trash", _no_trash)
    asked = []
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete",
                        lambda self, photos, jobs, permanent=False, **_kw: asked.append(permanent) or "permanent")
    w = rbw.ResultsBrowserWindow()
    w.open_directory(root)
    w.show()
    deadline = time.time() + 5
    while time.time() < deadline and len(w._filtered_photos) < 3:
        _app.processEvents()
        time.sleep(0.01)
    w._delete_photos(list(w._filtered_photos[:2]), in_fullscreen=False, permanent=True)
    deadline = time.time() + 5
    while time.time() < deadline and w._delete_cancel is not None:
        _app.processEvents()
        time.sleep(0.01)
    assert asked == [True]
    left = sorted(f for f in os.listdir(root) if not f.startswith("."))
    assert left == ["IMG_2.CR3", "IMG_2.xmp"]
    assert [p["filename"] for p in w._filtered_photos] == ["IMG_2"]
    w.close()


def test_delete_dialog_buttons_never_overlap():
    """
    四个选项(展开 / 永久删除 / 取消 / 移到回收站)按钮互不重叠、不出界。
    QMessageBox 在 macOS 上曾把四个按钮压成等宽而互相重叠、文字被截断。
    Four choices never overlap or overflow (QMessageBox squeezed them on macOS).
    """
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QPushButton
    from ui.custom_dialogs import DeleteConfirmDialog
    dlg = DeleteConfirmDialog(
        None, "Delete Permanently", "x" * 200,
        buttons=[("permanent", "Delete Permanently", "danger"),
                 ("cancel", "Cancel", "secondary"),
                 ("trash", "Move to Recycle Bin", "primary")],
        default_key="trash", links=[("expand", "Expand Bursts to Choose")],
        checkbox_text="From now on move to Recycle Bin without asking")
    dlg.show()
    _app.processEvents()
    rects = [QRect(b.mapTo(dlg, QPoint(0, 0)), b.size())
             for b in dlg.findChildren(QPushButton) if b.isVisible()]
    assert len(rects) == 4
    for i, r in enumerate(rects):
        assert r.right() < dlg.width() and r.bottom() < dlg.height()
        for r2 in rects[i + 1:]:
            assert not r.intersects(r2)
    assert dlg.default_key() == "trash"
    dlg.close()
