#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - Installer Updater

检测 GitHub Release → 下载平台对应的安装包 → 启动安装。
Detect a new GitHub release, download the matching installer, then hand
control to the OS-native install flow.

设计哲学 / Design philosophy:
用 OS 原生的安装链路替代 hot patch（code_updates overlay）。
Use the OS-native install flow instead of an in-app hot-patch overlay.
- macOS: 下 .dmg, `open` 触发挂载，用户拖到 Applications。
- Windows: 下 Setup_Full_Win64_*.exe（自带 PyTorch 与全部模型），启动安装
  向导即可覆盖升级。4.5.0 起不再构建 Lite。

与原 patch_manager.py 的关键差异:
- 启用 TLS 证书验证（用 certifi 提供 CA bundle）。
- 校验 GitHub API 返回的 size + SHA256。
- 流式分块下载，支持取消、进度回调、断线即失败（不写半成品）。
- 完成后用 rename 原子可见，临时文件 .part 隔离半成品状态。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional


# 下载分块大小，对慢网仍可观察进度变化
# Chunk size for streaming download — small enough to surface progress on slow links.
_DOWNLOAD_CHUNK = 1024 * 1024  # 1 MB

# 文件实际大小相对 API 元数据可接受的偏差（防止流量滥用 + 容忍小幅度元数据延迟）
# Acceptable size deviation between actual download and API metadata.
_SIZE_TOLERANCE = 1024 * 1024  # +/- 1 MB


@dataclass
class InstallerAsset:
    """
    一个 release 资产，足以下载并校验。
    A release asset sufficient to download and verify.
    """

    name: str
    download_url: str
    size: int
    sha256: Optional[str]  # 不带 "sha256:" 前缀，仅 hex；缺失时为 None


class InstallerUpdateError(Exception):
    """
    安装包更新流程中的可恢复错误。
    Recoverable error raised during the installer-update flow.
    """


# ── SSL / 选包 ──────────────────────────────────────────────────────────────


