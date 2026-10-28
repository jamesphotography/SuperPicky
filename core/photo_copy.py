# -*- coding: utf-8 -*-
"""
结果浏览器「复制到文件夹」的核心逻辑（无 Qt 依赖，便于单测）。

一张照片 = 照片本体 + 同名 .xmp 边车 + RAW+JPEG 拍摄的配套相机 JPEG。
CR3 等专有 RAW 的星级和鸟名只写在 .xmp 边车里，只复制 RAW 会丢掉这些信息，
所以边车必须一起复制。附属文件只按本体的目录 + 主名推导，与删除功能
（ui.results_browser_window._photo_companion_files）同一套规则，不信任数据库路径。

目标文件夹里已有同名文件时整组改用「主名 (N)」，同一张照片的几个文件始终
同名配套；绝不覆盖已有文件。复制先写临时文件，完成后再改成正式文件名，
取消或出错都不会留下半截文件。

Core of the results browser's "Copy to Folder" (Qt-free for unit tests). A
photo is its main file, the same-stem .xmp sidecar (proprietary RAW keep
ratings and species only there) and the paired camera JPEG. Name clashes rename
the whole group to "stem (N)" so the files stay paired; nothing is overwritten.
Each file is written to a temp name first and renamed when complete.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from tools.file_utils import sibling_jpeg

# 分块复制的块大小：既能在大 RAW 中途响应取消，又不至于拖慢吞吐
# Chunk size: small enough to honour a cancel mid-file, large enough for throughput
_CHUNK_BYTES = 8 * 1024 * 1024

# 临时文件后缀；以点开头的隐藏名，中断后即使残留也不像一张照片
# Temp suffix; a hidden dot-name so a leftover never looks like a photo
_TMP_SUFFIX = ".superpicky-copy-tmp"


@dataclass(frozen=True)
class CopyJob:
    """
    一个要复制的文件 / One file to copy.

    属性 / Attributes:
        src (str): 源文件绝对路径 / source path.
        dst (str): 目标文件绝对路径 / destination path.
        size (int): 字节数 / size in bytes.
        photo (int): 所属照片在计划里的序号 / index of the photo it belongs to.
    """

    src: str
    dst: str
    size: int
    photo: int = 0


@dataclass
class CopyPlan:
    """
    复制计划 / A copy plan.

    属性 / Attributes:
        jobs (list[CopyJob]): 要复制的文件，同一张照片的文件相邻 / files to copy.
        photo_count (int): 计划复制的照片张数 / photos in the plan.
        renamed (int): 因重名改用「主名 (N)」的照片张数 / photos renamed for a clash.
        missing (int): 本体文件已不存在而跳过的张数 / photos skipped as missing.
    """

    jobs: list[CopyJob] = field(default_factory=list)
    photo_count: int = 0
    renamed: int = 0
    missing: int = 0

    @property
    def total_bytes(self) -> int:
        """全部文件的总字节数 / Total bytes of all files."""
        return sum(job.size for job in self.jobs)


@dataclass
class CopyResult:
    """
    复制结果 / Outcome of a copy run.

    属性 / Attributes:
        copied_files (int): 成功复制的文件数 / files copied.
        copied_photos (int): 全部文件都复制成功的照片张数 / photos fully copied.
        failed (list[tuple[str, str]]): (源文件, 错误信息) / (source, error).
        cancelled (bool): 是否被用户取消 / whether the user cancelled.
    """

    copied_files: int = 0
    copied_photos: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


def photo_copy_sources(main_path: str) -> list[str]:
    """
    一张照片要复制的全部文件：本体在前，再是同名 .xmp 边车与配套相机 JPEG。

    List every file of one photo: the main file first, then the same-stem .xmp
    sidecar and the paired camera JPEG, when they exist.

    参数 / Parameters:
        main_path (str): 照片本体的绝对路径 / the photo's main file.

    返回 / Returns:
        list[str]: 存在的文件；本体不存在时为空 / existing files, empty when the
        main file is gone.
    """
    main = os.path.normpath(main_path or "")
    if not main_path or not os.path.isfile(main):
        return []
    files = [main]
    for path in (os.path.splitext(main)[0] + ".xmp", sibling_jpeg(main) or ""):
        if not path or not os.path.isfile(path):
            continue
        path = _on_disk_name(os.path.normpath(path))
        if _same_file(path, main) or any(_same_file(path, f) for f in files):
            continue
        files.append(path)
    return files


def _on_disk_name(path: str) -> str:
    """
    换成目录里真实的文件名大小写。

    大小写不敏感的文件系统上，「IMG_1.jpg」能找到实际叫「IMG_1.JPG」的文件
    （sibling_jpeg 先试小写），照原样复制会把目标扩展名的大小写改掉。

    Return the path with the file name's on-disk casing. On case-insensitive
    filesystems "IMG_1.jpg" also finds "IMG_1.JPG", and copying under the probed
    name would change the extension's case.

    参数 / Parameters:
        path (str): 已确认存在的文件 / an existing file.

    返回 / Returns:
        str: 真实大小写的路径；读不到目录时原样返回 / on-disk casing, or the input.
    """
    folder, name = os.path.split(path)
    try:
        entries = os.listdir(folder or ".")
    except OSError:
        return path
    if name in entries:
        return path
    lowered = name.lower()
    for entry in entries:
        if entry.lower() == lowered:
            return os.path.join(folder, entry)
    return path


def _same_file(a: str, b: str) -> bool:
    """
    两个路径是否指向同一文件（兼容大小写不敏感的文件系统）。

    Whether two paths name the same file (case-insensitive filesystems included).
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(a) == os.path.normcase(b)


