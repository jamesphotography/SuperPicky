# -*- coding: utf-8 -*-
"""
多目录队列执行（实验室 lab_multi_folder）的主窗口行为测试。

_launch_worker 与 _do_reset_directory 被换成记录器，不起真实工作线程；完成、取消、
出错、重置完成都由测试直接调用对应槽来模拟，再用 processEvents() 冲掉
QTimer.singleShot(0, …)。

覆盖:
1. 三个新目录严格按顺序：第 i+1 个只在第 i 个 finished 之后启动；汇总与提示音各一次。
2. 等价性：同一目录单独处理与在队列中处理，传给 _launch_worker 的 resume / scan_results 相等。
3. 已处理目录默认跳过；勾「全部重新处理」先重置（skip_exif_reset/skip_confirm 均为 True），
   重置完成后才启动；未处理完的目录 resume=True。
4. 第 2 个出错：第 3 个照常执行，汇总列出出错目录。
5. 第 2 个处理中停止：第 3 个不执行（not_started），按钮恢复、列表可编辑。
6. 存储卡警告与开始确认各只弹一次；目录在开始后被删除（外接盘拔了）：记出错并继续。

Main-window tests for the multi-folder queue (Labs). _launch_worker and
_do_reset_directory are recorders; completion is simulated by calling the
slots directly and flushing QTimer.singleShot(0, …) with processEvents().
"""
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


class _FakePrecheckDialog:
    """
    DeleteConfirmDialog 替身：记录构造参数，exec() 后 choice/checked 取类属性。
    Stand-in for DeleteConfirmDialog; choice/checked come from class attributes.
    """

    instances: List["_FakePrecheckDialog"] = []
    next_choice: Optional[str] = "start"
    next_checked: bool = False

    def __init__(self, parent=None, title: str = "", message: str = "",
                 buttons: Optional[list] = None, default_key: Optional[str] = None,
                 links: Optional[list] = None, checkbox_text: Optional[str] = None) -> None:
        self.title = title
        self.message = message
        self.buttons = buttons or []
        self.default_key = default_key
        self.checkbox_text = checkbox_text
        self.choice: Optional[str] = None
        self.checked = False
        _FakePrecheckDialog.instances.append(self)

    def exec(self) -> int:
        """按类属性给出选择 / Answer from the class attributes."""
        self.choice = _FakePrecheckDialog.next_choice
        self.checked = _FakePrecheckDialog.next_checked
        return 0


class _DummyExifTool:
    """ExifTool 管理器替身 / Stand-in ExifTool manager."""


