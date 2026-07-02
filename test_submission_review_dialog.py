# -*- coding: utf-8 -*-
"""SubmissionReviewDialog.groups_to_items 纯逻辑单测。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ui.submission_review_dialog import SubmissionReviewDialog, SpeciesGroup

_app = QApplication.instance() or QApplication([])


def _g():
    failed = {"filename": "FAIL", "original_path": "/x/FAIL.NEF"}
    pos = [
        {"filename": "P1", "original_path": "/x/P1.NEF"},
        {"filename": "P2", "current_path": "/x/P2.jpg"},
    ]
    return SpeciesGroup(corrected_cn="棕脸鹟莺", corrected_en="Rufous-faced Warbler",
                        model_class_id=7447, failed=failed, positives=pos)


def test_groups_to_items_selected_only_and_flags_failed():
    g = _g()
    items = SubmissionReviewDialog.groups_to_items([g], selected_keys={"FAIL", "P1"})
    keys = {os.path.basename(i.photo_path): i for i in items}
    assert set(keys) == {"FAIL.NEF", "P1.NEF"}       # P2 未勾选被排除
    assert keys["FAIL.NEF"].is_the_failed_one is True
    assert keys["P1.NEF"].is_the_failed_one is False
    assert keys["FAIL.NEF"].model_class_id == 7447
    assert keys["FAIL.NEF"].chinese == "棕脸鹟莺"


def test_group_without_class_id_is_skipped():
    g = _g()
    g.model_class_id = None
    items = SubmissionReviewDialog.groups_to_items([g], selected_keys={"FAIL", "P1"})
    assert items == []
