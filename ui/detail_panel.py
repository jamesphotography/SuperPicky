# -*- coding: utf-8 -*-
"""
SuperPicky - 结果浏览器右侧详情面板
DetailPanel: 大图预览 + 元数据展示 + 上一张/下一张导航
"""

import os
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QFormLayout,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize, QThread, Slot, QTimer
from PySide6.QtGui import QPixmap, QFont, QGuiApplication, QImage

from ui.styles import COLORS, FONTS


# ============================================================
#  后台异步图片加载器
# ============================================================

class _ImageLoader(QThread):
    """后台线程加载 QPixmap，避免主线程阻塞。"""
    ready = Signal(object)   # QPixmap

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        if self._path and os.path.exists(self._path):
            img = QImage(self._path)
            if not self._cancelled:
                self.ready.emit(img)
        else:
            if not self._cancelled:
                self.ready.emit(QImage())


# 对焦状态显示颜色（与缩略图圆点、筛选面板保持一致）
_FOCUS_COLORS = {
    "BEST":  COLORS['focus_best'],    # 绿 — 精焦
    "GOOD":  COLORS['focus_good'],    # 琥珀 — 合焦
    "BAD":   COLORS['focus_bad'],     # 近白灰 — 失焦
    "WORST": COLORS['focus_worst'],   # 灰 — 脱焦
}


def _make_section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"""
        QLabel {{
            color: {COLORS['text_tertiary']};
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            background: transparent;
        }}
    """)
    return lbl


def _display_filename(photo: dict) -> str:
    path = photo.get("current_path") or photo.get("original_path") or ""
    if path:
        return os.path.basename(path)
    return photo.get("filename") or ""


def _format_file_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(max(num_bytes, 0))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return "\u2014"


def _make_value_label(text: str = "—") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"""
        QLabel {{
            color: {COLORS['text_primary']};
            font-size: 12px;
            font-family: {FONTS['mono']};
            background: transparent;
        }}
    """)
    lbl.setWordWrap(True)
    return lbl


class _NoWrapLabel(QLabel):
    """单行不换行的 QLabel：minimumSizeHint 返回小宽度，避免撑宽父容器。"""
    def minimumSizeHint(self):
        h = super().minimumSizeHint()
        return QSize(40, h.height())


