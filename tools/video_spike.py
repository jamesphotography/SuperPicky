#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频 YOLO 推理性能 Spike 脚本

目标：在真实素材上验证「自适应抽帧 + YOLO 鸟类检测」的实际性能，
为 Phase 1 提供性能基线数据。

策略：
    短视频（≤60s）        : 1 秒/帧
    长视频（>60s）        : ceil(duration / max_frames) 秒/帧
    上限 max_frames=60   : 保证 YOLO 推理时间被压在常数级

测量项：
    1. 视频元数据读取耗时
    2. 抽帧耗时（cv2 seek + read）
    3. YOLO 推理耗时（逐帧）
    4. 检测结果：有鸟帧 / 无鸟帧 / 区间数

Performance spike script for video YOLO inference.
Goal: validate adaptive sampling + YOLO bird detection on real footage,
providing performance baseline data for Phase 1.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Tuple

import cv2
import numpy as np

# 把项目根加入 sys.path，便于直接导入 config / ai_model
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import config  # noqa: E402
from ultralytics import YOLO  # noqa: E402


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass
class FrameResult:
    """单帧 YOLO 检测结果 / Single frame YOLO detection result"""
    timestamp_sec: float
    has_bird: bool
    max_bird_conf: float           # 该帧最高鸟类置信度
    bird_count: int                # 鸟的数量
    yolo_ms: float                 # YOLO 推理耗时
    decode_ms: float               # 帧解码耗时


@dataclass
class VideoReport:
    """单个视频的完整分析报告 / Complete analysis report for one video"""
    path: str
    strategy: str                  # 'seek' 或 'grab'
    width: int
    height: int
    fps: float
    duration_sec: float
    frame_interval_sec: float
    sampled_frames: int
    total_decode_ms: float
    total_yolo_ms: float
    total_wall_ms: float           # 总耗时（含一切开销）
    bird_frames: int
    no_bird_frames: int
    frame_results: List[FrameResult] = field(default_factory=list)


# ============================================================================
# 抽帧策略 / Adaptive frame sampling strategy
# ============================================================================

def compute_frame_interval(duration_sec: float, max_frames: int = 60) -> float:
    """
    自适应抽帧间隔计算

    短视频（duration ≤ max_frames）：固定 1 秒/帧
    长视频：interval = ceil(duration / max_frames)，保证总帧数 ≤ max_frames

    参数:
        duration_sec (float): 视频总时长（秒）
        max_frames (int): 最大抽帧数上限，默认 60

    返回:
        float: 抽帧间隔（秒）

    Compute adaptive frame sampling interval.
    Short videos: fixed 1s/frame. Long videos: interval scales to cap total frames.

    Parameters:
        duration_sec (float): video duration in seconds
        max_frames (int): max total sampled frames, default 60

    Return:
        float: sampling interval in seconds
    """
    if duration_sec <= max_frames:
        return 1.0
    return float(math.ceil(duration_sec / max_frames))


# ============================================================================
# 视频抽帧 + YOLO 推理 / Video sampling + YOLO inference
# ============================================================================

# 混合策略阈值：当 (fps × interval_sec) 超过此值时，seek 比 grab 更快
# 经验值来自 4K 视频 A/B 测试：30fps×1s=30 用 grab 快 2.4×；100fps×1s=100 用 seek 快 1.75×
# Threshold for hybrid strategy: seek wins when frames-to-skip exceeds this.
SEEK_THRESHOLD_FRAMES = 60


def pick_strategy(fps: float, interval_sec: float,
                  threshold: int = SEEK_THRESHOLD_FRAMES) -> Literal['seek', 'grab']:
    """
    根据 fps × interval_sec 自适应选择抽帧策略

    grab 适合「采样间隔较密 + 帧率低」的场景（跳过帧数少）
    seek 适合「采样间隔稀疏 + 帧率高」的场景（直接跳到 I-frame 更划算）

    参数:
        fps (float): 视频帧率
        interval_sec (float): 抽帧间隔（秒）
        threshold (int): 切换阈值（帧），默认 60

    返回:
        'seek' 或 'grab'

    Adaptively choose decode strategy based on frames-to-skip per sample.
    """
    frames_per_sample = fps * interval_sec
    return 'seek' if frames_per_sample > threshold else 'grab'


