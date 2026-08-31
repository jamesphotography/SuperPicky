# -*- coding: utf-8 -*-
"""
Apple Photos 导入控制器的路径、安全、批次和平台隔离回归测试。

Regression tests for Apple Photos path selection, argument safety, batching,
process cleanup, and platform isolation.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from ui import apple_photos_importer as photos_importer

_app = QApplication.instance() or QApplication([])


def _candidate(index: int) -> photos_importer.ImportCandidate:
    """构造无需真实文件的控制器候选。/ Build a controller-only candidate."""

    return photos_importer.ImportCandidate(
        path=Path(f"/tmp/SuperPicky import {index}.jpg"),
        photo_identity=("source", f"bird-{index}.jpg"),
        source_size=1000 + index,
        metadata=photos_importer.ApplePhotosMetadata(
            title=f"Bird {index}",
            description=None,
            keywords=(f"SuperPicky Rating {index % 4}★",),
        ),
    )


def _request(
    album_name: str,
    count: int,
    *,
    batch_size: int = 100,
) -> photos_importer.ApplePhotosImportRequest:
    """构造控制器测试请求。/ Build a controller test request."""

    candidates = tuple(_candidate(index) for index in range(count))
    return photos_importer.ApplePhotosImportRequest(
        album_name=album_name,
        candidates=candidates,
        requested=count,
        preflight_skipped=0,
        batch_size=batch_size,
    )


def _wait_for(predicate, timeout: float = 5.0) -> None:
    """泵送 Qt 事件直到条件成立。/ Pump Qt events until a condition is true."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for Qt process completion")


