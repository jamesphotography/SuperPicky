#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评分/鸟种变化时的文件移动工具。
File-move helper triggered by rating or bird-name changes in the result browser.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from typing import Optional

from core.folder_layout import compute_target_folder, normalize_layout

# Manifest 并发写保护 / concurrent-write guard for manifest JSON
_manifest_lock = threading.Lock()


def _cleanup_empty_dirs(dir_path: str, old_folder_abs: str) -> None:
    """
    从 old_folder_abs 开始，逐级向上删除空目录，直到 dir_path（根目录）为止。
    Walk up from old_folder_abs, removing empty directories until dir_path is reached.

    Args:
        dir_path:       批处理根目录（绝对路径），不会被删除
        old_folder_abs: 刚才移走文件的那个目录（绝对路径）
    """
    root = os.path.normpath(dir_path)
    current = os.path.normpath(old_folder_abs)
    while current != root:
        try:
            if not os.path.isdir(current):
                break
            if os.listdir(current):  # 非空，停止
                break
            os.rmdir(current)
            current = os.path.dirname(current)
        except Exception:
            break


def _is_in_burst(rel_path: str) -> bool:
    """
    检查相对路径中是否含有 burst_ 段。
    Return True if any path segment starts with 'burst_'.
    """
    parts = os.path.normpath(rel_path).split(os.sep)
    return any(p.startswith("burst_") for p in parts)


def _is_in_root(rel_path: str) -> bool:
    """
    文件是否直接在根目录（无子目录）。
    Return True if the file sits directly in the root (no sub-directory).
    """
    return os.path.dirname(os.path.normpath(rel_path)) in ("", ".")


