# -*- coding: utf-8 -*-
"""
SuperPicky - UI 样式定义
极简艺术风格 (Minimalist Artistic Design)
"""

# ==================== 色彩系统 ====================
# 极简色板 - 黑白为主，单一强调色
COLORS = {
    # 背景层级 (从深到浅)
    'bg_void': '#0a0a0a',           # 最深背景
    'bg_primary': '#111111',         # 主背景
    'bg_elevated': '#1a1a1a',        # 抬升背景
    'bg_card': '#1f1f1f',            # 卡片背景
    'bg_input': '#262626',           # 输入框背景

    # 文字层级
    'text_primary': '#fafafa',       # 主文字
    'text_secondary': '#a1a1a1',     # 次要文字
    'text_tertiary': '#909090',      # 第三级文字
    'text_muted': '#8a8a8a',         # 静默文字（hint / 说明），53% 亮度保证深色背景可读

    # 强调色 - 优雅的青绿色
    'accent': '#00d4aa',
    'accent_hover': '#00e6b8',            # :hover 亮一级
    'accent_pressed': '#00b894',          # :pressed 暗一级
    'accent_light': '#00ffcc',            # 渐变亮端
    'accent_deep': '#00a080',             # 渐变暗端
    'accent_dim': 'rgba(0, 212, 170, 0.15)',
    'accent_glow': 'rgba(0, 212, 170, 0.3)',

    # 焦点状态语义色（全系统统一）
    'focus_best':   '#00cc44',   # 精焦 — 绿（相机 AF 确认色）
    'focus_good':   '#a8e066',   # 合焦 — 黄绿（淡于精焦深绿，区分明显）
    'focus_bad':    '#e0e0e0',   # 失焦 — 近白灰
    'focus_worst':  '#a1a1a1',   # 脱焦 — 灰

    # 状态色 (降低饱和度，更优雅)
    'star_gold': '#d4a800',          # 星级金色
    'success': '#22c55e',            # 成功绿
    'warning': '#eab308',            # 警告黄
    'error': '#ef4444',              # 错误红

    # 边框
    'border': '#2a2a2a',
    'border_subtle': '#1e1e1e',

    # 兼容旧代码
    'bg_secondary': '#1a1a1a',
    'bg_tertiary': '#1f1f1f',
    'separator': '#2a2a2a',
    'fill': '#404040',
}

# ==================== 字体系统 ====================
# 中英文混排优化: Qt 兼容的字体名称
# 注意: Qt 不支持 CSS 的 -apple-system/BlinkMacSystemFont，需要使用实际字体名
FONTS = {
    # 无衬线：苹方优先 (macOS)，雅黑备选 (Windows)，最终回退 sans-serif
    'sans': '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
    # 等宽：Menlo/Monaco (macOS)，Consolas/Courier New (Windows)
    'mono': '"Menlo", "Consolas", "Monaco", "Courier New", "PingFang SC", "Microsoft YaHei", monospace',
}

