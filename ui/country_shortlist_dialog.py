# -*- coding: utf-8 -*-
"""
编辑识鸟「常用国家」（#143）：列出常用国家，可逐个移除或恢复默认。
在「更多国家」里选的新国家会自动加进常用国家，不必在这里添加。

Edit the Bird-ID country shortlist (#143): remove entries one by one or
restore the defaults. Countries picked in "More countries" are added
automatically, so there is no add button here.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)


class CountryShortlistDialog(QDialog):
    """
    常用国家编辑框。exec() 返回 Accepted 后用 result_codes() 取结果：
    国家代码列表（按显示顺序），或 None 表示恢复默认。

    Shortlist editor; after exec() returns Accepted, result_codes() gives the
    codes in display order, or None when the user restored the defaults.
    """

    def __init__(self, i18n, codes: List[str], names: Dict[str, str],
                 default_codes: Sequence[str] = (), parent=None) -> None:
        """
        参数:
        i18n: 译文对象（提供 t(key)）
        codes (list[str]): 当前常用国家代码
        names (dict): 国家代码 → 显示名（含默认国家）
        default_codes (sequence): 恢复默认时显示的国家
        parent: 父窗口
        """
        super().__init__(parent)
        t = i18n.t
        self._names = names
        self._default_codes = list(default_codes)
        self._is_default = False
        self.setWindowTitle(t("settings.birdid_shortlist_title"))
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        hint = QLabel(t("settings.birdid_shortlist_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        self._fill(codes)
        self._empty = QLabel(t("settings.birdid_shortlist_empty"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._empty)

        buttons = QHBoxLayout()
        self._remove_btn = QPushButton(t("settings.birdid_shortlist_remove"))
        self._remove_btn.clicked.connect(self.remove_selected)
        reset_btn = QPushButton(t("settings.birdid_shortlist_reset"))
        reset_btn.clicked.connect(self.reset_to_defaults)
        done_btn = QPushButton(t("settings.done"))
        done_btn.setDefault(True)
        done_btn.clicked.connect(self.accept)
        buttons.addWidget(self._remove_btn)
        buttons.addWidget(reset_btn)
        buttons.addStretch(1)
        buttons.addWidget(done_btn)
        layout.addLayout(buttons)

        self._list.currentRowChanged.connect(lambda _row: self._sync_state())
        self._sync_state()

    def _fill(self, codes: List[str]) -> None:
        """按代码填列表 / Fill the list from codes."""
        self._list.clear()
        for code in codes:
            item = QListWidgetItem(self._names.get(code, code))
            item.setData(Qt.ItemDataRole.UserRole, code)
            self._list.addItem(item)

    def remove_selected(self) -> None:
        """移除选中的国家 / Remove the selected country."""
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            self._is_default = False
        self._sync_state()

    def reset_to_defaults(self) -> None:
        """恢复默认常用国家 / Restore the default shortlist."""
        self._fill(self._default_codes)
        self._is_default = True
        self._sync_state()

    def result_codes(self) -> Optional[List[str]]:
        """编辑结果：国家代码（按显示顺序），恢复默认后未再改动时为 None / Codes, or None for defaults."""
        if self._is_default:
            return None
        return [self._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._list.count())]

    def _sync_state(self) -> None:
        """空列表时显示提示、移除按钮按有无选中启用 / Empty hint and remove-button state."""
        empty = self._list.count() == 0
        self._list.setVisible(not empty)
        self._empty.setVisible(empty)
        self._remove_btn.setEnabled(self._list.currentRow() >= 0)
