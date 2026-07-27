# -*- coding: utf-8 -*-
"""
Apple Photos import controller for the macOS results browser.

The module keeps file selection, AppleScript construction, batching, and the
QProcess lifecycle in one place. Paths and album names are passed through
``argv`` rather than interpolated into AppleScript source.
"""

from __future__ import annotations

import math
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from constants import (
    HEIF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    JPG_EXTENSIONS,
    RAW_EXTENSIONS,
)

_DISPLAY_EXTENSIONS = tuple(JPG_EXTENSIONS + HEIF_EXTENSIONS)
_RAW_EXTENSIONS = tuple(RAW_EXTENSIONS)
_SUPPORTED_EXTENSIONS = frozenset(ext.lower() for ext in IMAGE_EXTENSIONS)
_SIBLING_RAW_EXTENSIONS = tuple(
    extension
    for raw_extension in RAW_EXTENSIONS
    for extension in (raw_extension.lower(), raw_extension.upper())
)
_SIBLING_DISPLAY_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".JPG",
    ".JPEG",
    ".heif",
    ".heic",
    ".hif",
    ".HEIF",
    ".HEIC",
    ".HIF",
)
_IMPORT_FOLDER_NAME = "SuperPicky Imports"
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_MAX_ARGUMENT_BYTES = 256 * 1024


_APPLE_PHOTOS_IMPORT_SCRIPT = r"""
on run argv
    if (count of argv) < 3 then error "Missing album name, candidate count, or import files"

    set targetAlbumName to item 1 of argv
    set candidateCount to (item 2 of argv) as integer
    set sourceFiles to {}
    set candidateRecords to {}
    set argumentIndex to 3

    repeat candidateCount times
        set sourcePath to item argumentIndex of argv
        set expectedFilename to item (argumentIndex + 1) of argv
        set expectedSize to (item (argumentIndex + 2) of argv) as integer
        set hasTitle to (item (argumentIndex + 3) of argv) is "1"
        set metadataTitle to item (argumentIndex + 4) of argv
        set hasDescription to (item (argumentIndex + 5) of argv) is "1"
        set metadataDescription to item (argumentIndex + 6) of argv
        set keywordCount to (item (argumentIndex + 7) of argv) as integer
        set metadataKeywords to {}
        repeat with keywordOffset from 1 to keywordCount
            set end of metadataKeywords to item (argumentIndex + 7 + keywordOffset) of argv
        end repeat

        set end of sourceFiles to POSIX file sourcePath
        set end of candidateRecords to {sourcePath, expectedFilename, expectedSize, hasTitle, metadataTitle, hasDescription, metadataDescription, metadataKeywords, false}
        set argumentIndex to argumentIndex + 8 + keywordCount
    end repeat

    tell application "/System/Applications/Photos.app"
        activate
        if exists folder "SuperPicky Imports" then
            set targetFolder to folder "SuperPicky Imports"
        else
            set targetFolder to make new folder named "SuperPicky Imports"
        end if

        set matchingAlbums to every album of targetFolder whose name is targetAlbumName
        if (count of matchingAlbums) > 0 then
            set targetAlbum to item 1 of matchingAlbums
        else
            set targetAlbum to make new album named targetAlbumName at targetFolder
        end if

        set importedItems to import sourceFiles into targetAlbum skip check duplicates false
        set metadataApplied to 0
        set metadataNotApplied to 0

        repeat with importedItem in importedItems
            set matchingCandidate to missing value
            set importedFilename to filename of importedItem
            set importedSize to (size of importedItem) as integer

            repeat with candidateRecord in candidateRecords
                if (item 9 of candidateRecord) is false then
                    if (item 2 of candidateRecord) is importedFilename and (item 3 of candidateRecord) is importedSize then
                        set matchingCandidate to candidateRecord
                        exit repeat
                    end if
                end if
            end repeat

            if matchingCandidate is missing value and candidateCount is 1 and (count of importedItems) is 1 then
                set matchingCandidate to item 1 of candidateRecords
            end if

            if matchingCandidate is missing value then
                set metadataNotApplied to metadataNotApplied + 1
            else
                set item 9 of matchingCandidate to true
                set hasMetadata to (item 4 of matchingCandidate) or (item 6 of matchingCandidate) or ((count of item 8 of matchingCandidate) > 0)
                if hasMetadata then
                    try
                        if item 4 of matchingCandidate then
                            set name of importedItem to item 5 of matchingCandidate
                        end if
                        if item 6 of matchingCandidate then
                            set description of importedItem to item 7 of matchingCandidate
                        end if

                        set metadataKeywords to item 8 of matchingCandidate
                        if (count of metadataKeywords) > 0 then
                            set mergedKeywords to keywords of importedItem
                            if mergedKeywords is missing value then set mergedKeywords to {}
                            repeat with metadataKeyword in metadataKeywords
                                set keywordExists to false
                                repeat with existingKeyword in mergedKeywords
                                    ignoring case
                                        if (existingKeyword as text) is (metadataKeyword as text) then
                                            set keywordExists to true
                                            exit repeat
                                        end if
                                    end ignoring
                                end repeat
                                if keywordExists is false then
                                    set end of mergedKeywords to metadataKeyword as text
                                end if
                            end repeat
                            set keywords of importedItem to mergedKeywords
                        end if
                        set metadataApplied to metadataApplied + 1
                    on error
                        set metadataNotApplied to metadataNotApplied + 1
                    end try
                else
                    set metadataNotApplied to metadataNotApplied + 1
                end if
            end if
        end repeat

        return ((count of importedItems) as text) & tab & (metadataApplied as text) & tab & (metadataNotApplied as text)
    end tell
end run
""".strip()


