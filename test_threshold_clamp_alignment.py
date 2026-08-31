# -*- coding: utf-8 -*-
"""
阈值 clamp 范围与 UI 滑块范围的对齐回归测试。

背景 / Background:
    advanced_config 的 setter clamp 一旦与 UI 滑块范围不一致，用户拖到超出
    clamp 的那段行程会被静默截断，并在 custom 档恢复时把阈值改写回边界值
    (GUI 与 CLI 评星同时受影响)。锐度已因此修过一次(下限 200→100)，美学
    随后重犯同样的错(下限 4.0，滑块却是 0.0-7.0)。本测试把「clamp 必须与
    滑块对齐」这条约定固化下来，防止第三次发生。

    When an advanced_config setter's clamp disagrees with its UI slider range,
    the out-of-clamp part of the slider is silently truncated and the custom
    preset later overwrites the user's threshold with the boundary value. This
    happened once for sharpness and again for aesthetics; these tests pin the
    invariant so it cannot happen a third time.

约定 / Convention:
    所有测试都通过 ``AdvancedConfig(config_file=...)`` 注入临时路径，绝不触碰
    用户真实的 advanced_config.json，也绝不调用 save()。
    Every test injects a temp path and never calls save(), so the user's real
    advanced_config.json is never read for writing nor modified.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from advanced_config import AdvancedConfig

REPO_ROOT = Path(__file__).resolve().parent


@pytest.fixture()
def config(tmp_path: Path) -> AdvancedConfig:
    """返回一个写入临时文件的配置实例。/ Config instance bound to a temp file."""

    return AdvancedConfig(config_file=str(tmp_path / "advanced_config.json"))


# ── 行为回归：美学阈值不得被下限截断 / Aesthetics must not be truncated ──────


@pytest.mark.parametrize("value", [0.0, 1.0, 2.0, 3.9, 4.0, 5.5, 7.0])
def test_custom_aesthetics_keeps_every_value_the_slider_can_produce(
    config: AdvancedConfig, value: float
) -> None:
    """滑块 0-70 能产生的每个值都必须原样保留。/ Keep every slider-reachable value."""

    config.set_custom_aesthetics(value)
    assert config.custom_aesthetics == pytest.approx(value)


def test_custom_aesthetics_clamps_only_outside_the_slider_range(
    config: AdvancedConfig,
) -> None:
    """仅在滑块范围之外才 clamp。/ Clamp only beyond the slider range."""

    config.set_custom_aesthetics(-1.0)
    assert config.custom_aesthetics == pytest.approx(0.0)
    config.set_custom_aesthetics(9.0)
    assert config.custom_aesthetics == pytest.approx(7.0)


def test_custom_aesthetics_matches_min_nima_across_the_range(
    config: AdvancedConfig,
) -> None:
    """
    custom_aesthetics 与 min_nima 必须接受同一值域。

    两者代表同一个用户意图(美学阈值)，只是一个是当前值、一个是 custom 档记忆。
    值域不一致就会在 custom 档恢复时产生漂移。

    Both represent the same user intent, so their accepted ranges must agree;
    otherwise restoring the custom preset drifts the threshold.
    """

    for slider_position in range(0, 71):
        value = slider_position / 10.0
        config.set_min_nima(value)
        config.set_custom_aesthetics(value)
        assert config.min_nima == pytest.approx(config.custom_aesthetics), (
            f"滑块位置 {slider_position} 处两者不一致 / mismatch at slider {slider_position}"
        )


def test_custom_sharpness_matches_min_sharpness_across_the_range(
    config: AdvancedConfig,
) -> None:
    """锐度侧的同一约定(已于早前修复，一并锁定)。/ Same invariant for sharpness."""

    for value in range(100, 601, 10):
        config.set_min_sharpness(value)
        config.set_custom_sharpness(value)
        assert config.min_sharpness == config.custom_sharpness


# ── 静态对齐：源码里的 clamp 常量必须等于滑块 setRange ─────────────────────


def _clamp_bounds(source: str, setter_name: str) -> tuple[float, float]:
    """
    从 setter 源码中提取 ``max(lo, min(hi, ...))`` 的边界常量。

    参数 / Parameters:
        source: 模块源码。/ Module source.
        setter_name: setter 方法名。/ Setter method name.

    返回 / Returns:
        tuple[float, float]: (下限, 上限)。/ (lower, upper) bounds.
    """

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != setter_name:
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "max"
                and len(call.args) == 2
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[1], ast.Call)
                and isinstance(call.args[1].func, ast.Name)
                and call.args[1].func.id == "min"
                and isinstance(call.args[1].args[0], ast.Constant)
            ):
                return (
                    float(call.args[0].value),
                    float(call.args[1].args[0].value),
                )
    raise AssertionError(f"未找到 {setter_name} 的 clamp 边界 / clamp not found")


def _slider_range(source: str, slider_attr: str) -> tuple[float, float]:
    """
    从 UI 源码中提取 ``<slider>.setRange(lo, hi)`` 的范围。

    参数 / Parameters:
        source: UI 模块源码。/ UI module source.
        slider_attr: 滑块属性名，如 ``nima_slider``。/ Slider attribute name.

    返回 / Returns:
        tuple[float, float]: (下限, 上限)。/ (lower, upper) range.
    """

    match = re.search(
        rf"{re.escape(slider_attr)}\.setRange\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)", source
    )
    assert match, f"未找到 {slider_attr} 的 setRange / setRange not found"
    return float(match.group(1)), float(match.group(2))


@pytest.mark.parametrize(
    ("ui_module", "slider_attr", "setter_name", "scale"),
    [
        ("ui/main_window.py", "self.nima_slider", "set_custom_aesthetics", 10.0),
        ("ui/main_window.py", "self.nima_slider", "set_min_nima", 10.0),
        ("ui/settings_center.py", "self._cull_nima", "set_custom_aesthetics", 10.0),
        ("ui/main_window.py", "self.sharp_slider", "set_custom_sharpness", 1.0),
        ("ui/main_window.py", "self.sharp_slider", "set_min_sharpness", 1.0),
        ("ui/settings_center.py", "self._cull_sharp", "set_min_sharpness", 1.0),
    ],
)
def test_setter_clamp_matches_ui_slider_range(
    ui_module: str, slider_attr: str, setter_name: str, scale: float
) -> None:
    """
    setter 的 clamp 必须与对应 UI 滑块范围完全一致。

    以源码为准做静态比对，因此无需构造 Qt 控件，也不会切换全局 i18n 语言。
    Compared statically from source, so no Qt widget is constructed and the
    global i18n language is never switched.
    """

    config_source = (REPO_ROOT / "advanced_config.py").read_text(encoding="utf-8")
    ui_source = (REPO_ROOT / ui_module).read_text(encoding="utf-8")

    clamp_lo, clamp_hi = _clamp_bounds(config_source, setter_name)
    slider_lo, slider_hi = _slider_range(ui_source, slider_attr)

    assert (clamp_lo, clamp_hi) == (slider_lo / scale, slider_hi / scale), (
        f"{setter_name} 的 clamp ({clamp_lo}, {clamp_hi}) 与 "
        f"{ui_module} 中 {slider_attr} 的范围 "
        f"({slider_lo / scale}, {slider_hi / scale}) 不一致"
    )