@pytest.fixture
def env(monkeypatch) -> Dict[str, list]:
    """
    打桩所有弹窗、ExifTool、危险目录检查、提示音与打开结果。

    返回:
    Dict[str, list]: 各计数器（confirm / removable_q / sound / report / open / warnings / questions）

    Stub dialogs, ExifTool, dangerous-root checks, sound and result opening.
    Returns a dict of call recorders.
    """
    import ui.main_window as mw
    import ui.custom_dialogs as cd
    from ui.custom_dialogs import StyledMessageBox
    from core import recursive_scanner, folder_queue
    import tools.exiftool_manager as etm

    rec: Dict[str, list] = {k: [] for k in (
        "confirm", "sound", "report", "open", "warnings", "questions", "video")}

    def _warning(parent, title, message, ok_text=None):
        rec["warnings"].append((title, message))

    def _question(parent, title, message, yes_text=None, no_text=None):
        rec["questions"].append((title, message))
        # 存储卡警告答「是」，其余（识鸟国家提示）答「否」继续
        # Yes to the removable warning; No to anything else (country prompt)
        from tools.i18n import get_i18n
        if title == get_i18n().t("messages.removable_warning_title"):
            return StyledMessageBox.Yes
        return StyledMessageBox.No

    monkeypatch.setattr(StyledMessageBox, "warning", staticmethod(_warning))
    monkeypatch.setattr(StyledMessageBox, "question", staticmethod(_question))
    monkeypatch.setattr(etm, "get_exiftool_manager", lambda: _DummyExifTool())
    monkeypatch.setattr(recursive_scanner, "is_dangerous_root", lambda p, *a, **k: (False, ""))
    monkeypatch.setattr(folder_queue, "is_dangerous_root", lambda p, *a, **k: (False, ""))

    _FakePrecheckDialog.instances = []
    _FakePrecheckDialog.next_choice = "start"
    _FakePrecheckDialog.next_checked = False
    monkeypatch.setattr(cd, "DeleteConfirmDialog", _FakePrecheckDialog)

    cls = mw.SuperPickyMainWindow
    monkeypatch.setattr(cls, "_require_initialization_for_processing", lambda self: True)
    monkeypatch.setattr(cls, "_is_removable_source", lambda self, path=None: False)
    monkeypatch.setattr(cls, "_confirm_start",
                        lambda self: rec["confirm"].append(1) or True)
    monkeypatch.setattr(cls, "_maybe_prompt_video_first_run",
                        lambda self, *a, **k: rec["video"].append(a))
    monkeypatch.setattr(cls, "_play_completion_sound", lambda self: rec["sound"].append(1))
    monkeypatch.setattr(cls, "_show_statistics_report",
                        lambda self, stats: rec["report"].append(stats))
    monkeypatch.setattr(cls, "_auto_open_results",
                        lambda self, path=None: rec["open"].append(("browser", path)))
    monkeypatch.setattr(cls, "_open_finder_results",
                        lambda self, path=None: rec["open"].append(("finder", path)))
    return rec


