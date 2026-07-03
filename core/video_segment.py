#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 — 视频时间段合并

纯函数模块，不依赖任何 AI 模型或 IO，便于单元测试。
提供：
    - BirdSegment           ：时间段数据类
    - merge_frame_results() ：把逐帧检测结果合并为连续时间段
    - filter_short_segments(): 过滤过短的时间段（误检）

Pure-function module for video segment merging.
No AI model or IO dependencies, easy to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass(slots=True)
class FrameDetection:
    """
    单帧检测结果（来自 video_analyzer）

    Per-frame detection result (produced by video_analyzer).
    """
    timestamp_sec: float        # 该帧在视频中的时间戳（秒）
    has_bird: bool              # 是否检测到鸟
    max_conf: float = 0.0       # 该帧最高鸟类置信度（无鸟时为 0）
    bird_count: int = 0         # 该帧检测到的鸟数量
    # V4.3 Phase 2: 最高置信度鸟的 bbox (x1, y1, x2, y2)；无鸟帧为 None
    # Bounding box of the highest-confidence bird (x1, y1, x2, y2); None if no bird.
    bbox: Optional[Tuple[int, int, int, int]] = None


@dataclass(slots=True)
class BirdSegment:
    """
    连续时间段（有鸟或无鸟）

    A contiguous video segment, either bird-present or bird-absent.
    """
    start_sec: float            # 段起始时间（秒）
    end_sec: float              # 段结束时间（秒）
    has_bird: bool              # 该段是否有鸟
    frame_count: int = 0        # 段内采样帧数
    avg_conf: float = 0.0       # 段内有鸟帧的平均置信度（无鸟段为 0）
    max_conf: float = 0.0       # 段内最大置信度（无鸟段为 0）

    # V4.3 Phase 2: 段内 max_conf 帧的时间戳和 bbox（供后处理鸟种/飞行识别）
    # Phase 2: timestamp and bbox of the max-conf frame in this segment.
    best_frame_sec: Optional[float] = None
    best_frame_bbox: Optional[Tuple[int, int, int, int]] = None

    # V4.3 Phase 2: 鸟种识别结果（Phase 2 填充，Phase 1 留空）
    # Phase 2: species identification result (filled by Phase 2, empty in Phase 1).
    species_zh: Optional[str] = None        # 中文名 / Chinese name
    species_en: Optional[str] = None        # 英文名 / English name
    species_conf: float = 0.0               # 鸟种识别置信度 (0-1)

    # V4.3 Phase 2: 飞行状态（Phase 2 填充）
    # Phase 2: flight state (filled by Phase 2).
    is_flying: Optional[bool] = None        # None 表示未检测；True/False 为已判定
    flight_conf: float = 0.0                # 飞行检测置信度

    @property
    def duration_sec(self) -> float:
        """段时长 / Segment duration in seconds"""
        return max(0.0, self.end_sec - self.start_sec)


# ============================================================================
# 区间合并 / Segment merging
# ============================================================================