def _write_fake_osascript(tmp_path: Path) -> Path:
    """
    写入可预测的 osascript 替身。/ Write a deterministic osascript stand-in.

    argv 布局为 ``-e SCRIPT -- OPERATION ...``。替身返回逐条 Photos ID
    和字段掩码，并模拟部分失败、持久失败、崩溃及慢批次。

    Arguments use ``-e SCRIPT -- OPERATION ...``. The helper returns item IDs
    and field masks while simulating partial failures, crashes, and slow batches.
    """

    helper = tmp_path / "fake_osascript.py"
    helper.write_text(
        f"""#!{sys.executable}
import sys
import time

operation = sys.argv[4]
with open({str(tmp_path / "operations.log")!r}, "a", encoding="utf-8") as handle:
    handle.write("\\t".join(sys.argv[4:]) + "\\n")

def parse_records(start, count):
    records = []
    offset = start
    for _ in range(count):
        candidate_index = int(sys.argv[offset])
        reference = sys.argv[offset + 1]
        metadata_mask = int(sys.argv[offset + 4])
        keyword_count = int(sys.argv[offset + 7])
        records.append((candidate_index, reference, metadata_mask))
        offset += 8 + keyword_count
    return records

if operation == "repair":
    candidate_count = int(sys.argv[5])
    records = parse_records(6, candidate_count)
    if any(reference.startswith("SLOWREPAIR-") for _, reference, _ in records):
        time.sleep(30)
    print(f"REPAIR\\t{{candidate_count}}")
    for candidate_index, photos_id, requested_mask in records:
        failed_mask = requested_mask if photos_id.startswith("PERSIST-") else 0
        successful_mask = requested_mask ^ failed_mask
        print(
            f"ITEM\\t{{candidate_index}}\\t{{photos_id}}"
            f"\\t{{successful_mask}}\\t{{failed_mask}}"
        )
    raise SystemExit(0)

folder = sys.argv[5]
album = sys.argv[6]
candidate_count = int(sys.argv[7])
records = parse_records(8, candidate_count)
if folder != "SuperPicky Imports":
    print(f"unexpected folder: {{folder}}", file=sys.stderr)
    raise SystemExit(9)
if album == "DENY":
    print("Not authorized to send Apple events. (-1743)", file=sys.stderr)
    raise SystemExit(1)
if album == "CRASH":
    raise SystemExit(7)
if album == "MALFORMED":
    print("not a structured response")
    raise SystemExit(0)
if album in {{"SLOW", "CANCEL_GRACE"}}:
    time.sleep(0.2 if album == "CANCEL_GRACE" else 30)

if album == "DUPLICATE":
    imported = max(0, candidate_count - 1)
else:
    imported = candidate_count
print(f"IMPORT\\t{{imported}}")
for item_offset, (candidate_index, _path, requested_mask) in enumerate(records[:imported]):
    photos_prefix = "PERSIST" if album == "METAFAIL" else album
    photos_id = f"{{photos_prefix}}-{{candidate_index}}"
    if album in {{"PARTIAL", "METAFAIL", "SLOWREPAIR"}} and item_offset == 0:
        failed_mask = requested_mask & 4
        successful_mask = requested_mask ^ failed_mask
    else:
        successful_mask = requested_mask
        failed_mask = 0
    print(
        f"ITEM\\t{{candidate_index}}\\t{{photos_id}}"
        f"\\t{{successful_mask}}\\t{{failed_mask}}"
    )

if album == "BADCOUNTS":
    print("ITEM\\t999\\textra\\t0\\t0")
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    return helper


def test_resolve_prefers_raw_and_ignores_cache(tmp_path: Path) -> None:
    """RAW+JPG 优先 RAW，且不选 temp_jpeg_path 缓存。/ Prefer RAW over previews."""

    raw = tmp_path / "鸟照.NEF"
    jpeg = tmp_path / "鸟照.JPG"
    cache = tmp_path / "cache.jpg"
    raw.write_bytes(b"raw")
    jpeg.write_bytes(b"jpeg")
    cache.write_bytes(b"cache")

    resolved = photos_importer.resolve_photo_import_path(
        {
            "current_path": str(raw),
            "original_path": str(raw),
            "temp_jpeg_path": str(cache),
        }
    )

    assert resolved is not None
    assert os.path.samefile(resolved, raw)


def test_resolve_prefers_direct_heif_then_falls_back_to_raw(tmp_path: Path) -> None:
    """直接 HEIF 优先；没有显示边车时回退 RAW。/ Prefer HEIF, then RAW."""

    heif = tmp_path / "display.HEIC"
    raw = tmp_path / "raw.ARW"
    heif.write_bytes(b"heif")
    raw.write_bytes(b"raw")

    assert (
        photos_importer.resolve_photo_import_path({"current_path": str(heif)})
        == heif.resolve()
    )
    assert (
        photos_importer.resolve_photo_import_path({"current_path": str(raw)})
        == raw.resolve()
    )


def test_resolve_prefers_every_supported_raw_extension(tmp_path: Path) -> None:
    """所有项目 RAW 格式都优先于同名 JPEG。/ Prefer every project RAW format."""

    for index, extension in enumerate(photos_importer.RAW_EXTENSIONS):
        stem = f"bird-{index}"
        raw = tmp_path / f"{stem}{extension.upper()}"
        jpeg = tmp_path / f"{stem}.jpg"
        raw.write_bytes(b"raw")
        jpeg.write_bytes(b"jpeg")

        resolved = photos_importer.resolve_photo_import_path(
            {
                "current_path": str(jpeg),
                "original_path": str(raw),
            }
        )

        assert resolved is not None
        assert os.path.samefile(resolved, raw)


def test_preflight_builds_localized_photos_metadata_from_report(
    tmp_path: Path,
) -> None:
    """数据库生成英文标题、双语关键词和星级，不依赖 XMP。/ Build metadata from DB."""

    raw = tmp_path / "鸟照.ARW"
    raw.write_bytes(b"untouched raw")
    result = photos_importer.preflight_photos(
        [
            {
                "current_path": str(raw),
                "filename": raw.name,
                "bird_species_cn": "普通燕鸥",
                "bird_species_en": "Common Tern",
                "caption": "第一行\n第二行",
                "rating": 3,
                "picked": 1,
                "confidence": 94,
                "head_sharp": 432.65,
                "nima_score": 5.5,
                "is_flying": 1,
            }
        ],
        prefer_english=True,
    )

    assert result.skipped == 0
    candidate = result.candidates[0]
    assert candidate.path == raw.resolve()
    assert candidate.source_size == len(b"untouched raw")
    assert candidate.metadata.title == "Common Tern"
    assert candidate.metadata.description == (
        "Common Tern · 3★ Excellent\nAI 94% · Sharp 432.65 · Aesthetics 5.50 · Flying"
    )
    assert "第一行" not in candidate.metadata.description
    assert candidate.metadata.keywords == (
        "普通燕鸥",
        "Common Tern",
        "SuperPicky Rating 3★",
        "SuperPicky Picked",
    )
    assert not raw.with_suffix(".xmp").exists()


def test_metadata_title_precedence_rejected_and_keyword_deduplication() -> None:
    """已有标题优先，拒片使用稳定关键词，鸟名忽略大小写去重。/ Normalize metadata."""

    metadata = photos_importer.build_photos_metadata(
        {
            "title": " Existing title ",
            "bird_species_cn": "Common Tern",
            "bird_species_en": "common tern",
            "caption": "  ",
            "rating": -1,
            "picked": "invalid",
        },
        prefer_english=True,
    )

    assert metadata.title == "Existing title"
    assert metadata.description == "Existing title · Rejected"
    assert metadata.keywords == ("Common Tern", "SuperPicky Rejected")


def test_compact_photos_description_uses_chinese_and_normalizes_confidence() -> None:
    """中文摘要最多两行，并兼容 0-1 置信度。/ Build a two-line Chinese summary."""

    metadata = photos_importer.build_photos_metadata(
        {
            "bird_species_cn": "普通燕鸥",
            "bird_species_en": "Common Tern",
            "caption": "不应复制到照片的完整诊断说明",
            "rating": 2,
            "confidence": 0.941,
            "head_sharp": 432.654,
            "nima_score": 5.5,
            "is_flying": 1,
        },
        prefer_english=False,
    )

    assert metadata.description == (
        "普通燕鸥 · 2★ 良好\nAI 94% · 锐度 432.65 · 美学 5.50 · 飞鸟"
    )


def test_resolve_merged_relative_record_and_preflight_deduplicates(
    tmp_path: Path,
) -> None:
    """合并项目相对路径可解析，重复真实文件只导入一次。/ Resolve merged paths and dedupe."""

    photo_path = tmp_path / "子目录" / "same.jpg"
    photo_path.parent.mkdir()
    photo_path.write_bytes(b"jpeg")
    records = [
        {
            "_base_dir": str(tmp_path),
            "current_path": str(Path("子目录") / "same.jpg"),
            "source_dir": "one",
            "filename": "same.jpg",
        },
        {
            "current_path": str(photo_path),
            "source_dir": "two",
            "filename": "same.jpg",
        },
        {"current_path": str(tmp_path / "missing.jpg"), "filename": "missing.jpg"},
    ]

    result = photos_importer.preflight_photos(records)

    assert result.requested == 3
    assert [candidate.path for candidate in result.candidates] == [photo_path.resolve()]
    assert result.skipped == 2


def test_arguments_keep_unicode_quotes_and_newlines_out_of_script(
    tmp_path: Path,
) -> None:
    """特殊字符只存在 argv，不进入固定 AppleScript。/ Keep special text in argv only."""

    path = tmp_path / '白胸鸲鹟 "精选"\n01.jpg'
    path.write_bytes(b"jpeg")
    album = '鸟类 "精选"\n夏季'
    candidate = photos_importer.ImportCandidate(
        path=path.resolve(),
        photo_identity=("", path.name),
        source_size=path.stat().st_size,
        metadata=photos_importer.ApplePhotosMetadata(
            title='普通燕鸥 "精选"\n标题',
            description="第一行\n第二行",
            keywords=("普通燕鸥", "Common Tern", "SuperPicky Rating 3★"),
        ),
    )

    arguments = photos_importer.build_osascript_arguments(album, [candidate])

    assert arguments[0] == "-e"
    assert album not in arguments[1]
    assert os.fspath(path.resolve()) not in arguments[1]
    assert arguments[2:] == [
        "--",
        "import",
        # 文件夹名与相册名一样经 argv 传入，不再硬编码在 AppleScript 源里
        # The folder name travels through argv too, no longer hard-coded
        "SuperPicky Imports",
        '鸟类 "精选" 夏季',
        "1",
        "0",
        os.fspath(path.resolve()),
        path.name,
        str(path.stat().st_size),
        "7",
        '普通燕鸥 "精选"\n标题',
        "第一行\n第二行",
        "3",
        "普通燕鸥",
        "Common Tern",
        "SuperPicky Rating 3★",
    ]


def test_metadata_handler_compiles_native_photos_property_codes(
    tmp_path: Path,
) -> None:
    """
    确保 handler 在 Photos 作用域内编译原生元数据属性。

    Ensure the handler compiles native metadata properties in Photos scope.

    仅检查 AppleScript 语法不足以发现作用域错误；编译产物必须包含标题、
    说明和关键词对应的 Photos Apple Event 属性代码。

    Syntax-only compilation misses terminology-scope regressions. The compiled
    script must contain Photos Apple Event property codes for all three fields.
    """

    if sys.platform != "darwin":
        return
    compiled_script = tmp_path / "photos-metadata-helper.scpt"
    result = subprocess.run(
        [
            "/usr/bin/osacompile",
            "-o",
            os.fspath(compiled_script),
            "-e",
            photos_importer._APPLE_PHOTOS_IMPORT_SCRIPT,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    compiled_bytes = compiled_script.read_bytes()
    assert b"pnam" in compiled_bytes
    assert b"IPde" in compiled_bytes
    assert b"IPkw" in compiled_bytes


def test_album_helpers_are_deterministic() -> None:
    """默认相册含目录名和日期，输入空白被安全折叠。/ Album helpers are deterministic."""

    assert (
        photos_importer.default_album_name(
            "/Pictures/鸟图",
            date(2026, 7, 23),
        )
        == "鸟图 - 2026-07-23"
    )
    assert photos_importer.sanitize_album_name("  夏季\n\t鸟图  ") == "夏季 鸟图"


def test_batches_isolate_ambiguous_filename_and_size(tmp_path: Path) -> None:
    """同文件名同大小候选拆为单张批次，避免 Photos 条目误配。/ Isolate ambiguity."""

    records = []
    for folder_name in ("one", "two"):
        folder = tmp_path / folder_name
        folder.mkdir()
        path = folder / "same.ARW"
        path.write_bytes(b"same-size")
        records.append(
            {
                "current_path": str(path),
                "filename": path.name,
                "rating": 2,
            }
        )
    preflight = photos_importer.preflight_photos(records)

    batches = photos_importer.build_import_batches(
        "Birds",
        preflight.candidates,
        batch_size=100,
        max_argument_bytes=256 * 1024,
    )

    assert [len(batch) for batch in batches] == [1, 1]


def test_batches_respect_utf8_argument_byte_budget() -> None:
    """批次同时受候选数量与 UTF-8 argv 字节预算限制。/ Respect argv byte budget."""

    candidates = (_candidate(0), _candidate(1))
    one_candidate_limit = max(
        photos_importer._argument_bytes("Birds", [candidate])
        for candidate in candidates
    )
    assert photos_importer._argument_bytes("Birds", candidates) > one_candidate_limit

    batches = photos_importer.build_import_batches(
        "Birds",
        candidates,
        batch_size=100,
        max_argument_bytes=one_candidate_limit,
    )

    assert [len(batch) for batch in batches] == [1, 1]


def test_importer_batches_and_reports_photos_not_imported(
    tmp_path: Path, monkeypatch
) -> None:
    """205 张按 100 分三批，并准确累计 Photos 返回数。/ Batch and aggregate counts."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    progress = []
    importer.completed.connect(results.append)
    importer.progress.connect(lambda done, total: progress.append((done, total)))

    importer.start(_request("Birds", 205))
    _wait_for(lambda: bool(results))

    result = results[0]
    assert result.newly_imported == 205
    assert result.completed_batches == 3
    assert result.metadata_applied == 205
    assert result.metadata_partially_applied == 0
    assert result.metadata_not_applied == 0
    assert result.photos_not_imported == 0
    assert result.indeterminate == 0
    assert result.remaining == 0
    assert progress == [(0, 205), (100, 205), (200, 205), (205, 205)]


def test_importer_reports_duplicates_and_persistent_metadata_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """分别统计重复项及自动重试后的部分元数据。/ Report distinct outcomes."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)

    duplicate_importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    duplicate_results = []
    duplicate_importer.completed.connect(duplicate_results.append)
    duplicate_importer.start(_request("DUPLICATE", 3))
    _wait_for(lambda: bool(duplicate_results))
    duplicate_result = duplicate_results[0]
    assert duplicate_result.newly_imported == 2
    assert duplicate_result.photos_not_imported == 1
    assert duplicate_result.metadata_applied == 2
    assert duplicate_result.metadata_partially_applied == 0
    assert duplicate_result.metadata_not_applied == 0

    metadata_importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    metadata_results = []
    metadata_importer.completed.connect(metadata_results.append)
    metadata_importer.start(_request("METAFAIL", 3))
    _wait_for(lambda: bool(metadata_results))
    metadata_result = metadata_results[0]
    assert metadata_result.newly_imported == 3
    assert metadata_result.photos_not_imported == 0
    assert metadata_result.metadata_applied == 2
    assert metadata_result.metadata_partially_applied == 1
    assert metadata_result.metadata_not_applied == 0
    assert metadata_result.retryable_metadata == 1


def test_partial_metadata_failure_repairs_by_exact_photos_id(
    tmp_path: Path, monkeypatch
) -> None:
    """失败字段自动按 Photos ID 修复，不重新导入源文件。/ Repair by exact ID."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("PARTIAL", 2))
    _wait_for(lambda: bool(results))

    result = results[0]
    assert result.newly_imported == 2
    assert result.metadata_applied == 2
    assert result.metadata_partially_applied == 0
    assert result.retryable_metadata == 0
    operations = (tmp_path / "operations.log").read_text(encoding="utf-8").splitlines()
    assert [line.split("\t", 1)[0] for line in operations] == ["import", "repair"]
    assert "PARTIAL-0" in operations[1]
    assert os.fspath(_candidate(0).path) not in operations[1]


def test_import_helper_round_trips_chinese_metadata_as_utf8(
    tmp_path: Path, monkeypatch
) -> None:
    """中文元数据经 helper argv 写入并以 UTF-8 读回。/ Round-trip Chinese UTF-8."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    candidate = photos_importer.ImportCandidate(
        path=tmp_path / "普通燕鸥.ARW",
        photo_identity=("鸟图", "普通燕鸥.ARW"),
        source_size=1234,
        metadata=photos_importer.ApplePhotosMetadata(
            title="普通燕鸥",
            description="普通燕鸥 · 3★ 优选\nAI 94% · 飞鸟",
            keywords=("普通燕鸥", "SuperPicky 优选"),
        ),
    )
    request = photos_importer.ApplePhotosImportRequest(
        album_name="中文元数据",
        candidates=(candidate,),
        requested=1,
        preflight_skipped=0,
    )
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(request)
    _wait_for(lambda: bool(results))

    written_arguments = (tmp_path / "operations.log").read_text(encoding="utf-8")
    assert "普通燕鸥" in written_arguments
    assert "普通燕鸥 · 3★ 优选\nAI 94% · 飞鸟" in written_arguments
    assert "SuperPicky 优选" in written_arguments
    assert results[0].metadata_applied == 1


def test_persistent_failure_supports_metadata_only_manual_retry(
    tmp_path: Path, monkeypatch
) -> None:
    """持久失败可手动仅重试元数据，且不增加导入数。/ Retry metadata only."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("METAFAIL", 1))
    _wait_for(lambda: len(results) == 1)
    assert results[0].retryable_metadata == 1
    importer.retry_metadata()
    _wait_for(lambda: len(results) == 2)

    assert results[1].newly_imported == 1
    assert results[1].metadata_partially_applied == 1
    operations = (tmp_path / "operations.log").read_text(encoding="utf-8").splitlines()
    assert [line.split("\t", 1)[0] for line in operations] == [
        "import",
        "repair",
        "repair",
        "repair",
        "repair",
    ]
    assert all("PERSIST-0" in line for line in operations[1:])


def test_metadata_failure_without_any_success_is_reported_separately(
    tmp_path: Path, monkeypatch
) -> None:
    """所有请求字段失败时不误报为部分成功。/ Separate total metadata failure."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    candidate = photos_importer.ImportCandidate(
        path=Path("/tmp/metadata-failure.ARW"),
        photo_identity=("source", "metadata-failure.ARW"),
        source_size=99,
        metadata=photos_importer.ApplePhotosMetadata(
            title=None,
            description=None,
            keywords=("SuperPicky Picked",),
        ),
    )
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)
    importer.start(
        photos_importer.ApplePhotosImportRequest(
            album_name="METAFAIL",
            candidates=(candidate,),
            requested=1,
            preflight_skipped=0,
        )
    )
    _wait_for(lambda: bool(results))

    assert results[0].metadata_applied == 0
    assert results[0].metadata_partially_applied == 0
    assert results[0].metadata_not_applied == 1
    assert results[0].retryable_metadata == 1


def test_importer_rejects_inconsistent_metadata_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """拒绝与导入数量不一致的 helper 元数据统计。/ Reject inconsistent counts."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("BADCOUNTS", 3))
    _wait_for(lambda: bool(results))

    assert results[0].error is not None
    assert "Unexpected Photos response" in results[0].error


def test_importer_stops_on_permission_failure(tmp_path: Path, monkeypatch) -> None:
    """Apple Events 拒绝后停止并保留可识别错误。/ Stop on Automation denial."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("DENY", 3))
    _wait_for(lambda: bool(results))

    assert results[0].completed_batches == 0
    assert results[0].newly_imported == 0
    assert results[0].indeterminate == 3
    assert results[0].remaining == 0
    assert photos_importer.is_automation_permission_error(results[0].error or "")


def test_importer_cancel_finishes_active_batch_and_stops_future_batches(
    tmp_path: Path, monkeypatch
) -> None:
    """普通取消解析当前批次，仅留下未启动批次。/ Gracefully finish active batch."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("CANCEL_GRACE", 101))
    importer.cancel()
    _wait_for(lambda: bool(results))

    assert results[0].cancelled is True
    assert results[0].attempted == 100
    assert results[0].newly_imported == 100
    assert results[0].indeterminate == 0
    assert results[0].remaining == 1
    assert importer.is_running is False


def test_process_failure_and_malformed_response_are_indeterminate(
    tmp_path: Path, monkeypatch
) -> None:
    """崩溃或损坏响应不会被误报为未导入。/ Keep unknown batches indeterminate."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    for album in ("CRASH", "MALFORMED"):
        importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
        results = []
        importer.completed.connect(results.append)
        importer.start(_request(album, 3))
        _wait_for(lambda current_results=results: bool(current_results))

        result = results[0]
        assert result.error
        assert result.attempted == 0
        assert result.photos_not_imported == 0
        assert result.indeterminate == 3
        assert result.remaining == 0


def test_importer_shutdown_kills_helper_without_completion(
    tmp_path: Path, monkeypatch
) -> None:
    """窗口退出同步清理 helper，且不回调已销毁 UI。/ Shutdown suppresses completion callbacks."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("SLOW", 1))
    importer.shutdown()

    assert importer.is_running is False
    assert importer._process.state() == photos_importer.QProcess.NotRunning
    assert results == []


def test_importer_shutdown_during_metadata_repair_is_deterministic(
    tmp_path: Path, monkeypatch
) -> None:
    """修复阶段关闭也同步清理 helper。/ Clean up deterministically during repair."""

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(osascript_path=str(helper))
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("SLOWREPAIR", 1))

    def repair_started() -> bool:
        """判断替身是否已进入修复阶段。/ Return whether repair has started."""

        log_path = tmp_path / "operations.log"
        if not log_path.exists():
            return False
        return "\nrepair\t" in log_path.read_text(encoding="utf-8")

    _wait_for(repair_started)
    importer.shutdown()

    assert importer.is_running is False
    assert importer._process.state() == photos_importer.QProcess.NotRunning
    assert results == []


