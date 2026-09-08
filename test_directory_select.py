# -*- coding: utf-8 -*-
"""
多目录合并前的目录选择测试。

背景：一个父目录下可能有几十个已处理批次（一天分上下午就是两个），用户往往
只想合并其中某几天，而不是全部——25 个目录一万五千张照片全加载既慢又不是
他要的。所以打开父目录时先让用户挑，挑完只加载选中的。

概览数字（每个目录多少张、多少种）直接从各自 report.db 只读查询，不经
ReportDB——后者会触发 schema 升级与建索引，为了一个列表数字不值当，也不该
在用户还没决定要不要打开时就去改人家的库。

Before merging, let the user pick which batches to include; the per-directory
counts come from a plain read-only query rather than opening a full ReportDB,
which would migrate schemas for a directory the user may not even open.
"""
import os
import sqlite3
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


def _seed(root: str, name: str, rows):
    """造一个含 report.db 的批次目录。rows=[(filename, species, rating)]"""
    from tools.report_db import PHOTO_COLUMNS

    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, ".superpicky"), exist_ok=True)
    con = sqlite3.connect(os.path.join(d, ".superpicky", "report.db"))
    defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
    defs += [f"{n} {t}" for n, t, _ in PHOTO_COLUMNS]
    con.execute(f"CREATE TABLE photos ({', '.join(defs)})")
    for filename, species, rating in rows:
        con.execute(
            "INSERT INTO photos (filename, bird_species_cn, rating) VALUES (?,?,?)",
            (filename, species, rating))
    con.commit()
    con.close()
    return d


# ── 概览统计 / Per-directory summary ────────────────────────────────────────

def test_summary_reports_photo_and_species_counts(tmp_path):
    """每个目录给出照片数与鸟种数，供用户判断要不要选它。"""
    from tools.merged_report_db import summarize_directories

    root = str(tmp_path)
    a = _seed(root, "2026-09-01", [("A1", "家燕", 3), ("A2", "家燕", 2),
                                   ("A3", "大山雀", 3)])
    b = _seed(root, "2026-09-02", [("B1", "绿鹂", 3)])

    rows = summarize_directories([a, b])

    assert [r["name"] for r in rows] == ["2026-09-01", "2026-09-02"]
    assert rows[0]["photos"] == 3 and rows[0]["species"] == 2
    assert rows[1]["photos"] == 1 and rows[1]["species"] == 1


def test_summary_ignores_unidentified_photos_in_species_count(tmp_path):
    """没识别出鸟种的照片计入张数，但不计入鸟种数。"""
    from tools.merged_report_db import summarize_directories

    root = str(tmp_path)
    d = _seed(root, "d", [("A1", "家燕", 3), ("A2", None, 0), ("A3", "", 1)])

    rows = summarize_directories([d])

    assert rows[0]["photos"] == 3
    assert rows[0]["species"] == 1


def test_summary_survives_an_unreadable_directory(tmp_path):
    """
    某个目录的库损坏或缺失时，其余目录照常给出数字 —— 不能因为一个坏目录
    让整个选择框打不开。
    """
    from tools.merged_report_db import summarize_directories

    root = str(tmp_path)
    good = _seed(root, "good", [("A1", "家燕", 3)])
    broken = os.path.join(root, "broken")
    os.makedirs(os.path.join(broken, ".superpicky"), exist_ok=True)
    with open(os.path.join(broken, ".superpicky", "report.db"), "w") as f:
        f.write("this is not a database")

    rows = {r["name"]: r for r in summarize_directories([good, broken])}

    assert rows["good"]["photos"] == 1
    assert rows["broken"]["photos"] is None, "读不出就是 None，不能瞎报 0"


def test_summary_does_not_modify_the_databases(tmp_path):
    """
    只读概览：不得触发 schema 升级或建索引。

    用户可能只是打开父目录看看，最后一个都不选；为了列表上的数字就去改人家
    几十个库（升级 schema、建索引、写 WAL）是不可接受的。
    """
    from tools.merged_report_db import summarize_directories

    root = str(tmp_path)
    d = _seed(root, "d", [("A1", "家燕", 3)])
    db_path = os.path.join(d, ".superpicky", "report.db")
    before = os.path.getmtime(db_path), os.path.getsize(db_path)

    summarize_directories([d])

    assert (os.path.getmtime(db_path), os.path.getsize(db_path)) == before, \
        "概览查询不得改动数据库文件"


# ── 嵌套批次 / Nested batches ───────────────────────────────────────────────

