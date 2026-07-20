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
    QSizePolicy, QToolButton
)
from PySide6.QtCore import Qt, Signal, QSize, QThread, Slot, QTimer
from PySide6.QtGui import QPixmap, QFont, QGuiApplication, QImage, QImageReader

from ui.styles import COLORS, FONTS
from ui.icon_utils import load_tinted_icon, stars_pixmap, tinted_png_path, glyph_png_path, ICON_IDLE, ICON_ACTIVE
from core.rarity_tier import gbif_score_to_tier, tier_name, tier_icon, tier_color


# ============================================================
#  后台异步图片加载器
# ============================================================

# 详情面板预览的最大解码边长。面板固定 300 宽 / 200 高,解码全尺寸
# temp JPEG(实测 4608×3072)纯属浪费;1024 已远超显示所需并留缩放余量。
# Max decode edge for the detail preview. The panel is fixed at 300×200,
# so decoding the full temp JPEG (4608×3072 in practice) is wasted work.
_DETAIL_MAX_EDGE = 1024


class _ImageLoader(QThread):
    """后台线程加载 QImage(解码期降采样到 _DETAIL_MAX_EDGE),避免主线程阻塞。"""
    ready = Signal(object)   # QImage

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
            # QImageReader.setScaledSize: JPEG 走 libjpeg DCT 降采样解码,
            # 14MP 预览图不再全尺寸解码(详情面板只有 300×200 显示区)。
            # Decode-time downscaling via libjpeg DCT scaling.
            reader = QImageReader(self._path)
            reader.setAutoTransform(True)
            src = reader.size()
            if src.isValid() and max(src.width(), src.height()) > _DETAIL_MAX_EDGE:
                scale = _DETAIL_MAX_EDGE / max(src.width(), src.height())
                reader.setScaledSize(QSize(
                    max(1, round(src.width() * scale)),
                    max(1, round(src.height() * scale)),
                ))
            img = reader.read()
            if img is None:
                img = QImage()
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

# 对焦状态 → i18n 标签 key（中文：精焦/合焦/失焦/脱焦）与图标 svg
_FOCUS_STATE_KEY = {
    "BEST": "focus_state_best", "GOOD": "focus_state_good",
    "BAD": "focus_state_bad", "WORST": "focus_state_worst",
}
_FOCUS_ICON = {
    "BEST": "scan-eye.svg", "GOOD": "fullscreen.svg",
    "BAD": "scan.svg", "WORST": "scan.svg",
}

# IUCN 红色名录等级 → (中文全名, 英文全名, 官方色)
# IUCN Red List category → (Chinese, English, official color)
_IUCN_INFO = {
    "LC":       ("无危",                 "Least Concern",                         "#60C659"),
    "NT":       ("近危",                 "Near Threatened",                       "#CCE226"),
    "VU":       ("易危",                 "Vulnerable",                            "#F9E814"),
    "EN":       ("濒危",                 "Endangered",                            "#FC7F3F"),
    "CR":       ("极危",                 "Critically Endangered",                 "#D81E05"),
    "CR (PE)":  ("极危（可能已灭绝）",      "Critically Endangered (Possibly Extinct)", "#D81E05"),
    "CR (PEW)": ("极危（野外可能已灭绝）",   "Critically Endangered (Possibly Extinct in the Wild)", "#D81E05"),
    "EW":       ("野外灭绝",              "Extinct in the Wild",                   "#542344"),
    "EX":       ("灭绝",                 "Extinct",                               "#000000"),
    "DD":       ("数据不足",              "Data Deficient",                        "#B2B2B2"),
    "NE":       ("未评估",                "Not Evaluated",                         "#B2B2B2"),
}


