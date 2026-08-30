#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步 ExifTool 官方 production release 到仓库资源目录。

该脚本只整理上游官方分发包，不尝试重新编译 ExifTool。macOS/Unix 资源来自
Image-ExifTool-*.tar.gz 中的 Perl 脚本和 lib 目录；Windows 资源来自官方
exiftool-*_64.zip 中的 exiftool.exe 与 exiftool_files 目录。

Synchronize the official ExifTool production release into repository assets.

This script repackages upstream official distributions only. It does not
compile ExifTool. macOS/Unix assets come from the Perl distribution tarball,
while Windows assets come from the official 64-bit executable zip package.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
EXIFTOOLS_MAC_DIR = ROOT_DIR / "exiftools_mac"
EXIFTOOLS_WIN_DIR = ROOT_DIR / "exiftools_win"
VERSION_RECORD = ROOT_DIR / "exiftools" / "VERSION.json"
# ExifTool 历史页候选地址（按顺序尝试，首个可解析者生效）。
# 官网已把 history 页迁到 sourceforge 镜像，exiftool.org/history.html 现返回 404；
# 保留旧地址作为兜底，以防镜像再次变更。
# Candidate ExifTool history page URLs, tried in order until one parses.
# The official site moved this page to the sourceforge mirror; exiftool.org/history.html
# now returns 404. The legacy URL is kept as a fallback in case the mirror moves again.
HISTORY_URLS: tuple[str, ...] = (
    "https://exiftool.sourceforge.net/history.html",
    "https://exiftool.org/history.html",
)
SOURCEFORGE_DOWNLOAD_BASE_URL = "https://sourceforge.net/projects/exiftool/files"
HTTP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class DownloadAsset:
    """
    下载资源描述。

    参数:
    name: 资源名称，用于日志和版本记录。
    url: 下载地址。
    sha256: 下载后的 SHA256 摘要。
    path: 本地临时文件路径。

    Download asset description.

    Parameters:
    name: Asset name for logs and version records.
    url: Download URL.
    sha256: SHA256 digest after download.
    path: Local temporary file path.
    """

    name: str
    url: str
    sha256: str
    path: Path


