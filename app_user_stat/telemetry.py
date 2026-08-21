#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky 遥测投递层：自建端点、按日轮换匿名 ID。

本模块负责：
- 是否上报由 advanced_config.telemetry_enabled 单一开关控制
- 匿名安装 ID 的本地持久化（永不上报，仅用于派生当日 ID）
- 事件节流（install 只发一次，heartbeat 每 7 天一次）与状态存储
- 向自建 POST /t 端点投递 JSON

Telemetry delivery layer for SuperPicky: self-hosted endpoint, daily-
rotating anonymous id.

This module owns:
- the single on/off switch, advanced_config.telemetry_enabled
- local persistence of the anonymous install id (never transmitted; used
  only to derive the day's reporting id)
- event throttling (install fires once, heartbeat every 7 days) and state
  storage
- delivery of a JSON payload to the self-hosted POST /t endpoint
"""

from __future__ import annotations

import hashlib
import json
import locale
import os
import platform
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import request

from constants import APP_VERSION

# 自建端点。原 Countly Flex 实例（superpicky-*.flex.countly.com）的域名
# 已不存在，数月来所有打包版都在向它静默投递失败、零数据落地。
# 域名属第三方、拿不回来，故已发布版本的数据永久缺失，无兼容负担。
# Self-hosted endpoint; the former Countly Flex host no longer resolves.
_TELEMETRY_ENDPOINT = "https://superpicky.app/t"
_PAYLOAD_VERSION = 1

_REQUEST_TIMEOUT_SECONDS = 1.5
_HEARTBEAT_INTERVAL = timedelta(days=7)
_STATE_FILE_NAME = "telemetry_state.json"
_STATE_SCHEMA_VERSION = 1
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED = False


def bootstrap_telemetry(parent: Any = None, on_ready: Optional[Callable[[], None]] = None) -> None:
    """
    初始化遥测并立即返回，幂等（`_BOOTSTRAPPED` 锁保证只跑一次）。

    是否上报只看 advanced_config.telemetry_enabled 这一个开关；实际网络
    投递发生在守护线程上，启动流程不会被 HTTP I/O 阻塞。所有异常均被吞掉。

    Initialize telemetry once and return immediately (idempotent via the
    `_BOOTSTRAPPED` lock). Delivery is gated solely by
    advanced_config.telemetry_enabled; actual network I/O happens on a
    daemon thread so app startup is never blocked. All failures are
    intentionally swallowed.
    """
    global _BOOTSTRAPPED

    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        _BOOTSTRAPPED = True

    try:
        runner = _TelemetryBootstrap(parent=parent, on_ready=on_ready)
        if _schedule_on_qt_event_loop(runner.run):
            return
        runner.run()
    except Exception as exc:
        _debug_log(f"bootstrap failed: {exc}")
        _invoke_callback(on_ready)


class _TelemetryBootstrap:
    """
    启动期遥测流程：读取开关判定是否投递，并保证 on_ready 回调必然触发。

    Startup telemetry sequence: gate delivery on the settings switch, and
    guarantee the on_ready callback always fires.
    """

    def __init__(self, parent: Any, on_ready: Optional[Callable[[], None]]) -> None:
        self._parent = parent
        self._on_ready = on_ready

    def run(self) -> None:
        try:
            from advanced_config import AdvancedConfig

            if not AdvancedConfig().telemetry_enabled:
                _debug_log("telemetry skipped: disabled in settings")
                return

            _TelemetryClient().bootstrap()
        finally:
            # 这个 try/finally 必须原样保留。启动期弹窗（onboarding）挂在
            # on_ready 上（main.py:253），任何提前 return 都必须仍然触发它，
            # 否则新用户永远看不到 onboarding 且没有任何报错。
            # Keep this try/finally: onboarding hangs off on_ready.
            _invoke_callback(self._on_ready)


class _TelemetryClient:
    """
    匿名启动遥测客户端，投递到自建 POST /t 端点。

    Anonymous startup telemetry client, delivering to the self-hosted
    POST /t endpoint.
    """

    def __init__(self) -> None:
        self._config_dir = _get_config_dir()
        self._state_path = self._config_dir / _STATE_FILE_NAME

    def bootstrap(self) -> None:
        state = _load_or_create_state(self._state_path)
        planned_events = self._build_due_events(state)

        if not planned_events:
            _debug_log("telemetry skipped: no due events")
            return

        worker = threading.Thread(
            target=self._send_due_events,
            args=(state, planned_events),
            name="superpicky-telemetry",
            daemon=True,
        )
        worker.start()

    def build_self_test_report(self) -> Dict[str, Any]:
        state = _load_or_create_state(self._state_path)
        events = self._build_due_events(state)
        payload = _build_request_payload(state["device_id"], events) if events else None
        return {
            "app_version": APP_VERSION,
            "endpoint_url": _TELEMETRY_ENDPOINT,
            "state_path": str(self._state_path),
            "device_id": state["device_id"],
            "due_events": events,
            "payload_preview": payload,
        }

    def send_blocking_for_self_test(self) -> bool:
        state = _load_or_create_state(self._state_path)
        events = self._build_due_events(state)
        if not events:
            _debug_log("self-test send skipped: no due events")
            return True
        return self._send_due_events(state, events)

    def _build_due_events(self, state: Dict[str, Any]) -> List[str]:
        events: List[str] = []

        if not state.get("install_reported_at"):
            events.append("install")

        events.append("app_start")

        last_heartbeat_at = _parse_iso8601(state.get("last_heartbeat_at"))
        now = datetime.now(timezone.utc)
        if last_heartbeat_at is None or (now - last_heartbeat_at) >= _HEARTBEAT_INTERVAL:
            events.append("heartbeat_weekly")

        return events

    def _send_due_events(self, state: Dict[str, Any], events: List[str]) -> bool:
        """
        投递本次到期事件；网络与解析异常全部吞掉，绝不向上抛出。

        Deliver the due events for this run; all network/parse errors are
        swallowed so telemetry can never break startup.
        """
        payload = _build_request_payload(state["device_id"], events)

        try:
            body = json.dumps(payload).encode("utf-8")
            request_obj = request.Request(
                _TELEMETRY_ENDPOINT,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(request_obj, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                _debug_log(f"telemetry delivered: status={response.status}")
                if not (200 <= response.status < 300):
                    return False
        except Exception as exc:
            _debug_log(f"telemetry delivery failed: {exc}")
            return False

        now = _utc_now_iso8601()
        changed = False

        if "install" in events and not state.get("install_reported_at"):
            state["install_reported_at"] = now
            changed = True

        if "heartbeat_weekly" in events:
            state["last_heartbeat_at"] = now
            changed = True

        if changed:
            _save_json(self._state_path, state)

        return True


def _get_config_dir() -> Path:
    """Match the existing AdvancedConfig storage location."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SuperPicky"
    if sys.platform == "win32":
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "SuperPicky"

        user_profile = os.getenv("USERPROFILE")
        if user_profile:
            return Path(user_profile) / "AppData" / "Local" / "SuperPicky"

        return Path.home() / "AppData" / "Local" / "SuperPicky"
    return Path.home() / ".config" / "SuperPicky"


def _default_state() -> Dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA_VERSION,
        "device_id": uuid.uuid4().hex,
        "install_reported_at": None,
        "last_heartbeat_at": None,
    }


