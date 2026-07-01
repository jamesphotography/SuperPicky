# -*- coding: utf-8 -*-
"""advanced_config.py 纠错提交首次同意标志单测。"""
from advanced_config import AdvancedConfig


def test_default_consent_false():
    cfg = AdvancedConfig()
    assert cfg.correction_consent_shown is False


def test_set_consent_true():
    cfg = AdvancedConfig()
    cfg.set_correction_consent_shown(True)
    assert cfg.correction_consent_shown is True
