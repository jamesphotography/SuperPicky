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
    QStackedWidget, QMenu, QInputDialog, QLineEdit, QProgressDialog
)
from PySide6.QtCore import Qt, Signal, Slot, QProcess, QSize, QTimer
from PySide6.QtGui import QAction, QKeyEvent, QIcon

from ui.icon_utils import load_tinted_icon, ICON_IDLE

from ui.styles import COLORS, GLOBAL_STYLE, FONTS
from ui.filter_panel import FilterPanel
from ui.thumbnail_grid import ThumbnailGrid
from ui.detail_panel import DetailPanel
from ui.fullscreen_viewer import FullscreenViewer
from ui.comparison_viewer import ComparisonViewer
from typing import Optional

from tools.i18n import get_i18n
from tools.report_db import ReportDB
from constants import APP_VERSION


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


# 键盘打星键集(数字 0-3 + 上下箭头) / keys handled by keyboard rating
_RATING_KEYS = (Qt.Key_0, Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_Up, Qt.Key_Down)


def _rating_key_action(key: int, current_rating) -> Optional[int]:
    """
    键盘打星决策(Paul 反馈 P0-3):数字键 0-3 直接设星;Up/Down 星级 ±1,
    钳制 0-3——-1★(无鸟)可经 Up(→0)或数字键救回,Down 减到 0 为止。

    Decide the new star rating for a key press: digits 0-3 set directly;
    Up/Down step by one within 0-3 (-1 recovers via Up→0 or digits; Down
    never goes below 0).

    参数 / Parameters:
        key (int): Qt 键码 / Qt key code.
        current_rating: 当前星级(可能为 None/-1..3) / current rating.

    返回 / Returns:
        Optional[int]: 新星级;None 表示与打星无关或星级无变化。
    """
    digit_map = {Qt.Key_0: 0, Qt.Key_1: 1, Qt.Key_2: 2, Qt.Key_3: 3}
    try:
        cur = int(current_rating) if current_rating is not None else 0
    except (TypeError, ValueError):
        cur = 0
    if key in digit_map:
        new = digit_map[key]
    elif key == Qt.Key_Up:
        new = 0 if cur < 0 else min(3, cur + 1)
    elif key == Qt.Key_Down:
        if cur <= 0:
            return None
        new = cur - 1
    else:
        return None
    return new if new != cur else None


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


def _burst_sharpness(photo: dict) -> float:
    """
    连拍代表照片选取用的锐度值：优先 adj_sharpness，回退 head_sharp，缺失视为最低。
    与 report.db 排序 COALESCE(adj_sharpness, head_sharp, -1e99) 保持一致。

    Sharpness used to pick a burst's cover photo: prefer adj_sharpness, fall back to
    head_sharp, treat missing as lowest — mirrors the DB's sharpness_desc ordering.
    """
    v = photo.get("adj_sharpness")
    if v is None:
        v = photo.get("head_sharp")
    return float(v) if v is not None else float("-inf")


from core.burst_ranking import burst_composite_key

def _burst_representative(photos: list) -> dict:
    """
    组内「最佳」代表:分层排序(对焦仲裁档 → 层内眼清为主+头锐为辅)。
    折叠封面与展开标红均取此结果。

    Pick the burst representative via tiered ranking (focus tier, then
    eye-led + head sharpness). Used for the collapsed cover and highlight.
    """
    return max(photos, key=burst_composite_key)

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


def _photo_bird_name(photo: dict, i18n) -> str:
    """
    按界面语言从 photo dict 取鸟种名，供 compute_target_folder 使用。
    Return the species name in the UI language for folder-name computation.
    """
    use_en = i18n.current_lang.startswith("en")
    key = "bird_species_en" if use_en else "bird_species_cn"
    return (photo.get(key) or "").strip()


def _trigger_rating_move(
    dir_path: str,
    photo: dict,
    new_rating: int,
    i18n,
    report_db,
    db_key,
) -> None:
    """
    在后台线程中执行因改星等引发的文件移动。
    Spawn a daemon thread to move files after a rating change.
    """
    from advanced_config import get_advanced_config
    from core.rating_mover import move_photo_on_metadata_change

    bird_name = _photo_bird_name(photo, i18n)
    layout = get_advanced_config().folder_layout

    def _do() -> None:
        try:
            move_photo_on_metadata_change(
                dir_path, photo, new_rating, bird_name, layout, report_db, db_key
            )
        except Exception as e:
            from tools.utils import log_message
            log_message(f"[rating_mover] move failed: {e}")

    import threading
    threading.Thread(target=_do, daemon=True).start()


def _photos_of_same_species(photos: list, target: dict) -> list:
    """
    从照片池里挑出与 target 同一鸟种的全部照片（用于整种合并）。

    匹配以中文鸟名优先；target 没有中文名时（英文环境处理的批次）退回英文名。
    没有鸟名的记录永远不匹配——空名不是一个「鸟种」。

    参数 / Args:
        photos: 照片字典列表（通常是浏览器的 _all_photos）
        target: 用户右键点中的那张照片

    返回 / Returns:
        同鸟种的照片列表，保持原顺序

    Pick every photo sharing the target's species. Chinese name takes
    priority; falls back to English when the target has no Chinese name.
    Records without a species never match.
    """
    target_cn = (target.get("bird_species_cn") or "").strip()
    target_en = (target.get("bird_species_en") or "").strip()

    if target_cn:
        key, expected = "bird_species_cn", target_cn
    elif target_en:
        key, expected = "bird_species_en", target_en
    else:
        return []

    return [p for p in photos if (p.get(key) or "").strip() == expected]


def _merge_reason_text(reason: str) -> str:
    """
    把 core 回传的稳定原因代码翻成本地化文案。

    core 只产出代码（target_exists / move_error:XxxError），中文/英文措辞留在
    UI 层，避免把界面文案写进算法模块。

    参数 / Args:
        reason: 原因代码

    返回 / Returns:
        本地化的失败说明

    Localize the stable reason codes produced by core.rating_mover.
    """
    i18n = get_i18n()
    if reason.startswith("move_error:"):
        return i18n.t('browser.merge_reason_move_error').format(
            detail=reason.split(":", 1)[1]
        )
    if reason == "target_exists":
        return i18n.t('browser.merge_reason_target_exists')
    return reason


