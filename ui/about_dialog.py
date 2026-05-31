# -*- coding: utf-8 -*-
"""
SuperPicky - 关于对话框
PySide6 版本 - 极简艺术风格
"""

import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton, QScrollArea, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap

from ui.styles import COLORS, FONTS


class AboutDialog(QDialog):
    """关于对话框 - 极简艺术风格"""

    def __init__(self, parent=None, i18n=None):
        super().__init__(parent)
        self.i18n = i18n
        self._setup_ui()

    def _setup_ui(self):
        """设置 UI"""
        self.setWindowTitle(self.i18n.t("about.window_title") if self.i18n else "About")
        self.setFixedSize(560, 680)
        self.setModal(True)

        # 应用样式
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_primary']};
            }}
            QLabel {{
                color: {COLORS['text_primary']};
            }}
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 24)
        layout.setSpacing(0)

        # 品牌头部
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(20)

        # 图标
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            icon_container = QFrame()
            icon_container.setFixedSize(64, 64)
            icon_container.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {COLORS['accent']}, stop:1 {COLORS['accent_deep']});
                    border-radius: 16px;
                }}
            """)
            icon_inner = QHBoxLayout(icon_container)
            icon_inner.setContentsMargins(12, 12, 12, 12)

            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(pixmap)
            icon_inner.addWidget(icon_label)
            header_layout.addWidget(icon_container)

        # 品牌文字
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(4)

        title = QLabel(self.i18n.t("app.brand_name") if self.i18n else "SuperPicky")
        title.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 24px;
            font-weight: 600;
            letter-spacing: -0.5px;
        """)
        brand_layout.addWidget(title)

        subtitle = QLabel(self.i18n.t("about.subtitle") if self.i18n else "AI Bird Photo Culling Tool")
        subtitle.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 13px;")
        brand_layout.addWidget(subtitle)

        from constants import APP_VERSION
        from core.build_info import COMMIT_HASH

        _commit = COMMIT_HASH
        if not _commit:
            try:
                import subprocess
                _commit = subprocess.check_output(
                    ['git', 'rev-parse', '--short', 'HEAD'],
                    stderr=subprocess.DEVNULL
                ).strip().decode('utf-8')
            except Exception:
                _commit = 'dev'

        version = QLabel(f"v{APP_VERSION} ({_commit})")
        version.setStyleSheet(f"""
            color: {COLORS['accent']};
            font-size: 12px;
            font-family: {FONTS['mono']};
        """)
        brand_layout.addWidget(version)

        header_layout.addLayout(brand_layout)
        header_layout.addStretch()
        layout.addWidget(header)

        layout.addSpacing(32)

        # 分隔线
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {COLORS['border_subtle']};")
        layout.addWidget(divider)

        layout.addSpacing(24)

        # 内容区域
        content = QLabel()
        content.setStyleSheet(f"""
            color: {COLORS['text_secondary']};
            font-size: 13px;
            line-height: 1.6;
        """)
        content.setWordWrap(True)
        content.setText(self._get_content())
        content.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(content, 1)

        layout.addSpacing(24)

        # 底部
        footer_layout = QHBoxLayout()
        footer_layout.addStretch()

        close_btn = QPushButton(self.i18n.t("buttons.close") if self.i18n else "Close")
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        footer_layout.addWidget(close_btn)

        footer_layout.addStretch()
        layout.addLayout(footer_layout)

    def _get_content(self) -> str:
        """获取关于内容"""
        if self.i18n:
            return self.i18n.t("about.content")
        return """James Yu
Australian-Chinese Professional Photographer, Author of "James' Landscape Photography Notes" Trilogy

Model Training: Jordan Yu
Development Team: Xiaoping, Lyapunov, osk.sh, yblpoi, jcchan23

Open Source Models
YOLO11 - Bird Detection by Ultralytics
OSEA - Bird Classification by Sun Jiao
TOPIQ - Aesthetic Scoring by Chaofeng Chen et al.

License: GPL-3.0
© 2024-2025 James Yu"""