@dataclass(frozen=True)
class SyncResult:
    """
    同步结果。

    参数:
    current_version: 仓库记录或资源目录中的当前版本。
    latest_version: 上游最新 production release 版本。
    update_available: 是否发现可同步的新版本。

    Synchronization result.

    Parameters:
    current_version: Current version recorded or detected in repository assets.
    latest_version: Latest upstream production release version.
    update_available: Whether a newer sync target is available.
    """

    current_version: str
    latest_version: str
    update_available: bool


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    返回:
    argparse.Namespace: 解析后的参数。

    Parse command-line arguments.

    Return:
    argparse.Namespace: Parsed arguments.
    """

    parser = argparse.ArgumentParser(description="Sync official ExifTool assets.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检测上游 production release，不修改资源目录。",
    )
    parser.add_argument(
        "--target-version",
        help="显式指定要同步的 ExifTool 版本，跳过 production release 检测。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使目标版本等于当前版本，也重新下载并刷新资源目录。",
    )
    parser.add_argument(
        "--github-output",
        default=os.environ.get("GITHUB_OUTPUT"),
        help="GitHub Actions 输出文件路径，默认读取 GITHUB_OUTPUT。",
    )
    return parser.parse_args()


def normalize_version(version: str) -> str:
    """
    规范化版本号文本。

    参数:
    version: 原始版本字符串。

    返回:
    str: 仅包含数字和点号的 ExifTool 版本号。

    Normalize version text.

    Parameters:
    version: Raw version string.

    Return:
    str: ExifTool version containing only digits and dots.
    """

    match = re.search(r"\d+(?:\.\d+)+", version.strip())
    if not match:
        raise ValueError(f"Invalid ExifTool version: {version!r}")
    return match.group(0)


def fetch_text(url: str) -> str:
    """
    读取远程文本，按 UTF-8 解码。

    参数:
    url: 远程地址。

    返回:
    str: 解码后的文本。

    Read remote text decoded as UTF-8.

    Parameters:
    url: Remote URL.

    Return:
    str: Decoded text.
    """

    # 注意：不要把 User-Agent 伪装成浏览器（Mozilla/...），SourceForge 会对伪装 UA 返回 403；
    # 本自定义 UA 与 urllib 默认 UA 均可正常获取。
    # Note: do NOT spoof a browser User-Agent (Mozilla/...) — SourceForge returns 403 for
    # spoofed UAs. This custom UA and urllib's default UA both work.
    request = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-ExifTool-Sync/1.0"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_production_version(history_html: str) -> str | None:
    """
    从历史页 HTML 中解析最新 production release 版本号。

    参数:
    history_html: 历史页原始 HTML 文本。

    返回:
    str | None: 解析到的版本号；页面结构不匹配时返回 None。

    Parse the latest production release version from history page HTML.

    Parameters:
    history_html: Raw HTML text of the history page.

    Return:
    str | None: Parsed version, or None when the page structure does not match.
    """

    decoded_html = html.unescape(history_html)
    note_match = re.search(
        r"most recent production release is\s+.*?Version\s+(\d+(?:\.\d+)+)",
        decoded_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if note_match:
        return normalize_version(note_match.group(1))

    release_match = re.search(
        r"Version\s+(\d+(?:\.\d+)+)\s*\(production release\)",
        decoded_html,
        flags=re.IGNORECASE,
    )
    if release_match:
        return normalize_version(release_match.group(1))

    return None


def get_latest_production_version() -> str:
    """
    从 ExifTool 官网历史页解析最新 production release。

    依次尝试 HISTORY_URLS 中的候选地址，返回首个成功解析的版本号。

    返回:
    str: 最新 production release 版本号。

    Raises:
    RuntimeError: 当所有候选地址均无法访问或无法解析时抛出。

    Parse the latest production release from the official ExifTool history page.

    Tries each candidate in HISTORY_URLS in order and returns the first version parsed.

    Return:
    str: Latest production release version.

    Raises:
    RuntimeError: Raised when every candidate URL is unreachable or unparsable.
    """

    failures: list[str] = []
    for url in HISTORY_URLS:
        try:
            history_html = fetch_text(url)
        except (urllib.error.URLError, OSError) as exc:
            failures.append(f"{url}: {exc}")
            continue

        version = parse_production_version(history_html)
        if version:
            return version
        failures.append(f"{url}: production release not found in page")

    detail = "; ".join(failures)
    raise RuntimeError(f"Unable to detect latest ExifTool production release. Tried: {detail}")


def run_command(command: list[str], *, cwd: Path | None = None) -> str:
    """
    执行命令并返回标准输出。

    参数:
    command: 命令参数列表。
    cwd: 可选工作目录。

    返回:
    str: UTF-8 解码后的标准输出。

    Raises:
    RuntimeError: 当命令执行失败时抛出。

    Run a command and return stdout.

    Parameters:
    command: Command argument list.
    cwd: Optional working directory.

    Return:
    str: UTF-8 decoded stdout.

    Raises:
    RuntimeError: Raised when the command fails.
    """

    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def detect_current_version() -> str:
    """
    检测仓库当前 ExifTool 版本。

    优先读取版本记录文件；若文件不存在，则调用当前平台可执行文件的 -ver。

    返回:
    str: 当前版本号，未知时返回空字符串。

    Detect the current ExifTool version in the repository.

    The version record is preferred. If missing, the script calls the
    platform-specific executable with -ver.

    Return:
    str: Current version, or an empty string when unknown.
    """

    if VERSION_RECORD.exists():
        data = json.loads(VERSION_RECORD.read_text(encoding="utf-8"))
        version = str(data.get("version", "")).strip()
        if version:
            return normalize_version(version)

    if platform.system() == "Windows":
        exiftool_path = EXIFTOOLS_WIN_DIR / "exiftool.exe"
        cwd = EXIFTOOLS_WIN_DIR
    else:
        exiftool_path = EXIFTOOLS_MAC_DIR / "exiftool"
        cwd = EXIFTOOLS_MAC_DIR

    if not exiftool_path.exists():
        return ""

    return normalize_version(run_command([str(exiftool_path), "-ver"], cwd=cwd))


def write_github_outputs(values: dict[str, str], output_path: str | None) -> None:
    """
    写入 GitHub Actions step outputs。

    参数:
    values: 输出键值。
    output_path: GITHUB_OUTPUT 文件路径；为空时不写入。

    Write GitHub Actions step outputs.

    Parameters:
    values: Output key-value pairs.
    output_path: GITHUB_OUTPUT file path; skipped when empty.
    """

    if not output_path:
        return
    target = Path(output_path)
    with target.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def sha256_file(path: Path) -> str:
    """
    计算文件 SHA256。

    参数:
    path: 文件路径。

    返回:
    str: 十六进制 SHA256 摘要。

    Calculate file SHA256.

    Parameters:
    path: File path.

    Return:
    str: Hex SHA256 digest.
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_asset(name: str, url: str, target_dir: Path) -> DownloadAsset:
    """
    下载远程资源到临时目录。

    参数:
    name: 资源名称。
    url: 下载地址。
    target_dir: 临时目录。

    返回:
    DownloadAsset: 下载结果。

    Download a remote asset into a temporary directory.

    Parameters:
    name: Asset name.
    url: Download URL.
    target_dir: Temporary directory.

    Return:
    DownloadAsset: Download result.
    """

    parsed_path = urllib.parse.urlparse(url).path.removesuffix("/download")
    archive_name = Path(parsed_path).name or name
    target_path = target_dir / archive_name
    if target_path.exists():
        target_path = target_dir / f"{name}-{archive_name}"
    request = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-ExifTool-Sync/1.0"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        with target_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    return DownloadAsset(name=name, url=url, sha256=sha256_file(target_path), path=target_path)