def _decode_frame_seek(cap: cv2.VideoCapture, t_sec: float,
                       _state: dict) -> Tuple[bool, np.ndarray]:
    """
    Seek 策略：直接 set(POS_MSEC) → read。
    缺点：每次 seek 都要回到最近 I-frame 重解码（4K H.265 GOP 长，很慢）。

    Seek strategy: jump to absolute timestamp.
    Slow because each seek re-decodes from the nearest I-frame.
    """
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
    return cap.read()


def _decode_frame_grab(cap: cv2.VideoCapture, t_sec: float,
                       state: dict) -> Tuple[bool, np.ndarray]:
    """
    Grab/Retrieve 策略：顺序遍历，用 grab()（只读 bitstream 不解码）跳到目标帧，
    再 retrieve() 解码目标帧。

    state 字典持有当前已 grab 到的帧索引 `cursor`，函数内会自增。

    Grab/Retrieve strategy: sequentially grab() (no decode) until target,
    then retrieve() to decode only the target frame.
    """
    fps = state['fps']
    target_idx = int(round(t_sec * fps))
    cursor = state['cursor']

    # 用 grab() 跳过中间帧（极快，因为不做 YUV→BGR 转换）
    # grab() through intermediate frames (very fast, no decode)
    while cursor < target_idx:
        ok = cap.grab()
        if not ok:
            return False, None  # 视频结束
        cursor += 1

    # retrieve() 解码当前 grab 到的帧
    # 注意：第一帧 (target=0) 时也需要 grab 一次再 retrieve
    if cursor == target_idx:
        ok = cap.grab()
        if not ok:
            return False, None
        cursor += 1

    state['cursor'] = cursor
    return cap.retrieve()


def analyze_video(video_path: str, model: YOLO, max_frames: int = 60,
                  conf_threshold: float = 0.5,
                  strategy: Literal['seek', 'grab', 'auto'] = 'auto') -> VideoReport:
    """
    对单个视频执行抽帧 + YOLO 鸟类检测

    参数:
        video_path (str): 视频文件绝对路径
        model (YOLO): 已加载的 YOLO 模型
        max_frames (int): 最大抽帧数，默认 60
        conf_threshold (float): YOLO 置信度阈值，默认 0.5
        strategy ('seek'|'grab'|'auto'): 抽帧策略，默认 'auto'（按 fps×interval 自适应）

    返回:
        VideoReport: 完整性能与检测报告

    Analyze a single video: adaptive sampling + YOLO bird detection.
    """
    wall_start = time.perf_counter()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"无法打开视频 / Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_sec = frame_count / fps if fps > 0 else 0.0

    interval_sec = compute_frame_interval(duration_sec, max_frames)
    sample_times = np.arange(0.0, duration_sec, interval_sec)

    # auto 策略：根据 fps × interval 自动选 seek 或 grab
    # auto strategy: pick seek/grab by fps × interval
    if strategy == 'auto':
        resolved = pick_strategy(fps, interval_sec)
        report_strategy = f"auto→{resolved}"
    else:
        resolved = strategy
        report_strategy = strategy

    bird_class_id = config.ai.BIRD_CLASS_ID
    frame_results: List[FrameResult] = []
    total_decode_ms = 0.0
    total_yolo_ms = 0.0
    bird_frames = 0
    no_bird_frames = 0

    # 选择抽帧函数 / Pick decode function by resolved strategy
    if resolved == 'grab':
        decode_fn = _decode_frame_grab
    elif resolved == 'seek':
        decode_fn = _decode_frame_seek
    else:
        raise ValueError(f"未知策略 / Unknown strategy: {resolved}")

    # grab 策略需要持续跟踪当前帧位置 / grab strategy needs a cursor
    state = {'fps': fps, 'cursor': 0}

    for t_sec in sample_times:
        decode_start = time.perf_counter()
        ret, frame = decode_fn(cap, float(t_sec), state)
        decode_ms = (time.perf_counter() - decode_start) * 1000.0
        total_decode_ms += decode_ms

        if not ret or frame is None:
            continue

        # —— YOLO 推理 / YOLO inference
        yolo_start = time.perf_counter()
        results = model(frame, verbose=False, conf=conf_threshold,
                        classes=[bird_class_id])
        yolo_ms = (time.perf_counter() - yolo_start) * 1000.0
        total_yolo_ms += yolo_ms

        # —— 提取结果 / Extract detection results
        boxes = results[0].boxes
        confs = boxes.conf.cpu().numpy() if boxes is not None and len(boxes) > 0 else np.array([])
        bird_count = int(len(confs))
        has_bird = bird_count > 0
        max_conf = float(confs.max()) if bird_count > 0 else 0.0

        if has_bird:
            bird_frames += 1
        else:
            no_bird_frames += 1

        frame_results.append(FrameResult(
            timestamp_sec=float(t_sec),
            has_bird=has_bird,
            max_bird_conf=max_conf,
            bird_count=bird_count,
            yolo_ms=yolo_ms,
            decode_ms=decode_ms,
        ))

        del results

    cap.release()
    total_wall_ms = (time.perf_counter() - wall_start) * 1000.0

    return VideoReport(
        path=video_path,
        strategy=report_strategy,
        width=width,
        height=height,
        fps=fps,
        duration_sec=duration_sec,
        frame_interval_sec=interval_sec,
        sampled_frames=len(frame_results),
        total_decode_ms=total_decode_ms,
        total_yolo_ms=total_yolo_ms,
        total_wall_ms=total_wall_ms,
        bird_frames=bird_frames,
        no_bird_frames=no_bird_frames,
        frame_results=frame_results,
    )


