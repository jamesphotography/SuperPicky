# -*- coding: utf-8 -*-
"""
缩略图加载线程的生命周期回归测试。

对应现网崩溃（2026-09-07 定位）：进程会间歇性整个 abort。18 份崩溃报告里
7 份是 ``QThread::~QThread()`` → ``QMessageLogger::fatal`` → ``abort``
（Qt 的 "QThread: Destroyed while thread is still running"），另外 11 份是
malloc freelist 损坏后的 SIGTRAP——同一根因的另一种表现：线程仍在往已释放
的对象里写，破坏了堆，下一个申请内存的人（torch / Qt）才 trap。

根因：``ThumbnailGrid`` 的 ``_PreloadWorker`` 持有 8 个 ``_ThumbnailWorker``
(QThread)，它们阻塞在 ``threading.Condition.wait()`` 等任务，只有 ``cancel()``
能唤醒并让它们退出。若 grid 没被 cleanup 就走到进程退出，PySide 的
destructionVisitor 会析构这些仍在运行的 QThread，Qt 直接 qFatal。

崩溃报告里的 8 个 ``_ThumbnailWorker`` 线程全部停在 ``_PyMutex_LockTimed``
（即那个 Condition.wait），正是这个状态。

Regression tests: a grid must not leave its worker threads running at process
exit, or PySide destroys live QThreads and Qt aborts the whole process.
"""
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(__file__))

_app = QApplication.instance() or QApplication([])

_REPO = os.path.dirname(os.path.abspath(__file__))


def _run_in_subprocess(body: str) -> subprocess.CompletedProcess:
    """
    在独立进程里跑一段用到 ThumbnailGrid 的代码，返回结果。

    崩溃是进程级 abort，同进程内无法断言，只能看子进程的退出码。
    The crash aborts the whole process, so it can only be asserted from
    outside via the child's exit code.
    """
    script = textwrap.dedent(f"""
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        sys.path.insert(0, {_REPO!r})
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        from tools.i18n import get_i18n
        from ui.thumbnail_grid import ThumbnailGrid

        def photos(n):
            return [{{"filename": "D%d" % i,
                      "current_path": "/nonexistent/D%d.NEF" % i,
                      "rating": 3}} for i in range(n)]

{textwrap.indent(textwrap.dedent(body), "        ")}
    """)
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=120)


def test_process_exits_cleanly_when_grid_is_never_cleaned_up():
    """
    建了网格却没调 cleanup 就退出进程 —— 不得 abort。

    这是崩溃的最小复现：8 个工作线程阻塞在条件变量上，进程退出时 PySide
    析构这些仍在运行的 QThread，Qt 调 qFatal 整个进程挂掉。应用里任何一处
    忘了 cleanup（或走了不经过 closeEvent 的退出路径）都会踩到。
    """
    r = _run_in_subprocess("""
        g = ThumbnailGrid(get_i18n())
        g.load_photos(photos(20))
        g._deferred_build()          # 启动 8 个工作线程
        assert g._loader is not None and g._loader.isRunning()
        print("READY")
    """)

    assert "READY" in r.stdout, f"子进程没跑到位: {r.stdout}\n{r.stderr}"
    assert "still running" not in r.stderr, (
        f"仍在销毁运行中的 QThread:\n{r.stderr}"
    )
    assert r.returncode == 0, (
        f"进程异常退出 (code={r.returncode})，"
        f"128+6=134 为 SIGABRT。stderr:\n{r.stderr}"
    )


def test_process_exits_cleanly_after_repeated_filter_switches():
    """
    连续切换筛选（反复 load_photos → _detach_loader）后退出 —— 不得 abort。

    每次切筛选都会废弃一个加载器；被废弃的那些同样要在退出前收干净，
    否则用户多切几次筛选再关应用就会崩。
    """
    r = _run_in_subprocess("""
        g = ThumbnailGrid(get_i18n())
        for i in range(4):
            g.load_photos(photos(10))
            g._deferred_build()
        print("READY")
    """)

    assert "READY" in r.stdout, f"子进程没跑到位: {r.stdout}\n{r.stderr}"
    assert "still running" not in r.stderr, f"仍有运行中的 QThread:\n{r.stderr}"
    assert r.returncode == 0, f"进程异常退出 (code={r.returncode}):\n{r.stderr}"


def test_explicit_cleanup_still_stops_the_workers():
    """显式 cleanup 依然要把线程停干净（原有行为不能被改坏）。"""
    from ui.thumbnail_grid import ThumbnailGrid
    from tools.i18n import get_i18n

    g = ThumbnailGrid(get_i18n())
    try:
        g.load_photos([{"filename": f"D{i}",
                        "current_path": f"/nonexistent/D{i}.NEF", "rating": 3}
                       for i in range(20)])
        g._deferred_build()
        loader = g._loader
        assert loader is not None and loader.isRunning()

        g.cleanup()

        assert not loader.isRunning(), "cleanup 后工作线程仍在运行"
    finally:
        g.close()
