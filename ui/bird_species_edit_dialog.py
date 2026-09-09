# -*- coding: utf-8 -*-
"""
鸟种编辑弹窗
轻量搜索对话框，供结果浏览器"修改鸟种"功能使用。
复用 birdname_search_widget 中的 BirdResultCard，但去掉版本选择器和罕见度详情面板。

Bird species edit dialog
A lightweight search popup for the "change bird species" feature in the result browser.
Reuses BirdResultCard from birdname_search_widget but omits the version selector and detail panel.
"""

import os
import sqlite3
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QScrollArea,
    QWidget,
    QFrame,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent

from ui.styles import COLORS
from ui.birdname_search_widget import BirdResultCard
from tools.i18n import get_i18n
from config import get_install_scoped_resource_path


def _get_birdname_db_path() -> str:
    """获取 birdname.db 路径（与 BirdNameSearchWidget 共用同一数据源）。"""
    return str(get_install_scoped_resource_path(os.path.join("ioc", "birdname.db")))


def _get_latest_version_id(db_path: str) -> Optional[int]:
    """
    查询 birdname.db 中最新的 IOC 版本 ID（按 created_at 降序取第一条）。

    Args:
        db_path: birdname.db 绝对路径

    Returns:
        最新版本 ID，数据库不存在或查询失败时返回 None
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT version_id FROM versions ORDER BY created_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


class BirdSpeciesEditDialog(QDialog):
    """
    鸟种编辑弹窗。

    用户在搜索框输入中/英文关键词，下方列表实时更新候选鸟名；
    点击某条记录选中，再点「确认修改」（或双击记录）提交。

    Bird species edit dialog.
    Users type a Chinese or English keyword; the candidate list updates in real-time.
    Click a result card to select, then confirm (or double-click) to submit.

    使用方法 / Usage:
        dialog = BirdSpeciesEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            cn = dialog.selected_cn   # 中文鸟名
            en = dialog.selected_en   # 英文鸟名
    """

    # 弹窗固定尺寸 / Fixed dialog size
    _WIDTH = 420
    _HEIGHT = 520

    def __init__(self, parent=None, session_species=None, exclude_species=None):
        """
        参数 / Args:
            parent:          父窗口
            session_species: 本次拍到的鸟种 [(中文名, 张数)]，按张数降序。
                             搜索框还空着时先列出它们——改鸟种最常见的情形是
                             「认成了隔壁那种」，而那种当天多半也拍到了。
                             传 None 则维持原样：打开是空的，等用户打字。
            exclude_species: 不列出的中文名（正在改的那一种）。把 X 改成 X
                             毫无意义，点了还会白跑一趟整批移动。

        注意：这是**默认视图**，不是过滤。整批从头认错时正确鸟种一张都没认对
        过，绝不在本次列表里，搜全库的路必须一直通着。
        A default view, never a filter — a wholly misidentified batch has the
        right species nowhere in that list.
        """
        super().__init__(parent)
        self.i18n = get_i18n()

        self._exclude = {(n or "").strip() for n in (exclude_species or [])}
        self._session: list = [
            (name, count) for name, count in (session_species or [])
            if (name or "").strip() and name not in self._exclude
        ]
        self._session_names = {name for name, _ in self._session}

        self.selected_cn: str = ""
        self.selected_en: str = ""
        self.selected_latin: str = ""
        self._selected_data: Optional[Dict] = None

        self._db_path = _get_birdname_db_path()
        self._version_id: Optional[int] = _get_latest_version_id(self._db_path)
        self._cards: list = []

        self.setWindowTitle(self.i18n.t("bird_species_edit.title"))
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setStyleSheet(
            f"QDialog {{ background-color: {COLORS['bg_elevated']}; }}"
        )

        self._build_ui()
        self._show_session_species()

    def _build_ui(self):
        """构建弹窗 UI：搜索框 + 候选列表 + 确认/取消按钮。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 标题提示
        hint = QLabel(self.i18n.t("bird_species_edit.hint"))
        hint.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 12px; background: transparent;"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 搜索框
        self._search_input = QLineEdit()
        self._search_input.setFixedHeight(36)
        self._search_input.setPlaceholderText(
            self.i18n.t("bird_species_edit.search_placeholder")
        )
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 0 12px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {COLORS['accent']}; }}
        """)
        self._search_input.textChanged.connect(self._on_text_changed)
        root.addWidget(self._search_input)

        # 候选列表区
        list_frame = QFrame()
        list_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 8px;
            }}
        """)
        list_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        list_frame_layout = QVBoxLayout(list_frame)
        list_frame_layout.setContentsMargins(6, 6, 6, 6)
        list_frame_layout.setSpacing(0)

        self._empty_label = QLabel(self.i18n.t("bird_species_edit.prompt"))
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._empty_label.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 12px; "
            f"background: transparent; border: none;"
        )
        list_frame_layout.addWidget(self._empty_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['border']}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._list_widget)
        self._scroll.hide()

        list_frame_layout.addWidget(self._scroll)
        root.addWidget(list_frame, 1)

        # 确认 / 取消按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._confirm_btn = QPushButton(self.i18n.t("bird_species_edit.confirm"))
        self._confirm_btn.setFixedHeight(36)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                border: none;
                border-radius: 6px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 600;
                padding: 0 20px;
            }}
            QPushButton:hover {{ background-color: {COLORS.get('accent_hover', COLORS['accent'])}; }}
            QPushButton:disabled {{
                background-color: {COLORS['border']};
                color: {COLORS['text_muted']};
            }}
        """)
        self._confirm_btn.clicked.connect(self._on_confirm)

        cancel_btn = QPushButton(self.i18n.t("bird_species_edit.cancel"))
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_secondary']};
                font-size: 13px;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['accent']};
                color: {COLORS['text_primary']};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._confirm_btn)
        root.addLayout(btn_row)

        # 自动聚焦搜索框
        QTimer.singleShot(50, self._search_input.setFocus)

    def _on_text_changed(self, text: str):
        """搜索框内容变化：300ms 防抖后触发搜索。"""
        text = text.strip()
        if hasattr(self, "_search_timer"):
            self._search_timer.stop()
        if not text:
            self._show_session_species()
            return
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._search(text))
        self._search_timer.start(300)

    def _search(self, query: str):
        """
        在 birdname.db 中搜索候选鸟名，支持中文/英文/拼音/缩写。

        Args:
            query: 用户输入的搜索词
        """
        if not self._version_id or not os.path.exists(self._db_path):
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            q_lower = query.lower()
            # 本次拍到的鸟种排在最前（优先级 0）。这一条必须写进 SQL 的排序，
            # 不能等取回结果再重排：下面有 LIMIT 50，搜「莺」这类宽泛的字库里
            # 有几百种，本次那几种若排在第 50 名之后压根不会被取回来——用户搜
            # 一个宽泛的词反而更找不到自己刚拍的鸟。
            # The priority lives in ORDER BY, not in post-processing: with the
            # LIMIT below, sorting after the fetch would come too late.
            session = sorted(self._session_names)
            session_ph = ",".join("?" * len(session)) if session else "NULL"
            sql = f"""
                SELECT * FROM birds
                WHERE version_id = ? AND (
                    chinese_name LIKE ? OR
                    english_name LIKE ? OR
                    latin_name   LIKE ? OR
                    pinyin_name  LIKE ? OR
                    abbreviation LIKE ? OR
                    LOWER(pinyin_name)  LIKE ? OR
                    LOWER(abbreviation) LIKE ?
                )
                ORDER BY
                    CASE
                        WHEN chinese_name IN ({session_ph}) THEN 0
                        WHEN chinese_name = ?          THEN 1
                        WHEN english_name = ?          THEN 2
                        WHEN abbreviation = ?          THEN 3
                        WHEN LOWER(abbreviation) = ?   THEN 4
                        WHEN chinese_name LIKE ?       THEN 5
                        WHEN english_name LIKE ?       THEN 6
                        ELSE 7
                    END,
                    chinese_name
                LIMIT 50
            """
            params = (
                self._version_id,
                f"%{query}%", f"%{query}%", f"%{query}%",
                f"%{query}%", f"%{query}%",
                f"%{q_lower}%", f"%{q_lower}%",
                *session,
                query, query, query, q_lower,
                f"{query}%", f"{query}%",
            )
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            self._show_results(rows)
        except Exception:
            self._clear_results()

    def _show_results(self, rows):
        """
        将搜索结果渲染为 BirdResultCard 列表。

        本次拍到的鸟种排在最前面并带「本次」标记：搜「鹭」时当天拍到的那几种
        鹭直接在最上面，不必在二三十个同属鸟里挑。其余结果顺序不变（库里那套
        精确匹配优先的排序仍然有效）。
        Species from this shoot float to the top, marked; everything else keeps
        the database's own ordering.
        """
        rows = [r for r in rows if r["chinese_name"] not in self._exclude]
        rows = ([r for r in rows if r["chinese_name"] in self._session_names] +
                [r for r in rows if r["chinese_name"] not in self._session_names])
        self._clear_results()
        if not rows:
            self._empty_label.setText(self.i18n.t("bird_species_edit.no_match"))
            self._empty_label.show()
            self._scroll.hide()
            return

        self._empty_label.hide()
        self._scroll.show()

        for row in rows:
            bird_data = {
                "bird_id":      row["bird_id"],
                "chinese_name": row["chinese_name"],
                "english_name": row["english_name"],
                "latin_name":   row["latin_name"],
                "pinyin_name":  row["pinyin_name"],
                "abbreviation": row["abbreviation"],
            }
            badge = (self.i18n.t("bird_species_edit.this_shoot")
                     if row["chinese_name"] in self._session_names else None)
            card = BirdResultCard(bird_data, tier_index=None, badge=badge)
            card.selected.connect(self._on_card_selected)
            # 双击直接确认
            card.mouseDoubleClickEvent = lambda _evt, d=bird_data: self._confirm_with(d)
            self._list_layout.addWidget(card)
            self._cards.append(card)

    def _show_session_species(self) -> None:
        """
        渲染「本次拍到的鸟种」默认视图（搜索框为空时）。

        鸟名要拿去写库和建目录，所以必须以鸟名库里的正式记录为准——本次鸟种
        只用中文名反查，查不到的（历史遗留名、手工填的名字）跳过，不能凭空
        造一条记录出去。查不到一个就跳一个，其余照常列出。

        Look every session species up in the name database and skip the ones
        that are not there; the picked name gets written to disk, so it must be
        a real record rather than something assembled here.
        """
        self._clear_results()
        if not self._session:
            return

        rows = self._lookup_by_chinese([name for name, _ in self._session])
        counts = dict(self._session)
        listed = [rows[name] for name, _ in self._session if name in rows]
        if not listed:
            return

        self._empty_label.hide()
        self._scroll.show()
        unit = "张" if not self.i18n.current_lang.startswith("en") else ""
        for row in listed:
            bird_data = {
                "bird_id":      row["bird_id"],
                "chinese_name": row["chinese_name"],
                "english_name": row["english_name"],
                "latin_name":   row["latin_name"],
                "pinyin_name":  row["pinyin_name"],
                "abbreviation": row["abbreviation"],
            }
            n = counts.get(row["chinese_name"], 0)
            badge = f"{n}{unit}" if n else None
            card = BirdResultCard(bird_data, tier_index=None, badge=badge)
            card.selected.connect(self._on_card_selected)
            card.mouseDoubleClickEvent = lambda _evt, d=bird_data: self._confirm_with(d)
            self._list_layout.addWidget(card)
            self._cards.append(card)

    def _lookup_by_chinese(self, names: list) -> Dict[str, "sqlite3.Row"]:
        """
        按中文名批量查鸟名库，返回 {中文名: row}；查不到的不在结果里。

        参数 / Args:
            names: 中文鸟名

        返回 / Returns:
            Dict[str, sqlite3.Row]: 命中的记录
        """
        if not names or not self._version_id or not os.path.exists(self._db_path):
            return {}
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" * len(names))
            rows = conn.execute(
                f"SELECT * FROM birds WHERE version_id = ? "
                f"AND chinese_name IN ({placeholders})",
                (self._version_id, *names)).fetchall()
            conn.close()
            return {r["chinese_name"]: r for r in rows}
        except Exception:
            return {}

    def _on_card_selected(self, bird_data: Dict):
        """单击卡片：选中高亮，激活确认按钮。"""
        for card in self._cards:
            card.set_selected(card.bird_data is bird_data)
        self._selected_data = bird_data
        self._confirm_btn.setEnabled(True)

    def _confirm_with(self, bird_data: Dict):
        """用指定的 bird_data 直接确认（双击快捷路径）。"""
        self._selected_data = bird_data
        self._on_confirm()

    def _on_confirm(self):
        """确认选择，写入 selected_cn / selected_en / selected_latin 后关闭。"""
        if not self._selected_data:
            return
        self.selected_cn = (self._selected_data.get("chinese_name") or "").strip()
        self.selected_en = (self._selected_data.get("english_name") or "").strip()
        self.selected_latin = (self._selected_data.get("latin_name") or "").strip()
        self.accept()

    def _clear_results(self):
        """清空候选列表，重置状态。"""
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards = []
        self._selected_data = None
        self._confirm_btn.setEnabled(False)
        self._scroll.hide()
        self._empty_label.setText(self.i18n.t("bird_species_edit.prompt"))
        self._empty_label.show()

    def keyPressEvent(self, event: QKeyEvent):
        """Enter 键触发确认，Escape 键取消。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._confirm_btn.isEnabled():
                self._on_confirm()
        elif event.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
