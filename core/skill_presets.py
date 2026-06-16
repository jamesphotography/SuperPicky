# -*- coding: utf-8 -*-
"""
摄影水平预设（Skill Level Presets）—— 非 UI、无 Qt 依赖的共享定义。

把 SKILL_PRESETS 与 get_skill_level_thresholds 从 ui/skill_level_dialog.py 抽到此处，
让 GUI 与 CLI 共用同一份「水平→锐度/美学阈值」映射，避免两份预设漂移。
ui/skill_level_dialog.py 仍 re-export 这两个符号，GUI 行为完全不变。

Skill-level presets shared by GUI and CLI (no Qt import), single source of truth
for the level → (sharpness, aesthetics) mapping. ui/skill_level_dialog.py re-exports
these so existing GUI imports keep working unchanged.
"""

from typing import Optional, Tuple

# 预设配置常量 / Preset constants
SKILL_PRESETS = {
    "beginner": {
        "sharpness": 300,
        "aesthetics": 4.5,
        "name_key": "skill_level.beginner",
        "desc_key": "skill_level.beginner_desc",
    },
    "intermediate": {
        "sharpness": 380,
        "aesthetics": 4.8,
        "name_key": "skill_level.intermediate",
        "desc_key": "skill_level.intermediate_desc",
    },
    "master": {
        "sharpness": 520,
        "aesthetics": 5.5,
        "name_key": "skill_level.master",
        "desc_key": "skill_level.master_desc",
    },
}


def get_skill_level_thresholds(level_key: str, config=None) -> Tuple[int, float]:
    """
    获取指定摄影水平的阈值。

    参数:
        level_key (str): 水平键 ("beginner" / "intermediate" / "master" / "custom")
        config: 配置对象，仅在 custom 模式下需要（读 custom_sharpness/custom_aesthetics）

    返回:
        Tuple[int, float]: (sharpness, aesthetics) 阈值元组

    Get the (sharpness, aesthetics) thresholds for a skill level. For "custom" the
    values come from the config object; otherwise from SKILL_PRESETS.
    """
    if level_key == "custom" and config is not None:
        return (config.custom_sharpness, config.custom_aesthetics)

    preset = SKILL_PRESETS.get(level_key, SKILL_PRESETS["intermediate"])
    return (preset["sharpness"], preset["aesthetics"])
