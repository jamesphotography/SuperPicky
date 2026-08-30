# -*- coding: utf-8 -*-
"""
Qt 标准上下文菜单的深色适配。

问题 / Problem
--------------
QLineEdit / QTextEdit 等文本控件的右键菜单由 Qt 自己生成（Undo/Cut/Copy/
Paste/Delete/Select All；只读控件则只有 Copy 与 Select All）。这些菜单项的
图标来自平台主题（``edit-copy``、``edit-select-all`` 等），是为浅色界面画的
深灰描线图，落在本项目的深色菜单底上几乎看不见，与旁边的浅色文字明显不搭。

菜单项的**文字**颜色能用样式表控制（GLOBAL_STYLE 的 ``QMenu::item``），但
**图标**不行——Qt 样式表没有给 QAction 图标重新着色的属性。只能在菜单显示前
把图标像素重新染一遍。

判据 / How we tell them apart
-----------------------------
只染 ``QIcon.name()`` 非空的图标：这类图标来自平台主题（实测标准菜单里的
图标 name 为 ``edit-copy`` / ``edit-select-all``）。项目自建菜单的图标是用
``ui.icon_utils.load_tinted_icon()`` 从 svg 渲染的，name 恒为空串，且早已按
设计系统染好色，因此不会被这里碰到。

Qt builds the context menus of text widgets itself, using platform-theme icons
drawn for light interfaces; on this project's dark menus they are nearly
invisible. Stylesheets can recolour menu *text* but not action icons, so the
icons are re-tinted just before the menu is shown. Only icons with a non-empty
QIcon.name() are touched — those come from the platform theme, whereas the
project's own menu icons are rendered from svg and carry an empty name.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu

from ui.styles import COLORS


def tint_icon(icon: QIcon, color: str, size: int = 16) -> QIcon:
    """
    把一个已有 QIcon 重新染成指定颜色（保留其 alpha 形状）。

    与 ``icon_utils.load_tinted_icon`` 的区别：那个从 svg 文件渲染，这个作用于
    已经存在、拿不到源文件的 QIcon（例如平台主题图标）。

    参数 / Parameters:
        icon (QIcon): 源图标。
        color (str): 目标颜色，如 "#a0a0a0"。
        size (int): 逻辑尺寸（px）。

    返回 / Return:
        QIcon: 染色后的图标；源图标为空时原样返回。

    Re-tint an existing QIcon, preserving its alpha shape. Unlike
    load_tinted_icon (which renders from svg) this works on icons whose source
    file is not available, such as platform-theme icons.
    """
    if icon.isNull():
        return icon
    pm = icon.pixmap(QSize(size, size))
    if pm.isNull():
        return icon
    tinted = QPixmap(pm.size())
    tinted.setDevicePixelRatio(pm.devicePixelRatio())
    tinted.fill(Qt.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pm)
    # SourceIn：用纯色填满，但只保留原图不透明的部分（即图标形状）。
    # SourceIn keeps only the opaque pixels of the source, giving a flat tint.
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return QIcon(tinted)


def tint_theme_icons(menu: QMenu, color: str | None = None, size: int = 16) -> int:
    """
    把菜单里来自平台主题的图标染成菜单文字色。

    参数 / Parameters:
        menu (QMenu): 目标菜单。
        color (str | None): 目标颜色，默认 COLORS['text_secondary']（与
            GLOBAL_STYLE 中 ``QMenu::item`` 的文字同色）。
        size (int): 图标逻辑尺寸（px）。

    返回 / Return:
        int: 实际染色的菜单项数量，便于测试断言。

    Re-tint a menu's platform-theme icons to the menu text colour.
    """
    color = color or COLORS["text_secondary"]
    count = 0
    for action in menu.actions():
        if action.isSeparator():
            continue
        icon = action.icon()
        # name() 非空 = 平台主题图标；项目自建图标 name 为空，保持原样。
        if icon.isNull() or not icon.name():
            continue
        action.setIcon(tint_icon(icon, color, size))
        count += 1
    return count


class _StandardMenuIconTinter(QObject):
    """
    应用级事件过滤器：任何菜单显示前，把它的平台主题图标染成菜单文字色。

    走事件过滤器而不是逐个控件接线，是因为这些菜单由 Qt 在右键那一刻临时创建，
    项目代码里根本没有它们的引用——每个文本框、每个输入框都会生成一个。

    An application-wide event filter: Qt creates these menus on the fly at
    right-click time, so there is no per-widget object to wire up.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Show and isinstance(obj, QMenu):
            tint_theme_icons(obj)
        return False


_tinter: _StandardMenuIconTinter | None = None


def install_standard_menu_tinting(app: QApplication) -> None:
    """
    安装标准菜单图标染色（幂等，重复调用只安装一次）。

    参数 / Parameters:
        app (QApplication): 目标应用实例。

    Install the tinting filter (idempotent).
    """
    global _tinter
    if _tinter is not None:
        return
    _tinter = _StandardMenuIconTinter(app)
    app.installEventFilter(_tinter)