def merge_frame_results(frames: List[FrameDetection],
                        video_duration_sec: float) -> List[BirdSegment]:
    """
    将逐帧检测结果合并为连续时间段

    规则：
        - 相邻同状态（有鸟/无鸟）的帧合并为一个段
        - 段的起止时间用「采样点中点」估算，避免一帧覆盖一个间隔产生的偏差
          首帧从 0 开始，末帧延伸到视频末尾
        - 有鸟段聚合 avg_conf / max_conf

    参数:
        frames (List[FrameDetection]): 时间戳升序排列的逐帧结果
        video_duration_sec (float): 视频总时长（秒），用于末段延伸

    返回:
        List[BirdSegment]: 时间段列表（时间升序）

    Merge per-frame results into contiguous time segments.

    Parameters:
        frames: per-frame results, sorted by timestamp ascending
        video_duration_sec: total video duration in seconds

    Return:
        Time segments in ascending order.
    """
    if not frames:
        return []

    segments: List[BirdSegment] = []
    cur_state = frames[0].has_bird
    cur_start = 0.0
    # 累积当前段的有鸟帧引用（仅有鸟段非空）—— 用于追踪 best_frame
    # Accumulate bird-frame references for the current segment (only non-empty for bird segments).
    cur_bird_frames: List[FrameDetection] = []
    cur_frame_count = 0

    if frames[0].has_bird:
        cur_bird_frames.append(frames[0])
    cur_frame_count = 1

    for i in range(1, len(frames)):
        f = frames[i]
        if f.has_bird != cur_state:
            # 状态切换：在两帧之间取中点作为段切点
            # State change: use midpoint between two frames as the cut
            cut_sec = (frames[i - 1].timestamp_sec + f.timestamp_sec) / 2.0
            segments.append(_finalize_segment(
                cur_start, cut_sec, cur_state, cur_frame_count, cur_bird_frames))
            cur_state = f.has_bird
            cur_start = cut_sec
            cur_bird_frames = [f] if f.has_bird else []
            cur_frame_count = 1
        else:
            if f.has_bird:
                cur_bird_frames.append(f)
            cur_frame_count += 1

    # 最后一段延伸到视频末尾 / Last segment extends to video end
    segments.append(_finalize_segment(
        cur_start, video_duration_sec, cur_state, cur_frame_count, cur_bird_frames))
    return segments


def _finalize_segment(start: float, end: float, has_bird: bool,
                      frame_count: int,
                      bird_frames: List[FrameDetection]) -> BirdSegment:
    """
    生成一个 BirdSegment：
    - avg_conf / max_conf: 段内有鸟帧的统计
    - best_frame_sec / best_frame_bbox: max_conf 帧的时间戳和 bbox（供 Phase 2 后处理）

    Build a BirdSegment with aggregate stats and best-frame pointers.
    """
    if bird_frames:
        confs = [f.max_conf for f in bird_frames]
        avg_conf = sum(confs) / len(confs)
        best = max(bird_frames, key=lambda f: f.max_conf)
        max_conf = best.max_conf
        best_frame_sec = best.timestamp_sec
        best_frame_bbox = best.bbox
    else:
        avg_conf = 0.0
        max_conf = 0.0
        best_frame_sec = None
        best_frame_bbox = None

    return BirdSegment(
        start_sec=start,
        end_sec=end,
        has_bird=has_bird,
        frame_count=frame_count,
        avg_conf=avg_conf,
        max_conf=max_conf,
        best_frame_sec=best_frame_sec,
        best_frame_bbox=best_frame_bbox,
    )


def filter_short_segments(segments: List[BirdSegment],
                          min_frames: int = 2,
                          video_duration_sec: float = 0.0) -> List[BirdSegment]:
    """
    过滤过短的时间段，避免误检产生的零星片段

    策略：
        - 若一个段的 frame_count < min_frames，将其并入「相邻较长的同向段」
          或就近合并到前一段
        - min_frames=1 时跳过过滤（保留所有段）

    参数:
        segments (List[BirdSegment]): 待过滤的段列表
        min_frames (int): 段最小帧数阈值，默认 2
        video_duration_sec (float): 视频总时长，用于末段边界

    返回:
        List[BirdSegment]: 合并后的段列表

    Filter out segments shorter than `min_frames` frames by merging into neighbors.
    """
    if min_frames <= 1 or len(segments) <= 1:
        return list(segments)

    # 反复扫描，直到没有过短段为止（最多 N 轮，避免极端构造）
    result = list(segments)
    for _ in range(len(segments)):
        idx = next((i for i, s in enumerate(result) if s.frame_count < min_frames), -1)
        if idx == -1:
            break
        result = _merge_at(result, idx)
        # 关键：吸收后可能产生「相邻同向段」，必须合并它们
        # Key: after absorption, may produce adjacent same-state segments.
        result = _coalesce_same_state(result)
    return result


