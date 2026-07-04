# -*- coding: utf-8 -*-
"""舵机控制上位机 — 主窗口。

布局：
    - 顶部 :class:`ConnectionPanel`（串口选择、波特率、连接/断开/紧急停止）
    - 中部 :class:`QTabWidget`：
        - "基础控制" → :class:`ServoPanel`
        - "循环控制" → :class:`LoopPanel`
    - 底部 :class:`LogPanel`（可折叠）
    - 状态栏：连接状态 + 最近一次发送/接收帧

实现注意：
    - 所有控件创建完成后才调用 :func:`get_stylesheet` 应用 QSS，
      避免内联样式覆盖（参考 UI_palette 的做法）。
    - ServoClient / BasicController / LoopRunner 通过连接面板的 signal 装配。
    - 退出时关闭 loop runner、断开 client、释放 transport。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..controller import BasicController
from ..serial_link import ServoClient
from ..theme import get_stylesheet
from .connection_panel import ConnectionPanel
from .log_panel import LogPanel
from .loop_panel import LoopPanel
from .servo_panel import ServoPanel


_LOG = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """主窗口。"""

    WINDOW_TITLE = "舵机控制上位机 — ZL Servo Control"
    DEFAULT_SIZE = (1400, 900)
    MIN_SIZE = (1024, 680)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(self.WINDOW_TITLE)
        self.resize(*self.DEFAULT_SIZE)
        self.setMinimumSize(*self.MIN_SIZE)

        self._client: Optional[ServoClient] = None
        self._controller: Optional[BasicController] = None

        # ---- 控件 ----
        self._log_panel = LogPanel()
        self._connection = ConnectionPanel()
        self._servo_panel = ServoPanel()
        self._loop_panel = LoopPanel()

        # 日志装到根 logger（在控件都创建好之后才装）
        self._log_panel.install_handler()

        # ---- 装配布局 ----
        self._setup_ui()
        self._setup_status_bar()
        self._wire_signals()

        # 默认禁用控制面板（连接前）
        self._servo_panel.set_enabled(False)
        self._loop_panel.set_enabled(False)

        # 应用 QSS —— 必须**最后**调用，参考 UI_palette 的做法
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(get_stylesheet())

    # ------------------------------------------------------------------ UI

    @staticmethod
    def _wrap_scrollable(widget: QWidget) -> QScrollArea:
        """把面板包在一个可滚动的 QScrollArea 中。

        窗口缩小时内容可通过滚动条而非被裁剪查看。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # 顶部：连接面板
        root.addWidget(self._connection)

        # 中部：标签页（每个标签包在 QScrollArea 里，屏幕小时可滚动）
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.addTab(self._wrap_scrollable(self._servo_panel), "基础控制")
        self._tabs.addTab(self._wrap_scrollable(self._loop_panel), "循环控制")
        root.addWidget(self._tabs, stretch=1)

        # 底部：日志面板（可折叠）
        self._log_visible = True
        self._log_toggle = QPushButton("▼  日志")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setChecked(True)
        self._log_toggle.setFixedHeight(24)
        self._log_toggle.setStyleSheet(
            "QPushButton { text-align: left; padding: 2px 8px; "
            "background-color: transparent; border: none; color: #7dcfff; }"
            "QPushButton:hover { color: #c0caf5; }"
        )
        self._log_toggle.clicked.connect(self._toggle_log_panel)

        log_header = QHBoxLayout()
        log_header.setContentsMargins(0, 0, 0, 0)
        log_header.addWidget(self._log_toggle)
        log_header.addStretch(1)
        root.addLayout(log_header)

        # 日志本体
        self._log_panel.setMinimumHeight(120)
        self._log_panel.setMaximumHeight(280)
        root.addWidget(self._log_panel)

    def _setup_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_conn = QCheckBox("未连接")
        self._status_conn.setEnabled(False)
        self._status_conn.setStyleSheet("color: #f7768e; font-weight: bold;")
        self._status_last_tx = QPushButton("TX: —")
        self._status_last_tx.setFlat(True)
        self._status_last_tx.setStyleSheet("color: #c0caf5; text-align: left;")
        self._status_last_tx.setMinimumWidth(220)
        self._status_last_rx = QPushButton("RX: —")
        self._status_last_rx.setFlat(True)
        self._status_last_rx.setStyleSheet("color: #c0caf5; text-align: left;")
        self._status_last_rx.setMinimumWidth(220)
        sb.addWidget(self._status_conn)
        sb.addPermanentWidget(self._status_last_tx, stretch=1)
        sb.addPermanentWidget(self._status_last_rx, stretch=1)
        sb.showMessage("就绪")

    def _wire_signals(self) -> None:
        # 连接/断开
        self._connection.transport_ready.connect(self._on_transport_ready)
        self._connection.transport_closed.connect(self._on_transport_closed)
        self._connection.status_message.connect(self._show_message)
        self._connection.emergency_stop_requested.connect(self._on_emergency)

        # 控制面板状态透传
        self._servo_panel.status_message.connect(self._show_message)
        self._loop_panel.status_message.connect(self._show_message)

    # ----------------------------------------------------------- 回调

    def _on_transport_ready(self, client: ServoClient) -> None:
        """连接成功：装配 BasicController，启动 reader，启用面板。"""
        self._client = client
        try:
            client.start_reader()
        except Exception:  # noqa: BLE001
            _LOG.exception("start_reader failed")
        self._controller = BasicController(client, self)
        self._servo_panel.bind_controller(self._controller)
        self._loop_panel.bind_controller(self._controller)

        # 状态栏
        self._status_conn.setText("已连接")
        self._status_conn.setStyleSheet("color: #9ece6a; font-weight: bold;")
        self.statusBar().showMessage("已连接")

        # 透传帧到状态栏
        client.frame_sent.connect(self._on_frame_sent)
        client.frame_received.connect(self._on_frame_received)
        client.error.connect(lambda msg: self._show_message(f"[client] {msg}"))

        # 关键：把应答信号接给面板（位置更新 → 基础面板，帧解析 → 基础面板模式/版本回显）
        client.position_updated.connect(self._servo_panel._on_position_updated)
        client.frame_received.connect(self._servo_panel._on_frame_received)

        self._show_message("连接就绪，发送/接收监控已启动")

    def _on_transport_closed(self) -> None:
        """断开连接：禁用面板，释放控制器引用。"""
        client = self._client
        if client is not None:
            try:
                client.frame_sent.disconnect(self._on_frame_sent)
                client.frame_received.disconnect(self._on_frame_received)
            except (TypeError, RuntimeError):
                pass
        self._client = None
        self._controller = None
        self._servo_panel.bind_controller(None)
        self._loop_panel.bind_controller(None)

        self._status_conn.setText("未连接")
        self._status_conn.setStyleSheet("color: #f7768e; font-weight: bold;")
        self.statusBar().showMessage("已断开")

    def _on_emergency(self, client: ServoClient) -> None:
        if client is None:
            return
        self._show_message("[emergency] 已广播 #255PDST!")
        self.statusBar().showMessage("紧急停止已发送", 3000)

    def _on_frame_sent(self, frame: str) -> None:
        self._status_last_tx.setText(f"TX: {frame}")

    def _on_frame_received(self, frame: str) -> None:
        self._status_last_rx.setText(f"RX: {frame}")

    def _show_message(self, msg: str) -> None:
        # 显示 5 秒，避免快速刷新遮挡
        self.statusBar().showMessage(msg, 5000)

    # ----------------------------------------------------------- 折叠

    def _toggle_log_panel(self) -> None:
        if self._log_visible:
            self._log_panel.hide()
            self._log_toggle.setText("▲  日志")
            self._log_toggle.setChecked(False)
        else:
            self._log_panel.show()
            self._log_toggle.setText("▼  日志")
            self._log_toggle.setChecked(True)
        self._log_visible = not self._log_visible

    # ----------------------------------------------------------- 关闭

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        # 1) 停止循环
        try:
            self._loop_panel.force_stop()
        except Exception:  # noqa: BLE001
            _LOG.exception("force_stop loop failed")
        # 2) 断开 client
        try:
            self._connection.force_disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("force_disconnect failed")
        # 3) 卸载日志 handler
        try:
            self._log_panel.uninstall_handler()
        except Exception:  # noqa: BLE001
            _LOG.exception("uninstall handler failed")
        super().closeEvent(event)


__all__ = ["MainWindow"]