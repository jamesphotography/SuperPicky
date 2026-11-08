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


def test_deferred_switch_from_finished_does_not_reselect(tmp_path, monkeypatch):
    """
    从 _on_finished 应用挂起的关闭：不重放日志、不额外排自动打开浏览器，
    directory_path 仍是第一个目录，输入框可见。
    A deferred switch-off applied from _on_finished must not replay the console
    log or schedule another browser open.
    """
    import ui.main_window as mw
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        w.config.set_keep_temp_files(True)
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        w._queue = object()
        _set_lab(w, False)
        assert w._lab_multi_pending_off is True
        w._queue = None

        scheduled = []
        monkeypatch.setattr(mw.QTimer, "singleShot",
                            staticmethod(lambda ms, cb: scheduled.append((ms, cb))))
        replays = []
        monkeypatch.setattr(w, "_restore_console_from_log", lambda d: replays.append(d) or False)
        selections = []
        monkeypatch.setattr(w, "_handle_directory_selection", lambda d: selections.append(d))
        monkeypatch.setattr(w, "_show_statistics_report", lambda stats: None)
        monkeypatch.setattr(w, "_play_completion_sound", lambda: None)
        monkeypatch.setattr(w, "_apply_current_results", lambda d, st: None)

        w._on_finished({})
        # 只有 _on_finished 自己排的那一次打开 / only _on_finished's own scheduled open
        opens = [cb for _ms, cb in scheduled
                 if getattr(cb, "__name__", "") in ("_auto_open_results", "_open_finder_results")]
        assert len(opens) == 1
        assert replays == []
        assert selections == []
        assert w._folder_list.isHidden()
        assert not w.dir_input.isHidden()
        assert w.directory_path == a
        assert w.dir_input.text() == a
    finally:
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


class _AliveWorker:
    """存活的假工作线程 / A fake worker that is alive."""

    def is_alive(self) -> bool:
        return True


