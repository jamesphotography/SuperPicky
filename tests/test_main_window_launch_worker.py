# -*- coding: utf-8 -*-
"""
_start_processing 启动工作线程的行为基线（特征测试）。

在抽出 _build_ui_settings / _prescan / _launch_worker 之前先针对旧代码写好，
重构后必须原样通过，用来证明单目录行为没有变化。

覆盖:
1. 正常路径：WorkerThread 收到 (目录, 9 项 ui_settings, 信号, i18n)，
   resume=False，scan_results 为预扫描得到的列表，并被 start()。
2. 预扫描抛异常：不阻断，scan_results=None，worker 照常启动。
3. 目录里没有照片：弹「没有照片」警告，worker 不启动。

Characterization tests for how _start_processing launches the worker. Written
against the code before extracting _build_ui_settings / _prescan /
_launch_worker; they must pass unchanged afterwards, proving single-folder
behaviour did not change.
"""
import os
from pathlib import Path
from typing import Any, List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


class _RecordingWorker:
    """
    记录构造参数的假 WorkerThread：start() 不真跑，is_alive() 恒 False。
    Fake WorkerThread that records its constructor args; start() is a no-op.
    """

    instances: List["_RecordingWorker"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args: Tuple[Any, ...] = args
        self.kwargs = kwargs
        self.started = False
        _RecordingWorker.instances.append(self)

    def start(self) -> None:
        """记录已启动 / Mark as started."""
        self.started = True

    def is_alive(self) -> bool:
        """恒为 False / Always False."""
        return False


class _DummyExifTool:
    """ExifTool 管理器替身 / Stand-in ExifTool manager."""


@pytest.fixture
def stubbed(monkeypatch) -> List[Tuple[str, str]]:
    """
    把所有弹窗、ExifTool、危险目录检查和 WorkerThread 打桩。

    返回:
    List[Tuple[str, str]]: 收集到的 warning 弹窗 (标题, 正文) 列表

    Stub dialogs, ExifTool, the dangerous-root check and WorkerThread.
    Returns the list collecting warning dialogs (title, message).
    """
    import ui.main_window as mw
    from ui.custom_dialogs import StyledMessageBox
    from core import recursive_scanner
    import tools.exiftool_manager as etm

    _RecordingWorker.instances = []
    monkeypatch.setattr(mw, "WorkerThread", _RecordingWorker)

    warnings: List[Tuple[str, str]] = []

    def _warning(parent, title, message, ok_text=None):
        warnings.append((title, message))
        return StyledMessageBox.Ok if hasattr(StyledMessageBox, "Ok") else None

    def _question(parent, title, message, yes_text=None, no_text=None):
        # 只会是识鸟国家提示（无断点状态、非可移动盘）；答「否」继续开始。
        # Only the Bird ID country prompt can appear here; answer No to proceed.
        return StyledMessageBox.No

    monkeypatch.setattr(StyledMessageBox, "warning", staticmethod(_warning))
    monkeypatch.setattr(StyledMessageBox, "question", staticmethod(_question))
    monkeypatch.setattr(etm, "get_exiftool_manager", lambda: _DummyExifTool())
    # macOS 的 tmp_path 在 /private 下会被判危险 / tmp_path under /private is flagged dangerous
    monkeypatch.setattr(recursive_scanner, "is_dangerous_root", lambda p: (False, ""))

    cls = mw.SuperPickyMainWindow
    monkeypatch.setattr(cls, "_require_initialization_for_processing", lambda self: True)
    monkeypatch.setattr(cls, "_is_removable_source", lambda self: False)
    monkeypatch.setattr(cls, "_confirm_start", lambda self: True)
    monkeypatch.setattr(cls, "_maybe_prompt_video_first_run", lambda self: None)
    return warnings


def _photo_dir(root: Path, name: str = "shoot", with_photo: bool = True) -> str:
    """
    建一个目录，可选放一张假照片（扫描按扩展名识别照片）。

    参数: root 父目录；name 子目录名；with_photo 是否放 x.jpg。返回: str 目录路径。
    Create a folder, optionally with one fake photo. Returns its path.
    """
    d = root / name
    d.mkdir()
    if with_photo:
        (d / "x.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return os.path.normpath(str(d))


def _expected_ui_settings() -> list:
    """
    按当前配置算出的 9 项 ui_settings（与 _start_processing 原代码一致）。
    The 9 ui_settings items computed from the current config.
    """
    from advanced_config import get_advanced_config
    adv = get_advanced_config()
    return [
        int(adv.min_confidence * 100),
        int(adv.min_sharpness),
        adv.min_nima,
        True,
        "log_compression",
        bool(adv.flight_check),
        False,
        bool(adv.burst_check),
        bool(adv.birdid_auto_identify),
    ]


def _window():
    """构造主窗口（conftest 已隔离配置与后台启动）/ Build the main window."""
    from ui.main_window import SuperPickyMainWindow
    return SuperPickyMainWindow()


def test_start_processing_launches_worker_with_scan_results(tmp_path, stubbed):
    """正常路径：参数、resume=False、scan_results 为列表、已 start。"""
    directory = _photo_dir(tmp_path)
    w = _window()
    try:
        w.directory_path = directory
        w._start_processing()

        assert len(_RecordingWorker.instances) == 1
        worker = _RecordingWorker.instances[0]
        assert worker.started is True
        assert w.worker is worker
        assert len(worker.args) == 4
        assert worker.args[0] == directory
        assert worker.args[1] == _expected_ui_settings()
        assert worker.args[2] is w.worker_signals
        assert worker.args[3] is w.i18n
        assert worker.kwargs["resume"] is False
        scan = worker.kwargs["scan_results"]
        assert isinstance(scan, list) and len(scan) >= 1
        assert stubbed == []
    finally:
        w.close()


def test_start_processing_scan_failure_does_not_block(tmp_path, stubbed, monkeypatch):
    """预扫描抛异常：scan_results=None，worker 照常启动，不弹警告。"""
    from core import recursive_scanner

    def _boom(*a, **k):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(recursive_scanner, "scan_directories", _boom)
    directory = _photo_dir(tmp_path)
    w = _window()
    try:
        w.directory_path = directory
        w._start_processing()

        assert len(_RecordingWorker.instances) == 1
        worker = _RecordingWorker.instances[0]
        assert worker.started is True
        assert worker.args[0] == directory
        assert worker.kwargs["resume"] is False
        assert worker.kwargs["scan_results"] is None
        assert stubbed == []
    finally:
        w.close()


def test_start_processing_no_photos_blocks(tmp_path, stubbed):
    """目录里没有照片：弹一次警告，worker 不启动。"""
    directory = _photo_dir(tmp_path, with_photo=False)
    w = _window()
    try:
        w.directory_path = directory
        w._start_processing()

        assert _RecordingWorker.instances == []
        assert len(stubbed) == 1
        assert stubbed[0][0] == w.i18n.t("health.no_photos_title")
    finally:
        w.close()
