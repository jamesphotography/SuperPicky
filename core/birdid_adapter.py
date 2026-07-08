#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 Phase 2 — BirdID 适配器

把现有 `birdid/bird_identifier.py` 的 `identify_bird()` 包装成
`core.bird_classifier_base.BirdClassifier` 接口，让 video_analyzer 可以无差别使用。

核心做法：
    1. numpy BGR 帧 → smart_square_crop（如有 bbox）→ PIL RGB Image
    2. 调 identify_bird(preloaded_crop=..., use_gps=False, use_ebird=False, ...)
       - 视频帧没有 EXIF / GPS，关闭这两项
       - preloaded_crop 跳过内部 YOLO 复检（性能 ↑）
    3. 把返回的 dict 列表 → SpeciesResult 列表

BirdID adapter wrapping birdid/bird_identifier.identify_bird() into the
BirdClassifier ABC interface for video_analyzer to consume uniformly.
"""

from __future__ import annotations

import os
import tempfile
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from core.bird_classifier_base import BirdClassifier, SpeciesResult
from tools.image_crop import smart_square_crop

# V4.4: identify() 在视频分析里逐帧调用，如果模型/数据库持续损坏，朴素的
# warning 会每帧打印一次刷屏；用一个进程级标志把日志限制为只打印一次，
# 既能定位问题又不会淹没其他日志。
# V4.4: identify() is called once per video frame; a naive warning would
# flood the log once per frame if the model/DB stays broken. A process-level
# flag caps this to a single print so the root cause is still discoverable
# without drowning out everything else.
_warned_identify_failure = False


class BirdIDAdapter(BirdClassifier):
    """
    包装现有 birdid 模块为 BirdClassifier 接口

    内部模型加载是 birdid.bird_identifier 内部 lazy 完成的，首次调用 identify_bird
    时触发，本类不主动管理模型生命周期。

    Adapter wrapping the in-tree birdid module as a BirdClassifier.
    Model loading is handled lazily by birdid.bird_identifier on first call.
    """

    def __init__(self, padding_ratio: float = 0.15,
                 name_format: Optional[str] = None):
        """
        参数:
            padding_ratio (float): smart_square_crop 的 padding 比例，默认 0.15
            name_format (Optional[str]): 鸟种英文名格式，传给 identify_bird
                                          ("default"/"avilist"/"clements"/"birdlife"/"scientific")
                                          None 表示使用 identify_bird 默认值

        Parameters:
            padding_ratio: padding ratio for smart_square_crop
            name_format: bird English name format passed to identify_bird
        """
        self.padding_ratio = padding_ratio
        self.name_format = name_format

    def identify(self, image: np.ndarray, top_k: int = 5,
                 bbox: Optional[Tuple[int, int, int, int]] = None,
                 ) -> List[SpeciesResult]:
        """
        识别图像中的鸟种

        参数:
            image (np.ndarray): BGR 帧（cv2 直出），HxWx3 uint8
            top_k (int): Top-K 候选，默认 5
            bbox (Optional[Tuple]): 鸟类 bbox (x1,y1,x2,y2)；提供则裁剪后识别

        返回:
            List[SpeciesResult]: Top-K 结果，置信度降序

        Identify bird species in the given BGR frame.
        """
        # —— Step 1: 裁剪到鸟类区域（若有 bbox）/ Crop to bird region if bbox given
        if bbox is not None:
            try:
                cropped_bgr = smart_square_crop(image, bbox,
                                                padding_ratio=self.padding_ratio)
            except ValueError:
                # bbox 无效则用整帧 / Fallback to full frame if bbox is invalid
                cropped_bgr = image
        else:
            cropped_bgr = image

        # —— Step 2: BGR → RGB → PIL Image（birdid 内部用 PIL）
        # —— Step 2: Convert BGR → RGB → PIL Image (birdid uses PIL internally)
        rgb = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb)

        # —— Step 3: 调 identify_bird，使用 preloaded_crop 跳过 YOLO 复检
        # —— Step 3: Call identify_bird with preloaded_crop to skip YOLO recheck
        # image_path 传一个不存在的虚拟路径即可（identify_bird 在 preloaded_crop 模式
        # 下不会真去读取，但参数签名要求一个 str）
        # image_path is a dummy here because preloaded_crop path skips file I/O.
        from birdid.bird_identifier import identify_bird

        kwargs = dict(
            image_path="",                  # dummy；preloaded_crop 路径不读取
            preloaded_crop=pil_image,
            use_yolo=False,                  # 已在 video_analyzer 层做过 YOLO
            use_gps=False,                   # 视频帧无 EXIF GPS
            use_ebird=False,                 # 同上
            top_k=top_k,
        )
        if self.name_format is not None:
            kwargs["name_format"] = self.name_format

        try:
            result_dict = identify_bird(**kwargs)
        except Exception as e:
            # 任何异常（模型加载失败、数据库缺失等）返回空列表；只在进程内首次
            # 失败时打印一次，避免逐帧刷屏（见模块顶部 _warned_identify_failure 说明）。
            # Return empty on any failure (model load, DB missing, etc.); only
            # print once per process to avoid per-frame log spam (see the
            # _warned_identify_failure note at the top of this module).
            global _warned_identify_failure
            if not _warned_identify_failure:
                _warned_identify_failure = True
                print(f"[BirdIDAdapter] identify_bird 调用失败(本进程仅提示一次) / identify_bird call failed (logged once per process): {e}")
            return []

        if not result_dict.get("success"):
            return []

        raw_results = result_dict.get("results") or []
        return [self._raw_to_species(r) for r in raw_results[:top_k]]

    @staticmethod
    def _raw_to_species(raw: dict) -> SpeciesResult:
        """
        把 identify_bird 返回的 dict 元素映射为 SpeciesResult

        identify_bird 返回字段：
            class_id, cn_name, en_name, scientific_name, confidence, ...
        注意：BirdID 的 confidence 是 softmax × 100（0-100 百分比），
              SpeciesResult 接口规范是 0-1，需要除 100 归一化。

        Map raw dict from identify_bird → SpeciesResult.
        Note: BirdID confidence is in 0-100 (softmax × 100); we normalize to 0-1
        to match the SpeciesResult interface contract.
        """
        raw_conf = float(raw.get("confidence") or 0.0)
        normalized_conf = raw_conf / 100.0 if raw_conf > 1.0 else raw_conf
        return SpeciesResult(
            name_zh=str(raw.get("cn_name") or ""),
            name_en=str(raw.get("en_name") or ""),
            name_sci=str(raw.get("scientific_name") or ""),
            confidence=normalized_conf,
            class_id=int(raw.get("class_id") if raw.get("class_id") is not None else -1),
        )
