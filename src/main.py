"""
舵机控制上位机 — 入口

支持两种启动方式：
  1. python -m src.main
  2. cd src && python main.py
  3. python src/main.py  (依赖本文件内的 fallback)
"""
import sys
from pathlib import Path

# 允许直接执行此文件：尝试把 src 的父目录加入 sys.path，
# 然后用绝对导入，让"python src/main.py"也能跑。
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))

try:
    # 包模式（推荐）
    from src.logger import setup_logging, get_logger
    from src.theme import get_stylesheet
    from src.ui.main_window import MainWindow
except ImportError:  # 兜底：直接脚本模式
    from .logger import setup_logging, get_logger
    from .theme import get_stylesheet
    from .ui.main_window import MainWindow

from PyQt5.QtWidgets import QApplication


def main() -> int:
    log_path = setup_logging(log_dir="logs")
    log = get_logger("servo.main")
    log.info("Application starting (log=%s)", log_path)

    app = QApplication(sys.argv)
    app.setApplicationName("ZL Servo Control")
    app.setOrganizationName("servo_ctrl")
    app.setStyleSheet(get_stylesheet())  # 必须在 MainWindow 之前

    win = MainWindow()
    win.show()

    try:
        return app.exec_()
    finally:
        log.info("Application exiting")


if __name__ == "__main__":
    sys.exit(main())