def test_nested_batch_inside_a_processed_one_is_dropped(tmp_path):
    """
    已处理目录内部再嵌一个已处理目录时，只保留外层 —— 内层多半是同一批照片
    的重复副本，两个都算会让合计张数与鸟种数虚高。

    现网实例：2026-03-07 与 2026-03-07/2026-03-07 是同一批 195 张，文件名逐个
    相同；全选合并时被算了两遍。

    A processed directory nested inside another is almost always a duplicate of
    the same batch; counting both inflates every total.
    """
    from tools.merged_report_db import find_processed_subdirs

    root = str(tmp_path)
    outer = _seed(root, "2026-03-07", [("A1", "家燕", 3)])
    _seed(outer, "2026-03-07", [("A1", "家燕", 3)])      # 内层同名副本

    found = find_processed_subdirs(root)

    assert found == [outer], f"应只保留外层，实际 {found}"


def test_nested_batch_is_kept_when_the_parent_is_not_processed(tmp_path):
    """
    外层目录本身没被处理过时，内层就是真正的批次，必须保留。

    现网实例：2026-03-06/Bird、2026-06-08/115JMSZ9 —— 外层只是相机卡或分类
    文件夹，没有 report.db。
    """
    from tools.merged_report_db import find_processed_subdirs

    root = str(tmp_path)
    parent = os.path.join(root, "2026-06-08")
    os.makedirs(parent, exist_ok=True)
    inner = _seed(parent, "115JMSZ9", [("A1", "家燕", 3)])

    found = find_processed_subdirs(root)

    assert found == [inner], f"外层未处理时应保留内层，实际 {found}"


# ── 目录列表管理器 / Directory list manager ─────────────────────────────────
#
# 用户的真实用法不是「打开一个父目录、在它下面挑」——很多时候要合并的目录根本
# 不在同一个父目录下（不同盘、不同年份文件夹）。所以对话框是一个可增删的目录
# 清单：反复点「添加目录…」把任意位置的目录攒进来，再勾选要合并的。
#
# The picker is a directory list the user builds up: the batches to merge often
# live in unrelated folders (or on different volumes), so confining the choice
# to one parent folder does not match how people actually shoot.

def _dialog(entries=None):
    """造一个对话框，默认从空列表开始。"""
    from tools.i18n import get_i18n
    from ui.directory_select_dialog import DirectorySelectDialog

    return DirectorySelectDialog(get_i18n(), entries or [], None)


def test_adding_a_processed_directory_appends_a_row(tmp_path):
    """添加一个本身就是批次的目录 —— 直接进列表。"""
    d = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])

    dlg = _dialog()
    try:
        added = dlg.add_directory(d)

        assert added == 1
        assert dlg.directories() == [d]
        assert dlg.selected_directories() == [d], "新加入的目录默认勾选"
    finally:
        dlg.deleteLater()


def test_adding_a_parent_prompts_and_adds_all_its_batches(tmp_path):
    """
    添加的是父目录时，提示「里面有 N 个批次，全部加入吗」，确认后一次性加入。

    用户拖一个年份文件夹进来是常见的偷懒方式，不该逼他一个个加。
    """
    root = str(tmp_path / "2026")
    a = _seed(root, "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(root, "2026-09-02", [("B1", "绿鹂", 3)])

    dlg = _dialog()
    asked = []
    dlg._confirm_add_batches = lambda name, count: (asked.append((name, count)), True)[1]
    try:
        added = dlg.add_directory(root)

        assert asked == [("2026", 2)], f"应提示一次，实际 {asked}"
        assert added == 2
        assert sorted(dlg.directories()) == sorted([a, b])
    finally:
        dlg.deleteLater()


def test_declining_the_parent_prompt_adds_nothing(tmp_path):
    """用户在提示里选「否」时什么都不加 —— 父目录本身没结果可看。"""
    root = str(tmp_path / "2026")
    _seed(root, "2026-09-01", [("A1", "家燕", 3)])

    dlg = _dialog()
    dlg._confirm_add_batches = lambda name, count: False
    try:
        assert dlg.add_directory(root) == 0
        assert dlg.directories() == []
    finally:
        dlg.deleteLater()


def test_adding_a_processed_directory_does_not_prompt(tmp_path):
    """
    目录本身就是批次时不问「要不要把里面的批次都加进来」。

    已处理目录内部再嵌批次是重复副本（见嵌套测试），既然只取外层，就没有可问
    的东西；平白弹一次框只会让人以为自己选错了目录。
    """
    outer = _seed(str(tmp_path), "2026-03-07", [("A1", "家燕", 3)])
    _seed(outer, "2026-03-07", [("A1", "家燕", 3)])       # 内层副本

    dlg = _dialog()
    dlg._confirm_add_batches = lambda name, count: pytest.fail("不该弹提示")
    try:
        assert dlg.add_directory(outer) == 1
        assert dlg.directories() == [outer]
    finally:
        dlg.deleteLater()


def test_adding_a_directory_without_results_reports_it(tmp_path):
    """
    目录里没有任何选鸟结果时，明确告诉用户，而不是静悄悄地什么都不发生。
    """
    empty = tmp_path / "空目录"
    empty.mkdir()

    dlg = _dialog()
    told = []
    dlg._notify_nothing_found = lambda path: told.append(path)
    try:
        assert dlg.add_directory(str(empty)) == 0
        assert told == [str(empty)]
        assert dlg.directories() == []
    finally:
        dlg.deleteLater()


def test_adding_the_same_directory_twice_is_ignored(tmp_path):
    """
    重复添加同一个目录不produce 第二行 —— 否则它的照片会被算两遍。
    """
    d = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])

    dlg = _dialog()
    try:
        dlg.add_directory(d)
        again = dlg.add_directory(os.path.join(d, ""))   # 同一目录，尾部带分隔符

        assert again == 0
        assert dlg.directories() == [d]
    finally:
        dlg.deleteLater()


