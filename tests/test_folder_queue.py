# -*- coding: utf-8 -*-
"""多目录队列纯逻辑测试 / Tests for core.folder_queue (no Qt)."""
import ntpath
import os

import core.folder_queue as fq
from core.folder_queue import folders_overlap


def test_overlap_parent_child():
    assert folders_overlap("/p", "/p/c")


def test_no_overlap_siblings_prefix():
    assert not folders_overlap("/p/ab", "/p/a")


def test_cross_drive_windows_not_overlap():
    assert not folders_overlap(r"C:\a", r"D:\a", pathmod=ntpath)


def test_validate_rejections(tmp_path, monkeypatch):
    a = tmp_path / "a"
    (a / "c").mkdir(parents=True)
    # macOS 的 tmp 位于 /private 下会被判危险，这里固定为非危险
    # macOS tmp lives under /private (flagged dangerous); pin to not dangerous
    monkeypatch.setattr(fq, "is_dangerous_root", lambda p: (False, ""))
    monkeypatch.setattr(fq, "scan_directories", lambda p, max_depth=0: [object()])
    assert fq.validate_new_folder([], str(tmp_path / "nope")) == fq.REJECT_MISSING
    assert fq.validate_new_folder([str(a)], str(a) + os.sep) == fq.REJECT_DUPLICATE
    assert fq.validate_new_folder([str(a)], str(a / "c")) == fq.REJECT_OVERLAP
    monkeypatch.setattr(fq, "scan_directories", lambda p, max_depth=0: [])
    assert fq.validate_new_folder([], str(a / "c")) == fq.REJECT_NO_PHOTOS


def test_validate_dangerous(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "is_dangerous_root", lambda p: (True, "x"))
    assert fq.validate_new_folder([], str(tmp_path)) == fq.REJECT_DANGEROUS


def test_validate_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "is_dangerous_root", lambda p: (False, ""))
    monkeypatch.setattr(fq, "scan_directories", lambda p, max_depth=0: [object()])
    assert fq.validate_new_folder([], str(tmp_path)) is None


def test_classify_folder(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert fq.classify_folder(str(d)) == fq.STATE_FRESH
    (d / ".superpicky").mkdir()
    (d / ".superpicky" / "report.db").write_bytes(b"")
    assert fq.classify_folder(str(d)) == fq.STATE_PROCESSED


def test_plan_actions(monkeypatch):
    states = {"/f": "fresh", "/r": "resume", "/p": "processed"}
    monkeypatch.setattr(fq, "classify_folder", states.get)
    assert fq.FolderQueue(["/f", "/r", "/p"], False).plan == [
        ("/f", "process"), ("/r", "resume"), ("/p", "skip")]
    assert fq.FolderQueue(["/p"], True).plan == [("/p", "reprocess")]


def test_queue_order_stop_and_outcomes(monkeypatch):
    monkeypatch.setattr(fq, "classify_folder", lambda p: "fresh")
    q = fq.FolderQueue(["/1", "/2", "/3"], False)
    assert q.next_item() == ("/1", "process")
    q.record("/1", "done", {"total": 5})
    assert q.next_item() == ("/2", "process")
    q.stop()
    q.record("/2", "stopped")
    assert q.next_item() is None
    assert [o.status for o in q.outcomes] == ["done", "stopped", "not_started"]


def test_skip_folder_outcome_and_never_returned(monkeypatch):
    states = {"/a": "fresh", "/p": "processed", "/b": "fresh"}
    monkeypatch.setattr(fq, "classify_folder", states.get)
    q = fq.FolderQueue(["/a", "/p", "/b"], False)
    assert [o.status for o in q.outcomes] == ["not_started", "skipped", "not_started"]
    assert q.next_item() == ("/a", "process")
    assert q.next_item() == ("/b", "process")
    assert q.next_item() is None
    assert q.outcomes[1].status == "skipped"


def test_processed_folders_order(tmp_path, monkeypatch):
    monkeypatch.setattr(fq, "classify_folder", lambda p: "fresh")
    dirs = []
    for n in ("x", "y", "z"):
        d = tmp_path / n
        (d / ".superpicky").mkdir(parents=True)
        dirs.append(str(d))
    (tmp_path / "x" / ".superpicky" / "report.db").write_bytes(b"")
    (tmp_path / "z" / ".superpicky" / "report.db").write_bytes(b"")
    q = fq.FolderQueue(dirs, False)
    assert q.processed_folders() == [dirs[0], dirs[2]]
