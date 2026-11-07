# -*- coding: utf-8 -*-
"""
多目录列表控件：显示待处理目录（含照片数）、添加/移除、拖放，并汇总总数。

Multi-folder list widget: shows folders to process (with photo counts),
add/remove, drag & drop and a total summary.
"""
import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFontMetrics
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core import folder_queue
from core.folder_queue import normalize_folder
from core.recursive_scanner import DEFAULT_SCAN_MAX_DEPTH, scan_directories
from tools.i18n import get_i18n
from ui.custom_dialogs import StyledMessageBox
from ui.styles import COLORS


def count_photos(path: str) -> int:
    """
    统计目录（递归）下的照片总数。

    参数: path (str) 目录。返回: int 照片数（扫描失败返回 0）。

    Count photos under a folder (recursive). Returns 0 if scanning fails.
    """
    try:
        return sum(d.photo_count for d in scan_directories(path, max_depth=DEFAULT_SCAN_MAX_DEPTH))
    except Exception:
        return 0


def folder_row_count(path: str) -> Optional[int]:
    """
    列表行显示的照片数：先按扫描计数；扫描为 0 但目录已处理过（或有续跑进度）时，
    照片都在被扫描排除的星级/鸟种目录里，改用 report.db 的只读计数
    （summarize_directories，不经 ReportDB、不触发 schema 升级）。

    参数: path (str) 目录。
    返回: Optional[int] 照片数；已处理但 report.db 读不出时为 None（行内显示「已处理」）。

    Photo count shown in a row: the scan count; when that is 0 but the folder
    is processed/resumable, a read-only report.db count (no schema
    migration). Returns None when processed but unreadable (row shows
    "processed").
    """
    n = count_photos(path)
    if n or folder_queue.classify_folder(path) == folder_queue.STATE_FRESH:
        return n
    try:
        from tools.merged_report_db import summarize_directories
        return summarize_directories([path])[0]["photos"]
    except Exception:
        return None


class ElidedLabel(QLabel):
    """
    单行标签：文字过长时中间省略，最小宽度不随文字增长（避免撑宽窗口）。

    Single-line label that middle-elides long text and whose minimum width does
    not grow with the text (so long paths never widen the window).
    """

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        """参数: text 完整文字 / Parameters: text full text."""
        super().__init__(parent)
        self._full = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._apply()

    def full_text(self) -> str:
        """返回未省略的完整文字 / The full, un-elided text."""
        return self._full

    def _apply(self) -> None:
        """按当前宽度重算省略文字 / Recompute the elided text for the width."""
        width = max(self.width(), 0)
        shown = QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, width) if width else self._full
        super().setText(shown)

    def resizeEvent(self, event) -> None:
        """宽度变化时重算 / Recompute on resize."""
        super().resizeEvent(event)
        self._apply()

    def minimumSizeHint(self):
        """最小宽度恒为 0 / Minimum width is always 0."""
        h = super().minimumSizeHint()
        h.setWidth(0)
        return h


