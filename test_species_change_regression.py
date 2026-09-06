# -*- coding: utf-8 -*-
"""
改鸟种全链路回归测试 / Regression tests for the species-change flow.

对应现网缺陷（2026-09-05 定位）：在选鸟结果浏览器里改鸟种后，照片没有被
重新分到新鸟种目录，元数据也没跟着变。根因有三层，本文件逐层钉死：

A. 信号传副本 / Signals copied the photo dict
   PySide6 的 ``Signal(dict)`` 在 emit 时会做 QVariantMap 往返转换，接收方
   拿到的是**新 dict**。于是 rating_mover 把移动后的新路径写回的是一个临时
   副本，``_filtered_photos`` 里的原对象 ``current_path`` 永远停在旧位置。
   必须用 ``Signal(object)`` 才传引用。

B. 失效路径静默失败 / Stale path failed silently
   承 A：第二次改同一张照片时，内存里的 ``current_path`` 已指向不存在的旧
   位置，``change_bird_species`` 第一行 ``os.path.exists`` 就返回 False，
   什么都不做也什么都不报——用户看到的正是「改了没反应」。

C. 从不写元数据 / Metadata was never written
   主处理流程会把鸟名写进 ``XMP:Title``（并按开关 merge-add 到
   ``XMP-dc:Subject``），但改鸟种链路从头到尾没有任何 exiftool 调用，磁盘上
   的标题与关键字一直是识别错的旧鸟名。

Three-layer regression suite: signals must pass the dict by reference, a
stale in-memory path must not silently abort the move, and a species change
must propagate to XMP Title/keywords the way the main pipeline does.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _pin_chinese_locale():
    """
    钉住 i18n 为中文：本文件断言硬编码中文目录名（3星_优选 等），而
    get_rating_folder_name / _folder_bird_name 都跟随全局 i18n 单例——同批
    先跑的测试若构造过 MainWindow（加载用户真实配置，可能是 en_US）会把语言
    切走，导致文件被移到英文目录名下。

    Pin the i18n singleton to zh_CN; folder naming follows the global i18n.
    """
    from tools.i18n import get_i18n
    i18n = get_i18n()
    original = i18n.current_lang
    if not original.startswith("zh"):
        i18n.switch_language("zh_CN")
    yield
    if i18n.current_lang != original:
        i18n.switch_language(original)


# ── A. 信号必须传引用 / Signals must pass by reference ──────────────────────

def _emitted_object_is_same(signal, payload):
    """emit 一次 payload，返回接收方拿到的是否为同一对象。"""
    received = []
    signal.connect(received.append)
    signal.emit(payload)
    return bool(received) and received[0] is payload


def test_fullscreen_species_edit_signal_passes_photo_by_reference():
    """
    全屏「编辑鸟种」信号必须把 photo 原对象交给父窗口。

    声明成 Signal(dict) 时 PySide6 会拷贝，后台线程写回的新 current_path
    就丢在副本里，浏览器内存中的照片路径永远停在旧位置。
    """
    from ui.fullscreen_viewer import FullscreenViewer
    from tools.i18n import get_i18n

    viewer = FullscreenViewer(get_i18n())
    photo = {"filename": "DSC_1234", "current_path": "/tmp/白腰雨燕/DSC_1234.NEF"}
    try:
        assert _emitted_object_is_same(viewer.species_edit_requested, photo)
    finally:
        viewer.close()


def test_thumbnail_grid_photo_selected_passes_photo_by_reference():
    """
    网格选中信号必须传原对象——详情面板据此持有照片，后续改鸟种/改星级
    都从这里拿 photo，一旦是副本，所有写回都到不了 _filtered_photos。
    """
    from ui.thumbnail_grid import ThumbnailGrid
    from tools.i18n import get_i18n

    grid = ThumbnailGrid(get_i18n())
    photo = {"filename": "DSC_1234", "current_path": "/tmp/白腰雨燕/DSC_1234.NEF"}
    try:
        assert _emitted_object_is_same(grid.photo_selected, photo)
    finally:
        grid.close()


# ── B. 失效路径不得静默失败 / A stale path must not silently abort ──────────

def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    return path


def _make_db(root: str, filename: str, rel_current: str, bird_cn: str,
             rating: int = 3, burst_id=None):
    """在 root 下建一个真实 ReportDB 并写入一条照片记录。"""
    from tools.report_db import ReportDB
    db = ReportDB(root)
    db.insert_photo({
        "filename": filename,
        "current_path": rel_current,
        "original_path": rel_current,
        "bird_species_cn": bird_cn,
        "bird_species_en": "White-throated Honeyeater",
        "rating": rating,
        "burst_id": burst_id,
    })
    return db


def test_second_species_change_still_moves_the_file(tmp_path):
    """
    连续改两次鸟种，第二次必须照样把文件移到新目录。

    现网缺陷正是卡在这里：第一次移动后内存里的 current_path 没更新，第二次
    调用时 os.path.exists 为 False 便直接 return False——DB 记下了新鸟名，
    文件却留在上一个鸟种目录里，鸟名与目录从此互相矛盾。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "bird_species_cn": "白喉抚蜜鸟",
        "rating": 3,
    }

    assert change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234"
    ) is True
    assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.NEF"))

    # 第二次：即便调用方手里的 photo 仍是第一次移动前的旧路径，也必须成功
    stale_photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "bird_species_cn": "家燕",
        "rating": 3,
    }
    result = change_bird_species(
        root, stale_photo, "大山雀", "Great Tit", "species-first", db, "DSC_1234"
    )

    assert result is True
    assert os.path.exists(os.path.join(root, "大山雀/3星_优选/DSC_1234.NEF"))
    assert not os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.NEF"))
    assert db.get_photo("DSC_1234")["current_path"] == os.path.join(
        "大山雀", "3星_优选", "DSC_1234.NEF"
    )


