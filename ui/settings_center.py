# -*- coding: utf-8 -*-
"""
统一设置中心:左侧分类导航 + 右侧内容页。

取代旧高级设置/技能等级/关于弹窗入口,后续 Task 3-6 用真实页替换占位页。

Unified Settings Center: left-side category nav + right-side content pages.

Replaces the old advanced settings / skill level / about dialog entry points;
Tasks 3-6 will replace the placeholder pages with real content.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.icon_utils import ICON_ACTIVE, ICON_IDLE, load_tinted_icon  # noqa: F401
from ui.styles import COLORS  # noqa: F401

# ── 常量 / Constants ──────────────────────────────────────────────────────────

PAGE_ORDER: list[str] = ["culling", "birdid", "output", "video", "apps", "about"]

_PAGE_ICON: dict[str, str] = {
    "culling": "gem.svg",
    "birdid": "bird.svg",
    "output": "download.svg",
    "video": "video.svg",
    "apps": "layout-grid.svg",
    "about": "info.svg",
}

_PAGE_TITLE_KEY: dict[str, str] = {
    "culling": "settings.nav_culling",
    "birdid": "settings.nav_birdid",
    "output": "settings.nav_output",
    "video": "settings.nav_video",
    "apps": "settings.nav_apps",
    "about": "settings.nav_about",
}


# ── 主对话框 / Main Dialog ────────────────────────────────────────────────────


class SettingsCenter(QDialog):
    """
    统一设置中心对话框。

    左侧 QListWidget 导航,右侧 QStackedWidget 内容区。
    各分页由 _build_page(key) 分发构建; Task 3-6 仅需替换对应分支。

    Unified settings center dialog.

    Left: QListWidget navigation. Right: QStackedWidget content area.
    Each page is dispatched via _build_page(key); Tasks 3-6 only need
    to replace the relevant branch.

    参数 / Parameters:
        i18n: i18n 实例,提供 .t(key) 方法 / i18n instance with .t(key) method.
        parent: 父窗口 / Parent widget.
        start_page: 初始显示的页 key / Initial page key to show.
    """

    def __init__(self, i18n, parent: QWidget | None = None, start_page: str = "culling") -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.t("settings.window_title"))
        self.resize(760, 560)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航 / Left-side navigation
        self._nav = QListWidget()
        self._nav.setFixedWidth(160)
        for key in PAGE_ORDER:
            item = QListWidgetItem(
                load_tinted_icon(_PAGE_ICON[key], ICON_IDLE, 18),
                "  " + i18n.t(_PAGE_TITLE_KEY[key]),
            )
            item.setData(Qt.UserRole, key)
            self._nav.addItem(item)
        root.addWidget(self._nav)

        # 右侧内容区 / Right-side content area
        right = QVBoxLayout()

        self._stack = QStackedWidget()
        for key in PAGE_ORDER:
            self._stack.addWidget(self._build_page(key))
        right.addWidget(self._stack, 1)

        # 底部完成按钮 / Bottom Done button
        footer = QHBoxLayout()
        footer.addStretch(1)
        done_btn = QPushButton(i18n.t("settings.done"))
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)
        right.addLayout(footer)

        root.addLayout(right, 1)

        # 导航切换联动 stack / Wire nav row change to stack
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)

        self.show_page(start_page)

    # ── 页面构建 / Page construction ─────────────────────────────────────────

    def _build_page(self, key: str) -> QWidget:
        """
        根据 key 构建对应内容页。

        Task 3-6 在此添加各自真实页的分支;其余保持占位。

        Build the content page for the given key.

        Tasks 3-6 add their real page branches here; others remain as placeholders.

        参数 / Parameters:
            key (str): 页面标识符,取自 PAGE_ORDER / Page identifier from PAGE_ORDER.

        返回 / Returns:
            QWidget: 内容页 widget / Content page widget.
        """
        # 后续各 Task 在此插入各自分支,例如:
        # if key == "culling":
        #     return CullingPage(self.i18n)
        return self._placeholder(self.i18n.t(_PAGE_TITLE_KEY[key]))

    def _placeholder(self, title: str) -> QWidget:
        """
        生成占位页(仅显示分页标题标签)。

        Generate a placeholder page (shows only the page title label).

        参数 / Parameters:
            title (str): 分页标题 / Page title.

        返回 / Returns:
            QWidget: 占位内容页 / Placeholder content page.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(title))
        lay.addStretch(1)
        return page

    # ── 外部接口 / Public API ─────────────────────────────────────────────────

    def show_page(self, key: str) -> None:
        """
        切换到指定 key 对应的页。

        供外部(header chip、识鸟面板等)调用以跳转到指定设置分页。

        Switch to the page identified by key.

        Called externally (header chip, bird-ID panel, etc.) to jump to a
        specific settings page.

        参数 / Parameters:
            key (str): 目标页 key,必须是 PAGE_ORDER 中的值 / Target page key from PAGE_ORDER.
        """
        if key in PAGE_ORDER:
            self._nav.setCurrentRow(PAGE_ORDER.index(key))
