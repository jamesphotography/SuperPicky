# -*- coding: utf-8 -*-
"""
合并多目录浏览时改鸟种 / Changing a species while browsing merged directories.

合并浏览（`MergedReportDB`）此前没有任何改鸟种的测试覆盖。本文件钉住两件事：

1. **功能可用**：合并视图里改鸟种必须和单目录一样生效——写对子库、把文件移到
   **该批次自己**的新鸟种目录下，并同步两份内存缓存。合并视图的照片带
   ``source_dir``，``db_key`` 因此是 ``(source_dir, filename)``，``_base_dir``
   指向各自的子目录而不是合并根；这几处任何一处错位，改动都会落到别的批次上，
   或者静默失败。

2. **`_patch_cached_photos` 的匹配边界**：库里的 ``burst_id`` 是 per-目录 的、
   跨批次会撞号（见 ``tools/merged_report_db.get_photos_by_burst_id``）。不过
   浏览器两条载入路径都会调 ``_compute_burst_ids`` 在全量照片上清空重算，所以
   载入后的 id 在当前视图内唯一——撞号经 UI 到不了（写这组测试时实测：两个批次
   各自的连拍组被重算成 1 和 2，最初按「会撞号」写的那条端到端测试因此在前提
   断言上就红了）。故这部分改为直接对函数喂手工构造的撞号数据，钉的是函数自身
   的契约，而不是一个假装可达的场景。

Merged-directory browsing had no species-change coverage. These tests pin that
a change reaches the right sub-database and sub-directory. The burst-scoping
rule is covered as a unit contract rather than an end-to-end scenario: the
browser recomputes globally-unique burst ids on load, so cross-batch collisions
are not reachable through the UI today.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """钉住 i18n 为中文：本文件断言中文鸟名与中文目录名。"""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _join_worker_threads(timeout: float = 5.0) -> None:
    """等改鸟种起的后台移动线程跑完，避免「DB 已关、线程还在写」的竞态。"""
    import threading
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not threading.current_thread() and t.daemon and t.is_alive()
                 and t.name.startswith("Thread-")]
        if not alive:
            return
        time.sleep(0.02)


def _stub_species_dialog(monkeypatch, cn: str, en: str, latin: str):
    """把选鸟弹窗替换成「用户选了指定鸟种并点了确定」。"""
    import ui.bird_species_edit_dialog as bsed

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None):
            self.session_species = list(session_species or [])
            self.exclude_species = list(exclude_species or [])
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, latin

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)


def _make_batch(root: str, batch: str, rows: list) -> None:
    """
    在 root/batch 下建一个批次：真实文件 + 真实 report.db。

    参数 / Parameters:
    rows (list): [(filename, 鸟种中文名, rating, burst_id)]；burst_id 非空时
        文件落在 ``鸟种/N星_xx/burst_<id>/`` 下，与主流程的目录形状一致。
    """
    from tools.report_db import ReportDB

    batch_dir = os.path.join(root, batch)
    db = ReportDB(batch_dir)
    for filename, bird_cn, rating, burst_id in rows:
        folder = f"{bird_cn}/{rating}星_优选"
        if burst_id is not None:
            folder = f"{folder}/burst_{burst_id}"
        rel = f"{folder}/{filename}.NEF"
        _touch(os.path.join(batch_dir, rel))
        db.insert_photo({
            "filename": filename,
            "current_path": rel,
            "original_path": rel,
            "bird_species_cn": bird_cn,
            "bird_species_en": "Old Species",
            "rating": rating,
            "has_bird": 1,
            "burst_id": burst_id,
        })
    db.close()


def _merged_browser(root: str, batches: list):
    """建一个真实 ResultsBrowserWindow 并载入合并视图。"""
    import ui.results_browser_window as rbw

    win = rbw.ResultsBrowserWindow()
    win._load_merged(root, [os.path.join(root, b) for b in batches])
    win._apply_filters(win._filter_panel.get_filters())
    return win


def _pick(win, filename: str) -> dict:
    return next(p for p in win._filtered_photos if p["filename"] == filename)


def test_species_change_works_in_merged_view(tmp_path, monkeypatch):
    """
    合并视图里改鸟种必须真正生效：库、文件、两份缓存都要跟上。

    这是用户直接问的那件事——合并报表之后能不能像单目录一样改鸟种。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [("DSC_0001", "白喉抚蜜鸟", 3, None)])
    _make_batch(root, "day2", [("DSC_0002", "噪吮蜜鸟", 3, None)])

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win = _merged_browser(root, ["day1", "day2"])
    try:
        photo = _pick(win, "DSC_0001")
        assert photo["source_dir"] == "day1"

        win._on_species_edit_requested(photo)
        _join_worker_threads()

        # 1. 文件移到了 day1 自己的新鸟种目录下，没跑到合并根或另一个批次
        assert os.path.exists(
            os.path.join(root, "day1", "家燕", "3星_优选", "DSC_0001.NEF")
        ), "文件没移到本批次的新鸟种目录"
        assert not os.path.exists(os.path.join(root, "家燕"))
        assert not os.path.exists(os.path.join(root, "day2", "家燕"))

        # 2. 写进了 day1 的子库
        rows = {p["filename"]: p for p in win._db.get_all_photos()}
        assert rows["DSC_0001"]["bird_species_cn"] == "家燕"
        assert rows["DSC_0002"]["bird_species_cn"] == "噪吮蜜鸟", "另一个批次被改了"

        # 3. 两份内存缓存都跟上了
        cached = {p["filename"]: p for p in win._all_photos}
        assert cached["DSC_0001"]["bird_species_cn"] == "家燕"
        assert cached["DSC_0002"]["bird_species_cn"] == "噪吮蜜鸟"
    finally:
        _join_worker_threads()
        win.close()


