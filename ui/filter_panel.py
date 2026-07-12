# -*- coding: utf-8 -*-
"""
SuperPicky - 结果浏览器左侧过滤面板
FilterPanel: 鸟种 / 评分 / 对焦状态 / 飞行状态 筛选

评分：单选 (★★★ / ★★ / ★ / 0)，默认 ★★★
对焦：单选 (精焦=BEST / 合焦=GOOD / 失焦=BAD+WORST)，默认精焦
飞行：多选 checkbox，默认全选
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QCheckBox, QComboBox, QScrollArea, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon

from ui.styles import COLORS, FONTS
from ui.icon_utils import load_tinted_icon, stars_pixmap, checkbox_indicator_qss

# 排序项图标:降序项(rarity/sharpness/aesthetic)用向下箭头,当前选中项用对勾
_SORT_DESC_ICON = "arrow-down.svg"
_SORT_SELECTED_ICON = "check.svg"

# 用图标替代 emoji/文字的评分筹码。
# Rating chips rendered as icons instead of emoji/text.
# 单图标筹码:精选(🏆→皇冠)、无鸟(×→禁止圈)
_ICON_CHIPS = {"picked": "crown.svg", "nobird": "circle-off.svg"}
# 多星筹码:★★★/★★/★ → N 颗 SVG 金星
_STAR_CHIPS = {"3": 3, "2": 2, "1": 1}
# 所有需图标化的筹码集合
_ICONIZED_CHIPS = set(_ICON_CHIPS) | set(_STAR_CHIPS)
# 星图标单颗逻辑像素与间距
_CHIP_STAR_SIZE = 13
_CHIP_STAR_GAP = 1


# 评分按钮配置 (mode_key, label, ratings_list)
# ratings_list = None → 不过滤评分
_RATING_OPTIONS = [
    ("picked", "🏆",   [3, 4, 5]),   # 精选：Top 25% 3★ 照片
    ("3",     "★★★", [3, 4, 5]),
    ("2",     "★★",  [2]),
    ("1",     "★",   [1]),
    # 未选用：0星(有鸟但0分) + 无鸟(-1) 合并为一个 ⊘ 筹码,避免 0 与无鸟混淆
    ("nobird", "×",  [-1, 0]),
]
# 默认勾选的评分按钮（V4.2.7：3星 + 2星，与摄影师常用「能用的片子」一致）
# Default checked rating buttons (V4.2.7): 3★ + 2★ — matches the "keeper" pile
# photographers typically review first.
_DEFAULT_RATINGS = {"3", "2"}

# 对焦按钮配置 (mode_key, statuses_list, color_key)
# statuses_list 是传给 DB 的 focus_status 列表;显示文案统一走
# browser.focus_state_* i18n 键,与右侧详情面板同词(Paul 反馈 P0-1)。
# Focus filter config (mode_key, statuses, color). Labels come from the
# browser.focus_state_* i18n keys so both panel sides use the same terms.
_FOCUS_OPTIONS = [
    ("BEST", ["BEST"],         COLORS['focus_best']),
    ("GOOD", ["GOOD"],         COLORS['focus_good']),
    ("BAD",  ["BAD", "WORST"], COLORS['focus_bad']),   # 失焦 = BAD + WORST 合并
]
_DEFAULT_FOCUS = "BEST"

# 对焦状态颜色（缩略图、detail_panel 共用）
_FOCUS_COLORS = {
    "BEST":  COLORS['focus_best'],
    "GOOD":  COLORS['focus_good'],
    "BAD":   COLORS['focus_bad'],
    "WORST": COLORS['focus_worst'],
}

# 默认勾选的对焦状态（detail_panel、其他组件参考用）
_DEFAULT_CHECKED_FOCUS = {"BEST", "GOOD", "BAD"}


def _section_label(text: str) -> QLabel:
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


class FilterPanel(QWidget):
    """
    左侧筛选面板。

    发出信号 filters_changed(dict) 通知外部刷新图片网格。
    """
    filters_changed = Signal(dict)

    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self._species_list: list = []

        # 当前激活的多选状态（set of mode keys）
        self._active_ratings: set = set(_DEFAULT_RATINGS)
        # 对焦多选状态（默认精焦+合焦）
        self._focus_checks: dict = {}  # mode -> QCheckBox（在 _build_focus_buttons 里填充）

        from advanced_config import get_advanced_config
        self._adv_config = get_advanced_config()

        self.setFixedWidth(236)
        self.setStyleSheet(
            f"background-color: {COLORS['bg_elevated']};"
            f" border-right: 1px solid {COLORS['border_subtle']};"
        )

        self._build_ui()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(20)

        # --- 鸟种（置顶）---
        layout.addWidget(_section_label(self.i18n.t("browser.section_species")))
        self.species_combo = QComboBox()
        self.species_combo.addItem(self.i18n.t("browser.species_all"), "")
        self.species_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 6px 12px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QComboBox:hover {{ border-color: {COLORS['text_muted']}; }}
            QComboBox:focus {{ border-color: {COLORS['accent']}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                selection-background-color: {COLORS['accent_dim']};
                selection-color: {COLORS['accent']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 12px;
                min-height: 24px;
            }}
        """)
        self.species_combo.currentIndexChanged.connect(self._on_species_changed)
        self._refresh_species_icon()
        layout.addWidget(self.species_combo)

        layout.addWidget(self._divider())

        # --- 评分筛选（单选）---
        layout.addWidget(_section_label(self.i18n.t("browser.filter_rating")))
        layout.addWidget(self._build_rating_buttons())

        layout.addWidget(self._divider())

        # --- 对焦状态（多选 checkbox）---
        layout.addWidget(_section_label(self.i18n.t("browser.section_focus")))
        layout.addWidget(self._build_focus_checkboxes())

        layout.addWidget(self._divider())

        # --- 飞行状态（多选 checkbox）---
        layout.addWidget(_section_label(self.i18n.t("browser.section_flight")))
        layout.addWidget(self._build_flight_checkboxes())

        layout.addWidget(self._divider())

        # --- 排序方式 ---
        layout.addWidget(_section_label(self.i18n.t("browser.section_sort")))
        self._sort_combo = QComboBox()
        self._sort_combo.addItem(self.i18n.t("browser.sort_rarity"), "rarity_desc")
        self._sort_combo.addItem(self.i18n.t("browser.sort_filename"), "filename")
        self._sort_combo.addItem(self.i18n.t("browser.sort_sharpness"), "sharpness_desc")
        self._sort_combo.addItem(self.i18n.t("browser.sort_aesthetic"), "aesthetic_desc")
        self._sort_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {COLORS['bg_input']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                padding: 6px 12px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QComboBox:hover {{ border-color: {COLORS['text_muted']}; }}
            QComboBox:focus {{ border-color: {COLORS['accent']}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['bg_elevated']};
                border: 1px solid {COLORS['border']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                selection-background-color: {COLORS['accent_dim']};
                selection-color: {COLORS['accent']};
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 12px;
                min-height: 24px;
            }}
        """)
        # 恢复用户上次选择（默认锐度）
        saved_sort = self._adv_config.get_browser_sort()
        idx = self._sort_combo.findData(saved_sort)
        if idx >= 0:
            self._sort_combo.setCurrentIndex(idx)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        self._refresh_sort_icons()
        layout.addWidget(self._sort_combo)

        layout.addStretch()

        # --- 数量标签 ---
        self._count_label = QLabel("")
        self._count_label.setAlignment(Qt.AlignCenter)
        self._count_label.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(self._count_label)

        # --- 重置按钮 ---
        reset_btn = QPushButton(self.i18n.t("browser.reset_filter"))
        reset_btn.setObjectName("secondary")
        reset_btn.clicked.connect(self.reset_all)
        layout.addWidget(reset_btn)

        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    #  评分按钮（单选，横排）
    # ------------------------------------------------------------------

    def _build_rating_buttons(self) -> QWidget:
        """5个评分互斥单选按钮（精选/★★★/★★/★/0），横排。"""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._rating_btns: dict = {}  # mode -> QPushButton

        # 窄按钮固定宽度(★★★ 用 Expanding,留出 3 颗星空间)
        _narrow = {"2": 40, "1": 30, "nobird": 32, "picked": 32}
        # 图标筹码 tooltip(图标无文字,用提示说明含义)
        _is_zh = not getattr(self.i18n, 'current_lang', 'zh_CN').startswith('en')
        _tips = {
            "picked": "精选 Top 25%" if _is_zh else "Picked (Top 25%)",
            "3": "三星" if _is_zh else "3 stars",
            "2": "二星" if _is_zh else "2 stars",
            "1": "一星" if _is_zh else "1 star",
            "nobird": "未选用:0星 / 无鸟" if _is_zh else "Unrated: 0★ / no bird",
        }

        for mode, label, ratings in _RATING_OPTIONS:
            active = (mode in self._active_ratings)
            if mode in _ICONIZED_CHIPS:
                btn = QPushButton("")  # 图标筹码,无文字
            else:
                btn = QPushButton(label)
            btn.setFixedHeight(30)
            if mode in _tips:
                btn.setToolTip(_tips[mode])
            if mode in _narrow:
                btn.setFixedWidth(_narrow[mode])
            else:
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setStyleSheet(self._rating_btn_style(active, mode))
            if mode in _ICONIZED_CHIPS:
                self._apply_chip_icon(btn, mode, active)
            _m = mode
            btn.clicked.connect(lambda _=None, m=_m: self._on_rating_btn(m))
            self._rating_btns[mode] = btn
            row.addWidget(btn)

        return w

    def _apply_chip_icon(self, btn, mode: str, active: bool) -> None:
        """给图标型筹码按激活态染色:激活=金,常态=灰。"""
        color = COLORS['star_gold'] if active else COLORS['text_muted']
        if mode in _STAR_CHIPS:
            n = _STAR_CHIPS[mode]
            btn.setIcon(QIcon(stars_pixmap(n, color, size=_CHIP_STAR_SIZE, gap=_CHIP_STAR_GAP)))
            btn.setIconSize(QSize(n * _CHIP_STAR_SIZE + (n - 1) * _CHIP_STAR_GAP, _CHIP_STAR_SIZE))
        else:
            btn.setIcon(load_tinted_icon(_ICON_CHIPS[mode], color, 16))
            btn.setIconSize(QSize(16, 16))

    def _rating_btn_style(self, active: bool, mode: str = "") -> str:
        # 精选按钮用金色高亮
        accent_color = COLORS['star_gold'] if mode == "picked" else COLORS['star_gold']
        if active:
            return (
                f"QPushButton {{ background-color: {COLORS['bg_card']};"
                f" border: 1px solid {accent_color};"
                f" border-radius: 6px;"
                f" color: {accent_color};"
                f" font-size: 13px; padding: 3px 4px; }}"
                f" QPushButton:hover {{ background-color: {COLORS['bg_input']}; }}"
            )
        else:
            return (
                f"QPushButton {{ background-color: transparent;"
                f" border: 1px solid {COLORS['border']};"
                f" border-radius: 6px;"
                f" color: {COLORS['text_muted']};"
                f" font-size: 13px; padding: 3px 4px; }}"
                f" QPushButton:hover {{ background-color: {COLORS['bg_card']};"
                f" border-color: {COLORS['text_muted']}; color: {COLORS['text_secondary']}; }}"
            )

    def _on_rating_btn(self, mode: str):
        if mode in self._active_ratings:
            self._active_ratings.discard(mode)
            # 全取消时默认显示全部（不加限制），不强制恢复默认
        else:
            self._active_ratings.add(mode)
        for m, btn in self._rating_btns.items():
            _active = m in self._active_ratings
            btn.setStyleSheet(self._rating_btn_style(_active, m))
            if m in _ICONIZED_CHIPS:
                self._apply_chip_icon(btn, m, _active)
        self._emit_filters()

    # ------------------------------------------------------------------
    #  对焦 checkbox（多选）
    # ------------------------------------------------------------------

    def _build_focus_checkboxes(self) -> QWidget:
        """3个对焦多选 checkbox（精焦/合焦/失焦），默认全选。文案走 i18n，与详情面板同词。"""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # 默认勾选全部对焦状态，避免 burst 结果被默认 focus 再过滤一次
        _defaults = set(_DEFAULT_CHECKED_FOCUS)

        for mode, statuses, color in _FOCUS_OPTIONS:
            label = self.i18n.t(f"browser.focus_state_{mode.lower()}")
            cb = QCheckBox(label)
            cb.setChecked(mode in _defaults)
            cb.setStyleSheet(
                f"QCheckBox {{ color: {color}; font-size: 12px; spacing: 6px; }}"
                + checkbox_indicator_qss(15, COLORS['text_muted'], color)
            )
            cb.stateChanged.connect(self._emit_filters)
            self._focus_checks[mode] = cb
            row.addWidget(cb)

        return w

    # ------------------------------------------------------------------
    #  飞行 checkbox（多选）
    # ------------------------------------------------------------------

    def _build_flight_checkboxes(self) -> QWidget:
        """飞行状态：2列 checkbox，默认全选。"""
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        options = [
            (1, self.i18n.t("browser.flying_option"),     0, 0),
            (0, self.i18n.t("browser.non_flying_option"), 0, 1),
        ]

        self._flight_cbs: dict = {}
        for value, label_text, row_idx, col_idx in options:
            cb = QCheckBox(label_text)
            cb.setChecked(True)
            cb.setStyleSheet(
                f"QCheckBox {{ color: {COLORS['text_secondary']}; font-size: 12px; spacing: 6px; }}"
                + checkbox_indicator_qss(15, COLORS['text_muted'], COLORS['accent'])
            )
            cb.stateChanged.connect(self._emit_filters)
            self._flight_cbs[value] = cb
            grid.addWidget(cb, row_idx, col_idx)

        return w

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(
            f"background-color: {COLORS['border_subtle']}; max-height: 1px; border: none;"
        )
        return line

    # ------------------------------------------------------------------
    #  公共接口
    # ------------------------------------------------------------------

    def update_count(self, count: int):
        """由 ResultsBrowserWidget 在每次应用筛选后调用，更新数量标签。"""
        if not hasattr(self, '_count_label'):
            return
        warning_color = COLORS.get('warning', '#E8C000')
        if count == 0:
            self._count_label.setStyleSheet(
                f"color: {warning_color}; font-size: 11px; background: transparent;"
            )
            self._count_label.setText(self.i18n.t("browser.no_result"))
        elif count < 10:
            self._count_label.setStyleSheet(
                f"color: {warning_color}; font-size: 11px; background: transparent;"
            )
            self._count_label.setText(self.i18n.t("browser.matched_count", count=count))
        else:
            self._count_label.setStyleSheet(
                f"color: {COLORS['text_muted']}; font-size: 11px; background: transparent;"
            )
            self._count_label.setText(self.i18n.t("browser.matched_count", count=count))

    def update_species_list(self, species: list):
        """更新鸟种下拉列表。"""
        self._species_list = species
        self.species_combo.blockSignals(True)
        current = self.species_combo.currentData()
        self.species_combo.clear()
        self.species_combo.addItem(self.i18n.t("browser.species_all"), "")
        for sp in species:
            self.species_combo.addItem(sp, sp)
        idx = self.species_combo.findData(current)
        if idx >= 0:
            self.species_combo.setCurrentIndex(idx)
        self.species_combo.blockSignals(False)
        self._refresh_species_icon()

    # ------------------------------------------------------------------
    #  筛选状态读取
    # ------------------------------------------------------------------

    def get_filters(self) -> dict:
        """返回当前筛选条件字典。"""
        # 评分：合并所有选中模式的 ratings（取并集），空选 = 不限星级（全选）
        selected_ratings_set: set = set()
        for mode, label, ratings in _RATING_OPTIONS:
            if mode in self._active_ratings:
                selected_ratings_set.update(ratings)
        selected_ratings = sorted(selected_ratings_set) if selected_ratings_set else None

        # 对焦：所有勾选的 checkbox 对应的 statuses 合并
        selected_focus = []
        for mode, statuses, color in _FOCUS_OPTIONS:
            cb = self._focus_checks.get(mode)
            if cb and cb.isChecked():
                selected_focus.extend(statuses)
        if not selected_focus:
            # 全取消时降级为全选，避免空结果
            selected_focus = [s for _, statuses, _ in _FOCUS_OPTIONS for s in statuses]

        # 飞行
        is_flying = [v for v, cb in self._flight_cbs.items() if cb.isChecked()]

        # 鸟种
        bird_species = self.species_combo.currentData() or ""
        is_en = self.i18n.current_lang.startswith('en')
        species_key = "bird_species_en" if is_en else "bird_species_cn"

        sort_by = self._sort_combo.currentData() if hasattr(self, '_sort_combo') else "sharpness_desc"

        return {
            "ratings":        selected_ratings,
            "focus_statuses": selected_focus,
            "is_flying":      is_flying,
            species_key:      bird_species,
            "sort_by":        sort_by,
            "picked_only":    "picked" in self._active_ratings,
        }

    # ------------------------------------------------------------------
    #  重置
    # ------------------------------------------------------------------

    def reset_all(self):
        """重置筛选条件到默认值。"""
        # 评分 → 默认 ★★★ + ★★
        self._active_ratings = set(_DEFAULT_RATINGS)
        for m, btn in self._rating_btns.items():
            _active = m in _DEFAULT_RATINGS
            btn.setStyleSheet(self._rating_btn_style(_active, m))
            if m in _ICONIZED_CHIPS:
                self._apply_chip_icon(btn, m, _active)

        # 对焦 → 默认全选
        _defaults = set(_DEFAULT_CHECKED_FOCUS)
        for mode, cb in self._focus_checks.items():
            cb.blockSignals(True)
            cb.setChecked(mode in _defaults)
            cb.blockSignals(False)

        # 飞行 → 全选
        for cb in self._flight_cbs.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)

        # 鸟种 → 全部
        self.species_combo.blockSignals(True)
        self.species_combo.setCurrentIndex(0)
        self.species_combo.blockSignals(False)
        self._refresh_species_icon()

        # 排序 → 恢复用户上次选择（不强制重置为锐度）
        self._sort_combo.blockSignals(True)
        saved_sort = self._adv_config.get_browser_sort()
        idx = self._sort_combo.findData(saved_sort)
        self._sort_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._sort_combo.blockSignals(False)
        self._refresh_sort_icons()

        self._emit_filters()

    def select_all_ratings(self):
        """回退：清空评分筛选，返回所有评分。用于默认筛选无结果时。"""
        self._active_ratings = set()
        for m, btn in self._rating_btns.items():
            btn.setStyleSheet(self._rating_btn_style(False, m))
            if m in _ICONIZED_CHIPS:
                self._apply_chip_icon(btn, m, False)
        self._emit_filters()

    # ------------------------------------------------------------------
    #  信号
    # ------------------------------------------------------------------

    def _refresh_species_icon(self):
        """鸟种下拉:当前选中项显示对勾(check),其余无图标。"""
        cur = self.species_combo.currentIndex()
        for i in range(self.species_combo.count()):
            if i == cur:
                self.species_combo.setItemIcon(i, load_tinted_icon(_SORT_SELECTED_ICON, COLORS['accent'], 14))
            else:
                self.species_combo.setItemIcon(i, QIcon())

    def _on_species_changed(self, *_):
        self._refresh_species_icon()
        self._emit_filters()

    def _refresh_sort_icons(self):
        """排序项图标:当前选中项→对勾(check);其余降序项→向下箭头;文件名无图标。"""
        cur = self._sort_combo.currentIndex()
        for i in range(self._sort_combo.count()):
            data = self._sort_combo.itemData(i)
            if i == cur:
                self._sort_combo.setItemIcon(i, load_tinted_icon(_SORT_SELECTED_ICON, COLORS['accent'], 14))
            elif isinstance(data, str) and data.endswith("_desc"):
                self._sort_combo.setItemIcon(i, load_tinted_icon(_SORT_DESC_ICON, COLORS['text_secondary'], 14))
            else:
                self._sort_combo.setItemIcon(i, QIcon())

    def _on_sort_changed(self, *_):
        sort_val = self._sort_combo.currentData()
        if sort_val:
            self._adv_config.set_browser_sort(sort_val)
            self._adv_config.save()
        self._refresh_sort_icons()
        self._emit_filters()

    def _emit_filters(self, *_):
        self.filters_changed.emit(self.get_filters())