def _merge_target_folders(photos: list, new_bird_name: str, layout: str) -> list:
    """
    计算整种合并后会用到的目标目录（相对根目录），用于确认弹窗提前展示。

    目录名跟随当前界面语言，所以这里把真实路径摆给用户看，可以在动手之前
    发现「英文界面整理的批次、现在用中文界面合并会生成中文目录」这类分叉。
    还在根目录（未整理）的照片不会被移动，不计入预览。

    参数 / Args:
        photos:        待合并的照片列表（current_path 需为绝对路径，且含 _base_dir）
        new_bird_name: 用于目录命名的新鸟名
        layout:        "species-first" | "rating-first"

    返回 / Returns:
        去重并排序后的相对目录列表

    Compute the target folders a merge would use, for the confirmation dialog.
    Root-level (unorganized) photos are excluded — they never move.
    """
    from core.folder_layout import compute_target_folder, normalize_layout
    from core.rating_mover import _is_in_root

    layout = normalize_layout(layout)
    folders: set = set()

    for photo in photos:
        current_abs = photo.get("current_path") or photo.get("original_path") or ""
        base_dir = photo.get("_base_dir") or ""
        if not current_abs or not base_dir:
            continue
        try:
            current_rel = os.path.relpath(current_abs, base_dir)
        except ValueError:
            continue                      # Windows 跨盘符
        if _is_in_root(current_rel):
            continue                      # 未整理的照片不移动
        folders.add(
            compute_target_folder(photo.get("rating") or 0, new_bird_name or None, layout)
        )

    return sorted(folders)


def _trigger_species_change(
    dir_path: str,
    photo: dict,
    new_bird_cn: str,
    new_bird_en: str,
    report_db,
    db_key,
) -> None:
    """
    在后台线程中执行因改鸟种引发的 DB 更新与文件移动。
    连拍组整组处理；普通照片仅移动当前文件。

    Spawn a daemon thread to update species in DB and move files after a species change.
    Burst groups are handled as a whole; single photos move individually.
    """
    from advanced_config import get_advanced_config
    from core.rating_mover import change_bird_species

    layout = get_advanced_config().folder_layout

    def _do() -> None:
        try:
            change_bird_species(
                dir_path, photo, new_bird_cn, new_bird_en, layout, report_db, db_key
            )
        except Exception as e:
            from tools.utils import log_message
            log_message(f"[rating_mover] species change failed: {e}")

    import threading
    threading.Thread(target=_do, daemon=True).start()


# ============================================================
#  纠错样本提交（Correction Submission）
#  说明：本文件内 ResultsBrowserWindow / ResultsBrowserWidget 是两个并行的
#  三栏浏览器实现（QMainWindow 版 + 可嵌入的 QWidget 版），改鸟种/提交纠错的
#  业务逻辑完全一致。参照上面 _trigger_species_change 的既有写法——把共享
#  逻辑放到模块级函数，两个类各自的方法只做薄封装——避免同一段逻辑在两个
#  类里各写一份、后续各自漂移。
#
#  Note: this file has two parallel three-pane browser implementations
#  (a QMainWindow version and an embeddable QWidget version) with identical
#  species-edit / submit-corrections logic. Following the existing pattern
#  of _trigger_species_change above, the shared logic lives in module-level
#  functions; each class's method is a thin wrapper. This avoids duplicating
#  the same block in both classes and letting them drift apart.
# ============================================================


def build_correction_payload(photo: dict, new_cn: str, new_en: str,
                              new_latin: str) -> dict:
    """
    从 photo 现值(原预测) + 新选鸟种拼 CorrectionTracker.record_correction 入参。

    **必须在把新鸟种覆盖进 photo/DB 之前调用**，否则原预测(wrong_*)已丢。

    Build the payload for CorrectionTracker.record_correction from the
    current (pre-overwrite) photo values plus the newly chosen species.

    **MUST be called before the new species is written into photo/DB**,
    otherwise the original prediction (wrong_*) is already lost.

    参数 / Args:
        photo: 当前照片字典（覆盖前）。
        new_cn / new_en / new_latin: 用户新选中文名/英文名/学名。

    返回 / Returns:
        dict: 供 CorrectionTracker.record_correction(**payload 派生参数) 使用的字段。
    """
    return {
        "filename": photo.get("filename"),
        "wrong_cn": photo.get("bird_species_cn"),
        "wrong_en": photo.get("bird_species_en"),
        "corrected_cn": new_cn,
        "corrected_en": new_en,
        "corrected_latin": new_latin,
        "birdid_confidence": photo.get("birdid_confidence"),
    }


def _record_species_correction(window, photo: dict, new_cn: str, new_en: str,
                                new_latin: str) -> None:
    """
    改鸟种确认后记录纠错样本（供「提交本次纠错」使用）。

    调用方必须在覆盖 photo["bird_species_cn"/"bird_species_en"] 之前调用本
    函数，否则原预测已丢失（build_correction_payload 依赖 photo 现值）。
    纠错记录失败不得阻断改鸟种主流程，因此内部吞掉异常仅打印警告。

    Record a correction sample after a species edit is confirmed (used later
    by "submit corrections"). Callers MUST invoke this BEFORE overwriting
    photo["bird_species_cn"/"bird_species_en"], otherwise the original
    prediction is already lost (build_correction_payload reads photo as-is).
    A recording failure must never block the species-change flow, so
    exceptions are swallowed here (with a printed warning).

    参数 / Args:
        window: 持有 _db / _correction_tracker 属性的窗口或部件实例
                （ResultsBrowserWindow 或 ResultsBrowserWidget）。
        photo: 当前照片字典（覆盖前，含原预测）。
        new_cn / new_en / new_latin: 用户新选中文名/英文名/学名。
    """
    if not getattr(window, "_db", None):
        return
    try:
        from core.correction_tracker import CorrectionTracker
        from birdid.bird_database_manager import BirdDatabaseManager
        if getattr(window, "_correction_tracker", None) is None:
            window._correction_tracker = CorrectionTracker(
                window._db, BirdDatabaseManager()
            )
        payload = build_correction_payload(photo, new_cn, new_en, new_latin)
        window._correction_tracker.record_correction(
            filename=payload["filename"],
            wrong_cn=payload["wrong_cn"], wrong_en=payload["wrong_en"],
            corrected_cn=payload["corrected_cn"],
            corrected_en=payload["corrected_en"],
            corrected_latin=payload["corrected_latin"],
            birdid_confidence=payload["birdid_confidence"],
        )
    except Exception as e:
        print(f"⚠️ [Correction] 记录纠错失败(不阻断改鸟种): {e}")


