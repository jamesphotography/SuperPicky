# -*- coding: utf-8 -*-
"""
鸟类名称查询Widget
支持多版本、中文/英文/拼音/缩写查询
此功能仅在简体中文系统下显示
"""

import os
import sqlite3
import configparser
from typing import List, Dict, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QApplication,
)
from PySide6.QtCore import Qt, Signal, QTimer

from ui.styles import COLORS, FONTS
from ui.icon_utils import tinted_png_path, glyph_pixmap
from tools.i18n import get_i18n
from config import get_birdname_settings_path, get_install_scoped_resource_path

# 罕见度分级 / IUCN 展示复用既有组件（与识别详情面板保持一致的视觉与配色）
# Reuse the existing rarity-tier and IUCN formatting helpers so this panel
# matches the recognition detail panel visually.
from core.rarity_tier import gbif_score_to_tier, tier_name, tier_icon, tier_color
from ui.detail_panel import _format_iucn


def get_birdname_db_path() -> str:
    """获取鸟类名称数据库路径"""
    return str(get_install_scoped_resource_path(os.path.join("ioc", "birdname.db")))


def get_bird_reference_db_path() -> str:
    """
    获取 birdid 参考库 bird_reference.sqlite 的路径（罕见度/IUCN/简介数据源）。

    与 get_birdname_db_path 一样借助 get_install_scoped_resource_path 跨
    Windows Lite 安装目录、PyInstaller bundle、源码目录三种环境解析。

    Return the path to the birdid reference DB (rarity / IUCN / intro source),
    resolved across install-scoped, frozen and source environments.
    """
    return str(
        get_install_scoped_resource_path(
            os.path.join("birdid", "data", "bird_reference.sqlite")
        )
    )


def get_birdname_ini_path() -> str:
    """获取 ioc 目录下的 ini 配置文件路径（用户设置，保留在用户可写目录）"""
    return str(get_birdname_settings_path())


def load_last_version() -> Optional[str]:
    """从 ini 文件读取上次选择的 version_name，读取失败返回 None"""
    ini_path = get_birdname_ini_path()
    if not os.path.exists(ini_path):
        return None
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        return cfg.get("settings", "last_version_name", fallback=None)
    except Exception:
        return None


def save_last_version(version_name: str):
    """将当前选择的 version_name 写入 ini 文件"""
    ini_path = get_birdname_ini_path()
    try:
        cfg = configparser.ConfigParser()
        cfg["settings"] = {"last_version_name": version_name}
        with open(ini_path, "w", encoding="utf-8") as f:
            cfg.write(f)
    except Exception:
        pass


