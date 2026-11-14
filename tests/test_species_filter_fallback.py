# -*- coding: utf-8 -*-
"""
选中的鸟种消失后，网格必须跟着下拉退回「全部鸟种」（#157）。

现网症状：鸟种下拉选着「苍鹭」（只有一张），把这张改成「大蓝鹭」后，下拉自动
显示「全部鸟种」，网格却一张都不显示；改一下星级筛选照片才出来。

根因：FilterPanel.update_species_list 重建下拉时屏蔽了信号，旧选中项不在新列表里
就静默退回第 0 项「全部」，不发 filters_changed；网格仍按旧鸟种过滤。

When the selected species disappears (its last photo was re-identified), the
combo silently fell back to "All" with signals blocked, so the grid kept
filtering by the vanished species and showed nothing (#157).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """钉住中文界面：断言用中文鸟名，目录命名跟随全局 i18n。/ Pin zh_CN."""
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _join_worker_threads(timeout: float = 5.0) -> None:
    """等改鸟种的后台移动线程结束再关窗口 / Wait for the background move thread."""
    import threading
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [t for t in threading.enumerate()
                 if t is not threading.current_thread() and t.daemon and t.is_alive()
                 and t.name.startswith("Thread-")]
        if not alive:
            return
        time.sleep(0.02)


def _stub_species_dialog(monkeypatch, cn: str, en: str, latin: str) -> None:
    """把选鸟弹窗换成「用户选了指定鸟种并点确定」/ Stub the species dialog."""
    import ui.bird_species_edit_dialog as bsed

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None, photo_path=None, burst_source=None,
                     reidentify_count=0):
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, latin

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)


def _browser(root: str):
    """库里两张 3 星：苍鹭一张、大蓝鹭一张 / Two 3-star photos, one per species."""
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    rows = [
        ("_T3A2095", "苍鹭", "Grey Heron"),
        ("_T3A2096", "大蓝鹭", "Great Blue Heron"),
    ]
    db = ReportDB(root)
    for name, cn, en in rows:
        rel = f"{cn}/3星_优选/{name}.CR3"
        _touch(os.path.join(root, rel))
        db.insert_photo({
            "filename": name, "current_path": rel, "original_path": rel,
            "bird_species_cn": cn, "bird_species_en": en,
            "rating": 3, "has_bird": 1,
        })
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    return win


def test_panel_reports_fallback_only_when_selection_vanishes():
    """返回值：选中项消失才为 True；原本就是「全部」或选中项仍在都为 False。"""
    from tools.i18n import get_i18n
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(get_i18n())
    assert panel.update_species_list(["苍鹭", "大蓝鹭"]) is False   # 原本是「全部」

    panel.species_combo.setCurrentIndex(panel.species_combo.findData("苍鹭"))
    assert panel.update_species_list(["苍鹭", "大蓝鹭"]) is False   # 仍在
    assert panel.species_combo.currentData() == "苍鹭"

    assert panel.update_species_list(["大蓝鹭"]) is True             # 消失
    assert panel.species_combo.currentData() == ""


def test_grid_shows_all_after_last_photo_of_selected_species_is_reidentified(
        tmp_path, monkeypatch):
    """用户报的路径：选着「苍鹭」把唯一一张改成「大蓝鹭」，网格要显示全部照片。"""
    _stub_species_dialog(monkeypatch, "大蓝鹭", "Great Blue Heron", "Ardea herodias")
    win = _browser(str(tmp_path))
    try:
        combo = win._filter_panel.species_combo
        combo.setCurrentIndex(combo.findData("苍鹭"))
        assert [p["filename"] for p in win._filtered_photos] == ["_T3A2095"]

        win._on_species_edit_requested(win._filtered_photos[0])

        assert combo.currentData() == ""
        assert sorted(p["filename"] for p in win._filtered_photos) == [
            "_T3A2095", "_T3A2096"], "下拉已退回「全部」，网格却没跟着重载"
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_rating_filter_that_hides_selected_species_reloads_with_all(tmp_path):
    """
    同一缺陷的另一条路径：换星级筛选让选中的鸟种没了照片，传入的筛选条件
    还带着旧鸟种，_apply_filters 必须改用下拉现在的条件。
    """
    win = _browser(str(tmp_path))
    try:
        combo = win._filter_panel.species_combo
        combo.setCurrentIndex(combo.findData("苍鹭"))

        stale = dict(win._filter_panel.get_filters())
        # 苍鹭降到 0 星：低星照片不进鸟种下拉，于是选中项消失
        # Dropping it to 0 stars removes 苍鹭 from the dropdown.
        win._db.update_photo("_T3A2095", {"rating": 0})
        win._apply_filters(stale)

        assert combo.currentData() == ""
        names = {p["filename"] for p in win._filtered_photos}
        assert "_T3A2096" in names, "下拉显示「全部」，网格却还只按苍鹭过滤"
    finally:
        win.close()
