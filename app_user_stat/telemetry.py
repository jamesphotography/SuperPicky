#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Countly telemetry bootstrap for SuperPicky.

All Countly-related concerns live in this module:
- configuration resolution
- GDPR-style user consent gating for anonymous usage stats
- anonymous device_id persistence
- event throttling and state storage
- HTTP /i request construction and delivery

Security note:
The Countly app key shipped in an open-source desktop client is not a real
secret. Using environment variables or a local `app_user_stat/_telemetry_build.py`
file only reduces casual abuse. If stronger protection or anti-abuse
guarantees are required, move telemetry submission behind a relay/proxy
controlled by the server side and keep the real key there.
"""

from __future__ import annotations

import importlib
import json
import locale
import os
import platform
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import parse, request

from constants import APP_VERSION


def _load_build_override(name: str) -> Optional[str]:
    for module_name in ("app_user_stat._telemetry_build", "_telemetry_build"):
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, name, None)
            if value:
                return value
        except Exception:
            continue
    return None


_BUILD_COUNTLY_APP_KEY = _load_build_override("COUNTLY_APP_KEY")
_BUILD_COUNTLY_SERVER_URL = _load_build_override("COUNTLY_SERVER_URL")


_PLACEHOLDER_COUNTLY_SERVER_URL = "https://countly.example.invalid"
_PLACEHOLDER_COUNTLY_APP_KEY = "__SET_COUNTLY_APP_KEY__"
_SDK_NAME = "python-native-desktop"
_SDK_VERSION = "1.0.0"
_REQUEST_TIMEOUT_SECONDS = 1.5
_HEARTBEAT_INTERVAL = timedelta(days=7)
_STATE_FILE_NAME = "telemetry_state.json"
_CONSENT_FILE_NAME = "telemetry_consent.json"
_STATE_SCHEMA_VERSION = 1
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED = False


@dataclass(frozen=True)
class CountlyConfig:
    """Resolved runtime telemetry configuration."""

    server_url: str
    app_key: str
    enabled: bool
    timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS

    @property
    def endpoint_url(self) -> str:
        base = self.server_url.rstrip("/")
        if base.endswith("/i"):
            return base
        return f"{base}/i"

    @property
    def has_real_app_key(self) -> bool:
        return bool(self.app_key) and self.app_key != _PLACEHOLDER_COUNTLY_APP_KEY

    @property
    def has_real_server_url(self) -> bool:
        return (
            bool(self.server_url)
            and self.server_url != _PLACEHOLDER_COUNTLY_SERVER_URL
            and self.server_url.startswith(("http://", "https://"))
        )

    @property
    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        return self.has_real_app_key and self.has_real_server_url


def bootstrap_telemetry(parent: Any = None, on_ready: Optional[Callable[[], None]] = None) -> None:
    """
    Initialize telemetry once and return immediately.

    Consent is handled on the UI thread after the Qt event loop starts. Actual
    network delivery happens on a daemon thread so app startup is never blocked
    by HTTP I/O. All failures are intentionally swallowed.
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
    """Consent-aware bootstrap sequence."""

    def __init__(self, parent: Any, on_ready: Optional[Callable[[], None]]) -> None:
        self._parent = parent
        self._on_ready = on_ready
        self._config = _resolve_countly_config()
        self._config_dir = _get_config_dir()
        self._consent_path = self._config_dir / _CONSENT_FILE_NAME

    def run(self) -> None:
        try:
            if not self._config.enabled:
                _debug_log("telemetry skipped: disabled by TELEMETRY_ENABLED")
                return

            if not self._config.has_real_app_key:
                _debug_log("telemetry skipped: no telemetry app key present")
                return

            if not self._ensure_user_consent():
                _debug_log("telemetry skipped: user did not grant consent")
                return

            if not self._config.has_real_server_url:
                _debug_log("telemetry skipped: COUNTLY_SERVER_URL is not configured")
                return

            if not self._config.is_configured:
                _debug_log("telemetry skipped: Countly config unresolved after consent")
                return

            client = _TelemetryClient(self._config)
            client.bootstrap()
        finally:
            _invoke_callback(self._on_ready)

    def _ensure_user_consent(self) -> bool:
        consent_state = _load_consent_state(self._consent_path)
        decision = consent_state.get("telemetry_consent")
        if isinstance(decision, bool):
            return decision

        if not _has_qapplication():
            return False

        decision = _show_consent_dialog(self._parent)
        consent_state["telemetry_consent"] = decision
        consent_state["consent_recorded_at"] = _utc_now_iso8601()
        _save_json(self._consent_path, consent_state)
        return decision


