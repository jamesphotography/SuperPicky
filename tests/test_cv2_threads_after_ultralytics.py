# -*- coding: utf-8 -*-
"""ultralytics 导入的 cv2 单线程副作用必须被中和。

The cv2 single-thread side effect of importing ultralytics must be
neutralized.

背景 / Background:
`import ultralytics` 会全局调用 cv2.setNumThreads(0)（防его训练场景
fork-DataLoader 死锁），把整个进程的 OpenCV 压成单线程：45MP 照片的
INTER_AREA 从 ~24ms 暴涨到 ~225ms，TOPIQ 两段式 resize 的提速在生产
App 里（必然导入 YOLO）被整个抵消，视频分析等所有 cv2 热路径同样受害。
SuperPicky 是纯推理 App（无 fork DataLoader；macOS/Windows 的
multiprocessing 均为 spawn），该保护不适用，导入后应立即恢复线程池。

`import ultralytics` globally calls cv2.setNumThreads(0) (guarding their
fork-DataLoader training deadlock), single-threading OpenCV for the whole
process: INTER_AREA on a 45MP frame jumps from ~24ms to ~225ms, wiping out
the TOPIQ two-stage resize speedup in the packaged app (which always imports
YOLO) and slowing every other cv2 hot path. SuperPicky is inference-only
(no fork DataLoaders; multiprocessing spawns on macOS/Windows), so the guard
does not apply — the thread pool must be restored right after import.

测试用子进程隔离：同一 pytest 进程里其他测试可能早已导入过相关模块。
Subprocess isolation: other tests in the same pytest process may have
already imported the relevant modules.
"""
import subprocess
import sys

import cv2
import pytest


@pytest.mark.skipif(cv2.getNumberOfCPUs() <= 1, reason="单核机器无线程池可言")
def test_birdid_import_keeps_cv2_thread_pool():
    """经 birdid 包链路导入 ultralytics 后，cv2 线程池必须仍然可用（>1）。"""
    code = (
        "import cv2, sys\n"
        "import birdid  # __init__ -> bird_identifier -> ultralytics\n"
        "n = cv2.getNumThreads()\n"
        "print(f'threads={n}', file=sys.stderr)\n"
        "sys.exit(0 if n > 1 else 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, (
        f"birdid 导入后 cv2 线程池未恢复: {result.stderr.strip()}"
    )


@pytest.mark.skipif(cv2.getNumberOfCPUs() <= 1, reason="单核机器无线程池可言")
def test_ai_model_import_keeps_cv2_thread_pool():
    """经主选片流程 ai_model 导入 YOLO 后，cv2 线程池必须仍然可用（>1）。"""
    code = (
        "import cv2, sys\n"
        "import ai_model\n"
        "n = cv2.getNumThreads()\n"
        "print(f'threads={n}', file=sys.stderr)\n"
        "sys.exit(0 if n > 1 else 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, (
        f"ai_model 导入后 cv2 线程池未恢复: {result.stderr.strip()}"
    )
