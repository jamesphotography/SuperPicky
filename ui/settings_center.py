# -*- coding: utf-8 -*-
"""
统一设置中心:左侧分类导航 + 右侧内容页。

取代旧高级设置/技能等级/关于弹窗入口,后续 Task 3-6 用真实页替换占位页。

Unified Settings Center: left-side category nav + right-side content pages.

Replaces the old advanced settings / skill level / about dialog entry points;
Tasks 3-6 will replace the placeholder pages with real content.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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

from ui.icon_utils import ICON_ACTIVE, ICON_IDLE, load_tinted_icon  # noqa: F401
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
        self._cull_sharp.setRange(200, 600)
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
        self._cull_nima.setRange(40, 70)
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
        self._cull_flight.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
        lay.addWidget(self._cull_flight)

        # 连拍检测 / Burst detection
        self._cull_burst = QCheckBox(self.i18n.t("settings.culling_burst_label"))
        self._cull_burst.setChecked(cfg.burst_check)
        self._cull_burst.setStyleSheet(f"color:{COLORS['text_secondary']};font-size:12px;")
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
        self._bid_auto.setStyleSheet(
            f"color:{COLORS['text_secondary']};font-size:12px;"
        )
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
        from PySide6.QtGui import QStandardItem

        for display_name, code in country_list.items():
            self._bid_country.addItem(display_name)
            if code in ("SEP1",):
                # 禁用分隔符项 / Disable separator item
                idx = self._bid_country.count() - 1
                from typing import Any, cast
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
        self._bid_region.addItem(self.i18n.t("birdid.region_entire_country"))

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

    def _restore_birdid_country(self, cfg) -> None:
        """
        从配置恢复国家/地区下拉菜单的初始选中项。

        先尝试按保存的 country_code 匹配；若未匹配则按 selected_country 显示名匹配。
        恢复国家后再调用 _on_bid_country_changed 填充地区列表，并恢复已保存的地区。

        Restore the initial selection of the country/region dropdowns from config.

        Tries to match by saved country_code first; falls back to selected_country
        display name. After restoring the country, calls _on_bid_country_changed to
        populate the region list, then restores the saved region.

        参数 / Parameters:
            cfg: advanced_config 实例 / AdvancedConfig instance.
        """
        saved_code = cfg.birdid_country_code
        saved_display = cfg.birdid_selected_country

        matched = False
        if saved_code is not None:
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

        # 填充地区并恢复 / Populate regions, then restore saved region
        self._on_bid_country_changed(self._bid_country.currentText())
        saved_region = cfg.birdid_selected_region
        if saved_region:
            idx = self._bid_region.findText(saved_region)
            if idx >= 0:
                self._bid_region.setCurrentIndex(idx)

    def _on_bid_country_changed(self, country_display: str) -> None:
        """
        国家下拉切换时动态填充地区下拉菜单。

        Dynamically populate the region dropdown when the country selection changes.

        参数 / Parameters:
            country_display (str): 当前选中的国家显示名 / Currently selected country display name.
        """
        if getattr(self, "_bid_applying", False):
            return

        country_code = self._bid_country_list.get(country_display)
        if country_code in ("SEP1",):
            return

        self._bid_region.clear()
        self._bid_region.addItem(self.i18n.t("birdid.region_entire_country"))

        is_english = self.i18n.current_lang.startswith("en")

        if country_code and country_code not in (None, "GLOBAL"):
            for country_entry in self._bid_regions_data.get("countries", []):
                if country_entry.get("code") == country_code:
                    if country_entry.get("has_regions") and country_entry.get("regions"):
                        for region_entry in country_entry["regions"]:
                            region_code = region_entry.get("code", "")
                            if is_english:
                                region_name = region_entry.get("name", region_code)
                            else:
                                region_name = (
                                    region_entry.get("name_cn")
                                    or region_entry.get("name", region_code)
                                )
                            self._bid_region.addItem(f"{region_name} ({region_code})")
                    break

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
        import re

        from advanced_config import get_advanced_config

        cfg = get_advanced_config()

        # 自动识鸟开关 / Auto-identify toggle
        cfg.set_birdid_auto_identify(self._bid_auto.isChecked())

        # 置信度 / Confidence
        cfg.set_birdid_confidence(self._bid_conf.value())

        # 数据源 / Data source
        use_ebird: bool = self._bid_ebird.isChecked()

        # 国家 / Country
        country_display = self._bid_country.currentText()
        country_code = self._bid_country_list.get(country_display)
        # 把特殊伪代码归一化为 None / Normalize special pseudo-codes to None
        if country_code in ("SEP1", "SEP2", "MORE", "GLOBAL"):
            country_code = None

        # 地区 / Region
        region_display = self._bid_region.currentText()
        region_code: str | None = None
        entire_country_text = self.i18n.t("birdid.region_entire_country")
        if region_display and region_display != entire_country_text:
            match = re.search(r"\(([A-Z]{2}-[A-Z0-9]+)\)", region_display)
            if match:
                region_code = match.group(1)

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
        cfg.save()

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
        self.accept()
