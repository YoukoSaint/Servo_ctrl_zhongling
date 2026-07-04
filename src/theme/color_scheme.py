# -*- coding: utf-8 -*-
"""
动态配色方案 + QSS 生成器。

完整照搬 https://github.com/YoukoSaint/UI_palette 的色彩系统：
  - 5 个基础色（TEXT/LIGHT/DARK/LINE/BTN）
  - 3 个图表曲线色
  - 2 个图表显示色
  - 派生色通过 _adjust(hex, amount) 内联生成

适配：本模块本身无 Qt 依赖（仅生成 QSS 字符串），图表相关样式保留以便后续添加 pyqtgraph。
"""
import re


HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

ROLES_BASE = ["TEXT", "LIGHT", "DARK", "LINE", "BTN"]
ROLES_CHART = ["SPECTRUM", "TREND", "RESISTANCE", "GRID", "AXIS"]

# 状态色（start/stop/save 等"硬编码"动作按钮，按原仓库约定绕过动态主题）
ROLE_STATUS = {
    "STATUS_OK": "#1a6b3c",     # 绿
    "STATUS_OK_TEXT": "#9ece6a",
    "STATUS_STOP": "#6b1a1a",   # 红
    "STATUS_STOP_TEXT": "#f28b82",
    "STATUS_INFO": "#1a4a6b",   # 蓝
    "STATUS_INFO_TEXT": "#7dcfff",
}


class ColorScheme:
    """动态配色方案。"""

    # 基础 5 色
    TEXT = "#c0caf5"
    LIGHT = "#1e1f2e"
    DARK = "#1a1b26"
    LINE = "#3b3d56"
    BTN = "#3b3d56"

    # 图表曲线
    SPECTRUM = "#0db9d7"
    TREND = "#bb9af7"
    RESISTANCE = "#f7768e"

    # 字号常量
    LABEL_SIZE = 14
    LABEL_ALPHA = "ff"
    AXIS_LABEL_SIZE = 13

    # 图表显示
    GRID = "#2c2d3f"
    AXIS = "#565f89"

    @classmethod
    def set_color(cls, role: str, hex_color: str) -> bool:
        if not HEX_PATTERN.match(hex_color):
            return False
        if hasattr(cls, role.upper()):
            setattr(cls, role.upper(), hex_color.upper())
            return True
        return False


