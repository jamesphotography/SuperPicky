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
