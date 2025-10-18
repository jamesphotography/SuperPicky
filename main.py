#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 简化版 (Pure Tkinter, 无PyQt依赖)
Version: 3.0.1
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import csv
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from find_bird_util import reset, raw_to_jpeg
from ai_model import load_yolo_model, detect_and_draw_birds
from utils import write_to_csv, log_message
from exiftool_manager import get_exiftool_manager
from temp_file_manager import get_temp_manager

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
    print("警告: 需要安装 Pillow 才能显示预览 (pip install Pillow)")


class WorkerThread(threading.Thread):
    """处理线程"""

    def __init__(self, dir_path, ui_settings, progress_callback, finished_callback, log_callback, preview_callback=None, work_dir=None, enable_preview=True):
        super().__init__(daemon=True)
        self.dir_path = dir_path
        self.ui_settings = ui_settings
        self.progress_callback = progress_callback
        self.finished_callback = finished_callback
        self.log_callback = log_callback
        self.preview_callback = preview_callback
        self.work_dir = work_dir  # 临时文件工作目录
        self.enable_preview = enable_preview  # 是否启用实时预览
        self._stop_event = threading.Event()

        # 统计数据
        self.stats = {
            'total': 0,
            'star_3': 0,  # 优选照片（3星+精选）
            'star_2': 0,  # 良好照片（2星）
            'star_1': 0,  # 普通照片（1星）
            'no_bird': 0,  # 无鸟照片（-1星）
            'start_time': 0,  # 开始时间
            'end_time': 0,  # 结束时间
            'total_time': 0,  # 总耗时（秒）
            'avg_time': 0  # 平均每张耗时（毫秒）
        }

    def run(self):
        """执行处理"""
        try:
            self.process_files()
            if self.finished_callback:
                self.finished_callback(self.stats)  # 传递统计数据
        except Exception as e:
            self.log_callback(f"❌ 错误: {e}")

    def process_files(self):
        """处理文件的核心逻辑（从Worker.py复制）"""
        import time

        # 记录开始时间
        start_time = time.time()
        self.stats['start_time'] = start_time

        raw_extensions = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng', '.3fr', 'iiq']
        jpg_extensions = ['.jpg', '.jpeg']

        raw_dict = {}
        jpg_dict = {}
        files_tbr = []

        # ⏱️ 计时点1：扫描文件
        scan_start = time.time()
        # 扫描文件（跳过隐藏文件，如 .DS_Store, ._xxx）
        for filename in os.listdir(self.dir_path):
            # 跳过隐藏文件和系统文件
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

        # 转换RAW文件（并行优化）
        raw_files_to_convert = []
        for key, value in raw_dict.items():
            if key in jpg_dict.keys():
                log_message(f"FILE: [{key}] has raw and jpg files", self.dir_path)
                jpg_dict.pop(key)
                continue
            else:
                raw_file_path = os.path.join(self.dir_path, key + value)
                raw_files_to_convert.append((key, raw_file_path))

        # 并行转换RAW文件
        if raw_files_to_convert:
            # ⏱️ 计时点2：RAW转换
            raw_start = time.time()
            import multiprocessing
            # 使用CPU核心数作为线程池大小（最大4个）
            max_workers = min(4, multiprocessing.cpu_count())
            self.log_callback(f"🔄 开始并行转换 {len(raw_files_to_convert)} 个RAW文件（{max_workers}线程）...")

            def convert_single_raw(args):
                """转换单个RAW文件的辅助函数"""
                key, raw_path = args
                try:
                    raw_to_jpeg(raw_path)
                    return (key, True, None)
                except Exception as e:
                    return (key, False, str(e))

            # 使用ThreadPoolExecutor并行转换
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_raw = {executor.submit(convert_single_raw, args): args for args in raw_files_to_convert}

                # 收集结果
                converted_count = 0
                for future in as_completed(future_to_raw):
                    key, success, error = future.result()
                    if success:
                        files_tbr.append(key + ".jpg")
                        converted_count += 1
                        # 不每张都输出，减少UI刷新开销
                        if converted_count % 5 == 0 or converted_count == len(raw_files_to_convert):
                            self.log_callback(f"  ✅ 已转换 {converted_count}/{len(raw_files_to_convert)} 张")
                    else:
                        self.log_callback(f"  ❌ 转换失败: {key}.NEF ({error})")

            raw_time = (time.time() - raw_start) * 1000
            avg_raw_time = raw_time / len(raw_files_to_convert) if len(raw_files_to_convert) > 0 else 0
            self.log_callback(f"⏱️  RAW转换耗时: {raw_time:.0f}ms (平均 {avg_raw_time:.1f}ms/张)\n")

        processed_files = set()
        process_bar = 0

        # 获取ExifTool管理器
        exiftool_mgr = get_exiftool_manager()

        # 批量EXIF写入：收集元数据列表
        exif_batch = []
        BATCH_SIZE = 1  # 每1张照片立即写入EXIF（v3.0.1修复）

        # ⏱️ 计时点3：加载模型
        model_start = time.time()
        self.log_callback("🤖 加载AI模型...")
        model = load_yolo_model()
        model_time = (time.time() - model_start) * 1000
        self.log_callback(f"⏱️  模型加载耗时: {model_time:.0f}ms")

        total_files = len(files_tbr)
        self.log_callback(f"📁 共 {total_files} 个文件待处理\n")

        # ⏱️ 计时点4：AI检测总耗时
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

            # 更新进度（每5张或每5%更新一次，减少UI刷新开销）
            should_update_progress = (
                process_bar % 5 == 0 or  # 每5张更新一次
                process_bar == total_files or  # 最后一张必须更新
                process_bar == 1  # 第一张必须更新
            )
            if should_update_progress:
                progress = int((process_bar / total_files) * 100)
                self.progress_callback(progress)

            self.log_callback(f"[{process_bar}/{total_files}] 处理: {filename}")
            file_prefix, file_ext = os.path.splitext(filename)
            filepath = os.path.join(self.dir_path, filename)

            if not os.path.exists(filepath):
                self.log_callback(f"  ❌ 文件不存在: {filename}")
                continue

            # 运行AI检测（传递预览回调，使用work_dir作为crop输出目录）
            # 只有在启用预览时才传递preview_callback
            try:
                result = detect_and_draw_birds(filepath, model, None, self.dir_path, self.ui_settings,
                                              crop_temp_dir=str(self.work_dir) if self.work_dir else None,
                                              preview_callback=self.preview_callback if self.enable_preview else None)
                if result is None:
                    self.log_callback(f"  ⚠️  无法处理: {filename} (AI推理失败)", "error")
                    continue
            except Exception as e:
                self.log_callback(f"  ❌ 处理异常: {filename} - {str(e)}", "error")
                continue

            detected, selected, confidence, sharpness, nima, brisque = result[0], result[1], result[2], result[3], result[4], result[5]

            # 获取RAW文件路径
            raw_file_path = None
            if file_prefix in raw_dict:
                raw_extension = raw_dict[file_prefix]
                raw_file_path = os.path.join(self.dir_path, file_prefix + raw_extension)

            # 构建IQA评分显示文本
            iqa_text = ""
            if nima is not None:
                iqa_text += f", NIMA:{nima:.2f}"
            if brisque is not None:
                iqa_text += f", BRISQUE:{brisque:.2f}"

            # 设置评分（新逻辑：3星/2星/1星/-1星）
            if selected:
                rating, pick = 3, 1
                self.stats['star_3'] += 1
                self.log_callback(f"  优秀照片 -> 3星 + 精选 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "success")
            elif detected and confidence >= 0.5 and sharpness >= 50:
                rating, pick = 2, 0
                self.stats['star_2'] += 1
                self.log_callback(f"  良好照片 -> 2星 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "info")
            elif detected:
                rating, pick = 1, 0
                self.stats['star_1'] += 1
                self.log_callback(f"  普通照片 -> 1星 (AI:{confidence:.2f}, 锐度:{sharpness:.1f}{iqa_text})", "warning")
            else:
                rating, pick = -1, -1
                self.stats['no_bird'] += 1
                self.log_callback(f"  无鸟照片 -> 已拒绝", "error")

            self.stats['total'] += 1

            # 收集EXIF元数据（批量写入优化）
            if raw_file_path and os.path.exists(raw_file_path):
                exif_batch.append({
                    'file': raw_file_path,
                    'rating': rating,
                    'pick': pick,
                    'sharpness': sharpness
                })

                # 达到批量大小时，执行批量写入
                if len(exif_batch) >= BATCH_SIZE:
                    self.log_callback(f"\n📦 批量写入EXIF ({len(exif_batch)}张)...")
                    batch_stats = exiftool_mgr.batch_set_metadata(exif_batch)
                    if batch_stats['failed'] > 0:
                        self.log_callback(f"  ⚠️  {batch_stats['failed']} 张照片EXIF写入失败")
                    exif_batch.clear()

        # 处理剩余的EXIF元数据（不足一批的部分）
        if exif_batch:
            self.log_callback(f"\n📦 批量写入EXIF ({len(exif_batch)}张)...")
            batch_stats = exiftool_mgr.batch_set_metadata(exif_batch)
            if batch_stats['failed'] > 0:
                self.log_callback(f"  ⚠️  {batch_stats['failed']} 张照片EXIF写入失败")
            exif_batch.clear()

        # ⏱️ 计时点5：AI检测总耗时
        ai_total_time = (time.time() - ai_total_start) * 1000
        avg_ai_time = ai_total_time / total_files if total_files > 0 else 0
        self.log_callback(f"\n⏱️  AI检测总耗时: {ai_total_time:.0f}ms (平均 {avg_ai_time:.1f}ms/张)")

        # ⏱️ 计时点6：移动临时JPG到work_dir（用于历史回看）
        cleanup_start = time.time()
        self.log_callback("\n🧹 整理临时文件...")
        moved_jpg = 0
        for filename in files_tbr:
            file_prefix, file_ext = os.path.splitext(filename)
            # 只处理RAW转换的JPG文件
            if file_prefix in raw_dict and file_ext.lower() in ['.jpg', '.jpeg']:
                jpg_path = os.path.join(self.dir_path, filename)
                dest_path = os.path.join(str(self.work_dir), filename)

                if os.path.exists(jpg_path):
                    try:
                        # 移动到work_dir而不是删除（用于历史回看）
                        import shutil
                        shutil.move(jpg_path, dest_path)
                        moved_jpg += 1
                    except:
                        # 如果移动失败，尝试删除
                        try:
                            os.remove(jpg_path)
                        except:
                            pass

        cleanup_time = (time.time() - cleanup_start) * 1000
        self.log_callback(f"✅ 已移动 {moved_jpg} 个临时JPG到预览目录")
        self.log_callback(f"⏱️  文件整理耗时: {cleanup_time:.0f}ms")

        # 不再删除Crop图片和临时JPG（改为隐藏文件，由reset功能统一清理）

        # 记录结束时间并计算统计数据
        end_time = time.time()
        self.stats['end_time'] = end_time
        self.stats['total_time'] = end_time - start_time

        # 计算平均每张照片的处理时间（排除无效照片）
        if self.stats['total'] > 0:
            self.stats['avg_time'] = self.stats['total_time'] / self.stats['total']  # 单位：秒

        self.progress_callback(100)

    def stop(self):
        """停止处理"""
        self._stop_event.set()


class SuperPickyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SuperPicky V3.0.1 - 慧眼选鸟")
        self.root.geometry("1200x750")  # 加宽以容纳预览面板

        # 设置图标（Tkinter在macOS上使用PNG）
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
        self.preview_photo = None  # 保持图片引用，避免被垃圾回收
        self.preview_photo2 = None  # 第二张预览图片引用

        # 临时文件管理器
        self.temp_manager = get_temp_manager()
        self.work_dir = None  # 当前工作目录

        # 预览历史记录
        self.preview_history = []  # 存储所有处理过的照片信息
        self.current_preview_index = -1  # 当前显示的索引

        # 启动时不再自动清理临时文件（保留历史记录）
        # 用户可以通过"清理临时文件"按钮手动清理
        pass

        self.create_widgets()

        # 绑定键盘快捷键
        self.root.bind('<Left>', lambda e: self.show_prev_preview())
        self.root.bind('<Right>', lambda e: self.show_next_preview())

        # 绑定窗口大小变化事件
        self.root.bind('<Configure>', self.on_window_resize)
        self.last_resize_time = 0

        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 加载默认预览图片
        self.load_default_preview()

        # 显示初始帮助信息
        self.show_initial_help()

    def create_widgets(self):
        # 创建主容器（左右分栏）
        main_container = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_container.pack(fill=tk.BOTH, expand=True)

        # 左侧面板（控制区）
        left_frame = ttk.Frame(main_container)
        main_container.add(left_frame, weight=3)

        # 右侧面板（预览区）
        right_frame = ttk.Frame(main_container)
        main_container.add(right_frame, weight=2)

        # 在左侧创建控制界面
        self.create_control_panel(left_frame)

        # 在右侧创建预览面板
        self.create_preview_panel(right_frame)

    def create_control_panel(self, parent):
        """创建左侧控制面板"""
        # 标题
        title = ttk.Label(
            parent,
            text="慧眼选鸟，选片照样爽",
            font=("Arial", 16, "bold")
        )
        title.pack(pady=10)

        # 目录选择
        dir_frame = ttk.LabelFrame(parent, text="选择照片目录", padding=10)
        dir_frame.pack(fill=tk.X, padx=10, pady=5)

        self.dir_entry = ttk.Entry(dir_frame, font=("Arial", 11))
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        # 启用拖拽支持(macOS)
        self._setup_drag_drop()

        ttk.Button(dir_frame, text="浏览", command=self.browse_directory, width=10).pack(side=tk.LEFT)

        # 参数设置
        settings_frame = ttk.LabelFrame(parent, text="优选照片设置", padding=10)
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        # 选鸟置信度（50%-100%）
        ai_frame = ttk.Frame(settings_frame)
        ai_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ai_frame, text="选鸟置信度:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.ai_var = tk.IntVar(value=80)
        self.ai_slider = ttk.Scale(ai_frame, from_=50, to=100, variable=self.ai_var, orient=tk.HORIZONTAL)
        self.ai_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.ai_label = ttk.Label(ai_frame, text="80%", width=6, font=("Arial", 11))
        self.ai_label.pack(side=tk.LEFT)
        self.ai_slider.configure(command=lambda v: self.ai_label.configure(text=f"{int(float(v))}%"))

        # 鸟面积占比（最大25%）
        ratio_frame = ttk.Frame(settings_frame)
        ratio_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ratio_frame, text="鸟面积占比:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.ratio_var = tk.DoubleVar(value=2.0)
        self.ratio_slider = ttk.Scale(ratio_frame, from_=0.5, to=25, variable=self.ratio_var, orient=tk.HORIZONTAL)
        self.ratio_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.ratio_label = ttk.Label(ratio_frame, text="2.0%", width=6, font=("Arial", 11))
        self.ratio_label.pack(side=tk.LEFT)
        self.ratio_slider.configure(command=lambda v: self.ratio_label.configure(text=f"{float(v):.1f}%"))

        # 鸟锐度阈值（默认100，最大200）
        sharp_frame = ttk.Frame(settings_frame)
        sharp_frame.pack(fill=tk.X, pady=5)
        ttk.Label(sharp_frame, text="鸟锐度阈值:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.sharp_var = tk.IntVar(value=2000)  # v3.0.1: 提高默认阈值，适配真实锐度值
        self.sharp_slider = ttk.Scale(sharp_frame, from_=0, to=10000, variable=self.sharp_var, orient=tk.HORIZONTAL)
        self.sharp_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.sharp_label = ttk.Label(sharp_frame, text="2000", width=6, font=("Arial", 11))
        self.sharp_label.pack(side=tk.LEFT)
        self.sharp_slider.configure(command=lambda v: self.sharp_label.configure(text=f"{int(float(v))}"))

        # 锐度归一化模式
        norm_frame = ttk.Frame(settings_frame)
        norm_frame.pack(fill=tk.X, pady=5)
        ttk.Label(norm_frame, text="锐度归一化:", width=14, font=("Arial", 11)).pack(side=tk.LEFT)
        self.norm_var = tk.StringVar(value="原始方差(推荐) - 不惩罚大小")
        norm_options = [
            "原始方差(推荐) - 不惩罚大小",
            "log归一化 - 最轻微惩罚大鸟",
            "gentle归一化 - 轻微惩罚大鸟",
            "sqrt归一化 - 温和惩罚大鸟",
            "linear归一化 - 严重惩罚大鸟"
        ]
        self.norm_combobox = ttk.Combobox(norm_frame, textvariable=self.norm_var, values=norm_options, state='readonly', font=("Arial", 11))
        self.norm_combobox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 进度显示
        progress_frame = ttk.LabelFrame(parent, text="处理进度", padding=10)
        progress_frame.pack(fill=tk.BOTH, padx=10, pady=5, expand=True)

        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(0, 10))

        # 日志框
        log_scroll = ttk.Scrollbar(progress_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(progress_frame, height=12, state='disabled', yscrollcommand=log_scroll.set,
                                font=("Menlo", 13), bg='#1e1e1e', fg='#d4d4d4',
                                spacing1=4, spacing2=2, spacing3=4, padx=8, pady=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll.config(command=self.log_text.yview)

        # 配置日志颜色标签（优化版：3星和无鸟互补，2星1星居中过渡）
        self.log_text.tag_config("success", foreground="#00ff88")  # 亮绿色 - 3星优秀
        self.log_text.tag_config("error", foreground="#ff0066")    # 洋红色 - 无鸟拒绝（与绿色互补）
        self.log_text.tag_config("warning", foreground="#ffaa00")  # 橙黄色 - 1星普通
        self.log_text.tag_config("info", foreground="#00aaff")     # 天蓝色 - 2星良好

        # 控制按钮
        btn_frame = ttk.Frame(parent, padding=10)
        btn_frame.pack(fill=tk.X)

        # 左侧：实时预览开关 + 提示
        preview_container = ttk.Frame(btn_frame)
        preview_container.pack(side=tk.LEFT, fill=tk.X, expand=False)

        self.enable_preview_var = tk.BooleanVar(value=True)  # 默认启用
        preview_checkbox = ttk.Checkbutton(
            preview_container,
            text="实时预览",
            variable=self.enable_preview_var,
            style='TCheckbutton'
        )
        preview_checkbox.pack(side=tk.LEFT, padx=(0, 5))

        # 提示文字（灰色小字）
        ttk.Label(
            preview_container,
            text="💡 大批量照片建议关闭以提速",
            font=("Arial", 9),
            foreground="#888888"
        ).pack(side=tk.LEFT, padx=5)

        # 右侧：按钮组
        button_container = ttk.Frame(btn_frame)
        button_container.pack(side=tk.RIGHT)

        ttk.Label(button_container, text="V3.0.1 - EXIF标记模式", font=("Arial", 9)).pack(side=tk.RIGHT, padx=10)

        self.cleanup_btn = ttk.Button(button_container, text="🧹 清理临时文件", command=self.cleanup_temp_files, width=15)
        self.cleanup_btn.pack(side=tk.RIGHT, padx=5)

        self.reset_btn = ttk.Button(button_container, text="🔄 重置目录", command=self.reset_directory, width=15, state='disabled')
        self.reset_btn.pack(side=tk.RIGHT, padx=5)

        self.start_btn = ttk.Button(button_container, text="▶️  开始处理", command=self.start_processing, width=15)
        self.start_btn.pack(side=tk.RIGHT, padx=5)

    def create_preview_panel(self, parent):
        """创建右侧预览面板"""
        # 图片显示区域（分为上下两部分）
        canvas_frame = ttk.Frame(parent)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 原图预览（上半部分，无标题框）
        original_container = ttk.Frame(canvas_frame)
        original_container.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self.preview_canvas_original = tk.Canvas(original_container, bg='#2d2d2d', highlightthickness=0)
        self.preview_canvas_original.pack(fill=tk.BOTH, expand=True)

        # 导航按钮框架（放在原图下方）
        nav_frame = ttk.Frame(canvas_frame)
        nav_frame.pack(fill=tk.X, pady=5)

        # 上一张按钮（左对齐）
        self.prev_btn = ttk.Button(nav_frame, text="← 上一张", command=self.show_prev_preview, state='disabled')
        self.prev_btn.pack(side=tk.LEFT, padx=5)

        # 下一张按钮（右对齐）
        self.next_btn = ttk.Button(nav_frame, text="下一张 →", command=self.show_next_preview, state='disabled')
        self.next_btn.pack(side=tk.RIGHT, padx=5)

        # 计数器（居中，放在两个按钮之间）
        self.preview_counter = ttk.Label(nav_frame, text="0/0", font=("Arial", 11), foreground="#888888")
        self.preview_counter.pack(expand=True)

        # 滑块框架（放在导航按钮下方）
        slider_frame = ttk.Frame(canvas_frame)
        slider_frame.pack(fill=tk.X, pady=(0, 5))

        # 滑块（水平，范围0到最大索引）
        self.preview_slider = ttk.Scale(
            slider_frame,
            from_=0,
            to=0,  # 初始值，会动态更新
            orient=tk.HORIZONTAL,
            command=self._on_slider_change,
            state='disabled'
        )
        self.preview_slider.pack(fill=tk.X, padx=10)

        # 用于防抖的定时器ID
        self._slider_timer = None

        # Crop预览（下半部分，带蒙版）
        crop_frame = ttk.LabelFrame(canvas_frame, text="鸟类识别（带蒙版）", padding=5)
        crop_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_canvas_crop = tk.Canvas(crop_frame, bg='#2d2d2d', highlightthickness=0)
        self.preview_canvas_crop.pack(fill=tk.BOTH, expand=True)

        # 默认提示文字
        self.preview_canvas_original.create_text(
            200, 100,
            text="等待处理...",
            fill='#888888',
            font=("Arial", 12),
            tags="placeholder"
        )
        self.preview_canvas_crop.create_text(
            200, 100,
            text="等待处理...",
            fill='#888888',
            font=("Arial", 12),
            tags="placeholder"
        )

        # 元数据显示区域
        meta_frame = ttk.LabelFrame(parent, text="照片信息", padding=10)
        meta_frame.pack(fill=tk.X, padx=10, pady=5)

        # 文件名（居中）
        self.preview_filename = ttk.Label(meta_frame, text="--", font=("Arial", 13, "bold"))
        self.preview_filename.pack(anchor=tk.CENTER, pady=2)

        # 分隔线
        ttk.Separator(meta_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 详细信息（网格布局，3列）
        info_grid = ttk.Frame(meta_frame)
        info_grid.pack(fill=tk.X)

        row = 0
        # 置信度
        ttk.Label(info_grid, text="置信度:", font=("Arial", 12)).grid(row=row, column=0, sticky=tk.W, padx=5, pady=3)
        self.preview_confidence = ttk.Label(info_grid, text="--", font=("Arial", 12, "bold"), foreground="#4ec9b0")
        self.preview_confidence.grid(row=row, column=1, sticky=tk.W, padx=5)

        # 锐度
        ttk.Label(info_grid, text="锐度:", font=("Arial", 12)).grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
        self.preview_sharpness = ttk.Label(info_grid, text="--", font=("Arial", 12, "bold"), foreground="#4ec9b0")
        self.preview_sharpness.grid(row=row, column=3, sticky=tk.W, padx=5)

        # 鸟面积
        ttk.Label(info_grid, text="鸟面积:", font=("Arial", 12)).grid(row=row, column=4, sticky=tk.W, padx=5, pady=3)
        self.preview_area = ttk.Label(info_grid, text="--", font=("Arial", 12))
        self.preview_area.grid(row=row, column=5, sticky=tk.W, padx=5)

        # 第二行：NIMA美学 和 BRISQUE技术
        row += 1
        # NIMA美学
        ttk.Label(info_grid, text="NIMA美学:", font=("Arial", 12)).grid(row=row, column=0, sticky=tk.W, padx=5, pady=3)
        self.preview_nima = ttk.Label(info_grid, text="--", font=("Arial", 12, "bold"), foreground="#9b59d0")
        self.preview_nima.grid(row=row, column=1, sticky=tk.W, padx=5)

        # BRISQUE技术
        ttk.Label(info_grid, text="BRISQUE技术:", font=("Arial", 12)).grid(row=row, column=2, sticky=tk.W, padx=5, pady=3)
        self.preview_brisque = ttk.Label(info_grid, text="--", font=("Arial", 12, "bold"), foreground="#d07959")
        self.preview_brisque.grid(row=row, column=3, sticky=tk.W, padx=5)

        # 星级评分（用emoji星星显示，匹配日志颜色）
        self.preview_rating = ttk.Label(meta_frame, text="", font=("Arial", 18))
        self.preview_rating.pack(pady=5)

    def _setup_drag_drop(self):
        """配置拖拽和粘贴支持"""
        try:
            # 尝试导入 tkinterdnd2 用于拖拽支持
            from tkinterdnd2 import DND_FILES, TkinterDnD

            # 如果成功导入，启用拖拽
            def on_drop(event):
                # macOS/Windows 拖拽数据格式可能包含花括号
                data = event.data
                # 清理路径（去除花括号和额外空格）
                if data.startswith('{') and data.endswith('}'):
                    data = data[1:-1]
                data = data.strip()

                # 检查是否为目录
                if os.path.isdir(data):
                    self.directory_path = data
                    self.dir_entry.delete(0, tk.END)
                    self.dir_entry.insert(0, data)
                    self.reset_btn.configure(state='normal')
                    self.log(f"✅ 已拖入目录: {data}\n")
                    self._handle_directory_selection(data)
                else:
                    messagebox.showwarning("警告", "请拖入文件夹（不是文件）！")

            # 为输入框启用拖拽
            self.dir_entry.drop_target_register(DND_FILES)
            self.dir_entry.dnd_bind('<<Drop>>', on_drop)
            # 标记拖拽可用（稍后在show_initial_help中显示）
            self._drag_drop_available = True
        except ImportError:
            # tkinterdnd2 未安装，使用粘贴方案
            self._drag_drop_available = False

        # 无论是否有拖拽，都支持粘贴和回车
        def on_paste_or_enter(event=None):
            """处理粘贴或回车事件"""
            path = self.dir_entry.get().strip()
            # 移除可能的引号
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if path.startswith("'") and path.endswith("'"):
                path = path[1:-1]

            if path and os.path.isdir(path):
                self.directory_path = path
                self.dir_entry.delete(0, tk.END)
                self.dir_entry.insert(0, path)
                self.reset_btn.configure(state='normal')
                self.log(f"✅ 已选择目录: {path}\n")
                self._handle_directory_selection(path)
            elif path:
                messagebox.showwarning("警告", f"目录不存在: {path}")

        # 绑定回车键
        self.dir_entry.bind('<Return>', on_paste_or_enter)
        # 绑定失焦事件（当用户点击其他地方时）
        self.dir_entry.bind('<FocusOut>', lambda e: on_paste_or_enter() if self.dir_entry.get().strip() and not self.directory_path else None)

    def _handle_directory_selection(self, directory):
        """处理目录选择的通用逻辑（用于浏览和拖拽）"""
        # 创建工作目录并尝试加载历史记录
        self.work_dir = self.temp_manager.get_work_dir(directory)

        # 检查是否有历史记录（CSV文件在_tmp目录中）
        csv_path = Path(directory) / "_tmp" / "report.csv"
        if csv_path.exists():
            # 弹窗询问用户
            result = messagebox.askyesnocancel(
                "检测到历史记录",
                f"此目录已有处理记录！\n\n检测到历史文件：\n• CSV报告\n• Crop预览图片\n\n您想要：\n\n【是】- 查看历史记录（保留数据）\n【否】- 重置目录（删除历史，重新处理）\n【取消】- 取消选择目录",
                icon='question'
            )

            if result is None:  # 取消
                self.directory_path = ""
                self.dir_entry.delete(0, tk.END)
                self.reset_btn.configure(state='disabled')
                self.log("❌ 已取消选择目录\n")
                return
            elif result:  # 是 - 查看历史记录
                self.log("📂 检测到历史记录，正在加载...\n", "info")
                self._load_history_from_csv()
                if self.preview_history:
                    self.log(f"✅ 已加载 {len(self.preview_history)} 张照片的历史记录\n", "success")
                    self.log("💡 您可以使用左右箭头键或按钮浏览历史照片\n", "info")
            else:  # 否 - 重置目录
                self.log("🔄 准备重置目录...\n", "warning")
                self.reset_directory()

    def browse_directory(self):
        directory = filedialog.askdirectory(title="选择照片目录")
        if directory:
            self.directory_path = directory
            self.dir_entry.delete(0, tk.END)
            self.dir_entry.insert(0, directory)
            self.reset_btn.configure(state='normal')
            self.log(f"✅ 已选择目录: {directory}\n")

            # 使用通用处理逻辑
            self._handle_directory_selection(directory)

    def log(self, message, tag=None):
        """添加日志（支持颜色标签）"""
        self.log_text.configure(state='normal')
        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state='disabled')

    def show_initial_help(self):
        """显示初始帮助信息"""
        # 判断拖拽功能是否可用
        if hasattr(self, '_drag_drop_available') and self._drag_drop_available:
            input_hint = "  1️⃣ 点击\"浏览\"选择照片目录 或 拖拽文件夹到输入框（支持RAW/JPG）"
        else:
            input_hint = "  1️⃣ 点击\"浏览\"选择照片目录 或 粘贴路径到输入框并按回车（支持RAW/JPG）"

        help_text = f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  欢迎使用 SuperPicky V3.0.1 - 慧眼选鸟 | AI智能筛选鸟类照片
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
使用步骤：
{input_hint}
  2️⃣ 调整筛选参数（可选，推荐默认值）
  3️⃣ 大批量照片建议关闭实时预览0.6秒/张，实时预览大约1.2秒/张
  4️⃣ 点击"▶️ 开始处理"自动识别并评分
  5️⃣ 处理完成后右侧可查看预览，用滑块/方向键快速浏览历史记录

💡 评分规则：⭐⭐⭐+精选=优秀 | ⭐⭐=良好 | ⭐=普通 | 🚫=无鸟
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
        self.log(help_text)

    def update_progress(self, value):
        """更新进度条（线程安全）"""
        self.root.after(0, lambda: self.progress_bar.configure(value=value))

    def thread_safe_log(self, message, tag=None):
        """线程安全的日志"""
        self.root.after(0, lambda: self.log(message, tag))

    def update_preview(self, crop_image_path, jpg_image_path, metadata):
        """更新预览窗口（线程安全）"""
        self.root.after(0, lambda: self._add_to_preview_history(crop_image_path, jpg_image_path, metadata))

    def _add_to_preview_history(self, crop_image_path, jpg_image_path, metadata):
        """添加到预览历史记录（保存文件路径）"""
        if not PIL_AVAILABLE:
            return

        try:
            # 保存到历史记录（crop路径 + jpg路径）
            self.preview_history.append({
                'crop_path': crop_image_path,  # Crop文件路径
                'jpg_path': jpg_image_path,    # JPG文件路径
                'metadata': metadata.copy()
            })

            # 更新到最新的一张
            self.current_preview_index = len(self.preview_history) - 1
            self._display_preview_at_index(self.current_preview_index)

            # 更新导航按钮状态
            self._update_nav_buttons()
        except Exception as e:
            print(f"无法加载预览图片: {e}")

    def _display_preview_at_index(self, index):
        """显示指定索引的预览（原图+Crop对比）"""
        if not PIL_AVAILABLE or index < 0 or index >= len(self.preview_history):
            return

        item = self.preview_history[index]
        crop_path = item.get('crop_path')
        jpg_path = item.get('jpg_path')  # 原图路径
        metadata = item['metadata']

        try:
            # === 显示Crop图片（带蒙版） ===
            if crop_path and os.path.exists(crop_path):
                crop_img = Image.open(crop_path)

                # 获取Canvas尺寸
                crop_width = self.preview_canvas_crop.winfo_width()
                crop_height = self.preview_canvas_crop.winfo_height()

                if crop_width <= 1:
                    crop_width = 400
                if crop_height <= 1:
                    crop_height = 300

                # 调整图片大小
                crop_img.thumbnail((crop_width - 20, crop_height - 20), Image.Resampling.LANCZOS)
                self.preview_photo = ImageTk.PhotoImage(crop_img)

                # 显示Crop图片
                self.preview_canvas_crop.delete("all")
                self.preview_canvas_crop.create_image(
                    crop_width // 2, crop_height // 2,
                    image=self.preview_photo
                )

            # === 显示原图（3:2比例，撑满窗口） ===
            if jpg_path and os.path.exists(jpg_path):
                original_img = Image.open(jpg_path)
                img_w, img_h = original_img.size

                # 获取Canvas尺寸
                orig_width = self.preview_canvas_original.winfo_width()
                orig_height = self.preview_canvas_original.winfo_height()

                if orig_width <= 1:
                    orig_width = 400
                if orig_height <= 1:
                    orig_height = 300

                # 判断是横拍还是竖拍
                is_horizontal = img_w > img_h

                if is_horizontal:
                    # 横拍：宽度撑满
                    scale = orig_width / img_w
                else:
                    # 竖拍：高度撑满
                    scale = orig_height / img_h

                new_w = int(img_w * scale)
                new_h = int(img_h * scale)

                # 调整图片大小
                original_img = original_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                self.preview_photo2 = ImageTk.PhotoImage(original_img)

                # 显示原图（居中）
                self.preview_canvas_original.delete("all")
                self.preview_canvas_original.create_image(
                    orig_width // 2, orig_height // 2,
                    image=self.preview_photo2
                )

            # 更新元数据
            self.preview_filename.config(text=metadata['filename'])
            self.preview_confidence.config(text=f"{metadata['confidence']*100:.1f}%")
            self.preview_sharpness.config(text=f"{metadata['sharpness']:.1f}")
            self.preview_area.config(text=f"{metadata['area_ratio']*100:.2f}%")

            # 更新 IQA 评分（如果存在）
            if 'nima_score' in metadata and metadata['nima_score'] is not None:
                self.preview_nima.config(text=f"{metadata['nima_score']:.2f}/10")
            else:
                self.preview_nima.config(text="--")

            if 'brisque_score' in metadata and metadata['brisque_score'] is not None:
                self.preview_brisque.config(text=f"{metadata['brisque_score']:.2f}/100")
            else:
                self.preview_brisque.config(text="--")

            # 评分显示（使用emoji星星，颜色匹配日志）
            rating = metadata['rating']
            pick = metadata.get('pick', 0)

            if rating == 3:
                stars = "⭐⭐⭐"
                color = "#00ff88"  # 亮绿色 - 3星优秀
            elif rating == 2:
                stars = "⭐⭐"
                color = "#00aaff"  # 天蓝色 - 2星良好
            elif rating == 1:
                stars = "⭐"
                color = "#ffaa00"  # 橙黄色 - 1星普通
            else:
                stars = "❌"
                color = "#ff0066"  # 洋红色 - 无鸟拒绝

            # 精选标记
            pick_text = " 🏆" if pick == 1 else ""
            self.preview_rating.config(text=f"{stars}{pick_text}", foreground=color)

        except FileNotFoundError as e:
            # 文件不存在的友好提示
            error_msg = f"图片文件未找到\n{os.path.basename(jpg_path) if jpg_path else '未知文件'}"

            # 在原图Canvas显示错误
            orig_width = self.preview_canvas_original.winfo_width() or 400
            orig_height = self.preview_canvas_original.winfo_height() or 300
            self.preview_canvas_original.delete("all")
            self.preview_canvas_original.create_text(
                orig_width // 2, orig_height // 2,
                text=error_msg,
                fill='#ff6666',
                font=("Arial", 12),
                justify=tk.CENTER
            )

            # 在Crop Canvas显示错误
            crop_width = self.preview_canvas_crop.winfo_width() or 400
            crop_height = self.preview_canvas_crop.winfo_height() or 300
            self.preview_canvas_crop.delete("all")
            self.preview_canvas_crop.create_text(
                crop_width // 2, crop_height // 2,
                text="识别图片未找到",
                fill='#ff6666',
                font=("Arial", 12)
            )

            print(f"⚠️  预览图片未找到: {e}")

        except Exception as e:
            # 其他错误的友好提示
            error_msg = f"无法加载图片\n{str(e)[:50]}"

            # 在原图Canvas显示错误
            orig_width = self.preview_canvas_original.winfo_width() or 400
            orig_height = self.preview_canvas_original.winfo_height() or 300
            self.preview_canvas_original.delete("all")
            self.preview_canvas_original.create_text(
                orig_width // 2, orig_height // 2,
                text=error_msg,
                fill='#ff8866',
                font=("Arial", 12),
                justify=tk.CENTER
            )

            print(f"❌ 预览更新失败: {e}")

    def _load_history_from_csv(self):
        """从CSV文件加载历史记录"""
        if not self.directory_path or not PIL_AVAILABLE or not self.work_dir:
            return

        # CSV在_tmp目录
        csv_path = Path(self.directory_path) / "_tmp" / "report.csv"
        if not csv_path.exists():
            return

        try:
            # 清空现有历史
            self.preview_history.clear()

            # 先统计总行数（用于显示进度）
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                total_rows = sum(1 for _ in f) - 1  # 减去表头

            self.log(f"📂 正在加载历史记录... (共 {total_rows} 张照片)", "info")

            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                loaded = 0
                skipped = 0

                for row in reader:
                    # 只加载有鸟的照片
                    if row.get('是否有鸟') != '是':
                        skipped += 1
                        continue

                    filename = row['文件名']
                    # 构建Crop文件路径
                    crop_filename = f"Crop_{filename}.jpg"
                    crop_path = self.work_dir / crop_filename

                    # 检查Crop文件是否存在（必须）
                    if not crop_path.exists():
                        continue

                    # 原图路径：优先查找原始目录中的JPG，如果不存在则查找临时目录中的JPG（RAW转换后的）
                    jpg_filename = f"{filename}.jpg"
                    jpg_path = Path(self.directory_path) / jpg_filename

                    # 如果原始目录中没有JPG，说明可能是RAW文件，使用临时转换的JPG（如果存在）
                    if not jpg_path.exists():
                        temp_jpg_path = self.work_dir / jpg_filename
                        if temp_jpg_path.exists():
                            jpg_path = temp_jpg_path
                        else:
                            # 最后尝试查找对应的RAW文件（虽然PIL可能无法直接读取）
                            for raw_ext in ['.NEF', '.CR2', '.CR3', '.ARW', '.RAF', '.ORF', '.RW2', '.PEF', '.DNG', '.3FR', '.IIQ']:
                                raw_path = Path(self.directory_path) / f"{filename}{raw_ext}"
                                if raw_path.exists():
                                    jpg_path = raw_path
                                    break

                    # 解析元数据
                    try:
                        confidence = float(row.get('置信度', 0))
                        sharpness = float(row.get('归一化锐度', 0))
                        area_ratio = float(row.get('鸟占比', '0%').rstrip('%')) / 100
                        centered = row.get('居中') == '是'

                        # 根据星等判断rating和pick
                        stars = row.get('星等', '❌')
                        if '⭐⭐⭐' in stars:
                            rating = 3
                            pick = 1
                        elif '⭐⭐' in stars:
                            rating = 2
                            pick = 0
                        elif '⭐' in stars:
                            rating = 1
                            pick = 0
                        else:
                            rating = 0
                            pick = 0

                        metadata = {
                            'filename': f"{filename}.jpg",  # 原始文件名
                            'confidence': confidence,
                            'sharpness': sharpness,
                            'area_ratio': area_ratio,
                            'centered': centered,
                            'rating': rating,
                            'pick': pick
                        }

                        self.preview_history.append({
                            'crop_path': str(crop_path),
                            'jpg_path': str(jpg_path),
                            'metadata': metadata
                        })

                        loaded += 1

                        # 每50张更新一次进度（避免过于频繁）
                        if loaded % 50 == 0:
                            self.log(f"  已加载 {loaded} 张...", "info")

                    except Exception as e:
                        skipped += 1
                        continue

            # 如果加载了历史记录，显示最后一张
            if self.preview_history:
                self.current_preview_index = len(self.preview_history) - 1
                self._display_preview_at_index(self.current_preview_index)
                self._update_nav_buttons()
                self.log(f"✅ 已加载 {loaded} 张照片历史记录 (跳过 {skipped} 张)", "success")
            else:
                self.log(f"⚠️  未找到可显示的历史记录 (跳过 {skipped} 张)", "warning")

        except Exception as e:
            self.log(f"❌ 历史记录加载失败: {e}", "error")

    def start_processing(self):
        if not self.directory_path:
            messagebox.showwarning("警告", "请先选择照片目录！")
            return

        if not os.path.exists(self.directory_path):
            messagebox.showerror("错误", f"目录不存在: {self.directory_path}")
            return

        # 统计JPG文件数量（用于智能提示）
        jpg_count = 0
        for filename in os.listdir(self.directory_path):
            if filename.lower().endswith(('.jpg', '.jpeg', '.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.pef', '.dng')):
                jpg_count += 1

        # 如果照片超过100张且启用了实时预览，提示用户
        if jpg_count > 100 and self.enable_preview_var.get():
            result = messagebox.askyesno(
                "性能提示",
                f"检测到 {jpg_count} 张照片！\n\n启用实时预览会使处理速度降低约50%。\n\n建议：关闭实时预览以获得最快速度（完成后可查看历史记录）。\n\n是否关闭实时预览？",
                icon='question'
            )
            if result:
                self.enable_preview_var.set(False)
                self.log("⚡ 已自动关闭实时预览以提升处理速度\n", "info")

        # 禁用按钮
        self.start_btn.configure(state='disabled')
        self.reset_btn.configure(state='disabled')

        # 清空日志
        self.log_text.configure(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state='disabled')

        # 清空预览历史
        self.preview_history.clear()
        self.current_preview_index = -1
        self._update_nav_buttons()

        self.progress_bar['value'] = 0
        self.log("开始处理照片...\n", "info")

        # 创建工作目录
        self.work_dir = self.temp_manager.get_work_dir(self.directory_path)
        self.log(f"📂 临时文件目录: {self.work_dir}\n", "info")

        # 显示预览状态
        if not self.enable_preview_var.get():
            self.log("⚡ 实时预览已禁用 - 处理速度更快（完成后可查看历史记录）\n", "info")

        # 写入CSV（暂时还是保存在原目录）
        write_to_csv(None, self.directory_path, True)

        # 将归一化模式文本映射到代码值（提取破折号前的关键词）
        selected_text = self.norm_var.get()
        # 从"原始方差(推荐) - 不惩罚大小"中提取"原始方差(推荐)"
        mode_key = selected_text.split(" - ")[0].strip()

        norm_mapping = {
            "原始方差(推荐)": None,
            "log归一化": "log",
            "gentle归一化": "gentle",
            "sqrt归一化": "sqrt",
            "linear归一化": "linear"
        }
        selected_norm = norm_mapping.get(mode_key, None)

        # 获取设置（[confidence, area, sharpness, center_threshold=15%, save_crop=True, normalization]）
        ui_settings = [
            self.ai_var.get(),          # AI置信度 (0-100)
            self.ratio_var.get(),       # 鸟类占比 (0.5-10)
            self.sharp_var.get(),       # 锐度阈值 (0-300)
            15,                         # 居中阈值硬编码为15%
            True,                       # 总是保存Crop图片（用于预览）
            selected_norm               # 锐度归一化模式
        ]

        # 启动Worker线程
        self.worker = WorkerThread(
            self.directory_path,
            ui_settings,
            self.update_progress,
            self.on_finished,
            self.thread_safe_log,
            self.update_preview,  # 传递预览回调
            self.work_dir,  # 传递工作目录
            self.enable_preview_var.get()  # 传递预览开关状态
        )
        self.worker.start()

    def _format_statistics_report(self, stats):
        """格式化统计报告（包含智能提示和时间统计）"""
        if stats['total'] == 0:
            return "", ""

        # 计算百分比
        star_3_pct = (stats['star_3'] / stats['total']) * 100
        star_2_pct = (stats['star_2'] / stats['total']) * 100
        star_1_pct = (stats['star_1'] / stats['total']) * 100
        no_bird_pct = (stats['no_bird'] / stats['total']) * 100

        # 有鸟照片总数（3星+2星+1星）
        with_bird = stats['star_3'] + stats['star_2'] + stats['star_1']
        with_bird_pct = (with_bird / stats['total']) * 100

        # 时间统计
        total_time = stats.get('total_time', 0)
        avg_time = stats.get('avg_time', 0)

        # 构建报告文本（用于日志窗口）
        log_report = f"\n{'='*50}\n"
        log_report += f"📊 处理统计报告\n"
        log_report += f"{'='*50}\n"
        log_report += f"总共识别：{stats['total']} 张照片\n"
        log_report += f"总耗时：{total_time:.1f} 秒 ({total_time/60:.1f} 分钟)\n"
        log_report += f"平均每张：{avg_time:.2f} 秒\n\n"
        log_report += f"⭐⭐⭐ 优选照片（3星）：{stats['star_3']} 张 ({star_3_pct:.1f}%)\n"
        log_report += f"⭐⭐ 良好照片（2星）：{stats['star_2']} 张 ({star_2_pct:.1f}%)\n"
        log_report += f"⭐ 普通照片（1星）：{stats['star_1']} 张 ({star_1_pct:.1f}%)\n"
        log_report += f"❌ 无鸟照片：{stats['no_bird']} 张 ({no_bird_pct:.1f}%)\n"
        log_report += f"\n有鸟照片总数：{with_bird} 张 ({with_bird_pct:.1f}%)\n"

        # 智能提示（多样化 + 幽默）
        tips = []

        # === 1. 无鸟照片占比提示 ===
        if no_bird_pct > 70:
            tips.append(f"😅 无鸟照片占比 {no_bird_pct:.1f}% ...这是在拍风景吗？建议调整拍摄角度或使用更长焦镜头")
        elif no_bird_pct > 50:
            tips.append(f"🤔 无鸟照片过半 ({no_bird_pct:.1f}%)，小鸟们可能在和你玩躲猫猫！建议提高拍摄耐心")
        elif no_bird_pct > 35:
            tips.append(f"⚠️  无鸟照片占比较高 ({no_bird_pct:.1f}%)，建议拍摄时多注意鸟的位置和距离")
        elif no_bird_pct > 25:
            tips.append(f"💡 无鸟照片占 {no_bird_pct:.1f}%，可以考虑使用鸟鸣引诱或守株待兔策略")

        # === 2. 优选照片占比提示 ===
        if star_3_pct > 30:
            tips.append(f"🏆 优选照片占比 {star_3_pct:.1f}% - 神级表现！你已经是大师级拍鸟人了！")
        elif star_3_pct > 20:
            tips.append(f"🎉 优选照片占比很高 ({star_3_pct:.1f}%)，拍摄质量优秀！可以开摄影展了")
        elif star_3_pct > 15:
            tips.append(f"👏 优选照片占比 {star_3_pct:.1f}% - 相当不错！继续保持这个水准")
        elif star_3_pct > 10:
            tips.append(f"👍 优选照片占比不错 ({star_3_pct:.1f}%)，继续保持！")
        elif star_3_pct > 5:
            tips.append(f"💪 优选照片占比 {star_3_pct:.1f}%，有进步空间，建议关注鸟的清晰度和构图")
        elif star_3_pct > 0:
            tips.append(f"🌱 优选照片占比 {star_3_pct:.1f}%，加油！可以尝试调整拍摄参数（快门速度、ISO）")
        else:
            tips.append(f"😢 本次没有优选照片...别灰心，拍鸟需要耐心和运气，多尝试几次！")

        # === 3. 有鸟照片总占比提示 ===
        if with_bird_pct >= 90:
            tips.append(f"🔥 有鸟照片占比 {with_bird_pct:.1f}% - 命中率爆表！你是鸟类磁铁吗？")
        elif with_bird_pct >= 80:
            tips.append(f"✨ 有鸟照片占比很高 ({with_bird_pct:.1f}%)，命中率出色！")
        elif with_bird_pct >= 70:
            tips.append(f"👌 有鸟照片占比 {with_bird_pct:.1f}%，命中率不错，拍摄效率很高")
        elif with_bird_pct >= 60:
            tips.append(f"📈 有鸟照片占比 {with_bird_pct:.1f}%，达到合格线，继续努力！")

        # === 4. 良好照片（2星）占比提示 ===
        if star_2_pct > 40:
            tips.append(f"💎 良好照片占比 {star_2_pct:.1f}% - 稳定输出！不过可以尝试提升到优选标准")
        elif star_2_pct > 30:
            tips.append(f"✅ 良好照片占比 {star_2_pct:.1f}%，质量稳定，建议关注锐度和鸟的面积占比")

        # === 5. 普通照片（1星）占比提示 ===
        if star_1_pct > 50:
            tips.append(f"📝 普通照片占比过半 ({star_1_pct:.1f}%)，建议提高快门速度以获得更清晰的照片")
        elif star_1_pct > 40:
            tips.append(f"💡 普通照片占 {star_1_pct:.1f}%，可以尝试调整曝光补偿和对焦模式")

        # === 6. 时间效率提示 ===
        if avg_time < 0.5:
            tips.append(f"⚡ 处理速度 {avg_time:.2f}秒/张 - 闪电般的效率！")
        elif avg_time > 2.0:
            tips.append(f"🐌 处理速度 {avg_time:.2f}秒/张，建议关闭实时预览以提速")

        if tips:
            log_report += f"\n{'='*50}\n"
            log_report += "💡 智能提示：\n"
            for tip in tips:
                log_report += f"   {tip}\n"

        log_report += f"{'='*50}\n"

        # 构建弹窗报告（更简洁）
        popup_report = f"📊 处理统计报告\n\n"
        popup_report += f"总共识别：{stats['total']} 张照片\n"
        popup_report += f"总耗时：{total_time:.1f} 秒 ({total_time/60:.1f} 分钟)\n"
        popup_report += f"平均每张：{avg_time:.2f} 秒\n\n"
        popup_report += f"⭐⭐⭐ 优选照片：{stats['star_3']} 张 ({star_3_pct:.1f}%)\n"
        popup_report += f"⭐⭐ 良好照片：{stats['star_2']} 张 ({star_2_pct:.1f}%)\n"
        popup_report += f"⭐ 普通照片：{stats['star_1']} 张 ({star_1_pct:.1f}%)\n"
        popup_report += f"❌ 无鸟照片：{stats['no_bird']} 张 ({no_bird_pct:.1f}%)\n"

        if tips:
            popup_report += f"\n💡 智能提示：\n"
            for tip in tips:
                popup_report += f"{tip}\n"

        return log_report, popup_report

    def on_finished(self, stats=None):
        """处理完成"""
        self.root.after(0, lambda: self.log("\n✅ 处理完成！"))

        # 生成统计报告
        if stats:
            log_report, popup_report = self._format_statistics_report(stats)
            if log_report:
                self.root.after(0, lambda: self.log(log_report))
                self.root.after(0, lambda: messagebox.showinfo("完成", popup_report))
            else:
                self.root.after(0, lambda: messagebox.showinfo("完成", "照片处理完成！"))
        else:
            self.root.after(0, lambda: messagebox.showinfo("完成", "照片处理完成！"))

        # 显示Lightroom导入提示
        self.root.after(0, lambda: self.show_lightroom_guide())

        # 处理完成后，从CSV加载完整历史记录
        self.root.after(0, lambda: self._load_history_from_csv())
        self.root.after(0, lambda: self.start_btn.configure(state='normal'))
        self.root.after(0, lambda: self.reset_btn.configure(state='normal'))

    def reset_directory(self):
        """重置目录：清理临时文件 + 重置EXIF元数据"""
        if not self.directory_path:
            messagebox.showwarning("警告", "请先选择目录！")
            return

        result = messagebox.askyesno(
            "确认重置",
            "确定要重置目录吗？\n\n将会：\n• 删除整个 _tmp 目录（Crop、临时JPG、CSV、日志）\n• 重置 ≤3星 照片的EXIF元数据\n• 保留 4-5星 照片的EXIF不变\n\n⚠️ 此操作不可撤销！"
        )

        if result:
            # 清空日志
            self.log_text.configure(state='normal')
            self.log_text.delete(1.0, tk.END)
            self.log_text.configure(state='disabled')

            self.log("🔄 开始重置目录...")

            def reset_thread():
                # 1. 先清理临时文件（调用cleanup，不显示确认框）
                self.thread_safe_log("📂 步骤1：清理临时文件...")
                self.cleanup_temp_files(show_confirm=False)

                # 等待清理完成（简单延迟，实际应该用事件同步）
                import time
                time.sleep(0.5)

                # 2. 重置EXIF元数据
                self.thread_safe_log("\n🏷️  步骤2：重置EXIF元数据...")
                success = reset(self.directory_path, log_callback=self.thread_safe_log)

                if success:
                    self.root.after(0, lambda: messagebox.showinfo("完成", "目录已完全重置！\n\n• 临时文件已清理\n• EXIF评分已重置"))
                else:
                    self.root.after(0, lambda: messagebox.showerror("错误", "重置失败！"))

            threading.Thread(target=reset_thread, daemon=True).start()

    def cleanup_temp_files(self, show_confirm=True):
        """清理所有临时文件（删除整个_tmp目录），但不重置EXIF"""
        if show_confirm:
            result = messagebox.askyesno(
                "确认清理",
                "确定要清理所有临时文件吗？\n\n将会删除整个 _tmp 目录，包括：\n• 所有 Crop 预览图片\n• 临时JPG文件（RAW转换后的）\n• 处理报告（CSV）\n• 处理日志\n\n保留：\n• EXIF评分和精选标记\n\n⚠️ 此操作不可撤销！"
            )
            if not result:
                return

        self.log("🧹 开始清理临时文件...")

        def cleanup_thread():
            cleaned_files = 0

            # 清理整个_tmp目录（包含Crop图片、临时JPG、CSV、日志）
            if self.work_dir and self.work_dir.exists():
                file_count = len(list(self.work_dir.iterdir()))
                self.temp_manager.clear_work_dir(self.work_dir)
                cleaned_files += file_count
                self.thread_safe_log(f"  ✅ 已删除 _tmp 目录中的 {file_count} 个文件")

            # 清空预览
            self.root.after(0, lambda: self.preview_history.clear())
            self.root.after(0, lambda: setattr(self, 'current_preview_index', -1))
            self.root.after(0, lambda: self._update_nav_buttons())
            self.root.after(0, lambda: self.load_default_preview())

            if show_confirm:
                self.root.after(0, lambda: messagebox.showinfo("完成", f"清理完成！\n\n已删除 _tmp 目录中的 {cleaned_files} 个文件。\nEXIF评分已保留。"))

        threading.Thread(target=cleanup_thread, daemon=True).start()

    def _set_preview_index(self, new_index):
        """
        统一的索引更新入口（同步滑块、按钮、显示）

        Args:
            new_index: 新的索引位置
        """
        # 验证索引范围
        if new_index < 0 or new_index >= len(self.preview_history):
            return

        # 更新索引
        self.current_preview_index = new_index

        # 同步滑块位置（不触发回调）
        self.preview_slider.set(new_index)

        # 更新显示
        self._display_preview_at_index(new_index)
        self._update_nav_buttons()

    def _on_slider_change(self, value):
        """
        滑块值变化时的回调（带防抖）

        Args:
            value: 滑块当前值（字符串）
        """
        # 取消之前的延迟加载
        if self._slider_timer:
            self.root.after_cancel(self._slider_timer)

        # 延迟150ms加载，避免拖动时频繁加载图片
        new_index = int(float(value))

        # 立即更新计数器（不等待延迟）
        total = len(self.preview_history)
        if total > 0:
            self.preview_counter.config(text=f"{new_index + 1}/{total}")

        # 延迟加载图片
        self._slider_timer = self.root.after(
            150,
            lambda: self._set_preview_index_from_slider(new_index)
        )

    def _set_preview_index_from_slider(self, new_index):
        """从滑块设置索引（内部方法，避免循环更新滑块）"""
        if new_index < 0 or new_index >= len(self.preview_history):
            return

        self.current_preview_index = new_index
        self._display_preview_at_index(new_index)
        self._update_nav_buttons()

    def show_prev_preview(self):
        """显示上一张预览"""
        if self.current_preview_index > 0:
            self._set_preview_index(self.current_preview_index - 1)

    def show_next_preview(self):
        """显示下一张预览"""
        if self.current_preview_index < len(self.preview_history) - 1:
            self._set_preview_index(self.current_preview_index + 1)


    def _update_nav_buttons(self):
        """更新导航按钮和滑块状态"""
        total = len(self.preview_history)

        if total == 0:
            # 没有历史记录
            self.prev_btn.config(state='disabled')
            self.next_btn.config(state='disabled')
            self.preview_slider.config(state='disabled')
            self.preview_counter.config(text="0/0")
        else:
            # 更新滑块范围
            self.preview_slider.config(from_=0, to=total - 1, state='normal')

            # 更新计数器
            self.preview_counter.config(text=f"{self.current_preview_index + 1}/{total}")

            # 更新按钮状态
            if self.current_preview_index > 0:
                self.prev_btn.config(state='normal')
            else:
                self.prev_btn.config(state='disabled')

            if self.current_preview_index < total - 1:
                self.next_btn.config(state='normal')
            else:
                self.next_btn.config(state='disabled')

    def on_window_resize(self, event):
        """窗口大小变化时重新绘制预览图片"""
        import time
        current_time = time.time()

        # 防抖：只在最后一次调整后300ms执行
        if current_time - self.last_resize_time < 0.3:
            return

        self.last_resize_time = current_time

        # 重新显示当前预览
        if self.current_preview_index >= 0:
            self.root.after(100, lambda: self._display_preview_at_index(self.current_preview_index))

    def show_lightroom_guide(self):
        """显示Lightroom导入使用指南"""
        guide_text = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📸 Lightroom 使用指南 - 快速导入评分结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
导入步骤：
  1️⃣ 打开Lightroom Classic → 点击"导入" → 选择照片目录" → 导入
  2️⃣ 照片已自动标记：⭐⭐⭐+🏆优秀 | ⭐⭐良好 | ⭐普通 | 🚫无鸟（排除旗标）
  3️⃣ 筛选照片：底部筛选栏"属性" → 星级/旗标筛选 → 按"城市"列排序查看最清晰照片
  4️⃣ 批量处理：筛选出3星+精选照片 → 高效完成后期！

💡 提示：锐度值存于IPTC:City字段（格式000.00-999.99）| CSV报告在_tmp/report.csv
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
        self.log(guide_text)

    def on_closing(self):
        """窗口关闭时的清理逻辑"""
        # 检查是否有正在运行的Worker线程
        if self.worker and self.worker.is_alive():
            result = messagebox.askyesno(
                "确认退出",
                "照片正在处理中，确定要退出吗？\n\n未完成的处理将会中断。",
                icon='warning'
            )
            if not result:
                return

            # 停止Worker线程
            self.log("🛑 正在停止处理...")
            self.worker.stop()

            # 等待线程结束（最多2秒）
            self.worker.join(timeout=2)

            if self.worker.is_alive():
                self.log("⚠️  强制退出，部分任务可能未完成")

        # 销毁窗口
        self.root.destroy()

    def load_default_preview(self):
        """加载默认预览图片"""
        if not PIL_AVAILABLE:
            return

        try:
            # 默认图片路径
            img_dir = os.path.join(os.path.dirname(__file__), "img")
            default_jpg = os.path.join(img_dir, "_Z9w0960.jpg")
            default_crop = os.path.join(img_dir, "Crop__Z9W0960.jpg")

            # 检查文件是否存在
            if not os.path.exists(default_jpg) or not os.path.exists(default_crop):
                return

            # 创建默认元数据
            default_metadata = {
                'filename': '_Z9w0960.jpg',
                'confidence': 0.94,
                'sharpness': 91.7,
                'area_ratio': 0.1201,  # 12.01%
                'centered': True,
                'rating': 2,
                'pick': 0
            }

            # 添加到预览历史
            self.preview_history.append({
                'crop_path': default_crop,
                'jpg_path': default_jpg,
                'metadata': default_metadata
            })

            # 显示默认预览
            self.current_preview_index = 0
            self._display_preview_at_index(0)
            self._update_nav_buttons()

        except Exception as e:
            print(f"加载默认预览失败: {e}")


def main():
    # 使用主题（如果可用）
    if THEME_AVAILABLE:
        root = ThemedTk(theme="arc")  # 现代化主题
    else:
        root = tk.Tk()

    app = SuperPickyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
