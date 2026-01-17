#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExifTool管理器
用于设置照片评分和锐度值到EXIF/IPTC元数据
"""

import os
import subprocess
import sys
from typing import Optional, List, Dict
from pathlib import Path
from constants import RATING_FOLDER_NAMES


class ExifToolManager:
    """ExifTool管理器 - 使用本地打包的exiftool"""

    def __init__(self):
        """初始化ExifTool管理器"""
        # 获取exiftool路径（支持PyInstaller打包）
        self.exiftool_path = self._get_exiftool_path()

        # 验证exiftool可用性
        if not self._verify_exiftool():
            raise RuntimeError(f"ExifTool不可用: {self.exiftool_path}")

        print(f"✅ ExifTool已加载: {self.exiftool_path}")

    def _get_exiftool_path(self) -> str:
        """获取exiftool可执行文件路径"""
        # V3.9.4: 处理 Windows 平台的可执行文件后缀
        is_windows = sys.platform.startswith('win')
        exe_name = 'exiftool.exe' if is_windows else 'exiftool'

        if hasattr(sys, '_MEIPASS'):
            # PyInstaller打包后的路径
            base_path = sys._MEIPASS
            print(f"🔍 PyInstaller环境检测到")
            print(f"   base_path (sys._MEIPASS): {base_path}")

            # 直接使用 exiftool_bundle/exiftool 路径（唯一打包位置）
            exiftool_path = os.path.join(base_path, 'exiftool_bundle', exe_name)
            abs_path = os.path.abspath(exiftool_path)

            print(f"   正在检查 {exe_name}...")
            print(f"   路径: {abs_path}")
            print(f"   存在: {os.path.exists(abs_path)}")
            
            if os.path.exists(abs_path):
                print(f"   ✅ 找到 {exe_name}")
                return abs_path
            else:
                # 尝试不带后缀的路径（以防打包逻辑有变）
                fallback_path = os.path.join(base_path, 'exiftool_bundle', 'exiftool')
                if os.path.exists(fallback_path):
                    print(f"   ✅ 找到 exiftool (fallback)")
                    return fallback_path
                
                print(f"   ⚠️  未找到可执行的 {exe_name}")
                return abs_path
        else:
            # 开发环境路径
            # V3.9.3: 优先使用系统 exiftool（解决 ARM64/Intel 不兼容问题）
            import shutil
            system_exiftool = shutil.which('exiftool')
            if system_exiftool:
                print(f"🔍 使用系统 ExifTool: {system_exiftool}")
                return system_exiftool
            
            # 回退到项目目录下的 exiftool
            project_root = os.path.dirname(os.path.abspath(__file__))
            
            # 优先检查带 .exe 的路径 (Windows 开发环境可能手动放了 exiftool.exe)
            if is_windows:
                win_path = os.path.join(project_root, 'exiftool.exe')
                if os.path.exists(win_path):
                    return win_path
            
            return os.path.join(project_root, 'exiftool')

    def _verify_exiftool(self) -> bool:
        """验证exiftool是否可用"""
        print(f"\n🧪 验证 ExifTool 是否可执行...")
        print(f"   路径: {self.exiftool_path}")
        print(f"   测试命令: {self.exiftool_path} -ver")

        try:
            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                [self.exiftool_path, '-ver'],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags
            )
            print(f"   返回码: {result.returncode}")
            print(f"   stdout: {result.stdout.strip()}")
            if result.stderr:
                print(f"   stderr: {result.stderr.strip()}")

            if result.returncode == 0:
                print(f"   ✅ ExifTool 验证成功")
                return True
            else:
                print(f"   ❌ ExifTool 返回非零退出码")
                return False

        except subprocess.TimeoutExpired:
            print(f"   ❌ ExifTool 执行超时（5秒）")
            return False
        except Exception as e:
            print(f"   ❌ ExifTool 验证异常: {type(e).__name__}: {e}")
            return False

    def _escape_arg(self, arg: str) -> str:
        """
        V4.2: 为 ExifTool -E 模式转义参数。
        将特殊字符转义为 HTML 实体，特别是将换行符转义为 &#10;，
        从而允许在 stdin (-@ -) 模式的单行中传递多行文本。
        """
        if arg is None:
            return ""
        # 必须转义 & 为 &amp; 因为我们开启了 -E 模式
        return str(arg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "&#10;").replace("\r", "&#13;")

    def set_rating_and_pick(self, file_path: str, rating: int, pick: int = 0, sharpness: float = None,
                          nima_score: float = None) -> bool:
        """
        设置照片的评分和旗标

        Args:
            file_path: 文件路径
            rating: 评分 (0-5)
            pick: 旗标 (1=精选, 0=无, -1=排除)
            sharpness: 锐度分数 (可选)
            nima_score: NIMA评分 (可选)

        Returns:
            是否成功
        """
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False

        # V4.2: 通过 batch_set_metadata 统一入口，获取 stdin 模式的稳定性
        # 将单文件封装为列表进行批量处理（底层使用 stdin 模式）
        result_stats = self.batch_set_metadata([{
            'file': file_path,
            'rating': rating,
            'pick': pick,
            'sharpness': sharpness,
            'nima_score': nima_score
        }])

        return result_stats['success'] > 0

    def batch_set_metadata(
        self,
        files_metadata: List[Dict[str, any]]
    ) -> Dict[str, int]:
        """
        批量设置元数据（使用-execute分隔符，支持不同文件不同参数）

        Args:
            files_metadata: 文件元数据列表
                [
                    {'file': 'path1.NEF', 'rating': 3, 'pick': 1, 'sharpness': 95.3, 'nima_score': 7.5, 'label': 'Green', 'focus_status': '精准'},
                    {'file': 'path2.NEF', 'rating': 2, 'pick': 0, 'sharpness': 78.5, 'nima_score': 6.8, 'focus_status': '偏移'},
                    {'file': 'path3.NEF', 'rating': -1, 'pick': -1, 'sharpness': 45.2, 'nima_score': 5.2},
                ]
                # V3.4: 添加 label 参数（颜色标签，如 'Green' 用于飞鸟）
                # V3.9: 添加 focus_status 参数（对焦状态）

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        stats = {'success': 0, 'failed': 0}

        # ExifTool批量模式：使用 -execute 分隔符为每个文件单独设置参数
        # V3.9.1: 改用 XMP 字段，XMP 原生支持 UTF-8 中文
        # V3.9.4: 强制指定编码为 utf8 解决 Windows/Mac 的中文乱码问题
        # V4.2: 改用 stdin (-@ -) 模式彻底解决 Windows 命令行编码破坏中文的问题
        # 同时增加 -E 参数以支持转义字符（如多行题注中的换行符）
        cmd = [self.exiftool_path, '-charset', 'utf8', '-E', '-@', '-']

        arg_lines = []
        for item in files_metadata:
            file_path = item['file']
            rating = item.get('rating', None)
            pick = item.get('pick', None)
            sharpness = item.get('sharpness', None)
            nima_score = item.get('nima_score', None)
            label = item.get('label', None)
            focus_status = item.get('focus_status', None)
            caption = item.get('caption', None)

            if not os.path.exists(file_path):
                print(f"⏭️  跳过不存在的文件: {file_path}")
                stats['failed'] += 1
                continue

            # V4.2: 参数通过 arg_lines 传递，不再直接拼在 cmd 中
            if rating is not None:
                arg_lines.append(f'-Rating={rating}')
            if pick is not None:
                arg_lines.append(f'-XMP:Pick={pick}')

            if sharpness is not None:
                sharpness_str = f'{sharpness:06.2f}'
                arg_lines.append(f'-XMP:City={sharpness_str}')

            if nima_score is not None:
                nima_str = f'{nima_score:05.2f}'
                arg_lines.append(f'-XMP:State={nima_str}')

            if label is not None:
                arg_lines.append(f'-XMP:Label={label}')
            
            if focus_status is not None:
                arg_lines.append(f'-XMP:Country={self._escape_arg(focus_status)}')
            
            if caption is not None:
                arg_lines.append(f'-XMP:Description={self._escape_arg(caption)}')

            arg_lines.append(file_path)
            arg_lines.append('-overwrite_original')
            arg_lines.append('-execute')

        if not arg_lines:
            return stats

        # 执行批量命令
        try:
            if len(files_metadata) > 1:
                print(f"📦 批量处理 {len(files_metadata)} 个文件 (Stdin 模式)...")

            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            # 将参数列表转换为换行符分隔的字符串
            arg_text = "\n".join(arg_lines) + "\n"
            
            result = subprocess.run(
                cmd,
                input=arg_text,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=300,
                creationflags=creationflags
            )

            if result.returncode == 0:
                stats['success'] = len(files_metadata) - stats['failed']
                if len(files_metadata) > 1:
                    print(f"✅ 批量处理完成: {stats['success']} 成功, {stats['failed']} 失败")
                self._create_xmp_sidecars_for_raf(files_metadata)
            else:
                print(f"❌ 批量处理失败: {result.stderr}")
                stats['failed'] = len(files_metadata)

        except Exception as e:
            print(f"❌ 批量处理异常: {e}")
            stats['failed'] = len(files_metadata)

        return stats
    
    def _create_xmp_sidecars_for_raf(self, files_metadata: List[Dict[str, any]]):
        """
        V3.9.2: 为 RAF/ORF 等需要侧车文件的格式创建 XMP 文件
        
        Lightroom 可以读取嵌入在大多数 RAW 格式中的 XMP，
        但 Fujifilm RAF 需要单独的 .xmp 侧车文件
        """
        needs_sidecar_extensions = {'.raf', '.orf'}  # Fujifilm, Olympus
        
        for item in files_metadata:
            file_path = item.get('file', '')
            if not file_path:
                continue
            
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in needs_sidecar_extensions:
                continue
            
            # 构建 XMP 侧车文件路径
            xmp_path = os.path.splitext(file_path)[0] + '.xmp'
            
            try:
                # 使用 exiftool 从 RAW 文件提取 XMP 到侧车文件
                cmd = [
                    self.exiftool_path,
                    '-o', xmp_path,
                    '-TagsFromFile', file_path,
                    '-XMP:all<XMP:all'
                ]
                # V3.9.4: 在 Windows 上隐藏控制台窗口
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
                
                result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=creationflags)
                # 不需要打印成功消息，避免刷屏
            except Exception:
                pass  # 侧车文件创建失败不影响主流程

    def read_metadata(self, file_path: str) -> Optional[Dict]:
        """
        读取文件的元数据

        Args:
            file_path: 文件路径

        Returns:
            元数据字典或None
        """
        if not os.path.exists(file_path):
            return None

        # V3.9.1: 使用 XMP 字段
        # V3.9.4: 增加 UTF-8 支持
        # V4.2: 改用 stdin 模式，防止 Windows 路径编码导致的读取失败
        cmd = [self.exiftool_path, '-charset', 'utf8', '-@', '-']
        
        arg_lines = [
            '-j',
            '-n',
            '-Rating',
            '-XMP:Pick',
            '-XMP:Label',
            '-XMP:City',
            '-XMP:State',
            '-XMP:Country',
            '-XMP:Description',
            file_path
        ]

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                cmd,
                input="\n".join(arg_lines) + "\n",
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=10,
                creationflags=creationflags
            )

            if result.returncode == 0:
                import json
                stdout = result.stdout or ""
                if not stdout.strip():
                    return None
                data = json.loads(stdout)
                return data[0] if data else None
            else:
                return None

        except Exception as e:
            print(f"❌ 读取元数据失败: {e}")
            return None

    def reset_metadata(self, file_path: str) -> bool:
        """
        重置照片的评分和旗标为初始状态

        Args:
            file_path: 文件路径

        Returns:
            是否成功
        """
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return False

        # 删除Rating、Pick、City、Country和Province-State字段
        # V4.2: 通过 stdin 模式重置
        cmd = [self.exiftool_path, '-charset', 'utf8', '-@', '-']
        arg_lines = [
            '-Rating=',
            '-XMP:Pick=',
            '-XMP:Label=',
            '-IPTC:City=',
            '-IPTC:Country-PrimaryLocationName=',
            '-IPTC:Province-State=',
            '-XMP:City=',           # V4.0: 锐度
            '-XMP:State=',          # V4.0: TOPIQ美学
            '-XMP:Country=',        # V4.0: 对焦状态
            '-XMP:Description=',    # V4.0: 详细评分说明
            file_path,
            '-overwrite_original',
            '-execute'
        ]

        try:
            # V3.9.4: 在 Windows 上隐藏控制台窗口
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
            
            result = subprocess.run(
                cmd,
                input="\n".join(arg_lines) + "\n",
                capture_output=True,
                text=True,
                timeout=30,
                encoding='utf-8',
                creationflags=creationflags
            )

            if result.returncode == 0:
                filename = os.path.basename(file_path)
                print(f"✅ EXIF已重置: {filename}")
                return True
            else:
                print(f"❌ ExifTool错误: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            print(f"❌ ExifTool超时: {file_path}")
            return False
        except Exception as e:
            print(f"❌ ExifTool异常: {e}")
            return False

    def batch_reset_metadata(self, file_paths: List[str], batch_size: int = 50, log_callback=None, i18n=None) -> Dict[str, int]:
        """
        批量重置元数据（强制清除所有EXIF评分字段）

        Args:
            file_paths: 文件路径列表
            batch_size: 每批处理的文件数量（默认50，避免命令行过长）
            log_callback: 日志回调函数（可选，用于UI显示）
            i18n: I18n instance for internationalization (optional)

        Returns:
            统计结果 {'success': 成功数, 'failed': 失败数}
        """
        def log(msg):
            """统一日志输出"""
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        stats = {'success': 0, 'failed': 0}
        total = len(file_paths)

        if i18n:
            log(i18n.t("logs.batch_reset_start", total=total))
        else:
            log(f"📦 开始重置 {total} 个文件的EXIF元数据...")
            log(f"   强制清除所有评分字段\n")

        # 分批处理（避免命令行参数过长）
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_files = file_paths[batch_start:batch_end]

            # 过滤不存在的文件
            valid_files = [f for f in batch_files if os.path.exists(f)]
            stats['failed'] += len(batch_files) - len(valid_files)

            if not valid_files:
                continue

            # 构建ExifTool参数列表
            # V4.2: 通过 stdin 模式重置，同时增加 -E 支持转义
            cmd = [self.exiftool_path, '-charset', 'utf8', '-E', '-@', '-']
            
            arg_lines = [
                '-Rating=',
                '-XMP:Pick=',
                '-XMP:Label=',
                '-XMP:City=',           # V4.0: 锐度
                '-XMP:State=',          # V4.0: TOPIQ美学
                '-XMP:Country=',        # V4.0: 对焦状态
                '-XMP:Description=',    # V4.0: 详细评分说明
                '-IPTC:City=',          # 旧版兼容
                '-IPTC:Country-PrimaryLocationName=',
                '-IPTC:Province-State=',
                '-overwrite_original'
            ]
            # 添加所有有效文件路径
            arg_lines.extend(valid_files)
            # 添加执行命令
            arg_lines.append('-execute')

            try:
                # V3.9.4: 在 Windows 上隐藏控制台窗口
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith('win') else 0
                
                # 将参数列表转换为换行符分隔的字符串
                arg_text = "\n".join(arg_lines) + "\n"
                
                result = subprocess.run(
                    cmd,
                    input=arg_text,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    timeout=120,
                    creationflags=creationflags
                )

                if result.returncode == 0:
                    # 所有文件都被处理
                    stats['success'] += len(valid_files)
                    if i18n:
                        log(i18n.t("logs.batch_progress", start=batch_start+1, end=batch_end, success=len(valid_files), skipped=0))
                    else:
                        log(f"  ✅ 批次 {batch_start+1}-{batch_end}: {len(valid_files)} 个文件已处理")
                else:
                    stats['failed'] += len(valid_files)
                    if i18n:
                        log(f"  ❌ {i18n.t('logs.batch_failed', start=batch_start+1, end=batch_end, error=result.stderr.strip())}")
                    else:
                        log(f"  ❌ 批次 {batch_start+1}-{batch_end} 失败: {result.stderr.strip()}")

            except subprocess.TimeoutExpired:
                stats['failed'] += len(valid_files)
                if i18n:
                    log(f"  ⏱️  {i18n.t('logs.batch_timeout', start=batch_start+1, end=batch_end)}")
                else:
                    log(f"  ⏱️  批次 {batch_start+1}-{batch_end} 超时")
            except Exception as e:
                stats['failed'] += len(valid_files)
                if i18n:
                    log(f"  ❌ {i18n.t('logs.batch_error', start=batch_start+1, end=batch_end, error=str(e))}")
                else:
                    log(f"  ❌ 批次 {batch_start+1}-{batch_end} 错误: {e}")

        if i18n:
            log(f"\n{i18n.t('logs.batch_complete', success=stats['success'], skipped=0, failed=stats['failed'])}")
        else:
            log(f"\n✅ 批量重置完成: {stats['success']} 成功, {stats['failed']} 失败")
        return stats

    def restore_files_from_manifest(self, dir_path: str, log_callback=None) -> Dict[str, int]:
        """
        V3.3: 根据 manifest 将文件恢复到原始位置
        V3.3.1: 增强版 - 也处理不在 manifest 中的文件
        
        Args:
            dir_path: str, 原始目录路径
            log_callback: callable, 日志回调函数
        
        Returns:
            dict: {'restored': int, 'failed': int, 'not_found': int}
        """
        import json
        import shutil
        
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)
        
        stats = {'restored': 0, 'failed': 0, 'not_found': 0}
        manifest_path = os.path.join(dir_path, ".superpicky_manifest.json")
        folders_to_check = set()
        
        # 第一步：从 manifest 恢复文件（如果存在）
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                
                files = manifest.get('files', [])
                if files:
                    log(f"\n📂 从 manifest 恢复 {len(files)} 个文件...")
                    
                    for file_info in files:
                        filename = file_info['filename']
                        folder = file_info['folder']
                        
                        src_path = os.path.join(dir_path, folder, filename)
                        dst_path = os.path.join(dir_path, filename)
                        
                        folders_to_check.add(os.path.join(dir_path, folder))
                        
                        if not os.path.exists(src_path):
                            stats['not_found'] += 1
                            continue
                        
                        if os.path.exists(dst_path):
                            stats['failed'] += 1
                            log(f"  ⚠️  目标已存在，跳过: {filename}")
                            continue
                        
                        try:
                            shutil.move(src_path, dst_path)
                            stats['restored'] += 1
                        except Exception as e:
                            stats['failed'] += 1
                            log(f"  ❌ 恢复失败: {filename} - {e}")
                
                # 删除 manifest 文件
                try:
                    os.remove(manifest_path)
                    log("  🗑️  已删除 manifest 文件")
                except Exception as e:
                    log(f"  ⚠️  删除 manifest 失败: {e}")
                    
            except Exception as e:
                log(f"⚠️  读取 manifest 失败: {e}")
        else:
            log("ℹ️  未找到 manifest 文件")
        
        # 第二步：扫描评分子目录，恢复任何剩余文件
        log("\n📂 扫描评分子目录...")
        
        # V3.3: 添加旧版目录到扫描列表（兼容旧版本）
        legacy_folders = ["2星_良好_锐度", "2星_良好_美学"]
        all_folders = list(RATING_FOLDER_NAMES.values()) + legacy_folders
        
        for folder_name in set(all_folders):  # 使用 set 去重
            folder_path = os.path.join(dir_path, folder_name)
            folders_to_check.add(folder_path)
            
            if not os.path.exists(folder_path):
                continue
            
            # 移动所有文件回主目录
            for filename in os.listdir(folder_path):
                src_path = os.path.join(folder_path, filename)
                dst_path = os.path.join(dir_path, filename)
                
                # 跳过子目录
                if os.path.isdir(src_path):
                    continue
                
                if os.path.exists(dst_path):
                    log(f"  ⚠️  目标已存在，跳过: {filename}")
                    continue
                
                try:
                    shutil.move(src_path, dst_path)
                    stats['restored'] += 1
                    log(f"  ✅ 恢复: {folder_name}/{filename}")
                except Exception as e:
                    stats['failed'] += 1
                    log(f"  ❌ 恢复失败: {filename} - {e}")
        
        # 第三步：删除空的分类文件夹
        for folder_path in folders_to_check:
            if os.path.exists(folder_path):
                try:
                    if not os.listdir(folder_path):
                        os.rmdir(folder_path)
                        folder_name = os.path.basename(folder_path)
                        log(f"  🗑️  删除空文件夹: {folder_name}/")
                except Exception as e:
                    log(f"  ⚠️  删除文件夹失败: {e}")
        
        log(f"\n✅ 文件恢复完成: 已恢复 {stats['restored']} 张")
        if stats['not_found'] > 0:
            log(f"⚠️  {stats['not_found']} 张文件未找到")
        if stats['failed'] > 0:
            log(f"❌ {stats['failed']} 张恢复失败")
        
        return stats


