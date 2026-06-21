# -*- coding: utf-8 -*-
"""_EnhanceWorker 回传修图结果 / worker emits enhanced array."""
import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from ui import crop_studio  # noqa: E402
from core.enhance.options import EnhanceOptions  # noqa: E402


def _spin_until(pred, timeout_ms=3000):
    QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(lambda: pred() and loop.quit())
    t.start(10)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    t.stop()


def test_worker_emits_enhanced(monkeypatch):
    img = np.full((16, 16, 3), 100, np.uint8)
    monkeypatch.setattr(crop_studio, "_pipeline_enhance",
                        lambda rgb, opts, **kw: np.full_like(rgb, 200),
                        raising=False)
    got = {}
    w = crop_studio._EnhanceWorker(img, EnhanceOptions())
    w.done.connect(lambda arr: got.__setitem__("arr", arr))
    w.start()
    _spin_until(lambda: "arr" in got)
    w.wait(2000)
    assert "arr" in got
    assert got["arr"][0, 0, 0] == 200
