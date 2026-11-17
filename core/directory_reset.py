# -*- coding: utf-8 -*-
"""
单个目录的重置（把选片整理过的照片移回原位、清掉评分目录与缓存）——界面与命令行共用。

此前界面与命令行各有一份实现：界面版在 V4.3.0 做过数据安全修复（同名跳过不覆盖、
目录确实为空才删除），命令行版没跟上——同名先删、移动失败也无条件 rmtree 子目录与
评分目录，会丢照片。界面的批量重置还会对已处理的子目录调用命令行版。现在两者都走这里。

Reset one directory (move organized photos back, remove rating folders and the
cache), shared by the GUI and the CLI. The CLI used to keep its own copy that
missed the V4.3.0 data-safety fixes (it deleted same-name files and removed
folders unconditionally), and the GUI's batch reset called it for subfolders.
"""
from __future__ import annotations

import os
import shutil
from typing import Callable


def reset_single_directory(
    directory_path: str,
    i18n,
    log: Callable[[str], None],
    skip_exif_reset: bool = False,
    exiftool_mgr=None,
) -> dict:
    """
    重置一个目录：摊平连拍/鸟种子目录 → 按清单与目录名把照片移回根目录 → 重置 EXIF
    → 删除已经清空的评分目录 → 删除 .superpicky 与清单文件。

    数据安全约定（V4.3.0）：任何一步遇到同名文件都跳过、绝不覆盖；评分目录只在里面
    确实没有文件时才删除，有残留就保留并在日志里列出。

    参数:
    directory_path (str): 要重置的目录
    i18n: 国际化对象（用 logs.* 文案）
    log (Callable[[str], None]): 日志输出（界面传信号发射函数，命令行传 print）
    skip_exif_reset (bool): 「快速复原」时为 True：不重置 EXIF、保留 .superpicky 缓存
    exiftool_mgr: ExifToolManager 实例，缺省时取全局单例

    返回:
    dict: {"success": bool, "restore_stats": dict}

    Reset one directory with the V4.3.0 data-safety rules: never overwrite a
    same-name file, and remove a rating folder only once it is truly empty.
    """
    from tools.find_bird_util import reset

    if exiftool_mgr is None:
        from tools.exiftool_manager import get_exiftool_manager
        exiftool_mgr = get_exiftool_manager()
    # 第 0 步：摊平评分目录下的连拍/鸟种子目录 / step 0: flatten burst/species subfolders
    log(i18n.t("logs.reset_step0"))
    from constants import ALL_RATING_FOLDER_NAMES
    rating_dirs = sorted(ALL_RATING_FOLDER_NAMES)
    subdir_stats = {'dirs_removed': 0, 'files_restored': 0}

    for rating_dir in rating_dirs:
        rating_path = os.path.join(directory_path, rating_dir)
        if not os.path.exists(rating_path):
            continue

        for entry in os.listdir(rating_path):
            entry_path = os.path.join(rating_path, entry)
            if os.path.isdir(entry_path):
                # 递归将所有文件移回评分目录（V4.3.0: 同名跳过、绝不覆盖删除）
                for root, dirs, files in os.walk(entry_path):
                    for filename in files:
                        src = os.path.join(root, filename)
                        dst = os.path.join(rating_path, filename)
                        if os.path.isfile(src):
                            if os.path.exists(dst):
                                continue  # 同名保留两者，不覆盖（数据安全）
                            try:
                                shutil.move(src, dst)
                                subdir_stats['files_restored'] += 1
                            except Exception as e:
                                log(i18n.t("logs.move_failed", filename=filename, error=e))

                # 删除子目录（V4.3.0: 仅当其内已无任何文件，避免误删残留）
                try:
                    if os.path.isdir(entry_path) and not any(
                        fs for _r, _d, fs in os.walk(entry_path)
                    ):
                        shutil.rmtree(entry_path, ignore_errors=True)
                        subdir_stats['dirs_removed'] += 1
                except Exception as e:
                    log(i18n.t("logs.burst_clean_failed", entry=entry, error=e))

    if subdir_stats['dirs_removed'] > 0:
        log(i18n.t("logs.burst_cleaned", dirs=subdir_stats['dirs_removed'], files=subdir_stats['files_restored']))
    else:
        log(i18n.t("logs.burst_no_clean"))

    log(i18n.t("logs.reset_step1"))
    restore_stats = exiftool_mgr.restore_files_from_manifest(
        directory_path, log_callback=log, i18n=i18n
    )

    restored_count = restore_stats.get('restored', 0)
    if restored_count > 0:
        log(i18n.t("logs.restored_files", count=restored_count))

    # V4.0.5: Manifest 可能不包含所有文件，扫描评分目录将残留文件移回根目录
    fallback_restored = 0
    for rating_dir in rating_dirs:
        rating_path = os.path.join(directory_path, rating_dir)
        if not os.path.exists(rating_path):
            continue

        for filename in os.listdir(rating_path):
            src = os.path.join(rating_path, filename)
            dst = os.path.join(directory_path, filename)
            if os.path.isfile(src):
                if os.path.exists(dst):
                    continue  # V4.3.0: 同名不覆盖根目录原文件（数据安全）
                try:
                    shutil.move(src, dst)
                    fallback_restored += 1
                except Exception as e:
                    log(i18n.t("logs.move_failed", filename=filename, error=e))

    if fallback_restored > 0:
        log(i18n.t("logs.restored_files", count=fallback_restored))

    # V4.3.1: 无条件「按目录名摊平」兜底——把仍残留在 鸟种/星级/burst_
    # 子目录里的文件递归移回根目录。manifest 可能不完整（连拍成员未入库等），
    # 仅靠上面的 manifest/根目录评分扫描会遗漏鸟种优先布局下的深层文件。
    # force_flatten 幂等安全（同名不覆盖、只动 SuperPicky 目录、不碰用户目录），
    # 无残留时 moved=0，因此始终执行无副作用。
    # V4.3.1: unconditional name-based flatten safety net — recursively move
    # any files still stranded in species/rating/burst_ subdirs back to root.
    flatten_moved = 0
    try:
        from tools.find_bird_util import force_flatten_directory
        _fstats = force_flatten_directory(directory_path, log_callback=log, i18n=i18n)
        flatten_moved = int(_fstats.get("moved", 0)) if _fstats else 0
    except Exception as _fe:
        log(f"⚠️ flatten fallback failed: {_fe}")

    # 恢复总数 = manifest 恢复 + 评分目录兜底 + 按目录名摊平兜底（合并显示,避免数字与实际不符）
    total_restored = restored_count + fallback_restored + flatten_moved
    if total_restored == 0:
        log(i18n.t("logs.no_files_to_restore"))
    else:
        log(i18n.t("logs.restored_files", count=total_restored))

    # V4.0.4: 根据模式决定是否重置EXIF
    if skip_exif_reset:
        log("\n" + i18n.t("logs.skip_exif_reset"))
        success = True
    else:
        log("\n" + i18n.t("logs.reset_step2"))
        success = reset(directory_path, log_callback=log, i18n=i18n)

    # V3.9: 删除评分目录（所有文件已移走）
    log(i18n.t("logs.reset_step3"))
    from tools.find_bird_util import cleanup_ignorable_reset_residue
    deleted_dirs = 0
    for rating_dir in rating_dirs:
        rating_path = os.path.join(directory_path, rating_dir)
        if os.path.exists(rating_path) and os.path.isdir(rating_path):
            # 先清理系统元数据残留，再判断是否仍有真实文件需要保留。
            # Remove OS metadata residue first, then preserve any real files that remain.
            cleanup_ignorable_reset_residue(rating_path)
            # V4.3.0: 仅当评分目录内已无任何文件才删除，避免误删残留（数据安全）
            residual_files = []
            for _r, _d, fs in os.walk(rating_path):
                for _filename in fs:
                    _rel = os.path.relpath(os.path.join(_r, _filename), rating_path)
                    residual_files.append(_rel)
            if residual_files:
                sample = ", ".join(residual_files[:3])
                if len(residual_files) > 3:
                    sample = f"{sample}, ..."
                log(i18n.t("logs.empty_dir_delete_failed",
                                dir=rating_dir,
                                error=i18n.t("logs.reset_dir_has_leftovers", sample=sample)))
                continue
            try:
                shutil.rmtree(rating_path, ignore_errors=True)
                log(i18n.t("logs.empty_dir_deleted", dir=rating_dir))
                deleted_dirs += 1
            except Exception as e:
                log(i18n.t("logs.empty_dir_delete_failed", dir=rating_dir, error=e))

    # V4.0.5: 清理 .superpicky 隐藏目录和 manifest 文件
    # Quick Restore: 重新处理时保留 .superpicky 缓存（预览图复用，节省时间）
    superpicky_dir = os.path.join(directory_path, ".superpicky")
    if not skip_exif_reset and os.path.exists(superpicky_dir):
        # #146：跨平台删除（去只读 + 重试）；原兜底 `rm -rf` 在 Windows 上不存在
        # #146: cross-platform removal; the old `rm -rf` fallback does not exist on Windows
        from core.superpicky_dir import remove_superpicky_dir
        if remove_superpicky_dir(directory_path):
            log("  ✅ .superpicky/")
            deleted_dirs += 1
        else:
            leftovers = ", ".join(sorted(os.listdir(superpicky_dir))[:5])
            log(i18n.t("logs.reset_cache_delete_failed", error=leftovers))
    elif skip_exif_reset:
        log(i18n.t("logs.reset_cache_kept"))

    manifest_file = os.path.join(directory_path, ".superpicky_manifest.json")
    if os.path.exists(manifest_file):
        try:
            os.remove(manifest_file)
            log("  ✅ .superpicky_manifest.json")
        except Exception as e:
            log(i18n.t("logs.manifest_delete_failed", error=e))

    # 清理 macOS ._burst_XXX 残留文件
    for filename in os.listdir(directory_path):
        if filename.startswith('._burst_') or filename.startswith('._其他') or filename.startswith('._栗'):
            try:
                os.remove(os.path.join(directory_path, filename))
            except Exception:
                pass

    if deleted_dirs > 0:
        log(i18n.t("logs.empty_dirs_cleaned", count=deleted_dirs))
    else:
        log(i18n.t("logs.no_empty_dirs"))

    return {"success": success, "restore_stats": restore_stats}