def _photo_dir(root: Path, name: str, photos: int = 1) -> str:
    """
    建一个含若干假照片的目录（扫描按扩展名识别照片）。

    参数: root 父目录；name 子目录名；photos 张数。返回: str 目录路径。
    Create a folder with `photos` fake photos. Returns its path.
    """
    d = root / name
    d.mkdir(parents=True)
    for i in range(photos):
        (d / f"x{i}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return os.path.normpath(str(d))


def _mark_processed(path: str) -> None:
    """放一个 report.db，让目录被判为「已处理」/ Make the folder look processed."""
    sp = os.path.join(path, ".superpicky")
    os.makedirs(sp, exist_ok=True)
    Path(sp, "report.db").write_bytes(b"")


def _mark_resume(path: str) -> None:
    """放一个断点续传状态 / Leave a resume state behind."""
    sp = os.path.join(path, ".superpicky")
    os.makedirs(sp, exist_ok=True)
    Path(sp, "resume_state.json").write_text("{}", encoding="utf-8")


def _window():
    """构造主窗口（conftest 已隔离配置与后台启动）/ Build the main window."""
    from ui.main_window import SuperPickyMainWindow
    return SuperPickyMainWindow()


class _Recorder:
    """
    记录 _launch_worker / _do_reset_directory 调用的替身。
    Records _launch_worker / _do_reset_directory calls.
    """

    def __init__(self) -> None:
        self.launches: List[Tuple[str, bool, Any, bool]] = []
        self.resets: List[Tuple[str, Dict[str, Any]]] = []

    def install(self, w, monkeypatch) -> None:
        """装到窗口实例上 / Install on the window instance."""

        def _launch(directory: str, resume: bool, scan_results, clear_log: bool = True) -> None:
            self.launches.append((directory, resume, scan_results, clear_log))

        def _reset(skip_exif_reset: bool = False, skip_confirm: bool = False) -> None:
            self.resets.append((w.directory_path, {"skip_exif_reset": skip_exif_reset,
                                                   "skip_confirm": skip_confirm}))

        monkeypatch.setattr(w, "_launch_worker", _launch)
        monkeypatch.setattr(w, "_do_reset_directory", _reset)


def _multi(w, folders: List[str]) -> None:
    """打开开关并设好列表 / Switch on and set the list."""
    w.config.set_lab_multi_folder(True)
    w._apply_lab_multi_folder()
    w._folder_list.set_folders(folders)
    assert w._folder_list.folders() == folders
    assert w._is_multi_mode()


def _stats(total: int = 1, s3: int = 1) -> dict:
    """一个最小的完成统计 / Minimal finished stats."""
    return {"total": total, "star_3": s3, "star_2": 0, "star_1": 0, "star_0": 0, "no_bird": 0}


def _flush() -> None:
    """冲掉 QTimer.singleShot(0, …) / Flush zero-delay timers."""
    for _ in range(3):
        QApplication.processEvents()


def _console(w) -> str:
    """控制台纯文本 / Console plain text."""
    return w.log_text.toPlainText()


def _core(text: str) -> str:
    """
    去掉文案开头的 emoji（控制台把 emoji 渲染成图片，纯文本里没有）。
    Strip a leading emoji (the console renders emoji as images, absent from plain text).
    """
    import re
    return re.sub(r"^[^\w\[/]+", "", text)


def test_three_fresh_folders_run_sequentially(tmp_path, env, monkeypatch):
    """三个新目录：依次启动，下一个只在上一个完成后；汇总与提示音各一次。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)

        w._on_start_btn_clicked()
        assert [c[0] for c in r.launches] == [dirs[0]]
        assert w._queue is not None
        assert w._folder_list._editable is False
        _flush()
        assert len(r.launches) == 1  # 还没完成就不启动下一个 / not before finished

        w._on_finished(_stats(1))
        assert len(r.launches) == 1  # 推进经 singleShot / advance goes through singleShot
        _flush()
        assert [c[0] for c in r.launches] == dirs[:2]
        w._on_finished(_stats(1))
        _flush()
        assert [c[0] for c in r.launches] == dirs
        w._on_finished(_stats(1))
        _flush()

        assert w._queue is None
        for d, call in zip(dirs, r.launches):
            assert call[0] == d
            assert call[1] is False
            assert isinstance(call[2], list) and call[2][0].path == d
            # 日志在队列开始时清一次，各目录启动都不再清，前面目录的日志保留
            # The log is cleared once at queue start; launches never clear it
            assert call[3] is False
        assert len(env["sound"]) == 1
        assert env["report"] == []  # 每个目录的统计报告跳过 / per-folder report skipped
        assert env["open"] == []  # 没有目录写出 report.db / no folder produced a report.db
        text = _console(w)
        title = _core(w.i18n.t("messages.queue_summary_title"))
        assert text.count(title) == 1
        for i, d in enumerate(dirs, 1):
            assert w.i18n.t("logs.queue_folder_start", i=i, n=3, path=d) in text
        assert w._folder_list._editable is True
        assert w.directory_path == dirs[0]
        assert w.start_btn.text() == w.i18n.t("labels.start_processing")
    finally:
        w.close()


def test_open_results_once_first_processed_folder(tmp_path, env, monkeypatch):
    """不保留预览图：只打开第一个有结果目录的文件夹一次。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        w.config.set_keep_temp_files(False)
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        _mark_processed(dirs[0])  # 模拟第一个目录写出了 report.db
        w._on_finished(_stats())
        _flush()
        _mark_processed(dirs[1])
        w._on_finished(_stats())
        _flush()
        # 打开结果经 singleShot(800) 延后 / opening is deferred via singleShot(800)
        import time
        deadline = time.time() + 3
        while not env["open"] and time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.05)
        assert env["open"] == [("finder", dirs[0])]
    finally:
        w.close()


