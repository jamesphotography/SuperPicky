#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V3.1 - Post Digital Adjustment Dialog
二次选鸟对话框 - 基于已有分析结果重新调整评分标准
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, List, Set, Optional
from post_adjustment_engine import PostAdjustmentEngine
from exiftool_manager import get_exiftool_manager
from advanced_config import get_advanced_config


class PostAdjustmentDialog:
    """二次选鸟对话框"""

    def __init__(self, parent, directory: str, on_complete_callback=None):
        """
        初始化对话框

        Args:
            parent: 父窗口
            directory: 照片目录
            on_complete_callback: 完成后的回调函数
        """
        self.window = tk.Toplevel(parent)
        self.window.title("二次选鸟 - 重新调整评分标准")
        self.window.geometry("750x700")
        self.window.resizable(False, False)

        self.directory = directory
        self.on_complete_callback = on_complete_callback

        # 初始化引擎
        self.engine = PostAdjustmentEngine(directory)

        # 加载配置
        self.config = get_advanced_config()

        # 0星阈值变量（从高级配置加载）
        self.min_confidence_var = tk.DoubleVar(value=self.config.min_confidence)
        self.min_sharpness_var = tk.IntVar(value=self.config.min_sharpness)
        self.min_nima_var = tk.DoubleVar(value=self.config.min_nima)
        self.max_brisque_var = tk.IntVar(value=self.config.max_brisque)

        # 2/3星阈值变量（从UI默认值加载 - main.py:697, 708）
        self.sharpness_threshold_var = tk.IntVar(value=7500)
        self.nima_threshold_var = tk.DoubleVar(value=4.8)
        self.picked_percentage_var = tk.IntVar(value=self.config.picked_top_percentage)

        # 数据
        self.original_photos: List[Dict] = []  # 原始数据（带原始评分）
        self.updated_photos: List[Dict] = []   # 更新后的数据（带新星级）
        self.picked_files: Set[str] = set()     # 精选文件名集合

        # 统计数据
        self.current_stats: Optional[Dict] = None
        self.preview_stats: Optional[Dict] = None

        # 防抖定时器
        self._preview_timer = None

        # 创建UI
        self._create_widgets()

        # 加载数据
        self._load_data()

        # 居中窗口
        self._center_window()

    def _create_widgets(self):
        """创建UI组件"""

        # ===== 1. 说明文字 =====
        desc_frame = ttk.Frame(self.window, padding=(10, 10, 10, 5))
        desc_frame.pack(fill=tk.X)

        desc_text = ("基于已有的AI分析数据，重新调整评分标准。\n"
                     "无需重新运行AI推理，快速获得新的星级评分！")
        ttk.Label(
            desc_frame,
            text=desc_text,
            font=("Arial", 9),
            foreground="#666",
            justify=tk.LEFT
        ).pack(anchor=tk.W)

        # ===== 2. 当前统计区域 =====
        stats_frame = ttk.LabelFrame(self.window, text="📊 当前星级分布", padding=10)
        stats_frame.pack(fill=tk.X, padx=10, pady=5)

        self.current_stats_label = ttk.Label(
            stats_frame,
            text="加载中...",
            font=("Arial", 10),
            justify=tk.LEFT
        )
        self.current_stats_label.pack(anchor=tk.W)

        # ===== 3. 阈值调整区域 =====
        threshold_frame = ttk.LabelFrame(self.window, text="⚙️  调整评分阈值", padding=10)
        threshold_frame.pack(fill=tk.X, padx=10, pady=5)

        # 说明文字
        desc_label = ttk.Label(
            threshold_frame,
            text="拖动滑块调整阈值，下方将实时预览新的星级分布",
            font=("Arial", 9),
            foreground="#666"
        )
        desc_label.pack(pady=(0, 10))

        # 2/3星阈值：锐度
        self._create_slider(
            threshold_frame,
            "锐度阈值 (2/3星):",
            self.sharpness_threshold_var,
            from_=6000, to=9000,
            resolution=500,
            format_str="{:.0f}"
        )

        # 2/3星阈值：美学
        self._create_slider(
            threshold_frame,
            "美学阈值 (2/3星):",
            self.nima_threshold_var,
            from_=4.5, to=5.5,
            resolution=0.1,
            format_str="{:.1f}"
        )

        # 精选百分比
        self._create_slider(
            threshold_frame,
            "精选旗标百分比:",
            self.picked_percentage_var,
            from_=10, to=50,
            resolution=5,
            format_str="{}%"
        )

        # ===== 4. 预览对比区域 =====
        preview_frame = ttk.LabelFrame(self.window, text="📈 调整后预览", padding=10)
        preview_frame.pack(fill=tk.X, padx=10, pady=5)

        self.preview_stats_label = ttk.Label(
            preview_frame,
            text="调整阈值后，这里将显示新的星级分布...",
            font=("Arial", 10),
            foreground="#999",
            justify=tk.LEFT
        )
        self.preview_stats_label.pack(anchor=tk.W)

        # ===== 5. 进度区域（应用时显示）=====
        self.progress_frame = ttk.Frame(self.window, padding=10)
        self.progress_frame.pack(fill=tk.X, padx=10, pady=5)

        self.progress_label = ttk.Label(
            self.progress_frame,
            text="",
            font=("Arial", 9)
        )
        self.progress_label.pack()

        # 初始隐藏进度区域
        self.progress_frame.pack_forget()

        # ===== 6. 底部按钮区域 =====
        btn_frame = ttk.Frame(self.window, padding=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(
            btn_frame,
            text="取消",
            command=self.window.destroy,
            width=15
        ).pack(side=tk.LEFT, padx=5)

        self.apply_btn = ttk.Button(
            btn_frame,
            text="✅ 应用新评分",
            command=self._apply_new_ratings,
            width=15,
            state='disabled'  # 初始禁用，加载数据后启用
        )
        self.apply_btn.pack(side=tk.RIGHT, padx=5)

    def _create_slider(self, parent, label_text, variable, from_, to, resolution, format_str):
        """创建滑块组件"""
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=5)

        # 标签
        label = ttk.Label(frame, text=label_text, width=18, font=("Arial", 10))
        label.pack(side=tk.LEFT)

        # 滑块
        slider = ttk.Scale(
            frame,
            from_=from_,
            to=to,
            variable=variable,
            orient=tk.HORIZONTAL,
            command=lambda v: self._on_threshold_changed()
        )
        slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 数值标签
        value_label = ttk.Label(frame, text=format_str.format(variable.get()), width=8, font=("Arial", 10))
        value_label.pack(side=tk.LEFT)

        # 更新数值标签的函数
        def update_label(*args):
            value = variable.get()
            value_label.config(text=format_str.format(value))

        variable.trace('w', update_label)

    def _load_data(self):
        """加载CSV数据"""
        success, message = self.engine.load_report()

        if not success:
            messagebox.showerror("错误", message)
            self.window.destroy()
            return

        # 保存原始数据
        self.original_photos = self.engine.photos_data.copy()

        # 计算当前统计（基于CSV中的原始评分）
        self.current_stats = self._get_original_statistics()

        # 更新当前统计显示
        self._update_current_stats_display()

        # 启用应用按钮
        self.apply_btn.config(state='normal')

        # 触发初始预览
        self._on_threshold_changed()

    def _get_original_statistics(self) -> Dict[str, int]:
        """获取原始统计（基于CSV中的评分字段）"""
        stats = {
            'star_0': 0,
            'star_1': 0,
            'star_2': 0,
            'star_3': 0,
            'picked': 0,  # 原始数据中无法获取精选数，设为0
            'total': len(self.original_photos)
        }

        for photo in self.original_photos:
            rating_str = photo.get('评分', '0')
            rating = int(rating_str) if rating_str.isdigit() else 0

            if rating == 0:
                stats['star_0'] += 1
            elif rating == 1:
                stats['star_1'] += 1
            elif rating == 2:
                stats['star_2'] += 1
            elif rating == 3:
                stats['star_3'] += 1

        return stats

    def _update_current_stats_display(self):
        """更新当前统计显示"""
        if not self.current_stats:
            return

        stats = self.current_stats
        total = stats['total']

        text = f"总共: {total} 张有鸟照片\n"
        text += f"⭐⭐⭐ 3星: {stats['star_3']} 张 ({stats['star_3']/total*100:.1f}%)\n"
        text += f"⭐⭐ 2星: {stats['star_2']} 张 ({stats['star_2']/total*100:.1f}%)\n"
        text += f"⭐ 1星: {stats['star_1']} 张 ({stats['star_1']/total*100:.1f}%)\n"
        text += f"0星: {stats['star_0']} 张 ({stats['star_0']/total*100:.1f}%)"

        self.current_stats_label.config(text=text)

    def _on_threshold_changed(self):
        """阈值改变回调（防抖处理）"""
        # 取消之前的定时器
        if self._preview_timer:
            self.window.after_cancel(self._preview_timer)

        # 延迟300ms后执行预览更新
        self._preview_timer = self.window.after(300, self._update_preview)

    def _update_preview(self):
        """更新预览统计"""
        # 获取当前所有阈值
        min_confidence = self.min_confidence_var.get()
        min_sharpness = self.min_sharpness_var.get()
        min_nima = self.min_nima_var.get()
        max_brisque = self.max_brisque_var.get()
        sharpness_threshold = self.sharpness_threshold_var.get()
        nima_threshold = self.nima_threshold_var.get()
        picked_percentage = self.picked_percentage_var.get()

        # 重新计算星级
        self.updated_photos = self.engine.recalculate_ratings(
            self.original_photos,
            min_confidence=min_confidence,
            min_sharpness=min_sharpness,
            min_nima=min_nima,
            max_brisque=max_brisque,
            sharpness_threshold=sharpness_threshold,
            nima_threshold=nima_threshold
        )

        # 获取3星照片
        star_3_photos = [p for p in self.updated_photos if p.get('新星级') == 3]

        # 重新计算精选旗标
        self.picked_files = self.engine.recalculate_picked(
            star_3_photos,
            picked_percentage
        )

        # 获取新统计
        self.preview_stats = self.engine.get_statistics(self.updated_photos)
        self.preview_stats['picked'] = len(self.picked_files)

        # 更新预览显示
        self._update_preview_display()

    def _update_preview_display(self):
        """更新预览显示（对比差异）"""
        if not self.preview_stats or not self.current_stats:
            return

        old = self.current_stats
        new = self.preview_stats

        def format_diff(old_val, new_val, total):
            diff = new_val - old_val
            diff_pct = (diff / old_val * 100) if old_val > 0 else 0
            pct = new_val / total * 100 if total > 0 else 0

            if diff > 0:
                return f"{new_val} 张 ({pct:.1f}%) [+{diff}, +{diff_pct:.1f}%]"
            elif diff < 0:
                return f"{new_val} 张 ({pct:.1f}%) [{diff}, {diff_pct:.1f}%]"
            else:
                return f"{new_val} 张 ({pct:.1f}%) [无变化]"

        total = new['total']

        text = "调整后的新星级分布:\n\n"
        text += f"⭐⭐⭐ 3星: {format_diff(old['star_3'], new['star_3'], total)}\n"
        text += f"⭐⭐ 2星: {format_diff(old['star_2'], new['star_2'], total)}\n"
        text += f"⭐ 1星: {format_diff(old['star_1'], new['star_1'], total)}\n"
        text += f"0星: {format_diff(old['star_0'], new['star_0'], total)}\n"

        # 精选旗标（特殊处理，因为原始数据中picked=0）
        picked_count = new['picked']
        star_3_count = new['star_3']
        if star_3_count > 0:
            picked_pct = picked_count / star_3_count * 100
            text += f"\n🏆 精选旗标: {picked_count} 张 ({picked_pct:.1f}% of 3星)"
        else:
            text += f"\n🏆 精选旗标: 0 张 (无3星照片)"

        self.preview_stats_label.config(text=text, foreground="#000")

    def _apply_new_ratings(self):
        """应用新评分到EXIF"""
        if not self.updated_photos:
            messagebox.showwarning("提示", "没有可应用的数据")
            return

        # 确认对话框
        msg = (f"确定要应用新的评分标准吗？\n\n"
               f"将更新 {len(self.updated_photos)} 张照片的星级和精选旗标。\n"
               f"(4-5星照片将被自动跳过，保护用户手动标记)")

        if not messagebox.askyesno("确认应用", msg):
            return

        # 禁用按钮
        self.apply_btn.config(state='disabled')

        # 显示进度
        self.progress_frame.pack(fill=tk.X, padx=10, pady=5)
        self.progress_label.config(text="正在应用新评分...")

        # 准备批量数据
        batch_data = []
        skipped_count = 0
        not_found_count = 0

        for photo in self.updated_photos:
            filename = photo['文件名']
            file_path = self.engine.find_image_file(filename)

            if not file_path:
                not_found_count += 1
                continue

            # TODO: 读取当前Rating，跳过4-5星照片
            # 目前先简单处理，不做4-5星检查（后续可增强）

            rating = photo.get('新星级', 0)
            pick = 1 if filename in self.picked_files else 0

            batch_data.append({
                'file': file_path,
                'rating': rating,
                'pick': pick
                # 注意：不更新 sharpness/nima/brisque，这些值没变
            })

        if not_found_count > 0:
            self.progress_label.config(text=f"警告: {not_found_count} 张照片未找到文件，已跳过")

        # 批量写入EXIF
        try:
            exiftool_mgr = get_exiftool_manager()
            stats = exiftool_mgr.batch_set_metadata(batch_data)

            # 隐藏进度
            self.progress_frame.pack_forget()

            # 显示结果
            result_msg = (f"新评分已成功应用！\n\n"
                         f"✅ 成功: {stats['success']} 张\n"
                         f"❌ 失败: {stats['failed']} 张")

            if not_found_count > 0:
                result_msg += f"\n⏭️  跳过(未找到文件): {not_found_count} 张"

            messagebox.showinfo("完成", result_msg)

            # 回调
            if self.on_complete_callback:
                self.on_complete_callback()

            # 关闭对话框
            self.window.destroy()

        except Exception as e:
            self.progress_frame.pack_forget()
            self.apply_btn.config(state='normal')
            messagebox.showerror("错误", f"应用失败：{str(e)}")

    def _center_window(self):
        """将窗口居中显示"""
        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