def test_stale_path_failure_is_reported_when_file_truly_missing(tmp_path):
    """
    文件是真的不在了（DB 里的路径也找不到）时，必须回报失败原因，
    不能像现在这样直接 return False 让 UI 无从得知。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    db = _make_db(root, "DSC_9999", "白喉抚蜜鸟/3星_优选/DSC_9999.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_9999",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_9999.NEF"),
        "rating": 3,
    }
    failures: list = []

    result = change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_9999", failures
    )

    assert result is False
    assert failures == [("DSC_9999.NEF", "source_missing")]


def test_source_missing_reason_is_localized():
    """
    新增的 source_missing 原因码必须有本地化文案，否则用户看到的是裸代码。
    """
    from ui.results_browser_window import _merge_reason_text

    text = _merge_reason_text("source_missing")
    assert text != "source_missing"
    assert text.strip()


def test_species_change_reports_failures_to_caller(tmp_path):
    """
    改鸟种失败必须回报给 UI。

    原先 _trigger_species_change 连 failures 参数都没传，移动失败的信息
    在后台线程里被彻底丢弃，用户只看到「什么都没发生」。
    """
    from ui.results_browser_window import _run_species_change

    root = str(tmp_path)
    db = _make_db(root, "DSC_9999", "白喉抚蜜鸟/3星_优选/DSC_9999.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_9999",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_9999.NEF"),
        "rating": 3,
    }
    reported: list = []

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_9999",
        on_failures=reported.append,
    )

    assert reported, "移动失败时必须回调通知 UI"
    assert reported[0] == [("DSC_9999.NEF", "source_missing")]


def test_species_change_does_not_report_on_success(tmp_path):
    """成功时不得打扰用户——回调只在真有失败时触发。"""
    from ui.results_browser_window import _run_species_change

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "rating": 3,
    }
    reported: list = []

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_1234",
        on_failures=reported.append,
    )

    assert reported == []
    assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.NEF"))


# ── 全屏视图必须跟着刷新 / The fullscreen view must refresh too ─────────────

def _browser_with_one_photo(root: str):
    """
    建一个真实 ResultsBrowserWindow，库里只有一张已整理的照片。
    返回 (window, photo)。
    """
    import ui.results_browser_window as rbw

    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
    return win, photo


def _join_worker_threads(timeout: float = 5.0) -> None:
    """
    等待改鸟种起的后台移动线程跑完。

    不等就关窗口会撞上「DB 已关、线程还在写」的竞态，噪音掩盖真实失败。
    Wait for the background move thread; closing the window first races with it.
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
    from PySide6.QtWidgets import QDialog
    import ui.bird_species_edit_dialog as bsed

    class _Stub:
        def __init__(self, parent=None):
            self.selected_cn, self.selected_en, self.selected_latin = cn, en, latin

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(bsed, "BirdSpeciesEditDialog", _Stub)


def test_fullscreen_species_label_refreshes_after_edit(tmp_path, monkeypatch):
    """
    在全屏里改鸟种后，全屏顶部的鸟名标签必须显示新鸟名。

    原先 _on_species_edit_requested 只刷新详情面板，全屏自己的
    _species_label 从不更新——用户在全屏里改完，界面上鸟名纹丝不动，
    看起来就像「什么都没发生」。
    """
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    win, photo = _browser_with_one_photo(str(tmp_path))
    try:
        win._show_fullscreen_photo(photo, nav_photos=win._filtered_photos)
        assert win._fullscreen._species_label.text() == "白喉抚蜜鸟"

        win._fullscreen._on_edit_species_clicked()

        assert win._fullscreen._species_label.text() == "家燕"
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()


# ── C. 改鸟种必须同步写 XMP / A species change must reach the metadata ──────

def test_replace_keyword_swaps_old_species_for_new():
    """改鸟种时旧鸟名关键字必须被换掉，而不是与新鸟名并存。"""
    from tools.exiftool_manager import replace_keyword_in_list

    assert replace_keyword_in_list(
        ["澳洲", "白喉抚蜜鸟", "晨光"], "白喉抚蜜鸟", "家燕"
    ) == ["澳洲", "家燕", "晨光"]


def test_replace_keyword_appends_when_old_absent():
    """原本没写过鸟名关键字（如后补录鸟种）时，直接追加新鸟名。"""
    from tools.exiftool_manager import replace_keyword_in_list

    assert replace_keyword_in_list(["澳洲"], None, "家燕") == ["澳洲", "家燕"]


def test_replace_keyword_returns_none_when_nothing_changes():
    """新鸟名已在列表里且无旧名可删时返回 None，调用方据此跳过写入。"""
    from tools.exiftool_manager import replace_keyword_in_list

    assert replace_keyword_in_list(["家燕"], None, "家燕") is None


def test_replace_keyword_does_not_duplicate_when_new_already_present():
    """旧名要删、新名已在——结果只删旧名，不产生重复。"""
    from tools.exiftool_manager import replace_keyword_in_list

    assert replace_keyword_in_list(
        ["白喉抚蜜鸟", "家燕"], "白喉抚蜜鸟", "家燕"
    ) == ["家燕"]


def test_change_collects_moved_raw_for_metadata_write(tmp_path):
    """
    core 必须交出「改完之后该写元数据的文件」——路径是移动后的新位置。

    UI 层据此写 XMP Title/关键字；拿不到列表就只能什么都不写（现状）。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "rating": 3,
    }
    changed: list = []

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234",
        changed_files=changed,
    )

    assert changed == [os.path.join(root, "家燕/3星_优选/DSC_1234.NEF")]


def test_unmoved_photo_still_collected_for_metadata_write(tmp_path):
    """
    根目录下（未整理）的照片不移动，但鸟名照样变了——元数据仍须更新。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "DSC_1234.NEF"),
        "rating": 3,
    }
    changed: list = []

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234",
        changed_files=changed,
    )

    assert changed == [os.path.join(root, "DSC_1234.NEF")]


