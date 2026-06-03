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
import hashlib
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from core.initialization_progress import (
    InitializationProgressEvent,
    PROGRESS_KIND_UV_INSTALL,
    STAGE_PREPARING_RUNTIME,
)

logging.basicConfig(level=logging.INFO)

_UV_BINARY_CACHE: Optional[Path] = None
_UV_HEARTBEAT_SECONDS = 5.0
_UV_MANAGED_PYTHON_ATTEMPTS = 3
_UV_MAX_SYNTHETIC_RATIO = 0.92
_UV_HTTP_RETRIES = "5"
_UV_HTTP_TIMEOUT_SECONDS = "60"
_PYTHON_ENVIRONMENT_VARIABLES_TO_DROP = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "CONDA_PROMPT_MODIFIER",
    "PIP_REQUIRE_VIRTUALENV",
    "__PYVENV_LAUNCHER__",
)
_VIRTUAL_ENV_PATH_MARKERS = frozenset({".venv", "venv", "virtualenv"})
_VIRTUAL_ENV_BIN_DIR_NAMES = frozenset({"scripts", "bin"})


def _appdata_managed_python_root() -> Path:
    """
    返回 Windows 打包态 uv managed Python 的稳定 AppData 根目录。

    返回:
    Path: `%LOCALAPPDATA%/SuperPicky/uv-python`，缺失 LOCALAPPDATA 时回退到
    用户目录下的 AppData/Local。

    Return the stable AppData root for packaged Windows uv managed Python.

    Return:
    Path: `%LOCALAPPDATA%/SuperPicky/uv-python`, falling back to the user's
    AppData/Local directory when LOCALAPPDATA is unavailable.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    base_dir = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base_dir / "SuperPicky" / "uv-python"


def _runtime_managed_python_key(runtime_dir: Path) -> str:
    """
    为运行时目录生成稳定短键，避免不同安装位置共享同一 managed Python。

    参数:
    runtime_dir (Path): 当前运行时目录。

    返回:
    str: 可作为目录名使用的稳定哈希键。

    Generate a stable short key for a runtime directory so different installs do
    not share the same managed Python directory.

    Parameters:
    runtime_dir (Path): The current runtime directory.

    Return:
    str: A stable hash key safe for use as a directory name.
    """
    try:
        runtime_text = os.path.abspath(os.fspath(runtime_dir))
    except OSError:
        runtime_text = os.fspath(runtime_dir)
    normalized = os.path.normcase(os.path.normpath(runtime_text))
    digest = hashlib.sha256(
        normalized.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]
    return f"runtime-{digest}"


def _is_safe_uv_managed_python_dir(uv_managed_python_dir: Path) -> bool:
    """
    判断目录是否属于 SuperPicky 可清理的 uv managed Python 位置。

    参数:
    uv_managed_python_dir (Path): 待检查的 managed Python 目录。

    返回:
    bool: 仅当目录是旧版 `.uv-python` 或新版 AppData 哈希目录时返回 True。

    Check whether a directory belongs to a SuperPicky-managed uv Python location.

    Parameters:
    uv_managed_python_dir (Path): The managed Python directory to inspect.

    Return:
    bool: True for the legacy `.uv-python` path or the new AppData hash path.
    """
    if uv_managed_python_dir.name == ".uv-python":
        return True
    return (
        uv_managed_python_dir.name.startswith("runtime-")
        and uv_managed_python_dir.parent.name == "uv-python"
        and uv_managed_python_dir.parent.parent.name == "SuperPicky"
    )


def runtime_managed_python_dir(runtime_dir: Path) -> Path:
    """
    返回 uv 专用的应用自有 Python 安装目录。

    参数:
    runtime_dir (Path): 当前运行时目录。

    返回:
    Path: 只属于当前应用安装/runtime 的 uv managed Python 目录。

    Return the app-owned Python installation directory used by uv.

    Parameters:
    runtime_dir (Path): The current runtime directory.

    Return:
    Path: The uv managed Python directory scoped to this app runtime only.
    """
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        return _appdata_managed_python_root() / _runtime_managed_python_key(runtime_dir)
    return runtime_dir / ".uv-python"


def is_uv_managed_python_path_error(error_text: str) -> bool:
    """
    判断 uv managed Python 是否因本地路径/装入点状态而失败。

    参数:
    error_text (str): uv 安装命令输出或异常文本。

    返回:
    bool: 如果错误指向 uv managed Python 本地路径不可访问，则返回 True。

    Detect whether uv managed Python failed because the local install path or a
    Windows mount-point state is inaccessible.

    Parameters:
    error_text (str): uv install output or exception text.

    Return:
    bool: True when the failure points to an inaccessible uv managed Python path.
    """
    lowered = (error_text or "").lower()
    managed_python_tokens = (
        "managed installations",
        "managed python",
        ".uv-python",
        "uv-python",
        "uv_python_install_dir",
    )
    return any(token in lowered for token in managed_python_tokens) and (
        is_uv_windows_mount_point_error(lowered)
    )


def is_uv_windows_mount_point_error(error_text: str) -> bool:
    """
    判断 uv 是否遇到 Windows 装入点/junction 遍历失败。

    参数:
    error_text (str): uv 安装命令输出或异常文本。

    返回:
    bool: 当错误指向 ERROR_UNTRUSTED_MOUNT_POINT/os error 448 时返回 True。

    Detect whether uv hit a Windows mount-point or junction traversal failure.

    Parameters:
    error_text (str): uv install output or exception text.

    Return:
    bool: True when the error points to ERROR_UNTRUSTED_MOUNT_POINT/os error 448.
    """
    lowered = (error_text or "").lower()
    context_tokens = (
        "failed to create python minor version link directory",
        "failed to query metadata of file",
        "failed to inspect python interpreter",
        "failed to canonicalize",
        "failed to traverse",
    )
    local_path_tokens = (
        "error_untrusted_mount_point",
        "untrusted mount point",
        "winerror 448",
        "os error 448",
        "无法遍历该路径",
        "不受信任的装入点",
        ".uv-python",
    )
    return any(token in lowered for token in context_tokens) and any(
        token in lowered for token in local_path_tokens
    )


def is_uv_python_interpreter_unavailable(error_text: str) -> bool:
    """
    判断 uv 是否因本机没有可用 Python 解释器而失败。

    参数:
    error_text (str): uv 安装命令输出或异常文本。

    返回:
    bool: 当失败指向解释器缺失，而不是 PyPI/Torch 源失败时返回 True。

    Detect whether uv failed because no usable system Python interpreter is
    available.

    Parameters:
    error_text (str): uv install output or exception text.

    Return:
    bool: True when the failure points to a missing interpreter rather than a
    PyPI/Torch source failure.
    """
    lowered = (error_text or "").lower()
    tokens = (
        "no interpreter found",
        "could not find a python interpreter",
        "could not find python",
        "python interpreter not found",
        "no python executable found",
        "no system python",
        "not found in search path",
    )
    return any(token in lowered for token in tokens)


def repair_uv_managed_python_dir(uv_managed_python_dir: Path) -> None:
    """
    清理应用自有 uv managed Python 目录，让 uv 在下一次运行时重新安装。

    参数:
    uv_managed_python_dir (Path): 由 runtime_managed_python_dir() 返回的目录。

    异常:
    RuntimeError: 当目录无法删除时抛出，调用方应停止源轮换并报告本地路径问题。

    Remove the app-owned uv managed Python directory so uv can reinstall it on
    the next run.

    Parameters:
    uv_managed_python_dir (Path): Directory returned by runtime_managed_python_dir().

    Raises:
    RuntimeError: Raised when the directory cannot be removed; callers should
    stop source retries and report the local path problem.
    """
    if not _is_safe_uv_managed_python_dir(uv_managed_python_dir):
        raise RuntimeError(
            "uv managed Python repair refused to remove unexpected path: "
            f"{uv_managed_python_dir}"
        )
    try:
        shutil.rmtree(uv_managed_python_dir)
    except FileNotFoundError:
        return
    except NotADirectoryError:
        try:
            uv_managed_python_dir.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError(
                f"uv managed Python repair failed: cannot remove "
                f"{uv_managed_python_dir} ({exc})"
            ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"uv managed Python repair failed: cannot remove "
            f"{uv_managed_python_dir} ({exc})"
        ) from exc


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
    use_managed_python: bool = False,
) -> list[str]:
    """
    Build a uv pip install command with one authoritative PyPI index.

    构建只使用一个权威 PyPI 索引的 uv pip install 命令。
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
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
        "-r",
        str(requirements_file),
    ]
    if target_dir is not None:
        cmd.extend(["--target", str(target_dir)])
        cmd.extend(["--python-version", python_version])
        if os.name == "nt":
            cmd.extend(["--python-platform", "windows"])
        if use_managed_python:
            cmd.extend(
                [
                    "--managed-python",
                    "--python",
                    python_version,
                    "--no-python-downloads",
                ]
            )
        else:
            cmd.extend(["--no-managed-python", "--no-python-downloads"])
    elif python_executable is not None:
        cmd.append("--no-python-downloads")
        cmd.extend(["--python", str(python_executable)])
    else:
        raise ValueError("target_dir or python_executable is required")
    if cache_dir is not None:
        cmd.extend(["--cache-dir", str(cache_dir)])
    return cmd


