#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 在线补丁管理器

职责：
- 从 GitHub Release 拉取 patch_meta.json，判断是否有新补丁
- 下载 code_patch_vX.Y.Z.zip 并解压到 code_updates/ 目录
- 写入本地 patch_meta.json 记录当前补丁状态
- 提供 clear_patch() 回滚接口

补丁只在 base_version 与当前应用版本匹配时才应用。
"""

import sys
import os
import json
import ssl
import stat
import shutil
import zipfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Tuple

from config import get_app_config_dir, get_patch_dir as shared_get_patch_dir


# GitHub Release Asset 文件名
PATCH_META_FILENAME = "patch_meta.json"

# GitCode（中国大陆优先 fallback）
GITCODE_FILE_BASE = "https://gitcode.com/Jamesphotography/SuperPicky/-/package_files/generic/release"

# 北京镜像服务器默认地址（兜底）
# ⚠️  不在模块级求值，改为函数内延迟读取，避免 import 时强制拉起 config 初始化链
_MIRROR_BASE_URL_DEFAULT = "http://1.119.150.179:59080/superpicky"


def _mirror_base_url() -> str:
    """延迟读取镜像地址，优先使用 config 覆盖值，避免模块级副作用。"""
    try:
        from config import config as _cfg
        return _cfg.endpoints.MIRROR_BASE_URL
    except Exception:
        return _MIRROR_BASE_URL_DEFAULT


def _get_app_data_dir() -> Path:
    """返回 SuperPicky 用户数据目录（跨平台）"""
    return get_app_config_dir()


def get_patch_dir() -> Path:
    """返回补丁解压目录，注入 sys.path 时使用"""
    return shared_get_patch_dir()


def get_patch_runtime_channel() -> str:
    """返回当前运行环境的发布渠道。"""
    try:
        from core.build_info import RELEASE_CHANNEL

        if RELEASE_CHANNEL in ("official", "nightly"):
            return RELEASE_CHANNEL
    except Exception:
        pass
    return "dev"


# === 4.3.0 起：在线补丁系统整体暂停，仅保留安装器（installer）更新流程 ===
# 背景见 issue #100：补丁覆盖层（sys.path 注入 code_updates/）在跨版本升级后
# 会以旧代码覆盖新代码，且与 installer_updater 形成两套并存的更新机制。
# 暂停而非删除：保留下载/应用/清理等函数结构，未来恢复时将开关置回 False 即可。
# Online patch system is suspended since 4.3.0; only the installer flow remains.
# Set this flag back to False to re-enable the online patch overlay.
PATCH_SYSTEM_SUSPENDED = True
_PATCH_SUSPENDED_REASON = "在线补丁系统自 4.3.0 起暂停，已改用安装器更新流程"


def get_patch_runtime_block_reason() -> Optional[str]:
    """返回当前环境禁止在线补丁的原因；允许时返回 None。"""
    if PATCH_SYSTEM_SUSPENDED:
        return _PATCH_SUSPENDED_REASON

    if not getattr(sys, "frozen", False):
        return "源码运行环境禁用在线补丁"

    channel = get_patch_runtime_channel()
    if channel not in ("nightly", "official"):
        return f"{channel} 渠道禁用在线补丁"

    return None


def _normalize_patch_channels(meta: dict) -> set[str]:
    channels: set[str] = set()

    for key in ("target_channels", "channels"):
        value = meta.get(key)
        if isinstance(value, list):
            channels.update(
                str(item).strip().lower()
                for item in value
                if str(item).strip()
            )

    for key in ("target_channel", "channel", "release_channel"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            channels.add(value.strip().lower())

    return channels


def validate_patch_metadata(meta: dict, current_app_version: str) -> Tuple[bool, str]:
    """校验当前运行环境与补丁元数据是否允许应用。"""
    blocked_reason = get_patch_runtime_block_reason()
    if blocked_reason:
        return False, blocked_reason

    if not isinstance(meta, dict):
        return False, "补丁元数据格式无效"

    base_version = str(meta.get("base_version", "")).strip()
    if not base_version:
        return False, "补丁元数据缺少 base_version"
    if base_version != current_app_version:
        return False, f"补丁 base_version={base_version} 与当前版本 {current_app_version} 不匹配"

    patch_version = str(meta.get("patch_version", "")).strip()
    if not patch_version:
        return False, "补丁元数据缺少 patch_version"

    target_channels = _normalize_patch_channels(meta)
    current_channel = get_patch_runtime_channel()
    if target_channels and current_channel not in target_channels:
        return False, f"补丁渠道限制为 {sorted(target_channels)}，当前渠道为 {current_channel}"

    return True, "ok"


def _get_local_meta_path() -> Path:
    return _get_app_data_dir() / "patch_meta.json"


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """GET 请求并解析 JSON，失败返回 None"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-PatchManager"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _find_patch_meta_url(assets: list) -> Optional[str]:
    """在 Release assets 中找到 patch_meta.json 的下载链接"""
    for asset in assets:
        if asset.get("name", "") == PATCH_META_FILENAME:
            return asset.get("browser_download_url")
    return None


