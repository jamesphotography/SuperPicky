# -*- coding: utf-8 -*-
"""
Apple Photos import controller for the macOS results browser.

The module keeps file selection, AppleScript construction, batching, and the
QProcess lifecycle in one place. Paths and album names are passed through
``argv`` rather than interpolated into AppleScript source.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from constants import HEIF_EXTENSIONS, IMAGE_EXTENSIONS, JPG_EXTENSIONS


_DISPLAY_EXTENSIONS = tuple(JPG_EXTENSIONS + HEIF_EXTENSIONS)
_SUPPORTED_EXTENSIONS = frozenset(ext.lower() for ext in IMAGE_EXTENSIONS)
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


_APPLE_PHOTOS_IMPORT_SCRIPT = r"""
on run argv
    if (count of argv) < 2 then error "Missing album name or import files"

    set targetAlbumName to item 1 of argv
    set sourceFiles to {}
    repeat with argumentIndex from 2 to count of argv
        set end of sourceFiles to POSIX file (item argumentIndex of argv)
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
        return (count of importedItems) as text
    end tell
end run
""".strip()


@dataclass(frozen=True)
class ImportCandidate:
    """
    已解析、可导入的单张照片。/ One resolved, importable photo.

    参数 / Parameters:
        path: 要交给 Apple Photos 的真实文件绝对路径。
        photo_identity: SuperPicky 记录身份(source_dir, filename)。

        path: Absolute path passed to Apple Photos.
        photo_identity: SuperPicky record identity (source_dir, filename).
    """

    path: Path
    photo_identity: tuple[str, str]


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
    cancelled: bool
    error: str | None = None


def _photo_identity(photo: dict) -> tuple[str, str]:
    """返回稳定的照片身份键。/ Return a stable photo identity key."""

    return (str(photo.get("source_dir") or ""), str(photo.get("filename") or ""))


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


def resolve_photo_import_path(photo: dict) -> Path | None:
    """
    为照片选择唯一导入文件，优先 JPEG/HEIF，再回退 RAW。

    临时预览和裁剪缓存故意不参与候选。先检查记录本身是否指向显示格式，
    再检查 RAW 同目录同主名的显示边车，最后才回退到原始图像。

    Select one import file for a photo, preferring JPEG/HEIF and falling back
    to RAW. Temporary previews and crop caches are intentionally excluded.

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
        if path.suffix.lower() in _DISPLAY_EXTENSIONS and _is_supported_file(path):
            return path.resolve()

    seen_siblings: set[str] = set()
    for path in base_paths:
        for sibling in _display_siblings(path):
            key = os.path.normcase(os.path.realpath(sibling))
            if key in seen_siblings:
                continue
            seen_siblings.add(key)
            if _is_supported_file(sibling):
                return sibling.resolve()

    for path in base_paths:
        if _is_supported_file(path):
            return path.resolve()
    return None


def preflight_photos(photos: Sequence[dict]) -> ImportPreflight:
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
        seen_paths.add(key)
        candidates.append(
            ImportCandidate(path=path, photo_identity=_photo_identity(photo))
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
    return [
        "-e",
        _APPLE_PHOTOS_IMPORT_SCRIPT,
        "--",
        clean_name,
        *(os.fspath(candidate.path) for candidate in candidates),
    ]


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
            ValueError: 相册名或 batch_size 无效。
        """

        if sys.platform != "darwin":
            raise RuntimeError("Apple Photos import is available only on macOS")
        if self.is_running:
            raise RuntimeError("Apple Photos import is already running")
        album_name = sanitize_album_name(request.album_name)
        if not album_name:
            raise ValueError("Apple Photos album name cannot be empty")
        if request.batch_size <= 0:
            raise ValueError("Apple Photos import batch size must be positive")
        if not request.candidates:
            raise ValueError("Apple Photos import requires at least one candidate")

        self._request = ApplePhotosImportRequest(
            album_name=album_name,
            candidates=request.candidates,
            requested=request.requested,
            preflight_skipped=request.preflight_skipped,
            batch_size=request.batch_size,
        )
        self._batches = [
            tuple(request.candidates[index : index + request.batch_size])
            for index in range(0, len(request.candidates), request.batch_size)
        ]
        self._next_batch_index = 0
        self._current_batch_size = 0
        self._attempted = 0
        self._newly_imported = 0
        self._completed_batches = 0
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
            imported_count = int(stdout.splitlines()[-1])
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

        self._newly_imported += imported_count
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
            cancelled=cancelled,
            error=error,
        )
        self._request = None
        if not self._suppress_completion:
            self.completed.emit(result)
