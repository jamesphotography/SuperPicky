# -*- coding: utf-8 -*-
"""照片阶段计时口径：worker 开始干活 → 照片阶段完成的 wall time。

Photo-stage timing semantics: wall time from when the worker starts real
work to when the photo stage completes.

背景 / Background:
此前单目录模式的 total_time 从 PhotoProcessor.process() 入口才开始算，
漏掉预扫描/系统信息采集等 worker 前置开销；批量模式却在目录循环外掐表
——同一批照片按目录结构不同会得到不同口径的「平均每张」。统一为：
起点 = WorkerThread.process_files() 入口（用户确认完毕、真正开始干活），
终点 = 照片阶段全部完成（视频阶段之前），单目录与批量共用同一个
_finalize_photo_stage_timing() 收口。

Single-dir total_time used to start at PhotoProcessor.process() entry,
missing pre-scan/system-info overhead, while batch mode timed the whole
directory loop — inconsistent per-photo averages for the same photos.
Unified: start = process_files() entry (user confirmations done, real work
begins), end = photo stage complete (before the video stage), both modes
finalized by the shared _finalize_photo_stage_timing().
"""
import pytest

from ui.main_window import WorkerThread


def test_finalize_computes_total_and_avg():
    """总时间 = 终点-起点；平均 = 总时间/照片数。"""
    stats = {"total": 50, "star_3": 5}
    WorkerThread._finalize_photo_stage_timing(stats, wall_start=100.0, wall_end=160.0)
    assert stats["total_time"] == pytest.approx(60.0)
    assert stats["avg_time"] == pytest.approx(1.2)
    assert stats["start_time"] == 100.0
    assert stats["end_time"] == 160.0
    assert stats["star_3"] == 5  # 不动其他字段 / other keys untouched


def test_finalize_zero_photos_no_division_error():
    """0 张照片时平均为 0，不抛除零异常。"""
    stats = {"total": 0}
    WorkerThread._finalize_photo_stage_timing(stats, wall_start=10.0, wall_end=15.0)
    assert stats["total_time"] == pytest.approx(5.0)
    assert stats["avg_time"] == 0


def test_finalize_overrides_processor_internal_timing():
    """必须覆盖 processor 内部口径的旧值（单目录模式的关键行为）。"""
    stats = {"total": 10, "total_time": 42.0, "avg_time": 4.2,
             "start_time": 1.0, "end_time": 43.0}
    WorkerThread._finalize_photo_stage_timing(stats, wall_start=0.0, wall_end=100.0)
    assert stats["total_time"] == pytest.approx(100.0)
    assert stats["avg_time"] == pytest.approx(10.0)
