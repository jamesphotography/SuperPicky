#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 4 — 批量视频处理引擎

把 Phase 1-3 的 VideoAnalyzer + VideoOrganizer 包装成一个**主流程友好**的引擎：
    - 接受主窗口 WorkerThread 的 log / progress / stop 回调
    - 从 advanced_config 读模式参数
    - lazy 加载 YOLO/BirdID/Flight 模型（首个视频处理时）
    - 批量处理一组视频，分析 + 归类一气呵成
    - 返回汇总统计供主窗口显示

Batch video processing engine for Phase 4 main-flow integration.
Wraps VideoAnalyzer + VideoOrganizer with WorkerThread-friendly callbacks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass(slots=True)
class VideoBatchStats:
    """
    批量视频处理统计 / Batch processing statistics
    """
    total: int = 0
    analyzed: int = 0              # 成功分析的视频数
    organized: int = 0             # 成功整理（move）的视频数
    failed: int = 0                # 整理失败的视频数
    cancelled: int = 0             # 被用户中断的视频数
    species_counts: dict = field(default_factory=dict)   # 鸟种 → 视频数
    total_wall_ms: float = 0.0     # 总耗时（不含模型加载）
    model_load_ms: float = 0.0     # 模型加载耗时


# ============================================================================
# 主引擎 / Main engine
# ============================================================================

# 回调函数类型签名 / Callback type signatures
LogCallback = Callable[[str, str], None]            # (message, level) → None
ProgressCallback = Callable[[int, int], None]       # (current, total) → None
StopCallback = Callable[[], bool]                   # () → True 表示请求停止


