# -*- coding: utf-8 -*-
"""「这不是鸟」：把误检成鸟的照片标记为无鸟。

Marking a falsely-detected photo as "not a bird".

动因：YOLO 偶尔把鳄鱼之类的东西认成鸟，用户以前没有任何办法纠正这件事——
`has_bird` 只有 AI 流程写得了（ai_model.py），UI 里没有入口。降星到 -1 也只
改了星级，`has_bird` 仍是 1、鸟种名仍挂着，于是报告的「本次鸟种」名录里照样
列着那个鸟种、还算它一张（core/report_export.py 按 has_bird 过滤）。

所以「标记为无鸟」必须一次写三个字段：has_bird=0（报告不再认它）、鸟种名清空
（决定目录）、rating=-1（进废片堆）。反向操作是改鸟种——改鸟种时把 has_bird
置回 1，等于撤销。

Marking as "no bird" must write three fields at once: has_bird=0, cleared
species names, and rating=-1. Editing the species again restores has_bird=1,
which is the undo path.
"""
import os

import pytest

# 复用改鸟种回归测试的夹具与构件，避免同一套浏览器搭建逻辑写两份。
# `_pin_chinese_locale` 是 autouse fixture：本文件断言硬编码中文目录名。
from test_species_change_regression import (  # noqa: F401
    _pin_chinese_locale,
    _embedded_write_mode,
    _make_jpeg,
    _settle_grid,
    _join_worker_threads,
    _auto_confirm,
)


