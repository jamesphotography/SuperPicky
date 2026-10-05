# -*- coding: utf-8 -*-
"""
_open_submission_review 的回归测试：DB 里的 current_path/original_path 是相对
dir_path 的相对路径，必须像其他消费者一样经 _resolve_photo_paths 转成绝对路径
后再交给 SpeciesGroup/build_submission，否则 load_image 打不开文件、全部照片
被判定为"无法读取"而跳过（实测：22 张全跳、打包 0 张）。

⚠️ 隔离要点：本测试会走到 correction_consent_shown 判断分支，AdvancedConfig()
默认读写用户真实 advanced_config.json，故用临时文件隔离（同 test_correction_
consent_config.py 的做法），不碰真实用户配置。

Regression test for _open_submission_review: current_path/original_path in the
DB are relative to dir_path, and must be resolved to absolute paths via
_resolve_photo_paths (like every other consumer in this file) before being
handed to SpeciesGroup/build_submission. Otherwise load_image cannot find the
files and every photo gets skipped as "unreadable" (observed: 22/22 skipped,
0 packed).

⚠️ Isolation: this test exercises the correction_consent_shown branch.
AdvancedConfig() defaults to the user's real advanced_config.json, so an
isolated temp-file config is used (same approach as
test_correction_consent_config.py) to avoid touching the real user config.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from advanced_config import AdvancedConfig
from tools.report_db import ReportDB
from tools.i18n import get_i18n
from core.correction_tracker import CorrectionTracker
from birdid.bird_database_manager import BirdDatabaseManager
from ui.results_browser_window import ResultsBrowserWindow, _open_submission_review


def _isolated_config() -> AdvancedConfig:
    """构造指向临时文件的隔离配置实例，且预置为已展示过同意说明。"""
    fd, path = tempfile.mkstemp(suffix="_advanced_config.json")
    os.close(fd)
    os.remove(path)
    cfg = AdvancedConfig(config_file=path)
    cfg.set_correction_consent_shown(True)  # 跳过弹窗，避免测试中真的弹 QMessageBox
    return cfg


class _FakeWindow:
    """duck-type _open_submission_review 所需的最小 window，复用真实的
    _resolve_photo_paths 实现，确保测的是生产代码的路径解析逻辑。"""

    _resolve_photo_paths = ResultsBrowserWindow._resolve_photo_paths
    _is_merged = False

    def __init__(self, directory, db):
        self._directory = directory
        self._db = db
        self._correction_tracker = CorrectionTracker(db, BirdDatabaseManager())
        self.i18n = get_i18n()


def test_open_submission_review_resolves_relative_paths_to_absolute(monkeypatch):
    directory = tempfile.mkdtemp()
    db = ReportDB(directory)

    # 被改正图 + 同鸟种正样本：DB 存的都是相对 dir_path 的相对路径（真实处理管线
    # 写入的就是这种相对路径，见 core/photo_processor.py 的 os.path.relpath 调用）。
    db.insert_photo({
        "filename": "FAIL", "has_bird": 1, "rating": 3,
        "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird",
        "current_path": os.path.join("3star_excellent", "Bird", "FAIL.NEF"),
    })
    db.insert_photo({
        "filename": "GOOD_1", "has_bird": 1, "rating": 3,
        "bird_species_cn": "黄腹花蜜鸟", "bird_species_en": "Olive-backed Sunbird",
        "original_path": os.path.join("3star_excellent", "Bird", "GOOD_1.NEF"),
    })
    db.insert_correction({
        "filename": "FAIL",
        "wrong_cn": "长嘴捕蛛鸟", "wrong_en": "Little Spiderhunter",
        "corrected_model_class_id": 7447,
        "corrected_cn": "黄腹花蜜鸟", "corrected_en": "Olive-backed Sunbird",
        "birdid_confidence": 0.012,
    })

    monkeypatch.setattr("advanced_config.get_advanced_config", _isolated_config)

    captured = {}

    class _FakeDialog:
        def __init__(self, groups, out_dir, app_version, parent=None):
            captured["groups"] = groups

        def exec(self):
            return None

    monkeypatch.setattr(
        "ui.submission_review_dialog.SubmissionReviewDialog", _FakeDialog
    )

    window = _FakeWindow(directory, db)
    _open_submission_review(window)
    db.close()

    assert "groups" in captured, "SubmissionReviewDialog 未被调用"
    groups = captured["groups"]
    assert len(groups) == 1
    g = groups[0]

    failed_path = g.failed.get("current_path") or g.failed.get("original_path")
    assert os.path.isabs(failed_path), f"被改正图路径未解析为绝对路径: {failed_path!r}"
    assert failed_path == os.path.join(directory, "3star_excellent", "Bird", "FAIL.NEF")

    assert len(g.positives) == 1
    pos_path = g.positives[0].get("current_path") or g.positives[0].get("original_path")
    assert os.path.isabs(pos_path), f"正样本路径未解析为绝对路径: {pos_path!r}"
    assert pos_path == os.path.join(directory, "3star_excellent", "Bird", "GOOD_1.NEF")
