#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 — 视频时间段合并与 SRT 字幕生成

纯函数模块，不依赖任何 AI 模型或 IO，便于单元测试。
提供：
    - BirdSegment           ：时间段数据类
    - merge_frame_results() ：把逐帧检测结果合并为连续时间段
    - filter_short_segments(): 过滤过短的时间段（误检）
    - write_srt()           ：写出 SRT 字幕文件

Pure-function module for video segment merging and SRT subtitle generation.
No AI model or IO dependencies, easy to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass
class FrameDetection:
    """
    单帧检测结果（来自 video_analyzer）

    Per-frame detection result (produced by video_analyzer).
    """
    timestamp_sec: float        # 该帧在视频中的时间戳（秒）
    has_bird: bool              # 是否检测到鸟
    max_conf: float = 0.0       # 该帧最高鸟类置信度（无鸟时为 0）
    bird_count: int = 0         # 该帧检测到的鸟数量


@dataclass
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
    cur_confs: List[float] = []
    cur_frame_count = 0

    if frames[0].has_bird:
        cur_confs.append(frames[0].max_conf)
    cur_frame_count = 1

    for i in range(1, len(frames)):
        f = frames[i]
        if f.has_bird != cur_state:
            # 状态切换：在两帧之间取中点作为段切点
            # State change: use midpoint between two frames as the cut
            cut_sec = (frames[i - 1].timestamp_sec + f.timestamp_sec) / 2.0
            segments.append(_finalize_segment(
                cur_start, cut_sec, cur_state, cur_frame_count, cur_confs))
            cur_state = f.has_bird
            cur_start = cut_sec
            cur_confs = [f.max_conf] if f.has_bird else []
            cur_frame_count = 1
        else:
            if f.has_bird:
                cur_confs.append(f.max_conf)
            cur_frame_count += 1

    # 最后一段延伸到视频末尾 / Last segment extends to video end
    segments.append(_finalize_segment(
        cur_start, video_duration_sec, cur_state, cur_frame_count, cur_confs))
    return segments


def _finalize_segment(start: float, end: float, has_bird: bool,
                      frame_count: int, confs: List[float]) -> BirdSegment:
    """生成一个 BirdSegment（计算平均/最大置信度）"""
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    max_conf = max(confs) if confs else 0.0
    return BirdSegment(
        start_sec=start,
        end_sec=end,
        has_bird=has_bird,
        frame_count=frame_count,
        avg_conf=avg_conf,
        max_conf=max_conf,
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

    Merge `guest` into `host`. Result keeps host's has_bird value.
    """
    new_frame_count = host.frame_count + guest.frame_count
    # 只统计与 host 同向的置信度（host 是有鸟段才有意义）
    if host.has_bird:
        # 用加权平均估算 / Weighted average
        host_total = host.avg_conf * host.frame_count
        guest_total = guest.avg_conf * guest.frame_count if guest.has_bird else 0.0
        new_avg = (host_total + guest_total) / new_frame_count if new_frame_count > 0 else 0.0
        new_max = max(host.max_conf, guest.max_conf if guest.has_bird else 0.0)
    else:
        new_avg = 0.0
        new_max = 0.0

    return BirdSegment(
        start_sec=min(host.start_sec, guest.start_sec),
        end_sec=max(host.end_sec, guest.end_sec),
        has_bird=host.has_bird,
        frame_count=new_frame_count,
        avg_conf=new_avg,
        max_conf=new_max,
    )


# ============================================================================
# SRT 字幕生成 / SRT subtitle generation
# ============================================================================

def write_srt(segments: List[BirdSegment], output_path: str | Path,
              bird_label: str = "🐦 检测到鸟类",
              no_bird_label: str = "[无鸟]") -> None:
    """
    把时间段列表写成 SRT 字幕文件

    SRT 格式（每条 4 行）：
        序号
        00:00:00,000 --> 00:00:05,000
        字幕内容
        （空行）

    参数:
        segments (List[BirdSegment]): 时间段列表
        output_path (str | Path)    : 输出 SRT 文件路径（UTF-8 编码）
        bird_label (str)            : 有鸟段的字幕前缀
        no_bird_label (str)         : 无鸟段的字幕

    返回:
        None

    Write segments as an SRT subtitle file (UTF-8).
    """
    lines: List[str] = []
    for idx, seg in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_format_srt_time(seg.start_sec)} --> {_format_srt_time(seg.end_sec)}")
        if seg.has_bird:
            conf_pct = int(round(seg.avg_conf * 100))
            lines.append(f"{bird_label} | 置信度 {conf_pct}%")
        else:
            lines.append(no_bird_label)
        lines.append("")  # 空行分隔

    out_path = Path(output_path)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _format_srt_time(seconds: float) -> str:
    """
    秒数 → SRT 时间格式 (HH:MM:SS,mmm)

    Convert seconds to SRT time format (HH:MM:SS,mmm).
    """
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
