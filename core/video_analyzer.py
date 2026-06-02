#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 1 — 视频分析主引擎

把 tools/video_spike.py 中验证过的策略重构为生产级 API：
    - 自适应抽帧（max_frames 上限，与视频时长解耦）
    - 混合 seek/grab 抽帧策略（fps × interval 决定）
    - YOLO 鸟类检测（COCO class 14）
    - 进度回调 + 可中断
    - 输出结构化 VideoAnalysisResult（含元数据、性能、帧检测、合并段）

设计原则：
    - 不依赖 PySide6（UI 层用 QThread 包装本模块）
    - 不写文件 IO（结果交给调用方处理）
    - YOLO 模型由调用方传入（便于批量处理时复用）

Video analysis main engine for Phase 1 (macOS only, YOLO bird/no-bird).
Produced as production-grade API derived from the validated spike script.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional, Tuple

import cv2
import numpy as np

from core.video_segment import (
    BirdSegment,
    FrameDetection,
    filter_short_segments,
    merge_frame_results,
)


# ============================================================================
# 常量 / Constants
# ============================================================================

# 混合策略阈值：当 (fps × interval_sec) 超过此值时，seek 比 grab 更快
# 经验值来自 4K 视频 A/B 测试（tools/video_spike.py）
# Hybrid threshold for seek vs grab decode strategy.
SEEK_THRESHOLD_FRAMES = 60

# COCO 数据集中鸟类的 class id
# COCO bird class id.
COCO_BIRD_CLASS_ID = 14


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass
class VideoAnalysisResult:
    """
    视频分析完整结果

    Complete video analysis result.
    """
    # 元数据 / Metadata
    video_path: str
    width: int
    height: int
    fps: float
    duration_sec: float

    # 抽帧信息 / Sampling info
    frame_interval_sec: float
    sampled_frames: int
    strategy_used: str             # 'seek' / 'grab'

    # 性能 / Performance
    total_decode_ms: float
    total_yolo_ms: float
    total_wall_ms: float

    # 检测结果 / Detection results
    frame_detections: List[FrameDetection] = field(default_factory=list)
    segments: List[BirdSegment] = field(default_factory=list)
    has_bird: bool = False         # 顶层判定：视频是否有任何有鸟段

    # 中断状态 / Cancellation
    cancelled: bool = False        # 是否被用户中断


@dataclass
class _CaptureState:
    """grab 策略需要的游标状态 / Cursor state for grab strategy"""
    fps: float
    cursor: int = 0                # 当前已 grab 到的帧索引


# ============================================================================
# 抽帧策略 / Sampling strategy
# ============================================================================

def compute_frame_interval(duration_sec: float, max_frames: int = 60) -> float:
    """
    自适应抽帧间隔

    短视频（duration ≤ max_frames）：固定 1 秒/帧
    长视频：interval = ceil(duration / max_frames)，保证总帧数 ≤ max_frames

    参数:
        duration_sec (float): 视频总时长（秒）
        max_frames (int): 最大抽帧数上限

    返回:
        float: 抽帧间隔（秒）

    Adaptive sampling interval. Short clips: 1s/frame; long clips: scaled to cap total frames.
    """
    if duration_sec <= 0 or max_frames <= 0:
        return 1.0
    if duration_sec <= max_frames:
        return 1.0
    return float(math.ceil(duration_sec / max_frames))


def pick_strategy(fps: float, interval_sec: float,
                  threshold: int = SEEK_THRESHOLD_FRAMES) -> Literal['seek', 'grab']:
    """
    按 fps × interval 选最优抽帧策略

    Empirically: grab wins when frames-to-skip < ~60; seek wins beyond that.

    Pick optimal decode strategy by fps × interval.
    """
    frames_per_sample = fps * interval_sec
    return 'seek' if frames_per_sample > threshold else 'grab'


# ============================================================================
# 帧解码函数 / Frame decoder functions
# ============================================================================

def _decode_frame_seek(cap: cv2.VideoCapture, t_sec: float,
                       _state: _CaptureState) -> Tuple[bool, Optional[np.ndarray]]:
    """
    Seek 策略：直接 set(POS_MSEC) → read
    Seek strategy: jump by absolute timestamp.
    """
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
    ok, frame = cap.read()
    return ok, frame if ok else None


def _decode_frame_grab(cap: cv2.VideoCapture, t_sec: float,
                      state: _CaptureState) -> Tuple[bool, Optional[np.ndarray]]:
    """
    Grab 策略：顺序 grab() 跳过中间帧（不解码），retrieve() 解码目标帧
    Grab strategy: sequentially grab() then retrieve() target frame only.
    """
    target_idx = int(round(t_sec * state.fps))

    while state.cursor < target_idx:
        if not cap.grab():
            return False, None
        state.cursor += 1

    if state.cursor == target_idx:
        if not cap.grab():
            return False, None
        state.cursor += 1

    ok, frame = cap.retrieve()
    return ok, frame if ok else None


# ============================================================================
# 主分析类 / Main analyzer class
# ============================================================================

