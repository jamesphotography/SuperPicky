# -*- coding: utf-8 -*-
"""
识鸟面板完成统计反映 report.db 现状的测试 / Completion panel reflects report.db.

背景：完成统计原先照搬日志里的 ``[Session End]`` 快照，用户在结果浏览器里改鸟种、
改星级后面板仍显示改之前的结果，重开目录也一样。现在结果部分从 report.db 现算
（``core.processed_lookup.collect_completion_stats``），总耗时仍取自日志；控制台
日志保持历史原样不动。

覆盖：
1. collect_completion_stats 的口径（星级、4/5★ 并入 3★、D10 鸟种门槛、罕见度排序、
   批量模式递归汇总、无库返回 None）；
2. 主窗口恢复面板时以库为准、耗时取日志；
3. 结果浏览器关闭后的刷新：面板显示完成统计时重绘，显示别的内容时不动。

Background: the completion panel used to replay the log snapshot, so species and
rating edits made in the results browser never reached it. Results now come from
report.db while timing still comes from the log.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from test_log_restore import _write_log_with_summary


def _make_db(directory, rows):
    """
    在 directory 建 report.db 并写入 rows / Build report.db with the given rows.

    参数 / Parameters:
        directory (Path): 目录 / Target directory.
        rows (list): 每项为 insert_photo 的字段字典 / insert_photo field dicts.
    """
    from tools.report_db import ReportDB

    db = ReportDB(str(directory))
    for row in rows:
        db.insert_photo(row)
    db.close()


def _bird(filename, cn, en, rating, score=None, **extra):
    """造一条有鸟记录 / Build a bird row."""
    row = {"filename": filename, "has_bird": 1, "rating": rating,
           "bird_species_cn": cn, "bird_species_en": en}
    if score is not None:
        row["gbif_rarity_100"] = score
    row.update(extra)
    return row


def _panel_text(window):
    """取识鸟面板结果区的全部文字 / Concatenate the dock's result texts."""
    layout = window.birdid_dock.results_layout
    texts = []
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget is not None and hasattr(widget, "text"):
            texts.append(widget.text())
    return "".join(texts)


def test_completion_stats_counts_and_scope(tmp_path):
    """星级分布、飞版/精焦计数，以及 D10 鸟种门槛与罕见度排序。
    Rating buckets, flags, the D10 species threshold and rarity ordering."""
    from core.processed_lookup import collect_completion_stats

    _make_db(tmp_path, [
        _bird("A1", "环颈鸻", "Kentish Plover", 3, 1.0, is_flying=1),
        _bird("A2", "环颈鸻", "Kentish Plover", 1, 1.0),
        _bird("B1", "勺嘴鹬", "Spoon-billed Sandpiper", 2, 99.0, focus_status="BEST"),
        _bird("C1", "白鹭", "Little Egret", 1, 1.0),          # 只有 1★：不上榜
        _bird("D1", "黑脸琵鹭", "Black-faced Spoonbill", 5),   # 手动升的 5★
        {"filename": "N1", "has_bird": 0, "rating": -1},
        {"filename": "Z1", "has_bird": 1, "rating": 0},
    ])

    stats = collect_completion_stats(str(tmp_path))

    assert stats["total"] == 7
    assert (stats["star_3"], stats["star_2"], stats["star_1"], stats["star_0"]) == (2, 1, 2, 1)
    assert stats["no_bird"] == 1
    assert stats["star_3"] + stats["star_2"] + stats["star_1"] + stats["star_0"] \
        + stats["no_bird"] == stats["total"], "4/5★ 必须并入 3★，各档之和才等于总数"
    assert stats["flying"] == 1 and stats["focus_precise"] == 1

    names = [s["cn_name"] for s in stats["bird_species"]]
    assert "白鹭" not in names, "只有 1★ 的鸟种不该上榜（D10）"
    # 勺嘴鹬档位最高排第一；无档位的黑脸琵鹭排最后
    assert names == ["勺嘴鹬", "环颈鸻", "黑脸琵鹭"]
    assert stats["bird_species"][1]["gbif_tier"] == 0     # 常见档 0 不能丢
    assert "gbif_tier" not in stats["bird_species"][2]


def test_completion_stats_follows_species_edit(tmp_path):
    """改鸟种只写库；现算结果必须跟上，旧鸟种从名录里消失。
    A species edit only touches the database; the recomputed list follows it."""
    import sqlite3

    from core.processed_lookup import collect_completion_stats

    _make_db(tmp_path, [_bird("A1", "家燕", "Barn Swallow", 3, 1.0)])
    conn = sqlite3.connect(str(tmp_path / ".superpicky" / "report.db"))
    conn.execute("UPDATE photos SET bird_species_cn='白腹毛脚燕', "
                 "bird_species_en='Common House Martin' WHERE filename='A1'")
    conn.commit()
    conn.close()

    names = [s["cn_name"] for s in collect_completion_stats(str(tmp_path))["bird_species"]]
    assert names == ["白腹毛脚燕"]


