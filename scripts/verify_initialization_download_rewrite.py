# -*- coding: utf-8 -*-
"""
Offline regression checks for the initialization download rewrite.

初始化下载链路重写的离线回归检查。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.initialization_progress import (
    InitializationProgressEvent,
    InitializationProgressModel,
    PROGRESS_KIND_DOWNLOAD,
    STAGE_DOWNLOADING,
)
from core.runtime_requirements import get_runtime_requirements
from core.source_registry import get_official_pypi_url, get_pypi_sources, get_torch_sources
from core.uv_runtime_manager import (
    build_install_command,
    build_uv_install_environment,
    ensure_uv_managed_python_ready,
    is_uv_managed_python_path_error,
    is_uv_windows_mount_point_error,
    is_uv_python_interpreter_unavailable,
    repair_uv_managed_python_dir,
    run_uv_install,
    runtime_managed_python_dir,
)
from scripts.download_models import _parse_hf_cli_size, verify_resource


class VerificationSkipped(RuntimeError):
    """One optional offline verification cannot run in the current environment."""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_source_registry_isolation() -> None:
    """
    Verify PyPI and Torch source pools stay separate.

    校验 PyPI 与 Torch 源池保持隔离。
    """
    pypi_sources = get_pypi_sources(include_overrides=False)
    torch_sources = get_torch_sources("cuda", include_overrides=False)
    pypi_urls = [source.url for source in pypi_sources]
    torch_urls = [source.url for source in torch_sources]

    _assert(get_official_pypi_url() in pypi_urls, "official PyPI source missing")
    _assert(
        all("pytorch" not in url.lower() for url in pypi_urls),
        "Torch source leaked into PyPI pool",
    )
    _assert(
        any("download.pytorch.org" in url for url in torch_urls),
        "official Torch CUDA source missing",
    )
    _assert(
        all("pypi.org/simple" not in url.lower() for url in torch_urls),
        "PyPI source leaked into Torch pool",
    )


def verify_torch_direct_wheel_urls() -> None:
    """
    Verify Torch CUDA wheels are represented as direct URL requirements.

    校验 Torch CUDA wheel 以直链 requirement 表示。
    """
    requirements = get_runtime_requirements("cuda")
    package_urls = requirements.torch_wheel_urls("https://download.pytorch.org/whl/cu118")
    torch_url = package_urls["torch"]
    _assert(torch_url.startswith("torch @ https://"), "torch is not a direct URL")
    _assert("%2Bcu118" in torch_url, "CUDA local version was not URL-encoded")
    _assert("win_amd64.whl" in torch_url, "Windows wheel platform tag missing")


def verify_uv_command_uses_single_pypi_index() -> None:
    """
    Verify uv install command does not mix Torch mirrors into PyPI indexes.

    校验 uv 安装命令不会把 Torch 镜像混入 PyPI 索引。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_verify_") as tmp:
        tmp_path = Path(tmp)
        requirements_file = tmp_path / "requirements.txt"
        requirements_file.write_text("timm>=0.9.0\n", encoding="utf-8")
        command = build_install_command(
            uv_path="uv",
            requirements_file=requirements_file,
            index_url=get_official_pypi_url(),
            target_dir=tmp_path / "target",
            cache_dir=tmp_path / "cache",
        )
    _assert("--default-index" in command, "uv command must use --default-index")
    _assert("--index-strategy" in command, "uv command must set index strategy")
    _assert("first-index" in command, "uv command must use first-index")
    _assert("--extra-index-url" not in command, "uv command must not use extra-index-url")
    _assert(
        not any("pytorch" in part.lower() for part in command),
        "Torch mirror leaked into uv index arguments",
    )