def test_equivalence_single_vs_queue_launch_args(tmp_path, env, monkeypatch):
    """同一目录：单目录 _start_processing 与队列传给 _launch_worker 的 resume/scan_results 相等。"""
    a = _photo_dir(tmp_path, "a", photos=3)
    sub = _photo_dir(tmp_path / "a", "sub", photos=2)  # 父目录批量模式 / batch mode
    b = _photo_dir(tmp_path, "b")
    assert os.path.isdir(sub)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        w.directory_path = a
        w._start_processing()
        assert len(r.launches) == 1
        single = r.launches[0]

        _multi(w, [a, b])
        w._start_queue()
        queued = r.launches[1]
        assert queued[0] == single[0] == a
        assert queued[1] == single[1]
        assert queued[2] == single[2]
        assert len(queued[2]) == 2
    finally:
        w.close()


def test_equivalence_resume_folder(tmp_path, env, monkeypatch):
    """未处理完的目录：单目录（答「继续」）与队列都传 resume=True、同样的 scan_results。"""
    from ui.custom_dialogs import StyledMessageBox
    a = _photo_dir(tmp_path, "a")
    b = _photo_dir(tmp_path, "b")
    _mark_resume(a)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: StyledMessageBox.Yes))
        # 识鸟国家提示答「是」会去设置页并中止，这里关掉识鸟让单目录直接到「继续」提问
        # Turn off auto-ID so Yes only answers the continue/restart question
        w.config.set_birdid_auto_identify(False)
        w.directory_path = a
        w._start_processing()
        single = r.launches[0]
        assert single[1] is True

        _multi(w, [a, b])
        w._start_queue()
        queued = r.launches[1]
        assert (queued[0], queued[1], queued[2]) == (single[0], single[1], single[2])
        # 预检框只列出 a（接着处理）/ the pre-check lists a as resume
        dlg = _FakePrecheckDialog.instances[-1]
        assert a in dlg.message
    finally:
        w.close()


def test_processed_folder_skipped_by_default(tmp_path, env, monkeypatch):
    """已处理目录默认跳过：不重置、不启动，汇总里为 skipped。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    _mark_processed(dirs[1])
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        assert len(_FakePrecheckDialog.instances) == 1
        dlg = _FakePrecheckDialog.instances[0]
        assert dlg.checkbox_text == w.i18n.t("dialogs.queue_reprocess_all")
        # 主按钮放最右（与应用其余对话框一致）/ primary rightmost, like the app's other dialogs
        assert [(b[0], b[2]) for b in dlg.buttons] == [("cancel", "secondary"), ("start", "primary")]
        assert dlg.default_key == "start"
        assert dirs[1] in dlg.message

        w._on_finished(_stats())
        _flush()
        w._on_finished(_stats())
        _flush()
        assert [c[0] for c in r.launches] == [dirs[0], dirs[2]]
        assert r.resets == []
        assert w._queue is None
    finally:
        w.close()


def test_precheck_cancel_starts_nothing(tmp_path, env, monkeypatch):
    """预检框点取消（或 Esc）：什么都不启动，也不记最近目录组。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    _mark_processed(dirs[0])
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        _FakePrecheckDialog.next_choice = None
        w._on_start_btn_clicked()
        assert r.launches == [] and r.resets == []
        assert w._queue is None
        assert w.config.get_recent_directory_groups() == []
        assert w._folder_list._editable is True
    finally:
        w.close()


