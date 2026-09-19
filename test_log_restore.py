# -*- coding: utf-8 -*-
"""
处理日志回放测试 / Processing-log replay tests.

覆盖两层：
1. core.log_restore：时间戳剥离、级别反推、超长日志的首尾截断计数。
2. ui.main_window._restore_console_from_log：选中已处理目录时把日志读回控制台，
   并且回放内容与实时日志走同一套着色（``_log_line_html``）。

Covers core.log_restore (timestamp stripping, level inference, head/tail
truncation accounting) and the main window's console replay.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ────────────────────────────── core.log_restore ──────────────────────────────


def test_parse_log_line_strips_timestamp_prefix():
    """行首 "[日期 时间]" 前缀应被剥离，只保留时钟部分放到时间列。
    The "[date time]" prefix is stripped; only the clock feeds the time column."""
    from core.log_restore import parse_log_line

    line = parse_log_line("[2026-09-19 19:44:50] [001/3842] DSC06158.jpg | 0★ (锐度太低) | 1.2s")
    assert line.time_text == "19:44:50"
    assert line.message.startswith("[001/3842] DSC06158.jpg")


def test_parse_log_line_without_prefix_keeps_text():
    """多行消息的续行没有时间戳前缀，应原样保留、时间列留空。
    Continuation lines of a multi-line message keep their text, with no clock."""
    from core.log_restore import parse_log_line

    line = parse_log_line("  AI Device          : MPS (Apple Silicon)")
    assert line.time_text == ""
    assert line.message == "  AI Device          : MPS (Apple Silicon)"


def test_infer_tag_matches_live_logging_levels():
    """能精确还原的行必须还原成产品代码当时用的级别。
    Lines with a fixed format must recover the level the product code used."""
    from core.log_restore import infer_tag

    # core/photo_processor.py 用 "species" 输出识鸟结果
    assert infer_tag("  🐦 Bird ID [DSC06192.ARW]: 环颈鸻 (82%)  ○ 常见") == "species"
    assert infer_tag("  🐦 Low confidence [DSC06188.ARW]: 环颈鸻 (30% < 70.0%)") == "species"
    # _log_photo_result_simple: 3★ 走 photo_good，其余 default
    assert infer_tag("[100/3842] DSC06258.jpg | 3★ (优选) | 320ms") == "photo_good"
    assert infer_tag("[101/3842] DSC06259.jpg | 0★ (锐度太低(71<520)) | 658ms") == "default"


def test_infer_tag_symbols_and_header():
    """符号级别与「只写文件」的会话头分别归到 warning/error/success 与 muted。
    Symbol levels and the file-only session header map to their own tags."""
    from core.log_restore import infer_tag

    assert infer_tag("⚠️ 磁盘空间不足") == "warning"
    assert infer_tag("❌ 处理异常: DSC01.ARW") == "error"
    assert infer_tag("✅ 早期连拍检测完成: 483 组") == "success"
    assert infer_tag("=" * 60) == "muted"
    assert infer_tag("[UI Settings]") == "muted"
    assert infer_tag("  AI Confidence      : 50%") == "muted"
    # 其余处理流程日志用默认的 info 级别（与 _log 的默认调用一致）
    assert infer_tag("📁 共 3842 个文件待处理") == "info"


def test_read_log_lines_keeps_head_and_tail_and_counts_omitted(tmp_path):
    """超长日志只保留首尾两段，中间省略的行数必须准确报出，不做无声截断。
    Long logs keep head and tail; the omitted count must be exact."""
    from core.log_restore import read_log_lines

    log = tmp_path / "superpicky.log"
    log.write_text(
        "\n".join(f"[2026-09-19 19:44:{i % 60:02d}] line {i}" for i in range(500)),
        encoding="utf-8",
    )

    result = read_log_lines(str(log), head_limit=10, tail_limit=20)
    assert result.total == 500
    assert len(result.head) == 10 and len(result.tail) == 20
    assert result.skipped == 470
    assert result.head[0].message == "line 0"
    assert result.tail[-1].message == "line 499"


def test_read_log_lines_short_file_has_no_gap(tmp_path):
    """文件短于上限时全部保留，不应报出省略行。
    A file shorter than the caps is kept whole with nothing omitted."""
    from core.log_restore import read_log_lines

    log = tmp_path / "superpicky.log"
    log.write_text("a\nb\nc\n", encoding="utf-8")

    result = read_log_lines(str(log), head_limit=10, tail_limit=20)
    assert result.total == 3
    assert result.skipped == 0
    assert [x.message for x in result.head] == ["a", "b", "c"]


def test_find_log_file(tmp_path):
    """只认目录根部的 superpicky.log；没有就返回 None。
    Only the directory's own superpicky.log counts; absent means None."""
    from core.log_restore import find_log_file

    assert find_log_file(str(tmp_path)) is None
    (tmp_path / "superpicky.log").write_text("x\n", encoding="utf-8")
    assert find_log_file(str(tmp_path)) == str(tmp_path / "superpicky.log")


