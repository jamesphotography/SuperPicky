# -*- coding: utf-8 -*-
"""窗口几何工具：把窗口矩形夹进屏幕可用区域。

Window geometry helpers: clamp a window rectangle into a screen's available area.

这些函数是纯几何计算，只依赖 QtCore 的 QRect/QSize，不接触任何窗口实例，
因此无需 QApplication 即可单独测试。

These helpers are pure geometry over QtCore's QRect/QSize and never touch a
window instance, so they can be unit-tested without a QApplication.
"""
from typing import Optional

from PySide6.QtCore import QRect, QSize


def clamp_rect_to_available(
    rect: QRect,
    available: QRect,
    minimum: Optional[QSize] = None,
) -> QRect:
    """
    把窗口矩形夹进屏幕可用区域，保证窗口完全可见、可操作。

    规则：
    1. 尺寸先缩到不超过可用区域；若给了 minimum，则不缩到 minimum 以下
       （可用区域比 minimum 还小的极端机器上，宁可超出也要保住布局下限，
       此时靠第 2 步的顶左对齐让窗口顶部与主要控件尽量露出）。
    2. 位置平移到矩形完全落在可用区域内；装不下的方向对齐可用区域的左/上边，
       因为窗口底部通常是「开始处理」这类关键按钮，被 Dock 切掉后无法点击。

    参数:
    rect (QRect): 待夹取的窗口矩形（屏幕坐标）。
    available (QRect): 目标屏幕的可用区域（已扣除菜单栏/任务栏/Dock）。
    minimum (Optional[QSize]): 窗口布局的最小尺寸；None 表示不设下限。

    返回:
    QRect: 夹取后的矩形；当 available 无效时原样返回 rect。

    Clamp a window rectangle into a screen's available area so the window stays
    fully visible and operable.

    Rules:
    1. Shrink the size to fit the available area, but never below `minimum`
       (on machines whose available area is smaller than the layout minimum we
       keep the minimum and rely on rule 2's top-left alignment instead).
    2. Translate the rectangle so it lands fully inside the available area,
       aligning to the left/top edge on any axis that cannot fit, because the
       bottom of the window holds critical buttons that a Dock would cut off.

    Parameters:
    rect (QRect): Window rectangle to clamp, in screen coordinates.
    available (QRect): Target screen's available area (menu bar / Dock excluded).
    minimum (Optional[QSize]): Layout minimum size; None means no lower bound.

    Return:
    QRect: The clamped rectangle; `rect` unchanged when `available` is invalid.
    """
    if not available.isValid():
        return QRect(rect)

    width = min(rect.width(), available.width())
    height = min(rect.height(), available.height())
    if minimum is not None:
        width = max(width, minimum.width())
        height = max(height, minimum.height())

    # 装得下就平移回可用区内，装不下就顶左对齐（保住顶部内容与关键按钮的可见性）
    # Fit → translate inside; overflow → align top-left to keep key controls visible.
    if width <= available.width():
        x = min(max(rect.x(), available.x()), available.right() - width + 1)
    else:
        x = available.x()

    if height <= available.height():
        y = min(max(rect.y(), available.y()), available.bottom() - height + 1)
    else:
        y = available.y()

    return QRect(x, y, width, height)
