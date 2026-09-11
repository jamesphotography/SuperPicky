# -*- coding: utf-8 -*-
"""
合并多目录浏览下的「整种合并」与「标记为无鸟」/ Whole-species merge and
mark-as-no-bird while browsing merged directories.

这两条路径都会**真的动用户的文件**，而在合并多目录（`MergedReportDB`）视图下
此前没有任何测试覆盖。合并视图与单目录的关键差别在于：照片分属不同批次目录，
每条记录带 ``source_dir``，``_base_dir`` 指向各自的批次根而不是合并根。任何一处
把合并根当成批次根，文件就会被移到错误的位置，或者落进别的批次。

本文件钉住的事实：

1. **整种合并的作用域是全部批次**。取样池是 ``_all_photos``，在合并视图下跨全部
   批次，所以对某鸟种执行合并会同时改动每一个批次里的该鸟种照片。这多半正是
   设计意图（「把整个白鹭改成牛背鹭」字面就是全库），但它此前从未被测试固定过，
   一旦有人把取样池改成「只当前筛选」或「只当前批次」，行为会静默改变。
2. **每个批次各自移动，互不串扰**：执行体按 ``_base_dir`` 分组、每批调一次
   ``merge_bird_species``，文件必须落在各自批次目录下，合并根不得凭空长出鸟种
   目录。
3. **标记为无鸟同样按批次落位**：鸟名清空、降为 0 星（rating=-1）、移进该批次
   自己的「其他鸟类/0星_放弃」，另一个批次不受影响。

Both flows move real files and had no coverage under merged browsing. These
tests pin the per-batch routing and the fact that a whole-species merge spans
every merged batch.
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


class _LayoutOverrideConfig:
    """把 folder_layout 钉成 species-first，其余走真实配置。"""

    folder_layout = "species-first"

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _join_worker_threads(timeout: float = 5.0) -> None:
    """等后台移动线程跑完，避免「DB 已关、线程还在写」的竞态。"""
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


def _make_batch(root: str, batch: str, rows: list) -> None:
    """
    在 root/batch 下建一个批次：真实文件 + 真实 report.db。

    参数 / Parameters:
    rows (list): [(filename, 鸟种中文名, 鸟种英文名, rating)]
    """
    from tools.report_db import ReportDB

    batch_dir = os.path.join(root, batch)
    db = ReportDB(batch_dir)
    for filename, cn, en, rating in rows:
        rel = os.path.join(cn, f"{rating}星_优选", filename)
        _touch(os.path.join(batch_dir, rel))
        db.insert_photo({
            "filename": filename,
            "current_path": rel,
            "original_path": rel,
            "bird_species_cn": cn,
            "bird_species_en": en,
            "rating": rating,
            "has_bird": 1,
        })
    db.close()


def _merged_browser(root: str, batches: list, monkeypatch):
    """
    建一个真实 ResultsBrowserWindow，载入合并视图，并把弹窗都替身掉。

    返回 (window, reported)：reported 收集结果提示框的文本。
    """
    import ui.bird_species_edit_dialog as edit_mod
    import ui.custom_dialogs as dialogs_mod
    import advanced_config as cfg_mod
    import ui.results_browser_window as rbw

    reported: list = []
    _real_cfg = cfg_mod.get_advanced_config()
    monkeypatch.setattr(
        cfg_mod, "get_advanced_config", lambda: _LayoutOverrideConfig(_real_cfg)
    )
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "question",
        staticmethod(lambda *a, **k: dialogs_mod.StyledMessageBox.Yes),
    )
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "information",
        staticmethod(lambda parent, title, message, *a, **k: reported.append(message)),
    )

    win = rbw.ResultsBrowserWindow()
    win._load_merged(root, [os.path.join(root, b) for b in batches])
    win._apply_filters(win._filter_panel.get_filters())
    return win, reported


def _stub_species_dialog(monkeypatch, cn: str, en: str, latin: str = ""):
    """把选鸟弹窗替换成「用户选了指定鸟种并点了确定」。"""
    import ui.bird_species_edit_dialog as edit_mod

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None):
            self.session_species = list(session_species or [])
            self.exclude_species = list(exclude_species or [])
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, latin

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(edit_mod, "BirdSpeciesEditDialog", _Stub)


def _pick(win, filename: str) -> dict:
    return next(p for p in win._filtered_photos if p["filename"] == filename)


# ── 整种合并 / Whole-species merge ──────────────────────────────────────────

def test_merge_spans_every_batch_and_moves_within_each(tmp_path, monkeypatch):
    """
    整种合并作用于**全部批次**，且每批的文件都落在各自批次目录下。

    取样池是跨批次的 _all_photos，所以「把整个白鹭改成牛背鹭」会同时动到 day1
    与 day2。这条同时钉住两件事：作用域确实是全库，以及文件没有被移到合并根或
    错误的批次。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "白鹭", "Little Egret", 2),
                               ("C.NEF", "苍鹭", "Grey Heron", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, reported = _merged_browser(root, ["day1", "day2"], monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "A.NEF"))
        _join_worker_threads()

        # 两个批次的白鹭都移到了各自批次的新鸟种目录
        assert os.path.exists(os.path.join(root, "day1", "牛背鹭", "3星_优选", "A.NEF"))
        assert os.path.exists(os.path.join(root, "day2", "牛背鹭", "2星_良好", "B.NEF"))
        # 合并根下不得凭空长出鸟种目录
        assert not os.path.exists(os.path.join(root, "牛背鹭"))
        # 非目标鸟种原地不动
        assert os.path.exists(os.path.join(root, "day2", "苍鹭", "3星_优选", "C.NEF"))

        rows = {p["filename"]: p for p in win._db.get_all_photos()}
        assert rows["A.NEF"]["bird_species_cn"] == "牛背鹭"
        assert rows["B.NEF"]["bird_species_cn"] == "牛背鹭"
        assert rows["C.NEF"]["bird_species_cn"] == "苍鹭"
    finally:
        _join_worker_threads()
        win.close()