def _find_environment_key(env: dict[str, str], name: str) -> str | None:
    """
    按大小写不敏感方式查找环境变量名。

    参数:
    env (dict[str, str]): 当前环境变量映射。
    name (str): 要查找的环境变量名。

    返回:
    str | None: 实际存在的环境变量名；不存在时返回 None。

    Find an environment-variable key case-insensitively.

    Parameters:
    env (dict[str, str]): Current environment mapping.
    name (str): Environment-variable name to find.

    Return:
    str | None: The actual key if present, otherwise None.
    """
    upper_name = name.upper()
    for key in env:
        if key.upper() == upper_name:
            return key
    return None


def _pop_environment_key(env: dict[str, str], name: str) -> None:
    """
    删除环境变量，并兼容 Windows 环境变量大小写不敏感行为。

    参数:
    env (dict[str, str]): 当前环境变量映射。
    name (str): 要删除的环境变量名。

    Remove an environment variable while respecting Windows case-insensitivity.

    Parameters:
    env (dict[str, str]): Current environment mapping.
    name (str): Environment-variable name to remove.
    """
    key = _find_environment_key(env, name)
    if key is not None:
        env.pop(key, None)


def _normalize_path_text(path_text: str) -> str:
    """
    仅做字符串层面的路径规范化，不访问文件系统。

    参数:
    path_text (str): PATH 中的单个路径片段。

    返回:
    str: 规范化后的路径文本。

    Normalize path text without touching the filesystem.

    Parameters:
    path_text (str): One path entry from PATH.

    Return:
    str: Normalized path text.
    """
    cleaned = (path_text or "").strip().strip('"')
    if not cleaned:
        return ""
    try:
        return os.path.normcase(os.path.normpath(cleaned))
    except (OSError, ValueError):
        return cleaned.lower()


