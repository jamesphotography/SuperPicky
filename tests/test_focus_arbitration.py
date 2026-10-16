"""
issue #107 对焦锐度仲裁纯函数单测。
Unit tests for the focus sharpness arbitration pure function (issue #107).
"""
import pytest

from core.focus_point_detector import arbitrate_focus_weights


def test_worst_upgrades_when_sharpness_meets_threshold():
    """框外0.5档、锐度683≥阈值400 → 升(0.9,1.0)并标记仲裁（issue#107本案）"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 683.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_bad_in_bbox_upgrades_when_sharpness_meets_threshold():
    """框内0.8档同样可仲裁升级"""
    weights, arbitrated = arbitrate_focus_weights((0.8, 0.9), 500.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_no_focus_data_penalty_upgrades_when_sharp():
    """读不到对焦数据的0.7档也属元数据惩罚，锐度达标应升级"""
    weights, arbitrated = arbitrate_focus_weights((0.7, 0.9), 450.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_worst_maintained_when_sharpness_below_threshold():
    """A7V真跟丢场景：鸟头真糊 → 维持WORST原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 250.0, 400.0)
    assert weights == (0.5, 0.8)
    assert arbitrated is False


def test_boundary_exactly_at_threshold_upgrades():
    """恰等于阈值 → 达标（≥语义）"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 400.0, 400.0)
    assert weights == (0.9, 1.0)
    assert arbitrated is True


def test_best_and_good_untouched():
    """BEST(1.1)/GOOD(0.9) 不触碰，即使锐度极高"""
    for w in [(1.1, 1.0), (0.9, 1.0), (1.0, 1.0)]:
        weights, arbitrated = arbitrate_focus_weights(w, 9999.0, 400.0)
        assert weights == w
        assert arbitrated is False


@pytest.mark.parametrize("bad_sharpness", [None, 0.0, -5.0])
def test_missing_sharpness_maintains_verdict(bad_sharpness):
    """关键点失败/无锐度数据 → 不仲裁维持原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), bad_sharpness, 400.0)
    assert weights == (0.5, 0.8)
    assert arbitrated is False


@pytest.mark.parametrize("bad_threshold", [None, 0.0, -1.0])
def test_invalid_threshold_maintains_verdict(bad_threshold):
    """阈值异常（未配置/非法）→ 保守维持原判"""
    weights, arbitrated = arbitrate_focus_weights((0.5, 0.8), 683.0, bad_threshold)
    assert weights == (0.5, 0.8)
    assert arbitrated is False


import json
from pathlib import Path


@pytest.mark.parametrize("locale_file", ["locales/zh_CN.json", "locales/en_US.json"])
def test_focus_arbitrated_log_key_exists_and_formats(locale_file):
    """仲裁日志键在中英locale都存在，且能用orig/sharp/thr格式化"""
    data = json.loads(Path(locale_file).read_text(encoding="utf-8"))
    template = data["logs"]["focus_arbitrated"]
    rendered = template.format(orig=0.5, sharp=683.0, thr=400)
    assert "0.5" in rendered
    assert "683" in rendered
    assert "400" in rendered
