# -*- coding: utf-8 -*-
"""
选片收尾时 EXIF 批量写入的进度与「连续超时就停」判断。

收尾阶段把排序池照片的评分一次性写回文件。机器上 ExifTool 一直无响应（例如被杀毒软件拦截、
文件被其他程序占用）时，每批都要等到超时；连续 MAX_CONSECUTIVE_TIMEOUTS 批超时就不再写，
避免用户对着「100%」干等一个多小时。评分本身已存进 report.db，结果浏览器里照常可见。

Progress accounting for the end-of-run EXIF batch writes, plus the rule to
stop after consecutive timeouts so a machine where ExifTool never responds
does not leave users staring at "100%" for over an hour. Ratings are already
in report.db and stay visible in the results browser.
"""
from __future__ import annotations

import threading
from typing import Dict


class ExifWriteGuard:
    """
    记录已排队 / 已完成 / 失败的张数，并判断是否该停止写入。线程安全（写入线程与主流程共用）。
    Tracks queued / done / failed counts and whether to stop writing. Thread-safe.
    """

    #: 连续几批超时就停止写入 / stop after this many consecutive timed-out batches
    MAX_CONSECUTIVE_TIMEOUTS = 2

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0
        self.done = 0
        self.failed = 0
        self.consecutive_timeouts = 0
        self.aborted = False

    def add_queued(self, count: int) -> None:
        """排队了 count 张 / count items were queued."""
        with self._lock:
            self.total += count

    def record(self, count: int, result: Dict) -> None:
        """
        记录一批的写入结果。

        参数:
        count (int): 这批张数
        result (dict): batch_set_metadata 的返回（success / failed / 可选 timed_out）

        Record one batch's result; two consecutive timeouts abort further writes.
        """
        with self._lock:
            self.done += count
            self.failed += int(result.get("failed", 0) or 0)
            if result.get("timed_out"):
                self.consecutive_timeouts += 1
                if self.consecutive_timeouts >= self.MAX_CONSECUTIVE_TIMEOUTS:
                    self.aborted = True
            else:
                self.consecutive_timeouts = 0

    def record_skipped(self, count: int) -> None:
        """已停止写入后跳过的一批，全算失败 / A batch skipped after aborting counts as failed."""
        with self._lock:
            self.done += count
            self.failed += count
