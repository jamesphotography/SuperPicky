# -*- coding: utf-8 -*-
"""
多目录整组重置（实验室 lab_multi_folder）的主窗口行为测试。

多目录模式下「重置目录」作用于整个列表：只确认一次（列出全部目录），然后按顺序
逐个调用与单目录相同的 _do_reset_directory(skip_exif_reset=False, skip_confirm=True)；
下一个只在上一个重置完成（或出错）之后开始；最后只弹一次汇总提示，列出失败的目录。

大部分测试把 _do_reset_directory 换成记录器，完成由测试直接调用 _on_reset_complete /
_on_reset_error 模拟，再用 processEvents() 冲掉 QTimer.singleShot(0, …)。
「释放浏览器」一项跑真实的 _do_reset_directory，只把后台线程换成不启动的替身，
以证明整组重置每个目录走的就是单目录那条真实路径。

Main-window tests for the multi-folder group reset (Labs). In multi mode
"Reset" acts on the whole list: one confirmation listing every folder, then
the same _do_reset_directory(skip_exif_reset=False, skip_confirm=True) per
folder in order, the next only after the previous completes or fails, and a
single final message listing failures. Most tests replace _do_reset_directory
with a recorder; the release-browser test runs the real method with the
background thread replaced by a non-starting stand-in.
"""
import os
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def env(monkeypatch) -> Dict[str, list]:
    """
    打桩确认框、提示框与危险目录检查；确认框默认答「是」。

    返回:
    Dict[str, list]: questions（确认框标题/正文）、messages（_show_message 调用）

    Stub question/message boxes and dangerous-root checks; questions answer Yes
    by default (set rec["answer"][0] to change it).
    """
    import ui.main_window as mw
    from ui.custom_dialogs import StyledMessageBox
    from core import recursive_scanner, folder_queue

    rec: Dict[str, list] = {"questions": [], "messages": [], "warnings": [],
                            "answer": [StyledMessageBox.Yes]}

    def _question(parent, title, message, yes_text=None, no_text=None):
        rec["questions"].append((title, message))
        return rec["answer"][0]

    def _warning(parent, title, message, ok_text=None):
        rec["warnings"].append((title, message))

    monkeypatch.setattr(StyledMessageBox, "question", staticmethod(_question))
    monkeypatch.setattr(StyledMessageBox, "warning", staticmethod(_warning))
    monkeypatch.setattr(recursive_scanner, "is_dangerous_root", lambda p, *a, **k: (False, ""))
    monkeypatch.setattr(folder_queue, "is_dangerous_root", lambda p, *a, **k: (False, ""))
    monkeypatch.setattr(mw.SuperPickyMainWindow, "_show_message",
                        lambda self, title, message, msg_type="info":
                        rec["messages"].append((title, message, msg_type)))
    return rec


