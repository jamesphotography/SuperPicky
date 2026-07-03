# -*- coding: utf-8 -*-
"""
统一设置中心:左侧分类导航 + 右侧内容页。

取代旧高级设置/技能等级/关于弹窗入口,后续 Task 3-6 用真实页替换占位页。

Unified Settings Center: left-side category nav + right-side content pages.

Replaces the old advanced settings / skill level / about dialog entry points;
Tasks 3-6 will replace the placeholder pages with real content.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, cast

from PySide6.QtCore import Qt
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

from ui.icon_utils import ICON_ACTIVE, ICON_IDLE, load_tinted_icon, checkbox_indicator_qss  # noqa: F401
from ui.styles import COLORS  # noqa: F401

# ── 常量 / Constants ──────────────────────────────────────────────────────────

PAGE_ORDER: list[str] = ["culling", "birdid", "output", "video", "apps", "about"]

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
    各分页由 _build_page(key) 分发构建; Task 3-6 仅需替换对应分支。

    Unified settings center dialog.

    Left: QListWidget navigation. Right: QStackedWidget content area.
    Each page is dispatched via _build_page(key); Tasks 3-6 only need
    to replace the relevant branch.

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

    def _build_page(self, key: str) -> QWidget:
        """
        根据 key 构建对应内容页。

        Task 3-6 在此添加各自真实页的分支;其余保持占位。

        Build the content page for the given key.

        Tasks 3-6 add their real page branches here; others remain as placeholders.

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

        包含:技能等级单选卡片行(含"自定义")、AI 置信度/锐度/美学三个阈值滑块、
        飞鸟检测/连拍检测开关、连拍速度 QSpinBox。

        技能等级 ↔ 阈值协同逻辑:
          - 选技能等级预设 → `_on_skill_preset_selected` 置 `_suppress=True`、
            填充对应阈值滑块、再置 False，避免回调回环。
          - 手动拖动任一阈值 → `_on_cull_threshold_changed` 检查 `_suppress`，
            若未抑制则将 `_current_skill_key` 切为 "custom" 并刷新卡片选中态。

        Build the Culling settings page.

        Contains: a row of skill-level cards (incl. "custom"), three threshold sliders
        for AI confidence / sharpness / aesthetics, flight-detection and burst-detection
        check-boxes, and a burst-fps QSpinBox.

        Coordination logic:
          - Selecting a skill preset → _on_skill_preset_selected sets _suppress=True,
            fills sliders, then False — prevents re-entrant loop.
          - Manually adjusting any threshold → _on_cull_threshold_changed checks _suppress;
            if not suppressed, sets _current_skill_key="custom" and refreshes card state.

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

        inner = QWidget()
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

        # ── 阈值区 / Threshold section ────────────────────────────────────────
        thresh_title = QLabel(self.i18n.t("settings.culling_threshold_section"))
        thresh_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(thresh_title)

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
        self._cull_ai.valueChanged.connect(self._on_cull_threshold_changed)
        ai_row.addWidget(ai_label)
        ai_row.addWidget(self._cull_ai, 1)
        ai_row.addWidget(self._cull_ai_value_label)
        lay.addLayout(ai_row)

        # 锐度滑块 (200-600, int; 对应 min_sharpness)
        # Sharpness slider (200-600 integer; maps to min_sharpness)
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

        # 美学(NIMA)滑块 (40-70, 值/10 = NIMA; 对应 min_nima 4.0..7.0)
        # Aesthetics (NIMA) slider (40-70; value/10 = NIMA float; maps to min_nima 4.0..7.0)
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
        lay.addWidget(self._cull_flight)

        # 连拍检测 / Burst detection
        self._cull_burst = QCheckBox(self.i18n.t("settings.culling_burst_label"))
        self._cull_burst.setChecked(cfg.burst_check)
        self._cull_burst.setStyleSheet(self._checkbox_qss())
        lay.addWidget(self._cull_burst)

        # 连拍速度 / Burst FPS
        fps_row = QHBoxLayout()
        fps_label = QLabel(self.i18n.t("settings.culling_burst_fps_label"))
        fps_label.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        fps_label.setFixedWidth(160)
        self._cull_burst_fps = QSpinBox()
        self._cull_burst_fps.setRange(4, 20)
        self._cull_burst_fps.setValue(cfg.burst_fps)
        fps_row.addWidget(fps_label)
        fps_row.addWidget(self._cull_burst_fps)
        fps_row.addStretch(1)
        lay.addLayout(fps_row)

        lay.addStretch(1)

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
        国家下拉菜单、地区下拉菜单(随国家变化动态填充)。

        初值来自 advanced_config 的 birdid_* 字段；
        通过 _save_birdid() 写回。

        Build the Bird-ID settings page.

        Contains: auto-identify toggle, confidence slider (30-95), data-source
        radio buttons (eBird / GBIF), country dropdown, and region dropdown
        (dynamically populated on country change).

        Initial values come from advanced_config's birdid_* fields;
        written back via _save_birdid().

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

        inner = QWidget()
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
        lay.addWidget(self._bid_auto)

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
        self._bid_ebird.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
        self._bid_gbif.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )

        # QButtonGroup 确保两者互斥 / QButtonGroup ensures mutual exclusivity
        self._bid_source_group = QButtonGroup(self)
        self._bid_source_group.addButton(self._bid_ebird)
        self._bid_source_group.addButton(self._bid_gbif)

        if cfg.birdid_use_ebird:
            self._bid_ebird.setChecked(True)
        else:
            self._bid_gbif.setChecked(True)

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
            if code in ("SEP1",):
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

        region_row.addWidget(region_label)
        region_row.addWidget(self._bid_region, 1)
        lay.addLayout(region_row)

        # 国家切换时动态填充地区 / Dynamically populate regions on country change
        self._bid_country.currentTextChanged.connect(self._on_bid_country_changed)

        # ── 恢复已保存的国家/地区初值 / Restore saved country/region ──────────
        self._bid_applying: bool = True
        self._restore_birdid_country(cfg)
        self._bid_applying = False

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
        国家下拉切换时动态填充地区下拉菜单。

        仅在用户主动切换国家时触发（由 currentTextChanged 信号连接）；
        初始化/恢复路径请使用 _populate_bid_regions，以绕开 _bid_applying 守卫。

        Dynamically populate the region dropdown when the country selection changes.

        Triggered only by user interaction (connected to currentTextChanged signal).
        For the init/restore path use _populate_bid_regions directly to bypass the
        _bid_applying guard.

        参数 / Parameters:
            country_display (str): 当前选中的国家显示名 / Currently selected country display name.
        """
        if getattr(self, "_bid_applying", False):
            return

        country_code = self._bid_country_list.get(country_display)
        if country_code in ("SEP1",):
            return

        self._populate_bid_regions(country_code)

    def _save_birdid(self) -> None:
        """
        将识鸟页当前值写回 advanced_config 并保存。

        依次调用:
          set_birdid_auto_identify — 自动识鸟开关
          set_birdid_confidence   — 置信度 (30-95)
          set_birdid_region       — 数据源/国家代码/地区代码及其显示名称

        Write the current Bird-ID page values back to advanced_config and save.

        Calls in order:
          set_birdid_auto_identify — auto-identify toggle
          set_birdid_confidence   — confidence (30-95)
          set_birdid_region       — source / country code / region code and display names
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # 自动识鸟开关 / Auto-identify toggle
        cfg.set_birdid_auto_identify(self._bid_auto.isChecked())

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

    # ── 精选页协同逻辑 / Culling page coordination logic ──────────────────────

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
        技能等级预设被选中时的回调。

        将 _suppress 置为 True,填充阈值滑块,再置 False,防止回环触发 _on_cull_threshold_changed。
        "custom" 档只刷新卡片,不覆写滑块。

        Callback when a skill-level preset card is clicked.

        Sets _suppress=True, fills threshold sliders from the preset, then False,
        preventing re-entrant calls to _on_cull_threshold_changed.
        "custom" level only refreshes the card state without overwriting sliders.

        参数 / Parameters:
            level_key (str): 被选中的档位 key / Selected skill level key.
        """
        from core.skill_presets import get_skill_level_thresholds

        self._current_skill_key = level_key
        self._select_skill_radio(level_key)
        if level_key == "custom":
            return

        # get_skill_level_thresholds 返回 Tuple[int, float]: (sharpness, aesthetics)
        # get_skill_level_thresholds returns Tuple[int, float]: (sharpness, aesthetics)
        th = get_skill_level_thresholds(level_key)
        self._suppress = True
        try:
            self._cull_sharp.setValue(int(th[0]))
            self._cull_nima.setValue(int(round(th[1] * 10)))
        finally:
            self._suppress = False

    def _on_cull_threshold_changed(self, *_) -> None:
        """
        任一阈值滑块被用户拖动时的回调。

        若 _suppress 为 True(由技能等级预设填充触发)则直接返回,避免回环。
        否则将当前技能等级切换为"自定义",并刷新卡片选中态。

        Callback fired when any threshold slider value changes.

        If _suppress is True (triggered by preset fill), returns immediately to avoid
        re-entry. Otherwise sets _current_skill_key="custom" and refreshes card state.
        """
        if getattr(self, "_suppress", False):
            return
        self._current_skill_key = "custom"
        self._select_skill_radio("custom")

    def _save_culling(self) -> None:
        """
        将精选页当前值写回 advanced_config 并保存。

        此方法由 accept() 触发(连接到 done_btn.clicked)。

        Write the current culling-page values back to advanced_config and save.

        Called on accept (connected to the Done button click).
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()
        cfg.set_min_confidence(self._cull_ai.value() / 100.0)
        cfg.set_min_sharpness(self._cull_sharp.value())
        cfg.set_min_nima(self._cull_nima.value() / 10.0)
        cfg.set_burst_fps(self._cull_burst_fps.value())
        cfg.set_flight_check(self._cull_flight.isChecked())
        cfg.set_burst_check(self._cull_burst.isChecked())
        cfg.set_skill_level(self._current_skill_key)
        # 当处于自定义档时同步写回 custom_* 字段，避免 CLI 路径读到陈旧值。
        # When in custom mode, also persist custom_* fields so CLI path reads fresh values.
        if self._current_skill_key == "custom":
            cfg.set_custom_sharpness(self._cull_sharp.value())
            cfg.set_custom_aesthetics(self._cull_nima.value() / 10.0)
        cfg.save()

    # ── 输出页 / Output page ──────────────────────────────────────────────────

    def _build_output_page(self) -> QWidget:
        """
        构建输出设置页，包含：分目录布局、元数据写入方式（XMP）、预览管理开关。

        初值来自 advanced_config 的 folder_layout / arw_write_mode /
        metadata_write_mode / keep_temp_files / completion_sound_enabled /
        detail_metadata_for_rejected 字段；通过 _save_output() 写回。

        Build the Output settings page.

        Contains: folder layout, XMP metadata write mode, and preview management
        toggles. Initial values come from advanced_config; written back via
        _save_output().

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

        inner = QWidget()
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
        # 恢复已保存的布局选项 / Restore saved folder layout
        fl_idx = self._folder_layout_combo.findData(cfg.folder_layout)
        self._folder_layout_combo.setCurrentIndex(fl_idx if fl_idx >= 0 else 0)

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

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep1)

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
        self._xmp_embedded.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
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
        self._xmp_sidecar.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
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
        self._xmp_none.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
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

        # ── 分隔线 / Divider ──────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color:{COLORS['border_subtle']};")
        lay.addWidget(sep2)

        # ── 预览管理区 / Preview management section ───────────────────────────
        prev_title = QLabel(self.i18n.t("advanced_settings.preview_management"))
        prev_title.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:13px;font-weight:600;"
        )
        lay.addWidget(prev_title)

        # 保留预览图 / Keep preview files
        self._keep_temp_files = QCheckBox(self.i18n.t("advanced_settings.keep_preview"))
        self._keep_temp_files.setChecked(cfg.keep_temp_files)
        self._keep_temp_files.setStyleSheet(self._checkbox_qss())
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
        lay.addWidget(self._detail_meta_for_rejected)

        detail_hint = QLabel(self.i18n.t("advanced_settings.detail_metadata_hint"))
        detail_hint.setWordWrap(True)
        detail_hint.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;margin-left:24px;"
        )
        lay.addWidget(detail_hint)

        lay.addStretch(1)

        scroll.setWidget(inner)
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.addWidget(scroll)
        return page

    def _save_output(self) -> None:
        """
        将输出页当前值写回 advanced_config 并保存。

        依次调用:
          set_folder_layout          — 分目录布局
          set_metadata_write_mode    — XMP 元数据写入方式
          set_keep_temp_files        — 保留预览图
          set_completion_sound_enabled — 完成提示音
          set_detail_metadata_for_rejected — 拒绝照片详情元数据

        Write current output-page values back to advanced_config and save.

        Calls in order:
          set_folder_layout          — folder layout
          set_metadata_write_mode    — XMP metadata write mode
          set_keep_temp_files        — keep preview files
          set_completion_sound_enabled — completion sound
          set_detail_metadata_for_rejected — detail metadata for rejected
        """
        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # 分目录布局 / Folder layout
        cfg.set_folder_layout(self._folder_layout_combo.currentData())

        # XMP 写入方式 / XMP metadata write mode
        btn_id = self._xmp_button_group.checkedId()
        mode_map = {0: "embedded", 1: "sidecar", 2: "none"}
        cfg.set_metadata_write_mode(mode_map.get(btn_id, "embedded"))

        # 预览管理 / Preview management
        cfg.set_keep_temp_files(self._keep_temp_files.isChecked())
        cfg.set_completion_sound_enabled(self._completion_sound.isChecked())
        cfg.set_detail_metadata_for_rejected(self._detail_meta_for_rejected.isChecked())

        cfg.save()

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

        inner = QWidget()
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

    def _on_remove_app(self) -> None:
        """
        删除列表中选中的应用条目。

        Remove the currently selected app entry from the list.
        """
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data.pop(row)
            self._refresh_apps_list()

    def _save_apps(self) -> None:
        """
        将外部应用列表写回 advanced_config 并保存。

        Write the external apps list back to advanced_config and save.
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

        inner = QWidget()
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
        "完成"按钮点击回调:先保存各页面数据,再关闭对话框。

        当前 Task 3 只保存精选页数据;后续 Task 4-6 在此追加各自的 _save_xxx 调用。

        "Done" button click callback: save page data then close the dialog.

        Task 3 only saves culling-page data; Tasks 4-6 will append their own
        _save_xxx calls here.
        """
        # 仅在精选页已构建时保存(即 _cull_ai 属性存在) / Save only if culling page was built
        if hasattr(self, "_cull_ai"):
            self._save_culling()
        # 仅在识鸟页已构建时保存(即 _bid_auto 属性存在) / Save only if bird-ID page was built
        if hasattr(self, "_bid_auto"):
            self._save_birdid()
        # 仅在输出页已构建时保存(即 _folder_layout_combo 属性存在) / Save only if output page was built
        if hasattr(self, "_folder_layout_combo"):
            self._save_output()
        # 仅在视频页已构建时保存(即 _video_auto_check 属性存在) / Save only if video page was built
        if hasattr(self, "_video_auto_check"):
            self._save_video()
        # 仅在外部应用页已构建时保存(即 _apps_list 属性存在) / Save only if apps page was built
        if hasattr(self, "_apps_list"):
            self._save_apps()
        self.accept()