# ────────────────────────── ui.main_window 控制台回放 ──────────────────────────


def test_main_window_restores_console_from_log(tmp_path):
    """选中已处理目录时，控制台应出现日志里的逐张结果与识鸟行。
    Selecting a processed directory replays its per-photo and Bird ID lines."""
    from ui.main_window import SuperPickyMainWindow

    log = tmp_path / "superpicky.log"
    log.write_text(
        "[2026-09-19 19:44:50] [001/2] DSC06158.jpg | 3★ (优选) | 1.2s\n"
        "[2026-09-19 19:44:51]   🐦 Bird ID [DSC06158.ARW]: 勺嘴鹬 (91%)\n",
        encoding="utf-8",
    )

    window = SuperPickyMainWindow()
    try:
        window.log_text.clear()
        assert window._restore_console_from_log(str(tmp_path)) is True
        text = window.log_text.toPlainText()
        assert "DSC06158.jpg" in text
        assert "勺嘴鹬" in text
        assert "19:44:50" in text          # 原始时间戳而非「现在」/ original clock
    finally:
        window.close()


def test_main_window_restore_returns_false_without_log(tmp_path):
    """没有日志文件时安静返回 False，不往控制台写任何东西。
    With no log file it returns False and writes nothing."""
    from ui.main_window import SuperPickyMainWindow

    window = SuperPickyMainWindow()
    try:
        window.log_text.clear()
        assert window._restore_console_from_log(str(tmp_path)) is False
        assert window.log_text.toPlainText().strip() == ""
    finally:
        window.close()


def _write_log_with_summary(tmp_path, *, total=5481, trailing_restart=False):
    """
    造一份带会话结束摘要的日志 / Build a log containing a session-end summary.

    参数 / Parameters:
        tmp_path (Path): 目录 / Directory to write into.
        total (int): 摘要里的总张数 / Total photo count in the summary.
        trailing_restart (bool): True 时在摘要之后再补一个未完成的会话头 /
            Append an unfinished session header after the summary.

    返回 / Returns:
        str: 日志文件路径 / Path to the log file.
    """
    lines = [
        "[2026-09-19 19:43:38] ",
        "============================================================",
        "  [Session Start]  2026-09-19 19:43:38",
        "============================================================",
        "[2026-09-19 19:44:50] [001/2] DSC06158.jpg | 3★ (优选) | 1.2s",
        "[2026-09-19 20:28:26] ",
        "============================================================",
        "  [Session End]  2026-09-19 20:28:26",
        "============================================================",
        "[Selection Results]",
        f"  Total Photos       : {total}",
        "  ⭐⭐⭐ 3-Star       : 649 (11.8%)",
        "    └─ 🏆 Picked     : 26 (4% of 3★)",
        "  ⭐⭐   2-Star       : 1363 (24.9%)",
        "  ⭐     1-Star       : 1873 (34.2%)",
        "  0⭐    0-Star       : 1484 (27.1%)",
        "  ❌    No Bird       : 112 (2.0%)",
        "",
        "[Flags]",
        "  🦅 Flying          : 1621",
        "  🎯 Precise Focus   : 1",
        "  💡 Exposure Issue  : 0",
        "  📦 Burst Groups    : 540  (moved 2554)",
        "",
        "[BirdID Identified]",
        "  勺嘴鹬/Spoon-billed Sandpiper, 环颈鸻/Kentish Plover",
        "",
        "[Performance]",
        "  Total Time         : 2687.2s  (44.8 min)",
        "  Avg per Photo      : 0.5s",
        "============================================================",
    ]
    if trailing_restart:
        lines += [
            "[2026-09-19 21:00:00] ",
            "============================================================",
            "  [Session Start]  2026-09-19 21:00:00",
            "============================================================",
            "[2026-09-19 21:00:05] [001/9] DSC07000.jpg | 0★ (锐度太低) | 300ms",
        ]

    log = tmp_path / "superpicky.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(log)