def _format_iucn(category: str, is_zh: bool) -> tuple:
    """
    根据 IUCN 等级代码返回 (显示文本, 颜色)。

    Args:
        category: IUCN 缩写（LC/NT/VU/EN/CR/...）
        is_zh: 当前是否为中文界面

    Returns:
        (display_text, color) — display_text 形如「易危 (VU)」/「Vulnerable (VU)」

    Format an IUCN category code into (display_text, color).
    """
    info = _IUCN_INFO.get(category)
    if not info:
        return (category, COLORS['text_primary'])
    zh_name, en_name, color = info
    name = zh_name if is_zh else en_name
    return (f"{name} ({category})", color)


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


def _is_no_bird_photo(photo: dict) -> bool:
    """
    判断当前记录是否为无鸟照片。

    兼容 SQLite 清洗后的整数值，以及旧数据中可能存在的字符串 yes/no。

    Return whether the current record represents a no-bird photo.

    This accepts integer values cleaned by SQLite and older string yes/no data.
    """
    has_bird = photo.get("has_bird")
    if isinstance(has_bird, str):
        return has_bird.strip().lower() in {"no", "0", "false"}
    if has_bird is not None:
        return not bool(has_bird)
    return photo.get("rating") == -1


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
    """单行不换行的 QLabel：minimumSizeHint 返回小宽度，避免撑宽父容器。

    V4.2.7: 增加 clicked 信号 — 鸟种行用它实现「点击复制鸟名」。
    """
    clicked = Signal()

    def minimumSizeHint(self):
        h = super().minimumSizeHint()
        return QSize(40, h.height())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
    # 用户点击铅笔图标请求修改鸟种，携带当前 photo dict
    # Emitted when user clicks the pencil icon to edit bird species; carries current photo dict.
    species_edit_requested = Signal(object)
    # 用户点击"裁剪建议"按钮，携带当前 photo dict，由上层处理弹窗
    # Emitted when user clicks "Crop Advice"; carries current photo dict; parent handles dialog.
    crop_advice_requested = Signal(object)

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

        prev_btn = QPushButton(f"  {self.i18n.t('browser.prev')}")
        prev_btn.setIcon(load_tinted_icon("arrow-left.svg", ICON_IDLE, 16))
        next_btn = QPushButton(f"{self.i18n.t('browser.next')}  ")
        next_btn.setIcon(load_tinted_icon("arrow-right.svg", ICON_IDLE, 16))
        next_btn.setLayoutDirection(Qt.RightToLeft)  # 图标置于文字右侧 / icon on the right
        for btn in (prev_btn, next_btn):
            btn.setFixedHeight(30)
            btn.setIconSize(QSize(16, 16))
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

        dec_btn = QPushButton()
        dec_btn.setIcon(load_tinted_icon("star-minus.svg", ICON_IDLE, 16))
        dec_btn.setIconSize(QSize(16, 16))
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

        inc_btn = QPushButton()
        inc_btn.setIcon(load_tinted_icon("star-plus.svg", ICON_ACTIVE, 16))
        inc_btn.setIconSize(QSize(16, 16))
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

        # V4.2.7: GBIF 全球罕见度（0-100 分制，AWS Open Data 2026-05 snapshot 派生）
        # V4.2.7: GBIF-derived global rarity (0-100, from AWS Open Data snapshot)
        self._val_gbif_rarity = _make_value_label()
        # iRateBird 鸟种颜值标签（0-100 分制，独立于逐张 TOPIQ 美学分）
        # iRateBird species-beauty label (0-100, distinct from the per-photo TOPIQ aesthetic score)
        self._val_species_beauty = _make_value_label()
        self._val_focus = _make_value_label()
        self._val_sharpness = _make_value_label()
        self._val_aesthetic = _make_value_label()
        self._val_flying = _make_value_label()
        self._val_species = _NoWrapLabel()
        self._val_species.setStyleSheet(f"color: {COLORS['accent']}; font-size: 12px; background: transparent;")
        self._val_species.setWordWrap(False)
        self._val_species.setMinimumHeight(28)
        # V4.2.7: 鸟种行点击复制鸟名到剪贴板
        # V4.2.7: Click the species label to copy the name to clipboard.
        self._val_species.setCursor(Qt.PointingHandCursor)
        self._val_species.clicked.connect(self._on_species_clicked)
        self._species_revert_text: Optional[str] = None
        # V4.2.7: IUCN 红色名录等级，紧贴鸟种之下显示
        # V4.2.7: IUCN Red List category, pinned directly under Species
        self._val_iucn = _make_value_label()
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

        # 文件名仍只在大图顶条显示;鸟种行按用户反馈(Paul P0-2)加回详情面板,
        # 置于全球罕见度上方,点击可复制鸟名(复用 _val_species 既有行为)。
        # The filename stays in the big-image top strip only; the species row
        # returns to the panel (above GBIF rarity) per user feedback, keeping
        # the existing click-to-copy behavior of _val_species.
        # issue #106: 鸟名右侧加铅笔按钮 → 发射 species_edit_requested,
        # 由浏览器窗口打开鸟种编辑弹窗(改名/补录 + 移动目录)。
        # issue #106: a pencil button next to the species emits
        # species_edit_requested; the browser opens the edit dialog.
        self._species_edit_btn = QToolButton()
        self._species_edit_btn.setIcon(
            load_tinted_icon("square-pen.svg", COLORS['text_muted'], size=13))
        self._species_edit_btn.setIconSize(QSize(13, 13))
        self._species_edit_btn.setFixedSize(18, 18)
        self._species_edit_btn.setCursor(Qt.PointingHandCursor)
        self._species_edit_btn.setFocusPolicy(Qt.NoFocus)
        self._species_edit_btn.setToolTip(self.i18n.t("fullscreen.tb_species"))
        self._species_edit_btn.setStyleSheet(f"""
            QToolButton {{ border: none; background: transparent; }}
            QToolButton:hover {{ background: {COLORS['accent_dim']}; border-radius: 4px; }}
        """)
        self._species_edit_btn.clicked.connect(self._on_species_edit_clicked)

        species_row = QWidget()
        species_row_lay = QHBoxLayout(species_row)
        species_row_lay.setContentsMargins(0, 0, 0, 0)
        species_row_lay.setSpacing(4)
        species_row_lay.addWidget(self._val_species)
        species_row_lay.addWidget(self._species_edit_btn)
        species_row_lay.addStretch(1)

        rows = [
            # 鸟类信息 4 行连续（鸟种 → 全球罕见度 → 鸟种颜值 → IUCN）
            # Four bird-related rows kept adjacent for natural reading
            # (species -> global rarity -> species beauty -> IUCN).
            ("browser.meta_species",    species_row),
            ("browser.meta_gbif_rarity", self._val_gbif_rarity),
            ("browser.meta_species_beauty", self._val_species_beauty),
            ("browser.meta_iucn",       self._val_iucn),
            ("browser.meta_focus",      self._val_focus),
            ("browser.meta_sharpness",  self._val_sharpness),
            ("browser.meta_aesthetic",  self._val_aesthetic),
            ("browser.meta_flying",     self._val_flying),
            ("browser.meta_camera",     self._val_camera),
            ("browser.meta_lens",       self._val_lens),
            ("browser.meta_shutter",    self._val_shutter),
            ("browser.meta_iso",        self._val_iso),
            ("browser.meta_focal",      self._val_focal),
            ("browser.meta_confidence", self._val_confidence),
            ("browser.meta_filesize",   self._val_filesize),
            ("browser.meta_datetime",   self._val_datetime),
        ]
        # 供测试断言行序 / kept for tests asserting row order
        self._meta_rows = rows
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

    def set_current_photo(self, photo: dict):
        """
        仅同步当前照片与元数据,不触发大图解码。
        供全屏导航期间调用:面板被 QStackedWidget 盖住不可见,解码大图纯属
        浪费 IO/CPU;退出全屏时 _switch_view 会用同步好的照片刷新一次图片。

        Sync the current photo and metadata WITHOUT loading the image.
        Used during fullscreen navigation where the panel is hidden behind
        the stacked widget — decoding for it wastes IO/CPU. On fullscreen
        exit, _switch_view refreshes the image once from the synced photo.

        参数 / Parameters:
            photo (dict): 照片记录 / photo record.
        """
        self._current_photo = photo
        self._copy_exif_btn.setEnabled(True)
        self._refresh_metadata()

    def clear(self):
        """清空面板。"""
        self._current_photo = None
        self._copy_exif_btn.setEnabled(False)
        self._img_label.set_pixmap(QPixmap())
        for val in (
            self._val_gbif_rarity,
            self._val_species_beauty,
            self._val_focus, self._val_sharpness,
            self._val_aesthetic, self._val_flying, self._val_species,
            self._val_iucn,
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
        svg = "arrow-down.svg" if self._caption_expanded else "arrow-right.svg"
        self._caption_toggle_btn.setIcon(load_tinted_icon(svg, ICON_IDLE, 14))
        self._caption_toggle_btn.setIconSize(QSize(14, 14))
        self._caption_toggle_btn.setText(f"  {self.i18n.t('browser.meta_caption')}")

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

        focus = p.get("focus_status")
        if focus in _FOCUS_STATE_KEY:
            focus = self.i18n.t(f"browser.{_FOCUS_STATE_KEY[focus]}")  # 英文枚举→中文标签
        elif not focus and _is_no_bird_photo(p):
            focus = self.i18n.t("browser.focus_no_bird")
        focus = focus or "—"
        sharp = p.get("adj_sharpness")
        topiq = p.get("adj_topiq")
        fl = p.get("focal_length")
        iso = p.get("iso")
        conf = p.get("confidence")

        if is_zh:
            species = p.get("bird_species_cn") or p.get("bird_species_en") or "—"
        else:
            species = p.get("bird_species_en") or p.get("bird_species_cn") or "—"

        gbif_r = p.get("gbif_rarity_100")
        iucn_raw = p.get("iucn_category")
        if iucn_raw:
            iucn_text, _ = _format_iucn(iucn_raw, is_zh)
        else:
            iucn_text = "—"

        lines = [
            f"{t('browser.meta_filename')}: {p.get('filename') or '—'}",
            f"{t('browser.meta_datetime')}: {(p.get('date_time_original') or '—')[:19]}",
            f"{t('browser.meta_camera')}: {p.get('camera_model') or '—'}",
            f"{t('browser.meta_lens')}: {p.get('lens_model') or '—'}",
            f"{t('browser.meta_shutter')}: {self._format_shutter(p.get('shutter_speed'))}",
            f"{t('browser.meta_iso')}: {iso if iso else '—'}",
            f"{t('browser.meta_focal')}: {f'{fl:.0f}mm' if fl else '—'}",
            f"{t('browser.meta_species')}: {species}",
            f"{t('browser.meta_gbif_rarity')}: {f'{tier_icon(gbif_score_to_tier(gbif_r))} {tier_name(gbif_score_to_tier(gbif_r), is_zh=is_zh)} ({gbif_r:.1f})' if gbif_r is not None else '—'}",
            f"{t('browser.meta_iucn')}: {iucn_text}",
            f"{t('browser.meta_focus')}: {focus}",
            f"{t('browser.meta_sharpness')}: {f'{sharp:.1f}' if sharp is not None else '—'}",
            f"{t('browser.meta_aesthetic')}: {f'{topiq:.2f}' if topiq is not None else '—'}",
            f"{t('browser.meta_confidence')}: {f'{conf*100:.1f}%' if conf is not None else '—'}",
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

    def _on_species_clicked(self):
        """点击鸟种行 → 复制鸟名到剪贴板 + 1.5s 反馈。"""
        text = (self._val_species.text() or "").strip()
        if not text or text == "—":
            return
        # 如果当前已经在「复制成功」反馈中，再点不重复处理
        if self._species_revert_text is not None:
            return
        QGuiApplication.clipboard().setText(text)
        self._species_revert_text = text
        self._val_species.setText(self.i18n.t("browser.species_copied"))
        self._val_species.setToolTip(self.i18n.t("browser.species_copied"))
        QTimer.singleShot(1500, self._restore_species_text)

    def _on_species_edit_clicked(self):
        """
        点击鸟名旁铅笔 → 发射 species_edit_requested(issue #106)。
        浏览器窗口接线到既有 _on_species_edit_requested 打开编辑弹窗。

        Pencil next to the species emits species_edit_requested (issue
        #106); the browser window opens the existing edit dialog.
        """
        if self._current_photo:
            self.species_edit_requested.emit(dict(self._current_photo))

    def _restore_species_text(self):
        """1.5s 后把鸟种行文本恢复（仅当用户没切换照片）。"""
        if self._species_revert_text is None:
            return
        original = self._species_revert_text
        self._species_revert_text = None
        # 当前 label 仍是「复制成功」时才恢复；用户已经切换照片就不动
        cur = (self._val_species.text() or "").strip()
        if cur == self.i18n.t("browser.species_copied"):
            self._val_species.setText(original)
            self._val_species.setToolTip(original)

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
        # 取消上一个未完成的加载:断开信号防旧图晚到覆盖新图,不在主线程 wait
        # (旧 wait(100) 让快速切图时每次白等最多 100ms;cancel 标志已保证
        # 解码完成后不再 emit,断开信号双保险)。
        # Cancel the previous load: disconnect so a late result can't
        # overwrite the new photo; never block the GUI thread waiting.
        if self._loader:
            self._loader.cancel()
            try:
                self._loader.ready.disconnect(self._on_image_ready)
            except (RuntimeError, TypeError):
                pass
            self._loader = None

        if not self._current_photo:
            self._img_label.set_pixmap(QPixmap())
            return

        # 立即显示 grid 缓存缩略图(零延迟反馈)。缓存键是 _photo_key 身份键
        # (干净图,不含角标),打星等状态变化后依然命中。
        # Show the grid's cached thumbnail instantly. The cache is keyed by
        # identity (_photo_key) and stores clean images, so it still hits
        # right after a rating change.
        try:
            from ui.thumbnail_grid import _thumb_cache, _photo_key
            cached = _thumb_cache.get(_photo_key(self._current_photo))
            if cached and not cached.isNull():
                self._img_label.set_pixmap(QPixmap.fromImage(cached))
        except Exception:
            pass

        # 解析目标路径
        path = self._resolve_image_path()

        # 后台异步加载大图(完成后自行销毁,避免 QThread 对象随导航累积)
        # Async load; the loader deletes itself when done so QThread
        # objects no longer accumulate with every navigation.
        if path:
            self._loader = _ImageLoader(path, self)
            self._loader.ready.connect(self._on_image_ready)
            self._loader.finished.connect(self._loader.deleteLater)
            self._loader.start()
        else:
            self._img_label.set_pixmap(QPixmap())

    def cleanup(self):
        if self._loader:
            try:
                self._loader.cancel()
                if self._loader.isRunning():
                    self._loader.wait(1000)
            except RuntimeError:
                # loader 已 deleteLater 销毁 / already destroyed via deleteLater
                pass
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

        # 评分:精选→皇冠(取代星级);1~5 星→SVG 金星;0/-1→文字
        rating = p.get("rating", 0)
        if p.get("picked"):
            self._rating_label.setPixmap(
                load_tinted_icon("crown.svg", COLORS['star_gold'], 22).pixmap(QSize(22, 22))
            )
        elif isinstance(rating, int) and rating >= 1:
            self._rating_label.setPixmap(stars_pixmap(rating, COLORS['star_gold'], size=18))
        else:
            self._rating_label.setText("0" if rating == 0 else _unknown)

        # GBIF 全球罕见度 → 5-tier 圆形充填图标 + tier 名 + 小字分数
        # GBIF rarity → 5-tier circle glyph + tier label + small score
        gbif_r = p.get("gbif_rarity_100")
        if gbif_r is not None:
            tidx = gbif_score_to_tier(gbif_r)
            is_zh = not self.i18n.current_lang.startswith('en')
            name = tier_name(tidx, is_zh=is_zh)
            color = tier_color(tidx) or COLORS['text_primary']
            # 罕见度图标用归一化 glyph(富文本内联),统一 ○◔◑◕● 大小;染 tier 颜色
            icon_png = glyph_png_path(tier_icon(tidx), color, 14)
            self._val_gbif_rarity.setText(
                f'<img src="{icon_png}" width="14" height="14" style="vertical-align:middle;">'
                f'  {name}  ({gbif_r:.1f})'
            )
            self._val_gbif_rarity.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: 600; background: transparent;"
            )
        else:
            self._val_gbif_rarity.setText(_unknown)
            self._val_gbif_rarity.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;"
            )

        # iRateBird 鸟种颜值（0–100，无数据显示占位）
        # iRateBird species beauty (0–100, placeholder when missing)
        beauty = p.get("aesthetic_index")
        if beauty is not None:
            self._val_species_beauty.setText(f"{beauty:.0f}")
            self._val_species_beauty.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 13px; font-weight: 600; background: transparent;"
            )
        else:
            self._val_species_beauty.setText(_unknown)
            self._val_species_beauty.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;"
            )

        # 对焦：中文标签 + 对应图标(svg 染对应颜色)，强化识别记忆
        focus_raw = p.get("focus_status")
        if focus_raw in _FOCUS_STATE_KEY:
            color = _FOCUS_COLORS.get(focus_raw, COLORS['text_primary'])
            label = self.i18n.t(f"browser.{_FOCUS_STATE_KEY[focus_raw]}")
            png = tinted_png_path(_FOCUS_ICON[focus_raw], color, size=14)
            self._val_focus.setText(f'<img src="{png}" width="14" height="14"> {label}')
            self._val_focus.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
        else:
            if not focus_raw and _is_no_bird_photo(p):
                txt = self.i18n.t("browser.focus_no_bird")
            else:
                txt = _unknown
            self._val_focus.setText(txt)
            self._val_focus.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;")

        # 锐度（颜色跟随对焦状态）
        sharp = p.get("adj_sharpness")
        self._val_sharpness.setText(f"{sharp:.1f}" if sharp is not None else _unknown)
        sharp_color = _FOCUS_COLORS.get(focus_raw, COLORS['text_primary'])
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

        # 鸟种（跟随界面语言）+ 显示铅笔编辑按钮
        # Bird species (follows UI language) + show pencil edit button.
        if self.i18n.current_lang.startswith('en'):
            species = p.get("bird_species_en") or p.get("bird_species_cn") or _unknown
        else:
            species = p.get("bird_species_cn") or p.get("bird_species_en") or _unknown
        self._val_species.setText(species)
        self._val_species.setToolTip(species)

        # IUCN 红色名录（中英全名 + 缩写，按官方色着色）
        # IUCN Red List (full name + abbreviation, official color)
        iucn = p.get("iucn_category")
        if iucn:
            is_zh = not self.i18n.current_lang.startswith('en')
            text, color = _format_iucn(iucn, is_zh)
            self._val_iucn.setText(text)
            self._val_iucn.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 600; background: transparent;"
            )
        else:
            self._val_iucn.setText(_unknown)
            self._val_iucn.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px; background: transparent;"
            )

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
        self._val_confidence.setText(f"{conf*100:.1f}%" if conf is not None else _unknown)

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
