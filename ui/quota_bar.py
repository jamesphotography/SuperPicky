# -*- coding: utf-8 -*-
"""
QuotaBar —— V2 评星三段配额分配条(自绘控件,可复用)。

一条 0–100% 的横条被两个可拖动分隔点切成 3★ / 2★ / 1★ 三段,三段和恒为
100%。Qt 无原生双滑块,故自绘:
  - 分隔点 A 位于 3★ 与 2★ 之间(位置 = 3★%);
  - 分隔点 B 位于 2★ 与 1★ 之间(位置 = 3★% + 2★%);
  - 拖 A:3★↑↓ 与 2★ 此消彼长,1★ 不变;
  - 拖 B:2★ 与 1★ 此消彼长,3★ 不变;
  - 每段最小 5%(1★ 为算术余量 100 − 3★ − 2★,不单独存储)。

约束(与 advanced_config setter clamp 对齐,SSOT 约定):
  3★ ∈ [5, 50]、2★ ∈ [5, 60]、1★ ≥ 5(即 3★+2★ ≤ 95)。

QuotaBar: a self-painted three-segment quota bar for V2 rating. A 0–100% track
is split by two draggable dividers into 3★/2★/1★ segments summing to 100%.
Dragging divider A trades 3★ against 2★ (1★ fixed); dragging B trades 2★ against
1★ (3★ fixed). Each segment keeps a 5% minimum; 1★ is the remainder and is not
stored. Ranges match the advanced_config setter clamps (SSOT convention).
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from ui.styles import COLORS


class QuotaBar(QWidget):
    """三段配额分配条 / three-segment quota allocation bar.

    信号 / Signal:
        quotasChanged(int q3, int q2): 用户拖动导致配额变化时发射(程序化
            set_quotas 不发射),1★ = 100 − q3 − q2 由消费方自行推导。
            Emitted on user drag only (not on programmatic set_quotas).
    """

    quotasChanged = Signal(int, int)

    # 各段约束(与 set_custom_quota3/2 clamp 一致)/ segment constraints
    Q3_MIN, Q3_MAX = 5, 50
    Q2_MIN, Q2_MAX = 5, 60
    Q1_MIN = 5

    _HANDLE_R = 7          # 分隔点半径(也用作左右留白)/ handle radius = side margin
    _MIN_H = 34            # 控件最小高度 / minimum widget height

    def __init__(self, q3: int = 20, q2: int = 25, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._q3 = 0
        self._q2 = 0
        self._dragging: Optional[str] = None   # 'A' | 'B' | None
        self.setMinimumHeight(self._MIN_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(False)
        # 初始化(不发信号)/ initialize without emitting
        self.set_quotas(q3, q2)

    # ── 公开接口 / public API ────────────────────────────────────────────────
    def quota3(self) -> int:
        """当前 3★ 配额百分比 / current 3-star quota percent."""
        return self._q3

    def quota2(self) -> int:
        """当前 2★ 配额百分比 / current 2-star quota percent."""
        return self._q2

    def quota1(self) -> int:
        """当前 1★ 配额百分比(算术余量)/ current 1-star quota (remainder)."""
        return 100 - self._q3 - self._q2

    def set_quotas(self, q3: int, q2: int) -> None:
        """
        程序化设置配额并夹紧到合法域(不发射 quotasChanged)。

        用于初始化、技能档预设联动、设置中心↔首页双向同步等场景,避免回环。

        Programmatically set quotas, clamped to the valid domain, WITHOUT
        emitting quotasChanged (used for init, preset fill, two-way sync).
        """
        q3, q2 = self._clamp(int(q3), int(q2))
        if q3 != self._q3 or q2 != self._q2:
            self._q3, self._q2 = q3, q2
            self.update()

    # ── 约束求解 / constraint solving ────────────────────────────────────────
    @classmethod
    def _clamp(cls, q3: int, q2: int) -> tuple[int, int]:
        """把 (q3, q2) 夹紧到满足全部段约束的最近合法值 / clamp to valid domain."""
        q3 = max(cls.Q3_MIN, min(cls.Q3_MAX, q3))
        # 2★ 先夹到自身范围,再夹到「保留 1★≥5%」的上限
        q2 = max(cls.Q2_MIN, min(cls.Q2_MAX, q2))
        q2 = min(q2, 100 - cls.Q1_MIN - q3)   # 3★+2★ ≤ 95
        q2 = max(cls.Q2_MIN, q2)
        return q3, q2

    # ── 绘制 / painting ──────────────────────────────────────────────────────
    def _track_rect(self) -> QRectF:
        """条形轨道区域(左右留出 handle 半径)/ track rect with side margins."""
        m = self._HANDLE_R
        h = 16.0
        y = (self.height() - h) / 2.0
        return QRectF(m, y, max(1.0, self.width() - 2 * m), h)

    def _pct_to_x(self, pct: float, track: QRectF) -> float:
        """百分比 → 轨道内 x 坐标 / percent → x within the track."""
        return track.left() + track.width() * pct / 100.0

    def _x_to_pct(self, x: float, track: QRectF) -> float:
        """轨道内 x 坐标 → 百分比 / x within the track → percent (0..100)."""
        if track.width() <= 0:
            return 0.0
        return max(0.0, min(100.0, (x - track.left()) / track.width() * 100.0))

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        track = self._track_rect()
        radius = track.height() / 2.0

        a = self._pct_to_x(self._q3, track)
        b = self._pct_to_x(self._q3 + self._q2, track)

        seg_gold = QColor(COLORS.get('star_gold', '#d4a800'))
        seg_silver = QColor('#9298a0')
        seg_muted = QColor('#5a5a5a')

        # 三段填充(用 clip 保证圆角外观)/ three segments (clipped to rounded track)
        p.save()
        clip = self._rounded_path(track, radius)
        p.setClipPath(clip)
        p.fillRect(QRectF(track.left(), track.top(), a - track.left(), track.height()), seg_gold)
        p.fillRect(QRectF(a, track.top(), b - a, track.height()), seg_silver)
        p.fillRect(QRectF(b, track.top(), track.right() - b, track.height()), seg_muted)
        p.restore()

        # 段内文字(段够宽显示「3★ N%」,较窄回退「N%」,再窄则不显示)
        # per-segment labels: full "3★ N%" if it fits, else "N%", else nothing
        self._draw_seg_label(p, track.left(), a, track,
                             f"3★ {self._q3}%", f"{self._q3}%", QColor('#2a2200'))
        self._draw_seg_label(p, a, b, track,
                             f"2★ {self._q2}%", f"{self._q2}%", QColor('#20242a'))
        self._draw_seg_label(p, b, track.right(), track,
                             f"1★ {self.quota1()}%", f"{self.quota1()}%", QColor('#eaeaea'))

        # 两个分隔点手柄 / the two divider handles
        for x in (a, b):
            self._draw_handle(p, x, track)
        p.end()

    def _rounded_path(self, rect: QRectF, radius: float):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _draw_seg_label(self, p: QPainter, x0: float, x1: float, track: QRectF,
                        full: str, short: str, color: QColor) -> None:
        """在 [x0,x1] 段中央绘制文字:优先完整串,放不下退短串,再放不下跳过。
        Centered label: full string if it fits, else the short one, else skip."""
        seg_w = x1 - x0
        font = QFont()
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        text = full if seg_w >= fm.horizontalAdvance(full) + 8 else short
        if seg_w < fm.horizontalAdvance(text) + 4:
            return
        p.setPen(QPen(color))
        rect = QRectF(x0, track.top(), seg_w, track.height())
        p.drawText(rect, Qt.AlignCenter, text)

    def _draw_handle(self, p: QPainter, x: float, track: QRectF) -> None:
        """绘制一个圆形分隔点手柄 / draw one circular divider handle."""
        r = self._HANDLE_R
        cy = track.center().y()
        p.setPen(QPen(QColor(COLORS.get('border', '#2a2a2a')), 1))
        p.setBrush(QColor(COLORS.get('text_primary', '#fafafa')))
        p.drawEllipse(QRectF(x - r, cy - r, 2 * r, 2 * r))

    # ── 交互 / interaction ───────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        track = self._track_rect()
        a = self._pct_to_x(self._q3, track)
        b = self._pct_to_x(self._q3 + self._q2, track)
        x = event.position().x()
        # 选中更近的手柄;等距时优先未越界的一侧 / pick the nearer handle
        self._dragging = 'A' if abs(x - a) <= abs(x - b) else 'B'
        self._apply_drag(x, track)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging is None:
            return
        self._apply_drag(event.position().x(), self._track_rect())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = None

    def _apply_drag(self, x: float, track: QRectF) -> None:
        """按当前拖动的手柄把鼠标 x 折算成配额并夹紧,变化则发信号。"""
        pct = round(self._x_to_pct(x, track))
        if self._dragging == 'A':
            # 拖 A:B 固定(1★ 不变),3★=A、2★=(3★+2★)−A
            total = self._q3 + self._q2               # = B 位置,保持不变
            new_q3 = int(pct)
            new_q3 = max(self.Q3_MIN, min(self.Q3_MAX, new_q3))
            new_q3 = min(new_q3, total - self.Q2_MIN)  # 2★ 不低于下限
            new_q3 = max(new_q3, total - self.Q2_MAX)  # 2★ 不高于上限
            new_q2 = total - new_q3
        else:
            # 拖 B:A 固定(3★ 不变),2★=B−3★、1★=100−B
            new_b = int(pct)
            new_b = min(new_b, 100 - self.Q1_MIN)      # 1★ 不低于下限
            new_b = max(new_b, self._q3 + self.Q2_MIN)  # 2★ 不低于下限
            new_b = min(new_b, self._q3 + self.Q2_MAX)  # 2★ 不高于上限
            new_q3 = self._q3
            new_q2 = new_b - self._q3
        new_q3, new_q2 = self._clamp(new_q3, new_q2)
        if new_q3 != self._q3 or new_q2 != self._q2:
            self._q3, self._q2 = new_q3, new_q2
            self.update()
            self.quotasChanged.emit(self._q3, self._q2)