def test_same_named_directories_from_different_places_are_distinguishable(tmp_path):
    """
    两个盘上都有 2026-09-01 时，行文案必须能区分 —— 只显示目录名的话用户
    看到两行一模一样，无从判断勾掉哪一个。
    """
    a = _seed(str(tmp_path / "volA"), "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(str(tmp_path / "volB"), "2026-09-01", [("B1", "绿鹂", 3)])

    dlg = _dialog()
    try:
        dlg.add_directory(a)
        dlg.add_directory(b)

        labels = dlg.row_labels()
        assert len(labels) == 2
        assert labels[0] != labels[1], f"同名目录的行文案重复：{labels}"
    finally:
        dlg.deleteLater()


def test_removing_a_row_drops_it(tmp_path):
    """移除某一行后它不再参与合并。"""
    a = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(str(tmp_path), "2026-09-02", [("B1", "绿鹂", 3)])

    dlg = _dialog()
    try:
        dlg.add_directory(a)
        dlg.add_directory(b)

        dlg.remove_directory(a)

        assert dlg.directories() == [b]
        assert dlg.selected_directories() == [b]
    finally:
        dlg.deleteLater()


def test_summary_counts_only_checked_rows(tmp_path):
    """底部概览统计的是勾选的行，不是全部行。"""
    a = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3), ("A2", "家燕", 2)])
    b = _seed(str(tmp_path), "2026-09-02", [("B1", "绿鹂", 3)])

    dlg = _dialog()
    try:
        dlg.add_directory(a)
        dlg.add_directory(b)
        dlg.set_checked(b, False)

        assert dlg.selected_directories() == [a]
        assert "2" in dlg.selection_summary(), \
            f"应只算 a 的 2 张，实际：{dlg.selection_summary()}"
    finally:
        dlg.deleteLater()


def test_initial_entries_are_listed(tmp_path):
    """
    构造时给的目录直接成行 —— 用户先选了一个父目录、我们把它的批次预填进来，
    他再继续添加别处的目录。
    """
    from tools.merged_report_db import summarize_directories

    a = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(str(tmp_path), "2026-09-02", [("B1", "绿鹂", 3)])

    dlg = _dialog(summarize_directories([a, b]))
    try:
        assert dlg.directories() == [a, b]
        assert dlg.selected_directories() == [a, b]
    finally:
        dlg.deleteLater()


# ── 任意目录组合 / Arbitrary directory sets ─────────────────────────────────
#
# 目录可以来自任何地方，不再保证有共同父目录。合并层原先假定「都在 root_dir
# 下」，用 relpath 算 source_dir——跨盘时这个假设不成立。

