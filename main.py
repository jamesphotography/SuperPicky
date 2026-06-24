#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - PySide6 版本入口点。
SuperPicky - PySide6 application entrypoint.

本模块负责最早期的进程初始化、补丁覆盖层注入、运行时自举分流与 Qt 应用启动。
This module owns early process initialization, patch overlay injection, runtime
bootstrap dispatch, and Qt application startup.
"""

import sys
import os
import multiprocessing

from config import (
    get_runtime_app_root,
    get_runtime_meipass,
    migrate_legacy_ioc_settings,
    migrate_old_data,
    set_runtime_app_root,
)

# macOS 的 PyInstaller GUI 进程必须在其他重量级导入前强制使用 `spawn`。
# macOS PyInstaller GUI processes must force `spawn` before any heavy imports.
if sys.platform == "darwin":
    multiprocessing.set_start_method("spawn", force=True)

# 冻结环境下提前启用 `freeze_support()`，避免子进程重复拉起完整 GUI。
# Enable `freeze_support()` early so frozen subprocesses do not re-launch the GUI.
multiprocessing.freeze_support()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _should_enable_patch_overlay() -> bool:
    """
    仅允许打包环境启用在线补丁覆盖层。
    Only allow the online patch overlay in packaged environments.
    """
    return bool(getattr(sys, "frozen", False))


def _inject_patch_path():
    """
    注入在线补丁目录并记录真实应用根目录。
    Inject the online patch directory and record the real application root.

    补丁覆盖层会把用户配置目录下的 `code_updates/` 放到 `sys.path` 最前面，
    同时保存真实应用根目录，供被覆盖模块继续定位模型、图标和 exiftool。
    The patch overlay prepends `code_updates/` to `sys.path` and stores the real
    app root so overridden modules can still resolve models, icons, and exiftool.
    """
    if sys.platform == "darwin":
        _patch_dir = os.path.join(
            os.path.expanduser("~"),
            "Library",
            "Application Support",
            "SuperPicky",
            "code_updates",
        )
    elif sys.platform == "win32":
        _patch_dir = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "SuperPicky", "code_updates"
        )
    else:
        _patch_dir = os.path.join(
            os.path.expanduser("~"), ".config", "SuperPicky", "code_updates"
        )
    # 4.3.0 起在线补丁系统已暂停：进入注入环节前先查运行时总闸，
    # 若补丁被停用（或状态不可用）则清理历史残留补丁目录，且不再注入覆盖层；
    # 这样跨版本升级后残留的旧补丁不会在本次启动被 import 覆盖新代码（issue #100）。
    # Online patch overlay is suspended since 4.3.0: clear any residual patch
    # directory and skip sys.path injection so stale patches cannot shadow new code.
    if _should_enable_patch_overlay() and os.path.isdir(_patch_dir):
        try:
            from tools.patch_manager import (
                get_patch_runtime_block_reason,
                safe_clear_patch,
            )

            block_reason = get_patch_runtime_block_reason()
        except Exception:
            # 导入失败时保守处理：不注入覆盖层。
            # Be conservative on import failure: do not inject the overlay.
            block_reason = "补丁系统状态不可用，跳过覆盖层注入"

        if block_reason:
            try:
                safe_clear_patch()
            except Exception:
                pass
        elif _patch_dir not in sys.path:
            sys.path.insert(0, _patch_dir)
    if get_runtime_app_root() is None:
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            set_runtime_app_root(os.path.dirname(os.path.abspath(sys.executable)))
        else:
            meipass = get_runtime_meipass()
            if meipass is not None:
                set_runtime_app_root(meipass)
            else:
                set_runtime_app_root(os.path.dirname(os.path.abspath(__file__)))


_inject_patch_path()


def _run_runtime_bootstrap_if_requested():
    """
    在请求时进入运行时自举流程并立即退出当前主入口。
    Enter the runtime bootstrap flow when requested and exit this main entrypoint.
    """
    if "--runtime-bootstrap" not in sys.argv[1:]:
        return
    from core.runtime_bootstrap import run_runtime_bootstrap

    raise SystemExit(run_runtime_bootstrap(sys.argv[1:]))


_run_runtime_bootstrap_if_requested()

if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream):
        """
        为 Windows 控制台流兜底成 UTF-8 文本输出。
        Ensure a Windows console stream falls back to UTF-8 text output.

        PyInstaller 的无控制台模式可能把 `stdout/stderr` 设为 `None`，
        而普通控制台也可能仍是非 UTF-8 编码，这里统一兜底避免日志写崩。
        PyInstaller windowed mode may set `stdout/stderr` to `None`, and regular
        consoles may still use a non-UTF-8 code page, so normalize both cases here.
        """
        if stream is None:
            return open(os.devnull, "w", encoding="utf-8", errors="replace")

        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            return stream
        except Exception:
            pass

        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace")
            except Exception:
                pass

        return stream

    sys.stdout = _ensure_utf8_stream(sys.stdout)
    sys.stderr = _ensure_utf8_stream(sys.stderr)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from app_user_stat.telemetry import bootstrap_telemetry
from ui.main_window import SuperPickyMainWindow
from ui.styles import APP_TOOLTIP_STYLE
from tools.system_logger import setup_error_logging

# 尽早接管未处理异常，确保源码和冻结包都能留下可诊断日志。
# Install logging early so both source runs and frozen builds preserve diagnostics.
setup_error_logging()

# 启动阶段先完成遗留数据迁移，避免后续模块读到旧路径状态。
# Finish legacy data migration before later modules observe stale paths.
migrate_old_data()
migrate_legacy_ioc_settings()
from advanced_config import get_advanced_config as _get_advanced_config
_get_advanced_config().migrate_birdid_dock_settings()

_memory_monitor = None
if os.environ.get("SP_MEMORY_MONITOR") == "1":
    from tools.memory_monitor import MemoryMonitor

    _memory_monitor = MemoryMonitor(interval=30)

_main_window = None


def main():
    """
    启动 Qt 应用并创建主窗口。
    Start the Qt application and create the main window.
    """
    global _main_window

    # macOS 双击启动 GUI 时 cwd 可能是只读根目录 `/`，需要切回用户目录。
    # macOS GUI launches may start from the read-only `/`, so switch to the home dir.
    if sys.platform == "darwin":
        safe_cwd = os.path.expanduser("~")
        os.chdir(safe_cwd)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    elif not isinstance(app, QApplication):
        raise RuntimeError("检测到非 QApplication 的 Qt 应用实例，无法继续启动 GUI。")

    from constants import APP_VERSION
    from core.build_info import COMMIT_HASH

    commit_hash = COMMIT_HASH
    if commit_hash == "154984fd":
        try:
            import subprocess

            hash_short = (
                subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
                .strip()
                .decode("utf-8")
            )
            commit_hash = hash_short
        except:
            pass

    app.setApplicationName("SuperPicky")
    app.setApplicationDisplayName(f"慧眼选鸟v{APP_VERSION} ({commit_hash})")
    app.setOrganizationName("JamesPhotography")
    app.setOrganizationDomain("jamesphotography.com.au")

    # 主窗口会在托盘与子窗口之间显隐切换，不能依赖“最后一个窗口关闭即退出”。
    # The main window may hide while tray or child windows remain active, so do not
    # couple process lifetime to the last visible top-level window.
    app.setQuitOnLastWindowClosed(False)

    icon_path = os.path.join(os.path.dirname(__file__), "img", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Windows 非整数 DPI 缩放下使用 PassThrough，避免字体和边框被提前取整。
    # Use PassThrough on fractional Windows DPI scales to avoid premature rounding.
    if sys.platform == "win32":
        from PySide6.QtCore import Qt

        app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

    # QToolTip 属于顶层窗口，需要在 QApplication 级别统一覆盖样式。
    # QToolTip is a top-level window, so its style must be applied at QApplication level.
    app.setStyleSheet(APP_TOOLTIP_STYLE)

    if _main_window is None:
        _main_window = SuperPickyMainWindow()
        _main_window.show()
        bootstrap_telemetry(_main_window, on_ready=_main_window.run_startup_prompts)
        app.aboutToQuit.connect(_main_window._cleanup_on_quit)
        if _memory_monitor is not None:
            _memory_monitor.start()
            app.aboutToQuit.connect(_memory_monitor.stop)
    else:
        _main_window.raise_()
        _main_window.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
