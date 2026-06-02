#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 2 — 鸟类识别 ABC 接口层

为视频分析的「鸟种识别 / 飞行检测」定义抽象接口，让 video_analyzer 不直接依赖
具体的 birdid 或 FlightDetector 实现，便于未来替换为其他模型（OSEA / MegaDetector 等）。

设计原则（参考 SuperVideo 项目）：
    - 输入统一为 numpy.ndarray（BGR 帧，cv2 直出）+ 可选 bbox
    - 输出统一为不可变 dataclass
    - 实现者负责内部模型加载/缓存

ABC interface layer for bird species ID and flight detection.
Decouples video_analyzer from concrete birdid/FlightDetector implementations,
enabling future replacement with other models (OSEA / MegaDetector / etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


# ============================================================================
# 数据结构 / Data structures
# ============================================================================

@dataclass(frozen=True, slots=True)
class SpeciesResult:
    """
    单个鸟种识别结果

    Single bird species identification result.
    """
    name_zh: str            # 中文名 / Chinese name (e.g. "白鹭")
    name_en: str            # 英文名 / English name (e.g. "Little Egret")
    name_sci: str           # 学名 / Scientific name (e.g. "Egretta garzetta")
    confidence: float       # 置信度 (0-1)
    class_id: int = -1      # 模型内部 class id（可选，便于调试）


@dataclass(frozen=True, slots=True)
class FlightResult:
    """
    飞行检测结果

    Flight detection result.
    """
    is_flying: bool         # 是否在飞行
    confidence: float       # 置信度 (0-1)


# ============================================================================
# 鸟种识别接口 / Bird species classifier interface
# ============================================================================

class BirdClassifier(ABC):
    """
    鸟种识别抽象接口

    实现者：
        - core/birdid_adapter.py: BirdIDAdapter（包装现有 birdid 模块）
        - 未来可加：OSEAClassifier、MegaDetectorClassifier 等

    Bird species classifier interface.
    Concrete implementations: BirdIDAdapter (wraps the in-tree birdid module).
    """

    @abstractmethod
    def identify(self, image: np.ndarray, top_k: int = 5,
                 bbox: Optional[Tuple[int, int, int, int]] = None,
                 ) -> List[SpeciesResult]:
        """
        识别给定图像中的鸟种

        参数:
            image (np.ndarray): BGR 帧（cv2 直出），HxWx3 uint8
            top_k (int): 返回 Top-K 个候选结果，默认 5
            bbox (Optional[Tuple[int,int,int,int]]): 鸟类边界框 (x1,y1,x2,y2)
                                                      若提供，实现可裁剪后识别（更快更准）
                                                      若为 None，实现自行检测

        返回:
            List[SpeciesResult]: 按置信度降序排列的 Top-K 结果
                                 空列表表示未识别出任何鸟

        Identify bird species in the given image.

        Parameters:
            image: BGR frame (cv2 native), shape HxWx3 uint8
            top_k: return top K candidates, default 5
            bbox: optional bird bounding box (x1,y1,x2,y2). If given, implementation
                  may crop before classification (faster & more accurate).
                  If None, implementation handles detection internally.

        Return:
            Top-K results sorted by confidence descending.
            Empty list = no bird identified.
        """
        ...


# ============================================================================
# 飞行检测接口 / Flight detector interface
# ============================================================================

class FlightClassifier(ABC):
    """
    飞行/停栖二分类接口

    实现者：
        - core/flight_adapter.py: FlightAdapter（包装现有 FlightDetector）

    Flight/perched binary classifier interface.
    """

    @abstractmethod
    def detect(self, image: np.ndarray,
               bbox: Optional[Tuple[int, int, int, int]] = None,
               ) -> FlightResult:
        """
        判断图像中的鸟是否在飞行

        参数:
            image (np.ndarray): BGR 帧（cv2 直出），HxWx3 uint8
            bbox (Optional): 鸟类边界框，提供时实现可裁剪后判断

        返回:
            FlightResult: is_flying + confidence

        Decide whether the bird in the image is flying.

        Parameters:
            image: BGR frame
            bbox: optional bird bbox; if given, crop before classification

        Return:
            FlightResult with is_flying + confidence.
        """
        ...