def test_reprocess_all_resets_then_launches(tmp_path, env, monkeypatch):
    """勾选全部重新处理：先 _do_reset_directory(True, True)，重置完成后才启动，且扫描在重置之后。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    _mark_processed(dirs[0])
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        _FakePrecheckDialog.next_checked = True
        shown = []
        monkeypatch.setattr(w, "_show_message", lambda *a, **k: shown.append(a))
        w._start_queue()
        assert r.resets == [(dirs[0], {"skip_exif_reset": True, "skip_confirm": True})]
        assert r.launches == []
        # 重置期间开始按钮仍是「停止」/ Stop stays available during the reset
        assert w.start_btn.text() == w.i18n.t("labels.stop_processing")

        # 模拟重置把照片移回根目录：重置后多一张，扫描结果必须反映重置后的状态
        # The reset moves photos back; the scan must reflect the post-reset tree
        Path(dirs[0], "moved_back.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        w._on_reset_complete(True, {"restored": 1}, {})
        _flush()
        assert shown == []  # 不弹「重置完成」提示 / no reset-complete message box
        assert len(r.launches) == 1
        path, resume, scan, _clear = r.launches[0]
        assert (path, resume) == (dirs[0], False)
        assert sum(s.photo_count for s in scan) == 2

        w._on_finished(_stats())
        _flush()
        assert [c[0] for c in r.launches] == dirs
        w._on_finished(_stats())
        _flush()
        assert w._queue is None
    finally:
        w.close()


def test_single_reset_complete_unchanged(tmp_path, env, monkeypatch):
    """无队列时 _on_reset_complete 仍弹提示并走 _check_report_csv。"""
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        w.directory_path = a
        shown, checked = [], []
        monkeypatch.setattr(w, "_show_message", lambda *a_, **k: shown.append(a_))
        monkeypatch.setattr(w, "_check_report_csv", lambda *a_, **k: checked.append(1))
        w._on_reset_complete(True, {"restored": 0}, {})
        assert len(shown) == 1 and checked == [1]
    finally:
        w.close()


def test_error_on_second_folder_continues(tmp_path, env, monkeypatch):
    """第 2 个目录出错：第 3 个照常执行，汇总列出出错目录。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        w._on_finished(_stats())
        _flush()
        w._on_error("boom 爆了")
        _flush()
        assert [c[0] for c in r.launches] == dirs
        w._on_finished(_stats())
        _flush()
        assert w._queue is None
        text = _console(w)
        assert "boom 爆了" in text
        errors_head = _core(w.i18n.t("messages.queue_summary_errors"))
        assert errors_head in text
        assert dirs[1] in text.split(errors_head, 1)[1]
        assert len(env["sound"]) == 1
    finally:
        w.close()