def _path_is_under(candidate_path: str, root_path: str) -> bool:
    """
    判断 candidate 是否位于 root 内部，且不解析符号链接或装入点。

    参数:
    candidate_path (str): 候选路径文本。
    root_path (str): 根路径文本。

    返回:
    bool: candidate 等于 root 或位于 root 内部时返回 True。

    Check whether candidate is inside root without resolving links or mount points.

    Parameters:
    candidate_path (str): Candidate path text.
    root_path (str): Root path text.

    Return:
    bool: True when candidate equals root or is under root.
    """
    candidate = _normalize_path_text(candidate_path)
    root = _normalize_path_text(root_path)
    if not candidate or not root:
        return False
    try:
        return os.path.commonpath([candidate, root]) == root
    except ValueError:
        return False


def _active_python_environment_roots(env: dict[str, str]) -> list[str]:
    """
    收集当前进程继承来的 Python 虚拟环境根目录。

    参数:
    env (dict[str, str]): 当前环境变量映射。

    返回:
    list[str]: 只基于环境变量文本得到的虚拟环境根目录列表。

    Collect inherited Python virtual-environment roots.

    Parameters:
    env (dict[str, str]): Current environment mapping.

    Return:
    list[str]: Virtual-environment roots derived from environment text only.
    """
    roots: list[str] = []
    for key, value in env.items():
        upper_key = key.upper()
        if upper_key == "VIRTUAL_ENV" or upper_key.startswith("CONDA_PREFIX"):
            normalized = _normalize_path_text(value)
            if normalized:
                roots.append(normalized)
    return roots


