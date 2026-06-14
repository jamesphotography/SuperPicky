#!/usr/bin/env python3
"""
鸟类识别停靠面板。
Bird-identification dock panel.

可停靠在主窗口边缘，负责识鸟入口、区域筛选与结果展示。
Dockable beside the main window and responsible for the BirdID entry,
region filtering, and result presentation.
"""

import os
import sys
from typing import Any, Optional, cast

from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QFileDialog,
    QProgressBar, QSizePolicy, QComboBox, QCheckBox, QSlider,
    QStackedWidget, QApplication
)
import json
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QPixmap, QDragEnterEvent, QDropEvent, QFont

from ui.styles import COLORS, FONTS
from config import (
    get_app_config_dir,
    get_install_scoped_resource_path,
    get_runtime_meipass,
)

from tools.i18n import get_i18n

ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
ALIGN_RIGHT_VCENTER = (
    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
)
LEFT_BUTTON = Qt.MouseButton.LeftButton
POINTING_HAND_CURSOR = Qt.CursorShape.PointingHandCursor
SIZE_POLICY_EXPANDING = QSizePolicy.Policy.Expanding
SIZE_POLICY_PREFERRED = QSizePolicy.Policy.Preferred
ALLOWED_DOCK_AREAS = (
    Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
)
USER_ROLE = int(Qt.ItemDataRole.UserRole)
KEEP_ASPECT_RATIO = Qt.AspectRatioMode.KeepAspectRatio
SMOOTH_TRANSFORMATION = Qt.TransformationMode.SmoothTransformation

def get_birdid_data_path(relative_path: str) -> str:
    """
    获取 `birdid/data` 目录下的资源路径。
    Return a resource path under `birdid/data`.
    """
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        return str(get_install_scoped_resource_path(os.path.join('birdid', 'data', relative_path)))
    if getattr(sys, 'frozen', False):
        meipass = get_runtime_meipass()
        if meipass is not None:
            return os.path.join(meipass, 'birdid', 'data', relative_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'birdid', 'data', relative_path)


def get_settings_path() -> str:
    """获取设置文件路径（统一使用标准配置目录）"""
    settings_dir = str(get_app_config_dir())
    os.makedirs(settings_dir, exist_ok=True)
    return os.path.join(settings_dir, 'birdid_dock_settings.json')


class IdentifyWorker(QThread):
    """后台识别线程"""
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, image_path: str, top_k: int = 5,
                 use_gps: bool = True, use_ebird: bool = True,
                 country_code: Optional[str] = None, region_code: Optional[str] = None,
                 name_format: Optional[str] = None):
        super().__init__()
        self.image_path = image_path
        self.top_k = top_k
        self.use_gps = use_gps
        self.use_ebird = use_ebird
        self.country_code = country_code
        self.region_code = region_code
        self.name_format = name_format

    def run(self):
        try:
            from birdid.bird_identifier import identify_bird
            result = identify_bird(
                self.image_path,
                top_k=self.top_k,
                use_gps=self.use_gps,
                use_ebird=self.use_ebird,
                country_code=self.country_code,
                region_code=self.region_code,
                name_format=self.name_format,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class DropArea(QFrame):
    """拖放区域 - 深色主题"""
    fileDropped = Signal(str)


    def __init__(self):
        super().__init__()
        self.setObjectName("DropArea")
        self.i18n = get_i18n()
        self.setAcceptDrops(True)
        self.setMinimumSize(250, 160)
        self.setStyleSheet(f"""
            QFrame#DropArea {{
                border: 2px dashed {COLORS['border']};
                border-radius: 10px;
                background-color: {COLORS['bg_elevated']};
            }}
            QFrame#DropArea:hover {{
                border: 2px dashed {COLORS['accent']};
                background-color: {COLORS['bg_card']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(ALIGN_CENTER)
        layout.setSpacing(8)

        # 图标 - + 号
        icon_label = QLabel("+")
        icon_label.setStyleSheet(f"""
            font-size: 48px;
            font-weight: 300;
            color: {COLORS['text_tertiary']};
            background: transparent;
        """)
        icon_label.setAlignment(ALIGN_CENTER)
        layout.addWidget(icon_label)

        # 提示文字
        hint_label = QLabel(self.i18n.t("birdid.drag_hint"))
        hint_label.setAlignment(ALIGN_CENTER)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 13px;
            background: transparent;
        """)
        layout.addWidget(hint_label)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()
        if mime.hasUrls():
            urls = mime.urls()
            if urls:
                file_path = urls[0].toLocalFile()
                self.fileDropped.emit(file_path)


    def mousePressEvent(self, event):
        if event.button() == LEFT_BUTTON:
            self.selectFile()

    def selectFile(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.i18n.t("birdid.select_image"),
            "",
            self.i18n.t("birdid.image_filter")
        )
        if file_path:
            self.fileDropped.emit(file_path)


class DropPreviewLabel(QLabel):
    """支持拖放的图片预览标签"""
    fileDropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            self.fileDropped.emit(file_path)


class ResultCard(QFrame):
    """识别结果卡片 - 深色主题，可点击选中"""

    clicked = Signal(int)  # 发送排名信号

    def __init__(self, rank: int, cn_name: str, en_name: str, confidence: float):
        super().__init__()
        self.setObjectName("ResultCard")
        self.rank = rank
        self.cn_name = cn_name
        self.en_name = en_name
        self.confidence = confidence
        self.i18n = get_i18n()
        self._selected = False

        self.setCursor(POINTING_HAND_CURSOR)
        self._update_style()

        # 外层水平布局：左侧色条 + 内容
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 左侧色条（#1用teal，其余用暗色）
        self.accent_bar = QFrame()
        self.accent_bar.setFixedWidth(3)
        bar_color = COLORS['accent'] if rank == 1 else COLORS['fill']
        self.accent_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {bar_color};
                border-radius: 3px;
            }}
        """)
        outer_layout.addWidget(self.accent_bar)

        # 内容布局
        content_widget = QWidget()
        content_widget.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(content_widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # 排名
        rank_color = COLORS['accent'] if rank == 1 else COLORS['text_tertiary']
        self.rank_label = QLabel(f"#{rank}")
        self.rank_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {rank_color};
            min-width: 24px;
            background: transparent;
            font-family: {FONTS['mono']};
        """)
        layout.addWidget(self.rank_label)

        # 名称 - 只显示当前语言，单行不换行
        is_en = self.i18n.current_lang.startswith('en')
        display_name = en_name if is_en else cn_name

        self.name_label = QLabel(display_name)
        self.name_label.setToolTip(self.i18n.t("birdid.click_to_copy") if hasattr(self.i18n, 't') else "Click to copy")
        self.name_label.setStyleSheet(f"""
            font-size: 13px;
            font-weight: 500;
            color: {COLORS['text_primary']};
            background: transparent;
        """)
        self.name_label.setSizePolicy(SIZE_POLICY_EXPANDING, SIZE_POLICY_PREFERRED)

        layout.addWidget(self.name_label, 1)

        # 置信度
        if confidence >= 70:
            conf_color = COLORS['success']
        elif confidence >= 40:
            conf_color = COLORS['warning']
        else:
            conf_color = COLORS['error']

        self.conf_label = QLabel(f"{confidence:.0f}%")
        self.conf_label.setStyleSheet(f"""
            font-size: 12px;
            font-weight: 600;
            color: {conf_color};
            font-family: {FONTS['mono']};
            background: transparent;
        """)
        self.conf_label.setFixedWidth(40)
        self.conf_label.setAlignment(ALIGN_RIGHT_VCENTER)
        layout.addWidget(self.conf_label)

        outer_layout.addWidget(content_widget, 1)

    def _update_style(self):
        """更新选中/未选中样式"""
        if self._selected:
            self.setStyleSheet(f"""
                QFrame#ResultCard {{
                    background-color: {COLORS['bg_card']};
                    border: 1px solid {COLORS['accent']};
                    border-radius: 8px;
                    border-left: none;
                }}
            """)
        else:
            is_first = self.rank == 1
            border_color = COLORS['accent'] if is_first else COLORS['border_subtle']
            hover_color = COLORS['accent'] if is_first else COLORS['text_muted']
            self.setStyleSheet(f"""
                QFrame#ResultCard {{
                    background-color: {COLORS['bg_card']};
                    border: 1px solid {border_color};
                    border-radius: 8px;
                    border-left: none;
                }}
                QFrame#ResultCard:hover {{
                    border: 1px solid {hover_color};
                    border-left: none;
                }}
            """)

    def set_selected(self, selected: bool):
        """设置选中状态"""
        self._selected = selected
        self._update_style()

    def is_selected(self):
        return self._selected

    def mousePressEvent(self, event):
        """点击事件"""
        self.clicked.emit(self.rank)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：复制鸟名"""
        from PySide6.QtWidgets import QMenu
        is_en = self.i18n.current_lang.startswith('en')
        name = self.en_name if is_en else self.cn_name

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 4px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QMenu::item {{
                padding: 6px 16px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {COLORS['border_subtle']};
                margin: 4px 8px;
            }}
        """)

        copy_label = f'Copy "{name}"' if is_en else f'复制 "{name}"'
        menu.addAction(copy_label, lambda: QApplication.clipboard().setText(name))

        menu.addSeparator()

        full = f"{self.cn_name} / {self.en_name} ({self.confidence:.0f}%)"
        full_label = "Copy full info" if is_en else "复制完整信息"
        menu.addAction(full_label, lambda: QApplication.clipboard().setText(full))

        menu.exec(event.globalPos())