@dataclass(frozen=True)
class ApplePhotosMetadata:
    """
    要写入 Apple Photos 资料库条目的原生元数据。

    参数:
        title: Photos 标题；None 表示保留导入值。
        description: Photos 说明；None 表示保留导入值。
        keywords: 与 Photos 已有关键词合并的 SuperPicky 关键词。

    Apple Photos native metadata applied to one library item.

    Parameters:
        title: Photos title; None preserves the imported value.
        description: Photos description; None preserves the imported value.
        keywords: SuperPicky keywords merged with imported Photos keywords.
    """

    title: str | None
    description: str | None
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class ImportCandidate:
    """
    已解析、可导入的单张照片。/ One resolved, importable photo.

    参数 / Parameters:
        path: 要交给 Apple Photos 的真实文件绝对路径。
        photo_identity: SuperPicky 记录身份(source_dir, filename)。
        source_size: 导入源文件字节数，用于匹配 Photos 返回条目。
        metadata: 只写入本次新导入条目的 Photos 原生元数据。

        path: Absolute path passed to Apple Photos.
        photo_identity: SuperPicky record identity (source_dir, filename).
        source_size: Source size in bytes used to match the returned Photos item.
        metadata: Photos-native metadata applied only to a newly imported item.
    """

    path: Path
    photo_identity: tuple[str, str]
    source_size: int
    metadata: ApplePhotosMetadata


@dataclass(frozen=True)
class ImportPreflight:
    """导入前路径解析结果。/ Path-resolution result before import."""

    requested: int
    candidates: tuple[ImportCandidate, ...]
    skipped: int


@dataclass(frozen=True)
class ApplePhotosImportRequest:
    """一次 Apple Photos 批量导入请求。/ One batched Photos import request."""

    album_name: str
    candidates: tuple[ImportCandidate, ...]
    requested: int
    preflight_skipped: int
    batch_size: int = _DEFAULT_BATCH_SIZE
    max_argument_bytes: int = _DEFAULT_MAX_ARGUMENT_BYTES


@dataclass(frozen=True)
class ApplePhotosImportResult:
    """Apple Photos 导入的最终或部分结果。/ Final or partial import result."""

    requested: int
    eligible: int
    attempted: int
    newly_imported: int
    preflight_skipped: int
    photos_not_imported: int
    remaining: int
    completed_batches: int
    metadata_applied: int
    metadata_not_applied: int
    cancelled: bool
    error: str | None = None


def _photo_identity(photo: dict) -> tuple[str, str]:
    """返回稳定的照片身份键。/ Return a stable photo identity key."""

    return (str(photo.get("source_dir") or ""), str(photo.get("filename") or ""))


def _nonempty_text(value: object) -> str | None:
    """返回去除首尾空白的非空文本。/ Return non-empty outer-trimmed text."""

    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text or None


