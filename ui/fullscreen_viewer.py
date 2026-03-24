# -*- coding: utf-8 -*-
"""
SuperPicky - 全屏图片查看器
FullscreenViewer: 全屏大图 + 焦点叠加指示
_FullscreenImageLabel: 支持滚轮缩放 + paintEvent 绘制焦点圆圈/十字
"""

import os
import threading as _threading
from collections import OrderedDict
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, Slot, QEvent
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QBrush

from ui.styles import COLORS, FONTS


# 焦点状态颜色映射
_FOCUS_COLORS = {
    "BEST":  QColor(COLORS['focus_best']),   # 绿 — 精焦
    "GOOD":  QColor(COLORS['focus_good']),   # 琥珀 — 合焦
    "BAD":   QColor("#ffcc00"),              # 黄 — 失焦
    "WORST": QColor("#999999"),              # 灰 — 焦点在鸟外
}


# ============================================================
#  高清图 LRU 缓存（模块级，21 slots，键为绝对路径）
# ============================================================


class _HdCache:
    """
    高清图片 LRU 缓存，键为文件绝对路径字符串。
    存储 QImage（线程安全），主线程读取时转换为 QPixmap。
    """

    def __init__(self, maxsize: int = 21):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = _threading.Lock()

    def get(self, key: str) -> Optional[QImage]:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def put(self, key: str, value: QImage):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)


_hd_cache = _HdCache(21)


# ============================================================
#  预加载工作线程
# ============================================================


