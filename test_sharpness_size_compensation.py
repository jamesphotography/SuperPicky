#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
锐度尺寸补偿测试。

验证 KeypointDetector._size_compensation 与 _calculate_sharpness 的行为:
Tenengrad 是梯度密度,ROI 越小密度越高(实测 raw ∝ s^-0.641),远拍小鸟锐度虚高。
补偿按对数曲线扣回,只减不加。

Tests for the sharpness size compensation: small ROIs inflate the Tenengrad
density, so the score is reduced along a log curve — downward only.

设计文档 / design doc: docs/plans/2026-07-24-sharpness-size-compensation.md
"""
import math
import unittest

import cv2
import numpy as np

from core.keypoint_detector import KeypointDetector


class TestSizeCompensationCurve(unittest.TestCase):
    """纯曲线行为 / the compensation curve itself."""

    def test_roi_at_or_above_ref_is_untouched(self):
        """ROI ≥ 参考尺寸时分数原样返回——这是「只减不加」的保证。"""
        ref = int(KeypointDetector.SIZE_COMP_REF)
        for side in (ref, ref + 1, ref * 2, 5000):
            self.assertEqual(
                KeypointDetector._size_compensation(600.0, side), 600.0,
                f"ROI={side}px 不应被补偿 / must not be penalised")

    def test_small_roi_is_penalised_by_log_curve(self):
        """ROI < 参考尺寸时按 K×ln(REF/ROI) 扣分。"""
        k = KeypointDetector.SIZE_COMP_K
        ref = KeypointDetector.SIZE_COMP_REF
        for side in (150, 100, 64):
            expected = 600.0 - k * math.log(ref / side)
            self.assertAlmostEqual(
                KeypointDetector._size_compensation(600.0, side), expected, places=6)

    def test_penalty_grows_as_roi_shrinks(self):
        """ROI 越小扣得越多（严格单调）。"""
        scores = [KeypointDetector._size_compensation(700.0, s)
                  for s in (280, 200, 150, 100, 60)]
        for a, b in zip(scores, scores[1:]):
            self.assertGreater(a, b, "补偿必须随 ROI 减小而单调增强")

    def test_result_is_clamped_at_zero(self):
        """极小 ROI 会扣成负数，必须 clamp 到 0。"""
        self.assertEqual(KeypointDetector._size_compensation(120.0, 20), 0.0)
        self.assertEqual(KeypointDetector._size_compensation(0.0, 30), 0.0)

    def test_continuous_at_reference_boundary(self):
        """参考尺寸处必须连续，不能有跳变（否则 ROI 差 1px 星级就翻）。"""
        ref = int(KeypointDetector.SIZE_COMP_REF)
        just_below = KeypointDetector._size_compensation(600.0, ref - 1)
        at_ref = KeypointDetector._size_compensation(600.0, ref)
        self.assertLess(abs(at_ref - just_below), 1.0,
                        "REF 边界两侧的分数差必须小于 1 分")

    def test_zero_k_disables_compensation(self):
        """K=0 即完全关闭，等价旧公式（单参数回滚）。"""
        original = KeypointDetector.SIZE_COMP_K
        try:
            KeypointDetector.SIZE_COMP_K = 0.0
            self.assertEqual(KeypointDetector._size_compensation(600.0, 30), 600.0)
        finally:
            KeypointDetector.SIZE_COMP_K = original

    def test_invalid_roi_side_is_safe(self):
        """ROI 尺寸为 0 或负数时不应崩溃（除零/log 定义域）。"""
        self.assertEqual(KeypointDetector._size_compensation(500.0, 0), 500.0)
        self.assertEqual(KeypointDetector._size_compensation(500.0, -5), 500.0)


class TestCalculateSharpnessIntegration(unittest.TestCase):
    """接入 _calculate_sharpness 后的端到端行为。"""

    @staticmethod
    def _checker(side: int, cell: int = 4, lo: int = 110, hi: int = 145) -> np.ndarray:
        """
        生成低对比度棋盘格，提供稳定可控的梯度。

        对比度刻意压到 35 灰阶：满对比度(0/255)的 raw Tenengrad 会超过
        MAX_VAL=154016 而被封顶到 1000 分，那样测不出补偿是否生效。
        Low contrast on purpose — a full-contrast checker saturates the
        log normalisation at 1000 and would mask the compensation.
        """
        img = np.full((side, side), lo, dtype=np.uint8)
        for y in range(0, side, cell):
            for x in range(0, side, cell):
                if ((y // cell) + (x // cell)) % 2 == 0:
                    img[y:y + cell, x:x + cell] = hi
        return img

    def setUp(self):
        # 不触发模型加载，只用到纯函数部分
        self.det = KeypointDetector.__new__(KeypointDetector)

    def test_no_resampling_large_roi_matches_manual_tenengrad(self):
        """大 ROI 不被补偿也不被重采样，结果应等于手工算的 Tenengrad 归一化值。"""
        side = 400          # > SIZE_COMP_REF
        img = self._checker(side)
        mask = np.full((side, side), 255, np.uint8)

        gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
        raw = float(cv2.mean(cv2.add(cv2.multiply(gx, gx), cv2.multiply(gy, gy)),
                             mask=mask)[0])
        self.assertLess(raw, 154016.0, "测试图案不得触发 log 归一化封顶，否则测不出补偿")
        expected = (math.log(raw) - math.log(100.0)) / \
                   (math.log(154016.0) - math.log(100.0)) * 1000.0

        self.assertAlmostEqual(self.det._calculate_sharpness(img, mask), expected, places=4)

    def test_small_roi_scores_lower_than_same_pattern_large(self):
        """同样的图案，小 ROI 的最终分数必须低于大 ROI——这就是本方案的目的。"""
        big = self._checker(400)
        small = self._checker(80)
        s_big = self.det._calculate_sharpness(big, np.full((400, 400), 255, np.uint8))
        s_small = self.det._calculate_sharpness(small, np.full((80, 80), 255, np.uint8))
        self.assertGreater(s_big, s_small,
                           "补偿后小 ROI 不应再高于同图案的大 ROI")

    def test_empty_mask_returns_zero(self):
        """空掩码仍返回 0，补偿不得引入异常路径。"""
        img = self._checker(200)
        self.assertEqual(self.det._calculate_sharpness(img, np.zeros((200, 200), np.uint8)), 0.0)

    def test_none_inputs_return_zero(self):
        self.assertEqual(self.det._calculate_sharpness(None, None), 0.0)


class TestNormRoiSizeRemoved(unittest.TestCase):
    """down128 已验证否决，必须彻底移除，避免遗留常量误导后来者。"""

    def test_no_leftover_norm_roi_size(self):
        self.assertFalse(hasattr(KeypointDetector, "NORM_ROI_SIZE"),
                         "NORM_ROI_SIZE(down128) 已否决，不应再存在")

    def test_thresholds_rolled_back(self):
        """阈值已从 down128 的阶段A临时值回滚——补偿只动小 ROI，无需重标。"""
        from core.rating_quota import MIN_SHARPNESS, QUOTA3_SHARP_FLOOR
        from core.skill_presets import SKILL_PRESETS

        self.assertEqual(MIN_SHARPNESS, 100.0)
        self.assertEqual(QUOTA3_SHARP_FLOOR, 300.0)
        self.assertEqual(
            [SKILL_PRESETS[k]["sharpness"] for k in ("beginner", "intermediate", "master")],
            [300, 380, 520])
        # 3★ 兜底应与初级档同量级（都是「刚够 3★」的下限）
        self.assertEqual(QUOTA3_SHARP_FLOOR, SKILL_PRESETS["beginner"]["sharpness"])


class TestCustomSharpnessClamp(unittest.TestCase):
    """
    从 test_sharpness_normalization.py 迁移而来（该文件随 down128 一并删除）。

    这条与锐度公式无关：set_custom_sharpness 的下限 200 与 set_min_sharpness
    及 UI 滑块(100-600)不一致，会在拖到 100-199 时静默截断。属独立的真 bug 修复，
    不随 down128 回滚。
    """

    def test_custom_sharpness_clamp_aligned(self):
        import os
        import tempfile
        from advanced_config import AdvancedConfig

        with tempfile.TemporaryDirectory() as d:
            cfg = AdvancedConfig(config_file=os.path.join(d, "c.json"))
            cfg.set_custom_sharpness(150)
            self.assertEqual(cfg.custom_sharpness, 150, "150 不应再被截断到 200")
            cfg.set_custom_sharpness(50)
            self.assertEqual(cfg.custom_sharpness, 100, "低于下限应夹到 100")


if __name__ == "__main__":
    unittest.main(verbosity=2)