def _open_submission_review(window) -> None:
    """
    「提交本次纠错」核心逻辑：读 corrections 表→按鸟种分组→（首次）征询
    自愿说明→弹复审窗打包到桌面。ResultsBrowserWindow / ResultsBrowserWidget
    共用本函数。

    Core "submit corrections" logic: read the corrections table, group by
    species, ask for one-time voluntary consent, then open the review
    dialog to pack results to the desktop. Shared by both
    ResultsBrowserWindow and ResultsBrowserWidget.

    参数 / Args:
        window: 持有 _db / _correction_tracker / i18n 属性的窗口或部件实例，
                同时作为弹窗 parent。
    """
    from advanced_config import get_advanced_config
    from ui.submission_review_dialog import SubmissionReviewDialog, SpeciesGroup

    if not window._db:
        return
    if not hasattr(window._db, "get_corrections"):
        # 合并多目录模式下的 MergedReportDB 暂不支持纠错查询，优雅退回而非崩溃。
        # MergedReportDB (merged multi-directory mode) doesn't support
        # correction queries yet; bail out gracefully instead of crashing.
        QMessageBox.information(
            window, window.i18n.t("submission.title"),
            window.i18n.t("submission.no_corrections"))
        return

    corrections = window._db.get_corrections()
    if not corrections:
        QMessageBox.information(
            window, window.i18n.t("submission.title"),
            window.i18n.t("submission.no_corrections"))
        return

    # 首次自愿说明
    # One-time voluntary consent prompt.
    cfg = get_advanced_config()
    if not cfg.correction_consent_shown:
        ans = QMessageBox.question(
            window, window.i18n.t("submission.consent_title"),
            window.i18n.t("submission.consent_body"),
            QMessageBox.Yes | QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        cfg.set_correction_consent_shown(True)

    # 按鸟种分组：每条 correction = 一个被改正图 + 同鸟种正样本
    # Group by species: each correction row = one corrected photo + positives of the same species.
    groups = []
    for c in corrections:
        failed = window._db.get_photo(c["filename"]) or {"filename": c["filename"]}
        positives = window._correction_tracker.find_positive_samples(
            c["corrected_cn"], c["corrected_en"], exclude_filename=c["filename"]
        ) if getattr(window, "_correction_tracker", None) else \
            window._db.get_photos_by_species(
                cn=c["corrected_cn"], en=c["corrected_en"],
                exclude_filename=c["filename"])
        # DB 里 current_path/original_path 存的是相对 dir_path 的相对路径，
        # 必须用 _resolve_photo_paths 转成绝对路径，否则 load_image 找不到文件。
        # current_path/original_path in the DB are stored relative to dir_path;
        # must resolve to absolute paths via _resolve_photo_paths, otherwise
        # load_image cannot locate the file.
        failed = window._resolve_photo_paths(failed)
        positives = [window._resolve_photo_paths(p) for p in positives]
        groups.append(SpeciesGroup(
            corrected_cn=c["corrected_cn"] or "",
            corrected_en=c["corrected_en"] or "",
            model_class_id=c["corrected_model_class_id"],
            failed=failed, positives=positives,
        ))

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out_dir = desktop if os.path.isdir(desktop) else os.path.expanduser("~")
    dlg = SubmissionReviewDialog(groups, out_dir, APP_VERSION, parent=window)
    dlg.exec()


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


def _build_context_menu(parent_widget, photo: dict, directory: str):
    """
    构建右键菜单(C4)并返回 QMenu,不负责弹出——便于单测各菜单项接线。
    外部应用列表从 advanced_config 读取。弹出由 _show_context_menu_impl 负责。

    Build and return the right-click QMenu (without showing it), so each menu
    item's wiring can be unit-tested. Display is handled separately.
    """
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

    # 修改鸟种 / 补录(取代原网格卡片上的常驻铅笔;对所有照片可用):
    # 有鸟名=纠错,无鸟名=人工补录,复用浏览器既有编辑弹窗与目录移动逻辑。
    # Edit/assign species (replaces the tile's always-on pencil; available
    # for every photo). Reuses the browser's existing edit dialog.
    species_action = QAction(_i18n.t('browser.ctx_edit_species'), parent_widget)

    def _edit_species(_checked=False, _p=photo):
        handler = getattr(parent_widget, "_on_species_edit_requested", None)
        if callable(handler):
            handler(_p)

    species_action.triggered.connect(_edit_species)
    menu.addAction(species_action)

    # 整种合并:把这张照片所属鸟种的全部照片一次性改为另一个鸟种。
    # 用于「整批都被认成同一个错误鸟种」的场景,避免逐张点「修改鸟种」。
    # 没有鸟名的照片无种可合并,不显示该项。
    # Whole-species merge: retag every photo of this species at once.
    # Hidden for photos without a species (nothing to merge).
    merge_species_name = (
        photo.get("bird_species_en") if _i18n.current_lang.startswith("en")
        else photo.get("bird_species_cn")
    ) or ""
    if merge_species_name:
        merge_action = QAction(
            _i18n.t('browser.ctx_merge_species').format(species=merge_species_name),
            parent_widget,
        )

        def _merge_species(_checked=False, _p=photo):
            handler = getattr(parent_widget, "_on_merge_species_requested", None)
            if callable(handler):
                handler(_p)

        merge_action.triggered.connect(_merge_species)
        menu.addAction(merge_action)

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

    return menu


def _show_context_menu_impl(parent_widget, photo: dict, pos, directory: str):
    """构建并在 pos 处弹出右键菜单(C4)。"""
    menu = _build_context_menu(parent_widget, photo, directory)
    menu.exec(pos)


def _move_to_trash(filepath: str) -> bool:
    """将文件移入系统回收站（跨平台）。返回是否成功。"""
    if not filepath:
        return False
    filepath = os.path.normpath(filepath)  # 消除 ./ 等冗余路径符，防止 Finder 误删父目录
    if not os.path.isfile(filepath):       # 只允许删除文件，禁止误删目录
        return False
    try:
        if sys.platform == "darwin":
            # macOS: osascript 调用 Finder 移入回收站。
            # 路径经 argv 传入，脚本本身是静态常量——绝不可把路径插值进脚本源。
            # 旧写法只转义双引号(filepath.replace('"', '\\"'))，反斜杠未转义，
            # 文件名中的 \" 组合会提前闭合 AppleScript 字符串，使其余部分作为
            # 代码执行(已实测可执行任意表达式)。macOS 文件名允许 \ 和 "，
            # 因此一个特制文件名即可在删除操作中触发任意代码执行。
            # Pass the path through argv against a static script — never
            # interpolate it into AppleScript source. The previous code escaped
            # only double quotes, leaving backslashes untouched, so a \" pair in
            # a filename closed the string early and executed the remainder as
            # code. macOS permits both \ and " in filenames, so a crafted name
            # meant arbitrary code execution during a delete.
            # POSIX file 的转换必须放在 tell 块之外：在 tell application "Finder"
            # 内部，POSIX file 会被 Finder 的术语解释，作用于变量时报 -1728。
            # The POSIX file coercion must happen outside the tell block: inside
            # tell application "Finder" it is resolved against Finder's own
            # terminology and fails with -1728 when applied to a variable.
            script = (
                "on run argv\n"
                "    set targetFile to POSIX file (item 1 of argv) as alias\n"
                '    tell application "Finder" to delete targetFile\n'
                "end run"
            )
            result = subprocess.run(
                ["osascript", "-e", script, "--", filepath],
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
        self._correction_tracker = None  # 惰性创建的纠错记录器 / lazily-created CorrectionTracker
        self._directory: str = ""
        self._all_photos: list = []
        self._filtered_photos: list = []
        self._raw_filtered_photos: list = [] # V5: Store unfiltered sorted photos
        self._expanded_bursts: set = set()   # V5: Track expanded burst IDs
        self._is_merged: bool = False
        self._sub_dirs: list = []
        self._fullscreen_nav_photos: list = []
        self._apple_photos_importer = None
        self._apple_photos_progress = None

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
        # issue #106: 网格鸟种编辑改由右键菜单进入(见 _show_context_menu_impl)
        # issue #106: grid species-edit now lives in the right-click menu
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
        self._fullscreen.species_edit_requested.connect(self._on_species_edit_requested)
        self._fullscreen.crop_advice_requested.connect(self._on_crop_advice_requested)
        self._fullscreen.auto_retouch_requested.connect(
            lambda p: self._open_studio_with_action(p, "enhance"))
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
        self._detail_panel.species_edit_requested.connect(self._on_species_edit_requested)
        self._detail_panel.crop_advice_requested.connect(self._on_crop_advice_requested)
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
        back_btn = QPushButton("  " + self.i18n.t("browser.back"))
        back_btn.setIcon(load_tinted_icon("birdhouse.svg", ICON_IDLE, 16))
        back_btn.setIconSize(QSize(16, 16))
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

        # Apple Photos 导入严格限于 macOS；模块只在用户触发时惰性加载，Windows
        # 启动和打包运行路径不导入任何 AppleScript 控制代码。
        # Apple Photos import is macOS-only. Its controller is loaded lazily on
        # user action so Windows startup never imports AppleScript control code.
        self._apple_photos_btn = None
        if sys.platform == "darwin":
            self._apple_photos_btn = QPushButton(self.i18n.t("browser.photos_import_btn"))
            self._apple_photos_btn.setIcon(load_tinted_icon("image-plus.svg", ICON_IDLE, 16))
            self._apple_photos_btn.setIconSize(QSize(16, 16))
            self._apple_photos_btn.setObjectName("secondary")
            self._apple_photos_btn.setFixedHeight(32)
            self._apple_photos_btn.setToolTip(self.i18n.t("browser.photos_import_tooltip"))
            self._apple_photos_btn.setEnabled(False)
            self._apple_photos_btn.clicked.connect(self._start_apple_photos_import)
            layout.addWidget(self._apple_photos_btn)

        # 缩略图尺寸:标签 + 滑块绑成一组,紧贴显示
        size_box = QHBoxLayout()
        size_box.setContentsMargins(0, 0, 0, 0)
        size_box.setSpacing(6)
        size_label = QLabel(self.i18n.t("browser.size_label"))
        size_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 10px; background: transparent;")
        size_box.addWidget(size_label)

        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(80, 300)
        self._size_slider.setValue(160)
        self._size_slider.setFixedWidth(100)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        size_box.addWidget(self._size_slider)
        layout.addLayout(size_box)

        # ExtremeSimple: 「提交本次纠错」按钮已从工具栏剥离（_on_submit_corrections/
        # _open_submission_review/CorrectionTracker 等纠错提交代码本身保留不动；
        # 鸟种手动编辑能力不受影响，record_correction_if_species_changed 仍会
        # 静默记录本地纠错样本，只是没有入口能打包提交。未来要恢复只需把这段
        # 按钮创建代码加回来）。
        # ExtremeSimple: the "Submit corrections" button is stripped from the
        # toolbar (_on_submit_corrections / _open_submission_review /
        # CorrectionTracker etc. are untouched). Manual species editing is
        # unaffected; record_correction_if_species_changed keeps silently
        # logging local correction samples, there's just no entry point to
        # package and submit them. Re-add this button-creation block to bring
        # it back.

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

        # 动态刷新鸟种下拉：只显示当前星级筛选下有照片的鸟种
        use_en = self.i18n.current_lang.startswith('en')
        species = self._db.get_distinct_species(use_en=use_en, ratings=filters.get('ratings'))
        self._filter_panel.update_species_list(species)

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
            best_photo = _burst_representative(photos)  # 折叠封面=组内锐度最高的一张
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
                        expanded_photo["is_burst_best"] = (_photo_identity(bp) == best_burst_photos[bid])
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
            self._thumb_grid.select_photo(selected_photo, ensure_visible=False)
            self._detail_panel.show_photo(selected_photo)
        else:
            self._detail_panel.clear()
        self._update_apple_photos_button()

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
        if self._stack.currentIndex() == 1:
            # 全屏导航中:详情面板被盖住不可见,只同步元数据不解码大图
            # (每次方向键少解码一整张;退出全屏时 _switch_view 刷新一次)。
            # During fullscreen navigation the panel is hidden — sync
            # metadata only; the image reloads once on fullscreen exit.
            self._detail_panel.set_current_photo(photo)
        else:
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
            if self._stack.currentIndex() == 1:   # 全屏模式:同步大图,面板只同步元数据
                self._fullscreen.show_photo(photo)
                self._detail_panel.set_current_photo(photo)
            else:
                self._detail_panel.show_photo(photo)

    @Slot()
    def _next_photo(self):
        photo = self._thumb_grid.select_next()
        if photo:
            if self._stack.currentIndex() == 1:   # 全屏模式:同步大图,面板只同步元数据
                self._fullscreen.show_photo(photo)
                self._detail_panel.set_current_photo(photo)
            else:
                self._detail_panel.show_photo(photo)

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
        """详情面板评分修改：写入 DB + 刷新缩略图角标 + 异步写 EXIF + 后台移动文件。"""
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
        # 后台移动文件（仅已整理的照片；burst / 根目录 / 新旧相同 时内部自动跳过）
        if current_photo:
            base_dir = current_photo.get("_base_dir") or self._directory
            _trigger_rating_move(base_dir, current_photo, new_rating, self.i18n, self._db, db_key)

    def _get_photo_file_path(self, photo_or_filename) -> "str | None":
        """根据 photo 或 filename 查找照片绝对路径。"""
        photo = _coerce_photo(photo_or_filename, self._filtered_photos)
        if photo:
            path = photo.get("current_path") or photo.get("original_path") or ""
            return path if path and os.path.exists(path) else None
        return None

    def _on_species_edit_requested(self, photo: dict):
        """
        用户点击铅笔图标 → 弹出鸟种搜索对话框，确认后后台更新 DB + 移动文件。
        User clicks pencil icon → open bird species search dialog; on confirm, update DB and move files in background.
        """
        from ui.bird_species_edit_dialog import BirdSpeciesEditDialog
        from PySide6.QtWidgets import QDialog

        dialog = BirdSpeciesEditDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        new_cn = dialog.selected_cn
        new_en = dialog.selected_en
        if not new_cn and not new_en:
            return

        db_key = _photo_db_key(photo)
        base_dir = photo.get("_base_dir") or self._directory

        # 纠错记录：必须在覆盖 bird_species 字段之前抓原预测。
        # Record correction BEFORE overwriting species fields (original prediction).
        self._record_correction(photo, new_cn, new_en, dialog.selected_latin)

        # 1. 同步更新 photo 副本 + 缓存列表
        # Update both the local photo copy and the cached list so show_photo displays the new name.
        photo["bird_species_cn"] = new_cn
        photo["bird_species_en"] = new_en
        for p in self._filtered_photos:
            if _photo_identity(p) == _photo_identity(photo):
                p["bird_species_cn"] = new_cn
                p["bird_species_en"] = new_en
                break

        # 2. 同步写入 DB 鸟种字段（使下拉刷新立即生效；文件移动仍在后台执行）
        # Write species fields to DB synchronously so the dropdown refresh sees new data immediately.
        if self._db:
            self._db.update_photo(db_key, {
                "bird_species_cn": new_cn or None,
                "bird_species_en": new_en or None,
            })

        # 3. 刷新详情面板
        self._detail_panel.show_photo(photo)

        # 4. 刷新左侧鸟种下拉
        use_en = self.i18n.current_lang.startswith("en")
        current_filters = self._filter_panel.get_filters()
        new_species = self._db.get_distinct_species(
            use_en=use_en, ratings=current_filters.get("ratings")
        )
        self._filter_panel.update_species_list(new_species)

        # 5. 后台执行文件移动（同时更新连拍组其他成员的 DB 鸟种字段及 current_path）
        # Background: move files and update burst group members' DB records.
        _trigger_species_change(base_dir, photo, new_cn, new_en, self._db, db_key)

    def _on_merge_species_requested(self, photo: dict):
        """
        整种合并：把这张照片所属鸟种的**全部**照片一次性改为另一个鸟种。

        用于「一整批都被认成同一个错误鸟种」的场景。范围是数据库里该鸟种的
        全部照片，与当前左侧筛选无关——否则会只改掉一部分、剩下的散落在别处。

        流程：收集同种照片 → 选目标鸟种 → 确认（含目标目录预览）→ 带进度执行
        → 结果报告（成功/仅改名/失败清单）。按用户决定，本操作不写 corrections。

        参数 / Args:
            photo: 用户右键点中的照片（提供源鸟种）

        Merge a whole species: retag every photo of this species at once.
        Scope is DB-wide for that species, independent of the current filters.
        """
        from ui.bird_species_edit_dialog import BirdSpeciesEditDialog
        from ui.custom_dialogs import StyledMessageBox
        from PySide6.QtWidgets import QDialog
        from advanced_config import get_advanced_config
        from core.rating_mover import merge_bird_species

        i18n = self.i18n
        use_en = i18n.current_lang.startswith("en")
        old_name = (
            photo.get("bird_species_en") if use_en else photo.get("bird_species_cn")
        ) or ""
        if not old_name or not self._db:
            return

        # 1. 收集全部同鸟种照片（_all_photos 存的是相对路径，必须先解析成绝对路径）
        resolved_pool = [self._resolve_photo_paths(p) for p in self._all_photos]
        targets = _photos_of_same_species(resolved_pool, photo)
        if not targets:
            return

        # 2. 选目标鸟种（复用单张编辑用的搜索弹窗）
        dialog = BirdSpeciesEditDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        new_cn = dialog.selected_cn
        new_en = dialog.selected_en
        if not new_cn and not new_en:
            return
        new_name = (new_en if use_en else new_cn) or ""

        # 3. 确认：把张数、连拍组数和真实目标目录摆出来再动手
        layout = get_advanced_config().folder_layout
        folders = _merge_target_folders(targets, new_name, layout)
        burst_count = len({p.get("burst_id") for p in targets if p.get("burst_id")})
        burst_note = (
            i18n.t('browser.merge_burst_note').format(bursts=burst_count)
            if burst_count else ""
        )
        body = i18n.t('browser.merge_confirm_body').format(
            old=old_name, count=len(targets), burst_note=burst_note,
            new=new_name, folders="\n".join(folders) if folders else "—",
        )
        confirmed = StyledMessageBox.question(
            self, i18n.t('browser.merge_confirm_title'), body
        )
        if confirmed != StyledMessageBox.Yes:
            return

        # 4. 执行：合并库的照片分属不同批次目录，按 _base_dir 分组各调一次
        by_base: dict = {}
        for p in targets:
            by_base.setdefault(p.get("_base_dir") or self._directory, []).append(p)

        progress = QProgressDialog(
            i18n.t('browser.merge_progress_title'), i18n.t('buttons.cancel'),
            0, len(targets), self,
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        state = {"offset": 0}

        def _on_progress(done: int, total: int, name: str) -> bool:
            """进度回调；返回 False 表示用户点了取消。Return False to cancel."""
            progress.setValue(state["offset"] + done)
            progress.setLabelText(
                i18n.t('browser.merge_progress_label').format(name=name)
            )
            QApplication.processEvents()
            return not progress.wasCanceled()

        total_moved = 0
        total_db_only = 0
        all_failed: list = []
        cancelled = False

        for base_dir, group in by_base.items():
            result = merge_bird_species(
                base_dir, group, new_cn, new_en, layout,
                self._db, _photo_db_key, _on_progress,
            )
            total_moved += result["moved"]
            total_db_only += result["db_only"]
            all_failed.extend(result["failed"])
            state["offset"] += len(group)
            if result["cancelled"]:
                cancelled = True
                break

        progress.close()

        # 5. 刷新：重新读库并重放当前筛选（_apply_filters 会顺带刷新鸟种下拉）
        self._all_photos = self._db.get_all_photos()
        self._apply_filters(self._filter_panel.get_filters())

        # 6. 结果报告：不静默，成功和失败都说清楚
        lines: list = []
        if total_moved:
            lines.append(i18n.t('browser.merge_result_moved').format(count=total_moved))
        if total_db_only:
            lines.append(
                i18n.t('browser.merge_result_db_only').format(count=total_db_only)
            )
        if all_failed:
            lines.append(
                i18n.t('browser.merge_result_failed').format(count=len(all_failed))
            )
            for name, reason in all_failed[:20]:
                lines.append(f"  • {name} — {_merge_reason_text(reason)}")
            if len(all_failed) > 20:
                lines.append("  …")
        if cancelled:
            lines.append(i18n.t('browser.merge_result_cancelled'))

        StyledMessageBox.information(
            self, i18n.t('browser.merge_result_title'), "\n".join(lines)
        )

    def _record_correction(self, photo: dict, new_cn: str, new_en: str,
                            new_latin: str) -> None:
        """
        薄封装：调用模块级共享实现 _record_species_correction，
        ResultsBrowserWindow / ResultsBrowserWidget 两处改鸟种入口共用同一份逻辑，避免发散。

        Thin wrapper delegating to the module-level shared implementation
        _record_species_correction, so both species-edit call sites
        (ResultsBrowserWindow / ResultsBrowserWidget) share one logic path.
        """
        _record_species_correction(self, photo, new_cn, new_en, new_latin)

    def _on_submit_corrections(self):
        """
        「提交本次纠错」：读 corrections 表，按鸟种分组，弹复审窗打包到桌面。
        首次使用先弹一次自愿说明并记住同意。

        "Submit corrections": read the corrections table, group by species,
        and open the review dialog to pack results to the desktop. Shows a
        one-time voluntary consent prompt on first use.
        """
        _open_submission_review(self)

    def _open_studio_with_action(self, photo: dict, action: str):
        """
        打开 Crop Studio 后跳到指定功能(大图「手动裁剪」/「自动修图」入口)。
        复用 _on_crop_advice_requested 打开工作区,再在已持有实例上应用初始动作。
        """
        self._on_crop_advice_requested(photo)
        studio = getattr(self, "_crop_studio", None)
        if studio is not None:
            QTimer.singleShot(0, lambda: studio.apply_initial_action(action))

    def _on_crop_advice_requested(self, photo: dict):
        """
        打开全屏「后期工作区」Crop Studio（非破坏性预览 + 裁剪导出）。
        Open the fullscreen Crop Studio (non-destructive preview + crop export).
        """
        # 复用详情面板的显示图解析：优先可解码的 temp JPEG，
        # 避免把 RAW(current_path)喂给工作区——cv2/PIL 解不了 RAW 会报 TIFF 错。
        # Reuse the detail panel's display-path resolution: prefer the decodable
        # temp JPEG, never feed a RAW file (cv2/PIL can't decode it).
        rp = self._resolve_photo_paths(photo)
        path = rp.get("temp_jpeg_path")
        if not path or not os.path.exists(path):
            path = rp.get("debug_crop_path")
        if not path or not os.path.exists(path):
            op = rp.get("original_path") or rp.get("current_path")
            if op and os.path.exists(op) and os.path.splitext(op)[1].lower() in ('.jpg', '.jpeg'):
                path = op
            else:
                path = None
        print(
            f"🪶 [CropStudio] 入口解析: temp_jpeg={rp.get('temp_jpeg_path')!r} "
            f"debug_crop={rp.get('debug_crop_path')!r} current={rp.get('current_path')!r} "
            f"original={rp.get('original_path')!r} → 选用={path!r}"
        )
        if not path or not os.path.exists(path):
            from ui.custom_dialogs import StyledMessageBox
            StyledMessageBox.warning(
                self,
                self.i18n.t("crop_advisor.title"),
                self.i18n.t("crop_advisor.no_decodable_image"),
            )
            return

        # 合并显示字段(鸟种/星级/罕见度/IUCN)与解析出的绝对路径；把可解码图注入
        # temp_jpeg_path 使工作区按同一坐标系分析/导出。
        # Merge display fields with resolved absolute paths; inject the decodable
        # image as temp_jpeg_path so the studio analyzes/exports in one coord space.
        studio_photo = dict(photo)
        for k in ("original_path", "current_path"):
            if rp.get(k):
                studio_photo[k] = rp.get(k)
        studio_photo["temp_jpeg_path"] = path

        from ui.crop_studio import CropStudio
        studio = CropStudio(studio_photo, self.i18n, parent=self)
        studio.closed.connect(lambda: setattr(self, "_crop_studio", None))
        self._crop_studio = studio  # 持有引用,避免被 GC / keep a reference
        studio.showFullScreen()

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

    def _apple_photos_target_photos(self) -> list[dict]:
        """
        返回 Apple Photos 导入目标：明确勾选优先，否则使用当前可见过滤结果。

        折叠连拍组在 ``_filtered_photos`` 中只有封面，因此未展开时只导入用户
        实际看到的代表图；展开后则按每张可见成员导入。只要存在一个或多个
        蓝色勾选项目，就严格按勾选集合导入，不包含双图对比自动补入的锚点。

        Return Apple Photos targets: one or more explicitly checked photos take
        precedence; otherwise use the currently visible filtered list.
        Collapsed bursts contribute only their visible representative. The
        unchecked anchor automatically added for two-photo comparison is never
        included in an import.
        """

        selected = self._thumb_grid.get_explicitly_selected_photos()
        if selected:
            return list(selected)
        return list(self._filtered_photos)

    def _update_apple_photos_button(self) -> None:
        """按结果和任务状态更新 macOS 导入按钮。/ Update import action availability."""

        button = getattr(self, "_apple_photos_btn", None)
        if button is None:
            return
        importer = self._apple_photos_importer
        is_running = bool(importer is not None and importer.is_running)
        button.setEnabled(bool(self._filtered_photos) and not is_running)

    @Slot()
    def _start_apple_photos_import(self) -> None:
        """
        预检目标、确认相册名并异步启动 Apple Photos 导入。

        Preflight targets, confirm the album name, and start a non-blocking
        Apple Photos import.
        """

        if sys.platform != "darwin":
            return

        from ui.apple_photos_importer import (
            ApplePhotosImporter,
            ApplePhotosImportRequest,
            default_album_name,
            preflight_photos,
            sanitize_album_name,
        )

        target_photos = self._apple_photos_target_photos()
        preflight = preflight_photos(
            target_photos,
            prefer_english=self.i18n.current_lang.startswith("en"),
        )
        if not preflight.candidates:
            QMessageBox.warning(
                self,
                self.i18n.t("browser.photos_import_unavailable_title"),
                self.i18n.t("browser.photos_import_no_files").format(
                    count=preflight.requested,
                ),
            )
            return

        suggested_name = default_album_name(self._directory)
        album_name, accepted = QInputDialog.getText(
            self,
            self.i18n.t("browser.photos_import_confirm_title"),
            self.i18n.t("browser.photos_import_confirm_message").format(
                count=len(preflight.candidates),
                skipped=preflight.skipped,
            ),
            QLineEdit.Normal,
            suggested_name,
        )
        if not accepted:
            return
        album_name = sanitize_album_name(album_name)
        if not album_name:
            QMessageBox.warning(
                self,
                self.i18n.t("browser.photos_import_unavailable_title"),
                self.i18n.t("browser.photos_import_empty_album"),
            )
            return

        importer = self._apple_photos_importer
        if importer is None:
            importer = ApplePhotosImporter(self)
            importer.progress.connect(self._on_apple_photos_progress)
            importer.completed.connect(self._on_apple_photos_completed)
            self._apple_photos_importer = importer

        request = ApplePhotosImportRequest(
            album_name=album_name,
            candidates=preflight.candidates,
            requested=preflight.requested,
            preflight_skipped=preflight.skipped,
        )
        progress = QProgressDialog(
            self.i18n.t("browser.photos_import_progress").format(
                completed=0,
                total=len(preflight.candidates),
            ),
            self.i18n.t("browser.photos_import_cancel"),
            0,
            len(preflight.candidates),
            self,
        )
        progress.setWindowTitle(self.i18n.t("browser.photos_import_confirm_title"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(importer.cancel)
        self._apple_photos_progress = progress
        progress.show()

        try:
            importer.start(request)
            self._update_apple_photos_button()
        except (RuntimeError, ValueError) as error:
            progress.close()
            progress.deleteLater()
            self._apple_photos_progress = None
            self._update_apple_photos_button()
            QMessageBox.critical(
                self,
                self.i18n.t("browser.photos_import_failed_title"),
                str(error),
            )

    @Slot(int, int)
    def _on_apple_photos_progress(self, completed: int, total: int) -> None:
        """同步批次进度到进度框。/ Synchronize batch progress to the dialog."""

        progress = self._apple_photos_progress
        if progress is None:
            return
        progress.setMaximum(total)
        progress.setValue(completed)
        progress.setLabelText(
            self.i18n.t("browser.photos_import_progress").format(
                completed=completed,
                total=total,
            )
        )

    @Slot(object)
    def _on_apple_photos_completed(self, result) -> None:
        """关闭进度框并展示成功、取消或错误摘要。/ Present the final import summary."""

        progress = self._apple_photos_progress
        if progress is not None:
            progress.close()
            progress.deleteLater()
            self._apple_photos_progress = None
        self._update_apple_photos_button()

        if result.error:
            from ui.apple_photos_importer import is_automation_permission_error

            message = self.i18n.t("browser.photos_import_failed_message").format(
                imported=result.newly_imported,
                not_imported=result.photos_not_imported,
                indeterminate=result.indeterminate,
                remaining=result.remaining,
                metadata_applied=result.metadata_applied,
                metadata_partial=result.metadata_partially_applied,
                metadata_not_applied=result.metadata_not_applied,
                error=result.error,
            )
            if is_automation_permission_error(result.error):
                message += "\n\n" + self.i18n.t("browser.photos_import_permission_help")
            title = self.i18n.t("browser.photos_import_failed_title")
            icon = QMessageBox.Critical
        elif result.cancelled:
            title = self.i18n.t("browser.photos_import_cancelled_title")
            message = self.i18n.t("browser.photos_import_cancelled_message").format(
                imported=result.newly_imported,
                not_imported=result.photos_not_imported,
                indeterminate=result.indeterminate,
                metadata_applied=result.metadata_applied,
                metadata_partial=result.metadata_partially_applied,
                metadata_not_applied=result.metadata_not_applied,
                remaining=result.remaining,
            )
            icon = QMessageBox.Information
        else:
            title = self.i18n.t("browser.photos_import_done_title")
            message = self.i18n.t("browser.photos_import_done_message").format(
                imported=result.newly_imported,
                not_imported=result.photos_not_imported,
                indeterminate=result.indeterminate,
                remaining=result.remaining,
                metadata_applied=result.metadata_applied,
                metadata_partial=result.metadata_partially_applied,
                metadata_not_applied=result.metadata_not_applied,
                skipped=result.preflight_skipped,
            )
            icon = QMessageBox.Information

        if result.retryable_metadata:
            message += "\n\n" + self.i18n.t(
                "browser.photos_import_retry_metadata_message"
            ).format(count=result.retryable_metadata)
            dialog = QMessageBox(icon, title, message, parent=self)
            retry_button = dialog.addButton(
                self.i18n.t("browser.photos_import_retry_metadata"),
                QMessageBox.AcceptRole,
            )
            dialog.addButton(QMessageBox.Close)
            dialog.exec()
            if dialog.clickedButton() is retry_button:
                self._retry_apple_photos_metadata(result.retryable_metadata)
            return

        QMessageBox(icon, title, message, QMessageBox.Ok, self).exec()

    def _retry_apple_photos_metadata(self, count: int) -> None:
        """
        启动仅元数据重试，不再次导入 RAW。

        Start a metadata-only retry without importing RAW files again.
        """

        importer = self._apple_photos_importer
        if importer is None:
            return
        progress = QProgressDialog(
            self.i18n.t("browser.photos_import_progress").format(
                completed=0,
                total=count,
            ),
            self.i18n.t("browser.photos_import_cancel"),
            0,
            count,
            self,
        )
        progress.setWindowTitle(self.i18n.t("browser.photos_import_confirm_title"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(importer.cancel)
        self._apple_photos_progress = progress
        progress.show()
        try:
            importer.retry_metadata()
            self._update_apple_photos_button()
        except RuntimeError as error:
            progress.close()
            progress.deleteLater()
            self._apple_photos_progress = None
            # 与 _start_apple_photos_import 的失败分支保持一致：任务未能启动时
            # 必须把按钮恢复可用，否则导入入口会一直保持禁用。
            # Mirror the failure path in _start_apple_photos_import: re-enable the
            # action when the task fails to start, or it stays disabled forever.
            self._update_apple_photos_button()
            QMessageBox.critical(
                self,
                self.i18n.t("browser.photos_import_failed_title"),
                str(error),
            )

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
        # 记录被删除照片在过滤列表中的位置，用于删除后正确跳转
        _del_identities = [_photo_identity(p) for p in self._filtered_photos]
        try:
            _deleted_idx = _del_identities.index(_photo_identity(photo))
        except ValueError:
            _deleted_idx = 0
        self._filtered_photos = [p for p in self._filtered_photos if _photo_identity(p) != target_identity]
        self._all_photos = [p for p in self._all_photos if _photo_identity(p) != target_identity]

        # 5. 缩略图同步
        self._thumb_grid.remove_photo(photo)
        self._fullscreen.set_photo_list(self._filtered_photos)
        # 导入按钮的可用性取决于 _filtered_photos 是否为空，删空后必须同步禁用，
        # 否则点击只会弹「没有可导入文件」。
        # The import action keys off _filtered_photos; refresh it after deletion
        # so an emptied list disables the button instead of failing on click.
        self._update_apple_photos_button()

        # 6. 跳转逻辑：跳到被删除位置的下一张，已是末尾则跳上一张
        if self._filtered_photos:
            next_idx = min(_deleted_idx, len(self._filtered_photos) - 1)
            nxt = self._filtered_photos[next_idx]
            self._thumb_grid.select_photo(nxt)
            self._fullscreen.show_photo(nxt)
            self._detail_panel.show_photo(nxt)
        else:
            self._exit_fullscreen()

        # 7. 更新状态栏
        self._update_status(len(self._all_photos), len(self._filtered_photos))

    def _delete_selected_photos(self):
        """Delete currently selected photo(s) in grid view or fullscreen view with Command + Backspace."""
        in_fullscreen = (self._stack.currentIndex() == 1)
        
        # 1. Gather target photos to delete
        if in_fullscreen:
            if not self._fullscreen._current_photo:
                return
            target_photos = [self._fullscreen._current_photo]
        else:
            target_photos = self._thumb_grid.get_multi_selected_photos()
            if not target_photos and self._detail_panel._current_photo:
                target_photos = [self._detail_panel._current_photo]
                
        if not target_photos:
            return

        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        
        # 2. Confirmation Dialog (if enabled in config)
        if cfg.delete_confirm:
            from PySide6.QtWidgets import QCheckBox
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(self.i18n.t("browser.delete_title"))
            msg_box.setIcon(QMessageBox.Warning)
            yes_btn = msg_box.addButton(self.i18n.t("browser.delete_confirm_btn"), QMessageBox.AcceptRole)
            msg_box.addButton(self.i18n.t("browser.delete_cancel_btn"), QMessageBox.RejectRole)
            cb = QCheckBox(self.i18n.t("browser.delete_no_ask"))
            msg_box.setCheckBox(cb)
            
            if len(target_photos) == 1:
                filename = target_photos[0].get("filename", "")
                msg_box.setText(self.i18n.t("browser.delete_msg").format(filename=filename))
            else:
                count = len(target_photos)
                if self.i18n.current_lang.startswith('en'):
                    msg_text = f"Move {count} selected photos to Trash?\n\n❗ This will also delete their database records. You'll need to reprocess after restoring."
                else:
                    msg_text = f"将选中的 {count} 张图片移入回收站？\n\n❗ 此操作会同时从数据库删除记录，恢复文件后需重新处理。"
                msg_box.setText(msg_text)
                
            msg_box.exec()
            if msg_box.clickedButton() != yes_btn:
                return
            if cb.isChecked():
                cfg.set_delete_confirm(False)
                cfg.save()
                
        # 3. Perform Deletion
        deleted_photos = []
        failed_paths = []
        for photo in target_photos:
            filepath = photo.get("current_path") or photo.get("original_path") or ""
            if filepath:
                if _move_to_trash(filepath):
                    deleted_photos.append(photo)
                else:
                    failed_paths.append(filepath)
            else:
                # If no filepath (rare DB fallback), count as deleted from DB at least
                deleted_photos.append(photo)
                
        # If any failed to move to trash, alert the user and stop DB/memory sync for them
        if failed_paths:
            QMessageBox.warning(
                self,
                self.i18n.t("browser.delete_failed"),
                self.i18n.t("browser.delete_failed_msg").format(error="\n".join(failed_paths))
            )
            
        if not deleted_photos:
            return
            
        # 4. DB Deletion
        if self._db:
            for photo in deleted_photos:
                self._db.delete_photo(_photo_db_key(photo))
                
        # 5. Memory & UI Sync
        deleted_identities = {_photo_identity(p) for p in deleted_photos}
        
        # If we are in fullscreen and deleting the current photo, determine the next photo to show
        next_photo = None
        if in_fullscreen and self._fullscreen._current_photo:
            curr_id = _photo_identity(self._fullscreen._current_photo)
            if curr_id in deleted_identities:
                # Find its index in filtered photos list
                _del_identities = [_photo_identity(p) for p in self._filtered_photos]
                try:
                    curr_idx = _del_identities.index(curr_id)
                except ValueError:
                    curr_idx = 0
                # Filter out all deleted photos
                remaining_photos = [p for p in self._filtered_photos if _photo_identity(p) not in deleted_identities]
                if remaining_photos:
                    next_idx = min(curr_idx, len(remaining_photos) - 1)
                    next_photo = remaining_photos[next_idx]
                    
        # Update our in-memory lists
        self._filtered_photos = [p for p in self._filtered_photos if _photo_identity(p) not in deleted_identities]
        self._all_photos = [p for p in self._all_photos if _photo_identity(p) not in deleted_identities]
        
        # Sync the grid UI
        for photo in deleted_photos:
            self._thumb_grid.remove_photo(photo)

        # 同上：批量删除后同步导入按钮可用性。
        # As above: refresh the import action after a batch deletion.
        self._update_apple_photos_button()


        # Reset selection if it was deleted
        if self._thumb_grid._selected_key in deleted_identities:
            self._thumb_grid._selected_key = None
            
        # Clear grid multi-select state
        self._thumb_grid.clear_multi_select()
        
        # Sync fullscreen viewer list
        self._fullscreen.set_photo_list(self._filtered_photos)
        
        # 6. Navigation / Transition UI state
        if in_fullscreen:
            if next_photo:
                self._thumb_grid.select_photo(next_photo)
                self._fullscreen.show_photo(next_photo)
                self._detail_panel.show_photo(next_photo)
            else:
                self._exit_fullscreen()
        else:
            # If not in fullscreen, just select the first available photo or clear panel
            if self._filtered_photos:
                first_remaining = self._filtered_photos[0]
                self._thumb_grid.select_photo(first_remaining)
                self._detail_panel.show_photo(first_remaining)
            else:
                self._detail_panel.show_photo(None)
                
        # 7. Update Status bar
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

        if key == Qt.Key_Left:
            if in_fullscreen:
                self._fullscreen_prev()
            else:
                self._prev_photo()
        elif key == Qt.Key_Right:
            if in_fullscreen:
                self._fullscreen_next()
            else:
                self._next_photo()
        elif key in _RATING_KEYS:
            # 键盘打星(Paul P0-3):Up/Down 由翻图改为星级±1,数字键 0-3 直设。
            # Keyboard rating: Up/Down now step the rating; digits set it.
            photo = (getattr(self._fullscreen, "_current_photo", None) if in_fullscreen
                     else getattr(self._detail_panel, "_current_photo", None))
            if photo:
                new_rating = _rating_key_action(key, photo.get("rating"))
                if new_rating is not None:
                    self._on_rating_changed(photo, new_rating)
                    if in_fullscreen:
                        self._fullscreen.update_rating_display(photo)
                    else:
                        self._detail_panel.show_photo(photo)
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
        elif key == Qt.Key_A and (event.modifiers() & Qt.ControlModifier):
            if not in_fullscreen and self._stack.currentIndex() != 2:
                self._thumb_grid.select_all()
        elif key == Qt.Key_Backspace and (event.modifiers() & Qt.ControlModifier):
            self._delete_selected_photos()
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
        importer = self._apple_photos_importer
        if importer is not None:
            try:
                importer.shutdown()
            except Exception:
                pass
            self._apple_photos_importer = None
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
