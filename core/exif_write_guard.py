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
from typing import Dict, Iterable, Optional


class ExifWriteGuard:
    """
    记录已排队 / 已完成 / 失败的条数，并判断是否该停止写入。线程安全（写入线程与主流程共用）。

    两套口径（#150）：
    - total / done / failed 按「写入条目」计：连续超时判断与失败数用它；
    - photos_total / photos_done 按「文件」去重计：给用户看的进度与汇总用它。
      一张识别出鸟种的照片会排两条（星级一条、标题与鸟名一条），按条数显示时
      86 张照片会显示成一百多，用户看不懂总数从哪来。

    Tracks queued / done / failed counts and whether to stop writing. Thread-safe.
    Item counts (total/done/failed) drive the timeout rule; per-file counts
    (photos_total/photos_done) are what users see — one identified photo
    queues two items, so item counts overstated the photo total (#150).
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
        # 每个文件还有几条没写完；值为 0 表示该文件已写完
        # Outstanding items per file; 0 means that file is done.
        self._pending: Dict[str, int] = {}

    @property
    def photos_total(self) -> int:
        """排过队的不同文件数 / distinct files queued so far."""
        with self._lock:
            return len(self._pending)

    @property
    def photos_done(self) -> int:
        """所有条目都已写完（或跳过）的文件数 / files with no outstanding items."""
        with self._lock:
            return sum(1 for n in self._pending.values() if n <= 0)

    def _settle_files(self, files: Optional[Iterable[str]]) -> None:
        """一批里的文件各减一条待写（调用方已持锁）/ Decrement per-file pending (lock held)."""
        for f in files or ():
            if f in self._pending:
                self._pending[f] -= 1

    def add_queued(self, count: int, files: Optional[Iterable[str]] = None) -> None:
        """
        排队了 count 条。

        参数:
        count (int): 条数
        files (Iterable[str] | None): 这些条目对应的文件路径（可重复），用于按张计进度

        count items were queued; `files` (may repeat) feeds the per-photo progress.
        """
        with self._lock:
            self.total += count
            for f in files or ():
                self._pending[f] = self._pending.get(f, 0) + 1

    def record(self, count: int, result: Dict, files: Optional[Iterable[str]] = None) -> None:
        """
        记录一批的写入结果。

        参数:
        count (int): 这批条数
        result (dict): batch_set_metadata 的返回（success / failed / 可选 timed_out）
        files (Iterable[str] | None): 这批条目对应的文件路径，与 add_queued 传入的一致

        Record one batch's result; two consecutive timeouts abort further writes.
        """
        with self._lock:
            self.done += count
            self._settle_files(files)
            self.failed += int(result.get("failed", 0) or 0)
            if result.get("timed_out"):
                self.consecutive_timeouts += 1
                if self.consecutive_timeouts >= self.MAX_CONSECUTIVE_TIMEOUTS:
                    self.aborted = True
            else:
                self.consecutive_timeouts = 0

    def record_skipped(self, count: int, files: Optional[Iterable[str]] = None) -> None:
        """已停止写入后跳过的一批，全算失败 / A batch skipped after aborting counts as failed."""
        with self._lock:
            self.done += count
            self.failed += count
            self._settle_files(files)
