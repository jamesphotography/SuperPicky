# -*- coding: utf-8 -*-
"""
SuperPicky - 选鸟结果图片浏览器主窗口
ResultsBrowserWindow(QMainWindow): 三栏布局
  左栏: FilterPanel  — 评分/对焦/曝光/飞行/鸟种 筛选
  中栏: ThumbnailGrid — 缩略图网格（异步加载）
  右栏: DetailPanel  — 大图预览 + 元数据

入口:
  1. 主窗口菜单栏「查看结果」
  2. 处理完成后弹窗「查看选片结果」按钮
"""

import os
import subprocess
import sys
from collections import Counter
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QStatusBar,
    QSlider, QComboBox, QMessageBox, QSizePolicy, QApplication,
    QStackedWidget, QMenu
)
from PySide6.QtCore import Qt, Signal, Slot, QProcess
from PySide6.QtGui import QAction, QKeyEvent, QIcon, QFont

from ui.styles import COLORS, GLOBAL_STYLE, FONTS
from ui.filter_panel import FilterPanel
from ui.thumbnail_grid import ThumbnailGrid
from ui.detail_panel import DetailPanel
from ui.fullscreen_viewer import FullscreenViewer
from ui.comparison_viewer import ComparisonViewer
from typing import Optional

from tools.i18n import get_i18n
from tools.report_db import ReportDB


def _photo_identity(photo: dict) -> tuple:
    return (photo.get("source_dir") or "", photo.get("filename") or "")


def _photo_db_key(photo: dict):
    source_dir = photo.get("source_dir")
    filename = photo.get("filename") or ""
    if source_dir:
        return (source_dir, filename)
    return filename


def _coerce_photo(photo_or_filename, photo_pool: list, fallback_photo: Optional[dict] = None) -> Optional[dict]:
    if isinstance(photo_or_filename, dict):
        return photo_or_filename

    filename = photo_or_filename or ""
    if fallback_photo and fallback_photo.get("filename") == filename:
        return fallback_photo

    matches = [p for p in photo_pool if p.get("filename") == filename]
    if len(matches) == 1:
        return matches[0]
    return fallback_photo if isinstance(fallback_photo, dict) else (matches[0] if matches else None)


def _parse_capture_time(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None

    formats = (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _burst_sort_key(photo: dict) -> tuple:
    capture_time = _parse_capture_time(photo.get("date_time_original"))
    fallback = datetime.max
    return (capture_time or fallback, photo.get("filename", ""))


def _extract_burst_group_key(photo: dict) -> Optional[str]:
    source_prefix = photo.get("source_dir") or ""
    for key in ("current_path", "original_path"):
        path = photo.get(key)
        if not path:
            continue
        normalized = os.path.normpath(path)
        parts = normalized.split(os.sep)
        for idx, part in enumerate(parts):
            if part.startswith("burst_"):
                return f"{source_prefix}|{'/'.join(parts[:idx + 1])}"
    return None


def _build_burst_update_map(photos: list) -> dict:
    grouped = {}
    for photo in photos:
        group_key = _extract_burst_group_key(photo)
        if not group_key:
            continue
        grouped.setdefault(group_key, []).append(photo)

    if not grouped:
        return {}

    burst_map = {}
    for burst_id, group_key in enumerate(sorted(grouped), 1):
        burst_photos = sorted(grouped[group_key], key=_burst_sort_key)
        if len(burst_photos) <= 1:
            continue
        for pos, photo in enumerate(burst_photos, 1):
            burst_map[_photo_identity(photo)] = (burst_id, pos)
    return burst_map


def _burst_totals_from_photos(photos: list) -> Counter:
    return Counter(p["burst_id"] for p in photos if p.get("burst_id") is not None)


# ============================================================
#  C4 — 右键菜单实现（应用列表来自用户配置）
# ============================================================


def _resolve_app_path(ap: str) -> str:
    """将配置中的 app 路径规范化为可传给 open -a 的形式。

    处理以下情况：
    - /Applications/Photoshop.app          → 原样返回（已有 .app）
    - /Applications/Adobe Photoshop 2026   → 可能是 Adobe 风格文件夹
      → 尝试同名 .app（Adobe Photoshop 2026.app）
      → 尝试文件夹内唯一 .app
      → 回退到只用名称（open -a 按名字搜索）
    """
    if ap.endswith(".app"):
        return ap
    # 尝试同名加 .app 后缀
    candidate = ap + ".app"
    if os.path.isdir(candidate):
        return candidate
    # 如果本身是目录（Adobe 风格：文件夹内含同名 .app）
    if os.path.isdir(ap):
        folder_name = os.path.basename(ap)
        inner = os.path.join(ap, folder_name + ".app")
        if os.path.isdir(inner):
            return inner
        # 找目录内任意 .app
        try:
            apps_inside = [x for x in os.listdir(ap) if x.endswith(".app")]
            if apps_inside:
                return os.path.join(ap, apps_inside[0])
        except OSError:
            pass
    # 无法确定完整路径：只返回显示名称，让 open -a 按名称搜索
    return os.path.splitext(os.path.basename(ap))[0]


def _best_reveal_target(*filepaths: str) -> str:
    """返回"在 Finder 中显示"时最合适的目标路径：
    - 按顺序尝试多个候选路径，第一个实际存在的文件 → open -R 精确定位
    - 所有文件均不存在 → 回退到第一个非空路径的父目录（至少打开文件夹）
    """
    first_valid = ""
    for filepath in filepaths:
        if not filepath:
            continue
        if not first_valid:
            first_valid = filepath
        if os.path.isfile(filepath):
            return filepath
    # 所有路径均不存在，回退到父目录
    if first_valid:
        parent = os.path.dirname(first_valid)
        if parent and os.path.isdir(parent):
            return parent
    return ""


def _show_context_menu_impl(parent_widget, photo: dict, pos, directory: str):
    """构建并显示右键菜单（C4）。外部应用列表从 advanced_config 读取。"""
    from advanced_config import get_advanced_config

    # current_path 是整理后的实际位置（优先），original_path 是处理时的原始位置（兜底）
    filepath = photo.get("current_path") or photo.get("original_path") or ""
    if not filepath:
        fn = photo.get("filename", "")
        if fn and directory:
            filepath = os.path.join(directory, fn)

    menu = QMenu(parent_widget)
    menu.setStyleSheet(f"""
        QMenu {{
            background-color: {COLORS['bg_elevated']};
            color: {COLORS['text_primary']};
            border: 1px solid {COLORS['border']};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{ padding: 6px 16px; border-radius: 4px; color: {COLORS['text_secondary']}; }}
        QMenu::item:selected {{ background-color: {COLORS['accent_dim']}; color: {COLORS['accent']}; }}
        QMenu::item:disabled {{ color: {COLORS['text_muted']}; }}
        QMenu::separator {{ height: 1px; background: {COLORS['border_subtle']}; margin: 4px 8px; }}
    """)

    # 在 Finder/Explorer 中显示
    current = photo.get("current_path") or ""
    original = photo.get("original_path") or ""

    def _reveal():
        if sys.platform == "darwin":
            # 按优先级依次尝试：current_path → original_path → filepath（兜底）
            target = _best_reveal_target(current, original, filepath)
            if not target:
                return
            # 文件存在 → open -R 精确定位；目录 → open 直接打开
            if os.path.isfile(target):
                QProcess.startDetached("open", ["-R", target])
            else:
                QProcess.startDetached("open", [target])
        elif sys.platform == "win32" and filepath:
            # Windows：优先用实际存在的路径
            win_target = _best_reveal_target(current, original, filepath)
            if os.path.isfile(win_target):
                QProcess.startDetached("explorer", ["/select,", win_target.replace("/", "\\")])
            elif win_target:
                QProcess.startDetached("explorer", [win_target.replace("/", "\\")])

    _i18n = get_i18n()
    if sys.platform == "win32":
        reveal_label = "在资源管理器中显示" if not _i18n.current_lang.startswith('en') else "Show in Explorer"
    else:
        reveal_label = _i18n.t('browser.ctx_show_in_finder')
    finder_action = QAction(reveal_label, parent_widget)
    finder_action.setEnabled(bool(filepath))
    finder_action.triggered.connect(_reveal)
    menu.addAction(finder_action)

    # 用户配置的外部应用列表（设置 → 外部应用）
    external_apps = get_advanced_config().get_external_apps()
    if external_apps:
        menu.addSeparator()
        for app in external_apps:
            app_name = app.get("name", "")
            app_path = app.get("path", "")
            if not app_name or not app_path:
                continue
            act = QAction(_i18n.t('browser.ctx_open_with').format(app_name=app_name), parent_widget)
            act.setEnabled(bool(filepath))

            def _open_in_app(_checked=False, _fp=filepath, _ap=app_path):
                import subprocess
                if sys.platform == "darwin" and _fp:
                    ap = _resolve_app_path(_ap)
                    subprocess.Popen(["open", "-a", ap, _fp])
                elif sys.platform == "win32" and _fp:
                    QProcess.startDetached(_ap, [_fp])

            act.triggered.connect(_open_in_app)
            menu.addAction(act)
    else:
        # 未配置时提示用户去设置
        menu.addSeparator()
        hint_action = QAction(_i18n.t('browser.ctx_add_external_app'), parent_widget)
        hint_action.setEnabled(False)
        menu.addAction(hint_action)

    menu.addSeparator()

    # 复制路径
    copy_action = QAction(_i18n.t('browser.ctx_copy_path'), parent_widget)
    copy_action.setEnabled(bool(filepath))
    if filepath:
        def _copy_path(_checked=False, _fp=filepath):
            QApplication.clipboard().setText(_fp)
        copy_action.triggered.connect(_copy_path)
    menu.addAction(copy_action)

    menu.exec(pos)


def _move_to_trash(filepath: str) -> bool:
    """将文件移入系统回收站（跨平台）。返回是否成功。"""
    if not filepath or not os.path.exists(filepath):
        return False
    try:
        if sys.platform == "darwin":
            # macOS: osascript 调用 Finder 移入回收站
            escaped = filepath.replace('"', '\\"')
            script = f'tell application "Finder" to delete POSIX file "{escaped}"'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        elif sys.platform == "win32":
            # Windows: SHFileOperationW (FOF_ALLOWUNDO 移入回收站)
            import ctypes
            from ctypes import wintypes
            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p),
                ]
            FO_DELETE = 0x0003
            FOF_ALLOWUNDO = 0x0040
            FOF_NOCONFIRMATION = 0x0010
            FOF_SILENT = 0x0004
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            op.pFrom = filepath + '\0'  # double null-terminated
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            shell32 = ctypes.windll.shell32
            result = shell32.SHFileOperationW(ctypes.byref(op))
            return result == 0
        else:
            print(f"⚠️ 当前平台不支持回收站: {sys.platform}")
            return False
    except Exception as e:
        print(f"⚠️ 移入回收站失败: {e}")
        return False


