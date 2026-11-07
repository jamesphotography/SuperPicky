# -*- coding: utf-8 -*-
"""
网格里删除折叠的连拍卡片要删整组,不能只删代表那一张。

此前全选删除后总剩几张:一组连拍折叠成一张卡片,删除只删了代表那张,剩下的
成员重新折叠成新卡片,一组 N 张要删 N 轮(用户 2026-11-07 反馈)。
全屏是单张浏览,只删眼前这一张。

Deleting a collapsed burst card in the grid deletes the whole burst; it
used to delete only the representative, so a burst of N took N rounds.
Fullscreen still deletes just the photo on screen.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

import ui.results_browser_window as rbw  # noqa: E402

# 夹具会替换 _confirm_delete,先留住真实实现 / keep the real method before fixtures stub it
_REAL_CONFIRM = rbw.ResultsBrowserWindow._confirm_delete


def _pump(seconds: float = 0.3) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)


def _wait_delete(win, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _app.processEvents()
        if win._delete_cancel is None:
            return
        time.sleep(0.01)
    raise AssertionError("delete not finished")


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """
    5 张单张 + 连拍 A(4 张,burst_001)+ 连拍 B(3 张,burst_002)。
    5 singles + burst A (4) + burst B (3); bursts come from burst_* folders.
    """
    from tools.report_db import ReportDB
    root = str(tmp_path)
    db = ReportDB(root)
    spec = ([(f"S{i}", "") for i in range(5)] + [(f"A{i}", "burst_001") for i in range(4)]
            + [(f"B{i}", "burst_002") for i in range(3)])
    for stem, folder in spec:
        if folder:
            os.makedirs(os.path.join(root, folder), exist_ok=True)
        rel = os.path.join(folder, stem + ".CR3") if folder else stem + ".CR3"
        open(os.path.join(root, rel), "w").close()
        db.insert_photo({"filename": stem, "current_path": rel, "original_path": stem + ".CR3",
                         "rating": 3, "has_bird": 1})
    db.close()

    trashed = []

    def _fake(paths):
        out = []
        for p in paths:
            trashed.append(os.path.splitext(os.path.basename(p))[0])
            os.remove(p)
            out.append(True)
        return out

    monkeypatch.setattr(rbw, "_move_many_to_trash", _fake)
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete",
                        lambda self, photos, jobs, permanent=False, **_kw: "permanent" if permanent else "trash")
    win = rbw.ResultsBrowserWindow()
    win.open_directory(root)
    win.show()
    _pump(0.5)
    yield win, trashed
    win.close()


def _cards(win) -> list:
    return [(p["filename"], p.get("burst_count", 1)) for p in win._thumb_grid._photos]


def test_select_all_delete_clears_bursts_in_one_round(browser):
    win, trashed = browser
    assert ("A0", 4) in _cards(win) and ("B0", 3) in _cards(win)
    win._thumb_grid.select_all()
    win._delete_selected_photos()
    _wait_delete(win)
    _pump()
    assert _cards(win) == []
    assert len(trashed) == 12


def test_single_burst_card_deletes_whole_burst(browser):
    win, trashed = browser
    card = next(p for p in win._filtered_photos if p.get("burst_count") == 3)
    win._delete_photos([card], in_fullscreen=False)
    _wait_delete(win)
    _pump()
    assert sorted(trashed) == ["B0", "B1", "B2"]
    assert all(not name.startswith("B") for name, _ in _cards(win))


def test_fullscreen_deletes_only_photo_on_screen(browser):
    win, trashed = browser
    card = next(p for p in win._filtered_photos if p.get("burst_count") == 4)
    win._enter_fullscreen(card)
    _pump()
    win._on_delete_photo(win._fullscreen._current_photo)
    _wait_delete(win)
    assert len(trashed) == 1 and trashed[0].startswith("A")


def test_expand_dedupes_and_keeps_singles():
    a = {"filename": "A0", "current_path": "/x/burst_001/A0.CR3"}
    b = {"filename": "A1", "current_path": "/x/burst_001/A1.CR3"}
    s = {"filename": "S0", "current_path": "/x/S0.CR3"}
    group = dict(a, is_burst_group=True, burst_count=2, burst_photos=[a, b])
    out = rbw._expand_burst_groups([group, s, a])
    assert [p["filename"] for p in out] == ["A0", "A1", "S0"]


# ── 涉及折叠连拍的确认框 / confirmation when collapsed bursts are involved ──


def test_burst_note_lists_star_breakdown_and_picks(browser):
    """说明里列出几组、几张、各星级张数与皇冠精选张数(用户 2026-11-07 要求)。"""
    win, _ = browser
    members = [{"rating": 3, "picked": 1}, {"rating": 3}, {"rating": 2},
               {"rating": 0}, {"rating": -1}]
    note = win._burst_delete_note([{"burst_photos": members[:3]}, {"burst_photos": members[3:]}])
    assert "2" in note and "5" in note                      # 2 组 5 张 / 2 groups, 5 photos
    assert "3★ 2" in note and "2★ 1" in note and "0★ 1" in note
    assert win.i18n.t("browser.rating_nobird") in note
    assert "👑" in note
    assert win._burst_delete_note([]) == ""


def test_bursts_always_ask_even_with_confirm_off(browser, monkeypatch):
    """关掉确认后,普通删除不弹框,但涉及折叠连拍仍要问,且不给「不再询问」。"""
    from advanced_config import get_advanced_config
    from ui.custom_dialogs import DeleteConfirmDialog
    win, _ = browser
    get_advanced_config().set_delete_confirm(False)
    seen = {}

    def _exec(self):
        seen["keys"] = self.button_keys()
        seen["has_checkbox"] = self.has_checkbox()
        seen["text"] = " ".join(lbl.text() for lbl in self.findChildren(rbw.QLabel))
        self.choose("expand")
        return 0

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _exec)
    card = next(p for p in win._filtered_photos if p.get("burst_count") == 4)
    jobs = [("", [], [])] * 4
    # fixture 把 _confirm_delete 打了桩,这里调原实现 / call the real method
    assert _REAL_CONFIRM(win, [card], jobs, bursts=[card]) == "expand"
    assert "expand" in seen["keys"]
    assert seen["has_checkbox"] is False
    assert "4" in seen["text"]

    # 永久删除请求同样提供「展开」/ permanent requests also offer expand
    assert _REAL_CONFIRM(win, [card], jobs, permanent=True, bursts=[card]) == "expand"

    # 不涉及连拍的普通删除:不弹框,直接回收站 / plain delete stays silent
    def _boom(self):
        raise AssertionError("dialog shown")

    monkeypatch.setattr(DeleteConfirmDialog, "exec", _boom)
    assert _REAL_CONFIRM(win, [card], jobs) == "trash"


def test_expand_choice_deletes_nothing_and_expands(browser, monkeypatch):
    win, trashed = browser
    monkeypatch.setattr(rbw.ResultsBrowserWindow, "_confirm_delete",
                        lambda self, photos, jobs, permanent=False, **kw: "expand")
    card = next(p for p in win._filtered_photos if p.get("burst_count") == 4)
    win._delete_photos([card], in_fullscreen=False)
    _pump()
    assert trashed == []
    names = [name for name, _ in _cards(win)]
    assert all(f"A{i}" in names for i in range(4))           # 组员逐张显示 / members shown