def _deduplicate_keywords(values: Iterable[object]) -> tuple[str, ...]:
    """
    按大小写不敏感规则去重并保留首次出现顺序。

    Deduplicate case-insensitively while preserving first-seen order.
    """

    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = _nonempty_text(value)
        if keyword is None:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(keyword)
    return tuple(keywords)


def _finite_number(value: object) -> float | None:
    """安全转换有限浮点数。/ Safely coerce a finite floating-point value."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_compact_photos_description(
    photo: dict,
    *,
    title: str | None,
    prefer_english: bool = False,
) -> str | None:
    """
    为 Apple Photos 构造最多两行的移动端友好摘要。

    完整诊断 caption 继续保存在结果数据库和 XMP 中；Photos 只展示身份、
    星级和少量核心指标，避免 iPhone 信息面板被长诊断文本占满。

    Build an at-most-two-line, mobile-friendly Apple Photos summary. The full
    diagnostic caption remains in the report database and XMP; Photos receives
    only identification, rating, and a few core metrics.

    参数 / Parameters:
        photo: 结果数据库照片记录。/ Report-database photo record.
        title: 已解析的 Photos 标题。/ Resolved Photos title.
        prefer_english: 是否使用英文标签。/ Whether to use English labels.

    返回 / Returns:
        str | None: 紧凑说明；没有可展示字段时为 None。/ Compact description.
    """

    labels = (
        {
            "ratings": {
                -1: "Rejected",
                0: "Poor",
                1: "Average",
                2: "Good",
                3: "Excellent",
            },
            "sharp": "Sharp",
            "aesthetics": "Aesthetics",
            "flying": "Flying",
        }
        if prefer_english
        else {
            "ratings": {
                -1: "拒绝",
                0: "问题",
                1: "普通",
                2: "良好",
                3: "优选",
            },
            "sharp": "锐度",
            "aesthetics": "美学",
            "flying": "飞鸟",
        }
    )

    headline_parts: list[str] = []
    if title:
        headline_parts.append(title)
    try:
        rating = int(photo.get("rating")) if photo.get("rating") is not None else None
    except (TypeError, ValueError):
        rating = None
    rating_labels = labels["ratings"]
    if rating in rating_labels:
        rating_text = (
            f"{rating}★ {rating_labels[rating]}"
            if rating >= 0
            else str(rating_labels[rating])
        )
        headline_parts.append(rating_text)

    metric_parts: list[str] = []
    confidence = _finite_number(photo.get("confidence"))
    if confidence is not None:
        confidence_percent = confidence * 100 if 0 <= confidence <= 1 else confidence
        metric_parts.append(f"AI {confidence_percent:.0f}%")
    sharpness = _finite_number(photo.get("head_sharp"))
    if sharpness is not None:
        metric_parts.append(f"{labels['sharp']} {sharpness:.2f}")
    aesthetics = _finite_number(photo.get("nima_score"))
    if aesthetics is not None:
        metric_parts.append(f"{labels['aesthetics']} {aesthetics:.2f}")
    if bool(photo.get("is_flying")):
        metric_parts.append(str(labels["flying"]))

    lines = [" · ".join(parts) for parts in (headline_parts, metric_parts) if parts]
    return "\n".join(lines) or None


def build_photos_metadata(
    photo: dict, *, prefer_english: bool = False
) -> ApplePhotosMetadata:
    """
    从结果数据库记录构造 Photos 标题、说明和可搜索关键词。

    标题优先使用数据库现有 title；为空时按当前界面语言回退到鸟种鉴定。
    星级保留为关键词，不映射为 Photos 的布尔型个人收藏。

    Build Photos title, description, and searchable keywords from one report
    record. Existing title wins; otherwise identification follows the current
    UI language. Ratings remain keywords rather than Photos favorites.

    参数 / Parameters:
        photo: 结果数据库照片记录。/ Report-database photo record.
        prefer_english: 鸟种标题是否优先英文。/ Prefer English for species title.

    返回 / Returns:
        ApplePhotosMetadata: 可安全写入新 Photos 条目的元数据。
    """

    species_cn = _nonempty_text(photo.get("bird_species_cn"))
    species_en = _nonempty_text(photo.get("bird_species_en"))
    species_title = (
        (species_en or species_cn) if prefer_english else (species_cn or species_en)
    )
    title = _nonempty_text(photo.get("title")) or species_title
    description = build_compact_photos_description(
        photo,
        title=title,
        prefer_english=prefer_english,
    )

    keywords: list[object] = [species_cn, species_en]
    rating_value = photo.get("rating")
    try:
        rating = int(rating_value) if rating_value is not None else None
    except (TypeError, ValueError):
        rating = None
    if rating == -1:
        keywords.append("SuperPicky Rejected")
    elif rating is not None and rating >= 0:
        keywords.append(f"SuperPicky Rating {rating}★")

    try:
        is_picked = int(photo.get("picked") or 0) == 1
    except (TypeError, ValueError):
        is_picked = False
    if is_picked:
        keywords.append("SuperPicky Picked")

    return ApplePhotosMetadata(
        title=title,
        description=description,
        keywords=_deduplicate_keywords(keywords),
    )


def _absolute_candidate(photo: dict, value: object) -> Path | None:
    """
    将记录内路径规范为绝对路径，但不要求文件存在。

    Normalize a record path to an absolute path without requiring it to exist.
    Relative paths use the resolved record's ``_base_dir`` when available.
    """

    if not isinstance(value, (str, os.PathLike)) or not str(value):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        base_dir = photo.get("_base_dir")
        if isinstance(base_dir, (str, os.PathLike)) and str(base_dir):
            path = Path(base_dir) / path
    return Path(os.path.abspath(os.fspath(path)))


def _is_supported_file(path: Path) -> bool:
    """判断路径是否为存在的受支持图像文件。/ Check for a supported image file."""

    return path.is_file() and path.suffix.lower() in _SUPPORTED_EXTENSIONS


def _display_siblings(path: Path) -> Iterable[Path]:
    """按显示格式优先级生成同主名 JPEG/HEIF 路径。/ Yield display-ready siblings."""

    stem = path.with_suffix("")
    for extension in _SIBLING_DISPLAY_EXTENSIONS:
        yield stem.with_suffix(extension)


def _raw_siblings(path: Path) -> Iterable[Path]:
    """按项目 RAW 格式顺序生成同主名文件。/ Yield same-stem RAW paths."""

    stem = path.with_suffix("")
    for extension in _SIBLING_RAW_EXTENSIONS:
        yield stem.with_suffix(extension)


def resolve_photo_import_path(photo: dict) -> Path | None:
    """
    为照片选择唯一导入文件，优先 RAW，再回退 JPEG/HEIF。

    临时预览和裁剪缓存故意不参与候选。先检查记录中的 RAW，再寻找同主名
    RAW；没有 RAW 时才选择直接记录或同主名的 JPEG/HEIF。

    Select one import file for a photo, preferring RAW and falling back to
    JPEG/HEIF. Temporary previews and crop caches are intentionally excluded.

    参数 / Parameters:
        photo: 结果浏览器中的已解析照片记录。/ Resolved browser photo record.

    返回 / Returns:
        Path | None: 真实可导入路径；无可用文件时为 None。
    """

    base_paths = [
        path
        for path in (
            _absolute_candidate(photo, photo.get("current_path")),
            _absolute_candidate(photo, photo.get("original_path")),
        )
        if path is not None
    ]

    for path in base_paths:
        if path.suffix.lower() in _RAW_EXTENSIONS and _is_supported_file(path):
            return path.resolve()

    seen_siblings: set[str] = set()
    for path in base_paths:
        for sibling in _raw_siblings(path):
            key = os.path.normcase(os.path.realpath(sibling))
            if key in seen_siblings:
                continue
            seen_siblings.add(key)
            if _is_supported_file(sibling):
                return sibling.resolve()

    for path in base_paths:
        if path.suffix.lower() in _DISPLAY_EXTENSIONS and _is_supported_file(path):
            return path.resolve()

    for path in base_paths:
        for sibling in _display_siblings(path):
            key = os.path.normcase(os.path.realpath(sibling))
            if key in seen_siblings:
                continue
            seen_siblings.add(key)
            if _is_supported_file(sibling):
                return sibling.resolve()
    return None


def preflight_photos(
    photos: Sequence[dict], *, prefer_english: bool = False
) -> ImportPreflight:
    """
    解析、规范化并去重导入文件。/ Resolve, canonicalize, and deduplicate files.

    每个缺失、不支持或解析到重复真实路径的记录计为一次 skipped。
    Missing, unsupported, or duplicate resolved records each count as skipped.
    """

    candidates: list[ImportCandidate] = []
    seen_paths: set[str] = set()
    for photo in photos:
        path = resolve_photo_import_path(photo)
        if path is None:
            continue
        key = os.path.normcase(os.path.realpath(path))
        if key in seen_paths:
            continue
        try:
            source_size = path.stat().st_size
        except OSError:
            continue
        seen_paths.add(key)
        candidates.append(
            ImportCandidate(
                path=path,
                photo_identity=_photo_identity(photo),
                source_size=source_size,
                metadata=build_photos_metadata(
                    photo,
                    prefer_english=prefer_english,
                ),
            )
        )

    requested = len(photos)
    return ImportPreflight(
        requested=requested,
        candidates=tuple(candidates),
        skipped=requested - len(candidates),
    )


def sanitize_album_name(value: str) -> str:
    """
    清理用户输入的相册名称。/ Normalize a user-provided album name.

    换行和控制空白被折叠，长度限制为 120 字符；空名称无效。
    Line breaks and control whitespace are collapsed, length is capped at 120,
    and an empty result is invalid.
    """

    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:120].rstrip()


def default_album_name(source_directory: str, today: date | None = None) -> str:
    """生成 ``目录名 - YYYY-MM-DD`` 默认相册名。/ Build the default dated album name."""

    directory = (
        Path(source_directory).expanduser() if source_directory else Path("Bird Photos")
    )
    folder_name = directory.name.strip() or "Bird Photos"
    import_date = today or date.today()
    return sanitize_album_name(f"{folder_name} - {import_date.isoformat()}")


def build_osascript_arguments(
    album_name: str, candidates: Sequence[ImportCandidate]
) -> list[str]:
    """
    构造固定脚本和独立 argv，避免 AppleScript 注入。

    Build a static script plus separate argv values, preventing AppleScript
    injection from album names or filesystem paths.
    """

    clean_name = sanitize_album_name(album_name)
    if not clean_name:
        raise ValueError("Apple Photos album name cannot be empty")
    if not candidates:
        raise ValueError("Apple Photos import batch cannot be empty")
    arguments = [
        "-e",
        _APPLE_PHOTOS_IMPORT_SCRIPT,
        "--",
        clean_name,
        str(len(candidates)),
    ]
    for candidate in candidates:
        metadata = candidate.metadata
        arguments.extend(
            [
                os.fspath(candidate.path),
                candidate.path.name,
                str(candidate.source_size),
                "1" if metadata.title is not None else "0",
                metadata.title or "",
                "1" if metadata.description is not None else "0",
                metadata.description or "",
                str(len(metadata.keywords)),
                *metadata.keywords,
            ]
        )
    return arguments


def _argument_bytes(album_name: str, candidates: Sequence[ImportCandidate]) -> int:
    """估算 exec argv UTF-8 字节数。/ Estimate exec argv UTF-8 byte size."""

    return sum(
        len(argument.encode("utf-8")) + 1
        for argument in build_osascript_arguments(album_name, candidates)
    )


def build_import_batches(
    album_name: str,
    candidates: Sequence[ImportCandidate],
    *,
    batch_size: int,
    max_argument_bytes: int,
) -> list[tuple[ImportCandidate, ...]]:
    """
    构造安全批次，并隔离无法按文件名与大小区分的候选。

    Build safe batches and isolate candidates sharing the same filename and
    byte size, so each returned Photos item can be matched deterministically.

    异常 / Raises:
        ValueError: 批次上限无效，或单个候选超过 argv 安全预算。
    """

    if batch_size <= 0:
        raise ValueError("Apple Photos import batch size must be positive")
    if max_argument_bytes <= 0:
        raise ValueError("Apple Photos argument byte limit must be positive")

    match_key_counts = Counter(
        (candidate.path.name.casefold(), candidate.source_size)
        for candidate in candidates
    )
    batches: list[tuple[ImportCandidate, ...]] = []
    current: list[ImportCandidate] = []

    def flush_current() -> None:
        if current:
            batches.append(tuple(current))
            current.clear()

    for candidate in candidates:
        match_key = (candidate.path.name.casefold(), candidate.source_size)
        if match_key_counts[match_key] > 1:
            flush_current()
            if _argument_bytes(album_name, [candidate]) > max_argument_bytes:
                raise ValueError(
                    f"Apple Photos metadata is too large for {candidate.path.name}"
                )
            batches.append((candidate,))
            continue

        proposed = [*current, candidate]
        if current and (
            len(proposed) > batch_size
            or _argument_bytes(album_name, proposed) > max_argument_bytes
        ):
            flush_current()
            proposed = [candidate]
        if _argument_bytes(album_name, proposed) > max_argument_bytes:
            raise ValueError(
                f"Apple Photos metadata is too large for {candidate.path.name}"
            )
        current.append(candidate)

    flush_current()
    return batches


def is_automation_permission_error(message: str) -> bool:
    """识别 macOS Apple Events 权限拒绝。/ Detect macOS Apple Events denial."""

    lowered = message.casefold()
    return any(
        marker in lowered
        for marker in (
            "-1743",
            "not authorized to send apple events",
            "not permitted to send apple events",
            "appleevent timed out due to sandboxing",
        )
    )


class ApplePhotosImporter(QObject):
    """
    使用单个可复用 QProcess 顺序导入多个批次。/ Sequential batched importer.

    ``completed`` 对成功、失败和取消均只发射一次；窗口关闭时调用 ``shutdown``
    会同步终止 helper 且不再回调 UI。

    ``completed`` is emitted exactly once for success, failure, or cancellation.
    ``shutdown`` synchronously terminates the helper and suppresses UI callbacks.
    """

    progress = Signal(int, int)
    completed = Signal(object)

    def __init__(
        self, parent: QObject | None = None, osascript_path: str = "/usr/bin/osascript"
    ):
        super().__init__(parent)
        self._osascript_path = osascript_path
        self._process = QProcess(self)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._request: ApplePhotosImportRequest | None = None
        self._batches: list[tuple[ImportCandidate, ...]] = []
        self._next_batch_index = 0
        self._current_batch_size = 0
        self._attempted = 0
        self._newly_imported = 0
        self._completed_batches = 0
        self._metadata_applied = 0
        self._metadata_not_applied = 0
        self._cancel_requested = False
        self._completion_emitted = False
        self._suppress_completion = False

    @property
    def is_running(self) -> bool:
        """返回导入是否处于活动状态。/ Return whether an import is active."""

        return self._request is not None and not self._completion_emitted

    def start(self, request: ApplePhotosImportRequest) -> None:
        """
        启动导入并立即返回。/ Start an import without blocking the UI.

        异常 / Raises:
            RuntimeError: 非 macOS、已有任务或请求无有效候选。
            ValueError: 相册名、批次上限或 argv 字节上限无效。
        """

        if sys.platform != "darwin":
            raise RuntimeError("Apple Photos import is available only on macOS")
        if self.is_running:
            raise RuntimeError("Apple Photos import is already running")
        album_name = sanitize_album_name(request.album_name)
        if not album_name:
            raise ValueError("Apple Photos album name cannot be empty")
        if not request.candidates:
            raise ValueError("Apple Photos import requires at least one candidate")

        batches = build_import_batches(
            album_name,
            request.candidates,
            batch_size=request.batch_size,
            max_argument_bytes=request.max_argument_bytes,
        )
        self._request = ApplePhotosImportRequest(
            album_name=album_name,
            candidates=request.candidates,
            requested=request.requested,
            preflight_skipped=request.preflight_skipped,
            batch_size=request.batch_size,
            max_argument_bytes=request.max_argument_bytes,
        )
        self._batches = batches
        self._next_batch_index = 0
        self._current_batch_size = 0
        self._attempted = 0
        self._newly_imported = 0
        self._completed_batches = 0
        self._metadata_applied = 0
        self._metadata_not_applied = 0
        self._cancel_requested = False
        self._completion_emitted = False
        self._suppress_completion = False
        self.progress.emit(0, len(request.candidates))
        self._start_next_batch()

    def cancel(self) -> None:
        """取消后续批次并终止当前 helper。/ Cancel remaining work and terminate the helper."""

        if not self.is_running:
            return
        self._cancel_requested = True
        if self._process.state() == QProcess.NotRunning:
            self._finish(cancelled=True)
            return
        self._process.terminate()
        QTimer.singleShot(1500, self._kill_if_running)

    def shutdown(self) -> None:
        """
        确定性停止外部进程，供窗口退出调用。/ Deterministically stop the helper on exit.
        """

        self._suppress_completion = True
        self._cancel_requested = True
        if self._process.state() != QProcess.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1500):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._request = None
        self._completion_emitted = True

    def _start_next_batch(self) -> None:
        """启动下一批，或在全部完成后汇总。/ Start the next batch or finalize."""

        if self._cancel_requested:
            self._finish(cancelled=True)
            return
        if self._request is None:
            return
        if self._next_batch_index >= len(self._batches):
            self._finish(cancelled=False)
            return

        batch = self._batches[self._next_batch_index]
        self._next_batch_index += 1
        self._current_batch_size = len(batch)
        self._attempted += self._current_batch_size
        arguments = build_osascript_arguments(self._request.album_name, batch)
        self._process.start(self._osascript_path, arguments)

    def _on_process_finished(
        self, exit_code: int, _exit_status: QProcess.ExitStatus
    ) -> None:
        """解析一个批次的 Photos 返回值。/ Parse one completed Photos batch."""

        if not self.is_running:
            return
        if self._cancel_requested:
            self._finish(cancelled=True)
            return

        stdout = (
            bytes(self._process.readAllStandardOutput())
            .decode("utf-8", errors="replace")
            .strip()
        )
        stderr = (
            bytes(self._process.readAllStandardError())
            .decode("utf-8", errors="replace")
            .strip()
        )
        if exit_code != 0:
            self._finish(
                cancelled=False,
                error=stderr or f"osascript exited with code {exit_code}",
            )
            return

        try:
            response_fields = stdout.splitlines()[-1].split("\t")
            if len(response_fields) != 3:
                raise ValueError
            imported_count, metadata_applied, metadata_not_applied = (
                int(value) for value in response_fields
            )
        except (IndexError, ValueError):
            self._finish(
                cancelled=False,
                error=f"Unexpected Photos response: {stdout or '<empty>'}",
            )
            return
        if not 0 <= imported_count <= self._current_batch_size:
            self._finish(
                cancelled=False, error=f"Invalid Photos import count: {imported_count}"
            )
            return
        if (
            metadata_applied < 0
            or metadata_not_applied < 0
            or metadata_applied + metadata_not_applied != imported_count
        ):
            self._finish(
                cancelled=False,
                error=(
                    "Invalid Photos metadata counts: "
                    f"{metadata_applied} applied, "
                    f"{metadata_not_applied} not applied"
                ),
            )
            return

        self._newly_imported += imported_count
        self._metadata_applied += metadata_applied
        self._metadata_not_applied += metadata_not_applied
        self._completed_batches += 1
        self.progress.emit(self._attempted, len(self._request.candidates))
        self._start_next_batch()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        """处理无法启动 helper 的路径。/ Handle helper startup failure."""

        if not self.is_running or error != QProcess.FailedToStart:
            return
        self._finish(cancelled=False, error=self._process.errorString())

    def _kill_if_running(self) -> None:
        """取消宽限期后强制结束 helper。/ Kill the helper after the cancellation grace period."""

        if self._cancel_requested and self._process.state() != QProcess.NotRunning:
            self._process.kill()

    def _finish(self, *, cancelled: bool, error: str | None = None) -> None:
        """构造并仅发射一次最终结果。/ Build and emit one final result."""

        if self._request is None or self._completion_emitted:
            return
        self._completion_emitted = True
        request = self._request
        eligible = len(request.candidates)
        result = ApplePhotosImportResult(
            requested=request.requested,
            eligible=eligible,
            attempted=self._attempted,
            newly_imported=self._newly_imported,
            preflight_skipped=request.preflight_skipped,
            photos_not_imported=max(0, self._attempted - self._newly_imported),
            remaining=max(0, eligible - self._attempted),
            completed_batches=self._completed_batches,
            metadata_applied=self._metadata_applied,
            metadata_not_applied=self._metadata_not_applied,
            cancelled=cancelled,
            error=error,
        )
        self._request = None
        if not self._suppress_completion:
            self.completed.emit(result)
