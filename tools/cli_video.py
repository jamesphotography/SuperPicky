# -*- coding: utf-8 -*-
"""无界面、默认只读的视频分析命令 / Headless, read-only-by-default video analysis."""

from __future__ import annotations

import json
import signal
import stat
import sys
import threading
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.video_analyzer import VideoAnalysisResult, VideoAnalyzer
    from tools.video_organizer import OrganizeResult


def _video_paths(raw_path: str) -> list[Path]:
    """验证单个视频或扫描目录首层，不跟随视频链接。

    Validate one video or scan the top level of a directory, skipping video
    symlinks. Raise ValueError for invalid or empty inputs.
    """
    from constants import VIDEO_EXTENSIONS

    path = Path(raw_path).expanduser()
    extensions = set(VIDEO_EXTENSIONS)
    if path.is_symlink():
        raise ValueError(f"Symbolic links are not accepted as inputs: {path}")
    if path.is_dir():
        videos = sorted(
            (
                entry
                for entry in path.resolve().iterdir()
                if entry.suffix.lower() in extensions
                and not entry.is_symlink()
                and entry.is_file()
            ),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
        if not videos:
            raise ValueError(f"No supported videos found in: {path}")
        return videos
    if not path.is_file():
        raise ValueError(f"Video file or directory not found: {path}")
    if path.suffix.lower() not in extensions:
        raise ValueError(f"Unsupported video format: {path}")
    return [path.resolve()]


def _build_analyzer(args: Namespace) -> VideoAnalyzer:
    """按命令行优先的规则读取视频设置，再延迟加载模型。

    Load one analyzer for the whole run, using CLI overrides ahead of saved
    video settings. Raise ValueError on invalid settings before loading models.
    """
    from advanced_config import AdvancedConfig, get_advanced_config

    config = get_advanced_config().config
    defaults = AdvancedConfig.DEFAULT_CONFIG

    def configured(key: str) -> int | float | str | bool:
        value = config.get(key)
        return defaults[key] if value is None else value

    max_frames = int(
        args.max_frames
        if args.max_frames is not None
        else configured("video_max_frames")
    )
    threshold = float(
        args.yolo_threshold
        if args.yolo_threshold is not None
        else configured("video_yolo_threshold")
    )
    min_segment_frames = int(configured("video_min_segment_frames"))
    species_mode = args.species_mode or configured("video_species_mode")
    species = args.species
    if species is None:
        species = bool(configured("video_enable_species_id"))
    flight = args.flight
    if flight is None:
        flight = bool(configured("video_enable_flight"))

    if max_frames < 1:
        raise ValueError("--max-frames must be positive")
    if not 0 <= threshold <= 1:
        raise ValueError("--yolo-threshold must be between 0 and 1")
    if min_segment_frames < 1:
        raise ValueError("video_min_segment_frames must be positive")
    if species_mode not in ("instant", "fast", "full"):
        raise ValueError(f"Invalid video species mode: {species_mode}")

    from ai_model import load_yolo_model
    from core.video_analyzer import VideoAnalyzer

    yolo_model = load_yolo_model()
    species_classifier = flight_classifier = None
    if species:
        from core.birdid_adapter import BirdIDAdapter

        species_classifier = BirdIDAdapter()
    if flight:
        from core.flight_adapter import FlightAdapter

        flight_classifier = FlightAdapter()
        # 极速模式依赖鸟种识别触发飞行检测 / Instant flight detection requires a species match.
        if not species and species_mode == "instant":
            species_mode = "fast"

    return VideoAnalyzer(
        yolo_model=yolo_model,
        max_frames=max_frames,
        yolo_threshold=threshold,
        min_segment_frames=min_segment_frames,
        species_mode=species_mode,
        species_classifier=species_classifier,
        flight_classifier=flight_classifier,
    )


def _check_manifest(path: Path) -> None:
    """拒绝通过链接或特殊文件写入清单 / Refuse to write a linked or special manifest."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"Unsafe video manifest: {path}")


def _run(args: Namespace) -> tuple[list[dict], bool, bool]:
    """逐个分析，仅在显式请求时归类；返回结果、失败标志和取消标志。

    Analyze each clip and organize only on request. Return report rows and
    failure/cancellation flags. Setup errors propagate to the command handler.
    """
    videos = _video_paths(args.path)
    organizer = manifest = None
    moves: list[OrganizeResult] = []
    if args.organize:
        from tools.i18n import get_i18n
        from tools.video_organizer import (
            VIDEO_MANIFEST_NAME,
            OrganizeOptions,
            VideoOrganizer,
        )

        manifest = videos[0].parent / VIDEO_MANIFEST_NAME
        _check_manifest(manifest)
        i18n = get_i18n()
        organizer = VideoOrganizer(
            OrganizeOptions(
                operation="move",
                use_english=i18n.current_lang.startswith("en"),
                use_taiwan=i18n.current_lang == "zh_TW",
                no_bird_folder=i18n.t("video.folder_no_bird"),
                other_species_folder=i18n.t("video.folder_other_species"),
                confine_to_source=True,
            )
        )

    analyzer = _build_analyzer(args)
    rows: list[dict] = []
    failed = interrupted = False

    def stop_requested() -> bool:
        return interrupted

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    # 不在移动与记录清单之间抛出异步异常 / Do not interrupt a move before it is journaled.
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGINT, request_stop)
    try:
        for video in videos:
            if interrupted:
                break
            row: dict = {"path": str(video)}
            rows.append(row)
            try:
                result: VideoAnalysisResult = analyzer.analyze(
                    str(video), stop_callback=stop_requested
                )
                if result.cancelled or interrupted:
                    interrupted = True
                    row["error"] = "Analysis interrupted"
                    break
                if result.sampled_frames == 0:
                    raise ValueError("No frames could be decoded")
                row["analysis"] = asdict(result)
                if organizer is not None:
                    if video.is_symlink() or not video.is_file():
                        raise ValueError("Source is no longer a regular video file")
                    organized = organizer.organize(str(video), result.segments)
                    if not organized.success:
                        raise OSError(organized.error or "Video organization failed")
                    row["target"] = organized.target_video_path
                    row["skipped"] = organized.skipped
                    if not organized.skipped and organized.target_video_path != str(
                        video
                    ):
                        moves.append(organized)
            except Exception as error:
                failed = True
                row["error"] = str(error)
                print(f"{video}: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            if moves and manifest is not None:
                try:
                    from tools.video_organizer import record_organized_results

                    _check_manifest(manifest)
                    if not record_organized_results(moves):
                        raise OSError("No video manifest was written")
                except Exception as error:
                    failed = True
                    print(
                        f"{manifest}: {error}; use the reported paths to recover moved videos.",
                        file=sys.stderr,
                    )
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGINT, previous_handler)
    return rows, failed, interrupted


def analyze_video(args: Namespace) -> int:
    """运行视频子命令；成功返回 0，失败返回 1，取消返回 130。

    Run the video subcommand. Return 0 on success, 1 on failure, 130 on
    cancellation. Requested JSON goes to stdout, diagnostics to stderr.
    """
    rows: list[dict] = []
    failed = interrupted = False
    try:
        # 库的日志不应污染机器可读输出 / Keep library logging out of machine-readable output.
        with redirect_stdout(sys.stderr):
            rows, failed, interrupted = _run(args)
    except KeyboardInterrupt:
        interrupted = True
    except Exception as error:
        failed = True
        print(f"Video analysis: {error}", file=sys.stderr)

    if getattr(args, "json", False):
        # ASCII 转义让 Unicode 路径可在任意管道编码下往返 / ASCII escapes preserve Unicode on any pipe encoding.
        print(json.dumps({"videos": rows, "failed": failed, "cancelled": interrupted}))
    else:
        for row in rows:
            result = row.get("analysis")
            if result is not None:
                verdict = "bird detected" if result["has_bird"] else "no bird detected"
                species = list(
                    dict.fromkeys(
                        segment["species_en"] or segment["species_zh"]
                        for segment in result["segments"]
                        if segment["has_bird"]
                        and (segment["species_en"] or segment["species_zh"])
                    )
                )
                details = f" ({', '.join(species)})" if species else ""
                print(
                    f"{row['path']}: {verdict}{details}; {result['sampled_frames']} frames"
                )
            if row.get("target") is not None:
                status = "Already organized" if row["skipped"] else "Moved to"
                print(f"  {status}: {row['target']}")

    if interrupted:
        print("Video analysis interrupted.", file=sys.stderr)
        return 130
    return int(failed)
