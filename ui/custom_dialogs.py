# -*- coding: utf-8 -*-
"""
SuperPicky - 自定义对话框
极简艺术风格 (Minimalist Artistic Design)
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QProgressDialog, QProgressBar, QCheckBox
)
from PySide6.QtCore import Qt, QSize
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


class StyledProgressDialog(QProgressDialog):
    """
    控件不再互相挤压的进度对话框:接管文字 / 进度条 / 取消按钮的摆放。

    QProgressDialog 用自己的手动布局(qprogressdialog.cpp 的私有 layout()),
    在 macOS 上取到的控件间距是 -1,且 sizeHint 没算进全局样式给按钮加的
    高度(取消按钮 44px):实测窗口只有 123px 高,文字、进度条、按钮三者
    上下重叠,空间局促时 Qt 还会主动把进度条压扁——只剩一条绿线,百分比
    被按钮盖住。

    这里保留 QProgressDialog 的全部行为(定时弹出、模态时 setValue 处理事件、
    取消信号等),只在 Qt 每次重排之后(尺寸变化 / 改文字 / 样式变化)按固定
    边距与间距重新摆放三者,并让 sizeHint 放得下全部控件。

    A progress dialog whose children no longer overlap. QProgressDialog lays
    out its label, bar and cancel button by hand; on macOS the spacing comes
    back as -1 and its sizeHint ignores the global 44px button height, so the
    three overlapped (measured 123px tall) and Qt squashed the bar to a line.
    All QProgressDialog behaviour is kept; after every Qt re-layout (resize,
    label change, style change) the children are placed again with fixed
    margins and spacing, and sizeHint fits them all.
    """

    MIN_WIDTH = 360     # 最小宽度,长文字(如复制进度)不至于被省略 / min width
    MARGIN_H = 20       # 左右边距 / horizontal margin
    MARGIN_V = 16       # 上下边距 / vertical margin
    SPACING = 12        # 控件间距 / spacing between children

    def _parts(self) -> tuple:
        """
        取内部的文字、进度条、取消按钮(可能为 None)。
        Return the internal label, bar and cancel button (any may be None).
        """
        return self.findChild(QLabel), self.findChild(QProgressBar), self.findChild(QPushButton)

    def sizeHint(self) -> QSize:
        """
        返回放得下文字、进度条与取消按钮的尺寸(按 _relayout 的摆法计算)。
        Return a size fitting label, bar and button as _relayout places them.

        返回 / Returns:
            QSize: 建议尺寸 / suggested size.
        """
        base = super().sizeHint()
        label, bar, button = self._parts()
        height = self.MARGIN_V * 2
        heights = [w.sizeHint().height() for w in (label, bar, button) if w is not None]
        height += sum(heights) + self.SPACING * max(0, len(heights) - 1)
        width = max(base.width(), self.MIN_WIDTH)
        if label is not None:
            width = max(width, label.sizeHint().width() + 2 * self.MARGIN_H)
        return QSize(width, max(base.height(), height))

    def _relayout(self) -> None:
        """
        自上而下摆放:文字(占满宽)→ 进度条(占满宽)→ 取消按钮(靠右)。
        Place top-down: label (full width) → bar (full width) → button (right).
        """
        label, bar, button = self._parts()
        x = self.MARGIN_H
        w = max(0, self.width() - 2 * self.MARGIN_H)
        y = self.MARGIN_V
        if label is not None:
            h = label.sizeHint().height()
            label.setGeometry(x, y, w, h)
            y += h + self.SPACING
        if bar is not None:
            h = bar.sizeHint().height()
            bar.setGeometry(x, y, w, h)
            y += h + self.SPACING
        if button is not None:
            hint = button.sizeHint()
            button.setGeometry(x + w - hint.width(), y, hint.width(), hint.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        self._relayout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._relayout()

    def setLabelText(self, text: str) -> None:
        """改文字后 Qt 会重排,之后再按本类摆法摆一次 / re-place after Qt's relayout."""
        super().setLabelText(text)
        if self.sizeHint().height() > self.height() or self.sizeHint().width() > self.width():
            self.resize(self.sizeHint().expandedTo(self.size()))
        self._relayout()


