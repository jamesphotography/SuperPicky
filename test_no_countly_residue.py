#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确认 Countly 残留已清除。

留着失效的注入步骤会让下一个人以为遥测仍走 Countly——正是这次
「配置齐全但数据为零」的误判来源。

Guard against Countly residue: stale wiring is what made the dead endpoint
go unnoticed for months.
"""
from pathlib import Path

ROOT = Path(__file__).parent


def test_workflow_has_no_countly_secrets() -> None:
    """CI 不再注入 Countly 凭据。/ No Countly secrets in CI."""
    workflow = (ROOT / ".github/workflows/build-release.yml").read_text(encoding="utf-8")
    assert "COUNTLY_APP_KEY" not in workflow
    assert "COUNTLY_SERVER_URL" not in workflow
    assert "prepare_telemetry_build" not in workflow


def test_prepare_script_removed() -> None:
    """注入脚本已删除。/ Injection script removed."""
    assert not (ROOT / "scripts/prepare_telemetry_build.py").exists()


def test_telemetry_endpoint_and_no_functional_countly_residue() -> None:
    """
    遥测端点确实归属自建服务，且模块不再有**功能性**的 Countly 残留。

    正面断言（更重要）：直接检查 `_TELEMETRY_ENDPOINT` 的值——这是我们真正
    关心的事实，即端点指向自建服务而非第三方。任何人把它改回 Countly 或
    别的第三方地址，这条立刻红，比在源码里搜字符串更直接、更抗改写。

    反面断言：源码不含 Countly 凭据变量名（`COUNTLY_`）或已删模块的引用
    （`_telemetry_build`）。

    刻意不查字面词「countly」或域名 `countly.com`：telemetry.py 的端点常量
    旁保留了一段说明「原 Countly Flex 域名已失效」的注释（逐字对应
    Task 8 提交时的原文），那正处在后人最可能「把端点改回去」的那一行，
    是防止重蹈覆辙的关键信息，不能为了让断言更严格而删掉或改写它。

    The positive assertion (the one that matters) checks the endpoint's
    actual value — the fact we care about is that it points at our own
    service, not a string-search proxy for that fact. The negative
    assertions guard against Countly credentials or references to the
    removed `_telemetry_build` module. The literal word "countly" (and the
    domain "countly.com") is deliberately not checked: the historical
    comment next to the endpoint constant explains why it moved, and sits
    exactly where someone would be tempted to move it back.
    """
    from app_user_stat.telemetry import _TELEMETRY_ENDPOINT

    assert _TELEMETRY_ENDPOINT == "https://superpicky.app/t"

    source = (ROOT / "app_user_stat/telemetry.py").read_text(encoding="utf-8")
    assert "COUNTLY_" not in source
    assert "_telemetry_build" not in source
