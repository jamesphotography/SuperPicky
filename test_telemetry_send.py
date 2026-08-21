#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遥测投递层测试：按日轮换 ID 与上报内容契约。

Telemetry delivery tests: daily-rotating ID and payload contract.
"""
import re

from app_user_stat.telemetry import _build_request_payload, _daily_rotating_id


def test_daily_id_is_sha256_hex() -> None:
    """上报 ID 为 64 位十六进制，与 Worker 的校验正则一致。/ 64-hex id."""
    value = _daily_rotating_id("install-abc", "2026-08-21")
    assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_daily_id_is_stable_within_a_day() -> None:
    """同一天内稳定，否则算不出去重日活。/ Stable within one day."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-21")
    assert a == b


def test_daily_id_rotates_across_days() -> None:
    """跨日必须变化，这是「不构成持久标识符」的关键。/ Rotates daily."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-abc", "2026-08-22")
    assert a != b


def test_daily_id_differs_between_installs() -> None:
    """不同安装不得碰撞，否则日活会被低估。/ Distinct per install."""
    a = _daily_rotating_id("install-abc", "2026-08-21")
    b = _daily_rotating_id("install-xyz", "2026-08-21")
    assert a != b


def test_payload_never_contains_the_local_install_id() -> None:
    """
    本地安装 ID 永不上报——这是整个匿名方案的立足点。

    The local install id must never appear in the payload.
    """
    payload = _build_request_payload("install-secret-value", ["app_start"])
    assert "install-secret-value" not in str(payload)


def test_payload_matches_worker_contract() -> None:
    """字段与 Worker 的 validatePayload 白名单一致。/ Matches /t contract."""
    payload = _build_request_payload("install-abc", ["install", "app_start"])
    assert payload["v"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["id"])
    assert payload["events"] == ["install", "app_start"]
    for key in ("app_version", "os", "arch", "python_version", "locale"):
        assert isinstance(payload[key], str) and payload[key]


def test_on_ready_fires_even_when_telemetry_disabled(monkeypatch) -> None:
    """
    遥测关闭时 on_ready 仍须触发，否则 onboarding 永不出现且无报错。

    on_ready must fire even when telemetry is off, or onboarding never shows.
    """
    import app_user_stat.telemetry as tm

    class _Off:
        telemetry_enabled = False

    monkeypatch.setattr(tm, "_schedule_on_qt_event_loop", lambda fn: False)
    monkeypatch.setitem(__import__("sys").modules, "advanced_config",
                        type("M", (), {"AdvancedConfig": lambda: _Off()}))
    tm._BOOTSTRAPPED = False

    fired = []
    tm.bootstrap_telemetry(parent=None, on_ready=lambda: fired.append(True))
    assert fired == [True]


def test_unreachable_endpoint_never_raises(monkeypatch) -> None:
    """
    端点不可达时不得抛异常——这正是 Countly 域名失效时的处境。

    当年那个死域名之所以数月无人察觉，是因为异常全被吞掉；吞异常本身是
    对的（统计不能拖垮启动），本测试守住这个行为不被「改成抛错好排查」。

    An unreachable endpoint must never raise: swallowing is correct here,
    since telemetry must never break startup.
    """
    import app_user_stat.telemetry as tm

    def _boom(*args, **kwargs):
        raise OSError("Could not resolve host")

    monkeypatch.setattr(tm.request, "urlopen", _boom)
    client = tm._TelemetryClient()
    state = {"device_id": "install-abc", "install_reported_at": None, "last_heartbeat_at": None}
    # 不应抛出任何异常
    client._send_due_events(state, ["app_start"])
