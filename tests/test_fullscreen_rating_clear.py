# -*- coding: utf-8 -*-
"""
#151 回归测试：全屏顶条从 N★ 照片切到 0★ 照片时，星级图必须被清掉。

QLabel 显示 pixmap 时文字为空，setText("") 会因「文字没变」直接返回，
旧星级图因此留在顶条上；上一张 / 下一张翻到 0★ 照片也是同一条路径。

#151: switching the fullscreen top strip from an N-star photo to a 0-star
photo must clear the star pixmap (setText("") was a no-op).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def viewer():
    from tools.i18n import get_i18n
    from ui.fullscreen_viewer import FullscreenViewer
    v = FullscreenViewer(get_i18n())
    yield v
    v.deleteLater()


@pytest.mark.parametrize("next_rating", [0, -1, None])
def test_low_rating_clears_previous_stars(viewer, next_rating):
    viewer.update_rating_display({"rating": 3})
    assert not viewer._rating_label.pixmap().isNull()
    viewer.update_rating_display({"rating": next_rating})
    assert viewer._rating_label.pixmap().isNull()


def test_crown_then_zero_clears(viewer):
    viewer.update_rating_display({"rating": 3, "picked": 1})
    viewer.update_rating_display({"rating": 0, "picked": 0})
    assert viewer._rating_label.pixmap().isNull()