def _adjust(hex_color: str, amount: int) -> str:
    hex_color = hex_color.lstrip("#")
    r = max(0, min(255, int(hex_color[0:2], 16) + amount))
    g = max(0, min(255, int(hex_color[2:4], 16) + amount))
    b = max(0, min(255, int(hex_color[4:6], 16) + amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgba(hex_color: str, alpha: int) -> tuple:
    hex_color = hex_color.lstrip("#")
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
        alpha,
    )


def get_stylesheet() -> str:
    text = ColorScheme.TEXT
    light = ColorScheme.LIGHT
    dark = ColorScheme.DARK
    line = ColorScheme.LINE
    btn = ColorScheme.BTN

    text2 = _adjust(text, -30)
    text3 = _adjust(text, -50)
    dark2 = _adjust(dark, -6)
    accent = _adjust(light, 30)
    btn_hover = _adjust(btn, 10)

    return f"""
QWidget {{
    background-color: {dark};
    color: {text};
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}}
QMainWindow {{ background-color: {dark2}; }}
QLabel {{ background-color: transparent; color: {text2}; border: none; }}

QGroupBox {{
    background-color: {light};
    border: 1px solid {line};
    border-radius: 8px;
    margin-top: 16px;
    padding: 16px 12px 12px 12px;
    font-weight: bold;
    font-size: 13px;
    color: {accent};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px; top: 2px;
    padding: 2px 8px;
    background-color: {light};
    border-radius: 4px;
    color: {accent};
}}

QPushButton {{
    background-color: {btn};
    color: {text};
    border: 1px solid {btn};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: bold;
    font-size: 13px;
}}
QPushButton:hover {{ background-color: {btn_hover}; border-color: {accent}; }}
QPushButton:pressed {{ background-color: {dark}; }}
QPushButton:disabled {{ background-color: {light}; color: {text3}; }}

QPushButton#btn_start {{
    background-color: {ROLE_STATUS["STATUS_OK"]};
    color: {ROLE_STATUS["STATUS_OK_TEXT"]};
    border-color: {_adjust(ROLE_STATUS["STATUS_OK"], 15)};
}}
QPushButton#btn_start:hover {{ background-color: {_adjust(ROLE_STATUS["STATUS_OK"], 12)}; }}
QPushButton#btn_stop {{
    background-color: {ROLE_STATUS["STATUS_STOP"]};
    color: {ROLE_STATUS["STATUS_STOP_TEXT"]};
    border-color: {_adjust(ROLE_STATUS["STATUS_STOP"], 15)};
}}
QPushButton#btn_stop:hover {{ background-color: {_adjust(ROLE_STATUS["STATUS_STOP"], 12)}; }}
QPushButton#btn_emergency {{
    background-color: #b00020;
    color: #ffffff;
    border-color: #ff1744;
    font-weight: 900;
}}
QPushButton#btn_emergency:hover {{ background-color: #d32f2f; }}

QLineEdit {{
    background-color: {dark2};
    color: {text};
    border: 1px solid {line};
    border-radius: 5px;
    padding: 5px 8px;
}}
QLineEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled {{ color: {text3}; }}

QSpinBox, QDoubleSpinBox {{
    background-color: {dark2};
    color: {text};
    border: 1px solid {line};
    border-radius: 5px;
    padding: 4px 6px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {text3}; }}

QComboBox {{
    background-color: {dark2};
    color: {text};
    border: 1px solid {line};
    border-radius: 5px;
    padding: 4px 8px;
}}
QComboBox:focus {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{
    background-color: {light};
    color: {text};
    border: 1px solid {line};
    border-radius: 4px;
    selection-background-color: {accent};
    outline: none;
}}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {line};
    border-radius: 3px;
    background-color: {dark2};
}}
QCheckBox::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}

QSplitter::handle {{ background-color: {line}; margin:1px; }}
QSplitter::handle:horizontal {{ width:3px; }}
QSplitter::handle:vertical {{ height:3px; }}
QSplitter::handle:hover {{ background-color: {accent}; }}

QPlainTextEdit, QTextEdit {{
    background-color: {dark2};
    color: {text2};
    border: 1px solid {line};
    border-radius: 6px;
    padding: 8px;
    font-family: "Consolas", "Courier New", "Microsoft YaHei", monospace;
    font-size: 12px;
}}
QPlainTextEdit:read-only, QTextEdit:read-only {{ selection-background-color: {accent}; }}

QStatusBar {{
    background-color: {dark2};
    color: {text2};
    border-top: 1px solid {line};
    padding: 2px 8px;
    font-size: 12px;
}}

QProgressBar {{
    background-color: {dark2};
    color: {text};
    border: 1px solid {line};
    border-radius: 4px;
    text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 3px;
}}

QScrollBar:vertical {{ background-color:{dark2}; width:10px; border-radius:5px; }}
QScrollBar::handle:vertical {{ background-color:{line}; border-radius:5px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background-color:{accent}; }}
QScrollBar:horizontal {{ background-color:{dark2}; height:10px; border-radius:5px; }}
QScrollBar::handle:horizontal {{ background-color:{line}; border-radius:5px; min-width:30px; }}
QScrollBar::handle:horizontal:hover {{ background-color:{accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; border:none; }}

QTabWidget::pane {{
    border: 1px solid {line};
    border-radius: 6px;
    background-color: {dark};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {light};
    color: {text2};
    padding: 6px 14px;
    border: 1px solid {line};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {dark};
    color: {accent};
    border-bottom: 1px solid {dark};
}}
"""
