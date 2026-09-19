# -*- coding: utf-8 -*-
"""
改鸟种必须同步鸟种级属性 / A species change must carry the species-level attributes.

对应现网缺陷（2026-09-19 发现）：在结果浏览器里把一张照片改成别的鸟种后，
报告里这个鸟种块的名字是新的，罕见度、IUCN 等级和颜值却还是**旧鸟种**的。

根因：`gbif_rarity_100` / `iucn_category` / `aesthetic_index` 是**鸟种级**属性，
只有处理流程（core/photo_processor.py）在识鸟时写过一次；改鸟种的三条路径
（单张 / 多选批量 / 整种合并）都只改 `bird_species_cn/en` 与 `has_bird`，从没
碰过这三个字段。而报告的 `SpeciesBlock` 正是从代表照片上取它们
（core/report_export.py 的 aggregate），并且**按罕见度档位给整个鸟种清单排序**
——于是一只被改成「家燕」的照片，会顶着上一个鸟种的「极罕见 + 濒危」标记
排在报告最前面。

修法：改鸟种时按新鸟种的学名重查这三个值一并写入；查不到就**写 None 清空**
——留着旧值是确定错的，宁可不显示徽标也不能显示错的。

Regression: a species change rewrote only the names, leaving the previous
species' rarity / IUCN / beauty on the photo. The report reads those from the
representative row and sorts the whole species list by rarity, so a corrected
photo kept the old species' badges and ranking.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])

# 参考库里的真实取值（birdid/data/bird_reference.sqlite）。
# 用真库而不是造假数据：这三个字段的意义就是「查得到的那个值」，
# 用替身会把「查询链路是否接上」这件唯一要验的事验没了。
# Real values from the shipped reference DB; stubbing them would void the test.
_SWALLOW_LATIN = "Hirundo rustica"          # 家燕
_SWALLOW_RARITY = 0.16
_SWALLOW_IUCN = "LC"
_SWALLOW_BEAUTY = 62.4

# 改之前照片上挂着的旧鸟种属性（极罕见 + 濒危 + 高颜值），
# 与家燕的真实取值差得足够远，断言不会因为四舍五入而误判。
_OLD_RARITY = 95.0
_OLD_IUCN = "EN"
_OLD_BEAUTY = 88.0


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文：本文件断言中文鸟名与中文目录名，而目录命名跟随全局 i18n
    单例——同批先跑的测试若构造过 MainWindow 就可能把语言切走。

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


def _join_worker_threads(timeout: float = 8.0) -> None:
    """等改鸟种起的后台移动线程跑完 / Wait for the background move thread."""
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


def _insert(db, filename: str, rel: str, rating: int = 3):
    db.insert_photo({
        "filename": filename,
        "current_path": rel,
        "original_path": rel,
        "bird_species_cn": "华丽掩鼻风鸟",
        "bird_species_en": "Magnificent Riflebird",
        "rating": rating,
        "has_bird": 1,
        "gbif_rarity_100": _OLD_RARITY,
        "iucn_category": _OLD_IUCN,
        "aesthetic_index": _OLD_BEAUTY,
    })


def _browser(root: str, burst: bool = False):
    """
    建一个真实 ResultsBrowserWindow。

    burst=False：库里一张已整理的照片；
    burst=True ：一个三张的连拍组（burst_ 目录，浏览器据此分组）。
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    if burst:
        for i in range(3):
            rel = f"华丽掩鼻风鸟/2星_良好/burst_001/DSC_200{i}.NEF"
            _touch(os.path.join(root, rel))
            _insert(db, f"DSC_200{i}", rel, rating=2)
    else:
        rel = "华丽掩鼻风鸟/3星_优选/DSC_1234.NEF"
        _touch(os.path.join(root, rel))
        _insert(db, "DSC_1234", rel)
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    return win


def _db_rows(root: str):
    """直接读库，绕开一切内存缓存 / Read the DB, bypassing every cache."""
    import sqlite3
    conn = sqlite3.connect(os.path.join(root, ".superpicky", "report.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT filename, bird_species_cn, gbif_rarity_100, iucn_category, "
            "aesthetic_index FROM photos ORDER BY filename")]
    finally:
        conn.close()


