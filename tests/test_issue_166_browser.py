# -*- coding: utf-8 -*-
"""
#166 结果浏览器小改进 / Results-browser improvements from #166.

Al 的建议里这一轮做了三项（用户 2026-11-14 拍板；Tab 在勾选集里切换涉及 Windows/macOS
差异先不做，按 EXIF 搜索也先不做）：

1. 按文件名搜索：``0103`` 直接找到 ``_T3A0103.CR3``，``*A02*`` 找 ``_T3A0200``–``0299``；
2. 详情面板显示文件名（2026-06 改版后网格模式下屏幕上哪里都看不到）与光圈；
3. 按 ←/→ 翻图时清掉多选的勾（与资源管理器/访达一致）；↑/↓ 是打星键，不清。

Three of Al's suggestions: filename search, filename + aperture in the detail
panel, and Left/Right clearing the checked selection (Up/Down still rate).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from core.filename_search import filename_matches, filter_by_filename  # noqa: E402


def _p(stem, ext=".CR3", original=None):
    return {"filename": stem, "current_path": f"/d/3星/{stem}{ext}",
            "original_path": original or f"/d/{stem}{ext}"}


# ---------------------------------------------------------------- 搜索 / search

def test_plain_number_matches_without_wildcards():
    """直接输数字按「包含」匹配，不必打星号。"""
    assert filename_matches("0103", _p("_T3A0103"))
    assert filename_matches("103", _p("_T3A0103"))
    assert not filename_matches("0104", _p("_T3A0103"))


def test_wildcards_follow_als_examples():
    """Al 的两个例子：*103* 与 *A02*（匹配 0200–0299，不含 0300）。"""
    assert filename_matches("*103*", _p("_T3A0103"))
    photos = [_p(f"_T3A{n:04d}") for n in (199, 200, 250, 299, 300)]
    assert [p["filename"] for p in filter_by_filename(photos, "*A02*")] == \
        ["_T3A0200", "_T3A0250", "_T3A0299"]


def test_case_insensitive_and_extension_wildcards():
    """不区分大小写；*.cr3 这类按扩展名的通配也行；? 匹配单个字符。"""
    assert filename_matches("_t3a0103", _p("_T3A0103"))
    assert filename_matches("*.cr3", _p("_T3A0103"))
    assert not filename_matches("*.nef", _p("_T3A0103"))
    assert filename_matches("_T3A010?", _p("_T3A0103"))


def test_several_terms_are_or():
    """空格分隔的多个词是「或」。"""
    photos = [_p("_T3A0103"), _p("_T3A0107"), _p("_T3A0110")]
    assert [p["filename"] for p in filter_by_filename(photos, "0103 0107")] == \
        ["_T3A0103", "_T3A0107"]


def test_empty_search_keeps_everything_in_order():
    photos = [_p("b"), _p("a")]
    assert filter_by_filename(photos, "  ") is photos


# ---------------------------------------------------------------- 详情面板 / panel

@pytest.fixture
def english():
    from tools.i18n import get_i18n
    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("en_US")
    yield i18n
    i18n.switch_language(previous)


def test_panel_shows_filename_and_aperture(english):
    """文件名与光圈两行都在 EXIF 区；光圈格式 f/5.6、f/4、f/8。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(english)
    try:
        keys = [k for k, _ in panel._meta_rows]
        assert keys.index("browser.meta_filename") < keys.index("browser.meta_camera")
        assert keys.index("browser.meta_shutter") < keys.index("browser.meta_aperture")
        photo = {"filename": "_T3A0103", "current_path": "/x/_T3A0103.CR3",
                 "bird_species_en": "Great Blue Heron", "rating": 2, "aperture": "5.6"}
        panel.set_current_photo(photo)
        assert "_T3A0103" in panel._val_filename.text()
        assert panel._val_aperture.text() == "f/5.6"
    finally:
        panel.close()


@pytest.mark.parametrize("raw, shown", [("4", "f/4"), ("8.0", "f/8"), (7.1, "f/7.1"),
                                        (None, "—"), ("", "—"), ("f/9", "f/9")])
def test_aperture_format(raw, shown):
    from ui.detail_panel import DetailPanel
    assert DetailPanel._format_aperture(raw) == shown


# ---------------------------------------------------------------- 方向键 / arrows

@pytest.fixture
def grid():
    from tools.i18n import get_i18n
    from ui.thumbnail_grid import ThumbnailGrid

    g = ThumbnailGrid(get_i18n())
    g._photos = [{"filename": f"p{i}"} for i in range(5)]
    g._handle_card_click(g._photos[1], Qt.NoModifier)
    g._handle_card_click(g._photos[3], Qt.ControlModifier)
    assert len(g.get_explicitly_selected_photos()) == 2
    yield g
    g.close()