def test_parse_session_summary_reads_last_completed_run(tmp_path):
    """会话结束摘要应还原成可直接喂给完成面板的 stats。
    The session-end summary becomes a stats dict for the completion panel."""
    from core.log_restore import parse_session_summary

    stats = parse_session_summary(_write_log_with_summary(tmp_path))

    assert stats["total"] == 5481
    assert (stats["star_3"], stats["star_2"], stats["star_1"], stats["star_0"]) == (
        649, 1363, 1873, 1484)
    assert stats["no_bird"] == 112
    assert stats["picked"] == 26
    assert stats["flying"] == 1621 and stats["focus_precise"] == 1
    assert stats["burst_groups"] == 540
    assert stats["total_time"] == 2687.2          # 总耗时只有日志里有 / log-only
    assert [s["cn_name"] for s in stats["bird_species"]] == ["勺嘴鹬", "环颈鸻"]
    assert stats["bird_species"][0]["en_name"] == "Spoon-billed Sandpiper"


def test_parse_session_summary_ignores_stale_summary_after_restart(tmp_path):
    """摘要之后又开了一轮没跑完的处理时，不能把旧摘要当成当前结果端出来。
    A summary followed by an unfinished run must not be reported as current."""
    from core.log_restore import parse_session_summary

    assert parse_session_summary(
        _write_log_with_summary(tmp_path, trailing_restart=True)) is None


def test_parse_session_summary_none_without_end_block(tmp_path):
    """只有处理过程、没有结束摘要（中途退出）时返回 None。
    A log with no session-end block (interrupted run) yields None."""
    from core.log_restore import parse_session_summary

    log = tmp_path / "superpicky.log"
    log.write_text(
        "[2026-09-19 19:43:38]   [Session Start]  2026-09-19 19:43:38\n"
        "[2026-09-19 19:44:50] [001/2] DSC06158.jpg | 3★ (优选) | 1.2s\n",
        encoding="utf-8",
    )
    assert parse_session_summary(str(log)) is None


def test_main_window_restores_completion_panel(tmp_path):
    """选中已处理目录时，右侧识鸟面板应显示上次的完成统计。
    Selecting a processed directory restores the completion panel on the right."""
    from ui.main_window import SuperPickyMainWindow

    _write_log_with_summary(tmp_path)

    window = SuperPickyMainWindow()
    try:
        assert window._restore_completion_panel(str(tmp_path)) is True
        texts = [
            window.birdid_dock.results_layout.itemAt(i).widget().text()
            for i in range(window.birdid_dock.results_layout.count())
            if hasattr(window.birdid_dock.results_layout.itemAt(i).widget(), "text")
        ]
        panel = "".join(texts)
        assert "5481" in panel                     # 总张数 / total
        assert "44.8" in panel                     # 总耗时(分钟) / total time
        assert "勺嘴鹬" in panel and "1621" in panel  # 鸟种名录与飞版计数
    finally:
        window.close()


def test_completion_panel_keeps_common_tier_zero(tmp_path):
    """罕见度档位 0（常见）是合法值，不能因为它是假值而被丢掉。
    Tier 0 (common) is a valid value and must not be dropped for being falsy."""
    from tools.report_db import ReportDB
    from ui.main_window import SuperPickyMainWindow

    _write_log_with_summary(tmp_path)
    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "DSC1", "bird_species_cn": "环颈鸻",
                     "bird_species_en": "Kentish Plover", "gbif_rarity_100": 1.0})
    db.insert_photo({"filename": "DSC2", "bird_species_cn": "勺嘴鹬",
                     "bird_species_en": "Spoon-billed Sandpiper",
                     "gbif_rarity_100": 99.0})
    db.close()

    captured = {}
    window = SuperPickyMainWindow()
    try:
        window.birdid_dock.show_completion_message = lambda stats: captured.update(stats)
        assert window._restore_completion_panel(str(tmp_path)) is True
        tiers = {s["cn_name"]: s.get("gbif_tier") for s in captured["bird_species"]}
        assert tiers["环颈鸻"] == 0, "常见档(0)被当成「没查到」丢掉了"
        assert tiers["勺嘴鹬"] == 4
    finally:
        window.close()


def test_log_line_html_shared_by_live_and_replay():
    """同一条消息，实时日志与回放必须得到同样的着色片段（共用 _log_line_html）。
    A message must render identically live and replayed (shared renderer)."""
    from ui.main_window import SuperPickyMainWindow
    from ui.styles import LOG_COLORS

    window = SuperPickyMainWindow()
    try:
        html = window._log_line_html(
            "  🐦 Bird ID [DSC06158.ARW]: 勺嘴鹬 (91%)", "species", "19:44:51"
        )
        assert LOG_COLORS["species"] in html
        assert "勺嘴鹬" in html
        assert "🐦" not in html            # emoji 统一剥离 / emoji stripped
        # 时间列可省略（回放整段插入时用于多行消息）
        assert "19:44:51" not in window._log_line_html("x" * 200, None, "19:44:51")
    finally:
        window.close()