def _find_patch_zip_url(assets: list, patch_version: str) -> Optional[str]:
    """在 Release assets 中找到对应版本的 patch zip 下载链接"""
    target = f"code_patch_{patch_version}.zip"
    for asset in assets:
        if asset.get("name", "") == target:
            return asset.get("browser_download_url")
    # 兜底：找任意 code_patch_*.zip
    for asset in assets:
        name = asset.get("name", "")
        if name.startswith("code_patch_") and name.endswith(".zip"):
            return asset.get("browser_download_url")
    return None


def read_local_meta() -> Optional[Dict]:
    """读取本地 patch_meta.json，不存在返回 None"""
    path = _get_local_meta_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_local_meta(meta: dict) -> None:
    path = _get_local_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_update_temp_dir() -> Path:
    """返回补丁下载临时目录（%TEMP%\\superpickyupdate 或 /tmp/superpickyupdate）"""
    base = Path(tempfile.gettempdir())
    tmp_dir = base / "superpickyupdate"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def _download_to_temp(url: str, timeout: int = 60) -> Optional[Path]:
    """下载文件到临时路径，返回临时文件 Path，失败返回 None"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SuperPicky-PatchManager"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            suffix = Path(url).suffix or ".tmp"
            tmp_dir = _get_update_temp_dir()
            # 使用固定目录 + 随机文件名，Windows 用户可在任务管理器/资源管理器看到
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=tmp_dir
            ) as f:
                shutil.copyfileobj(resp, f)
                return Path(f.name)
    except Exception:
        return None


def _make_path_writable(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def _remove_tree_safely(path: Path) -> None:
    def _onerror(func, target, _exc_info):
        _make_path_writable(target)
        func(target)

    shutil.rmtree(path, onerror=_onerror)


def safe_clear_patch() -> Tuple[bool, str]:
    """安全清理补丁目录与本地元数据。"""
    patch_dir = get_patch_dir()
    meta_path = _get_local_meta_path()

    try:
        if patch_dir.exists():
            _remove_tree_safely(patch_dir)
        if meta_path.exists():
            _make_path_writable(str(meta_path))
            meta_path.unlink(missing_ok=True)
        return True, "补丁环境已清除"
    except Exception as exc:
        return False, f"补丁环境清理失败: {exc}"


def apply_patch_file(zip_path: Path, meta: dict) -> bool:
    """
    解压 zip 到 code_updates/ 目录并写入 patch_meta.json。

    Args:
        zip_path: 本地 zip 文件路径（可以是下载的临时文件或本地测试文件）
        meta: 要写入的 patch_meta 字典

    Returns:
        True 表示成功，False 表示失败
    """
    patch_dir = get_patch_dir()
    try:
        from constants import APP_VERSION

        valid, reason = validate_patch_metadata(meta, APP_VERSION)
        if not valid:
            print(f"[PatchManager] 已拒绝应用补丁: {reason}")
            return False

        # 先清空旧补丁
        if patch_dir.exists():
            _remove_tree_safely(patch_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(patch_dir)

        _write_local_meta(meta)
        return True
    except Exception as e:
        print(f"[PatchManager] 解压失败: {e}")
        # 解压失败时清理残留
        if patch_dir.exists():
            shutil.rmtree(patch_dir, ignore_errors=True)
        return False


def clear_patch() -> None:
    """清除当前补丁（回滚到内置版本）"""
    _success, message = safe_clear_patch()
    print(f"[PatchManager] {message}")


def check_and_apply_patch_from_gitcode(
    gitcode_links: list,
    current_app_version: str,
    timeout: int = 30,
) -> Tuple[bool, str]:
    """
    从 GitCode release asset links 检查并应用补丁（GitHub API 不可达时使用）。

    Args:
        gitcode_links: GitCode release assets.links 列表（每项含 name + url）
        current_app_version: 当前应用版本号
        timeout: 超时秒数

    Returns:
        (patched, message)
    """
    # 找 patch_meta.json 链接
    meta_url = None
    for link in gitcode_links:
        if link.get("name", "") == PATCH_META_FILENAME:
            meta_url = link.get("url")
            break
    if not meta_url:
        return False, "GitCode release 中没有 patch_meta.json"

    remote_meta = _fetch_json(meta_url, timeout=10)
    if not remote_meta:
        return False, "拉取 GitCode patch_meta.json 失败"

    valid, reason = validate_patch_metadata(remote_meta, current_app_version)
    if not valid:
        return False, reason

    remote_patch_version = remote_meta.get("patch_version", "")
    local_meta = read_local_meta()
    local_patch_version = local_meta.get("patch_version", "") if local_meta else ""

    if remote_patch_version == local_patch_version:
        return False, f"补丁已是最新（{local_patch_version}）"

    # 找 zip 链接
    zip_url = None
    target = f"code_patch_{remote_patch_version}.zip"
    for link in gitcode_links:
        if link.get("name", "") == target:
            zip_url = link.get("url")
            break

    print(f"[PatchManager] 从 GitCode 下载补丁 {remote_patch_version} ...")
    tmp_path = _download_to_temp(zip_url, timeout=timeout) if zip_url else None
    if not tmp_path:
        mirror_zip_url = f"{_mirror_base_url()}/code_patch_{remote_patch_version}.zip"
        print(f"[PatchManager] GitCode CDN 失败，尝试北京镜像: {mirror_zip_url}")
        tmp_path = _download_to_temp(mirror_zip_url, timeout=timeout)
    if not tmp_path:
        return False, "补丁 zip 下载失败（GitCode + 北京镜像均不可用）"

    try:
        success = apply_patch_file(tmp_path, remote_meta)
    finally:
        tmp_path.unlink(missing_ok=True)

    if success:
        return True, f"补丁 {remote_patch_version} 已从 GitCode 应用"
    else:
        return False, "补丁解压失败"


def check_and_apply_patch_from_mirror(
    current_app_version: str,
    timeout: int = 30,
) -> Tuple[bool, str]:
    """
    完全从镜像服务器检查并应用补丁（GitHub 不可达时使用）。

    Args:
        current_app_version: 当前应用版本号
        timeout: 超时秒数

    Returns:
        (patched, message)
    """
    meta_url = f"{_mirror_base_url()}/patch_meta.json"
    remote_meta = _fetch_json(meta_url, timeout=10)
    if not remote_meta:
        return False, "镜像服务器不可用"

    valid, reason = validate_patch_metadata(remote_meta, current_app_version)
    if not valid:
        return False, reason

    remote_patch_version = remote_meta.get("patch_version", "")
    local_meta = read_local_meta()
    local_patch_version = local_meta.get("patch_version", "") if local_meta else ""

    if remote_patch_version == local_patch_version:
        return False, f"补丁已是最新（{local_patch_version}）"

    zip_url = f"{_mirror_base_url()}/code_patch_{remote_patch_version}.zip"
    print(f"[PatchManager] 从镜像下载补丁 {remote_patch_version} ...")
    tmp_path = _download_to_temp(zip_url, timeout=timeout)
    if not tmp_path:
        return False, "镜像补丁 zip 下载失败"

    try:
        success = apply_patch_file(tmp_path, remote_meta)
    finally:
        tmp_path.unlink(missing_ok=True)

    if success:
        return True, f"补丁 {remote_patch_version} 已从镜像应用"
    else:
        return False, "补丁解压失败"


def check_and_apply_patch(
    release_assets: list,
    current_app_version: str,
    timeout: int = 30,
) -> Tuple[bool, str]:
    """
    检查 Release assets 中是否有可用补丁，有则下载并应用。

    Args:
        release_assets: GitHub Release 的 assets 列表（已由 update_checker 获取）
        current_app_version: 当前应用版本号（如 "4.2.5"）
        timeout: 下载超时秒数

    Returns:
        (patched, message)
        patched=True 表示成功应用了新补丁
    """
    # 1. 找到 patch_meta.json 的下载链接
    meta_url = _find_patch_meta_url(release_assets)
    if not meta_url:
        return False, "Release 中没有 patch_meta.json"

    # 2. 拉取远端 patch_meta.json
    remote_meta = _fetch_json(meta_url, timeout=10)
    if not remote_meta:
        return False, "拉取 patch_meta.json 失败"

    # 3. 检查 base_version 是否匹配当前应用版本
    valid, reason = validate_patch_metadata(remote_meta, current_app_version)
    if not valid:
        return False, reason

    # 4. 对比本地补丁版本
    remote_patch_version = remote_meta.get("patch_version", "")
    local_meta = read_local_meta()
    local_patch_version = local_meta.get("patch_version", "") if local_meta else ""

    if remote_patch_version == local_patch_version:
        return False, f"补丁已是最新（{local_patch_version}）"

    # 5. 找到 zip 下载链接
    zip_url = _find_patch_zip_url(release_assets, remote_patch_version)
    if not zip_url:
        return False, f"Release 中找不到 code_patch_{remote_patch_version}.zip"

    # 6. 下载 zip（三级降级：GitHub CDN → 北京镜像 → GitCode）
    print(f"[PatchManager] 下载补丁 {remote_patch_version} ...")
    tmp_path = _download_to_temp(zip_url, timeout=timeout)
    if not tmp_path:
        mirror_zip_url = f"{_mirror_base_url()}/code_patch_{remote_patch_version}.zip"
        print(f"[PatchManager] GitHub CDN 失败，尝试北京镜像: {mirror_zip_url}")
        tmp_path = _download_to_temp(mirror_zip_url, timeout=timeout)
    if not tmp_path:
        gitcode_zip_url = f"{GITCODE_FILE_BASE}/{remote_patch_version}/code_patch_{remote_patch_version}.zip"
        print(f"[PatchManager] 北京镜像失败，尝试 GitCode: {gitcode_zip_url}")
        tmp_path = _download_to_temp(gitcode_zip_url, timeout=timeout)
    if not tmp_path:
        return False, "补丁 zip 下载失败（GitHub + 北京镜像 + GitCode 均不可用）"

    # 7. 解压并写入 meta
    try:
        success = apply_patch_file(tmp_path, remote_meta)
    finally:
        tmp_path.unlink(missing_ok=True)

    if success:
        return True, f"补丁 {remote_patch_version} 已应用"
    else:
        return False, "补丁解压失败"


# ── 独立运行测试 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SuperPicky PatchManager 测试工具")
    sub = parser.add_subparsers(dest="cmd")

    # 本地 zip 测试
    p_local = sub.add_parser("apply-local", help="应用本地 zip 文件（测试用）")
    p_local.add_argument("zip_path", help="本地 zip 文件路径")
    p_local.add_argument("--patch-version", default="test-patch", help="写入 meta 的 patch_version")

    # 清除补丁
    sub.add_parser("clear", help="清除当前补丁")

    # 查看本地 meta
    sub.add_parser("status", help="查看当前补丁状态")

    args = parser.parse_args()

    if args.cmd == "apply-local":
        from constants import APP_VERSION
        meta = {
            "patch_version": args.patch_version,
            "base_version": APP_VERSION,
            "applied_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        ok = apply_patch_file(Path(args.zip_path), meta)
        print(f"[PatchManager] {'成功' if ok else '失败'}，patch_dir={get_patch_dir()}")

    elif args.cmd == "clear":
        clear_patch()

    elif args.cmd == "status":
        meta = read_local_meta()
        if meta:
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        else:
            print("无本地补丁")

    else:
        parser.print_help()
