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


class TestSpeciesQuota(unittest.TestCase):
    def test_per_species_quota(self):
        """识鸟开启时配额按鸟种独立执行 / quotas apply per species."""
        # 80 张海鸥(高分) + 8 张塍鹬(低分):全局配额下塍鹬会全军覆没
        gulls = [make_photo(f"g{i:02d}", sharp=800 - i, topiq=6.0, species="gull")
                 for i in range(80)]
        godwits = [make_photo(f"w{i}", sharp=400 - i, topiq=4.5, species="godwit")
                   for i in range(8)]
        res = assign_ratings(gulls + godwits, quota3=25, quota2=25)
        gull3 = sum(1 for k, r in res.items() if k.startswith("g") and r.rating == 3)
        godwit3 = sum(1 for k, r in res.items() if k.startswith("w") and r.rating == 3)
        self.assertEqual(gull3, 20)    # ceil(80×25%)
        self.assertEqual(godwit3, 2)   # ceil(8×25%) —— 不再与海鸥同池竞争
        # 塍鹬进 3★ 的是种内 Q 最高者 / the godwit 3★s are its best shots
        self.assertEqual(res["w0"].rating, 3)
        self.assertEqual(res["w1"].rating, 3)

    def test_rare_species_keeps_best_shot(self):
        """小样本鸟种保底最好的 1 张(锐度兜底仍生效)/ singleton keeps its best."""
        photos = [make_photo(f"g{i:02d}", sharp=800 - i, topiq=6.0, species="gull")
                  for i in range(40)]
        photos.append(make_photo("rare", sharp=350, topiq=4.2, species="rare_bird"))
        photos.append(make_photo("rare_blurry", sharp=200, topiq=4.2, species="blurry_bird"))
        res = assign_ratings(photos, quota3=20, quota2=25)
        self.assertEqual(res["rare"].rating, 3)          # ceil(1×20%)=1 且过锐度兜底
        self.assertLess(res["rare_blurry"].rating, 3)    # 锐度<300 兜底挡住

    def test_no_species_falls_back_to_global(self):
        """species 全为 None(未开识鸟)→ 单组,等价全局配额。"""
        photos = [make_photo(f"p{i:03d}", sharp=800 - i * 5, topiq=6.5 - i * 0.02)
                  for i in range(100)]
        res = assign_ratings(photos, quota3=20, quota2=25)
        self.assertEqual(sum(1 for r in res.values() if r.rating == 3), 20)


class TestPendingRatingLog(unittest.TestCase):
    def test_log_photo_result_accepts_none_rating(self):
        """
        rating=None(星级待收尾分配)的日志路径不得抛异常。

        回归:T9 曾漏改 _log_photo_result_simple 的着色分级比较
        (rating >= 3),None 触发 TypeError 导致所有排序池照片被误标
        「处理异常已跳过」。此测试钉住该路径。

        Regression: the pending-rating (None) log path must not raise;
        a missed `rating >= 3` comparison once knocked out every pool photo.
        """
        from core.photo_processor import PhotoProcessor
        logs = []
        proc = object.__new__(PhotoProcessor)
        proc._log = lambda msg, level="default": logs.append((msg, level))
        from tools.i18n import get_i18n
        proc.i18n = get_i18n()
        # 全星级 + None 都不得抛异常 / every rating incl. None must not raise
        for rating in (3, 2, 1, 0, -1, None):
            PhotoProcessor._log_photo_result_simple(
                proc, 1, 10, "x.jpg", rating, "reason", 123.0, True, False, "BEST")
        self.assertEqual(len(logs), 6)
        self.assertIn("⏳", logs[-1][0])          # None → 待定符号
        self.assertEqual(logs[-1][1], "default")  # None → 普通着色


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