def _photo_dir(root: Path, name: str) -> str:
    """
    建一个含一张假照片的目录（扫描按扩展名识别照片）。
    Create a folder holding one fake photo. Returns its path.
    """
    d = root / name
    d.mkdir(parents=True)
    (d / "x0.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return os.path.normpath(str(d))


def _window():
    """构造主窗口（conftest 已隔离配置与后台启动）/ Build the main window."""
    from ui.main_window import SuperPickyMainWindow
    return SuperPickyMainWindow()


def _multi(w, folders: List[str]) -> None:
    """打开开关并设好列表 / Switch on and set the list."""
    w.config.set_lab_multi_folder(True)
    w._apply_lab_multi_folder()
    w._folder_list.set_folders(folders)
    assert w._folder_list.folders() == folders
    assert w._is_multi_mode()


def _flush() -> None:
    """冲掉 QTimer.singleShot(0, …) / Flush zero-delay timers."""
    for _ in range(3):
        QApplication.processEvents()


class _ResetRecorder:
    """
    记录 _do_reset_directory 调用（调用时的 directory_path 与参数）。
    Records _do_reset_directory calls (directory_path at call time + kwargs).
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def install(self, w, monkeypatch) -> None:
        """装到窗口实例上 / Install on the window instance."""

        def _reset(skip_exif_reset: bool = False, skip_confirm: bool = False) -> None:
            self.calls.append((w.directory_path, {"skip_exif_reset": skip_exif_reset,
                                                  "skip_confirm": skip_confirm}))

        monkeypatch.setattr(w, "_do_reset_directory", _reset)


_GROUP_KW = {"skip_exif_reset": False, "skip_confirm": True}


def test_two_folders_one_confirm_sequential(tmp_path, env, monkeypatch):
    """两个目录：只确认一次（列出两个目录），依次重置，第二个在第一个完成后；只弹一次完成提示。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)

        w._reset_directory()
        assert len(env["questions"]) == 1
        title, body = env["questions"][0]
        assert title == w.i18n.t("messages.reset_confirm_title")
        for d in dirs:
            assert d in body
        assert r.calls == [(dirs[0], _GROUP_KW)]
        assert w._folder_list._editable is False
        assert w._is_lab_processing()
        _flush()
        assert len(r.calls) == 1  # 第一个还没完成 / first not complete yet

        w._on_reset_complete(True, {"restored": 1, "failed": 0}, {"success": 1})
        assert env["messages"] == []  # 每个目录的完成提示都抑制 / per-folder message suppressed
        assert len(r.calls) == 1  # 推进经 singleShot / advance via singleShot
        _flush()
        assert r.calls == [(dirs[0], _GROUP_KW), (dirs[1], _GROUP_KW)]

        w._on_reset_complete(True, {"restored": 1, "failed": 0}, {"success": 1})
        _flush()
        assert len(r.calls) == 2
        assert len(env["messages"]) == 1
        _title, msg, _kind = env["messages"][0]
        assert w.i18n.t("messages.reset_group_done", count=2) in msg
        assert len(env["questions"]) == 1

        # 收尾：回到第一个目录、多目录状态条、可编辑、不在处理中
        # Finish: back to the first folder, multi banner, editable, not processing
        assert w._reset_queue is None
        assert not w._is_lab_processing()
        assert w.directory_path == dirs[0]
        assert w._folder_list._editable is True
        assert w.reset_btn.isEnabled() and w.start_btn.isEnabled()
    finally:
        w._reset_queue = None
        w.close()


def test_group_reset_does_not_check_report_csv(tmp_path, env, monkeypatch):
    """整组重置的每一步与收尾都不走 _check_report_csv（那是单目录状态条）。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        checked: List[int] = []
        monkeypatch.setattr(w, "_check_report_csv", lambda *a, **k: checked.append(1))
        w._reset_directory()
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert checked == []
        assert len(r.calls) == 2
    finally:
        w._reset_queue = None
        w.close()


def test_cancel_confirm_resets_nothing(tmp_path, env, monkeypatch):
    """取消确认：一个目录都不重置，列表仍可编辑。"""
    from ui.custom_dialogs import StyledMessageBox
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        env["answer"][0] = StyledMessageBox.No
        w._reset_directory()
        _flush()
        assert len(env["questions"]) == 1
        assert r.calls == []
        assert w._reset_queue is None
        assert w._folder_list._editable is True
        assert env["messages"] == []
    finally:
        w.close()


def test_release_browser_before_each_real_reset(tmp_path, env, monkeypatch):
    """
    跑真实的 _do_reset_directory（后台线程替换为不启动的替身）：每个目录重置前都
    先释放浏览器，且线程启动时 directory_path 就是该目录。
    Real _do_reset_directory with a non-starting thread: the browser is released
    before every folder's reset thread starts.
    """
    import ui.main_window as mw
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        events: List[Tuple[str, str]] = []

        class _Browser:
            """浏览器替身 / Results browser stand-in."""

            def release_directory(self) -> None:
                events.append(("release", w.directory_path))

        class _Thread:
            """不启动的线程替身 / Thread stand-in that never runs."""

            def __init__(self, target=None, daemon=None) -> None:
                self.target = target

            def start(self) -> None:
                events.append(("thread", w.directory_path))

        monkeypatch.setattr(mw, "threading",
                            types.SimpleNamespace(Thread=_Thread, Lock=mw.threading.Lock,
                                                  RLock=mw.threading.RLock,
                                                  Event=mw.threading.Event))
        w._results_browser = _Browser()
        _multi(w, dirs)
        w._reset_directory()
        assert events == [("release", dirs[0]), ("thread", dirs[0])]
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert events == [("release", dirs[0]), ("thread", dirs[0]),
                          ("release", dirs[1]), ("thread", dirs[1])]
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert len(env["messages"]) == 1
        assert len(env["questions"]) == 1  # 真实方法也没有再确认 / no extra confirm
    finally:
        w._results_browser = None
        w._reset_queue = None
        w.close()


def test_error_on_first_continues_and_lists_failure(tmp_path, env, monkeypatch):
    """第一个出错：第二个照常重置，最后提示列出失败的目录。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._reset_directory()
        w._on_reset_error("disk boom")
        assert env["messages"] == []  # 不单独弹出错框 / no per-folder error box
        _flush()
        assert [c[0] for c in r.calls] == dirs
        w._on_reset_complete(True, {"restored": 1, "failed": 0}, {})
        _flush()
        assert len(env["messages"]) == 1
        _title, msg, kind = env["messages"][0]
        assert w.i18n.t("messages.reset_group_done", count=1) in msg
        assert dirs[0] in msg and "disk boom" in msg
        assert dirs[1] not in msg
        assert kind == "warning"
    finally:
        w._reset_queue = None
        w.close()