class FolderListWidget(QWidget):
    """
    目录列表：每行「📁 路径 · N 张」+ ✕；下方「＋ 添加目录…」与合计。

    Folder list: one row per folder plus a remove button, an add button and a total.
    """

    foldersChanged = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """参数: parent 父控件 / Parameters: parent widget."""
        super().__init__(parent)
        self.i18n = get_i18n()
        self._folders: List[str] = []
        self._counts: Dict[str, Optional[int]] = {}
        self._editable = True
        self._remove_btns: List[QPushButton] = []
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(4)
        self._layout.addLayout(self._rows_box)

        footer = QHBoxLayout()
        self._add_btn = QPushButton(self.i18n.t("labels.add_folder"))
        self._add_btn.setObjectName("secondary")
        self._add_btn.clicked.connect(self._on_add_clicked)
        self._total_label = QLabel("")
        self._total_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        footer.addWidget(self._add_btn)
        footer.addStretch(1)
        footer.addWidget(self._total_label)
        self._layout.addLayout(footer)
        self._rebuild()

    # ---- 公共接口 / Public API ----
    def folders(self) -> List[str]:
        """按添加顺序返回目录副本 / Folders in insertion order (copy)."""
        return list(self._folders)

    def add_button(self) -> QPushButton:
        """返回「添加目录」按钮 / The add-folder button."""
        return self._add_btn

    def remove_buttons(self) -> List[QPushButton]:
        """返回各行 ✕ 按钮（行顺序）/ Per-row remove buttons in row order."""
        return list(self._remove_btns)

    def set_enabled_editing(self, on: bool) -> None:
        """处理中禁用添加与移除 / Disable add/remove while processing."""
        self._editable = bool(on)
        self._add_btn.setEnabled(self._editable)
        for btn in self._remove_btns:
            btn.setEnabled(self._editable)

    def try_add(self, path: str) -> Optional[str]:
        """
        校验并追加目录；被拒时弹窗提示（重复添加例外：静默忽略）。

        参数: path (str) 目录。
        返回: None 表示已加入（并发出 foldersChanged）；否则 REJECT_* 代码。

        Validate and append a folder; show a dialog on rejection.
        Returns None on success (emits foldersChanged) or a REJECT_* code.
        """
        code = folder_queue.validate_new_folder(self._folders, path)
        if code == folder_queue.REJECT_DUPLICATE:
            # 设计约定：重复添加静默忽略（不弹窗/不追加/不发信号）
            # Spec: duplicate adds are silently ignored (no dialog/append/signal)
            return code
        if code:
            StyledMessageBox.warning(
                self, self.i18n.t("messages.hint"),
                self.i18n.t(f"labels.folder_reject_{code}", directory=path))
            return code
        norm = normalize_folder(path)
        self._folders.append(norm)
        self._counts[norm] = folder_row_count(norm)
        self._rebuild()
        self.foldersChanged.emit(self.folders())
        return None

    def set_folders(self, paths: List[str]) -> List[str]:
        """
        静默设置目录列表（用于恢复最近目录）：不弹窗；不存在的返回，
        重复/重叠/危险/无照片的静默跳过。仅发出一次 foldersChanged。

        参数: paths 目录列表。返回: 因不存在而被跳过的目录。

        Silently replace the list (e.g. restoring recents): no dialogs; missing
        folders are returned, other invalid ones skipped silently. Emits once.
        """
        self._folders = []
        self._counts = {}
        missing: List[str] = []
        for p in paths:
            code = folder_queue.validate_new_folder(self._folders, p)
            if code == folder_queue.REJECT_MISSING:
                missing.append(p)
            elif code is None:
                norm = normalize_folder(p)
                self._folders.append(norm)
                self._counts[norm] = folder_row_count(norm)
        self._rebuild()
        self.foldersChanged.emit(self.folders())
        return missing

    # ---- 内部 / Internals ----
    def _on_add_clicked(self) -> None:
        """弹出目录选择框 / Open the directory picker."""
        start = self._folders[-1] if self._folders else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, self.i18n.t("labels.add_folder"), start)
        if path:
            self.try_add(path)

    def _remove(self, path: str) -> None:
        """移除一行并发信号 / Remove a row and emit."""
        if path in self._folders:
            self._folders.remove(path)
            self._counts.pop(path, None)
            self._rebuild()
            self.foldersChanged.emit(self.folders())

    def _rebuild(self) -> None:
        """按当前目录重建各行与合计 / Rebuild rows and the total."""
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._remove_btns = []
        for path in self._folders:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 2, 4, 2)
            h.setSpacing(6)
            n = self._counts.get(path, 0)
            # None：已处理但读不出张数，显示「已处理」/ processed, count unreadable
            count_text = (self.i18n.t("labels.folder_processed") if n is None
                          else self.i18n.t("labels.folder_photos", n=n))
            text = f"📁 {path} · {count_text}"
            lbl = ElidedLabel(text)
            lbl.setToolTip(path)
            lbl.setTextInteractionFlags(Qt.NoTextInteraction)
            lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
            btn = QPushButton("✕")
            btn.setObjectName("folderRemoveBtn")
            # 全局样式给按钮 44px 最小高度，必须显式压回，否则会被撑开重叠
            # Global QSS gives buttons a large min-height; override explicitly.
            btn.setFixedSize(24, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton#folderRemoveBtn {{ min-height: 0px; padding: 0px; border: none;"
                f" background: transparent; color: {COLORS['text_muted']}; font-size: 13px; }}"
                f"QPushButton#folderRemoveBtn:hover {{ color: {COLORS['text_primary']}; }}")
            btn.setEnabled(self._editable)
            btn.clicked.connect(lambda _=False, p=path: self._remove(p))
            h.addWidget(lbl, 1)
            h.addWidget(btn, 0)
            row.setStyleSheet(f"background-color: {COLORS['bg_card']}; border-radius: 6px;")
            self._rows_box.addWidget(row)
            self._remove_btns.append(btn)
        total = sum(self._counts.get(p) or 0 for p in self._folders)
        self._total_label.setText(
            self.i18n.t("labels.folder_total", count=len(self._folders), photos=total))

    # ---- 拖放 / Drag & drop ----
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """仅接受本地目录 / Accept local directories only."""
        if self._editable and event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile() and os.path.isdir(urls[0].toLocalFile()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """逐个尝试加入拖入的目录 / Try to add each dropped directory."""
        added = False
        for url in event.mimeData().urls():
            if url.isLocalFile() and os.path.isdir(url.toLocalFile()):
                self.try_add(url.toLocalFile())
                added = True
        if added:
            event.acceptProposedAction()
        else:
            event.ignore()
