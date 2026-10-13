# -*- coding: utf-8 -*-
"""
#123 回归测试：关窗行为可选 + 单实例保护。

1. advanced_config.close_action：默认 "ask"，非法值回退 "ask"；
2. MainWindow.closeEvent 按设置分流：tray 隐藏、quit 走 _quit_app、ask 询问
   并记住选择、关掉询问框则取消关窗；系统注销/托盘不可用时不受影响；
3. _ask_close_action 把按钮结果映射为 "tray" / "quit" / None；
4. SingleInstanceGuard：第二个实例能连上第一个并触发激活回调。

#123 regression: selectable close behavior and the single-instance guard.
"""

import types
import uuid

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    """提供进程级 QApplication。/ Provide a process-wide QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


# ── 1. 配置项 / Config ───────────────────────────────────────────────────────

def test_close_action_default_and_clamp():
    """默认 ask；合法值原样保存；非法值回退 ask。/ Default, valid, invalid."""
    from advanced_config import get_advanced_config
    cfg = get_advanced_config()
    assert cfg.close_action == "ask"
    for value in ("tray", "quit", "ask"):
        cfg.set_close_action(value)
        assert cfg.close_action == value
    cfg.set_close_action("minimize")
    assert cfg.close_action == "ask"
    cfg.config["close_action"] = "bogus"          # 手改坏的 json / hand-edited json
    assert cfg.close_action == "ask"


# ── 2. closeEvent 分流 / closeEvent routing ─────────────────────────────────

class _Event:
    """记录 accept/ignore 的假关闭事件。/ Fake close event recording the outcome."""

    def __init__(self):
        self.ignored = False
        self.accepted = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        self.accepted = True


def _fake_window(ask_result=None, tray_ok=True):
    """
    造一个只含 closeEvent 依赖成员的假窗口，记录调用了哪条路径。

    Build a stand-in self exposing only what closeEvent touches.
    """
    calls = []
    win = types.SimpleNamespace(
        _really_quit=False,
        worker=None,
        _tray_resident_available=lambda: tray_ok,
        _ask_close_action=lambda: calls.append("ask") or ask_result,
        _quit_app=lambda: calls.append("quit"),
        _hide_to_tray=lambda: calls.append("tray"),
        _save_main_window_placement=lambda: calls.append("save"),
        _stop_birdid_server=lambda: calls.append("stop_server"),
    )
    return win, calls


def _close(win):
    from ui.main_window import SuperPickyMainWindow
    event = _Event()
    SuperPickyMainWindow.closeEvent(win, event)
    return event


def test_close_tray_hides(qapp):
    from advanced_config import get_advanced_config
    get_advanced_config().set_close_action("tray")
    win, calls = _fake_window()
    event = _close(win)
    assert event.ignored and calls == ["save", "tray"]


def test_close_quit_goes_through_quit_app(qapp):
    from advanced_config import get_advanced_config
    get_advanced_config().set_close_action("quit")
    win, calls = _fake_window()
    event = _close(win)
    assert event.ignored and calls == ["quit"]


@pytest.mark.parametrize("choice,expected_calls", [
    ("tray", ["ask", "save", "tray"]),
    ("quit", ["ask", "quit"]),
])
def test_close_ask_remembers_choice(qapp, choice, expected_calls):
    """ask：按用户选择执行，并把选择写回配置。/ Act on the choice and remember it."""
    from advanced_config import get_advanced_config
    cfg = get_advanced_config()
    cfg.set_close_action("ask")
    win, calls = _fake_window(ask_result=choice)
    event = _close(win)
    assert event.ignored and calls == expected_calls
    assert cfg.close_action == choice


def test_close_ask_dismissed_cancels(qapp):
    """关掉询问框：窗口不关、设置仍为 ask。/ Dismissing cancels and keeps "ask"."""
    from advanced_config import get_advanced_config
    cfg = get_advanced_config()
    cfg.set_close_action("ask")
    win, calls = _fake_window(ask_result=None)
    event = _close(win)
    assert event.ignored and calls == ["ask"]
    assert cfg.close_action == "ask"


def test_close_without_tray_skips_setting(qapp, monkeypatch):
    """
    托盘不可用：走原有退出路径，不询问。/ No tray: legacy quit path, no question.
    """
    from advanced_config import get_advanced_config
    from ui.main_window import SuperPickyMainWindow
    get_advanced_config().set_close_action("ask")
    win, calls = _fake_window(tray_ok=False)
    # 兜底路径会调用 _cleanup 系列；只确认没有询问 / only assert no question
    win._force_quit = lambda: calls.append("force_quit")
    try:
        SuperPickyMainWindow.closeEvent(win, _Event())
    except Exception:
        pass
    assert "ask" not in calls


# ── 3. 询问框映射 / Dialog result mapping ───────────────────────────────────

@pytest.mark.parametrize("button_value,expected", [(2, "tray"), (3, "quit"), (0, None)])
def test_ask_close_action_mapping(qapp, monkeypatch, button_value, expected):
    from tools.i18n import get_i18n
    from ui.custom_dialogs import StyledMessageBox
    from ui.main_window import SuperPickyMainWindow
    monkeypatch.setattr(StyledMessageBox, "exec", lambda self: button_value)
    fake = types.SimpleNamespace(i18n=get_i18n())
    # parent=None：假 self 不是 QWidget / the stand-in self is not a QWidget
    class _NoParentBox(StyledMessageBox):
        def __init__(self, parent=None, **kw):
            super().__init__(parent=None, **kw)

    monkeypatch.setattr("ui.main_window.StyledMessageBox", _NoParentBox)
    assert SuperPickyMainWindow._ask_close_action(fake) == expected


# ── 4. 单实例 / Single instance ─────────────────────────────────────────────

def _pump_until(qapp, cond, seconds: float = 2.0) -> None:
    """处理事件直到 cond() 为真或超时。/ Pump events until cond() or timeout."""
    import time
    deadline = time.monotonic() + seconds
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _run_notify_in_subprocess(name: str, token: str):
    """
    在子进程里调用 notify_running_instance（真实场景就是两个进程），主线程负责
    处理服务端事件。子进程退出码 0 = 认定已有实例，1 = 照常启动。

    Run notify in a child process (the real scenario is two processes) while
    this process pumps the server's events. Exit 0 = verified instance, 1 = not.
    """
    import os
    import subprocess
    import sys
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
        "from tools.single_instance import SingleInstanceGuard as G;"
        "sys.exit(0 if G(sys.argv[2], token_path=sys.argv[3]).notify_running_instance() else 1)"
    )
    root = os.path.dirname(os.path.abspath(__file__))
    return subprocess.Popen([sys.executable, "-c", code, root, name, token])


def test_second_instance_activates_first(qapp, tmp_path):
    """第一个实例监听；第二个能连上、验证令牌并触发激活。/ Verified activation."""
    from tools.single_instance import SingleInstanceGuard

    name = f"SuperPicky-test-{uuid.uuid4().hex[:8]}"
    token = str(tmp_path / "token")
    activated = []
    first = SingleInstanceGuard(name, token_path=token)
    try:
        assert first.notify_running_instance() is False     # 还没有实例 / none yet
        assert first.listen(on_activate=lambda: activated.append(True))

        proc = _run_notify_in_subprocess(name, token)
        _pump_until(qapp, lambda: activated and proc.poll() is not None, seconds=15)
        assert proc.wait(15) == 0
        assert activated == [True]
    finally:
        first.close()

    # 关闭后名称释放，新实例可以重新监听 / name is free again after close
    third = SingleInstanceGuard(name, token_path=token)
    assert third.notify_running_instance() is False
    assert third.listen(on_activate=lambda: None)
    third.close()


def test_squatter_cannot_block_startup(qapp, tmp_path):
    """
    抢占者先建同名服务器、回错误内容：新进程不认它，照常启动。

    A squatter holding the name with a wrong reply must not stop startup.
    """
    from PySide6.QtNetwork import QLocalServer
    import tools.single_instance as si

    name = f"SuperPicky-test-{uuid.uuid4().hex[:8]}"
    squatter = QLocalServer()
    assert squatter.listen(name)

    def _answer():
        conn = squatter.nextPendingConnection()
        conn.waitForReadyRead(500)
        conn.readAll()
        conn.write(b"ok:not-the-token")
        conn.flush()
    squatter.newConnection.connect(_answer)

    token = str(tmp_path / "token")
    si.SingleInstanceGuard(name, token_path=token)._ensure_token()   # 本用户有真令牌
    proc = _run_notify_in_subprocess(name, token)
    _pump_until(qapp, lambda: proc.poll() is not None, seconds=15)
    squatter.close()
    assert proc.wait(15) == 1


def test_token_file_is_private(tmp_path):
    """POSIX 下令牌文件权限为 600。/ Token file is mode 600 on POSIX."""
    import os
    import sys
    from tools.single_instance import SingleInstanceGuard
    path = tmp_path / "sub" / "token"
    guard = SingleInstanceGuard("unused", token_path=str(path))
    token = guard._ensure_token()
    assert token and guard._ensure_token() == token          # 幂等 / stable
    if sys.platform != "win32":
        assert (os.stat(path).st_mode & 0o777) == 0o600


def test_server_name_is_stable_and_short():
    from tools.single_instance import server_name
    assert server_name() == server_name()
    assert server_name().startswith("SuperPicky-") and len(server_name()) < 40
