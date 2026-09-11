# -*- coding: utf-8 -*-
"""
整种合并确认弹窗的批次提示 / Batch hint in the whole-species merge confirmation.

合并多目录浏览时，整种合并的作用域是**全部批次**，但确认弹窗的目标目录清单是
相对各自批次的路径再去重——day1 与 day2 的照片若星级相同，只显示一行
``牛背鹭/3星_优选``，看起来像一个文件夹，实际是两个日期目录下各一份。张数是准确
的全库口径，操作本身也正确，但用户可能以为只动了当前这批。

本文件钉住加上批次提示后的行为，其中最重要的一条是**单目录模式逐字不变**：绝大
多数用户从不用合并视图，不能为了合并模式的提示把他们的弹窗改坏。

Pins the batch hint added to the merge confirmation. The single-directory
rendering must stay byte-identical: most users never open the merged view.
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
    """钉住 i18n 为中文：本文件断言中文文案。"""
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


def _make_batch(root: str, batch: str, rows: list) -> None:
    """在 root/batch 下建一个批次；batch 为 "" 时直接建在 root 下（单目录用）。"""
    from tools.report_db import ReportDB

    batch_dir = os.path.join(root, batch) if batch else root
    db = ReportDB(batch_dir)
    for filename, cn, en, rating in rows:
        rel = os.path.join(cn, f"{rating}星_优选", filename)
        _touch(os.path.join(batch_dir, rel))
        db.insert_photo({
            "filename": filename, "current_path": rel, "original_path": rel,
            "bird_species_cn": cn, "bird_species_en": en,
            "rating": rating, "has_bird": 1,
        })
    db.close()


def _browser(root: str, batches, monkeypatch):
    """
    建浏览器窗口并把弹窗替身掉，返回 (window, bodies)。

    确认框一律返回 No：本文件只关心弹窗**正文**，不需要真的移动文件，
    这样测试也快得多。bodies 收集每次确认框的正文。

    参数 / Parameters:
    batches: 批次名列表 → 合并视图；None → 单目录视图。
    """
    import ui.custom_dialogs as dialogs_mod
    import advanced_config as cfg_mod
    import ui.results_browser_window as rbw

    bodies: list = []
    _real_cfg = cfg_mod.get_advanced_config()
    monkeypatch.setattr(
        cfg_mod, "get_advanced_config", lambda: _LayoutOverrideConfig(_real_cfg)
    )

    def _capture(parent, title, message, *a, **k):
        bodies.append(message)
        return dialogs_mod.StyledMessageBox.No

    monkeypatch.setattr(dialogs_mod.StyledMessageBox, "question",
                        staticmethod(_capture))

    win = rbw.ResultsBrowserWindow()
    if batches is None:
        win._load_single(root)
    else:
        win._load_merged(root, [os.path.join(root, b) for b in batches])
        win._apply_filters(win._filter_panel.get_filters())
    return win, bodies


def _stub_species_dialog(monkeypatch, cn: str, en: str):
    """把选鸟弹窗替换成「用户选了指定鸟种并点了确定」。"""
    import ui.bird_species_edit_dialog as edit_mod

    class _Stub:
        def __init__(self, parent=None, session_species=None, exclude_species=None):
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, ""

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(edit_mod, "BirdSpeciesEditDialog", _Stub)


def _pick(win, filename: str) -> dict:
    return next(p for p in win._filtered_photos if p["filename"] == filename)


# ── 批次名收集 / Collecting batch labels ────────────────────────────────────

def test_batch_labels_empty_without_source_dir():
    """单目录模式的照片没有 source_dir，必须返回空列表——空列表即「不显示提示」。"""
    from ui.results_browser_window import _merge_batch_labels

    assert _merge_batch_labels([{"filename": "A"}, {"filename": "B"}]) == []


def test_batch_labels_are_deduped_and_sorted():
    """同批次多张只算一个，且顺序稳定（否则同一批照片两次打开弹窗顺序会变）。"""
    from ui.results_browser_window import _merge_batch_labels

    photos = [
        {"source_dir": "day2"}, {"source_dir": "day1"},
        {"source_dir": "day2"}, {"source_dir": "day1"},
    ]
    assert _merge_batch_labels(photos) == ["day1", "day2"]


def test_batch_labels_ignore_blank_source_dir():
    """空白 source_dir 不是一个批次，不能混进清单。"""
    from ui.results_browser_window import _merge_batch_labels

    photos = [{"source_dir": "day1"}, {"source_dir": ""}, {"source_dir": "   "}]
    assert _merge_batch_labels(photos) == ["day1"]


# ── 弹窗正文 / The confirmation body ────────────────────────────────────────

def test_single_directory_body_has_no_batch_hint(tmp_path, monkeypatch):
    """
    单目录模式的弹窗必须与改动前逐字相同。

    这是本次改动最重要的一条约束：绝大多数用户从不用合并视图，不能为了合并模式
    的提示把他们看到的弹窗改坏。
    """
    root = str(tmp_path)
    _make_batch(root, "", [("A.NEF", "白鹭", "Little Egret", 3),
                           ("B.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, bodies = _browser(root, None, monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "A.NEF"))

        assert len(bodies) == 1
        body = bodies[0]
        assert "批次" not in body, f"单目录弹窗混进了批次提示: {body}"
        assert "目标目录：" in body
        assert "（各批次下）" not in body
        assert "共 2 张" in body
    finally:
        win.close()


def test_merged_body_names_every_batch(tmp_path, monkeypatch):
    """跨批次时正文要说明涉及几个批次、分别是哪些。"""
    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3)])
    _make_batch(root, "day2", [("B.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, bodies = _browser(root, ["day1", "day2"], monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "A.NEF"))

        body = bodies[0]
        assert "2 个批次" in body, body
        assert "day1" in body and "day2" in body, body
        # 目录清单仍是相对路径去重，但标题点明它在各批次下各有一份
        assert "（各批次下）" in body, body
        assert "牛背鹭/3星_优选" in body, body
    finally:
        win.close()


def test_merged_single_batch_has_no_batch_hint(tmp_path, monkeypatch):
    """
    合并视图里只选了一个目录时不显示批次提示——「分布在 1 个批次」是噪音。
    """
    root = str(tmp_path)
    _make_batch(root, "day1", [("A.NEF", "白鹭", "Little Egret", 3),
                               ("B.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, bodies = _browser(root, ["day1"], monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "A.NEF"))

        body = bodies[0]
        assert "批次" not in body, f"单批次不该出现批次提示: {body}"
        assert "目标目录：" in body
    finally:
        win.close()


def test_many_batches_are_truncated(tmp_path, monkeypatch):
    """
    批次很多时清单要截断。

    批次名可能是「2026/03/15」这类嵌套路径，全列会把弹窗撑爆；截断后仍要说清
    总数，用户才知道自己动的是 8 天而不是 6 天。
    """
    root = str(tmp_path)
    names = [f"day{i}" for i in range(1, 9)]          # 8 个批次
    for n in names:
        _make_batch(root, n, [(f"{n}.NEF", "白鹭", "Little Egret", 3)])

    _stub_species_dialog(monkeypatch, "牛背鹭", "Cattle Egret")
    win, bodies = _browser(root, names, monkeypatch)
    try:
        win._on_merge_species_requested(_pick(win, "day1.NEF"))

        body = bodies[0]
        assert "8 个批次" in body, body
        assert "另 2 个" in body, f"没有截断或没说清剩余数量: {body}"
        assert "day7" not in body, f"超出上限的批次不该逐个列出: {body}"
    finally:
        win.close()