def ensure_safe_member_path(base_dir: Path, member_name: str) -> Path:
    """
    确认归档成员不会逃逸目标目录。

    参数:
    base_dir: 解压目标目录。
    member_name: 归档成员路径。

    返回:
    Path: 解析后的安全目标路径。

    Raises:
    RuntimeError: 当成员路径逃逸目标目录时抛出。

    Ensure an archive member cannot escape the target directory.

    Parameters:
    base_dir: Extraction target directory.
    member_name: Archive member path.

    Return:
    Path: Resolved safe target path.

    Raises:
    RuntimeError: Raised when the member path escapes the target directory.
    """

    target = (base_dir / member_name).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise RuntimeError(f"Unsafe archive member path: {member_name}")
    return target


def extract_tar_safely(archive_path: Path, target_dir: Path) -> None:
    """
    安全解压 tar.gz 文件。

    参数:
    archive_path: tar.gz 文件路径。
    target_dir: 解压目录。

    Safely extract a tar.gz archive.

    Parameters:
    archive_path: tar.gz archive path.
    target_dir: Extraction directory.
    """

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            ensure_safe_member_path(target_dir, member.name)
        # filter="data" 在 Python 3.12+ 中显式声明安全过滤策略，避免 DeprecationWarning，
        # 并在 Python 3.14 默认行为变更前保持一致性。
        # Explicitly pass filter="data" (Python 3.12+) to suppress DeprecationWarning
        # and stay consistent before the default changes in Python 3.14.
        archive.extractall(target_dir, filter="data")


def extract_zip_safely(archive_path: Path, target_dir: Path) -> None:
    """
    安全解压 zip 文件。

    参数:
    archive_path: zip 文件路径。
    target_dir: 解压目录。

    Safely extract a zip archive.

    Parameters:
    archive_path: zip archive path.
    target_dir: Extraction directory.
    """

    with zipfile.ZipFile(archive_path) as archive:
        for member_name in archive.namelist():
            ensure_safe_member_path(target_dir, member_name)
        archive.extractall(target_dir)


