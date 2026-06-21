#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky CLI - 命令行入口
完整功能版本 - 支持处理、重置、重新评星、鸟类识别

Usage:
    python superpicky_cli.py process /path/to/photos [options]
    python superpicky_cli.py reset /path/to/photos
    python superpicky_cli.py restar /path/to/photos [options]
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

    # 重新评星
    python superpicky_cli.py restar ~/Photos/Birds --sharpness 700 --nima 5.5

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
from tools.i18n import t

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_banner():
    """打印 CLI 横幅"""
    print("\n" + "━" * 60)
    print(t("cli.banner", version="4.2.0"))
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
        print(f"  照片数: {data['photos']}")
        print(f"  连拍组: {data['groups']}")
        
        for g in data['group_details']:
            print(f"    组 #{g['id']}: {g['count']} 张, 最佳: {g['best']}")
    
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
    print(f"⚙️  曝光检测: {'是' if settings.detect_exposure else '否'}")
    print(t("cli.detect_burst", value=t("cli.enabled") if settings.detect_burst else t("cli.disabled")))
    print(t("cli.organize_files", value=t("cli.enabled") if args.organize else t("cli.disabled")))
    print(f"⚙️  ARW 写入: {adv_config.config.get('arw_write_mode')}")
    print(f"⚙️  清理临时: {'否' if adv_config.keep_temp_files else '是'}")

    if settings.auto_identify:
        print(f"⚙️  自动识鸟: 是 (2★+ 照片)")
        if settings.birdid_country_code:
            print(f"  └─ 国家: {settings.birdid_country_code}")
        if settings.birdid_region_code:
            print(f"  └─ 区域: {settings.birdid_region_code}")
        print(f"  └─ 置信度阈值: {settings.birdid_confidence_threshold}%")
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

    print("\n✅ 处理完成!")
    return 0


