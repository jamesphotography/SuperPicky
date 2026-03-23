#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prepare the optional telemetry build override module for CI packaging.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare telemetry build override")
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the generated telemetry override module",
    )
    return parser.parse_args()


def _read_env(name: str) -> str:
    return os.getenv(name, "").strip()


def _render_module(app_key: str, server_url: str) -> str:
    return (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "\"\"\"\n"
        "CI-generated telemetry build overrides.\n"
        "\"\"\"\n"
        f"COUNTLY_APP_KEY = {app_key!r}\n"
        f"COUNTLY_SERVER_URL = {server_url!r}\n"
    )


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()
    app_key = _read_env("COUNTLY_APP_KEY")
    server_url = _read_env("COUNTLY_SERVER_URL")

    if not app_key or not server_url:
        _remove_if_exists(output_path)
        print("skipped")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    tmp_path.write_text(_render_module(app_key, server_url), encoding="utf-8")
    os.replace(tmp_path, output_path)
    print("injected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
