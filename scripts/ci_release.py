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

# GitHub Releases 单个 asset 上限：2 GiB（官方文档 "Each file included in a
# release must be under 2 GiB"）。注意是二进制 GiB = 2,147,483,648 字节，不是
# 十进制 2 GB = 2,000,000,000 字节；CUDA 安装器长期显示为 "2.11 GB" 而被误判
# 超限，实际只有 1.966 GiB，一直在上限之内。
# GitHub's per-asset limit is 2 GiB (2,147,483,648 bytes), NOT decimal 2 GB.
# The CUDA installer displays as "2.11 GB" in many tools but is 1.966 GiB and
# has always been within the limit.
GITHUB_RELEASE_ASSET_LIMIT_BYTES = 2 * 1024 ** 3

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


def find_optional_match(pattern: str) -> Path | None:
    """
    查找可选资产 / Find an optional asset.

    与 ensure_single_match 的区别：找不到文件时返回 None 而不是抛错，
    用于 CUDA 安装器这类「构建失败也不阻塞发布」的可选产物。

    Unlike ensure_single_match, this returns None instead of raising when the
    file is missing, for optional artifacts such as the CUDA installer whose
    build failure must not block the release.

    参数 / Parameters:
    pattern (str): 文件 glob 模式 / File glob pattern.

    返回 / Return:
    Path | None: 命中的唯一文件；无命中返回 None / The single match, or None.

    异常 / Raises:
    RuntimeError: 命中多于一个文件时 / When more than one file matches.
    """

    matches = [Path(item) for item in glob.glob(pattern) if Path(item).is_file()]
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(f"Expected at most one asset for pattern '{pattern}', found {len(matches)}.")
    return matches[0]


def format_size(size_bytes: int) -> str:
    """
    格式化字节数 / Format a byte count for logs.

    同时给出二进制 GiB 与十进制 GB，因为 GitHub 卡的是 GiB，而多数工具显示
    的是 GB——历史上这两者的混淆让 CUDA 安装器被误判为「超过 2GB 上限」。

    Prints both GiB and GB: GitHub enforces GiB while most tools display GB,
    and confusing the two previously caused the CUDA installer to be wrongly
    treated as exceeding the limit.

    参数 / Parameters:
    size_bytes (int): 字节数 / Size in bytes.

    返回 / Return:
    str: 形如 "2,111,142,835 bytes (1.9662 GiB / 2.111 GB)" 的描述文本。
         小数位取到 4 位，否则临界情况（超限 1 字节）在日志里会和上限显示成
         同一个数字 / Four decimals, otherwise a 1-byte overage renders
         identically to the limit in CI logs.
    """

    return (
        f"{size_bytes:,} bytes "
        f"({size_bytes / 1024 ** 3:.4f} GiB / {size_bytes / 10 ** 9:.3f} GB)"
    )


def check_asset_size(path: Path, max_bytes: int) -> tuple[int, bool]:
    """
    检查单个资产是否符合体积上限 / Check one asset against the size limit.

    参数 / Parameters:
    path (Path): 待检查的文件 / File to inspect.
    max_bytes (int): 允许的最大字节数 / Maximum allowed size in bytes.

    返回 / Return:
    tuple[int, bool]: (文件字节数, 是否在上限内) / (size in bytes, within limit).
    """

    size_bytes = path.stat().st_size
    return size_bytes, size_bytes <= max_bytes


def cmd_collect_assets(args: argparse.Namespace) -> int:
    """
    收集 release 资产 / Collect release assets.

    每个资产都会经过体积卡口：超过 --max-bytes（默认 GitHub 单文件上限
    2 GiB）时直接失败，避免把注定上传失败的文件交给 release 步骤。

    Every asset passes a size gate: exceeding --max-bytes (default: GitHub's
    2 GiB per-file limit) fails fast instead of handing a doomed upload to the
    release step.

    异常 / Raises:
    RuntimeError: 任一资产超过体积上限时 / When any asset exceeds the limit.
    """

    output_dir = repo_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(args.max_bytes)

    copied_files: list[str] = []
    for pattern in args.pattern:
        source_file = ensure_single_match(str(repo_path(pattern)))
        size_bytes, within_limit = check_asset_size(source_file, max_bytes)
        print(f"[体积检查 / size check] {source_file.name}: {format_size(size_bytes)}")
        if not within_limit:
            raise RuntimeError(
                f"Asset '{source_file.name}' is {format_size(size_bytes)}, "
                f"over the {format_size(max_bytes)} limit."
            )
        destination = output_dir / source_file.name
        shutil.copy2(source_file, destination)
        copied_files.append(destination.name)

    print(json.dumps({"output_dir": str(output_dir), "files": copied_files}, ensure_ascii=False))
    return 0