def cmd_reset(args):
    """重置目录"""
    from tools.find_bird_util import reset
    from tools.exiftool_manager import get_exiftool_manager
    from tools.i18n import get_i18n
    import shutil
    
    print_banner()
    print(f"\n🔄 重置目录: {args.directory}")
    
    if not args.yes:
        confirm = input("\n⚠️  这将重置所有评分和文件位置，确定继续? [y/N]: ")
        if confirm.lower() not in ['y', 'yes']:
            print("❌ 已取消")
            return 1
    
    # V4.0.5: 先处理所有子目录（burst_XXX、鸟种 Other_Birds 等）
    # 将文件移回评分目录，然后由步骤1的 manifest 恢复到根目录
    print("\n📂 步骤0: 清理评分目录中的子目录...")
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
                print(f"  ⚠️ 跳过符号链接目录: {rating_dir}/{entry.name}")
                continue
            if entry.is_dir(follow_symlinks=False):
                print(f"  📁 打平子目录: {rating_dir}/{entry.name}")
                # 递归将所有文件移回评分目录
                for root, dirs, files in os.walk(entry_path):
                    # 防御性处理：不进入任何符号链接子目录
                    dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                    for filename in files:
                        src = os.path.join(root, filename)
                        if os.path.islink(src):
                            print(f"    ⚠️ 跳过符号链接文件: {filename}")
                            continue
                        dst = os.path.join(rating_path, filename)
                        if os.path.isfile(src):
                            try:
                                if os.path.exists(dst):
                                    os.remove(dst)
                                shutil.move(src, dst)
                                subdir_stats['files_restored'] += 1
                            except Exception as e:
                                print(f"    ⚠️ 移动失败: {filename}: {e}")

                # 删除子目录
                try:
                    if os.path.exists(entry_path):
                        shutil.rmtree(entry_path)
                    subdir_stats['dirs_removed'] += 1
                except Exception as e:
                    print(f"    ⚠️ 删除目录失败: {entry.name}: {e}")
    
    if subdir_stats['dirs_removed'] > 0:
        print(f"  ✅ 已清理 {subdir_stats['dirs_removed']} 个子目录，恢复 {subdir_stats['files_restored']} 个文件")
    else:
        print("  ℹ️  无子目录需要清理")
    
    print("\n📂 步骤1: 恢复文件到主目录...")
    exiftool_mgr = get_exiftool_manager()
    restore_stats = exiftool_mgr.restore_files_from_manifest(args.directory)
    
    restored = restore_stats.get('restored', 0)
    if restored > 0:
        print(f"  ✅ 已通过 Manifest 恢复 {restored} 个文件")
    
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
                    print(f"    ⚠️ 回迁失败: {filename}: {e}")
    
    if fallback_restored > 0:
        print(f"  ✅ 额外恢复了 {fallback_restored} 个残留文件到根目录")
    
    # V4.3.1: 按目录名摊平兜底——manifest/根目录评分扫描会漏掉「鸟种优先」布局下
    # 鸟种/星级/burst_ 子目录里的深层文件;与 UI reset 保持一致。force_flatten 幂等安全
    # （同名不覆盖、只动 SuperPicky 目录、不碰用户目录）。
    flatten_moved = 0
    try:
        from tools.find_bird_util import force_flatten_directory
        _fstats = force_flatten_directory(args.directory)
        flatten_moved = int(_fstats.get("moved", 0)) if _fstats else 0
    except Exception as _fe:
        print(f"  ⚠️ 摊平兜底失败 / flatten fallback failed: {_fe}")

    total_restored = restored + fallback_restored + flatten_moved
    if total_restored == 0:
        print("  ℹ️  无需恢复文件")
    else:
        print(f"  ✅ 共恢复 {total_restored} 个文件")

    print("\n📝 步骤2: 清理并重置 EXIF 元数据...")
    i18n = get_i18n('zh_CN')
    success = reset(args.directory, i18n=i18n)
    
    # V4.0.5: 删除评分目录（所有文件已移走）
    print("\n🗑️  步骤3: 清理目录...")
    deleted_dirs = 0
    for rating_dir in rating_dirs:
        rating_path = os.path.join(args.directory, rating_dir)
        if os.path.exists(rating_path) and os.path.isdir(rating_path):
            try:
                shutil.rmtree(rating_path)
                print(f"  🗑️ 已删除: {rating_dir}")
                deleted_dirs += 1
            except Exception as e:
                print(f"  ⚠️ 删除目录失败: {rating_dir}: {e}")
    
    # V4.0.5: 清理 .superpicky 隐藏目录和 manifest 文件
    superpicky_dir = os.path.join(args.directory, ".superpicky")
    if os.path.exists(superpicky_dir):
        try:
            shutil.rmtree(superpicky_dir)
            print("  🗑️ 已删除: .superpicky/")
            deleted_dirs += 1
        except Exception:
            try:
                import subprocess
                subprocess.run(['rm', '-rf', superpicky_dir], check=True)
                print("  🗑️ 已删除: .superpicky/ (force)")
                deleted_dirs += 1
            except Exception as e2:
                print(f"  ⚠️ .superpicky 删除失败: {e2}")
    
    manifest_file = os.path.join(args.directory, ".superpicky_manifest.json")
    if os.path.exists(manifest_file):
        try:
            os.remove(manifest_file)
            print("  🗑️ 已删除: .superpicky_manifest.json")
        except Exception as e:
            print(f"  ⚠️ manifest 删除失败: {e}")
    
    # 清理 macOS ._burst_XXX 残留文件
    for filename in os.listdir(args.directory):
        if filename.startswith('._burst_') or filename.startswith('._其他') or filename.startswith('._栗'):
            try:
                os.remove(os.path.join(args.directory, filename))
            except Exception:
                pass
    
    if deleted_dirs > 0:
        print(f"  ✅ 已清理 {deleted_dirs} 个目录")
    else:
        print("  ℹ️  无空目录需要清理")
    
    if success:
        print("\n✅ 目录重置完成!")
        return 0
    else:
        print("\n❌ 重置失败")
        return 1


