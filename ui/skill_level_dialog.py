# -*- coding: utf-8 -*-
"""
SuperPicky V4.3 - 摄影水平选择组件
提供首次使用弹窗和设置页面的水平选择器
"""

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QFrame, QButtonGroup
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ui.styles import COLORS

# SKILL_PRESETS / get_skill_level_thresholds 已抽到 core.skill_presets（无 Qt 依赖，
# GUI 与 CLI 共用）。此处 re-export，保持现有 `from ui.skill_level_dialog import ...` 不变。
# Re-exported from core.skill_presets so existing GUI imports keep working unchanged.
from core.skill_presets import SKILL_PRESETS, get_skill_level_thresholds


class SkillLevelCard(QFrame):
    """单个水平选择卡片"""
    
    clicked = Signal(str)  # 发射卡片对应的 skill_level key
    
    def __init__(self, level_key: str, i18n, is_custom: bool = False, parent=None):
        super().__init__(parent)
        self.level_key = level_key
        self.i18n = i18n
        self.is_custom = is_custom
        self._selected = False
        
        self.setFixedHeight(100)
        self.setMinimumWidth(120)
        self.setCursor(Qt.PointingHandCursor if not is_custom else Qt.ArrowCursor)
        
        self._setup_ui()
        self._update_style()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)
        
        # 名称标签
        if self.is_custom:
            name = self.i18n.t("skill_level.custom")
        else:
            preset = SKILL_PRESETS.get(self.level_key, {})
            name = self.i18n.t(preset.get("name_key", "skill_level.intermediate"))
        
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_primary']};
                font-size: 14px;
                font-weight: 600;
                background: transparent;
                border: none;
            }}
        """)
        layout.addWidget(self.name_label)
        
        # 阈值信息（自选模式不显示固定阈值）
        if not self.is_custom:
            preset = SKILL_PRESETS.get(self.level_key, {})
            sharpness = preset.get("sharpness", 380)
            aesthetics = preset.get("aesthetics", 4.8)
            
            threshold_text = f"{self.i18n.t('labels.sharpness_short')} {sharpness}\n{self.i18n.t('labels.aesthetics')} {aesthetics}"
            self.threshold_label = QLabel(threshold_text)
            self.threshold_label.setAlignment(Qt.AlignCenter)
            self.threshold_label.setStyleSheet(f"""
                QLabel {{
                    color: {COLORS['text_tertiary']};
                    font-size: 11px;
                    background: transparent;
                    border: none;
                }}
            """)
            layout.addWidget(self.threshold_label)
        else:
            # 自选模式显示状态指示器
            self.status_label = QLabel("●")
            self.status_label.setAlignment(Qt.AlignCenter)
            self.status_label.setStyleSheet(f"""
                color: {COLORS['text_tertiary']};
                font-size: 16px;
            """)
            layout.addWidget(self.status_label)
    
    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()
    
    def _update_style(self):
        if self.is_custom:
            # 自选模式：灰色禁用样式
            bg_color = COLORS['bg_elevated'] if self._selected else COLORS['bg_secondary']
            border_color = COLORS['accent'] if self._selected else COLORS['border']
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {bg_color};
                    border: 2px solid {border_color};
                    border-radius: 8px;
                    opacity: 0.7;
                }}
            """)
        else:
            # 普通预设卡片
            if self._selected:
                self.setStyleSheet(f"""
                    QFrame {{
                        background-color: {COLORS['accent']}20;
                        border: 2px solid {COLORS['accent']};
                        border-radius: 8px;
                    }}
                """)
            else:
                self.setStyleSheet(f"""
                    QFrame {{
                        background-color: {COLORS['bg_elevated']};
                        border: 2px solid {COLORS['border']};
                        border-radius: 8px;
                    }}
                    QFrame:hover {{
                        border-color: {COLORS['accent']};
                    }}
                """)
    
    def mousePressEvent(self, event):
        if not self.is_custom:
            self.clicked.emit(self.level_key)
        super().mousePressEvent(event)


