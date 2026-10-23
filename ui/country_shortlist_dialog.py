# -*- coding: utf-8 -*-
"""
编辑识鸟「常用国家」（#143）：列出最近选过的国家，可逐个移除。本机所在国（系统地区）总是自动排第一，
不在这里列出，也不能移除。

Edit the Bird-ID country shortlist (#143): lists recently picked countries,
each removable. The user's own country (OS region) always comes first
automatically and is not listed here.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)


class CountryShortlistDialog(QDialog):
    """
    常用国家编辑框。exec() 返回 Accepted 后用 result_codes() 取编辑后的列表（最新在前）。

    Shortlist editor; after exec() returns Accepted, result_codes() gives the
    edited list (newest first).
    """

    def __init__(self, i18n, codes: List[str], names: Dict[str, str], parent=None) -> None:
        """
        参数:
        i18n: 译文对象（提供 t(key)）
        codes (list[str]): 最近选过的国家代码，最新在前
        names (dict): 国家代码 → 显示名
        parent: 父窗口
        """
        super().__init__(parent)
        t = i18n.t
        self.setWindowTitle(t("settings.birdid_shortlist_title"))
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        hint = QLabel(t("settings.birdid_shortlist_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        for code in codes:
            item = QListWidgetItem(names.get(code, code))
            item.setData(Qt.ItemDataRole.UserRole, code)
            self._list.addItem(item)
        self._empty = QLabel(t("settings.birdid_shortlist_empty"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._empty)

        buttons = QHBoxLayout()
        self._remove_btn = QPushButton(t("settings.birdid_shortlist_remove"))
        self._remove_btn.clicked.connect(self.remove_selected)
        done_btn = QPushButton(t("settings.done"))
        done_btn.setDefault(True)
        done_btn.clicked.connect(self.accept)
        buttons.addWidget(self._remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(done_btn)
        layout.addLayout(buttons)

        self._list.currentRowChanged.connect(lambda _row: self._sync_state())
        self._sync_state()

    def remove_selected(self) -> None:
        """移除选中的国家 / Remove the selected country."""
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
        self._sync_state()

    def result_codes(self) -> List[str]:
        """编辑后的国家代码（最新在前）/ Edited codes, newest first."""
        return [self._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._list.count())]

    def _sync_state(self) -> None:
        """空列表时显示提示、移除按钮按有无选中启用 / Empty hint and remove-button state."""
        empty = self._list.count() == 0
        self._list.setVisible(not empty)
        self._empty.setVisible(empty)
        self._remove_btn.setEnabled(self._list.currentRow() >= 0)