# 全局实例
exiftool_manager = None


def get_exiftool_manager() -> ExifToolManager:
    """获取ExifTool管理器单例"""
    global exiftool_manager
    if exiftool_manager is None:
        exiftool_manager = ExifToolManager()
    return exiftool_manager


# 便捷函数
def set_photo_metadata(file_path: str, rating: int, pick: int = 0, sharpness: float = None,
                      nima_score: float = None) -> bool:
    """设置照片元数据的便捷函数 (V3.2: 移除brisque_score)"""
    manager = get_exiftool_manager()
    return manager.set_rating_and_pick(file_path, rating, pick, sharpness, nima_score)


if __name__ == "__main__":
    # 测试代码
    print("=== ExifTool管理器测试 ===\n")

    # 初始化管理器
    manager = ExifToolManager()

    print("✅ ExifTool管理器初始化完成")

    # 如果提供了测试文件路径，执行实际测试
    test_files = [
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6782.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6783.NEF",
        "/Volumes/990PRO4TB/2025/2025-08-19/_Z9W6784.NEF"
    ]

    # 检查测试文件是否存在
    available_files = [f for f in test_files if os.path.exists(f)]

    if available_files:
        print(f"\n🧪 发现 {len(available_files)} 个测试文件，执行实际测试...")

        # 0️⃣ 先重置所有测试文件
        print("\n0️⃣ 重置测试文件元数据:")
        reset_stats = manager.batch_reset_metadata(available_files)
        print(f"   结果: {reset_stats}\n")

        # 单个文件测试 - 优秀照片
        print("\n1️⃣ 单个文件测试 - 优秀照片 (3星 + 精选旗标):")
        success = manager.set_rating_and_pick(
            available_files[0],
            rating=3,
            pick=1
        )
        print(f"   结果: {'✅ 成功' if success else '❌ 失败'}")

        # 批量测试
        if len(available_files) >= 2:
            print("\n2️⃣ 批量处理测试:")
            batch_data = [
                {'file': available_files[0], 'rating': 3, 'pick': 1},
                {'file': available_files[1], 'rating': 2, 'pick': 0},
            ]
            if len(available_files) >= 3:
                batch_data.append(
                    {'file': available_files[2], 'rating': -1, 'pick': -1}
                )

            stats = manager.batch_set_metadata(batch_data)
            print(f"   结果: {stats}")

        # 读取元数据验证
        print("\n3️⃣ 读取元数据验证:")
        for i, file_path in enumerate(available_files, 1):
            metadata = manager.read_metadata(file_path)
            filename = os.path.basename(file_path)
            if metadata:
                print(f"   {filename}:")
                print(f"      Rating: {metadata.get('Rating', 'N/A')}")
                print(f"      Pick: {metadata.get('Pick', 'N/A')}")
                print(f"      Label: {metadata.get('Label', 'N/A')}")
    else:
        print("\n⚠️  未找到测试文件，跳过实际测试")
