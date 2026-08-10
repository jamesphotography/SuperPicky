#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CI 发布辅助脚本 / CI release helper utilities.

集中处理 GitHub Actions 中的发布元数据、资产整理、补丁打包和临时文件操作，
避免在 workflow 中堆积大量平台相关 shell 逻辑。

This module centralizes release metadata handling, asset collection, patch
packaging, and temporary file operations for GitHub Actions workflows so the
workflow can stay small and shell-agnostic.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parent.parent
PATCH_ITEMS = (
    "constants.py",
    "advanced_config.py",
    "ai_model.py",
    "birdid_server.py",
    "birdid_cli.py",
    "iqa_scorer.py",
    "server_manager.py",
    "superpicky_cli.py",
    "topiq_model.py",
    "tools",
    "core",
    "ui",
    "birdid",
    "locales",
)
PATCH_EXCLUDED_DIRS = {"__pycache__"}
PATCH_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def configure_stdio() -> None:
    """
    强制标准输出为 UTF-8 / Force UTF-8 stdio when possible.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def optional_text(value: str | None) -> str | None:
    """
    规范化可选字符串 / Normalize optional strings.
    """

    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def repo_path(raw_path: str | Path) -> Path:
    """
    将相对仓库路径解析为绝对路径 / Resolve repository-relative paths.
    """

    path = Path(raw_path)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def write_github_outputs(values: dict[str, str], output_path: str | None = None) -> None:
    """
    写入 GitHub Actions step outputs / Write GitHub Actions step outputs.
    """

    target = optional_text(output_path) or optional_text(os.environ.get("GITHUB_OUTPUT"))
    if not target:
        return

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def infer_release_tag(event_name: str | None, input_version: str | None, ref_name: str | None) -> str:
    """
    计算 release tag / Resolve the release tag.
    """

    raw_version = input_version if optional_text(event_name) == "workflow_dispatch" else ref_name
    normalized = optional_text(raw_version)
    if not normalized:
        raise RuntimeError("Release version is required.")
    return normalized if normalized.startswith("v") else f"v{normalized}"


def cmd_resolve_metadata(args: argparse.Namespace) -> int:
    """
    解析 release 元数据 / Resolve release metadata.
    """

    tag = infer_release_tag(args.event_name, args.input_version, args.ref_name)
    values = {"tag": tag, "name": f"SuperPicky {tag}"}
    write_github_outputs(values, args.github_output)
    print(json.dumps(values, ensure_ascii=False))
    return 0


def ensure_single_match(pattern: str) -> Path:
    """
    确保 glob 模式只匹配一个文件 / Ensure a glob matches exactly one file.
    """

    matches = [Path(item) for item in glob.glob(pattern) if Path(item).is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one asset for pattern '{pattern}', found {len(matches)}.")
    return matches[0]


def cmd_collect_assets(args: argparse.Namespace) -> int:
    """
    收集 release 资产 / Collect release assets.
    """

    output_dir = repo_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    for pattern in args.pattern:
        source_file = ensure_single_match(str(repo_path(pattern)))
        destination = output_dir / source_file.name
        shutil.copy2(source_file, destination)
        copied_files.append(destination.name)

    print(json.dumps({"output_dir": str(output_dir), "files": copied_files}, ensure_ascii=False))
    return 0


def read_app_version() -> str:
    """
    读取应用版本号 / Read the application version.
    """

    constants_path = ROOT_DIR / "constants.py"
    content = constants_path.read_text(encoding="utf-8")
    marker = 'APP_VERSION = '
    for line in content.splitlines():
        if marker not in line:
            continue
        _, raw_value = line.split(marker, 1)
        return raw_value.strip().strip('"').strip("'")
    raise RuntimeError("Unable to read APP_VERSION from constants.py")


def infer_release_channel(tag: str) -> str:
    """
    根据 tag 判断渠道 / Infer release channel from tag.
    """

    return "nightly" if "-rc" in tag.lower() else "official"


def iter_patch_files() -> Iterable[tuple[Path, Path]]:
    """
    枚举补丁文件 / Enumerate patch files.
    """

    for item in PATCH_ITEMS:
        source_path = ROOT_DIR / item
        if not source_path.exists():
            continue
        if source_path.is_file():
            if source_path.name == "main.py" or source_path.suffix in PATCH_EXCLUDED_SUFFIXES:
                continue
            yield source_path, source_path.relative_to(ROOT_DIR)
            continue

        for file_path in sorted(source_path.rglob("*")):
            if not file_path.is_file():
                continue
            if any(part in PATCH_EXCLUDED_DIRS for part in file_path.parts):
                continue
            if file_path.suffix in PATCH_EXCLUDED_SUFFIXES:
                continue
            yield file_path, file_path.relative_to(ROOT_DIR)


def write_patch_zip(zip_path: Path) -> None:
    """
    创建补丁 ZIP / Create the patch ZIP.
    """

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.unlink(missing_ok=True)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for file_path, relative_path in iter_patch_files():
            archive.write(file_path, arcname=str(relative_path).replace("\\", "/"))


def write_patch_meta(meta_path: Path, patch_version: str, base_version: str, release_channel: str) -> None:
    """
    写入补丁元数据 / Write patch metadata.
    """

    payload = {
        "patch_version": patch_version,
        "base_version": base_version,
        "release_channel": release_channel,
        "target_channels": [release_channel],
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_build_patch(args: argparse.Namespace) -> int:
    """
    生成补丁 ZIP 和 patch_meta.json / Generate patch ZIP and patch_meta.json.
    """

    patch_version = infer_release_tag("workflow_dispatch", args.patch_version, args.patch_version)
    release_channel = infer_release_channel(patch_version)
    base_version = args.base_version or read_app_version()
    output_dir = repo_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_path = output_dir / f"code_patch_{patch_version}.zip"
    meta_path = output_dir / "patch_meta.json"
    write_patch_zip(zip_path)
    write_patch_meta(meta_path, patch_version, base_version, release_channel)

    values = {"patch_zip": str(zip_path), "patch_meta": str(meta_path)}
    write_github_outputs(values, args.github_output)
    print(json.dumps(values, ensure_ascii=False))
    return 0


def decode_secret_value(env_name: str) -> str:
    """
    读取环境变量中的 secret / Read a secret value from the environment.
    """

    value = os.environ.get(env_name, "")
    if not value:
        raise RuntimeError(f"Environment variable {env_name} is required.")
    return value


def cmd_materialize_secret_file(args: argparse.Namespace) -> int:
    """
    将 secret 落盘为文件 / Materialize a secret into a file.
    """

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_value = decode_secret_value(args.env_name)
    data = base64.b64decode(raw_value) if args.decode_base64 else raw_value.encode("utf-8")
    output_path.write_bytes(data)

    values = {"materialized_path": str(output_path)}
    write_github_outputs(values, args.github_output)
    print(json.dumps(values, ensure_ascii=False))
    return 0


def remove_path(path: Path) -> None:
    """
    删除文件或目录 / Remove a file or directory.
    """

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


def cmd_cleanup_paths(args: argparse.Namespace) -> int:
    """
    清理路径 / Clean up files or directories.
    """

    for raw_path in args.path:
        remove_path(Path(raw_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """
    创建命令行解析器 / Build the command-line parser.
    """

    parser = argparse.ArgumentParser(description="SuperPicky CI 发布辅助脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve-metadata", help="解析 release tag 和 name")
    resolve_parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    resolve_parser.add_argument("--input-version", default=os.environ.get("INPUT_VERSION"))
    resolve_parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME"))
    resolve_parser.add_argument("--github-output", help="可选，显式指定 GITHUB_OUTPUT 文件路径")
    resolve_parser.set_defaults(func=cmd_resolve_metadata)

    collect_parser = subparsers.add_parser("collect-assets", help="按 glob 收集 release 资产")
    collect_parser.add_argument("--output-dir", required=True, help="资产输出目录")
    collect_parser.add_argument("--pattern", action="append", required=True, help="需要匹配的文件 glob，可重复指定")
    collect_parser.set_defaults(func=cmd_collect_assets)

    patch_parser = subparsers.add_parser("build-patch", help="生成 code patch ZIP 与 patch_meta.json")
    patch_parser.add_argument("--output-dir", required=True, help="补丁输出目录")
    patch_parser.add_argument("--patch-version", required=True, help="补丁版本号，例如 v4.2.0 或 4.2.0")
    patch_parser.add_argument("--base-version", help="可选，显式指定 base version")
    patch_parser.add_argument("--github-output", help="可选，显式指定 GITHUB_OUTPUT 文件路径")
    patch_parser.set_defaults(func=cmd_build_patch)

    secret_parser = subparsers.add_parser("materialize-secret-file", help="将环境变量写入文件")
    secret_parser.add_argument("--env-name", required=True, help="secret 所在环境变量名")
    secret_parser.add_argument("--output", required=True, help="输出文件路径")
    secret_parser.add_argument("--decode-base64", action="store_true", help="按 Base64 解码后写入")
    secret_parser.add_argument("--github-output", help="可选，显式指定 GITHUB_OUTPUT 文件路径")
    secret_parser.set_defaults(func=cmd_materialize_secret_file)

    cleanup_parser = subparsers.add_parser("cleanup-paths", help="删除文件或目录")
    cleanup_parser.add_argument("--path", action="append", required=True, help="待删除的路径，可重复指定")
    cleanup_parser.set_defaults(func=cmd_cleanup_paths)

    return parser


def main() -> int:
    """
    脚本入口 / Script entrypoint.
    """

    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())