def cmd_restar(args):
    """重新评星"""
    from post_adjustment_engine import PostAdjustmentEngine
    from tools.exiftool_manager import get_exiftool_manager
    from advanced_config import get_advanced_config
    import shutil
    
    print_banner()
    print(f"\n🔄 重新评星: {args.directory}")
    print(f"⚙️  新锐度阈值: {args.sharpness}")
    print(f"⚙️  新美学阈值: {args.nima_threshold}")
    print(f"⚙️  连拍检测: {'是' if args.burst else '否'}")
    print(t("cli.xmp", value=t("cli.enabled") if args.xmp else t("cli.disabled")))

    # 更新 ARW 写入策略
    adv_config = get_advanced_config()
    adv_config.config["arw_write_mode"] = "sidecar" if args.xmp else "embedded"
    adv_config.save()
    
    # V4.0: 先清理 burst 子目录（将文件移回评分目录）
    print("\n📂 步骤0: 清理连拍子目录...")
    rating_dirs = ['3star_excellent', '2star_good', '1star_average', '0star_reject',
                   '3星_优选', '2星_良好', '1星_普通', '0星_放弃']  # Support both languages
    burst_stats = {'dirs_removed': 0, 'files_restored': 0}
    
    for rating_dir in rating_dirs:
        rating_path = os.path.join(args.directory, rating_dir)
        if not os.path.exists(rating_path):
            continue
        
        for entry in os.scandir(rating_path):
            if not entry.name.startswith('burst_'):
                continue

            burst_path = entry.path
            if entry.is_symlink():
                print(f"    ⚠️ 跳过符号链接连拍目录: {entry.name}")
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue

            for filename in os.listdir(burst_path):
                src = os.path.join(burst_path, filename)
                dst = os.path.join(rating_path, filename)
                if os.path.islink(src):
                    print(f"    ⚠️ 跳过符号链接文件: {filename}")
                    continue
                if os.path.isfile(src):
                    try:
                        if os.path.exists(dst):
                            os.remove(dst)
                        shutil.move(src, dst)
                        burst_stats['files_restored'] += 1
                    except Exception as e:
                        print(f"    ⚠️ 移动失败: {filename}: {e}")

            try:
                if not os.listdir(burst_path):
                    os.rmdir(burst_path)
                else:
                    shutil.rmtree(burst_path)
                burst_stats['dirs_removed'] += 1
            except Exception as e:
                print(f"    ⚠️ 删除目录失败: {entry.name}: {e}")
    
    if burst_stats['dirs_removed'] > 0:
        print(f"  ✅ 已清理 {burst_stats['dirs_removed']} 个连拍目录，恢复 {burst_stats['files_restored']} 个文件")
    else:
        print("  ℹ️  无连拍子目录需要清理")
    
    # 检查 report.db 是否存在
    db_path = os.path.join(args.directory, '.superpicky', 'report.db')
    if not os.path.exists(db_path):
        print("\n❌ 未找到 report.db，请先运行 process 命令")
        return 1
    
    # 初始化引擎
    engine = PostAdjustmentEngine(args.directory)
    
    # 加载报告
    success, msg = engine.load_report()
    if not success:
        print(f"\n❌ 加载数据失败: {msg}")
        return 1
    
    print(f"\n📊 {msg}")
    
    # 获取高级配置的 0 星阈值
    adv_config = get_advanced_config()
    # --confidence 显式给出时覆盖 min_confidence（与 GUI 同源旋钮），否则用配置值
    if getattr(args, 'confidence', None) is not None:
        min_confidence = args.confidence / 100.0
    else:
        min_confidence = getattr(adv_config, 'min_confidence', 0.5)
    min_sharpness = getattr(adv_config, 'min_sharpness', 250)
    min_nima = getattr(adv_config, 'min_nima', 4.0)

    # 锐度/美学阈值：未显式指定则跟随 skill 预设/配置（与 GUI 默认一致）
    from core.skill_presets import get_skill_level_thresholds
    preset_sharp, preset_aesth = get_skill_level_thresholds(
        adv_config.skill_level, adv_config)
    sharpness_threshold = args.sharpness if args.sharpness is not None else preset_sharp
    nima_threshold = args.nima_threshold if args.nima_threshold is not None else preset_aesth

    # 重新计算评分
    new_photos = engine.recalculate_ratings(
        photos=engine.photos_data,
        min_confidence=min_confidence,
        min_sharpness=min_sharpness,
        min_nima=min_nima,
        sharpness_threshold=sharpness_threshold,
        nima_threshold=nima_threshold
    )
    
    # 统计变化
    changed_photos = []
    old_stats = {'star_3': 0, 'star_2': 0, 'star_1': 0, 'star_0': 0}
    for photo in new_photos:
        old_rating = int(photo.get('rating', 0))
        new_rating = photo.get('新星级', 0)
        
        # 统计原始评分
        if old_rating == 3:
            old_stats['star_3'] += 1
        elif old_rating == 2:
            old_stats['star_2'] += 1
        elif old_rating == 1:
            old_stats['star_1'] += 1
        else:
            old_stats['star_0'] += 1
        
        if old_rating != new_rating:
            photo['filename'] = photo.get('filename', '')
            changed_photos.append(photo)
    
    # 统计新评分
    new_stats = engine.get_statistics(new_photos)
    
    # 使用共享格式化模块输出对比
    from core.stats_formatter import format_restar_comparison, print_summary
    lines = format_restar_comparison(old_stats, new_stats, len(changed_photos))
    print_summary(lines)
    
    if len(changed_photos) == 0:
        print("\n✅ 无需更新任何照片")
        # 即使评分无变化，如果开启了连拍检测，仍然运行
        if args.burst and args.organize:
            _run_burst_detection_restar(args.directory)
        return 0
    
    if not args.yes:
        confirm = input("\n确定应用新评分? [y/N]: ")
        if confirm.lower() not in ['y', 'yes']:
            print("❌ 已取消")
            return 1
    
    # 准备 EXIF 批量更新数据
    exiftool_mgr = get_exiftool_manager()
    batch_data = []
    
    for photo in changed_photos:
        filename = photo.get('filename', '')
        file_path = engine.find_image_file(filename)
        if file_path:
            rating = photo.get('新星级', 0)
            batch_data.append({
                'file': file_path,
                'rating': rating,
                'pick': 0
            })
    
    # 写入 EXIF
    print("\n📝 写入 EXIF 元数据...")
    exif_stats = exiftool_mgr.batch_set_metadata(batch_data)
    print(f"  ✅ 成功: {exif_stats.get('success', 0)}, 失败: {exif_stats.get('failed', 0)}")
    
    # 更新数据库
    print("\n📊 更新 report.db...")
    picked_files = set()  # CLI 模式暂不支持精选计算
    engine.update_report_csv(new_photos, picked_files)
    
    # 文件重分配
    if args.organize:
        from constants import get_rating_folder_name
        
        moved_count = 0
        for photo in changed_photos:
            filename = photo.get('filename', '')
            file_path = engine.find_image_file(filename)
            if not file_path:
                continue
            
            new_rating = photo.get('新星级', 0)
            target_folder = get_rating_folder_name(new_rating)
            target_dir = os.path.join(args.directory, target_folder)
            target_path = os.path.join(target_dir, os.path.basename(file_path))
            
            if os.path.dirname(file_path) == target_dir:
                continue
            
            try:
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                if not os.path.exists(target_path):
                    shutil.move(file_path, target_path)
                    moved_count += 1
            except Exception:
                pass
        
        if moved_count > 0:
            print(f"  ✅ 已移动 {moved_count} 个文件")
        
        # V4.0: 重新运行连拍检测
        if args.burst:
            _run_burst_detection_restar(args.directory)
    
    print("\n✅ 重新评星完成!")
    return 0


