# -*- coding: utf-8 -*-
"""
多选重新识别核对弹窗（#163）/ Multi-photo re-identification review dialog (#163).

多选若干张后在「改鸟种」弹窗里点「重新识别这 N 张」进入这里：后台逐张识别，每张一行，
列出当前鸟种与识别出的候选（下拉可改选或保持不变），用户核对后点「应用修改」才写入。

- 结果先核对再写入，不直接改（用户 2026-11-14 拍板）：一次改几十张，错了很难发现。
- 每张各出各的结果，不做连拍统一。
- 改鸟种以连拍组为单位整组执行（组内未勾选的也会改），所以同一组被选成不同鸟种时
  拦住「应用修改」，提示用户先选一致（逻辑见 core.batch_reidentify）。

结果放在 ``plans`` 属性：[(照片列表, 新中文名, 新英文名, 新学名)]，由浏览器的
``_execute_species_plans`` 执行。

Each selected photo is re-identified on a worker thread and listed with its
candidates; nothing is written until the user clicks Apply. Each photo keeps
its own result. Changes apply per burst group, so differing choices within a
burst block Apply. The accepted result is ``plans`` for _execute_species_plans.
"""

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.batch_reidentify import (
    ReidRow,
    build_plans,
    default_choice,
    find_burst_conflicts,
    is_change,
)
from tools.i18n import get_i18n
from ui.combo_popup import style_combo_popup
from ui.styles import COLORS

# 表格行内的紧凑深色下拉：GLOBAL_STYLE 的下拉 padding 10px，放进表格行会被挤压
# Compact dark combo for table rows; GLOBAL_STYLE's 10px padding gets squeezed
_COMBO_QSS = f"""
    QComboBox {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 6px;
        padding: 2px 8px;
        color: {COLORS['text_primary']};
        font-size: 12px;
        min-height: 22px;
    }}
    QComboBox:hover {{ border-color: {COLORS['text_muted']}; }}
    QComboBox:disabled {{ color: {COLORS['text_muted']}; border-color: {COLORS['border_subtle']}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background-color: {COLORS['bg_elevated']};
        border: none;
        selection-background-color: {COLORS['accent_dim']};
        selection-color: {COLORS['text_primary']};
        outline: none;
        padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 4px 8px;
        color: {COLORS['text_primary']};
        min-height: 22px;
    }}
"""

# 工作线程报「文件不存在」用的错误码，界面再换成当前语言的文案
# Error code for a missing file; the UI turns it into localized text
_MISSING = "missing"


def _photo_path(photo: Dict[str, Any]) -> str:
    """照片本体的绝对路径（浏览器里 current_path 已是绝对路径）/ the photo's path."""
    return photo.get("current_path") or photo.get("original_path") or ""


class _BatchIdentifyWorker(QThread):
    """
    后台逐张识别。每张完成发一次信号；可随时取消（当前这张识别完后停下）。

    Identify photos one by one on a worker thread; cancellable between photos.
    """

    row_done = Signal(int, dict)      # 行号, identify_bird 的结果 / row, result
    row_failed = Signal(int, str)     # 行号, 错误信息 / row, error

    def __init__(self, paths: List[str], params: Dict[str, Any]):
        super().__init__()
        self._paths = list(paths)
        self._params = dict(params)
        self._cancelled = False

    def cancel(self) -> None:
        """请求停止（当前这张识别完后生效）/ stop after the current photo."""
        self._cancelled = True

    def run(self) -> None:
        from birdid.bird_identifier import identify_bird
        for index, path in enumerate(self._paths):
            if self._cancelled:
                return
            if not path or not os.path.exists(path):
                self.row_failed.emit(index, _MISSING)
                continue
            try:
                result = identify_bird(path, **self._params)
            except Exception as e:  # 一张失败不影响其余 / one failure must not stop the rest
                self.row_failed.emit(index, f"BirdID: {e}")
                continue
            self.row_done.emit(index, result or {})