class DeleteConfirmDialog(QDialog):
    """
    删除确认框:正文 + 可选文字链接行 + 可选勾选框 + 一行按钮(危险项靠左,
    其余靠右),按钮宽度按文字自动算。

    QMessageBox 在 macOS 上会限制对话框宽度并把按钮压成等宽,放四个选项
    (取消 / 展开连拍 / 永久删除 / 移到废纸篓)时按钮互相重叠、文字被截断;
    StyledMessageBox 的按钮固定 180px、对话框最宽 500px,同样放不下。
    这里用标准布局,把「展开连拍，逐张挑选」这类不删除的选项做成正文下方
    单独一行的文字链接。

    A delete confirmation: message, optional link row, optional checkbox
    and one button row (danger left, others right), buttons sized to their
    text. QMessageBox on macOS caps the width and equalises button widths,
    so four choices overlapped and clipped; StyledMessageBox uses fixed
    180px buttons in a 500px dialog. Non-deleting choices such as "Expand
    bursts to choose" become a text link on their own row.

    用法 / Usage::

        dlg = DeleteConfirmDialog(parent, title, message,
                                  buttons=[("cancel", "取消", "secondary"),
                                           ("trash", "移到废纸篓", "primary")],
                                  default_key="trash")
        dlg.exec()
        dlg.choice        # "trash" / "cancel" / None(Esc 或关窗)
        dlg.checked       # 勾选框状态 / checkbox state
    """

    def __init__(self, parent=None, title: str = "", message: str = "",
                 buttons: list = None, default_key: str = None,
                 links: list = None, checkbox_text: str = None):
        """
        参数 / Parameters:
            parent: 父窗口 / parent widget.
            title (str): 标题(窗口标题与正文首行)/ title.
            message (str): 正文 / message body.
            buttons (list): [(key, 文字, 样式)],样式为 "primary" / "secondary" /
                "danger";danger 放在最左 / (key, text, style); danger goes left.
            default_key (str): 回车触发的按钮 / button triggered by Return.
            links (list): [(key, 文字)] 正文下方的文字链接 / text links below.
            checkbox_text (str): 勾选框文字,None 为不显示 / checkbox label.
        """
        super().__init__(parent)
        self.choice = None
        self._buttons = {}
        self._checkbox = None
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(640)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_primary']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)

        if title:
            title_label = QLabel(title)
            title_label.setWordWrap(True)
            title_label.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 16px; font-weight: 600; "
                f"font-family: {FONTS['sans']}; background: transparent;")
            layout.addWidget(title_label)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setTextFormat(Qt.PlainText)
        msg_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        msg_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 13px; "
            f"font-family: {FONTS['sans']}; background: transparent;")
        layout.addWidget(msg_label)

        if links:
            link_row = QHBoxLayout()
            link_row.setSpacing(16)
            for key, text in links:
                link = QPushButton(text)
                link.setFlat(True)
                link.setCursor(Qt.PointingHandCursor)
                link.setAutoDefault(False)
                link.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {COLORS['accent']}; border: none; "
                    f"padding: 2px 0; min-height: 0; font-size: 13px; font-weight: 500; text-align: left; }}"
                    f"QPushButton:hover {{ color: {COLORS['accent_hover']}; text-decoration: underline; }}")
                link.clicked.connect(lambda _c=False, k=key: self.choose(k))
                self._buttons[key] = link
                link_row.addWidget(link)
            link_row.addStretch()
            layout.addLayout(link_row)

        if checkbox_text:
            from ui.icon_utils import checkbox_indicator_qss
            self._checkbox = QCheckBox(checkbox_text)
            self._checkbox.setStyleSheet(
                f"QCheckBox {{ color: {COLORS['text_muted']}; font-size: 12px; background: transparent; }}"
                + checkbox_indicator_qss())
            layout.addWidget(self._checkbox)

        layout.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        buttons = buttons or []
        danger = [b for b in buttons if b[2] == "danger"]
        others = [b for b in buttons if b[2] != "danger"]
        for key, text, style in danger:
            btn_row.addWidget(self._make_button(key, text, style))
        btn_row.addStretch()
        for key, text, style in others:
            btn_row.addWidget(self._make_button(key, text, style))
        layout.addLayout(btn_row)

        for key, btn in self._buttons.items():
            if isinstance(btn, QPushButton) and not btn.isFlat():
                is_default = (key == default_key)
                btn.setAutoDefault(is_default)
                btn.setDefault(is_default)
        if default_key in self._buttons:
            self._buttons[default_key].setFocus()
        self.adjustSize()

    def _make_button(self, key: str, text: str, style: str) -> QPushButton:
        """
        按样式造一个按钮,宽度按文字算 / build a button sized to its text.

        参数 / Parameters:
            key (str): 选项键 / choice key.
            text (str): 文字 / label.
            style (str): "primary" / "secondary" / "danger".

        返回 / Returns:
            QPushButton: 按钮 / the button.
        """
        bg, fg, hover = {
            "primary": (COLORS['accent'], COLORS['bg_void'], COLORS['accent_hover']),
            "danger": (COLORS['error'], "#ffffff", COLORS['error']),
        }.get(style, (COLORS['bg_card'], COLORS['text_primary'], COLORS['bg_elevated']))
        border = f"1px solid {COLORS['border']}" if style == "secondary" else "none"
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(38)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {bg}; color: {fg}; border: {border}; border-radius: 8px; "
            f"padding: 8px 20px; min-height: 0; font-size: 14px; font-weight: 500; font-family: {FONTS['sans']}; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}")
        btn.setMinimumWidth(btn.fontMetrics().horizontalAdvance(text) + 2 * 20 + 12)
        btn.clicked.connect(lambda _c=False, k=key: self.choose(k))
        self._buttons[key] = btn
        return btn

    def choose(self, key: str) -> None:
        """
        记下所选项并关闭(Esc / 关窗时 choice 保持 None)。
        Record the choice and close (choice stays None on Esc / close).

        参数 / Parameters:
            key (str): 选项键 / choice key.
        """
        self.choice = key
        self.accept()

    @property
    def checked(self) -> bool:
        """勾选框是否勾上(没有勾选框时为 False)/ checkbox state."""
        return bool(self._checkbox is not None and self._checkbox.isChecked())

    def button_keys(self) -> list:
        """所有选项键(含链接),供测试与调试 / all choice keys."""
        return list(self._buttons)

    def default_key(self) -> str:
        """当前默认按钮的键 / key of the default button."""
        for key, btn in self._buttons.items():
            if btn.isDefault():
                return key
        return None

    def has_checkbox(self) -> bool:
        """是否带勾选框 / whether a checkbox is shown."""
        return self._checkbox is not None