# ==================== 全局样式表 ====================
GLOBAL_STYLE = f"""
/* ==================== 基础重置 ==================== */
QMainWindow {{
    background-color: {COLORS['bg_primary']};
}}

QWidget {{
    font-family: {FONTS['sans']};
    color: {COLORS['text_primary']};
    font-size: 13px;
}}

QDialog {{
    background-color: {COLORS['bg_primary']};
}}

/* ==================== 分组框 ==================== */
QGroupBox {{
    background-color: {COLORS['bg_elevated']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: 10px;
    margin-top: 14px;
    padding: 16px;
    padding-top: 8px;
    font-size: 13px;
    font-weight: 500;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 6px;
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ==================== 标签 ==================== */
QLabel {{
    color: {COLORS['text_primary']};
    font-size: 13px;
    background: transparent;
}}

QLabel#sectionLabel {{
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QLabel#valueLabel {{
    color: {COLORS['accent']};
    font-size: 14px;
    font-family: {FONTS['mono']};
    font-weight: 500;
}}

QLabel#mutedLabel {{
    color: {COLORS['text_muted']};
    font-size: 11px;
}}

/* ==================== 输入框 ==================== */
QLineEdit {{
    background-color: {COLORS['bg_input']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 12px 16px;
    color: {COLORS['text_primary']};
    font-size: 14px;
    font-family: {FONTS['mono']};
    selection-background-color: {COLORS['accent']};
}}

QLineEdit:focus {{
    border-color: {COLORS['accent']};
}}

QLineEdit::placeholder {{
    color: {COLORS['text_muted']};
}}

/* ==================== 按钮系统 ==================== */
/* 主按钮 - 强调色 */
QPushButton {{
    background-color: {COLORS['accent']};
    border: none;
    border-radius: 6px;
    padding: 12px 24px;
    color: {COLORS['bg_void']};
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {COLORS['accent_hover']};
}}

QPushButton:pressed {{
    background-color: {COLORS['accent_pressed']};
}}

QPushButton:disabled {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_muted']};
}}

/* 次级按钮 - 边框样式 */
QPushButton#secondary {{
    background-color: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text_secondary']};
}}

QPushButton#secondary:hover {{
    border-color: {COLORS['text_muted']};
    color: {COLORS['text_primary']};
}}

QPushButton#secondary:disabled {{
    background-color: {COLORS['bg_card']};
    border-color: {COLORS['border_subtle']};
    color: {COLORS['text_muted']};
}}

/* 第三级按钮 - 幽灵样式 */
QPushButton#tertiary {{
    background-color: transparent;
    border: none;
    color: {COLORS['text_tertiary']};
}}

QPushButton#tertiary:hover {{
    color: {COLORS['text_secondary']};
}}

QPushButton#tertiary:disabled {{
    color: {COLORS['text_muted']};
}}

/* 浏览按钮 */
QPushButton#browse {{
    background-color: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text_secondary']};
    padding: 12px 20px;
}}

QPushButton#browse:hover {{
    background-color: {COLORS['bg_elevated']};
    border-color: {COLORS['text_muted']};
    color: {COLORS['text_primary']};
}}

/* ==================== 滑块 ==================== */
QSlider::groove:horizontal {{
    height: 4px;
    background: {COLORS['bg_input']};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {COLORS['accent']};
    border-radius: 2px;
}}

QSlider::add-page:horizontal {{
    background: {COLORS['bg_input']};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    background: {COLORS['text_primary']};
    border-radius: 8px;
    border: none;
}}

QSlider::handle:horizontal:hover {{
    background: #e0e0e0;
    width: 18px;
    height: 18px;
    margin: -7px 0;
    border-radius: 9px;
}}

/* ==================== 进度条 ==================== */
QProgressBar {{
    background-color: {COLORS['bg_input']};
    border-radius: 2px;
    text-align: center;
    color: {COLORS['text_secondary']};
    font-size: 11px;
    font-weight: 500;
    min-height: 3px;
    max-height: 3px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {COLORS['accent']}, stop:1 {COLORS['accent_light']});
    border-radius: 2px;
}}

/* ==================== 文本编辑框 (日志) ==================== */
QTextEdit {{
    background-color: {COLORS['bg_void']};
    color: {COLORS['text_secondary']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: 10px;
    padding: 12px;
    font-family: {FONTS['mono']};
    font-size: 12px;
    line-height: 1.7;
    selection-background-color: {COLORS['accent']};
}}

QTextEdit:focus {{
    border-color: {COLORS['border']};
}}

/* ==================== 复选框 ==================== */
QCheckBox {{
    spacing: 10px;
    font-size: 13px;
    color: {COLORS['text_secondary']};
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid {COLORS['border']};
    background: transparent;
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['accent']};
}}

QCheckBox::indicator:hover {{
    border-color: {COLORS['accent']};
}}

QCheckBox::indicator:checked:hover {{
    background-color: {COLORS['accent_hover']};
    border-color: {COLORS['accent_hover']};
}}

/* ==================== 菜单栏 ==================== */
QMenuBar {{
    background-color: transparent;
    color: {COLORS['text_secondary']};
    font-size: 13px;
    padding: 4px 0;
}}

QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 6px;
}}

QMenuBar::item:selected {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
}}

QMenu {{
    background-color: {COLORS['bg_elevated']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 6px;
}}

QMenu::item {{
    padding: 8px 20px;
    border-radius: 6px;
    color: {COLORS['text_secondary']};
}}

QMenu::item:selected {{
    background-color: {COLORS['accent_dim']};
    color: {COLORS['accent']};
}}

/* ==================== 消息框 ==================== */
QMessageBox {{
    background-color: {COLORS['bg_primary']};
}}

QMessageBox QLabel {{
    color: {COLORS['text_primary']};
    font-size: 13px;
}}

QMessageBox QPushButton {{
    min-width: 80px;
}}

/* ==================== 滚动条 ==================== */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border-radius: 4px;
    margin: 4px 0;
}}

QScrollBar::handle:vertical {{
    background: {COLORS['text_muted']};
    border-radius: 4px;
    min-height: 40px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLORS['text_tertiary']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    border-radius: 4px;
    margin: 0 4px;
}}

QScrollBar::handle:horizontal {{
    background: {COLORS['text_muted']};
    border-radius: 4px;
    min-width: 40px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {COLORS['text_tertiary']};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ==================== 树形视图和列表视图 (用于文件对话框等) ==================== */
QTreeView, QListView, QTableView {{
    background-color: {COLORS['bg_input']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: 6px;
    outline: none;
}}

QTreeView::item, QListView::item, QTableView::item {{
    padding: 4px;
}}

QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{
    background-color: {COLORS['bg_elevated']};
}}

QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background-color: {COLORS['accent']};
    color: {COLORS['bg_void']};
}}

QHeaderView::section {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_secondary']};
    border: none;
    border-right: 1px solid {COLORS['border_subtle']};
    border-bottom: 1px solid {COLORS['border_subtle']};
    padding: 6px;
    font-weight: 500;
}}

QHeaderView {{
    background-color: {COLORS['bg_card']};
    border: none;
}}

/* ==================== 选项卡 ==================== */
QTabWidget::pane {{
    background-color: {COLORS['bg_elevated']};
    border: 1px solid {COLORS['border_subtle']};
    border-radius: 10px;
    padding: 16px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {COLORS['text_tertiary']};
    padding: 10px 20px;
    margin-right: 4px;
    border-radius: 6px;
    font-size: 13px;
}}

QTabBar::tab:selected {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
}}

QTabBar::tab:hover:!selected {{
    color: {COLORS['text_secondary']};
}}

/* ==================== 下拉框 ==================== */
QComboBox {{
    background-color: {COLORS['bg_input']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 10px 16px;
    color: {COLORS['text_primary']};
    font-size: 13px;
    min-width: 120px;
}}

QComboBox:hover {{
    border-color: {COLORS['text_muted']};
}}

QComboBox:focus {{
    border-color: {COLORS['accent']};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS['text_tertiary']};
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLORS['bg_elevated']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {COLORS['accent']};
    selection-color: {COLORS['bg_void']};
}}

/* ==================== 工具提示 ==================== */
QToolTip {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
}}

/* ==================== 分割线 ==================== */
QFrame[frameShape="4"] {{
    background-color: {COLORS['border_subtle']};
    max-height: 1px;
}}

QFrame[frameShape="5"] {{
    background-color: {COLORS['border_subtle']};
    max-width: 1px;
}}

/* ==================== Dock 停靠面板 ==================== */
QDockWidget {{
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    font-weight: 500;
    titlebar-close-icon: url(none);
    titlebar-normal-icon: url(none);
}}

QDockWidget::title {{
    background-color: {COLORS['bg_elevated']};
    padding: 6px 12px;
    text-align: left;
    border-bottom: 1px solid {COLORS['border_subtle']};
}}

QDockWidget::close-button, QDockWidget::float-button {{
    background: transparent;
    border: none;
    padding: 0px;
    width: 0px;
    height: 0px;
}}

/* 主窗口与 Dock 之间的分隔线 - 更柔和的颜色 */
QMainWindow::separator {{
    background-color: {COLORS['bg_void']};
    width: 1px;
    height: 1px;
}}

QMainWindow::separator:hover {{
    background-color: {COLORS['border']};
}}
"""

