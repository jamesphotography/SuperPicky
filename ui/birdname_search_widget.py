# -*- coding: utf-8 -*-
"""
鸟类名称查询Widget
支持多版本、中文/英文/拼音/缩写查询
此功能仅在简体中文系统下显示
"""

import os
import sys
import sqlite3
import configparser
from typing import List, Dict, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QScrollArea,
    QFrame, QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from ui.styles import COLORS, FONTS
from tools.i18n import get_i18n


def get_birdname_db_path() -> str:
    """获取鸟类名称数据库路径"""
    if getattr(sys, 'frozen', False):
        # macOS .app bundle struct: executable is in Contents/MacOS/
        # PyInstaller puts datas in Contents/Resources/
        if sys.platform == 'darwin':
            app_contents = os.path.dirname(os.path.dirname(sys.executable))
            res_dir = os.path.join(app_contents, 'Resources')
            path_in_res = os.path.join(res_dir, 'ioc', 'birdname.db')
            if os.path.exists(path_in_res):
                return path_in_res
        
        # Windows 或 one-dir / one-file 模式的回退
        return os.path.join(sys._MEIPASS, 'ioc', 'birdname.db')
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, 'ioc', 'birdname.db')


def get_birdname_ini_path() -> str:
    """获取 ioc 目录下的 ini 配置文件路径（用户设置，保留在用户可写目录）"""
    # 将持久化配置存储在用户的主目录 .superpicky/ 下，避免 macOS App 内沙盒只读权限问题
    user_home = os.path.expanduser('~')
    app_data_dir = os.path.join(user_home, '.superpicky', 'ioc')
    os.makedirs(app_data_dir, exist_ok=True)
    return os.path.join(app_data_dir, 'birdname_settings.ini')


def load_last_version() -> Optional[str]:
    """从 ini 文件读取上次选择的 version_name，读取失败返回 None"""
    ini_path = get_birdname_ini_path()
    if not os.path.exists(ini_path):
        return None
    try:
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding='utf-8')
        return cfg.get('settings', 'last_version_name', fallback=None)
    except Exception as e:
        print(f"读取版本设置失败: {e}")
        return None


def save_last_version(version_name: str):
    """将当前选择的 version_name 写入 ini 文件"""
    ini_path = get_birdname_ini_path()
    try:
        cfg = configparser.ConfigParser()
        cfg['settings'] = {'last_version_name': version_name}
        with open(ini_path, 'w', encoding='utf-8') as f:
            cfg.write(f)
    except Exception as e:
        print(f"保存版本设置失败: {e}")


