# -*- coding: utf-8 -*-
"""
评星 V2「鸟种保底」:每个已确认的鸟种在 2★ 及以上至少留一张。

用户 2026-11-07:多数人删 0★/1★、留 2★/3★,所以只要保证每个鸟种在 2★/3★ 里
至少有一张,删片时就不会把某种鸟整个删掉。2026-10 实拍 119 个(文件夹×鸟种)
里有 5 个最高只到 0★/1★:
  - 全部照片没过硬门槛(有鸟置信度低于用户设置 70%、美学 < 3.5);
  - 低置信度候选和确认照片一起排名,把唯一确认的白鹭挤到第 4 名 1★。

Species floor: every confirmed species keeps at least one photo at 2★+,
so deleting 0-1★ never removes a species. Real batches had 5 of 119
(folder × species) pairs topping out at 0-1★.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rating_quota import PhotoMetricsV2, apply_species_floor, assign_ratings  # noqa: E402
from core.rating_caption import CaptionFacts, build_caption, outcome_from_reason_key  # noqa: E402


def _p(key, species=None, confirmed=True, sharp=500.0, topiq=5.0, **kw):
    """默认过全部硬门槛的照片 / a photo passing every hard gate by default."""
    d = dict(detected=True, confidence=0.9, norm_sharpness=sharp, topiq=topiq,
             best_eye=0.9, beak_vis=0.9, species=species, species_confirmed=confirmed)
    d.update(kw)
    return PhotoMetricsV2(key=key, **d)


def _stars(res):
    return {k: r.rating for k, r in res.items()}


def test_all_gated_species_gets_best_photo_raised_to_two():
    """某种全部被硬门槛挡掉(0★):最锐的那张提到 2★,其余不动。"""
    photos = [
        _p("a1", "Song Lark", confidence=0.55, sharp=317),   # 有鸟置信度不够 / low detection conf
        _p("a2", "Song Lark", confidence=0.60, sharp=410),
        _p("x", "Other", sharp=600),
    ]
    res = assign_ratings(photos, quota3=15, quota2=20, min_confidence=0.7)
    assert res["a2"].rating == 2 and res["a2"].reason_key == "rating_v2.species_floor"
    assert res["a2"].reason_args["prev_rating"] == 0
    assert res["a1"].rating == 0


def test_low_confidence_candidates_cannot_bury_confirmed_species():
    """
    白鹭场景:3 张低置信度候选与唯一确认的白鹭同组排名,确认那张排末位 1★,
    而候选不写鸟种——保底把确认的那张提到 2★,候选星级不变。
    """
    photos = [_p("best", "Little Egret", confirmed=False, sharp=900),
              _p("c2", "Little Egret", confirmed=False, sharp=800),
              _p("c3", "Little Egret", confirmed=False, sharp=700),
              _p("conf", "Little Egret", confirmed=True, sharp=150)]
    photos += [_p(f"o{i}", "Black Swan", sharp=300 + i * 10) for i in range(20)]
    before = assign_ratings(photos, quota3=15, quota2=20, species_floor=False)
    assert before["conf"].rating == 1
    res = assign_ratings(photos, quota3=15, quota2=20)
    assert res["conf"].rating == 2
    assert res["conf"].reason_key == "rating_v2.species_floor"
    assert {k: res[k].rating for k in ("best", "c2", "c3")} == \
        {k: before[k].rating for k in ("best", "c2", "c3")}


def test_species_already_at_two_is_untouched():
    photos = [_p("a", "Pipit", sharp=250, best_eye=0.1)]      # 锐度兜底/眼睛封顶 → 2★
    res = assign_ratings(photos)
    assert res["a"].rating == 2 and res["a"].reason_key != "rating_v2.species_floor"


def test_unconfirmed_or_unknown_species_never_raised():
    photos = [_p("u", "Maybe Bird", confirmed=False, confidence=0.5),
              _p("n", None, confidence=0.5)]
    res = assign_ratings(photos, min_confidence=0.7)
    assert res["u"].rating == 0 and res["n"].rating == 0


def test_no_bird_never_raised():
    photos = [_p("nb", "Egret", detected=False)]
    res = assign_ratings(photos)
    assert res["nb"].rating == -1


def test_floor_never_lowers_or_touches_three_stars():
    """保底只升不降,不改变任何 3★ / raise-only; 3★ untouched."""
    photos = [_p(f"s{i}", "Swan", sharp=300 + i * 30) for i in range(10)]
    a = _stars(assign_ratings(photos, species_floor=False))
    b = _stars(assign_ratings(photos))
    assert a == b


def test_best_choice_prefers_passed_gates_then_sharpness():
    """1★(角度不佳)优先于 0★;同星级比锐度 / higher stars first, then sharpness."""
    photos = [_p("angle", "Wren", best_eye=0.1, beak_vis=0.1, sharp=200),   # 1★ 角度不佳
              _p("blur", "Wren", sharp=90)]                                  # 0★ 锐度门槛
    res = assign_ratings(photos)
    assert res["angle"].rating == 2 and res["blur"].rating == 0


def test_apply_species_floor_returns_raised_keys():
    photos = [_p("a", "Finch", topiq=3.0), _p("b", "Finch", topiq=3.2)]
    res = assign_ratings(photos, species_floor=False)
    assert apply_species_floor(photos, res) == ["b"]


def test_caption_explains_species_floor():
    from tools.i18n import get_i18n
    t = get_i18n().t
    f = CaptionFacts(rating=2, outcome=outcome_from_reason_key("rating_v2.species_floor"),
                     species_name="白鹭", floor_prev_rating=1, norm_sharpness=334.0)
    text = build_caption(f, t)
    lines = text.split("\n")
    assert "2★" in lines[0]
    assert "白鹭" in lines[1] and "1★" in lines[1]
