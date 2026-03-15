# -*- coding: utf-8 -*-
"""
SuperPicky - 结果浏览器缩略图网格
ThumbnailGrid: 网格视图 + 异步缩略图加载
ThumbnailCard: 单张照片卡片（评分角标 + 对焦指示点）
ThumbnailLoader: QThread 后台加载缩略图
"""

import os
from collections import OrderedDict
from typing import Optional

from PySide6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QFrame,
    QVBoxLayout, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, Slot, QSize, QTimer, QPoint
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


# ============================================================
#  ThumbnailLoader — 后台异步加载
# ============================================================

class _LoaderSignals(QObject):
    thumbnail_ready = Signal(str, object)   # filename, QImage
    load_error = Signal(str)


class ThumbnailLoader(QThread):
    """
    后台线程，按需加载缩略图 QPixmap。

    优先级：debug_crop_path > temp_jpeg_path > 原始 JPG
    """

    def __init__(self, tasks: list, thumb_size: int, parent=None):
        """
        Args:
            tasks: list of photo dicts (含 filename, debug_crop_path, temp_jpeg_path, original_path)
            thumb_size: 输出正方形尺寸（像素）
        """
        super().__init__(parent)
        self._tasks = tasks
        self._thumb_size = thumb_size
        self.signals = _LoaderSignals()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        size = QSize(self._thumb_size, self._thumb_size)

        for photo in self._tasks:
            if self._cancelled:
                break

            filename = photo.get("filename", "")

            # 先查缓存
            cached = _thumb_cache.get(filename)
            if cached is not None:
                self.signals.thumbnail_ready.emit(filename, cached)
                continue

            pixmap = self._load_pixmap(photo)
            if pixmap and not pixmap.isNull():
                # 等比缩放后居中裁切
                pixmap = pixmap.scaled(
                    size,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation
                )
                # 居中裁切到正方形
                if pixmap.width() > self._thumb_size or pixmap.height() > self._thumb_size:
                    x = (pixmap.width() - self._thumb_size) // 2
                    y = (pixmap.height() - self._thumb_size) // 2
                    pixmap = pixmap.copy(x, y, self._thumb_size, self._thumb_size)

                # V4.2 OPT: 后台绘制静态叠加层 (评分 + 对焦状态 + burst)
                self._draw_static_overlays(pixmap, photo)

                _thumb_cache.put(filename, pixmap)
                self.signals.thumbnail_ready.emit(filename, pixmap)
            else:
                self.signals.thumbnail_ready.emit(filename, QImage())

    def _draw_static_overlays(self, image: QImage, photo: dict):
        """在 QImage 上预先绘制静态叠加层（评分、对焦状态等），减轻 UI 线程负担。"""
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

    def _load_pixmap(self, photo: dict) -> Optional[QImage]:
        """按优先级查找可用图片文件并加载到 QImage。"""
        candidates = []

        # 1. yolo_debug_path（全图 + 检测框，构图感更好）
        ydp = photo.get("yolo_debug_path")
        if ydp and os.path.exists(ydp):
            candidates.append(ydp)

        # 2. temp_jpeg_path（全图 JPEG 预览）
        tjp = photo.get("temp_jpeg_path")
        if tjp and os.path.exists(tjp):
            candidates.append(tjp)

        # 3. debug_crop_path（裁切图，备用）
        dcp = photo.get("debug_crop_path")
        if dcp and os.path.exists(dcp):
            candidates.append(dcp)

        # 4. original_path（直接找原始 JPG）
        op = photo.get("original_path") or photo.get("current_path")
        if op and os.path.exists(op):
            ext = os.path.splitext(op)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                candidates.append(op)

        for path in candidates:
            img = QImage(path)
            if not img.isNull():
                return img

        return None


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

    def __init__(self, photo: dict, thumb_size: int = _DEFAULT_THUMB_SIZE, parent=None):
        super().__init__(parent)
        self.photo = photo
        self._thumb_size = thumb_size
        self._selected = False
        self._multi_selected_state = False
        self._raw_image: Optional[QImage] = None  # 存储带静态叠加层的 QImage
        self._final_pixmap: Optional[QPixmap] = None # 最终显示的 QPixmap

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

    def _draw_overlays(self):
        """在 img_label 的 pixmap 上绘制动态叠加层（选中边框、多选勾选）。"""
        if self._final_pixmap is None or self._final_pixmap.isNull():
            return

        # 始终从 raw_image 转换后的 pixmap 开始，避免反复叠加
        overlay = QPixmap(self._final_pixmap)
        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 多选勾选标记
        if getattr(self, '_multi_selected_state', False):
            # 蓝色勾选圆圈
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(59, 130, 246, 220))  # 蓝色
            painter.drawEllipse(4, 4, 20, 20)
            painter.setPen(QPen(QColor(255, 255, 255, 255), 2.0))
            painter.setBrush(Qt.NoBrush)
            # 绘制 √ 符号（简化为折线）
            painter.drawLine(8, 14, 11, 18)
            painter.drawLine(11, 18, 18, 9)

        # 选中状态：2px 实线青绿框
        if getattr(self, '_selected', False):
            pen = QPen(QColor(COLORS['accent']))  # #00d4aa
            pen.setWidth(2)
            pen.setStyle(Qt.SolidLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(1, 1, overlay.width() - 2, overlay.height() - 2)

        painter.end()
        self.img_label.setPixmap(overlay)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._draw_overlays()  # 从原始 pixmap 重绘，含/不含虚线框

    def _normal_style(self):
        return f"""
            QFrame {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
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
    """
    photo_selected = Signal(dict)
    photo_double_clicked = Signal(dict)
    multi_selection_changed = Signal(list)   # C3 多选信号

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self._thumb_size = _DEFAULT_THUMB_SIZE
        self._photos: list = []
        self._cards: dict = {}         # filename -> ThumbnailCard
        self._selected_filename: str = ""
        self._loader: Optional[ThumbnailLoader] = None
        # C3 多选状态
        self._multi_selected: set = set()       # filename 集合
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

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def load_photos(self, photos: list):
        """加载照片列表并重建网格。延迟 50ms 构建以等布局稳定，避免列数跳变。"""
        # 取消上一个加载任务
        if self._loader and self._loader.isRunning():
            self._loader.cancel()
            self._loader.wait(500)

        # 清空缩略图缓存
        _thumb_cache.clear()

        # 彻底重置状态
        self._cards.clear()
        self._selected_filename = ""
        self._multi_selected.clear()
        self._last_clicked_idx = -1
        self._anchor_photo = None

        # 移除旧卡片的清理逻辑到 _deferred_build 中执行，减少 load_photos 瞬间的白屏/空屏感
        
        # 移除加载提示，改为渐进式渲染
        self._pending_photos = photos
        self._build_timer.start()

    def _deferred_build(self):
        """延迟构建网格开始（布局稳定后执行）。"""
        photos = self._pending_photos
        if photos is None:
            return
        self._pending_photos = None
        self._photos = photos

        # 1. 计算列数
        self._current_col_count = max(1, (self.width() - 32) // (self._thumb_size + 8))
        
        # 2. 彻底清空网格
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)
            self._grid.setRowMinimumHeight(r, 0)

        # 3. 准备开始分批
        self._batch_timer.stop()
        self._current_batch_idx = 0
        
        # 4. 【关键优化】立即渲染第一批，避免跳转
        self._build_batch() 

        # 5. 启动后续分批
        if self._current_batch_idx < len(self._photos):
            self._batch_timer.start(5)

    def _build_batch(self):
        """分批构建卡片，防止 UI 假死。"""
        if not self._photos:
            self._batch_timer.stop()
            return

        batch_size = 60  # 稍微加大批次，平衡响应速度与首屏加载速度
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
            filename = photo.get("filename", "")
            self._cards[filename] = card
            self._grid.addWidget(card, row, col)
            
            # 强制设置行最小高度，确保布满高度，防止 squashing
            self._grid.setRowMinimumHeight(row, self._thumb_size + 32)

            # 如果该照片在缓存中已有 Ready 的 QImage，顺便刷上（避免等 Loader）
            cached = _thumb_cache.get(filename)
            if cached:
                card.set_pixmap(cached)

        self._current_batch_idx = end_idx

        if self._current_batch_idx >= total:
            self._batch_timer.stop()
            # 补一个尾部弹簧，将所有行推到顶部
            self._grid.setRowStretch(last_row + 1, 1)

            # 启动异步 Loader（处理未命中的图片）
            if self._loader and self._loader.isRunning():
                self._loader.cancel()
                self._loader.wait(500)
            self._loader = ThumbnailLoader(self._photos, self._thumb_size, self)
            self._loader.signals.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._loader.start()

    def set_thumb_size(self, size: int):
        """调整缩略图尺寸并重新加载。"""
        if size != self._thumb_size:
            self._thumb_size = size
            self.load_photos(self._photos)

    def get_multi_selected_photos(self) -> list:
        """返回对比视图所需的照片对（最多2张）。

        逻辑：
        - 若 _multi_selected 有 ≥2 张 → 取前2张
        - 若 _multi_selected 有 1 张 + _anchor_photo 存在且不在 multi_selected
          → 返回 [anchor, ctrl选中的那张]，实现"当前选中 + 再选一张"
        - 否则返回 _multi_selected 中的所有照片
        """
        in_multi = [p for p in self._photos if p.get("filename", "") in self._multi_selected]
        if len(in_multi) == 1 and self._anchor_photo:
            anchor_fn = self._anchor_photo.get("filename", "")
            if anchor_fn not in self._multi_selected:
                return [self._anchor_photo] + in_multi
        return in_multi

    def clear_multi_select(self):
        """公共接口：清空多选状态（ESC 快捷键或取消对比时使用）。"""
        self._clear_multi_selection()
        self._anchor_photo = None
        self._emit_multi_selection()

    def refresh_photo(self, filename: str, new_rating: int):
        """更新指定照片的评分角标（不重新加载缩略图）。"""
        card = self._cards.get(filename)
        if card:
            card.photo["rating"] = new_rating
            card._draw_overlays()

    def remove_photo(self, filename: str):
        """从网格中移除指定缩略图卡片（不重新加载全部数据）。"""
        card = self._cards.pop(filename, None)
        if card:
            self._grid.removeWidget(card)
            card.deleteLater()
        self._photos = [p for p in self._photos if p.get("filename", "") != filename]
        self._multi_selected.discard(filename)
        if self._selected_filename == filename:
            self._selected_filename = ""

    def select_photo(self, filename: str):
        """高亮选中指定文件名的卡片。"""
        if self._selected_filename and self._selected_filename in self._cards:
            self._cards[self._selected_filename].set_selected(False)
        self._selected_filename = filename
        if filename in self._cards:
            self._cards[filename].set_selected(True)
            # 滚动到可见区域
            card = self._cards[filename]
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
        filenames = [p.get("filename", "") for p in self._photos]
        try:
            idx = filenames.index(self._selected_filename)
        except ValueError:
            idx = -1
        new_idx = idx + delta
        if 0 <= new_idx < len(self._photos):
            photo = self._photos[new_idx]
            self.select_photo(photo.get("filename", ""))
            self.photo_selected.emit(photo)
            return photo
        return None

    @Slot(str, object)
    def _on_thumbnail_ready(self, filename: str, image: QImage):
        card = self._cards.get(filename)
        if card:
            card.set_pixmap(image)

    def _on_card_clicked(self, photo: dict):
        """处理卡片点击，支持 Ctrl/Shift 多选（C3）。"""
        from PySide6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        filename = photo.get("filename", "")
        filenames = [p.get("filename", "") for p in self._photos]
        try:
            clicked_idx = filenames.index(filename)
        except ValueError:
            clicked_idx = -1

        if modifiers & Qt.ControlModifier:
            # Ctrl+点击：切换该照片的多选状态
            if filename in self._multi_selected:
                self._multi_selected.discard(filename)
                card = self._cards.get(filename)
                if card:
                    card.set_multi_selected(False)
            else:
                self._multi_selected.add(filename)
                card = self._cards.get(filename)
                if card:
                    card.set_multi_selected(True)
            self._last_clicked_idx = clicked_idx
            self._emit_multi_selection()
        elif modifiers & Qt.ShiftModifier and self._last_clicked_idx >= 0 and clicked_idx >= 0:
            # Shift+点击：范围选中
            lo = min(self._last_clicked_idx, clicked_idx)
            hi = max(self._last_clicked_idx, clicked_idx)
            for i in range(lo, hi + 1):
                fn = filenames[i]
                self._multi_selected.add(fn)
                card = self._cards.get(fn)
                if card:
                    card.set_multi_selected(True)
            self._emit_multi_selection()
        else:
            # 普通点击：清空多选，单选当前，更新 anchor
            self._clear_multi_selection()
            self._anchor_photo = photo
            self._last_clicked_idx = clicked_idx
            self.select_photo(filename)
            self.photo_selected.emit(photo)
            self._emit_multi_selection()   # 让 compare 按钮隐藏

    def _clear_multi_selection(self):
        """清空所有多选状态。"""
        for fn in list(self._multi_selected):
            card = self._cards.get(fn)
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
                self._pending_photos = self._photos
                self._build_timer.start()
