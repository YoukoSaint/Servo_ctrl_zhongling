# -*- coding: utf-8 -*-
"""单舵机控制面板（"基础控制" 标签页）。

输入：
    - 舵机 ID (QSpinBox, 0–254)
    - 工作模式 (QComboBox: 270° 顺/逆、180° 顺/逆 —— 映射 1–4)
    - 起始 PWM / 终止 PWM (QSpinBox, 500–2500)
    - 角度 (QDoubleSpinBox, -135..+135 度)
    - 时间 (QSpinBox, 0–9999 ms)
    - 水平滑块 (-135..+135)，实时显示当前角度

按钮：
    - "读取位置" / "读取模式"  —— query_position / query_mode
    - "释放扭矩" / "恢复扭矩" —— release / recover
    - "暂停" / "继续" / "停止" —— pause / "继续"(自定义未在 controller 中) / stop
    - "运动到目标角度"  —— move_to_angle
    - "运动到目标 PWM"   —— move_to_pwm

状态标签：当前 PWM、当前模式、连接状态。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..controller import BasicController
from ..protocol import ServoMode


_LOG = logging.getLogger(__name__)


_MODE_ITEMS = [
    ("270° 顺时针", ServoMode.SERVO_270_CW.value),
    ("270° 逆时针", ServoMode.SERVO_270_CCW.value),
    ("180° 顺时针", ServoMode.SERVO_180_CW.value),
    ("180° 逆时针", ServoMode.SERVO_180_CCW.value),
]


class ServoPanel(QWidget):
    """单舵机控制面板。"""

    status_message = pyqtSignal(str)   # 透传到主窗口状态栏

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._controller: Optional[BasicController] = None
        self._suppress_signals = False
        self._build_ui()
        self.set_enabled(False)

    # ----------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_param_group())
        root.addWidget(self._build_action_group())
        root.addWidget(self._build_slider_group(), stretch=1)
        root.addWidget(self._build_status_group())

    def _build_param_group(self) -> QGroupBox:
        gb = QGroupBox("参数")
        form = QFormLayout(gb)

        self._id_spin = QSpinBox()
        self._id_spin.setRange(0, 254)
        self._id_spin.setValue(0)  # COM8 实测舵机 ID=0

        self._mode_combo = QComboBox()
        for label, code in _MODE_ITEMS:
            self._mode_combo.addItem(label, code)
        self._mode_combo.currentIndexChanged.connect(self._refresh_angle_limits)

        self._start_pwm = QSpinBox()
        self._start_pwm.setRange(500, 2500)
        self._start_pwm.setValue(1500)

        self._end_pwm = QSpinBox()
        self._end_pwm.setRange(500, 2500)
        self._end_pwm.setValue(1500)

        self._angle_spin = QDoubleSpinBox()
        self._angle_spin.setRange(-135.0, 135.0)
        self._angle_spin.setDecimals(2)
        self._angle_spin.setSingleStep(1.0)
        self._angle_spin.setSuffix(" °")
        self._angle_spin.setValue(0.0)

        self._time_spin = QSpinBox()
        self._time_spin.setRange(0, 9999)
        self._time_spin.setValue(1000)
        self._time_spin.setSuffix(" ms")

        form.addRow("舵机 ID：", self._id_spin)
        form.addRow("工作模式：", self._mode_combo)
        form.addRow("起始 PWM：", self._start_pwm)
        form.addRow("终止 PWM：", self._end_pwm)
        form.addRow("目标角度：", self._angle_spin)
        form.addRow("运动时间：", self._time_spin)

        self._angle_spin.valueChanged.connect(self._on_angle_spin_changed)
        return gb

    def _build_action_group(self) -> QGroupBox:
        gb = QGroupBox("动作")
        grid = QGridLayout(gb)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._btn_read_pos = QPushButton("读取位置")
        # PMOD 读在 ZL-ZServo v2.x 固件不支持，禁用以避免误导
        self._btn_read_mode = QPushButton("读取模式")
        self._btn_read_mode.setEnabled(False)
        self._btn_read_mode.setToolTip("ZL-ZServo v2.x 固件不支持读取工作模式")
        self._btn_release = QPushButton("释放扭矩")
        self._btn_recover = QPushButton("恢复扭矩")
        self._btn_pause = QPushButton("暂停")
        self._btn_resume = QPushButton("继续")
        self._btn_stop = QPushButton("停止")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_move_angle = QPushButton("运动到目标角度")
        self._btn_move_pwm = QPushButton("运动到目标 PWM")

        self._btn_read_pos.clicked.connect(self._on_read_position)
        self._btn_read_mode.clicked.connect(self._on_read_mode)
        self._btn_release.clicked.connect(self._on_release)
        self._btn_recover.clicked.connect(self._on_recover)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_resume.clicked.connect(self._on_resume)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_move_angle.clicked.connect(self._on_move_angle)
        self._btn_move_pwm.clicked.connect(self._on_move_pwm)

        for btn in (
            self._btn_read_pos, self._btn_read_mode,
            self._btn_release, self._btn_recover,
            self._btn_pause, self._btn_resume,
            self._btn_stop, self._btn_move_angle, self._btn_move_pwm,
        ):
            btn.setMinimumHeight(28)

        layout_buttons = [
            self._btn_read_pos, self._btn_read_mode, self._btn_release,
            self._btn_recover, self._btn_pause, self._btn_resume,
            self._btn_stop, self._btn_move_angle, self._btn_move_pwm,
        ]
        for idx, b in enumerate(layout_buttons):
            r, c = divmod(idx, 3)
            grid.addWidget(b, r, c)
        return gb

    def _build_slider_group(self) -> QGroupBox:
        gb = QGroupBox("实时角度滑块")
        v = QVBoxLayout(gb)

        slider_row = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(-1350, 1350)  # ×10 精度
        self._slider.setSingleStep(10)
        self._slider.setPageStep(50)
        self._slider.setValue(0)
        self._slider.setTickInterval(270)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self._slider)
        v.addLayout(slider_row)

        scale_row = QHBoxLayout()
        for txt in ("-135°", "-90°", "-45°", "0°", "+45°", "+90°", "+135°"):
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scale_row.addWidget(lbl, stretch=1)
        v.addLayout(scale_row)
        return gb

    def _build_status_group(self) -> QGroupBox:
        gb = QGroupBox("状态")
        grid = QGridLayout(gb)

        self._cur_pwm_lbl = QLabel("—")
        self._cur_mode_lbl = QLabel("—")
        self._cur_conn_lbl = QLabel("未连接")
        self._cur_conn_lbl.setStyleSheet("color: #f7768e; font-weight: bold;")

        for col, (lbl, val) in enumerate([
            ("当前 PWM：", self._cur_pwm_lbl),
            ("当前模式：", self._cur_mode_lbl),  # v2.x 不支持读取，始终显示"—"
            ("连接状态：", self._cur_conn_lbl),
        ]):
            grid.addWidget(QLabel(lbl), 0, col * 2)
            grid.addWidget(val, 0, col * 2 + 1)
        return gb

    # ------------------------------------------------------------- 绑定

    def bind_controller(self, controller: Optional[BasicController]) -> None:
        """由主窗口在连接成功后调用；断开时传 None。"""
        self._controller = controller
        connected = controller is not None
        self.set_enabled(connected)
        if connected:
            self._cur_conn_lbl.setText("已连接")
            self._cur_conn_lbl.setStyleSheet("color: #9ece6a; font-weight: bold;")
            client = controller.client
            client.position_updated.connect(self._on_position_updated)
            client.frame_received.connect(self._on_frame_received)
            client.error.connect(lambda msg: self.status_message.emit(f"[servo] {msg}"))
        else:
            self._cur_conn_lbl.setText("未连接")
            self._cur_conn_lbl.setStyleSheet("color: #f7768e; font-weight: bold;")
            self._cur_pwm_lbl.setText("—")
            self._cur_mode_lbl.setText("—")

    def set_enabled(self, enabled: bool) -> None:
        for w in (
            self._id_spin, self._mode_combo,
            self._start_pwm, self._end_pwm,
            self._angle_spin, self._time_spin,
            self._slider,
            self._btn_read_pos, self._btn_read_mode,
            self._btn_release, self._btn_recover,
            self._btn_pause, self._btn_resume, self._btn_stop,
            self._btn_move_angle, self._btn_move_pwm,
        ):
            w.setEnabled(enabled)

    # ------------------------------------------------------------ 事件

    def _refresh_angle_limits(self) -> None:
        mode = self._mode_combo.currentData()
        if mode in (ServoMode.SERVO_270_CW.value, ServoMode.SERVO_270_CCW.value):
            self._angle_spin.setRange(-135.0, 135.0)
        else:
            self._angle_spin.setRange(-90.0, 90.0)
        lo, hi = self._angle_spin.minimum(), self._angle_spin.maximum()
        if self._angle_spin.value() < lo:
            self._angle_spin.setValue(lo)
        elif self._angle_spin.value() > hi:
            self._angle_spin.setValue(hi)

    def _on_angle_spin_changed(self, val: float) -> None:
        if self._suppress_signals:
            return
        self._suppress_signals = True
        try:
            self._slider.setValue(int(round(val * 10)))
        finally:
            self._suppress_signals = False

    def _on_slider_changed(self, val: int) -> None:
        if self._suppress_signals:
            return
        self._suppress_signals = True
        try:
            self._angle_spin.setValue(val / 10.0)
        finally:
            self._suppress_signals = False

    # --------------------------------------------------------- 命令槽

    def _on_read_position(self) -> None:
        if self._controller is None:
            return
        ok = self._controller.query_position(self._id_spin.value())
        if ok:
            self.status_message.emit(f"[servo] 读取位置 → ID={self._id_spin.value()}")
        else:
            self.status_message.emit("[servo] 读取位置失败")

    def _on_read_mode(self) -> None:
        if self._controller is None:
            return
        ok = self._controller.query_mode(self._id_spin.value())
        if ok:
            self.status_message.emit(f"[servo] 读取模式 → ID={self._id_spin.value()}")
        else:
            self.status_message.emit("[servo] 读取模式失败")

    def _on_release(self) -> None:
        if self._controller is None:
            return
        if self._controller.release(self._id_spin.value()):
            self.status_message.emit(f"[servo] 已释放扭矩 ID={self._id_spin.value()}")

    def _on_recover(self) -> None:
        if self._controller is None:
            return
        if self._controller.recover(self._id_spin.value()):
            self.status_message.emit(f"[servo] 已恢复扭矩 ID={self._id_spin.value()}")

    def _on_pause(self) -> None:
        if self._controller is None:
            return
        if self._controller.pause(self._id_spin.value()):
            self.status_message.emit(f"[servo] 已暂停 ID={self._id_spin.value()}")

    def _on_resume(self) -> None:
        if self._controller is None:
            return
        if self._controller.resume(self._id_spin.value()):
            self.status_message.emit(f"[servo] 已继续 ID={self._id_spin.value()}")

    def _on_stop(self) -> None:
        if self._controller is None:
            return
        if self._controller.stop(self._id_spin.value()):
            self.status_message.emit(f"[servo] 已停止 ID={self._id_spin.value()}")

    def _on_move_angle(self) -> None:
        if self._controller is None:
            return
        angle = self._angle_spin.value()
        mode = self._mode_combo.currentData()
        t = self._time_spin.value()
        if self._controller.move_to_angle(self._id_spin.value(), angle, t, mode):
            self.status_message.emit(
                f"[servo] ID={self._id_spin.value()} → {angle:+.2f}° ({t}ms)"
            )

    def _on_move_pwm(self) -> None:
        if self._controller is None:
            return
        pwm = self._end_pwm.value()
        t = self._time_spin.value()
        if self._controller.move_to_pwm(self._id_spin.value(), pwm, t):
            self.status_message.emit(
                f"[servo] ID={self._id_spin.value()} → PWM={pwm} ({t}ms)"
            )

    # ----------------------------------------------------- 客户端回调

    def _on_position_updated(self, id_: int, pwm: int) -> None:
        if id_ != self._id_spin.value():
            return
        self._cur_pwm_lbl.setText(str(pwm))
        mode = self._mode_combo.currentData()
        try:
            if mode in (ServoMode.SERVO_270_CW.value, ServoMode.SERVO_270_CCW.value):
                from ..protocol import pwm_to_angle_270
                angle = pwm_to_angle_270(pwm)
            else:
                from ..protocol import pwm_to_angle_180
                angle = pwm_to_angle_180(pwm)
            self._suppress_signals = True
            try:
                self._slider.setValue(int(round(angle * 10)))
            finally:
                self._suppress_signals = False
        except Exception:  # noqa: BLE001
            pass

    def _on_frame_received(self, frame: str) -> None:
        from ..protocol import parse_response
        parsed = parse_response(frame)
        if parsed is None:
            return
        if parsed.kind == "mode" and parsed.mode is not None and parsed.id == self._id_spin.value():
            self._cur_mode_lbl.setText(f"模式 {parsed.mode}")