#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky V3.1 - 高级设置对话框
"""

import tkinter as tk
from tkinter import ttk, messagebox
from advanced_config import get_advanced_config


class AdvancedSettingsDialog:
    """高级设置对话框"""

    def __init__(self, parent):
        self.parent = parent
        self.config = get_advanced_config()
        self.dialog = None
        self.vars = {}  # 存储所有变量

    def show(self):
        """显示对话框"""
        # 创建顶层窗口
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("高级设置")
        self.dialog.geometry("550x500")
        self.dialog.resizable(False, False)

        # 居中显示
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # 创建Notebook（选项卡）
        notebook = ttk.Notebook(self.dialog)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tab 1: 评分阈值
        rating_frame = ttk.Frame(notebook, padding=15)
        notebook.add(rating_frame, text="评分阈值")
        self._create_rating_tab(rating_frame)

        # Tab 2: 输出设置
        output_frame = ttk.Frame(notebook, padding=15)
        notebook.add(output_frame, text="输出设置")
        self._create_output_tab(output_frame)

        # 底部按钮
        self._create_buttons()

        # 加载当前配置
        self._load_current_config()

    def _create_rating_tab(self, parent):
        """创建评分阈值选项卡"""
        # 说明文字
        desc = ttk.Label(parent, text="调整评分的硬编码阈值（影响0星判定，-1星仅用于完全没鸟）",
                        font=("Arial", 10), foreground="#666")
        desc.pack(pady=(0, 15))

        # AI置信度阈值
        self._create_slider_setting(
            parent,
            key="min_confidence",
            label="AI置信度最低阈值:",
            description="低于此值将被判定为0星（技术质量差）",
            from_=0.3, to=0.7, resolution=0.05,
            default=0.5,
            format_func=lambda v: f"{v:.2f} ({int(v*100)}%)"
        )

        # 锐度最低阈值
        self._create_slider_setting(
            parent,
            key="min_sharpness",
            label="锐度最低阈值:",
            description="低于此值将被判定为0星（技术质量差）",
            from_=2000, to=6000, resolution=100,
            default=4000,
            format_func=lambda v: f"{int(v)}"
        )

        # 美学最低阈值
        self._create_slider_setting(
            parent,
            key="min_nima",
            label="摄影美学最低阈值:",
            description="低于此值将被判定为0星（技术质量差）",
            from_=3.0, to=5.0, resolution=0.1,
            default=4.0,
            format_func=lambda v: f"{v:.1f}"
        )

        # 噪点最高阈值
        self._create_slider_setting(
            parent,
            key="max_brisque",
            label="画面噪点最高阈值:",
            description="高于此值将被判定为0星（技术质量差）",
            from_=20, to=50, resolution=1,
            default=30,
            format_func=lambda v: f"{int(v)}"
        )

    def _create_output_tab(self, parent):
        """创建输出设置选项卡"""
        # 说明文字
        desc = ttk.Label(parent, text="配置输出和日志相关设置",
                        font=("Arial", 10), foreground="#666")
        desc.pack(pady=(0, 15))

        # 精选旗标Top百分比
        self._create_slider_setting(
            parent,
            key="picked_top_percentage",
            label="精选旗标Top百分比:",
            description="3星照片中，美学+锐度双排名都在此百分比内的设为精选",
            from_=5, to=20, resolution=5,
            default=10,
            format_func=lambda v: f"{int(v)}%"
        )

        # CSV报告
        csv_frame = ttk.LabelFrame(parent, text="CSV报告", padding=10)
        csv_frame.pack(fill=tk.X, pady=5)

        self.vars["save_csv"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(csv_frame, text="保存CSV报告文件 (report.csv)",
                       variable=self.vars["save_csv"]).pack(anchor=tk.W)
        ttk.Label(csv_frame, text="CSV包含所有照片的详细评分数据",
                 font=("Arial", 9), foreground="#888").pack(anchor=tk.W, padx=20)

        # 日志详细程度
        log_frame = ttk.LabelFrame(parent, text="日志详细程度", padding=10)
        log_frame.pack(fill=tk.X, pady=5)

        self.vars["log_level"] = tk.StringVar(value="detailed")
        ttk.Radiobutton(log_frame, text="详细 - 显示每张照片的评分详情",
                       variable=self.vars["log_level"], value="detailed").pack(anchor=tk.W)
        ttk.Radiobutton(log_frame, text="简单 - 只显示处理进度和统计",
                       variable=self.vars["log_level"], value="simple").pack(anchor=tk.W)

        # 语言设置（灰色显示，后续实现）
        lang_frame = ttk.LabelFrame(parent, text="语言设置（开发中）", padding=10)
        lang_frame.pack(fill=tk.X, pady=5)

        ttk.Label(lang_frame, text="🚧 多语言功能将在后续版本实现",
                 font=("Arial", 9), foreground="#999").pack(anchor=tk.W)

    def _create_slider_setting(self, parent, key, label, description, from_, to, resolution, default, format_func):
        """创建滑块设置项"""
        frame = ttk.LabelFrame(parent, text=label, padding=10)
        frame.pack(fill=tk.X, pady=5)

        # 描述文字
        ttk.Label(frame, text=description, font=("Arial", 9),
                 foreground="#888").pack(anchor=tk.W)

        # 滑块和值显示
        slider_frame = ttk.Frame(frame)
        slider_frame.pack(fill=tk.X, pady=(5, 0))

        self.vars[key] = tk.DoubleVar(value=default)

        slider = ttk.Scale(slider_frame, from_=from_, to=to,
                          variable=self.vars[key], orient=tk.HORIZONTAL)
        slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        value_label = ttk.Label(slider_frame, text=format_func(default),
                               width=12, font=("Arial", 10, "bold"))
        value_label.pack(side=tk.LEFT)

        # 更新标签的回调
        def update_label(*args):
            value_label.configure(text=format_func(self.vars[key].get()))

        self.vars[key].trace_add('write', update_label)

    def _create_buttons(self):
        """创建底部按钮"""
        btn_frame = ttk.Frame(self.dialog, padding=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # 恢复默认值
        ttk.Button(btn_frame, text="恢复默认值",
                  command=self._reset_to_default).pack(side=tk.LEFT, padx=5)

        # 右侧按钮
        right_buttons = ttk.Frame(btn_frame)
        right_buttons.pack(side=tk.RIGHT)

        ttk.Button(right_buttons, text="取消",
                  command=self.dialog.destroy).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_buttons, text="保存",
                  command=self._save_settings).pack(side=tk.LEFT, padx=5)

    def _load_current_config(self):
        """加载当前配置到界面"""
        self.vars["min_confidence"].set(self.config.min_confidence)
        self.vars["min_sharpness"].set(self.config.min_sharpness)
        self.vars["min_nima"].set(self.config.min_nima)
        self.vars["max_brisque"].set(self.config.max_brisque)
        self.vars["picked_top_percentage"].set(self.config.picked_top_percentage)
        self.vars["save_csv"].set(self.config.save_csv)
        self.vars["log_level"].set(self.config.log_level)

    def _reset_to_default(self):
        """恢复默认设置"""
        if messagebox.askyesno("确认", "确定要恢复所有默认设置吗？"):
            self.config.reset_to_default()
            self._load_current_config()
            messagebox.showinfo("提示", "已恢复默认设置")

    def _save_settings(self):
        """保存设置"""
        # 更新配置
        self.config.set_min_confidence(self.vars["min_confidence"].get())
        self.config.set_min_sharpness(self.vars["min_sharpness"].get())
        self.config.set_min_nima(self.vars["min_nima"].get())
        self.config.set_max_brisque(self.vars["max_brisque"].get())
        self.config.set_picked_top_percentage(self.vars["picked_top_percentage"].get())
        self.config.set_save_csv(self.vars["save_csv"].get())
        self.config.set_log_level(self.vars["log_level"].get())

        # 保存到文件
        if self.config.save():
            messagebox.showinfo("成功", "设置已保存！\n重新处理照片时将使用新设置。")
            self.dialog.destroy()
        else:
            messagebox.showerror("错误", "保存设置失败！")
