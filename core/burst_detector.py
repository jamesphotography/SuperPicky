"""
连拍检测器模块 - V4.0

功能：
1. 读取毫秒级时间戳 (SubSecTimeOriginal)
2. 检测连拍组 (时间差 < 150ms)
3. 组内最佳选择
4. 分组处理 (子目录 + 标签)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple, Dict
import os
import subprocess
import json
import sys  # V3.9: 用于检测 PyInstaller 打包环境


@dataclass
class PhotoTimestamp:
    """照片时间戳信息"""
    filepath: str
    datetime_original: Optional[datetime] = None
    subsec: Optional[str] = None  # 毫秒部分，如 "045"
    precise_time: Optional[float] = None  # 精确时间戳（秒）
    rating: int = 0
    sharpness: float = 0.0
    topiq: float = 0.0
    
    @property
    def has_subsec(self) -> bool:
        """是否有毫秒信息"""
        return self.subsec is not None and self.subsec != ""


@dataclass
class BurstGroup:
    """连拍组"""
    group_id: int
    photos: List[PhotoTimestamp] = field(default_factory=list)
    best_index: int = 0  # 最佳照片在 photos 列表中的索引
    
    @property
    def count(self) -> int:
        return len(self.photos)
    
    @property
    def best_photo(self) -> Optional[PhotoTimestamp]:
        if self.photos and 0 <= self.best_index < len(self.photos):
            return self.photos[self.best_index]
        return None


class BurstDetector:
    """连拍检测器"""
    
    # 默认检测参数（可被 advanced_config 覆盖）
    TIME_THRESHOLD_MS = 250  # V3.9: 默认 250ms
    MIN_BURST_COUNT = 4      # V3.9: 默认 4 张
    MIN_RATING = 2           # 只处理 >= 2 星的照片
    
    # pHash 参数
    PHASH_THRESHOLD = 12     # 汉明距离阈值（<=12 视为相似）
    USE_PHASH = True         # 是否启用 pHash 验证
    
    def __init__(self, exiftool_path: str = None, use_phash: bool = True):
        """
        初始化连拍检测器
        
        Args:
            exiftool_path: ExifTool 路径
            use_phash: 是否启用 pHash 验证
        """
        self.exiftool_path = exiftool_path or self._find_exiftool()
        self.USE_PHASH = use_phash
        
        # V3.9: 从配置加载参数
        try:
            from advanced_config import get_advanced_config
            config = get_advanced_config()
            self.TIME_THRESHOLD_MS = config.burst_time_threshold
            self.MIN_BURST_COUNT = config.burst_min_count
        except Exception:
            pass  # 使用默认值
    
    def _find_exiftool(self) -> str:
        """查找 ExifTool 路径"""
        # V3.9.4: 处理 Windows 平台的可执行文件后缀
        is_windows = sys.platform.startswith('win')
        exe_name = 'exiftool.exe' if is_windows else 'exiftool'

        # V3.9: 优先检查 PyInstaller 打包环境
        if hasattr(sys, '_MEIPASS'):
            # V4.0.2: 使用平台特定目录
            if is_windows:
                bundled = os.path.join(sys._MEIPASS, 'exiftools_win', exe_name)
            else:
                bundled = os.path.join(sys._MEIPASS, 'exiftools_mac', exe_name)
            if os.path.exists(bundled):
                return bundled
            # 备选
            fallback = os.path.join(sys._MEIPASS, 'exiftools_mac', 'exiftool')
            if os.path.exists(fallback):
                return fallback
        
        # 开发环境: 优先使用项目内置的 exiftool（优先使用 main.py 注入的真实 app 根目录）
        project_root = getattr(sys, '_SUPERPICKY_APP_ROOT',
                               os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Windows 平台优先检查 exiftools_win 目录
        if is_windows:
            # 1. 检查 exiftools_win 目录
            exiftools_win = os.path.join(project_root, 'exiftools_win', 'exiftool.exe')
            if os.path.exists(exiftools_win):
                return exiftools_win
            
            # 2. 检查项目根目录的 exiftool.exe
            builtin_win = os.path.join(project_root, 'exiftool.exe')
            if os.path.exists(builtin_win):
                return builtin_win

        # 非 Windows 平台或 Windows 平台的其他检查
        # V4.0.2: 优先检查 exiftools_mac 目录
        exiftools_mac = os.path.join(project_root, 'exiftools_mac', 'exiftool')
        if os.path.exists(exiftools_mac):
            return exiftools_mac
        
        # 后备：项目根目录
        builtin = os.path.join(project_root, 'exiftool')
        if os.path.exists(builtin):
            return builtin
        
        # 否则使用系统 exiftool
        import shutil
        system_exiftool = shutil.which('exiftool')
        if system_exiftool:
            return system_exiftool
            
        return exe_name if is_windows else 'exiftool'
    
    def read_timestamps(self, filepaths: List[str]) -> List[PhotoTimestamp]:
        """
        批量读取照片的精确时间戳
        
        Args:
            filepaths: 文件路径列表
            
        Returns:
            PhotoTimestamp 列表
        """
        if not filepaths:
            return []
        
        # V3.9.4: 预处理路径，确保全部是规范的绝对路径
        filepaths = [os.path.abspath(p) for p in filepaths]
        
        # 使用 exiftool 批量读取，使用 -@ - 避免命令行长度限制
        cmd = [
            self.exiftool_path,
            '-charset', 'utf8',
            '-json',
            '-DateTimeOriginal',
            '-SubSecTimeOriginal',
            '-Rating',
            '-@', '-'
        ]
        
        try:
            # 将路径列表转换为换行符分隔的字符串
            paths_input = "\n".join(filepaths)
            
            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                cmd,
                input=paths_input.encode('utf-8'),  # 转换为字节
                capture_output=True,
                text=False,  # 使用 bytes 模式，避免自动解码
                timeout=max(60, len(filepaths) // 10),  # 根据文件数量动态调整超时
                creationflags=creationflags
            )
            
            stdout_bytes = result.stdout or b""
            if not stdout_bytes.strip():
                if result.stderr:
                    stderr_bytes = result.stderr
                    decoded_stderr = None
                    for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                        try:
                            decoded_stderr = stderr_bytes.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
                    if decoded_stderr is None and stderr_bytes:
                        decoded_stderr = stderr_bytes.decode('latin-1')
                    print(f"⚠️ ExifTool 输出为空: {decoded_stderr}")
                return []
            
            # 解码输出
            decoded_output = None
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    decoded_output = stdout_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if decoded_output is None:
                decoded_output = stdout_bytes.decode('latin-1')
            
            exif_data = json.loads(decoded_output)
            return self._parse_exif_timestamps(exif_data)
            
        except subprocess.TimeoutExpired:
            print("⚠️ ExifTool 读取超时")
            return []
        except json.JSONDecodeError as e:
            print(f"⚠️ 解析 EXIF JSON 失败: {e}")
            return []
    
    def _parse_exif_timestamps(self, exif_data: List[dict]) -> List[PhotoTimestamp]:
        """
        解析 EXIF 数据为 PhotoTimestamp 列表
        
        Args:
            exif_data: ExifTool JSON 输出
            
        Returns:
            PhotoTimestamp 列表
        """
        results = []
        
        for item in exif_data:
            # V3.9.4: 统一规范化路径
            filepath = os.path.normpath(item.get('SourceFile', ''))
            dt_str = item.get('DateTimeOriginal', '')
            subsec = item.get('SubSecTimeOriginal', '')
            rating = item.get('Rating', 0) or 0
            
            # 解析日期时间
            dt = None
            if dt_str:
                try:
                    # 格式: "2024:01:09 10:05:30"
                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    pass
            
            # 计算精确时间戳
            precise_time = None
            if dt:
                precise_time = dt.timestamp()
                if subsec:
                    # SubSecTimeOriginal 通常是毫秒部分，如 "045"
                    try:
                        subsec_float = float(f"0.{subsec}")
                        precise_time += subsec_float
                    except ValueError:
                        pass
            
            photo = PhotoTimestamp(
                filepath=filepath,
                datetime_original=dt,
                subsec=str(subsec) if subsec else None,
                precise_time=precise_time,
                rating=rating
            )
            results.append(photo)
        
        return results
    
    def detect_groups(self, photos: List[PhotoTimestamp]) -> List[BurstGroup]:
        """
        检测连拍组（保留星级过滤，兼容旧逻辑）
        
        Args:
            photos: PhotoTimestamp 列表
            
        Returns:
            BurstGroup 列表
        """
        # 1. 只处理 >= 2 星的照片
        candidates = [p for p in photos if p.rating >= self.MIN_RATING and p.precise_time is not None]
        return self._detect_groups_internal(candidates)
    
    def detect_groups_by_time_only(self, photos: List[PhotoTimestamp]) -> List[BurstGroup]:
        """
        V4.0.4: 纯时间戳连拍检测（不过滤星级）
        用于早期扫描阶段，在评分之前检测连拍组
        
        Args:
            photos: PhotoTimestamp 列表
            
        Returns:
            BurstGroup 列表
        """
        # 只过滤有效时间戳，不看星级
        candidates = [p for p in photos if p.precise_time is not None]
        return self._detect_groups_internal(candidates)
    
    def _detect_groups_internal(self, candidates: List[PhotoTimestamp]) -> List[BurstGroup]:
        """
        内部分组检测逻辑
        
        Args:
            candidates: 已过滤的候选照片列表
            
        Returns:
            BurstGroup 列表
        """
        if len(candidates) < self.MIN_BURST_COUNT:
            return []
        
        # 按精确时间排序
        candidates.sort(key=lambda p: p.precise_time)
        
        # 分组检测（基于时间戳）
        groups = []
        current_group = [candidates[0]]
        
        for i in range(1, len(candidates)):
            prev = candidates[i - 1]
            curr = candidates[i]
            
            # 计算时间差（毫秒）
            time_diff_ms = (curr.precise_time - prev.precise_time) * 1000
            
            if time_diff_ms <= self.TIME_THRESHOLD_MS:
                # 属于同一组
                current_group.append(curr)
            else:
                # 保存当前组（如果满足最小张数）
                if len(current_group) >= self.MIN_BURST_COUNT:
                    group = BurstGroup(
                        group_id=len(groups) + 1,
                        photos=current_group.copy()
                    )
                    groups.append(group)
                
                # 开始新组
                current_group = [curr]
        
        # 处理最后一组
        if len(current_group) >= self.MIN_BURST_COUNT:
            group = BurstGroup(
                group_id=len(groups) + 1,
                photos=current_group.copy()
            )
            groups.append(group)
        
        # V4.0: pHash 验证（过滤误判）
        if self.USE_PHASH and groups:
            groups = self.verify_groups_with_phash(groups)
        
        return groups
    
    def verify_groups_with_phash(self, groups: List[BurstGroup]) -> List[BurstGroup]:
        """
        使用 pHash 验证连拍组，过滤掉内容差异大的照片
        
        Args:
            groups: 初步检测的连拍组
            
        Returns:
            验证后的连拍组
        """
        try:
            from imagehash import phash
            from PIL import Image
        except ImportError:
            print("⚠️ imagehash 未安装，跳过 pHash 验证")
            return groups
        
        verified_groups = []
        
        for group in groups:
            if group.count < 2:
                verified_groups.append(group)
                continue
            
            # 计算组内所有照片的 pHash
            hashes = []
            for photo in group.photos:
                try:
                    # 使用预览图（如果存在）或原图
                    img_path = photo.filepath
                    # 尝试找 JPEG 预览（更快）
                    jpg_path = os.path.splitext(photo.filepath)[0] + '.jpg'
                    if os.path.exists(jpg_path):
                        img_path = jpg_path
                    
                    img = Image.open(img_path)
                    h = phash(img)
                    hashes.append((photo, h))
                except Exception as e:
                    # 无法计算 pHash，保留该照片
                    hashes.append((photo, None))
            
            # 验证相邻照片的相似度
            verified_photos = [hashes[0][0]]  # 保留第一张
            
            for i in range(1, len(hashes)):
                curr_photo, curr_hash = hashes[i]
                prev_photo, prev_hash = hashes[i - 1]
                
                if curr_hash is None or prev_hash is None:
                    # 无法比较，保留
                    verified_photos.append(curr_photo)
                else:
                    distance = curr_hash - prev_hash
                    if distance <= self.PHASH_THRESHOLD:
                        # 相似，保留在组内
                        verified_photos.append(curr_photo)
                    else:
                        # 不相似，可能是飞鸟或重构图
                        # 开始新组（如果剩余足够）
                        if len(verified_photos) >= self.MIN_BURST_COUNT:
                            verified_groups.append(BurstGroup(
                                group_id=len(verified_groups) + 1,
                                photos=verified_photos.copy()
                            ))
                        verified_photos = [curr_photo]
            
            # 保存最后的验证组
            if len(verified_photos) >= self.MIN_BURST_COUNT:
                verified_groups.append(BurstGroup(
                    group_id=len(verified_groups) + 1,
                    photos=verified_photos
                ))
        
        return verified_groups
    
    def select_best_in_groups(self, groups: List[BurstGroup]) -> List[BurstGroup]:
        """
        在每个连拍组中选择最佳照片
        
        Args:
            groups: BurstGroup 列表
            
        Returns:
            更新后的 BurstGroup 列表
        """
        for group in groups:
            if not group.photos:
                continue
            
            # 按综合分数排序：锐度 * 0.5 + 美学 * 0.5
            best_score = -1
            best_idx = 0
            
            for i, photo in enumerate(group.photos):
                score = photo.sharpness * 0.5 + photo.topiq * 0.5
                if score > best_score:
                    best_score = score
                    best_idx = i
            
            group.best_index = best_idx
        
        return groups
    
    def enrich_from_csv(self, photos: List[PhotoTimestamp], csv_path: str) -> List[PhotoTimestamp]:
        """
        [DEPRECATED] 从 CSV 报告中读取锐度和美学分数
        请使用 enrich_from_db 代替
        """
        import csv
        
        if not os.path.exists(csv_path):
            print(f"⚠️ CSV 报告不存在: {csv_path}")
            return photos
        
        csv_data = {}
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row.get('filename', '')
                    try:
                        sharpness = float(row.get('head_sharp', 0) or 0)
                    except (ValueError, TypeError):
                        sharpness = 0.0
                    try:
                        topiq = float(row.get('nima_score', 0) or 0)
                    except (ValueError, TypeError):
                        topiq = 0.0
                    csv_data[filename] = {'sharpness': sharpness, 'topiq': topiq}
        except Exception as e:
            print(f"⚠️ 读取 CSV 失败: {e}")
            return photos
        
        for photo in photos:
            basename = os.path.splitext(os.path.basename(photo.filepath))[0]
            if basename in csv_data:
                photo.sharpness = csv_data[basename]['sharpness']
                photo.topiq = csv_data[basename]['topiq']
        
        return photos
    
    def enrich_from_db(self, photos: List[PhotoTimestamp], directory: str) -> List[PhotoTimestamp]:
        """
        从 SQLite 报告数据库中读取锐度和美学分数
        
        Args:
            photos: PhotoTimestamp 列表
            directory: 工作目录路径（report.db 所在目录）
            
        Returns:
            更新后的 PhotoTimestamp 列表
        """
        from tools.report_db import ReportDB
        
        db_path = os.path.join(directory, '.superpicky', 'report.db')
        if not os.path.exists(db_path):
            print(f"⚠️ 报告数据库不存在: {db_path}")
            return photos
        
        try:
            db = ReportDB(directory)
            all_data = db.get_all_photos()
            db.close()
            
            # 构建查找字典
            db_data = {}
            for row in all_data:
                filename = row.get('filename', '')
                sharpness = float(row.get('head_sharp') or 0)
                topiq = float(row.get('nima_score') or 0)
                db_data[filename] = {'sharpness': sharpness, 'topiq': topiq}
            
            for photo in photos:
                basename = os.path.splitext(os.path.basename(photo.filepath))[0]
                if basename in db_data:
                    photo.sharpness = db_data[basename]['sharpness']
                    photo.topiq = db_data[basename]['topiq']
        except Exception as e:
            print(f"⚠️ 读取报告数据库失败: {e}")
        
        return photos
    
    def process_burst_groups(
        self,
        groups: List[BurstGroup],
        output_dir: str,
        exiftool_mgr=None,
        log_callback=None
    ) -> Dict[str, int]:
        """
        处理连拍组：创建子目录、移动文件、设置标签
        
        Args:
            groups: BurstGroup 列表
            output_dir: 输出目录（如 "3星_优选"）
            exiftool_mgr: ExifToolManager 实例（可选）
            
        Returns:
            统计结果 {'groups_processed': n, 'photos_moved': n, 'best_marked': n}
        """
        import shutil
        
        stats = {'groups_processed': 0, 'photos_moved': 0, 'best_marked': 0}
        
        for group in groups:
            if not group.photos or group.count < self.MIN_BURST_COUNT:
                continue
            
            # 创建子目录
            burst_dir = os.path.join(output_dir, f"burst_{group.group_id:03d}")
            os.makedirs(burst_dir, exist_ok=True)
            
            best_photo = group.best_photo
            
            # V4.1: Enhanced debug log
            if log_callback:
                log_callback(f"  📦 Processing group #{group.group_id}: {group.count} photos (best: {os.path.basename(best_photo.filepath) if best_photo else 'None'})", "info")
            
            for i, photo in enumerate(group.photos):
                if i == group.best_index:
                    # 最佳照片：保留原位
                    stats['best_marked'] += 1
                else:
                    try:
                        # V3.9.4: 统一规范化路径并进行不区分大小写的匹配（如果必要）
                        src_path = os.path.normpath(photo.filepath)
                        # 再次尝试匹配：如果 SourceFile 只有文件名，则拼上 output_dir
                        if not os.path.exists(src_path):
                            fallback_path = os.path.join(output_dir, os.path.basename(src_path))
                            if os.path.exists(fallback_path):
                                src_path = fallback_path
                        
                        filename = os.path.basename(src_path)
                        dest = os.path.join(burst_dir, filename)
                        
                        if os.path.exists(src_path):
                            # V4.1: 记录移动操作
                            shutil.move(src_path, dest)
                            stats['photos_moved'] += 1
                            
                            # 尝试同时移动对应的 sidecar 文件 (如 .xmp, .jpg)
                            file_prefix = os.path.splitext(src_path)[0]
                            for sidecar_ext in ['.xmp', '.jpg', '.JPG', '.ARW.xmp', '.nef.xmp']:
                                sidecar_path = file_prefix + sidecar_ext
                                if os.path.exists(sidecar_path):
                                    try:
                                        shutil.move(sidecar_path, os.path.join(burst_dir, os.path.basename(sidecar_path)))
                                    except:
                                        pass
                        else:
                            if log_callback:
                                log_callback(f"    ⚠️ File not found: {filename}", "warning")
                                print(f"DEBUG: File not found at {src_path}")
                    except Exception as e:
                        if log_callback:
                            log_callback(f"    ❌ Move failed {filename}: {e}", "error")
                        print(f"⚠️ Move file failed: {e}")
            
            stats['groups_processed'] += 1
        
        return stats
    
    def run_full_detection(
        self,
        directory: str,
        rating_dirs: List[str] = None
    ) -> Dict[str, any]:
        """
        运行完整的连拍检测流程
        V4.0: 支持递归扫描鸟种子目录
        
        Args:
            directory: 主目录路径
            rating_dirs: 评分子目录列表（默认简体/繁体 TW 的 3星、2星目录）
            
        Returns:
            完整结果
        """
        if rating_dirs is None:
            rating_dirs = ['3星_优选', '3星_優選', '2星_良好']
        
        results = {
            'total_photos': 0,
            'photos_with_subsec': 0,
            'groups_detected': 0,
            'groups_by_dir': {}
        }
        
        from constants import RAW_EXTENSIONS, HEIF_EXTENSIONS
        extensions = set(RAW_EXTENSIONS + HEIF_EXTENSIONS)
        
        def collect_files_recursive(dir_path: str) -> List[str]:
            """V4.0: 递归收集目录下所有 RAW 文件（包括鸟种子目录）"""
            filepaths = []
            if not os.path.exists(dir_path):
                return filepaths
            
            for entry in os.scandir(dir_path):
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in extensions:
                        filepaths.append(entry.path)
                elif entry.is_dir() and not entry.name.startswith('burst_'):
                    # 递归扫描鸟种子目录，但跳过已有的 burst 目录
                    filepaths.extend(collect_files_recursive(entry.path))
            return filepaths
        
        # 遍历评分目录
        for rating_dir in rating_dirs:
            subdir = os.path.join(directory, rating_dir)
            if not os.path.exists(subdir):
                continue
            
            # V4.0: 递归获取文件列表（包括鸟种子目录）
            filepaths = collect_files_recursive(subdir)
            
            if not filepaths:
                continue
            
            results['total_photos'] += len(filepaths)
            
            # 读取时间戳
            photos = self.read_timestamps(filepaths)
            results['photos_with_subsec'] += sum(1 for p in photos if p.has_subsec)
            
            # 从 SQLite 数据库读取锐度和美学
            photos = self.enrich_from_db(photos, directory)
            
            # 检测连拍组
            groups = self.detect_groups(photos)
            
            # 选择最佳
            groups = self.select_best_in_groups(groups)
            
            results['groups_detected'] += len(groups)
            results['groups_by_dir'][rating_dir] = {
                'photos': len(filepaths),
                'groups': len(groups),
                'group_details': [
                    {
                        'id': g.group_id,
                        'count': g.count,
                        'best': os.path.basename(g.best_photo.filepath) if g.best_photo else None
                    }
                    for g in groups
                ]
            }
        
        return results


# 测试函数
def test_burst_detector():
    """测试连拍检测器"""
    detector = BurstDetector()
    
    # 测试目录
    test_dir = '/Users/jameszhenyu/Desktop/Ti'
    
    if not os.path.exists(test_dir):
        print(f"测试目录不存在: {test_dir}")
        return
    
    # 获取所有图片文件
    from constants import RAW_EXTENSIONS, JPG_EXTENSIONS, HEIF_EXTENSIONS
    extensions = set(RAW_EXTENSIONS + JPG_EXTENSIONS + HEIF_EXTENSIONS)
    filepaths = []
    for entry in os.scandir(test_dir):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in extensions:
                filepaths.append(entry.path)
    
    print(f"找到 {len(filepaths)} 个图片文件")
    
    # 读取时间戳
    print("\n读取时间戳...")
    photos = detector.read_timestamps(filepaths)
    
    # 显示结果
    print(f"\n读取到 {len(photos)} 个时间戳：")
    for p in photos[:10]:  # 只显示前 10 个
        subsec_str = f".{p.subsec}" if p.subsec else ""
        dt_str = p.datetime_original.strftime("%Y-%m-%d %H:%M:%S") if p.datetime_original else "无"
        print(f"  {os.path.basename(p.filepath)}: {dt_str}{subsec_str} (评分: {p.rating})")
    
    # 检测连拍组
    print("\n检测连拍组...")
    groups = detector.detect_groups(photos)
    
    print(f"\n发现 {len(groups)} 个连拍组：")
    for group in groups:
        print(f"  组 #{group.group_id}: {group.count} 张照片")
        for p in group.photos:
            print(f"    - {os.path.basename(p.filepath)}")


if __name__ == '__main__':
    test_burst_detector()