# ==================== 组件特定样式 ====================

# 标题样式 (品牌区域)
TITLE_STYLE = f"""
QLabel {{
    color: {COLORS['text_primary']};
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.5px;
    background: transparent;
}}
"""

# 副标题样式
SUBTITLE_STYLE = f"""
QLabel {{
    color: {COLORS['text_tertiary']};
    font-size: 12px;
    font-weight: 400;
    background: transparent;
}}
"""

# 版本号样式
VERSION_STYLE = f"""
QLabel {{
    color: {COLORS['text_muted']};
    font-size: 11px;
    font-family: {FONTS['mono']};
    background-color: {COLORS['bg_elevated']};
    border-radius: 10px;
    padding: 4px 10px;
}}
"""

# 数值显示样式 (滑块旁边的数值)
VALUE_STYLE = f"""
QLabel {{
    color: {COLORS['accent']};
    font-size: 14px;
    font-family: {FONTS['mono']};
    font-weight: 500;
    background: transparent;
    min-width: 50px;
}}
"""

# 参数区域背景样式
PARAMETERS_SECTION_STYLE = f"""
QFrame {{
    background-color: {COLORS['bg_elevated']};
    border-radius: 10px;
    padding: 16px;
}}
"""

# 统计卡片样式
STAT_CARD_STYLE = f"""
QFrame {{
    background-color: {COLORS['bg_elevated']};
    border-radius: 8px;
    padding: 16px;
}}
"""

