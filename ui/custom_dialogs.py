# -*- coding: utf-8 -*-
"""
SuperPicky - 自定义对话框
极简艺术风格 (Minimalist Artistic Design)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ui.styles import COLORS, FONTS
from tools.i18n import get_i18n


class StyledMessageBox(QDialog):
    """
    自定义消息框 - 极简艺术风格
    替代标准 QMessageBox，保持 UI 一致性
    """

    # 对话框类型
    Information = "info"
    Warning = "warning"
    Critical = "error"
    Question = "question"

    # 返回值
    Yes = 1
    No = 0
    Ok = 1
    Cancel = 0

    def __init__(self, parent=None, title="", message="",
                 dialog_type="info", buttons=None):
        super().__init__(parent)

        self.dialog_type = dialog_type
        self.result_value = self.No

        # 默认按钮配置（使用 i18n 保证语言切换后显示正确）
        if buttons is None:
            _i18n = get_i18n()
            if dialog_type == self.Question:
                buttons = [(_i18n.t("labels.no"), self.No, "secondary"), (_i18n.t("labels.yes"), self.Yes, "primary")]
            else:
                buttons = [(_i18n.t("buttons.confirm"), self.Ok, "primary")]

        self._setup_ui(title, message, buttons)

    def _setup_ui(self, title, message, buttons):
        """设置 UI"""
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)
        self.setModal(True)

        # 隐藏标题栏图标，但保留关闭按钮
        self.setWindowFlags(
            Qt.Dialog |
            Qt.WindowCloseButtonHint |
            Qt.WindowTitleHint
        )

        # 应用样式
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg_primary']};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(0)

        # 内容区域
        content_frame = QFrame()
        content_frame.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        # 标题 (如果有)
        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(f"""
                color: {COLORS['text_primary']};
                font-size: 16px;
                font-weight: 600;
                font-family: {FONTS['sans']};
                background: transparent;
            """)
            title_label.setWordWrap(True)
            content_layout.addWidget(title_label)

        # 消息内容
        if message:
            msg_label = QLabel(message)
            msg_label.setStyleSheet(f"""
                color: {COLORS['text_secondary']};
                font-size: 14px;
                font-family: {FONTS['sans']};
                line-height: 1.6;
                background: transparent;
            """)
            msg_label.setWordWrap(True)
            msg_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            content_layout.addWidget(msg_label)

        layout.addWidget(content_frame)

        # 文字链接按钮（btn_style == "link"，如开始确认框的「更换国家…」）：单独一行放在正文下方、
        # 左对齐，不占 180 固定宽——三个 180 宽按钮并排会超出对话框（最多 500 宽）把布局挤坏
        # Link-style buttons (e.g. "Change country…") go on their own left-aligned
        # row under the message; three 180-wide buttons would overflow the dialog.
        link_buttons = [b for b in buttons if b[2] == "link"]
        buttons = [b for b in buttons if b[2] != "link"]
        if link_buttons:
            layout.addSpacing(10)
            link_layout = QHBoxLayout()
            link_layout.setSpacing(16)
            for btn_text, btn_value, _style in link_buttons:
                link = QPushButton(btn_text)
                link.setCursor(Qt.PointingHandCursor)
                link.setFlat(True)
                link.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {COLORS['accent']};
                        border: none;
                        padding: 2px 0;
                        font-size: 14px;
                        font-weight: 500;
                        font-family: {FONTS['sans']};
                        text-align: left;
                    }}
                    QPushButton:hover {{
                        color: {COLORS['accent_hover']};
                        text-decoration: underline;
                    }}
                """)
                link.clicked.connect(lambda checked, v=btn_value: self._on_button_clicked(v))
                link_layout.addWidget(link)
            link_layout.addStretch()
            layout.addLayout(link_layout)

        layout.addSpacing(28)

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        button_width = 180 if len(buttons) > 1 else 120

        if len(buttons) > 1:
            btn_layout.addStretch()

        for btn_text, btn_value, btn_style in buttons:
            btn = QPushButton(btn_text)
            btn.setFixedWidth(button_width)
            btn.setMinimumHeight(40)
            btn.setCursor(Qt.PointingHandCursor)

            if btn_style == "primary":
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLORS['accent']};
                        color: {COLORS['bg_void']};
                        border: none;
                        border-radius: 8px;
                        padding: 10px 24px;
                        font-size: 14px;
                        font-weight: 500;
                        font-family: {FONTS['sans']};
                    }}
                    QPushButton:hover {{
                        background-color: {COLORS['accent_hover']};
                    }}
                    QPushButton:pressed {{
                        background-color: {COLORS['accent_pressed']};
                    }}
                """)
            else:  # secondary
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {COLORS['bg_card']};
                        color: {COLORS['text_secondary']};
                        border: 1px solid {COLORS['border']};
                        border-radius: 8px;
                        padding: 10px 24px;
                        font-size: 14px;
                        font-weight: 500;
                        font-family: {FONTS['sans']};
                    }}
                    QPushButton:hover {{
                        border-color: {COLORS['text_muted']};
                        color: {COLORS['text_primary']};
                    }}
                    QPushButton:pressed {{
                        background-color: {COLORS['bg_elevated']};
                    }}
                """)

            btn.clicked.connect(lambda checked, v=btn_value: self._on_button_clicked(v))
            btn_layout.addWidget(btn)

        if len(buttons) > 1:
            btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # 调整大小以适应内容
        self.adjustSize()

    def _on_button_clicked(self, value):
        """按钮点击处理"""
        self.result_value = value
        if value == self.Yes or value == self.Ok:
            self.accept()
        else:
            self.reject()

    def exec(self):
        """执行对话框并返回结果"""
        super().exec()
        return self.result_value

    # ==================== 静态便捷方法 ====================

    @staticmethod
    def question(parent, title, message, yes_text=None, no_text=None):
        """
        显示询问对话框
        返回: StyledMessageBox.Yes 或 StyledMessageBox.No
        """
        _i18n = get_i18n()
        if yes_text is None:
            yes_text = _i18n.t("labels.yes")
        if no_text is None:
            no_text = _i18n.t("labels.no")
        dlg = StyledMessageBox(
            parent=parent,
            title=title,
            message=message,
            dialog_type=StyledMessageBox.Question,
            buttons=[
                (no_text, StyledMessageBox.No, "secondary"),
                (yes_text, StyledMessageBox.Yes, "primary")
            ]
        )
        return dlg.exec()

    @staticmethod
    def information(parent, title, message, ok_text=None):
        """显示信息对话框"""
        if ok_text is None:
            ok_text = get_i18n().t("buttons.confirm")
        dlg = StyledMessageBox(
            parent=parent,
            title=title,
            message=message,
            dialog_type=StyledMessageBox.Information,
            buttons=[(ok_text, StyledMessageBox.Ok, "primary")]
        )
        return dlg.exec()

    @staticmethod
    def warning(parent, title, message, ok_text=None):
        """显示警告对话框"""
        if ok_text is None:
            ok_text = get_i18n().t("buttons.confirm")
        dlg = StyledMessageBox(
            parent=parent,
            title=title,
            message=message,
            dialog_type=StyledMessageBox.Warning,
            buttons=[(ok_text, StyledMessageBox.Ok, "primary")]
        )
        return dlg.exec()

    @staticmethod
    def critical(parent, title, message, ok_text=None):
        """显示错误对话框"""
        if ok_text is None:
            ok_text = get_i18n().t("buttons.confirm")
        dlg = StyledMessageBox(
            parent=parent,
            title=title,
            message=message,
            dialog_type=StyledMessageBox.Critical,
            buttons=[(ok_text, StyledMessageBox.Ok, "primary")]
        )
        return dlg.exec()
