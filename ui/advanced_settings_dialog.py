# -*- coding: utf-8 -*-
"""
SuperPicky - 参数设置对话框
顶部标签页布局
"""

import os
import subprocess
import sys

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QPushButton,
    QWidget, QFrame, QRadioButton,
    QButtonGroup, QTabWidget, QCheckBox, QComboBox,
    QListWidget, QListWidgetItem, QFileDialog, QSizePolicy,
    QScrollArea
)
from PySide6.QtCore import Qt, Slot

from advanced_config import get_advanced_config
from tools.i18n import get_i18n
from ui.styles import COLORS, FONTS
from ui.custom_dialogs import StyledMessageBox

class AdvancedSettingsDialog(QDialog):
    """参数设置对话框 - 顶部标签页布局"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_advanced_config()
        self.i18n = get_i18n(self.config.language)

        self.vars = {}

        self._setup_ui()
        self._load_current_config()

    def _setup_ui(self):
        """设置 UI"""
        self.setWindowTitle(self.i18n.t("advanced_settings.window_title"))
        # V4.3 Phase 5: 适当放大默认尺寸，视频处理 tab 内容较多
        # V4.3 Phase 5: enlarged default size to fit the new Video tab.
        self.setMinimumSize(480, 480)
        self.resize(560, 620)
        self.setModal(True)

        # 应用样式
        self._apply_styles()

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 8, 0, 0)
        main_layout.setSpacing(0)

        # 标签页
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background-color: {COLORS['bg_primary']};
            }}
            QTabBar::tab {{
                background-color: {COLORS['bg_card']};
                color: {COLORS['text_secondary']};
                padding: 10px 24px;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 13px;
            }}
            QTabBar::tab:selected {{
                color: {COLORS['text_primary']};
                border-bottom: 2px solid {COLORS['accent']};
            }}
            QTabBar::tab:hover:!selected {{
                color: {COLORS['text_primary']};
                background-color: {COLORS['bg_elevated']};
            }}
        """)

        # 添加标签页
        self.tab_widget.addTab(
            self._create_culling_page(),
            self.i18n.t("advanced_settings.section_selection")
        )
        self.tab_widget.addTab(
            self._create_birdid_page(),
            self.i18n.t("advanced_settings.section_birdid")
        )
        self.tab_widget.addTab(
            self._create_output_page(),
            self.i18n.t("advanced_settings.section_output")
        )
        self.tab_widget.addTab(
            self._create_apps_page(),
            self.i18n.t("advanced_settings.section_apps")
        )
        # V4.3 Phase 5: 视频处理 tab
        # V4.3 Phase 5: Video processing tab
        self.tab_widget.addTab(
            self._create_video_page(),
            self.i18n.t("video_opts.tab")
        )

        main_layout.addWidget(self.tab_widget, 1)

        # 底部按钮区域
        self._create_buttons(main_layout)

    def _apply_styles(self):
        """应用全局样式"""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_primary']};
            }}
            QLabel {{
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
            QPushButton#secondary {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                color: {COLORS['text_secondary']};
            }}
            QPushButton#secondary:hover {{
                border-color: {COLORS['text_muted']};
                color: {COLORS['text_primary']};
            }}
            QPushButton#tertiary {{
                background-color: transparent;
                color: {COLORS['text_tertiary']};
            }}
            QPushButton#tertiary:hover {{
                color: {COLORS['text_secondary']};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {COLORS['bg_input']};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {COLORS['accent']};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                margin: -6px 0;
                background: {COLORS['text_primary']};
                border-radius: 8px;
            }}
            QRadioButton {{
                color: {COLORS['text_secondary']};
                font-size: 13px;
                color: {COLORS['text_secondary']};
                font-size: 13px;
                spacing: 8px;
                padding: 2px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}
            QRadioButton::indicator:unchecked {{
                border: 2px solid {COLORS['text_tertiary']};
                border-radius: 9px;
                background: transparent;
            }}
            QRadioButton::indicator:checked {{
                border: 2px solid {COLORS['accent']};
                border-radius: 9px;
                background: {COLORS['accent']};
            }}
        """)

    def _create_culling_page(self):
        """创建选片设置页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 检测敏感度
        self.vars["min_confidence"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.detection_sensitivity"),
            self.i18n.t("advanced_settings.detection_sensitivity_hint"),
            min_val=30, max_val=70, default=50,
            format_func=lambda v: f"{v}%"
        )

        # 清晰度要求
        self.vars["min_sharpness"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.sharpness_requirement"),
            self.i18n.t("advanced_settings.sharpness_requirement_hint"),
            min_val=100, max_val=500, default=100,
            step=50
        )

        # 画面美感要求 (V4.2.7: 高级设置允许下探到 0，0 = 关闭美学过滤)
        self.vars["min_nima"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.aesthetics_requirement"),
            self.i18n.t("advanced_settings.aesthetics_requirement_hint"),
            min_val=0, max_val=50, default=40,
            format_func=lambda v: f"{v/10:.1f}",
            scale=10
        )

        # 分隔线
        self._add_divider(layout)

        # 连拍速度
        self.vars["burst_fps"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.burst_fps"),
            self.i18n.t("advanced_settings.burst_fps_hint"),
            min_val=4, max_val=20, default=10,
            step=1,
            format_func=lambda v: f"{v} fps"
        )

        layout.addStretch()
        return page

    def _create_birdid_page(self):
        """创建识鸟设置页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 识别确信度
        self.vars["birdid_confidence"] = self._create_slider_setting(
            layout,
            self.i18n.t("advanced_settings.birdid_confidence"),
            self.i18n.t("advanced_settings.birdid_confidence_hint"),
            min_val=30, max_val=95, default=50,
            step=5,
            format_func=lambda v: f"{v}%"
        )

        # 分隔线
        self._add_divider(layout)

        # 鸟种英文名格式
        nf_container = QHBoxLayout()
        nf_container.setSpacing(16)

        nf_label = QLabel(self.i18n.t("advanced_settings.name_format"))
        nf_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        nf_container.addWidget(nf_label)

        name_format_combo = QComboBox()
        name_format_combo.addItem(self.i18n.t("advanced_settings.name_format_default"), "default")
        name_format_combo.addItem(self.i18n.t("advanced_settings.name_format_avilist"), "avilist")
        name_format_combo.addItem(self.i18n.t("advanced_settings.name_format_clements"), "clements")
        name_format_combo.addItem(self.i18n.t("advanced_settings.name_format_birdlife"), "birdlife")
        name_format_combo.addItem(self.i18n.t("advanced_settings.name_format_scientific"), "scientific")
        self.vars["name_format"] = name_format_combo
        nf_container.addWidget(name_format_combo)
        nf_container.addStretch()

        layout.addLayout(nf_container)

        nf_hint = QLabel(self.i18n.t("advanced_settings.name_format_hint"))
        nf_hint.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 96px;
            margin-bottom: 8px;
        """)
        layout.addWidget(nf_hint)

        layout.addStretch()
        return page

    def _create_output_page(self):
        """创建输出设置页面 - 目录布局 + XMP 设置 + 预览管理（含滚动）"""
        # V4.2.7: 用 ScrollArea 包裹，避免内容超出 dialog 高度时下方控件被挤压
        # V4.2.7: Wrap in QScrollArea so growing content (folder layout + XMP +
        # preview management) never gets clipped below the dialog's bottom edge.
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # === V4.2.7: 分目录布局 / Folder layout ===
        fl_title = QLabel(self.i18n.t("advanced_settings.folder_layout"))
        fl_title.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 4px;
        """)
        layout.addWidget(fl_title)

        fl_container = QHBoxLayout()
        fl_container.setSpacing(16)
        fl_label = QLabel(self.i18n.t("advanced_settings.folder_layout_label"))
        fl_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;"
        )
        fl_container.addWidget(fl_label)

        fl_combo = QComboBox()
        fl_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_rating_first"), "rating-first"
        )
        fl_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_species_first"), "species-first"
        )
        self.vars["folder_layout"] = fl_combo
        fl_container.addWidget(fl_combo)
        fl_container.addStretch()
        layout.addLayout(fl_container)

        fl_hint = QLabel(self.i18n.t("advanced_settings.folder_layout_hint"))
        fl_hint.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 96px;
            margin-bottom: 8px;
        """)
        fl_hint.setWordWrap(True)
        layout.addWidget(fl_hint)

        self._add_divider(layout)

        # 页面标题（XMP 区段）
        title = QLabel(self.i18n.t("advanced_settings.xmp_write_mode"))
        title.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 4px;
        """)
        layout.addWidget(title)

        # XMP 写入方式 - 使用单选按钮组
        xmp_group_widget = QWidget()
        xmp_group_widget.setObjectName("xmpGroup")
        xmp_group_widget.setStyleSheet(f"""
            #xmpGroup {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
            }}
        """)
        xmp_layout = QVBoxLayout(xmp_group_widget)
        xmp_layout.setContentsMargins(16, 12, 16, 12)
        xmp_layout.setSpacing(0)

        self.xmp_button_group = QButtonGroup(self)

        # 选项1: 写入文件（嵌入）—— RadioButton + 灰色小字同行
        embedded_container = QWidget()
        embedded_layout = QHBoxLayout(embedded_container)
        embedded_layout.setContentsMargins(0, 6, 0, 6)
        embedded_layout.setSpacing(8)

        embedded_option = QRadioButton(self.i18n.t("advanced_settings.write_embedded"))
        self.vars["xmp_embedded"] = embedded_option
        self.xmp_button_group.addButton(embedded_option, 0)
        embedded_layout.addWidget(embedded_option)

        embedded_hint = QLabel(self.i18n.t("advanced_settings.xmp_mode_embedded_hint"))
        embedded_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        embedded_layout.addWidget(embedded_hint)
        embedded_layout.addStretch()

        xmp_layout.addWidget(embedded_container)

        # 分隔线
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['border_subtle']};")
        xmp_layout.addWidget(sep)

        # 选项2: XMP 侧车文件（所有文件）—— RadioButton + 灰色小字同行
        sidecar_container = QWidget()
        sidecar_layout = QHBoxLayout(sidecar_container)
        sidecar_layout.setContentsMargins(0, 6, 0, 6)
        sidecar_layout.setSpacing(8)

        sidecar_option = QRadioButton(self.i18n.t("advanced_settings.write_sidecar"))
        self.vars["xmp_sidecar"] = sidecar_option
        self.xmp_button_group.addButton(sidecar_option, 1)
        sidecar_layout.addWidget(sidecar_option)

        sidecar_hint = QLabel(self.i18n.t("advanced_settings.xmp_mode_sidecar_hint"))
        sidecar_hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        sidecar_layout.addWidget(sidecar_hint)
        sidecar_layout.addStretch()

        xmp_layout.addWidget(sidecar_container)

        # 分隔线
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color: {COLORS['border_subtle']};")
        xmp_layout.addWidget(sep2)

        # 选项3: 不写入任何元数据
        none_container = QWidget()
        none_layout = QHBoxLayout(none_container)
        none_layout.setContentsMargins(0, 6, 0, 6)
        none_layout.setSpacing(8)

        none_option = QRadioButton(self.i18n.t("advanced_settings.write_none"))
        self.vars["xmp_none"] = none_option
        self.xmp_button_group.addButton(none_option, 2)
        none_layout.addWidget(none_option)
        none_layout.addStretch()

        xmp_layout.addWidget(none_container)

        layout.addWidget(xmp_group_widget)

        # 预览图管理
        preview_group_widget = QWidget()
        preview_group_widget.setObjectName("previewGroup")
        preview_group_widget.setStyleSheet(f"""
            #previewGroup {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
            }}
        """)
        preview_layout = QVBoxLayout(preview_group_widget)
        preview_layout.setContentsMargins(16, 12, 16, 12)
        preview_layout.setSpacing(12)

        # 标题
        preview_title = QLabel(self.i18n.t("advanced_settings.preview_management"))
        preview_title.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 13px;
            font-weight: 500;
        """)
        preview_layout.addWidget(preview_title)

        # 1. Checkbox: 保留预览图片
        keep_preview_check = QCheckBox(self.i18n.t("advanced_settings.keep_preview"))
        # 使用全局样式，移除由于覆盖样式导致的 indicator 丢失问题
        self.vars["keep_temp_files"] = keep_preview_check
        preview_layout.addWidget(keep_preview_check)

        keep_preview_hint = QLabel(self.i18n.t("advanced_settings.keep_preview_hint"))
        keep_preview_hint.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 24px;
        """)
        preview_layout.addWidget(keep_preview_hint)

        completion_sound_check = QCheckBox(self.i18n.t("advanced_settings.completion_sound"))
        self.vars["completion_sound_enabled"] = completion_sound_check
        preview_layout.addWidget(completion_sound_check)

        completion_sound_hint = QLabel(self.i18n.t("advanced_settings.completion_sound_hint"))
        completion_sound_hint.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 24px;
        """)
        preview_layout.addWidget(completion_sound_hint)

        detail_metadata_check = QCheckBox(
            self.i18n.t("advanced_settings.detail_metadata_for_rejected")
        )
        self.vars["detail_meta_for_rejected"] = detail_metadata_check
        preview_layout.addWidget(detail_metadata_check)

        detail_metadata_hint = QLabel(
            self.i18n.t("advanced_settings.detail_metadata_hint")
        )
        detail_metadata_hint.setWordWrap(True)
        detail_metadata_hint.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 24px;
        """)
        preview_layout.addWidget(detail_metadata_hint)

        # 说明：不保留预览图时，选鸟完成后自动打开 Finder 显示结果目录

        layout.addWidget(preview_group_widget)

        layout.addStretch()

        # 包一层 QScrollArea，让 page 在垂直方向超出时可滚动
        # Wrap in a vertical-scrolling viewport.
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll

    def _add_divider(self, layout):
        """添加分隔线"""
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {COLORS['border_subtle']}; margin: 4px 0;")
        layout.addWidget(divider)

    def _create_slider_setting(self, layout, label_text, hint_text,
                               min_val, max_val, default, step=1,
                               format_func=None, scale=1):
        """创建滑块设置项"""
        container = QHBoxLayout()
        container.setSpacing(16)

        label = QLabel(label_text)
        label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        container.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(default)
        slider.setSingleStep(step)
        container.addWidget(slider, 1)

        if format_func is None:
            format_func = lambda v: str(v)

        value_label = QLabel(format_func(default))
        value_label.setStyleSheet(f"""
            color: {COLORS['accent']};
            font-size: 14px;
            font-family: {FONTS['mono']};
            font-weight: 500;
            min-width: 50px;
        """)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        container.addWidget(value_label)

        slider.valueChanged.connect(lambda v: value_label.setText(format_func(v)))

        layout.addLayout(container)

        # 添加小字提示
        hint_label = QLabel(hint_text)
        hint_label.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            margin-left: 96px;
            margin-bottom: 8px;
        """)
        layout.addWidget(hint_label)

        slider.scale = scale
        return slider

    def _create_buttons(self, layout):
        """创建底部按钮"""
        btn_container = QWidget()
        btn_container.setStyleSheet(f"""
            QWidget {{
                background-color: {COLORS['bg_card']};
                border-top: 1px solid {COLORS['border_subtle']};
            }}
        """)

        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(24, 20, 24, 20)

        # 恢复默认
        reset_btn = QPushButton(self.i18n.t("advanced_settings.reset_defaults"))
        reset_btn.setObjectName("tertiary")
        reset_btn.clicked.connect(self._reset_to_default)
        btn_layout.addWidget(reset_btn)

        btn_layout.addStretch()

        # 取消
        cancel_btn = QPushButton(self.i18n.t("advanced_settings.cancel"))
        cancel_btn.setObjectName("secondary")
        cancel_btn.setMinimumWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        # 保存 - 使用绿色主色调
        save_btn = QPushButton(self.i18n.t("advanced_settings.save"))
        save_btn.setMinimumWidth(80)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        save_btn.clicked.connect(self._save_settings)
        btn_layout.addWidget(save_btn)

        layout.addWidget(btn_container)

    def _load_current_config(self):
        """加载当前配置"""
        self.vars["min_confidence"].setValue(int(self.config.min_confidence * 100))
        self.vars["min_sharpness"].setValue(int(self.config.min_sharpness))
        self.vars["min_nima"].setValue(int(self.config.min_nima * 10))
        self.vars["burst_fps"].setValue(int(self.config.burst_fps))
        self.vars["birdid_confidence"].setValue(int(self.config.birdid_confidence))

        # 加载鸟种英文名格式
        nf_combo = self.vars["name_format"]
        nf_index = nf_combo.findData(self.config.name_format)
        nf_combo.setCurrentIndex(nf_index if nf_index >= 0 else 0)

        # V4.2.7: 加载分目录布局 / Load folder layout
        fl_combo = self.vars["folder_layout"]
        fl_index = fl_combo.findData(self.config.folder_layout)
        fl_combo.setCurrentIndex(fl_index if fl_index >= 0 else 0)

        # 加载全局元数据写入模式设置
        try:
            global_mode = self.config.get_metadata_write_mode()
            if global_mode == "sidecar":
                self.vars["xmp_sidecar"].setChecked(True)
            elif global_mode == "none":
                self.vars["xmp_none"].setChecked(True)
            else:
                self.vars["xmp_embedded"].setChecked(True)
        except Exception:
            self.vars["xmp_embedded"].setChecked(True)

        # 加载预览图设置
        keep_temp = self.config.keep_temp_files
        self.vars["keep_temp_files"].setChecked(keep_temp)
        self.vars["completion_sound_enabled"].setChecked(
            self.config.completion_sound_enabled
        )
        self.vars["detail_meta_for_rejected"].setChecked(
            self.config.get_detail_metadata_for_rejected()
        )

        # V4.3 Phase 5: 加载视频处理设置 / Load video processing config
        # 直接读 config dict（这些键没有 property，由 Phase 4 直接存 dict）
        cfg = self.config.config
        self.vars["video_auto_process_in_main"].setChecked(
            bool(cfg.get("video_auto_process_in_main", True)))
        vm_combo = self.vars["video_species_mode"]
        vm_index = vm_combo.findData(cfg.get("video_species_mode", "instant"))
        vm_combo.setCurrentIndex(vm_index if vm_index >= 0 else 0)
        self.vars["video_max_frames"].setValue(int(cfg.get("video_max_frames", 60)))
        # YOLO threshold: stored as 0.3-0.9 float, slider uses 30-90 int
        self.vars["video_yolo_threshold"].setValue(
            int(round(float(cfg.get("video_yolo_threshold", 0.5)) * 100)))
        self.vars["video_enable_species_id"].setChecked(
            bool(cfg.get("video_enable_species_id", True)))
        self.vars["video_enable_flight"].setChecked(
            bool(cfg.get("video_enable_flight", True)))
        


    @Slot()
    def _reset_to_default(self):
        """恢复默认设置"""
        reply = StyledMessageBox.question(
            self,
            self.i18n.t("advanced_settings.confirm_reset_title"),
            self.i18n.t("advanced_settings.confirm_reset_msg"),
            yes_text=self.i18n.t("advanced_settings.yes"),
            no_text=self.i18n.t("advanced_settings.cancel")
        )

        if reply == StyledMessageBox.Yes:
            self.config.reset_to_default()
            self._load_current_config()
            StyledMessageBox.information(
                self,
                self.i18n.t("advanced_settings.reset_done_title"),
                self.i18n.t("advanced_settings.reset_done_msg"),
                ok_text=self.i18n.t("buttons.close")
            )

    @Slot()
    def _save_settings(self):
        """保存设置"""
        min_confidence = self.vars["min_confidence"].value() / 100.0
        min_sharpness = self.vars["min_sharpness"].value()
        min_nima = self.vars["min_nima"].value() / 10.0
        burst_fps = self.vars["burst_fps"].value()
        birdid_confidence = self.vars["birdid_confidence"].value()

        self.config.set_min_confidence(min_confidence)
        self.config.set_min_sharpness(min_sharpness)
        self.config.set_min_nima(min_nima)
        self.config.set_burst_fps(burst_fps)
        self.config.set_birdid_confidence(birdid_confidence)

        # 保存鸟种英文名格式
        name_format = self.vars["name_format"].currentData()
        self.config.set_name_format(name_format)

        # V4.2.7: 保存分目录布局 / Persist folder layout
        folder_layout = self.vars["folder_layout"].currentData()
        self.config.set_folder_layout(folder_layout)

        # 保存全局元数据写入模式设置
        btn_id = self.xmp_button_group.checkedId()
        mode_map = {0: "embedded", 1: "sidecar", 2: "none"}
        global_mode = mode_map.get(btn_id, "embedded")
        self.config.set_metadata_write_mode(global_mode)
        self.config.set_save_csv(True)

        # 保存预览图设置
        self.config.set_keep_temp_files(self.vars["keep_temp_files"].isChecked())
        self.config.set_completion_sound_enabled(
            self.vars["completion_sound_enabled"].isChecked()
        )
        self.config.set_detail_metadata_for_rejected(
            self.vars["detail_meta_for_rejected"].isChecked()
        )

        # 保存外部应用列表
        self.config.set_external_apps(self._apps_data)

        # V4.3 Phase 5: 保存视频处理设置 / Persist video processing config
        cfg = self.config.config
        cfg["video_auto_process_in_main"] = self.vars["video_auto_process_in_main"].isChecked()
        cfg["video_species_mode"] = self.vars["video_species_mode"].currentData()
        cfg["video_max_frames"] = int(self.vars["video_max_frames"].value())
        cfg["video_yolo_threshold"] = self.vars["video_yolo_threshold"].value() / 100.0
        cfg["video_enable_species_id"] = self.vars["video_enable_species_id"].isChecked()
        cfg["video_enable_flight"] = self.vars["video_enable_flight"].isChecked()

        if self.config.save():
            StyledMessageBox.information(
                self,
                self.i18n.t("advanced_settings.save_success_title"),
                self.i18n.t("advanced_settings.save_success_msg"),
                ok_text=self.i18n.t("buttons.close")
            )
            self.accept()
        else:
            StyledMessageBox.critical(
                self,
                self.i18n.t("advanced_settings.save_error_title"),
                self.i18n.t("advanced_settings.save_error_msg"),
                ok_text=self.i18n.t("buttons.close")
            )

    # ------------------------------------------------------------------
    #  外部应用标签页（第三页）
    # ------------------------------------------------------------------

    def _create_video_page(self) -> QWidget:
        """
        V4.3 Phase 5: 创建「视频处理」参数页

        让用户精细控制主流程视频处理行为：
            - 是否启用主流程视频处理（总开关）
            - 鸟种识别模式（instant/fast/full）
            - 抽帧上限
            - YOLO 置信度阈值
            - 启用鸟种识别 / 飞行检测

        Phase 5: Video processing settings tab.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # ── 总开关：是否在主流程自动处理视频 / Master toggle ──────────
        auto_check = QCheckBox(self.i18n.t("video_opts.enable_checkbox"))
        auto_check.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px;")
        auto_check.setToolTip(self.i18n.t("video_opts.enable_tooltip"))
        layout.addWidget(auto_check)
        self.vars["video_auto_process_in_main"] = auto_check

        self._add_divider(layout)

        # ── 识别模式 / Recognition mode（横排，无独立 hint 行）──────────
        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        mode_label = QLabel(self.i18n.t("video_opts.mode_label"))
        mode_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; min-width: 80px;")
        mode_row.addWidget(mode_label)
        mode_combo = QComboBox()
        mode_combo.addItem(self.i18n.t("video_opts.mode_fast"), "instant")
        mode_combo.addItem(self.i18n.t("video_opts.mode_standard"), "fast")
        mode_combo.addItem(self.i18n.t("video_opts.mode_full"), "full")
        mode_combo.setMinimumWidth(220)
        mode_combo.setToolTip(self.i18n.t("video_opts.mode_tooltip"))
        mode_row.addWidget(mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        self.vars["video_species_mode"] = mode_combo

        self._add_divider(layout)

        # ── 抽帧上限 / Max sampled frames（精简 hint）───────────────
        self.vars["video_max_frames"] = self._create_slider_setting(
            layout,
            self.i18n.t("video_opts.max_frames"),
            self.i18n.t("video_opts.max_frames_tooltip"),
            min_val=30, max_val=240, default=60,
            step=10,
            format_func=lambda v: f"{v}{self.i18n.t('video_opts.frames_suffix')}"
        )

        self._add_divider(layout)

        # ── YOLO 置信度阈值 ─────────────────────────────────────────
        self.vars["video_yolo_threshold"] = self._create_slider_setting(
            layout,
            self.i18n.t("video_opts.yolo_conf"),
            self.i18n.t("video_opts.yolo_conf_tooltip"),
            min_val=30, max_val=90, default=50,
            step=5,
            format_func=lambda v: f"{v/100:.2f}"
        )

        self._add_divider(layout)

        # ── 鸟种 + 飞行检测开关（同一行紧凑显示）/ Compact toggles row ─
        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(20)
        species_check = QCheckBox(self.i18n.t("video_opts.birdid_checkbox"))
        species_check.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px;")
        species_check.setToolTip(self.i18n.t("video_opts.birdid_tooltip"))
        toggles_row.addWidget(species_check)
        self.vars["video_enable_species_id"] = species_check

        flight_check = QCheckBox(self.i18n.t("video_opts.flight_checkbox"))
        flight_check.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 13px;")
        flight_check.setToolTip(self.i18n.t("video_opts.flight_tooltip"))
        toggles_row.addWidget(flight_check)
        self.vars["video_enable_flight"] = flight_check
        toggles_row.addStretch()
        layout.addLayout(toggles_row)

        layout.addStretch()
        return page

    def _create_apps_page(self) -> QWidget:
        """创建「外部应用」标签页：用户手动添加右键菜单中的外部编辑器。"""
        self._apps_data: list = list(self.config.get_external_apps())

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # 说明文字
        hint = QLabel(self.i18n.t("advanced_settings.apps_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        layout.addWidget(hint)

        # 应用列表
        self._apps_list = QListWidget()
        self._apps_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent_dim']};
                color: {COLORS['accent']};
            }}
        """)
        self._apps_list.setMinimumHeight(180)
        self._refresh_apps_list()
        layout.addWidget(self._apps_list, 1)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton(self.i18n.t("advanced_settings.add_app"))
        add_btn.setObjectName("secondary")
        add_btn.setFixedHeight(34)
        add_btn.clicked.connect(self._on_add_app)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton(self.i18n.t("advanced_settings.remove_app"))
        remove_btn.setObjectName("secondary")
        remove_btn.setFixedHeight(34)
        remove_btn.clicked.connect(self._on_remove_app)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return page

    def _refresh_apps_list(self):
        """用 _apps_data 重建 QListWidget 内容。"""
        self._apps_list.clear()
        for app in self._apps_data:
            name = app.get("name", "")
            path = app.get("path", "")
            item = QListWidgetItem(f"  {name}   —   {path}")
            item.setToolTip(path)
            self._apps_list.addItem(item)

    @Slot()
    def _on_add_app(self):
        """
        macOS：先尝试 osascript choose application（原生选择器）。
        若 osascript 失败（沙盒/权限拒绝）则自动 fallback 到 Qt 文件对话框。
        其他平台：直接使用 Qt 文件对话框。
        """
        path = ""

        if sys.platform == "darwin":
            # 尝试 macOS 原生应用选择器
            try:
                result = subprocess.run(
                    ["osascript", "-e", "POSIX path of (choose application)"],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    # osascript 返回路径可能有尾部 '/' 或换行，统一清理
                    path = result.stdout.strip().rstrip("/")
            except Exception:
                pass

            # Fallback：osascript 不可用时用 Qt 文件对话框浏览 /Applications
            if not path:
                path = QFileDialog.getExistingDirectory(
                    self,
                    self.i18n.t("advanced_settings.pick_app_title"),
                    "/Applications",
                    QFileDialog.Option.DontUseNativeDialog
                )
                if path:
                    path = path.rstrip("/")

        elif sys.platform == "win32":
            path, _ = QFileDialog.getOpenFileName(
                self,
                self.i18n.t("advanced_settings.pick_app_title"),
                "C:\\Program Files",
                "Executables (*.exe)"
            )

        if not path:
            return

        # 从路径提取显示名称（去掉 .app / .exe 后缀）
        basename = os.path.basename(path)
        name = basename.replace(".app", "").replace(".exe", "")

        # 去重（规范化路径再比较）
        norm = path.rstrip("/")
        if any(a.get("path", "").rstrip("/") == norm for a in self._apps_data):
            return

        self._apps_data.append({"name": name, "path": norm})
        self._refresh_apps_list()

    @Slot()
    def _on_remove_app(self):
        """删除列表中选中的应用条目。"""
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data.pop(row)
            self._refresh_apps_list()

    @Slot(str, int, float)
    def _on_skill_level_changed(self, level_key: str, sharpness: int, aesthetics: float):
        """处理水平变化"""
        self.config.set_skill_level(level_key)
        self.config.save()

        if self.parent() and hasattr(self.parent(), '_apply_skill_level_thresholds'):
            self.parent()._apply_skill_level_thresholds(level_key)

        print(f"✅ 已切换摄影水平: {level_key} (锐度={sharpness}, 美学={aesthetics})")
