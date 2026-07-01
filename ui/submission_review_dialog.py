# -*- coding: utf-8 -*-
"""
纠错样本复审窗（Submission Review Dialog）。

按鸟种分组显示「被改正图 + 建议正样本」，用户勾选/取消，显示总张数与
预估大小，「打包」按钮调 build_submission 产出桌面 zip。

Submission Review Dialog: shows the "corrected failed photo + suggested
positive samples" grouped by bird species. Users check/uncheck photos and
see the total count/estimated size; the "Pack" button calls build_submission
to produce a desktop zip.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Set

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QScrollArea, QWidget, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt

from ui.styles import COLORS
from tools.i18n import get_i18n
from core.submission_builder import (
    SubmissionItem, build_submission, EMAIL_SIZE_WARN_MB,
)


def _photo_path(photo: dict) -> str:
    """取照片可用路径（优先当前路径，退回原始路径）。"""
    return photo.get("current_path") or photo.get("original_path") or ""


@dataclass
class SpeciesGroup:
    """一个鸟种分组：被改正图 + 建议正样本。"""
    corrected_cn: str
    corrected_en: str
    model_class_id: Optional[int]
    failed: dict
    positives: List[dict] = field(default_factory=list)


class SubmissionReviewDialog(QDialog):
    """纠错样本复审窗。"""

    def __init__(self, groups: List[SpeciesGroup], out_dir: str,
                 app_version: str, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self._groups = groups
        self._out_dir = out_dir
        self._app_version = app_version
        self._checks: dict = {}  # filename -> QCheckBox
        self.result_zip_path: Optional[str] = None
        self.setWindowTitle(self.i18n.t("submission.title"))
        self.resize(520, 640)
        self._build_ui()

    @staticmethod
    def groups_to_items(
        groups: List[SpeciesGroup], selected_keys: Set[str]
    ) -> List[SubmissionItem]:
        """
        把分组 + 勾选集合转成 SubmissionItem 列表（纯逻辑，可单测）。

        未反查到 model_class_id 的组整组跳过（不瞎猜）。只保留 selected_keys
        中勾选的照片；被改正图 is_the_failed_one=True。
        """
        items: List[SubmissionItem] = []
        for g in groups:
            if g.model_class_id is None:
                continue
            all_photos = [(g.failed, True)] + [(p, False) for p in g.positives]
            for photo, is_failed in all_photos:
                fn = photo.get("filename")
                if fn not in selected_keys:
                    continue
                path = _photo_path(photo)
                if not path:
                    continue
                items.append(SubmissionItem(
                    photo_path=path,
                    model_class_id=g.model_class_id,
                    chinese=g.corrected_cn,
                    wrong_cn=None,
                    is_the_failed_one=is_failed,
                ))
        return items

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(self.i18n.t("submission.hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setAlignment(Qt.AlignTop)

        for g in self._groups:
            box = QFrame()
            box.setStyleSheet(
                f"QFrame {{ background: {COLORS['bg_card']}; border-radius: 8px; }}"
            )
            bl = QVBoxLayout(box)
            title = g.corrected_cn or g.corrected_en or "?"
            if g.model_class_id is None:
                title += "  ⚠️ " + self.i18n.t("submission.no_class_id")
            bl.addWidget(QLabel(f"<b>{title}</b>"))
            for photo, is_failed in [(g.failed, True)] + [(p, False) for p in g.positives]:
                fn = photo.get("filename", "")
                cb = QCheckBox(("★ " if is_failed else "") + fn)
                cb.setChecked(g.model_class_id is not None)
                cb.setEnabled(g.model_class_id is not None)
                cb.stateChanged.connect(self._update_summary)
                self._checks[fn] = cb
                bl.addWidget(cb)
            col.addWidget(box)

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._summary)

        btn_row = QHBoxLayout()
        cancel = QPushButton(self.i18n.t("submission.cancel"))
        cancel.clicked.connect(self.reject)
        self._pack_btn = QPushButton(self.i18n.t("submission.pack"))
        self._pack_btn.clicked.connect(self._on_pack)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._pack_btn)
        root.addLayout(btn_row)

        self._update_summary()

    def _selected_keys(self) -> Set[str]:
        return {fn for fn, cb in self._checks.items() if cb.isChecked()}

    def _update_summary(self):
        items = self.groups_to_items(self._groups, self._selected_keys())
        self._summary.setText(
            self.i18n.t("submission.summary", count=len(items))
        )
        self._pack_btn.setEnabled(len(items) > 0)

    def _on_pack(self):
        items = self.groups_to_items(self._groups, self._selected_keys())
        if not items:
            QMessageBox.information(self, self.i18n.t("submission.title"),
                                    self.i18n.t("submission.none_selected"))
            return
        try:
            res = build_submission(items, self._out_dir, self._app_version)
        except Exception as e:
            # 面向非技术用户的优雅错误处理：无写权限退回/图片损坏等意外情况，
            # 不让异常直接崩到 Qt 事件循环，改弹提示框告知用户。
            # User-friendly error handling: don't let exceptions (e.g. no
            # write permission, corrupted files) propagate into the Qt
            # event loop; show a message box instead.
            QMessageBox.critical(
                self, self.i18n.t("submission.title"),
                self.i18n.t("submission.pack_error", error=str(e)))
            return
        self.result_zip_path = res.zip_path
        msg = self.i18n.t("submission.done",
                          count=res.count, path=res.zip_path)
        if res.est_size_mb > EMAIL_SIZE_WARN_MB:
            msg += "\n" + self.i18n.t("submission.too_big",
                                      mb=EMAIL_SIZE_WARN_MB)
        if res.skipped:
            msg += "\n" + self.i18n.t("submission.skipped_n",
                                      n=len(res.skipped))
        QMessageBox.information(self, self.i18n.t("submission.title"), msg)
        self.accept()