class _TelemetryClient:
    """Small Countly client for anonymous startup telemetry."""

    def __init__(self, config: CountlyConfig) -> None:
        self._config = config
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
            name="countly-telemetry",
            daemon=True,
        )
        worker.start()

    def build_self_test_report(self) -> Dict[str, Any]:
        state = _load_or_create_state(self._state_path)
        consent_state = _load_consent_state(_get_config_dir() / _CONSENT_FILE_NAME)
        events = self._build_due_events(state)
        payload = self._build_request_payload(state["device_id"], events) if events else None
        return {
            "app_version": APP_VERSION,
            "enabled": self._config.enabled,
            "configured": self._config.is_configured,
            "consent_applicable": self._config.has_real_app_key,
            "endpoint_url": self._config.endpoint_url,
            "state_path": str(self._state_path),
            "consent_path": str(_get_config_dir() / _CONSENT_FILE_NAME),
            "consent_status": consent_state.get("telemetry_consent"),
            "device_id": state["device_id"],
            "due_events": [event["key"] for event in events],
            "payload_preview": payload,
        }

    def send_blocking_for_self_test(self) -> bool:
        state = _load_or_create_state(self._state_path)
        events = self._build_due_events(state)
        if not events:
            _debug_log("self-test send skipped: no due events")
            return True
        return self._send_due_events(state, events)

    def _build_due_events(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        common_fields = _build_common_fields()
        events: List[Dict[str, Any]] = []

        if not state.get("install_reported_at"):
            events.append(_build_event("install", common_fields))

        events.append(_build_event("app_start", common_fields))

        last_heartbeat_at = _parse_iso8601(state.get("last_heartbeat_at"))
        now = datetime.now(timezone.utc)
        if last_heartbeat_at is None or (now - last_heartbeat_at) >= _HEARTBEAT_INTERVAL:
            events.append(_build_event("heartbeat_weekly", common_fields))

        return events

    def _send_due_events(self, state: Dict[str, Any], events: List[Dict[str, Any]]) -> bool:
        payload = self._build_request_payload(state["device_id"], events)
        success = _send_countly_request(
            self._config.endpoint_url,
            payload,
            timeout_seconds=self._config.timeout_seconds,
        )
        if not success:
            return False

        now = _utc_now_iso8601()
        changed = False
        event_keys = {event["key"] for event in events}

        if "install" in event_keys and not state.get("install_reported_at"):
            state["install_reported_at"] = now
            changed = True

        if "heartbeat_weekly" in event_keys:
            state["last_heartbeat_at"] = now
            changed = True

        if changed:
            _save_json(self._state_path, state)

        return True

    def _build_request_payload(self, device_id: str, events: List[Dict[str, Any]]) -> Dict[str, str]:
        base_timestamp_ms = _unique_timestamp_ms()
        local_now = datetime.now().astimezone()
        hour = local_now.hour
        dow = (local_now.weekday() + 1) % 7
        tz_minutes = _get_timezone_offset_minutes(local_now)
        session_metrics = _build_session_metrics()
        event_payloads = []

        for index, event in enumerate(events):
            event_payload = dict(event)
            event_payload["timestamp"] = base_timestamp_ms + index
            event_payload["hour"] = hour
            event_payload["dow"] = dow
            event_payload["tz"] = tz_minutes
            event_payloads.append(event_payload)

        return {
            "app_key": self._config.app_key,
            "device_id": device_id,
            "begin_session": "1",
            "timestamp": str(base_timestamp_ms),
            "hour": str(hour),
            "dow": str(dow),
            "tz": str(tz_minutes),
            "sdk_name": _SDK_NAME,
            "sdk_version": _SDK_VERSION,
            "metrics": json.dumps(session_metrics, separators=(",", ":"), ensure_ascii=False),
            "events": json.dumps(event_payloads, separators=(",", ":"), ensure_ascii=False),
        }


def _resolve_countly_config() -> CountlyConfig:
    server_url = _first_non_empty(
        os.getenv("COUNTLY_SERVER_URL"),
        _BUILD_COUNTLY_SERVER_URL,
        _PLACEHOLDER_COUNTLY_SERVER_URL,
    )
    app_key = _first_non_empty(
        os.getenv("COUNTLY_APP_KEY"),
        _BUILD_COUNTLY_APP_KEY,
        _PLACEHOLDER_COUNTLY_APP_KEY,
    )
    enabled = _parse_bool(os.getenv("TELEMETRY_ENABLED"), default=True)
    return CountlyConfig(
        server_url=server_url,
        app_key=app_key,
        enabled=enabled,
    )


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


def _load_consent_state(consent_path: Path) -> Dict[str, Any]:
    if not consent_path.exists():
        return {
            "telemetry_consent": None,
            "consent_recorded_at": None,
        }

    try:
        with open(consent_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            if "telemetry_consent" not in data:
                data["telemetry_consent"] = None
            if "consent_recorded_at" not in data:
                data["consent_recorded_at"] = None
            return data
    except Exception as exc:
        _debug_log(f"consent load failed, resetting: {exc}")

    return {
        "telemetry_consent": None,
        "consent_recorded_at": None,
    }


def _save_json(target_path: Path, payload: Dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    os.replace(tmp_path, target_path)


def _show_consent_dialog(parent: Any) -> bool:
    copy = _load_consent_copy()
    try:
        from ui.custom_dialogs import StyledMessageBox

        reply = StyledMessageBox.question(
            parent,
            copy["title"],
            copy["body"],
            yes_text=copy["accept"],
            no_text=copy["decline"],
        )
        return reply == StyledMessageBox.Yes
    except Exception as exc:
        _debug_log(f"consent dialog failed: {exc}")
        return False


def _load_consent_copy() -> Dict[str, str]:
    language = _resolve_consent_language()
    module_name = f"app_user_stat.consent_texts.{language}"

    try:
        module = importlib.import_module(module_name)
    except Exception:
        module = importlib.import_module("app_user_stat.consent_texts.en_US")

    return {
        "title": getattr(module, "TITLE"),
        "body": getattr(module, "BODY"),
        "accept": getattr(module, "ACCEPT_BUTTON"),
        "decline": getattr(module, "DECLINE_BUTTON"),
    }


def _resolve_consent_language() -> str:
    try:
        from tools.i18n import get_i18n

        current_lang = (get_i18n().current_lang or "").lower()
        if current_lang.startswith("zh"):
            return "zh_CN"
    except Exception:
        pass

    env_locale = _detect_locale().lower()
    if env_locale.startswith("zh") or "chinese" in env_locale:
        return "zh_CN"
    return "en_US"


def _build_common_fields() -> Dict[str, str]:
    return {
        "app_version": APP_VERSION,
        "os": platform.system() or "unknown",
        "arch": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "locale": _detect_locale(),
    }


def _build_session_metrics() -> Dict[str, str]:
    os_name = platform.system() or "unknown"
    os_version = platform.release() or platform.version() or "unknown"
    device_name = f"Desktop/{platform.machine() or 'unknown'}"
    return {
        "_os": os_name,
        "_os_version": os_version,
        "_device": device_name,
        "_app_version": APP_VERSION,
    }


def _build_event(event_key: str, common_fields: Dict[str, str]) -> Dict[str, Any]:
    return {
        "key": event_key,
        "count": 1,
        "segmentation": dict(common_fields),
    }


def _send_countly_request(endpoint_url: str, payload: Dict[str, str], timeout_seconds: float) -> bool:
    encoded = parse.urlencode(payload).encode("utf-8")
    request_obj: request.Request

    try:
        if len(encoded) <= 2000:
            query = encoded.decode("utf-8")
            request_obj = request.Request(f"{endpoint_url}?{query}", method="GET")
        else:
            request_obj = request.Request(
                endpoint_url,
                data=encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )

        with request.urlopen(request_obj, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            _debug_log(f"telemetry delivered: status={response.status} body={raw_body!r}")
            if not (200 <= response.status < 300):
                return False

            parsed_body = json.loads(raw_body)
            return isinstance(parsed_body, dict) and "result" in parsed_body
    except Exception as exc:
        _debug_log(f"telemetry delivery failed: {exc}")
        return False


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


def _has_qapplication() -> bool:
    try:
        from PySide6.QtWidgets import QApplication

        return QApplication.instance() is not None
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


def _first_non_empty(*values: Optional[str]) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


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


def _get_timezone_offset_minutes(local_now: datetime) -> int:
    offset = local_now.utcoffset()
    if offset is None:
        return 0
    return int(offset.total_seconds() // 60)


_TIMESTAMP_LOCK = threading.Lock()
_LAST_TIMESTAMP_MS = 0


def _unique_timestamp_ms() -> int:
    global _LAST_TIMESTAMP_MS
    current = int(datetime.now(timezone.utc).timestamp() * 1000)
    with _TIMESTAMP_LOCK:
        if current <= _LAST_TIMESTAMP_MS:
            current = _LAST_TIMESTAMP_MS + 1
        _LAST_TIMESTAMP_MS = current
    return current


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
    client = _TelemetryClient(_resolve_countly_config())
    report = client.build_self_test_report()

    print("SuperPicky telemetry self-test")
    print(f"app_version={report['app_version']}")
    print(f"enabled={report['enabled']}")
    print(f"configured={report['configured']}")
    print(f"consent_applicable={report['consent_applicable']}")
    print(f"endpoint_url={report['endpoint_url']}")
    print(f"state_path={report['state_path']}")
    print(f"consent_path={report['consent_path']}")
    print(f"consent_status={report['consent_status']}")
    print(f"device_id={report['device_id']}")
    print(f"due_events={','.join(report['due_events']) if report['due_events'] else '(none)'}")

    payload_preview = report.get("payload_preview")
    if payload_preview:
        print("payload_preview=")
        print(json.dumps(payload_preview, indent=2, ensure_ascii=False))
    else:
        print("payload_preview=(none)")

    if send:
        if not report["configured"]:
            print("send_result=skipped (Countly config unresolved)")
            return 2
        ok = client.send_blocking_for_self_test()
        print(f"send_result={'ok' if ok else 'failed'}")
        return 0 if ok else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_run_self_test(send="--send" in sys.argv))
