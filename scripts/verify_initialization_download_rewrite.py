# -*- coding: utf-8 -*-
"""
Offline regression checks for the initialization download rewrite.

初始化下载链路重写的离线回归检查。
"""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

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
from core.uv_runtime_manager import build_install_command
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


def main() -> int:
    checks = [
        verify_source_registry_isolation,
        verify_torch_direct_wheel_urls,
        verify_uv_command_uses_single_pypi_index,
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