def _run_burst_detection_restar(directory: str):
    """Restar 后运行连拍检测"""
    from core.burst_detector import BurstDetector
    from tools.exiftool_manager import get_exiftool_manager
    
    print("\n📷 正在执行连拍检测...")
    detector = BurstDetector(use_phash=True)
    
    rating_dirs = ['3star_excellent', '2star_good', '3星_优选', '2星_良好']  # Support both languages
    total_groups = 0
    total_moved = 0
    
    exiftool_mgr = get_exiftool_manager()
    
    for rating_dir in rating_dirs:
        subdir = os.path.join(directory, rating_dir)
        if not os.path.exists(subdir):
            continue
        
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
        photos = detector.enrich_from_db(photos, directory)
        groups = detector.detect_groups(photos)
        groups = detector.select_best_in_groups(groups)
        
        burst_stats = detector.process_burst_groups(groups, subdir, exiftool_mgr)
        total_groups += burst_stats['groups_processed']
        total_moved += burst_stats['photos_moved']
    
    if total_groups > 0:
        print(f"  ✅ 连拍检测完成: {total_groups} 组, 移动 {total_moved} 张照片")
    else:
        print("  ℹ️  未检测到连拍组")


def cmd_info(args):
    """显示目录信息"""
    from tools.report_db import ReportDB
    
    print_banner()
    print(f"\n📁 目录: {args.directory}")
    
    # 检查各种文件
    db_path = os.path.join(args.directory, '.superpicky', 'report.db')
    manifest_path = os.path.join(args.directory, '.superpicky_manifest.json')
    
    print("\n📋 文件状态:")
    
    if os.path.exists(db_path):
        print("  ✅ report.db 存在")
        try:
            db = ReportDB(args.directory)
            stats = db.get_statistics()
            total = stats['total']
            print(f"     共 {total} 条记录")
            
            print("\n📊 评分分布:")
            for rating, count in sorted(stats['by_rating'].items()):
                stars = "⭐" * max(0, int(rating)) if rating >= 0 else "❌"
                print(f"     {stars} {rating}星: {count} 张")
            
            if stats['flying'] > 0:
                print(f"\n🦅 飞鸟照片: {stats['flying']} 张")
            
            db.close()
        except Exception as e:
            print(f"     读取失败: {e}")
    else:
        print("  ❌ report.db 不存在")
    
    if os.path.exists(manifest_path):
        print("  ✅ manifest 文件存在 (可重置)")
    else:
        print("  ℹ️  manifest 文件不存在")
    
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
        print("\n📂 分类文件夹:")
        for folder, count in existing_folders:
            print(f"     {folder}/: {count} 张")
    
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
    print(f"\n🐦 鸟类识别 / Bird Identification")
    print(f"📸 图片: {args.image}")
    print(f"🤖 模型: {getattr(args, 'model', 'birdid2024').upper()}"
          + (" + TTA" if getattr(args, 'model', '') == 'osea' and getattr(args, 'tta', False) else ""))
    print(f"⚙️  YOLO裁剪: {'是' if args.yolo else '否'}  GPS: {'是' if args.gps else '否'}"
          f"  eBird: {'是' if getattr(args, 'ebird', True) else '否'}")
    print("🔍 正在识别...")

    result = birdid_cli.identify_single(args, args.image)
    success = birdid_cli.display_result(result, verbose=True)

    if getattr(args, 'write_exif', False) and success:
        print(f"\n📝 写入 EXIF...")
        if birdid_cli.write_exif(args.image, result, args.threshold):
            print(f"  ✅ 已写入: {result['results'][0]['cn_name']}")
        else:
            print(f"  ❌ 写入失败")

    print()
    return 0 if success else 1


