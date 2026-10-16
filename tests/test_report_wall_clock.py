# -*- coding: utf-8 -*-
"""完成报告显示开始/结束墙钟时间，供用户对表自验总耗时。

The completion report shows wall-clock start/end times so users can verify
the total duration against a real clock.
"""
import inspect
import json
import re

from ui.main_window import _format_wall_clock


def test_format_wall_clock_is_hh_mm_ss():
    """时间戳格式化为 HH:MM:SS。"""
    import time

    out = _format_wall_clock(time.time())
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", out), out


def test_format_wall_clock_zero_returns_placeholder():
    """时间戳缺失(0)时返回占位符而不是 1970 年的误导时间。"""
    assert _format_wall_clock(0) == "--:--:--"


def test_locales_have_start_end_keys():
    """中英文语言包都必须有 report.start_at / report.end_at 且可格式化。"""
    for locale in ("locales/zh_CN.json", "locales/en_US.json"):
        with open(locale, encoding="utf-8") as f:
            data = json.load(f)
        report = data["report"]
        assert "start_at" in report, f"{locale} 缺 report.start_at"
        assert "end_at" in report, f"{locale} 缺 report.end_at"
        assert "12:34:56" in report["start_at"].format(time="12:34:56")
        assert "12:34:56" in report["end_at"].format(time="12:34:56")


def test_gui_report_shows_start_end_rows():
    """GUI 完成报告必须包含开始/结束时间两行（在总耗时旁边）。"""
    import ui.main_window as mw

    src = inspect.getsource(mw.SuperPickyMainWindow._show_statistics_report)
    assert "report.start_at" in src and "report.end_at" in src
    assert "_format_wall_clock" in src