def test_merged_report_reflects_the_change(tmp_path, monkeypatch):
    """合并视图导出的报告必须显示新鸟种（与单目录同一条缺陷）。"""
    from core.report_export import aggregate

    root = str(tmp_path)
    _make_batch(root, "day1", [("DSC_0001", "白喉抚蜜鸟", 3, None)])
    _make_batch(root, "day2", [("DSC_0002", "噪吮蜜鸟", 3, None)])

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win = _merged_browser(root, ["day1", "day2"])
    try:
        win._on_species_edit_requested(_pick(win, "DSC_0001"))
        _join_worker_threads()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        names = sorted(b.name_cn for b in aggregate(rows, include_gps=False).species)
        assert names == ["噪吮蜜鸟", "家燕"], f"报告鸟种名录不对: {names}"
    finally:
        _join_worker_threads()
        win.close()


def test_patch_cached_photos_scopes_burst_match_to_one_batch():
    """
    `_patch_cached_photos` 按连拍组同步时必须同时比对 source_dir。

    这是**防御性**约定，不是在复现一个可达的现网缺陷：浏览器两条载入路径都会
    调 `_compute_burst_ids` 在全量照片上清空重算，所以载入后 burst_id 在当前
    视图内唯一，撞号在 UI 上到不了（实测：两个批次各自的组被重算成 1 和 2）。
    但库里的 burst_id 本身是 per-目录 的、确实会撞号，所以这里直接喂手工构造的
    撞号数据，钉住这个函数自身的契约——将来重算逻辑一变，这条会先红。

    Defensive contract, not a reachable UI scenario: the browser recomputes
    globally-unique burst ids on load, but the ids stored per batch do collide,
    so this pins the helper's own behaviour with hand-built colliding rows.
    """
    from ui.results_browser_window import _patch_cached_photos

    a1 = {"source_dir": "day1", "filename": "A_0001", "burst_id": 1,
          "bird_species_cn": "白喉抚蜜鸟"}
    a2 = {"source_dir": "day1", "filename": "A_0002", "burst_id": 1,
          "bird_species_cn": "白喉抚蜜鸟"}
    b1 = {"source_dir": "day2", "filename": "B_0001", "burst_id": 1,
          "bird_species_cn": "噪吮蜜鸟"}
    cache = [a1, a2, b1]

    _patch_cached_photos(a1, {"bird_species_cn": "家燕"}, cache)

    assert a1["bird_species_cn"] == "家燕"
    assert a2["bird_species_cn"] == "家燕", "本批次同组没跟上"
    assert b1["bird_species_cn"] == "噪吮蜜鸟", "串到了另一个批次"


def test_patch_cached_photos_ignores_empty_burst_id():
    """
    burst_id 为空时不得按组匹配。

    非连拍照片的 burst_id 统一是 None，若把 None 也当成一个「组」，改一张的
    鸟种会把全库的非连拍照片一次全改掉。
    """
    from ui.results_browser_window import _patch_cached_photos

    p1 = {"source_dir": "day1", "filename": "A", "burst_id": None,
          "bird_species_cn": "白喉抚蜜鸟"}
    p2 = {"source_dir": "day1", "filename": "B", "burst_id": None,
          "bird_species_cn": "噪吮蜜鸟"}

    _patch_cached_photos(p1, {"bird_species_cn": "家燕"}, [p1, p2])

    assert p1["bird_species_cn"] == "家燕"
    assert p2["bird_species_cn"] == "噪吮蜜鸟", "把无连拍的照片当成同一组全改了"


def test_burst_group_members_follow_in_same_batch(tmp_path, monkeypatch):
    """
    改一张连拍的鸟种，本批次同组其余成员的内存缓存必须一起更新。

    改鸟种在库里是整组一起改的（core.rating_mover._change_bird_species_burst），
    内存只补被点的那一张，组里其余成员就会在报告里停在旧鸟名。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [
        ("A_0001", "白喉抚蜜鸟", 3, 1),
        ("A_0002", "白喉抚蜜鸟", 3, 1),
        ("A_0003", "白喉抚蜜鸟", 3, 1),
    ])

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win = _merged_browser(root, ["day1"])
    try:
        win._on_species_edit_requested(_pick(win, "A_0001"))
        _join_worker_threads()

        assert {p["bird_species_cn"] for p in win._all_photos} == {"家燕"}
    finally:
        _join_worker_threads()
        win.close()