class BirdIDDockWidget(QDockWidget):
    """鸟类识别停靠面板 - 深色主题"""

    def __init__(self, parent=None):
        self.i18n = get_i18n()
        super().__init__(self.i18n.t("birdid.title").upper(), parent)
        self.setObjectName("BirdIDDock")
        self.setAllowedAreas(ALLOWED_DOCK_AREAS)
        self.setMinimumWidth(280)

        # 使用自定义标题栏以控制按钮位置
        self._setup_title_bar()

        self.worker = None
        # 用于持住已退休但仍在运行的 worker 引用，防止 GC 析构运行中的 QThread
        # Keeps references to retired but still-running workers to prevent GC from
        # destructing a running QThread (which calls qFatal and aborts).
        self._retired_workers: list = []
        self.current_image_path = None
        self.identify_results = None

        # 加载区域数据和设置
        self.regions_data = self._load_regions_data()
        self.country_list = self._build_country_list()
        self.settings = self._load_settings()

        self._setup_ui()
        self._apply_settings()

    def _setup_title_bar(self):
        """创建自定义标题栏 - 标题靠左，按钮靠右"""
        title_bar = QWidget()
        title_bar.setObjectName("TitleBar")
        title_bar.setStyleSheet(f"""
            QWidget#TitleBar {{
                background-color: {COLORS['bg_elevated']};
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
        """)

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)

        # 标签切换区域
        tabs_layout = QHBoxLayout()
        tabs_layout.setSpacing(4)

        # 鸟类识别标签
        self.tab_identify = QPushButton(self.i18n.t("birdid.title").upper())
        self.tab_identify.setCheckable(True)
        self.tab_identify.setChecked(True)
        self.tab_identify.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 500;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        self.tab_identify.clicked.connect(lambda: self._switch_tab(0))
        tabs_layout.addWidget(self.tab_identify)

        # 查询鸟名标签（仅在简体中文系统显示）
        self.tab_search = QPushButton(self.i18n.t("birdid.query_birdname_btn"))
        self.tab_search.setCheckable(True)
        self.tab_search.setChecked(False)
        self.tab_search.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['text_tertiary']};
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_card']};
                color: {COLORS['text_secondary']};
            }}
        """)
        self.tab_search.clicked.connect(lambda: self._switch_tab(1))

        # 检查应用语言，仅在简体中文界面显示查询鸟名标签
        # 使用 i18n 系统而非 locale（macOS 的 locale 可能返回 'C'，不可靠）
        if self.i18n.current_lang.startswith('zh'):
            tabs_layout.addWidget(self.tab_search)
        else:
            self.tab_search.hide()

        layout.addLayout(tabs_layout)

        layout.addStretch()

        # 浮动按钮（靠右）- 用斜箭头表示状态
        self._float_btn = QPushButton("↗")  # 初始停靠状态 → 可弹出
        self._float_btn.setFixedSize(24, 24)
        self._float_btn.setToolTip(self.i18n.t("birdid.float_panel"))
        self._float_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS['text_tertiary']};
                font-size: 14px;
                border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_card']};
                color: {COLORS['text_secondary']};
            }}
        """)
        self._float_btn.clicked.connect(self._toggle_floating)
        layout.addWidget(self._float_btn)

        # 监听浮动状态变化，动态更新图标
        self.topLevelChanged.connect(self._on_float_changed)

        # 关闭按钮（最右）
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip(self.i18n.t("birdid.close_panel"))
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS['text_tertiary']};
                font-size: 12px;
                border-radius: 4px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['error']};
                color: {COLORS['text_primary']};
            }}
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        self.setTitleBarWidget(title_bar)

    def _toggle_floating(self):
        """切换浮动/停靠状态"""
        self.setFloating(not self.isFloating())

    def _on_float_changed(self, floating: bool):
        """浮动状态变化时更新按钮图标和 tooltip"""
        if hasattr(self, '_float_btn'):
            if floating:
                self._float_btn.setText("↙")  # 浮动中 → 可归位
                self._float_btn.setToolTip(self.i18n.t("birdid.dock_panel"))
            else:
                self._float_btn.setText("↗")  # 停靠中 → 可弹出
                self._float_btn.setToolTip(self.i18n.t("birdid.float_panel"))

    def _load_regions_data(self) -> dict:
        regions_path = get_birdid_data_path('ebird_regions.json')
        if os.path.exists(regions_path):
            try:
                with open(regions_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载区域数据失败: {e}")
        return {'countries': []}

    def _build_country_list(self) -> dict:
        from collections import OrderedDict

        t = self.i18n.t
        is_english = self.i18n.current_lang.startswith('en')

        country_list = OrderedDict()

        country_list[t("birdid.country_auto_gps")] = None
        country_list[t("birdid.country_global")] = "GLOBAL"

        country_list["─" * 15] = "SEP1"

        top10_codes = ['AU', 'BR', 'CN', 'GB', 'HK', 'ID', 'JP', 'MY', 'TW', 'US']

        top10_i18n = {
            'AU': 'birdid.country_au',
            'BR': 'birdid.country_br',
            'CN': 'birdid.country_cn',
            'GB': 'birdid.country_gb',
            'HK': 'birdid.country_hk',
            'ID': 'birdid.country_id',
            'JP': 'birdid.country_jp',
            'MY': 'birdid.country_my',
            'TW': 'birdid.country_tw',
            'US': 'birdid.country_us',
        }

        code_to_region = {}
        for region in self.regions_data.get('countries', []):
            code_to_region[region.get('code')] = region

        for code in top10_codes:
            i18n_key = top10_i18n.get(code)
            if i18n_key:
                display_name = t(i18n_key)
            else:
                region = code_to_region.get(code, {})
                if is_english:
                    display_name = region.get('name', code)
                else:
                    display_name = region.get('name_cn') or region.get('name', code)
            country_list[display_name] = code

        country_list["─" * 15 + " "] = "SEP2"

        country_list[t("birdid.country_more")] = "MORE"

        return country_list

    def _populate_country_combo(self):
        """填充国家下拉菜单，并禁用分隔符项"""
        from PySide6.QtGui import QStandardItem
        from PySide6.QtWidgets import QStyledItemDelegate

        self.country_combo.clear()

        for display_name, code in self.country_list.items():
            self.country_combo.addItem(display_name)

            # 如果是分隔符，禁用该项
            if code in ("SEP1", "SEP2"):
                idx = self.country_combo.count() - 1
                # 获取模型中的 item 并设置为不可选
                model = cast(Any, self.country_combo.model())
                item = model.item(idx)
                if item:
                    item.setEnabled(False)
                    # 设置分隔符样式
                    item.setSelectable(False)

    def _load_settings(self) -> dict:
        settings_path = get_settings_path()
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {
            'use_ebird': True,
            'auto_identify': False,
            'selected_country': self.i18n.t('birdid.country_auto_gps'),
            'selected_region': self.i18n.t('birdid.region_entire_country')
        }

    def _save_settings(self):
        country_display = self.country_combo.currentText()
        country_code = self.country_list.get(country_display)

        region_display = self.region_combo.currentText()
        region_code = None
        if region_display and region_display != self.i18n.t('birdid.region_entire_country'):
            import re
            match = re.search(r'\(([A-Z]{2}-[A-Z0-9]+)\)', region_display)
            if match:
                region_code = match.group(1)

        self.settings = {
            'use_ebird': self.ebird_checkbox.isChecked(),
            'auto_identify': self.auto_identify_checkbox.isChecked(),
            'selected_country': country_display,
            'country_code': country_code,
            'selected_region': region_display,
            'region_code': region_code
        }
        try:
            settings_path = get_settings_path()
            with open(settings_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存设置失败: {e}")

    def _apply_settings(self):
        self._applying_settings = True

        self.ebird_checkbox.setChecked(self.settings.get('use_ebird', True))
        self.auto_identify_checkbox.setChecked(self.settings.get('auto_identify', False))

        country_code = self.settings.get('country_code')
        saved_country = self.settings.get('selected_country', self.i18n.t('birdid.country_auto_gps'))

        matched = False
        if country_code:
            for display_name, code in self.country_list.items():
                if code == country_code:
                    idx = self.country_combo.findText(display_name)
                    if idx >= 0:
                        self.country_combo.setCurrentIndex(idx)
                        matched = True
                    break

        if not matched:
            idx = self.country_combo.findText(saved_country)
            if idx >= 0:
                self.country_combo.setCurrentIndex(idx)
            else:
                if country_code and country_code not in [None, "GLOBAL", "MORE"]:
                    for country in self.regions_data.get('countries', []):
                        if country.get('code') == country_code:
                            display_name = saved_country or country.get('name_cn') or country.get('name')
                            t = self.i18n.t
                            more_idx = self.country_combo.findText(t("birdid.country_more"))
                            if more_idx >= 0:
                                self.country_combo.insertItem(more_idx, display_name)
                                self.country_list[display_name] = country_code
                                self.country_combo.setCurrentText(display_name)
                            break

        saved_region = self.settings.get('selected_region', self.i18n.t('birdid.region_entire_country'))
        QTimer.singleShot(100, lambda: self._apply_saved_region(saved_region))

    def _apply_saved_region(self, saved_region: str):
        idx = self.region_combo.findText(saved_region)
        if idx >= 0:
            self.region_combo.setCurrentIndex(idx)
        self._applying_settings = False


    def _on_country_changed(self, country_display: str):
        country_code = self.country_list.get(country_display)

        if country_code in ("SEP1", "SEP2"):
            return

        if country_code == "MORE":
            self._show_more_countries_dialog()
            return

        self._updating_regions = True

        self.region_combo.clear()
        self.region_combo.addItem(self.i18n.t("birdid.region_entire_country"))

        _STATE_COUNTRIES = {"AU", "US", "CN"}
        is_english = self.i18n.current_lang.startswith('en')

        show_region = False
        if country_code and country_code not in (None, "GLOBAL"):
            for country in self.regions_data.get('countries', []):
                if country.get('code') == country_code:
                    if country.get('has_regions') and country.get('regions'):
                        for region in country['regions']:
                            region_code = region.get('code', '')
                            if is_english:
                                region_name = region.get('name', region_code)
                            else:
                                region_name = region.get('name_cn') or region.get('name', region_code)
                            self.region_combo.addItem(f"{region_name} ({region_code})")
                        show_region = country_code in _STATE_COUNTRIES
                    break

        if hasattr(self, '_region_row'):
            self._region_row.setVisible(show_region)

        self._updating_regions = False
        if not getattr(self, '_applying_settings', False):
            self._save_settings()

        self._reidentify_if_needed()


    def _on_region_changed(self, region_display: str):
        if getattr(self, '_updating_regions', False) or getattr(self, '_applying_settings', False):
            return

        self._save_settings()

        self._reidentify_if_needed()

    def _show_more_countries_dialog(self):
        from PySide6.QtWidgets import QDialog, QListWidget, QDialogButtonBox, QListWidgetItem, QLineEdit

        t = self.i18n.t
        is_english = self.i18n.current_lang.startswith('en')

        top10_codes = {'AU', 'BR', 'CN', 'GB', 'HK', 'ID', 'JP', 'MY', 'TW', 'US', 'GLOBAL'}

        continent_codes = {'AF', 'AS', 'EU', 'NA', 'SA', 'OC'}

        continent_i18n = {
            'AF': 'birdid.continent_af',
            'AS': 'birdid.continent_as',
            'EU': 'birdid.continent_eu',
            'NA': 'birdid.continent_na',
            'SA': 'birdid.continent_sa',
            'OC': 'birdid.continent_oc',
        }

        other_country_i18n = {
            'AR': 'birdid.country_ar',
            'CA': 'birdid.country_ca',
            'CH': 'birdid.country_ch',
            'CL': 'birdid.country_cl',
            'CO': 'birdid.country_co',
            'CR': 'birdid.country_cr',
            'DE': 'birdid.country_de',
            'EC': 'birdid.country_ec',
            'EG': 'birdid.country_eg',
            'ES': 'birdid.country_es',
            'FI': 'birdid.country_fi',
            'FR': 'birdid.country_fr',
            'GR': 'birdid.country_gr',
            'IN': 'birdid.country_in',
            'IT': 'birdid.country_it',
            'KE': 'birdid.country_ke',
            'KR': 'birdid.country_kr',
            'LK': 'birdid.country_lk',
            'MA': 'birdid.country_ma',
            'MG': 'birdid.country_mg',
            'MN': 'birdid.country_mn',
            'MX': 'birdid.country_mx',
            'NL': 'birdid.country_nl',
            'NO': 'birdid.country_no',
            'NP': 'birdid.country_np',
            'NZ': 'birdid.country_nz',
            'PE': 'birdid.country_pe',
            'PH': 'birdid.country_ph',
            'PL': 'birdid.country_pl',
            'PT': 'birdid.country_pt',
            'RU': 'birdid.country_ru',
            'SE': 'birdid.country_se',
            'SG': 'birdid.country_sg',
            'TH': 'birdid.country_th',
            'TZ': 'birdid.country_tz',
            'UA': 'birdid.country_ua',
            'VN': 'birdid.country_vn',
            'ZA': 'birdid.country_za',
        }

        dialog = QDialog(self)
        dialog.setWindowTitle(t("birdid.country_dialog_title"))
        dialog.setMinimumSize(320, 450)
        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_primary']};
            }}
            QLineEdit {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 6px;
                padding: 8px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {COLORS['accent']};
            }}
            QListWidget {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 8px;
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 搜索框
        search_input = QLineEdit()
        search_input.setPlaceholderText(t("birdid.search_country_placeholder"))
        layout.addWidget(search_input)

        list_widget = QListWidget()

        # 收集所有其他区域（排除 Top 10）
        other_regions = []
        for region in self.regions_data.get('countries', []):
            code = region.get('code', '')

            # 跳过已在下拉菜单中的国家
            if code in top10_codes:
                continue

            name_en = region.get('name', code)
            name_cn = region.get('name_cn', '')

            # 获取显示名称
            if code in continent_codes:
                # 大洲：添加 🌍 前缀
                i18n_key = continent_i18n.get(code)
                if i18n_key:
                    base_name = t(i18n_key)
                else:
                    base_name = name_cn if not is_english and name_cn else name_en
                display = f"🌍 {base_name}"
            else:
                # 普通国家
                i18n_key = other_country_i18n.get(code)
                if i18n_key:
                    display = t(i18n_key)
                else:
                    if is_english:
                        display = name_en
                    else:
                        display = name_cn if name_cn else name_en

            # 按英文名排序
            sort_key = name_en.lower()
            other_regions.append((sort_key, display, code, name_en))

        # 按英文名 A-Z 排序
        other_regions.sort(key=lambda x: x[0])

        for _, display, code, name_en in other_regions:
            item = QListWidgetItem(display)
            item.setData(USER_ROLE, code)
            item.setData(USER_ROLE + 1, name_en)  # 用于搜索
            list_widget.addItem(item)

        layout.addWidget(list_widget)

        def filter_countries(text):
            text = text.lower()
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                display_name = item.text().lower()
                en_name = (item.data(USER_ROLE + 1) or "").lower()
                visible = text in display_name or text in en_name
                item.setHidden(not visible)

        search_input.textChanged.connect(filter_countries)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = list_widget.currentItem()
            if selected:
                code_data = selected.data(USER_ROLE)
                if not isinstance(code_data, str):
                    return
                code = code_data
                display = selected.text()
                existing = [self.country_combo.itemText(i) for i in range(self.country_combo.count())]
                if display not in existing:
                    idx = self.country_combo.findText(t("birdid.country_more"))
                    if idx >= 0:
                        self.country_combo.insertItem(idx, display)
                        self.country_list[display] = code
                self.country_combo.setCurrentText(display)
        else:
            saved = self.settings.get('selected_country', t('birdid.country_auto_gps'))
            self.country_combo.setCurrentText(saved)

    def _setup_ui(self):
        container = QWidget()
        container.setStyleSheet(f"background-color: {COLORS['bg_void']};")

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background: transparent;")

        self.identify_panel = QWidget()
        identify_layout = QVBoxLayout(self.identify_panel)
        identify_layout.setContentsMargins(0, 0, 0, 0)
        identify_layout.setSpacing(12)

        self.drop_area = DropArea()
        self.drop_area.fileDropped.connect(self.on_file_dropped)
        filter_frame = QFrame()
        filter_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_elevated']};
                border-radius: 8px;
                padding: 8px;
            }}
        """)
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(8, 8, 8, 8)
        filter_layout.setSpacing(6)

        # 国家选择行
        country_row = QHBoxLayout()
        country_label = QLabel(self.i18n.t("birdid.country"))
        country_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
        """)
        country_row.addWidget(country_label)

        self.country_combo = QComboBox()
        self._populate_country_combo()
        self.country_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 4px;
                padding: 4px 8px;
                color: {COLORS['text_secondary']};
                font-size: 11px;
            }}
            QComboBox:hover {{
                border-color: {COLORS['accent']};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                selection-background-color: {COLORS['accent_dim']};
                selection-color: {COLORS['accent']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 5px 8px;
                min-height: 22px;
            }}
            QComboBox QAbstractItemView::item:disabled {{
                color: {COLORS['border']};
                background: transparent;
            }}
        """)
        self.country_combo.currentTextChanged.connect(self._on_country_changed)
        country_row.addWidget(self.country_combo, 1)
        filter_layout.addLayout(country_row)

        self._region_row = QWidget()
        self._region_row.setStyleSheet("background: transparent;")
        region_row_layout = QHBoxLayout(self._region_row)
        region_row_layout.setContentsMargins(0, 0, 0, 0)
        region_row_layout.setSpacing(6)
        self._region_label = QLabel(self.i18n.t("birdid.region"))
        self._region_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
        """)
        region_row_layout.addWidget(self._region_label)

        self.region_combo = QComboBox()
        self.region_combo.addItem(self.i18n.t("birdid.region_entire_country"))
        self.region_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 4px;
                padding: 4px 8px;
                color: {COLORS['text_secondary']};
                font-size: 11px;
            }}
            QComboBox:hover {{ border-color: {COLORS['accent']}; }}
            QComboBox::drop {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                selection-background-color: {COLORS['accent_dim']};
                selection-color: {COLORS['accent']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 5px 8px;
                min-height: 22px;
            }}
        """)
        self.region_combo.currentTextChanged.connect(self._on_region_changed)
        region_row_layout.addWidget(self.region_combo, 1)
        self._region_row.hide()
        filter_layout.addWidget(self._region_row)
        self.ebird_checkbox = QCheckBox()
        self.ebird_checkbox.setChecked(True)
        self.ebird_checkbox.hide()

        self.auto_identify_checkbox = QCheckBox()
        self.auto_identify_checkbox.setChecked(False)
        self.auto_identify_checkbox.hide()

        identify_layout.addWidget(filter_frame)
        identify_layout.addWidget(self.drop_area)

        self.preview_label = DropPreviewLabel()
        self.preview_label.setAlignment(ALIGN_CENTER)
        self.preview_label.setMinimumHeight(100)
        self.preview_label.setSizePolicy(SIZE_POLICY_EXPANDING, SIZE_POLICY_PREFERRED)
        self.preview_label.setStyleSheet(f"""
            background-color: {COLORS['bg_elevated']};
            border-radius: 10px;
            padding: 8px;
        """)
        self.preview_label.fileDropped.connect(self.on_file_dropped)
        self.preview_label.hide()
        self._current_pixmap = None
        self._result_crop_pixmap = None
        identify_layout.addWidget(self.preview_label)

        self.filename_label = QLabel()
        self.filename_label.setStyleSheet(f"""
            font-size: 11px;
            color: {COLORS['text_tertiary']};
            font-family: {FONTS['mono']};
        """)
        self.filename_label.setAlignment(ALIGN_CENTER)
        self.filename_label.setWordWrap(True)
        self.filename_label.hide()
        identify_layout.addWidget(self.filename_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumHeight(3)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {COLORS['bg_input']};
                border-radius: 2px;
                max-height: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS['accent']}, stop:1 {COLORS['accent_light']});
                border-radius: 2px;
            }}
        """)
        self.progress.hide()
        identify_layout.addWidget(self.progress)
        self.results_frame = QFrame()
        self.results_frame.setStyleSheet(f"""
            QFrame {{
                background-color: transparent;
            }}
        """)
        results_layout = QVBoxLayout(self.results_frame)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(6)

        self.results_title = QLabel(self.i18n.t("birdid.results"))
        self.results_title.setStyleSheet(f"""
            font-size: 11px;
            font-weight: 500;
            color: {COLORS['text_tertiary']};
            text-transform: uppercase;
            letter-spacing: 1px;
        """)
        results_layout.addWidget(self.results_title)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setSizePolicy(SIZE_POLICY_EXPANDING, SIZE_POLICY_EXPANDING)
        self.results_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
        """)

        self.results_widget = QWidget()
        self.results_widget.setStyleSheet("background: transparent;")
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(6)
        self.results_scroll.setWidget(self.results_widget)

        results_layout.addWidget(self.results_scroll)
        self.results_frame.hide()

        self.placeholder_frame = QFrame()
        self.placeholder_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 10px;
            }}
        """)
        ph_layout = QVBoxLayout(self.placeholder_frame)
        ph_layout.setAlignment(ALIGN_CENTER)
        ph_label = QLabel(self.i18n.t("birdid.drag_photo_hint"))
        ph_label.setAlignment(ALIGN_CENTER)
        ph_label.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 12px;
            background: transparent;
        """)
        ph_layout.addWidget(ph_label)
        identify_layout.addWidget(self.placeholder_frame, 1)

        identify_layout.addWidget(self.results_frame, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_new = QPushButton(self.i18n.t("birdid.btn_select"))
        self.btn_new.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                color: {COLORS['text_secondary']};
                border-radius: 6px;
                padding: 10px 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['text_muted']};
                color: {COLORS['text_primary']};
            }}
        """)
        self.btn_new.clicked.connect(self.drop_area.selectFile)
        btn_layout.addWidget(self.btn_new)

        self.btn_screenshot = QPushButton(self.i18n.t("birdid.btn_screenshot"))
        self.btn_screenshot.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                color: {COLORS['text_secondary']};
                border-radius: 6px;
                padding: 10px 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                color: {COLORS['accent']};
            }}
        """)
        self.btn_screenshot.clicked.connect(self._take_screenshot)
        btn_layout.addWidget(self.btn_screenshot)

        identify_layout.addLayout(btn_layout)

        self.status_label = QLabel("")
        self.status_label.hide()

        from ui.birdname_search_widget import BirdNameSearchWidget
        self.search_panel = BirdNameSearchWidget()

        self.stacked_widget.addWidget(self.identify_panel)
        self.stacked_widget.addWidget(self.search_panel)

        layout.addWidget(self.stacked_widget)

        self.setWidget(container)


    def _show_qimage_preview(self, qimage):
        from PySide6.QtGui import QImage

        pixmap = QPixmap.fromImage(qimage)
        if not pixmap.isNull():
            self._current_pixmap = pixmap
            self.drop_area.hide()
            self.preview_label.show()
            QTimer.singleShot(50, self._scale_preview)

    def on_file_dropped(self, file_path: str):
        if not os.path.exists(file_path):
            self.status_label.setText(self.i18n.t("birdid.file_not_found_short"))
            self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['error']};")
            return

        self.current_image_path = file_path
        self.status_label.setText(self.i18n.t("birdid.analyzing"))
        self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['accent']};")

        filename = os.path.basename(file_path)
        self.filename_label.setText(filename)
        self.filename_label.show()

        self.show_preview(file_path)

        self.clear_results()

        self.progress.show()
        self.results_frame.hide()

        self._start_identify(file_path)

    def _reidentify_if_needed(self):
        if hasattr(self, 'current_image_path') and self.current_image_path:
            if os.path.exists(self.current_image_path):
                self.status_label.setText(self.i18n.t("birdid.re_identifying"))
                self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['accent']};")

                self.clear_results()

                self.progress.show()
                self.results_frame.hide()

                self._start_identify(self.current_image_path)

    def _start_identify(self, file_path: str):
        if hasattr(self, 'worker') and self.worker is not None:
            try:
                self.worker.finished.disconnect()
                self.worker.error.disconnect()
            except Exception:
                pass
            if self.worker.isRunning():
                # 线程仍在运行：不能直接置 None（GC 析构运行中 QThread 会 qFatal 崩溃）
                # 将其移入退休列表持住引用，等线程自然结束后通过 finished 信号自清理。
                # Thread still running: setting None would let GC destruct a live QThread
                # (which calls qFatal). Move it to _retired_workers to keep a reference
                # alive; the finished signal will remove it once the thread ends.
                old_worker = self.worker
                self._retired_workers.append(old_worker)

                def _retire(w=old_worker):
                    """线程结束后从退休列表移除 / Remove from retired list when done."""
                    try:
                        self._retired_workers.remove(w)
                    except ValueError:
                        pass

                old_worker.finished.connect(_retire)
                old_worker.error.connect(lambda _e, w=old_worker: _retire(w))
            self.worker = None

        use_ebird = self.ebird_checkbox.isChecked()
        use_gps = True

        country_code = None
        region_code = None

        country_display = self.country_combo.currentText()
        country_code_raw = self.country_list.get(country_display)

        if country_code_raw and country_code_raw not in ("GLOBAL", "MORE"):
            country_code = country_code_raw

            region_display = self.region_combo.currentText()
            if region_display != self.i18n.t("birdid.region_entire_country"):
                import re
                match = re.search(r'\(([A-Z]{2}-[A-Z0-9]+)\)', region_display)
                if match:
                    region_code = match.group(1)

        from advanced_config import get_advanced_config
        advanced_config = get_advanced_config()
        self.worker = IdentifyWorker(
            file_path,
            top_k=5,
            use_gps=use_gps,
            use_ebird=use_ebird,
            country_code=country_code,
            region_code=region_code,
            name_format=advanced_config.name_format,
        )
        self.worker.finished.connect(self.on_identify_finished)
        self.worker.error.connect(self.on_identify_error)
        self.worker.start()

    def show_preview(self, file_path: str):
        try:
            ext = os.path.splitext(file_path)[1].lower()
            raw_extensions = ['.nef', '.cr2', '.cr3', '.arw', '.raf', '.orf', '.rw2', '.dng']

            if ext in raw_extensions:
                from birdid.bird_identifier import load_image
                pil_image = load_image(file_path)
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    pil_image.save(tmp.name, 'JPEG', quality=85)
                    pixmap = QPixmap(tmp.name)
                    os.unlink(tmp.name)
            else:
                pixmap = QPixmap(file_path)

            if not pixmap.isNull():
                self._current_pixmap = pixmap
                self.drop_area.hide()
                self.preview_label.show()
                QTimer.singleShot(50, self._scale_preview)
        except Exception as e:
            print(f"预览加载失败: {e}")

    def _scale_preview(self):
        if self._current_pixmap is None:
            return
        container = self.widget()
        if container:
            available_width = container.width() - 24 - 16
        else:
            available_width = self.width() - 40
        if available_width < 100:
            available_width = 256
        max_height = 280
        scaled = self._current_pixmap.scaled(
            available_width, max_height,
            KEEP_ASPECT_RATIO, SMOOTH_TRANSFORMATION
        )
        self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event):
        """面板大小变化时重新缩放预览图"""
        super().resizeEvent(event)
        if self._current_pixmap is not None and self.preview_label.isVisible():
            self._scale_preview()

    _FOCUS_STATUS_I18N = {
        'BEST':  'rating_engine.focus_best',
        'GOOD':  'rating_engine.focus_good',
        'BAD':   'rating_engine.focus_bad',
        'WORST': 'rating_engine.focus_worst',
    }
    _FOCUS_STATUS_COLOR = {
        'BEST':  COLORS['focus_best'],
        'GOOD':  COLORS['focus_good'],
        'BAD':   COLORS['focus_bad'],
        'WORST': COLORS['focus_worst'],
    }

    def update_crop_preview(self, debug_img, focus_status=None):
        try:
            import cv2
            from PySide6.QtGui import QImage

            rgb_img = cv2.cvtColor(debug_img, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_img.shape
            bytes_per_line = ch * w

            q_img = QImage(
                rgb_img.data,
                w,
                h,
                bytes_per_line,
                QImage.Format.Format_RGB888,
            )
            pixmap = QPixmap.fromImage(q_img)

            self._current_pixmap = pixmap
            self.preview_label.show()
            self._scale_preview()

        except Exception as e:
            print(f"[BirdIDDock] 预览更新失败: {e}")

        self.clear_results()
        self.placeholder_frame.hide()
        self.results_frame.show()

        if focus_status and focus_status in self._FOCUS_STATUS_I18N:
            i18n_key = self._FOCUS_STATUS_I18N[focus_status]
            raw_text = self.i18n.t(i18n_key)
            display_text = raw_text.lstrip("，, ").strip()
            color = self._FOCUS_STATUS_COLOR.get(focus_status, COLORS['text_secondary'])

            focus_label = QLabel(display_text)
            focus_label.setAlignment(ALIGN_CENTER)
            focus_label.setStyleSheet(f"""
                color: {color};
                font-size: 15px;
                font-weight: 600;
                padding: 12px;
                background-color: {COLORS['bg_elevated']};
                border-radius: 8px;
            """)
            self.results_layout.addWidget(focus_label)
            self.results_layout.addStretch()

    def show_completion_message(self, stats: dict):
        self.preview_label.hide()
        self._current_pixmap = None

        self.clear_results()
        self.placeholder_frame.hide()
        self.results_frame.show()

        total      = stats.get('total', 0)
        star_3     = stats.get('star_3', 0)
        star_2     = stats.get('star_2', 0)
        star_1     = stats.get('star_1', 0)
        star_0     = stats.get('star_0', 0)
        no_bird    = stats.get('no_bird', 0)
        total_time = stats.get('total_time', 0)
        flying     = stats.get('flying', 0)
        focus_precise = stats.get('focus_precise', 0)
        bird_species  = stats.get('bird_species', [])

        def pct(n):
            return f"{n/total*100:.1f}%" if total > 0 else "—"

        lines = [self.i18n.t("birdid.stats_complete").format(total=total, time_min=total_time/60), ""]
        if total > 0:
            lines.append(f"⭐⭐⭐  {star_3:>4}  ({pct(star_3)})")
            lines.append(f"⭐⭐    {star_2:>4}  ({pct(star_2)})")
            lines.append(f"⭐      {star_1:>4}  ({pct(star_1)})")
            lines.append(f"0⭐     {star_0:>4}  ({pct(star_0)})")
            lines.append(f"❌      {no_bird:>4}  ({pct(no_bird)})")

        if flying > 0 or focus_precise > 0:
            lines.append("")
            if flying > 0:
                lines.append(self.i18n.t("birdid.stats_flying").format(count=flying))
            if focus_precise > 0:
                lines.append(self.i18n.t("birdid.stats_focus_precise").format(count=focus_precise))

        if bird_species:
            is_chinese = self.i18n.current_lang.startswith('zh')
            names = []
            for sp in bird_species:
                if isinstance(sp, dict):
                    name = sp.get('cn_name', '') if is_chinese else sp.get('en_name', '')
                    if not name:
                        name = sp.get('en_name', '') or sp.get('cn_name', '')
                else:
                    name = str(sp)
                if name:
                    names.append(name)
            if names:
                lines.append("")
                lines.append(self.i18n.t("birdid.stats_species").format(count=len(names), names=', '.join(names)))

        info_label = QLabel('\n'.join(lines))
        info_label.setStyleSheet(f"""
            color: {COLORS['text_secondary']};
            font-size: 12px;
            font-family: {FONTS['mono']};
            padding: 16px;
            background-color: {COLORS['bg_elevated']};
            border-radius: 8px;
        """)
        info_label.setWordWrap(True)
        self.results_layout.addWidget(info_label)
        self.results_layout.addStretch()

    def clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget() # pyright: ignore[reportOptionalMemberAccess]
            if widget is not None:
                widget.deleteLater()

    def on_identify_finished(self, result: dict):
        self.progress.hide()
        t = self.i18n.t

        info_lines = []

        # 1. YOLO 检测状态
        yolo_info = result.get('yolo_info')
        if yolo_info is not None:
            if isinstance(yolo_info, dict) and yolo_info.get('bird_count', 1) == 0:
                info_lines.append(t("birdid.info_yolo_fail"))
            else:
                info_lines.append(t("birdid.info_yolo_ok"))

        # 2. 地理过滤状态
        gps_info = result.get('gps_info')
        ebird_info = result.get('ebird_info')

        if gps_info and gps_info.get('latitude'):
            # GPS 过滤生效
            count = ebird_info.get('species_count', 0) if ebird_info else 0
            lat = f"{gps_info['latitude']:.2f}"
            lon = f"{gps_info['longitude']:.2f}"
            info_lines.append(t("birdid.info_gps", lat=lat, lon=lon, count=count))
            # GPS 回退提示（优先显示国家级回退，其次全局）
            if ebird_info and ebird_info.get('country_fallback'):
                country = ebird_info.get('country_code', '?')
                info_lines.append(t("birdid.info_country_fallback", country=country))
            elif ebird_info and ebird_info.get('gps_fallback'):
                info_lines.append(t("birdid.info_gps_fallback", count=count))
        elif ebird_info and ebird_info.get('enabled'):
            # 区域过滤生效
            region = ebird_info.get('region_code', '')
            count = ebird_info.get('species_count', 0)
            if region:
                info_lines.append(t("birdid.info_region", region=region, count=count))
            else:
                info_lines.append(t("birdid.info_region", region="—", count=count))
        else:
            info_lines.append(t("birdid.info_global"))

        # === 处理失败/无结果 ===
        if not result.get('success'):
            info_lines.append(t("birdid.info_identify_fail"))
            self._show_info_panel(info_lines)
            return

        results = result.get('results', [])
        if not results:
            # 无鸟或无结果
            if isinstance(yolo_info, dict) and yolo_info.get('bird_count', 1) == 0:
                info_lines.append(t("birdid.info_no_bird_hint"))
            else:
                info_lines.append(t("birdid.info_no_result"))
            self._show_info_panel(info_lines)
            return

        # === 显示信息面板 + 结果卡片 ===
        self.results_frame.show()
        self.placeholder_frame.hide()
        self.result_cards = []
        self.selected_index = 0

        # 信息标签（在结果卡片之前）
        if info_lines:
            info_label = QLabel('\n'.join(info_lines))
            info_label.setWordWrap(True)
            info_label.setStyleSheet(f"""
                color: {COLORS['text_secondary']};
                font-size: 11px;
                padding: 8px 10px;
                background-color: {COLORS['bg_elevated']};
                border-radius: 6px;
                line-height: 1.4;
            """)
            self.results_layout.addWidget(info_label)

        if len(results) >= 2:
            gap = results[0].get('confidence', 0) - results[1].get('confidence', 0)
            show_count = 1 if gap >= 80 else min(3, len(results))
        else:
            show_count = 1

        for i, r in enumerate(results[:show_count], 1):
            card = ResultCard(
                rank=i,
                cn_name=r.get('cn_name', '未知'),
                en_name=r.get('en_name', 'Unknown'),
                confidence=r.get('confidence', 0)
            )
            card.clicked.connect(self.on_result_card_clicked)
            if i == 1:
                card.set_selected(True)
            self.result_cards.append(card)
            self.results_layout.addWidget(card)

        self.results_layout.addStretch()

        cropped_pil = result.get('cropped_image')
        if cropped_pil:
            try:
                from PySide6.QtGui import QImage
                rgb = cropped_pil.convert('RGB')
                data = rgb.tobytes('raw', 'RGB')
                q_img = QImage(
                    data,
                    rgb.width,
                    rgb.height,
                    rgb.width * 3,
                    QImage.Format.Format_RGB888,
                )
                pixmap = QPixmap.fromImage(q_img)
                if not pixmap.isNull():
                    self._current_pixmap = pixmap
                    self._result_crop_pixmap = pixmap
                    self._scale_preview()
            except Exception as _e:
                print(f"[BirdIDDock] 裁剪图预览更新失败: {_e}")

        self.identify_results = results

        self._update_status_label()

    def _show_info_panel(self, info_lines: list):
        self.results_frame.show()
        self.placeholder_frame.hide()
        info_label = QLabel('\n'.join(info_lines))
        info_label.setWordWrap(True)
        info_label.setStyleSheet(f"""
            color: {COLORS['text_secondary']};
            font-size: 11px;
            padding: 10px 12px;
            background-color: {COLORS['bg_elevated']};
            border-radius: 6px;
            line-height: 1.5;
        """)
        self.results_layout.addWidget(info_label)

    def on_identify_error(self, error_msg: str):
        self.progress.hide()
        self.status_label.setText(self.i18n.t("birdid.error_prefix") + error_msg[:30])
        self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['error']};")

    def on_result_card_clicked(self, rank: int):
        index = rank - 1
        if index < 0 or index >= len(self.result_cards):
            return

        if hasattr(self, 'result_cards'):
            for card in self.result_cards:
                card.set_selected(False)
        self.result_cards[index].set_selected(True)
        self.selected_index = index

        self._update_status_label()

        if getattr(self, '_result_crop_pixmap', None):
            self._current_pixmap = self._result_crop_pixmap
            self._scale_preview()

        if isinstance(self.identify_results, list) and 0 <= index < len(self.identify_results):
            result = self.identify_results[index]
            is_en = self.i18n.current_lang.startswith('en')
            bird_name = result.get('en_name', '') if is_en else result.get('cn_name', '')
            if not bird_name:
                bird_name = result.get('en_name', '') or result.get('cn_name', '')

            QApplication.clipboard().setText(bird_name)

            card = self.result_cards[index]
            original_style = card.name_label.styleSheet()
            card.name_label.setStyleSheet(f"""
                font-size: 13px;
                font-weight: 500;
                color: {COLORS['accent']};
                background: transparent;
            """)
            QTimer.singleShot(600, lambda: card.name_label.setStyleSheet(original_style))

    def _update_status_label(self):
        if hasattr(self, 'selected_index') and isinstance(self.identify_results, list):
            if 0 <= self.selected_index < len(self.identify_results):
                selected = self.identify_results[self.selected_index]
                self.status_label.setText(f"✓ {selected['cn_name']} ({selected['confidence']:.0f}%)")
                self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['success']};")

    def cleanup(self):
        """退出时清理所有后台识别线程，防止运行中的 QThread 被 GC 析构崩溃。

        供 main_window._cleanup_on_quit() 在程序退出前调用。
        Called by main_window._cleanup_on_quit() before the app exits.
        """
        # 断开并等待当前活跃 worker
        # Disconnect and wait for the currently active worker.
        if self.worker is not None:
            try:
                self.worker.finished.disconnect()
                self.worker.error.disconnect()
            except Exception:
                pass
            if self.worker.isRunning():
                self.worker.wait(5000)
            self.worker = None

        # 等待所有退休但仍在运行的 worker
        # Wait for all retired workers that may still be running.
        for w in list(self._retired_workers):
            try:
                w.finished.disconnect()
                w.error.disconnect()
            except Exception:
                pass
            if w.isRunning():
                w.wait(3000)
        self._retired_workers.clear()


    def _switch_tab(self, index: int):
        if index == 0:
            self.tab_identify.setChecked(True)
            self.tab_identify.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS['accent']};
                    color: {COLORS['bg_void']};
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: 500;
                    letter-spacing: 1px;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent_hover']};
                }}
            """)
            self.tab_search.setChecked(False)
            self.tab_search.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS['text_tertiary']};
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['bg_card']};
                    color: {COLORS['text_secondary']};
                }}
            """)
            self.stacked_widget.setCurrentIndex(0)
        else:
            # 切换到查询鸟名
            self.tab_search.setChecked(True)
            self.tab_search.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLORS['accent']};
                    color: {COLORS['bg_void']};
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent_hover']};
                }}
            """)
            self.tab_identify.setChecked(False)
            self.tab_identify.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {COLORS['text_tertiary']};
                    border: none;
                    border-radius: 4px;
                    padding: 4px 12px;
                    font-size: 11px;
                    font-weight: 500;
                    letter-spacing: 1px;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['bg_card']};
                    color: {COLORS['text_secondary']};
                }}
            """)
            self.stacked_widget.setCurrentIndex(1)

    def _take_screenshot(self):
        if sys.platform == 'darwin':
            self._take_screenshot_mac()
        elif sys.platform == 'win32':
            self._take_screenshot_win()

    def _take_screenshot_mac(self):
        import tempfile

        import subprocess as _sp
        _test_file = os.path.join(tempfile.gettempdir(), 'birdid_sc_test.png')
        try:
            _r = _sp.run(['screencapture', '-x', '-R', '0,0,1,1', _test_file],
                         capture_output=True, timeout=5)
            _test_ok = (_r.returncode == 0 and
                        os.path.exists(_test_file) and
                        os.path.getsize(_test_file) > 0)
            if os.path.exists(_test_file):
                os.remove(_test_file)
        except Exception:
            _test_ok = True  # 测试本身异常时不阻止，让后续截图自行处理

        if not _test_ok:
            print("[Screenshot] ⚠️ 屏幕录制权限未授予，显示提示")
            from PySide6.QtWidgets import QMessageBox
            is_en = self.i18n.current_lang.startswith('en')

            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(self.i18n.t("birdid.title"))

            if is_en:
                msg.setText("Screen Recording Access Needed")
                msg.setInformativeText(
                    "SuperPicky needs screen recording permission to capture screenshots.\n\n"
                    "Tap \"Open Settings\" — find this app and flip the switch on.\n"
                    "Then come back and try again!"
                )
                open_btn = msg.addButton("  Open Settings  ", QMessageBox.ButtonRole.AcceptRole)
                msg.addButton("Later", QMessageBox.ButtonRole.RejectRole)
            else:
                msg.setText("需要屏幕录制权限")
                msg.setInformativeText(
                    "截图识鸟功能需要「屏幕录制」权限才能工作。\n\n"
                    "点击下方按钮一键跳转设置页，为本应用开启权限后即可使用。"
                )
                open_btn = msg.addButton("  打开系统设置  ", QMessageBox.ButtonRole.AcceptRole)
                msg.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)

            msg.setStyleSheet(f"""
                QMessageBox {{
                    background-color: {COLORS['bg_elevated']};
                    color: {COLORS['text_primary']};
                }}
                QLabel {{
                    color: {COLORS['text_primary']};
                    font-size: 13px;
                }}
                QPushButton {{
                    background-color: {COLORS['bg_card']};
                    color: {COLORS['text_primary']};
                    border: 1px solid {COLORS['border']};
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 12px;
                    min-width: 80px;
                }}
                QPushButton:hover {{
                    background-color: {COLORS['accent']};
                    color: {COLORS['bg_void']};
                }}
            """)
            msg.exec()

            if msg.clickedButton() == open_btn:
                import subprocess as _open_sp

                _open_sp.Popen([
                    'open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'
                ])
            return
        print("[Screenshot] ✅ 屏幕录制权限已授予")

        self._sc_tmp_file = os.path.join(tempfile.gettempdir(), 'birdid_screenshot.png')
        if os.path.exists(self._sc_tmp_file):
            try:
                os.remove(self._sc_tmp_file)
            except Exception:
                pass

        self._sc_main_win = self.window()
        if self._sc_main_win:
            self._sc_main_win.hide()

        QTimer.singleShot(300, self._launch_screencapture_mac)

    def _launch_screencapture_mac(self):
        import subprocess

        print(f"[Screenshot] 启动 screencapture, 目标文件: {self._sc_tmp_file}")

        try:
            self._sc_proc = subprocess.Popen(
                ['screencapture', '-i', '-s', self._sc_tmp_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"[Screenshot] screencapture 进程已启动, PID: {self._sc_proc.pid}")
        except FileNotFoundError:
            if getattr(self, '_sc_main_win', None):
                self._sc_main_win.show()
                self._sc_main_win.raise_()
            self._show_screenshot_error("screencapture 不可用")
            return
        if hasattr(self, '_sc_poll_timer') and self._sc_poll_timer is not None:
            self._sc_poll_timer.stop()

        self._sc_poll_count = 0
        self._sc_poll_timer = QTimer(self)
        self._sc_poll_timer.timeout.connect(self._poll_screencapture_done)
        self._sc_poll_timer.start(200)

    def _poll_screencapture_done(self):
        self._sc_poll_count += 1

        if self._sc_poll_count > 600:
            print("[Screenshot] ⚠️ 超时 (120s)，停止轮询")
            self._sc_poll_timer.stop()
            if getattr(self, '_sc_main_win', None):
                self._sc_main_win.show()
                self._sc_main_win.raise_()
            return

        if not hasattr(self, '_sc_proc') or self._sc_proc is None:
            self._sc_poll_timer.stop()
            return

        if self._sc_proc.poll() is not None:
            # 进程已退出
            rc = self._sc_proc.returncode
            stdout_data = self._sc_proc.stdout.read().decode('utf-8', errors='replace') if self._sc_proc.stdout else ''
            stderr_data = self._sc_proc.stderr.read().decode('utf-8', errors='replace') if self._sc_proc.stderr else ''
            print(f"[Screenshot] screencapture 退出, returncode={rc}")
            if stdout_data.strip():
                print(f"[Screenshot] stdout: {stdout_data.strip()}")
            if stderr_data.strip():
                print(f"[Screenshot] stderr: {stderr_data.strip()}")

            self._sc_poll_timer.stop()
            self._sc_proc = None

            main_win = getattr(self, '_sc_main_win', None)
            if main_win:
                main_win.show()
                main_win.raise_()
                main_win.activateWindow()

            file_exists = os.path.exists(self._sc_tmp_file)
            if file_exists:
                file_size = os.path.getsize(self._sc_tmp_file)
                print(f"[Screenshot] ✅ 截图文件存在, 大小: {file_size} bytes, 路径: {self._sc_tmp_file}")
                if file_size > 0:
                    QTimer.singleShot(100, lambda: self.on_file_dropped(self._sc_tmp_file))
                else:
                    print("[Screenshot] ⚠️ 截图文件为空 (0 bytes)，可能缺少屏幕录制权限")
                    self._show_screenshot_error("截图文件为空，请检查系统偏好设置 > 隐私与安全 > 屏幕录制 权限")
            else:
                print(f"[Screenshot] ❌ 截图文件不存在 (用户可能取消了截图)")
                import glob
                tmp_dir = os.path.dirname(self._sc_tmp_file)
                related = glob.glob(os.path.join(tmp_dir, 'birdid_*'))
                if related:
                    print(f"[Screenshot] 临时目录中的相关文件: {related}")

    def _load_screenshot_from_clipboard(self):
        import tempfile
        clipboard = QApplication.clipboard()
        image = clipboard.image()
        if image is None or image.isNull():
            return
        tmp_file = os.path.join(tempfile.gettempdir(), 'birdid_screenshot.png')
        if image.save(tmp_file, b'PNG'):
            self.on_file_dropped(tmp_file)
        else:
            self._show_screenshot_error("截图保存失败")

    def _show_screenshot_error(self, msg: str):
        self.status_label.setText(msg)
        self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['error']};")
        self.status_label.show()

    def _take_screenshot_win(self):
        try:
            QApplication.clipboard().clear()
        except Exception:
            pass

        self._sc_main_win = self.window()
        if self._sc_main_win:
            self._sc_main_win.hide()

        QTimer.singleShot(300, self._launch_snip_win)

    def _launch_snip_win(self):
        import ctypes

        KEYEVENTF_KEYUP = 0x0002
        VK_LWIN  = 0x5B
        VK_SHIFT = 0x10
        VK_S     = 0x53

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            self._restore_win_window()
            self.status_label.setText(self.i18n.t("birdid.win_shortcut_unsupported"))
            self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['error']};")
            return
        keybd = windll.user32.keybd_event
        try:
            keybd(VK_LWIN,  0, 0, 0)
            keybd(VK_SHIFT, 0, 0, 0)
            keybd(VK_S,     0, 0, 0)
            keybd(VK_S,     0, KEYEVENTF_KEYUP, 0)
            keybd(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            keybd(VK_LWIN,  0, KEYEVENTF_KEYUP, 0)
        except Exception as e:
            self._restore_win_window()
            self.status_label.setText(f"截图快捷键发送失败: {e}")
            self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['error']};")
            return

        self._screenshot_poll_count = 0
        self._screenshot_timer = QTimer(self)
        self._screenshot_timer.timeout.connect(self._poll_clipboard_for_screenshot)
        self._screenshot_timer.start(500)

    def _restore_win_window(self):
        main_win = getattr(self, '_sc_main_win', None)
        if main_win:
            main_win.show()
            main_win.raise_()
            main_win.activateWindow()

    def _poll_clipboard_for_screenshot(self):
        import tempfile

        self._screenshot_poll_count += 1

        if self._screenshot_poll_count > 120:
            self._screenshot_timer.stop()
            self._restore_win_window()
            return

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        if mime and mime.hasImage():
            self._screenshot_timer.stop()

            image = clipboard.image()
            if image.isNull():
                self._restore_win_window()
                return

            tmp_file = os.path.join(tempfile.gettempdir(), 'birdid_screenshot.png')
            if image.save(tmp_file, b'PNG'):
                self._restore_win_window()
                QTimer.singleShot(100, lambda: self.on_file_dropped(tmp_file))
            else:
                self._restore_win_window()

    def reset_view(self):
        self.drop_area.show()
        self.preview_label.hide()
        self.filename_label.hide()
        self.results_frame.hide()
        self.placeholder_frame.show()
        self._result_crop_pixmap = None

        self.status_label.setText(self.i18n.t("labels.ready"))
        self.status_label.setStyleSheet(f"font-size: 11px; color: {COLORS['text_muted']};")
        self.current_image_path = None
        self.identify_results = None
        self._current_pixmap = None
        self.clear_results()