def test_browser_target_selection_precedence() -> None:
    """
    一张或多张明确勾选均优先；无勾选时导入当前可见结果。

    One or more explicitly checked photos take precedence; without a check,
    the currently visible results are imported.
    """

    from ui.results_browser_window import ResultsBrowserWindow

    class _Grid:
        def __init__(self, selected: list[dict]):
            self.selected = selected

        def get_explicitly_selected_photos(self) -> list[dict]:
            return self.selected

    class _Window:
        _filtered_photos = [{"filename": "visible-a"}, {"filename": "visible-b"}]

    fake = _Window()
    fake._thumb_grid = _Grid([{"filename": "selected-a"}, {"filename": "selected-b"}])
    assert [
        p["filename"] for p in ResultsBrowserWindow._apple_photos_target_photos(fake)
    ] == [
        "selected-a",
        "selected-b",
    ]

    fake._thumb_grid = _Grid([{"filename": "selected-only"}])
    assert [
        p["filename"] for p in ResultsBrowserWindow._apple_photos_target_photos(fake)
    ] == ["selected-only"]

    fake._thumb_grid = _Grid([])
    assert [
        p["filename"] for p in ResultsBrowserWindow._apple_photos_target_photos(fake)
    ] == [
        "visible-a",
        "visible-b",
    ]