def _browser_with_identified_photos(root: str, specs: list):
    """
    按 specs=[(filename, rel_path, rating, burst_id)] 建库并打开浏览器。

    与改鸟种测试的同名构件唯一的区别：显式写入 has_bird=1，模拟「AI 认为
    有鸟」的初始状态——本文件测的正是把它翻成 0。

    Same as the species-edit helper but writes has_bird=1 explicitly: these
    tests are about flipping it to 0.
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    for name, rel, rating, burst in specs:
        _make_jpeg(os.path.join(root, rel))
        db.insert_photo({
            "filename": name, "current_path": rel, "original_path": rel,
            "bird_species_cn": "白喉抚蜜鸟",
            "bird_species_en": "White-throated Honeyeater",
            "rating": rating, "burst_id": burst, "has_bird": 1,
        })
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    _settle_grid(win)
    return win


def _stub_dialog_marks_no_bird(monkeypatch):
    """把选鸟弹窗替换成「用户点了『这不是鸟』」。"""
    from PySide6.QtWidgets import QDialog
    import ui.bird_species_edit_dialog as bsed

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None):
            self.selected_cn = ""
            self.selected_en = ""
            self.selected_latin = ""
            self.mark_no_bird = True

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)


def _row(root: str, filename: str) -> dict:
    """从磁盘上的库里重新读一条记录，确认写的是真库不是内存副本。"""
    from tools.report_db import ReportDB
    db = ReportDB(root)
    try:
        return next(r for r in db.get_all_photos() if r["filename"] == filename)
    finally:
        db.close()


# ── A. 弹窗必须能表达「这不是鸟」 ────────────────────────────────────────

def test_dialog_defaults_to_not_marking_no_bird(qtbot_unused=None):
    """真实弹窗默认 mark_no_bird=False，正常选鸟流程不受影响。"""
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    dialog = BirdSpeciesEditDialog()
    try:
        assert dialog.mark_no_bird is False
    finally:
        dialog.deleteLater()


def test_dialog_no_bird_button_sets_flag_and_accepts():
    """点「这不是鸟」→ mark_no_bird=True 且弹窗以 Accepted 关闭。"""
    from PySide6.QtWidgets import QDialog
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    dialog = BirdSpeciesEditDialog()
    try:
        dialog._no_bird_btn.click()
        assert dialog.mark_no_bird is True
        assert dialog.result() == QDialog.Accepted
    finally:
        dialog.deleteLater()


# ── B. 三个字段必须一起写 ────────────────────────────────────────────────

def test_mark_no_bird_writes_all_three_fields(tmp_path, monkeypatch,
                                              _embedded_write_mode):
    """has_bird=0 + 鸟种名清空 + rating=-1，一个都不能少。"""
    _stub_dialog_marks_no_bird(monkeypatch)
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    win = _browser_with_identified_photos(
        root, [("DSC_1", "白喉抚蜜鸟/3星_优选/DSC_1.jpg", 3, None)])
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        row = _row(root, "DSC_1")
        assert row["has_bird"] == 0, "报告仍会把它算进鸟种名录"
        assert not row["bird_species_cn"], "鸟种名没清空，目录归置会出错"
        assert not row["bird_species_en"]
        assert row["rating"] == -1, "没降到无鸟档"
    finally:
        _join_worker_threads()
        win.close()


def test_mark_no_bird_moves_file_into_reject_pile(tmp_path, monkeypatch,
                                                  _embedded_write_mode):
    """文件落到「其他鸟类/0星_放弃」——低星强制走其他鸟类分支。"""
    _stub_dialog_marks_no_bird(monkeypatch)
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    win = _browser_with_identified_photos(
        root, [("DSC_1", "白喉抚蜜鸟/3星_优选/DSC_1.jpg", 3, None)])
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        moved = os.path.join(root, "其他鸟类", "0星_放弃", "DSC_1.jpg")
        assert os.path.exists(moved), f"文件不在废片堆：{os.listdir(root)}"
        assert not os.path.exists(
            os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1.jpg"))
    finally:
        _join_worker_threads()
        win.close()


def test_mark_no_bird_clears_species_title_on_disk(tmp_path, monkeypatch,
                                                   _embedded_write_mode):
    """磁盘上的 XMP:Title 里不能再留着那个错鸟名。"""
    from tools.exiftool_manager import get_exiftool_manager

    _stub_dialog_marks_no_bird(monkeypatch)
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    win = _browser_with_identified_photos(
        root, [("DSC_1", "白喉抚蜜鸟/3星_优选/DSC_1.jpg", 3, None)])
    mgr = get_exiftool_manager()
    try:
        path = os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1.jpg")
        mgr.set_metadata(path, {"XMP:Title": "白喉抚蜜鸟"})

        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        moved = os.path.join(root, "其他鸟类", "0星_放弃", "DSC_1.jpg")
        # 先确认文件确实被移走：否则读一个不存在的文件也会「读不到旧鸟名」，
        # 功能没实现时这条断言会假绿。
        # Assert the move first; reading a missing file would pass vacuously.
        assert os.path.exists(moved), "文件没进废片堆，下面的断言会假绿"
        title = (mgr.read_metadata(moved, extra_args=["-XMP:Title"]) or {}).get("Title")
        assert not title or "白喉抚蜜鸟" not in str(title), f"Title 仍是 {title!r}"
    finally:
        _join_worker_threads()
        win.close()


# ── C. 批量与连拍 ────────────────────────────────────────────────────────

def test_mark_no_bird_applies_to_whole_selection(tmp_path, monkeypatch,
                                                 _embedded_write_mode):
    """勾了一串鳄鱼，一次全标掉。"""
    from ui.thumbnail_grid import _photo_key

    _stub_dialog_marks_no_bird(monkeypatch)
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    specs = [(f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/DSC_{i}.jpg", 3, None)
             for i in (1, 2, 3)]
    win = _browser_with_identified_photos(root, specs)
    try:
        picked = list(win._filtered_photos)
        for p in picked:
            win._thumb_grid._multi_selected.add(_photo_key(p))

        win._on_species_edit_requested(picked[0])
        _join_worker_threads()

        for name in ("DSC_1", "DSC_2", "DSC_3"):
            row = _row(root, name)
            assert row["has_bird"] == 0, f"{name} 的 has_bird 还是 1"
            assert row["rating"] == -1, f"{name} 的 rating 是 {row['rating']}"
    finally:
        _join_worker_threads()
        win.close()


# ── D. 不写纠错记录（与整种合并一致的既定决策）────────────────────────────

def test_mark_no_bird_writes_no_correction(tmp_path, monkeypatch,
                                           _embedded_write_mode):
    """标记无鸟不进 corrections 表——这是拍板过的决策，别顺手加回来。"""
    from tools.report_db import ReportDB

    _stub_dialog_marks_no_bird(monkeypatch)
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    win = _browser_with_identified_photos(
        root, [("DSC_1", "白喉抚蜜鸟/3星_优选/DSC_1.jpg", 3, None)])
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        # 先确认标记真的生效——否则功能没实现时 corrections 本来就是空的，
        # 这条断言毫无意义。
        # Confirm the marking happened; otherwise the emptiness proves nothing.
        assert _row(root, "DSC_1")["has_bird"] == 0

        db = ReportDB(root)
        try:
            assert db.get_corrections() == []
        finally:
            db.close()
    finally:
        _join_worker_threads()
        win.close()


# ── E. 改鸟种 = 撤销 ─────────────────────────────────────────────────────

def test_species_edit_restores_has_bird(tmp_path, monkeypatch,
                                        _embedded_write_mode):
    """标错了要能改回来：改鸟种把 has_bird 置回 1。"""
    from test_species_change_regression import _stub_species_dialog

    _auto_confirm(monkeypatch)
    root = str(tmp_path)
    win = _browser_with_identified_photos(
        root, [("DSC_1", "白喉抚蜜鸟/3星_优选/DSC_1.jpg", 3, None)])
    try:
        # 先标成无鸟
        _stub_dialog_marks_no_bird(monkeypatch)
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()
        assert _row(root, "DSC_1")["has_bird"] == 0

        # 再改成某个鸟种 → has_bird 必须回到 1
        _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        row = _row(root, "DSC_1")
        assert row["has_bird"] == 1, "改回鸟种后报告仍然不认它"
        assert row["bird_species_cn"] == "家燕"
    finally:
        _join_worker_threads()
        win.close()


def test_batch_species_edit_restores_has_bird(tmp_path, monkeypatch,
                                              _embedded_write_mode):
    """批量改鸟种同样要把 has_bird 置回 1——整种合并与多选批改都走这条路。"""
    from ui.thumbnail_grid import _photo_key
    from test_species_change_regression import _stub_species_dialog

    _auto_confirm(monkeypatch)
    root = str(tmp_path)
    specs = [(f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/DSC_{i}.jpg", 3, None)
             for i in (1, 2)]
    win = _browser_with_identified_photos(root, specs)
    try:
        # 先批量标成无鸟
        _stub_dialog_marks_no_bird(monkeypatch)
        picked = list(win._filtered_photos)
        for p in picked:
            win._thumb_grid._multi_selected.add(_photo_key(p))
        win._on_species_edit_requested(picked[0])
        _join_worker_threads()
        assert all(_row(root, n)["has_bird"] == 0 for n in ("DSC_1", "DSC_2"))

        # 再批量改成某个鸟种
        _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
        picked = list(win._filtered_photos)
        for p in picked:
            win._thumb_grid._multi_selected.add(_photo_key(p))
        win._on_species_edit_requested(picked[0])
        _join_worker_threads()

        for name in ("DSC_1", "DSC_2"):
            row = _row(root, name)
            assert row["has_bird"] == 1, f"{name} 的 has_bird 没置回"
            assert row["bird_species_cn"] == "家燕"
    finally:
        _join_worker_threads()
        win.close()