def _path_parts_lower(path_text: str) -> tuple[str, ...]:
    """
    将路径文本拆分为小写片段，不访问文件系统。

    参数:
    path_text (str): PATH 中的单个路径片段。

    返回:
    tuple[str, ...]: 小写路径片段。

    Split path text into lowercase parts without touching the filesystem.

    Parameters:
    path_text (str): One path entry from PATH.

    Return:
    tuple[str, ...]: Lowercase path parts.
    """
    normalized = _normalize_path_text(path_text)
    if not normalized:
        return ()
    try:
        return tuple(part.lower() for part in Path(normalized).parts if part)
    except (OSError, ValueError):
        return tuple(
            part
            for part in normalized.replace("\\", "/").split("/")
            if part
        )


def _looks_like_python_environment_path(path_entry: str, active_roots: list[str]) -> bool:
    """
    判断 PATH 片段是否来自继承的 venv/conda 环境。

    参数:
    path_entry (str): PATH 中的单个路径片段。
    active_roots (list[str]): 当前进程继承来的虚拟环境根目录。

    返回:
    bool: 如果该路径应从 uv 子进程环境中移除，则返回 True。

    Determine whether a PATH entry belongs to an inherited venv/conda environment.

    Parameters:
    path_entry (str): One path entry from PATH.
    active_roots (list[str]): Inherited virtual-environment roots.

    Return:
    bool: True when the entry should be removed from uv subprocess env.
    """
    normalized = _normalize_path_text(path_entry)
    if not normalized:
        return True
    if any(_path_is_under(normalized, root) for root in active_roots):
        return True

    parts = _path_parts_lower(normalized)
    if not parts:
        return True
    if ".venv" in parts or "virtualenv" in parts:
        return True
    if len(parts) >= 2 and parts[-1] in _VIRTUAL_ENV_BIN_DIR_NAMES:
        return parts[-2] in _VIRTUAL_ENV_PATH_MARKERS
    if len(parts) >= 3 and parts[-1] in _VIRTUAL_ENV_BIN_DIR_NAMES:
        return parts[-3] == "envs"
    return False


def _sanitize_uv_path_environment(env: dict[str, str], active_roots: list[str]) -> str:
    """
    清理 uv 子进程 PATH，避免安装器上下文中的 venv 污染 Python 搜索。

    参数:
    env (dict[str, str]): 将传给 subprocess 的环境变量映射。
    active_roots (list[str]): 当前进程继承来的虚拟环境根目录。

    返回:
    str: 清理后的 PATH 文本，也可用于 UV_PYTHON_SEARCH_PATH。

    Sanitize the uv subprocess PATH so inherited installer venvs cannot pollute
    Python discovery.

    Parameters:
    env (dict[str, str]): Environment mapping for subprocess.
    active_roots (list[str]): Inherited virtual-environment roots.

    Return:
    str: Sanitized PATH text, also suitable for UV_PYTHON_SEARCH_PATH.
    """
    path_key = _find_environment_key(env, "PATH") or "PATH"
    path_value = env.get(path_key, "")
    entries = [entry for entry in path_value.split(os.pathsep) if entry.strip()]
    kept_entries = [
        entry
        for entry in entries
        if not _looks_like_python_environment_path(entry, active_roots)
    ]
    removed_count = len(entries) - len(kept_entries)
    if removed_count:
        logging.info(
            "Removed %d inherited Python environment PATH entries from uv subprocess env",
            removed_count,
        )
    sanitized_path = os.pathsep.join(kept_entries)
    env[path_key] = sanitized_path
    return sanitized_path


