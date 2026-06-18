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

    # 配套 JPEG
    jpeg_abs = photo.get("temp_jpeg_path") or ""
    if jpeg_abs and os.path.exists(jpeg_abs):
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

    if moved_raw_rel is None and moved_jpeg_rel is None:
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

    return True


def change_bird_species(
    dir_path: str,
    photo: dict,
    new_bird_cn: str,
    new_bird_en: str,
    layout: str,
    report_db,
    db_key,
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

    返回 / Returns:
        True 表示执行了更新（含仅 DB 更新），False 表示完全跳过
    """
    burst_id = photo.get("burst_id")
    if burst_id:
        return _change_bird_species_burst(
            dir_path, photo, new_bird_cn, new_bird_en, layout, report_db
        )
    return _change_bird_species_single(
        dir_path, photo, new_bird_cn, new_bird_en, layout, report_db, db_key
    )


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
) -> bool:
    """
    非连拍照片的鸟名变更：更新 DB 双语字段 + 按需移动文件。

    根目录下的照片（未整理）只更新 DB，不移动。
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

        jpeg_abs = photo.get("temp_jpeg_path") or ""
        if jpeg_abs and os.path.exists(jpeg_abs):
            files_to_move.append(("jpeg", jpeg_abs))

        xmp_abs = os.path.join(os.path.dirname(current_abs), raw_stem + ".xmp")
        if os.path.exists(xmp_abs):
            files_to_move.append(("xmp", xmp_abs))

        new_abs_folder = os.path.join(dir_path, new_rel_folder)
        os.makedirs(new_abs_folder, exist_ok=True)

        for kind, src in files_to_move:
            basename = os.path.basename(src)
            dst = os.path.join(new_abs_folder, basename)
            if os.path.exists(dst):
                continue
            try:
                shutil.move(src, dst)
                rel = os.path.join(new_rel_folder, basename)
                if kind == "raw":
                    path_update["current_path"] = rel
                    photo["current_path"] = os.path.join(dir_path, rel)
                elif kind == "jpeg":
                    path_update["temp_jpeg_path"] = rel
                    photo["temp_jpeg_path"] = os.path.join(dir_path, rel)
            except Exception:
                pass

        # 更新 manifest
        raw_basename = os.path.basename(current_abs)
        _update_manifest(dir_path, raw_basename, new_rel_folder)
        if jpeg_abs and "temp_jpeg_path" in path_update:
            _update_manifest(dir_path, os.path.basename(jpeg_abs), new_rel_folder)

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
) -> bool:
    """
    连拍组的鸟名变更：整组文件夹整体移动，批量更新组内所有照片的 DB 记录。

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
        if not os.path.exists(new_burst_abs):
            try:
                shutil.move(burst_folder_abs, new_burst_parent_abs)
                moved = True
            except Exception:
                pass

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

        if moved:
            bp_cur_path = bp.get("current_path") or ""
            if bp_cur_path:
                bp_basename = os.path.basename(bp_cur_path)
                new_cur_rel = os.path.join(
                    new_rating_folder_rel, burst_folder_name, bp_basename
                )
                update["current_path"] = new_cur_rel
                _update_manifest(
                    dir_path, bp_basename,
                    os.path.join(new_rating_folder_rel, burst_folder_name),
                )
                if bp_filename == photo.get("filename"):
                    photo["current_path"] = os.path.join(dir_path, new_cur_rel)

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