def _key(key, mods=Qt.NoModifier):
    return QKeyEvent(QEvent.KeyPress, key, mods)


@pytest.mark.parametrize("key", [Qt.Key_Left, Qt.Key_Right])
def test_left_right_clear_the_checks(grid, key):
    """不带修饰键的 ←/→ 清掉勾选（macOS 方向键自带 KeypadModifier，也算不带修饰键）。"""
    grid.keyPressEvent(_key(key, Qt.KeypadModifier))
    assert grid.get_explicitly_selected_photos() == []


@pytest.mark.parametrize("key", [Qt.Key_Up, Qt.Key_Down])
def test_up_down_keep_the_checks_for_batch_rating(grid, key):
    """↑/↓ 是打星键：多选时要批量打星，勾必须留着。"""
    grid.keyPressEvent(_key(key))
    assert len(grid.get_explicitly_selected_photos()) == 2


def test_modified_arrows_keep_the_checks(grid):
    """带 Shift/Ctrl 的方向键不清（不改变原有组合键行为）。"""
    assert grid.clear_multi_select_for_arrow(Qt.ShiftModifier) is False
    assert len(grid.get_explicitly_selected_photos()) == 2


# ---------------------------------------------------------------- 筛选面板 / panel

def test_filter_panel_search_box(english):
    """get_filters 带上搜索词；Esc 先清空且不外传（否则会关掉浏览器）；重置清空。"""
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(english)
    try:
        panel.filename_search.setText(" *A02* ")
        assert panel.get_filters()["filename_pattern"] == "*A02*"

        esc = _key(Qt.Key_Escape)
        assert panel.eventFilter(panel.filename_search, esc) is True
        assert panel.filename_search.text() == ""

        panel.filename_search.setText("0103")
        panel.reset_all()
        assert panel.get_filters()["filename_pattern"] == ""
    finally:
        panel.close()


def test_browser_applies_the_search(tmp_path, english):
    """真实浏览器 + 真实库：搜索只缩小显示范围，与星级等其他筛选叠加。"""
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    for n in (103, 200, 250, 300):
        rel = f"_T3A{n:04d}.CR3"
        open(os.path.join(root, rel), "w").close()
        db.insert_photo({"filename": f"_T3A{n:04d}", "current_path": rel,
                         "original_path": rel, "rating": 3, "has_bird": 1})
    db.close()

    win = rbw.ResultsBrowserWindow()
    try:
        win.open_directory(root)
        assert len(win._raw_filtered_photos) == 4
        filters = win._filter_panel.get_filters()
        filters["filename_pattern"] = "*A02*"
        win._apply_filters(filters)
        assert sorted(p["filename"] for p in win._raw_filtered_photos) == ["_T3A0200", "_T3A0250"]
        filters["filename_pattern"] = "0103"
        win._apply_filters(filters)
        assert [p["filename"] for p in win._raw_filtered_photos] == ["_T3A0103"]
    finally:
        win.close()


# ---------------------------------------------------------------- 对焦筛选排版 / focus layout

def _positions(panel):
    grid = panel._focus_checks["BEST"].parentWidget().layout()
    return {m: grid.getItemPosition(grid.indexOf(cb))[:2] for m, cb in panel._focus_checks.items()}


def test_english_focus_options_stack_instead_of_overlapping(english):
    """
    英文「Critical Focus」「Good Focus」两列放不下（网格平分宽度、还要扣滚动条），
    此前挤在一起文字重叠；现在改单列。
    """
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(english)
    try:
        assert {col for _row, col in _positions(panel).values()} == {0}
    finally:
        panel.close()


def test_chinese_focus_options_keep_two_columns():
    """中文文字短，仍是两列（排版与原来一致）。"""
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("zh_CN")
    panel = FilterPanel(i18n)
    try:
        assert _positions(panel) == {"BEST": (0, 0), "GOOD": (0, 1), "BAD": (1, 0)}
    finally:
        panel.close()
        i18n.switch_language(previous)


def test_grid_columns_rule():
    """最宽一项放得进半宽（扣间距）才两列。"""
    from ui.filter_panel import _grid_columns

    class _W:
        def __init__(self, width):
            self._w = width

        def ensurePolished(self):
            pass

        def sizeHint(self):
            from PySide6.QtCore import QSize
            return QSize(self._w, 20)

    assert _grid_columns([_W(90), _W(40)], spacing=6, available=190) == 2
    assert _grid_columns([_W(98), _W(89)], spacing=6, available=188) == 1
