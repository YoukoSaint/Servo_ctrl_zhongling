# -*- coding: utf-8 -*-
"""串口连接面板。

- 端口下拉 + 刷新按钮（调用 ``ServoTransport.available_ports()``）。
- 波特率下拉（默认 115200，覆盖 9600..1000000）。
- "Mock 模式（无硬件自测）" 复选框 —— 勾选时使用 :class:`MockTransport`。
- "连接" / "断开" 按钮：使用 ``objectName`` ``#btn_start``（绿） / ``#btn_stop``（红）。
- 紧急停止按钮：``objectName`` ``#btn_emergency``（深红）。
- 成功连接后禁用端口/波特率/Mock 复选框，启用"断开"和"紧急停止"。
- 失败时通过 ``status_message`` 显示错误。

信号：
    - ``transport_ready(object)`` —— 携带 ``ServoClient`` 实例。
    - ``transport_closed()`` —— 断开完成。
    - ``emergency_stop_requested(object)`` —— 携带 ``ServoClient`` 用于广播停止。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..serial_link import MockTransport, SerialTransport, ServoClient


_LOG = logging.getLogger(__name__)


_BAUDRATES = [9600, 19200, 38400, 57600, 115200, 128000, 256000, 1000000]


class ConnectionPanel(QGroupBox):
    """顶部串口连接面板。"""

    transport_ready = pyqtSignal(object)             # ServoClient
    transport_closed = pyqtSignal()
    emergency_stop_requested = pyqtSignal(object)    # ServoClient
    status_message = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("串口连接", parent)
        self._client: Optional[ServoClient] = None

        self._build_ui()
        self._refresh_ports()
        self._set_connected_state(False)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 16, 12, 12)
        outer.setSpacing(10)

        # ── 端口 ──────────────────────────────────────────────────────
        port_box = QWidget()
        port_lay = QVBoxLayout(port_box)
        port_lay.setContentsMargins(0, 0, 0, 0)
        port_lay.setSpacing(4)
        port_lbl = QLabel("端口")
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(140)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setFixedWidth(60)
        self._refresh_btn.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.setSpacing(4)
        port_row.addWidget(self._port_combo, stretch=1)
        port_row.addWidget(self._refresh_btn)
        port_lay.addWidget(port_lbl)
        port_lay.addLayout(port_row)
        outer.addWidget(port_box)

        # ── 波特率 ────────────────────────────────────────────────────
        baud_box = QWidget()
        baud_lay = QVBoxLayout(baud_box)
        baud_lay.setContentsMargins(0, 0, 0, 0)
        baud_lay.setSpacing(4)
        baud_lbl = QLabel("波特率")
        self._baud_combo = QComboBox()
        for b in _BAUDRATES:
            self._baud_combo.addItem(str(b), b)
        # 默认 115200
        idx = self._baud_combo.findData(115200)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)
        baud_lay.addWidget(baud_lbl)
        baud_lay.addWidget(self._baud_combo)
        outer.addWidget(baud_box)

        # ── Mock 复选 ────────────────────────────────────────────────
        mock_box = QWidget()
        mock_lay = QVBoxLayout(mock_box)
        mock_lay.setContentsMargins(0, 0, 0, 0)
        mock_lay.setSpacing(4)
        mock_lbl = QLabel("模式")
        mock_lbl.setVisible(False)  # 占位对齐
        self._mock_check = QCheckBox("Mock 模式（无硬件自测）")
        mock_lay.addWidget(mock_lbl)
        mock_lay.addWidget(self._mock_check)
        outer.addWidget(mock_box)

        outer.addStretch(1)

        # ── 状态指示 ──────────────────────────────────────────────────
        self._status_label = QLabel("未连接")
        self._status_label.setMinimumWidth(120)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._status_label)

        # ── 操作按钮 ──────────────────────────────────────────────────
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("btn_start")
        self._connect_btn.setFixedWidth(90)
        self._connect_btn.clicked.connect(self._on_connect_clicked)

        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.setObjectName("btn_stop")
        self._disconnect_btn.setFixedWidth(90)
        self._disconnect_btn.clicked.connect(self._on_disconnect_clicked)

        self._emergency_btn = QPushButton("紧急停止")
        self._emergency_btn.setObjectName("btn_emergency")
        self._emergency_btn.setFixedWidth(100)
        self._emergency_btn.clicked.connect(self._on_emergency_clicked)

        outer.addWidget(self._connect_btn)
        outer.addWidget(self._disconnect_btn)
        outer.addWidget(self._emergency_btn)

    # ------------------------------------------------------------ 行为

    def _refresh_ports(self) -> None:
        """刷新端口下拉框。优先用 SerialTransport，失败则退到 MockTransport。
        自动选中第一个真实端口（如 COM8）。"""
        current_data = self._port_combo.currentData()
        self._port_combo.blockSignals(True)
        self._port_combo.clear()
        try:
            ports = SerialTransport.available_ports()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SerialTransport.available_ports failed: %s", exc)
            ports = []
        if not ports:
            try:
                ports = MockTransport.available_ports()
            except Exception:  # noqa: BLE001
                ports = []
        if not ports:
            ports = [""]
        for p in ports:
            self._port_combo.addItem(p, p)
        # 自动选中第一个包含数字的端口（如 COM8），否则保持之前的选择
        if current_data:
            idx = self._port_combo.findData(current_data)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
        else:
            for i in range(self._port_combo.count()):
                txt = self._port_combo.itemText(i)
                if any(c.isdigit() for c in txt):
                    self._port_combo.setCurrentIndex(i)
                    break
        self._port_combo.blockSignals(False)
        _LOG.info("刷新端口: %s (共 %d 个)", ports, len(ports))

    def _on_connect_clicked(self) -> None:
        if self._client is not None:
            return
        port = self._port_combo.currentData() or self._port_combo.currentText()
        baud = int(self._baud_combo.currentData())
        use_mock = self._mock_check.isChecked()

        if not port:
            self._show_status("无可用端口", error=True)
            return

        try:
            transport = MockTransport() if use_mock else SerialTransport(port, baud)
            client = ServoClient(self)
            ok = client.connect_transport(transport, port_name=port, baudrate=baud)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("connect failed")
            self._show_status(f"打开失败：{exc}", error=True)
            return

        if not ok:
            self._show_status("连接失败", error=True)
            return

        self._client = client
        self._show_status(f"已连接：{port} @ {baud}", ok=True)
        self._set_connected_state(True)
        client.disconnected.connect(self._on_client_disconnected)
        client.error.connect(self._on_client_error)
        self.transport_ready.emit(client)

    def _on_disconnect_clicked(self) -> None:
        if self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            _LOG.exception("disconnect failed")
        # _on_client_disconnected() 会在 signal 回调中触发

    def _on_client_disconnected(self) -> None:
        self._show_status("未连接")
        self._set_connected_state(False)
        client = self._client
        self._client = None
        self.transport_closed.emit()
        if client is not None:
            try:
                client.deleteLater()
            except Exception:  # noqa: BLE001
                pass

    def _on_client_error(self, msg: str) -> None:
        self._show_status(msg, error=True)
        self.status_message.emit(msg)

    def _on_emergency_clicked(self) -> None:
        if self._client is None:
            return
        try:
            self._client.emergency_stop_all()
            self._show_status("已发送紧急停止 (#255PDST!)", warn=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("emergency stop failed")
            self._show_status(f"紧急停止失败：{exc}", error=True)
        self.emergency_stop_requested.emit(self._client)

    # ----------------------------------------------------------- 辅助

    def _set_connected_state(self, connected: bool) -> None:
        self._port_combo.setEnabled(not connected)
        self._baud_combo.setEnabled(not connected)
        self._refresh_btn.setEnabled(not connected)
        self._mock_check.setEnabled(not connected)
        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)
        self._emergency_btn.setEnabled(connected)

    def _show_status(self, text: str, *, ok: bool = False, warn: bool = False, error: bool = False) -> None:
        self._status_label.setText(text)
        if error:
            color = "#f7768e"
        elif warn:
            color = "#e0af68"
        elif ok:
            color = "#9ece6a"
        else:
            color = "#c0caf5"
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ----------------------------------------------------------- 公共

    @property
    def client(self) -> Optional[ServoClient]:
        return self._client

    def force_disconnect(self) -> None:
        """由主窗口在退出时调用，确保释放底层资源。"""
        if self._client is None:
            return
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass