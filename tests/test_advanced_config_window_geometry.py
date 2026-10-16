# -*- coding: utf-8 -*-
"""主窗口位置持久化（PR#105）的 advanced_config 访问器测试。

Tests for the main-window placement accessors added by PR#105.

PR 原实现未附带测试；这里锁定 get/set 的校验语义（缺键/类型异常返回 None、
None 清空、int 归一化），以及最大化标志的布尔归一化。全部使用临时配置文件，
不触碰真实用户配置（教训见 test_correction_consent_config 的隔离约定）。

The PR shipped without tests; these pin the get/set validation semantics
(missing keys / bad types return None, None clears, int normalization) and
the maximized-flag bool coercion. All tests use a temp config file and never
touch the real user config.
"""
from advanced_config import AdvancedConfig


def _make_config(tmp_path) -> AdvancedConfig:
    """构造使用临时文件的 AdvancedConfig / AdvancedConfig backed by a temp file."""
    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


def test_geometry_roundtrip_and_int_normalization(tmp_path):
    """合法几何字典可写读回，且浮点值被归一化为 int。"""
    cfg = _make_config(tmp_path)
    cfg.set_main_window_geometry({"x": 10.0, "y": 20, "width": 960.0, "height": 820})
    assert cfg.get_main_window_geometry() == {"x": 10, "y": 20, "width": 960, "height": 820}


def test_geometry_defaults_to_none_and_none_clears(tmp_path):
    """默认无保存值；set None 可清空已保存值。"""
    cfg = _make_config(tmp_path)
    assert cfg.get_main_window_geometry() is None

    cfg.set_main_window_geometry({"x": 0, "y": 0, "width": 900, "height": 800})
    assert cfg.get_main_window_geometry() is not None
    cfg.set_main_window_geometry(None)
    assert cfg.get_main_window_geometry() is None


def test_geometry_rejects_malformed_stored_values(tmp_path):
    """存储值缺键/类型错误时返回 None 而不是抛异常（配置文件可能被手改/损坏）。"""
    cfg = _make_config(tmp_path)

    cfg.config["main_window_geometry"] = {"x": 1, "y": 2}  # 缺 width/height
    assert cfg.get_main_window_geometry() is None

    cfg.config["main_window_geometry"] = {"x": "abc", "y": 2, "width": 3, "height": 4}
    assert cfg.get_main_window_geometry() is None

    cfg.config["main_window_geometry"] = "not-a-dict"
    assert cfg.get_main_window_geometry() is None


def test_maximized_flag_roundtrip_and_coercion(tmp_path):
    """最大化标志默认 False，set 时做布尔归一化。"""
    cfg = _make_config(tmp_path)
    assert cfg.main_window_maximized is False

    cfg.set_main_window_maximized(1)
    assert cfg.main_window_maximized is True
    cfg.set_main_window_maximized(False)
    assert cfg.main_window_maximized is False


def test_geometry_persists_across_reload(tmp_path):
    """几何与最大化状态写盘后重新加载仍在（持久化闭环）。"""
    path = tmp_path / "advanced_config.json"
    cfg = AdvancedConfig(config_file=str(path))
    cfg.set_main_window_geometry({"x": 5, "y": 6, "width": 1000, "height": 900})
    cfg.set_main_window_maximized(True)
    assert cfg.save()

    reloaded = AdvancedConfig(config_file=str(path))
    assert reloaded.get_main_window_geometry() == {"x": 5, "y": 6, "width": 1000, "height": 900}
    assert reloaded.main_window_maximized is True