class SkillLevelDialog(QDialog):
    """首次使用水平选择弹窗"""
    
    level_selected = Signal(str)  # 发射选中的 skill_level key
    
    def __init__(self, i18n, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self.selected_level = "intermediate"  # 默认选中中级
        
        self.setWindowTitle(self.i18n.t("skill_level.dialog_title"))
        self.setModal(True)
        self.setFixedSize(480, 340)
        self.setStyleSheet(f"background-color: {COLORS['bg_primary']};")
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        
        # 标题
        title_label = QLabel(self.i18n.t("skill_level.dialog_title"))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 18px;
            font-weight: 600;
        """)
        layout.addWidget(title_label)
        
        # 副标题
        subtitle_label = QLabel(self.i18n.t("skill_level.dialog_subtitle"))
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet(f"""
            color: {COLORS['text_secondary']};
            font-size: 12px;
        """)
        layout.addWidget(subtitle_label)
        
        layout.addSpacing(8)
        
        # 卡片容器
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)
        
        self.cards = {}
        for level_key in ["beginner", "intermediate", "master"]:
            card = SkillLevelCard(level_key, self.i18n)
            card.clicked.connect(self._on_card_clicked)
            self.cards[level_key] = card
            cards_layout.addWidget(card)
        
        # 默认选中 intermediate
        self.cards["intermediate"].set_selected(True)
        
        layout.addLayout(cards_layout)
        
        layout.addSpacing(8)
        
        # 提示文字
        hint_label = QLabel(self.i18n.t("skill_level.dialog_hint"))
        hint_label.setAlignment(Qt.AlignCenter)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"""
            color: {COLORS['text_tertiary']};
            font-size: 11px;
        """)
        layout.addWidget(hint_label)
        
        layout.addStretch()
        
        # 确定按钮
        confirm_btn = QPushButton(self.i18n.t("buttons.confirm"))
        confirm_btn.setMinimumHeight(36)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent']};
                color: {COLORS['bg_void']};
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: 500;
                padding: 8px 24px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_hover']};
            }}
        """)
        confirm_btn.clicked.connect(self._on_confirm)
        layout.addWidget(confirm_btn)
    
    def _on_card_clicked(self, level_key: str):
        self.selected_level = level_key
        for key, card in self.cards.items():
            card.set_selected(key == level_key)
    
    def _on_confirm(self):
        self.level_selected.emit(self.selected_level)
        self.accept()


class SkillLevelSelector(QWidget):
    """设置页面的水平选择器组件"""
    
    level_changed = Signal(str, int, float)  # level_key, sharpness, aesthetics
    
    def __init__(self, i18n, config, parent=None):
        super().__init__(parent)
        self.i18n = i18n
        self.config = config
        self._current_level = config.skill_level
        
        self._setup_ui()
        self._update_selection(self._current_level)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # 标题
        title_label = QLabel(self.i18n.t("skill_level.section_title"))
        title_label.setStyleSheet(f"""
            color: {COLORS['text_primary']};
            font-size: 13px;
            font-weight: 500;
        """)
        layout.addWidget(title_label)
        
        # 卡片容器
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)
        
        self.cards = {}
        for level_key in ["beginner", "intermediate", "master"]:
            card = SkillLevelCard(level_key, self.i18n)
            card.clicked.connect(self._on_card_clicked)
            self.cards[level_key] = card
            cards_layout.addWidget(card)
        
        # 自选卡片
        custom_card = SkillLevelCard("custom", self.i18n, is_custom=True)
        self.cards["custom"] = custom_card
        cards_layout.addWidget(custom_card)
        
        layout.addLayout(cards_layout)
    
    def _on_card_clicked(self, level_key: str):
        self._update_selection(level_key)
        
        preset = SKILL_PRESETS.get(level_key, {})
        sharpness = preset.get("sharpness", 380)
        aesthetics = preset.get("aesthetics", 4.8)
        
        self.level_changed.emit(level_key, sharpness, aesthetics)
    
    def _update_selection(self, level_key: str):
        self._current_level = level_key
        for key, card in self.cards.items():
            card.set_selected(key == level_key)
    
    def set_custom_mode(self):
        """当滑块被手动调整时，切换到自选模式"""
        self._update_selection("custom")
    
    def get_current_level(self) -> str:
        return self._current_level
    
    def set_level(self, level_key: str):
        """外部设置当前水平"""
        self._update_selection(level_key)


# get_skill_level_thresholds 已移至 core.skill_presets 并在文件顶部 re-export。
# Moved to core.skill_presets; re-exported at the top of this file.