# 统计数值样式
STAT_VALUE_STYLE = f"""
QLabel {{
    color: {COLORS['text_primary']};
    font-size: 24px;
    font-weight: 600;
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# 统计数值 - 金色 (星级)
STAT_VALUE_GOLD_STYLE = f"""
QLabel {{
    color: {COLORS['star_gold']};
    font-size: 24px;
    font-weight: 600;
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# 统计数值 - 强调色
STAT_VALUE_ACCENT_STYLE = f"""
QLabel {{
    color: {COLORS['accent']};
    font-size: 24px;
    font-weight: 600;
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# 统计标签样式
STAT_LABEL_STYLE = f"""
QLabel {{
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    background: transparent;
}}
"""

# 进度信息样式
PROGRESS_INFO_STYLE = f"""
QLabel {{
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    background: transparent;
}}
"""

# 进度百分比样式
PROGRESS_PERCENT_STYLE = f"""
QLabel {{
    color: {COLORS['text_secondary']};
    font-size: 11px;
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# ==================== 日志颜色 ====================
LOG_COLORS = {
    'success': COLORS['success'],
    'warning': COLORS['warning'],
    'error': COLORS['error'],
    'info': COLORS['accent'],
    'accent': COLORS['accent'],
    'default': COLORS['text_secondary'],
    'muted': COLORS['text_muted'],
    'time': COLORS['text_muted'],
}

# ==================== 对话框专用样式 ====================

# 对话框标题样式
DIALOG_TITLE_STYLE = f"""
QLabel {{
    color: {COLORS['text_primary']};
    font-size: 15px;
    font-weight: 500;
    background: transparent;
}}
"""

# 对话框面板样式
DIALOG_PANEL_STYLE = f"""
QFrame {{
    background-color: {COLORS['bg_elevated']};
    border-radius: 10px;
}}
"""

# 对话框面板标题
DIALOG_PANEL_TITLE_STYLE = f"""
QLabel {{
    color: {COLORS['text_tertiary']};
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 1px;
    background: transparent;
}}
"""

# 变化指示 - 上升
CHANGE_UP_STYLE = f"""
QLabel {{
    color: {COLORS['success']};
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# 变化指示 - 下降
CHANGE_DOWN_STYLE = f"""
QLabel {{
    color: {COLORS['error']};
    font-family: {FONTS['mono']};
    background: transparent;
}}
"""

# ==================== QApplication 级别 Tooltip 样式 ====================
# macOS 上 QToolTip 是顶层窗口，不继承 QMainWindow 的样式表。
# 需要通过 app.setStyleSheet() 在 QApplication 级别设置才能覆盖系统原生外观。
# 在浅色模式下，系统会将 QToolTip 渲染为毛玻璃浅色背景，导致文字几乎不可见。
APP_TOOLTIP_STYLE = f"""
QToolTip {{
    background-color: {COLORS['bg_card']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
    font-family: {FONTS['sans']};
    opacity: 255;
}}
"""
