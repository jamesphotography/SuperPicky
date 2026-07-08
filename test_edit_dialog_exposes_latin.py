# -*- coding: utf-8 -*-
"""BirdSpeciesEditDialog 确认后应暴露 selected_latin。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

_app = QApplication.instance() or QApplication([])


def test_confirm_sets_selected_latin():
    dlg = BirdSpeciesEditDialog()
    dlg._selected_data = {
        "chinese_name": "棕脸鹟莺",
        "english_name": "Rufous-faced Warbler",
        "latin_name": "Abroscopus albogularis",
    }
    dlg._on_confirm()
    assert dlg.selected_cn == "棕脸鹟莺"
    assert dlg.selected_latin == "Abroscopus albogularis"
