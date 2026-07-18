"""
iRateBird 鸟种美学指数纯变换函数单测。
Unit tests for the pure transform functions of the iRateBird aesthetic index.
"""
import pytest

from birdid.iratebirds_aesthetic import (
    normalize_score,
    derive_default_score,
    is_dimorphic,
)


@pytest.mark.parametrize("raw,expected", [
    (1.0, 0.0),      # 下边界
    (10.0, 100.0),   # 上边界
    (5.5, 50.0),     # 中点
    (7.3, 70.0),     # round 到 1 位
])
def test_normalize_score_boundaries(raw, expected):
    assert normalize_score(raw) == expected


def test_normalize_score_none():
    assert normalize_score(None) is None


def test_derive_default_prefers_max_of_sexes():
    """二态种：雄 90 雌 40 → 取 max=90（该种最佳颜值）"""
    assert derive_default_score(65.0, 90.0, 40.0) == 90.0


def test_derive_default_single_sex_present():
    """只有一性有分 → 用那个"""
    assert derive_default_score(65.0, 88.0, None) == 88.0
    assert derive_default_score(65.0, None, 42.0) == 42.0


def test_derive_default_falls_back_to_species():
    """无雌雄分 → 回退物种级"""
    assert derive_default_score(65.0, None, None) == 65.0


def test_derive_default_all_none():
    assert derive_default_score(None, None, None) is None


def test_is_dimorphic():
    assert is_dimorphic(90.0, 40.0) == 1
    assert is_dimorphic(90.0, None) == 0
    assert is_dimorphic(None, None) == 0