def cmd_batch(args):
    """递归批量处理子目录"""
    from core.recursive_scanner import is_dangerous_root, is_processed, scan_directories
    from core.batch_processor import BatchProcessor
    from tools.cli_settings import resolve_processing_settings, apply_config_overrides
    from advanced_config import get_advanced_config

    print_banner()
    print(f"\n📂 批量处理: {args.directory}")

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
    print(f"\n🔍 找到 {len(scan_results)} 个待处理目录:")
    total_photos = 0
    for i, scanned_dir in enumerate(scan_results, 1):
        rel = os.path.relpath(scanned_dir.path, args.directory)
        n = scanned_dir.photo_count
        processed = is_processed(scanned_dir.path)
        status = " (已处理)" if processed else ""
        print(f"  {i:3d}. {rel}/ ({n} 张){status}")
        total_photos += n
    print(f"\n  合计: {total_photos} 张照片")
    
    # Dry run
    if args.dry_run:
        print("\n📋 Dry run 模式，不执行处理")
        return 0
    
    # 确认
    if not args.yes:
        confirm = input(f"\n确定处理这 {len(scan_results)} 个目录? [y/N]: ")
        if confirm.lower() not in ['y', 'yes']:
            print("❌ 已取消")
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
    
    print("\n✅ 批量处理完成!")
    return 0 if result.failed_dirs == 0 else 1