def test_cancel_during_second_stops_queue(tmp_path, env, monkeypatch):
    """第 2 个处理中停止：第 3 个不启动（not_started），按钮恢复、列表可编辑。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        w._on_finished(_stats())
        _flush()
        queue = w._queue
        w._on_cancelled(_stats())
        _flush()
        assert [c[0] for c in r.launches] == dirs[:2]
        assert w._queue is None
        statuses = {o.path: o.status for o in queue.outcomes}
        assert statuses == {dirs[0]: "done", dirs[1]: "stopped", dirs[2]: "not_started"}
        assert w._folder_list._editable is True
        assert w.start_btn.text() == w.i18n.t("labels.start_processing")
        assert w.start_btn.isEnabled()
        assert w._recent_menu.isEnabled()
    finally:
        w.close()


def test_stop_button_during_reset_finishes_queue(tmp_path, env, monkeypatch):
    """重新处理的重置期间点停止：重置完成后不启动，队列结束。"""
    from ui.custom_dialogs import StyledMessageBox
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    _mark_processed(dirs[0])
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        _FakePrecheckDialog.next_checked = True
        monkeypatch.setattr(w, "_show_message", lambda *a, **k: None)
        w._start_queue()
        queue = w._queue
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: StyledMessageBox.Yes))
        w._on_start_btn_clicked()  # 停止 / Stop
        w._on_reset_complete(True, {}, {})
        _flush()
        assert r.launches == []
        assert w._queue is None
        statuses = {o.path: o.status for o in queue.outcomes}
        assert statuses[dirs[0]] == "stopped"
        assert statuses[dirs[1]] == "not_started"
    finally:
        w.close()


def test_removable_and_confirm_once(tmp_path, env, monkeypatch):
    """存储卡警告与开始确认各只弹一次，警告里列出位于存储卡的目录。"""
    import ui.main_window as mw
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    removable_calls = []

    def _removable(self, path=None):
        removable_calls.append(path)
        return path in (dirs[0], dirs[2])

    monkeypatch.setattr(mw.SuperPickyMainWindow, "_is_removable_source", _removable)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        title = w.i18n.t("messages.removable_warning_title")
        warns = [q for q in env["questions"] if q[0] == title]
        assert len(warns) == 1
        assert dirs[0] in warns[0][1] and dirs[2] in warns[0][1] and dirs[1] not in warns[0][1]
        assert len(env["confirm"]) == 1
        assert len(env["video"]) == 1
        for _ in range(3):
            w._on_finished(_stats())
            _flush()
        assert len(env["confirm"]) == 1
        assert len([q for q in env["questions"] if q[0] == title]) == 1
        assert w._queue is None
    finally:
        w.close()


def test_removable_declined_starts_nothing(tmp_path, env, monkeypatch):
    """存储卡警告答「否」：什么都不启动。"""
    import ui.main_window as mw
    from ui.custom_dialogs import StyledMessageBox
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    monkeypatch.setattr(mw.SuperPickyMainWindow, "_is_removable_source",
                        lambda self, path=None: True)
    monkeypatch.setattr(StyledMessageBox, "question",
                        staticmethod(lambda *a_, **k: StyledMessageBox.No))
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        assert r.launches == [] and w._queue is None
        assert env["confirm"] == []
    finally:
        w.close()


def test_missing_folder_errors_and_continues(tmp_path, env, monkeypatch):
    """加入后被删除（外接盘拔了）：记出错，继续下一个，汇总列出它。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b", "c")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        shutil.rmtree(dirs[1])
        w._start_queue()
        w._on_finished(_stats())
        _flush()
        assert [c[0] for c in r.launches] == [dirs[0], dirs[2]]
        queue = w._queue
        w._on_finished(_stats())
        _flush()
        statuses = {o.path: o.status for o in queue.outcomes}
        assert statuses[dirs[1]] == "error"
        text = _console(w)
        assert w.i18n.t("logs.queue_folder_missing") in text
        errors_head = _core(w.i18n.t("messages.queue_summary_errors"))
        assert dirs[1] in text.split(errors_head, 1)[1]
    finally:
        w.close()


def test_progress_maps_to_overall(tmp_path, env, monkeypatch):
    """队列模式进度：按已完成张数加当前目录进度换算总百分比。"""
    a = _photo_dir(tmp_path, "a", photos=1)
    b = _photo_dir(tmp_path, "b", photos=3)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, [a, b])
        w._start_queue()
        w._on_progress(100)
        assert w.progress_bar.value() == 25
        w._on_finished(_stats())
        _flush()
        w._on_progress(50)
        assert w.progress_bar.value() == 62
        w._on_finished(_stats())
        _flush()
        # 单目录模式不换算 / single-folder progress is unchanged
        w._on_progress(40)
        assert w.progress_bar.value() == 40
    finally:
        w.close()


def test_single_start_button_still_single(tmp_path, env, monkeypatch):
    """开关关闭时开始按钮仍走单目录 _start_processing。"""
    a = _photo_dir(tmp_path, "a")
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        w.directory_path = a
        w._on_start_btn_clicked()
        assert len(r.launches) == 1 and r.launches[0][3] is True
        assert w._queue is None
    finally:
        w.close()


