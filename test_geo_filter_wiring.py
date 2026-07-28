"""
identify_bird 分层候选接线测试 / Wiring tests for layered candidates in identify_bird.

用假的过滤器与假的 predict 验证「逐层放宽、命中即停」的控制流，不加载真实模型。

Verifies the widen-until-hit control flow with a fake filter and fake predict,
without loading the real model.
"""
import pytest

from birdid.geo_filter import (
    TIER_CELL_ALL,
    TIER_CELL_STRONG,
    TIER_COUNTRY,
    TIER_NONE,
)


class FakeFilter:
    """按预设脚本产出候选层 / Yields a scripted sequence of tiers."""

    def __init__(self, tiers):
        self._tiers = tiers

    def is_available(self) -> bool:
        return True

    def iter_candidates(self, lat, lon, country_code=None):
        return iter(self._tiers)


def test_stops_at_first_tier_with_results(monkeypatch):
    """L1 就有结果 → 不应继续放宽"""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 1, "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([
            ({1, 2}, TIER_CELL_STRONG),
            ({1, 2, 3}, TIER_CELL_ALL),
            (None, TIER_NONE),
        ]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code="AU",
    )
    assert tier == TIER_CELL_STRONG
    assert used == 2
    assert len(calls) == 1, "命中后不应再调用更宽的层"


def test_widens_when_tier_empty(monkeypatch):
    """L1 无结果 → 放宽到 L2"""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        if species_class_ids == {1, 2}:
            return []
        return [{"class_id": 3, "confidence": 80.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([
            ({1, 2}, TIER_CELL_STRONG),
            ({1, 2, 3}, TIER_CELL_ALL),
            (None, TIER_NONE),
        ]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code="AU",
    )
    assert tier == TIER_CELL_ALL
    assert results and results[0]["class_id"] == 3


def test_falls_through_to_unfiltered(monkeypatch):
    """所有层都无结果 → 最终无过滤"""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        if species_class_ids is None:
            return [{"class_id": 9, "confidence": 50.0}]
        return []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(
        bi, "get_geo_filter",
        lambda: FakeFilter([
            ({1}, TIER_CELL_STRONG),
            ({5}, TIER_COUNTRY),
            (None, TIER_NONE),
        ]),
    )
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=None, lon=None, country_code="AU",
        is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    assert tier == TIER_NONE
    assert used is None
    assert results[0]["class_id"] == 9


def test_no_filter_available(monkeypatch):
    """过滤器不可用 → 直接无过滤识别一次"""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 7, "confidence": 60.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: None)
    results, tier, used = bi._identify_with_tiers(
        object(), top_k=1, lat=-33.8, lon=151.2, country_code=None,
        is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    assert tier == TIER_NONE
    assert calls == [None]


def test_describe_tier_covers_every_tier():
    """每一层都有对应文案，且不回落到英文键名"""
    from birdid.geo_filter import (
        TIER_NEIGHBORHOOD, describe_tier,
    )

    for tier in (TIER_CELL_STRONG, TIER_CELL_ALL, TIER_NEIGHBORHOOD,
                 TIER_COUNTRY, TIER_NONE):
        text = describe_tier({"tier": tier, "species_count": 42, "country_code": "AU"})
        assert text and not text.startswith("birdid."), f"{tier} 缺文案: {text}"


def test_describe_tier_handles_none():
    """geo_info 为 None 时按未过滤处理，不抛异常"""
    from birdid.geo_filter import describe_tier

    text = describe_tier(None)
    assert text and not text.startswith("birdid.")
