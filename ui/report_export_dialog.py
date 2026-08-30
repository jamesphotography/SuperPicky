#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SuperPicky - 导出报告对话框 / Report export dialog.

收集导出选项（GPS 勾选）、展示预检结果与体积/耗时预估。
真正的生成工作在 ui/results_browser_window.py 的工作线程里进行。

Collects export options, shows the pre-flight result and the size estimate.
The actual generation runs in a worker thread owned by the browser window.
"""

from typing import Dict

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel,
                               QVBoxLayout)

from core.report_export import SIZE_WARN_BYTES
from ui.icon_utils import checkbox_indicator_qss

# 预览可用率低于此值时提示会有占位块（spec 7.1）。低于 50% 的批次由调用方
# 提前拦下，根本不会走到这个对话框。
# Below this ratio the dialog warns about placeholder blocks; batches under
# 50% are rejected by the caller and never reach this dialog.
_PREVIEW_WARN_RATIO = 0.9


class ReportExportDialog(QDialog):
    """
    导出报告的选项对话框。

    参数:
        i18n: 全局 i18n 实例，用于文案本地化。
        available (int): 预检得到的可用预览数。
        total (int): 照片总数。
        est_bytes (int): 预估文件字节数。
        est_seconds (int): 预估耗时秒数。
        parent: 父窗口。

    Option dialog shown before exporting a report.
    """

    def __init__(self, i18n, available: int, total: int, est_bytes: int,
                 est_seconds: int, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.t("report_export.title"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        ratio = (available / total) if total else 0.0
        est_mb = est_bytes / (1024 * 1024)
        layout.addWidget(QLabel(i18n.t("report_export.estimate",
                                       size=f"{est_mb:.0f}", secs=est_seconds)))

        # 预览可用率 50%~90%：可以导出，但要让用户知道会有占位块（spec 7.1）。
        # 低于 50% 的情况由调用方拦截，不会走到这个对话框。
        if ratio < _PREVIEW_WARN_RATIO:
            warn = QLabel(i18n.t("report_export.missing_previews",
                                 count=total - available))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#ffcc00")
            layout.addWidget(warn)

        if est_bytes >= SIZE_WARN_BYTES:
            big = QLabel(i18n.t("report_export.too_big"))
            big.setWordWrap(True)
            big.setStyleSheet("color:#ffcc00")
            layout.addWidget(big)

        # GPS 默认不勾（spec D3）：珍稀鸟点位泄露是真实风险。
        # GPS off by default; leaking rare-bird locations is a real risk.
        self._gps = QCheckBox(i18n.t("report_export.include_gps"))
        self._gps.setChecked(False)
        self._gps.setToolTip(i18n.t("report_export.include_gps_tip"))
        # 全局默认是方框指示器，本项目统一用圆圈/带勾圆圈（见 CLAUDE.md）。
        # The project standardizes on circle indicators instead of Qt's boxes.
        self._gps.setStyleSheet(checkbox_indicator_qss())
        layout.addWidget(self._gps)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_options(self) -> Dict[str, bool]:
        """
        返回用户选择的导出选项。

        Returns:
            Dict[str, bool]: 目前只有 include_gps。

        报告不再生成「全部照片明细」表，原先按张数自动判定的
        with_detail_thumbs 随之取消。

        Return the chosen export options. The all-photos detail table (and its
        thumbnail-count switch) was removed from the report.
        """
        return {
            "include_gps": self._gps.isChecked(),
        }
