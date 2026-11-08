# -*- coding: utf-8 -*-
"""
#160 回归测试：单击一张后 Ctrl(macOS 为 Command)+点击其他照片，先单击的那张
也算进勾选集合——与 Windows 资源管理器、macOS 访达一致。

此前它只有绿框、不在勾选集合里，右键菜单「N 张」少算一张。

#160: after a plain click, Ctrl/Cmd+clicking other photos also checks the
first (outlined) photo, as Explorer and Finder do.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def grid():
    """带 5 张照片的网格 / A grid with 5 photos."""
    from tools.i18n import get_i18n
    from ui.thumbnail_grid import ThumbnailGrid

    g = ThumbnailGrid(get_i18n())
    g._photos = [{"filename": f"p{i}"} for i in range(5)]
    yield g
    g.close()


def _click(g, idx: int, mods=Qt.NoModifier) -> None:
    """模拟带修饰键点击第 idx 张 / Simulate a click on photo idx with modifiers."""
    g._handle_card_click(g._photos[idx], mods)


def _checked(g) -> list:
    return [p["filename"] for p in g.get_explicitly_selected_photos()]


def test_ctrl_click_after_plain_click_includes_first_photo(grid):
    """单击 p0，再 Ctrl+点击 p1~p4：5 张全部勾选。"""
    _click(grid, 0)
    for i in range(1, 5):
        _click(grid, i, Qt.ControlModifier)
    assert _checked(grid) == ["p0", "p1", "p2", "p3", "p4"]


def test_ctrl_click_can_still_uncheck_first_photo(grid):
    """勾进来的第一张仍可用 Ctrl+点击取消。"""
    _click(grid, 0)
    _click(grid, 2, Qt.ControlModifier)
    _click(grid, 0, Qt.ControlModifier)
    assert _checked(grid) == ["p2"]


def test_ctrl_click_on_selected_photo_alone_toggles_only_it(grid):
    """单击 p1 后 Ctrl+点击 p1 本身：只切换 p1，不重复加入。"""
    _click(grid, 1)
    _click(grid, 1, Qt.ControlModifier)
    assert _checked(grid) == ["p1"]


def test_compare_pair_unchanged(grid):
    """单击 A、Ctrl+点击 B：对比视图仍取 [A, B]。"""
    _click(grid, 0)
    _click(grid, 3, Qt.ControlModifier)
    assert [p["filename"] for p in grid.get_multi_selected_photos()] == ["p0", "p3"]


def test_shift_click_range_unchanged(grid):
    """单击 p1、Shift+点击 p3：勾选 p1~p3（原有行为）。"""
    _click(grid, 1)
    _click(grid, 3, Qt.ShiftModifier)
    assert _checked(grid) == ["p1", "p2", "p3"]