def source_folder_conflict(main_paths: Iterable[str], dest_dir: str) -> bool:
    """
    目标文件夹是否就是某张照片当前所在的文件夹（是则应拒绝复制）。

    Whether the destination is the folder some photo already lives in; copying
    there must be refused.

    参数 / Parameters:
        main_paths (Iterable[str]): 照片本体路径 / main file paths.
        dest_dir (str): 目标文件夹 / destination folder.

    返回 / Returns:
        bool: 冲突为 True / True on a conflict.
    """
    for path in main_paths:
        if path and _same_file(os.path.dirname(os.path.normpath(path)), dest_dir):
            return True
    return False


def plan_photo_copy(main_paths: Sequence[str], dest_dir: str) -> CopyPlan:
    """
    为一组照片制定复制计划：收集附属文件、去重、给重名的照片整组编号。

    重名判断同时看目标文件夹里已有的文件和本次已计划的文件，按大小写不敏感比较
    （Windows / macOS 默认文件系统都不区分大小写）。

    Build the plan: gather companions, deduplicate, and give clashing photos a
    shared "stem (N)" across all their files. Clashes are checked against both
    existing files and names already planned, case-insensitively.

    参数 / Parameters:
        main_paths (Sequence[str]): 照片本体路径（顺序即复制顺序）/ main files.
        dest_dir (str): 目标文件夹 / destination folder.

    返回 / Returns:
        CopyPlan: 复制计划 / the plan.
    """
    plan = CopyPlan()
    try:
        taken = {name.lower() for name in os.listdir(dest_dir)}
    except OSError:
        taken = set()
    planned_sources: list[str] = []

    for main_path in main_paths:
        sources = photo_copy_sources(main_path)
        if not sources:
            plan.missing += 1
            continue
        # 同一个文件已经随别的照片计划过（例如 JPEG 被单独选中）就不再复制
        # Drop files already planned with another photo (e.g. a JPEG selected separately)
        sources = [s for s in sources if not any(_same_file(s, p) for p in planned_sources)]
        if not sources:
            continue
        stem = os.path.splitext(os.path.basename(sources[0]))[0]
        exts = [os.path.splitext(s)[1] for s in sources]
        new_stem = stem
        n = 0
        while any(f"{new_stem}{ext}".lower() in taken for ext in exts):
            n += 1
            new_stem = f"{stem} ({n})"
        if n:
            plan.renamed += 1
        for src, ext in zip(sources, exts):
            name = f"{new_stem}{ext}"
            taken.add(name.lower())
            planned_sources.append(src)
            plan.jobs.append(CopyJob(src, os.path.join(dest_dir, name),
                                     os.path.getsize(src), plan.photo_count))
        plan.photo_count += 1
    return plan


