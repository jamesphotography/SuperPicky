#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BirdID CLI - 独立鸟类识别命令行工具
支持完整的 eBird 区域过滤参数

Usage:
    python birdid_cli.py bird.jpg
    python birdid_cli.py bird.NEF --country AU --region AU-SA
    python birdid_cli.py bird.jpg --no-ebird
    python birdid_cli.py ~/Photos/*.jpg --batch --write-exif
"""

import argparse
import sys
import os
from pathlib import Path

# 确保模块路径正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.i18n import apply_saved_language, t


def _display_name(cn_name: str, en_name: str) -> str:
    """
    屏幕上显示的鸟名：英文界面只显示英文名，中文界面显示「中文名 (英文名)」。

    参数:
    cn_name (str): 中文名
    en_name (str): 英文名

    返回:
    str: 按界面语言组织的鸟名

    Name shown on screen: English only in an English UI, "中文 (English)" otherwise.
    """
    from tools.i18n import get_i18n
    if get_i18n().current_lang.startswith("en"):
        return en_name or cn_name
    return f"{cn_name} ({en_name})" if en_name else cn_name


def print_banner():
    """打印 CLI 横幅"""
    print("\n" + "=" * 60)
    print(t("cli.birdid_banner"))
    print("=" * 60)


def identify_single(args, image_path: str) -> dict:
    """识别单张图片"""
    model_type = getattr(args, 'model', 'birdid2024')

    if model_type == 'osea':
        return identify_single_osea(args, image_path)
    else:
        return identify_single_birdid2024(args, image_path)


def identify_single_birdid2024(args, image_path: str) -> dict:
    """使用 birdid2024 模型识别"""
    from birdid.bird_identifier import identify_bird

    result = identify_bird(
        image_path,
        use_yolo=args.yolo,
        use_gps=args.gps,
        use_geo_filter=args.ebird,
        country_code=args.country,
        region_code=args.region,
        top_k=args.top
    )

    return result


def identify_single_osea(args, image_path: str) -> dict:
    """使用 OSEA 模型识别"""
    from birdid.osea_classifier import get_osea_classifier
    from birdid.bird_identifier import load_image, get_yolo_detector, YOLO_AVAILABLE

    result = {
        'success': False,
        'image_path': image_path,
        'results': [],
        'yolo_info': None,
        'model': 'osea',
        'error': None
    }

    try:
        # 加载图像
        image = load_image(image_path)

        # RAW 自动对焦点：多目标时优先选「对焦的鸟」，与选鸟模式对齐
        # Auto-read RAW focus point so multi-subject selection matches picking mode
        from birdid.bird_identifier import _read_focus_point_for_path
        focus_point = _read_focus_point_for_path(image_path)

        # YOLO 裁剪 (可选)
        # V4.4: 记录是否真的裁剪成功，传给分类器选择对应的 transform
        # （已裁剪用直接 resize，未裁剪用 Resize+CenterCrop），否则已经是紧凑
        # 方形图的输入会被 CenterCrop 二次裁切，与 GUI 默认路径的结果不一致。
        # V4.4: Track whether the YOLO crop actually succeeded so we can tell
        # the classifier which transform to use (direct resize for an
        # already-cropped image vs. Resize+CenterCrop otherwise); without this
        # an already-tight square crop gets center-cropped a second time and
        # diverges from the GUI's default recognition path.
        is_yolo_cropped = False
        if args.yolo and YOLO_AVAILABLE:
            width, height = image.size
            if max(width, height) > 640:
                detector = get_yolo_detector()
                if detector:
                    cropped, info = detector.detect_and_crop_bird(
                        image, focus_point=focus_point
                    )
                    if cropped:
                        image = cropped
                        is_yolo_cropped = True
                        result['yolo_info'] = info
                    else:
                        # 严格模式：YOLO 未检测到鸟类，直接短路返回
                        result['success'] = True
                        result['results'] = []
                        result['yolo_info'] = {'bird_count': 0}
                        return result

        # 获取 OSEA 分类器
        classifier = get_osea_classifier()

        # 预测
        use_tta = getattr(args, 'tta', False)
        if use_tta:
            predictions = classifier.predict_with_tta(
                image, top_k=args.top, is_yolo_cropped=is_yolo_cropped
            )
        else:
            predictions = classifier.predict(
                image, top_k=args.top, is_yolo_cropped=is_yolo_cropped
            )

        result['success'] = True
        result['results'] = predictions

    except Exception as e:
        result['error'] = str(e)

    return result


def display_result(result: dict, verbose: bool = True):
    """显示识别结果"""
    if not result['success']:
        print(t("cli.identify_fail", error=result.get('error', 'Unknown')))
        return False
    
    if verbose:
        print(f"\n{'─' * 50}")

        # 显示使用的模型
        model_name = result.get('model', 'birdid2024')
        if model_name == 'osea':
            print(t("cli.bid_model_osea"))

        if result.get('yolo_info'):
            print(t("cli.yolo_info", info=result['yolo_info']))

        if result.get('gps_info'):
            gps = result['gps_info']
            print(t("cli.gps_info", info=gps['info']))

        if result.get('geo_info'):
            from birdid.geo_filter import describe_tier
            print(describe_tier(result['geo_info']))
    
    results = result.get('results', [])
    if not results:
        print(t("cli.no_bird"))
        print(t("cli.no_bird_hint"))
        return False
    
    print(t("cli.result_title", count=len(results)))
    for i, r in enumerate(results, 1):
        cn_name = r.get('cn_name', t("cli.bid_unknown"))
        en_name = r.get('en_name', t("cli.bid_unknown"))
        confidence = r.get('confidence', 0)
        ebird_match = "✓eBird" if r.get('ebird_match') else ""
        scientific_name = r.get('scientific_name', '')

        print(f"  {i}. {_display_name(cn_name, en_name)}")
        if scientific_name:
            print(t("cli.bid_scientific_name", scientific_name=scientific_name))
        print(t("cli.bid_confidence_line", confidence=confidence, ebird_match=ebird_match))

    return True


def write_exif(image_path: str, result: dict, threshold: float = 70.0) -> bool:
    """将识别结果写入 EXIF"""
    from tools.exiftool_manager import get_exiftool_manager
    
    results = result.get('results', [])
    if not results:
        return False
    
    best = results[0]
    confidence = best.get('confidence', 0)
    
    if confidence < threshold:
        print(t("cli.confidence_skip", confidence=confidence, threshold=threshold))
        return False
    
    bird_name = f"{best['cn_name']} ({best['en_name']})"
    
    exiftool_mgr = get_exiftool_manager()
    
    stats = exiftool_mgr.batch_set_metadata([{
        'file': image_path,
        'title': bird_name,
        'caption': bird_name,
    }])

    return stats.get('success', 0) > 0


def cmd_identify(args):
    """识别命令"""
    print_banner()
    
    images = args.images
    
    # 展开 glob 模式
    expanded_images = []
    for img in images:
        if '*' in img or '?' in img:
            from glob import glob
            expanded_images.extend(glob(img))
        else:
            expanded_images.append(img)
    
    images = [img for img in expanded_images if os.path.isfile(img)]
    
    if not images:
        print(t("cli.no_files"))
        return 1
    
    # 显示设置
    model_type = getattr(args, 'model', 'birdid2024')
    use_tta = getattr(args, 'tta', False)

    print(t("cli.bid_image_count", count=len(images)))
    print(t("cli.identify_model", model=model_type.upper()) + (" + TTA" if model_type == 'osea' and use_tta else ""))
    print(t("cli.bid_yolo_crop", value=t("cli.yes") if args.yolo else t("cli.no")))
    if model_type == 'birdid2024':
        print(t("cli.bid_gps_auto", value=t("cli.yes") if args.gps else t("cli.no")))
        print(t("cli.bid_ebird_filter", value=t("cli.yes") if args.ebird else t("cli.no")))
        if args.country:
            print(t("cli.birdid_country", country=args.country))
        if args.region:
            print(t("cli.birdid_region", region=args.region))
    print(t("cli.bid_top", top=args.top))
    if args.write_exif:
        print(t("cli.bid_write_exif_on", threshold=args.threshold))
    print()
    
    # 批量模式
    if len(images) > 1 or args.batch:
        return batch_identify(args, images)
    
    # 单张识别
    image_path = os.path.abspath(images[0])
    print(t("cli.bid_image", name=os.path.basename(image_path)))
    
    print(t("cli.identifying"))
    result = identify_single(args, image_path)
    
    success = display_result(result, verbose=True)
    
    # 写入 EXIF
    if args.write_exif and success:
        print(t("cli.writing_exif"))
        if write_exif(image_path, result, args.threshold):
            print(t("cli.written", name=result['results'][0]['cn_name']))
        else:
            print(t("cli.write_failed"))
    
    print()
    return 0 if success else 1


def batch_identify(args, images: list):
    """批量识别"""
    print(f"{'═' * 60}")
    print(t("cli.bid_batch_mode", count=len(images)))
    print(f"{'═' * 60}\n")
    
    stats = {
        'total': len(images),
        'success': 0,
        'failed': 0,
        'written': 0,
        'species': {}
    }
    
    for i, image_path in enumerate(images, 1):
        image_path = os.path.abspath(image_path)
        filename = os.path.basename(image_path)
        
        print(f"[{i}/{stats['total']}] {filename}")
        
        try:
            result = identify_single(args, image_path)
            
            if result['success'] and result.get('results'):
                stats['success'] += 1
                
                # 显示 Top 1 结果
                best = result['results'][0]
                cn_name = _display_name(best.get('cn_name', t("cli.bid_unknown")),
                                        best.get('en_name', ''))
                confidence = best.get('confidence', 0)
                print(f"  → {cn_name} ({confidence:.1f}%)")
                
                # 统计物种
                if cn_name not in stats['species']:
                    stats['species'][cn_name] = 0
                stats['species'][cn_name] += 1
                
                # 写入 EXIF
                if args.write_exif:
                    if write_exif(image_path, result, args.threshold):
                        stats['written'] += 1
                        print(t("cli.bid_exif_written"))
            else:
                stats['failed'] += 1
                error = result.get('error', t("cli.bid_not_identified"))
                print(f"  ⚠️  {error}")
                
        except Exception as e:
            stats['failed'] += 1
            print(t("cli.bid_error", e=e))
    
    # 打印统计
    print(f"\n{'═' * 60}")
    print(t("cli.bid_batch_done"))
    print(f"{'═' * 60}")
    print(t("cli.bid_stats_title"))
    print(t("cli.bid_success_count", success=stats['success'], total=stats['total']))
    print(t("cli.bid_failed_count", failed=stats['failed'], total=stats['total']))
    if args.write_exif:
        print(t("cli.bid_written_count", written=stats['written']))
    
    if stats['species']:
        print(t("cli.bid_species_found", count=len(stats['species'])))
        sorted_species = sorted(stats['species'].items(), key=lambda x: -x[1])
        for species, count in sorted_species[:10]:
            print(t("cli.bid_species_line", species=species, count=count))
        if len(sorted_species) > 10:
            print(t("cli.bid_more_species", count=len(sorted_species) - 10))
    
    print()
    return 0 if stats['failed'] < stats['total'] else 1


def cmd_organize(args):
    """批量识别并按鸟种分目录"""
    import shutil
    import json
    from birdid.bird_identifier import identify_bird
    from tools.exiftool_manager import get_exiftool_manager
    
    print_banner()
    
    directory = os.path.abspath(args.directory)
    if not os.path.isdir(directory):
        print(t("cli.dir_not_found", path=directory))
        return 1
    
    print(t("cli.bid_dir", directory=directory))
    print(t("cli.bid_threshold", threshold=args.threshold))
    print(t("cli.bid_ebird_filter", value=t("cli.yes") if args.ebird else t("cli.no")))
    if args.country:
        print(t("cli.birdid_country", country=args.country))
    if args.region:
        print(t("cli.birdid_region", region=args.region))
    print(t("cli.bid_write_exif", value=t("cli.yes") if args.write_exif else t("cli.no")))
    
    # 扫描图片文件
    extensions = {'.jpg', '.jpeg', '.png', '.nef', '.arw', '.cr2', '.cr3', '.rw2', '.orf', '.dng', '.raf'}
    images = []
    for filename in os.listdir(directory):
        if filename.startswith('.'):
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext in extensions:
            images.append(os.path.join(directory, filename))
    
    if not images:
        print(t("cli.bid_no_images"))
        return 1
    
    print(t("cli.bid_images_found", count=len(images)))
    
    if not args.yes:
        confirm = input(t("cli.bid_organize_confirm"))
        if confirm.lower() not in ['y', 'yes']:
            print(t("cli.cancelled"))
            return 1
    
    print(f"\n{'═' * 60}")
    print(t("cli.bid_organize_start"))
    print(f"{'═' * 60}\n")
    
    # 用于记录移动操作的 manifest
    manifest_path = os.path.join(directory, '.birdid_manifest.json')
    manifest = {
        'created': str(os.path.getmtime(directory)),
        'moves': []  # [{original: ..., moved_to: ..., species: ...}]
    }
    
    stats = {
        'total': len(images),
        'identified': 0,
        'moved': 0,
        'skipped': 0,
        'failed': 0,
        'species': {}
    }
    
    exiftool_mgr = get_exiftool_manager() if args.write_exif else None
    
    for i, image_path in enumerate(images, 1):
        filename = os.path.basename(image_path)
        print(f"[{i}/{stats['total']}] {filename}")
        
        try:
            result = identify_bird(
                image_path,
                use_yolo=True,
                use_gps=True,
                use_geo_filter=args.ebird,
                country_code=args.country,
                region_code=args.region,
                top_k=1
            )
            
            if result['success'] and result.get('results'):
                best = result['results'][0]
                cn_name = best.get('cn_name', t("cli.bid_unknown"))
                en_name = best.get('en_name', t("cli.bid_unknown"))
                confidence = best.get('confidence', 0)
                
                print(f"  → {_display_name(cn_name, en_name)} ({confidence:.1f}%)")
                
                # 检查置信度
                if confidence < args.threshold:
                    print(t("cli.bid_low_conf_skip"))
                    stats['skipped'] += 1
                    continue
                
                stats['identified'] += 1
                
                # 创建鸟种目录名 (中文名_英文名)
                safe_cn = cn_name.replace('/', '-').replace('\\', '-')
                safe_en = en_name.replace('/', '-').replace('\\', '-')
                species_folder = f"{safe_cn}_{safe_en}"
                species_dir = os.path.join(directory, species_folder)
                
                # 创建目录
                if not os.path.exists(species_dir):
                    os.makedirs(species_dir)
                
                # 移动文件
                new_path = os.path.join(species_dir, filename)
                if not os.path.exists(new_path):
                    shutil.move(image_path, new_path)
                    stats['moved'] += 1
                    print(t("cli.bid_moved_to", species_folder=species_folder))
                    
                    # 记录到 manifest
                    manifest['moves'].append({
                        'original': image_path,
                        'moved_to': new_path,
                        'species_cn': cn_name,
                        'species_en': en_name,
                        'confidence': confidence
                    })
                    
                    # 统计物种
                    if cn_name not in stats['species']:
                        stats['species'][cn_name] = 0
                    stats['species'][cn_name] += 1
                    
                    # 写入 EXIF
                    if args.write_exif and exiftool_mgr:
                        bird_name = f"{cn_name} ({en_name})"
                        metadata = {
                            'Title': bird_name,
                            'Caption-Abstract': bird_name,
                        }
                        exiftool_mgr.set_metadata(new_path, metadata)
                else:
                    print(t("cli.bid_target_exists"))
                    stats['skipped'] += 1
            else:
                stats['failed'] += 1
                print(t("cli.bid_unidentified"))
                
        except Exception as e:
            stats['failed'] += 1
            print(t("cli.bid_error", e=e))
    
    # 保存 manifest
    if manifest['moves']:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(t("cli.bid_manifest_saved"))
    
    # 打印统计
    print(f"\n{'═' * 60}")
    print(t("cli.bid_organize_done"))
    print(f"{'═' * 60}")
    print(t("cli.bid_stats_title"))
    print(t("cli.bid_total_files", total=stats['total']))
    print(t("cli.bid_identified", identified=stats['identified']))
    print(t("cli.bid_moved", moved=stats['moved']))
    print(t("cli.bid_skipped", skipped=stats['skipped']))
    print(t("cli.bid_failed", failed=stats['failed']))
    
    if stats['species']:
        print(t("cli.bid_species_folders", count=len(stats['species'])))
        sorted_species = sorted(stats['species'].items(), key=lambda x: -x[1])
        for species, count in sorted_species[:15]:
            print(t("cli.bid_species_folder_line", species=species, count=count))
        if len(sorted_species) > 15:
            print(t("cli.bid_more_folders", count=len(sorted_species) - 15))
    
    print(t("cli.bid_reset_hint", directory=directory))
    print()
    return 0


def cmd_reset(args):
    """重置目录 - 恢复原始结构"""
    import shutil
    import json
    
    print_banner()
    
    directory = os.path.abspath(args.directory)
    manifest_path = os.path.join(directory, '.birdid_manifest.json')
    
    print(t("cli.bid_reset_dir", directory=directory))
    
    # 检查 manifest
    if not os.path.exists(manifest_path):
        print(t("cli.bid_no_manifest"))
        print(t("cli.bid_no_manifest_hint"))
        return 1
    
    # 加载 manifest
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        print(t("cli.bid_manifest_read_failed", e=e))
        return 1
    
    moves = manifest.get('moves', [])
    if not moves:
        print(t("cli.bid_manifest_empty"))
        return 0
    
    print(t("cli.bid_moves_found", count=len(moves)))
    
    if not args.yes:
        confirm = input(t("cli.bid_reset_confirm"))
        if confirm.lower() not in ['y', 'yes']:
            print(t("cli.cancelled"))
            return 1
    
    stats = {'restored': 0, 'skipped': 0, 'failed': 0}
    empty_dirs = set()
    
    for move in moves:
        original = move.get('original')
        moved_to = move.get('moved_to')
        
        if not original or not moved_to:
            continue
        
        if os.path.exists(moved_to):
            try:
                # 确保原始目录存在
                original_dir = os.path.dirname(original)
                if not os.path.exists(original_dir):
                    os.makedirs(original_dir)
                
                # 移动回原位置
                if not os.path.exists(original):
                    shutil.move(moved_to, original)
                    stats['restored'] += 1
                    print(t("cli.bid_restored", name=os.path.basename(original)))
                    
                    # 记录可能为空的目录
                    empty_dirs.add(os.path.dirname(moved_to))
                else:
                    stats['skipped'] += 1
                    print(t("cli.bid_restore_skipped", name=os.path.basename(original)))
            except Exception as e:
                stats['failed'] += 1
                print(t("cli.bid_restore_failed", name=os.path.basename(original), e=e))
        else:
            stats['skipped'] += 1
    
    # 清理空目录
    removed_dirs = 0
    for dir_path in empty_dirs:
        if os.path.exists(dir_path) and os.path.isdir(dir_path):
            try:
                contents = os.listdir(dir_path)
                if len(contents) == 0:
                    os.rmdir(dir_path)
                    removed_dirs += 1
            except:
                pass
    
    # 删除 manifest
    if stats['restored'] > 0:
        try:
            os.remove(manifest_path)
            print(t("cli.bid_manifest_deleted"))
        except:
            pass
    
    # 打印统计
    print(f"\n{'═' * 60}")
    print(t("cli.bid_reset_done"))
    print(f"{'═' * 60}")
    print(t("cli.bid_stats_title"))
    print(t("cli.bid_restored_count", restored=stats['restored']))
    print(t("cli.bid_skipped", skipped=stats['skipped']))
    print(t("cli.bid_failed", failed=stats['failed']))
    if removed_dirs > 0:
        print(t("cli.bid_empty_dirs_removed", removed_dirs=removed_dirs))
    
    print()
    return 0


def cmd_list_countries(args):
    """列出支持的国家代码"""
    print_banner()
    print(t("cli.bid_countries_title"))
    
    countries = [
        ("AU", "澳大利亚", "Australia"),
        ("CN", "中国", "China"),
        ("US", "美国", "United States"),
        ("GB", "英国", "United Kingdom"),
        ("JP", "日本", "Japan"),
        ("DE", "德国", "Germany"),
        ("FR", "法国", "France"),
        ("CA", "加拿大", "Canada"),
        ("NZ", "新西兰", "New Zealand"),
        ("IN", "印度", "India"),
        ("BR", "巴西", "Brazil"),
        ("ZA", "南非", "South Africa"),
        ("KR", "韩国", "South Korea"),
        ("TW", "台湾", "Taiwan"),
        ("HK", "香港", "Hong Kong"),
        ("SG", "新加坡", "Singapore"),
        ("MY", "马来西亚", "Malaysia"),
        ("TH", "泰国", "Thailand"),
        ("ID", "印度尼西亚", "Indonesia"),
        ("PH", "菲律宾", "Philippines"),
    ]
    
    for code, cn, en in countries:
        print(f"  {code:4} {_display_name(cn, en)}")
    
    print(t("cli.bid_countries_hint"))
    print()
    return 0


def add_identify_arguments(parser, multi: bool = True):
    """
    为「识别」命令添加共享参数。供本 CLI 与 superpicky_cli 的 identify 子命令复用，
    确保两边参数与识别逻辑单一来源、不再分叉。

    参数:
        parser: argparse 解析器
        multi (bool): True → 位置参数 images(nargs='+') + --batch（本 CLI）；
                      False → 单张 image 位置参数（superpicky_cli）。

    Shared argument set for the identify command, reused by both birdid_cli and
    superpicky_cli so the two identify entry points stay in lockstep.
    """
    if multi:
        parser.add_argument('images', nargs='+', help=t("cli.bid_help_images"))
    else:
        parser.add_argument('image', help=t("cli.bid_help_image"))
    parser.add_argument('-t', '--top', type=int, default=5,
                        help=t("cli.bid_help_top"))
    # 模型选项
    parser.add_argument('--model', '-m', type=str, default='birdid2024',
                        choices=['birdid2024', 'osea'],
                        help=t("cli.bid_help_model"))
    parser.add_argument('--tta', action='store_true',
                        help=t("cli.bid_help_tta"))
    # YOLO / GPS / eBird 选项
    parser.add_argument('--no-yolo', action='store_false', dest='yolo',
                        help=t("cli.bid_help_no_yolo"))
    parser.add_argument('--no-gps', action='store_false', dest='gps',
                        help=t("cli.bid_help_no_gps"))
    parser.add_argument('--no-ebird', action='store_false', dest='ebird',
                        help=t("cli.bid_help_no_ebird"))
    parser.add_argument('--country', '-c', type=str, default=None,
                        help=t("cli.bid_help_country"))
    parser.add_argument('--region', '-r', type=str, default=None,
                        help=t("cli.bid_help_region"))
    # 写入选项
    parser.add_argument('--write-exif', '-w', action='store_true',
                        help=t("cli.bid_help_write_exif"))
    parser.add_argument('--threshold', type=float, default=70.0,
                        help=t("cli.bid_help_exif_threshold"))
    if multi:
        parser.add_argument('--batch', '-b', action='store_true',
                            help=t("cli.bid_help_batch"))
    parser.set_defaults(yolo=True, gps=True, ebird=True)


def main():
    """主入口"""
    # 跟随「设置」里的界面语言 / follow the language chosen in Settings
    apply_saved_language()
    parser = argparse.ArgumentParser(
        prog='birdid_cli',
        description=t("cli.bid_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("cli.bid_examples")
    )
    
    subparsers = parser.add_subparsers(dest='command', help=t("cli.help_commands"))
    
    # ===== 识别命令 (默认) =====
    p_identify = subparsers.add_parser('identify', help=t("cli.bid_help_identify"))
    add_identify_arguments(p_identify, multi=True)

    # ===== 按鸟种分目录命令 =====
    p_organize = subparsers.add_parser('organize', help=t("cli.bid_help_organize"))
    p_organize.add_argument('directory', help=t("cli.help_directory"))
    p_organize.add_argument('--threshold', type=float, default=70.0,
                           help=t("cli.bid_help_organize_threshold"))
    p_organize.add_argument('--no-ebird', action='store_false', dest='ebird',
                           help=t("cli.bid_help_no_ebird"))
    p_organize.add_argument('--country', '-c', type=str, default=None,
                           help=t("cli.bid_help_country"))
    p_organize.add_argument('--region', '-r', type=str, default=None,
                           help=t("cli.bid_help_region"))
    p_organize.add_argument('--write-exif', '-w', action='store_true',
                           help=t("cli.bid_help_also_write_exif"))
    p_organize.add_argument('-y', '--yes', action='store_true',
                           help=t("cli.help_yes"))
    p_organize.set_defaults(ebird=True)
    
    # ===== 重置目录命令 =====
    p_reset = subparsers.add_parser('reset', help=t("cli.bid_help_reset"))
    p_reset.add_argument('directory', help=t("cli.help_directory"))
    p_reset.add_argument('-y', '--yes', action='store_true',
                        help=t("cli.help_yes"))
    
    # ===== 列出国家命令 =====
    p_list = subparsers.add_parser('list-countries', help=t("cli.bid_help_list_countries"))
    
    # 解析参数
    args = parser.parse_args()
    
    # 如果没有指定命令但有位置参数，默认为 identify
    if args.command is None:
        if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
            # 检查第一个参数是否像文件路径
            first_arg = sys.argv[1]
            if os.path.exists(first_arg) or '*' in first_arg or '?' in first_arg or first_arg.endswith(('.jpg', '.jpeg', '.png', '.nef', '.arw', '.cr2', '.cr3')):
                # 重新解析为 identify 命令
                sys.argv.insert(1, 'identify')
                args = parser.parse_args()
            else:
                parser.print_help()
                return 1
        else:
            parser.print_help()
            return 1
    
    # 执行命令
    if args.command == 'identify':
        return cmd_identify(args)
    elif args.command == 'organize':
        return cmd_organize(args)
    elif args.command == 'reset':
        return cmd_reset(args)
    elif args.command == 'list-countries':
        return cmd_list_countries(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())