def verify_packaged_target_uv_avoids_managed_python_by_default() -> None:
    """
    Verify packaged target installs avoid uv managed Python by default.

    校验打包态 target 安装默认避开 uv managed Python。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_target_verify_") as tmp:
        tmp_path = Path(tmp)
        requirements_file = tmp_path / "requirements.txt"
        runtime_dir = tmp_path / "user_selected_runtime_env"
        local_appdata = tmp_path / "local_appdata"
        target_dir = runtime_dir / "site-packages.__installing__"
        requirements_file.write_text("timm>=0.9.0\n", encoding="utf-8")
        with patch.object(sys, "platform", "win32"), patch.object(
            sys, "frozen", True, create=True
        ), patch.dict(
            "os.environ",
            {
                "LOCALAPPDATA": str(local_appdata),
                "UV_MANAGED_PYTHON": "1",
                "UV_NO_MANAGED_PYTHON": "1",
                "UV_PYTHON_PREFERENCE": "system",
            },
        ):
            managed_python_dir = runtime_managed_python_dir(runtime_dir)
            command = build_install_command(
                uv_path="uv",
                requirements_file=requirements_file,
                index_url=get_official_pypi_url(),
                target_dir=target_dir,
                cache_dir=runtime_dir / "uv-cache",
                use_managed_python=False,
            )
            direct_env = build_uv_install_environment(None)
            managed_env = build_uv_install_environment(managed_python_dir)

    _assert("--managed-python" not in command, "packaged target install must avoid managed Python by default")
    _assert("--no-managed-python" in command, "packaged target install must disable managed Python")
    _assert(
        str(target_dir) in command,
        "packaged target install must keep the user-selected runtime target",
    )
    _assert(
        "--python-version" in command,
        "packaged target install must pin target Python version",
    )
    if sys.platform == "win32":
        _assert(
            "--python-platform" in command and "windows" in command,
            "Windows target install must pin the target platform",
        )
    _assert(
        "--no-python-downloads" in command,
        "packaged target install must not download Python during dependency install",
    )
    _assert(direct_env["UV_NO_CONFIG"] == "1", "uv must ignore user-global config")
    _assert(
        "UV_MANAGED_PYTHON" not in direct_env,
        "direct target install must ignore user managed-Python env",
    )
    _assert(
        "UV_PYTHON_PREFERENCE" not in direct_env,
        "direct target install must ignore user Python preference env",
    )
    _assert(
        managed_env["UV_PYTHON_INSTALL_DIR"] == str(managed_python_dir),
        "uv managed Python dir must be passed through the subprocess environment",
    )
    _assert(
        managed_python_dir.parent == local_appdata / "SuperPicky" / "uv-python",
        "packaged uv managed Python dir must live under AppData",
    )
    _assert(
        managed_python_dir.name.startswith("runtime-"),
        "packaged uv managed Python dir must be runtime-hash scoped",
    )
    _assert(
        managed_python_dir != runtime_dir / ".uv-python",
        "packaged uv managed Python dir must not live under the runtime dir",
    )
    _assert(
        managed_env["UV_PYTHON_DOWNLOADS"] == "automatic",
        "uv managed fallback must be allowed to bootstrap the app-scoped Python",
    )
    _assert(managed_env["UV_HTTP_RETRIES"] == "5", "uv managed Python download retries missing")
    _assert(managed_env["UV_HTTP_TIMEOUT"] == "60", "uv managed Python timeout missing")
    _assert(
        "UV_PYTHON_PREFERENCE" not in managed_env,
        "uv --managed-python must not conflict with UV_PYTHON_PREFERENCE",
    )
    _assert(
        "UV_NO_MANAGED_PYTHON" not in managed_env,
        "uv --managed-python must not conflict with UV_NO_MANAGED_PYTHON",
    )


def verify_uv_environment_sanitizes_installer_python_context() -> None:
    """
    Verify uv subprocess env ignores installer-inherited virtual environments.

    校验 uv 子进程环境会忽略安装器上下文继承来的虚拟环境。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_env_verify_") as tmp:
        tmp_path = Path(tmp)
        dev_venv = tmp_path / "repo" / ".venv"
        conda_env = tmp_path / "miniconda" / "envs" / "superpicky"
        standalone_python = tmp_path / "Python312"
        tools_dir = tmp_path / "tools"
        inherited_path_entries = [
            str(dev_venv / "Scripts"),
            str(conda_env / "Scripts"),
            str(standalone_python),
            str(tools_dir),
        ]
        with patch.dict(
            "os.environ",
            {
                "Path": os.pathsep.join(inherited_path_entries),
                "VIRTUAL_ENV": str(dev_venv),
                "CONDA_PREFIX": str(conda_env),
                "CONDA_PREFIX_1": str(tmp_path / "base_conda"),
                "PYTHONHOME": str(tmp_path / "python_home"),
                "PYTHONPATH": str(tmp_path / "python_path"),
                "UV_PYTHON_SEARCH_PATH": str(dev_venv / "Scripts"),
            },
            clear=True,
        ):
            direct_env = build_uv_install_environment(None)
            managed_env = build_uv_install_environment(tmp_path / "uv-python")

    sanitized_entries = [
        str(standalone_python),
        str(tools_dir),
    ]
    expected_path = os.pathsep.join(sanitized_entries)
    direct_path_key = "Path" if "Path" in direct_env else "PATH"
    managed_path_key = "Path" if "Path" in managed_env else "PATH"
    _assert(
        direct_env[direct_path_key] == expected_path,
        "direct uv env did not remove inherited venv/conda PATH entries",
    )
    _assert(
        direct_env["UV_PYTHON_SEARCH_PATH"] == expected_path,
        "direct uv env must constrain uv Python discovery to the sanitized PATH",
    )
    for key in (
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_PREFIX_1",
        "PYTHONHOME",
        "PYTHONPATH",
    ):
        _assert(key not in direct_env, f"direct uv env leaked {key}")
        _assert(key not in managed_env, f"managed uv env leaked {key}")
    _assert(
        str(dev_venv / "Scripts") not in managed_env[managed_path_key],
        "managed uv env leaked inherited venv PATH",
    )
    _assert(
        "UV_PYTHON_SEARCH_PATH" not in managed_env,
        "managed uv env should let uv use the managed Python install dir",
    )