def test_processed_folder_restored_via_list_reaches_precheck(tmp_path, env, monkeypatch):
    """已整理的目录经「最近目录组」放回列表后仍在列表中，开始时预检框列出它。"""
    a = _photo_dir(tmp_path, "a")
    done = tmp_path / "done"
    (done / "3星_优选").mkdir(parents=True)
    (done / "3星_优选" / "x.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    done = os.path.normpath(str(done))
    _mark_processed(done)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        w.config.set_lab_multi_folder(True)
        w._apply_lab_multi_folder()
        w._restore_recent_group([a, done])
        assert w._folder_list.folders() == [a, done]
        w._start_queue()
        dlg = _FakePrecheckDialog.instances[-1]
        assert done in dlg.message
        assert dlg.checkbox_text == w.i18n.t("dialogs.queue_reprocess_all")
    finally:
        w.close()


def test_quit_during_queue_without_worker_asks(tmp_path, env, monkeypatch):
    """队列在跑但没有存活工作线程（目录之间/重置中）：退出要确认，答「取消」不退出。"""
    from ui.custom_dialogs import StyledMessageBox
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        assert w._queue is not None and w.worker is None
        asked, quits = [], []
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: asked.append(a_[1]) or StyledMessageBox.Yes))
        monkeypatch.setattr(w, "_force_quit", lambda: quits.append(1))
        w._quit_app()
        assert asked == [w.i18n.t("messages.exit_title")]
        assert quits == []
        assert w._queue is not None and not w._queue_stop_requested

        # 确认退出：先停队列再退出 / confirmed: stop the queue, then quit
        queue = w._queue
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: StyledMessageBox.No))
        w._quit_app()
        assert quits == [1]
        assert queue.next_item() is None
    finally:
        w._queue = None
        w.close()


def test_quit_idle_does_not_ask(tmp_path, env, monkeypatch):
    """没有处理任务时退出不确认（单目录行为不变）。"""
    from ui.custom_dialogs import StyledMessageBox
    w = _window()
    try:
        asked, quits = [], []
        monkeypatch.setattr(StyledMessageBox, "question",
                            staticmethod(lambda *a_, **k: asked.append(1)))
        monkeypatch.setattr(w, "_force_quit", lambda: quits.append(1))
        w._quit_app()
        assert asked == [] and quits == [1]
    finally:
        w.close()


def test_reset_partial_failure_logs_warning(tmp_path, env, monkeypatch):
    """重新处理时有文件没能移回：写一行警告，照常启动。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    _mark_processed(dirs[0])
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        _FakePrecheckDialog.next_checked = True
        w._start_queue()
        w._on_reset_complete(True, {"restored": 3, "failed": 2}, {})
        _flush()
        assert len(r.launches) == 1
        expected = _core(w.i18n.t("logs.queue_reset_partial", path=dirs[0], failed=2))
        assert expected in _console(w)
    finally:
        w._queue = None
        w.close()


def test_queue_of_batch_parents_ends_has_results(tmp_path, env, monkeypatch):
    """
    队列处理两个父目录（批次模式，结果在子目录里）：结束后按钮为 has_results，可查看结果。
    After a queue of batch-mode parents, the buttons show has_results.
    """
    a = _photo_dir(tmp_path / "pa", "day1")
    b = _photo_dir(tmp_path / "pb", "day1")
    pa = os.path.normpath(str(tmp_path / "pa"))
    pb = os.path.normpath(str(tmp_path / "pb"))
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, [pa, pb])
        w._start_queue()
        _mark_processed(a)
        w._on_finished(_stats())
        _flush()
        _mark_processed(b)
        w._on_finished(_stats())
        _flush()
        assert w._queue is None
        assert w.view_results_btn.isVisibleTo(w)
        # 列表改动后同样认得 / also after a list change
        w._folder_list.set_folders([pa, pb])
        assert w.view_results_btn.isVisibleTo(w)
    finally:
        w.close()


def test_all_folders_failed_shows_error_status(tmp_path, env, monkeypatch):
    """所有目录都出错：状态显示「错误」而不是绿色「完成」。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        w._on_error("e1")
        _flush()
        w._on_error("e2")
        _flush()
        assert w._queue is None
        assert w.status_label.text() == w.i18n.t("labels.error")
        assert w.progress_info_label.text() != w.i18n.t("labels.complete")
        assert env["sound"] == []
    finally:
        w.close()


