#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/rating_quota.py(评星 V2:批内相对 + 配额)单元测试。

钉住:硬门槛链、配额切分、3★ 锐度兜底、眼睛可见度封顶、连拍组 3★ 上限、
技能等级配额映射、空批次/全淘汰批次的边界行为。

Unit tests for the batch-relative quota rating core: hard-gate chain, quota
split, the 3-star sharpness floor, the eye-visibility cap, the per-burst
3-star cap, skill-quota mapping, and empty/degenerate batches.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.rating_quota import (
    PhotoMetricsV2, assign_ratings, gate_photo, get_quota3_for_skill,
    DEFAULT_QUOTA3, SKILL_QUOTA3,
)


def make_photo(key, sharp=500.0, topiq=5.0, **kw):
    """构造通过全部硬门槛的默认照片 / a photo passing every hard gate."""
    defaults = dict(detected=True, confidence=0.9, norm_sharpness=sharp,
                    topiq=topiq, best_eye=0.9, beak_vis=0.9)
    defaults.update(kw)
    return PhotoMetricsV2(key=key, **defaults)


class TestHardGates(unittest.TestCase):
    def test_gate_chain(self):
        """硬门槛链与现行语义一致 / gate chain matches current semantics."""
        cases = [
            (make_photo("a", detected=False), -1),
            (make_photo("b", confidence=0.3), 0),
            (make_photo("c", best_eye=0.1, beak_vis=0.1), 1),   # 角度不佳
            (make_photo("d", sharp=50.0), 0),                    # 真糊
            (make_photo("e", topiq=3.0), 0),                     # 美学兜底
        ]
        for photo, expect in cases:
            r = gate_photo(photo)
            self.assertIsNotNone(r, photo.key)
            self.assertEqual(r.rating, expect, photo.key)

    def test_pass_returns_none(self):
        self.assertIsNone(gate_photo(make_photo("ok")))


class TestQuotaAssignment(unittest.TestCase):
    def _batch(self, n=100):
        """锐度/美学线性递减的 n 张照片 / n photos with descending quality."""
        return [make_photo(f"p{i:03d}", sharp=800 - i * 5, topiq=6.5 - i * 0.02)
                for i in range(n)]

    def test_quota_split(self):
        """3★=前20%、2★=接下来25%、其余1★ / 20/25/rest split."""
        res = assign_ratings(self._batch(100), quota3=20, quota2=25)
        stars = [res[f"p{i:03d}"].rating for i in range(100)]
        self.assertEqual(stars[:20], [3] * 20)
        self.assertEqual(stars[20:45], [2] * 25)
        self.assertEqual(stars[45:], [1] * 55)

    def test_sharp_floor_blocks_3star(self):
        """整批全烂时,3★ 配额不凑数(锐度兜底) / floor prevents fake 3★."""
        photos = [make_photo(f"p{i}", sharp=250 - i, topiq=5.0) for i in range(10)]
        res = assign_ratings(photos, quota3=20, quota2=25)
        self.assertTrue(all(r.rating < 3 for r in res.values()))

    def test_eye_visibility_caps_at_2(self):
        """眼睛可见度<0.5 → 封顶 2★ / low eye visibility caps stars at 2."""
        photos = self._batch(10)
        photos[0] = make_photo("p000", sharp=800, topiq=6.5, best_eye=0.4)
        res = assign_ratings(photos, quota3=50, quota2=30)
        self.assertEqual(res["p000"].rating, 2)
        self.assertEqual(res["p000"].reason_key, "rating_v2.eye_capped")

    def test_burst_cap(self):
        """同连拍组 3★ 封顶 2 张,溢出者降 2★ / per-burst 3-star cap."""
        photos = [make_photo(f"p{i}", sharp=800 - i, topiq=6.0, burst_id=7)
                  for i in range(5)]
        res = assign_ratings(photos, quota3=100, quota2=0, burst_cap3=2)
        stars = sorted((r.rating for r in res.values()), reverse=True)
        self.assertEqual(stars, [3, 3, 2, 2, 2])
        capped = [k for k, r in res.items() if r.reason_key == "rating_v2.burst_capped"]
        self.assertEqual(len(capped), 3)

    def test_gated_photos_not_in_pool(self):
        """被硬门槛淘汰者不占配额分母 / gated photos don't consume quota."""
        photos = self._batch(10) + [make_photo(f"bad{i}", detected=False) for i in range(90)]
        res = assign_ratings(photos, quota3=20, quota2=25)
        three = [k for k, r in res.items() if r.rating == 3]
        self.assertEqual(len(three), 2)  # 20% × 10(池内),而不是 ×100

    def test_empty_and_all_gated(self):
        self.assertEqual(assign_ratings([]), {})
        res = assign_ratings([make_photo("x", detected=False)])
        self.assertEqual(res["x"].rating, -1)

    def test_q_monotonic_with_quality(self):
        """Q 分随品质单调 / Q is monotonic with quality."""
        res = assign_ratings(self._batch(50), quota3=20, quota2=25)
        qs = [res[f"p{i:03d}"].q_score for i in range(50)]
        self.assertEqual(qs, sorted(qs, reverse=True))


class TestSkillQuota(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(get_quota3_for_skill("beginner"), 25.0)
        self.assertEqual(get_quota3_for_skill("intermediate"), 20.0)
        self.assertEqual(get_quota3_for_skill("master"), 10.0)
        self.assertEqual(get_quota3_for_skill("unknown"), DEFAULT_QUOTA3)

    def test_custom_reads_config(self):
        class Cfg:
            custom_quota3 = 33.0
        self.assertEqual(get_quota3_for_skill("custom", Cfg()), 33.0)

    def test_presets_complete(self):
        self.assertEqual(set(SKILL_QUOTA3), {"beginner", "intermediate", "master"})


if __name__ == "__main__":
    unittest.main()
