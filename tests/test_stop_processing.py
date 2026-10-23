# -*- coding: utf-8 -*-
"""
#144 中途停止选片：工作线程被要求停止后必须发「已取消」信号，主窗口据此恢复按钮。

此前取消只写一行日志，既不发 finished 也不发 error，主窗口一直停在「处理中」。

Regression tests for #144: once a stop is requested the worker must emit
`cancelled` (previously it only logged, leaving the main window stuck in the
processing state), and never `finished`.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from core.photo_processor import ProcessingCancelled  # noqa: E402
from ui.main_window import WorkerSignals, WorkerThread  # noqa: E402


def _run_worker(monkeypatch, process_files) -> dict:
    """
    用替身 process_files 同步跑一次 WorkerThread.run，记录发出的信号。
    Run WorkerThread.run synchronously with a stub process_files and record signals.
    """
    signals = WorkerSignals()
    got = {"finished": 0, "cancelled": 0, "error": []}
    signals.finished.connect(lambda _stats: got.__setitem__("finished", got["finished"] + 1))
    signals.cancelled.connect(lambda _stats: got.__setitem__("cancelled", got["cancelled"] + 1))
    signals.error.connect(lambda msg: got["error"].append(msg))

    worker = WorkerThread("/tmp/nowhere", {}, signals)
    monkeypatch.setattr(worker, "_start_caffeinate", lambda: None)
    monkeypatch.setattr(worker, "_stop_caffeinate", lambda: None)
    monkeypatch.setattr(worker, "_process_videos", lambda: None)
    monkeypatch.setattr(worker, "process_files", lambda: process_files(worker))
    worker.run()
    return got


def test_cancel_exception_emits_cancelled(monkeypatch):
    """处理器抛出取消 → 发 cancelled，不发 finished / error。"""
    def stop_midway(worker):
        worker.request_stop()
        raise ProcessingCancelled("Processing cancelled")

    got = _run_worker(monkeypatch, stop_midway)
    assert got == {"finished": 0, "cancelled": 1, "error": []}


def test_stop_requested_without_exception_still_cancelled(monkeypatch):
    """批量模式在目录之间停下、正常返回时，也算取消而不是完成。"""
    def stop_between_dirs(worker):
        worker.request_stop()

    got = _run_worker(monkeypatch, stop_between_dirs)
    assert got == {"finished": 0, "cancelled": 1, "error": []}


def test_normal_run_emits_finished(monkeypatch):
    got = _run_worker(monkeypatch, lambda worker: None)
    assert got == {"finished": 1, "cancelled": 0, "error": []}


def test_stop_strings_exist_in_all_locales():
    """新文案三种语言都要有 / New strings exist in every locale."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "locales"
    for name in ("en_US.json", "zh_CN.json", "zh_TW.json"):
        data = json.loads((root / name).read_text(encoding="utf-8"))
        for section, key in (("labels", "stop_processing"), ("labels", "stopping"), ("labels", "stopped"),
                             ("messages", "stop_confirm_title"), ("messages", "stop_confirm")):
            assert data[section].get(key), f"{name}: {section}.{key}"