def move_photo_on_metadata_change(
    dir_path: str,
    photo: dict,
    new_rating: int,
    new_bird_name: str,
    layout: str,
    report_db,
    db_key,
) -> bool:
    """
    因星等或鸟种变化，将照片（及配套 JPEG / XMP sidecar）移动到新目录。

    参数 / Args:
        dir_path:      批处理根目录（绝对路径）
        photo:         来自 DB 的 photo 字典，current_path 已解析为绝对路径
        new_rating:    新星等
        new_bird_name: 新鸟种名（改星等时传原值；改鸟种时传新值）
        layout:        "species-first" | "rating-first"
        report_db:     ReportDB / MergedReportDB 实例，或 None
        db_key:        _photo_db_key(photo) 的结果，直接透传给 report_db.update_photo

    返回 / Returns:
        True 表示实际发生了文件移动，False 表示跳过
    """
    # 1. 获取当前文件绝对路径
    current_abs = photo.get("current_path") or photo.get("original_path") or ""
    if not current_abs or not os.path.exists(current_abs):
        return False

    # 2. 计算相对路径（用于 burst / root 检测）
    try:
        current_rel = os.path.relpath(current_abs, dir_path)
    except ValueError:
        return False  # Windows 跨盘符

    # 3. Burst 子目录检测：不移动连拍组内的照片
    if _is_in_burst(current_rel):
        return False

    # 4. 根目录检测：未整理的文件不移动
    if _is_in_root(current_rel):
        return False

    # 5. 计算新目标目录
    layout = normalize_layout(layout)
    new_rel_folder = compute_target_folder(
        new_rating, new_bird_name or None, layout
    )
    old_rel_folder = os.path.dirname(current_rel)

    # 6. 新旧目录相同：no-op
    if os.path.normpath(new_rel_folder) == os.path.normpath(old_rel_folder):
        return False

    # 7. 收集需要移动的文件：RAW + 配套 JPEG + XMP sidecar
    files_to_move: list[tuple[str, str]] = []  # [(kind, abs_path)]

    raw_stem = os.path.splitext(os.path.basename(current_abs))[0]

    # RAW
    files_to_move.append(("raw", current_abs))

    # 配套 JPEG（同 change_bird_species：内部缓存预览不搬，见那里的说明）
    # Companion JPEG only; never relocate the internal cache preview.
    jpeg_abs = photo.get("temp_jpeg_path") or ""
    if jpeg_abs and os.path.exists(jpeg_abs) and not _is_internal_cache_path(jpeg_abs):
        files_to_move.append(("jpeg", jpeg_abs))

    # XMP sidecar（与 RAW 同目录同 stem）
    xmp_abs = os.path.join(os.path.dirname(current_abs), raw_stem + ".xmp")
    if os.path.exists(xmp_abs):
        files_to_move.append(("xmp", xmp_abs))

    # 8. 执行移动
    new_abs_folder = os.path.join(dir_path, new_rel_folder)
    os.makedirs(new_abs_folder, exist_ok=True)

    moved_raw_rel: Optional[str] = None
    moved_jpeg_rel: Optional[str] = None

    for kind, src in files_to_move:
        basename = os.path.basename(src)
        dst = os.path.join(new_abs_folder, basename)
        if os.path.exists(dst):
            continue  # 目标已存在，跳过不覆盖
        try:
            shutil.move(src, dst)
            rel = os.path.join(new_rel_folder, basename)
            if kind == "raw":
                moved_raw_rel = rel
            elif kind == "jpeg":
                moved_jpeg_rel = rel
        except Exception:
            pass

    # RAW 没搬走就整体判失败：与 change_bird_species 同一约定。只要 JPEG 搬
    # 成功就继续，会让 DB 记着新位置而 RAW 留在旧目录，两边说法从此不一致
    # （现网已出现 RAW 被永久落在旧鸟种目录的实例）。JPEG 得单独回滚，否则
    # 它会孤零零躺在新目录里。
    # Treat "RAW stayed behind" as failure, matching change_bird_species; a
    # JPEG-only move desynchronizes DB and disk. Roll the JPEG back so it does
    # not linger alone in the new folder.
    if moved_raw_rel is None:
        if moved_jpeg_rel is not None:
            try:
                shutil.move(os.path.join(dir_path, moved_jpeg_rel), jpeg_abs)
            except Exception:
                pass
        return False

    # 9. 更新 DB
    if report_db is not None:
        update: dict = {}
        if moved_raw_rel:
            update["current_path"] = moved_raw_rel
        if moved_jpeg_rel:
            update["temp_jpeg_path"] = moved_jpeg_rel
        if update:
            report_db.update_photo(db_key, update)

    # 10. 更新内存中的 photo dict（供同一会话内后续 UI 操作使用）
    if moved_raw_rel:
        photo["current_path"] = os.path.join(dir_path, moved_raw_rel)
    if moved_jpeg_rel:
        photo["temp_jpeg_path"] = os.path.join(dir_path, moved_jpeg_rel)

    # 11. 更新 manifest
    raw_basename = os.path.basename(current_abs)
    _update_manifest(dir_path, raw_basename, new_rel_folder)
    if jpeg_abs and moved_jpeg_rel:
        _update_manifest(dir_path, os.path.basename(jpeg_abs), new_rel_folder)

    # 12. 清理旧目录（若已空）
    _cleanup_empty_dirs(dir_path, os.path.dirname(current_abs))

    return True


def change_bird_species(
    dir_path: str,
    photo: dict,
    new_bird_cn: str,
    new_bird_en: str,
    layout: str,
    report_db,
    db_key,
    failures: Optional[list] = None,
    changed_files: Optional[list] = None,
) -> bool:
    """
    因鸟种变化，将照片（含连拍组整体）移动到新鸟种目录，并更新 DB 的双语鸟名字段。

    参数 / Args:
        dir_path:    批处理根目录（绝对路径）
        photo:       来自 DB 的 photo 字典，current_path 已解析为绝对路径
        new_bird_cn: 新中文鸟名
        new_bird_en: 新英文鸟名
        layout:      "species-first" | "rating-first"
        report_db:   ReportDB / MergedReportDB 实例，或 None
        db_key:      透传给 report_db.update_photo 的稳定键
        failures:    可选列表；移动失败时追加 (文件名, 原因代码)，供批量操作汇报
                     Optional list collecting (basename, reason_code) on failure.
        changed_files: 可选列表；追加所有鸟名已变的用户文件**移动后的绝对路径**，
                     供调用方据此同步写 XMP Title/关键字。本模块只管文件与 DB，
                     元数据写入留给上层（与浏览器改星级写 EXIF 同层）。
                     Optional list collecting post-move absolute paths of user
                     files whose species changed, so the caller can update
                     their XMP metadata; this module stays IO/DB-only.

    返回 / Returns:
        True 表示执行了更新（含仅 DB 更新），False 表示完全跳过或移动失败
    """
    if not _ensure_current_path(dir_path, photo, report_db, db_key, failures):
        return False

    burst_id = photo.get("burst_id")
    if burst_id:
        return _change_bird_species_burst(
            dir_path, photo, new_bird_cn, new_bird_en, layout, report_db, failures,
            changed_files,
        )
    return _change_bird_species_single(
        dir_path, photo, new_bird_cn, new_bird_en, layout, report_db, db_key, failures,
        changed_files,
    )


