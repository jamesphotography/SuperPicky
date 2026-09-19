# -*- coding: utf-8 -*-
"""
处理日志回放 / Processing-log replay.

重新浏览一个已处理过的目录时，把该目录根部的 ``superpicky.log`` 读回控制台，
让用户不重跑也能看到上次的处理过程。

为什么读根目录那一份：主窗口的控制台内容由 ``MainWindow._log`` 渲染，而
``ProcessingWorker.log_callback`` 会把同一条消息同时写进
``<选中目录>/superpicky.log``（见 ui/main_window.py 的 ``log_callback``）。
批量模式下子目录里也有同名日志，但那只是 ``photo_processor`` 直接调
``tools.utils.log_message`` 写的调试细节（补救扫描/YOLO 调试等），从未进过
控制台，所以回放只认根目录那一份。

日志文件只留文本，行级别（info/warning/species…）没有落盘，因此这里按内容
反推标签：能精确还原的（逐张结果行、Bird ID 行）精确还原，其余按惯例归类。
这是刻意的近似，目的是让回放读起来与当时的控制台一致，而不是逐字节复刻。

本模块不依赖 Qt，只负责「读文件 → 结构化行」，着色与插入由
``ui/main_window.py`` 完成，便于单元测试。

Replay of a processed directory's console log.

The main window's console is rendered by ``MainWindow._log``; the same messages
are mirrored to ``<selected dir>/superpicky.log`` by the worker's log callback,
so that file is what we replay. Per-subdirectory logs in batch mode only hold
``photo_processor`` debug details that never reached the console, so they are
ignored here.

Log files keep no level information, so tags are inferred from the text: lines
we can classify exactly (per-photo results, Bird ID lines) are restored exactly
and the rest fall back to conventions. Qt-free on purpose: this module turns a
file into structured lines; ui/main_window.py does the colouring.
"""
from __future__ import annotations

import os
import re
from collections import deque
from typing import List, NamedTuple, Optional

# 日志文件名（与 tools/utils.log_message 写入的名字一致）
# Log file name (matches what tools/utils.log_message writes).
LOG_FILENAME = "superpicky.log"

# 回放行数上限：头部保留会话头（设置/系统信息），尾部保留最近的处理过程与统计报告。
# 超长日志（十万张照片可达数十万行）全量塞进 QTextEdit 会卡住 UI，故只取两端。
# Replay caps: keep the session header at the head and the most recent
# processing lines plus the final report at the tail. Feeding a multi-hundred-
# thousand-line log into QTextEdit would freeze the UI.
DEFAULT_HEAD_LINES = 80
DEFAULT_TAIL_LINES = 2000

# "[2026-09-19 19:44:50] 消息" —— log_message 写入的行首时间戳
# Line prefix written by tools.utils.log_message.
_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]\s?(.*)$")

# "[001/3842] DSC06158.jpg | 3★ (...) | 1.2s" —— 逐张结果行
# Per-photo result line emitted by PhotoProcessor._log_photo_result_simple.
_PHOTO_LINE_RE = re.compile(r"^\[\d+/\d+\]\s")

# 会话头 / 段落标题 / "  Key : Value" 这类只写文件的结构化块
# Session header, section titles and "  Key : Value" rows (file-only blocks).
_SECTION_RE = re.compile(r"^\[[A-Za-z][A-Za-z ]*\]$")
_KEY_VALUE_RE = re.compile(r"^\s{2,}\S.*\s:\s")


class LogLine(NamedTuple):
    """
    一条回放日志行 / One replayed log line.

    属性 / Attributes:
        time_text (str): "HH:MM:SS"，没有时间戳前缀时为空串 /
            Clock text, empty when the raw line carried no timestamp.
        message (str): 去掉时间戳前缀后的正文 / Message without the prefix.
        tag (str): 推断出的日志级别标签，取值与 MainWindow._log 的 tag 一致 /
            Inferred level tag, using MainWindow._log's vocabulary.
    """

    time_text: str
    message: str
    tag: str


class LogRestoreResult(NamedTuple):
    """
    一次日志回放的读取结果 / Result of reading a log for replay.

    属性 / Attributes:
        head (List[LogLine]): 文件开头的若干行 / Leading lines.
        tail (List[LogLine]): 文件结尾的若干行 / Trailing lines.
        skipped (int): 头尾之间被省略的行数 / Lines omitted between them.
        total (int): 文件总行数 / Total lines in the file.
    """

    head: List[LogLine]
    tail: List[LogLine]
    skipped: int
    total: int