def test_completion_stats_sums_batch_subdirectories(tmp_path):
    """批量模式：父目录自己的库与递归子目录的库一并汇总，星级目录不往下走。
    Batch mode sums the parent's and nested subdirectories' databases."""
    from core.processed_lookup import collect_completion_stats, find_report_dbs

    _make_db(tmp_path, [_bird("R1", "环颈鸻", "Kentish Plover", 2)])
    nested = tmp_path / "day1" / "morning"
    nested.mkdir(parents=True)
    _make_db(nested, [_bird("M1", "勺嘴鹬", "Spoon-billed Sandpiper", 3),
                      {"filename": "M2", "has_bird": 0, "rating": -1}])

    assert len(find_report_dbs(str(tmp_path))) == 2
    stats = collect_completion_stats(str(tmp_path))
    assert stats["total"] == 3
    assert stats["star_3"] == 1 and stats["star_2"] == 1 and stats["no_bird"] == 1
    assert {s["cn_name"] for s in stats["bird_species"]} == {"环颈鸻", "勺嘴鹬"}


def test_completion_stats_none_without_database(tmp_path):
    """没有任何 report.db 时返回 None，调用方退回日志快照。
    No database means None, so callers fall back to the log snapshot."""
    from core.processed_lookup import collect_completion_stats

    assert collect_completion_stats(str(tmp_path)) is None


def test_restored_panel_prefers_database_but_keeps_log_timing(tmp_path):
    """恢复面板：结果以库为准，总耗时仍来自日志。
    The restored panel uses database results and the log's total time."""
    from ui.main_window import SuperPickyMainWindow

    _write_log_with_summary(tmp_path)              # 日志：5481 张、勺嘴鹬+环颈鸻
    _make_db(tmp_path, [_bird("A1", "白腹毛脚燕", "Common House Martin", 3)])

    captured = {}
    window = SuperPickyMainWindow()
    try:
        window.birdid_dock.show_completion_message = lambda stats: captured.update(stats)
        assert window._restore_completion_panel(str(tmp_path)) is True
        assert captured["total"] == 1
        assert [s["cn_name"] for s in captured["bird_species"]] == ["白腹毛脚燕"]
        assert captured["total_time"] == 2687.2, "总耗时库里没有，必须保留日志值"
    finally:
        window.close()


def test_browser_close_refreshes_completion_panel(tmp_path):
    """浏览器关闭后，面板正显示完成统计时按库重绘。
    Closing the browser redraws the completion panel from the database."""
    import sqlite3

    from ui.main_window import SuperPickyMainWindow

    _write_log_with_summary(tmp_path)
    _make_db(tmp_path, [_bird("A1", "家燕", "Barn Swallow", 3)])

    window = SuperPickyMainWindow()
    try:
        window.directory_path = str(tmp_path)
        assert window._restore_completion_panel(str(tmp_path)) is True
        assert "家燕" in _panel_text(window)

        conn = sqlite3.connect(str(tmp_path / ".superpicky" / "report.db"))
        conn.execute("UPDATE photos SET bird_species_cn='白腹毛脚燕' WHERE filename='A1'")
        conn.commit()
        conn.close()

        window._refresh_completion_after_browse()
        _app.processEvents()                        # 让 deleteLater 生效 / flush deletes
        text = _panel_text(window)
        assert "白腹毛脚燕" in text and "家燕" not in text.replace("白腹毛脚燕", "")
    finally:
        window.close()


def test_browser_close_leaves_other_panel_content_alone(tmp_path):
    """面板正显示别的内容（如识别结果）时，关闭浏览器不得把它冲掉。
    Closing the browser must not clobber non-summary panel content."""
    from ui.main_window import SuperPickyMainWindow

    _write_log_with_summary(tmp_path)
    _make_db(tmp_path, [_bird("A1", "家燕", "Barn Swallow", 3)])

    window = SuperPickyMainWindow()
    try:
        window.directory_path = str(tmp_path)
        window._restore_completion_panel(str(tmp_path))
        assert window.birdid_dock.is_showing_completion() is True

        window.birdid_dock.clear_results()           # 模拟切到别的内容 / switch away
        assert window.birdid_dock.is_showing_completion() is False

        calls = []
        window.birdid_dock.show_completion_message = lambda stats: calls.append(stats)
        window._refresh_completion_after_browse()
        assert calls == []
    finally:
        window.close()
