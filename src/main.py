"""
舵机控制上位机 — 入口

支持两种启动方式：
  1. python -m src.main
  2. cd src && python main.py
  3. python src/main.py  (依赖本文件内的 fallback)
"""
import sys
import argparse
from pathlib import Path

# 允许直接执行此文件：尝试把 src 的父目录加入 sys.path，
# 然后用绝对导入，让"python src/main.py"也能跑。
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))

try:
    # 包模式（推荐）
    from src.logger import setup_logging, get_logger
    from src.http_api import EndEffectorApiServer
    from src.theme import get_stylesheet
    from src.ui.main_window import MainWindow
except ImportError:  # 兜底：直接脚本模式
    from .logger import setup_logging, get_logger
    from .http_api import EndEffectorApiServer
    from .theme import get_stylesheet
    from .ui.main_window import MainWindow

from PyQt5.QtWidgets import QApplication


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8877)
    parser.add_argument("--no-api", action="store_true")
    args, qt_args = parser.parse_known_args(sys.argv[1:])

    log_path = setup_logging(log_dir="logs")
    log = get_logger("servo.main")
    log.info("Application starting (log=%s)", log_path)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("ZL Servo Control")
    app.setOrganizationName("servo_ctrl")
    app.setStyleSheet(get_stylesheet())  # 必须在 MainWindow 之前

    win = MainWindow()
    win.show()

    api_server = None
    if not args.no_api:
        try:
            api_server = EndEffectorApiServer(
                win.request_start_from_http,
                host=args.api_host,
                port=args.api_port,
            )
            api_server.start()
        except OSError as exc:
            log.error(
                "Cannot start end-effector HTTP API on %s:%d: %s",
                args.api_host,
                args.api_port,
                exc,
            )
            win.statusBar().showMessage(f"HTTP API 启动失败: {exc}", 10000)

    try:
        return app.exec_()
    finally:
        if api_server is not None:
            api_server.stop()
        log.info("Application exiting")


if __name__ == "__main__":
    sys.exit(main())
