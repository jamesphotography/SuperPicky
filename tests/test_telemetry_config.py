#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测开关的 advanced_config 契约测试。

注入 config_file 以隔离本机真实配置——测试写入用户真实
advanced_config.json 会静默改掉本机设置（见 CLAUDE.md 与既往教训）。

Contract tests for the telemetry toggle. config_file is injected so the
test never touches the user's real advanced_config.json.
"""
from pathlib import Path

import pytest

from advanced_config import AdvancedConfig


@pytest.fixture
def cfg(tmp_path: Path) -> AdvancedConfig:
    """返回一个写入临时目录的独立配置实例。/ Isolated config instance."""
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


def test_telemetry_defaults_to_enabled(cfg: AdvancedConfig) -> None:
    """默认开启（opt-out 策略）。/ Opt-out: enabled by default."""
    assert cfg.telemetry_enabled is True


def test_telemetry_can_be_disabled(cfg: AdvancedConfig) -> None:
    """可关闭并持久化。/ Can be turned off and persisted."""
    cfg.set_telemetry_enabled(False)
    assert cfg.telemetry_enabled is False
    cfg.save()

    reloaded = AdvancedConfig(config_file=cfg.config_file)
    assert reloaded.telemetry_enabled is False


def test_telemetry_setter_coerces_to_bool(cfg: AdvancedConfig) -> None:
    """setter 强制转 bool，避免 Qt 的 int 状态值直接落库。/ Coerce to bool."""
    cfg.set_telemetry_enabled(0)      # Qt.Unchecked
    assert cfg.telemetry_enabled is False
    cfg.set_telemetry_enabled(2)      # Qt.Checked
    assert cfg.telemetry_enabled is True
