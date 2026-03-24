# -*- coding: utf-8 -*-
"""
SuperPicky - 结果浏览器缩略图网格
ThumbnailGrid: 网格视图 + 异步缩略图加载
ThumbnailCard: 单张照片卡片（评分角标 + 对焦指示点）
ThumbnailLoader: QThread 后台加载缩略图
"""

import os
import threading
from collections import OrderedDict
from typing import Optional

from PySide6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QFrame,
    QVBoxLayout, QSizePolicy, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot, QSize, QTimer, QPoint, QRect, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QFont, QBrush, QImage

from ui.styles import COLORS, FONTS


# 对焦状态指示颜色（WORST 不显示圆点）
_FOCUS_DOT_COLORS = {
    "BEST":  QColor(COLORS['focus_best']),   # 绿 — 精焦
    "GOOD":  QColor(COLORS['focus_good']),   # 琥珀 — 合焦
    "BAD":   QColor(COLORS['focus_bad']),    # 近白灰 — 失焦
    # WORST 不入表 → _draw_overlays 中 `if focus in _FOCUS_DOT_COLORS` 自动跳过
}

# 评分标签颜色（2d：细化颜色）
_RATING_COLORS = {
    5: QColor("#FFD700"),   # 金色
    4: QColor("#E8C000"),   # 稍暗金色
    3: QColor("#FFD700"),   # 金色
    2: QColor("#E8C000"),   # 金色
    1: QColor("#FFD700"),   # 金色
    0: QColor(COLORS['text_muted']),
    -1: QColor(COLORS['text_muted']),
}

_DEFAULT_THUMB_SIZE = 160


def _photo_key(photo: dict):
    source_dir = photo.get("source_dir")
    filename = photo.get("filename", "")
    if source_dir:
        return (source_dir, filename)
    return filename


# ============================================================
#  LRU 缩略图缓存
# ============================================================

class _LRUCache:
    def __init__(self, maxsize: int = 500):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key) -> Optional[QImage]:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key, value: QImage):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()


_thumb_cache = _LRUCache(500)