# ============================================================================
# 报告打印 / Report printing
# ============================================================================

def print_report(report: VideoReport) -> None:
    """打印单个视频的分析报告 / Print analysis report for one video"""
    name = os.path.basename(report.path)
    print("=" * 78)
    print(f"📹 {name}")
    print(f"   分辨率 / Resolution : {report.width}x{report.height}")
    print(f"   帧率 / FPS          : {report.fps:.1f}")
    print(f"   时长 / Duration     : {report.duration_sec:.1f}s")
    print(f"   抽帧间隔 / Interval : {report.frame_interval_sec:.1f}s "
          f"→ 共 {report.sampled_frames} 帧")
    print(f"")
    print(f"   🔍 检测结果 / Detection:")
    print(f"      有鸟帧 / Bird frames    : {report.bird_frames}")
    print(f"      无鸟帧 / No-bird frames : {report.no_bird_frames}")
    print(f"")
    print(f"   ⏱  性能 / Performance:")
    if report.sampled_frames > 0:
        avg_decode = report.total_decode_ms / report.sampled_frames
        avg_yolo = report.total_yolo_ms / report.sampled_frames
        print(f"      解码耗时合计 / Total decode  : {report.total_decode_ms:7.1f} ms  "
              f"(avg {avg_decode:.1f} ms/帧)")
        print(f"      YOLO 推理合计 / Total YOLO   : {report.total_yolo_ms:7.1f} ms  "
              f"(avg {avg_yolo:.1f} ms/帧)")
    print(f"      总挂钟时间 / Total wall-time : {report.total_wall_ms:7.1f} ms  "
          f"({report.total_wall_ms/1000.0:.1f}s)")
    print()

    # 简单区间合并：连续相同状态帧 → 时间段 / Simple segment merge
    segments = _merge_segments(report.frame_results, report.frame_interval_sec,
                               report.duration_sec)
    print(f"   🕐 时间段合并 / Segments ({len(segments)} 段):")
    for i, (start, end, has_bird, avg_conf) in enumerate(segments[:15], 1):
        label = f"🐦 有鸟 (avg conf {avg_conf:.2f})" if has_bird else "[无鸟]"
        print(f"      {i:2d}. {_fmt_time(start)} → {_fmt_time(end)}  {label}")
    if len(segments) > 15:
        print(f"      ... ({len(segments) - 15} 段省略)")
    print()