def _db_current_path(dir_path: str, report_db, db_key) -> str:
    """
    从 DB 取该照片当前的绝对路径；取不到返回空串。

    DB 是文件位置的权威来源：调用方手里的 photo 可能是若干次操作之前的
    副本（浏览器多处各持一份），其 current_path 早已指向被移走的旧位置。

    MergedReportDB 未实现 get_photo，此处以 getattr 优雅降级（返回空串），
    行为退回到「按内存路径判断」，不会因缺方法抛异常。

    Return the photo's absolute path according to the DB (authoritative for
    file location), or "" when unavailable. Degrades gracefully for
    MergedReportDB, which does not implement get_photo.

    参数 / Args:
        dir_path:  批处理根目录（绝对路径）
        report_db: ReportDB / MergedReportDB 实例，或 None
        db_key:    filename 或 (source_dir, filename)

    返回 / Returns:
        str: 绝对路径，无记录/无该方法时为 ""
    """
    getter = getattr(report_db, "get_photo", None)
    if not callable(getter):
        return ""
    filename = db_key[1] if isinstance(db_key, tuple) else db_key
    if not filename:
        return ""
    try:
        row = getter(filename)
    except Exception:
        return ""
    rel = (row or {}).get("current_path") or ""
    if not rel:
        return ""
    return rel if os.path.isabs(rel) else os.path.join(dir_path, rel)


def _ensure_current_path(
    dir_path: str,
    photo: dict,
    report_db,
    db_key,
    failures: Optional[list] = None,
) -> bool:
    """
    保证 photo["current_path"] 指向磁盘上真实存在的文件，必要时按 DB 修正。

    2026-09-05 现网缺陷的防御层：改鸟种移动文件后，浏览器里若还有一份未同步
    的 photo 副本，下一次操作就会带着已失效的旧路径进来——原先直接
    ``return False``，既不移动也不报错，用户只看到「改了没反应」。现在改为
    先按 DB 重新解析；确实找不到文件才回报 ``source_missing``。

    Defensive layer: refresh a stale ``current_path`` from the DB before
    giving up, and report ``source_missing`` instead of failing silently.

    参数 / Args:
        dir_path:  批处理根目录（绝对路径）
        photo:     照片字典，就地修正其 current_path
        report_db: ReportDB / MergedReportDB 实例，或 None
        db_key:    透传给 DB 的稳定键
        failures:  可选列表；文件确实缺失时追加 (文件名, "source_missing")

    返回 / Returns:
        bool: True 表示 current_path 现在可用；False 表示文件确实不在
    """
    current_abs = photo.get("current_path") or photo.get("original_path") or ""
    if current_abs and os.path.exists(current_abs):
        return True

    fresh = _db_current_path(dir_path, report_db, db_key)
    if fresh and os.path.exists(fresh):
        photo["current_path"] = fresh
        return True

    basename = os.path.basename(current_abs or fresh)
    if not basename:
        filename = db_key[1] if isinstance(db_key, tuple) else (db_key or "")
        basename = str(filename or photo.get("filename") or "?")
    _record_failure(failures, basename, "source_missing")
    return False


