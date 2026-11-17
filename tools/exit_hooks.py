# -*- coding: utf-8 -*-
"""
进程退出钩子注册表：本项目代码一律用它代替 ``atexit.register``。

为什么不直接用 atexit：GUI 退出时 ``main.py`` 用 ``os._exit()`` 结束进程，
以跳过 Python 解释器析构与 C 的 ``exit()`` 静态析构——那两步会让 PySide/shiboken
销毁残存的 Qt 包装对象，在 macOS 上稳定段错误（见 ``main._finish_shutdown_and_exit``）。
``os._exit()`` 同时也会跳过所有 atexit 钩子，而 ExifTool 进程关闭、缩略图加载
线程停止等收尾都挂在钩子上。把钩子集中登记在这里，``main.py`` 就能在
``os._exit()`` 之前显式执行它们，一个都不漏。

非 GUI 路径（命令行、测试）照常由解释器退出：``run_all`` 本身也登记在 atexit 上，
且每次 ``register`` 都把它挪到 atexit 队尾，保证它先于更早登记的钩子
（例如 PySide 自己的模块清理）执行，与原先各自 ``atexit.register`` 的先后关系一致。
钩子执行一次即移出列表，两条路径都走到也不会重复执行。

Process-exit hook registry; project code uses this instead of ``atexit.register``.

The GUI ends the process with ``os._exit()`` (see
``main._finish_shutdown_and_exit``) to skip interpreter finalization and C
``exit()`` static destructors, where PySide/shiboken tear down surviving Qt
wrappers and segfault on macOS. ``os._exit()`` also skips atexit, which is where
ExifTool shutdown and thumbnail-loader stops live. Registering them here lets
``main.py`` run them explicitly before ``os._exit()``. Non-GUI paths (CLI,
tests) still exit normally: ``run_all`` is itself an atexit hook, re-queued on
every ``register`` so it runs before earlier atexit hooks such as PySide's own
module cleanup. Each hook runs at most once.
"""

import atexit
import threading
from typing import Callable, List

_hooks: List[Callable[[], None]] = []
_lock = threading.Lock()


def register(func: Callable[[], None]) -> Callable[[], None]:
    """
    登记一个退出钩子（无参可调用对象），用法同 ``atexit.register``。

    参数:
    func (Callable[[], None]): 退出时要执行的清理函数；应当幂等、自行吞掉可预期的异常

    返回:
    Callable[[], None]: 原样返回 func，便于当装饰器使用

    Register an exit hook (zero-arg callable), like ``atexit.register``.

    Parameters:
    func (Callable[[], None]): Cleanup to run at exit; should be idempotent.

    Return:
    Callable[[], None]: func itself, so this also works as a decorator.
    """
    with _lock:
        _hooks.append(func)
    # 挪到 atexit 队尾：atexit 后进先出，保证 run_all 先于此前登记的钩子执行
    # Move to the end of the atexit queue (LIFO) so run_all precedes earlier hooks
    atexit.unregister(run_all)
    atexit.register(run_all)
    return func


def run_all() -> None:
    """
    按登记的逆序（与 atexit 相同）执行并清空所有钩子；单个钩子失败不影响其余钩子。

    可重复调用：已执行的钩子已移出列表，不会再执行。

    Run and drain all hooks in reverse registration order (as atexit does); a
    failing hook does not stop the rest. Safe to call repeatedly.
    """
    while True:
        with _lock:
            if not _hooks:
                return
            func = _hooks.pop()
        try:
            func()
        except Exception as e:  # noqa: BLE001 - 退出阶段尽力而为 / best effort at exit
            name = getattr(func, "__qualname__", repr(func))
            print(f"[ExitHooks] exit hook {name} failed: {e}")
