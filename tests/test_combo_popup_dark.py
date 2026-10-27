# -*- coding: utf-8 -*-
"""
下拉框弹出列表（popup）深色外观回归测试。

背景 / Background
-----------------
Qt 把 QComboBox 的 itemView 装进 ``QComboBoxPrivateContainer``（QFrame 私有子类）
并作为**独立的顶层 popup 窗口**弹出，容器比 itemView 高约 12px（上下各 6px）。
容器若没有自己的样式表，macOS 用原生浅色菜单面板绘制它，深色列表上下便各露出
一条白边（用户反馈「白色不均匀、上下空太多」）。

关键结论（本次实测得出，测试即为固化它）：祖先样式表里的选择器**够不到**这个
顶层 popup——在 GLOBAL_STYLE 中写 ``QComboBoxPrivateContainer {...}`` 是无效的，
只有对容器实例本身调用 setStyleSheet 才生效。因此每个下拉都必须经
``ui.combo_popup.style_combo_popup()`` 单独接线，新增下拉忘记接线就会退回白边。

Qt shows a combo's item view inside a QComboBoxPrivateContainer presented as a
separate top-level popup window. Ancestor stylesheets cannot reach it, so each
combo must be wired through ``style_combo_popup()``; these tests pin that down.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QMainWindow, QLabel

from tools.i18n import get_i18n
from ui.combo_popup import style_combo_popup, combo_popup_qss
from ui.styles import COLORS, GLOBAL_STYLE

_app = QApplication.instance() or QApplication([])


def _popup_container(combo: QComboBox):
    """取下拉的弹出容器（读 view() 会促使 Qt 创建它）。"""
    view = combo.view()
    return None if view is None else view.parentWidget()


def test_style_combo_popup_paints_container_dark():
    """
    接线后，容器自身的样式表必须给出深色底 + 描边，而不是留空让系统原生绘制。

    After wiring, the container's own stylesheet must paint a dark panel.
    """
    combo = QComboBox()
    combo.addItems(["A", "B"])

    container = _popup_container(combo)
    assert container is not None
    assert container.styleSheet() == "", "接线前容器不应有样式表"

    assert style_combo_popup(combo) is True
    qss = container.styleSheet()
    assert COLORS["bg_elevated"] in qss, f"容器未上深色底: {qss}"
    assert COLORS["border"] in qss, f"容器缺描边: {qss}"
    assert "QComboBoxPrivateContainer" in qss


def test_style_combo_popup_is_idempotent():
    """重复接线只是重设同一份样式表，不应累加或抛异常。"""
    combo = QComboBox()
    combo.addItems(["A"])
    assert style_combo_popup(combo) is True
    first = _popup_container(combo).styleSheet()
    assert style_combo_popup(combo) is True
    assert _popup_container(combo).styleSheet() == first


def test_global_view_rule_has_no_border():
    """
    描边由容器负责，GLOBAL_STYLE 里的 itemView 规则不能再自带 border，
    否则容器描边 + 列表描边会叠成双层。

    The border belongs to the container; the view rule must not add its own.
    """
    start = GLOBAL_STYLE.index("QComboBox QAbstractItemView {")
    rule = GLOBAL_STYLE[start:GLOBAL_STYLE.index("}", start)]
    assert "border: none" in rule, f"itemView 规则不应自带描边: {rule}"


def test_every_combo_in_filter_panel_is_wired():
    """
    筛选面板里的每个下拉都必须接线（用户报告白边的正是这里的鸟种下拉）。

    Every combo in the browser's filter panel must be wired up.
    """
    from ui.filter_panel import FilterPanel

    win = QMainWindow()
    win.setStyleSheet(GLOBAL_STYLE)
    win.setCentralWidget(QLabel(""))
    panel = FilterPanel(get_i18n(), parent=win)
    panel.setParent(win.centralWidget())
    panel.show()

    combos = panel.findChildren(QComboBox)
    assert combos, "筛选面板里应当有下拉框"
    for combo in combos:
        container = _popup_container(combo)
        assert container is not None
        assert COLORS["bg_elevated"] in container.styleSheet(), (
            f"下拉 {combo.objectName() or combo} 的弹出容器未接线，会露出白边"
        )


def test_filter_panel_scoped_stylesheets_do_not_leak():
    """
    筛选面板的样式表必须带选择器：无选择器的裸声明会传播到子树内所有控件，
    连下拉的弹出容器也一并接管，把它打成透明后 macOS 会画原生白底。

    The panel's own sheets must be scoped; bare declarations propagate to every
    descendant, including combo popup containers.
    """
    from ui.filter_panel import FilterPanel

    panel = FilterPanel(get_i18n())
    sheet = panel.styleSheet()
    assert sheet.strip().startswith("QWidget#"), f"面板样式表缺选择器: {sheet}"


def test_hovered_item_is_clearly_highlighted():
    """
    #149：接线后悬停项要用强调色高亮，而不是与底色几乎相同的 #262626。

    Qt 默认的 QComboMenuDelegate 不认样式表的 ::item 规则；style_combo_popup
    换成 QStyledItemDelegate 后，GLOBAL_STYLE 里的悬停规则才生效。

    #149: after wiring, the hovered item uses the accent tint; the stock
    menu delegate ignored ::item rules and hover was nearly invisible.
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QMouseEvent

    previous_qss = _app.styleSheet()
    _app.setStyleSheet(GLOBAL_STYLE)
    combo = QComboBox()
    combo.addItems([f"Item {i}" for i in range(6)])
    style_combo_popup(combo)
    assert combo.itemDelegate().metaObject().className() == "QStyledItemDelegate"

    combo.show()
    combo.showPopup()
    QApplication.processEvents()
    try:
        view = combo.view()
        pos = view.visualRect(view.model().index(3, 0)).center()
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            QEvent.MouseMove, pos, view.viewport().mapToGlobal(pos),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        QApplication.processEvents()
        img = view.viewport().grab().toImage()

        def _bg(row: int) -> QColor:
            rect = view.visualRect(view.model().index(row, 0))
            return img.pixelColor(rect.left() + 3, rect.center().y())

        hovered, plain = _bg(3), _bg(0)
        # 强调色是青绿：悬停项绿通道要明显高于红通道，普通项则是中性灰
        # The accent is teal: the hovered row is clearly green-tinted, plain rows are grey.
        assert hovered.green() - hovered.red() >= 20, hovered.name()
        assert abs(plain.green() - plain.red()) <= 2, plain.name()
    finally:
        combo.hidePopup()
        _app.setStyleSheet(previous_qss)
