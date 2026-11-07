# -*- coding: utf-8 -*-
"""多目录列表控件测试 / Tests for FolderListWidget."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core import folder_queue
from ui.custom_dialogs import StyledMessageBox
from ui.folder_list_widget import FolderListWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app, monkeypatch):
    # macOS 的 tmp_path 位于 /private 下会被判为危险目录 / tmp_path is "dangerous" on macOS
    monkeypatch.setattr(folder_queue, "is_dangerous_root", lambda p: (False, ""))
    calls = []
    monkeypatch.setattr(StyledMessageBox, "warning",
                        staticmethod(lambda parent, title, msg, *a, **k: calls.append(msg)))
    w = FolderListWidget()
    w.dialog_calls = calls
    return w


def _photo_dir(root, name):
    d = root / name
    d.mkdir()
    (d / "a.jpg").write_bytes(b"x")
    return str(d)


def test_try_add_appends_and_emits(widget, tmp_path):
    seen = []
    widget.foldersChanged.connect(seen.append)
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    assert widget.try_add(a) is None
    assert widget.try_add(b) is None
    assert widget.folders() == [folder_queue.normalize_folder(a), folder_queue.normalize_folder(b)]
    assert len(seen) == 2 and len(seen[1]) == 2


def test_try_add_rejects_with_dialog(widget, tmp_path):
    a = _photo_dir(tmp_path, "a")
    widget.try_add(a)
    seen = []
    widget.foldersChanged.connect(seen.append)
    assert widget.try_add(a) == folder_queue.REJECT_DUPLICATE
    empty = tmp_path / "empty"
    empty.mkdir()
    assert widget.try_add(str(empty)) == folder_queue.REJECT_NO_PHOTOS
    assert len(widget.folders()) == 1
    assert len(widget.dialog_calls) == 2
    assert seen == []


def test_set_folders_skips_missing_silently(widget, tmp_path):
    a = _photo_dir(tmp_path, "a")
    gone = str(tmp_path / "gone")
    missing = widget.set_folders([a, gone, a])
    assert missing == [gone]
    assert widget.folders() == [folder_queue.normalize_folder(a)]
    assert widget.dialog_calls == []


def test_remove_button_removes_row(widget, tmp_path):
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    widget.try_add(a)
    widget.try_add(b)
    seen = []
    widget.foldersChanged.connect(seen.append)
    widget.remove_buttons()[0].click()
    assert widget.folders() == [folder_queue.normalize_folder(b)]
    assert seen[-1] == widget.folders()


def test_disable_editing(widget, tmp_path):
    widget.try_add(_photo_dir(tmp_path, "a"))
    widget.set_enabled_editing(False)
    assert not widget.add_button().isEnabled()
    assert not widget.remove_buttons()[0].isEnabled()
    widget.set_enabled_editing(True)
    assert widget.add_button().isEnabled()