class VideoBatchEngine:
    """
    批量视频处理引擎：分析 + 自动归类

    一次性处理一批视频（通常来自同一目录），lazy 加载模型，
    所有日志/进度/中断通过回调暴露给主窗口，UI 体验跟照片端一致。

    Batch video processing engine: analyze + auto-organize.
    """

    def __init__(self,
                 max_frames: int = 60,
                 yolo_threshold: float = 0.5,
                 min_segment_frames: int = 2,
                 species_mode: str = 'instant',
                 enable_species: bool = True,
                 enable_flight: bool = True):
        """
        参数:
            max_frames         : 单视频抽帧上限
            yolo_threshold     : YOLO 置信度阈值
            min_segment_frames : 段最少帧数（过滤误检）
            species_mode       : 'instant' / 'fast' / 'full'
            enable_species     : 是否启用鸟种识别
            enable_flight      : 是否启用飞行检测

        Parameters mirror VideoAnalyzer's constructor for one-shot config.
        """
        self.max_frames = max_frames
        self.yolo_threshold = yolo_threshold
        self.min_segment_frames = min_segment_frames
        self.species_mode = species_mode
        self.enable_species = enable_species
        self.enable_flight = enable_flight

        # Lazy-loaded models
        self._yolo = None
        self._species_clf = None
        self._flight_clf = None

    def process(self,
                video_paths: List[str],
                log_cb: Optional[LogCallback] = None,
                progress_cb: Optional[ProgressCallback] = None,
                stop_cb: Optional[StopCallback] = None) -> VideoBatchStats:
        """
        处理一批视频

        参数:
            video_paths : 视频文件绝对路径列表
            log_cb      : 日志回调 (message, level)；level ∈ info/success/warning/error
            progress_cb : 进度回调 (current_idx, total)
            stop_cb     : 中断检查回调 () → bool；True 表示用户请求停止

        返回:
            VideoBatchStats: 汇总统计

        Process a batch of videos. Returns aggregate stats.
        """
        import time
        from tools.video_organizer import (
            VideoOrganizer, OrganizeOptions, record_organized_results,
        )
        from core.video_analyzer import VideoAnalyzer
        from tools.i18n import get_i18n

        stats = VideoBatchStats(total=len(video_paths))
        if not video_paths:
            return stats

        # 1. 模型加载（首次） / Load models on first call
        self._log(log_cb, f"🎬 开始处理 {len(video_paths)} 个视频", "info")
        load_start = time.perf_counter()
        self._lazy_load_models(log_cb)
        stats.model_load_ms = (time.perf_counter() - load_start) * 1000.0

        if self._yolo is None:
            self._log(log_cb, "⚠️ YOLO 模型加载失败，跳过视频处理", "error")
            stats.failed = stats.total
            return stats

        # 2. 实例化分析器 + 整理器 / Build analyzer + organizer
        analyzer = VideoAnalyzer(
            yolo_model=self._yolo,
            max_frames=self.max_frames,
            yolo_threshold=self.yolo_threshold,
            min_segment_frames=self.min_segment_frames,
            species_classifier=self._species_clf,
            flight_classifier=self._flight_clf,
            species_mode=self.species_mode,
        )
        # 落地命名跟随界面语言 / Folder naming follows UI language
        _i18n = get_i18n()
        _use_en = _i18n.current_lang.startswith('en')
        organizer = VideoOrganizer(options=OrganizeOptions(
            operation='move',
            use_english=_use_en,
            no_bird_folder=_i18n.t("video.folder_no_bird"),
            other_species_folder=_i18n.t("video.folder_other_species"),
        ))
        org_results = []  # 收集用于写「归类清单」（供复原）/ collect for the manifest

        # 3. 逐个视频处理 / Process videos one by one
        batch_start = time.perf_counter()
        for i, path in enumerate(video_paths, 1):
            # 检查中断 / Check cancellation
            if stop_cb is not None and stop_cb():
                stats.cancelled = stats.total - stats.analyzed
                self._log(log_cb, f"⏸ 用户中断，已处理 {stats.analyzed}/{stats.total}", "warning")
                break

            if progress_cb is not None:
                progress_cb(i, len(video_paths))

            name = os.path.basename(path)
            self._log(log_cb, f"  [{i}/{len(video_paths)}] 分析: {name}", "info")

            # 3a. 分析 / Analyze
            try:
                result = analyzer.analyze(path, stop_callback=stop_cb)
            except Exception as e:
                self._log(log_cb, f"    ❌ 分析失败: {e}", "error")
                stats.failed += 1
                continue

            if result.cancelled:
                stats.cancelled += 1
                continue

            stats.analyzed += 1

            # 3b. 整理 / Organize
            try:
                org_result = organizer.organize(path, result.segments)
                org_results.append(org_result)
            except Exception as e:
                self._log(log_cb, f"    ❌ 归类失败: {e}", "error")
                stats.failed += 1
                continue

            if org_result.success:
                stats.organized += 1
                # 统计鸟种 / Tally species
                if org_result.species_used:
                    primary = org_result.species_used[0]
                    stats.species_counts[primary] = stats.species_counts.get(primary, 0) + 1
                    self._log(log_cb,
                              f"    ✅ {primary} → {os.path.basename(org_result.target_video_path or '')}",
                              "success")
                else:
                    label = "无鸟" if not any(s.has_bird for s in result.segments) else "其他鸟"
                    self._log(log_cb,
                              f"    ✅ {label} → {os.path.basename(org_result.target_video_path or '')}",
                              "success")
            else:
                stats.failed += 1
                self._log(log_cb, f"    ❌ 整理失败: {org_result.error}", "error")

        stats.total_wall_ms = (time.perf_counter() - batch_start) * 1000.0

        # 写「归类清单」，供主界面「重置」精确复原 / persist manifest for reset-undo
        try:
            record_organized_results(org_results)
        except Exception:
            pass

        # 4. 汇总日志 / Summary log
        self._log_summary(log_cb, stats)
        return stats

    # ── 私有方法 / Private helpers ────────────────────────────────────

    def _lazy_load_models(self, log_cb: Optional[LogCallback]):
        """
        Lazy 加载 YOLO + 可选 BirdID + Flight 模型

        Lazy-load YOLO + optional BirdID + Flight on first call.
        """
        if self._yolo is None:
            try:
                from ai_model import load_yolo_model
                self._yolo = load_yolo_model()
            except Exception as e:
                self._log(log_cb, f"YOLO 加载失败: {e}", "error")
                return

        if self.enable_species and self._species_clf is None:
            try:
                from core.birdid_adapter import BirdIDAdapter
                self._species_clf = BirdIDAdapter()
            except Exception as e:
                self._log(log_cb, f"BirdID 加载失败，跳过鸟种识别: {e}", "warning")
                self._species_clf = None

        if self.enable_flight and self._flight_clf is None:
            try:
                from core.flight_adapter import FlightAdapter
                self._flight_clf = FlightAdapter()
            except Exception as e:
                self._log(log_cb, f"FlightDetector 加载失败，跳过飞行检测: {e}", "warning")
                self._flight_clf = None

    def _log(self, log_cb: Optional[LogCallback], msg: str, level: str = "info"):
        """统一日志输出 / Unified log emission"""
        if log_cb is not None:
            log_cb(msg, level)
        else:
            print(f"[{level}] {msg}")

    def _log_summary(self, log_cb: Optional[LogCallback], stats: VideoBatchStats):
        """打印批量处理汇总 / Print batch summary"""
        self._log(log_cb,
                  f"🎬 视频处理完毕："
                  f"分析 {stats.analyzed}/{stats.total}，"
                  f"归类 {stats.organized}，"
                  f"失败 {stats.failed}"
                  + (f"，中断 {stats.cancelled}" if stats.cancelled else ""),
                  "success" if stats.failed == 0 and stats.cancelled == 0 else "info")
        if stats.species_counts:
            top = sorted(stats.species_counts.items(), key=lambda kv: -kv[1])[:5]
            top_str = ", ".join(f"{name}×{cnt}" for name, cnt in top)
            self._log(log_cb, f"   🐦 主要鸟种: {top_str}", "info")
        self._log(log_cb,
                  f"   ⏱ 总耗时 {stats.total_wall_ms/1000:.1f}s"
                  + (f"（模型加载 {stats.model_load_ms/1000:.1f}s）"
                     if stats.model_load_ms > 0 else ""),
                  "info")
