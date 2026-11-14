# -*- coding: utf-8 -*-
"""
多选重新识别（#163）/ Multi-photo re-identification (#163).

Al：只选一张时「改鸟种」弹窗里有「重新识别这张」，选两张以上就没有了。

用户 2026-11-14 拍板：
- 结果**先核对再写入**（一次改几十张，错了很难发现）；
- 每张各出各的结果，不做连拍统一。

既有约束：改鸟种以连拍组为单位整组执行，所以同一组被选成不同鸟种时必须拦住。

Al could only re-identify one photo at a time. Now several can be re-identified,
reviewed per photo, then applied; differing choices within a burst are blocked.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_app = QApplication.instance() or QApplication([])

from core.batch_reidentify import (  # noqa: E402
    ReidRow,
    build_plans,
    default_choice,
    find_burst_conflicts,
    is_change,
)


def _cand(cn, en, conf, latin=""):
    return {"chinese_name": cn, "english_name": en, "latin_name": latin, "confidence": conf}


GULL_RB = _cand("环嘴鸥", "Ring-billed Gull", 82, "Larus delawarensis")
GULL_CA = _cand("加州鸥", "California Gull", 12, "Larus californicus")
HERRING = _cand("银鸥", "Herring Gull", 75, "Larus argentatus")


def _photo(name, cn="加州鸥", en="California Gull", burst=None, base=""):
    return {"filename": name, "current_path": f"/x/{name}.CR3", "bird_species_cn": cn,
            "bird_species_en": en, "burst_id": burst, "_base_dir": base}


# ---------------------------------------------------------------- 纯逻辑 / logic

def test_default_picks_a_confident_different_top_result():
    """第一候选过阈值且与当前不同 → 默认选它。"""
    assert default_choice([GULL_RB, GULL_CA], _photo("a"), 50) == 0


def test_default_keeps_current_below_threshold():
    """低于阈值不替用户做主（与选片阶段同一个阈值），候选仍会列出。"""
    weak = _cand("环嘴鸥", "Ring-billed Gull", 40)
    assert default_choice([weak], _photo("a"), 50) is None


def test_default_keeps_current_when_same_species():
    """识别结果与当前一致 → 保持不变（不算修改）。"""
    same = _cand("加州鸥", "California Gull", 90)
    assert default_choice([same], _photo("a"), 50) is None
    assert default_choice([], _photo("a"), 50) is None


def test_build_plans_groups_by_new_species_and_skips_kept():
    """按新鸟种分组；保持不变、改成同一种的都不进计划。"""
    a = ReidRow(_photo("a"), [GULL_RB], 0, "done")
    b = ReidRow(_photo("b"), [HERRING], 0, "done")
    c = ReidRow(_photo("c"), [GULL_RB], 0, "done")
    kept = ReidRow(_photo("d"), [GULL_RB], None, "done")
    same = ReidRow(_photo("e"), [_cand("加州鸥", "California Gull", 90)], 0, "done")

    plans = build_plans([a, b, c, kept, same])

    assert [(len(ps), en) for ps, _cn, en, _l in plans] == [(2, "Ring-billed Gull"), (1, "Herring Gull")]
    assert plans[0][3] == "Larus delawarensis"
    assert not is_change(kept) and not is_change(same)


def test_burst_with_different_choices_is_a_conflict():
    """
    同一连拍组：一张改成环嘴鸥、另一张保持加州鸥 → 冲突（整组会被一起改，后写覆盖先写）。
    """
    a = ReidRow(_photo("a", burst=38), [GULL_RB], 0, "done")
    b = ReidRow(_photo("b", burst=38), [GULL_RB], None, "done")

    assert len(find_burst_conflicts([a, b])) == 1


def test_burst_with_matching_choices_is_fine():
    """同组都改成同一种 → 不冲突；只是都保持不变也不冲突。"""
    a = ReidRow(_photo("a", burst=38), [GULL_RB], 0, "done")
    b = ReidRow(_photo("b", burst=38), [GULL_RB], 0, "done")
    c = ReidRow(_photo("c", burst=39), [GULL_RB], None, "done")
    d = ReidRow(_photo("d", burst=39), [HERRING], None, "done")

    assert find_burst_conflicts([a, b, c, d]) == []


def test_same_burst_id_in_different_batches_is_not_one_burst():
    """合并库里不同批次目录的 burst_id 各自编号，不能串成一组。"""
    a = ReidRow(_photo("a", burst=1, base="/day1"), [GULL_RB], 0, "done")
    b = ReidRow(_photo("b", burst=1, base="/day2"), [HERRING], 0, "done")

    assert find_burst_conflicts([a, b]) == []


# ---------------------------------------------------------------- 弹窗 / dialog

@pytest.fixture
def english():
    from tools.i18n import get_i18n
    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("en_US")
    yield i18n
    i18n.switch_language(previous)


def _result(*cands):
    """identify_bird 形状的结果 / an identify_bird-shaped result."""
    return {"success": True, "results": [
        {"cn_name": c["chinese_name"], "en_name": c["english_name"],
         "scientific_name": c["latin_name"], "confidence": c["confidence"]} for c in cands]}


class _StartedWorker:
    """视为「已开始、未在跑」的替身 / stand-in for a started, idle worker."""

    @staticmethod
    def isRunning():
        return False

    @staticmethod
    def cancel():
        pass


def _dialog(photos, threshold=50):
    from ui.batch_reidentify_dialog import BatchReidentifyDialog
    return BatchReidentifyDialog(photos, threshold=threshold, autostart=False)


def test_dialog_applies_only_after_review(english):
    """
    逐张结果到齐后才能「应用」；默认选择按规则；生成的计划每张各自的鸟种。
    """
    dlg = _dialog([_photo("a"), _photo("b"), _photo("c")])
    try:
        assert not dlg.apply_button.isEnabled()
        dlg._worker = _StartedWorker()               # 视为已开始 / treat as started
        dlg.apply_result(0, _result(GULL_RB, GULL_CA))
        dlg.apply_result(1, _result(HERRING))
        assert not dlg.apply_button.isEnabled(), "还有一张没识别完就能应用"
        dlg.apply_error(2, "BirdID: boom")
        assert dlg.apply_button.isEnabled()
        assert "2" in dlg.apply_button.text()

        # 用户把第二张改回「保持不变」
        dlg._combos[1].setCurrentIndex(0)
        dlg._on_apply()

        assert dlg.result() == QDialog.Accepted
        assert [(len(ps), en) for ps, _cn, en, _l in dlg.plans] == [(1, "Ring-billed Gull")]
    finally:
        dlg.close()


def test_dialog_lists_low_confidence_candidates_but_keeps_current(english):
    """低置信度：候选照样列出（用户可自己选），默认保持不变。"""
    dlg = _dialog([_photo("a")], threshold=50)
    try:
        dlg._worker = _StartedWorker()
        dlg.apply_result(0, _result(_cand("环嘴鸥", "Ring-billed Gull", 30)))
        assert dlg._combos[0].count() == 2
        assert dlg._combos[0].currentIndex() == 0
        assert not dlg.apply_button.isEnabled()
        dlg._combos[0].setCurrentIndex(1)
        assert dlg.apply_button.isEnabled()
    finally:
        dlg.close()


def test_dialog_blocks_apply_on_burst_conflict(english):
    """同一连拍组选成不同鸟种 → 提示并禁用「应用」；改一致后恢复。"""
    dlg = _dialog([_photo("a", burst=38), _photo("b", burst=38)])
    try:
        dlg._worker = _StartedWorker()
        dlg.apply_result(0, _result(GULL_RB))
        dlg.apply_result(1, _result(HERRING))
        assert not dlg.conflict_label.isHidden()
        assert not dlg.apply_button.isEnabled()
        assert not dlg.burst_note.isHidden()

        dlg._combos[1].setCurrentIndex(0)            # 保持不变 = 加州鸥，仍与 a 不同
        assert not dlg.apply_button.isEnabled()
        dlg._combos[0].setCurrentIndex(0)            # 两张都保持不变 → 无修改、无冲突
        assert dlg.conflict_label.isHidden()
        dlg._combos[1].setCurrentIndex(1)
        dlg._combos[0].setCurrentIndex(0)
        assert not dlg.apply_button.isEnabled()
    finally:
        dlg.close()


def test_worker_reports_every_photo_and_survives_failures(monkeypatch, tmp_path):
    """后台线程：每张一个信号；文件不在、识别抛错都只影响那一张。"""
    import birdid.bird_identifier as bi
    from ui.batch_reidentify_dialog import _BatchIdentifyWorker

    ok = tmp_path / "a.CR3"
    ok.write_bytes(b"x")
    boom = tmp_path / "b.CR3"
    boom.write_bytes(b"x")

    def fake_identify(path, **kwargs):
        if path.endswith("b.CR3"):
            raise RuntimeError("model")
        return _result(GULL_RB)

    monkeypatch.setattr(bi, "identify_bird", fake_identify)
    worker = _BatchIdentifyWorker([str(ok), str(boom), str(tmp_path / "missing.CR3")], {})
    done, failed = [], []
    worker.row_done.connect(lambda i, r: done.append(i))
    worker.row_failed.connect(lambda i, e: failed.append((i, e)))

    worker.run()

    assert done == [0]
    assert [i for i, _ in failed] == [1, 2]
    assert "BirdID" in failed[0][1]


# ---------------------------------------------------------------- 入口 / entry

def test_edit_dialog_offers_batch_reidentify_for_multi_select(english):
    """多选打开改鸟种弹窗：有「重新识别这 N 张」，点了关闭弹窗并置标志。"""
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    dlg = BirdSpeciesEditDialog(reidentify_count=3)
    try:
        assert dlg.reidentify_button is not None
        assert "3" in dlg.reidentify_button.text()
        dlg.reidentify_button.click()
        assert dlg.batch_reidentify is True
        assert dlg.result() == QDialog.Accepted
    finally:
        dlg.close()


def test_whole_species_merge_has_no_batch_reidentify(english):
    """整种合并不传张数 → 没有这个按钮（合并面对的是整个鸟种，不是勾选集）。"""
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    dlg = BirdSpeciesEditDialog()
    try:
        assert dlg.reidentify_button is None
    finally:
        dlg.close()


def test_batch_edit_routes_to_reidentify(monkeypatch):
    """多选改鸟种：用户点了「重新识别这 N 张」→ 走 _batch_reidentify，不走统一改鸟种。"""
    import ui.bird_species_edit_dialog as bsed
    import ui.results_browser_window as rbw

    seen = {}

    class _Stub:
        def __init__(self, parent=None, session_species=None, exclude_species=None,
                     photo_path=None, burst_source=None, reidentify_count=0):
            seen["count"] = reidentify_count
            self.batch_reidentify = True

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)
    window = rbw.ResultsBrowserWindow.__new__(rbw.ResultsBrowserWindow)
    window._db = object()
    window.i18n = __import__("tools.i18n", fromlist=["get_i18n"]).get_i18n()
    window._session_species = lambda: []
    called = {}
    window._batch_reidentify = lambda targets: called.setdefault("targets", targets)
    targets = [_photo("a"), _photo("b")]

    rbw.ResultsBrowserWindow._batch_species_edit(window, targets)

    assert seen["count"] == 2
    assert called["targets"] is targets


# ---------------------------------------------------------------- 端到端 / end to end

@pytest.fixture
def chinese():
    from tools.i18n import get_i18n
    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("zh_CN")
    yield i18n
    i18n.switch_language(previous)


def test_each_photo_gets_its_own_species(tmp_path, monkeypatch, chinese):
    """
    真实浏览器 + 真实库：两张照片核对后分别改成不同鸟种，第三张保持不变；
    一次进度、一份报告，纠错样本逐张记录。
    """
    import ui.batch_reidentify_dialog as brd
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB
    from ui.custom_dialogs import StyledMessageBox

    root = str(tmp_path)
    for name in ("A1", "A2", "A3"):
        rel = f"加州鸥/2星_良好/{name}.CR3"
        os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
        open(os.path.join(root, rel), "w").close()
    db = ReportDB(root)
    for name in ("A1", "A2", "A3"):
        rel = f"加州鸥/2星_良好/{name}.CR3"
        db.insert_photo({"filename": name, "current_path": rel, "original_path": rel,
                         "bird_species_cn": "加州鸥", "bird_species_en": "California Gull",
                         "rating": 2, "has_bird": 1})
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    targets = sorted(win._filtered_photos, key=lambda p: p["filename"])

    class _Review:
        def __init__(self, photos, threshold, parent=None):
            self.plans = [([photos[0]], "环嘴鸥", "Ring-billed Gull", ""),
                          ([photos[1]], "银鸥", "Herring Gull", "")]

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(brd, "BatchReidentifyDialog", _Review)
    reports = []
    monkeypatch.setattr(StyledMessageBox, "information",
                        staticmethod(lambda *a, **k: reports.append(a)))
    corrections = []
    monkeypatch.setattr(win, "_record_correction",
                        lambda p, cn, en, latin: corrections.append((p["filename"], en)))
    monkeypatch.setattr(win, "_write_species_metadata_in_background", lambda *a, **k: None)
    try:
        win._batch_reidentify(targets)

        db = ReportDB(root)
        try:
            got = {n: db.get_photo(n)["bird_species_en"] for n in ("A1", "A2", "A3")}
        finally:
            db.close()
        assert got == {"A1": "Ring-billed Gull", "A2": "Herring Gull", "A3": "California Gull"}
        assert sorted(corrections) == [("A1", "Ring-billed Gull"), ("A2", "Herring Gull")]
        assert len(reports) == 1, "每组各弹一次报告了"
    finally:
        win.close()