class ClickableLabel(QLabel):
    """可点击复制的标签"""
    clicked = Signal()

    def __init__(self, text: str, original_color: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.original_color = original_color
        self.accent_color = COLORS['accent']

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            self.setStyleSheet(f"color: {self.accent_color};")
            QTimer.singleShot(500, lambda: self.setStyleSheet(f"color: {self.original_color};"))
        super().mousePressEvent(event)


class BirdResultCard(QFrame):
    """单个鸟类搜索结果卡片 - 固定高度，点击复制"""

    CARD_HEIGHT = 52

    def __init__(self, bird_data: Dict, parent=None):
        super().__init__(parent)
        self.bird_data = bird_data

        self.setFixedHeight(self.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.setStyleSheet(f"""
            BirdResultCard {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
            }}
            BirdResultCard:hover {{
                background-color: {COLORS['bg_elevated']};
                border-color: {COLORS['accent']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        cn_name = bird_data.get('chinese_name', '')
        if cn_name:
            cn_color = COLORS['text_primary']
            self.cn_label = ClickableLabel(cn_name, cn_color)
            self.cn_label.setStyleSheet(f"color: {cn_color}; font-size: 13px; font-weight: 500;")
            self.cn_label.clicked.connect(lambda: self._copy_text(cn_name))
            layout.addWidget(self.cn_label)

        en_name = bird_data.get('english_name', '')
        if en_name:
            en_color = COLORS['text_secondary']
            self.en_label = ClickableLabel(en_name, en_color)
            self.en_label.setStyleSheet(f"color: {en_color}; font-size: 11px;")
            self.en_label.clicked.connect(lambda: self._copy_text(en_name))
            layout.addWidget(self.en_label)

    def _copy_text(self, text: str):
        QApplication.clipboard().setText(text)


class BirdNameSearchWidget(QWidget):
    """鸟类名称查询Widget（简体中文系统专用）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self.db_path = get_birdname_db_path()
        self.current_version_id = None
        self._loading_versions = False  # 防止加载时触发保存

        self._setup_ui()
        self._load_versions()

    def _setup_ui(self):
        """设置UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # ── 行1：标题 + 版本选择（固定高度）────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        title_label = QLabel("🔍 查询鸟名")
        title_label.setFixedHeight(28)
        title_label.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 12px;
            font-weight: 500;
        """)
        title_row.addWidget(title_label)
        title_row.addStretch()

        version_label = QLabel("请选择版本:")
        version_label.setFixedHeight(28)
        version_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
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

        # ── 行2：搜索框 + 清空按钮（固定高度）──────────────────────
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(32)
        self.search_input.setPlaceholderText("输入中文/英文/拼音/缩写")
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
        self.clear_btn.setToolTip("清空搜索")
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

        # ── 结果区域：始终占满剩余空间 ──────────────────────────────
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

        # 空状态提示
        self.empty_label = QLabel("请在上方输入关键词搜索")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.empty_label.setStyleSheet(f"""
            color: {COLORS['text_muted']};
            font-size: 12px;
            background: transparent;
            border: none;
        """)
        results_area_layout.addWidget(self.empty_label)

        # 滚动区域
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

        main_layout.addWidget(self.results_area, 1)

        # ── 统计标签 ──────────────────────────────────────────────
        self.stats_label = QLabel("")
        self.stats_label.setFixedHeight(16)
        self.stats_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 9px;
        """)
        self.stats_label.hide()
        main_layout.addWidget(self.stats_label)

    def _load_versions(self):
        """加载版本列表，并还原上次选择的版本"""
        if not os.path.exists(self.db_path):
            self.version_combo.addItem("暂无数据")
            self.version_combo.setEnabled(False)
            return
        try:
            self._loading_versions = True  # 加载期间屏蔽保存

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT version_id, version_name FROM versions ORDER BY created_at DESC")
            versions = cursor.fetchall()
            conn.close()

            if not versions:
                self.version_combo.addItem("暂无数据")
                self.version_combo.setEnabled(False)
                return

            # 填充下拉框
            for version_id, version_name in versions:
                self.version_combo.addItem(version_name, version_id)

            # 还原上次选择的版本
            last_name = load_last_version()
            restored = False
            if last_name:
                idx = self.version_combo.findText(last_name)
                if idx >= 0:
                    self.version_combo.setCurrentIndex(idx)
                    self.current_version_id = self.version_combo.itemData(idx)
                    restored = True

            # 找不到上次记录则默认选第一项
            if not restored:
                self.version_combo.setCurrentIndex(0)
                self.current_version_id = versions[0][0]

        except Exception as e:
            print(f"加载版本列表失败: {e}")
            self.version_combo.addItem("加载失败")
            self.version_combo.setEnabled(False)
        finally:
            self._loading_versions = False

    def _on_version_changed(self, index: int):
        """版本切换：更新 current_version_id，保存到 ini，重新搜索"""
        if index < 0:
            return
        self.current_version_id = self.version_combo.itemData(index)

        # 只有用户主动切换时才保存（加载期间跳过）
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
        if hasattr(self, '_search_timer'):
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
                f'%{query}%', f'%{query}%', f'%{query}%',
                f'%{query}%', f'%{query}%',
                f'%{query_lower}%', f'%{query_lower}%',
                query, query, query, query_lower,
                f'{query}%', f'{query}%'
            )
            cursor.execute(sql, params)
            results = cursor.fetchall()
            self._display_results(results)
            conn.close()
        except Exception as e:
            print(f"搜索失败: {e}")
            self._clear_results()

    def _display_results(self, results: List):
        self._clear_results()
        if not results:
            self.empty_label.setText("未找到匹配结果")
            self.empty_label.show()
            self.results_scroll.hide()
            self.stats_label.hide()
            return

        self.empty_label.hide()
        self.results_scroll.show()

        for row in results:
            bird_data = {
                'bird_id': row['bird_id'],
                'chinese_name': row['chinese_name'],
                'english_name': row['english_name'],
                'latin_name': row['latin_name'],
                'pinyin_name': row['pinyin_name'],
                'abbreviation': row['abbreviation']
            }
            self.results_layout.addWidget(BirdResultCard(bird_data))

        self.stats_label.setText(f"找到 {len(results)} 个结果")
        self.stats_label.show()

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.results_scroll.hide()
        self.empty_label.setText("请在上方输入关键词搜索")
        self.empty_label.show()
        self.stats_label.hide()

    def _clear_search(self):
        self.search_input.clear()
        self._clear_results()
        self.search_input.setFocus()