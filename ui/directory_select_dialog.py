# -*- coding: utf-8 -*-
"""
多目录合并前的目录清单对话框。

这不是「在某个父目录下挑几个子目录」——要合并的批次常常压根不在同一个父目录
下：不同年份文件夹、甚至不同硬盘（外拍回来当天导到移动盘、后来又导了一批到
内置盘）。把选择限死在一个父目录里，用户就只能分几次打开、然后自己心算合计。

所以这里是一份可增删的目录清单：反复点「添加目录…」把任意位置的目录攒进来，
再勾选真正要合并的那些。添加的若是父目录，就问一句「里面有 N 个批次，全部
加入吗」——拖一个年份文件夹进来是常见的偷懒方式，不该逼他一个个加。

每行显示照片数与鸟种数，让人不必打开就知道量有多大；读不出来的目录显示为
「—」而不是 0，因为 0 会被误读成「这天没拍到」。

A build-your-own list of batch directories: the folders to merge often live in
unrelated places (or on different volumes), so confining the choice to one
parent folder does not match how people actually shoot.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from ui.styles import COLORS


def _norm(path: str) -> str:
    """
    目录路径归一化，用于判重。

    尾部分隔符、``.`` 段、相对路径都会让同一个目录看起来像两个，重复加入就会
    把它的照片算两遍。
    Normalize a directory path so the same folder cannot be added twice.

    参数 / Args:
        path: 任意形式的目录路径

    返回 / Returns:
        str: 绝对且规范化的路径
    """
    return os.path.normpath(os.path.abspath(path))


class DirectorySelectDialog(QDialog):
    """
    让用户攒出一份要合并的目录清单，并勾选其中要用的。

    参数 / Args:
        i18n:    全局 i18n 实例
        entries: 预填的目录（summarize_directories() 的结果，可为空）
        parent:  父窗口

    用法 / Usage:
        dlg = DirectorySelectDialog(i18n, entries, self)
        if dlg.exec() == QDialog.Accepted:
            dirs = dlg.selected_directories()
    """

    def __init__(self, i18n, entries: Optional[List[Dict[str, Any]]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        # 每项：{path, name, photos, species, checked}；path 已归一化
        self._rows: List[Dict[str, Any]] = []
        self._boxes: Dict[str, QCheckBox] = {}

        self.setWindowTitle(i18n.t("dir_select.title"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = QLabel(i18n.t("dir_select.header"))
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{COLORS['text_primary']};font-size:13px;")
        root.addWidget(head)

        # 目录清单（可滚动）/ Scrollable directory list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        root.addWidget(self._scroll, 1)

        # 「添加目录…」+ 全选/全不选 / Add + quick actions
        actions = QHBoxLayout()
        add_btn = QPushButton(i18n.t("dir_select.add"))
        add_btn.clicked.connect(self.browse_and_add)
        actions.addWidget(add_btn)
        for key, handler in (
            ("dir_select.all", self.select_all),
            ("dir_select.none", self.select_none),
        ):
            btn = QPushButton(i18n.t(key))
            btn.setObjectName("secondary")
            btn.clicked.connect(handler)
            actions.addWidget(btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self._summary = QLabel("")
        self._summary.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;")
        root.addWidget(self._summary)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText(i18n.t("dir_select.open"))
        buttons.button(QDialogButtonBox.Cancel).setText(i18n.t("buttons.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for entry in (entries or []):
            self._append_entry(entry)
        self._rebuild()

    # ── 对外接口 / Public API ──────────────────────────────────────────────

    def add_directory(self, directory: str) -> int:
        """
        把一个目录加入清单。

        三种情况 / Three cases:
          * 目录本身是批次     → 直接加它一行（内部若还嵌着批次，那是重复副本，
                                 find_processed_subdirs 已只取外层，不再多问）
          * 目录下有若干批次   → 问一句是否全部加入，确认后一次性加入
          * 什么结果都没有     → 明确告知用户，而不是静悄悄地毫无反应

        参数 / Args:
            directory: 用户挑中的目录

        返回 / Returns:
            int: 实际新增的行数（已在清单里的不重复计入）
        """
        from tools.merged_report_db import find_processed_subdirs, is_processed

        directory = _norm(directory)
        if not os.path.isdir(directory):
            self._notify_nothing_found(directory)
            return 0

        if is_processed(directory):
            found = [directory]
        else:
            found = find_processed_subdirs(directory)
            if not found:
                self._notify_nothing_found(directory)
                return 0
            if not self._confirm_add_batches(
                    os.path.basename(directory) or directory, len(found)):
                return 0

        added = self._add_paths(found)
        if added:
            self._rebuild()
        return added

    def browse_and_add(self) -> int:
        """
        弹系统目录选择框，把选中的目录加进清单。

        返回 / Returns:
            int: 新增的行数；用户取消时为 0
        """
        picked = self._pick_directory()
        return self.add_directory(picked) if picked else 0

    def remove_directory(self, path: str) -> None:
        """从清单里移除某个目录（不影响磁盘上的任何东西）。"""
        target = _norm(path)
        before = len(self._rows)
        self._rows = [r for r in self._rows if r["path"] != target]
        if len(self._rows) != before:
            self._rebuild()

    def directories(self) -> List[str]:
        """清单里的全部目录，按加入顺序（不论是否勾选）。"""
        return [r["path"] for r in self._rows]

    def selected_directories(self) -> List[str]:
        """勾选的目录绝对路径，顺序与清单一致。"""
        return [r["path"] for r in self._rows if r["checked"]]

    def set_checked(self, path: str, checked: bool) -> None:
        """设置某个目录的勾选状态。"""
        target = _norm(path)
        for row in self._rows:
            if row["path"] == target:
                row["checked"] = checked
                box = self._boxes.get(target)
                if box is not None:
                    box.setChecked(checked)

    def select_all(self) -> None:
        """全选。"""
        for row in self._rows:
            self.set_checked(row["path"], True)

    def select_none(self) -> None:
        """全不选。"""
        for row in self._rows:
            self.set_checked(row["path"], False)

    def row_labels(self) -> List[str]:
        """每一行的显示文案，顺序与清单一致（供测试与调试）。"""
        names = self._display_names()
        return [self._row_label(row, names[row["path"]]) for row in self._rows]

    def selection_summary(self) -> str:
        """当前选择的一句话概览，供底部显示。"""
        chosen = [r for r in self._rows if r["checked"]]
        total = sum(r.get("photos") or 0 for r in chosen)
        return self.i18n.t("dir_select.summary").format(
            count=len(chosen), photos=total)

    # ── 可替换的交互点 / Interaction hooks ─────────────────────────────────
    #
    # 三个会弹窗的动作单独拎成方法：既让「添加目录」的逻辑本身可被测试，
    # 也方便将来换成别的呈现方式。
    # Pulled out so the add logic itself stays testable without real dialogs.

    def _pick_directory(self) -> str:
        """
        弹系统目录选择框，返回选中的目录（取消时为空串）。

        起始位置取最后加入那个目录的上一级——接着加的多半是它的邻居（同一个
        年份文件夹里的另一天），从头翻起太累。清单还空着就回退到「图片」目录。
        Start next to the last added folder; its siblings are the likely picks.
        """
        if self._rows:
            start = os.path.dirname(self._rows[-1]["path"])
        else:
            start = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation) or ""
        if not os.path.isdir(start):
            start = ""
        return QFileDialog.getExistingDirectory(
            self, self.i18n.t("dir_select.add_title"), start,
            QFileDialog.Option.ShowDirsOnly)

    def _confirm_add_batches(self, name: str, count: int) -> bool:
        """问用户是否把该目录下的 N 个批次全部加入。"""
        from ui.custom_dialogs import StyledMessageBox

        return StyledMessageBox.question(
            self, self.i18n.t("dir_select.batch_prompt_title"),
            self.i18n.t("dir_select.batch_prompt").format(
                name=name, count=count))

    def _notify_nothing_found(self, path: str) -> None:
        """告知用户该目录里没有选鸟结果。"""
        from ui.custom_dialogs import StyledMessageBox

        StyledMessageBox.warning(
            self, self.i18n.t("messages.hint"),
            self.i18n.t("dir_select.nothing_found").format(
                name=os.path.basename(path) or path))

    # ── 内部 / Internals ───────────────────────────────────────────────────

    def _add_paths(self, paths: List[str]) -> int:
        """把若干目录并入清单，跳过已有的；返回实际新增数。"""
        from tools.merged_report_db import summarize_directories

        fresh = [p for p in (_norm(p) for p in paths)
                 if p not in {r["path"] for r in self._rows}]
        if not fresh:
            return 0
        for entry in summarize_directories(fresh):
            self._append_entry(entry)
        return len(fresh)

    def _append_entry(self, entry: Dict[str, Any]) -> None:
        """把一条概览结果转成清单里的一行（默认勾选）。"""
        path = _norm(entry["path"])
        if any(r["path"] == path for r in self._rows):
            return
        self._rows.append({
            "path": path,
            "name": entry.get("name") or os.path.basename(path),
            "photos": entry.get("photos"),
            "species": entry.get("species"),
            "checked": True,
        })

    def _display_names(self) -> Dict[str, str]:
        """
        每个目录用来显示的名字。

        同名目录（两个盘上都有 2026-09-01）只显示目录名的话，用户看到两行
        一模一样，无从判断勾掉哪一个——所以重名时带上上一级目录名。
        Same-named folders get their parent prefixed, or the rows are
        indistinguishable.
        """
        counts: Dict[str, int] = {}
        for row in self._rows:
            counts[row["name"]] = counts.get(row["name"], 0) + 1

        names: Dict[str, str] = {}
        for row in self._rows:
            if counts[row["name"]] > 1:
                parent = os.path.basename(os.path.dirname(row["path"]))
                names[row["path"]] = (f"{parent}/{row['name']}" if parent
                                      else row["path"])
            else:
                names[row["path"]] = row["name"]
        return names

    def _row_label(self, row: Dict[str, Any], name: str) -> str:
        """
        一行的显示文案：目录名 + 照片数 + 鸟种数。

        计数为 None 表示该目录的库读不出来，显示占位符而非 0——0 会被误读成
        「这天没拍到」。
        Unreadable batches show a dash, never 0.
        """
        if row.get("photos") is None:
            return self.i18n.t("dir_select.row_unreadable").format(name=name)
        return self.i18n.t("dir_select.row").format(
            name=name, photos=row["photos"], species=row.get("species") or 0)

    def _rebuild(self) -> None:
        """按当前清单重建列表区域。清单只有几十行，整块重建最省心。"""
        names = self._display_names()
        self._boxes.clear()

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        if not self._rows:
            empty = QLabel(self.i18n.t("dir_select.empty"))
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"color:{COLORS['text_secondary']};font-size:12px;padding:24px;")
            lay.addWidget(empty)
        else:
            for row in self._rows:
                lay.addLayout(self._build_row(row, names[row["path"]]))

        lay.addStretch(1)
        self._scroll.setWidget(inner)
        self._refresh_summary()

    def _build_row(self, row: Dict[str, Any], name: str) -> QHBoxLayout:
        """一行：勾选框（含文案）+ 右侧移除按钮。"""
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        box = QCheckBox(self._row_label(row, name))
        box.setChecked(row["checked"])
        box.setToolTip(row["path"])
        path = row["path"]
        box.toggled.connect(lambda on, p=path: self._on_toggled(p, on))
        self._boxes[path] = box
        line.addWidget(box, 1)

        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip(self.i18n.t("dir_select.remove"))
        remove.setCursor(Qt.PointingHandCursor)
        remove.setStyleSheet(
            f"QToolButton{{border:none;color:{COLORS['text_secondary']};"
            f"font-size:13px;padding:2px 6px;}}"
            f"QToolButton:hover{{color:{COLORS['text_primary']};}}")
        remove.clicked.connect(lambda _=False, p=path: self.remove_directory(p))
        line.addWidget(remove, 0)
        return line

    def _on_toggled(self, path: str, checked: bool) -> None:
        """勾选框变化时同步到数据行并刷新概览。"""
        for row in self._rows:
            if row["path"] == path:
                row["checked"] = checked
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """刷新底部的「已选 N 个 · 合计 M 张」。"""
        self._summary.setText(self.selection_summary())