def verify_uv_managed_python_path_repair_helpers() -> None:
    """
    Verify local uv managed Python path errors are detected and repairable.

    校验 uv managed Python 本地路径错误可识别，且可清理应用自有目录。
    """
    error_text = (
        "error: Failed to inspect Python interpreter from managed installations at "
        "`_internal\\runtime_env\\.uv-python\\cpython-3.13-windows-x86_64-none\\python.exe`\n"
        "Caused by: Failed to query Python interpreter\n"
        "Caused by: failed to query metadata of file "
        "`C:\\Users\\demo\\AppData\\Local\\Programs\\SuperPicky\\_internal\\runtime_env\\"
        ".uv-python\\cpython-3.13-windows-x86_64-none\\python.exe`: "
        "无法遍历该路径，因为它包含不受信任的装入点。 (os error 448)"
    )
    _assert(
        is_uv_managed_python_path_error(error_text),
        "uv managed Python mount-point failure was not detected",
    )
    minor_link_error = (
        "error: Failed to create Python minor version link directory\n"
        "Caused by: 无法遍历该路径，因为它包含不受信任的装入点。 "
        "(os error 448)"
    )
    _assert(
        is_uv_windows_mount_point_error(minor_link_error),
        "uv Windows mount-point link failure was not detected",
    )
    interpreter_error = (
        "error: No interpreter found for Python 3.12 in search path\n"
        "hint: Use `uv python install 3.12` to install a managed interpreter."
    )
    _assert(
        is_uv_python_interpreter_unavailable(interpreter_error),
        "missing system Python interpreter failure was not detected",
    )
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_python_repair_") as tmp:
        runtime_dir = Path(tmp) / "user_selected_runtime_env"
        default_runtime_dir = Path(tmp) / "default_runtime_env"
        managed_python_dir = runtime_managed_python_dir(runtime_dir)
        default_marker = default_runtime_dir / "keep.txt"
        python_dir = managed_python_dir / "cpython-3.13-windows-x86_64-none"
        python_dir.mkdir(parents=True, exist_ok=True)
        default_runtime_dir.mkdir(parents=True, exist_ok=True)
        (python_dir / "python.exe").write_text("placeholder\n", encoding="utf-8")
        default_marker.write_text("default runtime placeholder\n", encoding="utf-8")

        repair_uv_managed_python_dir(managed_python_dir)
        _assert(
            not managed_python_dir.exists(),
            "uv managed Python repair did not remove the app-owned directory",
        )
        _assert(
            default_marker.exists(),
            "uv managed Python repair touched a non-selected runtime directory",
        )
        repair_uv_managed_python_dir(managed_python_dir)

        local_appdata = Path(tmp) / "local_appdata"
        with patch.object(sys, "platform", "win32"), patch.object(
            sys, "frozen", True, create=True
        ), patch.dict("os.environ", {"LOCALAPPDATA": str(local_appdata)}):
            appdata_managed_dir = runtime_managed_python_dir(runtime_dir)
        appdata_python_dir = appdata_managed_dir / "cpython-3.12-windows-x86_64-none"
        appdata_python_dir.mkdir(parents=True, exist_ok=True)
        (appdata_python_dir / "python.exe").write_text("placeholder\n", encoding="utf-8")

        repair_uv_managed_python_dir(appdata_managed_dir)
        _assert(
            not appdata_managed_dir.exists(),
            "uv managed Python repair did not remove the AppData hash directory",
        )


