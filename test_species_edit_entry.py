# -*- coding: utf-8 -*-
"""
issue #106 回归测试:grid 卡片与详情面板的鸟种编辑铅笔入口。

- 卡片铅笔点击 → ThumbnailCard.species_edit_requested 携带 photo dict
- 无鸟种照片也显示铅笔(允许人工补录)
- 详情面板铅笔点击 → DetailPanel.species_edit_requested(修复幽灵信号)
- ThumbnailGrid 转发信号存在(浏览器接线依赖)

Regression tests for the issue #106 species-edit entries: the grid-card
pencil and the detail-panel pencil both emit species_edit_requested with
the photo dict, the pencil also shows for species-less photos, and the
grid-level relay signal exists.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def test_card_pencil_emits_species_edit_requested():
    """卡片铅笔点击发射 species_edit_requested(photo)。"""
    from ui.thumbnail_grid import ThumbnailCard

    photo = {"filename": "DSC01234.ARW",
             "bird_species_cn": "红脚鹬", "bird_species_en": "Common Redshank"}
    card = ThumbnailCard(photo, 180)
    received = []
    card.species_edit_requested.connect(received.append)
    card._edit_btn.click()
    assert received and received[0]["filename"] == "DSC01234.ARW"
    card.close()


def test_card_pencil_shown_without_species():
    """无鸟种照片也有铅笔(补录场景),点击同样携带 photo。"""
    from ui.thumbnail_grid import ThumbnailCard

    photo = {"filename": "DSC09999.NEF"}
    card = ThumbnailCard(photo, 180)
    assert card._edit_btn.isVisibleTo(card)
    received = []
    card.species_edit_requested.connect(received.append)
    card._edit_btn.click()
    assert received and received[0]["filename"] == "DSC09999.NEF"
    card.close()


def test_grid_relay_signal_exists():
    """ThumbnailGrid 有 species_edit_requested 转发信号(浏览器接线点)。"""
    from ui.thumbnail_grid import ThumbnailGrid

    grid = ThumbnailGrid(get_i18n())
    assert hasattr(grid, "species_edit_requested")
    grid.close()


def test_detail_panel_pencil_emits_with_current_photo():
    """详情面板铅笔:有照片时发射 dict 副本;无照片时静默不发射。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    received = []
    panel.species_edit_requested.connect(received.append)

    # 无照片:不发射 / no photo: no emit
    panel._species_edit_btn.click()
    assert received == []

    photo = {"filename": "DSC01234.ARW", "bird_species_cn": "红脚鹬",
             "current_path": "/nonexistent/DSC01234.ARW", "rating": 2}
    panel._current_photo = photo
    panel._species_edit_btn.click()
    assert received and received[0]["filename"] == "DSC01234.ARW"
    # 发射的是副本,不是同一对象(防止下游误改面板状态)
    # A copy is emitted, not the same object.
    assert received[0] is not photo
    panel.close()


def _card_line_texts(card):
    """取候选卡片两行文本(主行, 次行);缺行返回 None。"""
    primary = getattr(card, "primary_label", None)
    secondary = getattr(card, "secondary_label", None)
    return (primary.text() if primary else None,
            secondary.text() if secondary else None)


def test_result_card_lines_follow_ui_language(monkeypatch):
    """
    候选卡片两行随界面语言(issue #106 追加反馈):
    英文界面=英文名+拉丁名(斜体),中文界面=中文名+英文名。
    测试自钉 locale,不依赖运行环境语言(项目既有教训)。

    Candidate card lines follow the UI language: EN UI shows
    English + Latin (italic); the Chinese UI keeps Chinese + English.
    The locale is pinned inside the test.
    """
    import ui.birdname_search_widget as bsw

    bird = {"chinese_name": "红脚鹬", "english_name": "Common Redshank",
            "latin_name": "Tringa totanus"}

    class _FakeI18n:
        def __init__(self, lang):
            self.current_lang = lang

        def t(self, key, **kw):
            return key

    # 英文界面 / English UI
    monkeypatch.setattr(bsw, "get_i18n", lambda: _FakeI18n("en_US"))
    card_en = bsw.BirdResultCard(dict(bird))
    p, s = _card_line_texts(card_en)
    assert p == "Common Redshank"
    assert s == "Tringa totanus"
    assert "italic" in card_en.secondary_label.styleSheet()
    card_en.close()

    # 中文界面保持原样 / Chinese UI unchanged
    monkeypatch.setattr(bsw, "get_i18n", lambda: _FakeI18n("zh_CN"))
    card_zh = bsw.BirdResultCard(dict(bird))
    p, s = _card_line_texts(card_zh)
    assert p == "红脚鹬"
    assert s == "Common Redshank"
    assert "italic" not in card_zh.secondary_label.styleSheet()
    card_zh.close()

    # 英文界面但库里缺英文名 → 回退中文名,拉丁名仍在次行
    # EN UI with missing English name falls back to Chinese.
    monkeypatch.setattr(bsw, "get_i18n", lambda: _FakeI18n("en_US"))
    card_fb = bsw.BirdResultCard({"chinese_name": "红脚鹬",
                                  "latin_name": "Tringa totanus"})
    p, s = _card_line_texts(card_fb)
    assert p == "红脚鹬" and s == "Tringa totanus"
    card_fb.close()


def test_detail_panel_species_row_order_still_holds():
    """包装鸟种行后,行序断言(Paul P0-2)仍成立。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    keys = [k for k, _ in panel._meta_rows]
    assert "browser.meta_species" in keys
    assert keys.index("browser.meta_species") < keys.index("browser.meta_gbif_rarity")
    panel.close()
