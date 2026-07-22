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


def test_tile_label_single_line_species_or_filename():
    """
    RC8 起卡片标签单行:有鸟种显示鸟名(随界面语言),无鸟种显示文件名;
    文件名不再进标签(改由整卡 tooltip 悬停查看),连拍后缀附于行尾。
    Since RC8 the tile label is a single line — species name (localized) when
    present, otherwise the filename; the filename lives in the hover tooltip.
    """
    from ui.thumbnail_grid import _tile_label_text, _display_name

    photo = {"filename": "DSC01234.ARW",
             "bird_species_cn": "白胸鸲鹟", "bird_species_en": "White-breasted Robin"}
    text = _tile_label_text(photo)
    # 单行显示鸟名(随语言),不含文件名 / single line = species, no filename
    assert text == _display_name(photo)
    assert "DSC01234.ARW" not in text
    assert ("白胸鸲鹟" in text) or ("White-breasted Robin" in text)

    no_species = {"filename": "DSC09999.NEF"}
    assert _tile_label_text(no_species) == "DSC09999.NEF"

    # 连拍后缀附在单行行尾,且不再有第二行 / suffix appended, no second line
    text2 = _tile_label_text(photo, " (5)")
    assert text2 == _display_name(photo) + " (5)"
    assert "<br/>" not in text2


def test_detail_panel_species_row_above_gbif():
    """
    详情面板 rows 中鸟种行存在且位于全球罕见度行之前(Paul 截图诉求)。
    The species row exists in the detail panel and sits above GBIF rarity.
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    keys = [k for k, _ in panel._meta_rows]
    assert "browser.meta_species" in keys
    assert keys.index("browser.meta_species") < keys.index("browser.meta_gbif_rarity")
    panel.close()


def test_rating_key_action_digits_and_arrows():
    """
    键盘打星决策:数字键 0-3 直设,Up/Down ±1 钳制 0-3;-1 可经 Up/数字键
    救回(Up 从 -1 → 0);星级无变化返回 None;无关键返回 None。

    Keyboard rating decisions: digits set directly, Up/Down step within
    0-3, -1 recovers via Up (to 0) or digits, no-op returns None.
    """
    from PySide6.QtCore import Qt
    from ui.results_browser_window import _rating_key_action

    assert _rating_key_action(Qt.Key_2, 0) == 2
    assert _rating_key_action(Qt.Key_0, 3) == 0
    assert _rating_key_action(Qt.Key_3, 3) is None          # 无变化 / no-op
    assert _rating_key_action(Qt.Key_Up, 1) == 2
    assert _rating_key_action(Qt.Key_Up, 3) is None          # 顶格 / ceiling
    assert _rating_key_action(Qt.Key_Up, -1) == 0            # 救回 / recover
    assert _rating_key_action(Qt.Key_Down, 2) == 1
    assert _rating_key_action(Qt.Key_Down, 0) is None        # 到 0 为止 / floor
    assert _rating_key_action(Qt.Key_Down, -1) is None       # -1 不再降 / stays
    assert _rating_key_action(Qt.Key_1, -1) == 1             # 数字键救回
    assert _rating_key_action(Qt.Key_F, 2) is None           # 无关键 / unrelated
    assert _rating_key_action(Qt.Key_2, None) == 2           # rating 缺失按 0 处理


def test_grid_ignores_up_down_for_keyboard_rating():
    """
    回归钉(macOS Up/Down 打星失灵根因):ThumbnailGrid 曾把 Up/Down 消费为
    「选相邻照片」,事件到不了宿主窗口的打星分支。修复后 Up/Down 必须
    ignore 并冒泡,且不再移动选中;Left/Right 仍在网格内导航。

    Regression pin for the macOS Up/Down rating bug: ThumbnailGrid used to
    consume Up/Down as adjacent-selection so the host window's rating branch
    never saw them. After the fix Up/Down must be ignored (bubbling to the
    host) while Left/Right still navigate within the grid.
    """
    from PySide6.QtCore import Qt, QEvent
    from PySide6.QtGui import QKeyEvent
    from ui.thumbnail_grid import ThumbnailGrid

    grid = ThumbnailGrid(get_i18n())
    calls = []
    grid._select_adjacent = lambda step: calls.append(step)

    def _press(k):
        ev = QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier)
        ev.setAccepted(True)  # 预置为已接受,检验 handler 是否显式 ignore
        grid.keyPressEvent(ev)
        return ev

    # Up/Down: 必须 ignore(冒泡给宿主窗口打星),且不移动选中
    for k in (Qt.Key_Up, Qt.Key_Down):
        ev = _press(k)
        assert not ev.isAccepted(), f"{k} should be ignored for host-window rating"
    assert calls == [], "Up/Down must no longer move the selection"

    # Left/Right: 仍然网格内导航
    _press(Qt.Key_Left)
    _press(Qt.Key_Right)
    assert calls == [-1, 1]
    grid.close()
