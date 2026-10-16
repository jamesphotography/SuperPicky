# -*- coding: utf-8 -*-
"""core/correction_tracker.py 单测（用假 db manager，隔离真库）。"""
import tempfile

from tools.report_db import ReportDB
from core.correction_tracker import CorrectionTracker


class _FakeBirdDB:
    """假 model_class_id 反查器：只认预置映射。"""
    def __init__(self, mapping):
        self._m = mapping

    def get_class_id_by_scientific_name(self, latin, english_name=None):
        return self._m.get(latin)


def _tracker():
    d = tempfile.mkdtemp()
    db = ReportDB(d)
    fake = _FakeBirdDB({"Cinnyris jugularis": 7447})
    return CorrectionTracker(db, fake), db


def test_record_correction_resolves_and_persists():
    tracker, db = _tracker()
    cid = tracker.record_correction(
        filename="IMG_9", wrong_cn="长嘴捕蛛鸟", wrong_en="Little Spiderhunter",
        corrected_cn="黄腹花蜜鸟", corrected_en="Olive-backed Sunbird",
        corrected_latin="Cinnyris jugularis", birdid_confidence=0.01,
    )
    assert cid == 7447
    rows = db.get_corrections()
    assert len(rows) == 1
    assert rows[0]["corrected_model_class_id"] == 7447
    assert rows[0]["wrong_cn"] == "长嘴捕蛛鸟"
    db.close()


def test_record_correction_unresolved_writes_none():
    tracker, db = _tracker()
    cid = tracker.record_correction(
        filename="IMG_X", wrong_cn="甲", wrong_en="A",
        corrected_cn="乙", corrected_en="B", corrected_latin="Unknown sp",
    )
    assert cid is None
    assert db.get_corrections()[0]["corrected_model_class_id"] is None
    db.close()


def test_find_positive_samples_excludes_self():
    tracker, db = _tracker()
    db.insert_photo({"filename": "P1", "has_bird": 1, "rating": 3,
                     "bird_species_cn": "黄腹花蜜鸟", "original_path": "/x/P1.NEF"})
    db.insert_photo({"filename": "SELF", "has_bird": 1, "rating": 3,
                     "bird_species_cn": "黄腹花蜜鸟", "original_path": "/x/SELF.NEF"})
    got = tracker.find_positive_samples("黄腹花蜜鸟", None, exclude_filename="SELF")
    assert [p["filename"] for p in got] == ["P1"]
    db.close()