class _StartedWorker:
    """
    假 WorkerThread：start() 不真跑，is_alive() 恒 True（模拟处理中）。
    Fake WorkerThread: start() is a no-op and it reports alive.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.args = args

    def start(self) -> None:
        """不真跑 / no-op."""

    def is_alive(self) -> bool:
        return True


def _quiet_finish(w, monkeypatch) -> None:
    """把 _on_finished 的副作用（报告/提示音/识鸟面板/打开结果）打桩 / Stub finish side effects."""
    import ui.main_window as mw
    monkeypatch.setattr(w, "_show_statistics_report", lambda stats: None)
    monkeypatch.setattr(w, "_play_completion_sound", lambda: None)
    monkeypatch.setattr(w, "_apply_current_results", lambda d, st: None)
    monkeypatch.setattr(w, "_auto_open_results", lambda path=None: None)
    monkeypatch.setattr(w, "_open_finder_results", lambda path=None: None)


def test_switch_on_deferred_while_worker_alive(tmp_path, monkeypatch):
    """
    单目录处理中打开多目录开关：先挂起——列表不出现、不放入目录，「重置」保持禁用、
    开始按钮仍是「停止」；处理结束的槽里才切到列表并放入当前目录。
    Switching on mid-run is deferred: Reset stays disabled, Start stays "Stop",
    the list is not seeded until the run ends.
    """
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        _quiet_finish(w, monkeypatch)
        w._handle_directory_selection(a)
        w.worker = _AliveWorker()
        w._update_action_buttons("processing")
        _set_lab(w, True)
        assert w._folder_list.isHidden()
        assert w._folder_list.folders() == []
        assert not w.reset_btn.isEnabled()
        assert w.start_btn.text() == w.i18n.t("labels.stop_processing")
        assert w.directory_path == a
        w._on_finished({"total": 1})
        assert not w._folder_list.isHidden()
        assert w.dir_input.isHidden()
        assert w._folder_list.folders() == [a]
        assert w.directory_path == a
    finally:
        w.worker = None
        w.close()


def test_switch_on_then_off_while_running_cancels_pending(tmp_path, monkeypatch):
    """处理中开了又关：两次都挂起/取消，结束后仍是单目录界面。"""
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        _quiet_finish(w, monkeypatch)
        w._handle_directory_selection(a)
        w.worker = _AliveWorker()
        _set_lab(w, True)
        _set_lab(w, False)
        w._on_finished({"total": 1})
        assert w._folder_list.isHidden()
        assert not w.dir_input.isHidden()
        assert w.directory_path == a
    finally:
        w.worker = None
        w.close()


@pytest.mark.parametrize("slot", ["finished", "cancelled", "error"])
def test_single_run_with_lab_on_locks_list(tmp_path, monkeypatch, slot):
    """
    开关开启、列表只有 1 个目录时单目录处理：处理中列表/最近目录不可编辑，结束后恢复。
    Lab on with one folder: list editing and the recent menu are locked during
    the run and unlocked in each terminal slot.
    """
    import ui.main_window as mw
    a = _photo_dir(tmp_path, "a")
    monkeypatch.setattr(mw, "WorkerThread", _StartedWorker)
    w = _window()
    try:
        _quiet_finish(w, monkeypatch)
        _set_lab(w, True)
        w._folder_list.set_folders([a])
        w._launch_worker(a, False, None)
        assert w._folder_list._editable is False
        assert not w._recent_menu.isEnabled()
        assert not w._folder_list.add_button().isEnabled()
        {"finished": lambda: w._on_finished({"total": 1}),
         "cancelled": lambda: w._on_cancelled({}),
         "error": lambda: w._on_error("boom")}[slot]()
        assert w._folder_list._editable is True
        assert w._recent_menu.isEnabled()
        assert w._folder_list.add_button().isEnabled()
    finally:
        w.worker = None
        w.close()


def test_single_run_with_lab_off_leaves_inputs_as_dev(tmp_path, monkeypatch):
    """开关关闭时单目录处理：输入框、浏览按钮、最近目录菜单都不禁用（与 dev 相同）。"""
    import ui.main_window as mw
    a = _photo_dir(tmp_path, "a")
    monkeypatch.setattr(mw, "WorkerThread", _StartedWorker)
    w = _window()
    try:
        w.directory_path = a
        w._launch_worker(a, False, None)
        assert w.dir_input.isEnabled()
        assert w._browse_btn.isEnabled()
        assert w._recent_menu.isEnabled()
    finally:
        w.worker = None
        w.close()


def _batch_parent(root: Path, name: str = "parent") -> str:
    """
    批次模式处理过的父目录：两个子批次各有 report.db，照片在星级目录里，父目录自己没有 report.db。
    A batch-mode processed parent (report.db only in two sub-batches).
    """
    parent = root / name
    for day in ("day1", "day2"):
        (parent / day / ".superpicky").mkdir(parents=True)
        (parent / day / ".superpicky" / "report.db").write_bytes(b"")
        (parent / day / "3星_优选").mkdir()
        (parent / day / "3星_优选" / "a.jpg").write_bytes(b"x")
    return os.path.normpath(str(parent))


def test_recent_group_with_batch_parent_restores_without_loss(tmp_path):
    """最近目录组里有批次模式父目录：整组放回，不丢；按钮为 has_results（可查看结果）。"""
    a = _photo_dir(tmp_path, "a")
    parent = _batch_parent(tmp_path)
    w = _window()
    try:
        _set_lab(w, True)
        w._restore_recent_group([a, parent])
        assert w._folder_list.folders() == [a, parent]
        assert w.view_results_btn.isVisibleTo(w)
    finally:
        w.close()


def test_recent_group_rejected_folder_logged(tmp_path, monkeypatch):
    """目录组里被拒（无照片）的目录不再悄悄丢掉：写一条 warning 日志。"""
    a = _photo_dir(tmp_path, "a")
    empty = tmp_path / "empty"
    empty.mkdir()
    empty = os.path.normpath(str(empty))
    w = _window()
    try:
        _set_lab(w, True)
        logs = []
        monkeypatch.setattr(w, "_log", lambda m, tag=None: logs.append((m, tag)))
        w._restore_recent_group([a, empty])
        assert w._folder_list.folders() == [a]
        assert any(tag == "warning" and empty in m for m, tag in logs)
    finally:
        w.close()


def test_single_recent_rejected_logged(tmp_path, monkeypatch):
    """开关开启时点一个已不能加入的单个最近目录：写 warning 日志，而不是悄悄清空。"""
    a = _photo_dir(tmp_path, "a")
    empty = tmp_path / "empty"
    empty.mkdir()
    empty = os.path.normpath(str(empty))
    w = _window()
    try:
        _set_lab(w, True)
        w._folder_list.set_folders([a])
        w.config.add_recent_directory(empty)
        w._refresh_recent_menu()
        logs = []
        monkeypatch.setattr(w, "_log", lambda m, tag=None: logs.append((m, tag)))
        action = next(x for x in w._recent_menu.actions() if x.text() == empty)
        action.trigger()
        assert any(tag == "warning" and empty in m for m, tag in logs)
    finally:
        w.close()


def test_switch_on_seed_rejected_logged(tmp_path, monkeypatch):
    """打开开关时当前目录放不进列表（无照片）：写 warning 日志。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    empty = os.path.normpath(str(empty))
    w = _window()
    try:
        w.directory_path = empty
        logs = []
        monkeypatch.setattr(w, "_log", lambda m, tag=None: logs.append((m, tag)))
        _set_lab(w, True)
        assert any(tag == "warning" and empty in m for m, tag in logs)
    finally:
        w.close()


