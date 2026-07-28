# -*- coding: utf-8 -*-
"""循环控制模式面板（"循环控制" 标签页，核心）。

输入：
    - 循环次数  (QSpinBox, 1–9999) — ``cycles``（1 = 单程；2 = 1 个完整来回）
    - 单程时长  (QSpinBox, 1–60000 ms) — ``period_ms``，标签为 "单程时长 (ms)"
    - 起始角度  (QDoubleSpinBox, -135..+135) — ``start_angle``
    - 终止角度  (QDoubleSpinBox, -135..+135) — ``end_angle``
    - 舵机 ID   (QSpinBox, 0–254) —— 255 广播禁用
    - 工作模式  (QComboBox: 270° / 180°)

控件：
    - "启动循环" 按钮（绿，objectName="btn_start"）
    - "停止循环" 按钮（红，objectName="btn_stop"）
    - QProgressBar 显示 ``current_pair / total_pairs``
    - 状态标签：当前 state (Idle/Running/Completed/Aborted)
    - 摘要标签：完成后显示 "X 个完整来回，Y.YY 秒"

注：
    - :class:`LoopRunner` 在主窗口中创建（持有 ServoClient），
      loop_panel 只通过 ``start_loop`` / ``stop_loop`` 调用。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..controller import (
    BasicController,
    LoopParams,
    LoopRunner,
    SineParams,
    SineRunner,
    STATE_ABORTED,
    STATE_COMPLETED,
    STATE_IDLE,
    STATE_RUNNING,
)
from ..protocol import ServoMode


_LOG = logging.getLogger(__name__)

_LOOP_MODE_ITEMS = [("线性往复", "linear"), ("正弦曲线", "sine")]

_MODE_ITEMS = [
    ("270°", ServoMode.SERVO_270_CW.value),
    ("180°", ServoMode.SERVO_180_CW.value),
]


class LoopPanel(QWidget):
    """循环控制模式面板。"""

    status_message = pyqtSignal(str)   # 透传到主窗口状态栏

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._controller: Optional[BasicController] = None
        self._runner: Optional[LoopRunner] = None       # 线性往复
        self._sine_runner: Optional[SineRunner] = None  # 正弦曲线
        self._t_start: float = 0.0

        self._build_ui()
        self.set_enabled(False)

    # ----------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_param_group())
        root.addWidget(self._build_control_group())
        root.addWidget(self._build_status_group(), stretch=1)

    def _build_param_group(self) -> QGroupBox:
        gb = QGroupBox("循环参数")
        root = QVBoxLayout(gb)
        root.setSpacing(6)

        # ── 运动模式选择 ──
        self._loop_mode_combo = QComboBox()
        for label, val in _LOOP_MODE_ITEMS:
            self._loop_mode_combo.addItem(label, val)
        self._loop_mode_combo.currentIndexChanged.connect(self._on_loop_mode_changed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("运动模式："))
        mode_row.addWidget(self._loop_mode_combo, stretch=1)
        root.addLayout(mode_row)

        # ── 普通共用字段 ──
        self._cycles_spin = QSpinBox()
        self._cycles_spin.setRange(1, 9999)
        self._cycles_spin.setValue(2)
        self._cycles_spin.setSuffix(" 次")

        self._period_spin = QSpinBox()
        self._period_spin.setRange(1, 60000)
        self._period_spin.setValue(1000)
        self._period_spin.setSuffix(" ms")

        self._id_spin = QSpinBox()
        self._id_spin.setRange(0, 254)
        self._id_spin.setValue(0)

        self._mode_combo = QComboBox()
        for label, code in _MODE_ITEMS:
            self._mode_combo.addItem(label, code)
        self._mode_combo.currentIndexChanged.connect(self._refresh_angle_limits)

        form = QFormLayout()
        form.addRow("循环次数：", self._cycles_spin)
        form.addRow("周期时长 (ms)：", self._period_spin)
        form.addRow("舵机 ID：", self._id_spin)
        form.addRow("工作模式：", self._mode_combo)
        root.addLayout(form)

        # ── 线性往复参数子组 ──
        self._linear_group = QGroupBox("线性参数")
        lf = QFormLayout(self._linear_group)
        self._start_angle = QDoubleSpinBox()
        self._start_angle.setRange(-135.0, 135.0)
        self._start_angle.setDecimals(2)
        self._start_angle.setSingleStep(5.0)
        self._start_angle.setSuffix(" °")
        self._start_angle.setValue(-90.0)
        lf.addRow("起始角度：", self._start_angle)

        self._end_angle = QDoubleSpinBox()
        self._end_angle.setRange(-135.0, 135.0)
        self._end_angle.setDecimals(2)
        self._end_angle.setSingleStep(5.0)
        self._end_angle.setSuffix(" °")
        self._end_angle.setValue(90.0)
        lf.addRow("终止角度：", self._end_angle)
        root.addWidget(self._linear_group)

        # ── 正弦曲线参数子组（初始隐藏）──
        self._sine_group = QGroupBox("正弦参数")
        sf = QFormLayout(self._sine_group)
        self._sine_amplitude = QDoubleSpinBox()
        self._sine_amplitude.setRange(0.1, 135.0)
        self._sine_amplitude.setDecimals(1)
        self._sine_amplitude.setSingleStep(5.0)
        self._sine_amplitude.setSuffix(" °")
        self._sine_amplitude.setValue(45.0)
        sf.addRow("振幅：", self._sine_amplitude)

        self._sine_center = QDoubleSpinBox()
        self._sine_center.setRange(-135.0, 135.0)
        self._sine_center.setDecimals(1)
        self._sine_center.setSingleStep(5.0)
        self._sine_center.setSuffix(" °")
        self._sine_center.setValue(0.0)
        sf.addRow("中心角：", self._sine_center)

        self._sine_steps = QSpinBox()
        self._sine_steps.setRange(10, 200)
        self._sine_steps.setValue(50)
        self._sine_steps.setToolTip("越大越平滑（推荐 40–60）")
        sf.addRow("每周期点数：", self._sine_steps)
        root.addWidget(self._sine_group)
        self._sine_group.setVisible(False)

        self._on_loop_mode_changed()
        return gb

    def _on_loop_mode_changed(self) -> None:
        """模式切换：线性 ↔ 正弦。"""
        is_sine = self._loop_mode_combo.currentData() == "sine"
        self._linear_group.setVisible(not is_sine)
        self._sine_group.setVisible(is_sine)
        self._cycles_spin.setSuffix(" 个周期" if is_sine else " 次")

    def _build_control_group(self) -> QGroupBox:
        gb = QGroupBox("控制")
        v = QVBoxLayout(gb)

        row = QHBoxLayout()
        self._btn_start = QPushButton("启动循环")
        self._btn_start.setObjectName("btn_start")
        self._btn_start.setMinimumHeight(38)
        self._btn_start.clicked.connect(self._on_start_clicked)

        self._btn_stop = QPushButton("停止循环")
        self._btn_stop.setObjectName("btn_stop")
        self._btn_stop.setMinimumHeight(38)
        self._btn_stop.clicked.connect(self._on_stop_clicked)

        row.addWidget(self._btn_start, stretch=1)
        row.addWidget(self._btn_stop, stretch=1)
        v.addLayout(row)

        prog_row = QHBoxLayout()
        prog_lbl = QLabel("进度：")
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m  完整来回")
        self._progress.setTextVisible(True)
        prog_row.addWidget(prog_lbl)
        prog_row.addWidget(self._progress, stretch=1)
        v.addLayout(prog_row)

        return gb

    def _build_status_group(self) -> QGroupBox:
        gb = QGroupBox("状态")
        form = QFormLayout(gb)

        self._state_lbl = QLabel(STATE_IDLE)
        self._state_lbl.setStyleSheet("color: #c0caf5; font-weight: bold; font-size: 16px;")

        self._summary_lbl = QLabel("尚未运行")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet("color: #7dcfff;")

        form.addRow("当前状态：", self._state_lbl)
        form.addRow("摘要：", self._summary_lbl)

        return gb

    # ------------------------------------------------------------- 绑定

    def bind_controller(self, controller: Optional[BasicController]) -> None:
        """由主窗口在连接成功后调用；断开时传 None。"""
        self._controller = controller

        # 解绑旧的 runners
        for runner in (self._runner, self._sine_runner):
            if runner is None:
                continue
            try:
                runner.progress.disconnect(self._on_progress)
                runner.state_changed.disconnect(self._on_state_changed)
                runner.finished.disconnect(self._on_finished)
                runner.aborted.disconnect(self._on_aborted)
            except (TypeError, RuntimeError):
                pass
            if runner.is_running:
                runner.stop()
        self._runner = None
        self._sine_runner = None

        if controller is not None:
            # 线性 runner
            self._runner = LoopRunner(controller.client, self)
            self._runner.progress.connect(self._on_progress)
            self._runner.state_changed.connect(self._on_state_changed)
            self._runner.finished.connect(self._on_finished)
            self._runner.aborted.connect(self._on_aborted)
            # 正弦 runner
            self._sine_runner = SineRunner(controller.client, self)
            self._sine_runner.progress.connect(self._on_progress)
            self._sine_runner.state_changed.connect(self._on_state_changed)
            self._sine_runner.finished.connect(self._on_finished)
            self._sine_runner.aborted.connect(self._on_aborted)

        self.set_enabled(controller is not None)
        self._update_state_label(STATE_IDLE)
        self._summary_lbl.setText("尚未运行")

    def set_enabled(self, enabled: bool) -> None:
        running = bool(
            (self._runner and self._runner.is_running)
            or (self._sine_runner and self._sine_runner.is_running)
        )
        self._set_running_ui(enabled and not running, running)

    # ------------------------------------------------------------ 事件

    def _refresh_angle_limits(self) -> None:
        mode = self._mode_combo.currentData()
        if mode in (ServoMode.SERVO_270_CW.value, ServoMode.SERVO_270_CCW.value):
            lo, hi = -135.0, 135.0
        else:
            lo, hi = -90.0, 90.0
        for spin in (self._start_angle, self._end_angle):
            spin.setRange(lo, hi)
            v = spin.value()
            if v < lo:
                spin.setValue(lo)
            elif v > hi:
                spin.setValue(hi)

    def _on_start_clicked(self) -> None:
        _accepted, message = self.start_from_current_settings()
        self.status_message.emit(message)

    def start_from_current_settings(self) -> tuple[bool, str]:
        """使用当前 UI 参数启动，供按钮和本地 HTTP API 共用。"""
        if self._controller is None:
            return False, "[loop] 未连接，无法启动"
        is_sine = self._loop_mode_combo.currentData() == "sine"

        if is_sine:
            return self._start_sine()
        return self._start_linear()

    def _start_linear(self) -> tuple[bool, str]:
        if self._runner is None:
            return False, "[loop] 控制器未就绪"
        if self._runner.is_running:
            return False, "[loop] 线性往复已在运行"
        try:
            params = LoopParams(
                servo_id=self._id_spin.value(),
                start_angle=self._start_angle.value(),
                end_angle=self._end_angle.value(),
                period_ms=self._period_spin.value(),
                cycles=self._cycles_spin.value(),
                mode=self._mode_combo.currentData(),
            )
            params.validate()
        except ValueError as exc:
            return False, f"[loop] 参数错误：{exc}"

        total_pairs = params.cycles // 2
        self._progress.setRange(0, max(1, total_pairs))
        self._progress.setValue(0)
        self._summary_lbl.setText("运行中…")
        self._summary_lbl.setStyleSheet("color: #7dcfff;")

        if self._runner.start(params):
            self._set_running_ui(False, True)
            return True, (
                f"[loop] 启动：{params.cycles} 来回 × {params.period_ms}ms，"
                f"ID={params.servo_id}, {params.start_angle:+.1f}°→{params.end_angle:+.1f}°"
            )
        return False, "[loop] 启动失败，运动指令未成功发送"

    def _start_sine(self) -> tuple[bool, str]:
        if self._sine_runner is None:
            return False, "[sine] 控制器未就绪"
        if self._sine_runner.is_running:
            return False, "[sine] 正弦运动已在运行"
        try:
            params = SineParams(
                servo_id=self._id_spin.value(),
                amplitude=self._sine_amplitude.value(),
                center_angle=self._sine_center.value(),
                period_ms=self._period_spin.value(),
                cycles=self._cycles_spin.value(),
                mode=self._mode_combo.currentData(),
                steps=self._sine_steps.value(),
            )
            params.validate()
        except ValueError as exc:
            return False, f"[sine] 参数错误：{exc}"

        total_steps = params.steps * params.cycles if params.cycles > 0 else params.steps * 10
        self._progress.setRange(0, max(1, total_steps))
        self._progress.setValue(0)
        self._summary_lbl.setText("正弦运行中…")
        self._summary_lbl.setStyleSheet("color: #7dcfff;")

        if self._sine_runner.start(params):
            self._set_running_ui(False, True)
            return True, (
                f"[sine] 启动：振幅={params.amplitude:.1f}° "
                f"中心={params.center_angle:.1f}° "
                f"周期={params.period_ms}ms × {params.cycles}"
            )
        return False, "[sine] 启动失败，运动指令未成功发送"

    def _on_stop_clicked(self) -> None:
        stopped = False
        if self._runner is not None and self._runner.is_running:
            self._runner.stop()
            stopped = True
        if self._sine_runner is not None and self._sine_runner.is_running:
            self._sine_runner.stop()
            stopped = True
        if stopped:
            self.status_message.emit("[loop] 已请求停止")

    # ------------------------------------------------------- 运行回调

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setRange(0, max(1, total))
        self._progress.setValue(current)

    def _on_state_changed(self, state: str) -> None:
        self._update_state_label(state)
        if state == STATE_RUNNING:
            self._set_running_ui(False, True)
        else:
            self._set_running_ui(self._controller is not None, False)

    def _on_finished(self) -> None:
        # 摘要在 _on_state_changed 触达 COMPLETED 之后写入
        # 通过 runner 暴露的 _pairs_done / _total_pairs 拿统计
        runner = self._runner
        if runner is None:
            return
        # runner._pairs_done / _total_pairs 在 finish 后仍可读（finish 不清零）
        pairs = runner._pairs_done  # type: ignore[attr-defined]
        # 通过 _log 推断 elapsed 不优雅；直接读 _t0（runner 内部 monotonic）
        import time as _time
        elapsed = 0.0
        t0 = getattr(runner, "_t0", 0.0)
        if t0 > 0:
            elapsed = _time.monotonic() - t0
        text = f"{pairs} 个完整来回，{elapsed:.2f} 秒"
        self._summary_lbl.setText(text)
        self._summary_lbl.setStyleSheet("color: #9ece6a; font-weight: bold;")
        self.status_message.emit(f"[loop] {text}")

    def _on_aborted(self, reason: str) -> None:
        runner = self._runner
        pairs = runner._pairs_done if runner else 0  # type: ignore[attr-defined]
        text = f"已中止：完成 {pairs} 个完整来回（{reason}）"
        self._summary_lbl.setText(text)
        self._summary_lbl.setStyleSheet("color: #e0af68; font-weight: bold;")
        self.status_message.emit(f"[loop] {text}")

    # ----------------------------------------------------------- 辅助

    def _update_state_label(self, state: str) -> None:
        self._state_lbl.setText(state)
        if state == STATE_RUNNING:
            color = "#9ece6a"
        elif state == STATE_COMPLETED:
            color = "#7dcfff"
        elif state == STATE_ABORTED:
            color = "#e0af68"
        else:
            color = "#c0caf5"
        self._state_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 16px;")

    def _set_running_ui(self, can_edit: bool, running: bool) -> None:
        self._btn_start.setEnabled(can_edit)
        self._btn_stop.setEnabled(running)
        self._loop_mode_combo.setEnabled(can_edit)
        for w in (
            self._cycles_spin, self._period_spin,
            self._start_angle, self._end_angle,
            self._sine_amplitude, self._sine_center, self._sine_steps,
            self._id_spin, self._mode_combo,
        ):
            w.setEnabled(can_edit)

    def force_stop(self) -> None:
        """由主窗口在退出时调用，确保循环引擎停止。"""
        if self._runner is not None and self._runner.is_running:
            self._runner.stop()
        if self._sine_runner is not None and self._sine_runner.is_running:
            self._sine_runner.stop()