def build_uv_install_environment(
    uv_managed_python_dir: Path | None = None,
) -> dict[str, str]:
    """
    构建 uv 安装子进程环境，避免打包应用读取用户全局 uv 配置。

    参数:
    uv_managed_python_dir (Path | None): 当需要 uv managed Python 时，指定应用自有目录。

    返回:
    dict[str, str]: 可传给 subprocess 的环境变量。

    Build the uv install subprocess environment without reading user-global uv state.

    Parameters:
    uv_managed_python_dir (Path | None): App-owned directory for uv managed Python when needed.

    Return:
    dict[str, str]: Environment variables suitable for subprocess.
    """
    env = os.environ.copy()
    active_python_roots = _active_python_environment_roots(env)
    for key in list(env):
        upper_key = key.upper()
        if (
            upper_key in _PYTHON_ENVIRONMENT_VARIABLES_TO_DROP
            or upper_key.startswith("CONDA_PREFIX")
        ):
            env.pop(key, None)
    sanitized_path = _sanitize_uv_path_environment(env, active_python_roots)
    env["UV_PRINT_PIP_LOG"] = "1"
    env["UV_NO_CONFIG"] = "1"
    _pop_environment_key(env, "UV_MANAGED_PYTHON")
    _pop_environment_key(env, "UV_NO_MANAGED_PYTHON")
    _pop_environment_key(env, "UV_PYTHON_PREFERENCE")
    _pop_environment_key(env, "UV_PYTHON_SEARCH_PATH")
    if uv_managed_python_dir is not None:
        uv_managed_python_dir.mkdir(parents=True, exist_ok=True)
        env["UV_PYTHON_DOWNLOADS"] = "automatic"
        env["UV_PYTHON_INSTALL_DIR"] = str(uv_managed_python_dir)
        env["UV_PYTHON_INSTALL_REGISTRY"] = "0"
        env["UV_PYTHON_NO_REGISTRY"] = "1"
        env["UV_HTTP_RETRIES"] = _UV_HTTP_RETRIES
        env["UV_HTTP_TIMEOUT"] = _UV_HTTP_TIMEOUT_SECONDS
    else:
        env["UV_PYTHON_SEARCH_PATH"] = sanitized_path
    return env


def _build_managed_python_install_command(
    uv_path: str,
    uv_managed_python_dir: Path,
    *,
    python_version: str | None = None,
) -> list[str]:
    """
    构建 uv managed Python 的显式预安装命令。

    参数:
    uv_path (str): uv 可执行文件路径。
    uv_managed_python_dir (Path): 应用自有 managed Python 安装目录。
    python_version (str | None): 要安装的 Python 主次版本，默认跟随当前解释器。

    返回:
    list[str]: 可传给 subprocess 的 uv 命令。

    Build the explicit uv managed Python preinstall command.

    Parameters:
    uv_path (str): Path to the uv executable.
    uv_managed_python_dir (Path): App-owned managed Python install directory.
    python_version (str | None): Python major/minor version to install. Defaults
    to the current interpreter version.

    Return:
    list[str]: The uv command suitable for subprocess execution.
    """
    requested_version = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    return [
        uv_path,
        "--color",
        "never",
        "python",
        "install",
        requested_version,
        "--managed-python",
        "--install-dir",
        str(uv_managed_python_dir),
        "--no-bin",
        "--no-registry",
    ]


