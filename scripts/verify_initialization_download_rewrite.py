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
from core.source_probe import ProbeResult
from core.source_registry import (
    PackageSource,
    get_official_pypi_url,
    get_pypi_sources,
    get_torch_sources,
)
from core.uv_runtime_manager import (
    build_install_command,
    build_uv_install_environment,
    is_uv_managed_python_path_error,
    is_uv_windows_mount_point_error,
    is_uv_python_interpreter_unavailable,
    repair_uv_managed_python_dir,
    run_uv_install,
    runtime_managed_python_dir,
)
import scripts.download_models as download_models
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
    package_urls = requirements.torch_wheel_urls(
        "https://download.pytorch.org/whl/cu118"
    )
    torch_url = package_urls["torch"]
    _assert(torch_url.startswith("torch @ https://"), "torch is not a direct URL")
    _assert("%2Bcu118" in torch_url, "CUDA local version was not URL-encoded")
    _assert("win_amd64.whl" in torch_url, "Windows wheel platform tag missing")


def verify_torch_source_selection_uses_sampled_wheel_speed() -> None:
    """
    Verify Torch source selection follows sampled wheel download speed.

    校验 Torch 源选择依据 wheel 采样下载速度，而不是固定偏向国内镜像。
    """
    try:
        import core.initialization_manager as initialization_manager
        from core.initialization_manager import InitializationManager
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise VerificationSkipped("PySide6 unavailable") from exc
        raise

    pypi_url = get_official_pypi_url()
    torch_official_url = "https://download.pytorch.org/whl/cu118"
    torch_nju_url = "https://mirror.nju.edu.cn/pytorch/whl/cu118/"
    requirements = get_runtime_requirements("cuda")
    torch_official_probe_url = requirements.torch_wheel_urls(torch_official_url)[
        "torch"
    ].split(" @ ", 1)[1]
    torch_nju_probe_url = requirements.torch_wheel_urls(torch_nju_url)["torch"].split(
        " @ ", 1
    )[1]
    pypi_sources = [
        PackageSource(
            name="pypi-official",
            region="global",
            url=pypi_url,
            source_kind="pypi",
            trust_level="official",
        )
    ]
    torch_sources = [
        {
            "name": "torch-official",
            "region": "global",
            "url": torch_official_url,
            "source_kind": "torch",
            "trust_level": "official",
            "is_official": "true",
            "probe_url": torch_official_probe_url,
        },
        {
            "name": "torch-nju",
            "region": "cn",
            "url": torch_nju_url,
            "source_kind": "torch",
            "trust_level": "trusted",
            "is_official": "false",
            "probe_url": torch_nju_probe_url,
        },
    ]

    def fake_probe_sources_parallel(
        group_name: str,
        sources: list[dict],
        timeout: float = 2.0,
    ) -> list[ProbeResult]:
        if group_name == "pypi":
            return [
                ProbeResult(
                    name="pypi-official",
                    url=pypi_url,
                    ok=True,
                    total_ms=60.0,
                    first_byte_ms=40.0,
                    region="global",
                    source_kind="pypi",
                    trust_level="official",
                    is_official=True,
                )
            ]
        _assert(group_name == "torch-cuda", "unexpected source probe group")
        _assert(
            any(
                item.get("probe_url", "").endswith("win_amd64.whl")
                for item in sources
            ),
            "Torch source probe must use direct wheel URLs",
        )
        return [
            ProbeResult(
                name="torch-nju",
                url=torch_nju_url,
                ok=True,
                total_ms=150.0,
                first_byte_ms=20.0,
                region="cn",
                source_kind="torch",
                trust_level="trusted",
            ),
            ProbeResult(
                name="torch-official",
                url=torch_official_url,
                ok=True,
                total_ms=90.0,
                first_byte_ms=55.0,
                region="global",
                source_kind="torch",
                trust_level="official",
                is_official=True,
            ),
        ]

    manager = InitializationManager.__new__(InitializationManager)
    with (
        patch.object(
            initialization_manager,
            "get_pypi_sources",
            return_value=pypi_sources,
        ),
        patch.object(
            InitializationManager,
            "_torch_source_candidates",
            return_value=torch_sources,
        ),
        patch.object(
            initialization_manager,
            "probe_sources_parallel",
            side_effect=fake_probe_sources_parallel,
        ),
    ):
        selected = InitializationManager._resolve_best_sources(manager, "cuda")

    _assert(selected["pypi_primary"] == pypi_url, "PyPI primary should stay official")
    _assert(
        selected["torch_primary"] == torch_official_url,
        "Torch primary must follow sampled wheel speed and choose official here",
    )


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
        target_dir = runtime_dir / "site-packages.__installing__"
        requirements_file.write_text("timm>=0.9.0\n", encoding="utf-8")
        with patch.object(sys, "platform", "win32"), patch.object(
            sys, "frozen", True, create=True
        ), patch.dict(
            "os.environ",
            {
                "UV_MANAGED_PYTHON": "1",
                "UV_NO_MANAGED_PYTHON": "1",
                "UV_PYTHON": str(tmp_path / "poisoned_python.exe"),
                "UV_PYTHON_DOWNLOADS": "automatic",
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
            managed_command = build_install_command(
                uv_path="uv",
                requirements_file=requirements_file,
                index_url=get_official_pypi_url(),
                target_dir=target_dir,
                cache_dir=runtime_dir / "uv-cache",
                use_managed_python=True,
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
    _assert("--managed-python" in managed_command, "managed fallback command missing")
    _assert(
        "--no-python-downloads" not in managed_command,
        "managed fallback command must allow uv to download Python",
    )
    _assert(direct_env["UV_NO_CONFIG"] == "1", "uv must ignore user-global config")
    _assert(
        "UV_MANAGED_PYTHON" not in direct_env,
        "direct target install must ignore user managed-Python env",
    )
    _assert("UV_PYTHON" not in direct_env, "direct target install must ignore UV_PYTHON")
    _assert(
        "UV_PYTHON_DOWNLOADS" not in direct_env,
        "direct target install must rely on --no-python-downloads",
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
        managed_python_dir == runtime_dir / ".uv-python",
        "uv managed Python dir must stay under the selected runtime dir",
    )
    _assert(
        managed_env["UV_PYTHON_DOWNLOADS"] == "automatic",
        "uv managed fallback must be allowed to bootstrap the app-scoped Python",
    )
    _assert("UV_PYTHON" not in managed_env, "managed fallback must ignore user UV_PYTHON")
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
    """
    Verify hf CLI human size parsing uses decimal units.

    校验 hf CLI 人类可读大小按十进制（M=10^6）解析。hf CLI 把 56096965 字节显示为
    "56.1M"，若误用二进制（1024^2）会放大到 58825113，导致完整下载被误判为未完成。
    """
    _assert(_parse_hf_cli_size("1.5M") == int(1.5 * 1000 * 1000), "decimal M size parse failed")
    _assert(_parse_hf_cli_size("56.1M") == 56_100_000, "decimal M rounding parse failed")
    _assert(_parse_hf_cli_size("2G") == 2_000_000_000, "decimal G size parse failed")
    _assert(_parse_hf_cli_size("1024") == 1024, "plain byte size parse failed")


def verify_hf_endpoint_selection_uses_file_probe() -> None:
    """
    Verify HF endpoint ranking probes the actual resource URL and can prefer official.

    校验 HF 端点排序基于实际资源 URL 探测，并且 official 更快时可优先 official。
    """
    captured_sources: list[dict] = []

    def fake_probe_sources_parallel(group_name: str, sources: list[dict], timeout: float = 2.0):
        captured_sources.extend(sources)
        return [
            ProbeResult(
                name="official",
                url=download_models.HF_OFFICIAL_ENDPOINT,
                ok=True,
                total_ms=50.0,
                first_byte_ms=45.0,
            ),
            ProbeResult(
                name="hf-mirror",
                url=download_models.HF_MIRROR_ENDPOINT,
                ok=True,
                total_ms=500.0,
                first_byte_ms=450.0,
            ),
        ]

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HF_ENDPOINT", None)
        with patch.object(download_models, "probe_sources_parallel", fake_probe_sources_parallel):
            endpoints = download_models._resolve_hf_endpoints("owner/repo", "model.bin")

    _assert(
        endpoints[0] == ("official", download_models.HF_OFFICIAL_ENDPOINT),
        "HF endpoint selection must prefer official when it is faster",
    )
    _assert(
        any(
            item.get("probe_url")
            == "https://huggingface.co/owner/repo/resolve/main/model.bin"
            for item in captured_sources
        ),
        "HF endpoint selection did not probe the actual official file URL",
    )
    _assert(
        any(
            item.get("probe_url")
            == "https://hf-mirror.com/owner/repo/resolve/main/model.bin"
            for item in captured_sources
        ),
        "HF endpoint selection did not probe the actual mirror file URL",
    )


def verify_hf_endpoint_selection_uses_latency_and_speed() -> None:
    """
    Verify HF endpoint ranking considers both latency and sampled download speed.

    校验 HF 端点排序同时考虑延时与采样下载速度。
    """
    captured_sources: list[dict] = []
    sample_bytes = download_models.HF_ENDPOINT_PROBE_SAMPLE_BYTES

    def fake_probe_sources_parallel(
        group_name: str,
        sources: list[dict],
        timeout: float = 2.0,
    ) -> list[ProbeResult]:
        captured_sources.extend(sources)
        return [
            ProbeResult(
                name="official",
                url=download_models.HF_OFFICIAL_ENDPOINT,
                ok=True,
                total_ms=900.0,
                first_byte_ms=40.0,
                bytes_read=sample_bytes,
                sample_bytes=sample_bytes,
            ),
            ProbeResult(
                name="hf-mirror",
                url=download_models.HF_MIRROR_ENDPOINT,
                ok=True,
                total_ms=180.0,
                first_byte_ms=90.0,
                bytes_read=sample_bytes,
                sample_bytes=sample_bytes,
            ),
        ]

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HF_ENDPOINT", None)
        with patch.object(download_models, "probe_sources_parallel", fake_probe_sources_parallel):
            endpoints = download_models._resolve_hf_endpoints("owner/repo", "model.bin")

    _assert(
        endpoints[0] == ("hf-mirror", download_models.HF_MIRROR_ENDPOINT),
        "HF endpoint ranking ignored sampled download speed",
    )
    _assert(
        all(item.get("source_kind") == "hf" for item in captured_sources),
        "HF probes must mark their source kind",
    )
    _assert(
        all(int(item.get("probe_sample_bytes", 0)) == sample_bytes for item in captured_sources),
        "HF probes must request a fixed download-speed sample",
    )


def verify_configured_hf_endpoint_is_ranked_by_probe() -> None:
    """
    Verify a configured HF_ENDPOINT participates in ranking instead of forcing first.

    校验用户配置的 HF_ENDPOINT 参与测速排序，而不是无条件排第一。
    """
    configured_endpoint = "https://slow-hf.example.com"

    def fake_probe_sources_parallel(
        group_name: str,
        sources: list[dict],
        timeout: float = 2.0,
    ) -> list[ProbeResult]:
        urls = {item["url"] for item in sources}
        _assert(configured_endpoint in urls, "configured HF endpoint was not probed")
        return [
            ProbeResult(
                name="configured",
                url=configured_endpoint,
                ok=True,
                total_ms=2000.0,
                first_byte_ms=500.0,
                bytes_read=download_models.HF_ENDPOINT_PROBE_SAMPLE_BYTES,
                sample_bytes=download_models.HF_ENDPOINT_PROBE_SAMPLE_BYTES,
            ),
            ProbeResult(
                name="official",
                url=download_models.HF_OFFICIAL_ENDPOINT,
                ok=True,
                total_ms=200.0,
                first_byte_ms=50.0,
                bytes_read=download_models.HF_ENDPOINT_PROBE_SAMPLE_BYTES,
                sample_bytes=download_models.HF_ENDPOINT_PROBE_SAMPLE_BYTES,
            ),
            ProbeResult(
                name="hf-mirror",
                url=download_models.HF_MIRROR_ENDPOINT,
                ok=False,
                total_ms=100.0,
                first_byte_ms=0.0,
                error="offline",
            ),
        ]

    with patch.dict(os.environ, {"HF_ENDPOINT": configured_endpoint}, clear=False):
        with patch.object(download_models, "probe_sources_parallel", fake_probe_sources_parallel):
            endpoints = download_models._resolve_hf_endpoints("owner/repo", "model.bin")

    _assert(
        endpoints[0] == ("official", download_models.HF_OFFICIAL_ENDPOINT),
        "slow configured HF_ENDPOINT must not force first place",
    )
    _assert(
        any(endpoint == configured_endpoint for _name, endpoint in endpoints),
        "configured HF endpoint must remain available as fallback",
    )


def verify_hf_parallel_direct_download_shape() -> None:
    """
    Verify direct HF range downloads use a capped worker pool with a longer task queue.

    校验 HF 直拉 Range 下载使用固定上限的 worker 池和更长的任务队列。
    """
    total_bytes = download_models.HF_DIRECT_PARALLEL_MIN_PART_BYTES * 40
    segments = download_models._build_download_segments(total_bytes)
    _assert(
        len(segments) > download_models.HF_DIRECT_PARALLEL_THREADS,
        "parallel download must keep queued range work after all workers start",
    )
    _assert(
        all(
            segment.size <= download_models.HF_DIRECT_PARALLEL_MIN_PART_BYTES
            for segment in segments
        ),
        "parallel download segments must be small enough for workers to refill",
    )
    _assert(segments[0].start == 0, "first segment must start at byte zero")
    _assert(segments[-1].end == total_bytes - 1, "last segment must cover the final byte")
    for previous, current in zip(segments, segments[1:]):
        _assert(
            previous.end + 1 == current.start,
            "parallel download segments must be contiguous",
        )


def verify_download_prefers_httpx_before_hf_cli() -> None:
    """
    Verify model download uses httpx first and skips hf CLI when httpx succeeds.

    校验模型下载优先使用 httpx，且 httpx 成功时不会再调用 hf CLI。
    """
    calls: list[str] = []

    def fake_httpx_download(**kwargs) -> str:
        calls.append("httpx")
        return str(Path(kwargs["full_dest_dir"]) / kwargs["filename"])

    def fake_cli_download(**kwargs) -> str | None:
        calls.append("cli")
        return None

    with tempfile.TemporaryDirectory(prefix="superpicky_httpx_first_") as tmp:
        with (
            patch.object(
                download_models,
                "_resolve_hf_endpoints",
                return_value=[("official", download_models.HF_OFFICIAL_ENDPOINT)],
            ),
            patch.object(download_models, "_estimate_file_size_via_cli", return_value=1024),
            patch.object(download_models, "_download_via_httpx", side_effect=fake_httpx_download),
            patch.object(download_models, "_download_via_cli", side_effect=fake_cli_download),
        ):
            result = download_models._download_with_fallback(
                resource={"resource_id": "test", "filename": "model.bin"},
                repo_id="owner/repo",
                filename="model.bin",
                full_dest_dir=tmp,
            )

    _assert(result is not None, "httpx-first download did not return a path")
    _assert(calls == ["httpx"], "hf CLI was called before or after successful httpx")


def verify_direct_download_trusts_server_size_over_estimate() -> None:
    """
    Verify a complete httpx download is accepted when the server-reported size is
    smaller than the (rounded) hf CLI estimate.

    校验当服务器返回的真实大小小于 hf CLI 的四舍五入估算值时，完整下载不被误判为未完成。

    回归 PR#98 的真实事故：Xet 文件 Ultralytics/YOLO11/yolo11l-seg.pt 真实大小
    56096965 字节，hf CLI 显示 "56.1M" 被旧解析器按二进制放大到 58825113。Xet CDN
    对该直链返回 200 + 完整 Content-Length，下载完整拿到 56096965 字节，却因
    bytes_written(56096965) < expected_bytes(58825113) 被误判为 incomplete，
    五个下载策略全部失败、mac 打包中止。
    """
    real_size = 56096965 % 200000 + 4096  # 任意小体积，关键在比例而非大小
    inflated_estimate = real_size + 512  # 模拟二进制/十进制换算导致的偏大估算
    body = b"\x7f" * real_size

    class _Resp:
        def __init__(self, status_code: int, headers: dict, body: bytes = b"") -> None:
            self.status_code = status_code
            self.headers = headers
            self._body = body

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def iter_bytes(self, chunk_size: int = 65536):
            for index in range(0, len(self._body), chunk_size):
                yield self._body[index:index + chunk_size]

    class _StreamCtx:
        def __init__(self, resp: _Resp) -> None:
            self._resp = resp

        def __enter__(self) -> _Resp:
            return self._resp

        def __exit__(self, *exc) -> bool:
            return False

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def get(self, url: str, headers=None) -> _Resp:
            # 元数据探测：200 且无 accept-ranges → 不走并行分段，直接退到单流。
            return _Resp(200, {"content-length": str(real_size)})

        def stream(self, method: str, url: str, headers=None) -> _StreamCtx:
            # Xet 行为：无视 Range，返回 200 + 完整 Content-Length。
            return _StreamCtx(_Resp(200, {"content-length": str(real_size)}, body))

    class _FakeHttpx:
        Client = _Client

        @staticmethod
        def Timeout(*args, **kwargs):
            return None

    with tempfile.TemporaryDirectory(prefix="superpicky_size_floor_") as tmp:
        with patch.object(download_models, "httpx", _FakeHttpx):
            result = download_models._download_via_httpx(
                repo_id="Ultralytics/YOLO11",
                filename="model.bin",
                full_dest_dir=tmp,
                endpoint=download_models.HF_OFFICIAL_ENDPOINT,
                source_name="official",
                expected_bytes=inflated_estimate,
            )
        _assert(result is not None, "complete download rejected because estimate exceeded real size")
        written = Path(result).stat().st_size
        _assert(
            written == real_size,
            f"downloaded size mismatch: {written} != {real_size}",
        )


def verify_huggingface_hub_is_bundled() -> None:
    """
    Verify bundled HF dependency is declared for lightweight builds.

    校验轻量化构建声明了内置 HF 依赖。
    """
    requirements = (_PROJECT_ROOT / "requirements_base.txt").read_text(encoding="utf-8")
    spec_text = (_PROJECT_ROOT / "SuperPicky_lite_win.spec").read_text(encoding="utf-8")
    _assert("huggingface_hub" in requirements, "requirements_base.txt must include huggingface_hub")
    _assert(
        "'huggingface_hub'" in spec_text,
        "Lite spec must include huggingface_hub hidden import",
    )


def verify_download_cancellation_reaches_resource_downloader() -> None:
    """
    Verify initialization cancellation is not swallowed by model download fallback.

    校验初始化取消不会被模型下载 fallback 链路吞掉。
    """

    class InitializationInterrupted(Exception):
        """Synthetic cancellation exception with the production class name."""

    def raise_if_cancelled() -> None:
        raise InitializationInterrupted("cancelled")

    with tempfile.TemporaryDirectory(prefix="superpicky_download_cancel_") as tmp:
        try:
            download_models._download_with_fallback(
                resource={"resource_id": "test", "filename": "model.bin"},
                repo_id="owner/repo",
                filename="model.bin",
                full_dest_dir=tmp,
                raise_if_cancelled=raise_if_cancelled,
            )
        except InitializationInterrupted:
            return
    raise AssertionError("download cancellation was swallowed")


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
    # Torch 直链 wheel 矩阵只在 Windows 上展开（其它平台 torch 走 PyPI、
    # torch_url 为空），因此固定模拟 win32 以校验源对隔离逻辑，
    # 保证该回归检查在 mac/Linux 上同样可复现 21/21。
    # Torch direct-wheel pairs only expand on Windows (other platforms install
    # torch via PyPI with an empty torch_url), so pin sys.platform to win32 so
    # this regression check is reproducible on mac/Linux too.
    with patch.object(sys, "platform", "win32"):
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
        verify_torch_source_selection_uses_sampled_wheel_speed,
        verify_uv_command_uses_single_pypi_index,
        verify_packaged_target_uv_avoids_managed_python_by_default,
        verify_uv_environment_sanitizes_installer_python_context,
        verify_uv_managed_python_path_repair_helpers,
        verify_uv_install_subprocess_cancellation,
        verify_lite_spec_uses_downloaded_uv,
        verify_lite_build_tracks_latest_uv,
        verify_progress_terminal_is_fast,
        verify_resource_integrity_floor,
        verify_hf_cli_size_parser,
        verify_hf_endpoint_selection_uses_file_probe,
        verify_hf_endpoint_selection_uses_latency_and_speed,
        verify_configured_hf_endpoint_is_ranked_by_probe,
        verify_hf_parallel_direct_download_shape,
        verify_download_prefers_httpx_before_hf_cli,
        verify_direct_download_trusts_server_size_over_estimate,
        verify_huggingface_hub_is_bundled,
        verify_download_cancellation_reaches_resource_downloader,
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