def _is_internal_cache_path(path: str) -> bool:
    """
    判断路径是否指向 .superpicky 内部缓存（预览图等），而非用户的照片。

    改鸟种后要给「用户的照片」写新鸟名，缓存预览图既不该被写，也不该被
    当成配套 JPEG 一起搬走；主处理流程写 Title 时同样明确排除它
    （见 photo_processor 的 is_temp_file 判断）。

    Return True for paths inside the internal .superpicky cache (previews),
    which are neither user photos nor metadata-write targets.

    参数 / Args:
        path: 待判断的路径 / path to classify

    返回 / Returns:
        bool: True 表示内部缓存 / True when it is an internal cache file
    """
    if not path:
        return False
    normalized = os.path.normpath(path).replace(os.sep, "/")
    basename = os.path.basename(normalized)
    # 逐段比对，绝对路径与相对路径（DB 里就存 ".superpicky/cache/…"，没有前导
    # 斜杠）一视同仁——只按 "/.superpicky/" 匹配会漏掉相对写法。
    # Compare path segments so relative paths (as stored in the DB) match too.
    if ".superpicky" in normalized.split("/"):
        return True
    return basename.startswith(("tmp_", "tmp."))


def _collect_changed(changed_files: Optional[list], path: str) -> None:
    """
    登记一个「鸟名已变、需要同步元数据」的文件（去重，排除内部缓存）。

    Record a file whose species changed so the caller can update its
    metadata; internal cache files are skipped and duplicates ignored.
    """
    if changed_files is None or not path or _is_internal_cache_path(path):
        return
    if path not in changed_files:
        changed_files.append(path)


def _record_failure(failures: Optional[list], basename: str, reason: str) -> None:
    """
    记录一条移动失败。reason 是稳定的原因代码（target_exists / move_error），
    由 UI 层翻译成本地化文案，避免把中文写进 core。

    Append one failure as a stable reason code; the UI localizes it.
    """
    if failures is not None:
        failures.append((basename, reason))


def _folder_bird_name(new_bird_cn: str, new_bird_en: str) -> str:
    """根据当前界面语言选择用于目录命名的鸟名。"""
    from tools.i18n import get_i18n
    use_en = get_i18n().current_lang.startswith("en")
    return (new_bird_en if use_en else new_bird_cn) or ""