def _coalesce_same_state(segments: List[BirdSegment]) -> List[BirdSegment]:
    """
    合并相邻同向段（has_bird 相同）

    Merge adjacent segments that share the same has_bird state.
    """
    if len(segments) <= 1:
        return list(segments)
    merged: List[BirdSegment] = [segments[0]]
    for seg in segments[1:]:
        if seg.has_bird == merged[-1].has_bird:
            merged[-1] = _absorb(merged[-1], seg)
        else:
            merged.append(seg)
    return merged


def _merge_at(segments: List[BirdSegment], idx: int) -> List[BirdSegment]:
    """
    把 segments[idx] 并入相邻段（优先并入较长的前/后邻居）

    Merge segments[idx] into a neighbor (prefer the longer adjacent segment).
    """
    if len(segments) == 1:
        return segments

    target = segments[idx]
    prev_seg = segments[idx - 1] if idx > 0 else None
    next_seg = segments[idx + 1] if idx < len(segments) - 1 else None

    # 优先选择「同向」的邻居（has_bird 相同）；否则选时长更长的邻居吞并
    # Prefer neighbor of the same has_bird value; otherwise pick the longer one.
    if prev_seg and next_seg:
        if prev_seg.has_bird == target.has_bird:
            chosen = 'prev'
        elif next_seg.has_bird == target.has_bird:
            chosen = 'next'
        else:
            chosen = 'prev' if prev_seg.duration_sec >= next_seg.duration_sec else 'next'
    elif prev_seg:
        chosen = 'prev'
    else:
        chosen = 'next'

    merged: List[BirdSegment] = []
    skip_idx = -1
    for i, seg in enumerate(segments):
        if i == skip_idx:
            continue
        if i == idx:
            if chosen == 'prev':
                # prev 吸收 target，has_bird 保持 prev 的
                merged[-1] = _absorb(merged[-1], seg)
            else:
                # next 吸收 target，has_bird 保持 next 的
                # host 必须是 next（segments[idx+1]），guest 是 target（seg）
                merged.append(_absorb(segments[idx + 1], seg))
                skip_idx = idx + 1
        else:
            merged.append(seg)
    return merged


def _absorb(host: BirdSegment, guest: BirdSegment) -> BirdSegment:
    """
    把 guest 段并入 host 段。结果段的 has_bird 沿用 host。
    best_frame 取两段中 max_conf 更大的那一个（仅当 host 是有鸟段时有意义）。

    Merge `guest` into `host`. Result keeps host's has_bird value.
    best_frame keeps the higher max_conf one (only meaningful if host has birds).
    """
    new_frame_count = host.frame_count + guest.frame_count
    # 只统计与 host 同向的置信度（host 是有鸟段才有意义）
    if host.has_bird:
        # 用加权平均估算 / Weighted average
        host_total = host.avg_conf * host.frame_count
        guest_total = guest.avg_conf * guest.frame_count if guest.has_bird else 0.0
        new_avg = (host_total + guest_total) / new_frame_count if new_frame_count > 0 else 0.0
        # best_frame 取 max_conf 较大的（仅 guest 也是有鸟段时才需比较）
        if guest.has_bird and guest.max_conf > host.max_conf:
            new_max = guest.max_conf
            new_best_sec = guest.best_frame_sec
            new_best_bbox = guest.best_frame_bbox
        else:
            new_max = host.max_conf
            new_best_sec = host.best_frame_sec
            new_best_bbox = host.best_frame_bbox
    else:
        new_avg = 0.0
        new_max = 0.0
        new_best_sec = None
        new_best_bbox = None

    return BirdSegment(
        start_sec=min(host.start_sec, guest.start_sec),
        end_sec=max(host.end_sec, guest.end_sec),
        has_bird=host.has_bird,
        frame_count=new_frame_count,
        avg_conf=new_avg,
        max_conf=new_max,
        best_frame_sec=new_best_sec,
        best_frame_bbox=new_best_bbox,
    )