class ClickableLabel(QLabel):
    """可点击复制的标签"""

    clicked = Signal()

    def __init__(self, text: str, original_color: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.original_color = original_color
        self.accent_color = COLORS["accent"]

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            self.setStyleSheet(f"color: {self.accent_color};")
            QTimer.singleShot(
                500, lambda: self.setStyleSheet(f"color: {self.original_color};")
            )
        super().mousePressEvent(event)


class BirdResultCard(QFrame):
    """
    单个鸟类搜索结果卡片 - 固定高度。

    左侧中/英文名点击可复制（ClickableLabel）；整卡点击发 selected 信号，
    供上层展开下方详情。右侧显示一个罕见度分级图标（○◔◑◕●），无数据则不显示。

    A single search-result card: clickable Chinese/English names (copy), a
    rarity-tier glyph on the right, and a `selected` signal emitted on click so
    the parent can expand the detail area below the list.
    """

    CARD_HEIGHT = 52

    # 整卡被点击时发出，携带本行 bird_data（含 latin_name 供查补充信息）
    # Emitted on card click, carrying this row's bird_data (incl. latin_name).
    selected = Signal(dict)

    def __init__(
        self, bird_data: Dict, tier_index: Optional[int] = None, parent=None
    ):
        super().__init__(parent)
        self.bird_data = bird_data
        self._is_selected = False

        self.setFixedHeight(self.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style(False)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        # 名称两行随界面语言(issue #106 追加反馈):英文界面「英文名 + 拉丁名
        # (斜体)」;中文界面保持「中文名 + 英文名」。两行都保留点击复制行为。
        # Name lines follow the UI language (issue #106 follow-up): the
        # English UI shows the English name first with the Latin name in
        # italics below; the Chinese UI keeps Chinese-first + English below.
        # Both lines keep the click-to-copy behavior.
        is_en_ui = get_i18n().current_lang.startswith("en")
        if is_en_ui:
            primary = (bird_data.get("english_name") or
                       bird_data.get("chinese_name") or "")
            secondary = (bird_data.get("latin_name") or "").strip()
            secondary_italic = True
        else:
            primary = bird_data.get("chinese_name", "")
            secondary = bird_data.get("english_name", "")
            secondary_italic = False

        if primary:
            primary_color = COLORS["text_primary"]
            self.primary_label = ClickableLabel(primary, primary_color)
            self.primary_label.setStyleSheet(
                f"color: {primary_color}; font-size: 13px; font-weight: 500; "
                f"background: transparent;"
            )
            self.primary_label.clicked.connect(lambda: self._copy_text(primary))
            text_col.addWidget(self.primary_label)

        if secondary:
            secondary_color = COLORS["text_secondary"]
            italic_css = "font-style: italic; " if secondary_italic else ""
            self.secondary_label = ClickableLabel(secondary, secondary_color)
            self.secondary_label.setStyleSheet(
                f"color: {secondary_color}; font-size: 11px; {italic_css}"
                f"background: transparent;"
            )
            self.secondary_label.clicked.connect(lambda: self._copy_text(secondary))
            text_col.addWidget(self.secondary_label)

        root.addLayout(text_col, 1)

        # 右侧罕见度图标（仅在拿到分级时显示），颜色随分级 gray→green→…→red
        # Rarity glyph on the right, shown only when a tier is available.
        if tier_index is not None:
            color = tier_color(tier_index) or COLORS["text_secondary"]
            # 罕见度字形渲染成统一尺寸 pixmap,规避 ○◔◑◕● 各字形大小不一(列表并排时尤其明显)
            self.rarity_label = QLabel()
            self.rarity_label.setPixmap(glyph_pixmap(tier_icon(tier_index), color, 16))
            self.rarity_label.setAlignment(Qt.AlignCenter)
            self.rarity_label.setFixedWidth(22)
            self.rarity_label.setToolTip(tier_name(tier_index))
            self.rarity_label.setStyleSheet("background: transparent; border: none;")
            root.addWidget(self.rarity_label, 0, Qt.AlignVCenter)

    def _apply_style(self, selected: bool):
        """根据选中态切换边框/背景；未选中保留 hover 高亮。"""
        border = COLORS["accent"] if selected else COLORS["border"]
        bg = COLORS["bg_elevated"] if selected else COLORS["bg_card"]
        self.setStyleSheet(f"""
            BirdResultCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 4px;
            }}
            BirdResultCard:hover {{
                background-color: {COLORS['bg_elevated']};
                border-color: {COLORS['accent']};
            }}
        """)

    def set_selected(self, selected: bool):
        """设置选中态并刷新样式（由上层在选中变化时调用）。"""
        if self._is_selected == selected:
            return
        self._is_selected = selected
        self._apply_style(selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.bird_data)
        super().mousePressEvent(event)

    def _copy_text(self, text: str):
        QApplication.clipboard().setText(text)


class BirdNameSearchWidget(QWidget):
    """鸟类名称查询Widget（简体中文系统专用）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self.db_path = get_birdname_db_path()
        self.current_version_id = None
        self._loading_versions = False

        # 当前结果卡片与选中卡片（用于选中高亮 + 展开详情）
        # Current result cards and the selected one (highlight + detail expand).
        self._cards: List[BirdResultCard] = []
        self._selected_card: Optional[BirdResultCard] = None

        # 参考库管理器（罕见度/IUCN/简介数据源）。缺库或加载失败时降级为 None，
        # 此时列表不显示罕见度图标、不展开详情，搜索功能照常可用。
        # Reference-DB manager (rarity/IUCN/intro). Degrades to None on missing
        # DB or load failure; search still works, just without the extras.
        self.db_manager = self._init_db_manager()

        self._setup_ui()
        self._load_versions()

    @staticmethod
    def _init_db_manager():
        """尝试创建 BirdDatabaseManager；缺库或异常时返回 None（静默降级）。"""
        try:
            ref_path = get_bird_reference_db_path()
            if not os.path.exists(ref_path):
                return None
            # 延迟导入：BirdDatabaseManager 仅依赖 sqlite3 + i18n，不引入 torch。
            from birdid.bird_database_manager import BirdDatabaseManager
            return BirdDatabaseManager(ref_path)
        except Exception:
            return None

    def _setup_ui(self):
        """设置UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        # 标题前的放大镜复用 file-search-corner.svg(染主文字色)富文本内联,替代 🔍 emoji
        _search_icon = (
            f'<img src="{tinted_png_path("file-search-corner.svg", COLORS["text_primary"], 14)}" '
            f'width="14" height="14" style="vertical-align:middle;">'
        )
        title_label = QLabel(f'{_search_icon}&nbsp;{self.i18n.t("birdname_search.title")}')
        title_label.setFixedHeight(28)
        title_label.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 12px;
            font-weight: 500;
        """)
        title_row.addWidget(title_label)
        title_row.addStretch()

        version_label = QLabel(self.i18n.t("birdname_search.select_version"))
        version_label.setFixedHeight(28)
        version_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px;"
        )
        title_row.addWidget(version_label)

        self.version_combo = QComboBox()
        self.version_combo.setFixedSize(88, 28)
        self.version_combo.setFocusPolicy(Qt.StrongFocus)
        self.version_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 4px 8px;
                color: {COLORS['text_primary']};
                font-size: 10px;
            }}
            QComboBox:hover {{ border-color: {COLORS['text_muted']}; }}
            QComboBox:focus {{ border-color: {COLORS['accent']}; outline: none; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                selection-background-color: {COLORS['accent_dim']};
                selection-color: {COLORS['text_primary']};
                outline: none;
                padding: 2px;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 4px 8px;
                color: {COLORS['text_primary']};
                min-height: 24px;
            }}
        """)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        title_row.addWidget(self.version_combo)

        main_layout.addLayout(title_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(32)
        self.search_input.setPlaceholderText(self.i18n.t("birdname_search.placeholder"))
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 0px 12px;
                color: {COLORS['text_primary']};
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {COLORS['accent']}; }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self.search_input, 1)

        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedSize(32, 32)
        self.clear_btn.setToolTip(self.i18n.t("birdname_search.clear_tooltip"))
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_secondary']};
                font-size: 13px;
                font-weight: 500;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['bg_elevated']};
                border-color: {COLORS['accent']};
                color: {COLORS['accent']};
            }}
            QPushButton:pressed {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
            }}
        """)
        self.clear_btn.clicked.connect(self._clear_search)
        search_row.addWidget(self.clear_btn)

        main_layout.addLayout(search_row)

        self.results_area = QFrame()
        self.results_area.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
            }}
        """)
        self.results_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        results_area_layout = QVBoxLayout(self.results_area)
        results_area_layout.setContentsMargins(6, 6, 6, 6)
        results_area_layout.setSpacing(0)

        self.empty_label = QLabel(self.i18n.t("birdname_search.prompt_enter"))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.empty_label.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 12px;
            background: transparent;
            border: none;
        """)
        results_area_layout.addWidget(self.empty_label)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.results_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border']};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

        self.results_widget = QWidget()
        self.results_widget.setStyleSheet("background: transparent;")
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(4)
        self.results_layout.setAlignment(Qt.AlignTop)

        self.results_scroll.setWidget(self.results_widget)
        self.results_scroll.hide()

        results_area_layout.addWidget(self.results_scroll)

        main_layout.addWidget(self.results_area, 3)

        # 详情展开区：选中某条结果后在列表下方展示「罕见度 / IUCN / 简介」，
        # 视觉对齐识别详情面板。未选中时隐藏，不占空间；显示时与列表按 3:2
        # 分享纵向空间，保证简介有足够高度并能滚动。
        # Detail area shown below the list when a row is selected; it shares the
        # vertical space with the list (3:2) so the intro has room to scroll.
        self._build_detail_panel()
        main_layout.addWidget(self.detail_frame, 2)

        self.stats_label = QLabel("")
        self.stats_label.setFixedHeight(16)
        self.stats_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 9px;
        """)
        self.stats_label.hide()
        main_layout.addWidget(self.stats_label)

    def _build_detail_panel(self):
        """
        构建详情展开区（默认隐藏）。

        包含：标题行（中文名 + 学名）、罕见度行（图标+分级名+分数）、
        IUCN 行（等级文本，带配色）、简介区（可滚动、自动换行）。
        Build the (initially hidden) detail area: header, rarity row, IUCN row
        and a scrollable Chinese intro.
        """
        self.detail_frame = QFrame()
        # 可伸展 + 最小高度：去掉旧的 190 硬上限，避免简介区被压到只剩几十像素。
        # Expanding with a sensible minimum so the intro area is never starved.
        self.detail_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.detail_frame.setMinimumHeight(220)
        self.detail_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
            }}
        """)
        v = QVBoxLayout(self.detail_frame)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        # 标题：中文名 + 学名（学名灰色、斜体感）
        self.detail_header = QLabel("")
        self.detail_header.setWordWrap(True)
        self.detail_header.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 13px; "
            f"font-weight: 600; background: transparent; border: none;"
        )
        v.addWidget(self.detail_header)

        # 罕见度行
        self.detail_rarity_label = self._make_detail_row(v, self.i18n.t("birdname_search.rarity"))
        # IUCN 行
        self.detail_iucn_label = self._make_detail_row(v, "IUCN")

        # 简介标题
        intro_caption = QLabel(self.i18n.t("birdname_search.intro"))
        intro_caption.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 10px; "
            f"font-weight: 600; background: transparent; border: none;"
        )
        v.addWidget(intro_caption)

        # 简介内容（滚动 + 自动换行）。显式开垂直滚动条 AsNeeded + 给最小高度，
        # 确保长简介可滚动、短简介不留大片空白。
        # Vertical scrollbar AsNeeded + a minimum height so long intros scroll.
        intro_scroll = QScrollArea()
        intro_scroll.setWidgetResizable(True)
        intro_scroll.setMinimumHeight(90)
        intro_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        intro_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        intro_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        intro_scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border']}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self.detail_intro_label = QLabel("—")
        self.detail_intro_label.setWordWrap(True)
        self.detail_intro_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detail_intro_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 11px; "
            f"line-height: 150%; background: transparent; border: none;"
        )
        intro_scroll.setWidget(self.detail_intro_label)
        v.addWidget(intro_scroll, 1)

        self.detail_frame.hide()

    @staticmethod
    def _make_detail_row(parent_layout: QVBoxLayout, caption: str) -> QLabel:
        """生成一行「灰色小标题 + 值标签」，返回可后续更新的值标签。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        cap = QLabel(caption)
        cap.setFixedWidth(44)
        cap.setStyleSheet(
            f"color: {COLORS['text_tertiary']}; font-size: 10px; "
            f"font-weight: 600; background: transparent; border: none;"
        )
        row.addWidget(cap, 0, Qt.AlignVCenter)
        value = QLabel("—")
        value.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 12px; "
            f"background: transparent; border: none;"
        )
        row.addWidget(value, 1)
        parent_layout.addLayout(row)
        return value

    def _load_versions(self):
        """加载版本列表，并还原上次选择的版本"""
        if not os.path.exists(self.db_path):
            self.version_combo.addItem(self.i18n.t("birdname_search.no_data"))
            self.version_combo.setEnabled(False)
            return
        try:
            self._loading_versions = True

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT version_id, version_name FROM versions ORDER BY created_at DESC"
            )
            versions = cursor.fetchall()
            conn.close()

            if not versions:
                self.version_combo.addItem(self.i18n.t("birdname_search.no_data"))
                self.version_combo.setEnabled(False)
                return

            for version_id, version_name in versions:
                self.version_combo.addItem(version_name, version_id)

            last_name = load_last_version()
            restored = False
            if last_name:
                idx = self.version_combo.findText(last_name)
                if idx >= 0:
                    self.version_combo.setCurrentIndex(idx)
                    self.current_version_id = self.version_combo.itemData(idx)
                    restored = True

            if not restored:
                self.version_combo.setCurrentIndex(0)
                self.current_version_id = versions[0][0]

        except Exception:
            self.version_combo.addItem(self.i18n.t("birdname_search.load_failed"))
            self.version_combo.setEnabled(False)
        finally:
            self._loading_versions = False

    def _on_version_changed(self, index: int):
        """版本切换：更新 current_version_id，保存到 ini，重新搜索"""
        if index < 0:
            return
        self.current_version_id = self.version_combo.itemData(index)

        if not self._loading_versions:
            save_last_version(self.version_combo.currentText())

        self._clear_results()
        if self.search_input.text().strip():
            self._on_search_text_changed(self.search_input.text())

    def _on_search_text_changed(self, text: str):
        text = text.strip()
        if not text:
            self._clear_results()
            return
        if self.current_version_id is None:
            return
        if hasattr(self, "_search_timer"):
            self._search_timer.stop()
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._perform_search(text))
        self._search_timer.start(300)

    def _perform_search(self, query: str):
        if not os.path.exists(self.db_path):
            return
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query_lower = query.lower()
            sql = """
                SELECT * FROM birds
                WHERE version_id = ? AND (
                    chinese_name LIKE ? OR
                    english_name LIKE ? OR
                    latin_name LIKE ? OR
                    pinyin_name LIKE ? OR
                    abbreviation LIKE ? OR
                    LOWER(pinyin_name) LIKE ? OR
                    LOWER(abbreviation) LIKE ?
                )
                ORDER BY
                    CASE
                        WHEN chinese_name = ? THEN 1
                        WHEN english_name = ? THEN 2
                        WHEN abbreviation = ? THEN 3
                        WHEN LOWER(abbreviation) = ? THEN 4
                        WHEN chinese_name LIKE ? THEN 5
                        WHEN english_name LIKE ? THEN 6
                        ELSE 7
                    END,
                    chinese_name
                LIMIT 50
            """
            params = (
                self.current_version_id,
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                f"%{query}%",
                f"%{query_lower}%",
                f"%{query_lower}%",
                query,
                query,
                query,
                query_lower,
                f"{query}%",
                f"{query}%",
            )
            cursor.execute(sql, params)
            results = cursor.fetchall()
            self._display_results(results)
            conn.close()
        except Exception:
            self._clear_results()

    def _display_results(self, results: List):
        self._clear_results()
        if not results:
            self.empty_label.setText(self.i18n.t("birdname_search.no_match"))
            self.empty_label.show()
            self.results_scroll.hide()
            self.stats_label.hide()
            return

        self.empty_label.hide()
        self.results_scroll.show()

        bird_rows = [
            {
                "bird_id": row["bird_id"],
                "chinese_name": row["chinese_name"],
                "english_name": row["english_name"],
                "latin_name": row["latin_name"],
                "pinyin_name": row["pinyin_name"],
                "abbreviation": row["abbreviation"],
            }
            for row in results
        ]

        # 一次性批量查全部行的全球罕见度（避免逐行开连接），再按学名取分级
        # Batch-fetch global rarity for every row in one go, then derive tiers.
        score_map: Dict[str, float] = {}
        if self.db_manager:
            latins = [b["latin_name"] for b in bird_rows if b["latin_name"]]
            if latins:
                try:
                    score_map = self.db_manager.get_gbif_scores_by_scientific_names(
                        latins
                    )
                except Exception:
                    score_map = {}

        for bird_data in bird_rows:
            latin = (bird_data["latin_name"] or "").strip()
            score = score_map.get(latin)
            tier = gbif_score_to_tier(score) if score is not None else None
            card = BirdResultCard(bird_data, tier_index=tier)
            card.selected.connect(self._on_card_selected)
            self.results_layout.addWidget(card)
            self._cards.append(card)

        self.stats_label.setText(self.i18n.t("birdname_search.found_n", count=len(results)))
        self.stats_label.show()

    def _on_card_selected(self, bird_data: Dict):
        """某行被点击：更新选中高亮并展开下方详情。"""
        target = None
        for card in self._cards:
            is_target = card.bird_data is bird_data
            card.set_selected(is_target)
            if is_target:
                target = card
        self._selected_card = target
        self._show_detail(bird_data)

    def _show_detail(self, bird_data: Dict):
        """查补充信息并填充详情区；无库/无匹配/无数据时各字段显示「—」。"""
        cn = bird_data.get("chinese_name") or ""
        en = bird_data.get("english_name") or ""
        latin = (bird_data.get("latin_name") or "").strip()

        # 标题：「中文名  学名」，无中文名时退回英文名
        title = cn or en or latin or "—"
        if latin:
            title = f"{title}　<span style='color:{COLORS['text_muted']};'>{latin}</span>"
        self.detail_header.setText(title)

        info = None
        if self.db_manager and latin:
            try:
                info = self.db_manager.get_extra_info_by_scientific_name(latin)
            except Exception:
                info = None

        # 罕见度
        score = info.get("gbif_rarity_100") if info else None
        if score is not None:
            tier = gbif_score_to_tier(score)
            color = tier_color(tier) or COLORS["text_primary"]
            self.detail_rarity_label.setText(
                f"{tier_icon(tier)} {tier_name(tier)} ({score:.1f})"
            )
            self.detail_rarity_label.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 600; "
                f"background: transparent; border: none;"
            )
        else:
            self._reset_detail_value(self.detail_rarity_label)

        # IUCN
        iucn = info.get("iucn_category") if info else None
        if iucn:
            text, color = _format_iucn(iucn, True)
            self.detail_iucn_label.setText(text)
            self.detail_iucn_label.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 600; "
                f"background: transparent; border: none;"
            )
        else:
            self._reset_detail_value(self.detail_iucn_label)

        # 简介
        desc = (info.get("short_description_zh") if info else None) or ""
        desc = desc.strip()
        self.detail_intro_label.setText(desc if desc else "—")

        self.detail_frame.show()

    @staticmethod
    def _reset_detail_value(label: QLabel):
        """把某个值标签恢复为灰色「—」占位。"""
        label.setText("—")
        label.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 12px; "
            f"background: transparent; border: none;"
        )

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards = []
        self._selected_card = None
        if hasattr(self, "detail_frame"):
            self.detail_frame.hide()
        self.results_scroll.hide()
        self.empty_label.setText(self.i18n.t("birdname_search.prompt_enter"))
        self.empty_label.show()
        self.stats_label.hide()

    def _clear_search(self):
        self.search_input.clear()
        self._clear_results()
        self.search_input.setFocus()
