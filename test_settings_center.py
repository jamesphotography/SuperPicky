# -*- coding: utf-8 -*-
"""
SettingsCenter 骨架测试 — Task 2 TDD

测试左侧导航 QListWidget 6 项 + 右侧 QStackedWidget 页切换。

SettingsCenter skeleton tests — Task 2 TDD

Tests the left-side QListWidget with 6 items + right-side QStackedWidget page switching.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_nav_has_six_pages_and_switch():
    """
    验证导航列表有 6 个分页，且 show_page 能正确切换 stack。

    Verify the nav list has 6 pages and show_page correctly switches the stack.
    """
    from ui.settings_center import SettingsCenter, PAGE_ORDER
    w = SettingsCenter(get_i18n())
    assert PAGE_ORDER == ["culling", "birdid", "output", "video", "apps", "about"]
    assert w._nav.count() == 6
    w.show_page("about")
    assert w._stack.currentIndex() == PAGE_ORDER.index("about")
    w.close()


def test_skill_preset_fills_thresholds_and_manual_edit_switches_custom():
    """
    验证精选页协同逻辑:
    1. 选技能等级预设 → 阈值滑块被自动填充为该档对应值
    2. 手动拖动阈值滑块 → 档位切换到"自定义"

    Verify culling page coordination logic:
    1. Selecting a skill preset fills the threshold sliders with that level's values.
    2. Manually adjusting a threshold slider switches the level to "custom".

    注意:brief 假设 get_skill_level_thresholds 返回含键名的字典且存在 "advanced" 档位,
    但实际 API 返回 Tuple[int, float] 且档位为 beginner/intermediate/master/custom。
    此处用 "master"(最严格档)代替 "advanced",并用元组下标 th[0]/th[1] 访问。

    Note: The brief assumed a dict return with "advanced" level; actual API returns
    Tuple[int, float] and levels are beginner/intermediate/master/custom.
    We use "master" (strictest) in place of "advanced", and access via th[0]/th[1].
    """
    from ui.settings_center import SettingsCenter
    from core.skill_presets import get_skill_level_thresholds

    w = SettingsCenter(get_i18n())
    w.show_page("culling")

    # 选 master 预设档 → 阈值滑块被填为该档值 / Select master preset → sliders filled
    th = get_skill_level_thresholds("master")  # returns (sharpness: int, aesthetics: float)
    w._on_skill_preset_selected("master")
    assert w._cull_sharp.value() == int(th[0])

    # 手动改阈值 → 档位切到自定义 / Manual change → switches to custom
    w._cull_sharp.setValue(w._cull_sharp.value() + 30)
    assert w._current_skill_key == "custom"

    w.close()
