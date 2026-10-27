#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 系统信息收集与启动日志
在 App 启动时记录一次系统环境，写入 SuperPicky 配置目录的 startup.log
同时设置错误日志，捕获未处理异常并写入 superpicky_errors.log
"""

import platform
import sys
import os
import logging
import traceback
from datetime import datetime
from pathlib import Path


def _get_config_dir() -> Path:
    """返回 SuperPicky 配置目录（与 advanced_config 保持一致）"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SuperPicky"
    elif sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "SuperPicky"
    else:
        return Path.home() / ".config" / "SuperPicky"


def collect_system_info() -> dict:
    """收集系统环境信息，返回字典"""
    info = {}

    # ── App 启动位置 ───────────────────────────────────────
    info['launch_path'] = os.path.abspath(sys.argv[0]) if sys.argv else 'unknown'
    info['launch_cwd'] = os.getcwd()
    # 判断是 PyInstaller 打包版还是 Python 源码运行
    info['launch_mode'] = 'packaged (PyInstaller)' if getattr(sys, 'frozen', False) else 'source (python)'

    # ── SuperPicky 应用配置 ────────────────────────────────
    try:
        from advanced_config import get_advanced_config
        adv = get_advanced_config()
        _lang = adv.language or 'Auto'
        # 友好名称
        _lang_display = {
            'zh_CN': '中文 (zh_CN)',
            'en_US': 'English (en_US)',
            'Auto': 'Auto (系统语言)',
        }.get(_lang, _lang)
        info['language'] = _lang_display
        info['skill_level'] = adv.skill_level or 'unknown'
        info['config_file'] = adv.config_file
    except Exception as e:
        info['language'] = f'unknown ({e})'
        info['skill_level'] = 'unknown'
        info['config_file'] = 'unknown'

    # ── App 版本 ───────────────────────────────────────────
    try:
        from constants import APP_VERSION
        info['app_version'] = APP_VERSION
    except Exception:
        info['app_version'] = 'unknown'

    # ── 操作系统 ──────────────────────────────────────────
    info['os'] = platform.system()           # Darwin / Windows / Linux
    info['os_release'] = platform.release()  # 14.x / 11 / 22.04 等
    info['os_version'] = platform.version()  # 构建详情
    info['machine'] = platform.machine()     # arm64 / x86_64 / AMD64

    if sys.platform == "darwin":
        try:
            import subprocess
            r = subprocess.run(
                ['sw_vers', '-productVersion'],
                capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0:
                info['macos_version'] = r.stdout.strip()
        except Exception:
            pass

    # ── Python ────────────────────────────────────────────
    info['python_version'] = sys.version.split()[0]
    info['python_executable'] = sys.executable

    # ── 内存 ──────────────────────────────────────────────
    try:
        import psutil
        vm = psutil.virtual_memory()
        info['ram_total_gb'] = f"{vm.total / (1024**3):.1f}"
        info['ram_available_gb'] = f"{vm.available / (1024**3):.1f}"
    except ImportError:
        info['ram_total_gb'] = 'n/a (psutil missing)'
        info['ram_available_gb'] = 'n/a'

    # ── AI 推理设备 / GPU ──────────────────────────────────
    info['ai_device'] = 'CPU'
    try:
        import torch
        if torch.backends.mps.is_available():
            info['ai_device'] = 'MPS (Apple Silicon)'
            try:
                import subprocess, json
                r = subprocess.run(
                    ['system_profiler', 'SPDisplaysDataType', '-json'],
                    capture_output=True, text=True, timeout=5
                )
                sp = json.loads(r.stdout)
                gpus = sp.get('SPDisplaysDataType', [])
                if gpus:
                    info['gpu_name'] = gpus[0].get('sppci_model', 'Apple GPU')
            except Exception:
                info['gpu_name'] = 'Apple GPU'
        elif torch.cuda.is_available():
            # RTX 50 配 cu118 等 torch 未编进该架构的组合会被 get_best_device 改用 CPU
            # Combos such as RTX 50 + cu118 are routed to CPU by get_best_device
            from config import cuda_unsupported_reason
            gpu_warning = cuda_unsupported_reason()
            info['ai_device'] = 'CPU (GPU unsupported)' if gpu_warning else 'CUDA'
            if gpu_warning:
                info['gpu_warning'] = gpu_warning
            info['gpu_name'] = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            info['gpu_vram_gb'] = f"{vram:.1f}"
            info['cuda_version'] = str(torch.version.cuda)
        # else: CPU，保持默认
    except ImportError:
        info['ai_device'] = 'CPU (torch not loaded)'

    return info


def format_system_info(info: dict) -> str:
    """格式化系统信息为可读文本块"""
    lines = [
        "=" * 60,
        f"  SuperPicky {info.get('app_version', '?')}  —  Startup System Info",
        f"  Recorded : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        "",
        "[App]",
        f"  Version     : {info.get('app_version', '?')}",
        f"  Language    : {info.get('language', '?')}",
        f"  Skill Level : {info.get('skill_level', '?')}",
        f"  Launch Mode : {info.get('launch_mode', '?')}",
        f"  Launch Path : {info.get('launch_path', '?')}",
        f"  Working Dir : {info.get('launch_cwd', '?')}",
        f"  Config File : {info.get('config_file', '?')}",
        "",
        "[OS]",
        f"  System      : {info.get('os', '?')} {info.get('os_release', '')}",
    ]
    if 'macos_version' in info:
        lines.append(f"  macOS       : {info['macos_version']}")
    lines.extend([
        f"  Machine     : {info.get('machine', '?')}",
        f"  OS Detail   : {info.get('os_version', '?')}",
        "",
        "[Python]",
        f"  Version     : {info.get('python_version', '?')}",
        f"  Executable  : {info.get('python_executable', '?')}",
        "",
        "[Hardware]",
        f"  RAM Total   : {info.get('ram_total_gb', '?')} GB",
        f"  RAM Free    : {info.get('ram_available_gb', '?')} GB",
        f"  AI Device   : {info.get('ai_device', '?')}",
    ])
    if 'gpu_name' in info:
        lines.append(f"  GPU         : {info['gpu_name']}")
    if 'gpu_vram_gb' in info:
        lines.append(f"  VRAM        : {info['gpu_vram_gb']} GB")
    if 'cuda_version' in info:
        lines.append(f"  CUDA        : {info['cuda_version']}")
    lines.extend(["", "=" * 60])
    return "\n".join(lines)


def write_startup_log() -> str:
    """
    在 App 启动时写一次系统信息日志。
    写入 <SuperPicky 配置目录>/startup.log，覆盖上一次记录。
    返回日志文件路径（失败时返回 None）。
    """
    try:
        config_dir = _get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        log_path = config_dir / "startup.log"
        info = collect_system_info()
        text = format_system_info(info)

        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(text + "\n")

        return str(log_path)
    except Exception as e:
        print(f"[system_logger] Failed to write startup log: {e}")
        return None


def _write_error_to_log(message: str):
    """
    将错误信息写入 superpicky.log（优先写当前活跃工作目录，fallback 到 config dir）。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [ERROR] {message}\n"

    # 优先写入当前活跃工作目录的 superpicky.log
    written = False
    try:
        from tools.utils import get_active_log_directory
        active_dir = get_active_log_directory()
        if active_dir and os.path.isdir(active_dir):
            log_path = os.path.join(active_dir, "superpicky.log")
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(entry)
            written = True
    except Exception:
        pass

    # Fallback：写入 config dir 的 superpicky_errors.log
    if not written:
        try:
            config_dir = _get_config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            fallback_path = config_dir / "superpicky_errors.log"
            with open(str(fallback_path), 'a', encoding='utf-8') as f:
                f.write(entry)
        except Exception:
            pass


def setup_error_logging():
    """
    设置错误日志系统：
    - 捕获所有未处理的 Python 异常（sys.excepthook）写入 superpicky.log
    - 捕获子线程未处理异常（threading.excepthook）
    - 每条错误前附带完整 traceback
    """
    try:
        # ── 捕获主线程未处理异常 ─────────────────────────────
        _original_excepthook = sys.excepthook

        def _excepthook(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                _original_excepthook(exc_type, exc_value, exc_tb)
                return
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            _write_error_to_log(f"Unhandled exception:\n{tb_text}")
            _original_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = _excepthook

        # ── 捕获子线程未处理异常（Python 3.8+）──────────────
        import threading
        _original_thread_excepthook = threading.excepthook

        def _thread_excepthook(args):
            if args.exc_type is None or issubclass(args.exc_type, KeyboardInterrupt):
                _original_thread_excepthook(args)
                return
            tb_text = "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            ))
            thread_name = getattr(args.thread, 'name', 'unknown-thread')
            _write_error_to_log(
                f"Unhandled exception in thread [{thread_name}]:\n{tb_text}"
            )
            _original_thread_excepthook(args)

        threading.excepthook = _thread_excepthook

    except Exception as e:
        print(f"[system_logger] Failed to setup error logging: {e}")