def test_some_done_still_complete(tmp_path, env, monkeypatch):
    """有目录完成：仍显示「完成」。"""
    dirs = [_photo_dir(tmp_path, n) for n in ("a", "b")]
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, dirs)
        w._start_queue()
        w._on_finished(_stats())
        _flush()
        w._on_error("e2")
        _flush()
        assert w.status_label.text() == w.i18n.t("labels.complete")
    finally:
        w.close()


def test_start_queue_reuses_cached_counts(tmp_path, env, monkeypatch):
    """开始队列时总进度分母用列表已缓存的张数，不再逐个目录重新数。"""
    import ui.folder_list_widget as flw
    a = _photo_dir(tmp_path, "a", photos=1)
    b = _photo_dir(tmp_path, "b", photos=3)
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, [a, b])
        counted: List[str] = []
        monkeypatch.setattr(flw, "count_photos", lambda p: counted.append(p) or 0)
        w._start_queue()
        assert counted == []
        assert w._queue_total == 4
    finally:
        w.close()


def _batch_parent(root: Path, name: str) -> str:
    """
    批次模式处理过的父目录：day1/day2 各有 report.db，照片在星级目录里，父目录没有 report.db。
    A batch-mode processed parent (report.db only in day1/day2).
    """
    parent = root / name
    for day in ("day1", "day2"):
        _mark_processed(str(parent / day))
        (parent / day / "3星_优选").mkdir()
        (parent / day / "3星_优选" / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return os.path.normpath(str(parent))


def test_batch_parent_processed_skipped_by_default(tmp_path, env, monkeypatch):
    """
    批次模式处理过的父目录（结果只在子目录）默认跳过：预检框列在「已处理」里、带「全部
    重新处理」勾选框；不重置、不启动、汇总为 skipped，也不记「无照片」错误。
    A processed batch parent is skipped by default and listed under processed.
    """
    parent = _batch_parent(tmp_path, "parent")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, [parent, b])
        w._start_queue()
        assert len(_FakePrecheckDialog.instances) == 1
        dlg = _FakePrecheckDialog.instances[0]
        assert dlg.checkbox_text == w.i18n.t("dialogs.queue_reprocess_all")
        expected = w.i18n.t("dialogs.queue_precheck_processed", folders=parent)
        assert expected in dlg.message
        assert r.resets == []
        assert [c[0] for c in r.launches] == [b]
        outcomes = {o.path: o.status for o in w._queue.outcomes}
        assert outcomes[parent] == "skipped"
        w._on_finished(_stats())
        _flush()
        assert w._queue is None
        assert _core(w.i18n.t("health.no_photos_title")) not in _console(w)
    finally:
        w._queue = None
        w.close()


def test_batch_parent_reprocess_all_resets_then_launches(tmp_path, env, monkeypatch):
    """
    批次父目录 + 全部重新处理：先 _do_reset_directory(skip_exif_reset=True, skip_confirm=True)，
    重置完成（照片回到各子目录）后扫描并启动该父目录。
    Reprocess-all on a batch parent: quick restore first, then scan and launch.
    """
    parent = _batch_parent(tmp_path, "parent")
    b = _photo_dir(tmp_path, "b")
    w = _window()
    try:
        r = _Recorder()
        r.install(w, monkeypatch)
        _multi(w, [parent, b])
        _FakePrecheckDialog.next_checked = True
        w._start_queue()
        assert r.resets == [(parent, {"skip_exif_reset": True, "skip_confirm": True})]
        assert r.launches == []
        # 模拟重置：照片从星级目录回到各子目录根部 / simulate the reset moving photos back
        for day in ("day1", "day2"):
            src = Path(parent, day, "3星_优选", "a.jpg")
            src.rename(Path(parent, day, "a.jpg"))
        w._on_reset_complete(True, {"restored": 2}, {})
        _flush()
        assert len(r.launches) == 1
        path, resume, scan, _clear = r.launches[0]
        assert (path, resume) == (parent, False)
        assert sum(s.photo_count for s in scan) == 2
    finally:
        w._queue = None
        w.close()