def _draw_static_overlays(image: QImage, photo: dict):
    """在 QImage 上预先绘制静态叠加层（评分、对焦状态等）。"""
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    rating = photo.get("rating", 0)
    focus = photo.get("focus_status")

    # 右上角：评分星标
    if rating and rating > 0:
        if rating >= 4:
            stars = f"{rating}★"
        else:
            stars = "★" * rating
        color = _RATING_COLORS.get(rating, QColor(COLORS['text_muted']))
        bg = QColor(0, 0, 0, 160)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        rect_w = 40 if rating >= 4 else 36
        rect_h = 16
        x = image.width() - rect_w - 4
        painter.drawRoundedRect(x, 4, rect_w, rect_h, 4, 4)
        painter.setPen(color)
        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(x, 4, rect_w, rect_h, Qt.AlignCenter, stars)

    # 右下角：对焦状态圆点
    if focus and focus in _FOCUS_DOT_COLORS:
        dot_color = _FOCUS_DOT_COLORS[focus]
        cx = image.width() - 10
        cy = image.height() - 10
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(cx - 6, cy - 6, 12, 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(cx - 4, cy - 4, 8, 8)

    # 左下角：burst 编号
    burst_total = photo.get("burst_total")
    burst_pos = photo.get("burst_position")
    if burst_total is not None and burst_pos is not None:
        burst_text = f"B{burst_total}/{burst_pos}"
        bg = QColor(0, 0, 0, 160)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        rect_w, rect_h = 38, 16
        painter.drawRoundedRect(4, image.height() - rect_h - 4, rect_w, rect_h, 4, 4)
        painter.setPen(QColor(220, 220, 220))
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.drawText(4, image.height() - rect_h - 4, rect_w, rect_h, Qt.AlignCenter, burst_text)

    painter.end()


def _load_thumbnail_image(photo: dict, thumb_size: int) -> Optional[QImage]:
    """按优先级查找可用图片文件并返回裁切后的缩略图 QImage。"""
    candidates = []

    ydp = photo.get("yolo_debug_path")
    if ydp and os.path.exists(ydp):
        candidates.append(ydp)

    tjp = photo.get("temp_jpeg_path")
    if tjp and os.path.exists(tjp):
        candidates.append(tjp)

    dcp = photo.get("debug_crop_path")
    if dcp and os.path.exists(dcp):
        candidates.append(dcp)

    op = photo.get("original_path") or photo.get("current_path")
    if op and os.path.exists(op):
        ext = os.path.splitext(op)[1].lower()
        if ext in ('.jpg', '.jpeg'):
            candidates.append(op)

    for path in candidates:
        image = QImage(path)
        if image.isNull():
            continue
        size = QSize(thumb_size, thumb_size)
        image = image.scaled(
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation
        )
        if image.width() > thumb_size or image.height() > thumb_size:
            x = (image.width() - thumb_size) // 2
            y = (image.height() - thumb_size) // 2
            image = image.copy(x, y, thumb_size, thumb_size)
        return image

    return None


# ============================================================
#  ThumbnailLoader — 后台异步加载
# ============================================================

class _LoaderSignals(QObject):
    thumbnail_ready = Signal(object, object)   # photo_key, QImage
    load_error = Signal(object)

class _ThumbnailWorker(QThread):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        
    def run(self):
        while True:
            task = self.manager._get_next_task()
            if task is None:
                break # cancelled or finished
                
            photo, thumb_size = task
            photo_key = _photo_key(photo)
            
            # 先查缓存
            cached = _thumb_cache.get(photo_key)
            if cached is not None:
                self.manager.signals.thumbnail_ready.emit(photo_key, cached)
                continue

            pixmap = self.manager._load_pixmap(photo)
            if pixmap and not pixmap.isNull():
                size = QSize(thumb_size, thumb_size)
                pixmap = pixmap.scaled(
                    size,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation
                )
                if pixmap.width() > thumb_size or pixmap.height() > thumb_size:
                    x = (pixmap.width() - thumb_size) // 2
                    y = (pixmap.height() - thumb_size) // 2
                    pixmap = pixmap.copy(x, y, thumb_size, thumb_size)

                _draw_static_overlays(pixmap, photo)
                _thumb_cache.put(photo_key, pixmap)
                self.manager.signals.thumbnail_ready.emit(photo_key, pixmap)
            else:
                self.manager.signals.thumbnail_ready.emit(photo_key, QImage())

class ThumbnailLoader(QObject):
    """
    多线程后台加载器，优先处理可见区域的缩略图。
    """
    def __init__(self, tasks: list, thumb_size: int, parent=None):
        super().__init__(parent)
        self._thumb_size = thumb_size
        self.signals = _LoaderSignals()
        
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._tasks_map = {_photo_key(p): p for p in tasks}
        self._pending_set = set(self._tasks_map.keys())
        self._visible_keys = []
        
        self._cancelled = False
        self._workers = []
        
        num_workers = min(8, (os.cpu_count() or 4))
        for _ in range(num_workers):
            w = _ThumbnailWorker(self)
            self._workers.append(w)
            
    def start(self):
        for w in self._workers:
            w.start()

    def update_visible(self, photo_keys: list):
        with self._cond:
            self._visible_keys = [k for k in photo_keys if k in self._pending_set]
            if self._visible_keys:
                self._cond.notify_all()

    def cancel(self):
        with self._cond:
            self._cancelled = True
            self._pending_set.clear()
            self._cond.notify_all()
            
    def wait(self, msecs=None):
        for w in self._workers:
            w.wait()

    def isRunning(self):
        return any(w.isRunning() for w in self._workers)

    def cleanup(self):
        self.cancel()
        self.wait(1000)

    def _get_next_task(self):
        with self._cond:
            while not self._cancelled and not self._pending_set:
                self._cond.wait()
            
            if self._cancelled or not self._pending_set:
                return None
                
            # 优先可见区域
            for key in self._visible_keys:
                if key in self._pending_set:
                    self._pending_set.remove(key)
                    self._visible_keys.remove(key)
                    return self._tasks_map[key], self._thumb_size
            
            # 若无可见区域，弹出任意一个待处理任务
            if self._pending_set:
                key = self._pending_set.pop()
                return self._tasks_map[key], self._thumb_size
                
            return None

    def _load_pixmap(self, photo: dict) -> Optional[QImage]:
        """按优先级查找可用图片文件并加载到 QImage。"""
        return _load_thumbnail_image(photo, self._thumb_size)


# ============================================================
#  ThumbnailCard — 单张照片卡片
# ============================================================

class ThumbnailCard(QFrame):
    """
    单张照片的缩略图卡片。

    信号 clicked(photo_dict) 在用户单击时发出。
    信号 double_clicked(photo_dict) 在用户双击时发出。
    信号 context_menu_requested(photo_dict, QPoint) 在右键时发出（C4）。
    """
    clicked = Signal(dict)
    double_clicked = Signal(dict)
    context_menu_requested = Signal(dict, object)  # C4 右键菜单

    # V5: Add badge clicked signal
    badge_clicked = Signal(dict)

    def __init__(self, photo: dict, thumb_size: int = _DEFAULT_THUMB_SIZE, parent=None):
        super().__init__(parent)
        self.photo = photo
        self._thumb_size = thumb_size
        self._selected = False
        self._multi_selected_state = False
        self._raw_image: Optional[QImage] = None  # 存储带静态叠加层的 QImage
        self._final_pixmap: Optional[QPixmap] = None # 最终显示的 QPixmap
        self.is_burst_group = photo.get("is_burst_group", False)
        self.burst_count = photo.get("burst_count", 1)
        self.is_expanded_member = photo.get("is_expanded_burst_member", False)
        self.burst_position_index = photo.get("burst_position_index", 0)
        self.burst_total_count = photo.get("burst_total_count", 1)

        # Store rects for click detection
        self._badge_rect = QRect()

        self.setFixedSize(thumb_size + 8, thumb_size + 32)
        self.setStyleSheet(self._normal_style())
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # 图片 label
        self.img_label = QLabel()
        self.img_label.setFixedSize(thumb_size, thumb_size)
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setStyleSheet(f"""
            QLabel {{
                background-color: {COLORS['bg_void']};
                border-radius: 6px;
                color: {COLORS['text_muted']};
                font-size: 11px;
            }}
        """)
        # 移除 "..." 文字，改用背景色以减少视觉闪烁
        self.img_label.setText("") 
        layout.addWidget(self.img_label)

        # 文件名 label
        fn = photo.get("filename", "")
        if self.is_burst_group and self.burst_count > 1:
            fn = f"{fn} ({self.burst_count})"
        self.name_label = QLabel(fn)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_tertiary']};
                font-size: 10px;
                background: transparent;
            }}
        """)
        self.name_label.setMaximumWidth(thumb_size + 4)
        layout.addWidget(self.name_label)

    def set_pixmap(self, image: QImage):
        try:
            if image.isNull():
                self._raw_image = None
                self._final_pixmap = None
                self.img_label.setText("—")
            else:
                self._raw_image = image
                self._final_pixmap = QPixmap.fromImage(image)
                self.img_label.setText("")
            # 在图片上绘制动态叠加层（选中边框、多选勾选）
            self._draw_overlays()
        except RuntimeError:
            # 基础 C++ 对象已销毁，忽略此次更新
            pass

    def _draw_overlays(self):
        """在 img_label 的 pixmap 上绘制动态叠加层（选中边框、多选勾选）。"""
        try:
            if self._final_pixmap is None or self._final_pixmap.isNull():
                return
            
            # ... (rest of the method logic)
        except RuntimeError:
            return

        # 始终从 raw_image 转换后的 pixmap 开始，避免反复叠加
        overlay = QPixmap(self._final_pixmap)
        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w = overlay.width()
        h = overlay.height()

        # Stacked effect for burst groups
        if getattr(self, 'is_burst_group', False):
            # Simulated card 1 (bottom right)
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
            painter.drawLine(w - 3, 6, w - 3, h - 3)
            painter.drawLine(6, h - 3, w - 3, h - 3)
            
            # Simulated card 2 (further bottom right)
            painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
            painter.drawLine(w - 1, 9, w - 1, h - 1)
            painter.drawLine(9, h - 1, w - 1, h - 1)
            
            # Draw burst count badge in top-left
            self._badge_rect = QRect(4, 4, 32, 16)
            bg = QColor(0, 0, 0, 180)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(self._badge_rect, 4, 4)
            painter.setPen(QColor(255, 255, 255))
            font = QFont()
            font.setPixelSize(10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self._badge_rect, Qt.AlignCenter, str(self.burst_count))
            
        elif getattr(self, 'is_expanded_member', False):
            # Draw a left colored border indicator to visually group them
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS['accent']))
            painter.drawRect(0, 0, 4, h)
            
            # Add collapse badge on the first item
            if self.burst_position_index == 1:
                self._badge_rect = QRect(8, 4, 40, 16)
                bg = QColor(0, 0, 0, 180)
                painter.setBrush(bg)
                painter.drawRoundedRect(self._badge_rect, 4, 4)
                painter.setPen(QColor(255, 255, 255))
                font = QFont()
                font.setPixelSize(10)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(self._badge_rect, Qt.AlignCenter, f"< {self.burst_total_count} >")
            else:
                self._badge_rect = QRect()

        # 1. 多选勾选标记
        if getattr(self, '_multi_selected_state', False):
            # 蓝色勾选圆圈
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(59, 130, 246, 220))  # 蓝色
            # Move checkbox if badge is present
            cx = 40 if not self._badge_rect.isEmpty() else 4
            painter.drawEllipse(cx, 4, 20, 20)
            painter.setPen(QPen(QColor(255, 255, 255, 255), 2.0))
            painter.setBrush(Qt.NoBrush)
            # 绘制 √ 符号（简化为折线）
            painter.drawLine(cx + 4, 14, cx + 7, 18)
            painter.drawLine(cx + 7, 18, cx + 14, 9)

        # 选中状态：2px 实线青绿框
        if getattr(self, '_selected', False):
            pen = QPen(QColor(COLORS['accent']))  # #00d4aa
            pen.setWidth(2)
            pen.setStyle(Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(1, 1, w - 2, h - 2)

        painter.end()
        self.img_label.setPixmap(overlay)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._draw_overlays()  # 从原始 pixmap 重绘，含/不含虚线框

    def _normal_style(self):
        # UI is already dark (#111 primary, #1f1f1f card). 
        # For expanded members, we want them to pop out significantly, so we use a much lighter grey (#444444) 
        # which is distinctly lighter and acts as a clear visual block.
        bg_color = "#444444" if self.is_expanded_member else COLORS['bg_card']
        border_color = COLORS['border_subtle']
        
        # Optionally, we can give a slight tint to expanded members
        if self.is_expanded_member:
            return f"""
                QFrame {{
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-radius: 8px;
                }}
                QFrame:hover {{
                    border: 1px solid {COLORS['accent']};
                    background-color: #555555;
                }}
            """
        
        return f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QFrame:hover {{
                border: 1px solid {COLORS['accent_deep']};
                background-color: {COLORS['bg_elevated']};
            }}
        """

    def set_multi_selected(self, selected: bool):
        """设置多选高亮状态（供 ThumbnailGrid 调用）。"""
        self._multi_selected_state = selected
        self._draw_overlays()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 1. Check if clicked on badge inside img_label
            # The click event is relative to ThumbnailCard, but the rect is relative to overlay (which matches img_label size).
            # We need to map the event pos to img_label coordinates.
            pos_in_img = self.img_label.mapFrom(self, event.pos())
            if not self._badge_rect.isEmpty() and self._badge_rect.contains(pos_in_img):
                self.badge_clicked.emit(self.photo)
                # Don't emit standard click if badge was clicked to prevent selecting it
                return
                
            # 2. Check if clicked on the filename label (acts as toggle for burst groups)
            if self.name_label.geometry().contains(event.pos()):
                if self.is_burst_group or self.is_expanded_member:
                    self.badge_clicked.emit(self.photo)
                    return
                
            self.clicked.emit(self.photo)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.photo)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """C4：右键菜单 — 发射信号，由 ResultsBrowserWidget 处理。"""
        self.context_menu_requested.emit(self.photo, self.mapToGlobal(event.pos()))
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Right, Qt.Key_Down):
            # 沿 parent 链找到 ThumbnailGrid 并代理箭头键
            # 层级：card → _container → viewport → ThumbnailGrid
            node = self.parent()
            while node is not None:
                if isinstance(node, ThumbnailGrid):
                    node.keyPressEvent(event)
                    return
                node = node.parent()
        super().keyPressEvent(event)