class _PreloadWorker(QThread):
    """
    按优先级顺序预加载高清图片到 _hd_cache。
    调用 restart(paths) 可安全地重置任务列表并重新开始。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths: list = []
        self._cancelled: bool = False
        self._pending_restart: bool = False
        self._lock = _threading.Lock()
        self.finished.connect(self._check_restart)

    def restart(self, paths: list):
        """设置新的预加载路径列表，非阻塞地（重）启动。"""
        with self._lock:
            self._paths = list(paths)
        if self.isRunning():
            # 通知当前 run() 尽快退出，run() 结束后由 _check_restart 接力启动
            self._cancelled = True
            self._pending_restart = True
        else:
            self._cancelled = False
            self._pending_restart = False
            self.start()

    def _check_restart(self):
        """run() 结束后检查是否有新的预加载任务等待启动（主线程执行）。"""
        if self._pending_restart:
            self._pending_restart = False
            self._cancelled = False
            self.start()

    def run(self):
        with self._lock:
            paths = list(self._paths)
        for path in paths:
            if self._cancelled:
                break
            if not path or not os.path.exists(path):
                continue
            if _hd_cache.get(path) is not None:
                continue        # 已在缓存，跳过
            # QImage 可在工作线程安全使用；QPixmap 须在主线程转换
            img = QImage(path)
            if not img.isNull() and not self._cancelled:
                _hd_cache.put(path, img)


# ============================================================
#  后台异步图片加载器（复用 detail_panel 的实现思路）
# ============================================================

class _ImageLoader(QThread):
    """后台线程加载 QImage，避免主线程 QPixmap 线程安全问题。"""
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
            img = QImage(self._path)
            if not self._cancelled:
                self.ready.emit(img)
        else:
            if not self._cancelled:
                self.ready.emit(QImage())


# ============================================================
#  _FullscreenImageLabel — 图片显示 + 焦点叠加 + 滚轮缩放
# ============================================================

class _FullscreenImageLabel(QLabel):
    """
    全屏图片标签。
    - 单击（适配模式）→ 以鼠标位置为中心缩放到 100%
    - 单击（缩放模式）→ 返回适配模式
    - 拖拽（缩放模式）→ 平移图片
    - 滚轮（任意模式）→ 以鼠标为中心缩放 10%~500%
    - 触控板双指捐合 → 缩放（macOS NativeGesture）
    - toggle_focus()  → 切换焦点叠加显示/隐藏
    - 右键 → 发出 right_clicked 信号（带全局坐标）
    """

    right_clicked = Signal(object)  # QPoint（全局坐标）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._focus_x: Optional[float] = None
        self._focus_y: Optional[float] = None
        self._focus_status: Optional[str] = None
        self._focus_visible: bool = True  # 默认显示焦点叠加

        # 缩放/平移状态
        self._fit_mode: bool = True
        self._draw_ox: float = 0.0       # 图片左上角 x（label 坐标）
        self._draw_oy: float = 0.0       # 图片左上角 y（label 坐标）
        self._display_scale: float = 1.0

        # 丝滑缩放：目标值 + 动画插值
        self._target_scale: float = 1.0
        self._target_ox: float = 0.0
        self._target_oy: float = 0.0
        self._last_wheel_mx: float = -1.0  # 上次滚轮的鼠标 x（zoom hint 跟踪用）
        self._last_wheel_my: float = -1.0

        # 拖拽状态
        self._drag_active: bool = False
        self._drag_start_x: float = 0.0
        self._drag_start_y: float = 0.0
        self._drag_ox_start: float = 0.0
        self._drag_oy_start: float = 0.0

        # 双击吸收标志（防止第二次 release 误触发 click 逻辑）
        self._double_click_pending: bool = False

        # 对比视图同步（C5）：_sync_peer 为另一侧 label，_syncing 防止回环
        self._sync_peer: Optional['_FullscreenImageLabel'] = None
        self._syncing: bool = False

        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(200, 200)
        self.setStyleSheet(f"background-color: {COLORS['bg_void']};")
        self.setCursor(Qt.CrossCursor)

        # 功能3：缩放比例提示标签（悬浮胶囊，1.5s 后自动隐藏）
        self._zoom_hint = QLabel(self)
        self._zoom_hint.setAlignment(Qt.AlignCenter)
        self._zoom_hint.setStyleSheet("""
            QLabel {
                background-color: rgba(30, 30, 30, 200);
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                border-radius: 14px;
                padding: 4px 14px;
            }
        """)
        self._zoom_hint.setFixedSize(76, 28)
        self._zoom_hint.hide()

        self._zoom_hint_timer = QTimer(self)
        self._zoom_hint_timer.setSingleShot(True)
        self._zoom_hint_timer.setInterval(1500)
        self._zoom_hint_timer.timeout.connect(self._zoom_hint.hide)

        # 丝滑缩放动画定时器（~60fps）
        self._zoom_anim_timer = QTimer(self)
        self._zoom_anim_timer.setInterval(16)
        self._zoom_anim_timer.timeout.connect(self._zoom_anim_step)

    # ── 公共接口 ────────────────────────────────────────────

    def set_pixmap(self, pixmap_or_image):
        """设置图片（可以是 QPixmap 或 QImage），重置为适配模式。"""
        if isinstance(pixmap_or_image, QImage):
            self._pixmap = QPixmap.fromImage(pixmap_or_image)
        else:
            self._pixmap = pixmap_or_image
        self._fit_mode = True
        self._drag_active = False
        self._zoom_anim_timer.stop()  # 停止上一张图的动画
        self.setCursor(Qt.CrossCursor)
        self.update()

    def set_zoom_at(self, scale: float, mx: float, my: float):
        """以屏幕坐标 (mx, my) 为中心，设置指定缩放比例（带动画过渡）。"""
        if self._pixmap is None or self._pixmap.isNull():
            return
        self._ensure_manual_state()
        img_px = (mx - self._draw_ox) / max(self._display_scale, 1e-10)
        img_py = (my - self._draw_oy) / max(self._display_scale, 1e-10)
        self._target_scale = max(0.1, min(2.0, scale))
        self._target_ox = mx - img_px * self._target_scale
        self._target_oy = my - img_py * self._target_scale
        self._fit_mode = False
        self.setCursor(Qt.OpenHandCursor)
        self._last_wheel_mx = mx
        self._last_wheel_my = my
        if not self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.start()
        self._show_zoom_hint(self._target_scale, mx, my)

    def restore_zoom(self, scale: float, ox: float, oy: float):
        """功能2：直接还原缩放比例和平移位置，不重新以鼠标点计算。
        用于锁定缩放换图后精确恢复画面状态。
        """
        if self._pixmap is None or self._pixmap.isNull():
            return
        self._zoom_anim_timer.stop()  # 停止动画，瞬间恢复
        self._display_scale = scale
        self._draw_ox = ox
        self._draw_oy = oy
        self._target_scale = scale     # 同步 target 防止残留动画
        self._target_ox = ox
        self._target_oy = oy
        self._fit_mode = False
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        self._show_zoom_hint(scale)

    def set_focus(self, focus_x: Optional[float], focus_y: Optional[float],
                  focus_status: Optional[str]):
        """设置焦点坐标（归一化 0.0~1.0）和状态。"""
        self._focus_x = focus_x
        self._focus_y = focus_y
        self._focus_status = focus_status
        self.update()

    def toggle_focus(self):
        """切换焦点叠加显示/隐藏（同步作用于 peer）。"""
        self._focus_visible = not self._focus_visible
        self.update()
        if self._sync_peer and not self._syncing:
            self._sync_peer._focus_visible = self._focus_visible
            self._sync_peer.update()

    def toggle_zoom(self):
        """Z 键：在 fit（适配）和 100% 之间切换。"""
        if self._fit_mode:
            # fit → 100%，以当前视口中心为准
            self._zoom_to_100(self.width() / 2, self.height() / 2)
        else:
            # 100% → fit
            self._fit_mode = True
            self.setCursor(Qt.CrossCursor)
            self.update()
            self._emit_transform_sync()

    @property
    def focus_visible(self) -> bool:
        return self._focus_visible

    # ── 对比视图同步接口（C5）─────────────────────────────────

    def set_sync_peer(self, peer: Optional['_FullscreenImageLabel']):
        """设置对比视图的另一侧 label 为同步 peer。"""
        self._sync_peer = peer

    def _emit_transform_sync(self):
        """将当前 transform 同步给 peer（以归一化坐标传递，适应不同分辨率）。"""
        if self._syncing or self._sync_peer is None:
            return
        if self._pixmap is None or self._pixmap.isNull():
            return
        if self._fit_mode:
            self._sync_peer._apply_sync(-1.0, 0.0, 0.0, True)
            return
        fit_scale, _, _ = self._get_fit_transform()
        img_w = self._pixmap.width()
        img_h = self._pixmap.height()
        scale_ratio = self._display_scale / max(fit_scale, 1e-10)
        # 视口中心在图片坐标系中的归一化位置
        norm_cx = (self.width() / 2 - self._draw_ox) / max(img_w * self._display_scale, 1)
        norm_cy = (self.height() / 2 - self._draw_oy) / max(img_h * self._display_scale, 1)
        self._sync_peer._apply_sync(scale_ratio, norm_cx, norm_cy, False)

    def _apply_sync(self, scale_ratio: float, norm_cx: float, norm_cy: float, is_fit: bool):
        """接收来自 peer 的 transform 并应用（不回传，防止死循环）。"""
        self._syncing = True
        try:
            if is_fit:
                self._fit_mode = True
                self.setCursor(Qt.CrossCursor)
                self.update()
                return
            if self._pixmap is None or self._pixmap.isNull():
                return
            fit_scale, _, _ = self._get_fit_transform()
            img_w = self._pixmap.width()
            img_h = self._pixmap.height()
            self._display_scale = fit_scale * scale_ratio
            self._draw_ox = self.width() / 2 - norm_cx * img_w * self._display_scale
            self._draw_oy = self.height() / 2 - norm_cy * img_h * self._display_scale
            self._fit_mode = False
            self.setCursor(Qt.OpenHandCursor)
            self.update()
        finally:
            self._syncing = False

    # ── 内部辅助 ─────────────────────────────────────────────

    def _get_fit_transform(self):
        """计算适配模式下的 (scale, ox, oy)，不修改状态。"""
        if self._pixmap is None or self._pixmap.isNull():
            return 1.0, 0.0, 0.0
        img_w = self._pixmap.width()
        img_h = self._pixmap.height()
        label_w = self.width() or 1
        label_h = self.height() or 1
        if img_w == 0 or img_h == 0:
            return 1.0, 0.0, 0.0
        scale = min(label_w / img_w, label_h / img_h)
        ox = (label_w - img_w * scale) / 2.0
        oy = (label_h - img_h * scale) / 2.0
        return scale, ox, oy

    def _ensure_manual_state(self):
        """
        若当前在 fit_mode，将 _draw_ox/_oy/_display_scale 同步为当前适配值，
        以便后续 wheel/click 事件可直接使用这些字段做坐标变换。
        """
        if self._fit_mode:
            scale, ox, oy = self._get_fit_transform()
            self._display_scale = scale
            self._draw_ox = ox
            self._draw_oy = oy

    def _zoom_to_100(self, mx: float, my: float):
        """以屏幕坐标 (mx, my) 为中心，切换到 100% 缩放。"""
        self._ensure_manual_state()
        # 计算鼠标下方的图片像素坐标
        img_px = (mx - self._draw_ox) / self._display_scale
        img_py = (my - self._draw_oy) / self._display_scale
        # 缩放到 100%，使 img_px 保持在 mx 位置
        self._draw_ox = mx - img_px * 1.0
        self._draw_oy = my - img_py * 1.0
        self._display_scale = 1.0
        self._fit_mode = False
        self.setCursor(Qt.OpenHandCursor)
        self.update()
        self._emit_transform_sync()

    def _draw_focus_overlay(self, painter: QPainter, fx: float, fy: float):
        """相机取景器风格 AF 方块：精焦绿 / 合焦红 / 失焦白。"""
        color = QColor(_FOCUS_COLORS[self._focus_status])
        color.setAlpha(220)

        half = 26   # 方块半边长（屏幕像素）
        arm = 10    # 角臂长度
        x, y = int(fx), int(fy)

        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setStyle(Qt.SolidLine)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        # 四个角 L 形，组成方框轮廓
        for sx, sy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            cx = x + sx * half
            cy = y + sy * half
            painter.drawLine(cx, cy, cx - sx * arm, cy)   # 横臂（向内）
            painter.drawLine(cx, cy, cx, cy - sy * arm)   # 竖臂（向内）

        # 中心实心圆点，标记精确焦点位置
        dot_r = 3
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(x - dot_r, y - dot_r, dot_r * 2, dot_r * 2)

    # ── Qt 事件重写 ──────────────────────────────────────────

    def paintEvent(self, event):
        if self._pixmap is None or self._pixmap.isNull():
            super().paintEvent(event)
            return

        img_w = self._pixmap.width()
        img_h = self._pixmap.height()
        if img_w == 0 or img_h == 0:
            super().paintEvent(event)
            return

        # 适配模式：每帧重算坐标（支持窗口 resize）
        if self._fit_mode:
            scale, ox, oy = self._get_fit_transform()
            self._display_scale = scale
            self._draw_ox = ox
            self._draw_oy = oy
        else:
            scale = self._display_scale
            ox = self._draw_ox
            oy = self._draw_oy

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self._pixmap is None:
            painter.end()
            return

        # 方案C：用 painter transform 绘制，让 Qt/GPU 做缩放
        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)
        # 确保绘制的是 QPixmap；如果 self._pixmap 依然是 QImage（防御性检查），转为 QPixmap
        pix = self._pixmap
        if isinstance(pix, QImage):
            pix = QPixmap.fromImage(pix)
        painter.drawPixmap(0, 0, pix)
        painter.restore()

        # 焦点叠加（仅在可见且坐标/状态有效时绘制）
        if (self._focus_visible
                and self._focus_x is not None
                and self._focus_y is not None
                and self._focus_status in _FOCUS_COLORS):
            fx_s = ox + self._focus_x * img_w * scale
            fy_s = oy + self._focus_y * img_h * scale
            self._draw_focus_overlay(painter, fx_s, fy_s)

        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 适配模式下窗口 resize → 重绘（paintEvent 自动重算）
        self.update()
        # 功能3：重新定位缩放提示标签
        if not self._zoom_hint.isHidden():
            hw = self._zoom_hint.width()
            hh = self._zoom_hint.height()
            x = (self.width() - hw) // 2
            y = self.height() - hh - 20
            self._zoom_hint.move(x, max(0, y))

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(event.globalPosition().toPoint())
            return
        if event.button() == Qt.LeftButton:
            pos = event.position()
            self._drag_start_x = pos.x()
            self._drag_start_y = pos.y()
            self._drag_ox_start = self._draw_ox
            self._drag_oy_start = self._draw_oy
            self._drag_active = False
            if not self._fit_mode:
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self._fit_mode:
            pos = event.position()
            dx = pos.x() - self._drag_start_x
            dy = pos.y() - self._drag_start_y
            if not self._drag_active and (abs(dx) > 3 or abs(dy) > 3):
                self._drag_active = True
            if self._drag_active:
                self._draw_ox = self._drag_ox_start + dx
                self._draw_oy = self._drag_oy_start + dy
                self.update()
                self._emit_transform_sync()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 双击第二次 release：吸收，不触发 click 逻辑
            if self._double_click_pending:
                self._double_click_pending = False
                self._drag_active = False
                self.setCursor(Qt.OpenHandCursor if not self._fit_mode else Qt.CrossCursor)
                super().mouseReleaseEvent(event)
                return

            if not self._drag_active:
                # 纯点击（无拖拽移动）
                pos = event.position()
                mx, my = pos.x(), pos.y()
                if self._fit_mode:
                    self._zoom_to_100(mx, my)
                else:
                    # 回到适配模式
                    self._fit_mode = True
                    self.setCursor(Qt.CrossCursor)
                    self.update()
                    self._emit_transform_sync()
            else:
                # 拖拽结束，恢复张开手光标
                if not self._fit_mode:
                    self.setCursor(Qt.OpenHandCursor)
            self._drag_active = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """标记双击，防止第二次 release 误触发 click 逻辑。"""
        if event.button() == Qt.LeftButton:
            self._double_click_pending = True
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if self._pixmap is None or self._pixmap.isNull():
            return
        # 同步手动状态（fit_mode 下先获取当前适配值）
        self._ensure_manual_state()

        pos = event.position()
        mx, my = pos.x(), pos.y()
        self._last_wheel_mx = mx
        self._last_wheel_my = my

        # 方案B：区分触控板 vs 鼠标滚轮
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()
        if pixel_delta != 0:
            # 触控板：按像素距离做连续缩放（跟手感）
            factor = 1.0 + pixel_delta * 0.002
        elif angle_delta != 0:
            # 鼠标滚轮：6% 步进（比原来 15% 更细腻）
            factor = 1.06 if angle_delta > 0 else 1.0 / 1.06
        else:
            return

        # 使用当前目标值（而非实际值）计算，支持快速连续滚轮累积
        base_scale = self._target_scale if self._zoom_anim_timer.isActive() else self._display_scale
        base_ox = self._target_ox if self._zoom_anim_timer.isActive() else self._draw_ox
        base_oy = self._target_oy if self._zoom_anim_timer.isActive() else self._draw_oy

        # 鼠标下方的图片像素坐标（基于目标值）
        img_px = (mx - base_ox) / max(base_scale, 1e-10)
        img_py = (my - base_oy) / max(base_scale, 1e-10)

        new_scale = max(0.1, min(2.0, base_scale * factor))

        # 方案A：设置目标值，启动动画插值
        self._target_scale = new_scale
        self._target_ox = mx - img_px * new_scale
        self._target_oy = my - img_py * new_scale
        self._fit_mode = False
        self.setCursor(Qt.OpenHandCursor)
        if not self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.start()
        # 功能3：显示缩放比例提示（跟随鼠标，显示目标值）
        self._show_zoom_hint(new_scale, mx, my)

    def event(self, ev):
        """拦截 macOS 触控板双指捐合缩放（QNativeGestureEvent）。"""
        if ev.type() == QEvent.NativeGesture:
            try:
                from PySide6.QtCore import Qt as _Qt
                # ZoomNativeGesture = 4
                if ev.gestureType() == _Qt.ZoomNativeGesture:
                    if self._pixmap is None or self._pixmap.isNull():
                        return True
                    self._ensure_manual_state()
                    pos = ev.position()
                    mx, my = pos.x(), pos.y()
                    self._last_wheel_mx = mx
                    self._last_wheel_my = my
                    # ev.value() 是增量缩放因子，如 0.02 = 放大 2%
                    factor = 1.0 + ev.value()
                    base_scale = self._target_scale if self._zoom_anim_timer.isActive() else self._display_scale
                    base_ox = self._target_ox if self._zoom_anim_timer.isActive() else self._draw_ox
                    base_oy = self._target_oy if self._zoom_anim_timer.isActive() else self._draw_oy
                    img_px = (mx - base_ox) / max(base_scale, 1e-10)
                    img_py = (my - base_oy) / max(base_scale, 1e-10)
                    new_scale = max(0.1, min(2.0, base_scale * factor))
                    self._target_scale = new_scale
                    self._target_ox = mx - img_px * new_scale
                    self._target_oy = my - img_py * new_scale
                    self._fit_mode = False
                    self.setCursor(Qt.OpenHandCursor)
                    if not self._zoom_anim_timer.isActive():
                        self._zoom_anim_timer.start()
                    self._show_zoom_hint(new_scale, mx, my)
                    return True
            except Exception:
                pass
        return super().event(ev)

    def _show_zoom_hint(self, scale: float, mx: float = -1.0, my: float = -1.0):
        """功能3：显示缩放比例悬浮提示，1.5s 后自动隐藏。
        mx/my 为鼠标在 label 坐标系中的位置；不传则底部居中。
        """
        pct = int(round(scale * 100))
        self._zoom_hint.setText(f"{pct}%")
        hw = self._zoom_hint.width()   # 已 setFixedSize，尺寸稳定
        hh = self._zoom_hint.height()
        if mx >= 0 and my >= 0:
            # 跟随鼠标：偏右下 16px，超出边界时翻转到鼠标左上方
            x = mx + 16
            y = my + 16
            if x + hw > self.width():
                x = mx - hw - 16
            if y + hh > self.height():
                y = my - hh - 16
        else:
            # 无鼠标坐标（如锁定缩放换图）：底部居中
            x = (self.width() - hw) // 2
            y = self.height() - hh - 20
        self._zoom_hint.move(int(x), int(max(0, y)))
        self._zoom_hint.show()
        self._zoom_hint.raise_()
        self._zoom_hint_timer.start()

    def _zoom_anim_step(self):
        """方案A：每帧将 scale/ox/oy 向目标值做 ease-out 插值。"""
        t = 0.25  # 插值系数：每帧走完剩余距离的 25%，~100ms 完成 95%
        self._display_scale += (self._target_scale - self._display_scale) * t
        self._draw_ox += (self._target_ox - self._draw_ox) * t
        self._draw_oy += (self._target_oy - self._draw_oy) * t

        # 接近目标时停止（精度 0.001 即 0.1%）
        if (abs(self._display_scale - self._target_scale) < 0.001
                and abs(self._draw_ox - self._target_ox) < 0.5
                and abs(self._draw_oy - self._target_oy) < 0.5):
            self._display_scale = self._target_scale
            self._draw_ox = self._target_ox
            self._draw_oy = self._target_oy
            self._zoom_anim_timer.stop()

        self.update()
        self._emit_transform_sync()
        # 动画过程中持续更新 zoom hint 百分比
        self._show_zoom_hint(self._display_scale, self._last_wheel_mx, self._last_wheel_my)


# ============================================================
#  FullscreenViewer — 全屏查看器主组件
# ============================================================

class FullscreenViewer(QWidget):
    """
    全屏图片查看器（嵌入 QStackedWidget 的 Page 1）。

    信号:
        close_requested()   用户请求返回 grid
        prev_requested()    用户请求上一张
        next_requested()    用户请求下一张
    """
    close_requested = Signal()
    prev_requested = Signal()
    next_requested = Signal()
    burst_sequence_requested = Signal(dict)
    delete_requested = Signal(dict)   # 功能1：携带当前 photo dict
    context_menu_requested = Signal(dict, object)   # (photo, QPoint全局坐标)

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self._loader: Optional[_ImageLoader] = None
        self._preload_worker = _PreloadWorker(self)   # 预加载工作线程
        self._photos: list = []                        # 当前完整照片列表
        self._current_photo: dict = {}                 # 当前显示的 photo dict

        # 功能2：锁定缩放状态（同时锁定平移位置）
        self._zoom_locked: bool = False
        self._locked_scale: float = 1.0
        self._locked_ox: float = 0.0   # 锁定时的图片左上角 x 偏移
        self._locked_oy: float = 0.0   # 锁定时的图片左上角 y 偏移

        self.setStyleSheet(f"background-color: {COLORS['bg_void']};")
        self.setFocusPolicy(Qt.StrongFocus)            # 允许接收键盘事件
        self._build_ui()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- 顶栏 52px ---
        top_bar = self._build_top_bar()
        layout.addWidget(top_bar)

        # --- 图片区域（stretch=1）---
        self._img_label = _FullscreenImageLabel()
        self._img_label.right_clicked.connect(self._on_img_right_clicked)
        layout.addWidget(self._img_label, 1)

        # --- 底部导航栏 44px ---
        bottom_bar = self._build_bottom_bar()
        layout.addWidget(bottom_bar)

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(26, 26, 26, 210);
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
        """)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)

        # 返回按钮
        back_btn = QPushButton(self.i18n.t("browser.back"))
        back_btn.setObjectName("secondary")
        back_btn.setFixedHeight(36)
        back_btn.setMinimumWidth(100)
        back_btn.clicked.connect(self.close_requested)
        h.addWidget(back_btn)

        # 焦点图层开关按钮
        self._focus_btn = QPushButton(self.i18n.t("browser.focus_toggle"))
        self._focus_btn.setFixedHeight(36)
        self._focus_btn.setMinimumWidth(80)
        self._focus_btn.setToolTip(self.i18n.t("browser.focus_toggle_tooltip"))
        self._focus_btn.clicked.connect(self._on_focus_btn_clicked)
        h.addWidget(self._focus_btn)
        # 初始状态：焦点开启 → active 样式
        self._update_focus_btn_style(True)

        # 功能2：锁定缩放按钮
        self._lock_zoom_btn = QPushButton("🔓 锁定缩放")
        self._lock_zoom_btn.setFixedHeight(36)
        self._lock_zoom_btn.setToolTip("开启后翻页时保持当前缩放比例，并以鼠标位置为中心")
        self._lock_zoom_btn.clicked.connect(self._toggle_zoom_lock)
        self._update_lock_zoom_btn_style(False)
        h.addWidget(self._lock_zoom_btn)

        h.addStretch()
        self._burst_info_btn = QPushButton("")
        self._burst_info_btn.setFixedHeight(30)
        self._burst_info_btn.hide()
        self._burst_info_btn.clicked.connect(self._on_burst_info_clicked)
        h.addWidget(self._burst_info_btn)

        # 文件名标签
        self._filename_label = QLabel("")
        self._filename_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_primary']};
                font-size: 13px;
                font-family: {FONTS['mono']};
                background: transparent;
            }}
        """)
        self._filename_label.setAlignment(Qt.AlignCenter)
        h.addWidget(self._filename_label)

        # 评分标签
        self._rating_label = QLabel("")
        self._rating_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['star_gold']};
                font-size: 16px;
                background: transparent;
                min-width: 60px;
            }}
        """)
        self._rating_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(self._rating_label)

        # 功能1：删除按钮（红色危险样式）
        self._delete_btn = QPushButton("🗑 删除")
        self._delete_btn.setFixedHeight(36)
        self._delete_btn.setToolTip("删除当前图片（移入回收站）")
        self._delete_btn.setStyleSheet(
            f"QPushButton {{ background-color: #3a1a1a;"
            f" border: 1px solid #cc3333;"
            f" border-radius: 6px;"
            f" color: #ff6666;"
            f" font-size: 12px;"
            f" padding: 2px 12px; }}"
            f"QPushButton:hover {{ background-color: #cc3333; color: #ffffff; }}"
        )
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        h.addWidget(self._delete_btn)

        return bar

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f"""
            QWidget {{
                background-color: rgba(26, 26, 26, 210);
                border-top: 1px solid {COLORS['border_subtle']};
            }}
        """)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)

        h.addStretch()

        _nav_btn_style = (
            f"QPushButton {{ background-color: {COLORS['bg_card']};"
            f" border: 1px solid {COLORS['border']};"
            f" border-radius: 6px;"
            f" color: {COLORS['text_secondary']};"
            f" font-size: 12px;"
            f" padding: 2px 10px; }}"
        )

        prev_btn = QPushButton(self.i18n.t("browser.prev_arrow"))
        prev_btn.setFixedHeight(32)
        prev_btn.setFixedWidth(100)
        prev_btn.setStyleSheet(_nav_btn_style)
        prev_btn.clicked.connect(self.prev_requested)
        h.addWidget(prev_btn)

        next_btn = QPushButton(self.i18n.t("browser.next_arrow"))
        next_btn.setFixedHeight(32)
        next_btn.setFixedWidth(100)
        next_btn.setStyleSheet(_nav_btn_style)
        next_btn.clicked.connect(self.next_requested)
        h.addWidget(next_btn)

        h.addStretch()

        return bar

    # ------------------------------------------------------------------
    #  焦点图层控制
    # ------------------------------------------------------------------

    def toggle_focus(self):
        """切换焦点叠加（供外部 F 键调用 + 内部按钮调用）。"""
        self._img_label.toggle_focus()
        self._update_focus_btn_style(self._img_label.focus_visible)

    def _on_focus_btn_clicked(self):
        self.toggle_focus()

    def _on_img_right_clicked(self, global_pos):
        """大图右键 → 冒泡右键菜单信号给父组件。"""
        if self._current_photo:
            self.context_menu_requested.emit(self._current_photo, global_pos)

    def _on_burst_info_clicked(self):
        if self._current_photo and self._burst_info_btn.isEnabled():
            self.burst_sequence_requested.emit(self._current_photo)

    # 功能2：锁定缩放
    def _toggle_zoom_lock(self):
        """切换锁定缩放开/关。"""
        self._zoom_locked = not self._zoom_locked
        self._update_lock_zoom_btn_style(self._zoom_locked)
        if self._zoom_locked:
            # 记录当前缩放比例和平移位置
            lbl = self._img_label
            lbl._ensure_manual_state()   # 若在 fit_mode 先同步状态
            self._locked_scale = lbl._display_scale
            self._locked_ox = lbl._draw_ox
            self._locked_oy = lbl._draw_oy

    def _update_lock_zoom_btn_style(self, locked: bool):
        if locked:
            self._lock_zoom_btn.setText("🔒 锁定缩放")
            self._lock_zoom_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_input']};"
                f" border: 1px solid {COLORS['accent']};"
                f" border-radius: 6px;"
                f" color: {COLORS['accent']};"
                f" font-size: 12px;"
                f" padding: 2px 10px; }}"
            )
        else:
            self._lock_zoom_btn.setText("🔓 锁定缩放")
            self._lock_zoom_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_card']};"
                f" border: 1px solid {COLORS['border']};"
                f" border-radius: 6px;"
                f" color: {COLORS['text_secondary']};"
                f" font-size: 12px;"
                f" padding: 2px 10px; }}"
            )

    # 功能1：删除按钮点击
    def _on_delete_clicked(self):
        """发出删除信号（携带当前 photo dict），由 ResultsBrowserWindow 处理。"""
        if self._current_photo:
            self.delete_requested.emit(self._current_photo)

    def _update_focus_btn_style(self, visible: bool):
        """visible=True → accent 激活色；False → 灰色 secondary 样式。"""
        if visible:
            self._focus_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_input']};"
                f" border: 1px solid {COLORS['accent']};"
                f" border-radius: 6px;"
                f" color: {COLORS['accent']};"
                f" font-size: 12px;"
                f" padding: 2px 10px; }}"
            )
        else:
            self._focus_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_card']};"
                f" border: 1px solid {COLORS['border']};"
                f" border-radius: 6px;"
                f" color: {COLORS['text_secondary']};"
                f" font-size: 12px;"
                f" padding: 2px 10px; }}"
            )

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def set_photo_list(self, photos: list):
        """
        由 ResultsBrowserWindow 在过滤结果变化时调用，
        更新全屏查看器持有的照片列表（用于计算预加载范围）。
        """
        self._photos = photos

    def cleanup(self):
        if self._loader:
            self._loader.cancel()
            if self._loader.isRunning():
                self._loader.wait(1000)
            self._loader = None
        if self._preload_worker:
            self._preload_worker._cancelled = True
            if self._preload_worker.isRunning():
                self._preload_worker.wait(1000)

    def show_photo(self, photo: dict):
        """
        展示一张照片。流程：
        1. 更新顶栏（文件名、评分）
        2. 立即显示缩略图缓存（零延迟反馈）
        3. 设置焦点叠加坐标
        4. 优先检查高清 LRU 缓存（可能已被预加载命中）
        5. 未命中则启动 _ImageLoader 异步加载
        6. 触发 ±10 张预加载
        """
        self._current_photo = photo  # 功能1：保存当前 photo 供删除按钮使用

        # 功能2：锁定缩放 — 换图前保存当前 scale + ox/oy，换图后直接还原
        if self._zoom_locked:
            lbl = self._img_label
            lbl._ensure_manual_state()   # fit_mode 下先同步状态
            self._locked_scale = lbl._display_scale
            self._locked_ox = lbl._draw_ox
            self._locked_oy = lbl._draw_oy

        filename = os.path.basename(photo.get("current_path") or photo.get("original_path") or "") or photo.get("filename", "")
        self._filename_label.setText(filename)
        self._update_burst_info(photo)

        rating = photo.get("rating", 0)
        _rating_text = {5: "★★★★★", 4: "★★★★", 3: "★★★", 2: "★★", 1: "★"}
        self._rating_label.setText(_rating_text.get(rating, ""))

        # 1. 立即显示缩略图缓存
        try:
            from ui.thumbnail_grid import _thumb_cache
            cached = _thumb_cache.get(filename)
            if cached and not cached.isNull():
                self._img_label.set_pixmap(cached)
                # 功能2：缩略图加载后直接还原锁定的缩放和位置
                if self._zoom_locked:
                    self._img_label.restore_zoom(
                        self._locked_scale,
                        self._locked_ox,
                        self._locked_oy
                    )
        except Exception:
            pass

        # 2. 焦点叠加
        self._img_label.set_focus(
            photo.get("focus_x"),
            photo.get("focus_y"),
            photo.get("focus_status")
        )

        # 3. 取消上一个加载任务，断开信号防止旧图覆盖新显示
        if self._loader:
            self._loader.cancel()
            if self._loader.isRunning():
                self._loader.wait(100)
            try:
                self._loader.ready.disconnect()
            except RuntimeError:
                pass
            self._loader = None

        # 4. 优先检查高清缓存（存的是 QImage，需在主线程转为 QPixmap）
        hd_path = self._resolve_hd_path(photo)
        if hd_path:
            cached_img = _hd_cache.get(hd_path)
            if cached_img and not cached_img.isNull():
                px = QPixmap.fromImage(cached_img)
                self._img_label.set_pixmap(px)
                # 功能2：高清图加载后直接还原锁定的缩放和位置
                if self._zoom_locked:
                    self._img_label.restore_zoom(
                        self._locked_scale,
                        self._locked_ox,
                        self._locked_oy
                    )
            else:
                # 5. 后台加载，完成后存入高清缓存
                self._loader = _ImageLoader(hd_path, self)
                _path_capture = hd_path
                self._loader.ready.connect(
                    lambda px, p=_path_capture: self._on_image_ready(px, p)
                )
                self._loader.start()

        # 6. 触发 ±10 预加载
        self._trigger_preload(photo)

        # 确保全屏 viewer 持有键盘焦点（切换照片后维持焦点）
        self.setFocus()

    def _update_burst_info(self, photo: dict):
        burst_pos = photo.get("burst_position_index")
        burst_total = photo.get("burst_total_count")
        burst_count = photo.get("burst_count", 1)
        is_group = photo.get("is_burst_group") and burst_count > 1

        if burst_pos and burst_total:
            self._burst_info_btn.setText(f"{burst_pos}/{burst_total}")
            self._burst_info_btn.setEnabled(True)
            self._burst_info_btn.setCursor(Qt.PointingHandCursor)
            self._burst_info_btn.setToolTip("\u70b9\u51fb\u6536\u56de\u8fde\u62cd\u5e8f\u5217" if not str(getattr(self.i18n, "current_lang", "")).startswith("en") else "Click to collapse burst sequence")
            self._burst_info_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_input']};"
                f" border: 1px solid {COLORS['accent']};"
                f" border-radius: 14px;"
                f" color: {COLORS['accent']};"
                f" font-size: 12px;"
                f" font-weight: 600;"
                f" padding: 2px 12px; }}"
                f"QPushButton:hover {{ background-color: {COLORS['bg_card']};"
                f" border-color: {COLORS['accent']};"
                f" color: {COLORS['accent']}; }}"
            )
            self._burst_info_btn.show()
            return

        if is_group:
            lang = getattr(self.i18n, "current_lang", "")
            text = f"Burst Sequence ({burst_count})" if str(lang).startswith("en") else f"\u8fde\u62cd\u5e8f\u5217\uff08{burst_count}\u5f20\uff09"
            self._burst_info_btn.setText(text)
            self._burst_info_btn.setEnabled(True)
            self._burst_info_btn.setCursor(Qt.PointingHandCursor)
            self._burst_info_btn.setToolTip("")
            self._burst_info_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['bg_card']};"
                f" border: 1px solid {COLORS['border']};"
                f" border-radius: 14px;"
                f" color: {COLORS['text_secondary']};"
                f" font-size: 12px;"
                f" padding: 2px 12px; }}"
                f"QPushButton:hover {{ border-color: {COLORS['accent']};"
                f" color: {COLORS['accent']}; }}"
            )
            self._burst_info_btn.show()
            return

        self._burst_info_btn.hide()

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------

    def _resolve_hd_path(self, photo: dict) -> Optional[str]:
        """按优先级解析高清图路径：temp_jpeg_path → 原始 JPEG。
        debug_crop_path / yolo_debug_path 均不使用。
        """
        tjp = photo.get("temp_jpeg_path")
        if tjp and os.path.exists(tjp):
            return tjp
        # 回退到原始 JPEG
        op = photo.get("original_path") or photo.get("current_path")
        if op and os.path.exists(op):
            ext = os.path.splitext(op)[1].lower()
            if ext in ('.jpg', '.jpeg'):
                return op
        return None

    @Slot(object)
    def _on_image_ready(self, img: QImage, path: str = ""):
        """后台加载完成：转存进高清缓存，并更新图片显示。"""
        if not img.isNull():
            if path:
                _hd_cache.put(path, img)
            
            # 主线程中转换为 QPixmap
            px = QPixmap.fromImage(img)
            self._img_label.set_pixmap(px)
            # 功能2：后台高清图加载完成后也还原锁定的缩放和位置
            if self._zoom_locked:
                self._img_label.restore_zoom(
                    self._locked_scale,
                    self._locked_ox,
                    self._locked_oy
                )

    def _trigger_preload(self, current_photo: dict):
        """
        以 current_photo 为中心，按优先级
        0, +1, -1, +2, -2, ..., ±10 触发高清预加载。
        """
        if not self._photos:
            return
        filenames = [p.get("filename", "") for p in self._photos]
        fn = current_photo.get("filename", "")
        try:
            idx = filenames.index(fn)
        except ValueError:
            return

        n = len(self._photos)
        ordered_paths = []

        # 生成优先级偏移列表：0, +1, -1, +2, -2, ..., ±10
        offsets = [0]
        for d in range(1, 11):
            offsets.append(d)
            offsets.append(-d)

        for offset in offsets:
            i = idx + offset
            if 0 <= i < n:
                path = self._resolve_hd_path(self._photos[i])
                if path and path not in ordered_paths:
                    ordered_paths.append(path)

        self._preload_worker.restart(ordered_paths)

    # ------------------------------------------------------------------
    #  键盘事件（左右箭头导航，F 切换焦点，Escape 返回）
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        from PySide6.QtCore import Qt as _Qt
        key = event.key()
        if key in (_Qt.Key_Left, _Qt.Key_Up):
            self.prev_requested.emit()
        elif key in (_Qt.Key_Right, _Qt.Key_Down):
            self.next_requested.emit()
        elif key == _Qt.Key_F:
            self.toggle_focus()
        elif key == _Qt.Key_Z:
            self._img_label.toggle_zoom()
        elif key == _Qt.Key_Escape:
            self.close_requested.emit()
        elif key in (_Qt.Key_Delete, _Qt.Key_X):
            if self._current_photo:
                self.delete_requested.emit(self._current_photo)
        else:
            super().keyPressEvent(event)