def test_remove_to_one_is_not_a_selection(tmp_path, monkeypatch):
    """
    从 2 个移除到 1 个（剩下的不是当前目录）：只刷新状态，不当作「选择目录」——
    不重放日志、不自动打开浏览器；directory_path 与输入框指向剩下的目录。
    Removing down to one folder is not choosing it: no console replay, no auto-open.
    """
    import ui.main_window as mw
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    os.makedirs(os.path.join(b, ".superpicky"))
    Path(b, ".superpicky", "report.db").write_bytes(b"")
    w = _window()
    try:
        w.config.set_keep_temp_files(True)
        _set_lab(w, True)
        w._folder_list.set_folders([a, b])
        assert w.directory_path == a
        selections = []
        monkeypatch.setattr(w, "_handle_directory_selection", lambda d: selections.append(d))
        scheduled = []
        monkeypatch.setattr(mw.QTimer, "singleShot",
                            staticmethod(lambda ms, cb: scheduled.append((ms, cb))))
        w._folder_list.remove_buttons()[0].click()
        assert selections == []
        assert not [cb for _ms, cb in scheduled
                    if getattr(cb, "__name__", "") == "_auto_open_results"]
        assert w.directory_path == b
        assert w.dir_input.text() == b
        assert w.view_results_btn.isVisibleTo(w)
    finally:
        w.close()


def test_switch_on_after_finished_slot_applies_when_worker_exits(tmp_path, monkeypatch):
    """
    结束槽已执行、线程还没退出时打开开关：线程退出后由复查兜底应用，不必等下一次处理。
    A switch-on flipped after the finished slot but before the thread exits is
    applied by the poll once the thread is gone.
    """
    import ui.main_window as mw
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        scheduled = []
        monkeypatch.setattr(mw.QTimer, "singleShot",
                            staticmethod(lambda ms, cb: scheduled.append(cb)))
        w._handle_directory_selection(a)
        worker = _AliveWorker()
        w.worker = worker
        _set_lab(w, True)
        assert w._folder_list.isHidden()
        polls = [cb for cb in scheduled if getattr(cb, "__name__", "") == "_poll_pending_lab_switch"]
        assert polls
        w.worker = None  # 线程退出 / the thread exits
        polls[-1]()
        assert not w._folder_list.isHidden()
        assert w._folder_list.folders() == [a]
    finally:
        w.worker = None
        w.close()


def test_counts_refreshed_after_group_reset_or_queue_and_single_reset(tmp_path, monkeypatch):
    """
    整组重置 / 队列结束（_restore_multi_folder_ui）和列表里只有一个目录时的单目录重置
    （_end_single_reset）之后，列表张数都要重新统计。
    Rows are recounted after a group reset / queue end and after a single
    reset while the multi-folder switch is on.
    """
    w = _window()
    try:
        _set_lab(w, True)
        a = _photo_dir(tmp_path, "a")
        b = _photo_dir(tmp_path, "b")
        w._folder_list.set_folders([a, b])
        calls = []
        monkeypatch.setattr(w._folder_list, "refresh_counts", lambda: calls.append(1))
        w._restore_multi_folder_ui()
        assert calls == [1]
        w._end_single_reset()
        assert calls == [1, 1]
        _set_lab(w, False)
        w._end_single_reset()
        assert calls == [1, 1]          # 开关关着不统计 / not when the switch is off
    finally:
        w.close()
