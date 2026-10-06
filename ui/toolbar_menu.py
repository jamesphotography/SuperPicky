#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 工具栏下拉菜单的两行菜单项 / Two-line items for toolbar menus.

QMenu 原生菜单项只能显示一行文字。选鸟结果浏览器的「导出 ▾」菜单需要在每个
功能名下面再写一行灰色小字，说清它作用于哪些照片、产出什么——例如「全部照片
→ HTML 文件」与「勾选或筛选出的照片」的区别，用户只看名字是猜不到的。
这里用 QWidgetAction 承载一个自绘的两行小部件，并自己处理悬停高亮、点击
触发与禁用外观。

Native QMenu items show a single line of text. The results browser's
"Export ▾" menu needs a muted second line under each entry stating which
photos it acts on and what it produces, so each item is a QWidgetAction
hosting a small two-line widget that handles hover, click and disabled
appearance itself.
"""

from typing import Optional

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget, QWidgetAction,
)

from ui.styles import COLORS


class _TwoLineItem(QWidget):
    """
    两行菜单项的显示部件：左侧图标，右侧「标题 + 灰色说明」。

    点击时触发所属 QWidgetAction 并关闭菜单；禁用时整体变灰且不响应点击。

    Display widget for a two-line menu item: icon on the left, title plus
    muted hint on the right. A click triggers the owning action and closes
    the menu; when disabled it greys out and ignores clicks.
    """

    def __init__(self, action: "TwoLineMenuAction", icon: Optional[QIcon],
                 title: str, hint: str, parent: Optional[QWidget] = None) -> None:
        """
        参数:
        action (TwoLineMenuAction): 拥有本部件的菜单动作，点击时调用其 trigger()
        icon (QIcon | None): 左侧图标，None 时不显示
        title (str): 第一行功能名
        hint (str): 第二行灰色说明
        parent (QWidget | None): 父部件

        Parameters:
        action (TwoLineMenuAction): owning action, triggered on click
        icon (QIcon | None): leading icon, omitted when None
        title (str): first-line feature name
        hint (str): muted second line
        parent (QWidget | None): parent widget
        """
        super().__init__(parent)
        self._action = action
        self._hovered = False
        # 让 QSS 的背景色生效（纯 QWidget 默认不画样式表背景）。
        # Plain QWidgets ignore stylesheet backgrounds without this.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 16, 8)
        row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(QSize(18, 18))
        # 全局样式给 QLabel 铺了底色，图标后面会露出一块深色方块。
        # The global sheet paints QLabel backgrounds; keep the icon cell clear.
        self._icon_label.setStyleSheet("background: transparent;")
        if icon is not None:
            self._icon_label.setPixmap(icon.pixmap(QSize(18, 18)))
        row.addWidget(self._icon_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self._title_label = QLabel(title)
        self._hint_label = QLabel(hint)
        text_col.addWidget(self._title_label)
        text_col.addWidget(self._hint_label)
        row.addLayout(text_col, 1)

        self._apply_style()

    def _apply_style(self) -> None:
        """按悬停/禁用状态刷新配色。/ Refresh colors for hover/disabled state."""
        enabled = self.isEnabled()
        bg = COLORS['bg_card'] if (self._hovered and enabled) else "transparent"
        title_color = COLORS['text_primary'] if enabled else COLORS['text_muted']
        hint_color = COLORS['text_tertiary'] if enabled else COLORS['text_muted']
        self.setStyleSheet(
            f"_TwoLineItem {{ background-color: {bg}; border-radius: 6px; }}"
        )
        self._title_label.setStyleSheet(
            f"color: {title_color}; font-size: 13px; background: transparent;"
        )
        self._hint_label.setStyleSheet(
            f"color: {hint_color}; font-size: 11px; background: transparent;"
        )

    def set_hovered(self, hovered: bool) -> None:
        """
        设置悬停高亮。/ Set hover highlight.

        参数:
        hovered (bool): 是否处于悬停状态

        Parameters:
        hovered (bool): whether the pointer is over the item
        """
        if hovered != self._hovered:
            self._hovered = hovered
            self._apply_style()

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt 命名)
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.set_hovered(False)
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.EnabledChange:
            self._apply_style()
        super().changeEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """
        左键松开时触发动作并收起菜单。

        先收起菜单再触发：导出会弹出文件对话框，菜单若还开着会挡在对话框前面。

        Close the menu before triggering: the export opens a file dialog and
        a still-open menu would sit on top of it.
        """
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.set_hovered(False)
            menu = self._action.parent()
            if isinstance(menu, QMenu):
                menu.hide()
            self._action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TwoLineMenuAction(QWidgetAction):
    """
    带两行文字（功能名 + 灰色说明）的菜单动作。

    用法与普通 QAction 相同：连接 triggered 信号、用 setEnabled() 控制可用性。
    setEnabled() 会同步到显示部件，使其变灰。

    A menu action rendered as two lines (name + muted hint). Use it like a
    QAction: connect ``triggered`` and call ``setEnabled()``; enabled state
    is mirrored onto the display widget.
    """

    def __init__(self, menu: QMenu, icon: Optional[QIcon], title: str,
                 hint: str, tooltip: str = "") -> None:
        """
        参数:
        menu (QMenu): 所属菜单（同时作为父对象，点击后据此收起菜单）
        icon (QIcon | None): 左侧图标
        title (str): 功能名
        hint (str): 灰色说明，一句话说明作用于哪些照片、产出什么
        tooltip (str): 鼠标停留时的详细说明气泡，空串则不显示

        Parameters:
        menu (QMenu): owning menu, also the parent used to close it on click
        icon (QIcon | None): leading icon
        title (str): feature name
        hint (str): muted one-line scope/output description
        tooltip (str): detailed hover bubble; empty for none
        """
        super().__init__(menu)
        self.setText(title)
        self._item = _TwoLineItem(self, icon, title, hint)
        if tooltip:
            self._item.setToolTip(tooltip)
        self.setDefaultWidget(self._item)
        self.changed.connect(self._sync_enabled)

    def _sync_enabled(self) -> None:
        """把动作的可用状态同步到显示部件。/ Mirror enabled state to the widget."""
        self._item.setEnabled(self.isEnabled())

    def item_widget(self) -> QWidget:
        """返回显示部件（测试用）。/ Return the display widget (for tests)."""
        return self._item

