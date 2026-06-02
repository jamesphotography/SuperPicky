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

from core.bird_classifier_base import (
    BirdClassifier,
    FlightClassifier,
    SpeciesResult,
)
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

# 极速模式：BirdID 置信度达到此阈值即停止扫描
# Instant mode: stop scanning once BirdID confidence reaches this threshold
INSTANT_SPECIES_CONF_THRESHOLD = 0.30


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass(slots=True)
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


@dataclass(slots=True)
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
                 min_segment_frames: int = 2,
                 species_classifier: Optional[BirdClassifier] = None,
                 flight_classifier: Optional[FlightClassifier] = None,
                 species_mode: Literal['instant', 'fast', 'full'] = 'instant'):
        """
        初始化

        参数:
            yolo_model: 已加载的 YOLO 模型实例（ultralytics.YOLO）
            max_frames (int): 单视频抽帧上限，默认 60
            yolo_threshold (float): YOLO 置信度阈值，默认 0.5
            min_segment_frames (int): 段最少帧数，过滤误检，默认 2
            species_classifier (Optional[BirdClassifier]): 鸟种识别器（Phase 2 可选）
            flight_classifier (Optional[FlightClassifier]): 飞行检测器（Phase 2 可选）
            species_mode ('instant'|'fast'|'full'):
                'instant' — 极速：第一个有鸟帧识别成功即停止扫描，单段覆盖整个视频（默认）
                'fast'    — 标准：扫完全部抽帧，每段用 max_conf 帧识别（有时间轴）
                'full'    — 完整：每段所有有鸟帧 Top-1 加权投票（最准，最慢）

        Parameters:
            yolo_model: pre-loaded YOLO model instance
            max_frames: per-video sampling cap
            yolo_threshold: YOLO confidence threshold
            min_segment_frames: minimum frames per segment (filters spurious detections)
            species_classifier: Phase 2 bird species classifier (optional)
            flight_classifier: Phase 2 flight/perched classifier (optional)
            species_mode: 'instant' (stop at first match, default) /
                          'fast' (one frame per segment) /
                          'full' (all frames + weighted vote)
        """
        if yolo_model is None:
            raise ValueError("yolo_model 不能为 None / yolo_model must not be None")
        self.yolo_model = yolo_model
        self.max_frames = max_frames
        self.yolo_threshold = yolo_threshold
        self.min_segment_frames = min_segment_frames
        self.species_classifier = species_classifier
        self.flight_classifier = flight_classifier
        if species_mode not in ('instant', 'fast', 'full'):
            raise ValueError(f"无效 species_mode / Invalid species_mode: {species_mode}")
        self.species_mode = species_mode

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

            # 极速模式状态：记录命中的帧 + 鸟种结果 + 可选飞行结果
            # Instant mode state: matched frame + species + optional flight result
            instant_match: Optional[Tuple[FrameDetection, SpeciesResult, Optional[object]]] = None
            instant_enabled = (self.species_mode == 'instant'
                               and self.species_classifier is not None)

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
                # V4.3 Phase 2: 同时记录 max_conf 鸟的 bbox（供后处理鸟种/飞行识别）
                # Phase 2: also save bbox of the max-conf bird (for downstream species/flight ID).
                boxes = results[0].boxes
                max_bbox: Optional[Tuple[int, int, int, int]] = None
                if boxes is not None and len(boxes) > 0:
                    confs = boxes.conf.cpu().numpy()
                    bird_count = int(len(confs))
                    has_bird = True
                    max_idx = int(confs.argmax())
                    max_conf = float(confs[max_idx])
                    xyxy = boxes.xyxy.cpu().numpy()
                    x1, y1, x2, y2 = xyxy[max_idx]
                    max_bbox = (int(x1), int(y1), int(x2), int(y2))
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
                    bbox=max_bbox,
                ))

                if progress_callback is not None:
                    progress_callback(i + 1, total_samples)

                # 极速模式：检测到有鸟帧 → 立即跑 BirdID → 命中即跳出循环
                # Instant mode: on first bird frame, run BirdID; if matched, break out.
                if instant_enabled and has_bird:
                    try:
                        sp_results = self.species_classifier.identify(
                            frame, top_k=1, bbox=max_bbox)
                    except Exception:
                        sp_results = []
                    if sp_results and sp_results[0].confidence >= INSTANT_SPECIES_CONF_THRESHOLD:
                        # 命中！可选地立即跑飞行检测，然后跳出循环
                        # Matched! Optionally run flight detection, then break.
                        flight_res = None
                        if self.flight_classifier is not None:
                            try:
                                flight_res = self.flight_classifier.detect(
                                    frame, bbox=max_bbox)
                            except Exception:
                                pass
                        instant_match = (frame_dets[-1], sp_results[0], flight_res)
                        break

            if instant_match is not None:
                # 极速模式命中：直接构造单段覆盖整个视频（无时间轴信息）
                # Instant mode hit: build a single segment spanning the entire video.
                matched_fd, matched_sp, matched_flight = instant_match
                segments = [BirdSegment(
                    start_sec=0.0,
                    end_sec=duration_sec,
                    has_bird=True,
                    frame_count=1,
                    avg_conf=matched_fd.max_conf,
                    max_conf=matched_fd.max_conf,
                    best_frame_sec=matched_fd.timestamp_sec,
                    best_frame_bbox=matched_fd.bbox,
                    species_zh=matched_sp.name_zh,
                    species_en=matched_sp.name_en,
                    species_conf=matched_sp.confidence,
                    is_flying=(matched_flight.is_flying if matched_flight else None),
                    flight_conf=(matched_flight.confidence if matched_flight else 0.0),
                )]
            else:
                # 正常流程（fast/full 模式 或 instant 未命中的退化）
                # Normal flow (fast/full mode, or instant mode failed to match).
                # 合并段 + 过滤短段 / Merge segments and filter short ones
                segments = merge_frame_results(frame_dets, duration_sec)
                segments = filter_short_segments(segments,
                                                 min_frames=self.min_segment_frames,
                                                 video_duration_sec=duration_sec)

                # Phase 2: 段级鸟种 + 飞行判定（仅 fast/full 模式）
                # Phase 2: segment-level classification (fast/full only; instant skipped its own).
                if (not cancelled
                        and self.species_mode != 'instant'
                        and (self.species_classifier or self.flight_classifier)):
                    self._classify_segments(cap, segments, frame_dets, stop_callback)
        finally:
            cap.release()

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

    # ------------------------------------------------------------------
    # Phase 2 段级分类：鸟种 + 飞行
    # Phase 2 segment-level classification: species + flight
    # ------------------------------------------------------------------

    def _classify_segments(self, cap: cv2.VideoCapture,
                           segments: List[BirdSegment],
                           frame_dets: List[FrameDetection],
                           stop_callback: Optional[Callable[[], bool]]):
        """
        对每个有鸟段执行鸟种 + 飞行分类，原地修改 segment

        fast 模式：只在 best_frame_sec 上跑一次（足够覆盖单鸟种视频）
        full 模式：对段内所有有鸟帧都跑，鸟种 Top-1 加权投票、飞行多数表决

        Per-segment species ID and flight detection. Modifies segments in place.
        - fast: classify only at best_frame_sec (sufficient for single-species clips)
        - full: classify all bird frames in the segment, weighted vote for species
                and majority vote for flight.
        """
        for seg in segments:
            if not seg.has_bird:
                continue
            if stop_callback is not None and stop_callback():
                return

            if self.species_mode == 'fast':
                self._classify_segment_fast(cap, seg)
            else:
                self._classify_segment_full(cap, seg, frame_dets)

    def _classify_segment_fast(self, cap: cv2.VideoCapture, seg: BirdSegment):
        """
        快速模式：在段的 best_frame 上跑一次鸟种 + 飞行

        Fast mode: classify once at the segment's best_frame.
        """
        if seg.best_frame_sec is None:
            return
        frame = self._seek_and_read(cap, seg.best_frame_sec)
        if frame is None:
            return

        if self.species_classifier is not None:
            try:
                results = self.species_classifier.identify(
                    frame, top_k=1, bbox=seg.best_frame_bbox)
                if results:
                    seg.species_zh = results[0].name_zh
                    seg.species_en = results[0].name_en
                    seg.species_conf = results[0].confidence
            except Exception:
                pass

        if self.flight_classifier is not None:
            try:
                fr = self.flight_classifier.detect(
                    frame, bbox=seg.best_frame_bbox)
                seg.is_flying = fr.is_flying
                seg.flight_conf = fr.confidence
            except Exception:
                pass

    def _classify_segment_full(self, cap: cv2.VideoCapture, seg: BirdSegment,
                               frame_dets: List[FrameDetection]):
        """
        完整模式：段内所有有鸟帧跑分类，加权投票

        完整模式策略：
            - 鸟种：每帧取 Top-1，按其 confidence 加权投票，得分最高的鸟种胜出
            - 飞行：每帧二分类，count(is_flying=True) > count/2 则为飞行
                    confidence 取所有帧的均值

        Full mode: classify all bird frames in segment with weighted voting.
        """
        # 收集段内的有鸟帧 / Collect bird frames within this segment
        seg_frames = [
            f for f in frame_dets
            if f.has_bird and seg.start_sec <= f.timestamp_sec < seg.end_sec
        ]
        if not seg_frames:
            return

        # 鸟种识别：(name_zh, name_en) → 加权 confidence 累积
        # Species: weighted confidence accumulation by (name_zh, name_en)
        species_votes: dict[Tuple[str, str], float] = {}
        # 飞行：累积 (is_flying_count, confidence_sum)
        # Flight: accumulate (flying_count, confidence_sum)
        flying_count = 0
        flight_conf_sum = 0.0
        flight_total = 0

        for fd in seg_frames:
            frame = self._seek_and_read(cap, fd.timestamp_sec)
            if frame is None:
                continue

            if self.species_classifier is not None:
                try:
                    results = self.species_classifier.identify(
                        frame, top_k=1, bbox=fd.bbox)
                    if results:
                        top = results[0]
                        key = (top.name_zh, top.name_en)
                        species_votes[key] = species_votes.get(key, 0.0) + top.confidence
                except Exception:
                    pass

            if self.flight_classifier is not None:
                try:
                    fr = self.flight_classifier.detect(frame, bbox=fd.bbox)
                    flight_total += 1
                    flight_conf_sum += fr.confidence
                    if fr.is_flying:
                        flying_count += 1
                except Exception:
                    pass

        # 鸟种：得票最高的 / Species: winner takes all
        if species_votes:
            (winner_zh, winner_en), total_score = max(
                species_votes.items(), key=lambda kv: kv[1])
            seg.species_zh = winner_zh
            seg.species_en = winner_en
            seg.species_conf = total_score / len(seg_frames)  # 归一化置信度

        # 飞行：多数投票（>50% 帧判定为飞行）
        # Flight: majority vote (>50% frames flagged flying)
        if flight_total > 0:
            seg.is_flying = flying_count > flight_total / 2
            seg.flight_conf = flight_conf_sum / flight_total

    @staticmethod
    def _seek_and_read(cap: cv2.VideoCapture,
                       t_sec: float) -> Optional[np.ndarray]:
        """
        跳到指定时间点并读取一帧（用 seek，因为通常是稀疏访问）

        Seek to t_sec and read one frame (seek strategy, since access is sparse).
        """
        cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
        ok, frame = cap.read()
        return frame if ok else None


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


@dataclass(slots=True)
class VideoMetadata:
    """视频元数据（仅读 header，不解码帧） / Video metadata (header only, no decode)"""
    fps: float
    duration_sec: float
    width: int
    height: int
    frame_count: int


def probe_video_metadata(video_path: str) -> Optional[VideoMetadata]:
    """
    轻量探测视频元数据（仅读容器 header，不解码帧）

    用于 UI 在"待处理"阶段显示视频时长。单次调用通常 < 20ms。

    参数:
        video_path (str): 视频文件路径

    返回:
        Optional[VideoMetadata]: 元数据；无法打开时返回 None（不抛异常，便于 UI 容错）

    Lightly probe video metadata (container header only, no frame decode).
    Used by UI to show duration during the "pending" state.

    Returns None if the file cannot be opened (no exception, UI-friendly).
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration_sec = frame_count / fps if fps > 0 else 0.0
        return VideoMetadata(
            fps=fps, duration_sec=duration_sec,
            width=width, height=height, frame_count=frame_count,
        )
    finally:
        cap.release()
