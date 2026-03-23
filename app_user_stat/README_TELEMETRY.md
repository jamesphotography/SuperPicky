# SuperPicky Telemetry Notes

This project's Countly telemetry implementation lives in
`app_user_stat/telemetry.py`.

Current anonymous fields already include `APP_VERSION` from
`constants.py`:

- Event segmentation: `app_version`
- Session metrics: `_app_version`

That means version statistics are sourced from one place only:
`constants.APP_VERSION`.

## How To Extend Telemetry

Keep all telemetry logic inside `app_user_stat/telemetry.py`. Do not spread
new network calls, anonymous ID generation, or config loading into business
code.

### Add more anonymous fields

If the new data should be attached to custom events, extend
`_build_common_fields()`.

Example:

```python
def _build_common_fields() -> Dict[str, str]:
    return {
        "app_version": APP_VERSION,
        "os": platform.system() or "unknown",
        "arch": platform.machine() or "unknown",
        "python_version": platform.python_version(),
        "locale": _detect_locale(),
        "release_channel": "stable",
    }
```

If the new data should be attached to Countly session metrics, extend
`_build_session_metrics()`.

Example:

```python
def _build_session_metrics() -> Dict[str, str]:
    return {
        "_os": os_name,
        "_os_version": os_version,
        "_device": device_name,
        "_app_version": APP_VERSION,
    }
```

### Add more events

To add another event:

1. Add the event to `_build_due_events()`.
2. If the event needs throttling or one-time semantics, persist that state in
   `telemetry_state.json`.
3. Reuse `_build_event()` instead of building payload fragments in business
   code.

For example, a one-time event should update the saved state only after a
successful Countly response.

### Rules For Safe Extensions

- Keep it anonymous. Do not add username, email, MAC address, hardware serial,
  photo content, photo paths, or raw EXIF data.
- Keep startup non-blocking. Telemetry must stay on the existing background
  delivery path.
- Keep Countly config in one place. Use environment variables or
  the project-root `_telemetry_build.py`.
- If you expand the data scope in a user-visible way, update the consent copy
  in `app_user_stat/consent_texts/`.

## Verification

Use the built-in self-test:

```bash
py -3 -m app_user_stat.telemetry
py -3 -m app_user_stat.telemetry --send
```

The self-test output now prints `app_version`, so you can verify that the
runtime value matches `constants.APP_VERSION` before sending data.

## CI Build Injection

GitHub release packaging should store telemetry credentials in repository
secrets, not in tracked source files.

Required repository secrets:

- `COUNTLY_APP_KEY`
- `COUNTLY_SERVER_URL`

Both Windows release workflows now call:

```bash
py -3 scripts/prepare_telemetry_build.py --output _telemetry_build.py
```

The helper writes a temporary UTF-8 project-root `_telemetry_build.py` only
when both secrets are present. If either secret is missing, the helper removes
any stale generated override and the packaged app falls back to the existing
placeholder telemetry configuration, which keeps telemetry unresolved instead
of shipping real Countly credentials. Runtime code still accepts the packaged
`app_user_stat._telemetry_build` path as a compatibility fallback, but CI
should target the root override file.