def test_merge_target_pool_is_not_limited_to_the_clicked_batch(tmp_path, monkeypatch):
    """
    点 day2 的照片发起合并，day1 的同鸟种照片同样要被改到。

    与上一条方向相反：确认作用域不依赖用户从哪个批次发起。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, _ = _merged_browser(root, ["day1", "day2"], monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "B.NEF"))
        _join_worker_threads()

        assert {p["bird_species_cn"] for p in win._db.get_all_photos()} == {"牛背鹭"}
        assert os.path.exists(os.path.join(root, "day1", "牛背鹭", "3星_优选", "A.NEF"))
        assert os.path.exists(os.path.join(root, "day2", "牛背鹭", "3星_优选", "B.NEF"))
    finally:
        _join_worker_threads()
        win.close()


def test_merge_refreshes_all_photos_cache(tmp_path, monkeypatch):
    """合并后 _all_photos 必须反映新鸟名（报告与 eBird 导出读它）。"""
    from core.report_export import aggregate

    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, _ = _merged_browser(root, ["day1", "day2"], monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "A.NEF"))
        _join_worker_threads()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        names = [b.name_cn for b in aggregate(rows, include_gps=False).species]
        assert names == ["牛背鹭"], f"报告鸟种名录不对: {names}"
    finally:
        _join_worker_threads()
        win.close()


# ── 标记为无鸟 / Mark as no bird ────────────────────────────────────────────

def test_mark_no_bird_lands_in_its_own_batch(tmp_path, monkeypatch):
    """
    标记为无鸟：鸟名清空、降为 0 星，并移进**该批次自己**的「其他鸟类/0星_放弃」。

    它比改鸟种多做两步（改 rating、换目标目录），所以要单独确认这两步在合并
    视图下也按批次落位，没有跑到合并根或另一个批次。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "白鹭", "Little Egret", 3)])

    win, _ = _merged_browser(root, ["day1", "day2"], monkeypatch)
    try:
        target = _pick(win, "A.NEF")
        win._mark_photos_no_bird([target])
        _join_worker_threads()
        # 标记走后台线程 + 跨线程信号收尾，处理一次事件循环让刷新落地
        QApplication.processEvents()

        rows = {p["filename"]: p for p in win._db.get_all_photos()}
        assert not (rows["A.NEF"]["bird_species_cn"] or "")
        assert rows["A.NEF"]["rating"] == -1
        assert rows["A.NEF"]["has_bird"] == 0

        # 另一个批次完全不受影响
        assert rows["B.NEF"]["bird_species_cn"] == "白鹭"
        assert rows["B.NEF"]["rating"] == 3
        assert os.path.exists(os.path.join(root, "day2", "白鹭", "3星_优选", "B.NEF"))

        # 文件落在 day1 自己的「其他鸟类/0星_放弃」下，合并根下不得出现
        moved = os.path.join(root, "day1", "其他鸟类", "0星_放弃", "A.NEF")
        assert os.path.exists(moved), f"没落在本批次的放弃目录: {moved}"
        assert not os.path.exists(os.path.join(root, "其他鸟类"))
    finally:
        _join_worker_threads()
        win.close()


def test_mark_no_bird_removes_it_from_the_merged_report(tmp_path, monkeypatch):
    """
    标记为无鸟后，报告的鸟种名录里不得再有它。

    报告按 has_bird 过滤（report_export.aggregate 的 bird_rows），这条确认
    has_bird=0 一路传到了合并视图的 _all_photos。
    """
    from core.report_export import aggregate

    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "苍鹭", "Grey Heron", 3)])

    win, _ = _merged_browser(root, ["day1", "day2"], monkeypatch)
    try:
        win._mark_photos_no_bird([_pick(win, "A.NEF")])
        _join_worker_threads()
        QApplication.processEvents()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        names = [b.name_cn for b in aggregate(rows, include_gps=False).species]
        assert names == ["苍鹭"], f"无鸟的照片还在报告名录里: {names}"
    finally:
        _join_worker_threads()
        win.close()
