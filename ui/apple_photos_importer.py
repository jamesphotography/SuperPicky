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
# 与 _SIBLING_RAW_EXTENSIONS 一样从 constants 派生，而非手写字面量：以前这里
# 硬编码了 10 个扩展名，constants 新增显示格式时不会同步。分组顺序保持
# 「先 JPG 系列后 HEIF 系列」，组内优先级跟随 constants 的定义顺序。
# Derived from constants like _SIBLING_RAW_EXTENSIONS instead of hand-written
# literals, which would not follow new display formats added to constants. JPG
# variants still precede HEIF ones; within each group the order follows constants.
_SIBLING_DISPLAY_EXTENSIONS = tuple(
    extension
    for group in (JPG_EXTENSIONS, HEIF_EXTENSIONS)
    for extension in (
        *(base.lower() for base in group),
        *(base.upper() for base in group),
    )
)
_IMPORT_FOLDER_NAME = "SuperPicky Imports"
# 普通取消后等待当前 helper 自行返回的宽限期；超时则强制终止。
# Photos 可能在等待权限授权对话框而永不返回，此时若只设标志位，进度框会一直
# 停在原地，用户除了强退应用别无选择。
# Grace period after a normal cancel before the helper is force-terminated.
# Photos can block indefinitely on a permission dialog; without this timeout a
# cancelled import would hang the progress dialog with no way out but force-quit.
_CANCEL_GRACE_MS = 5000
_DEFAULT_BATCH_SIZE = 100
_DEFAULT_MAX_ARGUMENT_BYTES = 256 * 1024
_MAX_AUTOMATIC_METADATA_REPAIR_PASSES = 2
_METADATA_TITLE = 1
_METADATA_DESCRIPTION = 2
_METADATA_KEYWORDS = 4


