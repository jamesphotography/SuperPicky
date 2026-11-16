# -*- coding: utf-8 -*-
"""
结果浏览器缩略图网格：Shift 范围选的起点修正、Shift+←/→ 扩选、鼠标框选。

此前 Shift 的起点只在鼠标点击时更新：用 ←/→ 翻图后再 Shift+点击，范围从上次
鼠标点过的那张算起；刚打开还没点过时 Shift+点击退化为普通点击；连续 Shift
点击会越叠越多。

Thumbnail grid: Shift-range anchor follows the outlined photo, Shift+arrow
extends the range, and mouse rubber-band selection.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def grid():
    """带 10 张照片的网格（不建卡片）/ A grid with 10 photos, no cards."""
    from tools.i18n import get_i18n
    from ui.thumbnail_grid import ThumbnailGrid

    g = ThumbnailGrid(get_i18n())
    g._photos = [{"filename": f"p{i}"} for i in range(10)]
    yield g
    g.close()


def _click(g, idx: int, mods=Qt.NoModifier) -> None:
    """模拟带修饰键点击第 idx 张 / Simulate a click on photo idx."""
    g._handle_card_click(g._photos[idx], mods)


def _checked(g) -> list:
    return [p["filename"] for p in g.get_explicitly_selected_photos()]


def _outlined(g) -> str:
    return g._photos[g._selected_index()]["filename"]


def test_shift_click_after_arrow_starts_from_outlined_photo(grid):
    """点 p1，→→ 到 p3，再 Shift+点击 p5：勾 p3~p5，不是 p1~p5。"""
    _click(grid, 1)
    grid._select_adjacent(1)
    grid._select_adjacent(1)
    _click(grid, 5, Qt.ShiftModifier)
    assert _checked(grid) == ["p3", "p4", "p5"]


def test_shift_click_uses_programmatic_selection(grid):
    """没用鼠标点过、只有程序选中的 p2（如刚打开浏览器）：Shift+点击 p4 勾 p2~p4。"""
    grid.select_photo(grid._photos[2])
    _click(grid, 4, Qt.ShiftModifier)
    assert _checked(grid) == ["p2", "p3", "p4"]


def test_shift_click_without_any_selection_is_plain_click(grid):
    """什么都没选时 Shift+点击：当普通点击，只选中这张。"""
    _click(grid, 4, Qt.ShiftModifier)
    assert _checked(grid) == []
    assert _outlined(grid) == "p4"


def test_successive_shift_clicks_replace_previous_range(grid):
    """点 p4，Shift+点 p7，再 Shift+点 p2：结果是 p2~p4（第二次替换第一次）。"""
    _click(grid, 4)
    _click(grid, 7, Qt.ShiftModifier)
    _click(grid, 2, Qt.ShiftModifier)
    assert _checked(grid) == ["p2", "p3", "p4"]


def test_shift_range_keeps_earlier_ctrl_checks(grid):
    """点 p0，Ctrl+点 p5，再 Shift+点 p7：保留 p0，并勾 p5~p7。"""
    _click(grid, 0)
    _click(grid, 5, Qt.ControlModifier)
    _click(grid, 7, Qt.ShiftModifier)
    assert _checked(grid) == ["p0", "p5", "p6", "p7"]


def test_shift_click_moves_outline_not_anchor(grid):
    """Shift+点击后绿框移到点的那张，起点不变。"""
    _click(grid, 2)
    _click(grid, 5, Qt.ShiftModifier)
    assert _outlined(grid) == "p5"
    _click(grid, 0, Qt.ShiftModifier)
    assert _checked(grid) == ["p0", "p1", "p2"]


def test_shift_arrow_extends_and_shrinks(grid):
    """点 p3，Shift+→ 两次勾 p3~p5，再 Shift+← 一次缩回 p3~p4。"""
    _click(grid, 3)
    assert grid.extend_selection_by_arrow(1, Qt.ShiftModifier)
    assert grid.extend_selection_by_arrow(1, Qt.ShiftModifier)
    assert _checked(grid) == ["p3", "p4", "p5"]
    assert grid.extend_selection_by_arrow(-1, Qt.ShiftModifier)
    assert _checked(grid) == ["p3", "p4"]
    assert _outlined(grid) == "p4"


def test_shift_arrow_ignores_keypad_modifier(grid):
    """macOS 方向键自带 KeypadModifier，Shift+方向键仍算扩选。"""
    _click(grid, 3)
    assert grid.extend_selection_by_arrow(1, Qt.ShiftModifier | Qt.KeypadModifier)
    assert _checked(grid) == ["p3", "p4"]


def test_plain_arrow_not_handled_as_extend(grid):
    """不带 Shift 的方向键不归扩选处理（交给原来的清勾+翻图）。"""
    _click(grid, 3)
    assert not grid.extend_selection_by_arrow(1, Qt.NoModifier)
    assert not grid.extend_selection_by_arrow(1, Qt.ControlModifier | Qt.ShiftModifier)


def test_shift_arrow_at_end_is_consumed(grid):
    """在最后一张按 Shift+→：算已处理（不退回翻图），勾选不变。"""
    _click(grid, 9)
    assert grid.extend_selection_by_arrow(1, Qt.ShiftModifier)
    assert _checked(grid) == []


# ---------------------------------------------------------------------------
#  鼠标框选：需要真实卡片几何 / Rubber band: needs real card geometry
# ---------------------------------------------------------------------------

@pytest.fixture
def built_grid():
    """建好 12 张卡片、4 列的网格 / A grid with 12 real cards in 4 columns."""
    from tools.i18n import get_i18n
    from ui.thumbnail_grid import ThumbnailGrid

    g = ThumbnailGrid(get_i18n())
    g.resize(4 * (g._thumb_size + 8) + 40, 2000)
    g.show()
    g._photos = [{"filename": f"b{i}"} for i in range(12)]
    g._current_col_count = 4
    g._current_batch_idx = 0
    g._build_batch()
    _app.processEvents()
    g._container.adjustSize()
    _app.processEvents()
    yield g
    g.close()


def _card_center_global(g, idx: int) -> QPoint:
    """第 idx 张卡片中心的屏幕坐标 / Global centre of card idx."""
    from ui.thumbnail_grid import _photo_key

    card = g._cards[_photo_key(g._photos[idx])]
    return g._container.mapToGlobal(card.geometry().center())


def test_band_drag_checks_intersected_cards(built_grid):
    """从 b0 中心拖到 b5 中心（第 1 行第 1 列 → 第 2 行第 2 列）：勾 b0,b1,b4,b5。"""
    g = built_grid
    g.band_press(_card_center_global(g, 0), Qt.NoModifier)
    g.band_move(_card_center_global(g, 5))
    g.band_release()
    assert _checked(g) == ["b0", "b1", "b4", "b5"]
    assert not g._band_active


def test_band_tiny_move_is_just_a_click(built_grid):
    """拖动不到阈值：不出框、不改勾选。"""
    g = built_grid
    start = _card_center_global(g, 0)
    g.band_press(start, Qt.NoModifier)
    g.band_move(start + QPoint(1, 0))
    g.band_release()
    assert _checked(g) == []


def test_band_replaces_without_modifier(built_grid):
    """已勾 b10，再直接框 b0~b1：结果只剩框里的。"""
    g = built_grid
    _click(g, 10, Qt.ControlModifier)
    g.band_press(_card_center_global(g, 0), Qt.NoModifier)
    g.band_move(_card_center_global(g, 1))
    g.band_release()
    assert _checked(g) == ["b0", "b1"]


def test_band_with_ctrl_adds_to_checks(built_grid):
    """按住 Ctrl/⌘ 框选：保留原有的勾 b10。"""
    g = built_grid
    _click(g, 10, Qt.ControlModifier)
    g.band_press(_card_center_global(g, 0), Qt.ControlModifier)
    g.band_move(_card_center_global(g, 1))
    g.band_release()
    assert _checked(g) == ["b0", "b1", "b10"]


def test_band_shrinks_when_dragged_back(built_grid):
    """拖出去再拖回来：框外的卡片取消勾选。"""
    g = built_grid
    g.band_press(_card_center_global(g, 0), Qt.NoModifier)
    g.band_move(_card_center_global(g, 7))
    g.band_move(_card_center_global(g, 1))
    g.band_release()
    assert _checked(g) == ["b0", "b1"]


def test_reload_cancels_band(built_grid):
    """拖动中网格重建：框选状态清掉，不残留。"""
    g = built_grid
    g.band_press(_card_center_global(g, 0), Qt.NoModifier)
    g.band_move(_card_center_global(g, 5))
    g.load_photos([])
    assert g._band_origin is None and not g._band_active
