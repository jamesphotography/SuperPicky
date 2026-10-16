# -*- coding: utf-8 -*-
"""
issue #106 回归测试:鸟种编辑/补录入口。

入口现状(RC8 起,原网格卡片常驻铅笔已移除):
- grid 卡片:右键菜单「修改鸟种…」项 → 调用浏览器 _on_species_edit_requested(photo);
  对所有照片可用(有鸟名=纠错,无鸟名=人工补录)。
- 详情面板:铅笔按钮 → DetailPanel.species_edit_requested(保留,未变)。
- 卡片本身不再暴露 _edit_btn / species_edit_requested,标签回归单行干净显示。

Regression tests for the issue #106 species-edit entries. Since RC8 the grid
card's always-on pencil is gone; the entry lives in the tile's right-click
menu ("Edit Species…") and invokes the browser's _on_species_edit_requested
handler. The detail-panel pencil is unchanged.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def _invoke_context_menu(photo: dict):
    """
    构建 grid 右键菜单(不弹出)并模拟点击「修改鸟种…」项,返回 handler 收到的
    photo 列表。用 _build_context_menu 拿到 QMenu(避免 exec 阻塞),从而验证
    菜单确实含该项且已正确接线到浏览器 _on_species_edit_requested。
    """
    import ui.results_browser_window as rbw

    received = []

    class _FakeBrowser(QWidget):
        def _on_species_edit_requested(self, p):
            received.append(p)

    parent = _FakeBrowser()
    label = get_i18n().t('browser.ctx_edit_species')
    menu = rbw._build_context_menu(parent, photo, "/nonexistent")
    matched = [act for act in menu.actions() if act.text() == label]
    for act in matched:
        act.trigger()
    parent.close()
    return received, matched


def test_context_menu_species_edit_invokes_handler():
    """右键菜单「修改鸟种…」→ 携带 photo 调用浏览器 handler。"""
    photo = {"filename": "DSC01234.ARW", "current_path": "/nonexistent/DSC01234.ARW",
             "bird_species_cn": "红脚鹬", "bird_species_en": "Common Redshank"}
    received, matched = _invoke_context_menu(photo)
    assert matched, "菜单应含「修改鸟种…」项"
    assert received and received[0]["filename"] == "DSC01234.ARW"


def test_context_menu_species_edit_for_species_less_photo():
    """无鸟种照片:菜单同样提供「修改鸟种…」(人工补录场景)。"""
    photo = {"filename": "DSC09999.NEF", "current_path": "/nonexistent/DSC09999.NEF"}
    received, matched = _invoke_context_menu(photo)
    assert matched, "无鸟种照片也应有「修改鸟种…」项"
    assert received and received[0]["filename"] == "DSC09999.NEF"


def test_card_and_grid_have_no_stale_pencil():
    """卡片/网格不再暴露已废弃的铅笔按钮与转发信号(锁定 RC8 重构)。"""
    from ui.thumbnail_grid import ThumbnailCard, ThumbnailGrid

    card = ThumbnailCard({"filename": "DSC01234.ARW"}, 180)
    assert not hasattr(card, "_edit_btn")
    assert not hasattr(card, "species_edit_requested")
    card.close()

    grid = ThumbnailGrid(get_i18n())
    assert not hasattr(grid, "species_edit_requested")
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
