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


def test_detail_panel_species_row_order_still_holds():
    """包装鸟种行后,行序断言(Paul P0-2)仍成立。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(get_i18n())
    keys = [k for k, _ in panel._meta_rows]
    assert "browser.meta_species" in keys
    assert keys.index("browser.meta_species") < keys.index("browser.meta_gbif_rarity")
    panel.close()