def replace_directory(source_dir: Path, target_dir: Path) -> None:
    """
    用源目录原子式替换目标目录。

    参数:
    source_dir: 已准备好的源目录。
    target_dir: 仓库内目标目录。

    Replace a target directory with a prepared source directory.

    Parameters:
    source_dir: Prepared source directory.
    target_dir: Repository target directory.
    """

    backup_dir = target_dir.with_name(f"{target_dir.name}.bak")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if target_dir.exists():
        target_dir.rename(backup_dir)
    try:
        shutil.copytree(source_dir, target_dir)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        if backup_dir.exists():
            backup_dir.rename(target_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def first_existing_path(paths: Iterable[Path]) -> Path:
    """
    返回第一个存在的路径。

    参数:
    paths: 候选路径列表。

    返回:
    Path: 第一个存在的路径。

    Raises:
    RuntimeError: 当候选路径均不存在时抛出。

    Return the first existing path.

    Parameters:
    paths: Candidate paths.

    Return:
    Path: First existing path.

    Raises:
    RuntimeError: Raised when no candidate exists.
    """

    checked: list[Path] = []
    for path in paths:
        if path.exists():
            return path
        checked.append(path)
    # 在错误信息中包含所有已检查的路径，方便诊断归档结构变化。
    # Include all checked paths in the error message for easier diagnosis of archive layout changes.
    raise RuntimeError(f"None of the expected paths exist in archive: {[str(p) for p in checked]}")


def prepare_mac_assets(version: str, asset: DownloadAsset, work_dir: Path) -> Path:
    """
    准备 macOS/Unix ExifTool 资源目录。

    参数:
    version: 目标版本。
    asset: Perl 分发包下载结果。
    work_dir: 工作目录。

    返回:
    Path: 已整理好的资源目录。

    Prepare macOS/Unix ExifTool assets.

    Parameters:
    version: Target version.
    asset: Perl distribution download result.
    work_dir: Working directory.

    Return:
    Path: Prepared asset directory.
    """

    extract_dir = work_dir / "mac_extract"
    prepared_dir = work_dir / "exiftools_mac"
    extract_tar_safely(asset.path, extract_dir)
    prepared_dir.mkdir(parents=True, exist_ok=True)
    source_root = first_existing_path(
        [
            extract_dir / f"Image-ExifTool-{version}",
            extract_dir / f"exiftool-{version}",
        ]
    )
    shutil.copytree(source_root / "lib", prepared_dir / "lib")
    shutil.copy2(source_root / "exiftool", prepared_dir / "exiftool")
    (prepared_dir / "exiftool").chmod(0o755)
    return prepared_dir


def prepare_windows_assets(version: str, asset: DownloadAsset, work_dir: Path) -> Path:
    """
    准备 Windows ExifTool 资源目录。

    参数:
    version: 目标版本。
    asset: Windows zip 下载结果。
    work_dir: 工作目录。

    返回:
    Path: 已整理好的资源目录。

    Prepare Windows ExifTool assets.

    Parameters:
    version: Target version.
    asset: Windows zip download result.
    work_dir: Working directory.

    Return:
    Path: Prepared asset directory.
    """

    extract_dir = work_dir / "win_extract"
    prepared_dir = work_dir / "exiftools_win"
    extract_zip_safely(asset.path, extract_dir)
    prepared_dir.mkdir(parents=True, exist_ok=True)
    source_root = first_existing_path(
        [
            extract_dir / f"exiftool-{version}_64",
            extract_dir / f"exiftool-{version}",
        ]
    )
    exe_path = first_existing_path(
        [
            source_root / "exiftool.exe",
            source_root / "exiftool(-k).exe",
        ]
    )
    shutil.copy2(exe_path, prepared_dir / "exiftool.exe")
    shutil.copytree(source_root / "exiftool_files", prepared_dir / "exiftool_files")
    return prepared_dir


def verify_prepared_assets(version: str, mac_dir: Path, win_dir: Path) -> None:
    """
    校验已整理资源的版本号。

    参数:
    version: 目标版本。
    mac_dir: macOS/Unix 资源目录。
    win_dir: Windows 资源目录。

    Raises:
    RuntimeError: 当可运行平台的版本校验失败时抛出。

    Verify prepared asset versions.

    Parameters:
    version: Target version.
    mac_dir: macOS/Unix asset directory.
    win_dir: Windows asset directory.

    Raises:
    RuntimeError: Raised when verification fails on the runnable platform.
    """

    mac_version = normalize_version(run_command([str(mac_dir / "exiftool"), "-ver"], cwd=mac_dir))
    if mac_version != version:
        raise RuntimeError(f"macOS ExifTool version mismatch: expected {version}, got {mac_version}")

    if platform.system() == "Windows":
        win_version = normalize_version(run_command([str(win_dir / "exiftool.exe"), "-ver"], cwd=win_dir))
        if win_version != version:
            raise RuntimeError(f"Windows ExifTool version mismatch: expected {version}, got {win_version}")


def write_version_record(version: str, assets: list[DownloadAsset]) -> None:
    """
    写入 ExifTool 版本记录。

    参数:
    version: 已同步版本。
    assets: 下载资源列表。

    Write the ExifTool version record.

    Parameters:
    version: Synchronized version.
    assets: Downloaded assets.
    """

    VERSION_RECORD.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "source": "https://exiftool.org/",
        "release_type": "production",
        "synced_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "assets": [
            {
                "name": asset.name,
                "url": asset.url,
                "sha256": asset.sha256,
            }
            for asset in assets
        ],
    }
    VERSION_RECORD.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sync_assets(version: str) -> None:
    """
    下载并同步指定版本的 ExifTool 资源。

    参数:
    version: 目标版本。

    Download and synchronize ExifTool assets for a target version.

    Parameters:
    version: Target version.
    """

    perl_url = f"{SOURCEFORGE_DOWNLOAD_BASE_URL}/Image-ExifTool-{version}.tar.gz/download"
    windows_url = f"{SOURCEFORGE_DOWNLOAD_BASE_URL}/exiftool-{version}_64.zip/download"
    with tempfile.TemporaryDirectory(prefix="superpicky-exiftool-") as temp_name:
        work_dir = Path(temp_name)
        perl_asset = download_asset("perl_distribution", perl_url, work_dir)
        windows_asset = download_asset("windows_64_executable", windows_url, work_dir)
        mac_dir = prepare_mac_assets(version, perl_asset, work_dir)
        win_dir = prepare_windows_assets(version, windows_asset, work_dir)
        verify_prepared_assets(version, mac_dir, win_dir)
        replace_directory(mac_dir, EXIFTOOLS_MAC_DIR)
        replace_directory(win_dir, EXIFTOOLS_WIN_DIR)
        write_version_record(version, [perl_asset, windows_asset])


