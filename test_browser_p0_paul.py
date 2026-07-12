# -*- coding: utf-8 -*-
"""
Paul 反馈 P0 三项的回归测试:对焦文案统一/鸟名文件名并显/键盘打星。

Regression tests for the three P0 items from Paul's feedback: consistent
focus labels, species+filename display, and keyboard star rating.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_focus_filter_labels_match_detail_panel_terms():
    """
    筛选面板三个对焦 checkbox 的文本必须与右侧详情使用的
    browser.focus_state_* i18n 值一致(不再显示裸枚举 BEST/GOOD/BAD)。

    The three focus checkboxes must reuse the browser.focus_state_* strings
    shown in the detail panel (no more raw BEST/GOOD/BAD enums).
    """
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    panel = FilterPanel(i18n)
    for mode in ("BEST", "GOOD", "BAD"):
        cb = panel._focus_checks[mode]
        expected = i18n.t(f"browser.focus_state_{mode.lower()}")
        assert cb.text() == expected, f"{mode}: {cb.text()!r} != {expected!r}"
        assert cb.text() != mode  # 不允许裸枚举 / raw enum forbidden
    panel.close()


def test_focus_filter_labels_use_i18n_in_english_ui():
    """
    用假 i18n(英文态)构造面板,证明 checkbox 文案确实经 i18n.t 取值——
    修复前英文界面显示裸枚举 BEST/GOOD/BAD(Paul 反馈的左右不一致根因)。

    Build the panel with a fake English i18n to prove labels go through
    i18n.t; before the fix the English UI showed the raw enums.
    """
    from ui.filter_panel import FilterPanel

    class _FakeI18n:
        current_lang = "en_US"

        def t(self, key, **kw):
            return f"<{key}>"

    panel = FilterPanel(_FakeI18n())
    for mode in ("BEST", "GOOD", "BAD"):
        cb = panel._focus_checks[mode]
        assert cb.text() == f"<browser.focus_state_{mode.lower()}>", cb.text()
    panel.close()