def _load_or_create_state(state_path: Path) -> Dict[str, Any]:
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if not state_path.exists():
        state = _default_state()
        _save_json(state_path, state)
        return state

    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception as exc:
        _debug_log(f"state load failed, regenerating: {exc}")
        state = _default_state()
        _save_json(state_path, state)
        return state

    changed = False
    if not isinstance(state, dict):
        state = _default_state()
        changed = True

    if not state.get("device_id"):
        state["device_id"] = uuid.uuid4().hex
        changed = True

    if state.get("schema_version") != _STATE_SCHEMA_VERSION:
        state["schema_version"] = _STATE_SCHEMA_VERSION
        changed = True

    if "install_reported_at" not in state:
        state["install_reported_at"] = None
        changed = True

    if "last_heartbeat_at" not in state:
        state["last_heartbeat_at"] = None
        changed = True

    if changed:
        _save_json(state_path, state)

    return state


def _save_json(target_path: Path, payload: Dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, target_path)


def _daily_rotating_id(install_id: str, day: str) -> str:
    """
    由本地安装 ID 与日期派生出当日的上报 ID。

    参数:
    install_id (str): 本地安装 ID，仅存于 telemetry_state.json，永不上报。
    day (str): UTC 日期，格式 YYYY-MM-DD。

    返回:
    str: 64 位十六进制的 sha256 摘要。

    同一天内稳定（否则算不出去重日活），跨日必变（故不构成持久标识符，
    这正是「默认开启」得以成立的前提）。代价是算不了留存。

    Derive the day's reporting id from the local install id and the date.
    Stable within a day, rotates across days, so it is not a persistent
    identifier. The trade-off is that retention cannot be computed.

    Parameters:
    install_id (str): Local-only install id, never transmitted.
    day (str): UTC date as YYYY-MM-DD.

    Return:
    str: 64-char lowercase sha256 hex digest.
    """
    return hashlib.sha256(f"{install_id}:{day}".encode("utf-8")).hexdigest()