class ResultsBrowserWindow(QMainWindow):
    """
    独立的选鸟结果浏览器窗口。

    可以在主窗口之外独立显示/隐藏，不会阻塞主窗口操作。
    """
    closed = Signal()   # 窗口关闭时通知主窗口

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self._db: Optional[ReportDB] = None
        self._directory: str = ""
        self._all_photos: list = []
        self._filtered_photos: list = []
        self._raw_filtered_photos: list = [] # V5: Store unfiltered sorted photos
        self._expanded_bursts: set = set()   # V5: Track expanded burst IDs
        self._is_merged: bool = False
        self._sub_dirs: list = []
        self._fullscreen_nav_photos: list = []

        self._setup_window()
        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()

    # ------------------------------------------------------------------
    #  窗口配置
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle(self.i18n.t("browser.title"))
        self.setMinimumSize(1000, 680)
        self.resize(1280, 780)
        self.setStyleSheet(GLOBAL_STYLE)
        self.setFocusPolicy(Qt.StrongFocus)  # 确保窗口能接收键盘事件

        # 尝试复用主窗口图标
        try:
            import sys
            resource_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(__file__)))
            icon_path = os.path.join(resource_base, "img", "icon.png")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu(self.i18n.t("menu.file"))

        file_menu.addSeparator()

        close_action = QAction(self.i18n.t("buttons.close"), self)
        close_action.setShortcut("Ctrl+W")
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

    def _setup_ui(self):
        """
        布局：外层 QHBoxLayout = [QStackedWidget (左/中)] + [DetailPanel (右，始终可见)]
        QStackedWidget:
          Page 0 — 过滤面板 + 缩略图网格（两栏）
          Page 1 — 全屏查看器
        DetailPanel 在 Stack 外部，Tab 键开关可见性。
        """
        outer = QWidget()
        self.setCentralWidget(outer)
        outer_h = QHBoxLayout(outer)
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_h.setSpacing(0)

        # ── Stack（左/中部分）──────────────────────────────────────
        self._stack = QStackedWidget()
        outer_h.addWidget(self._stack, 1)

        # Page 0: 过滤面板 + 缩略图网格
        two_col = QWidget()
        main_h = QHBoxLayout(two_col)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        # 左侧：过滤面板
        self._filter_panel = FilterPanel(self.i18n, self)
        self._filter_panel.filters_changed.connect(self._apply_filters)
        main_h.addWidget(self._filter_panel)

        # 中央：网格 + 工具栏
        center_widget = QWidget()
        center_widget.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self._toolbar = self._build_toolbar()
        center_layout.addWidget(self._toolbar)

        self._thumb_grid = ThumbnailGrid(self.i18n, self)
        self._thumb_grid.photo_selected.connect(self._on_photo_selected)
        self._thumb_grid.photo_double_clicked.connect(self._enter_fullscreen)
        self._thumb_grid.multi_selection_changed.connect(self._on_multi_selection_changed)
        self._thumb_grid.burst_badge_clicked.connect(self._toggle_burst)
        center_layout.addWidget(self._thumb_grid, 1)

        main_h.addWidget(center_widget, 1)
        self._stack.addWidget(two_col)            # index 0

        # Page 1: 全屏查看器
        self._fullscreen = FullscreenViewer(self.i18n, self)
        self._fullscreen.close_requested.connect(self._exit_fullscreen)
        self._fullscreen.prev_requested.connect(self._fullscreen_prev)
        self._fullscreen.next_requested.connect(self._fullscreen_next)
        self._fullscreen.delete_requested.connect(self._on_delete_photo)
        self._fullscreen.context_menu_requested.connect(self._on_fullscreen_context_menu)
        self._fullscreen.burst_sequence_requested.connect(self._open_burst_sequence)
        self._stack.addWidget(self._fullscreen)   # index 1

        # Page 2: 对比查看器（C5）
        self._comparison = ComparisonViewer(self.i18n, self)
        self._comparison.close_requested.connect(self._exit_comparison)
        self._comparison.rating_changed.connect(self._on_rating_changed)
        self._stack.addWidget(self._comparison)   # index 2

        # ── 右侧详情面板（始终显示，Tab 键开关）──────────────────
        self._detail_panel = DetailPanel(self.i18n, self)
        self._detail_panel.prev_requested.connect(self._prev_photo)
        self._detail_panel.next_requested.connect(self._next_photo)
        self._detail_panel.rating_change_requested.connect(self._on_rating_changed)
        outer_h.addWidget(self._detail_panel, 0)

    def _build_toolbar(self) -> QWidget:
        """构建网格顶部工具栏（目录选择 + 缩略图尺寸滑块）。"""
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"""
            QWidget#toolbar {{
                background-color: {COLORS['bg_elevated']};
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        # P2: 返回主界面按钮（最左侧）
        back_btn = QPushButton(self.i18n.t("browser.back"))
        back_btn.setObjectName("tertiary")
        back_btn.setFixedHeight(32)
        back_btn.setToolTip(self.i18n.t("browser.back_tooltip"))
        back_btn.clicked.connect(self._go_back_to_main)
        layout.addWidget(back_btn)

        layout.addSpacing(8)

        # Directory switcher combo box
        self._dir_combo = QComboBox()
        self._dir_combo.setFixedHeight(32)
        self._dir_combo.setMinimumWidth(200)
        self._dir_combo.setMaximumWidth(400)
        self._dir_combo.setStyleSheet(f"""
            QComboBox {{
                color: {COLORS['text_secondary']};
                background: {COLORS['bg_primary']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
                font-family: {FONTS['mono']};
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
        """)
        self._dir_combo.currentIndexChanged.connect(self._on_subdir_changed)
        self._dir_combo.hide()
        layout.addWidget(self._dir_combo)

        # 目录显示标签
        self._dir_label = QLabel(self.i18n.t("browser.open_dir"))
        self._dir_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 12px;
                font-family: {FONTS['mono']};
                background: transparent;
            }}
        """)
        self._dir_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._dir_label)

        layout.addSpacing(16)

        # 多选计数标签（C3，默认隐藏）
        self._select_count_label = QLabel("")
        self._select_count_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['accent']};
                font-size: 12px;
                background: transparent;
            }}
        """)
        self._select_count_label.hide()
        layout.addWidget(self._select_count_label)

        # 对比按钮（C5，多选2张时显示）
        self._compare_btn = QPushButton(self.i18n.t("browser.compare_btn"))
        self._compare_btn.setObjectName("secondary")
        self._compare_btn.setFixedHeight(32)
        self._compare_btn.hide()
        self._compare_btn.clicked.connect(self._enter_comparison)
        layout.addWidget(self._compare_btn)

        # 缩略图尺寸滑块
        size_label = QLabel(self.i18n.t("browser.size_label"))
        size_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px; background: transparent;")
        layout.addWidget(size_label)

        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(80, 300)
        self._size_slider.setValue(160)
        self._size_slider.setFixedWidth(100)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        layout.addWidget(self._size_slider)

        return bar

    def _setup_statusbar(self):
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {COLORS['bg_elevated']};
                color: {COLORS['text_secondary']};
                font-size: 11px;
                border-top: 1px solid {COLORS['border_subtle']};
            }}
        """)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("—")

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def open_directory(self, directory: str):
        """Load report.db. Supports batch multi-dir mode."""
        if not directory:
            return

        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

        self._is_merged = False
        self._sub_dirs = []

        from tools.merged_report_db import find_processed_subdirs
        processed = find_processed_subdirs(directory)

        self._dir_combo.blockSignals(True)
        self._dir_combo.clear()

        if len(processed) > 1:
            self._sub_dirs = processed
            total = sum(self._count_db_photos(d) for d in processed)
            self._dir_combo.addItem(f"\U0001f4c2 All ({total})", "__ALL__")
            for d in processed:
                rel = os.path.relpath(d, directory)
                n = self._count_db_photos(d)
                label = f"  ./ ({n})" if rel == '.' else f"  {rel}/ ({n})"
                self._dir_combo.addItem(label, d)
            self._dir_combo.show()
            self._dir_label.hide()
        else:
            self._dir_combo.hide()
            self._dir_label.show()
            if not processed:
                db_path = os.path.join(directory, ".superpicky", "report.db")
                if not os.path.exists(db_path):
                    self._show_no_db_hint(directory)
                    self._dir_combo.blockSignals(False)
                    return

        self._dir_combo.blockSignals(False)
        self._directory = directory

        if len(processed) > 1:
            self._load_merged(directory, processed)
        elif len(processed) == 1:
            self._load_single(processed[0])
        else:
            self._load_single(directory)

    def _count_db_photos(self, directory: str) -> int:
        db_path = os.path.join(directory, ".superpicky", "report.db")
        if not os.path.exists(db_path):
            return 0
        try:
            import sqlite3 as _sql
            conn = _sql.connect(db_path)
            n = conn.execute("SELECT COUNT(*) FROM photos WHERE rating != -1").fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0

    def _load_single(self, directory: str):
        self._is_merged = False
        try:
            self._db = ReportDB(directory)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self._directory = directory
        short_name = os.path.basename(directory) or directory
        self._dir_label.setText(short_name)
        self._dir_label.setToolTip(directory)
        self._all_photos = self._db.get_all_photos()
        self._compute_burst_ids()
        self._filter_panel.reset_all()
        species = self._db.get_distinct_species(use_en=self.i18n.current_lang.startswith('en'))
        self._filter_panel.update_species_list(species)
        if len(self._all_photos) > 0 and len(self._filtered_photos) == 0:
            self._filter_panel.select_all_ratings()
        self.setWindowTitle(f"{self.i18n.t('browser.title')} \u2014 {short_name}")

    def _load_merged(self, root_dir: str, sub_dirs: list):
        from tools.merged_report_db import MergedReportDB
        self._is_merged = True
        try:
            self._db = MergedReportDB(root_dir, sub_dirs)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self._directory = root_dir
        self._all_photos = self._db.get_all_photos()
        self._compute_burst_ids()
        self._filter_panel.reset_all()
        species = self._db.get_distinct_species(use_en=self.i18n.current_lang.startswith('en'))
        self._filter_panel.update_species_list(species)
        if len(self._all_photos) > 0 and len(self._filtered_photos) == 0:
            self._filter_panel.select_all_ratings()
        short = os.path.basename(root_dir) or root_dir
        self.setWindowTitle(f"{self.i18n.t('browser.title')} \u2014 {short} (All)")

    def _on_subdir_changed(self, index: int):
        if index < 0:
            return
        value = self._dir_combo.itemData(index)
        if value == "__ALL__":
            self._load_merged(self._directory, self._sub_dirs)
        else:
            self._load_single(value)

    def _compute_burst_ids(self):
        """基于拍摄时间做 burst 分组，时间差 <= 1 秒视为同一组。"""
        if not self._db:
            return

        photos = self._db.get_all_photos()
        self._db.clear_burst_ids()
        burst_map = _build_burst_update_map(photos)
        if burst_map:
            self._db.update_burst_ids(burst_map)
            self._all_photos = self._db.get_all_photos()
        else:
            self._all_photos = photos

        if not self._all_photos:
            self._burst_totals = Counter()
            return

        self._burst_totals = _burst_totals_from_photos(self._all_photos)

    # ------------------------------------------------------------------
    #  私有槽
    # ------------------------------------------------------------------

    @Slot()
    def _go_back_to_main(self):
        """P2: 隐藏结果浏览器，通过 closed 信号通知主窗口恢复显示。"""
        self.hide()
        self.closed.emit()

    def _resolve_photo_paths(self, photo: dict) -> dict:
        _PATH_KEYS = ('original_path', 'current_path', 'temp_jpeg_path',
                      'debug_crop_path', 'yolo_debug_path')
        resolved = dict(photo)
        if self._is_merged and 'source_dir' in photo:
            base_dir = os.path.join(self._directory, photo['source_dir'])
        else:
            base_dir = self._directory
        resolved['_base_dir'] = base_dir
        for key in _PATH_KEYS:
            val = photo.get(key)
            if val and not os.path.isabs(val):
                resolved[key] = os.path.join(base_dir, val)
        bid = resolved.get("burst_id")
        if bid is not None and hasattr(self, '_burst_totals'):
            resolved["burst_total"] = self._burst_totals.get(bid, 1)
        return resolved

    @Slot(dict)
    def _apply_filters(self, filters: dict):
        if not self._db:
            self._thumb_grid.load_photos([])
            self._update_status(0, 0)
            return

        raw_photos = self._db.get_photos_by_filters(filters)
        resolved_photos = [self._resolve_photo_paths(p) for p in raw_photos]
        self._raw_filtered_photos = resolved_photos

        total = len(self._all_photos)
        filtered = len(resolved_photos)
        self._update_status(total, filtered)
        self._filter_panel.update_count(filtered)
        
        self._update_display_list()

    def _update_display_list(self):
        """Flattens the filtered photos list considering the expanded state of burst groups."""
        # Group by burst_id to find the "best" representative photo for each group
        burst_map = {}
        for p in self._raw_filtered_photos:
            bid = p.get("burst_id")
            if bid is not None:
                if bid not in burst_map:
                    burst_map[bid] = []
                burst_map[bid].append(p)
        burst_map = {bid: photos for bid, photos in burst_map.items() if len(photos) > 1}

        best_burst_photos = {}
        for bid, photos in burst_map.items():
            best_photo = max(photos, key=lambda x: (x.get("rating", 0), x.get("composite_score", 0.0)))
            best_burst_photos[bid] = _photo_identity(best_photo)

        grouped_photos = []
        processed_bursts = set()
        
        for p in self._raw_filtered_photos:
            bid = p.get("burst_id")
            
            if bid is None or bid not in burst_map:
                # Normal photo
                grouped_photos.append(dict(p))
            else:
                # It's part of a burst
                if bid in processed_bursts:
                    continue # Already handled this burst
                    
                processed_bursts.add(bid)
                burst_photos = burst_map[bid]
                
                burst_photos = sorted(burst_photos, key=_burst_sort_key)
                
                if bid in self._expanded_bursts:
                    # Expanded: add all photos in chronological order
                    for i, bp in enumerate(burst_photos, 1):
                        expanded_photo = dict(bp)
                        expanded_photo["is_expanded_burst_member"] = True
                        expanded_photo["burst_position_index"] = i
                        expanded_photo["burst_total_count"] = len(burst_photos)
                        expanded_photo["burst_id"] = bid
                        grouped_photos.append(expanded_photo)
                else:
                    # Collapsed: add only the representative photo
                    best_identity = best_burst_photos[bid]
                    best_p = next(x for x in burst_photos if _photo_identity(x) == best_identity)
                    
                    group_photo = dict(best_p)
                    group_photo["is_burst_group"] = True
                    group_photo["burst_count"] = len(burst_photos)
                    group_photo["burst_photos"] = burst_photos
                    group_photo["burst_id"] = bid
                    grouped_photos.append(group_photo)

        # Do NOT sort grouped_photos here. We want them in the exact order they appeared in _raw_filtered_photos,
        # which preserves the sorting (rating, time, etc.) applied by the database!
        # When a burst group is encountered, it is placed at the position of its first appearing member.
        
        self._filtered_photos = grouped_photos
        
        # Save selection state to try and restore it
        current_selection = self._thumb_grid._selected_key
        
        self._thumb_grid.load_photos(self._filtered_photos, keep_scroll=True)
        self._fullscreen.set_photo_list(self._filtered_photos)
        self._fullscreen_nav_photos = list(self._filtered_photos)

        if self._filtered_photos:
            target_identity = current_selection if current_selection else _photo_identity(self._filtered_photos[0])
            if not any(_photo_identity(p) == target_identity for p in self._filtered_photos):
                target_identity = _photo_identity(self._filtered_photos[0])

            selected_photo = next(p for p in self._filtered_photos if _photo_identity(p) == target_identity)
            self._thumb_grid.select_photo(selected_photo)
            self._detail_panel.show_photo(selected_photo)
        else:
            self._detail_panel.clear()
            
    @Slot(int)
    def _toggle_burst(self, burst_id: int):
        if len([p for p in self._raw_filtered_photos if p.get("burst_id") == burst_id]) <= 1:
            self._expanded_bursts.discard(burst_id)
            return
        if burst_id in self._expanded_bursts:
            self._expanded_bursts.remove(burst_id)
        else:
            self._expanded_bursts.add(burst_id)
        self._update_display_list()

    @Slot(dict)
    def _on_photo_selected(self, photo: dict):
        self._detail_panel.show_photo(photo)

    def _build_burst_sequence(self, photo: dict) -> list:
        burst_id = photo.get("burst_id")
        if burst_id is None:
            return []

        if photo.get("burst_photos"):
            burst_photos = [dict(p) for p in photo.get("burst_photos", [])]
        else:
            burst_photos = [dict(p) for p in self._raw_filtered_photos if p.get("burst_id") == burst_id]

        burst_photos = sorted(burst_photos, key=_burst_sort_key)
        if len(burst_photos) <= 1:
            return []

        total = len(burst_photos)
        sequence = []
        for pos, burst_photo in enumerate(burst_photos, 1):
            seq_photo = dict(burst_photo)
            seq_photo["is_expanded_burst_member"] = True
            seq_photo["burst_position_index"] = pos
            seq_photo["burst_total_count"] = total
            seq_photo["burst_id"] = burst_id
            sequence.append(seq_photo)
        return sequence

    def _build_collapsed_navigation_list(self) -> list:
        burst_map = {}
        for photo in self._raw_filtered_photos:
            burst_id = photo.get("burst_id")
            if burst_id is not None:
                burst_map.setdefault(burst_id, []).append(photo)

        collapsed_photos = []
        processed_bursts = set()
        for photo in self._raw_filtered_photos:
            burst_id = photo.get("burst_id")
            if burst_id is None:
                collapsed_photos.append(dict(photo))
                continue
            if burst_id in processed_bursts:
                continue

            processed_bursts.add(burst_id)
            burst_photos = sorted(burst_map[burst_id], key=_burst_sort_key)
            best_photo = max(burst_photos, key=lambda x: (x.get("rating", 0), x.get("composite_score", 0.0)))
            group_photo = dict(best_photo)
            group_photo.pop("is_expanded_burst_member", None)
            group_photo.pop("burst_position_index", None)
            group_photo.pop("burst_total_count", None)
            group_photo["is_burst_group"] = True
            group_photo["burst_count"] = len(burst_photos)
            group_photo["burst_photos"] = [dict(p) for p in burst_photos]
            group_photo["burst_id"] = burst_id
            collapsed_photos.append(group_photo)
        return collapsed_photos

    def _show_fullscreen_photo(self, photo: dict, nav_photos: Optional[list] = None):
        self._fullscreen_nav_photos = list(nav_photos) if nav_photos is not None else list(self._filtered_photos)
        self._fullscreen.set_photo_list(self._fullscreen_nav_photos)
        self._fullscreen.show_photo(photo)
        self._detail_panel.show_photo(photo)

        if any(_photo_identity(p) == _photo_identity(photo) for p in self._filtered_photos):
            self._thumb_grid.select_photo(photo)

    def _build_collapsed_burst_photo(self, photo: dict) -> Optional[dict]:
        burst_id = photo.get("burst_id")
        if burst_id is None:
            return None

        collapsed_nav = self._build_collapsed_navigation_list()
        existing_group = next(
            (dict(p) for p in collapsed_nav if p.get("burst_id") == burst_id and p.get("is_burst_group")),
            None,
        )
        if existing_group:
            return existing_group

        burst_photos = [dict(p) for p in self._raw_filtered_photos if p.get("burst_id") == burst_id]
        burst_photos = sorted(burst_photos, key=_burst_sort_key)
        if len(burst_photos) <= 1:
            return None

        base_photo = next(
            (dict(p) for p in self._filtered_photos if _photo_identity(p) == _photo_identity(photo)),
            None,
        )
        if base_photo is None:
            base_photo = dict(max(burst_photos, key=lambda x: (x.get("rating", 0), x.get("composite_score", 0.0))))

        base_photo.pop("is_expanded_burst_member", None)
        base_photo.pop("burst_position_index", None)
        base_photo.pop("burst_total_count", None)
        base_photo["is_burst_group"] = True
        base_photo["burst_count"] = len(burst_photos)
        base_photo["burst_photos"] = burst_photos
        base_photo["burst_id"] = burst_id
        return base_photo

    def _is_sequence_mode(self, photo: dict) -> bool:
        burst_id = photo.get("burst_id")
        if burst_id is None or not photo.get("is_expanded_burst_member"):
            return False
        return (
            len(self._fullscreen_nav_photos) > 1
            and all(p.get("burst_id") == burst_id for p in self._fullscreen_nav_photos)
        )

    @Slot(dict)
    def _open_burst_sequence(self, photo: dict):
        if self._is_sequence_mode(photo):
            collapsed_photo = self._build_collapsed_burst_photo(photo)
            if collapsed_photo:
                self._show_fullscreen_photo(collapsed_photo, nav_photos=self._build_collapsed_navigation_list())
                self._detail_panel._switch_view(True)
                self._toolbar.hide()
                self._stack.setCurrentIndex(1)
                self._fullscreen.setFocus()
            return

        sequence = self._build_burst_sequence(photo)
        if not sequence:
            return

        target_identity = _photo_identity(photo)
        selected_photo = next((p for p in sequence if _photo_identity(p) == target_identity), sequence[0])
        self._show_fullscreen_photo(selected_photo, nav_photos=sequence)
        self._detail_panel._switch_view(True)
        self._toolbar.hide()
        self._stack.setCurrentIndex(1)
        self._fullscreen.setFocus()

    @Slot()
    def _prev_photo(self):
        photo = self._thumb_grid.select_prev()
        if photo:
            self._detail_panel.show_photo(photo)
            if self._stack.currentIndex() == 1:   # 全屏模式同步大图
                self._fullscreen.show_photo(photo)

    @Slot()
    def _next_photo(self):
        photo = self._thumb_grid.select_next()
        if photo:
            self._detail_panel.show_photo(photo)
            if self._stack.currentIndex() == 1:   # 全屏模式同步大图
                self._fullscreen.show_photo(photo)

    @Slot(dict)
    def _enter_fullscreen(self, photo: dict):
        """双击缩略图 → 进入全屏查看器。"""
        if photo.get("is_expanded_burst_member"):
            self._open_burst_sequence(photo)
            return

        self._show_fullscreen_photo(photo)
        self._detail_panel._switch_view(True)   # 进入全屏 → 切到裁切图
        self._stack.setCurrentIndex(1)
        self._fullscreen.setFocus()  # 确保全屏 viewer 获得键盘焦点

    @Slot()
    def _exit_fullscreen(self):
        """返回 grid 视图。"""
        self._stack.setCurrentIndex(0)
        self._fullscreen_nav_photos = list(self._filtered_photos)
        self._detail_panel._switch_view(False)  # 退出全屏 → 切回全图
        self.setFocus()  # 确保窗口拿回焦点

    @Slot()
    def _fullscreen_prev(self):
        """全屏模式：上一张。"""
        if not self._fullscreen_nav_photos:
            return
        current_key = _photo_identity(getattr(self._fullscreen, "_current_photo", {}) or {})
        nav_keys = [_photo_identity(p) for p in self._fullscreen_nav_photos]
        try:
            idx = nav_keys.index(current_key)
        except ValueError:
            idx = -1
        new_idx = idx - 1
        if 0 <= new_idx < len(self._fullscreen_nav_photos):
            self._show_fullscreen_photo(self._fullscreen_nav_photos[new_idx], nav_photos=self._fullscreen_nav_photos)

    @Slot()
    def _fullscreen_next(self):
        """全屏模式：下一张。"""
        if not self._fullscreen_nav_photos:
            return
        current_key = _photo_identity(getattr(self._fullscreen, "_current_photo", {}) or {})
        nav_keys = [_photo_identity(p) for p in self._fullscreen_nav_photos]
        try:
            idx = nav_keys.index(current_key)
        except ValueError:
            idx = -1
        new_idx = idx + 1
        if 0 <= new_idx < len(self._fullscreen_nav_photos):
            self._show_fullscreen_photo(self._fullscreen_nav_photos[new_idx], nav_photos=self._fullscreen_nav_photos)

    @Slot(object, int)
    def _on_rating_changed(self, photo_or_filename, new_rating: int):
        """详情面板评分修改：写入 DB + 刷新缩略图角标 + 异步写 EXIF。"""
        current_photo = _coerce_photo(
            photo_or_filename,
            self._filtered_photos,
            getattr(self._detail_panel, "_current_photo", None),
        ) or {}
        filename = current_photo.get("filename") or (photo_or_filename if isinstance(photo_or_filename, str) else "")
        db_key = _photo_db_key(current_photo) if current_photo else filename
        if self._db:
            self._db.update_photo(db_key, {"rating": new_rating})
        for p in self._filtered_photos:
            if _photo_identity(p) == _photo_identity(current_photo) or (
                not current_photo and p.get("filename") == filename
            ):
                p["rating"] = new_rating
                break
        self._thumb_grid.refresh_photo(current_photo or filename, new_rating)
        # 异步写 EXIF（遵守 metadata_write_mode 设置，mode=none 时内部自动跳过）
        file_path = self._get_photo_file_path(current_photo or filename)
        if file_path:
            import threading
            from tools.exiftool_manager import get_exiftool_manager
            threading.Thread(
                target=get_exiftool_manager().set_rating_and_pick,
                args=(file_path, new_rating),
                daemon=True,
            ).start()

    def _get_photo_file_path(self, photo_or_filename) -> "str | None":
        """根据 photo 或 filename 查找照片绝对路径。"""
        photo = _coerce_photo(photo_or_filename, self._filtered_photos)
        if photo:
            path = photo.get("current_path") or photo.get("original_path") or ""
            return path if path and os.path.exists(path) else None
        return None

    @Slot(list)
    def _on_multi_selection_changed(self, photos: list):
        """C3：多选状态变化，更新工具栏显示。"""
        n = len(photos)
        if n > 1:
            self._select_count_label.setText(self.i18n.t("browser.selected_count").format(n=n))
            self._select_count_label.show()
        else:
            self._select_count_label.hide()
        # C5：仅当选中 2 张时显示对比按钮
        self._compare_btn.setVisible(n == 2)

    def _show_context_menu(self, photo: dict, pos):
        base_dir = photo.get('_base_dir', self._directory)
        _show_context_menu_impl(self, photo, pos, base_dir)

    @Slot(dict, object)
    def _on_fullscreen_context_menu(self, photo: dict, global_pos):
        """全屏大图右键菜单。"""
        _show_context_menu_impl(self, photo, global_pos, self._directory)

    @Slot(dict)
    def _on_delete_photo(self, photo: dict):
        """全屏模式删除图片：确认 → 回收站 → DB 删除 → 缩略图同步 → 跳下一张。"""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        filename = photo.get("filename", "")
        if not filename:
            return

        # 1. 确认弹窗（可勾选「以后不再询问」）
        if cfg.delete_confirm:
            from PySide6.QtWidgets import QCheckBox
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(self.i18n.t("browser.delete_title"))
            msg_box.setText(self.i18n.t("browser.delete_msg").format(filename=filename))
            msg_box.setIcon(QMessageBox.Warning)
            yes_btn = msg_box.addButton(self.i18n.t("browser.delete_confirm_btn"), QMessageBox.AcceptRole)
            msg_box.addButton(self.i18n.t("browser.delete_cancel_btn"), QMessageBox.RejectRole)
            cb = QCheckBox(self.i18n.t("browser.delete_no_ask"))
            msg_box.setCheckBox(cb)
            msg_box.exec()
            if msg_box.clickedButton() != yes_btn:
                return
            if cb.isChecked():
                cfg.set_delete_confirm(False)
                cfg.save()

        # 2. 移入回收站
        filepath = photo.get("current_path") or photo.get("original_path") or ""
        if filepath and not _move_to_trash(filepath):
            QMessageBox.warning(
                self,
                self.i18n.t("browser.delete_failed"),
                self.i18n.t("browser.delete_failed_msg").format(error=filepath)
            )
            return

        # 3. DB 删除
        if self._db:
            self._db.delete_photo(_photo_db_key(photo))

        # 4. 从内存列表移除
        target_identity = _photo_identity(photo)
        self._filtered_photos = [p for p in self._filtered_photos if _photo_identity(p) != target_identity]
        self._all_photos = [p for p in self._all_photos if _photo_identity(p) != target_identity]

        # 5. 缩略图同步
        self._thumb_grid.remove_photo(photo)
        self._fullscreen.set_photo_list(self._filtered_photos)

        # 6. 跳转逻辑
        if self._filtered_photos:
            nxt = self._thumb_grid.select_next()
            if nxt is None:
                nxt = self._thumb_grid.select_prev()
            if nxt:
                self._fullscreen.show_photo(nxt)
                self._detail_panel.show_photo(nxt)
            else:
                self._exit_fullscreen()
        else:
            self._exit_fullscreen()

        # 7. 更新状态栏
        self._update_status(len(self._all_photos), len(self._filtered_photos))

    def _enter_comparison(self):
        """C5：进入 2-up 对比视图（ResultsBrowserWindow）。"""
        photos = self._thumb_grid.get_multi_selected_photos()
        if len(photos) >= 2:
            self._comparison.show_pair(photos[0], photos[1])
            self._detail_panel.hide()   # 对比模式不显示详情面板
            self._stack.setCurrentIndex(2)
            self._comparison.setFocus()

    def _exit_comparison(self):
        """C5：退出对比视图，回到 grid（ResultsBrowserWindow）。"""
        self._detail_panel.show()
        self._stack.setCurrentIndex(0)
        self.setFocus()

    @Slot(int)
    def _on_size_changed(self, value: int):
        self._thumb_grid.set_thumb_size(value)

    # ------------------------------------------------------------------
    #  键盘快捷键
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        in_fullscreen = (self._stack.currentIndex() == 1)

        if key in (Qt.Key_Left, Qt.Key_Up):
            if in_fullscreen:
                self._fullscreen_prev()
            else:
                self._prev_photo()
        elif key in (Qt.Key_Right, Qt.Key_Down):
            if in_fullscreen:
                self._fullscreen_next()
            else:
                self._next_photo()
        elif key == Qt.Key_Tab:
            # Tab: 开关右侧详情面板
            self._detail_panel.setVisible(not self._detail_panel.isVisible())
        elif key == Qt.Key_Plus or key == Qt.Key_Equal:
            self._size_slider.setValue(min(300, self._size_slider.value() + 20))
        elif key == Qt.Key_Minus:
            self._size_slider.setValue(max(80, self._size_slider.value() - 20))
        elif key == Qt.Key_Escape:
            current_page = self._stack.currentIndex()
            if current_page == 1:
                self._exit_fullscreen()
            elif current_page == 2:
                self._exit_comparison()
            else:
                # grid 模式：有多选时先清选，否则关闭窗口
                if self._thumb_grid.get_multi_selected_photos():
                    self._thumb_grid.clear_multi_select()
                else:
                    self.close()
        elif key == Qt.Key_C:
            if not in_fullscreen and self._stack.currentIndex() == 0:
                photos = self._thumb_grid.get_multi_selected_photos()
                if len(photos) >= 2:
                    self._enter_comparison()
        elif key == Qt.Key_F:
            if in_fullscreen:
                self._fullscreen.toggle_focus()
            else:
                self._detail_panel._switch_view(not self._detail_panel._use_crop_view)
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------

    def _update_status(self, total: int, filtered: int):
        t = self.i18n.t("browser.total_photos").format(total=total)
        f = self.i18n.t("browser.filtered_photos").format(count=filtered)
        self._status_bar.showMessage(f"{t}  |  {f}")

    def _show_no_db_hint(self, directory: str):
        QMessageBox.information(
            self,
            self.i18n.t("browser.no_db"),
            f"{directory}\n\n{self.i18n.t('browser.no_db_hint')}"
        )

    # ------------------------------------------------------------------
    #  窗口关闭
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self.cleanup()
        self.closed.emit()
        super().closeEvent(event)

    def cleanup(self):
        """释放线程和 DB 连接。"""
        try:
            self._thumb_grid.cleanup()
        except Exception:
            pass
        try:
            self._fullscreen.cleanup()
        except Exception:
            pass
        try:
            self._comparison.cleanup()
        except Exception:
            pass
        try:
            self._detail_panel.cleanup()
        except Exception:
            pass
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None


