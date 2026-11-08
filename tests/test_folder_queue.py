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


def test_validate_accepts_processed_folder_without_scannable_photos(tmp_path, monkeypatch):
    """只有 .superpicky/report.db、扫不到照片的已处理目录可加入；真空目录仍是 no_photos。
    A processed folder (report.db only) is accepted; an empty folder is still no_photos."""
    monkeypatch.setattr(fq, "is_dangerous_root", lambda p: (False, ""))
    done = tmp_path / "done"
    (done / ".superpicky").mkdir(parents=True)
    (done / ".superpicky" / "report.db").write_bytes(b"")
    # 照片在被扫描排除的星级目录里 / photos sit in an excluded rating folder
    (done / "3星_优选").mkdir()
    (done / "3星_优选" / "a.jpg").write_bytes(b"x")
    empty = tmp_path / "empty"
    empty.mkdir()
    resume = tmp_path / "resume"
    (resume / ".superpicky").mkdir(parents=True)
    (resume / ".superpicky" / "resume_state.json").write_text("{}", encoding="utf-8")
    assert fq.validate_new_folder([], str(done)) is None
    assert fq.validate_new_folder([], str(resume)) is None
    assert fq.validate_new_folder([], str(empty)) == fq.REJECT_NO_PHOTOS
    # 前面的检查顺序不变：重复仍先于「无照片」/ earlier checks still come first
    assert fq.validate_new_folder([str(done)], str(done)) == fq.REJECT_DUPLICATE


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


def test_processed_folders_includes_parent_with_processed_subfolders(tmp_path, monkeypatch):
    """
    批次模式的父目录把 report.db 写进子目录、不写进自己，也算有结果。
    A batch-mode parent (report.db only in sub-folders) counts as having results.
    """
    monkeypatch.setattr(fq, "classify_folder", lambda p: "fresh")
    parent = tmp_path / "parent"
    for day in ("day1", "day2"):
        (parent / day / ".superpicky").mkdir(parents=True)
        (parent / day / ".superpicky" / "report.db").write_bytes(b"")
    plain = tmp_path / "plain"
    plain.mkdir()
    q = fq.FolderQueue([str(parent), str(plain)], False)
    assert q.processed_folders() == [str(parent)]
    assert fq.folder_has_results(str(parent))
    assert sorted(fq.result_dirs(str(parent))) == sorted(
        [str(parent / "day1"), str(parent / "day2")])
    assert fq.result_dirs(str(tmp_path / "missing")) == []


def _batch_parent(root, name="parent"):
    """
    批次模式处理过的父目录：两个子批次各有 report.db，照片都在被扫描排除的星级目录里，
    父目录自己没有 report.db。
    A batch-mode processed parent: two sub-batches with report.db each, photos in
    excluded rating folders, no report.db of its own.
    """
    parent = root / name
    for day in ("day1", "day2"):
        (parent / day / ".superpicky").mkdir(parents=True)
        (parent / day / ".superpicky" / "report.db").write_bytes(b"")
        (parent / day / "3星_优选").mkdir()
        (parent / day / "3星_优选" / "a.jpg").write_bytes(b"x")
    return parent


def test_validate_accepts_batch_parent_with_processed_subfolders(tmp_path, monkeypatch):
    """
    父目录在批次模式下处理过（report.db 只在子目录里）：扫不到照片也可加入，不能判成 no_photos。
    A batch-processed parent (report.db only in sub-folders) is accepted, not no_photos.
    """
    monkeypatch.setattr(fq, "is_dangerous_root", lambda p: (False, ""))
    parent = _batch_parent(tmp_path)
    assert fq.classify_folder(str(parent)) == fq.STATE_FRESH
    assert fq.validate_new_folder([], str(parent)) is None


# ========== 批次模式处理过的父目录 / Batch-mode processed parents ==========

def _batch_parent(root, name="parent", fresh_day=False):
    """
    批次模式处理过的父目录：day1/day2 各有 report.db，照片都在星级目录里（扫描排除），
    父目录自己没有 report.db。fresh_day=True 时再加一个还没处理、有照片的 day3。
    A batch-mode processed parent; fresh_day adds an unprocessed day3 with photos.
    """
    parent = root / name
    for day in ("day1", "day2"):
        (parent / day / ".superpicky").mkdir(parents=True)
        (parent / day / ".superpicky" / "report.db").write_bytes(b"")
        (parent / day / "3星_优选").mkdir()
        (parent / day / "3星_优选" / "a.jpg").write_bytes(b"x")
    if fresh_day:
        (parent / "day3").mkdir()
        (parent / "day3" / "b.jpg").write_bytes(b"x")
    return os.path.normpath(str(parent))


def test_queue_state_batch_parent_processed(tmp_path):
    """结果全在子目录、扫不到照片的父目录算「已处理」；classify_folder 仍为 fresh。"""
    parent = _batch_parent(tmp_path)
    assert fq.classify_folder(parent) == fq.STATE_FRESH
    assert fq.queue_state(parent) == fq.STATE_PROCESSED


def test_queue_state_partly_processed_parent_stays_fresh(tmp_path):
    """还有能扫到的照片（没处理完的子目录）的父目录仍要处理（fresh）。"""
    parent = _batch_parent(tmp_path, fresh_day=True)
    assert fq.queue_state(parent) == fq.STATE_FRESH


def test_queue_state_plain_states_unchanged(tmp_path):
    """普通目录：fresh / processed / resume 与 classify_folder 一致。"""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "a.jpg").write_bytes(b"x")
    done = tmp_path / "done"
    (done / ".superpicky").mkdir(parents=True)
    (done / ".superpicky" / "report.db").write_bytes(b"")
    resume = tmp_path / "resume"
    (resume / ".superpicky").mkdir(parents=True)
    (resume / ".superpicky" / "resume_state.json").write_text("{}", encoding="utf-8")
    for d in (fresh, done, resume):
        assert fq.queue_state(str(d)) == fq.classify_folder(str(d))
    assert fq.queue_state(str(tmp_path / "missing")) == fq.STATE_FRESH


def test_folder_queue_batch_parent_skip_or_reprocess(tmp_path):
    """FolderQueue：批次父目录默认 skip（结果 skipped），全部重新处理时 reprocess。"""
    parent = _batch_parent(tmp_path)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "a.jpg").write_bytes(b"x")
    q = fq.FolderQueue([parent, str(fresh)], reprocess_all=False)
    assert q.plan == [(parent, "skip"), (str(fresh), "process")]
    assert q.outcomes[0].status == "skipped"
    q2 = fq.FolderQueue([parent, str(fresh)], reprocess_all=True)
    assert q2.plan == [(parent, "reprocess"), (str(fresh), "process")]


def test_folder_queue_uses_given_states(tmp_path):
    """传入预检算好的 states 时直接采用，不重新判定（预检框与队列计划必然一致）。"""
    a = tmp_path / "a"
    a.mkdir()
    q = fq.FolderQueue([str(a)], reprocess_all=False, states={str(a): fq.STATE_PROCESSED})
    assert q.plan == [(str(a), "skip")]
