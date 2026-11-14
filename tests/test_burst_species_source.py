# -*- coding: utf-8 -*-
"""
连拍统一鸟种要让用户看得出来（#162）/ Make burst-unified species visible (#162).

现象（Al，4.6.5 RC4）：选片把一张照片识别成加州鸥，点「重新识别这张」候选里却没有
加州鸥。日志显示 `Burst #38: 3 photo(s) unified to California Gull (from _T3A2826.CR3, 36%)`
——加州鸥是整组随置信度最高的 _T3A2826 统一过来的，他点开的是组里另一帧；重新识别
只看这一帧，候选自然不同。识别没错，错在界面没说鸟种是「统一来的」，置信度也是那一帧的。

修复：report.db 新列 burst_species_source 记胜出帧文件名；详情面板与改鸟种弹窗据此说明。
用户以任何方式改了鸟种（单张、批量、整种合并、标记无鸟）都清空。

Al saw a species the Re-ID candidates did not contain: the burst had been unified
to another frame's result. The source frame is now stored and shown.
"""
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ---------------------------------------------------------------- 数据层 / data

def test_confirmed_fields_record_the_burst_source():
    """统一来的鸟种记下来源帧；置信度沿用胜出帧。"""
    from core.photo_processor import confirmed_species_db_fields

    fields = confirmed_species_db_fields("加州鸥", "California Gull", 36.0, "_T3A2826.CR3")

    assert fields["burst_species_source"] == "_T3A2826.CR3"
    assert fields["birdid_confidence"] == 36.0


def test_own_result_clears_a_stale_source():
    """照片自己的识别结论写 None：重处理后不能残留上一轮的「统一自」。"""
    from core.photo_processor import confirmed_species_db_fields

    fields = confirmed_species_db_fields("环嘴鸥", "Ring-billed Gull", 80.0, None)

    assert "burst_species_source" in fields
    assert fields["burst_species_source"] is None


def test_new_database_has_the_column(tmp_path):
    """新库建表即有此列，读写往返正常。"""
    from tools.report_db import ReportDB

    db = ReportDB(str(tmp_path))
    try:
        db.insert_photo({"filename": "_T3A2825", "has_bird": 1})
        db.update_photo("_T3A2825", {"burst_species_source": "_T3A2826.CR3"})
        assert db.get_photo("_T3A2825")["burst_species_source"] == "_T3A2826.CR3"
    finally:
        db.close()


def test_old_database_gets_the_column_backfilled(tmp_path):
    """
    旧版本建的库没有此列：打开时自动补列，已有数据不受影响（用户升级后直接打开旧目录）。
    """
    from tools.report_db import ReportDB

    db = ReportDB(str(tmp_path))
    db.insert_photo({"filename": "_T3A2825", "has_bird": 1, "bird_species_en": "California Gull"})
    db.close()
    path = os.path.join(str(tmp_path), ".superpicky", "report.db")
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE photos DROP COLUMN burst_species_source")
    conn.commit()
    conn.close()

    db = ReportDB(str(tmp_path))
    try:
        row = db.get_photo("_T3A2825")
        assert row["bird_species_en"] == "California Gull"
        assert "burst_species_source" in row and row["burst_species_source"] is None
    finally:
        db.close()


def test_manual_species_change_clears_the_source():
    """手动改鸟种（单张/批量/整种合并共用）：DB 字段与内存副本都清掉来源。"""
    from core.rating_mover import _apply_species_to_photo, _species_update_fields

    assert _species_update_fields("环嘴鸥", "Ring-billed Gull", None)["burst_species_source"] is None
    photo = {"bird_species_cn": "加州鸥", "burst_species_source": "_T3A2826.CR3"}
    _apply_species_to_photo(photo, "环嘴鸥", "Ring-billed Gull", None)
    assert photo["burst_species_source"] is None


# ---------------------------------------------------------------- 界面 / UI

