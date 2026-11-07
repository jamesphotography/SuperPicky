# -*- coding: utf-8 -*-
"""
主窗口多目录界面（实验室开关 lab_multi_folder）与「最近目录组」菜单的测试。

覆盖:
1. 开关关闭时目录列表隐藏、单目录输入框显示；开启后反过来。
2. 列表有两个目录时关闭开关：只剩第一个，directory_path 等于它。
3. 处理中（队列或工作线程在跑）关闭开关：先挂起，_on_cancelled 之后才收起。
4. 开关关闭时「最近目录」菜单里没有目录组；开启时点目录组会调用 set_folders，
   不存在的目录被跳过并写 warning 日志。

Tests for the main window's multi-folder UI (Labs switch) and the
recent-directory-group menu entries.
"""
import os
from pathlib import Path
from typing import List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _not_dangerous(monkeypatch):
    """
    macOS 的 tmp_path 在 /private 下会被判为危险目录，测试里放行。
    tmp_path lives under /private on macOS, which is flagged dangerous; allow it.
    """
    from core import folder_queue
    monkeypatch.setattr(folder_queue, "is_dangerous_root", lambda p: (False, ""))


def _photo_dir(root: Path, name: str) -> str:
    """
    建一个含一张假照片的目录（扫描按扩展名识别照片）。

    参数: root 父目录；name 子目录名。返回: str 目录路径。
    Create a folder with one fake photo. Returns its path.
    """
    d = root / name
    d.mkdir()
    (d / "x.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return os.path.normpath(str(d))


def _window():
    """构造主窗口（conftest 已隔离配置与后台启动）/ Build the main window."""
    from ui.main_window import SuperPickyMainWindow
    return SuperPickyMainWindow()


def _set_lab(w, on: bool) -> None:
    """改开关并应用 / Flip the Labs switch and apply it."""
    w.config.set_lab_multi_folder(on)
    w._apply_lab_multi_folder()


def _menu_texts(w) -> List[str]:
    """最近目录菜单各项文字 / Texts of the recent-dirs menu actions."""
    return [a.text() for a in w._recent_menu.actions()]


def test_switch_off_shows_dir_input_only():
    """开关关闭（默认）：列表隐藏，单目录输入框显示。"""
    w = _window()
    try:
        assert w._folder_list.isHidden()
        assert not w.dir_input.isHidden()
        assert w._queue is None
        assert w._is_multi_mode() is False
        assert w._multi_folders() == []
    finally:
        w.close()


def test_switch_on_swaps_visibility_and_seeds_current_dir(tmp_path):
    """开启后列表显示、输入框隐藏；已选目录成为列表第一项。"""
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        w._handle_directory_selection(a)
        _set_lab(w, True)
        assert not w._folder_list.isHidden()
        assert w.dir_input.isHidden()
        assert w._folder_list.folders() == [a]
        assert w._multi_folders() == [a]
        assert w._is_multi_mode() is False
        assert w.directory_path == a
    finally:
        w.close()


def test_two_folders_banner_and_buttons(tmp_path):
    """两个目录：directory_path=第一个，状态条显示已选 2 个目录，按钮按 report.db 定。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        assert w._is_multi_mode() is True
        assert w.directory_path == a
        assert "2" in w._status_banner.text()
        assert w.start_btn.isEnabled()
        assert not w.view_results_btn.isVisibleTo(w)
        # 任一目录有 report.db -> has_results / any report.db -> has_results
        os.makedirs(os.path.join(b, ".superpicky"))
        Path(b, ".superpicky", "report.db").write_bytes(b"")
        w._folder_list.set_folders([a, b])
        assert w.view_results_btn.isVisibleTo(w)
    finally:
        w.close()


def test_switch_off_keeps_first_folder(tmp_path):
    """列表有两个目录时关开关：只剩第一个，directory_path 等于它。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        _set_lab(w, False)
        assert w._folder_list.isHidden()
        assert not w.dir_input.isHidden()
        assert w.directory_path == a
        assert w.dir_input.text() == a
        assert w._multi_folders() == [a]
        assert w._is_multi_mode() is False
    finally:
        w.close()


def test_switch_off_deferred_while_queue_running(tmp_path):
    """队列运行中关开关：列表不变；队列结束后 _on_cancelled 才收起。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        w._queue = object()
        _set_lab(w, False)
        assert not w._folder_list.isHidden()
        assert w._folder_list.folders() == [a, b]
        assert w._lab_multi_pending_off is True
        w._queue = None
        w._on_cancelled({})
        assert w._folder_list.isHidden()
        assert w.directory_path == a
        assert w._lab_multi_pending_off is False
    finally:
        w.close()


def test_switch_off_deferred_while_worker_alive(tmp_path):
    """
    工作线程存活时关开关先挂起；_on_finished 的槽运行时线程可能还没退出，
    挂起的切换仍要在此时应用。
    The finished slot may run before the thread exits; the pending switch still applies.
    """
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")

    class _AliveWorker:
        def is_alive(self) -> bool:
            return True

    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        w.worker = _AliveWorker()
        _set_lab(w, False)
        assert not w._folder_list.isHidden()
        w._on_error("boom")
        assert w._folder_list.isHidden()
        assert w.directory_path == a
    finally:
        w.worker = None
        w.close()


def test_recent_groups_hidden_when_switch_off(tmp_path):
    """开关关闭时最近目录菜单里没有目录组；开启后出现在单目录之前。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        w.config.add_recent_directory(a)
        w.config.add_recent_directory_group([a, b])
        w._refresh_recent_menu()
        expected_group = w.i18n.t("menu.recent_group", count=2, names="a、b")
        assert expected_group not in _menu_texts(w)
        _set_lab(w, True)
        texts = _menu_texts(w)
        assert expected_group in texts
        assert texts.index(expected_group) < texts.index(a)
    finally:
        w.close()


def test_click_group_restores_and_skips_missing(tmp_path, monkeypatch):
    """点目录组：调用 set_folders，不存在的目录被跳过并写 warning 日志。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    gone = os.path.normpath(str(tmp_path / "gone"))
    w = _window()
    try:
        _set_lab(w, True)
        w.config.add_recent_directory_group([a, gone, b])
        w._refresh_recent_menu()

        calls = []
        orig = w._folder_list.set_folders

        def _spy(paths):
            calls.append(list(paths))
            return orig(paths)

        monkeypatch.setattr(w._folder_list, "set_folders", _spy)
        logs = []
        orig_log = w._log
        monkeypatch.setattr(w, "_log", lambda m, tag=None: (logs.append((m, tag)), orig_log(m, tag)))

        label = w.i18n.t("menu.recent_group", count=3, names="a、gone、b")
        action = next(x for x in w._recent_menu.actions() if x.text() == label)
        action.trigger()
        assert calls == [[a, gone, b]]
        assert w._folder_list.folders() == [a, b]
        assert any(tag == "warning" and gone in m for m, tag in logs)
    finally:
        w.close()


def test_click_single_recent_replaces_list_when_on(tmp_path):
    """开关开启时点单个最近目录：替换整个列表。"""
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    c = _photo_dir(tmp_path, "c")
    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        w.config.add_recent_directory(c)
        w._refresh_recent_menu()
        action = next(x for x in w._recent_menu.actions() if x.text() == c)
        action.trigger()
        assert w._folder_list.folders() == [c]
        assert w.directory_path == c
    finally:
        w.close()


def test_group_label_truncated():
    """目录组标签里的名字超过 60 字符时截断并加「…」。"""
    from ui.main_window import SuperPickyMainWindow
    long_dirs = [os.path.join(os.sep, "x", "n" * 30 + str(i)) for i in range(4)]
    names = SuperPickyMainWindow._group_names_text(long_dirs, "、")
    assert len(names) <= 61
    assert names.endswith("…")