def cmd_batch_reset(args):
    """批量重置所有已处理的子目录"""
    from core.recursive_scanner import is_processed
    from tools.merged_report_db import find_processed_subdirs
    import shutil
    
    print_banner()
    print(f"\n\U0001f504 batch reset: {args.directory}")
    
    # Find all processed directories (including root)
    processed_dirs = find_processed_subdirs(args.directory)
    
    if not processed_dirs:
        print("\n❌ 未找到已处理的子目录")
        return 1
    
    print(f"\n🔍 找到 {len(processed_dirs)} 个已处理目录:")
    for i, d in enumerate(processed_dirs, 1):
        rel = os.path.relpath(d, args.directory)
        print(f"  {i:3d}. {rel}/")
    
    if not args.yes:
        confirm = input(f"\n⚠️  确定重置这 {len(processed_dirs)} 个目录? [y/N]: ")
        if confirm.lower() not in ['y', 'yes']:
            print("❌ 已取消")
            return 1
    
    # 逐个重置（复用 cmd_reset 的核心逻辑）
    success_count = 0
    fail_count = 0
    for i, d in enumerate(processed_dirs, 1):
        rel = os.path.relpath(d, args.directory)
        print(f"\n{'━' * 40}")
        print(f"🔄 [{i}/{len(processed_dirs)}] 重置: {rel}/")
        
        # 创建一个模拟的 args 对象给 cmd_reset
        reset_args = SimpleNamespace(directory=d, yes=True)
        
        try:
            ret = cmd_reset(reset_args)
            if ret == 0:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"  ❌ 重置失败: {e}")
            fail_count += 1
    
    # 清理批量报告
    batch_report = os.path.join(args.directory, '.superpicky_batch.json')
    if os.path.exists(batch_report):
        os.remove(batch_report)
    
    print(f"\n{'═' * 40}")
    print(f"📊 批量重置完成: {success_count} 成功, {fail_count} 失败")
    return 0 if fail_count == 0 else 1