@pytest.fixture
def qapp():
    """共享 QApplication / shared QApplication."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def english():
    """钉住英文界面，测完恢复 / pin the English UI, restore afterwards."""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    previous = i18n.current_lang
    i18n.switch_language("en_US")
    yield i18n
    i18n.switch_language(previous)


def _photo(source=None, species="California Gull"):
    return {"filename": "_T3A2825.CR3", "bird_species_cn": "加州鸥", "bird_species_en": species,
            "birdid_confidence": 36.0, "rating": 2, "has_bird": 1,
            "burst_species_source": source}


def test_detail_panel_names_the_source_frame(qapp, english):
    """
    详情面板：统一来的鸟种下方注明来源帧；切到普通照片时说明消失，不残留上一张的。
    """
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(english)
    try:
        panel.set_current_photo(_photo("_T3A2826.CR3"))
        assert not panel._val_species_burst.isHidden()
        assert "_T3A2826.CR3" in panel._val_species_burst.text()

        panel.set_current_photo(_photo(None))
        assert panel._val_species_burst.isHidden()
        assert panel._val_species_burst.text() == ""
    finally:
        panel.close()


def test_detail_panel_hides_source_without_species(qapp, english):
    """鸟种已被清空（如标记无鸟）时即便残留来源也不显示。"""
    from ui.detail_panel import DetailPanel

    panel = DetailPanel(english)
    try:
        photo = _photo("_T3A2826.CR3")
        photo["bird_species_cn"] = photo["bird_species_en"] = ""
        panel.set_current_photo(photo)
        assert panel._val_species_burst.isHidden()
    finally:
        panel.close()


def test_edit_dialog_explains_the_burst_before_reidentify(qapp, english, tmp_path):
    """
    **#162 核心**：对统一来的照片打开改鸟种弹窗，「重新识别这张」上方要说明鸟种来自哪一帧、
    重新识别只看这一张。
    """
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    photo_file = tmp_path / "_T3A2825.CR3"
    photo_file.write_bytes(b"raw")
    dialog = BirdSpeciesEditDialog(photo_path=str(photo_file), burst_source="_T3A2826.CR3")
    try:
        assert dialog.burst_note is not None
        assert "_T3A2826.CR3" in dialog.burst_note.text()
        assert dialog.reidentify_button is not None
    finally:
        dialog.close()


def test_edit_dialog_has_no_note_for_ordinary_or_batch_edits(qapp, english, tmp_path):
    """普通照片不显示；多选/整种合并（没有单张照片）即使传了来源也不显示。"""
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog

    photo_file = tmp_path / "_T3A2826.CR3"
    photo_file.write_bytes(b"raw")
    ordinary = BirdSpeciesEditDialog(photo_path=str(photo_file))
    batch = BirdSpeciesEditDialog(burst_source="_T3A2826.CR3")
    try:
        assert ordinary.burst_note is None
        assert batch.burst_note is None
    finally:
        ordinary.close()
        batch.close()


def test_strings_exist_in_every_language():
    """三种界面语言都有文案，且占位符一致。"""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for lang in ("zh_CN", "zh_TW", "en_US"):
        data = json.loads((root / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        for section, key in (("bird_species_edit", "burst_unified_note"),
                             ("browser", "species_burst_unified"),
                             ("browser", "species_burst_unified_tip")):
            text = data[section][key]
            assert "{source}" in text, (lang, key)
            text.format(source="_T3A2826.CR3")


def test_single_photo_edit_passes_the_source_to_the_dialog(tmp_path, monkeypatch):
    """接线：单张改鸟种把照片记录里的来源帧交给弹窗（不构造整个浏览器窗口）。"""
    import ui.bird_species_edit_dialog as bsed
    import ui.results_browser_window as rbw

    photo = tmp_path / "_T3A2825.CR3"
    photo.write_bytes(b"raw")
    captured = {}

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None, photo_path=None, burst_source=None,
                     reidentify_count=0):
            captured["burst_source"] = burst_source

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Rejected

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)
    window = rbw.ResultsBrowserWindow.__new__(rbw.ResultsBrowserWindow)
    window._session_species = lambda: []
    window._stack = None
    window._thumb_grid = None
    rbw.ResultsBrowserWindow._on_species_edit_requested(
        window, {"filename": "_T3A2825", "current_path": str(photo),
                 "bird_species_cn": "加州鸥", "burst_species_source": "_T3A2826.CR3"})

    assert captured["burst_source"] == "_T3A2826.CR3"