def _ssl_context() -> ssl.SSLContext:
    """
    返回开启证书验证的 SSL 上下文，优先使用 certifi 提供的 CA bundle。
    Return a verifying SSL context, preferring the certifi CA bundle.

    在 PyInstaller 冻结环境下，certifi 必须被打包进 bundle；若 import 失败
    再退回系统默认，从而避免一上来就静默禁用证书验证。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def select_installer_asset(
    assets: List[dict], platform_key: Optional[str] = None
) -> Optional[InstallerAsset]:
    """
    根据当前平台从 release assets 中选择对应的安装包。
    Pick the installer asset for the current platform.

    选择规则 / Rules:
    - macOS: 选 *.dmg，优先 arm64（mac 端只发布 full 包，仍保留 Lite 字样过滤作防御）。
    - Windows: 选 Setup_Full_Win64_*.exe。4.5.0 起不再构建 Lite，Release 上
      只有 Full CPU（CUDA 因超 2 GiB 走网盘，不进 Release）。

    Args:
        assets: GitHub Release assets dict 数组。
        platform_key: 'darwin' / 'win32'；缺省取 sys.platform。

    Returns:
        InstallerAsset；找不到匹配则返回 None。
    """
    if platform_key is None:
        platform_key = sys.platform

    if platform_key == "darwin":
        return _select_mac_installer(assets)
    if platform_key == "win32":
        return _select_windows_installer(assets)
    return None


def _select_mac_installer(assets: List[dict]) -> Optional[InstallerAsset]:
    # 选 arm64 dmg。PyTorch 在 macOS x86_64 上的最后 wheel 是 2.2.2 (2024-03)，
    # 之后官方不再发 Intel wheel，SuperPicky v4.2.6 起也不再为 Intel mac 出包。
    # 排除 Lite 字样（mac 端只发布 full 包，过滤保留作防御）。
    # macOS dmg selection. PyTorch's last macOS x86_64 wheel was 2.2.2 (Mar
    # 2024); from SuperPicky v4.2.6 we no longer ship Intel mac builds.
    # We also filter "Lite" defensively — macOS only ships the full bundle.
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".dmg") and "arm64" in name and "Lite" not in name:
            return _make_asset(asset)

    # 兜底：任意非 Lite dmg。给未来 Universal 包或意外发布留个口子。
    # Fallback to any non-Lite dmg, in case a future universal build shows up.
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".dmg") and "Lite" not in name:
            return _make_asset(asset)
    return None


def _select_windows_installer(assets: List[dict]) -> Optional[InstallerAsset]:
    # 优先 Full（自带 PyTorch + 全部模型，安装即用）。4.5.0 起 Lite 已停止构建。
    # Prefer Full — it bundles PyTorch and every model, so it works offline.
    # Lite is no longer built as of 4.5.0.
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".exe") and "Full_Win64" in name:
            return _make_asset(asset)
    # 兜底：任意 SuperPicky Setup .exe（兼容仍挂着 Lite 的历史 release）。
    # Fallback to any SuperPicky Setup .exe, for older releases that still
    # carry the discontinued Lite installer.
    for asset in assets:
        name = asset.get("name", "")
        if name.endswith(".exe") and "Setup" in name:
            return _make_asset(asset)
    return None


def _make_asset(asset: dict) -> InstallerAsset:
    digest = str(asset.get("digest", "") or "")
    sha256: Optional[str] = None
    if digest.startswith("sha256:"):
        sha256 = digest[len("sha256:"):].lower()
    return InstallerAsset(
        name=str(asset["name"]),
        download_url=str(asset["browser_download_url"]),
        size=int(asset.get("size", 0)),
        sha256=sha256,
    )


# ── 下载 ────────────────────────────────────────────────────────────────────


def get_installer_download_dir() -> Path:
    """
    返回 installer 下载目录。
    Return the directory where installers are downloaded to.

    用 ~/Downloads/ 让用户事后可见、可重用（断点续传暂未实现，所以重启 app
    才能继续，但至少用户能知道文件去哪了）。系统不可写时退到临时目录。
    """
    home = Path.home()
    downloads = home / "Downloads"
    if downloads.is_dir() and os.access(downloads, os.W_OK):
        return downloads
    return Path(tempfile.gettempdir())


def download_installer(
    asset: InstallerAsset,
    dest_dir: Optional[Path] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    connect_timeout: int = 30,
) -> Path:
    """
    流式下载安装包到 `dest_dir / asset.name`，附带 SHA256 与 size 校验。
    Stream-download an installer with SHA256 and size verification.

    流程 / Flow:
    - 写到 `<name>.part`，下载完整后 rename 到 `<name>`，原子可见性。
    - 启用 TLS 证书验证（certifi）。
    - 每 chunk 检查 should_cancel()，立即停止。
    - 拒收超过 size + 1MB 的响应；下载结束二次校验大小。
    - sha256 由 GitHub Release API `digest` 字段提供；缺失时跳过 hash 校验。

    Args:
        asset: 由 `select_installer_asset` 返回。
        dest_dir: 目标目录，默认 `~/Downloads/`。
        on_progress(downloaded, total): 进度回调（字节）；total 可能为 0。
        should_cancel(): 调用返回 True 即放弃下载，抛出错误。
        connect_timeout: 建立连接的超时（秒）。

    Returns:
        下载完成后的文件 Path。

    Raises:
        InstallerUpdateError: 下载失败 / 校验失败 / 用户取消。
    """
    if dest_dir is None:
        dest_dir = get_installer_download_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    final_path = dest_dir / asset.name
    tmp_path = dest_dir / (asset.name + ".part")
    # 清掉上次半成品，避免续传时长度错位。
    tmp_path.unlink(missing_ok=True)

    max_size = asset.size + _SIZE_TOLERANCE if asset.size > 0 else None
    sha = hashlib.sha256() if asset.sha256 else None
    downloaded = 0

    req = urllib.request.Request(
        asset.download_url,
        headers={"User-Agent": "SuperPicky-InstallerUpdater"},
    )

    try:
        with urllib.request.urlopen(
            req, timeout=connect_timeout, context=_ssl_context()
        ) as resp:
            content_len = 0
            try:
                content_len = int(resp.headers.get("Content-Length", "0"))
            except (ValueError, TypeError):
                pass
            total = content_len if content_len > 0 else asset.size

            if max_size and total > max_size:
                raise InstallerUpdateError(
                    f"远端文件 {total} 字节超出 API 宣称大小 {asset.size} +1MB 容差"
                )

            with open(tmp_path, "wb") as f:
                while True:
                    if should_cancel and should_cancel():
                        raise InstallerUpdateError("用户取消下载")
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    if sha:
                        sha.update(chunk)
                    downloaded += len(chunk)
                    if max_size and downloaded > max_size:
                        raise InstallerUpdateError(
                            f"已下载 {downloaded} 字节超出预期 {asset.size}"
                        )
                    if on_progress:
                        on_progress(downloaded, total)

    except InstallerUpdateError:
        tmp_path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        tmp_path.unlink(missing_ok=True)
        raise InstallerUpdateError(f"下载失败: {type(e).__name__}: {e}") from e

    # SHA256 校验
    if sha and asset.sha256:
        actual = sha.hexdigest()
        if actual.lower() != asset.sha256.lower():
            tmp_path.unlink(missing_ok=True)
            raise InstallerUpdateError(
                f"SHA256 校验失败：远端宣称 {asset.sha256}，实际 {actual}"
            )

    # 大小最终校验（防 Content-Length 撒谎）
    actual_size = tmp_path.stat().st_size
    if asset.size > 0 and abs(actual_size - asset.size) > _SIZE_TOLERANCE:
        tmp_path.unlink(missing_ok=True)
        raise InstallerUpdateError(
            f"实际文件大小 {actual_size} 与预期 {asset.size} 偏差超过 ±1MB"
        )

    # 原子可见 — final_path 之前可能因为上次失败残留，先删
    final_path.unlink(missing_ok=True)
    try:
        tmp_path.rename(final_path)
    except OSError:
        # 跨设备 rename 不可用时退到 copy + remove
        shutil.move(str(tmp_path), str(final_path))

    return final_path


# ── 启动安装 ─────────────────────────────────────────────────────────────────


def trigger_install(installer_path: Path) -> None:
    """
    启动安装包并把控制权交给 OS。调用方应在此后退出 app。
    Hand control to the OS installer; the caller should quit afterwards.

    平台行为 / Per-platform behaviour:
    - macOS: `open <dmg>` 挂载并触发 Finder 弹出，用户拖到 Applications 完成
      覆盖安装。
    - Windows: 直接 `Popen([setup.exe])`，Inno Setup 安装器自带"close running
      instance" 提示，用户走完向导即覆盖装好。

    Raises:
        InstallerUpdateError: 路径不存在或子进程启动失败。
    """
    path_str = str(installer_path)
    if not installer_path.exists():
        raise InstallerUpdateError(f"安装包不存在: {path_str}")

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path_str])
        elif sys.platform == "win32":
            # shell=False, 列表 argv → 避免命令注入
            subprocess.Popen([path_str])
        else:
            raise InstallerUpdateError(
                f"unsupported platform for trigger_install: {sys.platform}"
            )
    except OSError as e:
        raise InstallerUpdateError(f"启动安装包失败: {e}") from e


# ── CLI 自测（不依赖 PySide6）───────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="InstallerUpdater 自测工具")
    sub = parser.add_subparsers(dest="cmd")

    p_select = sub.add_parser("select", help="测试 select_installer_asset")
    p_select.add_argument("--tag", default="v4.2.6-RC8")

    p_download = sub.add_parser("download", help="下载并校验当前平台 installer")
    p_download.add_argument("--tag", default="v4.2.6-RC8")
    p_download.add_argument("--dest", default=None)

    args = parser.parse_args()

    if args.cmd in ("select", "download"):
        api_url = (
            f"https://api.github.com/repos/jamesphotography/SuperPicky/releases/tags/{args.tag}"
        )
        req = urllib.request.Request(
            api_url, headers={"User-Agent": "SuperPicky-InstallerUpdater"}
        )
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        asset = select_installer_asset(data.get("assets", []))
        if not asset:
            print(f"找不到匹配 {sys.platform} 的安装包")
            sys.exit(1)
        print(f"selected: {asset.name}")
        print(f"  size:   {asset.size} ({asset.size/1048576:.1f} MB)")
        print(f"  sha256: {asset.sha256}")
        print(f"  url:    {asset.download_url}")

        if args.cmd == "download":
            dest_dir = Path(args.dest) if args.dest else None

            def _prog(d, t):
                ratio = d / t if t else 0
                print(f"  progress: {d}/{t} ({ratio*100:.1f}%)", end="\r")

            try:
                path = download_installer(asset, dest_dir=dest_dir, on_progress=_prog)
                print(f"\nOK: {path}")
            except InstallerUpdateError as e:
                print(f"\nFAIL: {e}")
                sys.exit(2)
    else:
        parser.print_help()
