#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record a SuperPicky onboarding demo on macOS using ffmpeg + app automation."
    )
    parser.add_argument("--source-dir", required=True, help="Photo directory to auto-load.")
    parser.add_argument("--output", help="Output video path.")
    parser.add_argument("--duration", type=int, default=90, help="Recording duration in seconds.")
    parser.add_argument("--framerate", type=int, default=30, help="Screen recording frame rate.")
    parser.add_argument("--screen-index", default="1", help="AVFoundation screen device index/name.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to launch main.py.",
    )
    parser.add_argument(
        "--skill",
        choices=["beginner", "intermediate", "master"],
        default="intermediate",
        help="Automation skill preset.",
    )
    parser.add_argument("--flight", action="store_true", help="Enable flight detection for the demo.")
    parser.add_argument("--burst", action="store_true", help="Enable burst detection for the demo.")
    parser.add_argument("--birdid", action="store_true", help="Enable Bird ID for the demo.")
    parser.add_argument(
        "--start-delay-ms",
        type=int,
        default=1200,
        help="Delay between app launch and automatic processing.",
    )
    parser.add_argument(
        "--initial-delay-ms",
        type=int,
        default=800,
        help="Delay before automation selects the directory.",
    )
    return parser


def timestamped_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / "Desktop" / f"superpicky-onboarding-{stamp}.mp4"


def require_macos() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This helper is macOS-only.")


def terminate_process(proc: subprocess.Popen | None, sig: int = signal.SIGTERM) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> int:
    require_macos()
    args = build_parser().parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"Source directory not found: {source_dir}")

    output_path = Path(args.output).expanduser().resolve() if args.output else timestamped_output()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SUPERPICKY_AUTOMATION_MODE"] = "1"
    env["SUPERPICKY_AUTOMATION_DIR"] = str(source_dir)
    env["SUPERPICKY_AUTOMATION_SKILL"] = args.skill
    env["SUPERPICKY_AUTOMATION_FLIGHT"] = "1" if args.flight else "0"
    env["SUPERPICKY_AUTOMATION_BURST"] = "1" if args.burst else "0"
    env["SUPERPICKY_AUTOMATION_BIRDID"] = "1" if args.birdid else "0"
    env["SUPERPICKY_AUTOMATION_AUTOSTART"] = "1"
    env["SUPERPICKY_AUTOMATION_START_DELAY_MS"] = str(args.start_delay_ms)
    env["SUPERPICKY_AUTOMATION_INITIAL_DELAY_MS"] = str(args.initial_delay_ms)

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "avfoundation",
        "-capture_cursor",
        "1",
        "-capture_mouse_clicks",
        "1",
        "-framerate",
        str(args.framerate),
        "-i",
        f"{args.screen_index}:none",
        "-pix_fmt",
        "yuv420p",
        "-vcodec",
        "h264_videotoolbox",
        str(output_path),
    ]

    app_cmd = [args.python, "main.py"]

    ffmpeg_proc = None
    app_proc = None
    try:
        print(f"Output: {output_path}")
        print("Starting screen recording...")
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, cwd=repo_root)
        time.sleep(1.0)
        if ffmpeg_proc.poll() is not None:
            raise SystemExit("ffmpeg failed to start. Check screen recording permission and screen index.")

        print("Launching SuperPicky automation...")
        app_proc = subprocess.Popen(app_cmd, cwd=repo_root, env=env)
        time.sleep(args.duration)
    finally:
        print("Stopping recorder...")
        terminate_process(ffmpeg_proc, signal.SIGINT)
        print("Leaving SuperPicky running.")
        if app_proc is not None and app_proc.poll() is not None:
            print("SuperPicky exited before the recording window ended.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
