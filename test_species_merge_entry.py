# -*- coding: utf-8 -*-
"""
整种合并入口的回归测试。

右键菜单在「修改鸟种…」之后提供「把整个「红脚鹬」改为…」，调用浏览器的
_on_merge_species_requested(photo)；照片没有鸟名时该项不出现（无种可合并）。

Regression tests for the whole-species merge entry in the grid's context menu.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from tools.i18n import get_i18n

_app = QApplication.instance() or QApplication([])


def _merge_menu_items(photo: dict):
    """
    构建右键菜单（不弹出），返回 (handler 收到的 photo 列表, 匹配到的菜单项)。
    Build the context menu without exec() and trigger the merge entry.
    """
    import ui.results_browser_window as rbw

    received = []

    class _FakeBrowser(QWidget):
        def _on_merge_species_requested(self, p):
            received.append(p)

    parent = _FakeBrowser()
    i18n = get_i18n()
    species = photo.get("bird_species_cn") or photo.get("bird_species_en") or ""
    if i18n.current_lang.startswith("en"):
        species = photo.get("bird_species_en") or ""
    label = i18n.t('browser.ctx_merge_species').format(species=species)
    menu = rbw._build_context_menu(parent, photo, "/nonexistent")
    matched = [act for act in menu.actions() if act.text() == label]
    for act in matched:
        act.trigger()
    parent.close()
    return received, matched


def test_context_menu_offers_merge_for_photo_with_species():
    """有鸟名的照片：菜单含整种合并项，并携带 photo 调用 handler。"""
    photo = {"filename": "DSC01234.ARW", "current_path": "/nonexistent/DSC01234.ARW",
             "bird_species_cn": "红脚鹬", "bird_species_en": "Common Redshank"}
    received, matched = _merge_menu_items(photo)
    assert matched, "有鸟名的照片应有整种合并项"
    assert received and received[0]["filename"] == "DSC01234.ARW"


def test_context_menu_hides_merge_for_species_less_photo():
    """无鸟名的照片：没有种可合并，不出现该菜单项。"""
    photo = {"filename": "DSC09999.NEF", "current_path": "/nonexistent/DSC09999.NEF"}
    import ui.results_browser_window as rbw

    parent = QWidget()
    menu = rbw._build_context_menu(parent, photo, "/nonexistent")
    texts = [act.text() for act in menu.actions()]
    parent.close()
    merge_label_stem = get_i18n().t('browser.ctx_merge_species').split("{")[0]
    assert not any(t.startswith(merge_label_stem) and merge_label_stem for t in texts), \
        f"无鸟名照片不应出现整种合并项，实际菜单：{texts}"


# ── 同鸟种照片收集 ───────────────────────────────────────────────────────────

def test_collects_only_photos_of_the_same_species():
    """按中文鸟名收集：其它鸟种不得混入。"""
    from ui.results_browser_window import _photos_of_same_species

    target = {"filename": "A.NEF", "bird_species_cn": "白鹭", "bird_species_en": "Little Egret"}
    pool = [
        target,
        {"filename": "B.NEF", "bird_species_cn": "白鹭", "bird_species_en": "Little Egret"},
        {"filename": "C.NEF", "bird_species_cn": "苍鹭", "bird_species_en": "Grey Heron"},
        {"filename": "D.NEF"},
    ]

    result = _photos_of_same_species(pool, target)

    assert [p["filename"] for p in result] == ["A.NEF", "B.NEF"]


def test_falls_back_to_english_name_when_chinese_missing():
    """只有英文鸟名的记录按英文名匹配（英文环境处理的批次）。"""
    from ui.results_browser_window import _photos_of_same_species

    target = {"filename": "A.NEF", "bird_species_en": "Little Egret"}
    pool = [
        target,
        {"filename": "B.NEF", "bird_species_en": "Little Egret"},
        {"filename": "C.NEF", "bird_species_en": "Grey Heron"},
    ]

    result = _photos_of_same_species(pool, target)

    assert [p["filename"] for p in result] == ["A.NEF", "B.NEF"]


# ── 目标目录预览 ─────────────────────────────────────────────────────────────

def _pin_zh():
    """钉住中文 locale：断言里是中文星等目录名。Pin zh_CN for folder names."""
    i18n = get_i18n()
    if not i18n.current_lang.startswith("zh"):
        i18n.switch_language("zh_CN")


def test_target_folder_preview_lists_each_rating_folder_once():
    """确认弹窗的目录预览：每个星等目录只列一次，按字典序排列。"""
    _pin_zh()
    from ui.results_browser_window import _merge_target_folders

    photos = [
        {"filename": "A.NEF", "rating": 3, "_base_dir": "/batch",
         "current_path": "/batch/白鹭/3星_优选/A.NEF"},
        {"filename": "B.NEF", "rating": 3, "_base_dir": "/batch",
         "current_path": "/batch/白鹭/3星_优选/B.NEF"},
        {"filename": "C.NEF", "rating": 2, "_base_dir": "/batch",
         "current_path": "/batch/白鹭/2星_良好/C.NEF"},
    ]

    folders = _merge_target_folders(photos, "中白鹭", "species-first")

    assert folders == [
        os.path.join("中白鹭", "2星_良好"),
        os.path.join("中白鹭", "3星_优选"),
    ]


def test_target_folder_preview_skips_unorganized_root_photos():
    """未整理（还在根目录）的照片不会被移动，预览里也不该出现它的目录。"""
    _pin_zh()
    from ui.results_browser_window import _merge_target_folders

    photos = [
        {"filename": "A.NEF", "rating": 3, "_base_dir": "/batch",
         "current_path": "/batch/A.NEF"},
    ]

    assert _merge_target_folders(photos, "中白鹭", "species-first") == []


# ── 失败原因本地化 ───────────────────────────────────────────────────────────

def test_reason_code_target_exists_is_localized():
    """core 回传的稳定原因代码在 UI 层翻成人话，而不是把代码露给用户。"""
    _pin_zh()
    from ui.results_browser_window import _merge_reason_text

    text = _merge_reason_text("target_exists")

    assert text == get_i18n().t('browser.merge_reason_target_exists')
    assert "target_exists" not in text


def test_reason_code_move_error_keeps_exception_detail():
    """移动异常保留异常类型，方便用户/日志定位（权限、磁盘满等）。"""
    _pin_zh()
    from ui.results_browser_window import _merge_reason_text

    text = _merge_reason_text("move_error:PermissionError")

    assert "PermissionError" in text
