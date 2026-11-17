# -*- coding: utf-8 -*-
"""
退出崩溃修复的回归测试（macOS ⌘Q / 「退出」Apple Event 段错误）。

背景：closeEvent 里调用 ``QApplication.quit()`` 会在 AppKit 终止流程中嵌套
``[NSApp terminate:]``，AppKit 直接 ``exit()``：清理不执行、shiboken 静态析构段错误。
修复后应用一律用 ``_exit_event_loop``（``QApplication.exit``），``main.py`` 在
``exec()`` 返回后执行 ``tools.exit_hooks`` 并 ``os._exit``。因为 ``os._exit`` 跳过
atexit，项目代码不得再直接用 ``atexit.register``。

Regression tests for the macOS quit segfault: no ``QApplication.quit()`` in app
code, no raw ``atexit.register`` (``os._exit`` would skip it), and the
exit-hook registry runs each hook once, newest first.
"""

import atexit
import re
from pathlib import Path

import pytest

from tools import exit_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"tests", ".venv", "venv", "build", "dist", "scripts_dev", "scripts", ".git"}


def _app_python_files() -> list:
    """
    列出应用代码的 .py 文件（排除测试、构建产物与开发脚本）。

    返回:
    list: Path 列表

    List the app's .py files, excluding tests, build output and dev scripts.
    """
    files = []
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] in _SKIP_DIRS or rel.parts[0].startswith("dist") or "site-packages" in rel.parts:
            continue
        if rel.parts[0] == ".claude":
            continue
        files.append(path)
    return files


def test_app_files_found() -> None:
    """扫描必须真的扫到应用代码，避免静默假绿 / The scan must actually see app code."""
    names = {p.relative_to(REPO_ROOT).as_posix() for p in _app_python_files()}
    assert "main.py" in names
    assert "ui/main_window.py" in names


def test_no_raw_atexit_register() -> None:
    """os._exit 会跳过 atexit：退出钩子必须经 tools.exit_hooks 登记。"""
    offenders = []
    for path in _app_python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "tools/exit_hooks.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\batexit\.register\(", text):
            offenders.append(rel)
    assert offenders == [], f"use tools.exit_hooks.register instead of atexit.register: {offenders}"


def test_no_qapplication_quit_in_app_code() -> None:
    """QApplication.quit() 在 macOS 退出流程中会嵌套 terminate 并段错误，须用 _exit_event_loop。"""
    pattern = re.compile(r"(QApplication|QCoreApplication)\.quit\b|\bapp\.quit\(\)")
    offenders = []
    for path in _app_python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code) and "``" not in code:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}")
    assert offenders == [], f"use _exit_event_loop() instead of quit(): {offenders}"


@pytest.fixture
def clean_registry(monkeypatch):
    """隔离全局钩子列表 / Isolate the global hook list."""
    monkeypatch.setattr(exit_hooks, "_hooks", [])
    yield
    atexit.unregister(exit_hooks.run_all)
    atexit.register(exit_hooks.run_all)


def test_run_all_lifo_once_and_isolates_failures(clean_registry) -> None:
    """后登记先执行；单个失败不影响其余；重复调用不重复执行。"""
    calls = []

    def boom() -> None:
        calls.append("boom")
        raise RuntimeError("expected")

    exit_hooks.register(lambda: calls.append("first"))
    exit_hooks.register(boom)
    exit_hooks.register(lambda: calls.append("last"))

    exit_hooks.run_all()
    exit_hooks.run_all()

    assert calls == ["last", "boom", "first"]


def test_main_window_exit_helper_uses_exit(monkeypatch) -> None:
    """_exit_event_loop 必须走 QApplication.exit（不经 AppKit terminate）。"""
    import ui.main_window as mw

    seen = []
    monkeypatch.setattr(mw.QApplication, "exit", staticmethod(lambda code=0: seen.append(code)))
    mw._exit_event_loop()
    assert seen == [0]