def ensure_uv_managed_python_ready(
    uv_path: str,
    uv_managed_python_dir: Path,
    *,
    python_version: str | None = None,
    progress_cb=None,
    raise_if_cancelled=None,
    active_process_cb: Callable[[subprocess.Popen[str] | None], None] | None = None,
    cwd: Path | None = None,
    max_attempts: int = _UV_MANAGED_PYTHON_ATTEMPTS,
) -> None:
    """
    预先安装 uv managed Python，并在失败时清理后重试。

    参数:
    uv_path (str): uv 可执行文件路径。
    uv_managed_python_dir (Path): 应用自有 managed Python 安装目录。
    python_version (str | None): 要安装的 Python 主次版本，默认跟随当前解释器。
    progress_cb: 初始化进度回调。
    raise_if_cancelled: 取消检查回调，调用方可在其中抛出自己的中断异常。
    active_process_cb: 子进程注册回调，用于统一退出清理。
    cwd (Path | None): uv 子进程工作目录，避免继承安装器启动上下文。
    max_attempts (int): 最大尝试次数，默认 3 次。

    异常:
    RuntimeError: 多次预安装失败，或本地装入点路径持续不可访问。

    Preinstall uv managed Python and clean/retry when the attempt fails.

    Parameters:
    uv_path (str): Path to the uv executable.
    uv_managed_python_dir (Path): App-owned managed Python install directory.
    python_version (str | None): Python major/minor version to install. Defaults
    to the current interpreter version.
    progress_cb: Initialization progress callback.
    raise_if_cancelled: Cancellation callback that may raise the caller's own
    interruption exception.
    active_process_cb: Subprocess registration callback for unified cleanup.
    cwd (Path | None): uv subprocess working directory, avoiding inherited
    installer launch contexts.
    max_attempts (int): Maximum attempts, defaulting to 3.

    Raises:
    RuntimeError: Raised when preinstall fails repeatedly or the local
    mount-point path remains inaccessible.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    command = _build_managed_python_install_command(
        uv_path,
        uv_managed_python_dir,
        python_version=python_version,
    )
    last_error: Exception | None = None
    for attempt_index in range(1, max_attempts + 1):
        label = f"Prepare uv managed Python ({attempt_index}/{max_attempts})"
        try:
            run_uv_install(
                uv_path,
                command,
                label,
                progress_cb=progress_cb,
                uv_managed_python_dir=uv_managed_python_dir,
                raise_if_cancelled=raise_if_cancelled,
                active_process_cb=active_process_cb,
                cwd=cwd,
            )
            return
        except Exception as exc:
            last_error = exc
            logging.warning(
                "uv managed Python 预安装失败 (%d/%d): %s",
                attempt_index,
                max_attempts,
                exc,
            )
            try:
                repair_uv_managed_python_dir(uv_managed_python_dir)
            except RuntimeError as repair_exc:
                raise RuntimeError(
                    "uv managed Python repair failed during bootstrap; "
                    f"local path may be inaccessible: {uv_managed_python_dir}"
                ) from repair_exc
            if attempt_index < max_attempts:
                continue

    error_text = str(last_error or "")
    if is_uv_managed_python_path_error(error_text) or is_uv_windows_mount_point_error(error_text):
        raise RuntimeError(
            "uv managed Python remains inaccessible after retries; "
            "Windows reported an untrusted mount point. Please launch "
            "SuperPicky from Explorer or Windows Terminal and avoid terminals "
            f"that trigger ERROR_UNTRUSTED_MOUNT_POINT. Path: {uv_managed_python_dir}"
        ) from last_error
    raise RuntimeError(
        f"uv managed Python bootstrap failed after {max_attempts} attempts: "
        f"{last_error}"
    ) from last_error


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


def _terminate_uv_process(process: subprocess.Popen[str]) -> None:
    """
    可靠终止 uv 子进程，避免取消初始化后继续后台下载。

    参数:
    process (subprocess.Popen[str]): 正在运行的 uv 子进程。

    Reliably terminate a uv subprocess so cancelled initialization cannot keep
    downloading in the background.

    Parameters:
    process (subprocess.Popen[str]): The running uv subprocess.
    """
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        finally:
            process.wait(timeout=5)


def run_uv_install(
    uv_path: str,
    install_cmd: list[str],
    label: str,
    *,
    progress_cb=None,
    uv_managed_python_dir: Path | None = None,
    raise_if_cancelled=None,
    active_process_cb: Callable[[subprocess.Popen[str] | None], None] | None = None,
    cwd: Path | None = None,
) -> str:
    """
    Run uv pip install, returning combined stdout+stderr output.

    运行 uv pip install，返回合并的 stdout+stderr 输出。
    """
    if raise_if_cancelled is not None:
        raise_if_cancelled()
    env = build_uv_install_environment(uv_managed_python_dir)
    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if cwd is not None:
        cwd.mkdir(parents=True, exist_ok=True)
        popen_kwargs["cwd"] = str(cwd)
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    logging.info("uv install: %s", " ".join(install_cmd))
    process = subprocess.Popen(install_cmd, **popen_kwargs)
    if active_process_cb is not None:
        active_process_cb(process)
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
                if raise_if_cancelled is not None:
                    raise_if_cancelled()
            except BaseException:
                _terminate_uv_process(process)
                raise
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
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        if return_code != 0:
            tail = "\n".join(output_lines[-15:])
            raise RuntimeError(
                f"{label} 失败 (exit={return_code})" + (f"\n{tail}" if tail else "")
            )
    finally:
        _terminate_uv_process(process)
        output_reader.join(timeout=1.0)
        if active_process_cb is not None:
            active_process_cb(None)

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
