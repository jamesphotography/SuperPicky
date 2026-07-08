# -*- coding: utf-8 -*-
"""设置入口菜单项必须固定在「设置」菜单内 + 提供标准快捷键。

The settings entry must stay inside the Settings menu with a standard
shortcut.

背景 / Background:
英文文案 "Preferences..." 会命中 macOS 的菜单文字启发式（PreferencesRole），
被 Qt 自动挪到应用菜单（SuperPicky → Settings…）——英文界面下「设置」菜单里
的设置入口凭空消失，而中文文案「参数设置...」不命中启发式、行为不同。
显式 NoRole 让入口在所有平台、所有语言下位置一致；Ctrl+,（macOS 显示 ⌘,）
是打开设置的跨平台标准快捷键。

The English label "Preferences..." matches macOS's text heuristic
(PreferencesRole) and Qt silently relocates the item to the application
menu — it vanishes from the Settings menu in the English UI while the
Chinese label stays put. Explicit NoRole pins it in place on every
platform/language; Ctrl+, (shown as ⌘, on macOS) is the standard shortcut.

用源码级断言而非整窗实例化：MainWindow 构造会写真实用户配置，违反测试
隔离约定。Source-level assertions instead of full-window construction:
building MainWindow writes the real user config, breaking test isolation.
"""
import inspect

import ui.main_window as mw


def _setup_menu_source() -> str:
    return inspect.getsource(mw.SuperPickyMainWindow._setup_menu)


def test_settings_action_pinned_with_no_role():
    """设置入口必须显式 NoRole，禁止 macOS 按文字启发式挪走。"""
    src = _setup_menu_source()
    assert "settings_action.setMenuRole(QAction.MenuRole.NoRole)" in src, (
        "settings_action 未设 NoRole——macOS 英文界面下会被挪出设置菜单"
    )


def test_settings_action_has_standard_shortcut():
    """设置入口必须绑定 Ctrl+,（macOS 显示为 ⌘,）标准快捷键。"""
    src = _setup_menu_source()
    assert 'QKeySequence("Ctrl+,")' in src and "settings_action.setShortcut" in src, (
        "settings_action 未绑定 Ctrl+, 快捷键"
    )
