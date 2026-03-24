#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - PySide6 版本入口点
Version: 4.0.6 - Country Selection Simplification
"""

import sys
import os

# V3.9.3: 修复 macOS PyInstaller 打包后的多进程问题
# 必须在所有其他导入之前设置
import multiprocessing
if sys.platform == 'darwin':
    multiprocessing.set_start_method('spawn', force=True)

# V3.9.4: 防止 PyInstaller 打包后 spawn 模式创建重复进程/窗口
# 这是 macOS PyInstaller 的标准做法
multiprocessing.freeze_support()

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fix Windows console encoding: default cp1252 cannot render emoji/CJK characters,
# causing UnicodeEncodeError crashes on print(). Reconfigure to UTF-8 with replacement
# fallback so all log output survives regardless of the console codepage.
if sys.platform == "win32":
    import io

    def _ensure_utf8_stream(stream):
        # PyInstaller windowed mode (`console=False`) may set stdout/stderr to None.
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

# V3.9.3: 全局窗口引用，防止重复创建
_main_window = None


def main():
    """主函数"""
    global _main_window

    # Fix: macOS GUI launch (double-click / Dock) sets CWD to read-only '/'.
    # YOLO attempts to create a 'runs/' dir relative to CWD, which fails with
    # [Errno 30] Read-only file system on Intel Macs (CPU inference path).
    # Switch to the user home dir so any YOLO cache writes succeed.
    if sys.platform == 'darwin':
        safe_cwd = os.path.expanduser('~')
        os.chdir(safe_cwd)
    
    # V3.9.3: 检查是否已有 QApplication 实例
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    else:
        print("⚠️  检测到已存在的 QApplication 实例")
    
    # 设置应用属性
    # V4.0.5: 动态设置应用名称
    from constants import APP_VERSION
    from core.build_info import COMMIT_HASH
    
    commit_hash = COMMIT_HASH
    if commit_hash == "154984fd": # 默认占位符
         try:
             import subprocess
             hash_short = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).strip().decode('utf-8')
             commit_hash = hash_short
         except:
             pass

    app.setApplicationName("SuperPicky")
    app.setApplicationDisplayName(f"慧眼选鸟v{APP_VERSION} ({commit_hash})")
    app.setOrganizationName("JamesPhotography")
    app.setOrganizationDomain("jamesphotography.com.au")

    # 防止隐藏主窗口（切到结果浏览器时）触发 Qt 自动退出
    # 退出由托盘菜单"退出"或 _quit_app() 显式控制，统一走 aboutToQuit 清理
    app.setQuitOnLastWindowClosed(False)
    
    # 设置应用图标
    icon_path = os.path.join(os.path.dirname(__file__), "img", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    # V4.1: Windows 高 DPI 缩放策略
    # Qt6/PySide6 已默认启用 HiDPI，但 RoundingPolicy 默认为 RoundPreferFloor，
    # 在 Windows 125%/150% 等非整数缩放下会导致文字/边框轻微模糊。
    # PassThrough 允许使用精确的小数缩放因子，避免像素取整问题。
    if sys.platform == "win32":
        from PySide6.QtCore import Qt
        app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    
    # V4.1: 在 QApplication 级别设置 QToolTip 样式
    # macOS 上 QToolTip 是顶层窗口，不继承 QMainWindow 的样式，
    # 在系统浅色模式下会被系统接管为毛玻璃浅色背景 → 文字不可见
    app.setStyleSheet(APP_TOOLTIP_STYLE)
    
    # V3.9.3: 防止重复创建窗口
    if _main_window is None:
        _main_window = SuperPickyMainWindow()
        _main_window.show()
        bootstrap_telemetry(_main_window, on_ready=_main_window.run_startup_prompts)
        # 统一退出清理：无论通过 X / 托盘 / Cmd+Q 退出，都会经由 aboutToQuit 信号
        app.aboutToQuit.connect(_main_window._cleanup_on_quit)
    else:
        print("⚠️  检测到已存在的主窗口实例")
        _main_window.raise_()
        _main_window.activateWindow()
    
    # 运行事件循环
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