def find_log_file(directory: str) -> Optional[str]:
    """
    找到目录对应的控制台日志文件。

    参数 / Parameters:
        directory (str): 已处理的照片目录 / A processed photo directory.

    返回 / Returns:
        Optional[str]: 日志文件绝对路径；不存在或不是文件时返回 None /
            Path to the log file, or None when absent.
    """
    if not directory:
        return None
    path = os.path.join(directory, LOG_FILENAME)
    return path if os.path.isfile(path) else None


def infer_tag(message: str) -> str:
    """
    按正文内容推断日志级别标签。

    还原优先级：逐张结果行与 Bird ID 行能精确还原（它们在产品代码里有固定
    格式与固定 level）；其余按符号与结构归类，取不到线索时归为 "info"——
    处理流程里绝大多数 ``_log`` 调用用的就是默认的 info。

    参数 / Parameters:
        message (str): 去掉时间戳前缀的日志正文 / Message without the prefix.

    返回 / Returns:
        str: MainWindow._log 能识别的 tag（species/photo_good/error/warning/
            success/muted/info/default）/ A tag understood by MainWindow._log.

    Infer a level tag from the message text. Per-photo and Bird ID lines have
    fixed formats and fixed levels in the product code, so they are restored
    exactly; everything else falls back to conventions, defaulting to "info"
    because most processing-time ``_log`` calls use the default info level.
    """
    text = message.strip()
    if not text:
        return "default"

    # 1. 识鸟结果行：core/photo_processor.py 用 "species" 级别输出
    #    Bird ID lines are logged with the "species" level.
    if "🐦" in text or "Bird ID [" in text or "Low confidence [" in text:
        return "species"

    # 2. 逐张结果行：3 星走 photo_good（绿），其余走 default
    #    Per-photo lines: 3-star uses photo_good (green), the rest default.
    if _PHOTO_LINE_RE.match(text):
        return "photo_good" if "3★" in text else "default"

    # 3. 符号级别：与 _log 的 error/warning/success 着色对应
    #    Symbol-based levels matching _log's error/warning/success colours.
    if "❌" in text or text.startswith("Error:"):
        return "error"
    if "⚠️" in text or "⚠" in text:
        return "warning"
    if "✅" in text:
        return "success"

    # 4. 会话头/统计块：只写文件、从未进过控制台，用弱化色排版，避免喧宾夺主
    #    Session header / summary blocks were file-only; render them muted.
    if set(text) <= {"=", "_", "━"} or _SECTION_RE.match(text) or _KEY_VALUE_RE.match(message):
        return "muted"

    return "info"


def parse_log_line(raw: str) -> LogLine:
    """
    解析一行原始日志文本。

    多行消息（如会话头）只有第一行带时间戳，后续行原样保留、时间列留空。

    参数 / Parameters:
        raw (str): 日志文件中的一行（不含换行符）/ One raw line, newline stripped.

    返回 / Returns:
        LogLine: 结构化后的行 / The structured line.

    Parse one raw log line. Multi-line messages (e.g. the session header) carry
    a timestamp only on their first line; continuation lines keep an empty
    clock column.
    """
    match = _TIMESTAMP_RE.match(raw)
    if match:
        message = match.group(3)
        return LogLine(time_text=match.group(2), message=message, tag=infer_tag(message))
    return LogLine(time_text="", message=raw, tag=infer_tag(raw))


def read_log_lines(
    path: str,
    head_limit: int = DEFAULT_HEAD_LINES,
    tail_limit: int = DEFAULT_TAIL_LINES,
) -> LogRestoreResult:
    """
    单遍读取日志文件，返回头部与尾部若干行。

    只保留两端且内存有界（尾部用定长 deque），因此对几十万行的日志同样安全。

    参数 / Parameters:
        path (str): 日志文件路径 / Path to the log file.
        head_limit (int): 保留的开头行数 / Number of leading lines to keep.
        tail_limit (int): 保留的结尾行数 / Number of trailing lines to keep.

    返回 / Returns:
        LogRestoreResult: 头部行、尾部行、省略行数与总行数 /
            Head lines, tail lines, omitted count and total count.

    异常 / Raises:
        OSError: 文件无法读取时由调用方处理 / Propagated to the caller.

    Read the log in a single pass with bounded memory (the tail uses a fixed
    deque), so multi-hundred-thousand-line logs stay safe.
    """
    head: List[str] = []
    tail: deque = deque(maxlen=max(0, tail_limit))
    total = 0

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            total += 1
            line = raw.rstrip("\n").rstrip("\r")
            if len(head) < head_limit:
                head.append(line)
            else:
                tail.append(line)

    skipped = max(0, total - len(head) - len(tail))
    return LogRestoreResult(
        head=[parse_log_line(x) for x in head],
        tail=[parse_log_line(x) for x in tail],
        skipped=skipped,
        total=total,
    )