def test_grid_explicit_selection_excludes_comparison_anchor() -> None:
    """
    批量操作只返回蓝色勾选项目，不包含对比视图自动补入的锚点。

    Batch operations return only blue-checked items and exclude the unchecked
    anchor automatically supplied to the comparison view.
    """

    from ui.thumbnail_grid import ThumbnailGrid

    class _GridState:
        _photos = [
            {"filename": "unchecked-anchor"},
            {"filename": "checked-only"},
        ]
        _multi_selected = {"checked-only"}
        _anchor_photo = _photos[0]

    fake = _GridState()
    assert ThumbnailGrid.get_multi_selected_photos(fake) == _GridState._photos
    assert ThumbnailGrid.get_explicitly_selected_photos(fake) == [
        {"filename": "checked-only"}
    ]


def test_windows_browser_has_no_apple_photos_action(monkeypatch) -> None:
    """Windows 构造浏览器时不创建 Apple Photos 按钮。/ No Photos action on Windows."""

    from ui import results_browser_window

    monkeypatch.setattr(results_browser_window.sys, "platform", "win32")
    window = results_browser_window.ResultsBrowserWindow()
    try:
        assert window._apple_photos_btn is None
    finally:
        window.cleanup()
        window.close()


def test_cancel_force_terminates_a_helper_that_never_returns(
    tmp_path: Path, monkeypatch
) -> None:
    """
    取消后 helper 若迟迟不返回，必须在宽限期后被强制终止。

    普通取消会先等当前批次自行收尾以保留可信计数，但 Photos 可能卡在权限授权
    对话框上永不返回。此前 cancel() 只设标志位，进度框会永久停住，用户只能强退
    应用。现在宽限期结束后走 terminate→kill，任务照常收尾。

    A cancelled import must not hang forever when the helper never returns
    (e.g. Photos blocked on a permission dialog). Before this, cancel() only set
    a flag and the progress dialog stayed up until the app was force-quit.
    """

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    # SLOW 让替身 sleep 30s，模拟永不返回的 helper
    importer = photos_importer.ApplePhotosImporter(
        osascript_path=str(helper), cancel_grace_ms=50
    )
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("SLOW", 3))
    _wait_for(lambda: importer._process.state() != QProcess.NotRunning)
    importer.cancel()

    # 宽限期(50ms)后应被强制终止并收尾，远早于替身的 30s
    _wait_for(lambda: bool(results), timeout=10.0)
    assert results[0].cancelled is True
    assert importer.is_running is False
    assert importer._process.state() == QProcess.NotRunning
    # 被强杀的批次无可信结果，必须记为不确定而非谎报成功或失败
    assert results[0].indeterminate == 3
    assert results[0].newly_imported == 0


