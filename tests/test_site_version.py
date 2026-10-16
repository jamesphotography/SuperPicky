#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
官网版本查询（tools/site_version.py）的回归测试。

覆盖版本比较语义（含 RC 预发布）、清单解析容错与网络失败处理。
所有用例均不发起真实网络请求，改用注入的 fetcher 替身。

Regression tests for the site version check (tools/site_version.py).

Covers version comparison semantics (including RC pre-releases), manifest
parsing tolerance, and network failure handling. No test performs a real
network request; a fetcher stand-in is injected instead.
"""

from __future__ import annotations

import json

import pytest

from tools import site_version


# ── 版本比较 / Version comparison ────────────────────────────────────────────

@pytest.mark.parametrize(
    "latest_tag, current, expected",
    [
        # 官网比本地新 → 应提示 / Site newer than local -> notify
        ("v4.6.0", "4.5.0", True),
        ("v4.5.0", "4.3.0", True),
        # 本地是更高版本的 RC，官网还停在旧正式版 → 不应提示
        # Local is an RC of a higher version while the site still has the old
        # stable release -> must not notify.
        ("v4.5.0", "4.6.0RC1", False),
        # 完全相同 → 不提示 / Identical -> no notification
        ("v4.5.0", "4.5.0", False),
        # 正式版胜过同版本号的 RC / Stable beats its own RC
        ("v4.6.0", "4.6.0RC1", True),
        # 本地正式版高于官网 RC / Local stable above site RC
        ("v4.6.0RC1", "4.6.0", False),
        # 带不带 v 前缀都要能处理 / Tolerate missing v prefix
        ("4.6.0", "4.5.0", True),
    ],
)
def test_is_newer_version(latest_tag: str, current: str, expected: bool) -> None:
    """版本比较应正确处理 RC 预发布语义。/ Comparison must honor RC semantics."""

    assert site_version.is_newer_version(latest_tag, current) is expected


def test_is_newer_version_rejects_garbage() -> None:
    """无法解析的版本号一律视为“没有更新”，绝不误报。

    Unparsable versions must never be reported as an update.
    """

    assert site_version.is_newer_version("not-a-version", "4.5.0") is False
    assert site_version.is_newer_version("v4.6.0", "garbage") is False
    assert site_version.is_newer_version("", "4.5.0") is False


# ── 清单解析 / Manifest parsing ──────────────────────────────────────────────

def _manifest(tag: str = "v4.6.0") -> str:
    return json.dumps({"latest": {"tag": tag, "files": {"mac_arm64": "x.dmg"}}})


def test_check_site_version_reports_update() -> None:
    """官网版本较新时应返回 has_update=True 及版本号。"""

    result = site_version.check_site_version(
        current_version="4.5.0", fetcher=lambda url, timeout: _manifest("v4.6.0")
    )
    assert result.has_update is True
    assert result.latest_version == "4.6.0"
    assert result.error is None


def test_check_site_version_up_to_date() -> None:
    """本地已是最新时 has_update=False，但仍返回官网版本号供展示。"""

    result = site_version.check_site_version(
        current_version="4.6.0", fetcher=lambda url, timeout: _manifest("v4.6.0")
    )
    assert result.has_update is False
    assert result.latest_version == "4.6.0"
    assert result.error is None


def test_check_site_version_local_rc_newer_than_site() -> None:
    """本地 RC 比官网正式版新时不应提示更新（RC 测试者场景）。"""

    result = site_version.check_site_version(
        current_version="4.6.0RC1", fetcher=lambda url, timeout: _manifest("v4.5.0")
    )
    assert result.has_update is False
    assert result.latest_version == "4.5.0"


@pytest.mark.parametrize(
    "payload",
    [
        "{}",                          # 缺 latest / missing latest
        '{"latest": {}}',              # 缺 tag / missing tag
        '{"latest": {"tag": ""}}',     # tag 为空 / empty tag
        "not json at all",             # 非法 JSON / invalid JSON
        '{"latest": []}',              # 类型不符 / wrong type
    ],
)
def test_check_site_version_malformed_manifest(payload: str) -> None:
    """清单异常时必须返回错误而非抛异常，且不得误报有更新。

    A malformed manifest must yield an error result instead of raising, and
    must never be reported as an available update.
    """

    result = site_version.check_site_version(
        current_version="4.5.0", fetcher=lambda url, timeout: payload
    )
    assert result.has_update is False
    assert result.error is not None


def test_check_site_version_network_failure() -> None:
    """网络异常必须被捕获并转成错误结果，不能向上抛。"""

    def _boom(url: str, timeout: int) -> str:
        raise OSError("network unreachable")

    result = site_version.check_site_version(
        current_version="4.5.0", fetcher=_boom
    )
    assert result.has_update is False
    assert result.latest_version is None
    assert result.error is not None
    assert "network unreachable" in result.error


def test_check_site_version_uses_configured_url() -> None:
    """应请求 config 中配置的清单地址，而非硬编码地址。"""

    seen: dict[str, object] = {}

    def _capture(url: str, timeout: int) -> str:
        seen["url"] = url
        seen["timeout"] = timeout
        return _manifest("v4.5.0")

    site_version.check_site_version(current_version="4.5.0", fetcher=_capture)
    assert isinstance(seen["url"], str)
    assert seen["url"].endswith("downloads_github.json")
    assert isinstance(seen["timeout"], int)


def test_site_version_does_not_depend_on_update_checker_switch() -> None:
    """本模块是用户主动触发的查询，不受自动检测总开关约束。

    This module is user-initiated and must not be gated by the automatic
    update-check kill switch introduced in 4.3.0.
    """

    import tools.update_checker as update_checker

    assert update_checker.ONLINE_UPDATE_CHECK_DISABLED is True
    result = site_version.check_site_version(
        current_version="4.5.0", fetcher=lambda url, timeout: _manifest("v4.6.0")
    )
    assert result.has_update is True
