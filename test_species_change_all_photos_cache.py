# -*- coding: utf-8 -*-
"""
改鸟种后 `_all_photos` 缓存必须同步 / The `_all_photos` cache must follow a species change.

对应现网缺陷（2026-09-11 报告）：在选鸟结果浏览器里改完鸟种，左侧鸟种下拉
已经显示新鸟名，但导出的 HTML 报告里还是旧鸟名。

根因：浏览器有两份内存缓存，单张改鸟种只补了其中一份。

  - `_filtered_photos` —— 当前筛选下的可见列表，改鸟种时**有**就地更新；
  - `_all_photos`      —— 全量列表，改鸟种时**没有**更新。

而这两个消费者读的是不同的源：

  - 鸟种下拉读 `compute_dropdown_species(self._db, ...)`，直接查库。改鸟种
    第 2 步已同步写库，所以下拉是对的——这正是缺陷难以察觉的原因，界面看起来
    完全正常。
  - HTML 报告、eBird 导出、「本次拍到的鸟种」、整种合并的取样池读
    `self._all_photos`，于是全部停在旧鸟名。

批量改鸟种（`_execute_batch_species_change`）与标记为无鸟
（`_refresh_after_background_change`）都以「重读库 + 重放筛选」收尾，只有单张
路径是就地打补丁——三条路径的刷新程度不一致，正是这类缺陷的温床。单张路径
故意不做全量重载（要保住滚动位置与选中状态，文件移动还在后台跑），所以修法是
把补丁打全，而不是改成重载。

Regression: after a single-photo species change the dropdown was right (it
queries the DB) while the exported report was wrong (it reads `_all_photos`,
which the patch-in-place path never touched).
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文：本文件断言中文鸟名与中文目录名，而目录命名跟随全局 i18n
    单例——同批先跑的测试若构造过 MainWindow（会加载用户真实配置，可能是
    en_US）就会把语言切走。

    Pin i18n to zh_CN; folder naming follows the global singleton.
    """
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _join_worker_threads(timeout: float = 5.0) -> None:
    """
    等改鸟种起的后台移动线程跑完，避免「DB 已关、线程还在写」的竞态。

    Wait for the background move thread before closing the window.
    """
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


def _stub_species_dialog(monkeypatch, cn: str, en: str, latin: str):
    """把选鸟弹窗替换成「用户选了指定鸟种并点了确定」。"""
    import ui.bird_species_edit_dialog as bsed

    class _Stub:
        def __init__(self, parent=None, session_species=None,
                     exclude_species=None):
            self.session_species = list(session_species or [])
            self.exclude_species = list(exclude_species or [])
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, latin

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)


def _browser_with_one_photo(root: str):
    """
    建一个真实 ResultsBrowserWindow，库里只有一张已整理的照片（白喉抚蜜鸟）。

    返回 (window, photo)：photo 取自 `_filtered_photos`，与界面上被点的那张
    是同一个对象——改鸟种入口拿到的正是它。
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    rel = "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"
    _touch(os.path.join(root, rel))
    db = ReportDB(root)
    db.insert_photo({
        "filename": "DSC_1234",
        "current_path": rel,
        "original_path": rel,
        "bird_species_cn": "白喉抚蜜鸟",
        "bird_species_en": "White-throated Honeyeater",
        "rating": 3,
        "has_bird": 1,
    })
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
    return win, photo


def test_all_photos_cache_follows_single_species_change(tmp_path, monkeypatch):
    """
    改完鸟种，`_all_photos` 里的那条记录必须是新鸟名。

    这是缺陷的最小复现：改之前两份缓存都是旧鸟名，改之后
    `_filtered_photos` 更新了而 `_all_photos` 没有。
    """
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win, photo = _browser_with_one_photo(str(tmp_path))
    try:
        assert [p["bird_species_cn"] for p in win._all_photos] == ["白喉抚蜜鸟"]

        win._on_species_edit_requested(photo)

        assert [p["bird_species_cn"] for p in win._filtered_photos] == ["家燕"]
        assert [p["bird_species_cn"] for p in win._all_photos] == ["家燕"], (
            "_all_photos 没跟着改鸟种更新——报告/eBird/整种合并都读它"
        )
        assert [p["bird_species_en"] for p in win._all_photos] == ["Barn Swallow"]
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_exported_report_shows_the_new_species(tmp_path, monkeypatch):
    """
    用户实际报的症状：改完鸟种，报告的鸟种名录必须是新鸟名。

    走报告真正的入口数据（`_all_photos` → aggregate），而不是只查内部字段，
    这样即使将来换了缓存实现，这条断言仍然钉住用户可见的结果。
    """
    from core.report_export import aggregate

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win, photo = _browser_with_one_photo(str(tmp_path))
    try:
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        data = aggregate(rows, include_gps=False)

        assert [b.name_cn for b in data.species] == ["家燕"], (
            "报告仍在用旧鸟名"
        )
    finally:
        _join_worker_threads()
        win.close()


def test_session_species_reflects_the_new_species(tmp_path, monkeypatch):
    """
    「本次拍到的鸟种」（选鸟弹窗的开场列表）同样读 `_all_photos`。

    改完鸟种后它若还列着旧鸟名，用户下一次纠错时看到的候选就是错的。
    """
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win, photo = _browser_with_one_photo(str(tmp_path))
    try:
        assert win._session_species() == [("白喉抚蜜鸟", 1)]

        win._on_species_edit_requested(photo)

        assert win._session_species() == [("家燕", 1)]
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_has_bird_restored_in_all_photos_after_naming_a_species(tmp_path, monkeypatch):
    """
    给「标记为无鸟」的照片重新指名鸟种时，`_all_photos` 的 has_bird 必须回到 1。

    报告的鸟种名录按 has_bird 过滤（core/report_export.py 的
    `bird_rows = [r for r in photos if r.get("has_bird")]`），所以只补鸟名而
    不补 has_bird 的话，这张照片仍然进不了报告——鸟名对了却整条不见，比显示
    旧鸟名更难排查。
    """
    from core.report_export import aggregate

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win, photo = _browser_with_one_photo(str(tmp_path))
    try:
        # 模拟这张先被标记成无鸟：库与两份缓存都置为 has_bird=0
        from ui.results_browser_window import _photo_db_key
        win._db.update_photo(
            _photo_db_key(photo),
            {"has_bird": 0, "bird_species_cn": None, "bird_species_en": None},
        )
        for bucket in (win._all_photos, win._filtered_photos, [photo]):
            for p in bucket:
                p["has_bird"] = 0
                p["bird_species_cn"] = ""
                p["bird_species_en"] = ""

        win._on_species_edit_requested(photo)

        assert [p["has_bird"] for p in win._all_photos] == [1]
        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        assert [b.name_cn for b in aggregate(rows, include_gps=False).species] == ["家燕"]
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()
