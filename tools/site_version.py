#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
官网发布版本查询（用户主动触发，只读）。

本模块只做一件事：读取官网的发布清单 downloads_github.json，判断官网是否有比
本地更新的版本，供设置中心「关于」页在用户点击时展示。

与 tools/update_checker 的关系（重要）：
    update_checker 的 ONLINE_UPDATE_CHECK_DISABLED 关闭的是「自动的、后台的」
    版本检测与应用内升级流程（原因见该模块注释：CUDA 包 >2GB 无法进 GitHub
    Release、Full/Lite 的 inno AppId 不同会脏覆盖）。本模块**不受该开关约束**，
    因为它语义完全不同：
      - 仅在用户明确点击时发起，绝不在启动或后台自动运行；
      - 只读一个静态 JSON，不碰 GitHub Release API；
      - 只显示文字提示并引导到官网，不下载、不安装、不改动任何本地文件。
    因此它不会重新引入被停用的那三类风险。

Site release version lookup (user-initiated, read-only).

This module reads the site's downloads_github.json manifest and reports whether
a newer version exists, for display in the Settings Center About page.

Relationship with tools/update_checker (important): that module's
ONLINE_UPDATE_CHECK_DISABLED kill switch disables *automatic, background*
version checks and the in-app upgrade flow. This module is deliberately NOT
gated by it, because it only runs on an explicit user click, only reads a static
JSON file, and never downloads or installs anything — so it cannot reintroduce
the risks that switch was created to prevent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

HTTP_TIMEOUT_SECONDS = 8
_USER_AGENT = "SuperPicky-SiteVersion/1.0"

# 网络读取函数签名：(url, timeout) -> 响应文本
# Fetcher signature: (url, timeout) -> response text
Fetcher = Callable[[str, int], str]


@dataclass(frozen=True)
class SiteVersionResult:
    """
    官网版本查询结果。

    参数:
    has_update: 官网是否存在比本地更新的版本。
    latest_version: 官网最新版本号（已去掉 v 前缀）；查询失败时为 None。
    error: 失败原因；成功时为 None。

    Site version lookup result.

    Parameters:
    has_update: Whether the site offers a version newer than the local build.
    latest_version: Latest site version without the leading v; None on failure.
    error: Failure reason, or None on success.
    """

    has_update: bool
    latest_version: Optional[str]
    error: Optional[str]


def _normalize_version(value: str) -> str:
    """
    去掉版本号的前导 v，便于展示与比较。

    参数:
    value: 原始版本文本，如 ``v4.6.0``。

    返回:
    str: 去掉前导 v 并去除首尾空白的版本号。

    Strip a leading v from a version string for display and comparison.

    Parameters:
    value: Raw version text such as ``v4.6.0``.

    Return:
    str: Version without the leading v, trimmed.
    """

    text = (value or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    return text


def is_newer_version(latest_tag: str, current_version: str) -> bool:
    """
    判断官网版本是否比本地版本新。

    使用 packaging.version 做 PEP 440 比较，因此 ``4.6.0RC1`` 会被正确识别为
    ``4.6.0`` 的预发布版：它高于 ``4.5.0``，但低于正式的 ``4.6.0``。任何一侧
    无法解析时一律返回 False——宁可漏报也不误报，避免给用户假的升级提示。

    参数:
    latest_tag: 官网版本号，可带或不带 v 前缀。
    current_version: 本地版本号，通常来自 constants.APP_VERSION。

    返回:
    bool: 官网更新时返回 True，否则返回 False。

    Return whether the site version is newer than the local one.

    Comparison uses packaging.version (PEP 440), so ``4.6.0RC1`` is correctly
    treated as a pre-release of ``4.6.0``: above ``4.5.0`` but below the final
    ``4.6.0``. If either side fails to parse, this returns False — a missed
    notification is preferable to a false one.

    Parameters:
    latest_tag: Site version, with or without a leading v.
    current_version: Local version, normally constants.APP_VERSION.

    Return:
    bool: True when the site version is newer.
    """

    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return False

    try:
        latest = Version(_normalize_version(latest_tag))
        current = Version(_normalize_version(current_version))
    except (InvalidVersion, TypeError):
        return False

    return latest > current


def _default_fetcher(url: str, timeout: int) -> str:
    """
    默认的清单读取实现，返回 UTF-8 解码后的响应文本。

    参数:
    url: 清单地址。
    timeout: 超时秒数。

    返回:
    str: 响应正文。

    Raises:
    urllib.error.URLError: 网络层失败时抛出，由调用方统一捕获。

    Default manifest fetcher returning UTF-8 decoded response text.
    """

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _resolve_manifest_url() -> str:
    """
    读取配置中的清单地址，配置不可用时回退到官网默认地址。

    返回:
    str: 发布清单 URL。

    Resolve the manifest URL from config, falling back to the site default.
    """

    try:
        from config import config as _cfg

        url = getattr(_cfg.endpoints, "DOWNLOAD_MANIFEST_URL", "")
        if url:
            return str(url)
    except Exception:
        pass
    return "https://superpicky.app/downloads_github.json"


def check_site_version(
    current_version: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    timeout: int = HTTP_TIMEOUT_SECONDS,
) -> SiteVersionResult:
    """
    查询官网发布清单，判断是否存在更新版本。

    本函数不抛异常：网络失败、JSON 损坏、字段缺失都会转成带 error 的结果对象，
    并保证 has_update 为 False，绝不因异常而误报更新。

    参数:
    current_version: 本地版本号；为 None 时读取 constants.APP_VERSION。
    fetcher: 清单读取函数，便于测试注入替身；为 None 时走真实网络。
    timeout: 网络超时秒数。

    返回:
    SiteVersionResult: 查询结果。

    Query the site release manifest and report whether an update exists.

    This function never raises: network errors, malformed JSON and missing
    fields are all converted into a result carrying an error message, with
    has_update forced to False so a failure can never look like an update.

    Parameters:
    current_version: Local version; falls back to constants.APP_VERSION.
    fetcher: Manifest reader, injectable for tests; real network when None.
    timeout: Network timeout in seconds.

    Return:
    SiteVersionResult: The lookup result.
    """

    if current_version is None:
        try:
            from constants import APP_VERSION

            current_version = APP_VERSION
        except Exception as exc:
            return SiteVersionResult(False, None, f"cannot read local version: {exc}")

    read = fetcher or _default_fetcher
    url = _resolve_manifest_url()

    try:
        payload = read(url, timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return SiteVersionResult(False, None, str(exc))

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError) as exc:
        return SiteVersionResult(False, None, f"invalid manifest: {exc}")

    latest_block = data.get("latest") if isinstance(data, dict) else None
    if not isinstance(latest_block, dict):
        return SiteVersionResult(False, None, "manifest missing 'latest' section")

    tag = _normalize_version(str(latest_block.get("tag", "")))
    if not tag:
        return SiteVersionResult(False, None, "manifest missing 'latest.tag'")

    return SiteVersionResult(
        has_update=is_newer_version(tag, current_version),
        latest_version=tag,
        error=None,
    )
