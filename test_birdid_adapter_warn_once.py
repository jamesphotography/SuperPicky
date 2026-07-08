# -*- coding: utf-8 -*-
"""BirdIDAdapter 失败告警限流：类属性封装，不用模块级全局变量。

BirdIDAdapter failure-warning rate limit: class-attribute state, no
module-level global.

背景 / Background:
「identify_bird 失败只警告一次」的限流状态曾是模块级全局变量
`_warned_identify_failure` + `global` 关键字，违反 CLAUDE.md
「避免全局变量、优先类封装」。这组测试锁定：状态收进 BirdIDAdapter
类属性、模块级不再有该全局名、且「进程内只打印一次」语义保持不变。

The once-per-process warning flag used to be a module-level global mutated
via the `global` keyword, violating CLAUDE.md's "avoid globals, prefer
class encapsulation". These tests pin: the state lives on the BirdIDAdapter
class, the module-level global is gone, and the warn-once semantics are
unchanged.
"""
import pytest

from core import birdid_adapter
from core.birdid_adapter import BirdIDAdapter


@pytest.fixture()
def fresh_warn_state():
    """每个测试前后复位类属性，避免测试间互相污染。"""
    BirdIDAdapter._warned_identify_failure = False
    yield
    BirdIDAdapter._warned_identify_failure = False


def test_no_module_level_global():
    """模块级不得再有 _warned_identify_failure 全局变量。"""
    assert not hasattr(birdid_adapter, "_warned_identify_failure")
    assert hasattr(BirdIDAdapter, "_warned_identify_failure")


def test_identify_failure_warns_once_per_process(fresh_warn_state, monkeypatch, capsys):
    """连续失败只打印一次警告（视频逐帧场景防刷屏），且仍返回空列表。

    跨实例也只警告一次——限流语义是进程级（类属性），与原实现一致。
    Warn-once holds across adapter instances: the semantics stay
    process-level (class attribute), same as the original behavior.
    """
    import numpy as np
    from birdid import bird_identifier

    def _boom(**kwargs):
        raise RuntimeError("model broken")

    # identify() 在方法体内做局部导入，需 patch 源模块的符号
    # identify() imports locally inside the method — patch the source module
    monkeypatch.setattr(bird_identifier, "identify_bird", _boom)

    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    adapter = BirdIDAdapter()
    assert adapter.identify(frame) == []
    assert adapter.identify(frame) == []
    assert BirdIDAdapter().identify(frame) == []  # 第二个实例 / second instance

    out = capsys.readouterr().out
    assert out.count("[BirdIDAdapter]") == 1
