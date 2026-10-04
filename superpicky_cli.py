#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky CLI - 命令行入口
完整功能版本 - 支持处理、重置、连拍检测、鸟类识别

Usage:
    python superpicky_cli.py process /path/to/photos [options]
    python superpicky_cli.py reset /path/to/photos
    python superpicky_cli.py info /path/to/photos
    python superpicky_cli.py identify /path/to/bird.jpg [options]

Examples:
    # 基本处理
    python superpicky_cli.py process ~/Photos/Birds

    # 自定义阈值
    python superpicky_cli.py process ~/Photos/Birds --sharpness 600 --nima 5.2

    # 不移动文件，只写EXIF
    python superpicky_cli.py process ~/Photos/Birds --no-organize

    # 重置目录
    python superpicky_cli.py reset ~/Photos/Birds

    # 鸟类识别
    python superpicky_cli.py identify ~/Photos/bird.jpg
    python superpicky_cli.py identify ~/Photos/bird.NEF --top 10
    python superpicky_cli.py identify ~/Photos/bird.jpg --write-exif
"""

import argparse
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH
from tools.i18n import apply_saved_language, t
from constants import APP_VERSION


# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_banner():
    """打印 CLI 横幅"""
    print("\n" + "━" * 60)
    print(t("cli.banner", version=APP_VERSION))
    print("━" * 60)


def cmd_burst(args):
    """连拍检测与分组"""
    from core.burst_detector import BurstDetector
    from tools.exiftool_manager import ExifToolManager
    
    print_banner()
    print(t("cli.target_dir", directory=args.directory))
    print(t("cli.min_burst", count=args.min_count))
    print(t("cli.time_threshold", ms=args.threshold))
    print(t("cli.phash", status=t("cli.enabled") if args.phash else t("cli.disabled")))
    print(t("cli.execute_mode", mode=t("cli.mode_real") if args.execute else t("cli.mode_preview")))
    print()
    
    # 创建检测器
    detector = BurstDetector(use_phash=args.phash)
    detector.MIN_BURST_COUNT = args.min_count
    detector.TIME_THRESHOLD_MS = args.threshold
    
    # 运行检测
    print(t("cli.detecting_burst"))
    results = detector.run_full_detection(args.directory)
    
    # 显示结果
    print(f"\n{'═' * 50}")
    print(t("cli.burst_result_title"))
    print(f"{'═' * 50}")
    print(t("cli.total_overview"))
    print(t("cli.total_photos", count=results['total_photos']))
    print(t("cli.photos_subsec", count=results['photos_with_subsec']))
    print(t("cli.groups_detected", count=results['groups_detected']))
    
    for dir_name, data in results['groups_by_dir'].items():
        print(f"\n📂 {dir_name}:")
        print(t("cli.burst_photos", photos=data['photos']))
        print(t("cli.burst_groups", groups=data['groups']))
        
        for g in data['group_details']:
            print(t("cli.burst_group_line", id=g['id'], count=g['count'], best=g['best']))
    
    # 执行模式
    if args.execute and results['groups_detected'] > 0:
        print(t("cli.processing_burst"))
        
        exiftool_mgr = ExifToolManager()
        total_stats = {'groups_processed': 0, 'photos_moved': 0, 'best_marked': 0}
        
        rating_dirs = ['3star_excellent', '2star_good', '3星_优选', '2星_良好']  # Support both languages
        for rating_dir in rating_dirs:
            subdir = os.path.join(args.directory, rating_dir)
            if not os.path.exists(subdir):
                continue
            
            # 重新获取该目录的 groups
            from constants import RAW_EXTENSIONS, HEIF_EXTENSIONS
            extensions = set(RAW_EXTENSIONS + HEIF_EXTENSIONS)
            filepaths = []
            for entry in os.scandir(subdir):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in extensions:
                        filepaths.append(entry.path)
            
            if not filepaths:
                continue
            
            photos = detector.read_timestamps(filepaths)
            photos = detector.enrich_from_db(photos, args.directory)
            groups = detector.detect_groups(photos)
            groups = detector.select_best_in_groups(groups)
            
            # 处理
            stats = detector.process_burst_groups(groups, subdir, exiftool_mgr)
            total_stats['groups_processed'] += stats['groups_processed']
            total_stats['photos_moved'] += stats['photos_moved']
            total_stats['best_marked'] += stats['best_marked']
        
        print(t("cli.processing_complete"))
        print(t("cli.processed_groups", count=total_stats['groups_processed']))
        print(t("cli.moved_photos", count=total_stats['photos_moved']))
        print(t("cli.marked_purple", count=total_stats['best_marked']))
    elif not args.execute:
        print(t("cli.preview_hint"))
    
    print()
    return 0


def cmd_process(args):
    """处理照片目录"""
    from tools.cli_processor import CLIProcessor
    from tools.cli_settings import resolve_processing_settings, apply_config_overrides
    from advanced_config import get_advanced_config

    print_banner()

    # 解析设置：CLI 显式 > advanced_config(= GUI 同源) > 默认。仅改内存，**不回写**。
    adv_config = get_advanced_config()
    apply_config_overrides(args, adv_config)                  # B 通道（folder_layout/metadata/min_*…）
    # 兼容：--no-cleanup 且未显式指定保留时，等价于保留临时文件
    if getattr(args, 'keep_temp', None) is None and not args.cleanup:
        adv_config.config["keep_temp_files"] = True
    if getattr(args, 'cleanup_days', None) is not None:
        adv_config.config["auto_cleanup_days"] = args.cleanup_days
    settings = resolve_processing_settings(args, adv_config)  # A 通道（ProcessingSettings）

    print(t("cli.target_dir", directory=args.directory))
    print(t("cli.sharpness", value=settings.sharpness_threshold))
    print(t("cli.aesthetics", value=settings.nima_threshold))
    print(t("cli.detect_flight", value=t("cli.enabled") if settings.detect_flight else t("cli.disabled")))
    print(t("cli.detect_exposure", value=t("cli.yes") if settings.detect_exposure else t("cli.no")))
    print(t("cli.detect_burst", value=t("cli.enabled") if settings.detect_burst else t("cli.disabled")))
    print(t("cli.organize_files", value=t("cli.enabled") if args.organize else t("cli.disabled")))
    print(t("cli.arw_write", value=adv_config.config.get('arw_write_mode')))
    print(t("cli.cleanup_temp", value=t("cli.no") if adv_config.keep_temp_files else t("cli.yes")))

    if settings.auto_identify:
        print(t("cli.auto_birdid_on"))
        if settings.birdid_country_code:
            print(t("cli.birdid_country", country=settings.birdid_country_code))
        if settings.birdid_region_code:
            print(t("cli.birdid_region", region=settings.birdid_region_code))
        print(t("cli.birdid_threshold", threshold=settings.birdid_confidence_threshold))
    print()

    processor = CLIProcessor(
        dir_path=args.directory,
        verbose=not args.quiet,
        settings=settings
    )

    # 连拍检测/跨目录合并由 PhotoProcessor 内部处理。
    stats = processor.process(
        organize_files=args.organize,
        cleanup_temp=not adv_config.keep_temp_files  # 保留则不清理
    )

    print(t("cli.processing_complete"))
    return 0


def cmd_reset(args):
    """重置目录"""
    from tools.find_bird_util import reset
    from tools.exiftool_manager import get_exiftool_manager
    from tools.i18n import get_i18n
    import shutil
    
    print_banner()
    print(t("cli.reset_dir", directory=args.directory))
    
    if not args.yes:
        confirm = input(t("cli.reset_confirm"))
        if confirm.lower() not in ['y', 'yes']:
            print(t("cli.cancelled"))
            return 1
    
    # V4.0.5: 先处理所有子目录（burst_XXX、鸟种 Other_Birds 等）
    # 将文件移回评分目录，然后由步骤1的 manifest 恢复到根目录
    print(t("cli.reset_step0"))
    rating_dirs = ['3star_excellent', '2star_good', '1star_average', '0star_reject',
                   '3星_优选', '2星_良好', '1星_普通', '0星_放弃']  # Support both languages
    subdir_stats = {'dirs_removed': 0, 'files_restored': 0}
    
    for rating_dir in rating_dirs:
        rating_path = os.path.join(args.directory, rating_dir)
        if not os.path.exists(rating_path):
            continue
        
        # 查找所有子目录（burst_XXX、鸟种目录等）
        for entry in os.scandir(rating_path):
            entry_path = entry.path
            if entry.is_symlink():
                print(t("cli.skip_symlink_dir", rating_dir=rating_dir, name=entry.name))
                continue
            if entry.is_dir(follow_symlinks=False):
                print(t("cli.flatten_subdir", rating_dir=rating_dir, name=entry.name))
                # 递归将所有文件移回评分目录
                for root, dirs, files in os.walk(entry_path):
                    # 防御性处理：不进入任何符号链接子目录
                    dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                    for filename in files:
                        src = os.path.join(root, filename)
                        if os.path.islink(src):
                            print(t("cli.skip_symlink_file", filename=filename))
                            continue
                        dst = os.path.join(rating_path, filename)
                        if os.path.isfile(src):
                            try:
                                if os.path.exists(dst):
                                    os.remove(dst)
                                shutil.move(src, dst)
                                subdir_stats['files_restored'] += 1
                            except Exception as e:
                                print(t("cli.move_failed", filename=filename, e=e))

                # 删除子目录
                try:
                    if os.path.exists(entry_path):
                        shutil.rmtree(entry_path)
                    subdir_stats['dirs_removed'] += 1
                except Exception as e:
                    print(t("cli.remove_subdir_failed", name=entry.name, e=e))
    
    if subdir_stats['dirs_removed'] > 0:
        print(t("cli.subdirs_cleaned", dirs_removed=subdir_stats['dirs_removed'], files_restored=subdir_stats['files_restored']))
    else:
        print(t("cli.no_subdirs"))
    
    print(t("cli.reset_step1"))
    exiftool_mgr = get_exiftool_manager()
    restore_stats = exiftool_mgr.restore_files_from_manifest(args.directory)
    
    restored = restore_stats.get('restored', 0)
    if restored > 0:
        print(t("cli.manifest_restored", restored=restored))
    
    # V4.0.5: Manifest 可能不包含所有文件（来自上次运行的残留文件）
    # 扫描评分目录，将所有文件强制移回根目录
    fallback_restored = 0
    for rating_dir in rating_dirs:
        rating_path = os.path.join(args.directory, rating_dir)
        if not os.path.exists(rating_path):
            continue
        
        for filename in os.listdir(rating_path):
            src = os.path.join(rating_path, filename)
            dst = os.path.join(args.directory, filename)
            if os.path.isfile(src):
                try:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
                    fallback_restored += 1
                except Exception as e:
                    print(t("cli.move_back_failed", filename=filename, e=e))
    
    if fallback_restored > 0:
        print(t("cli.fallback_restored", fallback_restored=fallback_restored))
    
    # V4.3.1: 按目录名摊平兜底——manifest/根目录评分扫描会漏掉「鸟种优先」布局下
    # 鸟种/星级/burst_ 子目录里的深层文件;与 UI reset 保持一致。force_flatten 幂等安全
    # （同名不覆盖、只动 SuperPicky 目录、不碰用户目录）。
    flatten_moved = 0
    try:
        from tools.find_bird_util import force_flatten_directory
        _fstats = force_flatten_directory(args.directory)
        flatten_moved = int(_fstats.get("moved", 0)) if _fstats else 0
    except Exception as _fe:
        print(t("cli.flatten_fallback_failed", error=_fe))

    total_restored = restored + fallback_restored + flatten_moved
    if total_restored == 0:
        print(t("cli.nothing_to_restore"))
    else:
        print(t("cli.total_restored", total_restored=total_restored))

    print(t("cli.reset_step2"))
    i18n = get_i18n()
    success = reset(args.directory, i18n=i18n)
    
    # V4.0.5: 删除评分目录（所有文件已移走）
    print(t("cli.reset_step3"))
    deleted_dirs = 0
    for rating_dir in rating_dirs:
        rating_path = os.path.join(args.directory, rating_dir)
        if os.path.exists(rating_path) and os.path.isdir(rating_path):
            try:
                shutil.rmtree(rating_path)
                print(t("cli.deleted", name=rating_dir))
                deleted_dirs += 1
            except Exception as e:
                print(t("cli.remove_rating_dir_failed", rating_dir=rating_dir, e=e))
    
    # V4.0.5: 清理 .superpicky 隐藏目录和 manifest 文件
    superpicky_dir = os.path.join(args.directory, ".superpicky")
    if os.path.exists(superpicky_dir):
        try:
            shutil.rmtree(superpicky_dir)
            print(t("cli.deleted", name=".superpicky/"))
            deleted_dirs += 1
        except Exception:
            try:
                import subprocess
                subprocess.run(['rm', '-rf', superpicky_dir], check=True, timeout=120)
                print(t("cli.deleted", name=".superpicky/ (force)"))
                deleted_dirs += 1
            except Exception as e2:
                print(t("cli.superpicky_dir_remove_failed", e2=e2))
    
    manifest_file = os.path.join(args.directory, ".superpicky_manifest.json")
    if os.path.exists(manifest_file):
        try:
            os.remove(manifest_file)
            print(t("cli.deleted", name=".superpicky_manifest.json"))
        except Exception as e:
            print(t("cli.manifest_remove_failed", e=e))
    
    # 清理 macOS ._burst_XXX 残留文件
    for filename in os.listdir(args.directory):
        if filename.startswith('._burst_') or filename.startswith('._其他') or filename.startswith('._栗'):
            try:
                os.remove(os.path.join(args.directory, filename))
            except Exception:
                pass
    
    if deleted_dirs > 0:
        print(t("cli.dirs_cleaned", deleted_dirs=deleted_dirs))
    else:
        print(t("cli.no_empty_dirs"))
    
    if success:
        print(t("cli.reset_done"))
        return 0
    else:
        print(t("cli.reset_failed"))
        return 1


def cmd_info(args):
    """显示目录信息"""
    from tools.report_db import ReportDB
    
    print_banner()
    print(t("cli.info_dir", directory=args.directory))
    
    # 检查各种文件
    db_path = os.path.join(args.directory, '.superpicky', 'report.db')
    manifest_path = os.path.join(args.directory, '.superpicky_manifest.json')
    
    print(t("cli.file_status"))
    
    if os.path.exists(db_path):
        print(t("cli.report_db_exists"))
        try:
            db = ReportDB(args.directory)
            stats = db.get_statistics()
            total = stats['total']
            print(t("cli.report_db_records", total=total))
            
            print(t("cli.rating_distribution"))
            for rating, count in sorted(stats['by_rating'].items()):
                stars = "⭐" * max(0, int(rating)) if rating >= 0 else "❌"
                print(t("cli.rating_line", stars=stars, rating=rating, count=count))
            
            if stats['flying'] > 0:
                print(t("cli.flying_count", flying=stats['flying']))
            
            db.close()
        except Exception as e:
            print(t("cli.read_failed", e=e))
    else:
        print(t("cli.report_db_missing"))
    
    if os.path.exists(manifest_path):
        print(t("cli.manifest_exists"))
    else:
        print(t("cli.manifest_missing"))
    
    # 检查分类文件夹
    folders = ['3star_excellent', '2star_good', '1star_average', '0star_reject',
               '3星_优选', '2星_良好', '1星_普通', '0星_放弃']  # Support both languages
    existing_folders = []
    for folder in folders:
        folder_path = os.path.join(args.directory, folder)
        if os.path.exists(folder_path):
            count = len([f for f in os.listdir(folder_path) 
                        if f.lower().endswith(('.nef', '.cr2', '.arw', '.jpg', '.jpeg'))])
            existing_folders.append((folder, count))
    
    if existing_folders:
        print(t("cli.sorted_folders"))
        for folder, count in existing_folders:
            print(t("cli.folder_count", folder=folder, count=count))
    
    print()
    return 0


def cmd_identify(args):
    """识别鸟类。

    委托给 birdid_cli 的共享实现（identify_single / display_result / write_exif），
    与 `birdid_cli identify` 完全同一套逻辑（支持 osea/tta/ebird/country/region），
    不再维护本地的简化版（已合并，消除分叉）。
    """
    import birdid_cli

    print_banner()
    print(t("cli.identify_title"))
    print(t("cli.identify_image", image=args.image))
    print(t("cli.identify_model", model=getattr(args, 'model', 'birdid2024').upper())
          + (" + TTA" if getattr(args, 'model', '') == 'osea' and getattr(args, 'tta', False) else ""))
    print(t("cli.identify_options", yolo=t("cli.yes") if args.yolo else t("cli.no"), gps=t("cli.yes") if args.gps else t("cli.no"), ebird=t("cli.yes") if getattr(args, 'ebird', True) else t("cli.no")))
    print(t("cli.identifying"))

    result = birdid_cli.identify_single(args, args.image)
    success = birdid_cli.display_result(result, verbose=True)

    if getattr(args, 'write_exif', False) and success:
        print(t("cli.writing_exif"))
        if birdid_cli.write_exif(args.image, result, args.threshold):
            print(t("cli.written", name=result['results'][0]['cn_name']))
        else:
            print(t("cli.write_failed"))

    print()
    return 0 if success else 1


def cmd_batch(args):
    """递归批量处理子目录"""
    from core.recursive_scanner import is_dangerous_root, is_processed, scan_directories
    from core.batch_processor import BatchProcessor
    from tools.cli_settings import resolve_processing_settings, apply_config_overrides
    from advanced_config import get_advanced_config

    print_banner()
    print(t("cli.batch_dir", directory=args.directory))

    is_dangerous, reason = is_dangerous_root(args.directory)
    if is_dangerous:
        print(f"\n❌ {t('health.dangerous_dir_title')}")
        print(t("health.dangerous_dir_msg", directory=args.directory, reason=reason))
        return 1
    
    # 扫描
    scan_results = scan_directories(args.directory, max_depth=args.max_depth)
    
    if not scan_results:
        print(f"\n❌ {t('health.no_photos_title')}")
        print(t("health.no_photos_msg", directory=args.directory))
        return 1
    
    # 预览
    print(t("cli.batch_found", count=len(scan_results)))
    total_photos = 0
    for i, scanned_dir in enumerate(scan_results, 1):
        rel = os.path.relpath(scanned_dir.path, args.directory)
        n = scanned_dir.photo_count
        processed = is_processed(scanned_dir.path)
        status = t("cli.batch_processed_mark") if processed else ""
        print(t("cli.batch_dir_line", i=i, rel=rel, n=n, status=status))
        total_photos += n
    print(t("cli.batch_total", total_photos=total_photos))
    
    # Dry run
    if args.dry_run:
        print(t("cli.dry_run"))
        return 0
    
    # 确认
    if not args.yes:
        confirm = input(t("cli.batch_confirm", count=len(scan_results)))
        if confirm.lower() not in ['y', 'yes']:
            print(t("cli.cancelled"))
            return 1
    
    # 解析设置：CLI 显式 > advanced_config(= GUI) > 默认。仅改内存，不回写。
    adv_config = get_advanced_config()
    apply_config_overrides(args, adv_config)
    if getattr(args, 'keep_temp', None) is None and not args.cleanup:
        adv_config.config["keep_temp_files"] = True
    settings = resolve_processing_settings(args, adv_config)

    # 执行批量处理
    processor = BatchProcessor(
        root_dir=args.directory,
        settings=settings,
        skip_existing=args.skip_existing,
        max_depth=args.max_depth,
    )
    
    result = processor.process(
        dirs=scan_results,
        organize_files=args.organize,
        cleanup_temp=not adv_config.keep_temp_files,
    )
    
    print(t("cli.batch_done"))
    return 0 if result.failed_dirs == 0 else 1


def cmd_batch_reset(args):
    """批量重置所有已处理的子目录"""
    from core.recursive_scanner import is_processed
    from tools.merged_report_db import find_processed_subdirs
    import shutil
    
    print_banner()
    print(t("cli.batch_reset_dir", directory=args.directory))
    
    # Find all processed directories (including root)
    processed_dirs = find_processed_subdirs(args.directory)
    
    if not processed_dirs:
        print(t("cli.no_processed_dirs"))
        return 1
    
    print(t("cli.processed_found", count=len(processed_dirs)))
    for i, d in enumerate(processed_dirs, 1):
        rel = os.path.relpath(d, args.directory)
        print(f"  {i:3d}. {rel}/")
    
    if not args.yes:
        confirm = input(t("cli.batch_reset_confirm", count=len(processed_dirs)))
        if confirm.lower() not in ['y', 'yes']:
            print(t("cli.cancelled"))
            return 1
    
    # 逐个重置（复用 cmd_reset 的核心逻辑）
    success_count = 0
    fail_count = 0
    for i, d in enumerate(processed_dirs, 1):
        rel = os.path.relpath(d, args.directory)
        print(f"\n{'━' * 40}")
        print(t("cli.batch_reset_item", i=i, count=len(processed_dirs), rel=rel))
        
        # 创建一个模拟的 args 对象给 cmd_reset
        reset_args = SimpleNamespace(directory=d, yes=True)
        
        try:
            ret = cmd_reset(reset_args)
            if ret == 0:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(t("cli.reset_failed_error", e=e))
            fail_count += 1
    
    # 清理批量报告
    batch_report = os.path.join(args.directory, '.superpicky_batch.json')
    if os.path.exists(batch_report):
        os.remove(batch_report)
    
    print(f"\n{'═' * 40}")
    print(t("cli.batch_reset_done", success_count=success_count, fail_count=fail_count))
    return 0 if fail_count == 0 else 1


def _add_processing_args(parser):
    """
    为 process / batch 添加共享的处理参数。

    设计：可覆盖项一律 `default=None`（哨兵）。None = 用户未在命令行指定 →
    由 tools/cli_settings 回落到 advanced_config（与 GUI 同源），使「不带参数」时
    默认值与 GUI 完全一致；显式给出则覆盖。每个设置都有 flag → 完全可控（面向 agent）。
    布尔三态用 argparse.BooleanOptionalAction（--x / --no-x，未给为 None）。
    """
    parser.add_argument('directory', help=t("cli.help_directory"))
    # —— 评分阈值 / Scoring thresholds ——
    parser.add_argument('-s', '--sharpness', type=int, default=None,
                        help=t("cli.help_sharpness"))
    parser.add_argument('-n', '--nima-threshold', type=float, default=None,
                        help=t("cli.help_nima"))
    parser.add_argument('-c', '--confidence', type=int, default=None,
                        help=t("cli.help_confidence"))
    parser.add_argument('--skill-level',
                        choices=['beginner', 'intermediate', 'master', 'custom'],
                        default=None,
                        help=t("cli.help_skill"))
    # —— 检测开关（三态）/ Detection toggles ——
    parser.add_argument('--flight', action=argparse.BooleanOptionalAction, default=None,
                        help=t("cli.help_flight"))
    parser.add_argument('--burst', action=argparse.BooleanOptionalAction, default=None,
                        help=t("cli.help_burst"))
    parser.add_argument('--exposure', action=argparse.BooleanOptionalAction, default=None,
                        help=t("cli.help_exposure"))
    parser.add_argument('--exposure-threshold', type=float, default=None,
                        help=t("cli.help_exposure_threshold"))
    # —— 0 星判定阈值（高级）/ Zero-star thresholds ——
    parser.add_argument('--min-sharpness', type=int, default=None,
                        help=t("cli.help_min_sharpness"))
    parser.add_argument('--min-nima', type=float, default=None,
                        help=t("cli.help_min_nima"))
    parser.add_argument('--picked-top', type=int, default=None,
                        help=t("cli.help_picked_top"))
    # —— 元数据 / 目录布局 / Metadata & layout ——
    parser.add_argument('--xmp', action=argparse.BooleanOptionalAction, default=None,
                        help=t("cli.help_xmp"))
    parser.add_argument('--arw-write-mode',
                        choices=['sidecar', 'embedded', 'inplace', 'auto'], default=None,
                        help=t("cli.help_arw_mode"))
    parser.add_argument('--metadata-mode',
                        choices=['embedded', 'sidecar', 'none'], default=None,
                        help=t("cli.help_metadata_mode"))
    parser.add_argument('--folder-layout',
                        choices=['species-first', 'rating-first'], default=None,
                        help=t("cli.help_folder_layout"))
    parser.add_argument('--name-format',
                        choices=['default', 'avilist', 'clements', 'birdlife', 'scientific'],
                        default=None, help=t("cli.help_name_format"))
    # —— BirdID 自动识鸟 / Auto bird ID ——
    parser.add_argument('--auto-identify', '-i', action='store_true', default=False,
                        help=t("cli.help_auto_identify"))
    parser.add_argument('--ebird', action=argparse.BooleanOptionalAction, default=None,
                        help=t("cli.help_ebird"))
    parser.add_argument('--birdid-country', type=str, default=None,
                        help=t("cli.help_country"))
    parser.add_argument('--birdid-region', type=str, default=None,
                        help=t("cli.help_region"))
    parser.add_argument('--birdid-threshold', type=float, default=None,
                        help=t("cli.help_birdid_threshold"))
    # —— 临时文件 / 裁剪 / Temp & crop ——
    parser.add_argument('--keep-temp-files', action=argparse.BooleanOptionalAction,
                        dest='keep_temp', default=None,
                        help=t("cli.help_keep_temp"))
    parser.add_argument('--save-crop', action='store_true', default=False,
                        help=t("cli.help_save_crop"))


def main():
    """主入口"""
    apply_saved_language()
    parser = argparse.ArgumentParser(
        prog='superpicky_cli',
        description=t("cli.sp_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("cli.examples")
    )
    
    subparsers = parser.add_subparsers(dest='command', help=t("cli.help_commands"))
    
    # ===== process 命令 =====
    # 设计：可覆盖项一律 default=None（哨兵）。None = 用户未指定 → 由 tools/cli_settings
    # 回落到 advanced_config（与 GUI 同源，默认值一致）。每项都有 flag → 完全可控（面向 agent）。
    p_process = subparsers.add_parser('process', help=t("cli.cmd_process"))
    _add_processing_args(p_process)
    p_process.add_argument('--no-organize', action='store_false', dest='organize',
                          help=t("cli.help_no_organize"))
    p_process.add_argument('--no-cleanup', action='store_false', dest='cleanup',
                          help=t("cli.help_no_cleanup"))
    p_process.add_argument('-q', '--quiet', action='store_true', help=t("cli.help_quiet"))
    p_process.add_argument('--cleanup-days', type=int, default=30,
                          help=t("cli.help_cleanup_days"))
    p_process.set_defaults(organize=True, cleanup=True)

    # ===== reset 命令 =====
    p_reset = subparsers.add_parser('reset', help=t("cli.cmd_reset"))
    p_reset.add_argument('directory', help=t("cli.help_directory"))
    p_reset.add_argument('-y', '--yes', action='store_true',
                        help=t("cli.help_yes"))
    
    # ===== info 命令 =====
    p_info = subparsers.add_parser('info', help=t("cli.cmd_info"))
    p_info.add_argument('directory', help=t("cli.help_directory"))
    
    # ===== burst 命令 =====
    p_burst = subparsers.add_parser('burst', help=t("cli.cmd_burst"))
    p_burst.add_argument('directory', help=t("cli.help_directory"))
    p_burst.add_argument('-m', '--min-count', type=int, default=4,
                         help=t("cli.help_min_count"))
    p_burst.add_argument('-t', '--threshold', type=int, default=250,
                         help=t("cli.help_threshold_ms"))
    p_burst.add_argument('--no-phash', action='store_false', dest='phash',
                         help=t("cli.help_no_phash"))
    p_burst.add_argument('--execute', action='store_true',
                         help=t("cli.help_execute"))
    p_burst.set_defaults(phash=True)

    # ===== identify 命令 =====
    # 与 birdid_cli 共用同一套参数（model/tta/ebird/country/region…）与识别逻辑，避免分叉。
    import birdid_cli
    p_identify = subparsers.add_parser('identify', help=t("cli.cmd_identify"))
    birdid_cli.add_identify_arguments(p_identify, multi=False)
    
    # ===== batch 命令 =====
    # 共享处理参数（可覆盖项 default=None，与 process 一致），加 batch 专属选项。
    p_batch = subparsers.add_parser('batch', help=t("cli.help_batch"))
    _add_processing_args(p_batch)
    p_batch.add_argument('--no-organize', action='store_false', dest='organize')
    p_batch.add_argument('--no-cleanup', action='store_false', dest='cleanup')
    p_batch.add_argument('--skip-existing', action='store_true',
                        help=t("cli.help_skip_existing"))
    p_batch.add_argument('--dry-run', action='store_true',
                        help=t("cli.help_dry_run"))
    p_batch.add_argument('--max-depth', type=int, default=DEFAULT_SCAN_MAX_DEPTH,
                        help=t("cli.help_max_depth", depth=DEFAULT_SCAN_MAX_DEPTH))
    p_batch.add_argument('-y', '--yes', action='store_true',
                        help=t("cli.help_yes"))
    p_batch.add_argument('-q', '--quiet', action='store_true')
    p_batch.set_defaults(organize=True, cleanup=True,
                        skip_existing=False, dry_run=False)
    
    # ===== batch-reset 命令 =====
    p_batch_reset = subparsers.add_parser('batch-reset', help=t("cli.help_batch_reset"))
    p_batch_reset.add_argument('directory', help=t("cli.help_root"))
    p_batch_reset.add_argument('-y', '--yes', action='store_true',
                              help=t("cli.help_yes"))

    # 解析参数
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # identify 命令验证文件，其他命令验证目录
    if args.command == 'identify':
        if not os.path.isfile(args.image):
            print(t("cli.file_not_found", path=args.image))
            return 1
        args.image = os.path.abspath(args.image)
    else:
        # 验证目录
        if not os.path.isdir(args.directory):
            print(t("cli.dir_not_found", path=args.directory))
            return 1
        args.directory = os.path.abspath(args.directory)

    # 执行命令
    if args.command == 'process':
        return cmd_process(args)
    elif args.command == 'reset':
        return cmd_reset(args)
    elif args.command == 'info':
        return cmd_info(args)
    elif args.command == 'burst':
        return cmd_burst(args)
    elif args.command == 'identify':
        return cmd_identify(args)
    elif args.command == 'batch':
        return cmd_batch(args)
    elif args.command == 'batch-reset':
        return cmd_batch_reset(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
