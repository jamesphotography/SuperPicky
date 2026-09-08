# -*- coding: utf-8 -*-
"""
全局测试防线：不让模态对话框把测试进程静默挂死。

事故（2026-09-08）：某个端到端测试建了两个已处理批次后调 open_directory，
产品代码弹出目录选择框，`QDialog.exec()` 在无头环境里永远等不到人点。整个
pytest 进程就那么停住——没有报错、没有超时、没有任何输出。更糟的是当时用
``pytest ... | tail -3`` 取结果，管道的退出码是 tail 的，把进程 kill 掉之后
harness 看到的是 exit 0，于是「全量测试绿」被误报了两次。

阻塞比失败危险得多：失败会告诉你是哪一行，阻塞只会让人以为「还在跑」。所以
这里把模态入口统统换成立刻抛错——测试要么自己 stub 掉那个交互点，要么当场
看到一条指名道姓的报错。

需要走对话框的测试照常 monkeypatch 自己那一个（monkeypatch 在用例体内执行，
晚于本 fixture，所以一定盖得住这里的替身）。

A modal dialog in a headless test run blocks forever with no output, which is
far worse than a failure. Every modal entry point raises instead; tests that
need one stub it themselves.
"""
import os

import pytest

# Qt 平台必须在任何 QApplication 之前定好，否则 CI/无头机上会去连显示服务
# Must be set before any QApplication exists.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _blocked(name: str):
    """造一个「一调用就报错」的替身，报错里写清楚该怎么办。"""

    def _raise(*_args, **_kwargs):
        raise RuntimeError(
            f"测试中调用了模态对话框 {name}()，它会永久阻塞整个测试进程。"
            f"请在用例里 stub 掉这个交互点"
            f"（例如 monkeypatch.setattr(窗口类, '_ask_which_batches', ...)）。"
            f"\nBlocking modal {name}() called during tests; stub it instead."
        )

    return _raise


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """把所有模态入口换成立刻抛错的替身（每个用例自动生效）。"""
    try:
        from PySide6.QtWidgets import (QDialog, QFileDialog, QInputDialog,
                                       QMessageBox)
    except Exception:          # 没装 Qt 的环境（纯逻辑测试）直接跳过
        return

    monkeypatch.setattr(QDialog, "exec", _blocked("QDialog.exec"))
    # QMessageBox / QFileDialog / QInputDialog 的静态便捷方法在 C++ 里自己起
    # 事件循环，盖不住上面那个 QDialog.exec，必须逐个替换。
    # The static convenience methods spin their own C++ event loop.
    for cls, methods in (
        (QMessageBox, ("warning", "information", "critical", "question", "about")),
        (QFileDialog, ("getExistingDirectory", "getOpenFileName",
                       "getOpenFileNames", "getSaveFileName")),
        (QInputDialog, ("getText", "getItem", "getInt", "getDouble")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method,
                                staticmethod(_blocked(f"{cls.__name__}.{method}")),
                                raising=False)