def resolve_sync_result(args: argparse.Namespace) -> SyncResult:
    """
    解析当前版本、目标版本和是否需要更新。

    参数:
    args: 命令行参数。

    返回:
    SyncResult: 同步判断结果。

    Resolve current version, target version, and update availability.

    Parameters:
    args: Command-line arguments.

    Return:
    SyncResult: Synchronization decision.
    """

    current_version = detect_current_version()
    latest_version = normalize_version(args.target_version) if args.target_version else get_latest_production_version()
    update_available = args.force or current_version != latest_version
    return SyncResult(
        current_version=current_version,
        latest_version=latest_version,
        update_available=update_available,
    )


def main() -> int:
    """
    脚本入口。

    返回:
    int: 进程退出码。

    Script entry point.

    Return:
    int: Process exit code.
    """

    args = parse_args()
    result = resolve_sync_result(args)
    print(f"Current ExifTool version: {result.current_version or 'unknown'}")
    print(f"Latest production version: {result.latest_version}")
    print(f"Update available: {str(result.update_available).lower()}")

    if result.update_available and not args.check_only:
        sync_assets(result.latest_version)
        print(f"Synced ExifTool {result.latest_version}.")
    elif args.check_only:
        print("Check-only mode enabled; repository assets were not changed.")
    else:
        print("ExifTool assets are already up to date.")

    write_github_outputs(
        {
            "current_version": result.current_version,
            "latest_version": result.latest_version,
            "update_available": str(result.update_available).lower(),
        },
        args.github_output,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