def _change_bird_species_single(
    dir_path: str,
    photo: dict,
    new_bird_cn: str,
    new_bird_en: str,
    layout: str,
    report_db,
    db_key,
    failures: Optional[list] = None,
    changed_files: Optional[list] = None,
) -> bool:
    """
    非连拍照片的鸟名变更：更新 DB 双语字段 + 按需移动文件。

    根目录下的照片（未整理）只更新 DB，不移动。
    changed_files 收集鸟名已变的用户文件路径（移动后），供上层写元数据。
    """
    current_abs = photo.get("current_path") or photo.get("original_path") or ""
    if not current_abs or not os.path.exists(current_abs):
        return False

    # 计算相对路径
    try:
        current_rel = os.path.relpath(current_abs, dir_path)
    except ValueError:
        return False

    # DB 更新内容（无论是否移动都写入）
    species_update: dict = {
        "bird_species_cn": new_bird_cn or None,
        "bird_species_en": new_bird_en or None,
    }

    # 根目录文件（未整理）：仅更新 DB 不移动
    if _is_in_root(current_rel):
        if report_db is not None:
            report_db.update_photo(db_key, species_update)
        photo["bird_species_cn"] = new_bird_cn
        photo["bird_species_en"] = new_bird_en
        # 没移动不等于不用改元数据：鸟名变了，Title/关键字照样要更新
        # Not moving does not mean not retagging: the species still changed.
        _collect_changed(changed_files, current_abs)
        return True

    # 计算新目标目录
    folder_name = _folder_bird_name(new_bird_cn, new_bird_en)
    rating = photo.get("rating") or 0
    layout = normalize_layout(layout)
    new_rel_folder = compute_target_folder(rating, folder_name or None, layout)
    old_rel_folder = os.path.dirname(current_rel)

    path_update: dict = {}
    if os.path.normpath(new_rel_folder) != os.path.normpath(old_rel_folder):
        # 移动 RAW + 配套 JPEG + XMP sidecar
        raw_stem = os.path.splitext(os.path.basename(current_abs))[0]
        files_to_move: list = [("raw", current_abs)]

        # 只搬「真配套 JPEG」(RAW+JPEG 双格式拍摄的那张)。temp_jpeg_path 常指向
        # .superpicky 里的内部缓存预览，把它搬进鸟种目录会造成真实损坏：再处理
        # 该目录时它与 RAW 同 prefix，organize 阶段会用它覆盖 RAW 的
        # current_path，RAW 从此被永久落在旧目录(2026-09-05 现网确认)。
        # Move only a real companion JPEG; temp_jpeg_path usually points at the
        # internal cache preview, and relocating that corrupts the library.
        jpeg_abs = photo.get("temp_jpeg_path") or ""
        if jpeg_abs and os.path.exists(jpeg_abs) and not _is_internal_cache_path(jpeg_abs):
            files_to_move.append(("jpeg", jpeg_abs))

        xmp_abs = os.path.join(os.path.dirname(current_abs), raw_stem + ".xmp")
        if os.path.exists(xmp_abs):
            files_to_move.append(("xmp", xmp_abs))

        new_abs_folder = os.path.join(dir_path, new_rel_folder)
        os.makedirs(new_abs_folder, exist_ok=True)

        raw_moved = False
        for kind, src in files_to_move:
            basename = os.path.basename(src)
            dst = os.path.join(new_abs_folder, basename)
            if os.path.exists(dst):
                # 目标已有同名文件：不覆盖，但必须回报（原来是静默 continue）
                _record_failure(failures, basename, "target_exists")
                continue
            try:
                shutil.move(src, dst)
                rel = os.path.join(new_rel_folder, basename)
                if kind == "raw":
                    raw_moved = True
                    path_update["current_path"] = rel
                    photo["current_path"] = os.path.join(dir_path, rel)
                    _collect_changed(changed_files, photo["current_path"])
                elif kind == "jpeg":
                    path_update["temp_jpeg_path"] = rel
                    photo["temp_jpeg_path"] = os.path.join(dir_path, rel)
                    _collect_changed(changed_files, photo["temp_jpeg_path"])
            except Exception as exc:
                _record_failure(
                    failures, basename, f"move_error:{exc.__class__.__name__}"
                )

        # RAW 没搬走就不改 DB 鸟种，避免 DB 与磁盘目录说法不一致
        # Leave the species untouched when the RAW stayed behind.
        if not raw_moved:
            return False

        # 更新 manifest
        raw_basename = os.path.basename(current_abs)
        _update_manifest(dir_path, raw_basename, new_rel_folder)
        if jpeg_abs and "temp_jpeg_path" in path_update:
            _update_manifest(dir_path, os.path.basename(jpeg_abs), new_rel_folder)

        # 清理旧目录（若已空）
        _cleanup_empty_dirs(dir_path, os.path.dirname(current_abs))

    # 目录未变(例如低星照片一律归「其他鸟类」)时上面不会登记，这里补上：
    # 只要鸟名变了就得改元数据。
    # When the folder did not change, still retag: the species changed.
    _collect_changed(changed_files, photo.get("current_path") or current_abs)

    # 合并写入 DB
    all_updates = {**species_update, **path_update}
    if report_db is not None:
        report_db.update_photo(db_key, all_updates)

    photo["bird_species_cn"] = new_bird_cn
    photo["bird_species_en"] = new_bird_en
    return True