def _add_processing_args(parser):
    """
    为 process / batch 添加共享的处理参数。

    设计：可覆盖项一律 `default=None`（哨兵）。None = 用户未在命令行指定 →
    由 tools/cli_settings 回落到 advanced_config（与 GUI 同源），使「不带参数」时
    默认值与 GUI 完全一致；显式给出则覆盖。每个设置都有 flag → 完全可控（面向 agent）。
    布尔三态用 argparse.BooleanOptionalAction（--x / --no-x，未给为 None）。
    """
    parser.add_argument('directory', help='照片目录路径')
    # —— 评分阈值 / Scoring thresholds ——
    parser.add_argument('-s', '--sharpness', type=int, default=None,
                        help='锐度阈值 (默认: 跟随 skill 预设/配置)')
    parser.add_argument('-n', '--nima-threshold', type=float, default=None,
                        help='美学阈值 TOPIQ (默认: 跟随 skill 预设/配置)')
    parser.add_argument('-c', '--confidence', type=int, default=None,
                        help='AI置信度阈值 0-100 (默认: 配置 min_confidence×100)')
    parser.add_argument('--skill-level',
                        choices=['beginner', 'intermediate', 'master', 'custom'],
                        default=None,
                        help='摄影水平预设，驱动锐度/美学默认 (默认: 配置)')
    # —— 检测开关（三态）/ Detection toggles ——
    parser.add_argument('--flight', action=argparse.BooleanOptionalAction, default=None,
                        help='飞鸟检测 (默认: 跟随配置；GUI 默认关)')
    parser.add_argument('--burst', action=argparse.BooleanOptionalAction, default=None,
                        help='连拍检测 (默认: 跟随配置；GUI 默认关)')
    parser.add_argument('--exposure', action=argparse.BooleanOptionalAction, default=None,
                        help='曝光检测 (默认: 跟随配置；GUI 默认关)')
    parser.add_argument('--exposure-threshold', type=float, default=None,
                        help='曝光阈值 0.05-0.20 (默认: 配置)')
    # —— 0 星判定阈值（高级）/ Zero-star thresholds ——
    parser.add_argument('--min-sharpness', type=int, default=None,
                        help='0星锐度下限 (默认: 配置)')
    parser.add_argument('--min-nima', type=float, default=None,
                        help='0星美学下限 (默认: 配置)')
    parser.add_argument('--picked-top', type=int, default=None,
                        help='精选 Top 百分比 (默认: 配置)')
    # —— 元数据 / 目录布局 / Metadata & layout ——
    parser.add_argument('--xmp', action=argparse.BooleanOptionalAction, default=None,
                        help='[兼容] 写 XMP 侧车不改 RAW (= --arw-write-mode sidecar)')
    parser.add_argument('--arw-write-mode',
                        choices=['sidecar', 'embedded', 'inplace', 'auto'], default=None,
                        help='ARW 写入策略 (默认: 配置)')
    parser.add_argument('--metadata-mode',
                        choices=['embedded', 'sidecar', 'none'], default=None,
                        help='元数据写入模式 (默认: 配置)')
    parser.add_argument('--folder-layout',
                        choices=['species-first', 'rating-first'], default=None,
                        help='分目录布局 (默认: 配置)')
    parser.add_argument('--name-format',
                        choices=['default', 'avilist', 'clements', 'birdlife', 'scientific'],
                        default=None, help='鸟种英文名格式 (默认: 配置)')
    # —— BirdID 自动识鸟 / Auto bird ID ——
    parser.add_argument('--auto-identify', '-i', action='store_true', default=False,
                        help='自动识别 2★+ 照片鸟种并按鸟种分目录')
    parser.add_argument('--ebird', action=argparse.BooleanOptionalAction, default=None,
                        help='BirdID eBird 地区过滤 (默认: 开)')
    parser.add_argument('--birdid-country', type=str, default=None,
                        help='BirdID 国家代码 (如 AU, CN, US)')
    parser.add_argument('--birdid-region', type=str, default=None,
                        help='BirdID 区域代码 (如 AU-SA)')
    parser.add_argument('--birdid-threshold', type=float, default=None,
                        help='BirdID 置信度阈值 (默认: 配置)')
    # —— 临时文件 / 裁剪 / Temp & crop ——
    parser.add_argument('--keep-temp-files', action=argparse.BooleanOptionalAction,
                        dest='keep_temp', default=None,
                        help='保留临时预览图 (默认: 配置)')
    parser.add_argument('--save-crop', action='store_true', default=False,
                        help='保留 bird/debug 裁剪图 (.superpicky/cache/debug)')


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        prog='superpicky_cli',
        description=t("cli.sp_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s process ~/Photos/Birds              # 处理照片
  %(prog)s process ~/Photos/Birds -s 600       # 自定义锐度阈值
  %(prog)s reset ~/Photos/Birds -y             # 重置目录(无确认)
  %(prog)s restar ~/Photos/Birds -s 700 -n 5.5 # 重新评星
  %(prog)s info ~/Photos/Birds                 # 查看目录信息
  %(prog)s identify ~/Photos/bird.jpg          # 识别鸟类
  %(prog)s identify bird.NEF --write-exif      # 识别并写入EXIF
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # ===== process 命令 =====
    # 设计：可覆盖项一律 default=None（哨兵）。None = 用户未指定 → 由 tools/cli_settings
    # 回落到 advanced_config（与 GUI 同源，默认值一致）。每项都有 flag → 完全可控（面向 agent）。
    p_process = subparsers.add_parser('process', help=t("cli.cmd_process"))
    _add_processing_args(p_process)
    p_process.add_argument('--no-organize', action='store_false', dest='organize',
                          help='不移动文件到分类文件夹')
    p_process.add_argument('--no-cleanup', action='store_false', dest='cleanup',
                          help='不清理临时JPG文件')
    p_process.add_argument('-q', '--quiet', action='store_true', help='静默模式')
    p_process.add_argument('--cleanup-days', type=int, default=30,
                          help='自动清理周期（天），0=永久 (默认: 30)')
    p_process.set_defaults(organize=True, cleanup=True)

    # ===== reset 命令 =====
    p_reset = subparsers.add_parser('reset', help=t("cli.cmd_reset"))
    p_reset.add_argument('directory', help='照片目录路径')
    p_reset.add_argument('-y', '--yes', action='store_true',
                        help='跳过确认提示')
    
    # ===== restar 命令 =====
    p_restar = subparsers.add_parser('restar', help=t("cli.cmd_restar"))
    p_restar.add_argument('directory', help='照片目录路径')
    p_restar.add_argument('-s', '--sharpness', type=int, default=None,
                         help='新锐度阈值 (默认: 跟随 skill 预设/配置)')
    p_restar.add_argument('-n', '--nima-threshold', type=float, default=None,
                         help='TOPIQ 美学评分阈值 (默认: 跟随 skill 预设/配置)')
    p_restar.add_argument('-c', '--confidence', type=int, default=None,
                         help='AI置信度阈值 0-100 (默认: 配置 min_confidence×100)')
    p_restar.add_argument('--burst', action='store_true', default=True,
                         help='连拍检测 (默认: 开启)')
    p_restar.add_argument('--no-burst', action='store_false', dest='burst',
                         help='禁用连拍检测')
    # XMP 侧车写入
    p_restar.add_argument('--xmp', action='store_true', dest='xmp',
                         help='写入XMP侧车(不改RAW)')
    p_restar.add_argument('--no-xmp', action='store_false', dest='xmp',
                         help='直接写入RAW(默认)')
    p_restar.add_argument('--no-organize', action='store_false', dest='organize',
                         help='不重新分配文件目录')
    p_restar.add_argument('-y', '--yes', action='store_true',
                         help='跳过确认提示')
    p_restar.set_defaults(organize=True, burst=True, xmp=False)
    
    # ===== info 命令 =====
    p_info = subparsers.add_parser('info', help=t("cli.cmd_info"))
    p_info.add_argument('directory', help='照片目录路径')
    
    # ===== burst 命令 =====
    p_burst = subparsers.add_parser('burst', help=t("cli.cmd_burst"))
    p_burst.add_argument('directory', help='照片目录路径')
    p_burst.add_argument('-m', '--min-count', type=int, default=4,
                         help='最小连拍张数 (默认: 4)')
    p_burst.add_argument('-t', '--threshold', type=int, default=250,
                         help='时间阈值(ms) (默认: 250)')
    p_burst.add_argument('--no-phash', action='store_false', dest='phash',
                         help='禁用 pHash 验证（默认启用）')
    p_burst.add_argument('--execute', action='store_true',
                         help='实际执行处理（默认仅预览）')
    p_burst.set_defaults(phash=True)

    # ===== identify 命令 =====
    # 与 birdid_cli 共用同一套参数（model/tta/ebird/country/region…）与识别逻辑，避免分叉。
    import birdid_cli
    p_identify = subparsers.add_parser('identify', help=t("cli.cmd_identify"))
    birdid_cli.add_identify_arguments(p_identify, multi=False)
    
    # ===== batch 命令 =====
    # 共享处理参数（可覆盖项 default=None，与 process 一致），加 batch 专属选项。
    p_batch = subparsers.add_parser('batch', help='递归批量处理子目录')
    _add_processing_args(p_batch)
    p_batch.add_argument('--no-organize', action='store_false', dest='organize')
    p_batch.add_argument('--no-cleanup', action='store_false', dest='cleanup')
    p_batch.add_argument('--skip-existing', action='store_true',
                        help='跳过已处理的目录')
    p_batch.add_argument('--dry-run', action='store_true',
                        help='仅列出待处理目录，不执行')
    p_batch.add_argument('--max-depth', type=int, default=DEFAULT_SCAN_MAX_DEPTH,
                        help=f'最大递归深度 (默认: {DEFAULT_SCAN_MAX_DEPTH})')
    p_batch.add_argument('-y', '--yes', action='store_true',
                        help='跳过确认提示')
    p_batch.add_argument('-q', '--quiet', action='store_true')
    p_batch.set_defaults(organize=True, cleanup=True,
                        skip_existing=False, dry_run=False)
    
    # ===== batch-reset 命令 =====
    p_batch_reset = subparsers.add_parser('batch-reset', help='批量重置所有已处理的子目录')
    p_batch_reset.add_argument('directory', help='根目录路径')
    p_batch_reset.add_argument('-y', '--yes', action='store_true',
                              help='跳过确认提示')

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
    elif args.command == 'restar':
        return cmd_restar(args)
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