def test_unsuccessful_and_partial_listed(tmp_path, env, monkeypatch):
    """success=False 与「部分文件没能移回」都列为失败（后者写警告日志）。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._reset_directory()
        w._on_reset_complete(False, {"restored": 0, "failed": 0}, {})
        _flush()
        w._on_reset_complete(True, {"restored": 3, "failed": 2}, {})
        _flush()
        w._on_reset_complete(True, {"restored": 1, "failed": 0}, {})
        _flush()
        assert len(r.calls) == 3
        assert len(env["messages"]) == 1
        _title, msg, _kind = env["messages"][0]
        assert w.i18n.t("messages.reset_group_done", count=1) in msg
        assert dirs[0] in msg and dirs[1] in msg and dirs[2] not in msg
    finally:
        w._reset_queue = None
        w.close()


def test_missing_folder_skipped(tmp_path, env, monkeypatch):
    """列表中的目录不存在（外接盘拔了）：跳过、写日志、列为失败，其余照常。"""
    import shutil
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        shutil.rmtree(dirs[1])
        w._reset_directory()
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert [c[0] for c in r.calls] == [dirs[0], dirs[2]]
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert len(env["messages"]) == 1
        _title, msg, _kind = env["messages"][0]
        assert w.i18n.t("messages.reset_group_done", count=2) in msg
        assert dirs[1] in msg
        assert dirs[1] in w.log_text.toPlainText()
    finally:
        w._reset_queue = None
        w.close()


def test_logs_kept_across_folders(tmp_path, env, monkeypatch):
    """真实 _do_reset_directory 在整组重置中不清掉前面目录的日志。"""
    import ui.main_window as mw
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        class _Thread:
            """不启动的线程替身 / Thread stand-in that never runs."""

            def __init__(self, target=None, daemon=None) -> None:
                pass

            def start(self) -> None:
                pass

        monkeypatch.setattr(mw, "threading", types.SimpleNamespace(Thread=_Thread))
        _multi(w, dirs)
        w._reset_directory()
        w._log("MARKER-FIRST-FOLDER")
        w._on_reset_complete(True, {"restored": 0, "failed": 0}, {})
        _flush()
        assert "MARKER-FIRST-FOLDER" in w.log_text.toPlainText()
    finally:
        w._reset_queue = None
        w.close()


def test_single_mode_reset_unchanged(tmp_path, env, monkeypatch):
    """单目录（开关关闭，或开关打开但列表只有 1 个）：仍调用 _do_reset_directory(False, False)。"""
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        w.directory_path = a
        w._reset_directory()
        assert r.calls == [(a, {"skip_exif_reset": False, "skip_confirm": False})]
        assert env["questions"] == []  # 确认由 _do_reset_directory 自己问 / it asks itself

        w.config.set_lab_multi_folder(True)
        w._apply_lab_multi_folder()
        w._folder_list.set_folders([a])
        assert not w._is_multi_mode()
        w._reset_directory()
        assert r.calls[-1] == (a, {"skip_exif_reset": False, "skip_confirm": False})
        assert w._reset_queue is None
    finally:
        w.close()


def test_quit_during_group_reset_asks(tmp_path, env, monkeypatch):
    """整组重置进行中退出要确认；答「取消」不退出。"""
    from ui.custom_dialogs import StyledMessageBox
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _ResetRecorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._reset_directory()
        assert w._reset_queue is not None and w.worker is None
        asked, quits = [], []
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: asked.append(a_[1]) or StyledMessageBox.Yes))
        monkeypatch.setattr(w, "_force_quit", lambda: quits.append(1))
        w._quit_app()
        assert asked == [w.i18n.t("messages.exit_title")]
        assert quits == []
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: StyledMessageBox.No))
        w._quit_app()
        assert quits == [1]
    finally:
        w._reset_queue = None
        w.close()
