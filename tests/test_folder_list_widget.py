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
    empty = tmp_path / "empty"
    empty.mkdir()
    assert widget.try_add(str(empty)) == folder_queue.REJECT_NO_PHOTOS
    assert len(widget.folders()) == 1
    assert len(widget.dialog_calls) == 1
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


def test_duplicate_add_silently_ignored(widget, tmp_path):
    a = _photo_dir(tmp_path, "a")
    widget.try_add(a)
    seen = []
    widget.foldersChanged.connect(seen.append)
    before = widget.folders()
    assert widget.try_add(a) == folder_queue.REJECT_DUPLICATE
    assert widget.dialog_calls == []
    assert seen == []
    assert widget.folders() == before


def test_long_path_row_does_not_widen(widget, tmp_path):
    d = tmp_path
    for i in range(12):
        d = d / ("very_long_directory_name_%02d" % i)
    d.mkdir(parents=True)
    (d / "a.jpg").write_bytes(b"x")
    assert widget.try_add(str(d)) is None
    from ui.folder_list_widget import ElidedLabel
    lbl = widget.findChildren(ElidedLabel)[0]
    assert lbl.minimumSizeHint().width() < 300
    assert widget.minimumSizeHint().width() < 400
    assert str(d) in lbl.toolTip()


def _processed_dir(root, name, rows=0):
    """
    已处理且已整理的目录：照片在被扫描排除的星级目录里，.superpicky/report.db
    含 rows 行（rows=0 时放一个空文件，读不出张数）。
    A processed, organized folder; report.db has `rows` rows (empty file if 0).
    """
    import sqlite3
    d = root / name
    (d / "3星_优选").mkdir(parents=True)
    (d / "3星_优选" / "a.jpg").write_bytes(b"x")
    (d / ".superpicky").mkdir()
    db = d / ".superpicky" / "report.db"
    if rows:
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE photos (filename TEXT, bird_species_cn TEXT)")
        con.executemany("INSERT INTO photos VALUES (?, NULL)", [(f"p{i}",) for i in range(rows)])
        con.commit()
        con.close()
    else:
        db.write_bytes(b"")
    return str(d)


def test_processed_folder_can_be_added_with_db_count(widget, tmp_path):
    """已处理目录可加入，行内张数取自 report.db；set_folders 也保留它。"""
    from ui.folder_list_widget import ElidedLabel
    p = _processed_dir(tmp_path, "done", rows=4)
    assert widget.try_add(p) is None
    assert widget.folders() == [os.path.normpath(p)]
    assert widget.dialog_calls == []
    lbl = widget.findChildren(ElidedLabel)[0]
    assert widget.i18n.t("labels.folder_photos", n=4) in lbl.full_text()
    widget.set_folders([])
    assert widget.set_folders([p]) == []
    assert widget.folders() == [os.path.normpath(p)]


def test_processed_folder_unreadable_db_shows_label(widget, tmp_path):
    """report.db 读不出张数时行内显示「已处理」，合计按 0 计。"""
    from ui.folder_list_widget import ElidedLabel
    p = _processed_dir(tmp_path, "done", rows=0)
    assert widget.try_add(p) is None
    lbl = widget.findChildren(ElidedLabel)[0]
    assert widget.i18n.t("labels.folder_processed") in lbl.full_text()


def _batch_parent(root, name, rows=(2, 3)):
    """
    批次模式处理过的父目录：每个子批次一个 report.db（各含 rows[i] 行），父目录自己没有。
    A batch-mode parent: one report.db per sub-batch (rows[i] rows each), none of its own.
    """
    import sqlite3
    parent = root / name
    for i, n in enumerate(rows):
        day = parent / f"day{i + 1}"
        (day / "3星_优选").mkdir(parents=True)
        (day / "3星_优选" / "a.jpg").write_bytes(b"x")
        (day / ".superpicky").mkdir()
        con = sqlite3.connect(str(day / ".superpicky" / "report.db"))
        con.execute("CREATE TABLE photos (filename TEXT, bird_species_cn TEXT)")
        con.executemany("INSERT INTO photos VALUES (?, NULL)", [(f"p{k}",) for k in range(n)])
        con.commit()
        con.close()
    return str(parent)


def test_batch_parent_added_with_summed_count(widget, tmp_path):
    """批次模式父目录可加入，行内张数 = 各子批次 report.db 张数之和。"""
    from ui.folder_list_widget import ElidedLabel
    p = _batch_parent(tmp_path, "parent", rows=(2, 3))
    assert widget.try_add(p) is None
    assert widget.dialog_calls == []
    lbl = widget.findChildren(ElidedLabel)[0]
    assert widget.i18n.t("labels.folder_photos", n=5) in lbl.full_text()
    assert widget.cached_count(os.path.normpath(p)) == 5


def test_set_folders_reports_rejected(widget, tmp_path):
    """set_folders 把「不存在」以外被拒的目录记下（rejected()），不再悄悄丢掉。"""
    a = _photo_dir(tmp_path, "a")
    empty = tmp_path / "empty"
    empty.mkdir()
    missing = widget.set_folders([a, str(empty), str(tmp_path / "gone")])
    assert missing == [str(tmp_path / "gone")]
    assert widget.folders() == [os.path.normpath(a)]
    assert widget.rejected() == [(str(empty), folder_queue.REJECT_NO_PHOTOS)]
    widget.set_folders([a])
    assert widget.rejected() == []


def test_last_change_kind(widget, tmp_path):
    """last_change() 区分添加 / 移除 / 整体设置，主窗口据此判断「移除不等于选择」。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    widget.set_folders([a])
    assert widget.last_change() == "set"
    widget.try_add(b)
    assert widget.last_change() == "add"
    widget.remove_buttons()[0].click()
    assert widget.last_change() == "remove"
