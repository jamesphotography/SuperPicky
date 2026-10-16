# -*- coding: utf-8 -*-
"""
identify_bird 区域候选接线测试 / Wiring tests for region candidates in identify_bird.

用假的过滤器与假的 predict 验证「逐层放宽、命中即停」，以及 GPS 定位与手选
分开传给过滤器，不加载真实模型。

Verifies widen-until-hit with a fake filter and predict, and that GPS location
and manual selection reach the filter separately, without loading the model.
"""
from birdid.geo_filter import TIER_COUNTRY, TIER_NONE, TIER_SUBNATIONAL


class FakeFilter:
    """按预设脚本产出候选层并记录调用参数 / Scripted tiers; records call arguments."""

    def __init__(self, tiers):
        self._tiers = tiers
        self.calls = []

    def is_available(self) -> bool:
        return True

    def iter_candidates(self, gps_country, gps_subnational, manual_country, manual_subnational):
        self.calls.append((gps_country, gps_subnational, manual_country, manual_subnational))
        return iter(self._tiers)


def run_tiers(bi, **overrides):
    """用默认参数调用 _identify_with_tiers / Call _identify_with_tiers with defaults."""
    kwargs = dict(
        top_k=1, gps_country=None, gps_subnational=None, manual_country=None,
        manual_subnational=None, is_yolo_cropped=True, name_format=None, photo_country_code=None,
    )
    kwargs.update(overrides)
    return bi._identify_with_tiers(object(), **kwargs)


def test_stops_at_first_tier_with_results(monkeypatch):
    """省州层就有结果 → 不再放宽 / Stop at the first tier with results."""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 1, "confidence": 90.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([
        ({1, 2}, TIER_SUBNATIONAL, "AU-NSW"),
        ({1, 2, 3}, TIER_COUNTRY, "AU"),
        (None, TIER_NONE, None),
    ]))
    results, tier, used, region = run_tiers(bi, gps_country="AU", gps_subnational="AU-NSW")
    assert (tier, used, region) == (TIER_SUBNATIONAL, 2, "AU-NSW")
    assert len(calls) == 1


def test_widens_when_tier_empty(monkeypatch):
    """省州层无结果 → 放宽到国家 / Widen to the country when the subnational tier is empty."""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        return [{"class_id": 3, "confidence": 70.0}] if species_class_ids and 3 in species_class_ids else []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([
        ({1, 2}, TIER_SUBNATIONAL, "AU-NSW"),
        ({1, 2, 3}, TIER_COUNTRY, "AU"),
        (None, TIER_NONE, None),
    ]))
    results, tier, used, region = run_tiers(bi)
    assert (tier, used, region) == (TIER_COUNTRY, 3, "AU")
    assert results[0]["class_id"] == 3


def test_falls_through_to_unfiltered(monkeypatch):
    """所有层都无结果 → 不过滤 / Fall through to unfiltered."""
    from birdid import bird_identifier as bi

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        return [{"class_id": 9, "confidence": 50.0}] if species_class_ids is None else []

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: FakeFilter([({5}, TIER_COUNTRY, "AU"), (None, TIER_NONE, None)]))
    results, tier, used, region = run_tiers(bi, manual_country="AU")
    assert (tier, used, region) == (TIER_NONE, None, None)
    assert results[0]["class_id"] == 9


def test_no_filter_available(monkeypatch):
    """过滤器不可用 → 直接无过滤识别一次 / No filter: identify once unfiltered."""
    from birdid import bird_identifier as bi

    calls = []

    def fake_predict(image, top_k=5, species_class_ids=None, **kw):
        calls.append(species_class_ids)
        return [{"class_id": 7, "confidence": 60.0}]

    monkeypatch.setattr(bi, "predict_bird", fake_predict)
    monkeypatch.setattr(bi, "get_geo_filter", lambda: None)
    _, tier, _, region = run_tiers(bi, gps_country="AU")
    assert (tier, region) == (TIER_NONE, None)
    assert calls == [None]


def _patch_identify(monkeypatch, bi, gps, located):
    """
    替换 identify_bird 的外部依赖 / Stub identify_bird's external dependencies.

    参数 / Parameters:
        gps (tuple): extract_gps_from_exif 的返回 / Return of extract_gps_from_exif.
        located (tuple): _locate_gps 的返回 / Return of _locate_gps.

    返回 / Returns:
        FakeFilter: 记录了调用参数的假过滤器 / The recording fake filter.
    """
    fake = FakeFilter([({1}, TIER_SUBNATIONAL, "AU-NSW"), (None, TIER_NONE, None)])
    monkeypatch.setattr(bi, "get_geo_filter", lambda: fake)
    monkeypatch.setattr(bi, "predict_bird", lambda image, **kw: [{"class_id": 1, "confidence": 80.0}])
    monkeypatch.setattr(bi, "extract_gps_from_exif", lambda path: gps)
    monkeypatch.setattr(bi, "_locate_gps", lambda lat, lon: located)
    return fake


def test_identify_passes_manual_region_without_gps(monkeypatch):
    """无 GPS：手选国家与省州原样传给过滤器（spec §2.5 回归）/ Manual selection reaches the filter."""
    from birdid import bird_identifier as bi

    fake = _patch_identify(monkeypatch, bi, gps=(None, None, "no gps"), located=(None, None))
    result = bi.identify_bird("x.jpg", country_code="AU", region_code="AU-NSW", preloaded_crop=object())
    assert fake.calls == [(None, None, "AU", "AU-NSW")]
    assert result["geo_info"] == {"enabled": True, "tier": TIER_SUBNATIONAL, "species_count": 1, "region_code": "AU-NSW"}


def test_identify_passes_gps_location(monkeypatch):
    """有 GPS：定位结果写入 gps_info 并传给过滤器 / GPS location is recorded and passed on."""
    from birdid import bird_identifier as bi

    fake = _patch_identify(monkeypatch, bi, gps=(-33.87, 151.21, "ok"), located=("AU", "AU-NSW"))
    result = bi.identify_bird("x.jpg", country_code="CN", region_code="CN-11", preloaded_crop=object())
    assert fake.calls == [("AU", "AU-NSW", "CN", "CN-11")]
    assert result["gps_info"]["country_code"] == "AU"
    assert result["gps_info"]["subnational_code"] == "AU-NSW"


def test_reverse_geocoder_is_gone():
    """不再引用 reverse_geocoder / reverse_geocoder is no longer referenced."""
    import inspect

    from birdid import bird_identifier as bi

    source = inspect.getsource(bi)
    assert "import reverse_geocoder" not in source and "_RG_" not in source
    assert not hasattr(bi, "_resolve_country_code_from_gps")