def _build_request_payload(install_id: str, events: List[str]) -> Dict[str, Any]:
    """
    构造 POST /t 的上报内容。

    参数:
    install_id (str): 本地安装 ID，只用于派生当日 ID，不进入返回值。
    events (List[str]): 事件名列表，取值须在 Worker 的白名单内
                        （install / app_start / heartbeat_weekly）。

    返回:
    Dict[str, Any]: 与 Worker 端 validatePayload 契约一致的字典。

    Build the POST /t payload. install_id is used only to derive the daily id
    and never appears in the result.

    Parameters:
    install_id (str): Local-only install id.
    events (List[str]): Event keys from the Worker's allow-list.

    Return:
    Dict[str, Any]: Payload matching the Worker's validatePayload contract.
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    common = _build_common_fields()
    return {
        "v": _PAYLOAD_VERSION,
        "id": _daily_rotating_id(install_id, day),
        "app_version": common["app_version"],
        "os": common["os"],
        "arch": common["arch"],
        "python_version": common["python_version"],
        "locale": common["locale"],
        "events": list(events),
    }


def _build_common_fields() -> Dict[str, str]:
    return {
        "app_version": APP_VERSION,
        "os": platform.system() or "unknown",
        "arch": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "locale": _detect_locale(),
    }


def _schedule_on_qt_event_loop(callback: Callable[[], None]) -> bool:
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            return False
        QTimer.singleShot(0, callback)
        return True
    except Exception:
        return False


def _invoke_callback(callback: Optional[Callable[[], None]]) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception as exc:
        _debug_log(f"startup callback failed: {exc}")


def _parse_bool(raw_value: Optional[str], default: bool) -> bool:
    value = (raw_value or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _detect_locale() -> str:
    lang, encoding = locale.getlocale()
    if lang and encoding:
        return f"{lang}.{encoding}"
    if lang:
        return lang

    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.getenv(env_key)
        if value:
            return value

    return "unknown"


def _parse_iso8601(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        if raw_value.endswith("Z"):
            raw_value = raw_value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw_value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _debug_log(message: str) -> None:
    if not _parse_bool(os.getenv("TELEMETRY_DEBUG"), default=False):
        return
    try:
        print(f"[telemetry] {message}")
    except Exception:
        pass


def _run_self_test(send: bool = False) -> int:
    """
    命令行自检：打印端点、待发事件与 payload 预览，不含本地安装 ID。

    CLI self-test: print the endpoint, due events, and a payload preview
    that never includes the local install id.
    """
    client = _TelemetryClient()
    report = client.build_self_test_report()

    print("SuperPicky telemetry self-test")
    print(f"app_version={report['app_version']}")
    print(f"endpoint_url={report['endpoint_url']}")
    print(f"state_path={report['state_path']}")
    print(f"device_id={report['device_id']}  # 本地安装 ID，仅本机诊断用，从不上报")
    print(f"due_events={','.join(report['due_events']) if report['due_events'] else '(none)'}")

    payload_preview = report.get("payload_preview")
    if payload_preview:
        print("payload_preview=")
        print(json.dumps(payload_preview, indent=2, ensure_ascii=False))
    else:
        print("payload_preview=(none)")

    if send:
        ok = client.send_blocking_for_self_test()
        print(f"send_result={'ok' if ok else 'failed'}")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_run_self_test(send="--send" in sys.argv))