# ----------------------------------------------------------------------
#  纯函数层：按学名查鸟种级属性
# ----------------------------------------------------------------------

def test_lookup_returns_the_species_attributes_for_a_known_latin_name():
    """已知学名必须查得到罕见度、IUCN 与颜值 —— 三者缺一，报告徽标就少一个。"""
    from core.species_extras import lookup_species_extras

    extras = lookup_species_extras(_SWALLOW_LATIN)

    assert extras["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY)
    assert extras["iucn_category"] == _SWALLOW_IUCN
    assert extras["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)


def test_lookup_returns_all_keys_as_none_for_an_unknown_latin_name():
    """
    查不到也必须把三个键都给出来（值为 None）。

    调用方会把这个字典整体并进 DB 更新：键缺席等于「保留旧值」，而旧值属于
    上一个鸟种，是确定错的。宁可清空。

    A miss must still yield all three keys so the caller clears stale values.
    """
    from core.species_extras import lookup_species_extras

    extras = lookup_species_extras("Nonexistentus fakeus")

    assert set(extras) == {"gbif_rarity_100", "iucn_category", "aesthetic_index"}
    assert all(v is None for v in extras.values())


def test_lookup_tolerates_an_empty_latin_name():
    """没有学名（老批次 / 用户自定义鸟名）不能抛异常，同样返回三个 None。"""
    from core.species_extras import lookup_species_extras

    assert lookup_species_extras("") == {
        "gbif_rarity_100": None,
        "iucn_category": None,
        "aesthetic_index": None,
    }


# ----------------------------------------------------------------------
#  单张改鸟种
# ----------------------------------------------------------------------

def test_single_species_change_rewrites_the_attributes_in_db(tmp_path, monkeypatch):
    """改完鸟种，库里的罕见度/IUCN/颜值必须是新鸟种的值。"""
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    root = str(tmp_path)
    win = _browser(root)
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        row = _db_rows(root)[0]
        assert row["bird_species_cn"] == "家燕"
        assert row["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY)
        assert row["iucn_category"] == _SWALLOW_IUCN
        assert row["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)
    finally:
        _join_worker_threads()
        win.close()


def test_single_species_change_rewrites_the_attributes_in_all_photos(tmp_path, monkeypatch):
    """`_all_photos` 是报告的数据源，三个字段同样要跟着换。"""
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    win = _browser(str(tmp_path))
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
        win._on_species_edit_requested(photo)

        cached = win._all_photos[0]
        assert cached["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY)
        assert cached["iucn_category"] == _SWALLOW_IUCN
        assert cached["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


def test_report_block_shows_the_new_species_badges(tmp_path, monkeypatch):
    """
    用户可见的症状：报告里「家燕」这一块不能再顶着旧鸟种的濒危标记。

    走报告真正的入口数据（`_all_photos` → aggregate），钉住用户看到的结果。
    """
    from core.rarity_tier import gbif_score_to_tier
    from core.report_export import aggregate

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    win = _browser(str(tmp_path))
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        rows = [win._resolve_photo_paths(p) for p in win._all_photos]
        block = aggregate(rows, include_gps=False).species[0]

        assert block.name_cn == "家燕"
        assert block.iucn == _SWALLOW_IUCN, "报告仍挂着旧鸟种的 IUCN 等级"
        assert block.tier == gbif_score_to_tier(_SWALLOW_RARITY), "罕见度档位仍是旧鸟种的"
        assert block.beauty == pytest.approx(_SWALLOW_BEAUTY)
    finally:
        _join_worker_threads()
        win.close()


def test_unknown_species_clears_the_previous_attributes(tmp_path, monkeypatch):
    """
    改成一个查不到学名的鸟种时，旧鸟种的三个属性必须被清空而不是留着。

    留着的话，报告会用上一个鸟种的罕见度给这一块排序、挂它的濒危徽标——
    比不显示更糟。
    """
    _stub_species_dialog(monkeypatch, "自定义鸟", "Custom Bird", "Nonexistentus fakeus")
    root = str(tmp_path)
    win = _browser(root)
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        row = _db_rows(root)[0]
        assert row["bird_species_cn"] == "自定义鸟"
        assert row["gbif_rarity_100"] is None
        assert row["iucn_category"] is None
        assert row["aesthetic_index"] is None
        assert win._all_photos[0]["gbif_rarity_100"] is None
    finally:
        _join_worker_threads()
        win.close()


# ----------------------------------------------------------------------
#  连拍组
# ----------------------------------------------------------------------

def test_burst_group_members_get_the_new_attributes(tmp_path, monkeypatch):
    """
    改一张连拍组的照片会整组改鸟种，三个属性也必须整组换。

    只改代表图的话，组内其余照片会带着旧鸟种的罕见度进报告——而报告的鸟种块
    恰恰是从组内**锐度最高**的那张取值的，代表图未必就是它。
    """
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    root = str(tmp_path)
    win = _browser(root, burst=True)
    try:
        photo = win._filtered_photos[0]
        assert photo.get("burst_id") is not None, "测试前提：这是一个连拍组"

        win._on_species_edit_requested(photo)
        _join_worker_threads()

        rows = _db_rows(root)
        assert len(rows) == 3
        for row in rows:
            assert row["bird_species_cn"] == "家燕"
            assert row["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY), (
                f"{row['filename']} 仍带着旧鸟种的罕见度"
            )
            assert row["iucn_category"] == _SWALLOW_IUCN
            assert row["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)
    finally:
        _join_worker_threads()
        win.close()


# ----------------------------------------------------------------------
#  整种合并 / 多选批量
# ----------------------------------------------------------------------

def _silence_dialogs(monkeypatch):
    """
    把合并/批量流程里的确认框与结果框替换掉。

    这两条路径都以「弹确认 → 带进度执行 → 弹结果」收尾，离线跑测试时必须
    让它们自动应答，否则测试会停在模态框上。

    Auto-answer the confirm/result dialogs of the merge and batch flows.
    """
    import ui.custom_dialogs as dialogs_mod
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "question",
        staticmethod(lambda *a, **k: dialogs_mod.StyledMessageBox.Yes),
    )
    monkeypatch.setattr(
        dialogs_mod.StyledMessageBox, "information",
        staticmethod(lambda *a, **k: None),
    )


def test_whole_species_merge_rewrites_the_attributes(tmp_path, monkeypatch):
    """
    整种合并同样要换掉鸟种级属性。

    合并是「一整批都被认错」时用的，全批照片都会带着旧鸟种的罕见度进报告，
    比单张漏改影响更大。
    """
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    _silence_dialogs(monkeypatch)
    root = str(tmp_path)
    win = _browser(root)
    try:
        photo = win._resolve_photo_paths(win._all_photos[0])

        win._on_merge_species_requested(photo)
        _join_worker_threads()

        row = _db_rows(root)[0]
        assert row["bird_species_cn"] == "家燕"
        assert row["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY)
        assert row["iucn_category"] == _SWALLOW_IUCN
        assert row["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)
    finally:
        _join_worker_threads()
        win.close()


def test_batch_species_edit_rewrites_the_attributes(tmp_path, monkeypatch):
    """多选批量改鸟种：勾选集里的每一张都要换掉鸟种级属性。"""
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", _SWALLOW_LATIN)
    _silence_dialogs(monkeypatch)
    root = str(tmp_path)
    win = _browser(root)
    try:
        targets = [win._resolve_photo_paths(p) for p in win._all_photos]

        win._batch_species_edit(targets)
        _join_worker_threads()

        row = _db_rows(root)[0]
        assert row["bird_species_cn"] == "家燕"
        assert row["gbif_rarity_100"] == pytest.approx(_SWALLOW_RARITY)
        assert row["iucn_category"] == _SWALLOW_IUCN
        assert row["aesthetic_index"] == pytest.approx(_SWALLOW_BEAUTY)
    finally:
        _join_worker_threads()
        win.close()
