# -*- coding: utf-8 -*-
"""
Mac 打包脚本的重试与超时测试：hdiutil "Resource busy"、stapler 断网等 CI 偶发失败
应自动重试，全部失败才抛出；notarytool submit 必须带子进程硬超时。

Tests for the Mac build script's retry and timeout handling.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_release_mac as brm  # noqa: E402


def _fake_run(results):
    """
    依次返回 results 里的结果；元素是返回码或要抛出的异常。

    Return the queued results in order; each item is a return code or an exception.
    """
    calls = []

    def run(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        item = results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return subprocess.CompletedProcess(cmd, item, "", "")

    return run, calls


def test_retry_succeeds_after_transient_failures(monkeypatch):
    """前两次失败（非零返回码、超时）、第三次成功 → 返回成功结果，重试前调用清理。"""
    run, calls = _fake_run([1, subprocess.TimeoutExpired("hdiutil", 5), 0])
    monkeypatch.setattr(brm.subprocess, "run", run)
    monkeypatch.setattr(brm.time, "sleep", lambda _s: None)
    cleaned = []
    result = brm.run_command_with_retry(
        ["hdiutil", "create"], label="生成 DMG", attempts=3, timeout=99,
        before_retry=lambda: cleaned.append(True),
    )
    assert result.returncode == 0
    assert calls == [99, 99, 99]
    assert len(cleaned) == 2


def test_retry_raises_after_all_attempts_fail(monkeypatch):
    """全部失败时抛出最后一次的异常，且只尝试 attempts 次。"""
    run, calls = _fake_run([68, 68, 68, 0])
    monkeypatch.setattr(brm.subprocess, "run", run)
    monkeypatch.setattr(brm.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="装订公证票据"):
        brm.run_command_with_retry(["xcrun", "stapler", "staple", "x.dmg"], label="装订公证票据", attempts=3)
    assert len(calls) == 3


def test_notary_submit_has_hard_timeout():
    """notarytool submit 必须带子进程硬超时，且比 --wait 的 30 分钟长。"""
    src = (ROOT / "build_release_mac.py").read_text(encoding="utf-8")
    assert "timeout=NOTARY_SUBMIT_HARD_TIMEOUT" in src
    assert brm.NOTARY_SUBMIT_HARD_TIMEOUT > 30 * 60
