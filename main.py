#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 简化版 (Pure Tkinter, 无PyQt依赖)
Version: 3.2.0 - 二次选鸟功能 (Post-DA)
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import csv
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from find_bird_util import reset, raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds
from utils import write_to_csv, log_message
from exiftool_manager import get_exiftool_manager
from advanced_config import get_advanced_config
from advanced_settings_dialog import AdvancedSettingsDialog
from post_adjustment_dialog import PostAdjustmentDialog

# 尝试导入主题和图片库
try:
    from ttkthemes import ThemedTk
    THEME_AVAILABLE = True
except ImportError:
    THEME_AVAILABLE = False
    print("提示: 安装 ttkthemes 可获得更美观的主题 (pip install ttkthemes)")

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("提示: 需要安装 Pillow 才能显示图标 (pip install Pillow)")


class WorkerThread(threading.Thread):
    """处理线程"""

    def __init__(self, dir_path, ui_settings, progress_callback, finished_callback, log_callback):
        super().__init__(daemon=True)
        self.dir_path = dir_path
        self.ui_settings = ui_settings
        self.progress_callback = progress_callback
        self.finished_callback = finished_callback
        self.log_callback = log_callback
        self._stop_event = threading.Event()
        self.caffeinate_process = None  # caffeinate进程（防休眠）

        # 统计数据
        self.stats = {
            'total': 0,
            'star_3': 0,  # 优选照片（3星）
            'picked': 0,  # 精选照片（3星中美学+锐度双Top的）
            'star_2': 0,  # 良好照片（2星）
            'star_1': 0,  # 普通照片（1星）
            'star_0': 0,  # 0星照片（技术质量差）
            'no_bird': 0,  # 无鸟照片（-1星）
            'start_time': 0,
            'end_time': 0,
            'total_time': 0,
            'avg_time': 0
        }

    @staticmethod
    def _format_time(seconds):
        """格式化时间：秒转为 分钟+秒 格式"""
        if seconds < 60:
            return f"{seconds:.1f}秒"
        else:
            minutes = int(seconds // 60)
            secs = seconds % 60
            return f"{minutes}分{secs:.0f}秒"

    def _start_caffeinate(self):
        """启动caffeinate防止系统休眠和屏幕保护程序"""
        try:
            # -d: 防止显示器休眠（同时阻止屏幕保护程序）
            # -i: 防止系统空闲休眠
            self.caffeinate_process = subprocess.Popen(
                ['caffeinate', '-d', '-i'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.log_callback("☕ 已启动防休眠保护（处理期间Mac不会休眠或启动屏幕保护程序）")
        except Exception as e:
            self.log_callback(f"⚠️  防休眠启动失败: {e}（不影响正常处理）")
            self.caffeinate_process = None

    def _stop_caffeinate(self):
        """停止caffeinate"""
        if self.caffeinate_process:
            try:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=2)
                self.log_callback("☕ 已停止防休眠保护")
            except Exception:
                # 如果terminate失败，强制kill
                try:
                    self.caffeinate_process.kill()
                except Exception:
                    pass
            finally:
                self.caffeinate_process = None

    def run(self):
        """执行处理"""
        try:
            # 启动防休眠保护
            self._start_caffeinate()

            # 执行主要处理逻辑
            self.process_files()

            if self.finished_callback:
                self.finished_callback(self.stats)
        except Exception as e:
            self.log_callback(f"❌ 错误: {e}")
        finally:
            # 确保停止caffeinate（即使出错也要停止）
            self._stop_caffeinate()

    def process_files(self):
        """处理文件的核心逻辑"""
        import time

        start_time = time.time()
        self.stats['start_time'] = start_time

        raw_extensions = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng', '.3fr', 'iiq']
        jpg_extensions = ['.jpg', '.jpeg']

        raw_dict = {}
        jpg_dict = {}
        files_tbr = []

        # V3.1: 收集所有3星照片，用于后续计算精选旗标（美学+锐度双排名交集）
        star_3_photos = []  # [(raw_file_path, nima_score, sharpness), ...]

        # 扫描文件
        scan_start = time.time()
        for filename in os.listdir(self.dir_path):
            if filename.startswith('.'):
                continue

            file_prefix, file_ext = os.path.splitext(filename)
            if file_ext.lower() in raw_extensions:
                raw_dict[file_prefix] = file_ext
            if file_ext.lower() in jpg_extensions:
                jpg_dict[file_prefix] = file_ext
                files_tbr.append(filename)

        scan_time = (time.time() - scan_start) * 1000
        self.log_callback(f"⏱️  文件扫描耗时: {scan_time:.1f}ms")

        # 转换RAW文件
        raw_files_to_convert = []
        for key, value in raw_dict.items():
            if key in jpg_dict.keys():
                log_message(f"FILE: [{key}] has raw and jpg files", self.dir_path)
                jpg_dict.pop(key)
                continue
            else:
                raw_file_path = os.path.join(self.dir_path, key + value)
                raw_files_to_convert.append((key, raw_file_path))

        if raw_files_to_convert:
            raw_start = time.time()
            import multiprocessing
            max_workers = min(4, multiprocessing.cpu_count())
            self.log_callback(f"🔄 开始并行转换 {len(raw_files_to_convert)} 个RAW文件（{max_workers}线程）...")

            def convert_single_raw(args):
                key, raw_path = args
                try:
                    raw_to_jpeg(raw_path)
                    return (key, True, None)
                except Exception as e:
                    return (key, False, str(e))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_raw = {executor.submit(convert_single_raw, args): args for args in raw_files_to_convert}
                converted_count = 0
                for future in as_completed(future_to_raw):
                    key, success, error = future.result()
                    if success:
                        files_tbr.append(key + ".jpg")
                        converted_count += 1
                        if converted_count % 5 == 0 or converted_count == len(raw_files_to_convert):
                            self.log_callback(f"  ✅ 已转换 {converted_count}/{len(raw_files_to_convert)} 张")
                    else:
                        self.log_callback(f"  ❌ 转换失败: {key}.NEF ({error})")

            raw_time_sec = time.time() - raw_start
            avg_raw_time_sec = raw_time_sec / len(raw_files_to_convert) if len(raw_files_to_convert) > 0 else 0
            self.log_callback(f"⏱️  RAW转换耗时: {self._format_time(raw_time_sec)} (平均 {avg_raw_time_sec:.1f}秒/张)\n")

        processed_files = set()
        process_bar = 0

        # 获取ExifTool管理器
        exiftool_mgr = get_exiftool_manager()

        # 加载模型
        model_start = time.time()
        self.log_callback("🤖 加载AI模型...")
        model = load_yolo_model()
        model_time = (time.time() - model_start) * 1000
        self.log_callback(f"⏱️  模型加载耗时: {model_time:.0f}ms")

        total_files = len(files_tbr)
        self.log_callback(f"📁 共 {total_files} 个文件待处理\n")

        ai_total_start = time.time()

        # 处理每个文件
        for i, filename in enumerate(files_tbr):
            if self._stop_event.is_set():
                break

            if filename in processed_files:
                continue
            if i < process_bar:
                continue

            process_bar += 1
            processed_files.add(filename)

            # 更新进度
            should_update_progress = (
                process_bar % 5 == 0 or
                process_bar == total_files or
                process_bar == 1
            )
            if should_update_progress:
                progress = int((process_bar / total_files) * 100)
                self.progress_callback(progress)

            filepath = os.path.join(self.dir_path, filename)
            file_prefix, _ = os.path.splitext(filename)

            self.log_callback(f"[{process_bar}/{total_files}] 处理: {filename}")

            # 记录单张照片处理开始时间
            photo_start = time.time()

            # 运行AI检测（V3.1: 不再需要preview_callback和work_dir）
            try:
                result = detect_and_draw_birds(filepath, model, None, self.dir_path, self.ui_settings)
                if result is None:
                    self.log_callback(f"  ⚠️  无法处理: {filename} (AI推理失败)", "error")
                    continue
            except Exception as e:
                self.log_callback(f"  ❌ 处理异常: {filename} - {str(e)}", "error")
                continue

            detected, selected, confidence, sharpness, nima, brisque = result

            # 获取RAW文件路径
            raw_file_path = None
            if file_prefix in raw_dict:
                raw_extension = raw_dict[file_prefix]
                raw_file_path = os.path.join(self.dir_path, file_prefix + raw_extension)

            # 构建IQA评分显示文本
            iqa_text = ""
            if nima is not None:
                iqa_text += f", 美学:{nima:.2f}"
            if brisque is not None:
                iqa_text += f", 失真:{brisque:.2f}"

            # V3.1: 新的评分逻辑（带具体原因，使用高级配置）
            config = get_advanced_config()
            reject_reason = ""
            quality_issue = ""

            if not detected:
                rating_value = -1
                reject_reason = "完全没鸟"
            elif selected:
                rating_value = 3
            else:
                # 检查0星的具体原因（使用配置阈值）
                if confidence < config.min_confidence:
                    rating_value = 0
                    quality_issue = f"置信度太低({confidence:.0%}<{config.min_confidence:.0%})"
                elif brisque is not None and brisque > config.max_brisque:
                    rating_value = 0
                    quality_issue = f"失真过高({brisque:.1f}>{config.max_brisque})"
                elif nima is not None and nima < config.min_nima:
                    rating_value = 0
                    quality_issue = f"美学太差({nima:.1f}<{config.min_nima:.1f})"
                elif sharpness < config.min_sharpness:
                    rating_value = 0
                    quality_issue = f"锐度太低({sharpness:.0f}<{config.min_sharpness})"
                elif sharpness >= self.ui_settings[1] or \
                     (nima is not None and nima >= self.ui_settings[2]):
                    rating_value = 2
                else:
                    rating_value = 1

            # 设置Lightroom评分（带详细原因）
            # V3.1: 3星照片暂时不设置pick，等全部处理完成后，根据美学+锐度双排名交集设置
            if rating_value == 3:
                rating, pick = 3, 0
                self.stats['star_3'] += 1
                self.log_callback(f"  ⭐⭐⭐ 优选照片 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "success")
            elif rating_value == 2:
                rating, pick = 2, 0
                self.stats['star_2'] += 1
                self.log_callback(f"  ⭐⭐ 良好照片 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "info")
            elif rating_value == 1:
                rating, pick = 1, 0
                self.stats['star_1'] += 1
                self.log_callback(f"  ⭐ 普通照片 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "warning")
            elif rating_value == 0:
                rating, pick = 0, 0
                self.stats['star_0'] += 1
                self.log_callback(f"  0星 - {quality_issue} (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "warning")
            else:  # -1
                rating, pick = -1, -1
                self.stats['no_bird'] += 1
                self.log_callback(f"  ❌ 已拒绝 - {reject_reason}", "error")

            self.stats['total'] += 1

            # V3.1: 单张即时写入EXIF元数据
            if raw_file_path and os.path.exists(raw_file_path):
                exif_start = time.time()
                single_batch = [{
                    'file': raw_file_path,
                    'rating': rating,
                    'pick': pick,
                    'sharpness': sharpness,
                    'nima_score': nima,
                    'brisque_score': brisque
                }]
                batch_stats = exiftool_mgr.batch_set_metadata(single_batch)
                exif_time = (time.time() - exif_start) * 1000

                if batch_stats['failed'] > 0:
                    self.log_callback(f"  ⚠️  EXIF写入失败")
                # 不显示成功日志，避免刷屏

                # V3.1: 收集3星照片信息（用于后续计算精选旗标）
                if rating_value == 3 and nima is not None:
                    star_3_photos.append({
                        'file': raw_file_path,
                        'nima': nima,
                        'sharpness': sharpness
                    })

        # V3.1: 计算精选旗标（3星照片中美学+锐度双排名交集）
        if len(star_3_photos) > 0:
            picked_start = time.time()
            self.log_callback(f"\n🎯 计算精选旗标 (共{len(star_3_photos)}张3星照片)...")
            config = get_advanced_config()
            top_percent = config.picked_top_percentage / 100.0

            # 计算需要选取的数量（至少1张）
            top_count = max(1, int(len(star_3_photos) * top_percent))

            # 按美学排序，取Top N%
            sorted_by_nima = sorted(star_3_photos, key=lambda x: x['nima'], reverse=True)
            nima_top_files = set([photo['file'] for photo in sorted_by_nima[:top_count]])

            # 按锐度排序，取Top N%
            sorted_by_sharpness = sorted(star_3_photos, key=lambda x: x['sharpness'], reverse=True)
            sharpness_top_files = set([photo['file'] for photo in sorted_by_sharpness[:top_count]])

            # 计算交集（同时在美学和锐度Top N%中的照片）
            picked_files = nima_top_files & sharpness_top_files

            if len(picked_files) > 0:
                self.log_callback(f"  📌 美学Top{config.picked_top_percentage}%: {len(nima_top_files)}张")
                self.log_callback(f"  📌 锐度Top{config.picked_top_percentage}%: {len(sharpness_top_files)}张")
                self.log_callback(f"  ⭐ 双排名交集: {len(picked_files)}张 → 设为精选")

                # 批量写入Rating=3和Pick=1到这些照片（复用现有的exiftool_mgr）
                # 注意：虽然之前已经写过Rating=3，但exiftool的batch模式需要完整参数
                picked_batch = []
                for file_path in picked_files:
                    picked_batch.append({
                        'file': file_path,
                        'rating': 3,  # 确保是3星
                        'pick': 1
                    })

                exif_picked_start = time.time()
                picked_stats = exiftool_mgr.batch_set_metadata(picked_batch)
                exif_picked_time = (time.time() - exif_picked_start) * 1000

                if picked_stats['failed'] > 0:
                    self.log_callback(f"  ⚠️  {picked_stats['failed']} 张照片精选旗标写入失败")
                else:
                    self.log_callback(f"  ✅ 精选旗标写入成功")
                self.log_callback(f"  ⏱️  精选EXIF写入耗时: {exif_picked_time:.1f}ms")

                # 更新统计数据
                self.stats['picked'] = len(picked_files) - picked_stats.get('failed', 0)
            else:
                self.log_callback(f"  ℹ️  双排名交集为空，未设置精选旗标")
                self.stats['picked'] = 0

            picked_total_time = (time.time() - picked_start) * 1000
            self.log_callback(f"  ⏱️  精选旗标计算总耗时: {picked_total_time:.1f}ms")

        # AI检测总耗时
        ai_total_time_sec = time.time() - ai_total_start
        avg_ai_time_sec = ai_total_time_sec / total_files if total_files > 0 else 0
        self.log_callback(f"\n⏱️  AI检测总耗时: {self._format_time(ai_total_time_sec)} (平均 {avg_ai_time_sec:.1f}秒/张)")

        # V3.1: 清理临时JPG文件
        self.log_callback("\n🧹 清理临时文件...")
        deleted_count = 0
        for filename in files_tbr:
            file_prefix, file_ext = os.path.splitext(filename)
            # 只删除RAW转换的JPG文件
            if file_prefix in raw_dict and file_ext.lower() in ['.jpg', '.jpeg']:
                jpg_path = os.path.join(self.dir_path, filename)
                try:
                    if os.path.exists(jpg_path):
                        os.remove(jpg_path)
                        deleted_count += 1
                except Exception as e:
                    self.log_callback(f"  ⚠️  删除失败 {filename}: {e}")

        if deleted_count > 0:
            self.log_callback(f"✅ 已删除 {deleted_count} 个临时JPG文件")

        # 记录结束时间
        end_time = time.time()
        self.stats['end_time'] = end_time
        self.stats['total_time'] = end_time - start_time
        self.stats['avg_time'] = (self.stats['total_time'] / total_files) if total_files > 0 else 0

        # V3.1: 不在这里显示"处理完成"，而是在finished_callback中清屏后显示完整报告


class AboutWindow:
    """关于窗口"""
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("关于 慧眼选鸟")
        self.window.geometry("700x600")
        self.window.resizable(False, False)

        # 设置窗口图标（如果有的话）
        # self.window.iconbitmap("icon.ico")

        # 创建主容器
        main_frame = ttk.Frame(self.window, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 创建滚动文本区域
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        # 添加滚动条
        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建文本框
        self.text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set,
            font=("Arial", 10),
            padx=10,
            pady=10
        )
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)

        # 配置文本样式
        self.text.tag_configure("title", font=("Arial", 18, "bold"), spacing1=10)
        self.text.tag_configure("version", font=("Arial", 10), foreground="gray")
        self.text.tag_configure("section", font=("Arial", 12, "bold"), spacing1=15, spacing3=5)
        self.text.tag_configure("subsection", font=("Arial", 11, "bold"), spacing1=10, spacing3=5)
        self.text.tag_configure("body", font=("Arial", 10), spacing1=5)
        self.text.tag_configure("link", font=("Arial", 10), foreground="blue", underline=True)
        self.text.tag_configure("code", font=("Courier", 9), background="#f0f0f0")

        # 填充内容
        self._populate_content()

        # 禁止编辑
        self.text.config(state=tk.DISABLED)

        # 添加关闭按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(pady=(10, 0))

        close_btn = ttk.Button(btn_frame, text="关闭", command=self.window.destroy, width=15)
        close_btn.pack()

        # 窗口居中
        self._center_window()

    def _populate_content(self):
        """填充关于窗口的内容"""
        content = """慧眼选鸟 (SuperPicky)

版本: V3.2.0
发布日期: 2025-10-25

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

👨‍💻 作者信息

开发者: 詹姆斯·于震 (James Yu)
网站: www.jamesphotography.com.au
YouTube: youtube.com/@JamesZhenYu
邮箱: james@jamesphotography.com.au

关于作者:
詹姆斯·于震是一位澳籍华裔职业摄影师，著有畅销三部曲《詹姆斯的风景摄影笔记》（总销量超10万册），他开发慧眼选鸟以提高鸟类摄影师后期筛选效率，让摄影师将更多时间专注于拍摄而非选片。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 软件简介

慧眼选鸟是一款专为鸟类摄影师设计的智能照片筛选工具。

✓ 自动识别鸟类 - 使用先进的AI技术检测照片中的鸟类主体
✓ 多维度评分 - 综合锐度、美学、技术质量等指标智能评级
✓ 精选推荐 - 自动标记美学与锐度双优的顶级作品
✓ 无缝集成 - 直接写入EXIF元数据，与Lightroom完美配合
✓ 批量处理 - 支持RAW格式，高效处理大量照片

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 使用的开源技术

慧眼选鸟基于以下优秀的开源项目构建：

1. Ultralytics YOLOv11
   用于鸟类目标检测与分割，精确识别照片中的鸟类位置和轮廓。
   许可证: AGPL-3.0
   项目地址: github.com/ultralytics/ultralytics

2. PyIQA (Image Quality Assessment)
   用于图像质量评估，包括NIMA美学评分和BRISQUE技术质量评分。
   许可证: CC BY-NC-SA 4.0 (非商业使用)
   项目地址: github.com/chaofengc/IQA-PyTorch
   引用: Chen et al., "TOPIQ", IEEE TIP, 2024

3. ExifTool
   用于EXIF元数据读写，将评分和旗标写入RAW文件。
   许可证: Perl Artistic License / GPL
   项目地址: exiftool.org

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📜 版权与许可

版权所有 © 2024-2025 詹姆斯·于震 (James Yu)

慧眼选鸟是基于开源技术开发的非商业用途摄影工具。

使用条款:
✓ 允许: 个人使用、教育学习、分享推荐
✗ 禁止: 商业用途、销售盈利、移除版权

免责声明:
本软件按"现状"提供，不提供任何保证。作者不对使用本软件产生的任何后果负责。

重要提示:
• AI模型可能误判，请勿完全依赖自动评分
• 处理前请备份原始文件
• 重要项目建议先小批量测试

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 开源声明

慧眼选鸟遵循其依赖项目的开源许可要求：

• AGPL-3.0 (YOLOv11): 修改并分发需开源，网络服务需提供源代码
• CC BY-NC-SA 4.0 (PyIQA): 限制非商业使用

商业使用: 如需商业用途，请联系作者及相关开源项目获取商业许可

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🙏 致谢

感谢以下项目和开发者：
• Ultralytics团队 - 提供了卓越的YOLOv11目标检测框架
• Chaofeng Chen和Jiadi Mo - 开发了PyIQA图像质量评估工具箱
• Phil Harvey - 开发了强大的ExifTool元数据处理工具
• 所有鸟类摄影师 - 你们的反馈和建议推动了慧眼选鸟的不断改进

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📧 联系方式

如果您在使用过程中遇到问题、有改进建议，或希望合作开发：

邮箱: james@jamesphotography.com.au

詹姆斯独立开发的更多免费工具：
慧眼选鸟：AI 鸟类摄影选片工具
慧眼识鸟：AI 鸟种识别工具 （Mac/Win Lightroom 插件）
慧眼找鸟：eBird信息检索工具  Web 测试版
慧眼去星：AI 银河去星软件（Max Photoshop 插件）
图忆作品集：Tui Portfolio IOS 手机专用 
镜书：AI 旅游日记写作助手 IOS 手机专用

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

慧眼选鸟 - 让AI帮你挑选最美的瞬间 🦅📸
"""

        self.text.config(state=tk.NORMAL)
        self.text.insert("1.0", content)
        self.text.config(state=tk.DISABLED)

    def _center_window(self):
        """将窗口居中显示"""
        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')


class SuperPickyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SuperPicky V3.2.0 - 慧眼选鸟")
        self.root.geometry("750x700")  # V3.1: 增加窗口高度，确保所有控件可见
        self.root.minsize(700, 650)  # 设置最小尺寸
        # 允许窗口调整大小（默认行为）

        # 加载高级配置
        self.config = get_advanced_config()

        # 创建菜单栏
        self._create_menu()

        # 设置图标
        icon_path = os.path.join(os.path.dirname(__file__), "img", "icon.png")
        if os.path.exists(icon_path) and PIL_AVAILABLE:
            try:
                icon_img = Image.open(icon_path)
                icon_photo = ImageTk.PhotoImage(icon_img)
                self.root.iconphoto(True, icon_photo)
            except Exception as e:
                print(f"图标加载失败: {e}")

        self.directory_path = ""
        self.worker = None

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.show_initial_help()

    def create_widgets(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)
        self.create_control_panel(main_frame)

    def create_control_panel(self, parent):
        """创建控制面板"""
        # 标题
        title = ttk.Label(parent, text="慧眼选鸟，选片照样爽", font=("Arial", 16, "bold"))
        title.pack(pady=10)

        # 目录选择
        dir_frame = ttk.LabelFrame(parent, text="选择照片目录", padding=10)
        dir_frame.pack(fill=tk.X, padx=10, pady=5)

        self.dir_entry = ttk.Entry(dir_frame, font=("Arial", 11))
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        # V3.1: 支持粘贴路径并按回车确认
        self.dir_entry.bind('<Return>', self._on_path_entered)
        self.dir_entry.bind('<KP_Enter>', self._on_path_entered)

        ttk.Button(dir_frame, text="浏览", command=self.browse_directory, width=10).pack(side=tk.LEFT)

        # 参数设置
        settings_frame = ttk.LabelFrame(parent, text="优选参数", padding=10)
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        # V3.1: 隐藏置信度和归一化选择
        self.ai_var = tk.IntVar(value=50)
        self.norm_var = tk.StringVar(value="对数压缩(V3.1) - 大小鸟公平")

        # 鸟锐度阈值
        sharp_frame = ttk.Frame(settings_frame)
        sharp_frame.pack(fill=tk.X, pady=5)
        ttk.Label(sharp_frame, text="鸟锐度阈值:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.sharp_var = tk.IntVar(value=7500)
        self.sharp_slider = ttk.Scale(sharp_frame, from_=6000, to=9000, variable=self.sharp_var, orient=tk.HORIZONTAL)
        self.sharp_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.sharp_label = ttk.Label(sharp_frame, text="7500", width=6, font=("Arial", 11))
        self.sharp_label.pack(side=tk.LEFT)
        self.sharp_slider.configure(command=lambda v: self._update_sharp_label(v))

        # 摄影美学阈值（NIMA）- V3.1: 范围4.5-5.5，默认4.8
        nima_frame = ttk.Frame(settings_frame)
        nima_frame.pack(fill=tk.X, pady=5)
        ttk.Label(nima_frame, text="摄影美学阈值:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.nima_var = tk.DoubleVar(value=4.8)
        self.nima_slider = ttk.Scale(nima_frame, from_=4.5, to=5.5, variable=self.nima_var, orient=tk.HORIZONTAL)
        self.nima_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.nima_label = ttk.Label(nima_frame, text="4.8", width=6, font=("Arial", 11))
        self.nima_label.pack(side=tk.LEFT)
        self.nima_slider.configure(command=lambda v: self.nima_label.configure(text=f"{float(v):.1f}"))

        # 进度显示
        progress_frame = ttk.LabelFrame(parent, text="处理进度", padding=10)
        progress_frame.pack(fill=tk.BOTH, padx=10, pady=5, expand=True)

        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(0, 10))

        # 日志框（V3.1: 减小固定高度，允许自适应）
        log_scroll = ttk.Scrollbar(progress_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(progress_frame, height=10, state='disabled', yscrollcommand=log_scroll.set,
                                font=("Menlo", 13), bg='#1e1e1e', fg='#d4d4d4',
                                spacing1=4, spacing2=2, spacing3=4, padx=8, pady=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=self.log_text.yview)

        # 配置日志颜色
        self.log_text.tag_config("success", foreground="#00ff88")
        self.log_text.tag_config("error", foreground="#ff0066")
        self.log_text.tag_config("warning", foreground="#ffaa00")
        self.log_text.tag_config("info", foreground="#00aaff")

        # 控制按钮
        btn_frame = ttk.Frame(parent, padding=10)
        btn_frame.pack(fill=tk.X)

        button_container = ttk.Frame(btn_frame)
        button_container.pack(side=tk.RIGHT)

        ttk.Label(button_container, text="V3.2.0 - EXIF标记模式", font=("Arial", 9)).pack(side=tk.RIGHT, padx=10)

        self.reset_btn = ttk.Button(button_container, text="🔄 重置目录", command=self.reset_directory, width=15, state='disabled')
        self.reset_btn.pack(side=tk.RIGHT, padx=5)

        self.post_da_btn = ttk.Button(button_container, text="📊 二次选鸟", command=self.open_post_adjustment, width=15, state='disabled')
        self.post_da_btn.pack(side=tk.RIGHT, padx=5)

        self.start_btn = ttk.Button(button_container, text="▶️  开始处理", command=self.start_processing, width=15)
        self.start_btn.pack(side=tk.RIGHT, padx=5)

    def _create_menu(self):
        """创建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 设置菜单
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="设置", menu=settings_menu)
        settings_menu.add_command(label="高级设置...", command=self.show_advanced_settings)

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="关于慧眼选鸟...", command=self.show_about)

    def show_advanced_settings(self):
        """显示高级设置对话框"""
        dialog = AdvancedSettingsDialog(self.root)
        dialog.show()

    def show_about(self):
        """显示关于窗口"""
        AboutWindow(self.root)

    def _check_report_csv(self):
        """检测目录中是否存在 report.csv，控制二次选鸟按钮状态"""
        if not self.directory_path:
            self.post_da_btn.config(state='disabled')
            return

        report_path = os.path.join(self.directory_path, "_tmp", "report.csv")
        if os.path.exists(report_path):
            self.post_da_btn.config(state='normal')
            self.log("📊 检测到历史分析数据，可使用'二次选鸟'功能\n")
        else:
            self.post_da_btn.config(state='disabled')

    def open_post_adjustment(self):
        """打开二次选鸟对话框"""
        if not self.directory_path:
            messagebox.showwarning("提示", "请先选择照片目录")
            return

        report_path = os.path.join(self.directory_path, "_tmp", "report.csv")
        if not os.path.exists(report_path):
            messagebox.showwarning("提示", "未找到分析报告，请先运行'开始处理'")
            return

        # 打开对话框
        PostAdjustmentDialog(
            self.root,
            self.directory_path,
            on_complete_callback=self._on_post_adjustment_complete
        )

    def _on_post_adjustment_complete(self):
        """二次选鸟完成后的回调"""
        self.log("✅ 二次选鸟完成！评分已更新到EXIF元数据\n")

    def _update_sharp_label(self, value):
        """更新锐度滑块标签（步长500）"""
        rounded_value = round(float(value) / 500) * 500
        self.sharp_var.set(int(rounded_value))
        self.sharp_label.configure(text=f"{int(rounded_value)}")

    def _on_path_entered(self, event):
        """处理粘贴路径后按回车键事件（V3.1）"""
        directory = self.dir_entry.get().strip()
        if directory:
            # 验证目录是否存在
            if os.path.isdir(directory):
                self._handle_directory_selection(directory)
            else:
                messagebox.showerror("错误", f"目录不存在：\n{directory}")
                self.log(f"❌ 目录不存在: {directory}\n", "error")

    def browse_directory(self):
        """浏览目录"""
        directory = filedialog.askdirectory(title="选择照片目录")
        if directory:
            self._handle_directory_selection(directory)

    def _handle_directory_selection(self, directory):
        """处理目录选择"""
        self.directory_path = directory
        self.dir_entry.delete(0, tk.END)
        self.dir_entry.insert(0, directory)
        self.reset_btn.config(state='normal')
        self.log(f"📂 已选择目录: {directory}\n")

        # 检测是否存在 report.csv，启用/禁用"二次选鸟"按钮
        self._check_report_csv()

    def reset_directory(self):
        """重置目录"""
        if not self.directory_path:
            messagebox.showwarning("提示", "请先选择照片目录")
            return

        if messagebox.askyesno("确认重置", "⚠️  重置将清除所有EXIF标记和临时文件，是否继续？"):
            self.log("🔄 开始重置目录...\n")
            success = reset(self.directory_path, log_callback=self.log)
            if success:
                self.log("\n✅ 目录重置完成！")
                messagebox.showinfo("完成", "目录已重置！")
            else:
                messagebox.showerror("错误", "目录重置失败，请查看日志")

    def start_processing(self):
        """开始处理"""
        if not self.directory_path:
            messagebox.showwarning("提示", "请先选择照片目录")
            return

        if self.worker and self.worker.is_alive():
            messagebox.showwarning("提示", "正在处理中，请稍候...")
            return

        # 清空日志和进度
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')
        self.progress_bar['value'] = 0

        self.log("开始处理照片...\n")

        # 获取归一化模式
        selected_text = self.norm_var.get()
        mode_key = selected_text.split(" - ")[0].strip()

        norm_mapping = {
            "对数压缩(V3.1)": "log_compression",
            "原始方差": None,
            "log归一化": "log",
            "gentle归一化": "gentle",
            "sqrt归一化": "sqrt",
            "linear归一化": "linear"
        }
        selected_norm = norm_mapping.get(mode_key, "log_compression")

        # V3.1: ui_settings = [ai_confidence, sharpness_threshold, nima_threshold, save_crop, normalization]
        ui_settings = [
            self.ai_var.get(),
            self.sharp_var.get(),
            self.nima_var.get(),
            False,  # V3.1: 不保存crop图片
            selected_norm
        ]

        # 启动Worker线程
        self.worker = WorkerThread(
            self.directory_path,
            ui_settings,
            self.update_progress,
            self.on_finished,
            self.thread_safe_log
        )

        self.start_btn.config(state='disabled')
        self.reset_btn.config(state='disabled')
        self.worker.start()

    def update_progress(self, value):
        """更新进度条"""
        self.root.after(0, lambda: self.progress_bar.configure(value=value))

    def thread_safe_log(self, message, tag=None):
        """线程安全的日志输出"""
        self.root.after(0, lambda: self.log(message, tag))

    def log(self, message, tag=None):
        """输出日志"""
        self.log_text.config(state='normal')
        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def on_finished(self, stats):
        """处理完成回调"""
        self.start_btn.config(state='normal')
        self.reset_btn.config(state='normal')
        self.post_da_btn.config(state='normal')  # 启用二次选鸟
        self.progress_bar['value'] = 100

        # V3.1: 清空日志窗口，然后显示最终报告（方便查看，无需滚动）
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')

        # 显示统计报告
        report = self._format_statistics_report(stats)
        self.log(report)

        # 显示Lightroom使用指南
        self.show_lightroom_guide()

    def _format_statistics_report(self, stats):
        """格式化统计报告"""
        total = stats['total']
        star_3 = stats['star_3']
        star_2 = stats['star_2']
        star_1 = stats['star_1']
        star_0 = stats.get('star_0', 0)
        no_bird = stats['no_bird']
        total_time = stats['total_time']
        avg_time = stats['avg_time']

        # 有鸟照片
        bird_total = star_3 + star_2 + star_1 + star_0

        report = "\n"
        report += "=" * 50 + "\n"
        report += "📊 处理统计报告\n"
        report += "=" * 50 + "\n"
        report += f"总共识别：{total} 张照片\n"
        report += f"总耗时：{total_time:.1f} 秒 ({total_time/60:.1f} 分钟)\n"
        report += f"平均每张：{avg_time:.2f} 秒\n\n"

        picked = stats.get('picked', 0)

        report += f"⭐⭐⭐ 优选照片（3星）：{star_3} 张 ({star_3/total*100 if total > 0 else 0:.1f}%)\n"
        if picked > 0:
            report += f"  └─ 🏆 精选旗标（美学+锐度双Top）：{picked} 张 ({picked/star_3*100 if star_3 > 0 else 0:.1f}% of 3星）\n"
        report += f"⭐⭐ 良好照片（2星）：{star_2} 张 ({star_2/total*100 if total > 0 else 0:.1f}%)\n"
        report += f"⭐ 普通照片（1星）：{star_1} 张 ({star_1/total*100 if total > 0 else 0:.1f}%)\n"
        if star_0 > 0:
            report += f"0星 技术质量差：{star_0} 张 ({star_0/total*100 if total > 0 else 0:.1f}%)\n"
        report += f"❌ 无鸟照片：{no_bird} 张 ({no_bird/total*100 if total > 0 else 0:.1f}%)\n\n"

        report += f"有鸟照片总数：{bird_total} 张 ({bird_total/total*100 if total > 0 else 0:.1f}%)\n\n"

        report += "=" * 50 + "\n"
        report += "💡 智能提示：\n"

        # 智能提示
        if no_bird / total > 0.8 if total > 0 else False:
            report += "   😅 无鸟照片占比过高...建议调整拍摄角度或使用更长焦镜头\n"
        if star_3 == 0:
            report += "   😢 本次没有优选照片...别灰心，拍鸟需要耐心和运气！\n"
        if star_3 / bird_total > 0.5 if bird_total > 0 else False:
            report += "   🎉 优选照片占比超过50%！拍摄质量很高！\n"
        if avg_time > 2000:
            report += f"   🐌 处理速度 {avg_time/1000:.2f}秒/张\n"

        report += "=" * 50 + "\n"

        return report

    def show_lightroom_guide(self):
        """显示Lightroom使用指南"""
        guide = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📸 Lightroom Classic 使用指南 - 如何查看与使用慧眼选鸟的评分结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【方式1: 导入新照片】
  1️⃣ 打开Lightroom Classic → 点击"文件" → "导入照片和视频"
  2️⃣ 选择处理过的照片目录 → 点击"导入"
  3️⃣ 照片自动带有星级和旗标标记！

【方式2: 重新读取已导入照片的元数据】⭐推荐
  如果照片已经在Lightroom中，需要重新读取EXIF评分：

  1️⃣ 在图库中选中所有处理过的照片（Cmd+A / Ctrl+A 全选）
  2️⃣ 右键点击 → 选择"元数据" → "从文件读取元数据"
  3️⃣ 确认读取 → 星级和旗标将自动更新！

【筛选优选照片】
  方法1 - 按星级筛选：
    • 点击底部筛选栏的"属性"
    • 点击"⭐⭐⭐"图标 → 只显示3星照片（优选）
    • 或点击"≥⭐⭐"→ 显示2星及以上

  方法2 - 按旗标筛选：
    • 点击底部筛选栏的"属性"
    • 点击"🏆精选"旗标 → 只显示精选照片

【按质量指标排序】
  1️⃣ 切换到"网格视图"（G键）
  2️⃣ 点击元数据
  3️⃣ 点击自定义，添加：
     ☑ 城市（锐度值 - 数值越高越清晰）
     ☑ 省/州（摄影美学 - 数值越高越美）
     ☑ 国家（画面失真 - 数值越低越好）
  4️⃣ 点击排序依据（例如点击"城市"按锐度排序）

【评分字段说明】
  • 星级(Rating): -1星(拒绝) / 0星(质量差) / 1星 / 2星 / 3星(优选)
  • 旗标(Pick): 🏆精选(3星中美学+锐度双Top 10%) / 🚫排除(-1星)
  • 城市(City): 锐度值，范围6000-10000，越高越清晰
  • 省/州(Province-State): 摄影美学，范围0-10，越高越符合人类审美
  • 国家(Country): 画面失真，范围0-100，越低质量越好

【快捷工作流程】
  ✅ 步骤1: 筛选3星+精选照片 → 这是最优质的照片
  ✅ 步骤2: 按"城市"降序排序 → 查看最锐利的照片
  ✅ 步骤3: 开始后期处理！

💡 提示：CSV报告保存在照片目录的 report.csv 文件中
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        self.log(guide)

    def show_initial_help(self):
        """显示初始帮助信息"""
        help_text = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  欢迎使用 SuperPicky V3.2.0 - 慧眼选鸟 | 比你更聪明的选片工具
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
使用步骤：
  1️⃣ 点击"浏览"选择照片目录（支持RAW/JPG）
  2️⃣ 调整三星与精选参数（可选，推荐默认值）
  3️⃣ 点击"开始处理"，等待AI分析完成
  4️⃣ 导入Lightroom或用Bridge进行排序与后期处理

📊 评分规则：
  • ⭐⭐⭐ = 锐度+美学双达标（3星）
    └─ 🏆 精选旗标 = 3星中美学+锐度双排名Top 10%交集
  • ⭐⭐ = 锐度或美学达标之一（2星）
  • ⭐ = 有鸟但未达标（1星）
  • 0星 = 技术质量太差（置信度/失真/美学/锐度不达标）
  • ❌ = 完全没鸟

准备好了吗？选择目录开始吧！
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1.  慧眼选鸟：AI 鸟类摄影选片工具
  2.  慧眼识鸟：AI 鸟种识别工具 （Mac/Win Lightroom 插件）
  3.  慧眼找鸟：eBird信息检索工具  Web 测试版
  4.  慧眼去星：AI 银河去星软件（Mac Photoshop 插件）
  5.  图忆作品集：Tui Portfolio IOS 手机专用 鸟种统计工具
  6.  镜书：AI 旅游日记写作助手 IOS 手机专用
"""
        self.log(help_text)

    def on_closing(self):
        """窗口关闭事件"""
        if self.worker and self.worker.is_alive():
            if messagebox.askokcancel("退出", "正在处理中，确定要退出吗？"):
                self.worker._stop_event.set()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    if THEME_AVAILABLE:
        root = ThemedTk(theme="arc")
    else:
        root = tk.Tk()

    app = SuperPickyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