def _change_bird_species_burst(
    dir_path: str,
    photo: dict,
    new_bird_cn: str,
    new_bird_en: str,
    layout: str,
    report_db,
    failures: Optional[list] = None,
    changed_files: Optional[list] = None,
) -> bool:
    """
    连拍组的鸟名变更：整组文件夹整体移动，批量更新组内所有照片的 DB 记录。

    changed_files 收集组内每一张的新路径——整组都改成了新鸟种，元数据也得
    整组更新，只写代表图会让组内其余照片留着错误鸟名。

    目录变换规则：只替换鸟名父目录，星等子目录保持不变。
    """
    current_abs = photo.get("current_path") or photo.get("original_path") or ""
    if not current_abs or not os.path.exists(current_abs):
        return False

    burst_folder_abs = os.path.dirname(current_abs)
    burst_folder_name = os.path.basename(burst_folder_abs)
    if not burst_folder_name.startswith("burst_"):
        return False

    rating_folder_abs = os.path.dirname(burst_folder_abs)
    try:
        rating_folder_rel = os.path.relpath(rating_folder_abs, dir_path)
    except ValueError:
        return False

    parts = os.path.normpath(rating_folder_rel).split(os.sep)
    if len(parts) != 2:
        return False  # 非预期目录层级

    layout_norm = normalize_layout(layout)
    folder_name = _folder_bird_name(new_bird_cn, new_bird_en)

    if layout_norm == "species-first":
        old_bird_part, rating_part = parts[0], parts[1]
        new_rating_folder_rel = os.path.join(folder_name or old_bird_part, rating_part)
    else:  # rating-first
        rating_part, old_bird_part = parts[0], parts[1]
        new_rating_folder_rel = os.path.join(rating_part, folder_name or old_bird_part)

    # 执行移动（仅在目录实际改变时）
    moved = False
    if os.path.normpath(new_rating_folder_rel) != os.path.normpath(rating_folder_rel):
        new_burst_parent_abs = os.path.join(dir_path, new_rating_folder_rel)
        os.makedirs(new_burst_parent_abs, exist_ok=True)
        new_burst_abs = os.path.join(new_burst_parent_abs, burst_folder_name)
        if os.path.exists(new_burst_abs):
            # 目标已有同名连拍文件夹：整组不动，回报失败
            _record_failure(failures, burst_folder_name, "target_exists")
            return False
        try:
            shutil.move(burst_folder_abs, new_burst_parent_abs)
            moved = True
            # burst 文件夹移走后，清理旧星等目录和鸟种目录（若已空）
            _cleanup_empty_dirs(dir_path, rating_folder_abs)
        except Exception as exc:
            _record_failure(
                failures, burst_folder_name, f"move_error:{exc.__class__.__name__}"
            )
            return False

    # 批量更新组内所有照片的 DB 记录
    burst_id = photo.get("burst_id")
    is_merged = hasattr(report_db, "root_dir")
    burst_photos = (
        report_db.get_photos_by_burst_id(burst_id, dir_path)
        if is_merged
        else report_db.get_photos_by_burst_id(burst_id)
    ) if report_db is not None else []

    species_update = {
        "bird_species_cn": new_bird_cn or None,
        "bird_species_en": new_bird_en or None,
    }

    for bp in burst_photos:
        bp_filename = bp["filename"]
        update = dict(species_update)

        bp_cur_path = bp.get("current_path") or ""
        if moved:
            if bp_cur_path:
                bp_basename = os.path.basename(bp_cur_path)
                new_cur_rel = os.path.join(
                    new_rating_folder_rel, burst_folder_name, bp_basename
                )
                update["current_path"] = new_cur_rel
                _collect_changed(
                    changed_files, os.path.join(dir_path, new_cur_rel)
                )
                _update_manifest(
                    dir_path, bp_basename,
                    os.path.join(new_rating_folder_rel, burst_folder_name),
                )
                if bp_filename == photo.get("filename"):
                    photo["current_path"] = os.path.join(dir_path, new_cur_rel)

        if not moved and bp_cur_path:
            # 目录没变（同名鸟种目录）也要更新元数据
            # Folder unchanged, but the species did change — still retag.
            _collect_changed(
                changed_files,
                bp_cur_path if os.path.isabs(bp_cur_path)
                else os.path.join(dir_path, bp_cur_path),
            )

        if is_merged:
            bp_key = (bp.get("source_dir", ""), bp_filename)
        else:
            bp_key = bp_filename

        if report_db is not None:
            report_db.update_photo(bp_key, update)

        if bp_filename == photo.get("filename"):
            photo["bird_species_cn"] = new_bird_cn
            photo["bird_species_en"] = new_bird_en

    return True


