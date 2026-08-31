# -*- coding: utf-8 -*-
"""
下拉框弹出列表（popup）的深色外观接线。

背景 / Background
-----------------
Qt 并不是把 QComboBox 的弹出列表直接显示出来，而是把 itemView 装进一个
``QComboBoxPrivateContainer``（QFrame 的私有子类）里，再把这个容器作为**独立的
顶层 popup 窗口**弹出。容器比 itemView 高出约 12px，itemView 上下各留 6px。

这 6px 是问题所在：容器如果没有自己的样式表，macOS 会用**原生浅色菜单面板**
绘制它，于是深色列表的上下就各露出一条白边，观感是「白色不均匀、上下空太多」，
与原生 macOS 菜单差异明显。

为什么不能写在 GLOBAL_STYLE 里
------------------------------
容器是独立的顶层 popup 窗口，祖先样式表里的选择器够不到它。实测在
GLOBAL_STYLE 中写 ``QComboBoxPrivateContainer {...}``（或 ``QComboBox QFrame``）
完全无效——容器依旧由系统原生绘制，白边照旧。只有对容器实例本身调用
``setStyleSheet()`` 才能接管它的绘制，因此必须逐个下拉接线。

同理，任何**无选择器的裸样式表**（如 ``w.setStyleSheet("background: transparent;")``）
会传播到子树内所有控件，且「更近的祖先」优先级高于更远的祖先，这也是同一处白底
问题的另一个来源；本模块设置的是容器**自身**的样式表，优先级最高，不受其影响。

Qt does not show a combo's item view directly: it wraps the view in a
``QComboBoxPrivateContainer`` (a private QFrame subclass) shown as a separate
top-level popup window, ~12px taller than the view (6px above and below). With
no stylesheet of its own that container is painted by macOS with its native
light menu panel, leaking a white band above and below the dark list. Ancestor
stylesheets cannot reach a top-level popup — a ``QComboBoxPrivateContainer``
rule in GLOBAL_STYLE is verifiably a no-op — so each combo must be wired up
individually via :func:`style_combo_popup`.
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from ui.styles import COLORS


def combo_popup_qss(
    bg: str | None = None,
    border: str | None = None,
    radius: int = 8,
) -> str:
    """
    生成弹出列表容器的样式表。

    参数 / Parameters:
        bg (str | None): 容器背景色，默认取 COLORS['bg_elevated']。
        border (str | None): 容器描边色，默认取 COLORS['border']。
        radius (int): 圆角半径（px），默认 8，与 GLOBAL_STYLE 中
            ``QComboBox QAbstractItemView`` 的圆角保持一致。

    返回 / Return:
        str: 可直接交给容器 ``setStyleSheet()`` 的样式表字符串。

    Build the stylesheet for a combo popup container.
    """
    bg = bg or COLORS["bg_elevated"]
    border = border or COLORS["border"]
    return (
        f"QComboBoxPrivateContainer {{"
        f" background-color: {bg};"
        f" border: 1px solid {border};"
        f" border-radius: {radius}px;"
        f" }}"
    )


def style_combo_popup(
    combo: QComboBox,
    bg: str | None = None,
    border: str | None = None,
    radius: int = 8,
) -> bool:
    """
    给下拉框的弹出列表容器上深色样式，消除 macOS 原生浅色面板露出的白边。

    读取 ``combo.view()`` 会促使 Qt 创建容器，因此本函数可以在构造下拉之后
    立即调用，不必等到第一次弹出。函数是幂等的，重复调用只是重设同一份样式表。

    参数 / Parameters:
        combo (QComboBox): 目标下拉框。
        bg (str | None): 容器背景色，默认 COLORS['bg_elevated']。
        border (str | None): 容器描边色，默认 COLORS['border']。
        radius (int): 圆角半径（px），默认 8。

    返回 / Return:
        bool: 成功接线返回 True；拿不到容器（Qt 内部结构变化）时返回 False，
            此时弹出列表只是回落到系统原生外观，不会抛异常影响 UI 构建。

    Style a combo's popup container so macOS's native light panel no longer
    leaks white bands around the dark list. Idempotent; returns False if the
    container cannot be reached instead of raising.
    """
    view = combo.view()
    if view is None:
        return False
    container = view.parentWidget()
    if container is None:
        return False
    container.setStyleSheet(combo_popup_qss(bg, border, radius))
    return True
