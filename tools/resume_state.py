#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中断续跑进度文件 / Resume-progress file.

选片时每处理完一张就把「还没处理的照片」写进 `.superpicky/resume_state.json`，
中途停止后下次可以接着处理。它只是辅助记录：写不进去最多导致续跑时重做几张，
**绝不能**让一张已经处理完的照片被判为失败（#156）。

#156：Windows 上 os.replace 覆盖目标文件时，只要别的进程（杀毒软件、Windows
搜索索引、OneDrive 等同步程序）正以不允许删除的方式打开着它，就会报
「[WinError 5] 拒绝访问」。这类占用通常只持续几十毫秒，所以这里短暂重试；
仍然失败就放弃这一次、返回 False，由调用方记一条警告，选片继续。

Records which photos are still pending so a stopped run can continue. It is
bookkeeping only: failing to write it may at worst redo a few photos on resume,
and must never mark an already-processed photo as failed (#156).

#156: on Windows, os.replace onto the target fails with "[WinError 5] Access is
denied" while another process (antivirus, the Windows Search indexer, OneDrive
or another sync client) holds the file open without delete sharing. Such locks
usually last tens of milliseconds, so writes retry briefly; if it still fails,
the write is skipped and False is returned so the caller can log a warning and
carry on.
"""

import json
import os
import tempfile
import time
from typing import Dict, List, Optional

from .file_utils import ensure_hidden_directory

# 被占用时的重试间隔（秒），合计约 0.35 秒。只针对 PermissionError：磁盘满、
# 目录不存在之类重试也没用。
# Retry delays (seconds) while the file is locked, about 0.35 s in total. Only
# PermissionError is retried; disk-full or missing-directory errors would not
# go away.
_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _with_lock_retry(action) -> None:
    """
    执行文件操作，遇到 PermissionError（文件被别的进程占用）时短暂重试。

    参数:
    action (Callable[[], None]): 要执行的文件操作

    异常:
    OSError: 重试用尽后仍失败，或遇到不可重试的错误时原样抛出

    Run a file operation, retrying briefly on PermissionError (file held open
    by another process).

    Parameters:
    action (Callable[[], None]): the file operation

    Raises:
    OSError: re-raised when retries are exhausted or the error is not retryable
    """
    for delay in _LOCK_RETRY_DELAYS:
        try:
            action()
            return
        except PermissionError:
            time.sleep(delay)
    action()


class ResumeStateManager:
    """
    在报告数据库之外保存轻量的续跑进度。

    所有写操作（start / mark_completed / clear）都不抛异常：成功返回 True，
    失败返回 False，失败原因放在 last_error 里供调用方记日志。

    Persists lightweight resume state outside the report database. Every write
    (start / mark_completed / clear) never raises: it returns True on success
    and False on failure, with the reason in last_error for the caller's log.
    """

    FILENAME = "resume_state.json"

    def __init__(self, directory: str):
        self.directory = directory
        self.state_dir = os.path.join(directory, ".superpicky")
        self.state_path = os.path.join(self.state_dir, self.FILENAME)
        # 最近一次写失败的异常；成功写入不会清空它 / Last write failure.
        self.last_error: Optional[OSError] = None

    def exists(self) -> bool:
        return os.path.exists(self.state_path)

    def load(self) -> Optional[Dict]:
        if not self.exists():
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def start(self, ordered_prefixes: List[str]) -> bool:
        """
        开始新一轮：全部照片都是待处理 / Start a run with every photo pending.

        返回:
        bool: 写入成功为 True / True if written
        """
        payload = {
            "version": 1,
            "status": "running",
            "total_files": len(ordered_prefixes),
            "next_index": 1,
            "pending_prefixes": list(ordered_prefixes),
        }
        return self._write(payload)

    def get_resume_plan(self, available_prefixes: List[str]) -> Optional[Dict]:
        state = self.load()
        if not state or state.get("status") != "running":
            return None
        available_set = set(available_prefixes)
        pending = [prefix for prefix in (state.get("pending_prefixes") or []) if prefix in available_set]
        if not pending:
            return None
        total_files = int(state.get("total_files") or len(available_prefixes))
        completed = max(0, total_files - len(pending))
        return {
            "total_files": total_files,
            "next_index": completed + 1,
            "pending_prefixes": pending,
        }

    def mark_completed(self, prefix: str) -> bool:
        """
        把一张照片从待处理里去掉 / Remove one photo from the pending list.

        参数:
        prefix (str): 照片前缀（不含扩展名）/ photo prefix without extension

        返回:
        bool: 写入成功（或本来就无需写）为 True；文件被占用写不进去为 False
              True if written (or nothing to write); False if the file was locked
        """
        state = self.load()
        if not state:
            return True
        pending = [item for item in (state.get("pending_prefixes") or []) if item != prefix]
        state["pending_prefixes"] = pending
        total_files = int(state.get("total_files") or 0)
        state["next_index"] = min(total_files + 1, total_files - len(pending) + 1) if total_files > 0 else 1
        if not pending:
            return self.clear()
        return self._write(state)

    def clear(self) -> bool:
        """
        删除进度文件 / Delete the progress file.

        返回:
        bool: 已删除或本来就不存在为 True / True if removed or already absent
        """
        def _remove() -> None:
            try:
                os.remove(self.state_path)
            except FileNotFoundError:
                pass

        try:
            _with_lock_retry(_remove)
            return True
        except OSError as exc:
            self.last_error = exc
            return False

    def _write(self, payload: Dict) -> bool:
        """
        先写临时文件再整体替换，避免写到一半留下坏文件。

        返回:
        bool: 成功为 True；目录建不了、临时文件写不了或替换失败为 False

        Write a temp file, then replace the target atomically so a crash never
        leaves a half-written file.

        Return:
        bool: True on success; False if the directory, temp file or replace failed
        """
        temp_path: Optional[str] = None
        try:
            ensure_hidden_directory(self.state_dir)
            fd, temp_path = tempfile.mkstemp(prefix="resume_", suffix=".json", dir=self.state_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            _with_lock_retry(lambda: os.replace(temp_path, self.state_path))
            return True
        except OSError as exc:
            self.last_error = exc
            return False
        finally:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