_APPLE_PHOTOS_IMPORT_SCRIPT = r"""
on joinLines(outputLines)
    set previousDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to linefeed
    set outputText to outputLines as text
    set AppleScript's text item delimiters to previousDelimiters
    return outputText
end joinLines

on applyMetadata(targetItem, candidateRecord, requestedMask)
    tell application "/System/Applications/Photos.app"
        set successfulMask to 0
        set failedMask to 0

        if requestedMask mod 2 is 1 then
            try
                set name of targetItem to item 6 of candidateRecord
                set successfulMask to successfulMask + 1
            on error
                set failedMask to failedMask + 1
            end try
        end if

        if (requestedMask div 2) mod 2 is 1 then
            try
                set description of targetItem to item 7 of candidateRecord
                set successfulMask to successfulMask + 2
            on error
                set failedMask to failedMask + 2
            end try
        end if

        if (requestedMask div 4) mod 2 is 1 then
            try
                set metadataKeywords to item 8 of candidateRecord
                set mergedKeywords to keywords of targetItem
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
                set keywords of targetItem to mergedKeywords
                set successfulMask to successfulMask + 4
            on error
                set failedMask to failedMask + 4
            end try
        end if

    end tell
    return {successfulMask, failedMask}
end applyMetadata

on run argv
    if (count of argv) < 2 then error "Missing operation or candidate count"
    set operationName to item 1 of argv

    if operationName is "import" then
        if (count of argv) < 5 then error "Missing folder name, album name, candidate count, or import files"
        set targetFolderName to item 2 of argv
        set targetAlbumName to item 3 of argv
        set candidateCount to (item 4 of argv) as integer
        set argumentIndex to 5
    else if operationName is "repair" then
        set candidateCount to (item 2 of argv) as integer
        set argumentIndex to 3
    else
        error "Unsupported Apple Photos operation"
    end if

    set sourceFiles to {}
    set candidateRecords to {}

    repeat candidateCount times
        set candidateIndex to (item argumentIndex of argv) as integer
        set sourceReference to item (argumentIndex + 1) of argv
        set expectedFilename to item (argumentIndex + 2) of argv
        set expectedSize to (item (argumentIndex + 3) of argv) as integer
        set metadataMask to (item (argumentIndex + 4) of argv) as integer
        set metadataTitle to item (argumentIndex + 5) of argv
        set metadataDescription to item (argumentIndex + 6) of argv
        set keywordCount to (item (argumentIndex + 7) of argv) as integer
        set metadataKeywords to {}
        repeat with keywordOffset from 1 to keywordCount
            set end of metadataKeywords to item (argumentIndex + 7 + keywordOffset) of argv
        end repeat

        if operationName is "import" then set end of sourceFiles to POSIX file sourceReference
        set end of candidateRecords to {candidateIndex, sourceReference, expectedFilename, expectedSize, metadataMask, metadataTitle, metadataDescription, metadataKeywords, false}
        set argumentIndex to argumentIndex + 8 + keywordCount
    end repeat

    tell application "/System/Applications/Photos.app"
        if operationName is "repair" then
            set outputLines to {"REPAIR" & tab & (candidateCount as text)}
            repeat with candidateRecord in candidateRecords
                set candidateIndex to item 1 of candidateRecord
                set mediaItemID to item 2 of candidateRecord
                set requestedMask to item 5 of candidateRecord
                try
                    set targetItem to media item id mediaItemID
                    set maskResult to my applyMetadata(targetItem, candidateRecord, requestedMask)
                    set end of outputLines to "ITEM" & tab & (candidateIndex as text) & tab & mediaItemID & tab & ((item 1 of maskResult) as text) & tab & ((item 2 of maskResult) as text)
                on error
                    set end of outputLines to "ITEM" & tab & (candidateIndex as text) & tab & mediaItemID & tab & "0" & tab & (requestedMask as text)
                end try
            end repeat
            return my joinLines(outputLines)
        else
            activate
            if exists folder targetFolderName then
                set targetFolder to folder targetFolderName
            else
                set targetFolder to make new folder named targetFolderName
            end if

            set matchingAlbums to every album of targetFolder whose name is targetAlbumName
            if (count of matchingAlbums) > 0 then
                set targetAlbum to item 1 of matchingAlbums
            else
                set targetAlbum to make new album named targetAlbumName at targetFolder
            end if

            set importedItems to import sourceFiles into targetAlbum skip check duplicates false
            set outputLines to {"IMPORT" & tab & ((count of importedItems) as text)}

            repeat with importedItem in importedItems
                set matchingCandidate to missing value
                set importedFilename to filename of importedItem
                set importedSize to (size of importedItem) as integer

                repeat with candidateRecord in candidateRecords
                    if (item 9 of candidateRecord) is false then
                        if (item 3 of candidateRecord) is importedFilename and (item 4 of candidateRecord) is importedSize then
                            set matchingCandidate to candidateRecord
                            exit repeat
                        end if
                    end if
                end repeat

                if matchingCandidate is missing value and candidateCount is 1 and (count of importedItems) is 1 then
                    set matchingCandidate to item 1 of candidateRecords
                end if

                set mediaItemID to id of importedItem as text
                if matchingCandidate is missing value then
                    set end of outputLines to "ITEM" & tab & "-1" & tab & mediaItemID & tab & "0" & tab & "0"
                else
                    set item 9 of matchingCandidate to true
                    set requestedMask to item 5 of matchingCandidate
                    set maskResult to my applyMetadata(importedItem, matchingCandidate, requestedMask)
                    set end of outputLines to "ITEM" & tab & ((item 1 of matchingCandidate) as text) & tab & mediaItemID & tab & ((item 1 of maskResult) as text) & tab & ((item 2 of maskResult) as text)
                end if
            end repeat

            return my joinLines(outputLines)
        end if
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
    indeterminate: int
    completed_batches: int
    metadata_applied: int
    metadata_partially_applied: int
    metadata_not_applied: int
    retryable_metadata: int
    cancelled: bool
    error: str | None = None


@dataclass(frozen=True)
class _MetadataRepairTarget:
    """
    通过精确 Photos ID 重试失败字段的内部记录。

    Internal record for retrying failed fields through an exact Photos ID.
    """

    candidate_index: int
    photos_id: str
    successful_mask: int
    failed_mask: int


@dataclass(frozen=True)
class _HelperItemResult:
    """单个 helper 条目响应。/ One item returned by the helper."""

    candidate_index: int
    photos_id: str
    successful_mask: int
    failed_mask: int


@dataclass(frozen=True)
class _HelperResponse:
    """已验证的 helper 批次响应。/ One validated helper batch response."""

    operation: str
    reported_count: int
    items: tuple[_HelperItemResult, ...]


def _metadata_mask(metadata: ApplePhotosMetadata) -> int:
    """计算有值的 Photos 元数据字段掩码。/ Return the populated metadata field mask."""

    mask = 0
    if metadata.title is not None:
        mask |= _METADATA_TITLE
    if metadata.description is not None:
        mask |= _METADATA_DESCRIPTION
    if metadata.keywords:
        mask |= _METADATA_KEYWORDS
    return mask


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
    album_name: str,
    candidates: Sequence[ImportCandidate],
    candidate_indices: Sequence[int] | None = None,
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
    indices = tuple(
        range(len(candidates)) if candidate_indices is None else candidate_indices
    )
    if len(indices) != len(candidates):
        raise ValueError("Apple Photos candidate indices do not match the batch")
    arguments = [
        "-e",
        _APPLE_PHOTOS_IMPORT_SCRIPT,
        "--",
        "import",
        # 文件夹名同样经 argv 传入，与相册名、路径一致。此前脚本里硬编码了三处
        # "SuperPicky Imports"，与本模块的 _IMPORT_FOLDER_NAME 构成两份真相：
        # 改了常量而漏改脚本，照片会被导进另一个文件夹且不报任何错。
        # The folder name travels through argv like the album name and paths.
        # It used to be hard-coded three times inside the script while this
        # module also defined _IMPORT_FOLDER_NAME — two sources of truth, where
        # updating only the constant would silently import into a different
        # folder.
        _IMPORT_FOLDER_NAME,
        clean_name,
        str(len(candidates)),
    ]
    for candidate_index, candidate in zip(indices, candidates, strict=True):
        metadata = candidate.metadata
        arguments.extend(
            [
                str(candidate_index),
                os.fspath(candidate.path),
                candidate.path.name,
                str(candidate.source_size),
                str(_metadata_mask(metadata)),
                metadata.title or "",
                metadata.description or "",
                str(len(metadata.keywords)),
                *metadata.keywords,
            ]
        )
    return arguments


def _build_repair_arguments(
    candidates: Sequence[ImportCandidate],
    targets: Sequence[_MetadataRepairTarget],
) -> list[str]:
    """
    为精确 Photos ID 元数据修复构造静态脚本参数。

    Build static-script arguments for metadata repair by exact Photos ID.
    """

    if not targets:
        raise ValueError("Apple Photos metadata repair batch cannot be empty")
    arguments = [
        "-e",
        _APPLE_PHOTOS_IMPORT_SCRIPT,
        "--",
        "repair",
        str(len(targets)),
    ]
    for target in targets:
        candidate = candidates[target.candidate_index]
        metadata = candidate.metadata
        arguments.extend(
            [
                str(target.candidate_index),
                target.photos_id,
                "",
                "0",
                str(target.failed_mask),
                metadata.title or "",
                metadata.description or "",
                str(len(metadata.keywords)),
                *metadata.keywords,
            ]
        )
    return arguments


def _parse_helper_response(stdout: str) -> _HelperResponse:
    """
    解析并验证 helper 的逐条结构化响应。

    Parse and validate the helper's structured item-by-item response.

    异常 / Raises:
        ValueError: 响应为空、字段损坏、掩码冲突或条目数量不一致。
    """

    lines = [line for line in stdout.splitlines() if line]
    if not lines:
        raise ValueError("empty response")
    header = lines[0].split("\t")
    if len(header) != 2 or header[0] not in {"IMPORT", "REPAIR"}:
        raise ValueError("invalid response header")
    try:
        reported_count = int(header[1])
    except ValueError as error:
        raise ValueError("invalid response count") from error
    items: list[_HelperItemResult] = []
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 5 or fields[0] != "ITEM":
            raise ValueError("invalid item response")
        try:
            candidate_index = int(fields[1])
            successful_mask = int(fields[3])
            failed_mask = int(fields[4])
        except ValueError as error:
            raise ValueError("invalid item value") from error
        if (
            successful_mask < 0
            or failed_mask < 0
            or successful_mask & failed_mask
            or (successful_mask | failed_mask)
            > (_METADATA_TITLE | _METADATA_DESCRIPTION | _METADATA_KEYWORDS)
        ):
            raise ValueError("invalid metadata field masks")
        items.append(
            _HelperItemResult(
                candidate_index=candidate_index,
                photos_id=fields[2],
                successful_mask=successful_mask,
                failed_mask=failed_mask,
            )
        )
    if len(items) != reported_count:
        raise ValueError("response item count does not match header")
    return _HelperResponse(
        operation=header[0].lower(),
        reported_count=reported_count,
        items=tuple(items),
    )


def _argument_bytes(album_name: str, candidates: Sequence[ImportCandidate]) -> int:
    """
    估算 exec argv UTF-8 字节数(精确实现)。

    这是字节预算的权威定义。批次切分不直接调用它——那样每加入一个候选就要重建
    整个 argv 并把 4.5KB 的 AppleScript 源重新编码一遍，整体退化为 O(n²)；切分
    改用下面的增量分解，并由测试保证两者结果一致。

    Exact argv byte size — the authoritative definition of the budget. Batching
    does not call this per candidate (that would rebuild the whole argv and
    re-encode the 4.5KB script each time, making it O(n²)); it uses the
    incremental decomposition below, which tests pin to this function.
    """

    return sum(
        len(argument.encode("utf-8")) + 1
        for argument in build_osascript_arguments(album_name, candidates)
    )


# AppleScript 源固定不变，其字节数只需在模块加载时算一次。
# The script is a constant; encode it once at import time.
_SCRIPT_ARGUMENT_BYTES = len(_APPLE_PHOTOS_IMPORT_SCRIPT.encode("utf-8")) + 1


def _text_bytes(value: str) -> int:
    """单个 argv 项的字节数(含 NUL 分隔符)。/ Bytes of one argv entry incl. NUL."""

    return len(value.encode("utf-8")) + 1


def _import_header_bytes(album_name: str) -> int:
    """
    import 操作的固定头部字节数(不含 candidateCount 字段)。

    对应 argv 前缀 ``-e SCRIPT -- import FOLDER ALBUM``。相册名在此处同样经
    sanitize_album_name 规范化，与 build_osascript_arguments 保持一致。

    Fixed header bytes for an import (argv prefix ``-e SCRIPT -- import FOLDER
    ALBUM``), excluding the candidateCount field.
    """

    return (
        _text_bytes("-e")
        + _SCRIPT_ARGUMENT_BYTES
        + _text_bytes("--")
        + _text_bytes("import")
        + _text_bytes(_IMPORT_FOLDER_NAME)
        + _text_bytes(sanitize_album_name(album_name))
    )


def _candidate_payload_bytes(candidate: ImportCandidate) -> int:
    """
    单个候选除下标外的字节数。

    这部分只取决于候选自身，与它落在批次中的哪个位置无关，因此可以在切分前
    一次性预计算并在累加中复用。

    Per-candidate bytes excluding its index field. Independent of the candidate's
    position in a batch, so it can be precomputed once and reused while packing.
    """

    metadata = candidate.metadata
    total = (
        _text_bytes(os.fspath(candidate.path))
        + _text_bytes(candidate.path.name)
        + _text_bytes(str(candidate.source_size))
        + _text_bytes(str(_metadata_mask(metadata)))
        + _text_bytes(metadata.title or "")
        + _text_bytes(metadata.description or "")
        + _text_bytes(str(len(metadata.keywords)))
    )
    for keyword in metadata.keywords:
        total += _text_bytes(keyword)
    return total


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

    # argv 字节按「固定头部 + Σ(下标字段 + 候选载荷)」分解，各项只算一次，
    # 装箱时增量累加，避免每加入一个候选就重建整个 argv(含 4.5KB 脚本源)。
    # 与 _argument_bytes 的等价性由 test_incremental_byte_accounting_matches_exact
    # 保证。/ Decompose argv bytes into a fixed header plus per-candidate terms,
    # each computed once, so packing is linear instead of quadratic.
    header_bytes = _import_header_bytes(album_name)
    payload_bytes = [_candidate_payload_bytes(candidate) for candidate in candidates]

    current_payload = 0
    current_index_bytes = 0

    def batch_bytes(extra_payload: int, size: int) -> int:
        """当前批次再加入一个候选后的 argv 字节数。/ argv bytes after adding one."""

        return (
            header_bytes
            + _text_bytes(str(size))
            + current_payload
            + extra_payload
            + current_index_bytes
            + _text_bytes(str(size - 1))
        )

    def flush_current() -> None:
        nonlocal current_payload, current_index_bytes
        if current:
            batches.append(tuple(current))
            current.clear()
            current_payload = 0
            current_index_bytes = 0

    for position, candidate in enumerate(candidates):
        candidate_payload = payload_bytes[position]
        match_key = (candidate.path.name.casefold(), candidate.source_size)
        if match_key_counts[match_key] > 1:
            flush_current()
            if batch_bytes(candidate_payload, 1) > max_argument_bytes:
                raise ValueError(
                    f"Apple Photos metadata is too large for {candidate.path.name}"
                )
            batches.append((candidate,))
            continue

        if current and (
            len(current) + 1 > batch_size
            or batch_bytes(candidate_payload, len(current) + 1) > max_argument_bytes
        ):
            flush_current()
        if batch_bytes(candidate_payload, len(current) + 1) > max_argument_bytes:
            raise ValueError(
                f"Apple Photos metadata is too large for {candidate.path.name}"
            )
        current_index_bytes += _text_bytes(str(len(current)))
        current_payload += candidate_payload
        current.append(candidate)

    flush_current()
    return batches


def _build_repair_batches(
    candidates: Sequence[ImportCandidate],
    targets: Sequence[_MetadataRepairTarget],
    *,
    batch_size: int,
    max_argument_bytes: int,
) -> list[tuple[_MetadataRepairTarget, ...]]:
    """按数量和 argv 字节预算拆分元数据修复。/ Batch metadata repairs safely."""

    batches: list[tuple[_MetadataRepairTarget, ...]] = []
    current: list[_MetadataRepairTarget] = []
    for target in targets:
        proposed = [*current, target]
        proposed_bytes = sum(
            len(argument.encode("utf-8")) + 1
            for argument in _build_repair_arguments(candidates, proposed)
        )
        if current and (
            len(proposed) > batch_size or proposed_bytes > max_argument_bytes
        ):
            batches.append(tuple(current))
            current = [target]
        else:
            current = proposed
        single_bytes = sum(
            len(argument.encode("utf-8")) + 1
            for argument in _build_repair_arguments(candidates, current)
        )
        if single_bytes > max_argument_bytes:
            raise ValueError(
                "Apple Photos metadata repair arguments exceed the safe byte limit"
            )
    if current:
        batches.append(tuple(current))
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
    使用单个 QProcess 顺序导入并按精确 Photos ID 修复元数据。

    Import sequentially with one QProcess and repair metadata by exact Photos ID.

    普通取消会等待当前批次返回，以保留可信计数；窗口关闭仍同步强制清理。
    Normal cancellation lets the active batch report a trustworthy outcome;
    window shutdown still performs deterministic hard cleanup.
    """

    progress = Signal(int, int)
    completed = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        osascript_path: str = "/usr/bin/osascript",
        cancel_grace_ms: int = _CANCEL_GRACE_MS,
    ):
        super().__init__(parent)
        self._osascript_path = osascript_path
        # 取消后的宽限期，可注入以便测试无需真等数秒。
        # Injectable so tests need not wait the full production grace period.
        self._cancel_grace_ms = cancel_grace_ms
        self._process = QProcess(self)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._request: ApplePhotosImportRequest | None = None
        self._batches: list[tuple[ImportCandidate, ...]] = []
        self._batch_indices: list[tuple[int, ...]] = []
        self._next_batch_index = 0
        self._current_import_batch: tuple[ImportCandidate, ...] = ()
        self._current_candidate_indices: tuple[int, ...] = ()
        self._repair_batches: list[tuple[_MetadataRepairTarget, ...]] = []
        self._next_repair_batch_index = 0
        self._current_repair_batch: tuple[_MetadataRepairTarget, ...] = ()
        self._repair_pass = 0
        self._phase = "idle"
        self._confirmed_processed = 0
        self._confirmed_not_imported = 0
        self._indeterminate = 0
        self._newly_imported = 0
        self._completed_batches = 0
        self._metadata_targets: dict[int, _MetadataRepairTarget] = {}
        self._unmatched_metadata_items = 0
        self._cancel_requested = False
        self._completion_emitted = False
        self._suppress_completion = False
        self._cancel_timer: QTimer | None = None

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
        self._batch_indices = self._derive_batch_indices(
            batches, len(request.candidates)
        )
        self._next_batch_index = 0
        self._current_import_batch = ()
        self._current_candidate_indices = ()
        self._repair_batches = []
        self._next_repair_batch_index = 0
        self._current_repair_batch = ()
        self._repair_pass = 0
        self._phase = "import"
        self._confirmed_processed = 0
        self._confirmed_not_imported = 0
        self._indeterminate = 0
        self._newly_imported = 0
        self._completed_batches = 0
        self._metadata_targets = {}
        self._unmatched_metadata_items = 0
        self._cancel_requested = False
        self._completion_emitted = False
        self._suppress_completion = False
        self._stop_cancel_grace_timer()
        self.progress.emit(0, len(request.candidates))
        self._start_next_import_batch()

    def cancel(self) -> None:
        """
        当前 helper 完成后停止，不再启动后续批次。

        优先让活动批次自行返回以保留可信计数；但 helper 可能永不返回(例如
        Photos 正在等待权限授权对话框)，因此设置 _CANCEL_GRACE_MS 宽限期，
        超时后强制终止，避免进度框永久卡住、用户只能强退应用。

        Stop after the active helper completes, without starting another batch.
        The active batch is given _CANCEL_GRACE_MS to report a trustworthy
        outcome; if it never returns (e.g. Photos is blocked on a permission
        dialog) it is force-terminated so the UI cannot hang forever.
        """

        if not self.is_running:
            return
        self._cancel_requested = True
        if self._process.state() == QProcess.NotRunning:
            self._finish(cancelled=True)
            return
        self._start_cancel_grace_timer()

    def _start_cancel_grace_timer(self) -> None:
        """启动取消宽限计时器(幂等)。/ Arm the cancel grace timer (idempotent)."""

        if self._cancel_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._force_stop_after_cancel)
        timer.start(self._cancel_grace_ms)
        self._cancel_timer = timer

    def _stop_cancel_grace_timer(self) -> None:
        """停止并释放宽限计时器。/ Disarm and release the grace timer."""

        timer = self._cancel_timer
        self._cancel_timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _force_stop_after_cancel(self) -> None:
        """
        宽限期结束仍未返回：强制终止 helper。

        走与 shutdown() 相同的 terminate→kill 路径。终止会触发 finished 信号，
        由 _on_process_finished 把该批次记为不确定并收尾，因此这里不直接
        _finish，以免重复计数。

        Force-terminate a helper that outlived the grace period, using the same
        terminate→kill path as shutdown(). The resulting finished signal lets
        _on_process_finished mark the batch indeterminate and wrap up, so this
        method deliberately does not call _finish itself.
        """

        self._cancel_timer = None
        if not self.is_running:
            return
        if self._process.state() == QProcess.NotRunning:
            self._finish(cancelled=True)
            return
        self._process.terminate()
        if not self._process.waitForFinished(1500):
            self._process.kill()
            self._process.waitForFinished(1000)

    @property
    def has_retryable_metadata(self) -> bool:
        """返回是否仍有可按 ID 修复的字段。/ Return whether exact-ID repairs remain."""

        return bool(self._failed_metadata_targets())

    def retry_metadata(self) -> None:
        """
        仅重试已导入条目的失败字段，不重新导入文件。

        Retry failed fields on imported items without importing source files again.

        异常 / Raises:
            RuntimeError: 导入仍在运行，或没有可修复元数据。
        """

        if self.is_running:
            raise RuntimeError("Apple Photos import is still running")
        if self._request is None or not self.has_retryable_metadata:
            raise RuntimeError("No Apple Photos metadata is available to retry")
        self._cancel_requested = False
        self._completion_emitted = False
        self._suppress_completion = False
        self._repair_pass = 0
        self._phase = "repair"
        self._stop_cancel_grace_timer()
        self._begin_repair_pass()

    def shutdown(self) -> None:
        """
        确定性停止外部进程，供窗口退出调用。/ Deterministically stop the helper on exit.
        """

        self._suppress_completion = True
        self._cancel_requested = True
        self._stop_cancel_grace_timer()
        if self._process.state() != QProcess.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(1500):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._request = None
        self._completion_emitted = True
        self._phase = "idle"

    def _start_next_import_batch(self) -> None:
        """启动下一导入批次，或转入元数据修复。/ Start import or begin repair."""

        if self._cancel_requested:
            self._finish(cancelled=True)
            return
        if self._request is None:
            return
        if self._next_batch_index >= len(self._batches):
            self._begin_repair_pass()
            return

        batch = self._batches[self._next_batch_index]
        indices = self._batch_indices[self._next_batch_index]
        self._next_batch_index += 1
        self._current_import_batch = batch
        self._current_candidate_indices = indices
        self._phase = "import"
        arguments = build_osascript_arguments(
            self._request.album_name,
            batch,
            indices,
        )
        self._process.start(self._osascript_path, arguments)

    @staticmethod
    def _derive_batch_indices(
        batches: Sequence[tuple[ImportCandidate, ...]], total: int
    ) -> list[tuple[int, ...]]:
        """
        由批次长度推导每批候选在原序列中的下标。

        build_import_batches 产出的是候选序列的**有序连续划分**：既不重排也不
        重复或遗漏，因此第 k 批的下标就是紧接前一批之后的一段连续区间。以位置
        推导取代原先的 ``{id(candidate): index}`` 映射，既去掉了每批重建全量
        字典的 O(n²) 开销，也消除了同一候选对象被引用两次时下标被覆盖、元数据
        写到错误照片上的隐患。

        Derive each batch's indices from batch lengths. build_import_batches
        returns an ordered, contiguous partition of the candidate sequence, so
        batch k occupies the next contiguous slice. This replaces a per-batch
        ``{id(candidate): index}`` map, removing both its O(n²) cost and the
        risk of a duplicated candidate object silently colliding in that map.

        参数 / Parameters:
            batches: 已切分的批次。/ The batches produced for this request.
            total: 候选总数。/ Total number of candidates.

        返回 / Returns:
            list[tuple[int, ...]]: 与 batches 一一对应的下标元组。

        异常 / Raises:
            ValueError: 批次总长与候选总数不符(划分前提被破坏)。
        """

        indices: list[tuple[int, ...]] = []
        cursor = 0
        for batch in batches:
            indices.append(tuple(range(cursor, cursor + len(batch))))
            cursor += len(batch)
        if cursor != total:
            raise ValueError(
                "Apple Photos batches are not a contiguous partition of the "
                f"candidates ({cursor} != {total})"
            )
        return indices

    def _failed_metadata_targets(self) -> tuple[_MetadataRepairTarget, ...]:
        """返回仍有失败字段且具有 Photos ID 的目标。/ Return retryable failures."""

        return tuple(
            target
            for target in self._metadata_targets.values()
            if target.failed_mask and target.photos_id
        )

    def _begin_repair_pass(self) -> None:
        """启动一次自动修复；达到上限后完成。/ Begin one bounded repair pass."""

        if self._request is None:
            return
        targets = self._failed_metadata_targets()
        if not targets or self._repair_pass >= _MAX_AUTOMATIC_METADATA_REPAIR_PASSES:
            self._finish(cancelled=False)
            return
        self._repair_pass += 1
        try:
            self._repair_batches = _build_repair_batches(
                self._request.candidates,
                targets,
                batch_size=self._request.batch_size,
                max_argument_bytes=self._request.max_argument_bytes,
            )
        except ValueError as error:
            self._finish(cancelled=False, error=str(error))
            return
        self._next_repair_batch_index = 0
        self._current_repair_batch = ()
        self._phase = "repair"
        self.progress.emit(0, len(targets))
        self._start_next_repair_batch()

    def _start_next_repair_batch(self) -> None:
        """启动下一个精确 ID 修复批次。/ Start the next exact-ID repair batch."""

        if self._cancel_requested:
            self._finish(cancelled=True)
            return
        if self._request is None:
            return
        if self._next_repair_batch_index >= len(self._repair_batches):
            self._begin_repair_pass()
            return
        batch = self._repair_batches[self._next_repair_batch_index]
        self._next_repair_batch_index += 1
        self._current_repair_batch = batch
        arguments = _build_repair_arguments(self._request.candidates, batch)
        self._process.start(self._osascript_path, arguments)

    def _on_process_finished(
        self, exit_code: int, _exit_status: QProcess.ExitStatus
    ) -> None:
        """解析一个批次的 Photos 返回值。/ Parse one completed Photos batch."""

        if not self.is_running:
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
            self._mark_current_import_indeterminate()
            self._finish(
                cancelled=self._cancel_requested,
                error=stderr or f"osascript exited with code {exit_code}",
            )
            return

        try:
            response = _parse_helper_response(stdout)
            if self._phase == "import":
                self._accept_import_response(response)
            elif self._phase == "repair":
                self._accept_repair_response(response)
            else:
                raise ValueError("helper completed outside an active phase")
        except ValueError as error:
            self._mark_current_import_indeterminate()
            self._finish(
                cancelled=self._cancel_requested,
                error=(
                    f"Unexpected Photos response: {stdout or '<empty>'} " f"({error})"
                ),
            )
            return

        if self._cancel_requested:
            self._finish(cancelled=True)
            return
        if self._phase == "import":
            self._start_next_import_batch()
        else:
            self._start_next_repair_batch()

    def _accept_import_response(self, response: _HelperResponse) -> None:
        """验证并累计一个导入批次。/ Validate and aggregate one import batch."""

        if self._request is None or response.operation != "import":
            raise ValueError("operation does not match import phase")
        batch_size = len(self._current_import_batch)
        if not 0 <= response.reported_count <= batch_size:
            raise ValueError("invalid Photos import count")
        valid_indices = set(self._current_candidate_indices)
        seen_indices: set[int] = set()
        for item in response.items:
            if not item.photos_id:
                raise ValueError("imported Photos item has no ID")
            if item.candidate_index == -1:
                if item.successful_mask or item.failed_mask:
                    raise ValueError("unmatched Photos item returned metadata masks")
                self._unmatched_metadata_items += 1
                continue
            if (
                item.candidate_index not in valid_indices
                or item.candidate_index in seen_indices
            ):
                raise ValueError("Photos item returned an invalid candidate index")
            seen_indices.add(item.candidate_index)
            expected_mask = _metadata_mask(
                self._request.candidates[item.candidate_index].metadata
            )
            if item.successful_mask | item.failed_mask != expected_mask:
                raise ValueError("initial metadata masks do not match requested fields")
            self._metadata_targets[item.candidate_index] = _MetadataRepairTarget(
                candidate_index=item.candidate_index,
                photos_id=item.photos_id,
                successful_mask=item.successful_mask,
                failed_mask=item.failed_mask,
            )
        self._newly_imported += response.reported_count
        self._confirmed_processed += batch_size
        self._confirmed_not_imported += batch_size - response.reported_count
        self._completed_batches += 1
        self.progress.emit(
            self._confirmed_processed,
            len(self._request.candidates),
        )
        self._current_import_batch = ()
        self._current_candidate_indices = ()

    def _accept_repair_response(self, response: _HelperResponse) -> None:
        """验证修复结果并只更新原失败字段。/ Validate and merge repair results."""

        if response.operation != "repair":
            raise ValueError("operation does not match repair phase")
        if response.reported_count != len(self._current_repair_batch):
            raise ValueError("metadata repair count does not match batch")
        expected = {
            target.candidate_index: target for target in self._current_repair_batch
        }
        seen: set[int] = set()
        for item in response.items:
            target = expected.get(item.candidate_index)
            if (
                target is None
                or item.candidate_index in seen
                or item.photos_id != target.photos_id
                or item.successful_mask | item.failed_mask != target.failed_mask
            ):
                raise ValueError("metadata repair item does not match its exact target")
            seen.add(item.candidate_index)
            self._metadata_targets[item.candidate_index] = _MetadataRepairTarget(
                candidate_index=item.candidate_index,
                photos_id=item.photos_id,
                successful_mask=target.successful_mask | item.successful_mask,
                failed_mask=item.failed_mask,
            )
        if seen != set(expected):
            raise ValueError("metadata repair response omitted a target")
        completed = sum(
            len(batch)
            for batch in self._repair_batches[: self._next_repair_batch_index]
        )
        self.progress.emit(completed, sum(map(len, self._repair_batches)))
        self._current_repair_batch = ()

    def _mark_current_import_indeterminate(self) -> None:
        """仅把无有效响应的导入批次标为不确定。/ Mark only an unparsed import batch."""

        if self._phase == "import" and self._current_import_batch:
            self._indeterminate += len(self._current_import_batch)
            self._current_import_batch = ()
            self._current_candidate_indices = ()

    def _metadata_counts(self) -> tuple[int, int, int, int]:
        """
        返回完整、部分、失败及可重试条目数。

        Return fully applied, partially applied, failed, and retryable counts.
        """

        applied = 0
        partial = 0
        failed = self._unmatched_metadata_items
        retryable = 0
        for target in self._metadata_targets.values():
            if not target.failed_mask:
                applied += 1
            elif target.successful_mask:
                partial += 1
                retryable += 1
            else:
                failed += 1
                retryable += 1
        return applied, partial, failed, retryable

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        """处理 helper 启动失败，并保留不确定状态。/ Handle helper startup failure."""

        if not self.is_running or error != QProcess.FailedToStart:
            return
        self._mark_current_import_indeterminate()
        self._finish(cancelled=False, error=self._process.errorString())

    def _finish(self, *, cancelled: bool, error: str | None = None) -> None:
        """构造并仅发射一次最终结果。/ Build and emit one final result."""

        if self._request is None or self._completion_emitted:
            return
        self._completion_emitted = True
        # 任务已收尾，撤掉宽限计时器，避免它在下一批次运行时误杀进程。
        # Disarm the grace timer so it cannot kill a later batch's process.
        self._stop_cancel_grace_timer()
        request = self._request
        eligible = len(request.candidates)
        metadata_applied, metadata_partial, metadata_failed, retryable = (
            self._metadata_counts()
        )
        result = ApplePhotosImportResult(
            requested=request.requested,
            eligible=eligible,
            attempted=self._confirmed_processed,
            newly_imported=self._newly_imported,
            preflight_skipped=request.preflight_skipped,
            photos_not_imported=self._confirmed_not_imported,
            remaining=max(
                0,
                eligible - self._confirmed_processed - self._indeterminate,
            ),
            indeterminate=self._indeterminate,
            completed_batches=self._completed_batches,
            metadata_applied=metadata_applied,
            metadata_partially_applied=metadata_partial,
            metadata_not_applied=metadata_failed,
            retryable_metadata=retryable,
            cancelled=cancelled,
            error=error,
        )
        self._phase = "idle"
        if not self._suppress_completion:
            self.completed.emit(result)