def verify_uv_managed_python_preinstall_retries() -> None:
    """
    Verify managed Python bootstrap retries before runtime source rotation.

    校验 managed Python 会在运行时源轮换前独立重试。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_python_ready_") as tmp:
        managed_python_dir = runtime_managed_python_dir(Path(tmp) / "runtime_env")
        stale_marker = managed_python_dir / "stale.txt"
        stale_marker.parent.mkdir(parents=True, exist_ok=True)
        stale_marker.write_text("stale\n", encoding="utf-8")
        calls: list[list[str]] = []

        def fake_run_uv_install(
            uv_path: str,
            command: list[str],
            label: str,
            *,
            progress_cb=None,
            uv_managed_python_dir: Path | None = None,
            raise_if_cancelled=None,
            active_process_cb=None,
            cwd: Path | None = None,
        ) -> str:
            calls.append(command)
            _assert(uv_path == "uv", "uv path was not forwarded")
            _assert(
                uv_managed_python_dir == managed_python_dir,
                "managed Python dir was not forwarded",
            )
            if len(calls) == 1:
                raise RuntimeError("temporary Python download failure")
            return ""

        with patch("core.uv_runtime_manager.run_uv_install", side_effect=fake_run_uv_install):
            ensure_uv_managed_python_ready("uv", managed_python_dir, max_attempts=2)

        command = calls[0]
        _assert(len(calls) == 2, "managed Python bootstrap did not retry once")
        _assert("python" in command and "install" in command, "uv python install missing")
        _assert("--managed-python" in command, "uv python install must use managed Python")
        _assert("--install-dir" in command, "uv python install must pin install dir")
        _assert("--no-bin" in command, "uv python install must avoid global bin links")
        _assert("--no-registry" in command, "uv python install must avoid registry writes")
        _assert(not stale_marker.exists(), "failed bootstrap did not clean stale Python dir")

    with tempfile.TemporaryDirectory(prefix="superpicky_uv_python_path_") as tmp:
        managed_python_dir = runtime_managed_python_dir(Path(tmp) / "runtime_env")
        path_error = RuntimeError(
            "error: Failed to create Python minor version link directory\n"
            "Caused by: ERROR_UNTRUSTED_MOUNT_POINT (os error 448)"
        )
        path_calls = 0

        def fake_path_error_run(
            uv_path: str,
            command: list[str],
            label: str,
            *,
            progress_cb=None,
            uv_managed_python_dir: Path | None = None,
            raise_if_cancelled=None,
            active_process_cb=None,
            cwd: Path | None = None,
        ) -> str:
            nonlocal path_calls
            path_calls += 1
            raise path_error

        try:
            with patch("core.uv_runtime_manager.run_uv_install", side_effect=fake_path_error_run):
                ensure_uv_managed_python_ready("uv", managed_python_dir, max_attempts=3)
        except RuntimeError as exc:
            _assert(
                "ERROR_UNTRUSTED_MOUNT_POINT" in str(exc),
                "persistent path failure did not surface launch guidance",
            )
        else:
            raise AssertionError("persistent path failure did not raise")
        _assert(path_calls == 3, "persistent path failure did not use all retry attempts")


def verify_uv_install_subprocess_cancellation() -> None:
    """
    Verify uv subprocesses are registered and terminated on cancellation.

    校验 uv 子进程会注册到调用方，并在取消时终止。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_uv_cancel_") as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "slow_uv.py"
        script_path.write_text(
            "import sys, time\n"
            "print('slow uv started', flush=True)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        cancelled = False
        processes: list[subprocess.Popen[str] | None] = []

        def raise_if_cancelled() -> None:
            if cancelled:
                raise RuntimeError("cancelled by test")

        def active_process_cb(process: subprocess.Popen[str] | None) -> None:
            processes.append(process)

        try:
            import threading

            def request_cancel() -> None:
                nonlocal cancelled
                time.sleep(0.8)
                cancelled = True

            cancel_thread = threading.Thread(target=request_cancel, daemon=True)
            cancel_thread.start()
            run_uv_install(
                sys.executable,
                [sys.executable, str(script_path)],
                "slow uv cancellation probe",
                raise_if_cancelled=raise_if_cancelled,
                active_process_cb=active_process_cb,
                cwd=tmp_path,
            )
        except RuntimeError as exc:
            _assert("cancelled by test" in str(exc), "cancel exception was not propagated")
        else:
            raise AssertionError("cancelled uv process did not raise")

        started_processes = [process for process in processes if process is not None]
        _assert(started_processes, "uv subprocess was not registered")
        _assert(processes[-1] is None, "uv subprocess registration was not cleared")
        _assert(
            all(process.poll() is not None for process in started_processes),
            "cancelled uv subprocess is still running",
        )


def verify_lite_spec_uses_downloaded_uv() -> None:
    """
    Verify Lite packaging does not collect uv from the build machine PATH.

    校验 Lite 打包不会从构建机 PATH 收集 uv。
    """
    spec_text = (_PROJECT_ROOT / "SuperPicky_lite_win.spec").read_text(encoding="utf-8")
    _assert(
        "SUPERPICKY_UV_BINARY" in spec_text,
        "Lite spec must require the downloaded uv binary path",
    )
    _assert(
        "shutil.which('uv')" not in spec_text and 'shutil.which("uv")' not in spec_text,
        "Lite spec must not discover uv from the build machine PATH",
    )
    _assert(
        "which('uv')" not in spec_text and 'which("uv")' not in spec_text,
        "Lite spec must not package local uv shims",
    )


def verify_lite_build_tracks_latest_uv() -> None:
    """
    Verify Lite build defaults to the latest upstream uv release.

    校验 Lite 构建默认跟踪上游最新 uv 发布版本。
    """
    build_script = (_PROJECT_ROOT / "build_release_win.py").read_text(encoding="utf-8")
    _assert(
        'os.environ.get("SUPERPICKY_UV_VERSION", "latest")' in build_script,
        "Lite build must default SUPERPICKY_UV_VERSION to latest",
    )
    _assert(
        "https://api.github.com/repos/astral-sh/uv/releases/latest" in build_script,
        "Lite build must resolve the latest uv release from GitHub",
    )
    _assert(
        "normalize_uv_version_tag" in build_script,
        "Lite build must normalize release tags before cache/version checks",
    )


def verify_progress_terminal_is_fast() -> None:
    """
    Verify terminal download progress reaches the phase end immediately.

    校验下载终态进度会立即到达阶段终点。
    """
    model = InitializationProgressModel()
    snapshot = model.on_progress_event(
        InitializationProgressEvent(
            stage=STAGE_DOWNLOADING,
            progress_kind=PROGRESS_KIND_DOWNLOAD,
            message="validated",
            ratio=1.0,
            is_terminal=True,
        ),
        now=0.1,
    )
    _assert(snapshot.display_percent >= 99, "terminal download progress did not finish")


def verify_resource_integrity_floor() -> None:
    """
    Verify zero-byte resources are never considered valid.

    校验 0 字节资源不会被视为有效。
    """
    with tempfile.TemporaryDirectory(prefix="superpicky_resource_verify_") as tmp:
        empty_file = Path(tmp) / "empty.bin"
        empty_file.write_bytes(b"")
        _assert(
            not verify_resource({"filename": "empty.bin", "sha256": None}, empty_file),
            "zero-byte resource was treated as valid",
        )


def verify_hf_cli_size_parser() -> None:
    """Verify hf CLI human size parsing."""
    _assert(_parse_hf_cli_size("1.5M") == int(1.5 * 1024 * 1024), "M size parse failed")
    _assert(_parse_hf_cli_size("1024") == 1024, "plain byte size parse failed")


def verify_runtime_install_attempt_matrix() -> None:
    """
    Verify InitializationManager tries isolated PyPI/Torch source pairs.

    校验 InitializationManager 会按隔离的 PyPI/Torch 源组合重试。
    """
    try:
        from core.initialization_manager import InitializationManager
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise VerificationSkipped("PySide6 unavailable") from exc
        raise

    manager = InitializationManager.__new__(InitializationManager)
    manager._source_map = {
        "pypi_candidates": ["https://pypi-a.example/simple", "https://pypi-b.example/simple"],
        "torch_candidates": ["https://torch-a.example/cu118", "https://torch-b.example/cu118"],
    }
    attempts = InitializationManager._runtime_install_attempts(manager, "cuda")
    pairs = [(attempt.pypi_url, attempt.torch_url) for attempt in attempts]
    _assert(
        pairs == [
            ("https://pypi-a.example/simple", "https://torch-a.example/cu118"),
            ("https://pypi-b.example/simple", "https://torch-a.example/cu118"),
            ("https://pypi-a.example/simple", "https://torch-b.example/cu118"),
            ("https://pypi-b.example/simple", "https://torch-b.example/cu118"),
        ],
        "runtime install attempts are not isolated PyPI/Torch pairs",
    )
    first_attempt = attempts[0]
    tried_pairs = {(first_attempt.pypi_url, first_attempt.torch_url)}
    next_after_pypi = InitializationManager._next_runtime_install_attempt(
        attempts,
        tried_pairs,
        first_attempt,
        "pypi",
    )
    next_after_torch = InitializationManager._next_runtime_install_attempt(
        attempts,
        tried_pairs,
        first_attempt,
        "torch",
    )
    local_runtime_error = (
        "All runtime install source attempts failed: Install cuda runtime "
        "(30/30, PyPI=https://pypi.org/simple, "
        "Torch=https://download.pytorch.org/whl/cu118) after managed Python "
        "repair 失败 (exit=2)\n"
        "Downloading cpython-3.12.13-windows-x86_64-none (download) (20.9MiB)\n"
        "Downloaded cpython-3.12.13-windows-x86_64-none (download)\n"
        "error: Failed to create Python minor version link directory\n"
        "Caused by: 无法遍历该路径，因为它包含不受信任的装入点。 "
        "(os error 448)"
    )
    failed_pool = InitializationManager._classify_runtime_install_failure(
        local_runtime_error,
        first_attempt,
    )
    next_after_local_runtime = InitializationManager._next_runtime_install_attempt(
        attempts,
        tried_pairs,
        first_attempt,
        failed_pool,
    )
    _assert(
        (next_after_pypi.pypi_url, next_after_pypi.torch_url)
        == ("https://pypi-b.example/simple", "https://torch-a.example/cu118"),
        "PyPI failure did not switch only the PyPI pool",
    )
    _assert(
        (next_after_torch.pypi_url, next_after_torch.torch_url)
        == ("https://pypi-a.example/simple", "https://torch-b.example/cu118"),
        "Torch failure did not switch only the Torch pool",
    )
    _assert(
        failed_pool == "local_runtime",
        "uv managed Python local path error was not classified as local runtime",
    )
    _assert(
        next_after_local_runtime is None,
        "local runtime path failure must not rotate PyPI/Torch source pools",
    )


def main() -> int:
    checks = [
        verify_source_registry_isolation,
        verify_torch_direct_wheel_urls,
        verify_uv_command_uses_single_pypi_index,
        verify_packaged_target_uv_avoids_managed_python_by_default,
        verify_uv_environment_sanitizes_installer_python_context,
        verify_uv_managed_python_path_repair_helpers,
        verify_uv_managed_python_preinstall_retries,
        verify_uv_install_subprocess_cancellation,
        verify_lite_spec_uses_downloaded_uv,
        verify_lite_build_tracks_latest_uv,
        verify_progress_terminal_is_fast,
        verify_resource_integrity_floor,
        verify_hf_cli_size_parser,
        verify_runtime_install_attempt_matrix,
    ]
    for check in checks:
        try:
            check()
        except VerificationSkipped as exc:
            print(f"SKIP {check.__name__} ({exc})")
        else:
            print(f"OK {check.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
