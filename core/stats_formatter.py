#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 统计摘要格式化模块
CLI 和 GUI 共享输出格式
"""

from typing import Dict, List, Callable, Optional

from tools.i18n import t


def format_processing_summary(stats: Dict, include_time: bool = True) -> List[str]:
    """
    格式化处理完成摘要
    
    Args:
        stats: 统计数据字典，包含 total, star_3, star_2, star_1, star_0, 
               no_bird, picked, flying, total_time, avg_time
        include_time: 是否包含时间统计
    
    Returns:
        格式化的行列表
    """
    # 文案走语言包（cli.summary_*），跟随命令行语言；此前写死中文，且把 0 星误标成「普通」
    # Localized via cli.summary_*; the old text was hard-coded Chinese and
    # mislabelled 0★ as "average".
    lines = []
    lines.append("=" * 60)
    lines.append(t("cli.summary_title"))
    lines.append("")
    lines.append(t("cli.summary_total", total=stats.get('total', 0)))
    lines.append(t("cli.summary_star3", count=stats.get('star_3', 0)))
    lines.append(t("cli.summary_picked", count=stats.get('picked', 0)))
    lines.append(t("cli.summary_star2", count=stats.get('star_2', 0)))
    lines.append(t("cli.summary_star1", count=stats.get('star_1', 0)))
    lines.append(t("cli.summary_star0", count=stats.get('star_0', 0)))
    lines.append(t("cli.summary_no_bird", count=stats.get('no_bird', 0)))

    # 飞鸟统计
    flying = stats.get('flying', 0)
    if flying > 0:
        lines.append("")
        lines.append(t("cli.summary_flying", count=flying))

    # 时间统计
    if include_time:
        lines.append("")
        lines.append(t("cli.summary_total_time", seconds=stats.get('total_time', 0)))
        lines.append(t("cli.summary_avg_time", seconds=stats.get('avg_time', 0)))

    lines.append("=" * 60)
    lines.append("")
    lines.append(t("cli.summary_exif_done"))

    return lines


def print_summary(lines: List[str], log_func: Optional[Callable[[str], None]] = None):
    """
    输出摘要（可用于 CLI 和 GUI）
    
    Args:
        lines: 格式化的行列表
        log_func: 日志输出函数，如果为 None 则使用 print
    """
    output = log_func if log_func else print
    for line in lines:
        output(line)
