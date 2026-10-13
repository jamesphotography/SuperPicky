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

防抢占：管道名由用户名推导、可被预测，同机其他账户或程序可以抢先建同名管道，
让本程序误以为「已有实例」而退出（打不开）。因此已有实例收到 "activate" 后必须
回一个只存在本用户配置目录里的随机令牌（single_instance.token），新进程核对一致
才认定是真实例；不一致或不回应就照常启动——最坏只是失去单实例保护。

名称按当前系统用户区分，不同用户各自一个实例。QtNetwork 不可用或监听失败时
不阻止启动（只是失去保护），保证这一层出错不会让程序打不开。
开发调试可设环境变量 SUPERPICKY_ALLOW_MULTI_INSTANCE=1 关闭保护。

Uses QLocalServer/QLocalSocket (named pipes on Windows, Unix domain sockets
on macOS/Linux). On start, try to connect to the per-user server: success
means an instance is already running, so ask it to come forward and exit.
Otherwise listen and call on_activate whenever a later launch asks. Any
failure here degrades to "no guard" rather than blocking startup. A running
instance must answer with a random token kept in the user's config dir, so a
squatter that pre-creates the predictable pipe name cannot make SuperPicky
refuse to start; an unverified answer means "start normally". Set
SUPERPICKY_ALLOW_MULTI_INSTANCE=1 to disable the guard for development.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import secrets
import sys
from typing import Callable, Optional

# 连接已有实例时等待的毫秒数。连接在系统层完成，不依赖对方事件循环是否空闲
# （对方正忙着加载模型也能连上），所以 1 秒足够，又不会明显拖慢正常启动。
# Connect timeout in ms. The OS completes the connection without the other
# side's event loop, so a busy instance still accepts within this window.
CONNECT_TIMEOUT_MS = 1000

# 等待已有实例回令牌的毫秒数。回令牌要等对方事件循环处理，主窗口构造期间
# 主线程可能忙上几秒，所以比连接超时宽松；只在确有进程占着名字时才会等。
# Wait for the token reply. The reply needs the other side's event loop, which
# can be busy for seconds while its window is built; only paid when something
# actually holds the name.
REPLY_TIMEOUT_MS = 8000

ACTIVATE_MESSAGE = b"activate"
REPLY_PREFIX = b"ok:"
TOKEN_FILE_NAME = "single_instance.token"

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

    def __init__(self, name: Optional[str] = None, token_path: Optional[str] = None) -> None:
        """
        参数:
        name (Optional[str]): 本地服务器名称；默认用 server_name()（测试可注入）
        token_path (Optional[str]): 令牌文件路径；默认放在应用配置目录（测试可注入）

        Parameters:
        name (Optional[str]): server name; defaults to server_name() (tests inject one)
        token_path (Optional[str]): token file; defaults to the app config dir
        """
        self.name = name or server_name()
        if token_path is None:
            from config import get_app_config_dir
            token_path = str(get_app_config_dir() / TOKEN_FILE_NAME)
        self.token_path = token_path
        self._token: Optional[bytes] = None
        self._server = None
        self._on_activate: Optional[Callable[[], None]] = None

    @staticmethod
    def disabled_by_env() -> bool:
        """
        是否通过环境变量关闭了单实例保护（开发调试用）。

        Return True when SUPERPICKY_ALLOW_MULTI_INSTANCE=1 disables the guard.
        """
        return os.environ.get(ENV_ALLOW_MULTI) == "1"

    def _read_token(self) -> Optional[bytes]:
        """
        读令牌文件；不存在或读失败返回 None。

        Read the token file; None if missing or unreadable.
        """
        try:
            with open(self.token_path, "rb") as fh:
                token = fh.read().strip()
            return token or None
        except OSError:
            return None

    def _ensure_token(self) -> Optional[bytes]:
        """
        取令牌，没有就生成一个随机令牌写入（POSIX 权限 600；Windows 的用户配置
        目录本身只有本用户可读）。写失败返回 None，调用方据此放弃监听。

        Return the token, creating a random one if missing (mode 600 on POSIX;
        the Windows per-user config dir is already private). None on failure.
        """
        token = self._read_token()
        if token:
            return token
        token = secrets.token_hex(16).encode("ascii")
        try:
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(token)
        except OSError as e:
            print(f"[SingleInstance] cannot write token, guard disabled: {e}")
            return None
        return token

    def notify_running_instance(self) -> bool:
        """
        尝试连接已运行的实例并请求它显示窗口。

        返回:
        bool: True 表示已有实例在运行且回了正确令牌（调用方应退出）；False 表示
              没有、对方无法验证（可能是抢占者），或 QtNetwork 不可用（照常启动）

        Try to reach a running instance and ask it to show its window.

        Return:
        bool: True if a verified instance is running (the caller should exit);
              False if none, the responder is unverified (possibly a squatter),
              or QtNetwork is unavailable (start normally)
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

        # 核对令牌：真实例会回 "ok:<令牌>"；抢占者读不到本用户的令牌文件
        # Verify: a genuine instance replies "ok:<token>"; a squatter cannot
        # read this user's token file.
        reply = b""
        if socket.waitForReadyRead(REPLY_TIMEOUT_MS):
            reply = bytes(socket.readAll().data())
        socket.disconnectFromServer()
        token = self._read_token()
        if token and reply.strip() == REPLY_PREFIX + token:
            return True
        print("[SingleInstance] responder did not prove it is SuperPicky; starting normally.")
        return False

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

        token = self._ensure_token()
        if token is None:
            return False
        self._token = token
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
            is_activate = data.startswith(ACTIVATE_MESSAGE)
            if is_activate and self._token:
                # 先回令牌证明身份，再激活 / prove identity first, then activate
                conn.write(REPLY_PREFIX + self._token)
                conn.flush()
                conn.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            conn.disconnectFromServer()
            conn.deleteLater()
            if is_activate and self._on_activate is not None:
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
