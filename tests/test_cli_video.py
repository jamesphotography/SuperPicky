# -*- coding: utf-8 -*-
"""视频 CLI 回归测试，无需模型权重 / Video CLI regressions without model weights."""

import json
import re
import signal
import sys
from argparse import Namespace
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video_segment import BirdSegment
from tools import cli_video, video_organizer
from tools.i18n import t


def _msg(key: str) -> str:
    """
    取语言包模板里最长的一段固定文字，用来断言本地化后的报错。

    参数:
    key (str): 语言包键，例如 "cli.export_no_parent"

    返回:
    str: 模板中去掉 {占位符} 后最长的一段文字（与当前测试语言无关）

    Return the longest literal fragment of a locale template, so assertions on
    localized messages hold whatever the test language is.
    """
    return max(re.split(r"\{[^}]*\}", t(key)), key=len).strip()


def _args(path: Path, **overrides: object) -> Namespace:
    """模拟解析后的命令行参数 / Simulate parsed command-line options."""
    values: dict[str, object] = dict(
        path=str(path),
        organize=False,
        max_frames=None,
        yolo_threshold=None,
        species_mode=None,
        species=None,
        flight=None,
        json=True,
    )
    values.update(overrides)
    return Namespace(**values)


@dataclass
class _Analysis:
    """提供报告和整理使用的结果字段 / Provide result fields used by reporting and organization."""

    sampled_frames: int = 3
    has_bird: bool = True
    cancelled: bool = False
    segments: list[BirdSegment] = field(
        default_factory=lambda: [
            BirdSegment(0.0, 2.0, True, species_zh="蓝鹀", species_en="Blue Bunting")
        ]
    )


def _install_analyzer(monkeypatch: pytest.MonkeyPatch, analyze) -> None:
    """替换推理边界 / Replace the inference boundary."""
    monkeypatch.setattr(
        cli_video, "_build_analyzer", lambda _: SimpleNamespace(analyze=analyze)
    )


