# -*- coding: utf-8 -*-
"""
uv-based runtime environment manager.

The packaged application must carry a uv executable; initialization must not
download the package manager before it can download packages. Development runs
may still use uv from PATH.

基于 uv 的运行时环境管理器。

打包应用必须携带 uv 可执行文件；初始化流程不能先联网下载包管理器再下载依赖。
开发环境仍可使用 PATH 中的 uv。
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from core.initialization_progress import (
    InitializationProgressEvent,
    PROGRESS_KIND_UV_INSTALL,
    STAGE_PREPARING_RUNTIME,
)

logging.basicConfig(level=logging.INFO)

_UV_BINARY_CACHE: Optional[Path] = None
_UV_HEARTBEAT_SECONDS = 5.0
_UV_MAX_SYNTHETIC_RATIO = 0.92


def _uv_binary_name() -> str:
    return "uv.exe" if os.name == "nt" else "uv"


def _resolve_uv_path() -> Optional[str]:
    """
    Find uv on PATH. Lazy-caches the result so repeated calls are cheap.

    在 PATH 中查找 uv。惰性缓存结果以避免重复查询。
    """
    global _UV_BINARY_CACHE
    if _UV_BINARY_CACHE is not None:
        return str(_UV_BINARY_CACHE) if _UV_BINARY_CACHE else None
    import shutil
    found = shutil.which("uv")
    if found:
        _UV_BINARY_CACHE = Path(found)
        logging.info("uv 已找到: %s", found)
    else:
        _UV_BINARY_CACHE = ""
        logging.info("uv 未在 PATH 中找到")
    return str(_UV_BINARY_CACHE) if isinstance(_UV_BINARY_CACHE, Path) else None


def _bundled_uv_candidates(runtime_dir: Path) -> list[Path]:
    """
    Return possible bundled uv binary locations.

    返回可能的内置 uv 二进制位置。
    """
    binary_name = _uv_binary_name()
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        candidates.append(Path(meipass) / "uv" / binary_name)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                exe_dir / "_internal" / "uv" / binary_name,
                exe_dir / "uv" / binary_name,
            ]
        )
    project_root = Path(__file__).resolve().parent.parent
    candidates.extend(
        [
            runtime_dir / ".uv" / binary_name,
            project_root / "uv" / binary_name,
            project_root / "vendor" / "uv" / binary_name,
        ]
    )
    return candidates


def _validate_uv_binary(candidate: Path) -> None:
    """
    校验 uv 二进制可直接执行，提前发现被错误打包的 shim。

    Validate that the uv binary is directly executable, catching incorrectly
    packaged shims before runtime source retries begin.

    参数:
    candidate (Path): 待校验的 uv 二进制路径。

    Parameters:
    candidate (Path): The uv binary path to validate.

    异常:
    RuntimeError: 当 uv 无法执行或版本命令失败时抛出。

    Raises:
    RuntimeError: Raised when uv cannot execute or its version command fails.
    """
    try:
        result = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:
        raise RuntimeError(f"uv executable is not runnable: {candidate} ({exc})") from exc
    if result.returncode != 0:
        output = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        raise RuntimeError(
            f"uv executable self-check failed: {candidate}"
            + (f"\n{output}" if output else "")
        )


def ensure_uv_bootstrapped(runtime_dir: Path) -> str:
    """
    Ensure a uv binary is available and return its path.

    Priority:
      1. Bundled uv from the frozen app or project vendor folder.
      2. System uv on PATH for development.

    确保 uv 二进制文件可用并返回其路径。

    优先级：
      1. 冻结包或项目 vendor 目录中的内置 uv
      2. 开发环境 PATH 中的 uv
    """
    for candidate in _bundled_uv_candidates(runtime_dir):
        if candidate.exists():
            try:
                candidate.chmod(0o755)
            except OSError as exc:
                logging.debug("无法调整 uv 二进制权限，继续尝试执行: %s (%s)", candidate, exc)
            _validate_uv_binary(candidate)
            logging.info("使用内置 uv 二进制文件: %s", candidate)
            return str(candidate)
    system_uv = _resolve_uv_path()
    if system_uv and not getattr(sys, "frozen", False):
        _validate_uv_binary(Path(system_uv))
        return system_uv
    raise RuntimeError(
        "uv executable not found. The Lite package must bundle uv under "
        "_internal/uv/, or development runs must have uv on PATH."
    )


def create_venv(
    uv_path: str,
    runtime_dir: Path,
    python_cmd: Optional[list[str]] = None,
) -> None:
    """
    Create a virtual environment using uv.

    使用 uv 创建虚拟环境。
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    cmd = [uv_path, "venv", str(runtime_dir)]
    if python_cmd:
        python_str = python_cmd[0] if python_cmd else sys.executable
        cmd.extend(["--python", python_str])
    else:
        cmd.extend(["--python", sys.executable])

    logging.info("uv venv: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        stderr = result.stderr.strip()[-500:] if result.stderr else "(no stderr)"
        raise RuntimeError(f"uv venv 创建失败 (exit={result.returncode}): {stderr}")


def build_install_command(
    uv_path: str,
    requirements_file: Path,
    *,
    index_url: str,
    target_dir: Path | None = None,
    python_executable: Path | None = None,
    cache_dir: Path | None = None,
) -> list[str]:
    """
    Build a uv pip install command with one authoritative PyPI index.

    构建只使用一个权威 PyPI 索引的 uv pip install 命令。
    """
    cmd = [
        uv_path,
        "--color",
        "never",
        "pip",
        "install",
        "--index-strategy",
        "first-index",
        "--default-index",
        index_url,
        "--only-binary",
        ":all:",
        "--link-mode",
        "copy",
        "--compile-bytecode",
        "--no-python-downloads",
        "-r",
        str(requirements_file),
    ]
    if target_dir is not None:
        cmd.extend(["--target", str(target_dir)])
    elif python_executable is not None:
        cmd.extend(["--python", str(python_executable)])
    else:
        raise ValueError("target_dir or python_executable is required")
    if cache_dir is not None:
        cmd.extend(["--cache-dir", str(cache_dir)])
    return cmd


def _synthetic_uv_ratio(event_count: int) -> float:
    """
    Return a conservative monotonic progress ratio for uv output events.

    为 uv 输出事件返回保守且单调递增的进度比例。
    """
    return min(_UV_MAX_SYNTHETIC_RATIO, 0.06 + (max(0, event_count) * 0.018))


def _read_uv_output_stream(process: subprocess.Popen[str], output_queue: queue.Queue[str | None]) -> None:
    """
    Split uv stdout on both newline and carriage-return progress refreshes.

    同时按换行与回车刷新切分 uv stdout 进度输出。
    """
    assert process.stdout is not None
    buffer: list[str] = []
    try:
        while True:
            chunk = process.stdout.read(1)
            if chunk == "":
                break
            if chunk in ("\r", "\n"):
                text = "".join(buffer).strip()
                buffer.clear()
                if text:
                    output_queue.put(text)
                continue
            buffer.append(chunk)
        text = "".join(buffer).strip()
        if text:
            output_queue.put(text)
    finally:
        output_queue.put(None)


def run_uv_install(
    uv_path: str,
    install_cmd: list[str],
    label: str,
    *,
    progress_cb=None,
) -> str:
    """
    Run uv pip install, returning combined stdout+stderr output.

    运行 uv pip install，返回合并的 stdout+stderr 输出。
    """
    env = os.environ.copy()
    env["UV_PRINT_PIP_LOG"] = "1"
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    logging.info("uv install: %s", " ".join(install_cmd))
    process = subprocess.Popen(install_cmd, **popen_kwargs)
    output_lines: list[str] = []
    output_queue: queue.Queue[str | None] = queue.Queue()
    output_reader = threading.Thread(
        target=_read_uv_output_stream,
        args=(process, output_queue),
        name="sp-uv-output-reader",
        daemon=True,
    )
    output_reader.start()
    event_count = 0
    started_at = time.monotonic()
    next_heartbeat_at = started_at + _UV_HEARTBEAT_SECONDS
    if progress_cb:
        progress_cb(
            InitializationProgressEvent(
                stage=STAGE_PREPARING_RUNTIME,
                progress_kind=PROGRESS_KIND_UV_INSTALL,
                message=f"{label}: started",
                ratio=_synthetic_uv_ratio(event_count),
            )
        )

    try:
        output_finished = False
        while not output_finished:
            try:
                text = output_queue.get(timeout=0.25)
            except queue.Empty:
                if progress_cb and process.poll() is None:
                    now = time.monotonic()
                    if now >= next_heartbeat_at:
                        event_count += 1
                        elapsed = int(now - started_at)
                        progress_cb(
                            InitializationProgressEvent(
                                stage=STAGE_PREPARING_RUNTIME,
                                progress_kind=PROGRESS_KIND_UV_INSTALL,
                                message=f"{label}: still working, downloading or installing packages ({elapsed}s)",
                                ratio=_synthetic_uv_ratio(event_count),
                            )
                        )
                        next_heartbeat_at = now + _UV_HEARTBEAT_SECONDS
                continue
            if text is None:
                output_finished = True
                continue
            output_lines.append(text)
            event_count += 1
            if progress_cb:
                progress_cb(
                    InitializationProgressEvent(
                        stage=STAGE_PREPARING_RUNTIME,
                        progress_kind=PROGRESS_KIND_UV_INSTALL,
                        message=f"{label}: {text}",
                        ratio=_synthetic_uv_ratio(event_count),
                    )
                )
        return_code = process.wait()
        output_reader.join(timeout=1.0)
        if return_code != 0:
            tail = "\n".join(output_lines[-15:])
            raise RuntimeError(
                f"{label} 失败 (exit={return_code})" + (f"\n{tail}" if tail else "")
            )
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()

    if progress_cb:
        progress_cb(
            InitializationProgressEvent(
                stage=STAGE_PREPARING_RUNTIME,
                progress_kind=PROGRESS_KIND_UV_INSTALL,
                message=f"{label} completed",
                ratio=1.0,
                is_terminal=True,
            )
        )

    return "\n".join(output_lines)
