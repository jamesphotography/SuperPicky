# -*- coding: utf-8 -*-
"""
结果浏览器右键「复制到文件夹」的界面回归测试（用户反馈：想把选中的照片批量
复制到硬盘）。

- 菜单文案带张数，范围规则与「修改鸟种」「删除」一致；
- 真实走一遍：选文件夹 → 后台复制（连同 .xmp 边车）→ 结果弹窗，记住上次的文件夹；
- 目标是照片所在文件夹、磁盘空间不够时直接拦下，什么都不复制。

UI tests for the browser's "Copy to Folder": count in the label, the full
flow including sidecars and the remembered folder, and refusals.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


def _pump_until(cond, timeout: float = 5.0) -> bool:
    """处理事件直到 cond() 为真或超时 / Pump events until cond() or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """
    三张带 .xmp 边车的照片的真实浏览器；选文件夹和结果弹窗换成假的。

    A real browser with three photos (each with a sidecar); the folder picker
    and the result dialog are faked.
    """
    import ui.results_browser_window as rbw
    from PySide6.QtWidgets import QFileDialog
    from tools.report_db import ReportDB
    from ui.custom_dialogs import StyledMessageBox

    root = tmp_path / "photos"
    root.mkdir()
    db = ReportDB(str(root))
    for i in (1, 2, 3):
        (root / f"IMG_{i}.CR3").write_bytes(f"raw{i}".encode())
        (root / f"IMG_{i}.xmp").write_text(f"<x 鸟名='白鹭{i}'/>", encoding="utf-8")
        db.insert_photo({"filename": f"IMG_{i}", "current_path": f"IMG_{i}.CR3",
                         "original_path": f"IMG_{i}.CR3", "rating": 2, "has_bird": 1,
                         "focus_status": "BEST", "is_flying": 0})
    db.close()

    dest = tmp_path / "dest"
    dest.mkdir()
    state = {"pick": str(dest), "messages": [], "dialog_start": None}

    def _pick(_parent, _title, start):
        state["dialog_start"] = start
        return state["pick"]

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(_pick))
    monkeypatch.setattr(StyledMessageBox, "exec",
                        lambda self: state["messages"].append("result") or StyledMessageBox.Cancel)
    for name in ("warning", "information"):
        monkeypatch.setattr(StyledMessageBox, name, staticmethod(
            lambda _p, _t, msg, *a, **k: state["messages"].append(msg)))

    win = rbw.ResultsBrowserWindow()
    win.open_directory(str(root))
    _pump_until(lambda: bool(win._thumb_grid._photos))
    QApplication.processEvents()
    yield win, root, dest, state
    win.close()


def _copy_action(win, photo):
    from tools.i18n import get_i18n
    from ui.results_browser_window import _build_context_menu
    i18n = get_i18n()
    labels = {i18n.t("browser.ctx_copy_to_folder")} | {
        i18n.t("browser.ctx_copy_to_folder_batch").format(count=n) for n in range(2, 10)}
    menu = _build_context_menu(win, photo, win._directory)
    for act in menu.actions():
        if act.text() in labels:
            return act
    raise AssertionError([a.text() for a in menu.actions()])


def test_label_shows_count_for_checked_set(browser):
    from tools.i18n import get_i18n
    win, _, _, _ = browser
    win._thumb_grid.select_all()
    act = _copy_action(win, win._filtered_photos[0])
    assert act.text() == get_i18n().t("browser.ctx_copy_to_folder_batch").format(count=3)


def test_copies_checked_photos_with_sidecars_and_remembers_folder(browser):
    from advanced_config import get_advanced_config
    win, root, dest, state = browser
    win._thumb_grid.select_all()
    _copy_action(win, win._filtered_photos[0]).trigger()
    assert _pump_until(lambda: "result" in state["messages"])
    assert sorted(os.listdir(dest)) == sorted(
        f"IMG_{i}{ext}" for i in (1, 2, 3) for ext in (".CR3", ".xmp"))
    assert (dest / "IMG_2.xmp").read_text(encoding="utf-8") == "<x 鸟名='白鹭2'/>"
    # 原文件不动 / originals untouched
    assert (root / "IMG_1.CR3").read_bytes() == b"raw1"
    assert get_advanced_config().get_browser_copy_dir() == os.path.normpath(str(dest))
    assert win._copy_cancel is None


def test_unchecked_photo_copies_only_itself(browser):
    win, _, dest, state = browser
    photos = win._filtered_photos
    win._thumb_grid.select_all()
    win._thumb_grid._multi_selected.discard(photos[2]["filename"])
    _copy_action(win, photos[2]).trigger()
    assert _pump_until(lambda: "result" in state["messages"])
    stem = photos[2]["filename"]
    assert sorted(os.listdir(dest)) == [f"{stem}.CR3", f"{stem}.xmp"]


def test_refuses_the_photos_own_folder(browser):
    from tools.i18n import get_i18n
    win, root, _, state = browser
    state["pick"] = str(root)
    before = sorted(os.listdir(root))
    win._copy_photos_to_folder([win._filtered_photos[0]])
    assert state["messages"] == [get_i18n().t("browser.copy_same_folder")]
    assert sorted(os.listdir(root)) == before


def test_refuses_when_disk_is_too_small(browser, monkeypatch):
    import shutil
    from collections import namedtuple
    win, _, dest, state = browser
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage(100, 100, 1))
    win._copy_photos_to_folder([win._filtered_photos[0]])
    assert len(state["messages"]) == 1 and "result" not in state["messages"]
    assert os.listdir(dest) == []


def test_cancelled_picker_does_nothing(browser):
    win, _, dest, state = browser
    state["pick"] = ""
    win._copy_photos_to_folder([win._filtered_photos[0]])
    assert state["messages"] == [] and os.listdir(dest) == []
