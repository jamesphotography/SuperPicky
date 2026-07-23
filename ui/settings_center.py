# -*- coding: utf-8 -*-
"""
统一设置中心:左侧分类导航 + 右侧内容页。

取代旧高级设置/技能等级/关于弹窗入口,统管精选/识鸟/输出/外部应用/关于五页。
所有控件走「统一即时保存」模型:每个控件的 changed 信号即时写回 advanced_config,
关闭对话框(完成/ESC/关闭按钮)经 done() 再做一次幂等兜底 flush。

Unified Settings Center: left-side category nav + right-side content pages.

Replaces the old advanced settings / skill level / about dialog entry points,
covering five pages (culling / bird-ID / output / external apps / about). Every
control uses the unified immediate-save model: each control's changed signal
persists to advanced_config right away, and closing the dialog (Done / ESC /
close button) runs one idempotent safety-net flush via done().
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, cast

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.icon_utils import (  # noqa: F401
    ICON_ACTIVE,
    ICON_IDLE,
    checkbox_indicator_qss,
    load_tinted_icon,
    radio_indicator_qss,
)
from ui.styles import COLORS  # noqa: F401


def _radio_style() -> str:
    """
    设置中心单选按钮的统一样式：文字样式 + 圆圈指示器。

    指示器必须显式样式化——QRadioButton 挂自定义 stylesheet 后 Qt 走
    QStyleSheetStyle 渲染，Windows 深色界面下原生选中圆点与背景融合，
    被选中的选项反而看不见指示器（4.5.0RC2 用户反馈）。
    与 QCheckBox 的 checkbox_indicator_qss 同一套视觉语言。

    返回:
    str: 完整的 QRadioButton 样式表字符串。

    Unified style for Settings Center radio buttons: text style + circle
    indicator. The indicator must be styled explicitly — with a custom
    stylesheet Qt renders it via QStyleSheetStyle, and on Windows dark UI
    the native checked dot blends into the background, leaving the selected
    option visually indicator-less (user report on 4.5.0RC2). Matches the
    QCheckBox visual language from checkbox_indicator_qss.

    Return:
    str: Complete QRadioButton stylesheet string.
    """
    # 注意：文字样式必须包进 QRadioButton{} 选择器——同一张样式表里
    # 「裸声明 + 选择器规则」混用会导致 Qt 解析失败、整张表被丢弃，
    # 指示器回落到原生渲染（正是要修的隐形问题）。
    # Note: the text style must be wrapped in a QRadioButton{} selector —
    # mixing bare declarations with selector rules in one stylesheet makes
    # Qt discard the whole sheet, falling back to native indicator rendering
    # (the very invisibility bug being fixed).
    return (
        f"QRadioButton {{ color:{COLORS['text_secondary']}; font-size:12px; }}"
        + radio_indicator_qss()
    )

# ── 常量 / Constants ──────────────────────────────────────────────────────────

# ExtremeSimple: "video" 已从 PAGE_ORDER 剥离（导航项+页面均由这一个列表驱动，
# 摘掉这一个 key 就同时去掉了导航条目和 stack 页；_PAGE_ICON/_PAGE_TITLE_KEY 的
# "video" 项与 _build_video_page() 方法本身都保留不动，未来要恢复只需把
# "video" 加回列表）。
# ExtremeSimple: "video" is stripped from PAGE_ORDER (both the nav item and the
# stacked page are driven by this single list, so removing this one key cuts
# both). The "video" entries in _PAGE_ICON/_PAGE_TITLE_KEY and the
# _build_video_page() method itself are untouched; re-add "video" to bring it back.
PAGE_ORDER: list[str] = ["culling", "birdid", "output", "apps", "about"]

_PAGE_ICON: dict[str, str] = {
    "culling": "gem.svg",
    "birdid": "bird.svg",
    "output": "download.svg",
    "video": "video.svg",
    "apps": "layout-grid.svg",
    "about": "info.svg",
}

_PAGE_TITLE_KEY: dict[str, str] = {
    "culling": "settings.nav_culling",
    "birdid": "settings.nav_birdid",
    "output": "settings.nav_output",
    "video": "settings.nav_video",
    "apps": "settings.nav_apps",
    "about": "settings.nav_about",
}


# ── 主对话框 / Main Dialog ────────────────────────────────────────────────────


class SettingsCenter(QDialog):
    """
    统一设置中心对话框。

    左侧 QListWidget 导航,右侧 QStackedWidget 内容区。
    各分页由 _build_page(key) 分发构建。

    Unified settings center dialog.

    Left: QListWidget navigation. Right: QStackedWidget content area.
    Each page is dispatched via _build_page(key).

    参数 / Parameters:
        i18n: i18n 实例,提供 .t(key) 方法 / i18n instance with .t(key) method.
        parent: 父窗口 / Parent widget.
        start_page: 初始显示的页 key / Initial page key to show.
    """

    def __init__(self, i18n, parent: QWidget | None = None, start_page: str = "culling") -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.t("settings.window_title"))
        self.resize(760, 560)

        # Fix D: 提前初始化协同守卫,避免 late-init 依赖
        # Fix D: Early-init coordination guards to eliminate late-init dependencies
        self._suppress: bool = False
        self._current_skill_key: str = "custom"

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧导航 / Left-side navigation
        self._nav = QListWidget()
        self._nav.setFixedWidth(160)
        for key in PAGE_ORDER:
            item = QListWidgetItem(
                load_tinted_icon(_PAGE_ICON[key], ICON_IDLE, 18),
                "  " + i18n.t(_PAGE_TITLE_KEY[key]),
            )
            item.setData(Qt.UserRole, key)
            self._nav.addItem(item)
        root.addWidget(self._nav)

        # 右侧内容区 / Right-side content area
        right = QVBoxLayout()

        self._stack = QStackedWidget()
        for key in PAGE_ORDER:
            self._stack.addWidget(self._build_page(key))
        right.addWidget(self._stack, 1)

        # 底部完成按钮 / Bottom Done button
        footer = QHBoxLayout()
        footer.addStretch(1)
        done_btn = QPushButton(i18n.t("settings.done"))
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)
        right.addLayout(footer)

        root.addLayout(right, 1)

        # 导航切换联动 stack / Wire nav row change to stack
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)

        self.show_page(start_page)

    # ── 页面构建 / Page construction ─────────────────────────────────────────

    def _checkbox_qss(self) -> str:
        """设置中心统一开关样式:文字 + 圆圈(未选)/带勾圆圈(选中),与首页快速面板一致。
        统一为圆圈+勾,替代全局默认的「方块选中全绿」(不直观,易被忽略)。

        Unified checkbox style: text + circle (unchecked) / checked-circle indicator,
        matching the home quick-panel; replaces the global square-checkbox look.
        """
        return (
            f"QCheckBox {{ color: {COLORS['text_secondary']}; font-size: 12px;"
            f" background: transparent; }}"
            + checkbox_indicator_qss(16, COLORS['text_muted'], COLORS['accent'])
        )

    def _divider(self) -> QFrame:
        """1px 分隔线,供各设置页在小节之间使用。/ 1px divider between sections."""
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        return line

    def _build_page(self, key: str) -> QWidget:
        """
        根据 key 构建对应内容页。

        Build the content page for the given key.

        参数 / Parameters:
            key (str): 页面标识符,取自 PAGE_ORDER / Page identifier from PAGE_ORDER.

        返回 / Returns:
            QWidget: 内容页 widget / Content page widget.
        """
        if key == "culling":
            return self._build_culling_page()
        if key == "birdid":
            return self._build_birdid_page()
        if key == "output":
            return self._build_output_page()
        if key == "video":
            return self._build_video_page()
        if key == "apps":
            return self._build_apps_page()
        if key == "about":
            return self._build_about_page()
        return self._placeholder(self.i18n.t(_PAGE_TITLE_KEY[key]))

    def _build_culling_page(self) -> QWidget:
        """
        构建精选(Culling)设置页。

        包含:技能等级单选卡片行(含"自定义")、3星配额/锐度/美学阈值滑块、
        飞鸟检测/连拍检测(含缩进的连拍速度)/无鸟补救扫描开关，以及底部折叠的
        「高级选项」(AI 置信度滑块 + 旧版 V1 评星算法开关)。

        技能等级 ↔ 阈值协同逻辑:
          - 选技能等级预设 → `_on_skill_preset_selected` 置 `_suppress=True`、
            填充对应阈值滑块、再置 False，避免回调回环。
          - 手动拖动任一阈值 → `_on_cull_threshold_changed` 检查 `_suppress`，
            若未抑制则将 `_current_skill_key` 切为 "custom" 并刷新卡片选中态。

        统一即时保存(Task: unify save model):每个控件的 changed 信号都直接
        持久化对应字段，不再依赖"完成"按钮做统一保存；ESC/关窗与点"完成"
        效果一致(见 SettingsCenter.done() 的兜底 flush)。`_save_culling()`
        仍保留为一次性批量落盘的辅助方法(供测试与偶发的兜底调用)。

        Build the Culling settings page.

        Contains: a row of skill-level cards (incl. "custom"), quota/sharpness/
        aesthetics threshold sliders, flight/burst (with an indented burst-fps
        row)/rescue-scan toggles, and a bottom "Advanced" disclosure holding the
        AI-confidence slider and the legacy V1 rating-algorithm toggle.

        Coordination logic:
          - Selecting a skill preset → _on_skill_preset_selected sets _suppress=True,
            fills sliders, then False — prevents re-entrant loop.
          - Manually adjusting any threshold → _on_cull_threshold_changed checks _suppress;
            if not suppressed, sets _current_skill_key="custom" and refreshes card state.

        Every control's changed signal persists its field immediately; the
        "Done" button and ESC/close are now equivalent (see SettingsCenter.done()'s
        safety-net flush). _save_culling() stays as a one-shot bulk-flush helper
        (used by tests and as a defensive fallback).

        返回 / Returns:
            QWidget: 精选设置页 / Culling settings page widget.
        """
        from advanced_config import get_advanced_config
        from ui.skill_level_dialog import SkillLevelCard

        cfg = get_advanced_config()

        # 协同守卫初始化 / Guard init
        self._suppress: bool = False
        self._current_skill_key: str = cfg.skill_level

        # ── 容器 + 滚动区 / Container + scroll area ───────────────────────────
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # macOS 原生 QStyle 下 QScrollArea 的 viewport 不会继承祖先 QDialog 的
        # QSS 背景色，系统外观为浅色时会露出原生浅灰 #ececec（与深色主题不符）。
        # 显式设 transparent 让 QDialog 的深色背景透出来（同 birdid_dock.py 已验证的写法）。
        # On macOS the native QStyle paints a QScrollArea's viewport without
        # inheriting the ancestor QDialog's QSS background, so it falls back to
        # the native light gray #ececec when the system appearance is Light —
        # clashing with the dark theme. Setting transparent lets the QDialog's
        # dark background show through (same fix already proven in birdid_dock.py).
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ── 技能等级区 / Skill-level section ─────────────────────────────────
        skill_title = QLabel(self.i18n.t("settings.culling_skill_section"))
        skill_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(skill_title)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)

        self._skill_cards: dict[str, SkillLevelCard] = {}
        for level_key in ["beginner", "intermediate", "master"]:
            card = SkillLevelCard(level_key, self.i18n)
            card.clicked.connect(self._on_skill_preset_selected)
            self._skill_cards[level_key] = card
            cards_row.addWidget(card)

        # 自定义卡片(仅显示,不可点击) / Custom card (display-only)
        custom_card = SkillLevelCard("custom", self.i18n, is_custom=True)
        self._skill_cards["custom"] = custom_card
        cards_row.addWidget(custom_card)
        cards_row.addStretch(1)
        lay.addLayout(cards_row)

        # 刷新初始选中态 / Refresh initial selection
        self._select_skill_radio(self._current_skill_key)

        # V4.6(rating-v2): rating_algorithm 默认 v2，决定下方阈值区显示星级配额
        # 分配条(V2) 还是 锐度/美学两个旧滑块(V1)；V1/V2 切换开关显眼放在阈值
        # 标题正下方(见下),方便用户随时回滚旧版评星。
        # V4.6 (rating-v2): rating_algorithm defaults to v2 and decides whether
        # the threshold section below shows the quota bar (V2) or the legacy
        # sharpness/aesthetics sliders (V1); the V1/V2 toggle sits prominently
        # right under the threshold title (see below) for easy rollback.
        self._rating_v2 = cfg.rating_algorithm == "v2"

        # ── 阈值区 / Threshold section ────────────────────────────────────────
        thresh_title = QLabel(self.i18n.t("settings.culling_threshold_section"))
        thresh_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(thresh_title)

        # V1/V2 评星算法切换(从折叠的「高级选项」提到此显眼处):勾选=回滚旧版
        # 绝对阈值 V1,直接决定下方显示「星级配额分配条」(V2) 还是「锐度/美学」
        # 两滑块(V1)。放在阈值标题正下方,与其控制的控件紧邻,切换所见即所得。
        # V1/V2 rating-algorithm toggle promoted from the Advanced disclosure to
        # this prominent spot: checking it rolls back to legacy fixed-threshold
        # V1 and swaps the controls right below (quota bar for V2 vs sharpness/
        # aesthetics sliders for V1).
        self._algo_legacy_checkbox = QCheckBox(
            self.i18n.t("settings.culling_algo_legacy_label")
        )
        self._algo_legacy_checkbox.setChecked(cfg.rating_algorithm != "v2")
        self._algo_legacy_checkbox.setStyleSheet(self._checkbox_qss())
        self._algo_legacy_checkbox.stateChanged.connect(self._on_algo_legacy_toggled)
        lay.addWidget(self._algo_legacy_checkbox)

        algo_hint = QLabel(self.i18n.t("settings.culling_algo_v1_desc"))
        algo_hint.setWordWrap(True)
        algo_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(algo_hint)

        # V4.6(rating-v2/T4): v2 用单一「3星配额」滑块取代锐度/美学两个阈值滑块
        # (星级=批内相对排序,阈值不再决定星级);v1 回滚开关下保留原两滑块。
        # 范围 5-50 与 set_custom_quota3 的 clamp 一致(SSOT 约定)。
        # V4.6 (rating-v2/T4): under v2 a single "3-star quota" slider replaces
        # the sharpness/aesthetics threshold sliders (stars are batch-relative);
        # the v1 rollback switch keeps the legacy sliders. Range 5-50 matches
        # the set_custom_quota3 clamp (SSOT convention).
        # V4.6(rating-v2)+三段配额:v2 用「星级配额分配条」QuotaBar 取代单一 3星
        # 配额滑块——一条 3★/2★/1★ 三段条,拖分隔点即改配额(三段和恒 100%,
        # 1★ 为算术余量)。约束(3★∈[5,50]/2★∈[5,60]/1★≥5)与 set_custom_quota3/2
        # clamp 对齐(SSOT)。v1 回滚开关下仍显示下方锐度/美学两滑块。
        # V4.6 + 3-segment quota: under v2 the QuotaBar (3★/2★/1★ split) replaces
        # the single 3-star quota slider; ranges match the setter clamps (SSOT).
        from core.rating_quota import get_quota3_for_skill, get_quota2_for_skill
        from ui.quota_bar import QuotaBar
        quota_row = QHBoxLayout()
        quota_label = QLabel(self.i18n.t("settings.culling_quota_split_label"))
        quota_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        quota_label.setFixedWidth(160)
        self._cull_quota = QuotaBar(
            int(get_quota3_for_skill(self._current_skill_key, cfg)),
            int(get_quota2_for_skill(self._current_skill_key, cfg)),
        )
        self._cull_quota.quotasChanged.connect(self._on_cull_quota_changed)
        quota_row.addWidget(quota_label)
        quota_row.addWidget(self._cull_quota, 1)
        lay.addLayout(quota_row)
        self._quota_row_widgets = (quota_label, self._cull_quota)

        # 锐度滑块 (100-600, int; 对应 min_sharpness，范围与 set_min_sharpness clamp 一致)
        # Sharpness slider (100-600 integer; maps to min_sharpness, matches the
        # set_min_sharpness clamp range)
        sharp_row = QHBoxLayout()
        sharp_label = QLabel(self.i18n.t("settings.culling_sharp_label"))
        sharp_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        sharp_label.setFixedWidth(160)
        self._cull_sharp = QSlider(Qt.Horizontal)
        self._cull_sharp.setRange(100, 600)
        self._cull_sharp.setValue(int(cfg.min_sharpness))
        self._cull_sharp_value_label = QLabel(str(self._cull_sharp.value()))
        self._cull_sharp_value_label.setFixedWidth(30)
        self._cull_sharp_value_label.setStyleSheet(
            f"color:{COLORS['text_tertiary']};font-size:11px;"
        )
        self._cull_sharp.valueChanged.connect(
            lambda v: self._cull_sharp_value_label.setText(str(v))
        )
        self._cull_sharp.valueChanged.connect(self._on_cull_threshold_changed)
        sharp_row.addWidget(sharp_label)
        sharp_row.addWidget(self._cull_sharp, 1)
        sharp_row.addWidget(self._cull_sharp_value_label)
        lay.addLayout(sharp_row)
        self._sharp_row_widgets = (sharp_label, self._cull_sharp, self._cull_sharp_value_label)

        # 美学(NIMA)滑块 (0-70, 值/10 = NIMA; 对应 min_nima 0.0..7.0)
        # Aesthetics (NIMA) slider (0-70; value/10 = NIMA float; maps to min_nima 0.0..7.0)
        nima_row = QHBoxLayout()
        nima_label = QLabel(self.i18n.t("settings.culling_nima_label"))
        nima_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        nima_label.setFixedWidth(160)
        self._cull_nima = QSlider(Qt.Horizontal)
        self._cull_nima.setRange(0, 70)
        self._cull_nima.setValue(int(round(cfg.min_nima * 10)))
        self._cull_nima_value_label = QLabel(f"{self._cull_nima.value() / 10:.1f}")
        self._cull_nima_value_label.setFixedWidth(30)
        self._cull_nima_value_label.setStyleSheet(
            f"color:{COLORS['text_tertiary']};font-size:11px;"
        )
        self._cull_nima.valueChanged.connect(
            lambda v: self._cull_nima_value_label.setText(f"{v / 10:.1f}")
        )
        self._cull_nima.valueChanged.connect(self._on_cull_threshold_changed)
        nima_row.addWidget(nima_label)
        nima_row.addWidget(self._cull_nima, 1)
        nima_row.addWidget(self._cull_nima_value_label)
        lay.addLayout(nima_row)
        self._nima_row_widgets = (nima_label, self._cull_nima, self._cull_nima_value_label)

        # 按当前算法应用滑块行可见性 / Apply slider-row visibility per algorithm
        self._apply_algo_visibility()

        # ── 检测开关区 / Detection section ───────────────────────────────────
        detect_title = QLabel(self.i18n.t("settings.culling_detect_section"))
        detect_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(detect_title)

        # 飞鸟检测 / Flight detection
        self._cull_flight = QCheckBox(self.i18n.t("settings.culling_flight_label"))
        self._cull_flight.setChecked(cfg.flight_check)
        self._cull_flight.setStyleSheet(self._checkbox_qss())
        self._cull_flight.stateChanged.connect(self._on_flight_check_changed)
        lay.addWidget(self._cull_flight)

        # 连拍检测 / Burst detection
        self._cull_burst = QCheckBox(self.i18n.t("settings.culling_burst_label"))
        self._cull_burst.setChecked(cfg.burst_check)
        self._cull_burst.setStyleSheet(self._checkbox_qss())
        self._cull_burst.stateChanged.connect(self._on_burst_check_changed)
        lay.addWidget(self._cull_burst)

        # 连拍速度(缩进,随连拍开关禁用/启用):关闭连拍检测时这个数字无意义,
        # 置灰比"独立一行、开关关了也能编辑"更清楚地表达依赖关系。
        # Burst speed (indented, enabled state follows the burst toggle): the
        # value is meaningless while burst detection is off, so graying it out
        # communicates the dependency more clearly than a same-level standalone row.
        fps_row = QHBoxLayout()
        fps_row.setContentsMargins(24, 0, 0, 0)
        fps_label = QLabel(self.i18n.t("settings.culling_burst_fps_label"))
        fps_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        fps_label.setFixedWidth(136)
        self._cull_burst_fps = QSpinBox()
        self._cull_burst_fps.setRange(4, 20)
        self._cull_burst_fps.setValue(cfg.burst_fps)
        self._cull_burst_fps.valueChanged.connect(self._on_burst_fps_changed)
        fps_row.addWidget(fps_label)
        fps_row.addWidget(self._cull_burst_fps)
        fps_row.addStretch(1)
        lay.addLayout(fps_row)
        self._burst_fps_row_widgets = (fps_label, self._cull_burst_fps)
        self._set_burst_fps_enabled(cfg.burst_check)

        # 无鸟补救扫描 (V4.6): 判无鸟/低置信度时 1024px 重扫 + 识鸟守门
        # No-bird rescue scan (V4.6): 1024px rescan + BirdID gate on rejects
        self._cull_rescue = QCheckBox(self.i18n.t("settings.culling_rescue_label"))
        self._cull_rescue.setChecked(cfg.rescue_scan_enabled)
        self._cull_rescue.setStyleSheet(self._checkbox_qss())
        self._cull_rescue.stateChanged.connect(self._on_rescue_changed)
        lay.addWidget(self._cull_rescue)

        lay.addStretch(1)

        # ── 高级选项(折叠,默认收起) / Advanced (collapsed by default) ────────
        # AI 置信度是"多数用户不需要天天碰"的设置,折叠收起(V1/V2 切换已提到
        # 上方阈值区,不再放这里)。
        # AI confidence is a setting most users never touch day to day, so it
        # stays collapsed here (the V1/V2 toggle was promoted up to the
        # threshold section and no longer lives here).
        lay.addWidget(self._divider())

        self._advanced_expanded = False
        self._advanced_toggle_btn = QPushButton()
        self._advanced_toggle_btn.setFlat(True)
        self._advanced_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._advanced_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                color: {COLORS['text_tertiary']};
                font-size: 12px;
                background: transparent;
                border: none;
                text-align: left;
                padding: 4px 0px;
            }}
            QPushButton:hover {{ color: {COLORS['text_secondary']}; }}
        """)
        self._advanced_toggle_btn.clicked.connect(self._toggle_culling_advanced)
        lay.addWidget(self._advanced_toggle_btn)

        self._advanced_content = QWidget()
        self._advanced_content.setVisible(False)
        adv_lay = QVBoxLayout(self._advanced_content)
        adv_lay.setContentsMargins(0, 8, 0, 0)
        adv_lay.setSpacing(12)

        # AI 置信度滑块 (30-70, 显示 ×100 整数; 对应 min_confidence 0.3..0.7)
        # AI confidence slider (30-70 integer; maps to min_confidence 0.3..0.7)
        ai_row = QHBoxLayout()
        ai_label = QLabel(self.i18n.t("settings.culling_ai_label"))
        ai_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        ai_label.setFixedWidth(160)
        self._cull_ai = QSlider(Qt.Horizontal)
        self._cull_ai.setRange(30, 70)
        self._cull_ai.setValue(int(round(cfg.min_confidence * 100)))
        self._cull_ai_value_label = QLabel(str(self._cull_ai.value()))
        self._cull_ai_value_label.setFixedWidth(30)
        self._cull_ai_value_label.setStyleSheet(f"color:{COLORS['text_tertiary']};font-size:11px;")
        self._cull_ai.valueChanged.connect(
            lambda v: self._cull_ai_value_label.setText(str(v))
        )
        self._cull_ai.valueChanged.connect(self._on_ai_confidence_changed)
        ai_row.addWidget(ai_label)
        ai_row.addWidget(self._cull_ai, 1)
        ai_row.addWidget(self._cull_ai_value_label)
        adv_lay.addLayout(ai_row)

        lay.addWidget(self._advanced_content)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    # ── 识鸟页 / Bird-ID page ─────────────────────────────────────────────────

    def _build_birdid_page(self) -> QWidget:
        """
        构建识鸟(BirdID)设置页。

        包含：自动识鸟开关、识别置信度滑块(30-95)、数据源单选(eBird/GBIF)、
        国家下拉菜单(含"更多国家"完整列表入口,与识鸟面板 dock 能力对等)、
        地区下拉菜单(随国家变化动态填充)、鸟名显示格式下拉。

        初值来自 advanced_config 的 birdid_* / name_format 字段；每个控件的
        changed 信号即时持久化(见 _on_birdid_field_changed);_save_birdid()
        仍保留为一次性批量落盘的辅助方法(供测试与偶发的兜底调用)。

        Build the Bird-ID settings page.

        Contains: auto-identify toggle, confidence slider (30-95), data-source
        radio buttons (eBird / GBIF), country dropdown (with a "more countries"
        entry offering the full list — matching the Bird-ID dock's coverage),
        region dropdown (dynamically populated on country change), and a bird
        name display-format dropdown.

        Initial values come from advanced_config's birdid_* / name_format
        fields; every control persists immediately on change (see
        _on_birdid_field_changed); _save_birdid() remains as an idempotent
        one-shot bulk-flush helper (used by tests and as a defensive fallback).

        返回 / Returns:
            QWidget: 识鸟设置页 / Bird-ID settings page widget.
        """
        from collections import OrderedDict

        from advanced_config import get_advanced_config
        from core.region_data import load_regions_data

        cfg = get_advanced_config()
        regions_data = load_regions_data()

        # ── 容器 + 滚动区 / Container + scroll area ───────────────────────────
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # macOS 原生 QStyle 下 QScrollArea 的 viewport 不会继承祖先 QDialog 的
        # QSS 背景色，系统外观为浅色时会露出原生浅灰 #ececec（与深色主题不符）。
        # 显式设 transparent 让 QDialog 的深色背景透出来（同 birdid_dock.py 已验证的写法）。
        # On macOS the native QStyle paints a QScrollArea's viewport without
        # inheriting the ancestor QDialog's QSS background, so it falls back to
        # the native light gray #ececec when the system appearance is Light —
        # clashing with the dark theme. Setting transparent lets the QDialog's
        # dark background show through (same fix already proven in birdid_dock.py).
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ── 自动识鸟开关 / Auto-identify toggle ──────────────────────────────
        auto_title = QLabel(self.i18n.t("settings.birdid_section_auto"))
        auto_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(auto_title)

        self._bid_auto = QCheckBox(self.i18n.t("settings.birdid_auto_label"))
        self._bid_auto.setChecked(cfg.birdid_auto_identify)
        self._bid_auto.setStyleSheet(self._checkbox_qss())
        self._bid_auto.stateChanged.connect(self._on_birdid_field_changed)
        lay.addWidget(self._bid_auto)

        # 写入关键字开关(Paul P1-1) / write-keywords toggle
        self._bid_keywords = QCheckBox(self.i18n.t("settings.birdid_keywords_label"))
        self._bid_keywords.setChecked(cfg.birdid_write_keywords)
        self._bid_keywords.setStyleSheet(self._checkbox_qss())
        self._bid_keywords.stateChanged.connect(self._on_birdid_field_changed)
        lay.addWidget(self._bid_keywords)

        # ── 置信度滑块 / Confidence slider (range 30-95, mirrors set_birdid_confidence clamp) ──
        conf_title = QLabel(self.i18n.t("settings.birdid_section_conf"))
        conf_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(conf_title)

        conf_row = QHBoxLayout()
        conf_label = QLabel(self.i18n.t("settings.birdid_conf_label"))
        conf_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        conf_label.setFixedWidth(160)

        self._bid_conf = QSlider(Qt.Horizontal)
        # 30-95 与 set_birdid_confidence(value) 的 clamp 范围完全对齐
        # 30-95 exactly mirrors the clamp range in set_birdid_confidence()
        self._bid_conf.setRange(30, 95)
        self._bid_conf.setValue(int(cfg.birdid_confidence))

        self._bid_conf_label = QLabel(str(self._bid_conf.value()) + "%")
        self._bid_conf_label.setFixedWidth(40)
        self._bid_conf_label.setStyleSheet(
            f"color:{COLORS['text_tertiary']};font-size:11px;"
        )
        self._bid_conf.valueChanged.connect(
            lambda v: self._bid_conf_label.setText(f"{v}%")
        )
        self._bid_conf.valueChanged.connect(self._on_birdid_field_changed)

        conf_row.addWidget(conf_label)
        conf_row.addWidget(self._bid_conf, 1)
        conf_row.addWidget(self._bid_conf_label)
        lay.addLayout(conf_row)

        # ── 数据源单选 / Data-source radio buttons (eBird / GBIF) ────────────
        source_title = QLabel(self.i18n.t("settings.birdid_section_source"))
        source_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(source_title)

        source_row = QHBoxLayout()
        self._bid_ebird = QRadioButton(self.i18n.t("settings.birdid_source_ebird"))
        self._bid_gbif = QRadioButton(self.i18n.t("settings.birdid_source_gbif"))
        self._bid_ebird.setStyleSheet(_radio_style())
        self._bid_gbif.setStyleSheet(_radio_style())

        # QButtonGroup 确保两者互斥 / QButtonGroup ensures mutual exclusivity
        self._bid_source_group = QButtonGroup(self)
        self._bid_source_group.addButton(self._bid_ebird)
        self._bid_source_group.addButton(self._bid_gbif)

        if cfg.birdid_use_ebird:
            self._bid_ebird.setChecked(True)
        else:
            self._bid_gbif.setChecked(True)
        self._bid_ebird.toggled.connect(self._on_birdid_field_changed)

        source_row.addWidget(self._bid_ebird)
        source_row.addWidget(self._bid_gbif)
        source_row.addStretch(1)
        lay.addLayout(source_row)

        # ── 地区选择 / Region selection ──────────────────────────────────────
        region_title = QLabel(self.i18n.t("settings.birdid_section_region"))
        region_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(region_title)

        # 构建国家列表 (OrderedDict: 显示名 → 代码)
        # Build country list (OrderedDict: display name → code)
        is_english = self.i18n.current_lang.startswith("en")
        country_list: OrderedDict[str, str | None] = OrderedDict()
        country_list[self.i18n.t("birdid.country_auto_gps")] = None
        country_list[self.i18n.t("birdid.country_global")] = "GLOBAL"
        country_list["─" * 15] = "SEP1"

        top10_codes = ["AU", "BR", "CN", "GB", "HK", "ID", "JP", "MY", "TW", "US"]
        top10_i18n = {
            "AU": "birdid.country_au",
            "BR": "birdid.country_br",
            "CN": "birdid.country_cn",
            "GB": "birdid.country_gb",
            "HK": "birdid.country_hk",
            "ID": "birdid.country_id",
            "JP": "birdid.country_jp",
            "MY": "birdid.country_my",
            "TW": "birdid.country_tw",
            "US": "birdid.country_us",
        }
        code_to_region = {
            r.get("code"): r for r in regions_data.get("countries", [])
        }
        for code in top10_codes:
            i18n_key = top10_i18n.get(code)
            if i18n_key:
                display_name = self.i18n.t(i18n_key)
            else:
                region_entry = code_to_region.get(code, {})
                if is_english:
                    display_name = region_entry.get("name", code)
                else:
                    display_name = region_entry.get("name_cn") or region_entry.get("name", code)
            country_list[display_name] = code

        # 「更多国家」入口(与识鸟面板 dock 能力对等):补上分隔符 + 完整列表入口,
        # 选中后弹出 show_country_picker_dialog(含搜索框的完整国家/大洲列表)。
        # "More countries" entry (parity with the Bird-ID dock): a separator
        # plus a full-list entry that opens show_country_picker_dialog
        # (a searchable full country/continent list) on selection.
        country_list["─" * 15 + " "] = "SEP2"
        country_list[self.i18n.t("birdid.country_more")] = "MORE"

        # 保存 country_list 供 _on_country_changed_birdid 使用
        # Store country_list for _on_country_changed_birdid to use
        self._bid_country_list = country_list
        self._bid_regions_data = regions_data

        # 国家下拉 / Country dropdown
        country_row = QHBoxLayout()
        country_label = QLabel(self.i18n.t("settings.birdid_country_label"))
        country_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        country_label.setFixedWidth(160)

        self._bid_country = QComboBox()
        for display_name, code in country_list.items():
            self._bid_country.addItem(display_name)
            if code in ("SEP1", "SEP2"):
                # 禁用分隔符项 / Disable separator item
                idx = self._bid_country.count() - 1
                model = cast(Any, self._bid_country.model())
                item: QStandardItem | None = model.item(idx)
                if item is not None:
                    item.setEnabled(False)
                    item.setSelectable(False)

        country_row.addWidget(country_label)
        country_row.addWidget(self._bid_country, 1)
        lay.addLayout(country_row)

        # 地区下拉 / Region dropdown
        region_row = QHBoxLayout()
        region_label = QLabel(self.i18n.t("settings.birdid_region_label"))
        region_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        region_label.setFixedWidth(160)

        self._bid_region = QComboBox()
        self._bid_region.addItem(self.i18n.t("birdid.region_entire_country"), None)
        self._bid_region.currentIndexChanged.connect(self._on_birdid_field_changed)

        region_row.addWidget(region_label)
        region_row.addWidget(self._bid_region, 1)
        lay.addLayout(region_row)

        # 国家切换时动态填充地区 / Dynamically populate regions on country change
        self._bid_country.currentTextChanged.connect(self._on_bid_country_changed)

        # ── 恢复已保存的国家/地区初值 / Restore saved country/region ──────────
        self._bid_applying: bool = True
        self._restore_birdid_country(cfg)
        self._bid_applying = False

        lay.addWidget(self._divider())

        # ── 鸟名显示格式 / Bird name display format ──────────────────────────
        # V4.x: name_format 此前只有 setter/属性,任何界面都改不了
        # (advanced_settings_dialog.py 被合并进设置中心时漏掉了这一项 UI)。
        # 复用其已有的 i18n 词条(advanced_settings.name_format*)补回入口。
        # V4.x: name_format previously had a setter/property but no UI at all
        # (dropped when advanced_settings_dialog.py was folded into this
        # Settings Center). Restored here, reusing its existing i18n keys.
        fmt_title = QLabel(self.i18n.t("advanced_settings.name_format"))
        fmt_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(fmt_title)

        fmt_row = QHBoxLayout()
        fmt_label = QLabel(self.i18n.t("settings.birdid_section_name_format"))
        fmt_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        fmt_label.setFixedWidth(160)
        self._bid_name_format = QComboBox()
        for value, key in (
            ("default", "advanced_settings.name_format_default"),
            ("avilist", "advanced_settings.name_format_avilist"),
            ("clements", "advanced_settings.name_format_clements"),
            ("birdlife", "advanced_settings.name_format_birdlife"),
            ("scientific", "advanced_settings.name_format_scientific"),
        ):
            self._bid_name_format.addItem(self.i18n.t(key), value)
        fmt_idx = self._bid_name_format.findData(cfg.name_format)
        self._bid_name_format.setCurrentIndex(fmt_idx if fmt_idx >= 0 else 0)
        self._bid_name_format.currentIndexChanged.connect(self._on_name_format_changed)
        fmt_row.addWidget(fmt_label)
        fmt_row.addWidget(self._bid_name_format, 1)
        lay.addLayout(fmt_row)

        fmt_hint = QLabel(self.i18n.t("advanced_settings.name_format_hint"))
        fmt_hint.setWordWrap(True)
        fmt_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:160px;"
        )
        lay.addWidget(fmt_hint)

        lay.addStretch(1)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    def _populate_bid_regions(self, country_code: str | None) -> None:
        """
        根据国家代码填充地区下拉菜单，不受 _bid_applying 守卫的影响。

        此方法是地区列表填充的核心实现，供 _on_bid_country_changed（用户交互路径）
        和 _restore_birdid_country（初始化路径）共同调用，从而绕开
        _on_bid_country_changed 中的 _bid_applying 守卫所带来的问题（C1 fix）。

        Populate the region dropdown for the given country_code, bypassing the
        _bid_applying guard.

        This is the core implementation shared by _on_bid_country_changed (user
        interaction path) and _restore_birdid_country (init path), so that
        restoring a saved sub-national region is not blocked by the guard in
        _on_bid_country_changed (C1 fix).

        参数 / Parameters:
            country_code (str | None): ISO 国家代码，或 None / "GLOBAL" 表示无子级地区。
                                       ISO country code, or None / "GLOBAL" for no sub-regions.
        """
        self._bid_region.clear()
        self._bid_region.addItem(self.i18n.t("birdid.region_entire_country"), None)

        is_english = self.i18n.current_lang.startswith("en")

        if country_code and country_code not in (None, "GLOBAL"):
            for country_entry in self._bid_regions_data.get("countries", []):
                if country_entry.get("code") == country_code:
                    if country_entry.get("has_regions") and country_entry.get("regions"):
                        for region_entry in country_entry["regions"]:
                            rc = region_entry.get("code", "")
                            if is_english:
                                region_name = region_entry.get("name", rc)
                            else:
                                region_name = (
                                    region_entry.get("name_cn")
                                    or region_entry.get("name", rc)
                                )
                            self._bid_region.addItem(region_name, rc)
                    break

    def _restore_birdid_country(self, cfg) -> None:
        """
        从配置恢复国家/地区下拉菜单的初始选中项。

        先尝试按保存的 country_code 匹配；若未匹配则按 selected_country 显示名匹配。
        恢复国家后直接调用 _populate_bid_regions 填充地区列表（绕开 _bid_applying 守卫），
        再恢复已保存的地区。

        Restore the initial selection of the country/region dropdowns from config.

        Tries to match by saved country_code first; falls back to selected_country
        display name. After restoring the country, calls _populate_bid_regions directly
        (bypassing the _bid_applying guard) to fill the region list, then restores the
        saved region.

        参数 / Parameters:
            cfg: advanced_config 实例 / AdvancedConfig instance.
        """
        saved_code = cfg.birdid_country_code
        saved_display = cfg.birdid_selected_country

        # top-10 已在下拉中的国家代码集合，用于判断是否需要动态补入
        # Set of country codes already in the dropdown (top-10), used to detect missing entries
        existing_codes = set(self._bid_country_list.values()) - {None, "GLOBAL", "SEP1", "SEP2", "MORE"}

        matched = False
        if saved_code is not None:
            if saved_code not in ("GLOBAL",) and saved_code not in existing_codes:
                # 非 top-10 国家：动态追加到下拉并选中，防止数据丢失
                # Non-top-10 country: dynamically append to dropdown and select it to prevent data loss
                extra_display = saved_display or saved_code
                self._bid_country.addItem(extra_display)
                self._bid_country_list[extra_display] = saved_code
                idx = self._bid_country.findText(extra_display)
                if idx >= 0:
                    self._bid_country.setCurrentIndex(idx)
                matched = True
            else:
                for display_name, code in self._bid_country_list.items():
                    if code == saved_code:
                        idx = self._bid_country.findText(display_name)
                        if idx >= 0:
                            self._bid_country.setCurrentIndex(idx)
                            matched = True
                        break

        if not matched:
            idx = self._bid_country.findText(saved_display)
            if idx >= 0:
                self._bid_country.setCurrentIndex(idx)

        # 直接填充地区（绕开 _bid_applying 守卫），再恢复已保存的地区选项
        # Populate regions directly (bypass guard), then restore the saved region
        current_code = self._bid_country_list.get(self._bid_country.currentText())
        self._populate_bid_regions(current_code)
        # 优先按 region_code(itemData)恢复,回退按显示名(兼容旧数据)
        # Restore by region_code (itemData) first; fall back to display name (legacy)
        saved_region_code = cfg.birdid_region_code
        idx = self._bid_region.findData(saved_region_code) if saved_region_code else -1
        if idx < 0:
            idx = self._bid_region.findText(cfg.birdid_selected_region or "")
        if idx >= 0:
            self._bid_region.setCurrentIndex(idx)

    def _on_bid_country_changed(self, country_display: str) -> None:
        """
        国家下拉切换时动态填充地区下拉菜单；选中「更多国家」则弹出完整列表对话框；
        普通切换完成后立即持久化(与设置中心统一即时保存模型一致)。

        仅在用户主动切换国家时触发（由 currentTextChanged 信号连接）；
        初始化/恢复路径请使用 _populate_bid_regions，以绕开 _bid_applying 守卫。

        Dynamically populate the region dropdown when the country selection
        changes; selecting "More countries" opens the full-list dialog.
        Persists immediately after a normal change (consistent with the
        Settings Center's unified immediate-save model).

        Triggered only by user interaction (connected to currentTextChanged signal).
        For the init/restore path use _populate_bid_regions directly to bypass the
        _bid_applying guard.

        参数 / Parameters:
            country_display (str): 当前选中的国家显示名 / Currently selected country display name.
        """
        if getattr(self, "_bid_applying", False):
            return

        country_code = self._bid_country_list.get(country_display)
        if country_code in ("SEP1", "SEP2"):
            return

        if country_code == "MORE":
            from advanced_config import get_advanced_config
            from ui.birdid_dock import show_country_picker_dialog

            top10_and_global = {
                "AU", "BR", "CN", "GB", "HK", "ID", "JP", "MY", "TW", "US", "GLOBAL",
            }
            # 取消时恢复用户上次保存的选项(而非当前下拉文本——此刻它已是
            # "更多国家"本身),与 birdid_dock 的既有行为一致。
            # On cancel, restore the last SAVED selection (not the combo's
            # current text, which is already "More countries" itself at this
            # point) — matching the existing birdid_dock behavior.
            fallback = get_advanced_config().birdid_selected_country or self.i18n.t("birdid.country_auto_gps")
            show_country_picker_dialog(
                parent=self,
                i18n=self.i18n,
                regions_data=self._bid_regions_data,
                country_combo=self._bid_country,
                country_list=self._bid_country_list,
                exclude_codes=top10_and_global,
                fallback_display=fallback,
                more_item_text=self.i18n.t("birdid.country_more"),
            )
            # show_country_picker_dialog 内部会重设 currentText,重新触发本回调
            # (确认选择新国家) 或什么都不做(取消,已恢复为 fallback);两种情况
            # 都无需在此继续处理。
            # show_country_picker_dialog re-sets currentText internally, which
            # re-enters this callback (on confirm) or is a no-op (on cancel,
            # already restored to fallback); nothing further to do here.
            return

        self._populate_bid_regions(country_code)
        self._on_birdid_field_changed()

    def _save_birdid(self) -> None:
        """
        将识鸟页当前值批量写回 advanced_config 并保存(幂等)。

        由 _on_birdid_field_changed 在每次字段变化时调用，实现统一即时保存；
        同时保留作为测试与偶发兜底调用的辅助方法。

        依次调用:
          set_birdid_auto_identify — 自动识鸟开关
          set_birdid_confidence   — 置信度 (30-95)
          set_birdid_region       — 数据源/国家代码/地区代码及其显示名称

        Bulk-flush the Bird-ID page's current values to advanced_config
        (idempotent).

        Called by _on_birdid_field_changed on every field change to implement
        unified immediate saving; also kept as a helper for tests and
        occasional defensive calls.

        Calls in order:
          set_birdid_auto_identify — auto-identify toggle
          set_birdid_confidence   — confidence (30-95)
          set_birdid_region       — source / country code / region code and display names
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # 自动识鸟开关 / Auto-identify toggle
        cfg.set_birdid_auto_identify(self._bid_auto.isChecked())

        # 写入关键字开关 / write-keywords toggle
        cfg.set_birdid_write_keywords(self._bid_keywords.isChecked())

        # 置信度(set_birdid_confidence 不内部 save，此处显式补调，确保值持久化)
        # Confidence (set_birdid_confidence doesn't call save internally; call it explicitly here)
        cfg.set_birdid_confidence(self._bid_conf.value())
        cfg.save()

        # 数据源 / Data source
        use_ebird: bool = self._bid_ebird.isChecked()

        # 国家 / Country
        country_display = self._bid_country.currentText()
        country_code = self._bid_country_list.get(country_display)
        # 仅归一化分隔符伪代码为 None；"GLOBAL" 保留原样以便重新打开时能正确恢复选项
        # Only normalize separator pseudo-codes to None; keep "GLOBAL" as-is so the
        # dropdown can be correctly restored on re-open.
        if country_code in ("SEP1", "SEP2", "MORE"):
            country_code = None

        # 兜底守卫：若下拉解析到 None 而原存储值是真实国家代码（非 None/GLOBAL），
        # 保留原值以防止非 top-10 国家被意外覆盖为 None。
        # Fallback guard: if the dropdown resolves to None but the stored value is a real
        # country code (non-None/GLOBAL), preserve the stored value to prevent silent data
        # loss for non-top-10 countries.
        if country_code is None:
            stored_code = cfg.birdid_country_code
            if stored_code and stored_code not in ("GLOBAL",):
                country_code = stored_code

        # 地区 / Region
        region_display = self._bid_region.currentText()
        region_code: str | None = self._bid_region.currentData()

        cfg.set_birdid_region(
            use_ebird=use_ebird,
            country_code=country_code,
            selected_country=country_display,
            region_code=region_code,
            selected_region=region_display,
        )

    def _on_birdid_field_changed(self, *_args) -> None:
        """
        识鸟页任一字段(自动识鸟/关键字/置信度/数据源/国家/地区)变化时的
        统一即时保存回调；恢复初值期间(_bid_applying)不触发，避免重复写盘。

        Unified immediate-save callback for any Bird-ID page field
        (auto-identify / keywords / confidence / source / country / region);
        skipped while restoring initial values (_bid_applying) to avoid
        redundant writes.
        """
        if getattr(self, "_bid_applying", False):
            return
        self._save_birdid()

    def _on_name_format_changed(self, _index: int) -> None:
        """鸟名显示格式下拉变化 → 立即持久化。/ Persist immediately on change."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_name_format(self._bid_name_format.currentData())
        cfg.save()

    # ── 精选页协同逻辑 / Culling page coordination logic ──────────────────────

    def _apply_algo_visibility(self) -> None:
        """
        按当前评星算法切换滑块行可见性：v2 显示「3星配额」行，v1 显示
        锐度/美学阈值行（两套控件均常驻构建，只切显示）。

        Toggle slider-row visibility by the current rating algorithm: the
        quota row under v2, the legacy sharpness/aesthetics rows under v1
        (both sets stay constructed; only visibility changes).
        """
        for w in self._quota_row_widgets:
            w.setVisible(self._rating_v2)
        for w in self._sharp_row_widgets + self._nima_row_widgets:
            w.setVisible(not self._rating_v2)

    def _on_algo_selected(self, algo_key: str) -> None:
        """
        评星算法切换回调（由「高级选项」区的旧版 V1 复选框触发）：立即持久化
        rating_algorithm（下次跑批生效），同步复选框勾选态并切换滑块行可见性。

        Rating-algorithm switch callback (triggered by the legacy-V1 checkbox
        in the "Advanced" section): persists rating_algorithm immediately
        (takes effect on the next run), syncs the checkbox state, and toggles
        slider-row visibility.

        参数 / Parameters:
            algo_key (str): 目标算法 key（"v1"/"v2"）/ target algorithm key.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        # V4.8: 切到 V1 前先告知代价。V1 把同一个阈值同时当判废线和达标线
        # (rating_engine 的 min_nima 与 nima_threshold 同源)，1★/2★ 恒为空集，
        # 只会产生 0★/3★；实测 433 张批次在美学阈值 7.0 下 99.7% 被判 0★。
        # 用户取消则保持 V2，复选框回滚。
        # V4.8: warn before switching to V1. It feeds one value into both the
        # reject line and the pass line, so 1★/2★ are always empty — only 0★/3★
        # are reachable. On a real 433-shot batch an aesthetics threshold of 7.0
        # rejected 99.7% of the photos. Cancelling keeps V2 and reverts the box.
        if algo_key == "v1" and not self._confirm_switch_to_v1(cfg):
            checkbox = getattr(self, "_algo_legacy_checkbox", None)
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
            return

        cfg.set_rating_algorithm(algo_key)
        cfg.save()
        self._rating_v2 = algo_key == "v2"
        checkbox = getattr(self, "_algo_legacy_checkbox", None)
        if checkbox is not None:
            checkbox.blockSignals(True)
            checkbox.setChecked(algo_key == "v1")
            checkbox.blockSignals(False)
        self._apply_algo_visibility()

    # V1 美学阈值高于此值时追加「大量判废」的额外警告 / extra warning above this
    _V1_NIMA_WARN_LEVEL = 5.5

    def _confirm_switch_to_v1(self, cfg) -> bool:
        """
        切换到 V1 前的确认对话框。

        说明 V1 只产生 0★/3★（1★/2★ 恒为空集），并回显当前两个阈值；
        美学阈值偏高时追加一段「会导致大量判废」的警告。

        参数:
        cfg: advanced_config 单例，用于读取当前锐度/美学阈值

        返回:
        bool: True=用户确认切换到 V1；False=取消，维持 V2

        Confirmation dialog shown before switching to V1. Explains that V1 can
        only produce 0★/3★, echoes the current thresholds, and appends an extra
        warning when the aesthetics threshold is high enough to reject most
        photos. Returns True when the user confirms the switch.
        """
        from PySide6.QtWidgets import QMessageBox

        from core.skill_presets import get_skill_level_thresholds

        # 窗口不可见 = 没有用户在看（测试、程序化 setChecked、CLI 构造），
        # 此时弹模态框会永久阻塞——没人可点。这种场景直接放行。
        # Not visible = nobody is looking (tests, programmatic setChecked,
        # headless construction). A modal dialog would block forever with no
        # one to dismiss it, so skip the prompt and allow the switch.
        if not self.isVisible():
            return True

        sharp, nima = get_skill_level_thresholds(cfg.skill_level, cfg)
        advice = (
            self.i18n.t("settings.culling_algo_v1_warn_high", nima=nima)
            if float(nima) > self._V1_NIMA_WARN_LEVEL else ""
        )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.i18n.t("settings.culling_algo_v1_warn_title"))
        box.setText(self.i18n.t(
            "settings.culling_algo_v1_warn_body",
            nima=nima, sharp=int(sharp), advice=advice))
        ok = box.addButton(self.i18n.t("settings.culling_algo_v1_warn_ok"),
                           QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.i18n.t("settings.culling_algo_v1_warn_cancel"),
                      QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(box.buttons()[-1])   # 默认保持 V2 / default to keeping V2
        box.exec()
        return box.clickedButton() is ok

    def _on_algo_legacy_toggled(self, _state: int) -> None:
        """
        「使用旧版绝对阈值评星(V1)」复选框状态变化回调：委托给 _on_algo_selected。

        Callback for the "use legacy fixed-threshold rating (V1)" checkbox;
        delegates to _on_algo_selected.
        """
        self._on_algo_selected("v1" if self._algo_legacy_checkbox.isChecked() else "v2")

    def _select_skill_radio(self, level_key: str) -> None:
        """
        刷新技能等级卡片的选中状态。

        Refresh the selected state of skill-level cards.

        参数 / Parameters:
            level_key (str): 当前选中的档位 key / Currently selected level key.
        """
        for key, card in self._skill_cards.items():
            card.set_selected(key == level_key)

    def _on_skill_preset_selected(self, level_key: str) -> None:
        """
        技能等级预设被选中时的回调:立即持久化 skill_level,非"自定义"档还
        联动填充并持久化阈值滑块(min_sharpness/min_nima/配额)。

        将 _suppress 置为 True,填充阈值滑块,再置 False,防止回环触发 _on_cull_threshold_changed。
        "custom" 档只刷新卡片,不覆写滑块(维持用户当前自定义值)。

        Callback when a skill-level preset card is clicked: persists
        skill_level immediately; non-"custom" levels also fill and persist
        the threshold sliders (min_sharpness/min_nima/quota).

        Sets _suppress=True, fills threshold sliders from the preset, then False,
        preventing re-entrant calls to _on_cull_threshold_changed.
        "custom" level only refreshes the card state without overwriting sliders
        (keeps the user's current custom values).

        参数 / Parameters:
            level_key (str): 被选中的档位 key / Selected skill level key.
        """
        from advanced_config import get_advanced_config
        from core.skill_presets import get_skill_level_thresholds

        self._current_skill_key = level_key
        self._select_skill_radio(level_key)
        cfg = get_advanced_config()
        cfg.set_skill_level(level_key)
        if level_key == "custom":
            cfg.save()
            return

        # get_skill_level_thresholds 返回 Tuple[int, float]: (sharpness, aesthetics)
        # get_skill_level_thresholds returns Tuple[int, float]: (sharpness, aesthetics)
        th = get_skill_level_thresholds(level_key)
        self._suppress = True
        try:
            # V4.6+三段配额: v2 下预设联动配额分配条(3★/2★ 一对);v1 联动旧阈值滑块
            # V4.6 + 3-seg quota: presets drive the QuotaBar (3★/2★ pair) under v2
            if getattr(self, "_rating_v2", False) and self._cull_quota is not None:
                from core.rating_quota import (
                    SKILL_QUOTA3, SKILL_QUOTA2, DEFAULT_QUOTA3, DEFAULT_QUOTA2)
                self._cull_quota.set_quotas(
                    int(SKILL_QUOTA3.get(level_key, DEFAULT_QUOTA3)),
                    int(SKILL_QUOTA2.get(level_key, DEFAULT_QUOTA2)))
            self._cull_sharp.setValue(int(th[0]))
            self._cull_nima.setValue(int(round(th[1] * 10)))
        finally:
            self._suppress = False
        cfg.set_min_sharpness(int(th[0]))
        cfg.set_min_nima(float(th[1]))
        cfg.save()

    def _on_cull_threshold_changed(self, *_) -> None:
        """
        任一阈值滑块(AI 置信度除外)被用户拖动时的回调。

        若 _suppress 为 True(由技能等级预设填充触发)则直接返回,避免回环。
        否则将当前技能等级切换为"自定义"并立即持久化(min_sharpness/min_nima
        及自定义档记忆字段 custom_sharpness/custom_aesthetics/custom_quota3)。

        Callback fired when any threshold slider (except AI confidence) changes.

        If _suppress is True (triggered by preset fill), returns immediately to
        avoid re-entry. Otherwise switches skill level to "custom" and persists
        immediately (min_sharpness/min_nima plus the custom-mode memory fields
        custom_sharpness/custom_aesthetics/custom_quota3).
        """
        if getattr(self, "_suppress", False):
            return
        self._current_skill_key = "custom"
        self._select_skill_radio("custom")

        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_skill_level("custom")
        cfg.set_min_sharpness(self._cull_sharp.value())
        cfg.set_min_nima(self._cull_nima.value() / 10.0)
        cfg.set_custom_sharpness(self._cull_sharp.value())
        cfg.set_custom_aesthetics(self._cull_nima.value() / 10.0)
        if getattr(self, "_rating_v2", False) and self._cull_quota is not None:
            cfg.set_custom_quota3(self._cull_quota.quota3())
            cfg.set_custom_quota2(self._cull_quota.quota2())
        cfg.save()

    def _on_cull_quota_changed(self, q3: int, q2: int) -> None:
        """
        星级配额分配条被用户拖动的回调:切自定义档并持久化 custom_quota3/quota2。

        1★ = 100 − q3 − q2 为算术余量,不单独存储。_suppress 期间(预设联动)
        由 set_quotas 静默填充、不发信号,故此处无需再判 _suppress。

        Callback when the user drags the QuotaBar: switch to the custom skill
        level and persist custom_quota3/quota2 (1★ is the derived remainder).
        """
        self._current_skill_key = "custom"
        self._select_skill_radio("custom")

        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_skill_level("custom")
        cfg.set_custom_quota3(q3)
        cfg.set_custom_quota2(q2)
        cfg.save()

    def _on_ai_confidence_changed(self, value: int) -> None:
        """AI 置信度滑块变化 → 立即持久化(与技能档/自定义状态无关)。
        AI confidence slider changed → persist immediately (independent of
        skill level / custom state)."""
        from advanced_config import get_advanced_config
        get_advanced_config().set_min_confidence(value / 100.0)
        get_advanced_config().save()

    def _on_flight_check_changed(self, _state: int) -> None:
        """飞鸟检测开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_flight_check(self._cull_flight.isChecked())
        cfg.save()

    def _on_burst_check_changed(self, _state: int) -> None:
        """
        连拍检测开关变化 → 立即持久化,并同步连拍速度行的启用状态
        (关闭连拍检测时该数值无意义,置灰更清楚地表达依赖关系)。

        Burst-detection toggle changed → persist immediately and sync the
        burst-speed row's enabled state (graying it out communicates the
        dependency more clearly than a same-level standalone row).
        """
        from advanced_config import get_advanced_config
        checked = self._cull_burst.isChecked()
        cfg = get_advanced_config()
        cfg.set_burst_check(checked)
        cfg.save()
        self._set_burst_fps_enabled(checked)

    def _set_burst_fps_enabled(self, enabled: bool) -> None:
        """按连拍检测开关状态启用/禁用连拍速度行控件。
        Enable/disable the burst-speed row widgets per the burst toggle."""
        for w in self._burst_fps_row_widgets:
            w.setEnabled(enabled)

    def _on_burst_fps_changed(self, value: int) -> None:
        """连拍速度变化 → 立即持久化。/ Persist immediately on change."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_burst_fps(value)
        cfg.save()

    def _on_rescue_changed(self, _state: int) -> None:
        """无鸟补救扫描开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_rescue_scan_enabled(self._cull_rescue.isChecked())
        cfg.save()

    def _toggle_culling_advanced(self) -> None:
        """展开/收起精选页底部「高级选项」区。/ Expand/collapse the Advanced disclosure."""
        self._advanced_expanded = not self._advanced_expanded
        self._advanced_content.setVisible(self._advanced_expanded)
        svg = "arrow-down.svg" if self._advanced_expanded else "arrow-right.svg"
        self._advanced_toggle_btn.setIcon(load_tinted_icon(svg, ICON_IDLE, 14))
        self._advanced_toggle_btn.setIconSize(QSize(14, 14))
        self._advanced_toggle_btn.setText(f"  {self.i18n.t('settings.culling_advanced_toggle')}")

    def _save_culling(self) -> None:
        """
        将精选页当前值批量写回 advanced_config 并保存。

        每个控件的 changed 信号已各自即时保存(见上方各 _on_*_changed 方法);
        本方法作为一次性批量落盘的兜底/测试辅助保留，行为与逐控件即时保存
        叠加时完全等价(幂等)。

        Bulk-flush the culling page's current values to advanced_config.

        Each control already persists on its own changed signal (see the
        _on_*_changed methods above); this method remains as an idempotent
        one-shot fallback / test helper — calling it after the per-control
        saves is a no-op difference.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_min_confidence(self._cull_ai.value() / 100.0)
        cfg.set_min_sharpness(self._cull_sharp.value())
        cfg.set_min_nima(self._cull_nima.value() / 10.0)
        cfg.set_burst_fps(self._cull_burst_fps.value())
        cfg.set_flight_check(self._cull_flight.isChecked())
        cfg.set_burst_check(self._cull_burst.isChecked())
        cfg.set_rescue_scan_enabled(self._cull_rescue.isChecked())
        cfg.set_skill_level(self._current_skill_key)
        # 当处于自定义档时同步写回 custom_* 字段，避免 CLI 路径读到陈旧值。
        # When in custom mode, also persist custom_* fields so CLI path reads fresh values.
        if self._current_skill_key == "custom":
            cfg.set_custom_sharpness(self._cull_sharp.value())
            cfg.set_custom_aesthetics(self._cull_nima.value() / 10.0)
            # V4.6+三段配额: v2 自定义配额(3★/2★)同步写回,1★ 为余量不存储
            # V4.6 + 3-seg quota: persist custom 3★/2★ quotas under v2 (1★ derived)
            if getattr(self, "_rating_v2", False) and self._cull_quota is not None:
                cfg.set_custom_quota3(self._cull_quota.quota3())
                cfg.set_custom_quota2(self._cull_quota.quota2())
        cfg.save()

    # ── 输出页 / Output page ──────────────────────────────────────────────────

    def _build_output_page(self) -> QWidget:
        """
        构建输出设置页，包含：分目录布局(含连拍子目录开关)、元数据写入方式
        （XMP）、通用设置(预览管理/删除确认/清理缓存)。

        初值来自 advanced_config 的 folder_layout / burst_group_folders /
        metadata_write_mode / keep_temp_files / completion_sound_enabled /
        detail_metadata_for_rejected / delete_confirm 字段；每个控件的
        changed 信号即时持久化(与设置中心统一即时保存模型一致)。

        Build the Output settings page.

        Contains: folder layout (with the burst-subfolder toggle), XMP
        metadata write mode, and a General section (preview management /
        delete confirmation / cache clearing). Initial values come from
        advanced_config; every control persists immediately on change.

        返回 / Returns:
            QWidget: 输出设置页 / Output settings page widget.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # ── 容器 + 滚动区 / Container + scroll area ───────────────────────────
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # macOS 原生 QStyle 下 QScrollArea 的 viewport 不会继承祖先 QDialog 的
        # QSS 背景色，系统外观为浅色时会露出原生浅灰 #ececec（与深色主题不符）。
        # 显式设 transparent 让 QDialog 的深色背景透出来（同 birdid_dock.py 已验证的写法）。
        # On macOS the native QStyle paints a QScrollArea's viewport without
        # inheriting the ancestor QDialog's QSS background, so it falls back to
        # the native light gray #ececec when the system appearance is Light —
        # clashing with the dark theme. Setting transparent lets the QDialog's
        # dark background show through (same fix already proven in birdid_dock.py).
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ── 分目录布局 / Folder layout ────────────────────────────────────────
        fl_title = QLabel(self.i18n.t("advanced_settings.folder_layout"))
        fl_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(fl_title)

        fl_row = QHBoxLayout()
        fl_label = QLabel(self.i18n.t("advanced_settings.folder_layout_label"))
        fl_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        fl_label.setFixedWidth(160)

        self._folder_layout_combo = QComboBox()
        self._folder_layout_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_rating_first"), "rating-first"
        )
        self._folder_layout_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_species_first"), "species-first"
        )
        # V4.6(Paul P1): 平铺——识别评分但不移动文件 / flat: rate in place
        self._folder_layout_combo.addItem(
            self.i18n.t("advanced_settings.folder_layout_flat"), "flat"
        )
        # 恢复已保存的布局选项 / Restore saved folder layout
        fl_idx = self._folder_layout_combo.findData(cfg.folder_layout)
        self._folder_layout_combo.setCurrentIndex(fl_idx if fl_idx >= 0 else 0)
        self._folder_layout_combo.currentIndexChanged.connect(self._on_folder_layout_changed)

        fl_row.addWidget(fl_label)
        fl_row.addWidget(self._folder_layout_combo)
        fl_row.addStretch(1)
        lay.addLayout(fl_row)

        fl_hint = QLabel(self.i18n.t("advanced_settings.folder_layout_hint"))
        fl_hint.setWordWrap(True)
        fl_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:160px;"
        )
        lay.addWidget(fl_hint)

        # 连拍子目录开关(Paul P1，从精选页挪到这里——它是"目录怎么组织"的一部分，
        # 不是检测行为):关=连拍照片按星级/鸟种常规归档。
        # Burst-subfolder toggle (Paul P1; moved here from the culling page —
        # it's about directory organization, not detection behavior): off =
        # bursts filed normally by rating/species.
        self._cull_burst_folders = QCheckBox(self.i18n.t("settings.culling_burst_folders_label"))
        self._cull_burst_folders.setChecked(cfg.burst_group_folders)
        self._cull_burst_folders.setStyleSheet(self._checkbox_qss())
        self._cull_burst_folders.stateChanged.connect(self._on_burst_folders_changed)
        lay.addWidget(self._cull_burst_folders)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        lay.addWidget(self._divider())

        # ── XMP 写入方式区标题 / XMP write mode section title ─────────────────
        xmp_title = QLabel(self.i18n.t("advanced_settings.xmp_write_mode"))
        xmp_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(xmp_title)

        # XMP 写入方式单选组 / XMP write mode radio group
        self._xmp_button_group = QButtonGroup(self)

        # 选项1: 嵌入写入 / Embedded
        self._xmp_embedded = QRadioButton(
            self.i18n.t("advanced_settings.write_embedded")
        )
        self._xmp_embedded.setStyleSheet(_radio_style())
        self._xmp_button_group.addButton(self._xmp_embedded, 0)
        lay.addWidget(self._xmp_embedded)

        emb_hint = QLabel(self.i18n.t("advanced_settings.xmp_mode_embedded_hint"))
        emb_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(emb_hint)

        # 选项2: 侧车文件 / Sidecar
        self._xmp_sidecar = QRadioButton(
            self.i18n.t("advanced_settings.write_sidecar")
        )
        self._xmp_sidecar.setStyleSheet(_radio_style())
        self._xmp_button_group.addButton(self._xmp_sidecar, 1)
        lay.addWidget(self._xmp_sidecar)

        sidecar_hint = QLabel(self.i18n.t("advanced_settings.xmp_mode_sidecar_hint"))
        sidecar_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(sidecar_hint)

        # 选项3: 不写入 / None
        self._xmp_none = QRadioButton(
            self.i18n.t("advanced_settings.write_none")
        )
        self._xmp_none.setStyleSheet(_radio_style())
        self._xmp_button_group.addButton(self._xmp_none, 2)
        lay.addWidget(self._xmp_none)

        # 恢复已保存的 XMP 写入模式 / Restore saved XMP write mode
        try:
            global_mode = cfg.get_metadata_write_mode()
        except Exception:
            global_mode = "embedded"
        if global_mode == "sidecar":
            self._xmp_sidecar.setChecked(True)
        elif global_mode == "none":
            self._xmp_none.setChecked(True)
        else:
            self._xmp_embedded.setChecked(True)
        # 三选一互斥组：任一按钮的 toggled(True) 都对应一次实际切换 /
        # Any button's toggled(True) corresponds to one actual switch.
        self._xmp_button_group.buttonToggled.connect(self._on_xmp_mode_changed)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        lay.addWidget(self._divider())

        # ── 通用区 / General section ───────────────────────────────────────────
        # 原「预览管理」改名「通用」并入删除确认/清理缓存,避免把不相关设置
        # 硬塞进"预览管理"这个过窄的标题下。
        # Renamed from "Preview Management" to "General" and folded in delete
        # confirmation / cache clearing, instead of cramming unrelated
        # settings under an overly narrow heading.
        general_title = QLabel(self.i18n.t("advanced_settings.section_general"))
        general_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(general_title)

        # 保留预览图 / Keep preview files
        self._keep_temp_files = QCheckBox(self.i18n.t("advanced_settings.keep_preview"))
        self._keep_temp_files.setChecked(cfg.keep_temp_files)
        self._keep_temp_files.setStyleSheet(self._checkbox_qss())
        self._keep_temp_files.stateChanged.connect(self._on_keep_temp_files_changed)
        lay.addWidget(self._keep_temp_files)

        keep_hint = QLabel(self.i18n.t("advanced_settings.keep_preview_hint"))
        keep_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(keep_hint)

        # 完成提示音 / Completion sound
        self._completion_sound = QCheckBox(
            self.i18n.t("advanced_settings.completion_sound")
        )
        self._completion_sound.setChecked(cfg.completion_sound_enabled)
        self._completion_sound.setStyleSheet(self._checkbox_qss())
        self._completion_sound.stateChanged.connect(self._on_completion_sound_changed)
        lay.addWidget(self._completion_sound)

        sound_hint = QLabel(self.i18n.t("advanced_settings.completion_sound_hint"))
        sound_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(sound_hint)

        # 拒绝照片详情元数据 / Detail metadata for rejected photos
        self._detail_meta_for_rejected = QCheckBox(
            self.i18n.t("advanced_settings.detail_metadata_for_rejected")
        )
        self._detail_meta_for_rejected.setChecked(cfg.get_detail_metadata_for_rejected())
        self._detail_meta_for_rejected.setStyleSheet(self._checkbox_qss())
        self._detail_meta_for_rejected.stateChanged.connect(self._on_detail_metadata_changed)
        lay.addWidget(self._detail_meta_for_rejected)

        detail_hint = QLabel(self.i18n.t("advanced_settings.detail_metadata_hint"))
        detail_hint.setWordWrap(True)
        detail_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(detail_hint)

        # 删除确认弹窗(此前误勾"不再确认"后无处恢复,补上开关入口)
        # Delete-confirmation dialog (previously had no way back once
        # dismissed via "don't ask again"; this restores the toggle)
        self._delete_confirm = QCheckBox(self.i18n.t("advanced_settings.delete_confirm_label"))
        self._delete_confirm.setChecked(cfg.delete_confirm)
        self._delete_confirm.setStyleSheet(self._checkbox_qss())
        self._delete_confirm.stateChanged.connect(self._on_delete_confirm_changed)
        lay.addWidget(self._delete_confirm)

        delete_confirm_hint = QLabel(self.i18n.t("advanced_settings.delete_confirm_hint"))
        delete_confirm_hint.setWordWrap(True)
        delete_confirm_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(delete_confirm_hint)

        # 清理所有预览缓存(接入 parent().directory_path 当前目录)
        # Clear all preview caches (targets the current directory via
        # parent().directory_path)
        clear_cache_row = QHBoxLayout()
        clear_cache_btn = QPushButton(self.i18n.t("advanced_settings.clear_cache_button"))
        clear_cache_btn.setObjectName("secondary")
        clear_cache_btn.setFixedHeight(32)
        clear_cache_btn.clicked.connect(self._on_clear_cache_clicked)
        clear_cache_row.addWidget(clear_cache_btn)
        clear_cache_row.addStretch(1)
        lay.addLayout(clear_cache_row)

        clear_cache_hint = QLabel(self.i18n.t("advanced_settings.clear_cache_hint"))
        clear_cache_hint.setWordWrap(True)
        clear_cache_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;"
        )
        lay.addWidget(clear_cache_hint)

        lay.addStretch(1)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    def _save_output(self) -> None:
        """
        将输出页当前值批量写回 advanced_config 并保存(幂等)。

        每个控件的 changed 信号已各自即时保存(见下方各 _on_*_changed 方法);
        本方法作为一次性批量落盘的兜底/测试辅助保留。

        依次调用:
          set_folder_layout          — 分目录布局
          set_burst_group_folders    — 连拍归入独立子文件夹
          set_metadata_write_mode    — XMP 元数据写入方式
          set_keep_temp_files        — 保留预览图
          set_completion_sound_enabled — 完成提示音
          set_detail_metadata_for_rejected — 拒绝照片详情元数据
          set_delete_confirm         — 删除确认弹窗

        Bulk-flush the Output page's current values to advanced_config
        (idempotent). Each control already persists on its own changed
        signal (see the _on_*_changed methods below); this method remains
        as a one-shot fallback / test helper.

        Calls in order:
          set_folder_layout          — folder layout
          set_burst_group_folders    — group bursts into subfolders
          set_metadata_write_mode    — XMP metadata write mode
          set_keep_temp_files        — keep preview files
          set_completion_sound_enabled — completion sound
          set_detail_metadata_for_rejected — detail metadata for rejected
          set_delete_confirm         — delete-confirmation dialog
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # 分目录布局 / Folder layout
        cfg.set_folder_layout(self._folder_layout_combo.currentData())
        cfg.set_burst_group_folders(self._cull_burst_folders.isChecked())

        # XMP 写入方式 / XMP metadata write mode
        btn_id = self._xmp_button_group.checkedId()
        mode_map = {0: "embedded", 1: "sidecar", 2: "none"}
        cfg.set_metadata_write_mode(mode_map.get(btn_id, "embedded"))

        # 通用 / General
        cfg.set_keep_temp_files(self._keep_temp_files.isChecked())
        cfg.set_completion_sound_enabled(self._completion_sound.isChecked())
        cfg.set_detail_metadata_for_rejected(self._detail_meta_for_rejected.isChecked())
        cfg.set_delete_confirm(self._delete_confirm.isChecked())

        cfg.save()

    def _on_folder_layout_changed(self, _index: int) -> None:
        """分目录布局变化 → 立即持久化。/ Persist immediately on change."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_folder_layout(self._folder_layout_combo.currentData())
        cfg.save()

    def _on_burst_folders_changed(self, _state: int) -> None:
        """连拍子目录开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_burst_group_folders(self._cull_burst_folders.isChecked())
        cfg.save()

    def _on_xmp_mode_changed(self, _button, checked: bool) -> None:
        """
        XMP 写入方式单选组变化 → 立即持久化。三选一互斥组每次切换会先后收到
        旧按钮 toggled(False) 和新按钮 toggled(True)，只在 checked=True 时处理一次。

        XMP write-mode radio group changed → persist immediately. A mutually
        exclusive group fires toggled(False) on the old button then
        toggled(True) on the new one; only act on the True firing.
        """
        if not checked:
            return
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        btn_id = self._xmp_button_group.checkedId()
        mode_map = {0: "embedded", 1: "sidecar", 2: "none"}
        cfg.set_metadata_write_mode(mode_map.get(btn_id, "embedded"))
        cfg.save()

    def _on_keep_temp_files_changed(self, _state: int) -> None:
        """保留预览图开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_keep_temp_files(self._keep_temp_files.isChecked())
        cfg.save()

    def _on_completion_sound_changed(self, _state: int) -> None:
        """完成提示音开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_completion_sound_enabled(self._completion_sound.isChecked())
        cfg.save()

    def _on_detail_metadata_changed(self, _state: int) -> None:
        """拒绝照片详情元数据开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_detail_metadata_for_rejected(self._detail_meta_for_rejected.isChecked())
        cfg.save()

    def _on_delete_confirm_changed(self, _state: int) -> None:
        """删除确认弹窗开关变化 → 立即持久化。/ Persist immediately on toggle."""
        from advanced_config import get_advanced_config
        cfg = get_advanced_config()
        cfg.set_delete_confirm(self._delete_confirm.isChecked())
        cfg.save()

    def _on_clear_cache_clicked(self) -> None:
        """
        「清理所有预览缓存」按钮点击回调。

        目标目录取自父窗口(主窗口)当前打开的 directory_path——设置中心本身
        不持有目录概念。删除前二次确认(不可撤销的破坏性操作)；删除后同步
        清空该目录 report.db 里指向已删缓存文件的路径字段，避免结果浏览器
        读到死链接。原始照片不受影响，仅清 .superpicky/cache。

        "Clear all preview caches" button callback.

        The target directory comes from the parent (main) window's currently
        open directory_path — Settings Center itself has no directory concept.
        Confirms before deleting (an irreversible destructive action); after
        deletion, also clears the report.db path columns that pointed at the
        now-gone cache files, so the results browser doesn't read dangling
        paths. Original photos are untouched — only .superpicky/cache is removed.
        """
        import shutil

        from ui.custom_dialogs import StyledMessageBox

        t = self.i18n.t
        btn_title = t("advanced_settings.clear_cache_button")

        directory = getattr(self.parent(), "directory_path", None)
        if not directory or not os.path.isdir(directory):
            StyledMessageBox.information(self, btn_title, t("advanced_settings.clear_cache_no_dir"))
            return

        cache_dir = os.path.join(directory, ".superpicky", "cache")
        if not os.path.isdir(cache_dir):
            StyledMessageBox.information(self, btn_title, t("advanced_settings.clear_cache_none"))
            return

        reply = StyledMessageBox.question(
            self,
            t("advanced_settings.clear_cache_confirm_title"),
            t("advanced_settings.clear_cache_confirm_msg", directory=os.path.basename(directory)),
        )
        if reply != StyledMessageBox.Yes:
            return

        try:
            shutil.rmtree(cache_dir)
        except Exception as e:
            StyledMessageBox.warning(self, btn_title, str(e))
            return

        # 清空数据库里指向已删除缓存文件的路径字段(与 photo_processor 里
        # _cleanup_temp_files 收尾时的清理动作一致),避免结果浏览器读到死链接。
        # Clear the DB's now-dangling cache path columns (mirrors the cleanup
        # photo_processor._cleanup_temp_files does after a run) so the results
        # browser doesn't read stale paths.
        db_path = os.path.join(directory, ".superpicky", "report.db")
        if os.path.exists(db_path):
            try:
                from tools.report_db import ReportDB
                db = ReportDB(directory)
                db.clear_cache_paths()
                db.close()
            except Exception:
                pass

        StyledMessageBox.information(self, btn_title, t("advanced_settings.clear_cache_done"))

    # ── 视频页 / Video page ───────────────────────────────────────────────────

    def _build_video_page(self) -> QWidget:
        """
        构建视频处理设置页。

        包含：主流程视频总开关、识别模式下拉、抽帧上限 SpinBox、
        YOLO 置信度阈值 SpinBox、鸟种识别/飞行检测开关。

        控件范围与 advanced_config 默认值对齐:
          video_max_frames       : 30-240（存储整数，默认 60）
          video_yolo_threshold   : 滑块 30-90 对应浮点 0.30-0.90（默认 0.5 → 50）
          video_min_segment_frames: 1-10（默认 2，不公开在 UI 中）

        Build the Video processing settings page.

        Contains: main-flow video master toggle, recognition mode dropdown,
        max frames SpinBox, YOLO threshold SpinBox, and species/flight
        detection toggles.

        Widget ranges are aligned with advanced_config defaults/clamps:
          video_max_frames      : 30-240 (integer, default 60)
          video_yolo_threshold  : slider 30-90 maps to float 0.30-0.90
                                  (default 0.5 → 50)

        返回 / Returns:
            QWidget: 视频设置页 / Video settings page widget.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        raw = cfg.config  # 直接访问 dict，这些字段无 property / Direct dict access

        # ── 容器 + 滚动区 / Container + scroll area ───────────────────────────
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # macOS 原生 QStyle 下 QScrollArea 的 viewport 不会继承祖先 QDialog 的
        # QSS 背景色，系统外观为浅色时会露出原生浅灰 #ececec（与深色主题不符）。
        # 显式设 transparent 让 QDialog 的深色背景透出来（同 birdid_dock.py 已验证的写法）。
        # On macOS the native QStyle paints a QScrollArea's viewport without
        # inheriting the ancestor QDialog's QSS background, so it falls back to
        # the native light gray #ececec when the system appearance is Light —
        # clashing with the dark theme. Setting transparent lets the QDialog's
        # dark background show through (same fix already proven in birdid_dock.py).
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)

        # ── 总开关区 / Master toggle section ─────────────────────────────────
        self._video_auto_check = QCheckBox(self.i18n.t("video_opts.enable_checkbox"))
        self._video_auto_check.setChecked(bool(raw.get("video_auto_process_in_main", False)))
        self._video_auto_check.setStyleSheet(self._checkbox_qss())
        self._video_auto_check.setToolTip(self.i18n.t("video_opts.enable_tooltip"))
        lay.addWidget(self._video_auto_check)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep1)

        # ── 识别模式 / Recognition mode ───────────────────────────────────────
        mode_row = QHBoxLayout()
        mode_label = QLabel(self.i18n.t("video_opts.mode_label"))
        mode_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        mode_label.setFixedWidth(160)

        self._video_mode_combo = QComboBox()
        self._video_mode_combo.addItem(self.i18n.t("video_opts.mode_fast"), "instant")
        self._video_mode_combo.addItem(self.i18n.t("video_opts.mode_standard"), "fast")
        self._video_mode_combo.addItem(self.i18n.t("video_opts.mode_full"), "full")
        self._video_mode_combo.setToolTip(self.i18n.t("video_opts.mode_tooltip"))
        # 恢复已保存值 / Restore saved value
        vm_idx = self._video_mode_combo.findData(
            raw.get("video_species_mode", "instant")
        )
        self._video_mode_combo.setCurrentIndex(vm_idx if vm_idx >= 0 else 0)

        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._video_mode_combo)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep2)

        # ── 抽帧上限 / Max frames ─────────────────────────────────────────────
        # 范围 30-240，与注释中 "范围 30-240，默认 60" 完全对齐
        # Range 30-240, exactly matches the comment "range 30-240, default 60"
        frames_row = QHBoxLayout()
        frames_label = QLabel(self.i18n.t("video_opts.max_frames"))
        frames_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        frames_label.setFixedWidth(160)

        self._video_max_frames = QSpinBox()
        # 30-240 与配置注释范围对齐 / 30-240 matches the config comment range
        self._video_max_frames.setRange(30, 240)
        self._video_max_frames.setSingleStep(10)
        self._video_max_frames.setValue(int(raw.get("video_max_frames", 60)))
        self._video_max_frames.setToolTip(self.i18n.t("video_opts.max_frames_tooltip"))

        frames_row.addWidget(frames_label)
        frames_row.addWidget(self._video_max_frames)
        frames_row.addStretch(1)
        lay.addLayout(frames_row)

        frames_hint = QLabel(self.i18n.t("video_opts.max_frames_tooltip"))
        frames_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:160px;"
        )
        frames_hint.setWordWrap(True)
        lay.addWidget(frames_hint)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep3 = QFrame()
        sep3.setFixedHeight(1)
        sep3.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep3)

        # ── YOLO 置信度阈值 / YOLO confidence threshold ───────────────────────
        # 存储浮点 0.30-0.90，滑块以整数 30-90 表示（×100 转换）
        # Stored as float 0.30-0.90; slider uses integer 30-90 (×100 conversion)
        yolo_row = QHBoxLayout()
        yolo_label = QLabel(self.i18n.t("video_opts.yolo_conf"))
        yolo_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        yolo_label.setFixedWidth(160)

        self._yolo_threshold_spin = QSpinBox()
        # 30-90 对应浮点 0.30-0.90，与 old dialog "min_val=30, max_val=90" 对齐
        # 30-90 maps to float 0.30-0.90, matching old dialog "min_val=30, max_val=90"
        self._yolo_threshold_spin.setRange(30, 90)
        self._yolo_threshold_spin.setSingleStep(5)
        self._yolo_threshold_spin.setSpecialValueText("")
        # 存储值是 float 0.30-0.90，转为整数 30-90 / Convert stored float to int 30-90
        stored_yolo = float(raw.get("video_yolo_threshold", 0.5))
        self._yolo_threshold_spin.setValue(int(round(stored_yolo * 100)))
        self._yolo_threshold_spin.setToolTip(self.i18n.t("video_opts.yolo_conf_tooltip"))

        yolo_row.addWidget(yolo_label)
        yolo_row.addWidget(self._yolo_threshold_spin)
        yolo_row.addStretch(1)
        lay.addLayout(yolo_row)

        yolo_hint = QLabel(self.i18n.t("video_opts.yolo_conf_tooltip"))
        yolo_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:160px;"
        )
        yolo_hint.setWordWrap(True)
        lay.addWidget(yolo_hint)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep4 = QFrame()
        sep4.setFixedHeight(1)
        sep4.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep4)

        # ── 识别开关区 / Detection toggles section ────────────────────────────
        toggles_title = QLabel(self.i18n.t("settings.culling_detect_section"))
        toggles_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(toggles_title)

        # 鸟种识别开关 / Species ID toggle
        self._video_species_id = QCheckBox(self.i18n.t("video_opts.birdid_checkbox"))
        self._video_species_id.setChecked(bool(raw.get("video_enable_species_id", True)))
        self._video_species_id.setStyleSheet(self._checkbox_qss())
        self._video_species_id.setToolTip(self.i18n.t("video_opts.birdid_tooltip"))
        lay.addWidget(self._video_species_id)

        # 飞行检测开关 / Flight detection toggle
        self._video_flight = QCheckBox(self.i18n.t("video_opts.flight_checkbox"))
        self._video_flight.setChecked(bool(raw.get("video_enable_flight", True)))
        self._video_flight.setStyleSheet(self._checkbox_qss())
        self._video_flight.setToolTip(self.i18n.t("video_opts.flight_tooltip"))
        lay.addWidget(self._video_flight)

        lay.addStretch(1)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    def _save_video(self) -> None:
        """
        将视频页当前值写回 advanced_config 并保存。

        直接写入 config dict（这些键无对应 property/setter），与 old dialog 保持一致。

        Write current video-page values back to advanced_config and save.

        Values are written directly to the config dict (no property/setter exists
        for these keys), consistent with the old dialog implementation.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        raw = cfg.config

        raw["video_auto_process_in_main"] = self._video_auto_check.isChecked()
        raw["video_species_mode"] = self._video_mode_combo.currentData()
        raw["video_max_frames"] = int(self._video_max_frames.value())
        # SpinBox 存的是 30-90 整数，转回浮点 0.30-0.90 再存储
        # SpinBox stores int 30-90; convert back to float 0.30-0.90 before storing
        raw["video_yolo_threshold"] = self._yolo_threshold_spin.value() / 100.0
        raw["video_enable_species_id"] = self._video_species_id.isChecked()
        raw["video_enable_flight"] = self._video_flight.isChecked()

        cfg.save()

    # ── 外部应用页 / External apps page ──────────────────────────────────────

    def _build_apps_page(self) -> QWidget:
        """
        构建外部应用设置页，允许用户添加/删除右键菜单中的外部编辑器。

        应用列表通过 get_external_apps() 读取，通过 set_external_apps() 写回。
        添加应用时：macOS 优先使用 osascript 原生选择器，失败则 fallback 到
        Qt 文件对话框；Windows 使用 Qt 文件对话框；其他平台同 Qt 对话框。

        Build the External Apps settings page.

        Allows users to add/remove external editors from the right-click menu.
        The app list is read via get_external_apps() and written back via
        set_external_apps(). On macOS, osascript native picker is tried first,
        with Qt file dialog as fallback. On Windows, Qt dialog is used directly.

        返回 / Returns:
            QWidget: 外部应用设置页 / External apps settings page widget.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        self._apps_data: list[dict] = list(cfg.get_external_apps())

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        # 说明文字 / Description text
        hint = QLabel(self.i18n.t("advanced_settings.apps_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        lay.addWidget(hint)

        # 应用列表 / App list
        self._apps_list = QListWidget()
        self._apps_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {COLORS['bg_card']};
                border: 1px solid {COLORS['border_subtle']};
                border-radius: 6px;
                color: {COLORS['text_primary']};
                font-size: 13px;
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-bottom: 1px solid {COLORS['border_subtle']};
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['accent_dim']};
                color: {COLORS['accent']};
            }}
            """
        )
        self._apps_list.setMinimumHeight(180)
        self._refresh_apps_list()
        lay.addWidget(self._apps_list, 1)

        # 按钮行 / Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton(self.i18n.t("advanced_settings.add_app"))
        add_btn.setObjectName("secondary")
        add_btn.setFixedHeight(34)
        add_btn.clicked.connect(self._on_add_app)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton(self.i18n.t("advanced_settings.remove_app"))
        remove_btn.setObjectName("secondary")
        remove_btn.setFixedHeight(34)
        remove_btn.clicked.connect(self._on_remove_app)
        btn_row.addWidget(remove_btn)

        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        return page

    def _refresh_apps_list(self) -> None:
        """
        用 _apps_data 重建 QListWidget 内容。

        Rebuild the QListWidget contents from _apps_data.
        """
        self._apps_list.clear()
        for app in self._apps_data:
            name = app.get("name", "")
            path = app.get("path", "")
            item = QListWidgetItem(f"  {name}   —   {path}")
            item.setToolTip(path)
            self._apps_list.addItem(item)

    def _on_add_app(self) -> None:
        """
        添加外部应用：macOS 优先 osascript，失败则 Qt 对话框；Windows 用 Qt 对话框。

        Add an external app: macOS prefers osascript, falls back to Qt dialog;
        Windows uses Qt file dialog.
        """
        path = ""

        if sys.platform == "darwin":
            # 尝试 macOS 原生应用选择器 / Try macOS native app picker
            try:
                result = subprocess.run(
                    ["osascript", "-e", "POSIX path of (choose application)"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    path = result.stdout.strip().rstrip("/")
            except Exception:
                pass

            # Fallback: osascript 不可用时用 Qt 对话框 / Fallback to Qt dialog
            if not path:
                path = QFileDialog.getExistingDirectory(
                    self,
                    self.i18n.t("advanced_settings.pick_app_title"),
                    "/Applications",
                    QFileDialog.Option.DontUseNativeDialog,
                )
                if path:
                    path = path.rstrip("/")

        elif sys.platform == "win32":
            path, _ = QFileDialog.getOpenFileName(
                self,
                self.i18n.t("advanced_settings.pick_app_title"),
                "C:\\Program Files",
                "Executables (*.exe)",
            )

        else:
            # 其他平台：Qt 文件对话框 / Other platforms: Qt file dialog
            path, _ = QFileDialog.getOpenFileName(
                self,
                self.i18n.t("advanced_settings.pick_app_title"),
            )

        if not path:
            return

        # 从路径提取显示名（去掉 .app / .exe 后缀）/ Extract display name
        basename = os.path.basename(path)
        name = basename.replace(".app", "").replace(".exe", "")

        # 去重（规范化路径比较）/ Deduplicate (normalize path for comparison)
        norm = path.rstrip("/")
        if any(a.get("path", "").rstrip("/") == norm for a in self._apps_data):
            return

        self._apps_data.append({"name": name, "path": norm})
        self._refresh_apps_list()
        self._save_apps()

    def _on_remove_app(self) -> None:
        """
        删除列表中选中的应用条目，并立即持久化。

        Remove the currently selected app entry from the list and persist
        immediately.
        """
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data.pop(row)
            self._refresh_apps_list()
            self._save_apps()

    def _save_apps(self) -> None:
        """
        将外部应用列表写回 advanced_config 并保存(幂等)。

        由 _on_add_app/_on_remove_app 在列表变化时立即调用；同时保留作为
        测试与偶发兜底调用的辅助方法。

        Bulk-flush the external apps list to advanced_config (idempotent).
        Called immediately by _on_add_app/_on_remove_app on list changes;
        also kept as a helper for tests and occasional defensive calls.
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_external_apps(self._apps_data)
        cfg.save()

    # ── 关于页 / About page ───────────────────────────────────────────────────

    def _build_about_page(self) -> QWidget:
        """
        构建关于页，展示应用名称、版本号、致谢和许可证信息（只读，无保存逻辑）。

        内容从 about_dialog.py 迁移而来，使用现有 i18n 键（about.subtitle、
        about.content、app.brand_name）。不删除原 about_dialog.py（由 Task 9 负责）。

        Build the About page, displaying app name, version, acknowledgements,
        and license info (read-only; no save logic required).

        Content migrated from about_dialog.py, reusing existing i18n keys
        (about.subtitle, about.content, app.brand_name).
        The original about_dialog.py is NOT deleted here (Task 9 will handle that).

        返回 / Returns:
            QWidget: 关于内容页 / About content page widget.
        """
        from constants import APP_VERSION
        from core.build_info import COMMIT_HASH

        # 解析 commit hash：优先用打包时写入的 COMMIT_HASH，
        # 其次尝试 git rev-parse，最终 fallback 到 "dev"。
        # Resolve commit hash: prefer build-time COMMIT_HASH, then git, then "dev".
        _commit = COMMIT_HASH or ""
        if not _commit:
            try:
                _commit = subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                ).strip().decode("utf-8")
            except Exception:
                _commit = "dev"

        # ── 容器 + 滚动区 / Container + scroll area ───────────────────────────
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # macOS 原生 QStyle 下 QScrollArea 的 viewport 不会继承祖先 QDialog 的
        # QSS 背景色，系统外观为浅色时会露出原生浅灰 #ececec（与深色主题不符）。
        # 显式设 transparent 让 QDialog 的深色背景透出来（同 birdid_dock.py 已验证的写法）。
        # On macOS the native QStyle paints a QScrollArea's viewport without
        # inheriting the ancestor QDialog's QSS background, so it falls back to
        # the native light gray #ececec when the system appearance is Light —
        # clashing with the dark theme. Setting transparent lets the QDialog's
        # dark background show through (same fix already proven in birdid_dock.py).
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(0)

        # ── 品牌头部 / Brand header ───────────────────────────────────────────
        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(20)

        # 应用图标 / App icon
        icon_path = os.path.join(os.path.dirname(__file__), "..", "img", "icon.png")
        if os.path.exists(icon_path):
            from PySide6.QtGui import QPixmap

            icon_container = QFrame()
            icon_container.setFixedSize(64, 64)
            icon_container.setStyleSheet(
                f"""QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 {COLORS['accent']}, stop:1 {COLORS['accent_deep']});
                    border-radius: 16px;
                }}"""
            )
            icon_inner = QHBoxLayout(icon_container)
            icon_inner.setContentsMargins(12, 12, 12, 12)

            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(
                40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            icon_label.setPixmap(pixmap)
            icon_inner.addWidget(icon_label)
            header_layout.addWidget(icon_container)

        # 品牌文字列 / Brand text column
        brand_layout = QVBoxLayout()
        brand_layout.setSpacing(4)

        app_name = self.i18n.t("app.brand_name") if self.i18n else "SuperPicky"
        title_label = QLabel(app_name)
        title_label.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:22px;font-weight:600;letter-spacing:-0.5px;"
        )
        brand_layout.addWidget(title_label)

        subtitle_text = (
            self.i18n.t("about.subtitle") if self.i18n else "AI Bird Photo Culling Tool"
        )
        subtitle_label = QLabel(subtitle_text)
        subtitle_label.setStyleSheet(
            f"color:{COLORS['text_tertiary']};font-size:13px;"
        )
        brand_layout.addWidget(subtitle_label)

        # 版本号标签（含 commit hash），测试断言依赖此 QLabel 含 APP_VERSION 文本
        # Version label (with commit hash) — the test assertion depends on this QLabel
        version_label = QLabel(f"v{APP_VERSION} ({_commit})")
        version_label.setStyleSheet(
            f"color:{COLORS['accent']};font-size:12px;"
        )
        brand_layout.addWidget(version_label)

        header_layout.addLayout(brand_layout)
        header_layout.addStretch()
        lay.addWidget(header)

        lay.addSpacing(24)

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(divider)

        lay.addSpacing(20)

        # ── 致谢与许可证内容 / Acknowledgements & license content ────────────
        content_text = (
            self.i18n.t("about.content")
            if self.i18n
            else (
                "James Yu\n"
                'Australian-Chinese Professional Photographer, Author of "James\' Landscape Photography Notes" Trilogy\n\n'
                "Model Training: Jordan Yu\n"
                "Development Team: Xiaoping, Lyapunov, osk.sh, yblpoi, jcchan23\n\n"
                "Open Source Models\n"
                "YOLO11 - Bird Detection by Ultralytics\n"
                "OSEA - Bird Classification by Sun Jiao\n"
                "TOPIQ - Aesthetic Scoring by Chaofeng Chen et al.\n\n"
                "Data Sources\n"
                "Species Beauty Index - iRateBird Citizen Science Dataset (CC-BY 4.0)\n"
                "Santangeli, A. et al. (2023), Scientific Data\n"
                "Note: ratings skew toward Finnish and English-speaking users, "
                "reflecting a Western aesthetic perspective\n\n"
                "License: GPL-3.0\n"
                "© 2024-2025 James Yu"
            )
        )
        content_label = QLabel(content_text)
        content_label.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:13px;line-height:1.6;"
        )
        content_label.setWordWrap(True)
        content_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(content_label, 1)

        lay.addStretch(1)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    def _placeholder(self, title: str) -> QWidget:
        """
        生成占位页(仅显示分页标题标签)。

        Generate a placeholder page (shows only the page title label).

        参数 / Parameters:
            title (str): 分页标题 / Page title.

        返回 / Returns:
            QWidget: 占位内容页 / Placeholder content page.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(title))
        lay.addStretch(1)
        return page

    # ── 外部接口 / Public API ─────────────────────────────────────────────────

    def show_page(self, key: str) -> None:
        """
        切换到指定 key 对应的页。

        供外部(header chip、识鸟面板等)调用以跳转到指定设置分页。

        Switch to the page identified by key.

        Called externally (header chip, bird-ID panel, etc.) to jump to a
        specific settings page.

        参数 / Parameters:
            key (str): 目标页 key,必须是 PAGE_ORDER 中的值 / Target page key from PAGE_ORDER.
        """
        if key in PAGE_ORDER:
            self._nav.setCurrentRow(PAGE_ORDER.index(key))

    def _on_done(self) -> None:
        """
        "完成"按钮点击回调:直接 accept()。

        统一即时保存模型下,每个控件已在其 changed 信号里即时持久化(见各
        _on_*_changed 方法),这里不再需要显式保存——兜底批量 flush 统一在
        done() 里进行,accept()/reject()/ESC/关闭按钮都会等价地经过它。

        "Done" button click callback: just accept().

        Under the unified immediate-save model, every control already
        persists on its own changed signal (see the _on_*_changed methods);
        no explicit save is needed here. The defensive bulk flush lives in
        done(), which accept()/reject()/ESC/the close button all funnel
        through equivalently.
        """
        self.accept()

    def done(self, r: int) -> None:
        """
        QDialog.done() 的统一入口——accept()/reject()/ESC/窗口关闭按钮最终
        都会调用它。在此做一次兜底批量 flush，确保即便某个控件的即时保存
        钩子遗漏，用户以任何方式关闭设置中心都不会丢失改动；这也是"ESC 与
        点完成等价"的实现点：不再有专属于"完成"按钮的保存路径。

        Unified entry point for QDialog.done() — accept()/reject()/ESC/the
        window close button all funnel through it. Performs one defensive
        bulk flush so that even if a control's immediate-save hook were
        missed, closing Settings Center any way never loses a change. This
        is also where "ESC behaves like Done" is implemented: there is no
        save path exclusive to the Done button anymore.

        参数 / Parameters:
            r (int): QDialog 结果码(Accepted/Rejected)/ QDialog result code.
        """
        # 仅在对应页已构建时保存(以关键属性是否存在为判据)
        # Save only if the corresponding page was built (gated on a key attribute)
        if hasattr(self, "_cull_ai"):
            self._save_culling()
        if hasattr(self, "_bid_auto"):
            self._save_birdid()
        if hasattr(self, "_folder_layout_combo"):
            self._save_output()
        if hasattr(self, "_video_auto_check"):
            self._save_video()
        if hasattr(self, "_apps_list"):
            self._save_apps()
        super().done(r)
