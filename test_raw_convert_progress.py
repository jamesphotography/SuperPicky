# -*- coding: utf-8 -*-
"""RAW 预览提取阶段的进度反馈与取消响应测试。

Progress feedback and cancellation tests for the RAW preview extraction stage.

背景 / Background:
阶段2（_convert_raws，4 线程提取 RAW 内嵌 JPEG）此前只在首尾各写一条日志，
成功路径全程静默。1000 张以上时用户看到界面几十秒到十几分钟毫无反应，误判
程序假死；期间点「停止」也无效，因为 _check_cancelled() 直到阶段2 结束后
才被调用。这组测试锁定两条语义：提取过程必须周期性输出计数（且节流，不能
每张一条刷屏），以及取消请求必须在阶段2 内部及时生效。

Stage 2 used to log only at start and finish, staying silent for the whole
success path. With 1000+ files users saw a frozen-looking UI, and pressing
Stop did nothing because _check_cancelled() only ran after stage 2 finished.
These tests pin both semantics: periodic throttled counting output, and
prompt cancellation inside the stage.
"""
import time
from typing import List, Tuple

import pytest

from core.photo_processor import (
    PhotoProcessor,
    ProcessingCallbacks,
    ProcessingCancelled,
    ProcessingSettings,
)


def _make_processor(tmp_path, should_stop=None) -> Tuple[PhotoProcessor, List[Tuple[str, str]]]:
    """构造指向临时目录的最小 PhotoProcessor，并捕获日志。

    Build a minimal PhotoProcessor pointed at a temp dir, capturing logs.
    """
    logs: List[Tuple[str, str]] = []
    callbacks = ProcessingCallbacks(
        log=lambda msg, level="info": logs.append((level, msg)),
        should_stop=should_stop,
    )
    processor = PhotoProcessor(str(tmp_path), ProcessingSettings(), callbacks)
    return processor, logs


def _fake_raw_list(tmp_path, count: int):
    """生成 count 个 (key, raw_path) 待转换条目。

    Build `count` (key, raw_path) entries to convert.
    """
    return [(f"IMG_{i:04d}", str(tmp_path / f"IMG_{i:04d}.NEF")) for i in range(count)]


def test_progress_logged_periodically_but_throttled(tmp_path, monkeypatch):
    """1000 张提取过程中必须周期性输出计数，且远少于每张一条。

    Extraction of 1000 files must emit periodic count lines, far fewer than
    one line per file.
    """
    monkeypatch.setattr(
        "core.photo_processor.raw_to_jpeg",
        lambda raw_path: str(tmp_path / "preview.jpg"),
    )
    processor, logs = _make_processor(tmp_path)

    processor._convert_raws(_fake_raw_list(tmp_path, 1000), [])

    progress_lines = [msg for _, msg in logs if "/1000" in msg]
    assert progress_lines, "提取过程必须输出进度计数 / must emit progress counts"
    assert len(progress_lines) < 100, (
        f"进度日志应节流，实际 {len(progress_lines)} 条 / progress logs must be throttled"
    )


def test_final_count_is_logged(tmp_path, monkeypatch):
    """最后一条进度必须显示完整计数，让用户看到阶段确实跑完。

    The final progress line must show the complete count.
    """
    monkeypatch.setattr(
        "core.photo_processor.raw_to_jpeg",
        lambda raw_path: str(tmp_path / "preview.jpg"),
    )
    processor, logs = _make_processor(tmp_path)

    processor._convert_raws(_fake_raw_list(tmp_path, 1000), [])

    assert any("1000/1000" in msg for _, msg in logs), (
        "缺少收尾计数 1000/1000 / final 1000/1000 count missing"
    )


def test_cancellation_stops_extraction_early(tmp_path, monkeypatch):
    """取消请求必须在阶段2 内部生效，不必等全部 1000 张提取完。

    A stop request must take effect inside stage 2 rather than after all
    1000 files are extracted.
    """
    calls = {"n": 0}

    def slow_raw_to_jpeg(raw_path):
        calls["n"] += 1
        time.sleep(0.002)
        return str(tmp_path / "preview.jpg")

    monkeypatch.setattr("core.photo_processor.raw_to_jpeg", slow_raw_to_jpeg)

    checks = {"n": 0}

    def should_stop():
        checks["n"] += 1
        return checks["n"] > 10

    processor, _ = _make_processor(tmp_path, should_stop=should_stop)

    with pytest.raises(ProcessingCancelled):
        processor._convert_raws(_fake_raw_list(tmp_path, 1000), [])

    assert calls["n"] < 500, (
        f"取消后仍提取了 {calls['n']} 张，排队任务未被取消 / queued work not cancelled"
    )


def test_slow_path_falls_back_to_time_based_output(tmp_path, monkeypatch):
    """极慢路径下，即使张数间隔未到也必须按时间兜底输出。

    张数节流的间隔是 总耗时/20，所以阶段超过 60 秒时它会稀疏到 3 秒以上；
    此时必须由 3 秒兜底接管，否则 1000 张 A7M5(耗时数分钟)仍会长时间静默。
    用虚拟时钟推进时间，避免真的跑 60 秒。

    When the stage runs long, the count rule spaces out beyond 3s and the
    time rule must take over. A virtual clock avoids a 60s real-time test.
    """
    monkeypatch.setattr(
        "core.photo_processor.raw_to_jpeg",
        lambda raw_path: str(tmp_path / "preview.jpg"),
    )
    # 虚拟时钟：每次读表推进 2 秒，模拟每张耗时很久的慢路径
    # Virtual clock: each read advances 2s, simulating a very slow path
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 2.0
        return clock["t"]

    monkeypatch.setattr("time.time", fake_time)

    processor, logs = _make_processor(tmp_path)
    processor._convert_raws(_fake_raw_list(tmp_path, 1000), [])

    progress_lines = [msg for _, msg in logs if "/1000" in msg]
    # 纯张数节流只会给 20 条(1000//50)；超过说明时间兜底确实接管了
    # Count-only throttling yields 20 lines; more proves the time rule fired
    assert len(progress_lines) > 20, (
        f"时间兜底未生效，仅 {len(progress_lines)} 条 / time-based fallback did not fire"
    )
