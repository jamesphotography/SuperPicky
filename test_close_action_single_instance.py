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

def test_second_instance_activates_first(qapp):
    """第一个实例监听；第二个能连上并触发激活。/ Second launch activates the first."""
    from tools.single_instance import SingleInstanceGuard

    name = f"SuperPicky-test-{uuid.uuid4().hex[:8]}"
    activated = []
    first = SingleInstanceGuard(name)
    try:
        assert first.notify_running_instance() is False     # 还没有实例 / none yet
        assert first.listen(on_activate=lambda: activated.append(True))

        second = SingleInstanceGuard(name)
        assert second.notify_running_instance() is True

        for _ in range(200):                                # 最多约 2 秒 / up to ~2s
            qapp.processEvents()
            if activated:
                break
            import time
            time.sleep(0.01)
        assert activated == [True]
    finally:
        first.close()

    # 关闭后名称释放，新实例可以重新监听 / name is free again after close
    third = SingleInstanceGuard(name)
    assert third.notify_running_instance() is False
    assert third.listen(on_activate=lambda: None)
    third.close()


def test_server_name_is_stable_and_short():
    from tools.single_instance import server_name
    assert server_name() == server_name()
    assert server_name().startswith("SuperPicky-") and len(server_name()) < 40
