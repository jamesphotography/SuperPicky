# -*- coding: utf-8 -*-
"""
单实例保护（#123）/ Single-instance guard (#123).

主窗口关闭后默认可以驻留托盘，如果再双击启动一次，旧版本会再开一个完整进程
（各自预加载模型、占几百 MB 内存），全部关窗后任务管理器里挂着好几个进程。
本模块用 Qt 的 QLocalServer/QLocalSocket（Windows 上是命名管道，macOS/Linux
上是 Unix 域套接字）实现：

1. 启动时先尝试连接同名本地服务器；连得上说明已有实例在运行，发一条
   "activate" 让它把窗口调到前台，然后本进程直接退出；
2. 连不上则自己监听，之后收到 "activate" 就回调 on_activate。

名称按当前系统用户区分，不同用户各自一个实例。QtNetwork 不可用或监听失败时
不阻止启动（只是失去保护），保证这一层出错不会让程序打不开。
开发调试可设环境变量 SUPERPICKY_ALLOW_MULTI_INSTANCE=1 关闭保护。

Uses QLocalServer/QLocalSocket (named pipes on Windows, Unix domain sockets
on macOS/Linux). On start, try to connect to the per-user server: success
means an instance is already running, so ask it to come forward and exit.
Otherwise listen and call on_activate whenever a later launch asks. Any
failure here degrades to "no guard" rather than blocking startup. Set
SUPERPICKY_ALLOW_MULTI_INSTANCE=1 to disable the guard for development.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import sys
from typing import Callable, Optional

# 连接已有实例时等待的毫秒数。连接在系统层完成，不依赖对方事件循环是否空闲
# （对方正忙着加载模型也能连上），所以 1 秒足够，又不会明显拖慢正常启动。
# Connect timeout in ms. The OS completes the connection without the other
# side's event loop, so a busy instance still accepts within this window.
CONNECT_TIMEOUT_MS = 1000

ACTIVATE_MESSAGE = b"activate"

ENV_ALLOW_MULTI = "SUPERPICKY_ALLOW_MULTI_INSTANCE"


def server_name() -> str:
    """
    生成按系统用户区分的本地服务器名称。

    用户名做哈希，避免特殊字符进入管道/套接字名，并控制长度（Unix 域套接字路径
    有约 104 字节上限）。

    返回:
    str: 形如 "SuperPicky-1a2b3c4d5e6f" 的名称

    Build a per-user server name; the user name is hashed to keep it short and
    free of special characters (Unix socket paths are limited to ~104 bytes).

    Return:
    str: a name like "SuperPicky-1a2b3c4d5e6f"
    """
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    digest = hashlib.sha1(user.encode("utf-8")).hexdigest()[:12]
    return f"SuperPicky-{digest}"


class SingleInstanceGuard:
    """
    单实例保护器。必须在 QApplication 创建之后、主窗口创建之前使用。

    用法:
        guard = SingleInstanceGuard()
        if guard.notify_running_instance():
            sys.exit(0)                     # 已有实例，已请求它显示窗口
        guard.listen(on_activate=window_show_callback)

    Single-instance guard. Use after QApplication exists and before the main
    window is built. Keep a reference for the app's lifetime so the server is
    not garbage-collected.
    """

    def __init__(self, name: Optional[str] = None) -> None:
        """
        参数:
        name (Optional[str]): 本地服务器名称；默认用 server_name()（测试可注入）

        Parameters:
        name (Optional[str]): server name; defaults to server_name() (tests inject one)
        """
        self.name = name or server_name()
        self._server = None
        self._on_activate: Optional[Callable[[], None]] = None

    @staticmethod
    def disabled_by_env() -> bool:
        """
        是否通过环境变量关闭了单实例保护（开发调试用）。

        Return True when SUPERPICKY_ALLOW_MULTI_INSTANCE=1 disables the guard.
        """
        return os.environ.get(ENV_ALLOW_MULTI) == "1"

    def notify_running_instance(self) -> bool:
        """
        尝试连接已运行的实例并请求它显示窗口。

        返回:
        bool: True 表示已有实例在运行（调用方应退出）；False 表示没有，或
              QtNetwork 不可用（调用方继续正常启动）

        Try to reach a running instance and ask it to show its window.

        Return:
        bool: True if another instance is running (the caller should exit);
              False if none, or QtNetwork is unavailable (start normally)
        """
        try:
            from PySide6.QtNetwork import QLocalSocket
        except Exception as e:
            print(f"[SingleInstance] QtNetwork unavailable, guard disabled: {e}")
            return False

        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        # Windows 的前台锁：后台进程自己调 activateWindow 通常只会让任务栏按钮
        # 闪烁。本进程刚由用户启动、持有前台权限，先把权限让给已有实例。
        # Windows foreground lock: a background process calling activateWindow
        # usually only flashes its taskbar button. This freshly launched process
        # holds foreground rights, so hand them to the running instance first.
        if sys.platform == "win32":
            try:
                import ctypes
                ASFW_ANY = -1
                ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
            except Exception:
                pass
        socket.write(ACTIVATE_MESSAGE)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def listen(self, on_activate: Callable[[], None]) -> bool:
        """
        开始监听，之后的启动请求会触发 on_activate（在主线程执行）。

        先 removeServer 清掉上次崩溃残留的 Unix 套接字文件（Windows 上无副作用）；
        只有在 notify_running_instance 确认没有存活实例之后才能调用本方法，
        否则会删掉正在用的套接字。

        参数:
        on_activate (Callable[[], None]): 收到 "activate" 时的回调，通常是显示主窗口

        返回:
        bool: 是否监听成功；失败只记日志，不影响启动

        Start listening; later launches trigger on_activate on the GUI thread.
        removeServer first clears a stale Unix socket left by a crash (no-op on
        Windows). Only call this after notify_running_instance found nobody,
        otherwise a live socket would be removed.

        Parameters:
        on_activate (Callable[[], None]): callback, usually "show the main window"

        Return:
        bool: whether listening succeeded; failure is logged, startup goes on
        """
        try:
            from PySide6.QtNetwork import QLocalServer
        except Exception as e:
            print(f"[SingleInstance] QtNetwork unavailable, guard disabled: {e}")
            return False

        self._on_activate = on_activate
        QLocalServer.removeServer(self.name)
        server = QLocalServer()
        # 仅当前用户可连 / only the current user may connect
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            print(f"[SingleInstance] listen failed, guard disabled: {server.errorString()}")
            return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        """
        处理新连接：读到 "activate" 就回调；回调异常只记日志，不影响主程序。

        Handle a new connection: call back on "activate"; callback errors are
        logged and never propagate into the app.
        """
        server = self._server
        if server is None:
            return
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                break
            if conn.bytesAvailable() == 0:
                conn.waitForReadyRead(CONNECT_TIMEOUT_MS)
            data = bytes(conn.readAll().data())
            conn.disconnectFromServer()
            conn.deleteLater()
            if data.startswith(ACTIVATE_MESSAGE) and self._on_activate is not None:
                try:
                    self._on_activate()
                except Exception as e:
                    print(f"[SingleInstance] activate callback failed: {e}")

    def close(self) -> None:
        """
        停止监听并释放名称（退出时调用；进程退出时系统也会回收）。

        Stop listening and release the name (call on quit; the OS also
        reclaims it when the process exits).
        """
        if self._server is not None:
            self._server.close()
            self._server = None