def test_merge_root_is_the_common_parent(tmp_path):
    """同一父目录下的几个批次，合并根就是那个父目录。"""
    from tools.merged_report_db import merge_root

    root = str(tmp_path / "2026")
    a = _seed(root, "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(root, "2026-09-02", [("B1", "绿鹂", 3)])

    assert merge_root([a, b]) == root


def test_merge_root_survives_directories_on_different_drives(monkeypatch, tmp_path):
    """
    跨盘目录没有共同父目录（Windows 上 commonpath 直接抛 ValueError）——
    不能因此崩掉，必须给出一个可用的根。
    """
    from tools.merged_report_db import merge_root

    a = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(str(tmp_path), "2026-09-02", [("B1", "绿鹂", 3)])

    def _no_common(_paths):
        raise ValueError("Paths don't have the same drive")
    monkeypatch.setattr(os.path, "commonpath", _no_common)

    root = merge_root([a, b])

    assert root, "跨盘时也必须给出一个根"
    assert os.path.isabs(root)


def test_merging_directories_with_no_common_root(monkeypatch, tmp_path):
    """
    没有共同根时照样要能合并 —— source_dir 只需唯一且稳定，不必好看。

    原实现用 os.path.relpath(sub_dir, root_dir) 算 source_dir，Windows 上跨盘
    会抛 ValueError，整个合并直接失败；而跨盘正是这个功能的常见用法（当天导
    到移动盘、之后又导了一批到内置盘）。
    """
    from tools.merged_report_db import MergedReportDB

    a = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(str(tmp_path), "2026-09-02", [("B1", "绿鹂", 3)])

    real_relpath = os.path.relpath

    def _cross_drive(path, start=None):
        if start is not None:
            raise ValueError("path is on mount 'D:', start on mount 'C:'")
        return real_relpath(path)
    monkeypatch.setattr(os.path, "relpath", _cross_drive)

    db = MergedReportDB(str(tmp_path), [a, b])
    try:
        photos = db.get_all_photos()
    finally:
        db.close()

    assert len(photos) == 2, f"跨盘两个目录各一张，实际 {len(photos)}"
    assert len({p["source_dir"] for p in photos}) == 2, "source_dir 必须唯一"


def test_span_label_shows_the_date_range(tmp_path):
    """
    多目录合并时窗口标题给出首尾目录名 —— 合并根的名字（多半是「2026」这种
    年份文件夹，跨盘时甚至是「/」）说明不了用户看的是哪几天。
    """
    from tools.merged_report_db import merged_span_label

    root = str(tmp_path / "2026")
    dirs = [_seed(root, f"2026-09-{d:02d}", [("A", "家燕", 3)])
            for d in (7, 1, 4)]

    assert merged_span_label(dirs) == "2026-09-01 ~ 2026-09-07"


def test_span_label_of_a_single_directory_is_its_name(tmp_path):
    """只有一个目录时就显示它自己的名字，不要写成「X ~ X」。"""
    from tools.merged_report_db import merged_span_label

    d = _seed(str(tmp_path), "2026-09-01", [("A", "家燕", 3)])

    assert merged_span_label([d]) == "2026-09-01"


# ── 打开目录时的路由 / open_directory routing ───────────────────────────────

class _FakeWindow:
    """只带路由所需状态的假窗口，避免为几行判断去构造整个浏览器窗口。"""

    def __init__(self, answer=None):
        self._answer = answer
        self.asked = []
        self.no_db_hint = []

    def _ask_which_batches(self, dirs):
        self.asked.append(list(dirs))
        return list(self._answer or [])

    def _show_no_db_hint(self, directory):
        self.no_db_hint.append(directory)


def _resolve(win, directory):
    from ui.results_browser_window import ResultsBrowserWindow

    return ResultsBrowserWindow._resolve_directories(win, directory)


def test_opening_a_folder_of_batches_asks_which_ones(tmp_path):
    """父目录下有多个批次时先问用户要哪几个。"""
    root = str(tmp_path / "2026")
    a = _seed(root, "2026-09-01", [("A1", "家燕", 3)])
    b = _seed(root, "2026-09-02", [("B1", "绿鹂", 3)])

    win = _FakeWindow(answer=[a])

    assert _resolve(win, root) == [a]
    assert win.asked == [[a, b]], "应把找到的批次预填进选择框"


def test_opening_a_single_batch_does_not_ask(tmp_path):
    """
    目录本身就是一个批次时直接打开 —— 每次都弹个只有一行的选择框纯属打扰。
    """
    d = _seed(str(tmp_path), "2026-09-01", [("A1", "家燕", 3)])

    win = _FakeWindow()

    assert _resolve(win, d) == [d]
    assert win.asked == []


def test_cancelling_the_picker_opens_nothing(tmp_path):
    """用户在选择框点取消时不载入任何东西。"""
    root = str(tmp_path / "2026")
    _seed(root, "2026-09-01", [("A1", "家燕", 3)])
    _seed(root, "2026-09-02", [("B1", "绿鹂", 3)])

    win = _FakeWindow(answer=[])

    assert _resolve(win, root) == []


def test_opening_a_folder_without_results_shows_the_hint(tmp_path):
    """没有任何选鸟结果的目录给出原有提示，而不是弹一个空列表。"""
    empty = tmp_path / "空目录"
    empty.mkdir()

    win = _FakeWindow()

    assert _resolve(win, str(empty)) == []
    assert win.no_db_hint == [str(empty)]
