# -*- coding: utf-8 -*-
"""窗口矩形夹取工具的单元测试。

Unit tests for the window-rectangle clamping helper.

背景：主窗口保存的几何（本机实测 y=57 / height=820，底边 877）在 Dock 常驻时
超出可用区域（约 850），导致「开始处理」「重置」两个按钮被 Dock 切掉且点不到。
原 `_is_valid_main_window_rect` 只检查窗口与屏幕「相交」，不检查「放得下」，
所以超出的几何每次都被原样恢复。这里锁定夹取函数的语义，使恢复后的窗口必然
完全落在目标屏幕的可用区域内。

Context: the saved main-window geometry (measured locally: y=57, height=820,
bottom edge 877) overflows the available area (~850) when the Dock is pinned,
pushing the start/reset buttons off-screen. The old validity check only tested
for intersection, never for fit. These tests pin the clamp semantics so a
restored window always lands fully inside the target screen's available area.
"""
from PySide6.QtCore import QRect, QSize

from ui.geometry_utils import clamp_rect_to_available


def test_rect_already_inside_is_unchanged():
    """完全落在可用区内的矩形原样返回 / A rect that already fits is untouched."""
    available = QRect(0, 25, 1512, 913)
    rect = QRect(100, 100, 960, 700)
    assert clamp_rect_to_available(rect, available) == rect


def test_bottom_overflow_is_shifted_up():
    """底边超出时上移，尺寸不变（真实故障场景：Dock 常驻切掉按钮）。"""
    # 可用区 y=25..875（Dock 常驻），保存的窗口底边 877 超出 2px
    available = QRect(0, 25, 1512, 850)
    rect = QRect(200, 57, 960, 820)
    result = clamp_rect_to_available(rect, available)

    assert result.size() == rect.size(), "放得下时不应缩小尺寸"
    assert available.contains(result), "夹取后必须完全落在可用区内"
    assert result.bottom() <= available.bottom()
    assert result.x() == rect.x(), "只需上移，不应改动横向位置"


def test_oversized_rect_is_resized_to_available():
    """尺寸超过可用区时缩到可用区大小并对齐左上角。"""
    available = QRect(0, 25, 1200, 800)
    rect = QRect(-50, 0, 1600, 1000)
    result = clamp_rect_to_available(rect, available)

    assert result == QRect(0, 25, 1200, 800)


def test_minimum_size_wins_over_small_available_area():
    """可用区小于窗口最小尺寸时保尺寸、顶左对齐，让内容尽量露出。"""
    available = QRect(0, 25, 700, 600)
    rect = QRect(300, 300, 960, 820)
    result = clamp_rect_to_available(rect, available, QSize(800, 720))

    assert result.size() == QSize(800, 720), "不能缩到最小尺寸以下"
    assert result.topLeft() == available.topLeft(), "装不下时对齐可用区左上角"


def test_offset_available_area_second_screen():
    """可用区不是从原点开始时（副屏/菜单栏偏移）夹取仍然正确。"""
    available = QRect(1512, 100, 1920, 1000)
    rect = QRect(1400, 50, 1000, 900)
    result = clamp_rect_to_available(rect, available)

    assert available.contains(result)
    assert result.size() == rect.size()
    assert result.topLeft() == available.topLeft()


def test_negative_offscreen_rect_is_pulled_back():
    """完全在可用区左上方之外的矩形被拉回可用区内。"""
    available = QRect(0, 25, 1512, 913)
    rect = QRect(-2000, -900, 900, 760)
    result = clamp_rect_to_available(rect, available)

    assert available.contains(result)
    assert result.size() == rect.size()