def _update_manifest(dir_path: str, basename: str, new_folder: str) -> None:
    """
    更新 .superpicky_manifest.json 中指定文件的 folder 字段。
    Update the 'folder' field for the given file in the manifest JSON.
    """
    manifest_path = os.path.join(dir_path, ".superpicky_manifest.json")
    if not os.path.exists(manifest_path):
        return
    with _manifest_lock:
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            changed = False
            for entry in manifest.get("files", []):
                if entry.get("filename") == basename:
                    entry["folder"] = new_folder
                    changed = True
                    break
            if changed:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def merge_bird_species(
    dir_path: str,
    photos: list,
    new_bird_cn: str,
    new_bird_en: str,
    layout: str,
    report_db,
    db_key_of,
    progress_cb=None,
    changed_files: Optional[list] = None,
) -> dict:
    """
    整种合并：把一批照片（通常是同一个被识别错的鸟种的全部照片）统一改为新鸟种。

    参数 / Args:
        dir_path:    批处理根目录（绝对路径）
        photos:      待改鸟种的 photo 字典列表，current_path 已解析为绝对路径
        new_bird_cn: 新中文鸟名
        new_bird_en: 新英文鸟名
        layout:      "species-first" | "rating-first"
        report_db:   ReportDB / MergedReportDB 实例，或 None
        db_key_of:   callable(photo) -> db_key，由调用方提供，避免 core 依赖 ui
        progress_cb: callable(done, total, filename) -> bool，返回 False 表示用户取消
        changed_files: 可选列表；收集所有鸟名已变的用户文件（移动后的绝对路径），
                     供调用方统一写 XMP Title/关键字。连拍组由
                     change_bird_species 整组登记，组内每张都会进来。
                     Optional list collecting post-move paths for the caller's
                     metadata write; burst members are included as a group.

    返回 / Returns:
        {
            "moved":     实际移动了文件的照片数,
            "db_only":   只更新了 DB 鸟名、未移动文件的照片数（未整理的根目录照片）,
            "failed":    [(文件名, 原因代码)],
            "cancelled": 是否被用户中途取消,
        }

    Merge a whole species: retag every given photo to the new species.
    """
    moved = 0
    db_only = 0
    failed: list = []
    total = len(photos)
    done = 0
    cancelled = False

    for leader, members in _group_by_burst(photos):
        group_failures: list = []
        path_before = leader.get("current_path")
        ok = change_bird_species(
            dir_path, leader, new_bird_cn, new_bird_en, layout,
            report_db, db_key_of(leader), group_failures, changed_files,
        )
        failed.extend(group_failures)
        if ok:
            # 路径没变 = 未整理的根目录照片，只改了 DB 鸟名
            if leader.get("current_path") == path_before:
                db_only += len(members)
            else:
                # 连拍组由 change_bird_species 整组处理，组内每张都算搬成功
                moved += len(members)
            for member in members[1:]:
                member["bird_species_cn"] = new_bird_cn
                member["bird_species_en"] = new_bird_en
        done += len(members)
        if progress_cb is not None:
            if progress_cb(done, total, leader.get("filename", "")) is False:
                cancelled = True
                break

    return {
        "moved": moved,
        "db_only": db_only,
        "failed": failed,
        "cancelled": cancelled,
    }


def _group_by_burst(photos: list) -> list:
    """
    把照片按 burst_id 分组：同一连拍组只保留一个代表用于调用移动逻辑，
    因为 change_bird_species 会整组搬迁——重复调用时第二张的 current_path
    已失效，会被误判为失败。

    返回 / Returns:
        [(代表 photo, [组内全部 photo])]，非连拍照片自成一组。

    Group photos by burst_id; a burst folder is moved as a whole, so calling
    the mover once per member would fail on the second one.
    """
    groups: list = []
    burst_members: dict = {}
    for photo in photos:
        burst_id = photo.get("burst_id")
        if not burst_id:
            groups.append((photo, [photo]))
            continue
        if burst_id in burst_members:
            burst_members[burst_id].append(photo)
            continue
        members = [photo]
        burst_members[burst_id] = members
        groups.append((photo, members))
    return groups