def test_cached_preview_is_not_offered_for_metadata_write(tmp_path):
    """
    .superpicky/cache 下的预览图是内部缓存，不是用户的照片，绝不能拿去写元数据
    （主处理流程同样明确排除它，见 photo_processor 的 is_temp_file 判断）。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    cache_jpg = _touch(os.path.join(root, ".superpicky", "cache", "temp_preview", "DSC_1234.jpg"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "temp_jpeg_path": cache_jpg,
        "rating": 3,
    }
    changed: list = []

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234",
        changed_files=changed,
    )

    assert all(".superpicky" not in p for p in changed), changed


def test_burst_group_collects_every_member_for_metadata_write(tmp_path):
    """连拍整组改鸟种，组内每一张都要写新鸟名，不能只写代表图。"""
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    for name in ("DSC_1234", "DSC_1235"):
        _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "burst_001", f"{name}.NEF"))
    from tools.report_db import ReportDB
    db = ReportDB(root)
    for name in ("DSC_1234", "DSC_1235"):
        db.insert_photo({
            "filename": name,
            "current_path": f"白喉抚蜜鸟/3星_优选/burst_001/{name}.NEF",
            "bird_species_cn": "白喉抚蜜鸟",
            "rating": 3,
            "burst_id": 7,
        })
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/burst_001/DSC_1234.NEF"),
        "rating": 3,
        "burst_id": 7,
    }
    changed: list = []

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234",
        changed_files=changed,
    )

    assert sorted(changed) == sorted([
        os.path.join(root, "家燕/3星_优选/burst_001/DSC_1234.NEF"),
        os.path.join(root, "家燕/3星_优选/burst_001/DSC_1235.NEF"),
    ])


class _FakeMetadataWriter:
    """记录 update_species_metadata 调用，替代真实 exiftool。"""

    def __init__(self):
        self.calls: list = []

    def update_species_metadata(self, file_path, new_title, old_title=None,
                                write_keywords=False):
        self.calls.append({
            "file": file_path, "new_title": new_title,
            "old_title": old_title, "write_keywords": write_keywords,
        })
        return True


def test_species_change_writes_new_title_to_moved_file(tmp_path):
    """
    改鸟种后必须把新鸟名写进照片元数据——这正是此前完全缺失的一环：
    主流程把鸟名写进 XMP:Title，改鸟种却从不更新，磁盘上一直是错的旧名。
    """
    from ui.results_browser_window import _run_species_change

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "rating": 3,
    }
    writer = _FakeMetadataWriter()

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_1234",
        old_bird_cn="白喉抚蜜鸟", old_bird_en="White-throated Honeyeater",
        metadata_writer=writer,
    )

    assert len(writer.calls) == 1
    call = writer.calls[0]
    assert call["file"] == os.path.join(root, "家燕/3星_优选/DSC_1234.NEF")
    assert call["new_title"] == "家燕"
    assert call["old_title"] == "白喉抚蜜鸟"


def test_species_change_writes_metadata_for_every_burst_member(tmp_path):
    """连拍整组改鸟种，组内每张都要写新鸟名。"""
    from ui.results_browser_window import _run_species_change
    from tools.report_db import ReportDB

    root = str(tmp_path)
    for name in ("DSC_1234", "DSC_1235"):
        _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "burst_001", f"{name}.NEF"))
    db = ReportDB(root)
    for name in ("DSC_1234", "DSC_1235"):
        db.insert_photo({
            "filename": name,
            "current_path": f"白喉抚蜜鸟/3星_优选/burst_001/{name}.NEF",
            "bird_species_cn": "白喉抚蜜鸟", "rating": 3, "burst_id": 7,
        })
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/burst_001/DSC_1234.NEF"),
        "rating": 3, "burst_id": 7,
    }
    writer = _FakeMetadataWriter()

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_1234",
        old_bird_cn="白喉抚蜜鸟", metadata_writer=writer,
    )

    assert sorted(c["file"] for c in writer.calls) == sorted([
        os.path.join(root, "家燕/3星_优选/burst_001/DSC_1234.NEF"),
        os.path.join(root, "家燕/3星_优选/burst_001/DSC_1235.NEF"),
    ])
    assert all(c["new_title"] == "家燕" for c in writer.calls)


def test_no_metadata_write_when_move_failed(tmp_path):
    """
    文件都找不到就别去写元数据——否则会对着不存在的路径空转并刷错误日志。
    """
    from ui.results_browser_window import _run_species_change

    root = str(tmp_path)
    db = _make_db(root, "DSC_9999", "白喉抚蜜鸟/3星_优选/DSC_9999.NEF", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_9999",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_9999.NEF"),
        "rating": 3,
    }
    writer = _FakeMetadataWriter()

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_9999",
        old_bird_cn="白喉抚蜜鸟", metadata_writer=writer,
    )

    assert writer.calls == []


# ── 真实写入 + 读回验证（中文样本）/ Real write + read-back, Chinese sample ──

def _make_jpeg(path: str) -> str:
    """生成一张最小可用的真实 JPEG，供 exiftool 实写实读。"""
    from PIL import Image
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 16), (90, 120, 60)).save(path, "JPEG")
    return path


@pytest.fixture
def _embedded_write_mode(monkeypatch):
    """
    把元数据写入模式钉成 embedded，并隔离用户真实配置。

    本机 metadata_write_mode 实际是 sidecar；不钉住的话写入会落到 .xmp 侧车，
    断言读本体就会失败——这类「读全局配置」的测试曾长期被误判成产品缺陷。

    Pin embedded mode; the user's real config is sidecar and would send the
    write to a .xmp sidecar instead of the file body.
    """
    from tools.exiftool_manager import get_exiftool_manager
    # 必须打在**单例实例**上，不能只打类：test_birdid_lr_keywords 用
    # monkeypatch.setattr(mgr, ...) 打过同名实例属性，pytest 撤销时会把类方法
    # 回写成实例属性；实例属性优先，之后再打类属性就不生效了（全量跑时本文件
    # 因此写进了侧车、读本体为空）。
    # Patch the singleton INSTANCE: another test's monkeypatch leaves the
    # original class method installed as an instance attribute, which would
    # shadow a class-level patch and silently route writes to the .xmp sidecar.
    mgr = get_exiftool_manager()
    monkeypatch.setattr(mgr, "_get_metadata_write_mode", lambda: "embedded")


def test_species_change_writes_chinese_title_readable_back(tmp_path, _embedded_write_mode):
    """
    改鸟种后，真实读回文件的 XMP:Title 必须是新的中文鸟名（无乱码）。
    """
    from ui.results_browser_window import _run_species_change
    from tools.exiftool_manager import get_exiftool_manager

    root = str(tmp_path)
    src = _make_jpeg(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.jpg"))
    mgr = get_exiftool_manager()
    assert mgr.set_metadata(src, {"Title": "白喉抚蜜鸟"}) is True

    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.jpg", "白喉抚蜜鸟")
    photo = {
        "filename": "DSC_1234",
        "current_path": src,
        "rating": 3,
    }

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_1234",
        old_bird_cn="白喉抚蜜鸟", old_bird_en="White-throated Honeyeater",
    )

    moved = os.path.join(root, "家燕/3星_优选/DSC_1234.jpg")
    assert os.path.exists(moved)
    # read_metadata 不带 extra_args 只返回固定标签组，须显式点名 Title
    # read_metadata returns a fixed tag set unless the tag is named explicitly
    read_back = mgr.read_metadata(moved, extra_args=["-XMP:Title"]) or {}
    assert read_back.get("Title") == "家燕"


def test_species_change_replaces_stale_keyword_on_disk(tmp_path, _embedded_write_mode,
                                                        monkeypatch):
    """
    开启鸟名关键字时，磁盘上的旧鸟名关键字必须被换成新鸟名，
    其余关键字原样保留——否则一张照片同时挂着两个鸟种。
    """
    from ui.results_browser_window import _run_species_change
    from tools.exiftool_manager import get_exiftool_manager
    from advanced_config import get_advanced_config

    monkeypatch.setattr(
        type(get_advanced_config()), "birdid_write_keywords", property(lambda self: True)
    )

    root = str(tmp_path)
    src = _make_jpeg(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.jpg"))
    mgr = get_exiftool_manager()
    assert mgr.set_metadata(
        src, {"Title": "白喉抚蜜鸟", "XMP-dc:Subject": ["澳大利亚", "白喉抚蜜鸟"]}
    ) is True

    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.jpg", "白喉抚蜜鸟")
    photo = {"filename": "DSC_1234", "current_path": src, "rating": 3}

    _run_species_change(
        root, photo, "家燕", "Barn Swallow", db, "DSC_1234",
        old_bird_cn="白喉抚蜜鸟", old_bird_en="White-throated Honeyeater",
    )

    moved = os.path.join(root, "家燕/3星_优选/DSC_1234.jpg")
    subject = (mgr.read_metadata(moved, extra_args=["-XMP-dc:Subject"]) or {}).get("Subject")
    subject = [subject] if isinstance(subject, str) else list(subject or [])
    assert "家燕" in subject
    assert "白喉抚蜜鸟" not in subject
    assert "澳大利亚" in subject


def test_browser_species_edit_updates_file_metadata_end_to_end(
    tmp_path, monkeypatch, _embedded_write_mode
):
    """
    端到端：在浏览器里点「修改鸟种」确认后，磁盘上那张照片的 XMP:Title
    必须变成新鸟名，且旧鸟名关键字被替换掉。

    这是用户实际走的路径——handler 必须把「改之前的鸟名」一路传到元数据
    写入层，否则关键字里的旧鸟名删不掉。
    """
    import ui.results_browser_window as rbw
    from tools.exiftool_manager import get_exiftool_manager
    from advanced_config import get_advanced_config

    monkeypatch.setattr(
        type(get_advanced_config()), "birdid_write_keywords", property(lambda self: True)
    )
    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")

    root = str(tmp_path)
    src = _make_jpeg(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.jpg"))
    mgr = get_exiftool_manager()
    assert mgr.set_metadata(
        src, {"Title": "白喉抚蜜鸟", "XMP-dc:Subject": ["澳大利亚", "白喉抚蜜鸟"]}
    ) is True
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.jpg", "白喉抚蜜鸟")
    db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    try:
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_1234")
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        moved = os.path.join(root, "家燕/3星_优选/DSC_1234.jpg")
        assert os.path.exists(moved), "文件应被移到新鸟种目录"
        read_back = mgr.read_metadata(
            moved, extra_args=["-XMP:Title", "-XMP-dc:Subject"]
        ) or {}
        assert read_back.get("Title") == "家燕"
        subject = read_back.get("Subject")
        subject = [subject] if isinstance(subject, str) else list(subject or [])
        assert "家燕" in subject
        assert "白喉抚蜜鸟" not in subject, "旧鸟名关键字必须被替换掉"
    finally:
        _join_worker_threads()
        win.close()


# ── D. 多选批量改鸟种 / Batch species edit over a multi-selection ───────────

class _FakeGrid:
    """只提供勾选集的假网格。"""

    def __init__(self, selected):
        self._selected = selected

    def get_explicitly_selected_photos(self):
        return list(self._selected)


class _FakeWindow:
    def __init__(self, selected):
        self._thumb_grid = _FakeGrid(selected)


def test_right_click_on_unselected_photo_targets_only_that_photo():
    """
    勾了 A/B/C 却右键点在没勾选的 D 上 —— 只改 D。
    右键点哪张就指向哪张，与 Finder / Lightroom 一致。
    """
    from ui.results_browser_window import _species_edit_targets

    a, b, c = ({"filename": n} for n in ("A", "B", "C"))
    d = {"filename": "D"}
    win = _FakeWindow([a, b, c])

    assert _species_edit_targets(win, d) == [d]


def test_right_click_on_selected_photo_targets_whole_selection():
    """右键点在已勾选的照片上 —— 作用于整个勾选集。"""
    from ui.results_browser_window import _species_edit_targets

    a, b, c = ({"filename": n} for n in ("A", "B", "C"))
    win = _FakeWindow([a, b, c])

    assert _species_edit_targets(win, b) == [a, b, c]


def test_no_selection_targets_the_clicked_photo():
    """一个都没勾选时，就是单张编辑的老行为。"""
    from ui.results_browser_window import _species_edit_targets

    a = {"filename": "A"}
    win = _FakeWindow([])

    assert _species_edit_targets(win, a) == [a]


def test_merge_collects_changed_files_for_metadata(tmp_path):
    """
    批量改鸟种同样要交出待写元数据的文件——多选批量复用这个执行器，
    它不收集的话批量改完磁盘上还是旧鸟名（整种合并此前正是如此）。
    """
    from core.rating_mover import merge_bird_species
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    photos = []
    for name in ("DSC_1", "DSC_2"):
        rel = f"白喉抚蜜鸟/3星_优选/{name}.NEF"
        _touch(os.path.join(root, rel))
        db.insert_photo({"filename": name, "current_path": rel,
                         "bird_species_cn": "白喉抚蜜鸟", "rating": 3})
        photos.append({"filename": name,
                       "current_path": os.path.join(root, rel), "rating": 3})
    changed: list = []

    result = merge_bird_species(
        root, photos, "家燕", "Barn Swallow", "species-first", db,
        lambda p: p["filename"], changed_files=changed,
    )

    assert result["moved"] == 2
    assert sorted(changed) == sorted([
        os.path.join(root, "家燕/3星_优选/DSC_1.NEF"),
        os.path.join(root, "家燕/3星_优选/DSC_2.NEF"),
    ])


def test_multi_selection_with_burst_members_processes_group_once(tmp_path):
    """
    多选里同时勾了同一连拍组的好几张时，整组只能被处理一次，
    但组内每一张都要拿到新鸟名（含没被勾选的成员）。
    """
    from core.rating_mover import merge_bird_species
    from tools.report_db import ReportDB

    root = str(tmp_path)
    db = ReportDB(root)
    members = ("DSC_1", "DSC_2", "DSC_3")
    for name in members:
        rel = f"白喉抚蜜鸟/3星_优选/burst_001/{name}.NEF"
        _touch(os.path.join(root, rel))
        db.insert_photo({"filename": name, "current_path": rel,
                         "bird_species_cn": "白喉抚蜜鸟", "rating": 3,
                         "burst_id": 5})
    # 用户只勾了组里的两张
    picked = [
        {"filename": n, "rating": 3, "burst_id": 5,
         "current_path": os.path.join(root, f"白喉抚蜜鸟/3星_优选/burst_001/{n}.NEF")}
        for n in ("DSC_1", "DSC_2")
    ]
    changed: list = []
    seen: list = []

    merge_bird_species(
        root, picked, "家燕", "Barn Swallow", "species-first", db,
        lambda p: p["filename"],
        progress_cb=lambda done, total, fn: seen.append(fn) is None,
        changed_files=changed,
    )

    assert len(seen) == 1, f"整组应只处理一次，实际 {seen}"
    # 组内三张(含未勾选的 DSC_3)都要跟着改
    for name in members:
        assert db.get_photo(name)["bird_species_cn"] == "家燕"
    assert sorted(os.path.basename(p) for p in changed) == [
        "DSC_1.NEF", "DSC_2.NEF", "DSC_3.NEF"
    ]


def _browser_with_photos(root: str, specs: list):
    """
    按 specs=[(filename, rel_path, rating, burst_id)] 建库并打开浏览器。
    文件用真实 JPEG，便于顺带验证元数据。
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    db = ReportDB(root)
    for name, rel, rating, burst in specs:
        _make_jpeg(os.path.join(root, rel))
        db.insert_photo({
            "filename": name, "current_path": rel, "original_path": rel,
            "bird_species_cn": "白喉抚蜜鸟", "bird_species_en": "White-throated Honeyeater",
            "rating": rating, "burst_id": burst,
        })
    db.close()
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    _settle_grid(win)
    return win


