# -*- coding: utf-8 -*-
"""
advanced_config.py 纠错提交首次同意标志单测。

⚠️ 隔离要点：AdvancedConfig() 默认读写用户真实 advanced_config.json；
set_correction_consent_shown 会 self.save() 持久化。若直接用真实实例，
test_set_consent_true 会把 True 写进真实配置，导致 test_default_consent_false
复跑失败并污染用户「首次说明」状态。故本测试统一指向临时文件隔离。

Consent-flag tests for advanced_config.py. AdvancedConfig() defaults to the
user's real config file and the setter persists via save(); tests MUST use an
isolated temp file so they never mutate the user's real consent state.
"""
import os
import tempfile

from advanced_config import AdvancedConfig


def _isolated_config() -> AdvancedConfig:
    """构造指向临时文件的隔离配置实例（文件不存在 → load() 走默认值）。"""
    fd, path = tempfile.mkstemp(suffix="_advanced_config.json")
    os.close(fd)
    os.remove(path)  # 删掉空文件，让 load() 落到 DEFAULT_CONFIG
    return AdvancedConfig(config_file=path)


def test_default_consent_false():
    cfg = _isolated_config()
    assert cfg.correction_consent_shown is False


def test_set_consent_true():
    cfg = _isolated_config()
    cfg.set_correction_consent_shown(True)
    assert cfg.correction_consent_shown is True
