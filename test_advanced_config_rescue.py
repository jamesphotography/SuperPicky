# -*- coding: utf-8 -*-
"""
advanced_config.py 无鸟补救扫描字段单测。

⚠️ 隔离要点：AdvancedConfig() 默认读写用户真实 advanced_config.json，
测试必须指向临时文件，避免污染用户配置（同 test_correction_consent_config.py）。

Rescue-scan config tests. Must use an isolated temp config file so tests
never mutate the user's real advanced_config.json.
"""
import os
import tempfile

from advanced_config import AdvancedConfig


def _isolated_config() -> AdvancedConfig:
    """构造指向临时文件的隔离配置实例 / isolated instance backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix="_advanced_config.json")
    os.close(fd)
    os.remove(path)
    return AdvancedConfig(config_file=path)


def test_rescue_defaults():
    cfg = _isolated_config()
    assert cfg.rescue_scan_enabled is True
    assert cfg.rescue_birdid_gate == 10


def test_rescue_setters_and_clamp():
    cfg = _isolated_config()
    cfg.set_rescue_scan_enabled(False)
    assert cfg.rescue_scan_enabled is False
    cfg.set_rescue_birdid_gate(150)
    assert cfg.rescue_birdid_gate == 100
    cfg.set_rescue_birdid_gate(-5)
    assert cfg.rescue_birdid_gate == 0
    cfg.set_rescue_birdid_gate(25.7)
    assert cfg.rescue_birdid_gate == 25
