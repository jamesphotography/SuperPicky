# -*- coding: utf-8 -*-
"""
在「修改鸟种」弹窗里重新识别这张照片 / Re-identify a photo from the species dialog.

用户 2026-09-23 提出：选片结果里归入「其他鸟类」的照片，或者识别结果不满意时，
应该能让模型**重新识别一次**，而不是只能手工搜索鸟名。

为什么重跑会得到不同结果（不是白跑一遍）：主处理流程调
``identify_bird(preloaded_crop=选片阶段 YOLO 裁好的框)``，直接复用那个裁切图；
而单张识别传 ``preloaded_crop=None``，会**全图重跑 YOLO** 并读对焦点在多只鸟里
挑。「框错了鸟」「框得不完整」这两类错误因此有机会被纠正——历史上竖拍 RAW 那次
正是栽在这条差异上（长嘴捕蛛鸟 1% → 黄腹花蜜鸟 99.8%）。

做进现有弹窗而不是新开窗口：用户的最终动作就是「把这张改成正确的鸟种」，而那条
写入链路（确认 → 写库 → 移文件 → 写元数据 → 刷三份缓存 → 重查鸟种级属性）已经
完整。重新识别只是**换一种产生候选的方式**，与搜索并列，不该复制一条写入链路。

识别结果自带 cn_name / en_name / scientific_name，正好对上确认时需要的三个字段，
所以这条路径不经过名录库——「识别出的鸟种不在名录里」的问题在这里不会发生。

Re-identification is offered inside the existing species dialog: it is just
another way to produce candidates, so the whole confirm/write path is reused.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


def _identify_result(**over):
    """造一条 identify_bird 的结果条目 / One identify_bird result entry."""
    base = {
        "class_id": 6380,
        "cn_name": "鵙雀鹟",
        "en_name": "Crested Shriketit",
        "scientific_name": "Falcunculus frontatus",
        "iucn_category": "LC",
        "gbif_rarity_100": 32.5,
        "aesthetic_index": 55.0,
        "confidence": 82.97,
        "ebird_code": "creshr1",
        "region_match": True,
        "description": "",
    }
    base.update(over)
    return base


# ----------------------------------------------------------------------
#  纯映射：识别结果 → 候选卡片的数据形状
# ----------------------------------------------------------------------

def test_identify_result_maps_onto_the_card_shape():
    """
    识别结果要能直接喂给现成的候选卡片与确认逻辑。

    卡片和 `_on_confirm` 读的是 chinese_name / english_name / latin_name，
    而识别结果用的是 cn_name / en_name / scientific_name——差一层命名，
    这个函数就是那层翻译。
    """
    from ui.bird_species_edit_dialog import identify_result_to_bird_data

    data = identify_result_to_bird_data(_identify_result())

    assert data["chinese_name"] == "鵙雀鹟"
    assert data["english_name"] == "Crested Shriketit"
    assert data["latin_name"] == "Falcunculus frontatus"


def test_mapping_keeps_the_confidence_for_display():
    """置信度要留着：用户要靠它判断这次重跑值不值得信。"""
    from ui.bird_species_edit_dialog import identify_result_to_bird_data

    assert identify_result_to_bird_data(_identify_result())["confidence"] == 82.97


def test_mapping_tolerates_a_result_without_a_scientific_name():
    """
    学名缺失不得让整条候选失效。

    识别结果的学名来自参考库，少数 class_id 查不到；此时仍应把这条列出来
    （用户 2026-09-23 明确要求候选全给出来让他自己选），只是学名为空。
    """
    from ui.bird_species_edit_dialog import identify_result_to_bird_data

    data = identify_result_to_bird_data(
        _identify_result(scientific_name=None, en_name=None))

    assert data["chinese_name"] == "鵙雀鹟"
    assert data["latin_name"] == ""
    assert data["english_name"] == ""


# ----------------------------------------------------------------------
#  弹窗行为
# ----------------------------------------------------------------------

def _dialog(**kw):
    from ui.bird_species_edit_dialog import BirdSpeciesEditDialog
    return BirdSpeciesEditDialog(**kw)


def test_button_is_absent_without_a_photo_path(tmp_path):
    """
    没传照片路径时不显示「重新识别」按钮。

    弹窗还被整种合并、多选批量等入口复用，那些场景没有「这一张」可识别，
    按钮出现在那里只会让人困惑。
    """
    dialog = _dialog()
    try:
        assert dialog.reidentify_button is None
    finally:
        dialog.deleteLater()


def test_button_appears_when_a_photo_path_is_given(tmp_path):
    """传了照片路径就提供「重新识别」。"""
    photo = tmp_path / "DSC_0001.NEF"
    photo.write_bytes(b"raw")

    dialog = _dialog(photo_path=str(photo))
    try:
        assert dialog.reidentify_button is not None
        assert dialog.reidentify_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_button_is_absent_when_the_file_is_gone(tmp_path):
    """
    照片文件不在了就不显示按钮。

    库里有记录但文件被移走/删掉是常见状态，点了必然失败，不如不给。
    """
    dialog = _dialog(photo_path=str(tmp_path / "nope.NEF"))
    try:
        assert dialog.reidentify_button is None
    finally:
        dialog.deleteLater()


def test_results_become_selectable_candidates(tmp_path):
    """
    识别结果渲染成候选卡片，选中后产出正确的三个字段。

    这是整条链路的关键接缝：选中的候选最终要交给改鸟种流程写库与移文件。
    """
    photo = tmp_path / "DSC_0001.NEF"
    photo.write_bytes(b"raw")
    dialog = _dialog(photo_path=str(photo))
    try:
        dialog._show_identify_results({
            "success": True,
            "results": [
                _identify_result(cn_name="北鵙雀鹟", en_name="Northern Shriketit",
                                 scientific_name="Falcunculus whitei", confidence=61.2),
                _identify_result(),
            ],
        })

        assert len(dialog._cards) == 2
        dialog._on_card_selected(dialog._cards[0].bird_data)
        dialog._on_confirm()

        assert dialog.selected_cn == "北鵙雀鹟"
        assert dialog.selected_en == "Northern Shriketit"
        assert dialog.selected_latin == "Falcunculus whitei"
    finally:
        dialog.deleteLater()


def test_low_confidence_candidates_are_still_offered(tmp_path):
    """
    低置信度的候选也必须列出来（用户 2026-09-23 拍板）。

    「其他鸟类」里的照片本来就是低置信度，如果按阈值过滤，用户点一次重新识别
    会得到一个空列表——那正是他要解决的处境。全部给出、标明置信度，由他判断。
    """
    photo = tmp_path / "DSC_0001.NEF"
    photo.write_bytes(b"raw")
    dialog = _dialog(photo_path=str(photo))
    try:
        dialog._show_identify_results({
            "success": True,
            "results": [_identify_result(confidence=3.1),
                        _identify_result(cn_name="灰雀鹟", confidence=1.8)],
        })

        assert len(dialog._cards) == 2
    finally:
        dialog.deleteLater()


def test_empty_result_does_not_wipe_the_dialog(tmp_path):
    """
    识别不出任何候选时给出提示，而不是留一个空白列表让人以为卡住了。
    """
    photo = tmp_path / "DSC_0001.NEF"
    photo.write_bytes(b"raw")
    dialog = _dialog(photo_path=str(photo))
    try:
        dialog._show_identify_results({"success": True, "results": []})

        assert dialog._cards == []
        assert dialog._empty_label.isVisible() or dialog._empty_label.text()
    finally:
        dialog.deleteLater()


# ----------------------------------------------------------------------
#  接线：浏览器把「这一张」的路径传进弹窗
# ----------------------------------------------------------------------

def test_single_photo_edit_passes_the_photo_path(tmp_path, monkeypatch):
    """
    单张改鸟种时把照片路径传给弹窗，否则按钮永远不会出现。

    只断言「传了什么」而不去构造整个浏览器窗口：这里要钉的就是接线本身。
    """
    import ui.bird_species_edit_dialog as bsed
    import ui.results_browser_window as rbw

    photo = tmp_path / "DSC_0001.NEF"
    photo.write_bytes(b"raw")
    captured = {}

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None, photo_path=None):
            captured["photo_path"] = photo_path
            self.selected_cn = self.selected_en = self.selected_latin = ""

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Rejected

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)

    window = rbw.ResultsBrowserWindow.__new__(rbw.ResultsBrowserWindow)
    window._session_species = lambda: []
    window._stack = None
    window._thumb_grid = None
    rbw.ResultsBrowserWindow._on_species_edit_requested(
        window, {"filename": "DSC_0001", "current_path": str(photo),
                 "bird_species_cn": "鵙雀鹟"})

    assert captured["photo_path"] == str(photo)


def test_whole_species_merge_does_not_offer_re_identification(tmp_path, monkeypatch):
    """
    整种合并不传路径——它作用于该鸟种的**全部**照片，没有「这一张」可重识别。
    """
    import ui.bird_species_edit_dialog as bsed
    import ui.results_browser_window as rbw

    captured = {}

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None, photo_path=None):
            captured["photo_path"] = photo_path

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Rejected

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)

    window = rbw.ResultsBrowserWindow.__new__(rbw.ResultsBrowserWindow)
    window._session_species = lambda: []
    window._db = object()
    window._all_photos = []
    window._resolve_photo_paths = lambda p: p
    window.i18n = __import__("tools.i18n", fromlist=["get_i18n"]).get_i18n()
    rbw.ResultsBrowserWindow._on_merge_species_requested(
        window, {"bird_species_cn": "鵙雀鹟", "bird_species_en": "Crested Shriketit"})

    assert captured.get("photo_path", "sentinel") in (None, "sentinel")