class VideoAnalyzer:
    """
    视频鸟类分析引擎（Phase 1：仅 YOLO 有鸟/无鸟）

    用法 / Usage:
        from ai_model import load_yolo_model
        analyzer = VideoAnalyzer(yolo_model=load_yolo_model())
        result = analyzer.analyze(
            video_path,
            progress_callback=lambda done, total: print(f"{done}/{total}"),
            stop_callback=lambda: user_clicked_cancel,
        )
        print(f"has_bird={result.has_bird}, segments={len(result.segments)}")

    Video bird-detection engine (Phase 1: YOLO bird/no-bird only).
    """

    def __init__(self, yolo_model, max_frames: int = 60,
                 yolo_threshold: float = 0.5,
                 min_segment_frames: int = 2):
        """
        初始化

        参数:
            yolo_model: 已加载的 YOLO 模型实例（ultralytics.YOLO）
            max_frames (int): 单视频抽帧上限，默认 60
            yolo_threshold (float): YOLO 置信度阈值，默认 0.5
            min_segment_frames (int): 段最少帧数，过滤误检，默认 2

        Parameters:
            yolo_model: pre-loaded YOLO model instance
            max_frames: per-video sampling cap
            yolo_threshold: YOLO confidence threshold
            min_segment_frames: minimum frames per segment (filters spurious detections)
        """
        if yolo_model is None:
            raise ValueError("yolo_model 不能为 None / yolo_model must not be None")
        self.yolo_model = yolo_model
        self.max_frames = max_frames
        self.yolo_threshold = yolo_threshold
        self.min_segment_frames = min_segment_frames

    def analyze(self, video_path: str,
                progress_callback: Optional[Callable[[int, int], None]] = None,
                stop_callback: Optional[Callable[[], bool]] = None,
                ) -> VideoAnalysisResult:
        """
        分析单个视频

        参数:
            video_path (str): 视频文件绝对路径
            progress_callback: 进度回调 (done, total) → None；每帧推理完调用一次
            stop_callback: 中断检查回调 () → bool；返回 True 时中断分析

        返回:
            VideoAnalysisResult: 包含元数据、性能、检测结果、合并段

        异常:
            IOError: 无法打开视频文件

        Analyze a single video file.

        Parameters:
            video_path: absolute video file path
            progress_callback: called after each frame as (done, total) -> None
            stop_callback: called before each frame; return True to cancel

        Return:
            VideoAnalysisResult with metadata, performance, detections, segments.

        Raises:
            IOError: cannot open video file
        """
        wall_start = time.perf_counter()

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise IOError(f"无法打开视频 / Cannot open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration_sec = frame_count / fps if fps > 0 else 0.0

            interval_sec = compute_frame_interval(duration_sec, self.max_frames)
            sample_times = np.arange(0.0, max(duration_sec, interval_sec), interval_sec)

            strategy = pick_strategy(fps, interval_sec)
            decode_fn = _decode_frame_grab if strategy == 'grab' else _decode_frame_seek
            state = _CaptureState(fps=fps, cursor=0)

            frame_dets: List[FrameDetection] = []
            total_decode_ms = 0.0
            total_yolo_ms = 0.0
            cancelled = False
            total_samples = len(sample_times)

            for i, t_sec in enumerate(sample_times):
                # 中断检查 / Cancellation check
                if stop_callback is not None and stop_callback():
                    cancelled = True
                    break

                # 解码 / Decode
                decode_start = time.perf_counter()
                ok, frame = decode_fn(cap, float(t_sec), state)
                total_decode_ms += (time.perf_counter() - decode_start) * 1000.0

                if not ok or frame is None:
                    continue

                # YOLO 推理 / YOLO inference
                yolo_start = time.perf_counter()
                results = self.yolo_model(
                    frame,
                    verbose=False,
                    conf=self.yolo_threshold,
                    classes=[COCO_BIRD_CLASS_ID],
                )
                total_yolo_ms += (time.perf_counter() - yolo_start) * 1000.0

                # 提取结果 / Extract detection results
                boxes = results[0].boxes
                if boxes is not None and len(boxes) > 0:
                    confs = boxes.conf.cpu().numpy()
                    bird_count = int(len(confs))
                    has_bird = True
                    max_conf = float(confs.max())
                else:
                    bird_count = 0
                    has_bird = False
                    max_conf = 0.0

                # 立即释放 GPU 张量 / Free GPU tensors immediately
                del results

                frame_dets.append(FrameDetection(
                    timestamp_sec=float(t_sec),
                    has_bird=has_bird,
                    max_conf=max_conf,
                    bird_count=bird_count,
                ))

                if progress_callback is not None:
                    progress_callback(i + 1, total_samples)
        finally:
            cap.release()

        # 合并段 + 过滤短段 / Merge segments and filter short ones
        segments = merge_frame_results(frame_dets, duration_sec)
        segments = filter_short_segments(segments,
                                         min_frames=self.min_segment_frames,
                                         video_duration_sec=duration_sec)
        has_bird = any(s.has_bird for s in segments)

        total_wall_ms = (time.perf_counter() - wall_start) * 1000.0

        return VideoAnalysisResult(
            video_path=video_path,
            width=width,
            height=height,
            fps=fps,
            duration_sec=duration_sec,
            frame_interval_sec=interval_sec,
            sampled_frames=len(frame_dets),
            strategy_used=strategy,
            total_decode_ms=total_decode_ms,
            total_yolo_ms=total_yolo_ms,
            total_wall_ms=total_wall_ms,
            frame_detections=frame_dets,
            segments=segments,
            has_bird=has_bird,
            cancelled=cancelled,
        )


# ============================================================================
# 便捷工具函数 / Convenience utilities
# ============================================================================

def is_supported_video(file_path: str) -> bool:
    """
    判断文件是否为支持的视频格式

    根据 constants.VIDEO_EXTENSIONS 判断（默认 .mp4/.mov/.m4v）

    参数:
        file_path (str): 文件路径

    返回:
        bool: 是否为支持的视频

    Check if a file is a supported video format.
    """
    from constants import VIDEO_EXTENSIONS_ALL
    ext = os.path.splitext(file_path)[1]
    return ext in VIDEO_EXTENSIONS_ALL