def cmd_stage_optional_asset(args: argparse.Namespace) -> int:
    """
    暂存可选 release 资产 / Stage an optional release asset.

    专供 CUDA 安装器：体积在上限内就复制进 release 资产目录随正式发布上传；
    超限或产物缺失则跳过并输出 staged=false，由 workflow 回退到 workflow
    artifact 通道，全程不使发布失败。

    Built for the CUDA installer: stage it into the release asset directory when
    it fits the limit, otherwise emit staged=false so the workflow can fall back
    to the workflow-artifact channel. Never fails the release.

    返回 / Return:
    int: 始终为 0（该步骤不阻塞发布）/ Always 0; this step never blocks release.
    """

    output_dir = repo_path(args.output_dir)
    max_bytes = int(args.max_bytes)
    source_file = find_optional_match(str(repo_path(args.pattern)))

    if source_file is None:
        print(f"[跳过 / skip] 未找到可选资产 / optional asset not found: {args.pattern}")
        write_github_outputs({"staged": "false", "reason": "missing", "size_bytes": "0"}, args.github_output)
        return 0

    size_bytes, within_limit = check_asset_size(source_file, max_bytes)
    print(f"[体积检查 / size check] {source_file.name}: {format_size(size_bytes)}")
    print(f"[上限 / limit] {format_size(max_bytes)}")

    if not within_limit:
        print(
            f"::warning::{source_file.name} 超过 GitHub 单文件上限 "
            f"({format_size(size_bytes)} > {format_size(max_bytes)})，"
            f"改为上传 workflow artifact / exceeds the GitHub per-file limit; "
            f"falling back to the workflow artifact channel."
        )
        write_github_outputs({"staged": "false", "reason": "oversize", "size_bytes": str(size_bytes)}, args.github_output)
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source_file.name
    shutil.copy2(source_file, destination)
    headroom = max_bytes - size_bytes
    print(f"[余量 / headroom] {format_size(headroom)}")
    write_github_outputs({"staged": "true", "reason": "ok", "size_bytes": str(size_bytes)}, args.github_output)
    print(json.dumps({"output_dir": str(output_dir), "file": destination.name}, ensure_ascii=False))
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
    collect_parser.add_argument(
        "--max-bytes",
        type=int,
        default=GITHUB_RELEASE_ASSET_LIMIT_BYTES,
        help=f"单个资产体积上限，默认 GitHub 上限 {GITHUB_RELEASE_ASSET_LIMIT_BYTES} 字节 (2 GiB)",
    )
    collect_parser.set_defaults(func=cmd_collect_assets)

    stage_parser = subparsers.add_parser(
        "stage-optional-asset",
        help="按体积上限暂存可选资产（CUDA 安装器），超限或缺失时跳过而不失败",
    )
    stage_parser.add_argument("--output-dir", required=True, help="资产输出目录")
    stage_parser.add_argument("--pattern", required=True, help="需要匹配的文件 glob")
    stage_parser.add_argument(
        "--max-bytes",
        type=int,
        default=GITHUB_RELEASE_ASSET_LIMIT_BYTES,
        help=f"单个资产体积上限，默认 GitHub 上限 {GITHUB_RELEASE_ASSET_LIMIT_BYTES} 字节 (2 GiB)",
    )
    stage_parser.add_argument("--github-output", help="可选，显式指定 GITHUB_OUTPUT 文件路径")
    stage_parser.set_defaults(func=cmd_stage_optional_asset)

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