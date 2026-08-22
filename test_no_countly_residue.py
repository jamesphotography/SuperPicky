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


def test_telemetry_module_has_no_functional_countly_residue() -> None:
    """
    遥测模块不再有**功能性**的 Countly 残留。

    刻意不查字面词「countly」：telemetry.py 的端点常量旁保留了一段说明
    「原 Countly Flex 域名已失效」的注释，那正处在后人最可能「把端点改回去」
    的那一行，是防止重蹈覆辙的关键信息。本用例要防的是凭据、URL 与配置变量。

    No *functional* Countly residue. The literal word is allowed: the comment
    explaining why the endpoint moved sits exactly where someone would be
    tempted to move it back.
    """
    source = (ROOT / "app_user_stat/telemetry.py").read_text(encoding="utf-8")
    assert "COUNTLY_" not in source
    assert "countly.com" not in source
    assert "_telemetry_build" not in source
