# -*- coding: utf-8 -*-
"""
Packaged uv runtime bootstrap helper.

This compatibility entrypoint is used only when the frozen executable is
launched with ``--runtime-bootstrap``. The main initialization manager now calls
uv directly, but keeping this entrypoint uv-based prevents older launch paths
from falling back to pip.

打包态 uv 运行时引导辅助入口。

此兼容入口仅在冻结可执行文件以 ``--runtime-bootstrap`` 启动时使用。主初始化管理器
现在会直接调用 uv；保留这个 uv 化入口可避免旧启动路径回退到 pip。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.source_registry import get_official_pypi_url
from core.uv_runtime_manager import (
    build_install_command,
    ensure_uv_managed_python_ready,
    ensure_uv_bootstrapped,
    is_uv_managed_python_path_error,
    is_uv_windows_mount_point_error,
    is_uv_python_interpreter_unavailable,
    run_uv_install,
    runtime_managed_python_dir,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runtime-bootstrap", action="store_true")
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--index-url", default=None)
    return parser.parse_args(argv)


def _ensure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _write_manifest(
    runtime_dir: Path,
    site_packages_dir: Path,
    args: argparse.Namespace,
    uv_path: str,
) -> None:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime_dir),
        "site_packages_dir": str(site_packages_dir),
        "requirements": str(Path(args.requirements).resolve()),
        "index_url": args.index_url or get_official_pypi_url(),
        "python_version": sys.version,
        "bootstrap_executable": sys.executable,
        "uv_path": uv_path,
    }
    manifest_path = runtime_dir / "runtime_install_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def run_runtime_bootstrap(argv: list[str]) -> int:
    """
    Install runtime packages into app-local site-packages with uv.

    使用 uv 将运行时包安装到应用本地 site-packages。
    """
    _ensure_utf8_stdio()
    args = _parse_args(argv)
    runtime_dir = Path(args.runtime_dir).resolve()
    site_packages_dir = runtime_dir / "site-packages"
    use_managed_python = getattr(sys, "frozen", False) and sys.platform == "win32"
    uv_python_dir = runtime_managed_python_dir(runtime_dir) if use_managed_python else None
    site_packages_dir.mkdir(parents=True, exist_ok=True)

    uv_path = ensure_uv_bootstrapped(runtime_dir)
    command = build_install_command(
        uv_path=uv_path,
        requirements_file=Path(args.requirements).resolve(),
        index_url=args.index_url or get_official_pypi_url(),
        target_dir=site_packages_dir,
        cache_dir=runtime_dir / "uv-cache",
        use_managed_python=False,
    )
    print(f"[runtime-bootstrap] target={site_packages_dir}")
    try:
        run_uv_install(
            uv_path,
            command,
            "runtime bootstrap uv install",
            progress_cb=None,
            uv_managed_python_dir=None,
            cwd=runtime_dir,
        )
    except Exception as exc:
        if (
            use_managed_python
            and uv_python_dir is not None
            and is_uv_python_interpreter_unavailable(str(exc))
        ):
            try:
                ensure_uv_managed_python_ready(
                    uv_path,
                    uv_python_dir,
                    progress_cb=None,
                    cwd=runtime_dir,
                )
                managed_command = build_install_command(
                    uv_path=uv_path,
                    requirements_file=Path(args.requirements).resolve(),
                    index_url=args.index_url or get_official_pypi_url(),
                    target_dir=site_packages_dir,
                    cache_dir=runtime_dir / "uv-cache",
                    use_managed_python=True,
                )
                run_uv_install(
                    uv_path,
                    managed_command,
                    "runtime bootstrap uv install with managed Python fallback",
                    progress_cb=None,
                    uv_managed_python_dir=uv_python_dir,
                    cwd=runtime_dir,
                )
            except Exception as fallback_exc:
                print(
                    "[runtime-bootstrap] uv managed Python fallback failed: "
                    f"{fallback_exc}",
                    file=sys.stderr,
                )
                return 1
        else:
            if (
                uv_python_dir is not None
                and (
                    is_uv_managed_python_path_error(str(exc))
                    or is_uv_windows_mount_point_error(str(exc))
                )
            ):
                print(
                    "[runtime-bootstrap] uv hit a Windows mount-point traversal "
                    "failure. Close SuperPicky and start it again from the "
                    "Start menu or Explorer, then retry initialization.",
                    file=sys.stderr,
                )
                return 1
            print(f"[runtime-bootstrap] uv install failed: {exc}", file=sys.stderr)
            return 1

    if str(site_packages_dir) not in sys.path:
        sys.path.insert(0, str(site_packages_dir))

    import torch  # noqa: F401

    _write_manifest(runtime_dir, site_packages_dir, args, uv_path)
    print("[runtime-bootstrap] torch import verified")
    return 0