def test_cancel_grace_timer_does_not_kill_a_later_batch(
    tmp_path: Path, monkeypatch
) -> None:
    """
    正常完成的任务不得留下待触发的宽限计时器。

    若计时器在任务收尾后仍存活，它会在下一次导入运行到一半时触发 terminate，
    把一个健康的批次杀掉。
    """

    monkeypatch.setattr(photos_importer.sys, "platform", "darwin")
    helper = _write_fake_osascript(tmp_path)
    importer = photos_importer.ApplePhotosImporter(
        osascript_path=str(helper), cancel_grace_ms=50
    )
    results = []
    importer.completed.connect(results.append)

    importer.start(_request("Birds", 2))
    _wait_for(lambda: bool(results))
    assert results[0].cancelled is False
    assert importer._cancel_timer is None, "任务收尾后不应残留宽限计时器"


def test_batch_indices_follow_candidate_positions(tmp_path: Path) -> None:
    """
    批次下标由位置推导，且与候选序列严格对应。

    取代原先的 {id(candidate): index} 映射：既去掉每批重建全量字典的 O(n²)
    开销，也消除同一候选对象被引用两次时下标互相覆盖的隐患。
    """

    derive = photos_importer.ApplePhotosImporter._derive_batch_indices
    batches = [("a", "b", "c"), ("d",), ("e", "f")]
    assert derive(batches, 6) == [(0, 1, 2), (3,), (4, 5)]

    # 划分前提被破坏时必须报错，而不是静默产生错位的下标
    with pytest.raises(ValueError):
        derive(batches, 7)