def _link(link: Path, target: Path, *, directory: bool = False) -> None:
    """仅在平台不允许创建链接时跳过 / Skip only if symlinks are unavailable."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Cannot create a symlink: {error}")


def test_flat_scan_reports_video_results_without_touching_files(
    tmp_path, monkeypatch, capsys
):
    files = {"a.mP4": b"mp4", "B.MOV": b"mov", "c.M4V": b"m4v", "bird.JPG": b"jpg"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "D.mp4").touch()
    (tmp_path / "folder.mov").mkdir()
    analyzed = []

    def analyze(path, stop_callback):
        assert not stop_callback()
        analyzed.append(Path(path).name)
        print("model diagnostics")
        return _Analysis()

    _install_analyzer(monkeypatch, analyze)

    def forbid_organizing(*_args, **_kwargs):
        pytest.fail("Read-only analysis instantiated VideoOrganizer")

    monkeypatch.setattr(video_organizer, "VideoOrganizer", forbid_organizing)
    assert cli_video.analyze_video(_args(tmp_path)) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert "model diagnostics" in captured.err
    assert analyzed == ["a.mP4", "B.MOV", "c.M4V"]
    assert [Path(row["path"]).name for row in report["videos"]] == analyzed
    assert report["videos"][0]["analysis"]["segments"][0]["species_zh"] == "蓝鹀"
    assert not report["failed"] and not report["cancelled"]
    assert {entry.name for entry in tmp_path.iterdir()} == files.keys() | {
        "nested",
        "folder.mov",
    }
    for name, data in files.items():
        assert (tmp_path / name).read_bytes() == data


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("missing", "cli.video_not_found"),
        ("unsupported", "cli.video_unsupported"),
        ("empty", "cli.video_none_in_dir"),
    ],
)
def test_invalid_paths_fail_before_loading_model(
    tmp_path, monkeypatch, capsys, kind, message
):
    path = tmp_path / "clip.mp4"
    if kind == "unsupported":
        path = path.with_suffix(".jpg")
        path.touch()
    elif kind == "empty":
        path = tmp_path

    def forbid_loading(_):
        pytest.fail("Invalid input loaded the analyzer")

    monkeypatch.setattr(cli_video, "_build_analyzer", forbid_loading)
    assert cli_video.analyze_video(_args(path)) == 1
    captured = capsys.readouterr()
    assert _msg(message) in captured.err
    assert json.loads(captured.out) == {
        "videos": [],
        "failed": True,
        "cancelled": False,
    }


def test_directory_skips_symlink_videos_and_explicit_symlink_is_rejected(
    tmp_path, monkeypatch, capsys
):
    original = tmp_path / "original.mp4"
    original.touch()
    link = tmp_path / "linked.mp4"
    _link(link, original)
    _install_analyzer(monkeypatch, lambda path, stop_callback: _Analysis())
    assert cli_video.analyze_video(_args(tmp_path)) == 0
    assert len(json.loads(capsys.readouterr().out)["videos"]) == 1
    assert cli_video.analyze_video(_args(link)) == 1
    assert _msg("cli.video_symlink_input") in capsys.readouterr().err


@pytest.fixture
def fake_inference(monkeypatch):
    """替换模型导入并记录构造参数 / Stub model imports and capture analyzer arguments."""
    yolo = object()
    species = object()
    flight = object()
    model_loads = []

    def load_yolo_model():
        model_loads.append(yolo)
        return yolo

    for name, stub in {
        "ai_model": SimpleNamespace(load_yolo_model=load_yolo_model),
        "core.video_analyzer": SimpleNamespace(VideoAnalyzer=SimpleNamespace),
        "core.birdid_adapter": SimpleNamespace(BirdIDAdapter=lambda: species),
        "core.flight_adapter": SimpleNamespace(FlightAdapter=lambda: flight),
    }.items():
        monkeypatch.setitem(sys.modules, name, stub)
    return SimpleNamespace(yolo=yolo, species=species, flight=flight, loads=model_loads)


def test_saved_video_config_and_explicit_overrides(tmp_path, fake_inference):
    from advanced_config import get_advanced_config

    config = get_advanced_config().config
    config.update(
        video_max_frames=80,
        video_yolo_threshold=0.7,
        video_min_segment_frames=4,
        video_species_mode="full",
        video_enable_species_id=True,
        video_enable_flight=False,
    )
    before = config.copy()
    analyzer = cli_video._build_analyzer(_args(tmp_path))
    assert vars(analyzer) == dict(
        yolo_model=fake_inference.yolo,
        max_frames=80,
        yolo_threshold=0.7,
        min_segment_frames=4,
        species_mode="full",
        species_classifier=fake_inference.species,
        flight_classifier=None,
    )
    overrides = _args(
        tmp_path,
        max_frames=12,
        yolo_threshold=0.0,
        species_mode="instant",
        species=False,
        flight=True,
    )
    analyzer = cli_video._build_analyzer(overrides)
    assert analyzer.max_frames == 12
    assert analyzer.yolo_threshold == 0.0
    assert analyzer.species_mode == "fast"
    assert analyzer.species_classifier is None
    assert analyzer.flight_classifier is fake_inference.flight
    assert config == before


@pytest.mark.parametrize(
    "override",
    [dict(max_frames=0), dict(yolo_threshold=float("nan")), dict(yolo_threshold=1.01)],
)
def test_bad_options_do_not_load_model(tmp_path, fake_inference, capsys, override):
    video = tmp_path / "clip.mp4"
    video.touch()
    assert cli_video.analyze_video(_args(video, **override)) == 1
    assert not fake_inference.loads
    err = capsys.readouterr().err
    assert _msg("cli.video_bad_max_frames") in err or _msg("cli.video_bad_threshold") in err


def test_per_clip_failure_continues_without_moving_failed_clips(
    tmp_path, monkeypatch, capsys
):
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(name.encode("utf-8"))

    def analyze(path, stop_callback):
        if path.endswith("a.mp4"):
            raise OSError("Cannot open video")
        if path.endswith("b.mp4"):
            return _Analysis(sampled_frames=0, has_bird=False, segments=[])
        return _Analysis()

    _install_analyzer(monkeypatch, analyze)
    monkeypatch.setattr(video_organizer, "_locate_exiftool", lambda: None)
    assert cli_video.analyze_video(_args(tmp_path, organize=True)) == 1
    rows = json.loads(capsys.readouterr().out)["videos"]
    assert [row.get("error") for row in rows] == [
        "Cannot open video",
        t("cli.video_no_frames"),
        None,
    ]
    assert (tmp_path / "a.mp4").is_file() and (tmp_path / "b.mp4").is_file()
    target = Path(rows[2]["target"])
    assert target.parent == tmp_path / "蓝鹀"
    assert target.read_bytes() == b"c.mp4"
    assert not (tmp_path / "c.mp4").exists()
    manifest = tmp_path / video_organizer.VIDEO_MANIFEST_NAME
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"] == [
        {"original": str(tmp_path / "c.mp4"), "video": str(target)}
    ]
    assert video_organizer.restore_organized_videos(str(tmp_path))["restored"] == 1
    assert (tmp_path / "c.mp4").read_bytes() == b"c.mp4"
    assert not manifest.exists()


@pytest.mark.parametrize("linked", ["destination", "manifest"])
def test_organize_cannot_write_through_symlinks(tmp_path, monkeypatch, capsys, linked):
    folder = tmp_path / "input"
    folder.mkdir()
    video = folder / "clip.mp4"
    video.write_bytes(b"clip")
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked == "destination":
        _link(folder / "蓝鹀", outside, directory=True)
    else:
        _link(folder / video_organizer.VIDEO_MANIFEST_NAME, outside / "manifest.json")
    _install_analyzer(monkeypatch, lambda path, stop_callback: _Analysis())
    monkeypatch.setattr(video_organizer, "_locate_exiftool", lambda: None)
    assert cli_video.analyze_video(_args(folder, organize=True)) == 1
    key = ("cli.video_unsafe_destination" if linked == "destination"
           else "cli.video_unsafe_manifest")
    assert _msg(key) in capsys.readouterr().err
    assert list(outside.iterdir()) == []
    assert video.read_bytes() == b"clip"


def test_interrupt_preserves_manifest_for_completed_moves(
    tmp_path, monkeypatch, capsys
):
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).touch()
    seen = []
    previous_handler = signal.getsignal(signal.SIGINT)

    def analyze(path, stop_callback):
        seen.append(Path(path).name)
        if len(seen) == 2:
            signal.raise_signal(signal.SIGINT)
            assert stop_callback()
        return _Analysis(cancelled=stop_callback())

    _install_analyzer(monkeypatch, analyze)
    monkeypatch.setattr(video_organizer, "_locate_exiftool", lambda: None)
    assert cli_video.analyze_video(_args(tmp_path, organize=True)) == 130
    assert seen == ["a.mp4", "b.mp4"]
    assert signal.getsignal(signal.SIGINT) is previous_handler
    captured = capsys.readouterr()
    assert t("cli.video_interrupted") in captured.err
    report = json.loads(captured.out)
    assert report["cancelled"] and not report["failed"]
    assert "target" in report["videos"][0] and "target" not in report["videos"][1]
    assert video_organizer.restore_organized_videos(str(tmp_path))["restored"] == 1
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        "a.mp4",
        "b.mp4",
        "c.mp4",
    ]


def test_real_decoder_with_mock_yolo(tmp_path, monkeypatch, capsys):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    clip = tmp_path / "generated.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (64, 48))
    try:
        if not writer.isOpened():
            pytest.skip("OpenCV MP4 encoder is unavailable")
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        for _ in range(10):
            writer.write(frame)
    finally:
        writer.release()
    frames = []

    def predict(frame, **options):
        assert frame.shape == (48, 64, 3)
        assert options["classes"] == [14]
        frames.append(frame)
        return [SimpleNamespace(boxes=None)]

    monkeypatch.setitem(
        sys.modules, "ai_model", SimpleNamespace(load_yolo_model=lambda: predict)
    )
    assert (
        cli_video.analyze_video(_args(clip, species=False, flight=False, json=False))
        == 0
    )
    assert len(frames) == 2
    assert t("cli.video_result", path=str(clip), verdict=t("cli.video_verdict_no_bird"),
             details="", frames=2) in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [clip]