class _ZoomableImageLabel(QLabel):
    """支持鼠标滚轮缩放的图片 Label（基础实现）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._original: Optional[QPixmap] = None
        self._scale = 1.0
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background-color: {COLORS['bg_void']}; border-radius: 8px;")

    def set_pixmap(self, pixmap: QPixmap):
        self._original = pixmap
        self._scale = 1.0
        self._refresh()

    def _refresh(self):
        if self._original is None or self._original.isNull():
            muted = COLORS['text_muted']
            self.setText(f"<span style='color:{muted}'>—</span>")
            return
        target_w = int(self._original.width() * self._scale)
        target_h = int(self._original.height() * self._scale)
        scaled = self._original.scaled(
            min(target_w, self.width()),
            min(target_h, self.height()),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        super().setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._scale = min(self._scale * 1.15, 8.0)
        else:
            self._scale = max(self._scale / 1.15, 0.1)
        self._refresh()


class DetailPanel(QWidget):
    """
    右侧详情面板。

    信号:
        prev_requested()              用户点击"上一张"
        next_requested()              用户点击"下一张"
        rating_change_requested(object, int)  用户点击 ▼/▲ 修改评分 (photo, new_rating)
    """
    prev_requested = Signal()
    next_requested = Signal()
    rating_change_requested = Signal(object, int)

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self._current_photo: Optional[dict] = None
        self._use_crop_view = False     # True=裁切图, False=全图
        self._loader: Optional[_ImageLoader] = None

        self.setFixedWidth(300)
        self.setStyleSheet(f"background-color: {COLORS['bg_elevated']}; border-left: 1px solid {COLORS['border_subtle']};")

        self._build_ui()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- 图片预览区 ---
        self._img_label = _ZoomableImageLabel()
        self._img_label.setFixedHeight(200)
        layout.addWidget(self._img_label)

        # --- 裁切/全图切换 ---
        view_bar = QWidget()
        view_bar.setStyleSheet(f"background-color: {COLORS['bg_card']}; border-bottom: 1px solid {COLORS['border_subtle']};")
        vb_layout = QHBoxLayout(view_bar)
        vb_layout.setContentsMargins(8, 4, 8, 4)
        vb_layout.setSpacing(6)

        self._crop_btn = QPushButton(self.i18n.t("browser.crop_view"))
        self._full_btn = QPushButton(self.i18n.t("browser.full_view"))
        for btn in (self._crop_btn, self._full_btn):
            btn.setObjectName("secondary")
            btn.setFixedHeight(28)
        # 默认全图模式激活
        self._full_btn.setStyleSheet(self._active_btn_style())
        self._crop_btn.setStyleSheet(self._inactive_btn_style())
        self._crop_btn.clicked.connect(lambda: self._switch_view(True))
        self._full_btn.clicked.connect(lambda: self._switch_view(False))
        vb_layout.addWidget(self._crop_btn)
        vb_layout.addWidget(self._full_btn)
        layout.addWidget(view_bar)

        # --- 导航按钮 ---
        nav_bar = QWidget()
        nav_bar.setStyleSheet(f"background-color: {COLORS['bg_card']};")
        nb_layout = QHBoxLayout(nav_bar)
        nb_layout.setContentsMargins(8, 4, 8, 4)
        nb_layout.setSpacing(6)

        prev_btn = QPushButton(f"◀  {self.i18n.t('browser.prev')}")
        next_btn = QPushButton(f"{self.i18n.t('browser.next')}  ▶")
        for btn in (prev_btn, next_btn):
            btn.setFixedHeight(30)
            btn.setStyleSheet(self._nav_btn_style())
        prev_btn.clicked.connect(self.prev_requested)
        next_btn.clicked.connect(self.next_requested)
        nb_layout.addWidget(prev_btn)
        nb_layout.addWidget(next_btn)
        layout.addWidget(nav_bar)

        # --- 元数据滚动区域 ---
        meta_scroll = QScrollArea()
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        meta_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        meta_container = QWidget()
        meta_container.setStyleSheet("background: transparent;")
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(16, 12, 16, 16)
        meta_layout.setSpacing(12)

        # 评分行（大号显示 + ▼/▲ 调整按钮）
        rating_row = QHBoxLayout()
        rating_row.setSpacing(8)
        self._rating_label = QLabel("—")
        self._rating_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['star_gold']};
                font-size: 20px;
                background: transparent;
            }}
        """)
        rating_row.addWidget(self._rating_label)
        rating_row.addStretch()

        dec_btn = QPushButton("▼")
        dec_btn.setFixedSize(28, 28)
        dec_btn.setToolTip(self.i18n.t("labels.rating_dec_tooltip"))
        dec_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 5px;
                color: {COLORS['text_primary']};
                font-size: 13px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background: {COLORS['bg_elevated']};
                border-color: {COLORS['text_muted']};
                color: {COLORS['warning']};
            }}
        """)
        dec_btn.clicked.connect(self._on_rating_dec)
        rating_row.addWidget(dec_btn)

        inc_btn = QPushButton("▲")
        inc_btn.setFixedSize(28, 28)
        inc_btn.setToolTip(self.i18n.t("labels.rating_inc_tooltip"))
        inc_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['bg_input']};
                border: 1px solid {COLORS['accent']};
                border-radius: 5px;
                color: {COLORS['accent']};
                font-size: 13px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background: {COLORS['accent_dim']};
                color: {COLORS['accent_hover']};
                border-color: {COLORS['accent_hover']};
            }}
        """)
        inc_btn.clicked.connect(self._on_rating_inc)
        rating_row.addWidget(inc_btn)

        meta_layout.addLayout(rating_row)

        meta_layout.addWidget(self._divider())

        # FormLayout 元数据行
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)

        def _lbl(key: str) -> QLabel:
            l = QLabel(self.i18n.t(key))
            l.setStyleSheet(f"color: {COLORS['text_tertiary']}; font-size: 11px; background: transparent;")
            return l

        self._val_focus = _make_value_label()
        self._val_sharpness = _make_value_label()
        self._val_aesthetic = _make_value_label()
        self._val_flying = _make_value_label()
        self._val_species = _NoWrapLabel()
        self._val_species.setStyleSheet(f"color: {COLORS['accent']}; font-size: 12px; background: transparent;")
        self._val_species.setWordWrap(False)
        self._val_species.setMinimumHeight(28)
        self._val_camera = _make_value_label()
        self._val_lens = _NoWrapLabel()
        self._val_lens.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; font-family: {FONTS['mono']}; background: transparent;")
        self._val_lens.setWordWrap(False)
        self._val_lens.setMinimumHeight(28)
        self._val_shutter = _make_value_label()
        self._val_iso = _make_value_label()
        self._val_focal = _make_value_label()
        self._val_confidence = _make_value_label()
        self._val_filesize = _make_value_label()
        self._val_filename = _make_value_label()
        self._val_datetime = _make_value_label()
        self._val_caption = _make_value_label()
        self._val_caption.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px; font-family: {FONTS['mono']}; background: transparent;")
        self._val_caption.setWordWrap(True)

        rows = [
            ("browser.meta_focus",      self._val_focus),
            ("browser.meta_sharpness",  self._val_sharpness),
            ("browser.meta_aesthetic",  self._val_aesthetic),
            ("browser.meta_flying",     self._val_flying),
            ("browser.meta_species",    self._val_species),
            ("browser.meta_camera",     self._val_camera),
            ("browser.meta_lens",       self._val_lens),
            ("browser.meta_shutter",    self._val_shutter),
            ("browser.meta_iso",        self._val_iso),
            ("browser.meta_focal",      self._val_focal),
            ("browser.meta_confidence", self._val_confidence),
            ("browser.meta_filesize",   self._val_filesize),
            ("browser.meta_filename",   self._val_filename),
            ("browser.meta_datetime",   self._val_datetime),
        ]
        for key, val_widget in rows:
            form.addRow(_lbl(key), val_widget)

        meta_layout.addLayout(form)
        meta_layout.addStretch()

        # --- 折叠式选片备注（默认收起，点箭头展开）---
        meta_layout.addWidget(self._divider())

        self._caption_expanded = False

        self._caption_toggle_btn = QPushButton()
        self._caption_toggle_btn.setFlat(True)
        self._caption_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._caption_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                color: {COLORS['text_tertiary']};
                font-size: 11px;
                background: transparent;
                border: none;
                text-align: left;
                padding: 4px 0px;
            }}
            QPushButton:hover {{ color: {COLORS['text_secondary']}; }}
        """)
        self._caption_toggle_btn.clicked.connect(self._toggle_caption)
        meta_layout.addWidget(self._caption_toggle_btn)

        self._caption_content = QWidget()
        self._caption_content.setVisible(False)
        caption_inner = QVBoxLayout(self._caption_content)
        caption_inner.setContentsMargins(0, 2, 0, 6)
        caption_inner.setSpacing(0)
        caption_inner.addWidget(self._val_caption)
        meta_layout.addWidget(self._caption_content)

        self._update_caption_toggle_label()

        meta_scroll.setWidget(meta_container)
        layout.addWidget(meta_scroll, 1)

        # --- 底部：复制 EXIF 信息按钮 ---
        copy_bar = QWidget()
        copy_bar.setStyleSheet(f"background-color: {COLORS['bg_card']}; border-top: 1px solid {COLORS['border_subtle']};")
        cb_layout = QHBoxLayout(copy_bar)
        cb_layout.setContentsMargins(8, 6, 8, 6)

        self._copy_exif_btn = QPushButton(self.i18n.t("browser.copy_exif"))
        self._copy_exif_btn.setFixedHeight(28)
        self._copy_exif_btn.setStyleSheet(self._inactive_btn_style())
        self._copy_exif_btn.clicked.connect(self._on_copy_exif)
        self._copy_exif_btn.setEnabled(False)
        cb_layout.addWidget(self._copy_exif_btn)

        layout.addWidget(copy_bar)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background-color: {COLORS['border_subtle']}; max-height: 1px; border: none;")
        return line

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def show_photo(self, photo: dict):
        """显示一张照片的详情。"""
        self._current_photo = photo
        self._copy_exif_btn.setEnabled(True)
        self._refresh_image()
        self._refresh_metadata()

    def clear(self):
        """清空面板。"""
        self._current_photo = None
        self._copy_exif_btn.setEnabled(False)
        self._img_label.set_pixmap(QPixmap())
        for val in (
            self._val_focus, self._val_sharpness,
            self._val_aesthetic, self._val_flying, self._val_species,
            self._val_caption,
            self._val_camera, self._val_lens, self._val_shutter,
            self._val_iso, self._val_focal, self._val_confidence,
            self._val_filesize, self._val_filename, self._val_datetime,
        ):
            val.setText("—")
        self._rating_label.setText("—")
        self._caption_content.setVisible(False)
        self._caption_expanded = False
        self._update_caption_toggle_label()

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------

    def _toggle_caption(self):
        self._caption_expanded = not self._caption_expanded
        self._caption_content.setVisible(self._caption_expanded)
        self._update_caption_toggle_label()

    def _update_caption_toggle_label(self):
        arrow = "▼" if self._caption_expanded else "▶"
        label = self.i18n.t("browser.meta_caption")
        self._caption_toggle_btn.setText(f"{arrow} {label}")

    def _on_rating_dec(self):
        """▼ 按钮：评分 -1（最低 -1）。"""
        if not self._current_photo:
            return
        current = self._current_photo.get("rating", 0)
        new_val = max(-1, current - 1)
        if new_val == current:
            return
        self._current_photo["rating"] = new_val
        self._refresh_metadata()
        self.rating_change_requested.emit(dict(self._current_photo), new_val)

    def _on_rating_inc(self):
        """▲ 按钮：评分 +1（最高 5）。"""
        if not self._current_photo:
            return
        current = self._current_photo.get("rating", 0)
        new_val = min(5, current + 1)
        if new_val == current:
            return
        self._current_photo["rating"] = new_val
        self._refresh_metadata()
        self.rating_change_requested.emit(dict(self._current_photo), new_val)

    def _on_copy_exif(self):
        """复制当前照片的 EXIF 信息到剪贴板。"""
        if not self._current_photo:
            return
        p = self._current_photo
        lang = getattr(self.i18n, 'current_lang', 'zh_CN')
        is_zh = not lang.startswith('en')

        def t(key):
            return self.i18n.t(key)

        _rating_text = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★", 1: "★", 0: "0", -1: "—"}
        rating = p.get("rating", 0)

        focus = p.get("focus_status") or "—"
        sharp = p.get("adj_sharpness")
        topiq = p.get("adj_topiq")
        fl = p.get("focal_length")
        iso = p.get("iso")
        conf = p.get("confidence")

        if is_zh:
            species = p.get("bird_species_cn") or p.get("bird_species_en") or "—"
        else:
            species = p.get("bird_species_en") or p.get("bird_species_cn") or "—"

        lines = [
            f"{t('browser.meta_filename')}: {p.get('filename') or '—'}",
            f"{t('browser.meta_datetime')}: {(p.get('date_time_original') or '—')[:19]}",
            f"{t('browser.meta_camera')}: {p.get('camera_model') or '—'}",
            f"{t('browser.meta_lens')}: {p.get('lens_model') or '—'}",
            f"{t('browser.meta_shutter')}: {self._format_shutter(p.get('shutter_speed'))}",
            f"{t('browser.meta_iso')}: {iso if iso else '—'}",
            f"{t('browser.meta_focal')}: {f'{fl:.0f}mm' if fl else '—'}",
            f"{t('browser.meta_species')}: {species}",
            f"{t('browser.meta_focus')}: {focus}",
            f"{t('browser.meta_sharpness')}: {f'{sharp:.1f}' if sharp is not None else '—'}",
            f"{t('browser.meta_aesthetic')}: {f'{topiq:.2f}' if topiq is not None else '—'}",
            f"{t('browser.meta_confidence')}: {f'{conf*100:.1f}%' if conf else '—'}",
            f"{t('browser.meta_rating')}: {_rating_text.get(rating, '—')}",
        ]
        text = "\n".join(lines)
        QGuiApplication.clipboard().setText(text)

        # 短暂反馈
        self._copy_exif_btn.setText(self.i18n.t("browser.copy_exif_done"))
        self._copy_exif_btn.setStyleSheet(self._active_btn_style())
        QTimer.singleShot(1500, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self._copy_exif_btn.setText(self.i18n.t("browser.copy_exif"))
        self._copy_exif_btn.setStyleSheet(self._inactive_btn_style())

    def _nav_btn_style(self) -> str:
        """导航按钮（◀/▶）样式 — 比一般次级按钮更明显。"""
        return (
            f"QPushButton {{ background-color: {COLORS['bg_input']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
            f" color: {COLORS['text_primary']};"
            f" font-size: 12px;"
            f" padding: 2px 8px; }}"
            f" QPushButton:hover {{ background-color: {COLORS['bg_card']};"
            f" border-color: {COLORS['accent']}; color: {COLORS['accent']}; }}"
        )

    def _active_btn_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {COLORS['bg_input']};"
            f" border: 1px solid {COLORS['accent']};"
            f" border-radius: 6px;"
            f" color: {COLORS['accent']};"
            f" font-size: 12px;"
            f" padding: 2px 8px; }}"
        )

    def _inactive_btn_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {COLORS['bg_card']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
            f" color: {COLORS['text_secondary']};"
            f" font-size: 12px;"
            f" padding: 2px 8px; }}"
        )

    def _switch_view(self, use_crop: bool):
        self._use_crop_view = use_crop
        if use_crop:
            self._crop_btn.setStyleSheet(self._active_btn_style())
            self._full_btn.setStyleSheet(self._inactive_btn_style())
        else:
            self._full_btn.setStyleSheet(self._active_btn_style())
            self._crop_btn.setStyleSheet(self._inactive_btn_style())
        self._refresh_image()

    def _refresh_image(self):
        # 取消上一个未完成的加载
        if self._loader and self._loader.isRunning():
            self._loader.cancel()
            self._loader.wait(100)
            self._loader = None

        if not self._current_photo:
            self._img_label.set_pixmap(QPixmap())
            return

        # 立即显示 grid 缓存缩略图（零延迟反馈）
        try:
            from ui.thumbnail_grid import _thumb_cache
            fn = self._current_photo.get("filename", "")
            cached = _thumb_cache.get(fn)
            if cached and not cached.isNull():
                self._img_label.set_pixmap(cached)
        except Exception:
            pass

        # 解析目标路径
        path = self._resolve_image_path()

        # 后台异步加载全图
        if path:
            self._loader = _ImageLoader(path, self)
            self._loader.ready.connect(self._on_image_ready)
            self._loader.start()
        else:
            self._img_label.set_pixmap(QPixmap())

    def cleanup(self):
        if self._loader:
            self._loader.cancel()
            if self._loader.isRunning():
                self._loader.wait(1000)
            self._loader = None

    def _resolve_image_path(self) -> Optional[str]:
        """根据当前视图模式解析目标图片路径。"""
        p = self._current_photo
        if self._use_crop_view:
            # 裁切图：YOLO 裁切区域，退而用干净大图
            path = p.get("debug_crop_path")
            if not path or not os.path.exists(path):
                path = p.get("temp_jpeg_path")
        else:
            # 全图：干净 temp JPEG（无检测框叠加）
            path = p.get("temp_jpeg_path")
            if not path or not os.path.exists(path):
                path = p.get("debug_crop_path")

        if not path or not os.path.exists(path):
            op = p.get("original_path") or p.get("current_path")
            if op and os.path.exists(op) and os.path.splitext(op)[1].lower() in ('.jpg', '.jpeg'):
                path = op

        return path if path and os.path.exists(path) else None

    @Slot(object)
    def _on_image_ready(self, img: QImage):
        """后台加载完成，更新图片显示。"""
        px = QPixmap.fromImage(img)
        self._img_label.set_pixmap(px)

    @staticmethod
    def _format_shutter(val) -> str:
        """将小数快门速度（如 '0.0008'）转为摄影惯用格式（如 '1/1250s'）。"""
        if not val:
            return "—"
        try:
            v = float(val)
        except (ValueError, TypeError):
            return str(val)
        if v <= 0:
            return "—"
        if v >= 1:
            return f"{int(v)}s" if v == int(v) else f"{v:.1f}s"
        denom = round(1.0 / v)
        return f"1/{denom}s"

    def _refresh_metadata(self):
        if not self._current_photo:
            return

        p = self._current_photo
        lang = getattr(self.i18n, 'current_lang', 'zh_CN')
        is_zh = not lang.startswith('en')
        _unknown = "—"

        # 评分（支持 -1 ~ 5）
        rating = p.get("rating", 0)
        _rating_text = {
            5: "★★★★★",
            4: "★★★★",
            3: "★★★",
            2: "★★",
            1: "★",
            0: "0",
            -1: "—",
        }
        self._rating_label.setText(_rating_text.get(rating, _unknown))

        # 对焦
        focus = p.get("focus_status") or _unknown
        self._val_focus.setText(focus)
        color = _FOCUS_COLORS.get(focus, COLORS['text_primary'])
        self._val_focus.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")

        # 锐度（颜色跟随对焦状态）
        sharp = p.get("adj_sharpness")
        self._val_sharpness.setText(f"{sharp:.1f}" if sharp is not None else _unknown)
        sharp_color = _FOCUS_COLORS.get(focus, COLORS['text_primary'])
        self._val_sharpness.setStyleSheet(
            f"color: {sharp_color}; font-size: 13px; font-weight: 600; background: transparent;"
        )

        # 美学分（靛紫）
        topiq = p.get("adj_topiq")
        self._val_aesthetic.setText(f"{topiq:.2f}" if topiq is not None else _unknown)
        self._val_aesthetic.setStyleSheet(
            "color: #818cf8; font-size: 13px; font-weight: 600; background: transparent;"
        )

        # 飞行
        flying = p.get("is_flying")
        if flying == 1:
            self._val_flying.setText(self.i18n.t("browser.flying_yes"))
        elif flying == 0:
            self._val_flying.setText(self.i18n.t("browser.flying_no"))
        else:
            self._val_flying.setText(_unknown)

        # 鸟种（跟随界面语言）
        if self.i18n.current_lang.startswith('en'):
            species = p.get("bird_species_en") or p.get("bird_species_cn") or _unknown
        else:
            species = p.get("bird_species_cn") or p.get("bird_species_en") or _unknown
        self._val_species.setText(species)
        self._val_species.setToolTip(species)

        # 相机
        self._val_camera.setText(p.get("camera_model") or _unknown)

        # 镜头
        lens = p.get("lens_model") or _unknown
        self._val_lens.setText(lens)
        self._val_lens.setToolTip(lens)

        # 快门
        self._val_shutter.setText(self._format_shutter(p.get("shutter_speed")))

        # ISO
        iso = p.get("iso")
        self._val_iso.setText(str(iso) if iso else _unknown)

        # 焦距
        fl = p.get("focal_length")
        self._val_focal.setText(f"{fl:.0f}mm" if fl else _unknown)

        # AI置信度
        conf = p.get("confidence")
        self._val_confidence.setText(f"{conf*100:.1f}%" if conf else _unknown)

        file_path = p.get("current_path") or p.get("original_path") or ""
        if file_path and os.path.exists(file_path):
            try:
                self._val_filesize.setText(_format_file_size(os.path.getsize(file_path)))
            except OSError:
                self._val_filesize.setText(_unknown)
        else:
            self._val_filesize.setText(_unknown)

        # 文件名
        fn = _display_filename(p) or _unknown
        burst_pos = p.get("burst_position_index")
        burst_total = p.get("burst_total_count")
        if burst_pos and burst_total:
            fn = f"{fn} ({burst_pos}/{burst_total})"
        elif p.get("is_burst_group") and p.get("burst_count", 1) > 1:
            fn = f"{fn} (1/{p.get('burst_count')})"
            
        self._val_filename.setText(fn)

        # 拍摄时间
        dt = p.get("date_time_original") or _unknown
        # 只取日期时间部分（去掉秒后面的内容）
        if len(dt) > 19:
            dt = dt[:19]
        self._val_datetime.setText(dt)

        # 选片备注（折叠区）
        cap = p.get("caption") or _unknown
        self._val_caption.setText(cap)
        self._val_caption.setToolTip(cap)
        self._update_caption_toggle_label()