class BatchReidentifyDialog(QDialog):
    """
    多选重新识别核对弹窗。

    参数 / Args:
        photos: 勾选的照片记录（就地引用）
        threshold: 识鸟置信度阈值（%），决定默认是否选中第一候选
        parent: 父窗口
        autostart: 构造后立即开始识别（测试里传 False 以手动驱动）

    属性 / Attributes:
        rows: 每张照片一行的 ReidRow
        plans: 接受后要执行的改鸟种分组
    """

    def __init__(self, photos: List[Dict[str, Any]], threshold: float,
                 parent=None, autostart: bool = True):
        super().__init__(parent)
        self.i18n = get_i18n()
        self.rows: List[ReidRow] = [ReidRow(photo=p) for p in photos]
        self.plans: list = []
        self._threshold = float(threshold)
        self._worker: Optional[_BatchIdentifyWorker] = None
        self._finished_count = 0
        self._combos: List[QComboBox] = []

        self.setWindowTitle(self.i18n.t("batch_reid.title"))
        self.resize(760, 540)
        self.setStyleSheet(f"QDialog {{ background-color: {COLORS['bg_elevated']}; }}")
        self._build_ui()
        self._refresh_state()
        if autostart:
            self.start()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        """构建：说明 + 表格 + 连拍提示 + 状态行 + 按钮。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        intro = QLabel(self.i18n.t("batch_reid.intro", count=len(self.rows)))
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;")
        root.addWidget(intro)

        self.table = QTableWidget(len(self.rows), 3)
        self.table.setHorizontalHeaderLabels([
            self.i18n.t("batch_reid.col_file"),
            self.i18n.t("batch_reid.col_current"),
            self.i18n.t("batch_reid.col_result"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
                color: {COLORS['text_primary']};
                font-size: 12px;
                gridline-color: {COLORS['border_subtle']};
            }}
            QHeaderView::section {{
                background-color: {COLORS['bg_elevated']};
                color: {COLORS['text_tertiary']};
                border: none;
                padding: 6px;
                font-size: 11px;
            }}
        """)
        from tools.zh_convert import species_display
        lang = self.i18n.current_lang
        for index, row in enumerate(self.rows):
            photo = row.photo
            name = photo.get("filename") or os.path.basename(_photo_path(photo))
            path_name = os.path.basename(_photo_path(photo))
            self.table.setItem(index, 0, QTableWidgetItem(path_name or name))
            current = species_display(photo.get("bird_species_cn"),
                                      photo.get("bird_species_en"), lang) or "—"
            self.table.setItem(index, 1, QTableWidgetItem(current))
            combo = QComboBox()
            combo.setStyleSheet(_COMBO_QSS)
            # 弹出列表容器需逐个接线，祖先样式表够不到顶层 popup（见 ui/combo_popup.py）
            # Per-instance popup styling; ancestor sheets cannot reach the popup
            style_combo_popup(combo)
            combo.addItem(self.i18n.t("batch_reid.pending"), None)
            combo.setEnabled(False)
            combo.currentIndexChanged.connect(
                lambda _i, r=index: self._on_choice_changed(r))
            self.table.setCellWidget(index, 2, combo)
            self._combos.append(combo)
        root.addWidget(self.table, 1)

        # 连拍说明：选集里有连拍组时常驻显示（整组一起改，含未勾选成员）
        # Burst note, shown whenever the selection contains bursts
        has_burst = any(r.photo.get("burst_id") for r in self.rows)
        self.burst_note = QLabel(self.i18n.t("batch_reid.burst_note"))
        self.burst_note.setWordWrap(True)
        self.burst_note.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 11px; background: transparent;")
        self.burst_note.setVisible(has_burst)
        root.addWidget(self.burst_note)

        self.conflict_label = QLabel("")
        self.conflict_label.setWordWrap(True)
        self.conflict_label.setStyleSheet(
            f"color: {COLORS['warning']}; font-size: 12px; background: transparent;")
        self.conflict_label.setVisible(False)
        root.addWidget(self.conflict_label)

        btn_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;")
        btn_row.addWidget(self.status_label, 1)

        self.cancel_button = QPushButton(self.i18n.t("batch_reid.cancel"))
        self.cancel_button.setFixedHeight(36)
        self.cancel_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_secondary']};
                font-size: 13px;
                padding: 0 16px;
            }}
        """)
        self.cancel_button.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_button)

        self.apply_button = QPushButton("")
        self.apply_button.setFixedHeight(36)
        self.apply_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }}
            QPushButton:disabled {{
                background-color: {COLORS['border']};
                color: {COLORS['text_muted']};
            }}
        """)
        self.apply_button.clicked.connect(self._on_apply)
        btn_row.addWidget(self.apply_button)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------ 识别

    def start(self) -> None:
        """
        开始后台识别；参数取自 advanced_config，与单张「重新识别这张」一致。
        Start the worker with the same parameters as single-photo re-identify.
        """
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        params = dict(
            top_k=5,
            use_gps=True,
            use_geo_filter=bool(getattr(cfg, "birdid_use_geo_filter", True)),
            country_code=getattr(cfg, "birdid_country_code", None),
            region_code=getattr(cfg, "birdid_region_code", None),
            name_format=cfg.name_format,
        )
        self._worker = _BatchIdentifyWorker([_photo_path(r.photo) for r in self.rows], params)
        self._worker.row_done.connect(self.apply_result)
        self._worker.row_failed.connect(self.apply_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()
        self._refresh_state()

    def apply_result(self, index: int, result: dict) -> None:
        """
        一张识别完成：填候选、按规则给默认选择。

        参数:
        index (int): 行号
        result (dict): identify_bird 的返回值
        """
        from ui.bird_species_edit_dialog import identify_result_to_bird_data
        row = self.rows[index]
        items = (result or {}).get("results") or []
        row.candidates = [identify_result_to_bird_data(it) for it in items]
        row.status = "done" if row.candidates else "none"
        row.choice = default_choice(row.candidates, row.photo, self._threshold)
        self._fill_combo(index)
        self._finished_count += 1
        self._refresh_state()

    def apply_error(self, index: int, message: str) -> None:
        """一张识别失败：这一行保持不变并显示原因 / keep the row and show why."""
        row = self.rows[index]
        row.status = "error"
        row.error = message
        row.choice = None
        self._fill_combo(index)
        self._finished_count += 1
        self._refresh_state()

    def _fill_combo(self, index: int) -> None:
        """按候选重建这一行的下拉 / rebuild the row's combo from its candidates."""
        from tools.zh_convert import species_display
        row = self.rows[index]
        combo = self._combos[index]
        combo.blockSignals(True)
        combo.clear()
        if row.status == "error":
            error = (self.i18n.t("batch_reid.row_missing") if row.error == _MISSING
                     else row.error)
            combo.addItem(self.i18n.t("batch_reid.row_failed", error=error), None)
        elif row.status == "none":
            combo.addItem(self.i18n.t("batch_reid.row_none"), None)
        else:
            combo.addItem(self.i18n.t("batch_reid.keep"), None)
            lang = self.i18n.current_lang
            for cand_index, cand in enumerate(row.candidates):
                label = species_display(cand.get("chinese_name"), cand.get("english_name"),
                                        lang) or "?"
                conf = cand.get("confidence")
                if conf is not None:
                    label = f"{label}  {float(conf):.0f}%"
                combo.addItem(label, cand_index)
            combo.setCurrentIndex(0 if row.choice is None else row.choice + 1)
        combo.setEnabled(row.status == "done")
        combo.blockSignals(False)

    def _on_choice_changed(self, index: int) -> None:
        """用户改了某行的下拉 / the user picked another option for a row."""
        data = self._combos[index].currentData()
        self.rows[index].choice = data if isinstance(data, int) else None
        self._refresh_state()

    def _on_worker_finished(self) -> None:
        self._refresh_state()

    def is_running(self) -> bool:
        """后台识别是否还在跑 / whether identification is still running."""
        return self._worker is not None and self._worker.isRunning()

    # ------------------------------------------------------------------ 状态

    def _refresh_state(self) -> None:
        """更新状态行、冲突提示与「应用修改」按钮 / refresh status, conflicts, Apply."""
        total = len(self.rows)
        changes = sum(1 for r in self.rows if is_change(r))
        running = self._finished_count < total and (self._worker is not None)
        if running:
            self.status_label.setText(
                self.i18n.t("batch_reid.status_running", done=self._finished_count, total=total))
        elif self._worker is None and self._finished_count == 0:
            self.status_label.setText("")
        else:
            self.status_label.setText(self.i18n.t("batch_reid.status_done", count=changes))

        conflicts = find_burst_conflicts(self.rows)
        if conflicts:
            names = "; ".join(
                ", ".join(os.path.basename(_photo_path(r.photo)) or (r.photo.get("filename") or "")
                          for r in group)
                for group in conflicts)
            self.conflict_label.setText(self.i18n.t("batch_reid.conflict", names=names))
        self.conflict_label.setVisible(bool(conflicts))

        self.apply_button.setText(self.i18n.t("batch_reid.apply", count=changes))
        self.apply_button.setEnabled(changes > 0 and not conflicts and not running)

    # ------------------------------------------------------------------ 结束

    def _on_apply(self) -> None:
        """生成分组并接受 / build the plans and accept."""
        if find_burst_conflicts(self.rows):
            return
        self.plans = build_plans(self.rows)
        if self.plans:
            self.accept()

    def _stop_worker(self) -> None:
        """停下后台识别并等它结束（当前这张识别完）/ stop and wait for the worker."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(30000)

    def reject(self) -> None:
        self._stop_worker()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop_worker()
        super().closeEvent(event)
