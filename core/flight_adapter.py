#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 2 — FlightDetector 适配器

把现有 `core/flight_detector.py` 的 `FlightDetector` 包装成
`core.bird_classifier_base.FlightClassifier` 接口，让 video_analyzer 可以无差别使用。

策略：
    - 全局单例（复用 get_flight_detector）
    - 首次 detect 时 lazy 加载模型（避免 UI 启动阻塞）
    - 输入 BGR 帧 + 可选 bbox，先 smart_square_crop 再传 FlightDetector
      （FlightDetector 期望「单只鸟」输入，整帧效果差）

FlightDetector adapter wrapping core.flight_detector.FlightDetector as
the FlightClassifier ABC interface for video_analyzer.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from core.bird_classifier_base import FlightClassifier
from core.bird_classifier_base import FlightResult as IfaceFlightResult
from tools.image_crop import smart_square_crop


class FlightAdapter(FlightClassifier):
    """
    包装 FlightDetector 单例为 FlightClassifier 接口

    Adapter wrapping core.flight_detector.FlightDetector singleton as a FlightClassifier.
    """

    def __init__(self, padding_ratio: float = 0.15,
                 threshold: Optional[float] = None):
        """
        参数:
            padding_ratio (float): bbox 周围 padding 比例，默认 0.15
            threshold (Optional[float]): 飞行二分类阈值；None 表示用 FlightDetector 默认 0.5

        Parameters:
            padding_ratio: bbox padding ratio for smart_square_crop
            threshold: flight binary threshold; None uses FlightDetector default (0.5)
        """
        self.padding_ratio = padding_ratio
        self.threshold = threshold
        self._detector = None   # lazy-loaded

    def _ensure_loaded(self):
        """首次调用时加载模型 / Lazy-load on first call"""
        if self._detector is not None and self._detector.model_loaded:
            return
        from core.flight_detector import get_flight_detector
        self._detector = get_flight_detector()
        if not self._detector.model_loaded:
            self._detector.load_model()

    def detect(self, image: np.ndarray,
               bbox: Optional[Tuple[int, int, int, int]] = None,
               ) -> IfaceFlightResult:
        """
        判断图像中的鸟是否飞行

        参数:
            image (np.ndarray): BGR 帧
            bbox (Optional): 鸟类 bbox，提供则裁剪后判断

        返回:
            FlightResult: is_flying + confidence

        Decide whether the bird in the image is flying.
        """
        # —— Step 1: 按 bbox 裁剪到单只鸟 / Crop to single bird if bbox given
        if bbox is not None:
            try:
                cropped = smart_square_crop(image, bbox,
                                            padding_ratio=self.padding_ratio)
            except ValueError:
                cropped = image
        else:
            cropped = image

        # —— Step 2: lazy 加载模型
        try:
            self._ensure_loaded()
        except Exception:
            return IfaceFlightResult(is_flying=False, confidence=0.0)

        # —— Step 3: 调 FlightDetector（自动处理 BGR→RGB→PIL→tensor）
        try:
            raw = self._detector.detect(cropped, threshold=self.threshold)
        except Exception:
            return IfaceFlightResult(is_flying=False, confidence=0.0)

        return IfaceFlightResult(
            is_flying=bool(raw.is_flying),
            confidence=float(raw.confidence),
        )