def test_import_folder_name_is_passed_through_argv(tmp_path: Path) -> None:
    """
    导入文件夹名必须经 argv 传入，不得硬编码在 AppleScript 源中。

    此前脚本里硬编码了三处 "SuperPicky Imports"，与模块常量
    _IMPORT_FOLDER_NAME 构成两份真相：改了常量而漏改脚本，照片会被导进另一个
    文件夹且不报任何错。
    """

    path = tmp_path / "bird.jpg"
    path.write_bytes(b"jpeg")
    candidate = photos_importer.ImportCandidate(
        path=path.resolve(),
        photo_identity=("", path.name),
        source_size=path.stat().st_size,
        metadata=photos_importer.ApplePhotosMetadata(
            title=None, description=None, keywords=()
        ),
    )
    arguments = photos_importer.build_osascript_arguments("相册", [candidate])

    # 脚本源里不得再出现文件夹名字面量
    assert photos_importer._IMPORT_FOLDER_NAME not in arguments[1]
    # 且必须作为 import 操作后的第一个参数传入
    assert arguments[3:5] == ["import", photos_importer._IMPORT_FOLDER_NAME]


def test_incremental_byte_accounting_matches_exact(tmp_path: Path) -> None:
    """
    批次切分用的增量字节分解必须与 _argument_bytes 精确一致。

    切分不能对每个候选都重建整个 argv(含 4.5KB AppleScript 源)，否则退化为
    O(n²)；改用「固定头部 + Σ(下标字段 + 候选载荷)」增量累加。本测试锁定两者
    等价，防止优化在某些输入下低估字节数、让 argv 突破 exec 上限。
    """

    album = '鸟类 "精选"'
    for count in (1, 2, 9, 10, 11, 99, 100, 101):
        candidates = [_candidate(index) for index in range(count)]
        exact = photos_importer._argument_bytes(album, candidates)
        incremental = (
            photos_importer._import_header_bytes(album)
            + photos_importer._text_bytes(str(count))
            + sum(
                photos_importer._text_bytes(str(index))
                + photos_importer._candidate_payload_bytes(candidate)
                for index, candidate in enumerate(candidates)
            )
        )
        assert incremental == exact, f"count={count} 时增量与精确不一致"