def _tmp_path(dst: str) -> str:
    """目标文件对应的临时文件路径 / Temp path for a destination file."""
    folder, name = os.path.split(dst)
    return os.path.join(folder, f".{name}{_TMP_SUFFIX}")


def _remove_quietly(path: str) -> None:
    """删除文件，失败忽略 / Remove a file, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


def execute_copy_plan(
    plan: CopyPlan,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> CopyResult:
    """
    按计划逐个复制文件（供后台线程调用）。

    每个文件先分块写到隐藏的临时文件，写完复制修改时间等属性，再改成正式名字；
    正式名字此时已被占用（复制期间别的程序写入）就算失败，绝不覆盖。取消会在
    当前块结束后生效，并删掉写了一半的临时文件。

    Copy the plan file by file (meant for a worker thread). Each file is written
    in chunks to a hidden temp file, gets its timestamps copied, then is renamed;
    if the final name appeared meanwhile the file fails rather than overwrite.
    Cancelling takes effect after the current chunk and removes the partial file.

    参数 / Parameters:
        plan (CopyPlan): 复制计划 / the plan.
        cancel_event (threading.Event | None): 置位即取消 / set to cancel.
        progress_cb (Callable[[int, int], None] | None): (已复制字节, 总字节)
            / (bytes done, total bytes).

    返回 / Returns:
        CopyResult: 复制结果 / the outcome.
    """
    result = CopyResult()
    total = plan.total_bytes
    done = 0

    def _report() -> None:
        if progress_cb is not None:
            progress_cb(done, total)

    # 每张照片还没复制完的文件数；减到 0 且没有失败才算这张复制成功
    # Files left per photo; a photo counts once all of its files are copied
    remaining: dict[int, int] = {}
    for job in plan.jobs:
        remaining[job.photo] = remaining.get(job.photo, 0) + 1
    failed_photos: set[int] = set()

    _report()
    for job in plan.jobs:
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            break
        tmp = _tmp_path(job.dst)
        written = 0
        try:
            # 上次异常退出留下的同名临时文件是自己的，先清掉
            # A temp file left by an earlier crash is ours; clear it first
            _remove_quietly(tmp)
            with open(job.src, "rb") as fin, open(tmp, "xb") as fout:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        result.cancelled = True
                        break
                    chunk = fin.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    fout.write(chunk)
                    written += len(chunk)
                    done += len(chunk)
                    _report()
            if result.cancelled:
                _remove_quietly(tmp)
                break
            shutil.copystat(job.src, tmp)
            if os.path.exists(job.dst):
                raise FileExistsError(job.dst)
            os.rename(tmp, job.dst)
            result.copied_files += 1
            remaining[job.photo] -= 1
            if remaining[job.photo] == 0 and job.photo not in failed_photos:
                result.copied_photos += 1
        except Exception as e:  # noqa: BLE001 — 单个文件失败不影响其余文件 / one failure must not stop the rest
            _remove_quietly(tmp)
            result.failed.append((job.src, str(e)))
            failed_photos.add(job.photo)
            # 失败文件没复制完的部分也计入进度，进度条才能走到头
            # Count the unwritten remainder too so the progress bar still completes
            done += max(job.size - written, 0)
            _report()
    return result
