# -*- coding: utf-8 -*-
"""单张照片处理失败的兜底行为测试。

Per-photo failure handling tests for PhotoProcessor.

背景 / Background:
V4.5 给单张照片处理循环加了整体 try/except 兜底，但异常分支曾把失败照片
mark_resume_completed()，导致中断续跑时该照片被永久跳过，且不计入任何统计
——用户看到"处理完成"但照片原地未动。这组测试锁定正确语义：
失败照片必须留在 resume pending 列表（续跑可重试），并计入 failed 统计。

V4.5 wrapped the per-photo loop in a try/except, but the except branch used
to mark_resume_completed() the failed photo, so an interrupted-then-resumed
run would skip it forever with no accounting. These tests pin the correct
semantics: a failed photo must stay in the resume pending list (retryable)
and be counted in the 'failed' stat.
"""
from typing import List, Tuple

import pytest

from core.photo_processor import (
    PhotoProcessor,
    ProcessingCallbacks,
    ProcessingSettings,
)


def _make_processor(tmp_path) -> Tuple[PhotoProcessor, List[Tuple[str, str]]]:
    """构造指向临时目录的最小 PhotoProcessor，并捕获日志。

    Build a minimal PhotoProcessor pointed at a temp dir, capturing logs.
    """
    logs: List[Tuple[str, str]] = []
    callbacks = ProcessingCallbacks(log=lambda msg, level="info": logs.append((level, msg)))
    processor = PhotoProcessor(str(tmp_path), ProcessingSettings(), callbacks)
    return processor, logs


def test_failed_photo_stays_pending_for_resume(tmp_path):
    """失败照片必须留在 resume pending 列表中，续跑时可重试。

    A failed photo must remain pending in resume state so a resumed run
    retries it.
    """
    processor, _ = _make_processor(tmp_path)
    processor.resume_state.start(["IMG_0001", "IMG_0002"])

    processor._handle_photo_failure(1, "IMG_0001.NEF", "IMG_0001", Exception("boom"))

    plan = processor.resume_state.get_resume_plan(["IMG_0001", "IMG_0002"])
    assert plan is not None
    assert "IMG_0001" in plan["pending_prefixes"]


def test_failed_photo_counted_in_stats(tmp_path):
    """失败照片计入 stats['failed'] 并记录文件名，最终汇总可见。

    Failed photos are counted in stats['failed'] with their filenames
    recorded for the end-of-run summary.
    """
    processor, logs = _make_processor(tmp_path)

    processor._handle_photo_failure(3, "IMG_0003.NEF", "IMG_0003", RuntimeError("db locked"))
    processor._handle_photo_failure(7, "IMG_0007.NEF", "IMG_0007", OSError("disk"))

    assert processor.stats["failed"] == 2
    assert processor.failed_photos == ["IMG_0003.NEF", "IMG_0007.NEF"]
    # 每次失败都应有一条 error 级日志，包含文件名
    # Each failure logs one error-level line naming the file
    error_logs = [msg for level, msg in logs if level == "error"]
    assert any("IMG_0003.NEF" in msg for msg in error_logs)
    assert any("IMG_0007.NEF" in msg for msg in error_logs)


def test_failure_without_prefix_still_counted(tmp_path):
    """original_prefix 尚未赋值（None）时也要正常计数、不抛异常。

    A failure before original_prefix is known (None) must still count
    without raising.
    """
    processor, _ = _make_processor(tmp_path)

    processor._handle_photo_failure(1, "IMG_0001.NEF", None, ValueError("early"))

    assert processor.stats["failed"] == 1
