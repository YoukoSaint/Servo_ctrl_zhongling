# -*- coding: utf-8 -*-
"""日志面板。

- 通过 :class:`QtSignalHandler`（``logging.Handler`` 子类）把每条日志消息以
  ``pyqtSignal(str)`` 形式广播；面板订阅该信号并按级别着色。
- "清空" 按钮直接清空 :class:`QPlainTextEdit`。
- "导出" 按钮通过 :class:`QFileDialog` 选择目标文件并写入。

颜色（来自 ``color_scheme.ColorScheme``）：
    - INFO    -> ColorScheme.TEXT  (默认)
    - WARNING -> "#e0af68"
    - ERROR   -> "#f7768e"
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QTextCharFormat, QTextCursor, QColor, QFont
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
#  QtSignalHandler —— 把 logging 桥接到 Qt 信号
# ---------------------------------------------------------------------------

class QtSignalHandler(logging.Handler, QObject):
    """``logging.Handler`` 子类：emit 时把日志消息通过 Qt 信号发送出去。

    使用方式::

        handler = QtSignalHandler()
        handler.setLevel(logging.DEBUG)
        handler.message.connect(some_slot)
        logging.getLogger().addHandler(handler)
    """

    message = pyqtSignal(int, str)   # (level, formatted_message)

    def __init__(self, level: int = logging.NOTSET, parent: Optional[QObject] = None):
        logging.Handler.__init__(self, level=level)
        QObject.__init__(self, parent)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            msg = record.getMessage()
        self.message.emit(record.levelno, msg)


# ---------------------------------------------------------------------------
#  LogPanel —— 实际显示日志的 QWidget
# ---------------------------------------------------------------------------

class LogPanel(QWidget):
    """日志显示面板。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._handler: Optional[QtSignalHandler] = None
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # 工具条
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setFixedWidth(80)
        self._clear_btn.clicked.connect(self.clear)

        self._export_btn = QPushButton("导出")
        self._export_btn.setFixedWidth(80)
        self._export_btn.clicked.connect(self._export_log)

        bar.addStretch(1)
        bar.addWidget(self._clear_btn)
        bar.addWidget(self._export_btn)
        root.addLayout(bar)

        # 日志视图
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(500)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # 等宽字体在 QSS 中已设置，这里仅放保险
        font = QFont("Consolas", 10)
        self._view.setFont(font)
        root.addWidget(self._view, stretch=1)

    # ----------------------------------------------------------- 公共 API

    def install_handler(self, logger: Optional[logging.Logger] = None) -> QtSignalHandler:
        """把 QtSignalHandler 装到指定 logger（默认根 logger），并连接到面板。"""
        if self._handler is not None:
            return self._handler
        target = logger if logger is not None else logging.getLogger()
        handler = QtSignalHandler(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d - %(message)s",
            datefmt="%H:%M:%S",
        ))
        handler.message.connect(self._append_level)
        target.addHandler(handler)
        self._handler = handler
        return handler

    def uninstall_handler(self) -> None:
        if self._handler is None:
            return
        root = logging.getLogger()
        try:
            root.removeHandler(self._handler)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._handler.message.disconnect(self._append_level)
        except (TypeError, RuntimeError):
            pass
        self._handler = None

    def clear(self) -> None:
        self._view.clear()

    # --------------------------------------------------------- 内部槽

    def _append_level(self, level: int, message: str) -> None:
        color = self._color_for_level(level)
        self._append_colored(message, color)

    @staticmethod
    def _color_for_level(level: int) -> str:
        if level >= logging.ERROR:
            return "#f7768e"
        if level >= logging.WARNING:
            return "#e0af68"
        # INFO / DEBUG：默认主题文字色
        return "#c0caf5"

    def _append_colored(self, text: str, color_hex: str) -> None:
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color_hex))
        cursor.insertText(text + "\n", fmt)

        # 自动滚动到底
        sb = self._view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _export_log(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"servo_log_{ts}.txt"
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志",
            default_name,
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not path_str:
            return
        try:
            Path(path_str).write_text(self._view.toPlainText(), encoding="utf-8")
        except OSError as exc:  # noqa: PERF203
            self._append_colored(f"[log] 导出失败：{exc}", "#f7768e")