# ============================================================
#  ResultsBrowserWidget — 嵌入式浏览器（供主窗口 QStackedWidget 使用）
# ============================================================

class ResultsBrowserWidget(QWidget):
    """
    与 ResultsBrowserWindow 相同的三栏布局，但以 QWidget 形式嵌入主窗口 QStackedWidget。
    信号 back_requested 在用户点击「返回」时发出。
    """
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.i18n = get_i18n()
        self._db: Optional[ReportDB] = None
        self._directory: str = ""
        self._all_photos: list = []
        self._filtered_photos: list = []
        self._raw_filtered_photos: list = [] # V5: Store unfiltered sorted photos
        self._expanded_bursts: set = set()   # V5: Track expanded burst IDs
        self._is_merged: bool = False
        self._sub_dirs: list = []

        self.setStyleSheet(GLOBAL_STYLE)
        self.setFocusPolicy(Qt.StrongFocus)
        self._setup_ui()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        self._toolbar = self._build_toolbar()
        main_v.addWidget(self._toolbar)

        outer_h = QHBoxLayout()
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_h.setSpacing(0)
        main_v.addLayout(outer_h, 1)

        self._stack = QStackedWidget()
        outer_h.addWidget(self._stack, 1)

        # Page 0: 过滤面板 + 缩略图网格
        two_col = QWidget()
        main_h = QHBoxLayout(two_col)
        main_h.setContentsMargins(0, 0, 0, 0)
        main_h.setSpacing(0)

        self._filter_panel = FilterPanel(self.i18n, self)
        self._filter_panel.filters_changed.connect(self._apply_filters)
        main_h.addWidget(self._filter_panel)

        center_widget = QWidget()
        center_widget.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self._thumb_grid = ThumbnailGrid(self.i18n, self)
        self._thumb_grid.photo_selected.connect(self._on_photo_selected)
        self._thumb_grid.photo_double_clicked.connect(self._enter_fullscreen)
        self._thumb_grid.multi_selection_changed.connect(self._on_multi_selection_changed)
        self._thumb_grid.burst_badge_clicked.connect(self._toggle_burst)
        center_layout.addWidget(self._thumb_grid, 1)

        main_h.addWidget(center_widget, 1)
        self._stack.addWidget(two_col)

        # Page 1: 全屏查看器
        self._fullscreen = FullscreenViewer(self.i18n, self)
        self._fullscreen.close_requested.connect(self._exit_fullscreen)
        self._fullscreen.prev_requested.connect(self._fullscreen_prev)
        self._fullscreen.next_requested.connect(self._fullscreen_next)
        self._fullscreen.delete_requested.connect(self._on_delete_photo)
        self._fullscreen.context_menu_requested.connect(self._on_fullscreen_context_menu)
        self._stack.addWidget(self._fullscreen)

        # Page 2: 对比查看器（C5）
        self._comparison = ComparisonViewer(self.i18n, self)
        self._comparison.close_requested.connect(self._exit_comparison)
        self._comparison.rating_changed.connect(self._on_rating_changed)
        self._stack.addWidget(self._comparison)

        # 右侧详情面板
        self._detail_panel = DetailPanel(self.i18n, self)
        self._detail_panel.prev_requested.connect(self._prev_photo)
        self._detail_panel.next_requested.connect(self._next_photo)
        self._detail_panel.rating_change_requested.connect(self._on_rating_changed)
        outer_h.addWidget(self._detail_panel, 0)

        # 底部状态栏（简单 label）
        self._status_label = QLabel("—")
        self._status_label.setFixedHeight(24)
        self._status_label.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['bg_elevated']};
                color: {COLORS['text_secondary']};
                font-size: 11px;
                border-top: 1px solid {COLORS['border_subtle']};
                padding: 4px 16px;
            }}
        """)
        main_v.addWidget(self._status_label)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"""
            QWidget#toolbar {{
                background-color: {COLORS['bg_elevated']};
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        back_btn = QPushButton(self.i18n.t("browser.back"))
        back_btn.setObjectName("tertiary")
        back_btn.setFixedHeight(32)
        back_btn.setToolTip(self.i18n.t("browser.back_tooltip"))
        back_btn.clicked.connect(self.back_requested)
        layout.addWidget(back_btn)

        layout.addSpacing(8)

        # Directory switcher combo box (hidden by default, shown for batch dirs)
        self._dir_combo = QComboBox()
        self._dir_combo.setFixedHeight(32)
        self._dir_combo.setMinimumWidth(200)
        self._dir_combo.setMaximumWidth(400)
        self._dir_combo.setStyleSheet(f"""
            QComboBox {{
                color: {COLORS['text_secondary']};
                background: {COLORS['bg_primary']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
                font-family: {FONTS['mono']};
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
        """)
        self._dir_combo.currentIndexChanged.connect(self._on_subdir_changed)
        self._dir_combo.hide()
        layout.addWidget(self._dir_combo)

        self._dir_label = QLabel(self.i18n.t("browser.open_dir"))
        self._dir_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 12px;
                font-family: {FONTS['mono']};
                background: transparent;
            }}
        """)
        self._dir_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self._dir_label)

        layout.addSpacing(16)

        # 多选计数标签（C3，默认隐藏）
        self._select_count_label = QLabel("")
        self._select_count_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['accent']};
                font-size: 12px;
                background: transparent;
            }}
        """)
        self._select_count_label.hide()
        layout.addWidget(self._select_count_label)

        # 对比按钮（C5，默认隐藏，多选2张时显示）
        self._compare_btn = QPushButton(self.i18n.t("browser.compare_btn"))
        self._compare_btn.setObjectName("secondary")
        self._compare_btn.setFixedHeight(32)
        self._compare_btn.hide()
        self._compare_btn.clicked.connect(self._enter_comparison)
        layout.addWidget(self._compare_btn)

        size_label = QLabel(self.i18n.t("browser.size_label"))
        size_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px; background: transparent;")
        layout.addWidget(size_label)

        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(80, 300)
        self._size_slider.setValue(160)
        self._size_slider.setFixedWidth(100)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        layout.addWidget(self._size_slider)

        return bar

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def open_directory(self, directory: str):
        """Load report.db from directory. Supports batch multi-dir mode."""
        if not directory:
            return

        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

        self._is_merged = False
        self._sub_dirs = []

        from tools.merged_report_db import find_processed_subdirs
        processed = find_processed_subdirs(directory)

        self._dir_combo.blockSignals(True)
        self._dir_combo.clear()

        if len(processed) > 1:
            self._sub_dirs = processed
            total = sum(self._count_db_photos(d) for d in processed)
            self._dir_combo.addItem(f"\U0001f4c2 All ({total})", "__ALL__")
            for d in processed:
                rel = os.path.relpath(d, directory)
                n = self._count_db_photos(d)
                label = f"  ./ ({n})" if rel == '.' else f"  {rel}/ ({n})"
                self._dir_combo.addItem(label, d)
            self._dir_combo.show()
            self._dir_label.hide()
        else:
            self._dir_combo.hide()
            self._dir_label.show()
            if not processed:
                db_path = os.path.join(directory, ".superpicky", "report.db")
                if not os.path.exists(db_path):
                    QMessageBox.information(
                        self,
                        self.i18n.t("browser.no_db"),
                        f"{directory}\n\n{self.i18n.t('browser.no_db_hint')}"
                    )
                    self._dir_combo.blockSignals(False)
                    return

        self._dir_combo.blockSignals(False)
        self._directory = directory

        if len(processed) > 1:
            self._load_merged(directory, processed)
        elif len(processed) == 1:
            self._load_single(processed[0])
        else:
            self._load_single(directory)

    def _count_db_photos(self, directory: str) -> int:
        db_path = os.path.join(directory, ".superpicky", "report.db")
        if not os.path.exists(db_path):
            return 0
        try:
            import sqlite3 as _sql
            conn = _sql.connect(db_path)
            n = conn.execute("SELECT COUNT(*) FROM photos WHERE rating != -1").fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0

    def _load_single(self, directory: str):
        self._is_merged = False
        try:
            self._db = ReportDB(directory)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self._directory = directory
        short_name = os.path.basename(directory) or directory
        self._dir_label.setText(short_name)
        self._dir_label.setToolTip(directory)
        self._all_photos = self._db.get_all_photos()
        self._compute_burst_ids()
        self._filter_panel.reset_all()
        species = self._db.get_distinct_species(use_en=self.i18n.current_lang.startswith('en'))
        self._filter_panel.update_species_list(species)
        if len(self._all_photos) > 0 and len(self._filtered_photos) == 0:
            self._filter_panel.select_all_ratings()

    def _load_merged(self, root_dir: str, sub_dirs: list):
        from tools.merged_report_db import MergedReportDB
        self._is_merged = True
        try:
            self._db = MergedReportDB(root_dir, sub_dirs)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return
        self._directory = root_dir
        self._all_photos = self._db.get_all_photos()
        self._compute_burst_ids()
        self._filter_panel.reset_all()
        species = self._db.get_distinct_species(use_en=self.i18n.current_lang.startswith('en'))
        self._filter_panel.update_species_list(species)
        if len(self._all_photos) > 0 and len(self._filtered_photos) == 0:
            self._filter_panel.select_all_ratings()

    def _on_subdir_changed(self, index: int):
        if index < 0:
            return
        value = self._dir_combo.itemData(index)
        if value == "__ALL__":
            self._load_merged(self._directory, self._sub_dirs)
        else:
            self._load_single(value)

    def _compute_burst_ids(self):
        """基于拍摄时间做 burst 分组，时间差 <= 1 秒视为同一组。"""
        if not self._db:
            return

        photos = self._db.get_all_photos()
        self._db.clear_burst_ids()
        burst_map = _build_burst_update_map(photos)
        if burst_map:
            self._db.update_burst_ids(burst_map)
            self._all_photos = self._db.get_all_photos()
        else:
            self._all_photos = photos

        if not self._all_photos:
            self._burst_totals = Counter()
            return

        self._burst_totals = _burst_totals_from_photos(self._all_photos)

    def cleanup(self):
        """释放 DB 连接（切换回处理页前调用）。"""
        try:
            self._thumb_grid.cleanup()
        except Exception:
            pass
        try:
            self._fullscreen.cleanup()
        except Exception:
            pass
        try:
            self._comparison.cleanup()
        except Exception:
            pass
        try:
            self._detail_panel.cleanup()
        except Exception:
            pass
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    # ------------------------------------------------------------------
    #  私有槽
    # ------------------------------------------------------------------

    def _resolve_photo_paths(self, photo: dict) -> dict:
        _PATH_KEYS = ('original_path', 'current_path', 'temp_jpeg_path',
                      'debug_crop_path', 'yolo_debug_path')
        resolved = dict(photo)
        if self._is_merged and 'source_dir' in photo:
            base_dir = os.path.join(self._directory, photo['source_dir'])
        else:
            base_dir = self._directory
        resolved['_base_dir'] = base_dir
        for key in _PATH_KEYS:
            val = photo.get(key)
            if val and not os.path.isabs(val):
                resolved[key] = os.path.join(base_dir, val)
        bid = resolved.get("burst_id")
        if bid is not None and hasattr(self, '_burst_totals'):
            resolved["burst_total"] = self._burst_totals.get(bid, 1)
        return resolved

    @Slot(dict)
    def _apply_filters(self, filters: dict):
        if not self._db:
            self._thumb_grid.load_photos([])
            self._update_status(0, 0)
            return

        raw_photos = self._db.get_photos_by_filters(filters)
        self._raw_filtered_photos = [self._resolve_photo_paths(p) for p in raw_photos]
        total = len(self._all_photos)
        filtered = len(self._raw_filtered_photos)
        self._update_status(total, filtered)
        self._filter_panel.update_count(filtered)
        self._update_display_list()

    def _update_display_list(self):
        burst_map = {}
        for photo in self._raw_filtered_photos:
            burst_id = photo.get("burst_id")
            if burst_id is None:
                continue
            burst_map.setdefault(burst_id, []).append(photo)
        burst_map = {burst_id: photos for burst_id, photos in burst_map.items() if len(photos) > 1}

        best_burst_photos = {}
        for burst_id, photos in burst_map.items():
            best_photo = max(photos, key=lambda x: (x.get("rating", 0), x.get("composite_score", 0.0)))
            best_burst_photos[burst_id] = _photo_identity(best_photo)

        grouped_photos = []
        processed_bursts = set()
        for photo in self._raw_filtered_photos:
            burst_id = photo.get("burst_id")
            if burst_id is None or burst_id not in burst_map:
                grouped_photos.append(dict(photo))
                continue
            if burst_id in processed_bursts:
                continue

            processed_bursts.add(burst_id)
            burst_photos = sorted(burst_map[burst_id], key=_burst_sort_key)
            if burst_id in self._expanded_bursts:
                for pos, burst_photo in enumerate(burst_photos, 1):
                    expanded_photo = dict(burst_photo)
                    expanded_photo["is_expanded_burst_member"] = True
                    expanded_photo["burst_position_index"] = pos
                    expanded_photo["burst_total_count"] = len(burst_photos)
                    grouped_photos.append(expanded_photo)
            else:
                best_identity = best_burst_photos[burst_id]
                best_photo = next(x for x in burst_photos if _photo_identity(x) == best_identity)
                group_photo = dict(best_photo)
                group_photo["is_burst_group"] = True
                group_photo["burst_count"] = len(burst_photos)
                group_photo["burst_photos"] = burst_photos
                grouped_photos.append(group_photo)

        self._filtered_photos = grouped_photos
        current_selection = self._thumb_grid._selected_key
        self._thumb_grid.load_photos(self._filtered_photos, keep_scroll=True)
        self._fullscreen.set_photo_list(self._filtered_photos)

        if self._filtered_photos:
            target_identity = current_selection or _photo_identity(self._filtered_photos[0])
            if not any(_photo_identity(p) == target_identity for p in self._filtered_photos):
                target_identity = _photo_identity(self._filtered_photos[0])
            selected_photo = next(p for p in self._filtered_photos if _photo_identity(p) == target_identity)
            self._thumb_grid.select_photo(selected_photo)
            self._detail_panel.show_photo(selected_photo)
        else:
            self._detail_panel.clear()

    @Slot(int)
    def _toggle_burst(self, burst_id: int):
        if len([p for p in self._raw_filtered_photos if p.get("burst_id") == burst_id]) <= 1:
            self._expanded_bursts.discard(burst_id)
            return
        if burst_id in self._expanded_bursts:
            self._expanded_bursts.remove(burst_id)
        else:
            self._expanded_bursts.add(burst_id)
        self._update_display_list()

    @Slot(dict)
    def _on_photo_selected(self, photo: dict):
        self._detail_panel.show_photo(photo)

    @Slot()
    def _prev_photo(self):
        photo = self._thumb_grid.select_prev()
        if photo:
            self._detail_panel.show_photo(photo)
            if self._stack.currentIndex() == 1:   # 全屏模式同步大图
                self._fullscreen.show_photo(photo)

    @Slot()
    def _next_photo(self):
        photo = self._thumb_grid.select_next()
        if photo:
            self._detail_panel.show_photo(photo)
            if self._stack.currentIndex() == 1:   # 全屏模式同步大图
                self._fullscreen.show_photo(photo)

    @Slot(dict)
    def _enter_fullscreen(self, photo: dict):
        if photo.get("is_expanded_burst_member"):
            self._open_burst_sequence(photo)
            return

        self._show_fullscreen_photo(photo)
        self._detail_panel._switch_view(True)
        self._toolbar.hide()
        self._stack.setCurrentIndex(1)
        self._fullscreen.setFocus()

    @Slot()
    def _exit_fullscreen(self):
        self._toolbar.show()
        self._stack.setCurrentIndex(0)
        self._fullscreen_nav_photos = list(self._filtered_photos)
        self._detail_panel._switch_view(False)
        self.setFocus()

    @Slot()
    def _fullscreen_prev(self):
        if not self._fullscreen_nav_photos:
            return
        current_key = _photo_identity(getattr(self._fullscreen, "_current_photo", {}) or {})
        nav_keys = [_photo_identity(p) for p in self._fullscreen_nav_photos]
        try:
            idx = nav_keys.index(current_key)
        except ValueError:
            idx = -1
        new_idx = idx - 1
        if 0 <= new_idx < len(self._fullscreen_nav_photos):
            self._show_fullscreen_photo(self._fullscreen_nav_photos[new_idx], nav_photos=self._fullscreen_nav_photos)

    @Slot()
    def _fullscreen_next(self):
        if not self._fullscreen_nav_photos:
            return
        current_key = _photo_identity(getattr(self._fullscreen, "_current_photo", {}) or {})
        nav_keys = [_photo_identity(p) for p in self._fullscreen_nav_photos]
        try:
            idx = nav_keys.index(current_key)
        except ValueError:
            idx = -1
        new_idx = idx + 1
        if 0 <= new_idx < len(self._fullscreen_nav_photos):
            self._show_fullscreen_photo(self._fullscreen_nav_photos[new_idx], nav_photos=self._fullscreen_nav_photos)

    @Slot(object, int)
    def _on_rating_changed(self, photo_or_filename, new_rating: int):
        """详情面板评分修改：写入 DB + 刷新缩略图角标 + 异步写 EXIF。"""
        current_photo = _coerce_photo(
            photo_or_filename,
            self._filtered_photos,
            getattr(self._detail_panel, "_current_photo", None),
        ) or {}
        filename = current_photo.get("filename") or (photo_or_filename if isinstance(photo_or_filename, str) else "")
        db_key = _photo_db_key(current_photo) if current_photo else filename
        if self._db:
            self._db.update_photo(db_key, {"rating": new_rating})
        for p in self._filtered_photos:
            if _photo_identity(p) == _photo_identity(current_photo) or (
                not current_photo and p.get("filename") == filename
            ):
                p["rating"] = new_rating
                break
        self._thumb_grid.refresh_photo(current_photo or filename, new_rating)
        # 异步写 EXIF（遵守 metadata_write_mode 设置，mode=none 时内部自动跳过）
        file_path = self._get_photo_file_path(current_photo or filename)
        if file_path:
            import threading
            from tools.exiftool_manager import get_exiftool_manager
            threading.Thread(
                target=get_exiftool_manager().set_rating_and_pick,
                args=(file_path, new_rating),
                daemon=True,
            ).start()

    def _get_photo_file_path(self, photo_or_filename) -> "str | None":
        """根据 photo 或 filename 查找照片绝对路径。"""
        photo = _coerce_photo(photo_or_filename, self._filtered_photos)
        if photo:
            path = photo.get("current_path") or photo.get("original_path") or ""
            return path if path and os.path.exists(path) else None
        return None

    @Slot(list)
    def _on_multi_selection_changed(self, photos: list):
        """C3：多选状态变化，更新工具栏显示。"""
        n = len(photos)
        if n > 1:
            self._select_count_label.setText(self.i18n.t("browser.selected_count").format(n=n))
            self._select_count_label.show()
        else:
            self._select_count_label.hide()
        # C5：仅当选中 2 张时显示对比按钮
        self._compare_btn.setVisible(n == 2)

    def _show_context_menu(self, photo: dict, pos):
        """C4: context menu."""
        base_dir = photo.get('_base_dir', self._directory)
        _show_context_menu_impl(self, photo, pos, base_dir)

    @Slot(dict)
    def _on_delete_photo(self, photo: dict):
        """全屏模式删除图片：确认 → 回收站 → DB 删除 → 缩略图同步 → 跳下一张。"""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        filename = photo.get("filename", "")
        if not filename:
            return

        # 1. 确认弹窗
    @Slot(dict, object)
    def _on_fullscreen_context_menu(self, photo: dict, global_pos):
        """全屏大图右键菜单。"""
        _show_context_menu_impl(self, photo, global_pos, self._directory)

        if cfg.delete_confirm:
            from PySide6.QtWidgets import QCheckBox
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(self.i18n.t("browser.delete_title"))
            msg_box.setText(self.i18n.t("browser.delete_msg").format(filename=filename))
            msg_box.setIcon(QMessageBox.Warning)
            yes_btn = msg_box.addButton(self.i18n.t("browser.delete_confirm_btn"), QMessageBox.AcceptRole)
            msg_box.addButton(self.i18n.t("browser.delete_cancel_btn"), QMessageBox.RejectRole)
            cb = QCheckBox(self.i18n.t("browser.delete_no_ask"))
            msg_box.setCheckBox(cb)
            msg_box.exec()
            if msg_box.clickedButton() != yes_btn:
                return
            if cb.isChecked():
                cfg.set_delete_confirm(False)
                cfg.save()

        # 2. 移入回收站
        filepath = photo.get("current_path") or photo.get("original_path") or ""
        if filepath and not _move_to_trash(filepath):
            QMessageBox.warning(
                self,
                self.i18n.t("browser.delete_failed"),
                self.i18n.t("browser.delete_failed_msg").format(error=filepath)
            )
            return

        # 3. DB 删除
        if self._db:
            self._db.delete_photo(_photo_db_key(photo))

        # 4. 从内存列表移除
        target_identity = _photo_identity(photo)
        self._filtered_photos = [p for p in self._filtered_photos if _photo_identity(p) != target_identity]
        self._all_photos = [p for p in self._all_photos if _photo_identity(p) != target_identity]

        # 5. 缩略图同步
        self._thumb_grid.remove_photo(photo)
        self._fullscreen.set_photo_list(self._filtered_photos)

        # 6. 跳转逻辑
        if self._filtered_photos:
            nxt = self._thumb_grid.select_next()
            if nxt is None:
                nxt = self._thumb_grid.select_prev()
            if nxt:
                self._fullscreen.show_photo(nxt)
                self._detail_panel.show_photo(nxt)
            else:
                self._exit_fullscreen()
        else:
            self._exit_fullscreen()

        # 7. 更新状态栏
        self._update_status(len(self._all_photos), len(self._filtered_photos))

    def _enter_comparison(self):
        """C5：进入 2-up 对比视图。"""
        photos = self._thumb_grid.get_multi_selected_photos()
        if len(photos) >= 2:
            self._comparison.show_pair(photos[0], photos[1])
            self._toolbar.hide()
            self._detail_panel.hide()   # 对比模式不显示详情面板
            self._stack.setCurrentIndex(2)
            self._comparison.setFocus()

    def _exit_comparison(self):
        """C5：退出对比视图，回到 grid。"""
        self._toolbar.show()
        self._detail_panel.show()
        self._stack.setCurrentIndex(0)
        self.setFocus()

    @Slot(int)
    def _on_size_changed(self, value: int):
        self._thumb_grid.set_thumb_size(value)

    def _update_status(self, total: int, filtered: int):
        t = self.i18n.t("browser.total_photos").format(total=total)
        f = self.i18n.t("browser.filtered_photos").format(count=filtered)
        self._status_label.setText(f"{t}  |  {f}")

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        in_fullscreen = (self._stack.currentIndex() == 1)

        if key in (Qt.Key_Left, Qt.Key_Up):
            if in_fullscreen:
                self._fullscreen_prev()
            else:
                self._prev_photo()
        elif key in (Qt.Key_Right, Qt.Key_Down):
            if in_fullscreen:
                self._fullscreen_next()
            else:
                self._next_photo()
        elif key == Qt.Key_Tab:
            self._detail_panel.setVisible(not self._detail_panel.isVisible())
        elif key == Qt.Key_Plus or key == Qt.Key_Equal:
            self._size_slider.setValue(min(300, self._size_slider.value() + 20))
        elif key == Qt.Key_Minus:
            self._size_slider.setValue(max(80, self._size_slider.value() - 20))
        elif key == Qt.Key_Escape:
            current_page = self._stack.currentIndex()
            if current_page == 1:
                self._exit_fullscreen()
            elif current_page == 2:
                self._exit_comparison()
            else:
                # grid 模式：有多选时先清选，否则返回主界面
                if self._thumb_grid.get_multi_selected_photos():
                    self._thumb_grid.clear_multi_select()
                else:
                    self.back_requested.emit()
        elif key == Qt.Key_C:
            if not in_fullscreen and self._stack.currentIndex() == 0:
                photos = self._thumb_grid.get_multi_selected_photos()
                if len(photos) >= 2:
                    self._enter_comparison()
        elif key == Qt.Key_F:
            if in_fullscreen:
                self._fullscreen.toggle_focus()
            else:
                self._detail_panel._switch_view(not self._detail_panel._use_crop_view)
        else:
            super().keyPressEvent(event)