# ============================================================
#  ThumbnailGrid — 缩略图网格
# ============================================================

class ThumbnailGrid(QScrollArea):
    """
    照片缩略图网格。

    信号 photo_selected(photo_dict) 在用户选中一张照片时发出。
    信号 photo_double_clicked(photo_dict) 在用户双击缩略图时发出。
    信号 multi_selection_changed(list) 多选状态变化时发出（C3）。
    信号 burst_badge_clicked(burst_id) 当连拍角标被点击时发出。
    """
    photo_selected = Signal(dict)
    photo_double_clicked = Signal(dict)
    multi_selection_changed = Signal(list)   # C3 多选信号
    burst_badge_clicked = Signal(int)        # V5: Burst badge click signal

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self._thumb_size = _DEFAULT_THUMB_SIZE
        self._photos: list = []
        self._cards: dict = {}         # photo_key -> ThumbnailCard
        self._selected_key = None
        self._loader: Optional[ThumbnailLoader] = None
        self._transition_overlay: Optional[QLabel] = None
        self._transition_effect: Optional[QGraphicsOpacityEffect] = None
        self._transition_anim: Optional[QPropertyAnimation] = None
        # C3 多选状态
        self._multi_selected: set = set()       # photo_key 集合
        self._last_clicked_idx: int = -1        # Shift 范围选起点
        self._anchor_photo: Optional[dict] = None  # 单选锚点（对比视图左侧）
        self._pending_photos: Optional[list] = None  # 延迟构建用
        # 分批构建定时器
        self._batch_timer = QTimer(self)
        self._batch_timer.timeout.connect(self._build_batch)
        self._current_batch_idx = 0
        self._current_col_count = 1  # 记录当前批次使用的列数，保持一致

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea {{ background-color: {COLORS['bg_primary']}; border: none; }}")
        self.setFocusPolicy(Qt.StrongFocus)

        self._container = QWidget()
        self._container.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(16, 16, 16, 16)
        self.setWidget(self._container)

        # 空状态 label
        self._empty_label = QLabel(self.i18n.t("browser.no_results"))
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_muted']};
                font-size: 14px;
                background: transparent;
            }}
        """)
        self._grid.addWidget(self._empty_label, 0, 0, 1, 1)

        # 加载中提示（缩略图建网格前短暂显示）
        self._loading_label = QLabel(self.i18n.t("browser.loading"))
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_muted']};
                font-size: 14px;
                background: transparent;
            }}
        """)
        self._loading_label.hide()

        # 延迟构建定时器（等布局稳定后再计算列数）
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(50)
        self._build_timer.timeout.connect(self._deferred_build)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._update_visible_items()



    def _on_scroll(self, value):
        self._update_visible_items()

    def _update_visible_items(self):
        if not self._loader or not self._photos:
            return
        
        y_offset = self.verticalScrollBar().value()
        viewport_height = self.viewport().height()
        visible_top = y_offset
        visible_bottom = y_offset + viewport_height
        
        row_h = self._thumb_size + 40
        start_row = max(0, (visible_top - 16) // row_h)
        end_row = (visible_bottom - 16) // row_h + 1
        
        col_count = self._current_col_count
        if col_count <= 0: return
        
        start_idx = start_row * col_count
        end_idx = min(len(self._photos), (end_row + 1) * col_count)
        
        visible_keys = []
        for i in range(start_idx, end_idx):
            visible_keys.append(_photo_key(self._photos[i]))
            
        self._loader.update_visible(visible_keys)

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def load_photos(self, photos: list, keep_scroll: bool = False):
        """加载照片列表并重建网格。延迟 50ms 构建以等布局稳定，避免列数跳变。"""
        # 取消上一个加载任务
        if self._loader and self._loader.isRunning():
            self._loader.cancel()
            self._loader.wait(500)
        self._start_transition_overlay()

        # 记录高精度滚动位置（基于索引的浮点行偏移，可跨列数精确恢复）
        self._saved_scroll_index = -1.0
        if keep_scroll and self._photos:
            y_offset = self.verticalScrollBar().value()
            row_h = self._thumb_size + 40
            # 计算当前位于第几行（浮点数，保留行内偏移）
            current_row_float = max(0.0, (y_offset - 16) / row_h)
            # 转换为相对于总列表的“虚拟索引”
            self._saved_scroll_index = current_row_float * self._current_col_count

        # 清空缩略图缓存

        # 彻底重置状态
        self._cards.clear()
        self._multi_selected.clear()
        self._last_clicked_idx = -1
        self._anchor_photo = None
        
        # 移除加载提示，改为渐进式渲染
        self._pending_photos = photos
        self._build_timer.start()

    def cleanup(self):
        self._build_timer.stop()
        self._batch_timer.stop()
        self._clear_transition_overlay()
        if self._loader:
            self._loader.cleanup()
            self._loader = None

    def _deferred_build(self):
        """延迟构建网格开始（布局稳定后执行）。"""
        photos = self._pending_photos
        if photos is None:
            return
        self._pending_photos = None
        self._photos = photos

        # 1. 计算新列数
        self._current_col_count = max(1, (self.width() - 32) // (self._thumb_size + 8))
        
        # 2. 【关键优化】预设容器总高度，确保滚动条立即获得正确的 Range，防止 setValue 失效
        num_photos = len(self._photos)
        num_rows = (num_photos + self._current_col_count - 1) // self._current_col_count
        row_h = self._thumb_size + 40
        total_h = num_rows * row_h + 32 # 16*2 margins
        self._container.setMinimumHeight(total_h)

        # 3. 彻底清空网格
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w in (self._empty_label, self._loading_label):
                w.hide()
                continue
            if w:
                w.deleteLater()
        
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
            self._grid.setRowMinimumHeight(r, 0)

        if not self._photos:
            self._batch_timer.stop()
            self._container.setMinimumHeight(200)
            self._grid.addWidget(self._empty_label, 0, 0, 1, 1)
            self._empty_label.show()
            QTimer.singleShot(0, self._finish_transition_overlay)
            return

        # 4. 准备开始分批
        self._batch_timer.stop()
        self._current_batch_idx = 0
        
        # 立即渲染第一批
        self._build_batch() 

        # 5. 恢复滚动位置
        if getattr(self, '_saved_scroll_index', -1.0) >= 0:
            # 根据保存的虚拟索引和新列数，计算新的物理 Y 坐标
            target_row_float = self._saved_scroll_index / self._current_col_count
            target_y = int(target_row_float * row_h + 16)
            # 确保在布局引擎更新后执行
            QTimer.singleShot(0, lambda y=target_y: self.verticalScrollBar().setValue(y))
            self._saved_scroll_index = -1.0

        QTimer.singleShot(0, self._finish_transition_overlay)

        # 6. 启动后续分批
        if self._current_batch_idx < len(self._photos):
            self._batch_timer.start(5)

    def _build_batch(self):
        """分批构建卡片，防止 UI 假死。"""
        if not self._photos:
            self._batch_timer.stop()
            return

        batch_size = 60
        total = len(self._photos)
        end_idx = min(self._current_batch_idx + batch_size, total)

        col_count = self._current_col_count
        last_row = 0

        for idx in range(self._current_batch_idx, end_idx):
            photo = self._photos[idx]
            row, col = divmod(idx, col_count)
            last_row = row
            card = ThumbnailCard(photo, self._thumb_size)
            card.clicked.connect(self._on_card_clicked)
            card.double_clicked.connect(lambda p: self.photo_double_clicked.emit(p))
            card.context_menu_requested.connect(self._on_context_menu_requested)
            card.badge_clicked.connect(self._on_badge_clicked)
            photo_key = _photo_key(photo)
            self._cards[photo_key] = card
            self._grid.addWidget(card, row, col)
            
            # 强制设置行最小高度
            self._grid.setRowMinimumHeight(row, self._thumb_size + 32)

            cached = _thumb_cache.get(photo_key)
            if cached:
                card.set_pixmap(cached)

        self._current_batch_idx = end_idx

        if self._current_batch_idx >= total:
            self._batch_timer.stop()
            # 补一个尾部弹簧
            self._grid.setRowStretch(last_row + 1, 1)
            # 完成后移除 MinimumHeight 限制，让布局自由发挥
            self._container.setMinimumHeight(0)

            if self._loader and self._loader.isRunning():
                self._loader.cancel()
                self._loader.wait(500)
            self._loader = ThumbnailLoader(self._photos, self._thumb_size, self)
            self._loader.signals.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._loader.start()
            self._update_visible_items()

    def set_thumb_size(self, size: int):
        """调整缩略图尺寸并重新加载。"""
        if size != self._thumb_size:
            self._thumb_size = size
            _thumb_cache.clear()
            self.load_photos(self._photos, keep_scroll=True)

    def get_multi_selected_photos(self) -> list:
        """返回对比视图所需的照片对（最多2张）。"""
        in_multi = [p for p in self._photos if _photo_key(p) in self._multi_selected]
        if len(in_multi) == 1 and self._anchor_photo:
            anchor_key = _photo_key(self._anchor_photo)
            if anchor_key not in self._multi_selected:
                return [self._anchor_photo] + in_multi
        return in_multi

    def clear_multi_select(self):
        """公共接口：清空多选状态（ESC 快捷键或取消对比时使用）。"""
        self._clear_multi_selection()
        self._anchor_photo = None
        self._emit_multi_selection()

    def refresh_photo(self, photo_or_key, new_rating: int):
        """更新指定照片的评分角标（不重新加载缩略图）。"""
        photo_key = _photo_key(photo_or_key) if isinstance(photo_or_key, dict) else photo_or_key
        card = self._cards.get(photo_key)
        if card:
            card.photo["rating"] = new_rating
            image = _load_thumbnail_image(card.photo, self._thumb_size)
            if image and not image.isNull():
                _draw_static_overlays(image, card.photo)
                _thumb_cache.put(photo_key, image)
                card.set_pixmap(image)
            else:
                card._draw_overlays()

    def remove_photo(self, photo_or_key):
        """从网格中移除指定缩略图卡片（不重新加载全部数据）。"""
        photo_key = _photo_key(photo_or_key) if isinstance(photo_or_key, dict) else photo_or_key
        card = self._cards.pop(photo_key, None)
        if card:
            self._grid.removeWidget(card)
            card.deleteLater()
        self._photos = [p for p in self._photos if _photo_key(p) != photo_key]
        self._multi_selected.discard(photo_key)
        if self._selected_key == photo_key:
            self._selected_key = None

    def select_photo(self, photo_or_key):
        """高亮选中指定照片卡片。"""
        photo_key = _photo_key(photo_or_key) if isinstance(photo_or_key, dict) else photo_or_key
        if self._selected_key and self._selected_key in self._cards:
            self._cards[self._selected_key].set_selected(False)
        self._selected_key = photo_key
        if photo_key in self._cards:
            self._cards[photo_key].set_selected(True)
            # 滚动到可见区域
            card = self._cards[photo_key]
            self.ensureWidgetVisible(card)

    def select_next(self) -> Optional[dict]:
        """选中下一张，返回 photo dict；已在末尾则返回 None。"""
        return self._select_adjacent(1)

    def select_prev(self) -> Optional[dict]:
        """选中上一张，返回 photo dict；已在开头则返回 None。"""
        return self._select_adjacent(-1)

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------

    def _select_adjacent(self, delta: int) -> Optional[dict]:
        if not self._photos:
            return None
        photo_keys = [_photo_key(p) for p in self._photos]
        try:
            idx = photo_keys.index(self._selected_key)
        except ValueError:
            idx = -1
        new_idx = idx + delta
        if 0 <= new_idx < len(self._photos):
            photo = self._photos[new_idx]
            self.select_photo(photo)
            self.photo_selected.emit(photo)
            return photo
        return None

    @Slot(object, object)
    def _on_thumbnail_ready(self, photo_key, image: QImage):
        card = self._cards.get(photo_key)
        if card:
            card.set_pixmap(image)

    def _on_badge_clicked(self, photo: dict):
        burst_id = photo.get("burst_id")
        if burst_id is not None:
            self.burst_badge_clicked.emit(burst_id)

    def _on_card_clicked(self, photo: dict):
        """处理卡片点击，支持 Ctrl/Shift 多选（C3）。"""
        from PySide6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        photo_key = _photo_key(photo)
        photo_keys = [_photo_key(p) for p in self._photos]
        try:
            clicked_idx = photo_keys.index(photo_key)
        except ValueError:
            clicked_idx = -1

        if modifiers & Qt.ControlModifier:
            # Ctrl+点击：切换该照片的多选状态
            if photo_key in self._multi_selected:
                self._multi_selected.discard(photo_key)
                card = self._cards.get(photo_key)
                if card:
                    card.set_multi_selected(False)
            else:
                self._multi_selected.add(photo_key)
                card = self._cards.get(photo_key)
                if card:
                    card.set_multi_selected(True)
            self._last_clicked_idx = clicked_idx
            self._emit_multi_selection()
        elif modifiers & Qt.ShiftModifier and self._last_clicked_idx >= 0 and clicked_idx >= 0:
            # Shift+点击：范围选中
            lo = min(self._last_clicked_idx, clicked_idx)
            hi = max(self._last_clicked_idx, clicked_idx)
            for i in range(lo, hi + 1):
                key = photo_keys[i]
                self._multi_selected.add(key)
                card = self._cards.get(key)
                if card:
                    card.set_multi_selected(True)
            self._emit_multi_selection()
        else:
            # 普通点击：清空多选，单选当前，更新 anchor
            self._clear_multi_selection()
            self._anchor_photo = photo
            self._last_clicked_idx = clicked_idx
            self.select_photo(photo)
            self.photo_selected.emit(photo)
            self._emit_multi_selection()   # 让 compare 按钮隐藏

    def _clear_multi_selection(self):
        """清空所有多选状态。"""
        for key in list(self._multi_selected):
            card = self._cards.get(key)
            if card:
                card.set_multi_selected(False)
        self._multi_selected.clear()

    def _emit_multi_selection(self):
        """发射多选变化信号（含 anchor 逻辑，最多传出 2 张）。"""
        self.multi_selection_changed.emit(self.get_multi_selected_photos())

    def _on_context_menu_requested(self, photo: dict, pos):
        """C4：将右键菜单请求向上传递（由父级窗口处理）。"""
        # 通过信号链向上传递：ThumbnailGrid → ResultsBrowserWidget
        # 使用 parent chain 找到 ResultsBrowserWidget
        node = self.parent()
        while node is not None:
            handler = getattr(node, '_show_context_menu', None)
            if handler:
                handler(photo, pos)
                return
            node = node.parent()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Up):
            self._select_adjacent(-1)
        elif key in (Qt.Key_Right, Qt.Key_Down):
            self._select_adjacent(1)
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口改变大小时延迟重排网格（复用 _build_timer 防抖）
        if self._photos and not self._pending_photos:
            # 计算新列数，如果没变就跳过重排，极大减少闪烁
            new_col_count = max(1, (self.width() - 32) // (self._thumb_size + 8))
            if new_col_count != self._current_col_count:
                # 记录当前位置
                y_offset = self.verticalScrollBar().value()
                row_h = self._thumb_size + 40
                current_row_float = max(0.0, (y_offset - 16) / row_h)
                self._saved_scroll_index = current_row_float * self._current_col_count
                
                self._pending_photos = self._photos
                self._build_timer.start()

    def _start_transition_overlay(self):
        """Keep the previous grid visible until the rebuilt layout is ready to fade in."""
        if not self.viewport().isVisible() or not self._cards:
            self._clear_transition_overlay()
            return

        snapshot = self.viewport().grab()
        if snapshot.isNull():
            self._clear_transition_overlay()
            return

        self._clear_transition_overlay()
        overlay = QLabel(self.viewport())
        overlay.setPixmap(snapshot)
        overlay.setScaledContents(True)
        overlay.setGeometry(self.viewport().rect())
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        overlay.show()
        overlay.raise_()

        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(1.0)
        overlay.setGraphicsEffect(effect)

        self._transition_overlay = overlay
        self._transition_effect = effect

    def _finish_transition_overlay(self):
        if not self._transition_overlay or not self._transition_effect:
            return

        anim = QPropertyAnimation(self._transition_effect, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._clear_transition_overlay)
        self._transition_anim = anim
        anim.start()

    def _clear_transition_overlay(self):
        if self._transition_anim:
            self._transition_anim.stop()
            self._transition_anim.deleteLater()
            self._transition_anim = None
        if self._transition_overlay:
            self._transition_overlay.deleteLater()
            self._transition_overlay = None
        self._transition_effect = None
