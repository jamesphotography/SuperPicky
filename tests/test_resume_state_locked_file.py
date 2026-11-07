# -*- coding: utf-8 -*-
"""
续跑进度文件被别的进程占用时的行为（#156）。

现网症状（Windows 11）：选片中途偶发
「Photo 141 hit an error and was skipped ... [WinError 5] Access is denied:
'...resume_xxx.json' -> '...resume_state.json'」，同一批重跑时换一张出现或不出现。
照片其实已经处理完（评星、识鸟都做了），却被计为失败，汇总里还说它「没评星、
没整理」。

根因：每处理完一张都要 os.replace 覆盖 resume_state.json；Windows 上目标文件被
杀毒软件 / 搜索索引 / 同步程序短暂打开时替换会被拒绝，异常一路冒到单张照片的
兜底 except。

要守住的行为：短暂占用 → 重试后写成功；一直占用 → 返回 False、不抛异常、
不留临时文件。ResumeStateManager 不抛异常，选片循环就不会把照片判为失败。

On Windows, replacing resume_state.json fails with WinError 5 while another
process briefly holds it; the error used to bubble up and fail a photo that was
already fully processed. Writes now retry briefly and never raise.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.resume_state as rs
from tools.resume_state import ResumeStateManager

ROOT = Path(__file__).resolve().parent.parent


def _locked_replace(monkeypatch, failures: int) -> list:
    """
    让 os.replace 前 failures 次抛 WinError 5 式的 PermissionError。

    Make os.replace raise a WinError-5-style PermissionError `failures` times.
    """
    calls = []
    real_replace = os.replace

    def fake_replace(src, dst):
        calls.append(dst)
        if len(calls) <= failures:
            raise PermissionError(13, "Access is denied", src)
        real_replace(src, dst)

    monkeypatch.setattr(rs.os, "replace", fake_replace)
    monkeypatch.setattr(rs.time, "sleep", lambda _s: None)
    return calls


def _leftover_temp_files(manager: ResumeStateManager) -> list:
    return [n for n in os.listdir(manager.state_dir)
            if n.startswith("resume_") and n != manager.FILENAME]


def test_brief_lock_is_retried_and_write_succeeds(tmp_path, monkeypatch):
    """被占用两次后放开：写入成功，进度正确，不留临时文件。"""
    manager = ResumeStateManager(str(tmp_path))
    assert manager.start(["A", "B", "C"]) is True

    calls = _locked_replace(monkeypatch, failures=2)
    assert manager.mark_completed("A") is True

    assert len(calls) == 3
    data = json.loads(Path(manager.state_path).read_text(encoding="utf-8"))
    assert data["pending_prefixes"] == ["B", "C"]
    assert _leftover_temp_files(manager) == []


def test_persistent_lock_returns_false_without_raising(tmp_path, monkeypatch):
    """一直被占用：mark_completed 返回 False、不抛异常，原进度保留、不留临时文件。"""
    manager = ResumeStateManager(str(tmp_path))
    manager.start(["A", "B"])

    _locked_replace(monkeypatch, failures=99)
    assert manager.mark_completed("A") is False

    assert isinstance(manager.last_error, PermissionError)
    data = json.loads(Path(manager.state_path).read_text(encoding="utf-8"))
    assert data["pending_prefixes"] == ["A", "B"], "写失败时原进度不该被破坏"
    assert _leftover_temp_files(manager) == []


def test_start_with_locked_file_returns_false(tmp_path, monkeypatch):
    """开始新一轮时上一轮的进度文件被占用：返回 False，不抛异常。"""
    manager = ResumeStateManager(str(tmp_path))
    manager.start(["old"])

    _locked_replace(monkeypatch, failures=99)
    assert manager.start(["A"]) is False


def test_clear_with_locked_file_returns_false(tmp_path, monkeypatch):
    """选片结束时删进度文件被拒：返回 False，不让整轮在收尾时报错。"""
    manager = ResumeStateManager(str(tmp_path))
    manager.start(["A"])

    def locked_remove(_path):
        raise PermissionError(13, "Access is denied", _path)

    monkeypatch.setattr(rs.os, "remove", locked_remove)
    monkeypatch.setattr(rs.time, "sleep", lambda _s: None)
    assert manager.clear() is False


def test_last_photo_completing_clears_the_file(tmp_path):
    """最后一张完成时删掉进度文件，返回 True（原有行为不变）。"""
    manager = ResumeStateManager(str(tmp_path))
    manager.start(["A"])
    assert manager.mark_completed("A") is True
    assert not manager.exists()


def test_warning_text_exists_in_every_locale():
    """三种界面都有这条警告，且都带 {error} 占位符。"""
    for loc in ("en_US", "zh_CN", "zh_TW"):
        data = json.loads((ROOT / "locales" / f"{loc}.json").read_text(encoding="utf-8"))
        text = data["logs"]["resume_state_write_failed"]
        assert "{error}" in text, loc