def test_batching_is_linear_not_quadratic() -> None:
    """
    切分开销应随候选数线性增长，而非平方增长。

    以 _argument_bytes 的调用次数作为代理指标：优化前每个候选都会调用它一次
    (每次重建全量 argv)，优化后切分路径完全不再调用它。
    """

    calls = []
    original = photos_importer._argument_bytes

    def counting(album_name, candidates):
        calls.append(len(candidates))
        return original(album_name, candidates)

    photos_importer._argument_bytes = counting
    try:
        photos_importer.build_import_batches(
            "Birds",
            tuple(_candidate(index) for index in range(300)),
            batch_size=100,
            max_argument_bytes=256 * 1024,
        )
    finally:
        photos_importer._argument_bytes = original

    assert calls == [], f"切分路径不应再调用 _argument_bytes，实际调用 {len(calls)} 次"


def test_sibling_display_extensions_follow_constants() -> None:
    """
    显示格式边车扩展名必须由 constants 派生，不得手写字面量。

    以前这里硬编码 10 个扩展名，constants 新增显示格式时不会同步，导致新格式的
    同主名边车永远找不到。
    """

    from constants import HEIF_EXTENSIONS, JPG_EXTENSIONS

    derived = photos_importer._SIBLING_DISPLAY_EXTENSIONS
    for base in (*JPG_EXTENSIONS, *HEIF_EXTENSIONS):
        assert base.lower() in derived, f"{base} 的小写变体缺失"
        assert base.upper() in derived, f"{base} 的大写变体缺失"
    assert len(derived) == 2 * (len(JPG_EXTENSIONS) + len(HEIF_EXTENSIONS))
    # JPG 系列仍优先于 HEIF 系列
    first_heif = min(derived.index(e.lower()) for e in HEIF_EXTENSIONS)
    last_jpg = max(derived.index(e.upper()) for e in JPG_EXTENSIONS)
    assert last_jpg < first_heif, "JPG 变体应排在 HEIF 之前"