def _merge_segments(frames: List[FrameResult], interval_sec: float,
                    duration_sec: float) -> List[Tuple[float, float, bool, float]]:
    """
    简单区间合并：相邻同状态帧合并为一个时间段

    返回 [(start_sec, end_sec, has_bird, avg_conf), ...]

    Simple segment merge: adjacent same-state frames → one time segment.
    """
    if not frames:
        return []

    segments: List[Tuple[float, float, bool, float]] = []
    cur_state = frames[0].has_bird
    cur_start = 0.0
    cur_confs: List[float] = [frames[0].max_bird_conf]

    for i in range(1, len(frames)):
        f = frames[i]
        if f.has_bird != cur_state:
            seg_end = frames[i].timestamp_sec
            avg_conf = float(np.mean(cur_confs)) if cur_state and cur_confs else 0.0
            segments.append((cur_start, seg_end, cur_state, avg_conf))
            cur_state = f.has_bird
            cur_start = seg_end
            cur_confs = [f.max_bird_conf]
        else:
            cur_confs.append(f.max_bird_conf)

    # 最后一段一直延伸到视频末尾 / Last segment extends to video end
    avg_conf = float(np.mean(cur_confs)) if cur_state and cur_confs else 0.0
    segments.append((cur_start, duration_sec, cur_state, avg_conf))
    return segments


def _fmt_time(sec: float) -> str:
    """秒 → mm:ss 格式 / Seconds → mm:ss"""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


# ============================================================================
# 主入口 / Main entry
# ============================================================================

def main():
    video_dir = "/Users/jameszhenyu/Desktop/Bird-Video"
    videos = sorted([
        os.path.join(video_dir, f)
        for f in os.listdir(video_dir)
        if f.lower().endswith(('.mov', '.mp4', '.m4v'))
    ])

    if not videos:
        print(f"❌ 未找到测试视频 / No test videos found in: {video_dir}")
        return 1

    print(f"🚀 加载 YOLO 模型 / Loading YOLO model...")
    model_path = config.ai.get_model_path()
    print(f"   模型路径 / Model path: {model_path}")

    load_start = time.perf_counter()
    model = YOLO(model_path)

    # macOS 优先用 MPS 加速 / Prefer MPS on macOS
    try:
        from config import get_best_device
        device = get_best_device()
        print(f"   推理设备 / Device: {device.type}")
    except Exception as e:
        print(f"   ⚠️ 设备检测失败 / Device detection failed: {e}")

    load_ms = (time.perf_counter() - load_start) * 1000.0
    print(f"   加载耗时 / Load time: {load_ms:.0f}ms\n")

    # 跑三种策略：seek / grab / auto（混合自适应）做 3-way 对比
    # Run three strategies: seek / grab / auto (hybrid) for 3-way comparison
    strategies: List[Literal['seek', 'grab', 'auto']] = ['seek', 'grab', 'auto']
    reports_by_strategy: dict[str, List[VideoReport]] = {s: [] for s in strategies}
    grand_start = time.perf_counter()

    for strategy in strategies:
        print(f"\n{'#' * 78}")
        print(f"#  策略 / Strategy: {strategy.upper()}")
        print(f"{'#' * 78}\n")
        for v in videos:
            try:
                r = analyze_video(v, model, max_frames=60,
                                  conf_threshold=0.5, strategy=strategy)
                print_report(r)
                reports_by_strategy[strategy].append(r)
            except Exception as e:
                print(f"❌ 处理失败 / Failed: {os.path.basename(v)}: {e}")

    grand_wall_ms = (time.perf_counter() - grand_start) * 1000.0

    # 汇总 + 3-way 对比 / Summary + 3-way comparison
    print("=" * 78)
    print("📊 三策略对比 / 3-WAY STRATEGY COMPARISON")
    print("=" * 78)
    _print_3way_table(reports_by_strategy)
    print()
    print(f"   总挂钟时间 / Total wall-time (3 轮): {grand_wall_ms/1000.0:.1f}s")
    print(f"   模型加载 / Model load (一次)       : {load_ms/1000.0:.1f}s")
    print()
    print("✅ Spike 完成 / Spike complete")
    return 0