def _settle_grid(win, timeout: float = 5.0) -> None:
    """
    等网格真正建好。

    ThumbnailGrid.load_photos 用 50ms 定时器延迟构建（等布局稳定），在此之前
    grid._photos 是空的，勾选集自然也取不到——测试必须等它落定。
    Wait for the deferred grid build; grid._photos is empty until then.
    """
    import time
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if win._thumb_grid._photos:
            QApplication.processEvents()
            return
        time.sleep(0.02)


def _auto_confirm(monkeypatch):
    """把确认框钉成「是」、结果报告钉成无操作，避免测试阻塞在弹窗上。"""
    from ui.custom_dialogs import StyledMessageBox
    monkeypatch.setattr(StyledMessageBox, "question",
                        staticmethod(lambda *a, **k: StyledMessageBox.Yes))
    monkeypatch.setattr(StyledMessageBox, "information",
                        staticmethod(lambda *a, **k: None))


def test_multi_selection_species_edit_changes_every_selected_photo(
    tmp_path, monkeypatch, _embedded_write_mode
):
    """
    勾选多张后改鸟种，必须每一张都改——现网缺陷是只改了右键点中的那一张。
    """
    from ui.thumbnail_grid import _photo_key

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    specs = [(f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/DSC_{i}.jpg", 3, None) for i in (1, 2, 3)]
    win = _browser_with_photos(root, specs)
    try:
        picked = [p for p in win._filtered_photos if p["filename"] in ("DSC_1", "DSC_2")]
        assert len(picked) == 2
        for p in picked:
            win._thumb_grid._multi_selected.add(_photo_key(p))

        win._on_species_edit_requested(picked[0])
        _join_worker_threads()

        for name in ("DSC_1", "DSC_2"):
            assert os.path.exists(os.path.join(root, f"家燕/3星_优选/{name}.jpg")), \
                f"{name} 应被移到新鸟种目录"
        # 没勾选的第三张不能被动到
        assert os.path.exists(os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_3.jpg"))
    finally:
        _join_worker_threads()
        win.close()


def test_multi_selection_species_edit_writes_metadata_for_all(
    tmp_path, monkeypatch, _embedded_write_mode
):
    """批量改完，每一张磁盘上的 XMP:Title 都要是新鸟名。"""
    from ui.thumbnail_grid import _photo_key
    from tools.exiftool_manager import get_exiftool_manager

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    specs = [(f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/DSC_{i}.jpg", 3, None) for i in (1, 2)]
    win = _browser_with_photos(root, specs)
    mgr = get_exiftool_manager()
    try:
        picked = list(win._filtered_photos)
        for p in picked:
            win._thumb_grid._multi_selected.add(_photo_key(p))

        win._on_species_edit_requested(picked[0])
        _join_worker_threads()

        for name in ("DSC_1", "DSC_2"):
            moved = os.path.join(root, f"家燕/3星_优选/{name}.jpg")
            title = (mgr.read_metadata(moved, extra_args=["-XMP:Title"]) or {}).get("Title")
            assert title == "家燕", f"{name} 的 Title 是 {title!r}"
    finally:
        _join_worker_threads()
        win.close()


def test_selecting_one_burst_member_retags_whole_group(
    tmp_path, monkeypatch, _embedded_write_mode
):
    """
    只勾选连拍组里的一张，整组都要跟着改（用户明确要求的行为）。
    """
    from ui.thumbnail_grid import _photo_key

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    specs = [
        (f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/burst_001/DSC_{i}.jpg", 3, 5)
        for i in (1, 2, 3)
    ]
    win = _browser_with_photos(root, specs)
    try:
        photo = win._filtered_photos[0]
        win._thumb_grid._multi_selected.add(_photo_key(photo))
        win._on_species_edit_requested(photo)
        _join_worker_threads()

        for i in (1, 2, 3):
            assert os.path.exists(
                os.path.join(root, f"家燕/3星_优选/burst_001/DSC_{i}.jpg")
            ), f"连拍组内 DSC_{i} 应一起移动"
    finally:
        _join_worker_threads()
        win.close()


def test_metadata_writer_infers_old_species_from_file_title(tmp_path, _embedded_write_mode):
    """
    没告诉写入器旧鸟名时，它必须从文件现有的 XMP:Title 推断出来并据此删旧关键字。

    批量改鸟种时每张照片的旧鸟名各不相同，调用方给不出统一的旧名；而文件自己
    的 Title 正是主流程写进去的那个旧鸟名，直接读它最准。
    """
    from tools.exiftool_manager import get_exiftool_manager

    mgr = get_exiftool_manager()
    src = _make_jpeg(os.path.join(str(tmp_path), "a.jpg"))
    assert mgr.set_metadata(
        src, {"Title": "白喉抚蜜鸟", "XMP-dc:Subject": ["澳大利亚", "白喉抚蜜鸟"]}
    ) is True

    mgr.update_species_metadata(src, "家燕", old_title=None, write_keywords=True)

    read_back = mgr.read_metadata(src, extra_args=["-XMP:Title", "-XMP-dc:Subject"]) or {}
    subject = read_back.get("Subject")
    subject = [subject] if isinstance(subject, str) else list(subject or [])
    assert read_back.get("Title") == "家燕"
    assert "家燕" in subject
    assert "白喉抚蜜鸟" not in subject, "旧鸟名应按文件 Title 推断出来并删掉"
    assert "澳大利亚" in subject


def test_fullscreen_edit_never_batches_the_selection(tmp_path, monkeypatch,
                                                      _embedded_write_mode):
    """
    全屏是单张浏览：即使之前在网格里勾了多张，在全屏里点「编辑鸟种」也只能
    改当前这一张——全屏画面上就只有它，批量改会是彻底的意外操作。
    """
    from ui.thumbnail_grid import _photo_key

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")
    _auto_confirm(monkeypatch)

    root = str(tmp_path)
    specs = [(f"DSC_{i}", f"白喉抚蜜鸟/3星_优选/DSC_{i}.jpg", 3, None) for i in (1, 2)]
    win = _browser_with_photos(root, specs)
    try:
        for p in win._filtered_photos:
            win._thumb_grid._multi_selected.add(_photo_key(p))
        photo = win._filtered_photos[0]

        # 必须走 _enter_fullscreen：_show_fullscreen_photo 只换图不切页
        # Must use _enter_fullscreen; _show_fullscreen_photo does not switch page
        win._enter_fullscreen(photo)
        assert win._stack.currentIndex() == 1
        win._fullscreen._on_edit_species_clicked()
        _join_worker_threads()

        assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1.jpg"))
        assert os.path.exists(os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_2.jpg")), \
            "全屏里改鸟种不得波及其它勾选的照片"
    finally:
        _join_worker_threads()
        win.close()


def test_context_menu_label_says_how_many_will_change():
    """
    多选时右键菜单必须写明会改几张——文案还写「修改鸟种…」的话，
    用户根本不知道这一下会动 N 张照片。
    """
    from PySide6.QtWidgets import QWidget
    import ui.results_browser_window as rbw

    a, b, c = ({"filename": n, "current_path": f"/x/{n}.NEF"} for n in ("A", "B", "C"))

    class _Browser(QWidget):
        def __init__(self):
            super().__init__()
            self._thumb_grid = _FakeGrid([a, b, c])

        def _on_species_edit_requested(self, p):
            pass

    parent = _Browser()
    try:
        menu = rbw._build_context_menu(parent, b, "/x")
        labels = [act.text() for act in menu.actions()]
        assert any("3" in lb for lb in labels), \
            f"菜单里应写明选中张数，实际: {labels}"
    finally:
        parent.close()


# ── E. 内部缓存预览图绝不能被当成用户照片 ──────────────────────────────────
#     Internal cache previews must never be treated as user photos.

def test_cache_path_detected_for_relative_paths():
    """
    缓存判定必须同时认得相对路径与绝对路径。

    ``.superpicky/cache/…`` 这种相对写法（DB 里存的就是它）没有前导斜杠，
    只按 "/.superpicky/" 匹配会漏判，预览图就会被当成用户照片搬走。
    """
    from core.rating_mover import _is_internal_cache_path

    assert _is_internal_cache_path(".superpicky/cache/temp_preview/a.jpg")
    assert _is_internal_cache_path("/vol/2026-09-04/.superpicky/cache/temp_preview/a.jpg")
    assert _is_internal_cache_path("tmp_a.jpg")
    assert not _is_internal_cache_path("家燕/3星_优选/a.jpg")
    assert not _is_internal_cache_path("")


def test_species_change_does_not_move_cached_preview_into_photo_folder(tmp_path):
    """
    改鸟种不得把 .superpicky 缓存预览图搬进鸟种目录。

    现网后果：预览图被搬到输出目录后，再处理时与 RAW 同 prefix，organize
    阶段会用它覆盖 RAW 的 current_path，RAW 从此被永久落在旧目录。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    cache_jpg = _touch(
        os.path.join(root, ".superpicky", "cache", "temp_preview", "DSC_1234.jpg")
    )
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    db.update_photo("DSC_1234",
                    {"temp_jpeg_path": ".superpicky/cache/temp_preview/DSC_1234.jpg"})
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "temp_jpeg_path": cache_jpg,
        "rating": 3,
    }

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234"
    )

    assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.NEF"))
    assert not os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.jpg")), \
        "缓存预览图不得被搬进鸟种目录"
    assert os.path.exists(cache_jpg), "缓存预览应留在原处"


def test_rating_move_does_not_move_cached_preview(tmp_path):
    """改星级同样不得搬走缓存预览图——现网数据正是被这条路径污染的。"""
    from core.rating_mover import move_photo_on_metadata_change

    root = str(tmp_path)
    _touch(os.path.join(root, "其他鸟类", "1星_普通", "DSC_1234.NEF"))
    cache_jpg = _touch(
        os.path.join(root, ".superpicky", "cache", "temp_preview", "DSC_1234.jpg")
    )
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "其他鸟类/1星_普通/DSC_1234.NEF"),
        "temp_jpeg_path": cache_jpg,
    }

    move_photo_on_metadata_change(
        root, photo, 2, "", "species-first", None, "DSC_1234"
    )

    assert os.path.exists(os.path.join(root, "其他鸟类/2星_良好/DSC_1234.NEF"))
    assert not os.path.exists(os.path.join(root, "其他鸟类/2星_良好/DSC_1234.jpg"))
    assert os.path.exists(cache_jpg)


def test_real_companion_jpeg_is_still_moved(tmp_path):
    """
    真正的配套 JPEG（RAW+JPEG 双格式拍摄，与 RAW 同目录）必须照常一起移动，
    不能被上面的缓存排除误伤。
    """
    from core.rating_mover import change_bird_species

    root = str(tmp_path)
    _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.NEF"))
    companion = _touch(os.path.join(root, "白喉抚蜜鸟", "3星_优选", "DSC_1234.jpg"))
    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/3星_优选/DSC_1234.NEF", "白喉抚蜜鸟")
    db.update_photo("DSC_1234", {"temp_jpeg_path": "白喉抚蜜鸟/3星_优选/DSC_1234.jpg"})
    photo = {
        "filename": "DSC_1234",
        "current_path": os.path.join(root, "白喉抚蜜鸟/3星_优选/DSC_1234.NEF"),
        "temp_jpeg_path": companion,
        "rating": 3,
    }

    change_bird_species(
        root, photo, "家燕", "Barn Swallow", "species-first", db, "DSC_1234"
    )

    assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.NEF"))
    assert os.path.exists(os.path.join(root, "家燕/3星_优选/DSC_1234.jpg")), \
        "真配套 JPEG 应跟着 RAW 一起走"


def test_organize_jpeg_does_not_override_raw_current_path():
    """
    整理阶段：同一 prefix 既有 RAW 又有 JPEG 时，current_path 必须指向 RAW。

    现网损坏根因——RAW 与同名 JPEG 共用 prefix，原代码对两者都写
    current_path，后处理的 JPEG 覆盖了 RAW 的路径，RAW 从此从库里消失。
    XMP 早有这道跳过保护（见 photo_processor 里的注释），JPEG 一直没有。
    """
    from core.photo_processor import compute_organize_db_updates

    files = [
        {"filename": "DSC_1234.NEF", "folder": "家燕/3星_优选"},
        {"filename": "DSC_1234.xmp", "folder": "家燕/3星_优选"},
        {"filename": "DSC_1234.jpg", "folder": "家燕/3星_优选"},
    ]

    updates = compute_organize_db_updates(files)

    assert updates["DSC_1234"]["current_path"] == os.path.join("家燕/3星_优选", "DSC_1234.NEF")
    assert updates["DSC_1234"]["temp_jpeg_path"] == os.path.join("家燕/3星_优选", "DSC_1234.jpg")


def test_organize_pure_jpeg_still_uses_jpeg_as_current_path():
    """纯 JPEG 照片（没有 RAW）时，current_path 当然还是那个 JPEG。"""
    from core.photo_processor import compute_organize_db_updates

    files = [{"filename": "DSC_9999.jpg", "folder": "家燕/3星_优选"}]

    updates = compute_organize_db_updates(files)

    assert updates["DSC_9999"]["current_path"] == os.path.join("家燕/3星_优选", "DSC_9999.jpg")
    assert updates["DSC_9999"]["temp_jpeg_path"] == os.path.join("家燕/3星_优选", "DSC_9999.jpg")


def test_organize_xmp_never_becomes_current_path():
    """XMP 侧车永远不能当 current_path（既有保护，一并钉死防回归）。"""
    from core.photo_processor import compute_organize_db_updates

    files = [
        {"filename": "DSC_1234.xmp", "folder": "家燕/3星_优选"},
        {"filename": "DSC_1234.NEF", "folder": "家燕/3星_优选"},
    ]

    updates = compute_organize_db_updates(files)

    assert updates["DSC_1234"]["current_path"].endswith(".NEF")


# ── F. 合并报告模式下的纠错记录 / Corrections in merged multi-dir mode ──────

def _merged_db(tmp_path, days=("2026-09-02", "2026-09-03")):
    """建一个两目录的合并库，每个目录各一张照片。"""
    from tools.report_db import ReportDB
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    subs = []
    for i, day in enumerate(days):
        sub = os.path.join(root, day)
        rel = f"白喉抚蜜鸟/3星_优选/DSC_{i}.NEF"
        _touch(os.path.join(sub, rel))
        db = ReportDB(sub)
        db.insert_photo({
            "filename": f"DSC_{i}", "current_path": rel, "original_path": rel,
            "bird_species_cn": "白喉抚蜜鸟", "bird_species_en": "White-throated Honeyeater",
            "rating": 3,
        })
        db.close()
        subs.append(sub)
    return MergedReportDB(root, subs), root, subs


def test_merged_db_records_correction_into_the_right_sub_database(tmp_path):
    """
    合并模式下改鸟种，纠错记录必须真的落库，并落进照片所属的那个子目录。

    现网表现：MergedReportDB 根本没有 insert_correction，调用抛
    AttributeError 被上层 except 吞掉——用户所有目录的 corrections 表
    长期为 0 条，纠错样本一条没攒下。
    """
    from tools.report_db import ReportDB

    mdb, root, subs = _merged_db(tmp_path)
    try:
        mdb.insert_correction({
            "filename": "DSC_1",
            "wrong_cn": "白喉抚蜜鸟", "wrong_en": "White-throated Honeyeater",
            "corrected_model_class_id": 42,
            "corrected_cn": "家燕", "corrected_en": "Barn Swallow",
            "birdid_confidence": 0.83,
        })
    finally:
        mdb.close()

    # 只能落在 DSC_1 所在的第二个子目录
    db1 = ReportDB(subs[1])
    db0 = ReportDB(subs[0])
    try:
        rows1 = db1.get_corrections()
        rows0 = db0.get_corrections()
    finally:
        db1.close(); db0.close()

    assert len(rows1) == 1, "纠错应写入该照片所属子库"
    assert rows1[0]["corrected_cn"] == "家燕"
    assert rows1[0]["wrong_cn"] == "白喉抚蜜鸟"
    assert rows0 == [], "不该写进别的子库"


def test_merged_db_reads_corrections_across_all_sub_databases(tmp_path):
    """「提交本次纠错」要能读到合并库里各子目录的全部纠错记录。"""
    mdb, root, subs = _merged_db(tmp_path)
    try:
        mdb.insert_correction({"filename": "DSC_0", "wrong_cn": "白喉抚蜜鸟",
                               "corrected_cn": "家燕"})
        mdb.insert_correction({"filename": "DSC_1", "wrong_cn": "白喉抚蜜鸟",
                               "corrected_cn": "大山雀"})
        rows = mdb.get_corrections()
    finally:
        mdb.close()

    assert sorted(r["corrected_cn"] for r in rows) == ["大山雀", "家燕"]


def test_species_edit_in_merged_mode_records_correction(tmp_path, monkeypatch):
    """
    端到端：合并模式的浏览器里改鸟种，纠错记录必须落库（此前静默丢失）。
    """
    import ui.results_browser_window as rbw
    from tools.report_db import ReportDB

    _stub_species_dialog(monkeypatch, "家燕", "Barn Swallow", "Hirundo rustica")

    root = str(tmp_path)
    for i, day in enumerate(("2026-09-02", "2026-09-03")):
        sub = os.path.join(root, day)
        rel = f"白喉抚蜜鸟/3星_优选/DSC_{i}.NEF"
        _touch(os.path.join(sub, rel))
        db = ReportDB(sub)
        db.insert_photo({"filename": f"DSC_{i}", "current_path": rel,
                         "original_path": rel, "bird_species_cn": "白喉抚蜜鸟",
                         "rating": 3})
        db.close()

    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    _settle_grid(win)
    try:
        assert win._is_merged, "应进入合并模式"
        photo = next(p for p in win._filtered_photos if p["filename"] == "DSC_0")
        win._on_species_edit_requested(photo)
        _join_worker_threads()
    finally:
        _join_worker_threads()
        win.close()

    db0 = ReportDB(os.path.join(root, "2026-09-02"))
    try:
        rows = db0.get_corrections()
    finally:
        db0.close()
    assert len(rows) == 1
    assert rows[0]["corrected_cn"] == "家燕"


def test_merged_correction_uses_photo_key_when_filenames_collide(tmp_path):
    """
    合并库里两个子目录有同名照片时（相机计数器循环，很常见），
    必须靠 (source_dir, filename) 精确定位，不能因为重名就丢掉纠错记录。
    """
    from tools.report_db import ReportDB
    from tools.merged_report_db import MergedReportDB

    root = str(tmp_path)
    subs = []
    for day in ("2026-09-02", "2026-09-03"):
        sub = os.path.join(root, day)
        rel = "白喉抚蜜鸟/3星_优选/_Z9W0001.NEF"
        _touch(os.path.join(sub, rel))
        db = ReportDB(sub)
        db.insert_photo({"filename": "_Z9W0001", "current_path": rel,
                         "original_path": rel, "bird_species_cn": "白喉抚蜜鸟",
                         "rating": 3})
        db.close()
        subs.append(sub)

    mdb = MergedReportDB(root, subs)
    try:
        mdb.insert_correction({
            "photo_key": ("2026-09-03", "_Z9W0001"),
            "filename": "_Z9W0001",
            "wrong_cn": "白喉抚蜜鸟", "corrected_cn": "家燕",
        })
    finally:
        mdb.close()

    db1 = ReportDB(subs[1]); db0 = ReportDB(subs[0])
    try:
        rows1, rows0 = db1.get_corrections(), db0.get_corrections()
    finally:
        db1.close(); db0.close()

    assert len(rows1) == 1, "应精确写入 photo_key 指定的子库"
    assert rows0 == [], "同名的另一子库不得被写入"


def test_correction_tracker_passes_photo_key_through(tmp_path):
    """
    CorrectionTracker 必须把 photo_key 透传给 DB，否则合并模式下重名照片
    的纠错仍然会丢——UI 层拿得到 source_dir，不传等于白拿。
    """
    from core.correction_tracker import CorrectionTracker

    class _FakeDB:
        def __init__(self): self.rows = []
        def insert_correction(self, data): self.rows.append(data)

    class _FakeBirdDB:
        def get_class_id_by_scientific_name(self, latin, english_name=None):
            return 7

    db = _FakeDB()
    tracker = CorrectionTracker(db, _FakeBirdDB())
    tracker.record_correction(
        filename="_Z9W0001", wrong_cn="白喉抚蜜鸟", wrong_en=None,
        corrected_cn="家燕", corrected_en="Barn Swallow",
        corrected_latin="Hirundo rustica", birdid_confidence=0.9,
        photo_key=("2026-09-03", "_Z9W0001"),
    )

    assert db.rows[0]["photo_key"] == ("2026-09-03", "_Z9W0001")
    assert db.rows[0]["corrected_model_class_id"] == 7


# ── G. RAW 没搬走就不许改 DB / Never update the DB when the RAW stayed ──────

def test_rating_move_does_not_update_db_when_raw_stays_behind(tmp_path):
    """
    改星级时 RAW 因目标同名文件占位而没搬走，就不能只搬 JPEG 还照改 DB。

    改鸟种早有这道保护（"RAW 没搬走就不改 DB"），改星级一直没有：它只要
    RAW 或 JPEG 任一搬成功就往下走。结果是 DB 与磁盘各说各话——现网那两张
    照片的 RAW 被永久落在旧目录，正是这类不一致的开端。
    """
    from core.rating_mover import move_photo_on_metadata_change

    root = str(tmp_path)
    src_raw = _touch(os.path.join(root, "白喉抚蜜鸟", "1星_普通", "DSC_1234.NEF"))
    src_jpg = _touch(os.path.join(root, "白喉抚蜜鸟", "1星_普通", "DSC_1234.jpg"))
    # 目标目录已存在同名 RAW，占住位置 → RAW 搬不过去，JPEG 却能
    _touch(os.path.join(root, "白喉抚蜜鸟", "2星_良好", "DSC_1234.NEF"))

    db = _make_db(root, "DSC_1234", "白喉抚蜜鸟/1星_普通/DSC_1234.NEF", "白喉抚蜜鸟",
                  rating=1)
    db.update_photo("DSC_1234", {"temp_jpeg_path": "白喉抚蜜鸟/1星_普通/DSC_1234.jpg"})
    photo = {
        "filename": "DSC_1234",
        "current_path": src_raw,
        "temp_jpeg_path": src_jpg,
        "bird_species_cn": "白喉抚蜜鸟",
    }

    result = move_photo_on_metadata_change(
        root, photo, 2, "白喉抚蜜鸟", "species-first", db, "DSC_1234"
    )

    assert result is False, "RAW 没搬走就该判定为失败"
    assert os.path.exists(src_raw), "RAW 仍在原处"
    row = db.get_photo("DSC_1234")
    assert row["current_path"] == os.path.join("白喉抚蜜鸟", "1星_普通", "DSC_1234.NEF"), \
        "DB 不得指向一个 RAW 并不在的位置"


# ── H. 脏数据修复脚本 / The library repair script ───────────────────────────

def _write_jpeg(path: str, dcraw: bool) -> str:
    """
    造一张 jpg：dcraw=True 时打上 SuperPicky 生成物的 Software 标记。
    dcraw=True marks it as a SuperPicky-generated preview.
    """
    _make_jpeg(path)
    if dcraw:
        from tools.exiftool_manager import get_exiftool_manager
        get_exiftool_manager().set_metadata(path, {"Software": "dcraw v9.26"})
    return path


def _repair_db(root: str, filename: str, current: str, original: str,
               temp_jpeg=None):
    from tools.report_db import ReportDB
    db = ReportDB(root)
    db.insert_photo({
        "filename": filename, "current_path": current,
        "original_path": original, "temp_jpeg_path": temp_jpeg,
        "bird_species_cn": "家燕", "rating": 3,
    })
    db.close()


def test_repair_deletes_stray_preview_beside_its_raw(tmp_path, _embedded_write_mode):
    """误搬到照片目录、且同名 RAW 就在旁边的生成预览图 —— 删。"""
    from scripts_dev.repair_stray_previews import plan

    root = str(tmp_path)
    _touch(os.path.join(root, "家燕", "3星_优选", "DSC_1.NEF"))
    _write_jpeg(os.path.join(root, "家燕", "3星_优选", "DSC_1.jpg"), dcraw=True)
    _repair_db(root, "DSC_1", "家燕/3星_优选/DSC_1.NEF", "DSC_1.NEF")

    to_delete, _, _, _ = plan(root)

    assert to_delete == [os.path.join("家燕", "3星_优选", "DSC_1.jpg")]


def test_repair_never_deletes_camera_jpeg(tmp_path, _embedded_write_mode):
    """
    相机直出的 JPEG（没有 dcraw 标记）绝不能删——那是用户 RAW+JPEG
    双格式拍摄的真照片。
    """
    from scripts_dev.repair_stray_previews import plan

    root = str(tmp_path)
    _touch(os.path.join(root, "家燕", "3星_优选", "DSC_1.NEF"))
    _write_jpeg(os.path.join(root, "家燕", "3星_优选", "DSC_1.jpg"), dcraw=False)
    _repair_db(root, "DSC_1", "家燕/3星_优选/DSC_1.NEF", "DSC_1.NEF")

    to_delete, _, _, _ = plan(root)

    assert to_delete == []


def test_repair_never_deletes_a_lone_jpeg_without_its_raw(tmp_path, _embedded_write_mode):
    """
    身边没有同名 RAW 的 jpg 一律不删——它可能是这张照片仅存的图像文件
    （例如用户在外部工具里改过名）。
    """
    from scripts_dev.repair_stray_previews import plan

    root = str(tmp_path)
    _write_jpeg(os.path.join(root, "家燕", "3星_优选", "DSC_9-2.jpg"), dcraw=True)
    _repair_db(root, "DSC_9", "家燕/3星_优选/DSC_9.NEF", "DSC_9.NEF")

    to_delete, _, _, _ = plan(root)

    assert to_delete == []


def test_repair_moves_stranded_raw_to_where_the_edit_intended(tmp_path, _embedded_write_mode):
    """
    RAW 被落在旧鸟种目录、DB 却指向新目录里的预览图 —— 把 RAW 搬过去，
    并让 current_path 指向它（现网 _Z9W2571 就是这个形态）。
    """
    from scripts_dev.repair_stray_previews import plan, apply_plan
    from tools.report_db import ReportDB

    root = str(tmp_path)
    _touch(os.path.join(root, "其他鸟类", "2星_良好", "DSC_1.NEF"))
    _write_jpeg(os.path.join(root, "红头摄蜜鸟", "2星_良好", "DSC_1.jpg"), dcraw=True)
    _repair_db(root, "DSC_1", "红头摄蜜鸟/2星_良好/DSC_1.jpg", "DSC_1.NEF")

    stats = apply_plan(root)

    assert stats["current_path_fixed"] == 1
    assert os.path.exists(os.path.join(root, "红头摄蜜鸟/2星_良好/DSC_1.NEF"))
    assert not os.path.exists(os.path.join(root, "其他鸟类/2星_良好/DSC_1.NEF"))
    assert not os.path.exists(os.path.join(root, "红头摄蜜鸟/2星_良好/DSC_1.jpg"))
    db = ReportDB(root)
    try:
        assert db.get_photo("DSC_1")["current_path"] == os.path.join(
            "红头摄蜜鸟", "2星_良好", "DSC_1.NEF")
    finally:
        db.close()
    # 幂等：再跑一次什么都不做
    assert apply_plan(root) == {"deleted": 0, "current_path_fixed": 0,
                                "temp_jpeg_cleared": 0, "relinked": 0}


def test_repair_is_a_noop_without_a_report_db(tmp_path):
    """目录里没有 report.db 时安全返回四个空列表（不能解包崩溃）。"""
    from scripts_dev.repair_stray_previews import plan

    assert plan(str(tmp_path)) == ([], [], [], [])