def _print_3way_table(reports: dict) -> None:
    """打印 seek / grab / auto 三策略对比表"""
    seek = reports.get('seek', [])
    grab = reports.get('grab', [])
    auto = reports.get('auto', [])
    if not seek or not grab or not auto:
        print("   ⚠️ 数据不全，跳过对比 / Incomplete data, skipping comparison")
        return

    # 表 1：总耗时（解码 + YOLO + 其他）/ Table 1: total wall time
    print(f"\n   ── 总耗时对比 (Wall-clock time) ──\n")
    print(f"   {'视频':<22s} {'fps':>5s} {'帧数':>4s}  "
          f"{'Seek':>8s} {'Grab':>8s} {'Auto':>8s}  "
          f"{'Auto 选择':>12s} {'Auto vs Seek':>12s}")
    print(f"   {'-' * 22} {'-' * 5} {'-' * 4}  "
          f"{'-' * 8} {'-' * 8} {'-' * 8}  {'-' * 12} {'-' * 12}")

    total_seek = 0.0
    total_grab = 0.0
    total_auto = 0.0

    for s, g, a in zip(seek, grab, auto):
        name = os.path.basename(s.path)[:20]
        seek_speedup = s.total_wall_ms / a.total_wall_ms if a.total_wall_ms > 0 else 0
        print(f"   {name:<22s} {s.fps:>5.0f} {s.sampled_frames:>4d}  "
              f"{s.total_wall_ms/1000:>6.1f}s {g.total_wall_ms/1000:>6.1f}s "
              f"{a.total_wall_ms/1000:>6.1f}s  "
              f"{a.strategy:>12s} {seek_speedup:>10.2f}×")
        total_seek += s.total_wall_ms
        total_grab += g.total_wall_ms
        total_auto += a.total_wall_ms

    print(f"   {'-' * 22} {'-' * 5} {'-' * 4}  "
          f"{'-' * 8} {'-' * 8} {'-' * 8}  {'-' * 12} {'-' * 12}")
    auto_vs_seek = total_seek / total_auto if total_auto > 0 else 0
    auto_vs_grab = total_grab / total_auto if total_auto > 0 else 0
    print(f"   {'汇总 / TOTAL':<22s} {'':5s} {'':4s}  "
          f"{total_seek/1000:>6.1f}s {total_grab/1000:>6.1f}s "
          f"{total_auto/1000:>6.1f}s")
    print()
    print(f"   ⚡ Auto vs Seek (基线): {auto_vs_seek:.2f}× 提速")
    print(f"   ⚡ Auto vs Grab       : {auto_vs_grab:.2f}× 提速")

    # 表 2：解码耗时细节 / Table 2: decode time breakdown
    print(f"\n   ── 解码耗时细节 (Decode time only) ──\n")
    print(f"   {'视频':<22s}  {'Seek 解码':>12s} {'Grab 解码':>12s} {'Auto 解码':>12s}")
    print(f"   {'-' * 22}  {'-' * 12} {'-' * 12} {'-' * 12}")
    for s, g, a in zip(seek, grab, auto):
        name = os.path.basename(s.path)[:20]
        print(f"   {name:<22s}  "
              f"{s.total_decode_ms:>10.0f}ms {g.total_decode_ms:>10.0f}ms "
              f"{a.total_decode_ms:>10.0f}ms")

    # 检测一致性校验 / Detection consistency check
    print()
    print(f"   🔬 检测结果一致性校验 / Detection consistency check:")
    all_match = True
    for s, g, a in zip(seek, grab, auto):
        name = os.path.basename(s.path)
        match = (s.bird_frames == a.bird_frames and s.no_bird_frames == a.no_bird_frames
                 and g.bird_frames == a.bird_frames and g.no_bird_frames == a.no_bird_frames)
        status = "✅" if match else "⚠️"
        if not match:
            all_match = False
        print(f"      {status} {name}: "
              f"seek={s.bird_frames}/{s.no_bird_frames}  "
              f"grab={g.bird_frames}/{g.no_bird_frames}  "
              f"auto={a.bird_frames}/{a.no_bird_frames}")
    if all_match:
        print(f"   ✅ 三策略检测结果完全一致 / All three strategies consistent")


if __name__ == "__main__":
    sys.exit(main())
