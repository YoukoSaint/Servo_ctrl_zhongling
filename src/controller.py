# -*- coding: utf-8 -*-
"""
舵机业务控制层（基础控制 + 循环控制模式）。

模块职责
--------
- :class:`BasicController` —— 单舵机/单帧动作的薄封装。所有方法直接转发到
  :class:`ServoClient`（具体是 ``serial_link.ServoClient``），不维护任何业务
  状态；真实应答通过 ``ServoClient`` 的信号异步回调（``position_updated``、
  ``frame_received`` 等）。

- :class:`LoopParams` —— 循环控制模式参数。
- :class:`LoopRunner`  —— 循环控制引擎。基于 ``QTimer.singleShot`` 的**非阻塞**
  状态机，避免占用 UI 线程。

依赖：
- ``PyQt5.QtCore``（QObject / pyqtSignal / QTimer）
- 同包 ``serial_link``（ServoClient）
- 同包 ``protocol``（ServoMode、angle_to_pwm_270、angle_to_pwm_180、常量）

语义约定（已与产品确认）
----------------------
- ``LoopParams.action_ms`` 是**单程动作时间**，``dwell_ms`` 是每次到达端点后的
  停滞时间。完整一个来回 = ``2 × (action_ms + dwell_ms)``。
- ``LoopParams.cycles`` 的语义是单程次数：``cycles=1`` 表示**只走单程**
  （去不回），``cycles=2`` 表示走 1 个完整来回后停在起点，``cycles=4`` 表示
  走 2 个完整来回，依此类推。``cycles`` 为奇数（如 3、5）时，最后一次会
  停在**终点**。
- 起始角度 == 终止角度被视作零任务，直接判定为完成。
- ``action_ms = 0`` 时，调用方按协议层 ``TIME_FAST=0``（最快速度）下发。
- ``servo_id = 255``（广播）**不允许**用于循环控制：广播帧无应答会失去同步。

日志约定
--------
- 所有日志走 ``%s`` 占位符，禁用 f-string（参考 ``logger`` 模块说明）。
- 长运行操作（循环控制）必须按规范同时打 ``START`` 和 ``STOP``，且附带量化
  指标（次数 / 耗时 / 误差）。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型注解用，避免无 PyQt5 环境 import 时炸掉
    from PyQt5.QtCore import QObject, QTimer
    from .serial_link import ServoClient
else:
    # 运行时 lazy：实际使用 pyqtSignal/QObject/QTimer 的类需要 PyQt5；纯 dataclass
    # （LoopParams）和不需要这些类的脚本可以无 PyQt5 加载本模块。
    try:
        from PyQt5.QtCore import QObject, QTimer, pyqtSignal  # type: ignore
    except Exception:  # noqa: BLE001
        QObject = object  # type: ignore[assignment,misc]
        QTimer = None     # type: ignore[assignment]
        pyqtSignal = lambda *a, **kw: None  # type: ignore[assignment]
    # servo 客户端是具体业务类型，类型注解用；运行时由使用方按需导入。
    ServoClient = None  # type: ignore[assignment,misc]

from .logger import audit, get_logger
from .protocol import (
    ID_BROADCAST,
    ID_MAX,
    ID_MIN,
    PWM_MAX,
    PWM_MIN,
    TIME_FAST,
    TIME_MAX,
    ProtocolError,
    ServoMode,
    angle_to_pwm_180,
    angle_to_pwm_270,
)


log = get_logger(__name__)


# ---------------------------------------------------------------------------
#  常量：状态机字符串
# ---------------------------------------------------------------------------

STATE_IDLE = "Idle"
STATE_RUNNING = "Running"
STATE_PAUSING = "Pausing"
STATE_COMPLETED = "Completed"
STATE_ABORTED = "Aborted"


# ---------------------------------------------------------------------------
#  数据类
# ---------------------------------------------------------------------------

@dataclass
class LoopParams:
    """循环控制参数。

    Attributes:
        servo_id:    舵机 ID，0–254。**不允许** 255（广播）。
        start_angle: 起始角度（度）。合法范围由 ``mode`` 决定。
        end_angle:   终止角度（度）。合法范围由 ``mode`` 决定。
        action_ms:   单程动作时间（起点→终点 或 终点→起点），毫秒。0 表示按
                     ``TIME_FAST``（最快速度）下发。
        dwell_ms:    每次到达起点或终点后的停滞时间，毫秒。0 表示不停滞。
        cycles:      循环单程数。``cycles=1`` 表示去程，``cycles=2`` 表示一个
                     完整往返；奇数时最终停在终点。
        mode:        270° 模式 (1/2) 或 180° 模式 (3/4)。其它模式（多圈/定时）
                     不支持角度循环。
    """

    servo_id: int
    start_angle: float
    end_angle: float
    action_ms: int
    dwell_ms: int
    cycles: int
    mode: int = 1

    def validate(self) -> None:
        """校验所有参数。越界抛 :class:`ValueError`。"""
        if not (ID_MIN <= self.servo_id <= ID_MAX):
            raise ValueError(
                f"servo_id={self.servo_id} 必须在 {ID_MIN}-{ID_MAX} 范围"
                f"（{ID_BROADCAST} 广播不允许用于循环）"
            )
        if self.servo_id == ID_BROADCAST:
            raise ValueError("循环控制不允许使用广播 ID（无应答会失去同步）")
        if self.mode not in (
            ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW,
            ServoMode.SERVO_180_CW, ServoMode.SERVO_180_CCW,
        ):
            raise ValueError(
                f"mode={self.mode} 不在 1/2/3/4 范围（循环仅支持 270°/180°）"
            )
        if self.mode in (ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW):
            ang_min, ang_max = -135.0, 135.0
        else:
            ang_min, ang_max = -90.0, 90.0
        if not (ang_min <= self.start_angle <= ang_max):
            raise ValueError(
                f"start_angle={self.start_angle} 超出 {ang_min}..{ang_max}"
            )
        if not (ang_min <= self.end_angle <= ang_max):
            raise ValueError(
                f"end_angle={self.end_angle} 超出 {ang_min}..{ang_max}"
            )
        if not (0 <= self.action_ms <= TIME_MAX):
            raise ValueError(
                f"action_ms={self.action_ms} 必须在 0..{TIME_MAX} 范围"
            )
        if not (0 <= self.dwell_ms <= 60000):
            raise ValueError("dwell_ms 必须在 0..60000 范围")
        if self.cycles < 1:
            raise ValueError(f"cycles={self.cycles} 必须 >= 1（cycles=1 表示单程）")


# ---------------------------------------------------------------------------
#  基础控制（透传）
# ---------------------------------------------------------------------------

class BasicController(QObject):
    """基础控制：单条指令的薄封装。

    所有方法直接调用 :class:`ServoClient` 的同名方法，返回值与底层一致
    （``True`` = 已成功入队，``False`` = 串口未连接或写入失败）。本类不维护
    任何业务状态，不发自定义信号——读指令的应答经 ``ServoClient.position_updated``
    / ``frame_received`` 异步回调。
    """

    def __init__(self, client: ServoClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.log = get_logger(__name__ + ".Basic")

    @property
    def client(self) -> ServoClient:
        """被封装的 ServoClient。"""
        return self._client

    # ---- 写指令 -----------------------------------------------------------

    def move_to_angle(self, id: int, angle: float, time_ms: int = 1000,
                      mode: int = 1) -> bool:
        """角度运动。先做角度→PWM 转换，再下发。

        Args:
            id: 舵机 ID（0–254 或 255 广播）。
            angle: 目标角度（度）。范围由 ``mode`` 决定（270°: ±135；180°: ±90）。
            time_ms: 单程时长。
            mode: 270° 模式 (1/2) 或 180° 模式 (3/4)。

        Returns:
            底层 ``ServoClient.move`` 的返回值（``True`` = 已入队）。
        """
        try:
            if mode in (ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW):
                pwm = angle_to_pwm_270(angle)
            elif mode in (ServoMode.SERVO_180_CW, ServoMode.SERVO_180_CCW):
                pwm = angle_to_pwm_180(angle)
            else:
                self.log.error("move_to_angle: 不支持的 mode=%d", mode)
                return False
        except (ProtocolError, ValueError) as exc:
            self.log.error("move_to_angle: %s", exc)
            return False
        if not self._client.ensure_mode(id, mode):
            self.log.error("move_to_angle: 设置硬件模式失败 id=%d mode=%d", id, mode)
            return False
        return self._client.move(id, pwm, time_ms)

    def move_to_pwm(self, id: int, pwm: int, time_ms: int = 1000) -> bool:
        """直接以 PWM 目标下发。"""
        if not (PWM_MIN <= pwm <= PWM_MAX):
            self.log.error(
                "move_to_pwm: pwm=%d 超出 %d..%d", pwm, PWM_MIN, PWM_MAX,
            )
            return False
        return self._client.move(id, pwm, time_ms)

    def release(self, id: int) -> bool:
        """释放扭矩（失力，可手动拨动）。"""
        return self._client.release_torque(id)

    def recover(self, id: int) -> bool:
        """恢复扭矩。"""
        return self._client.recover_torque(id)

    def pause(self, id: int) -> bool:
        """暂停。"""
        return self._client.pause(id)

    def resume(self, id: int) -> bool:
        """继续（取消暂停）。"""
        return self._client.resume(id)

    def stop(self, id: int) -> bool:
        """停止（单舵机）。"""
        return self._client.stop(id)

    # ---- 读指令 -----------------------------------------------------------

    def query_position(self, id: int) -> bool:
        """读取当前位置 PWM。"""
        return self._client.query_position(id)

    def query_mode(self, id: int) -> bool:
        """读取工作模式。"""
        return self._client.query_mode(id)

    # ---- 群控 / 紧急 ------------------------------------------------------

    def emergency_stop_all(self) -> bool:
        """对总线广播停止指令（地址 255）。"""
        return self._client.emergency_stop_all()


# ---------------------------------------------------------------------------
#  循环控制引擎
# ---------------------------------------------------------------------------

class LoopRunner(QObject):
    """循环控制引擎（基于 ``QTimer`` 的非阻塞状态机）。

    工作流程：
        1. :meth:`start` 启动：先把舵机移动到起点。
        2. 等待 ``action_ms + dwell_ms``，即动作完成并在端点停滞。
        3. :meth:`_advance` 交替下发起点/终点 PWM，并累计已完成单程数。
        4. 达到 ``total_pairs`` 时进入 ``Completed``，发出 ``finished`` 信号。
        5. 任何时候可调用 :meth:`stop` 立即中止（会下发硬件 ``stop``）。

    状态机：
        ``Idle → Running → (Completed | Aborted)``

    注：本类不通过 QThread 阻塞运行。所有推进都由 Qt 事件循环驱动，UI 线程
    不会卡顿。
    """

    # ---- Signal -----------------------------------------------------------

    state_changed = pyqtSignal(str)        # Idle / Running / Pausing / Completed / Aborted
    progress = pyqtSignal(int, int)        # (current_pair, total_pairs)
    finished = pyqtSignal()                # 正常完成
    aborted = pyqtSignal(str)              # 中止（reason）

    def __init__(self, client: ServoClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.log = get_logger(__name__ + ".Loop")

        self._params: LoopParams | None = None
        self._state: str = STATE_IDLE
        self._step: int = 0                 # 0 = 当前在起点准备去终点；1 = 在终点准备回起点
        self._pairs_done: int = 0           # 已完成的完整来回数
        self._total_pairs: int = 0          # 目标完整来回数
        self._half_steps_done: int = 0      # 已下发的起点↔终点单程数
        self._t0: float = 0.0               # monotonic 起点
        self._advance_timer: QTimer | None = None

    # ---- 公共属性 ---------------------------------------------------------

    @property
    def state(self) -> str:
        """当前状态机状态。"""
        return self._state

    @property
    def is_running(self) -> bool:
        """是否在运行中（含 Pausing 过渡）。"""
        return self._state in (STATE_RUNNING, STATE_PAUSING)

    # ---- 公共 API ---------------------------------------------------------

    def start(self, params: LoopParams) -> bool:
        """启动一次循环控制。

        行为：
        - 若当前已在运行中，记录警告并直接返回 ``False``。
        - 若 ``start_angle == end_angle``，记日志后直接走完成路径。
        - 状态机切到 ``Running``，先把舵机送到起点，等待动作和停滞完成后
          再触发下一步。

        Args:
            params: 已填写好的循环参数。建议先调用 :meth:`LoopParams.validate`，
                    本方法内部会再校验一次以防调用方绕开。

        Returns:
            成功启动返回 ``True``；参数非法或已在运行中返回 ``False``。

        Raises:
            ValueError: ``params.validate()`` 失败时抛出。
        """
        if self.is_running:
            self.log.warning(
                "start() ignored: loop is already %s (id=%d)",
                self._state, self._params.servo_id if self._params else -1,
            )
            return False
        # 重新校验，防止调用方绕开 validate
        params.validate()
        if not self._client.ensure_mode(params.servo_id, params.mode):
            self.log.error(
                "Loop start failed: cannot set hardware mode (id=%d, mode=%d)",
                params.servo_id, params.mode,
            )
            return False

        self._params = params
        self._state = STATE_RUNNING
        self._step = 0
        self._pairs_done = 0
        self._half_steps_done = 0
        # cycles=1 -> 单程；cycles=2 -> 1 个完整来回；cycles=N -> N//2 个完整来回
        self._total_pairs = params.cycles // 2
        self._t0 = time.monotonic()

        # ---- START 日志（含量化指标） --------------------------------
        self.log.info(
            "Loop started (id=%d, start=%.2f° -> end=%.2f°, action=%dms, "
            "dwell=%dms, cycles=%d [=>%d pairs], mode=%d)",
            params.servo_id, params.start_angle, params.end_angle,
            params.action_ms, params.dwell_ms, params.cycles,
            self._total_pairs, params.mode,
        )
        audit(
            "loop_start",
            resource=f"servo:{params.servo_id}",
            id=params.servo_id, start_angle=params.start_angle,
            end_angle=params.end_angle, action_ms=params.action_ms,
            dwell_ms=params.dwell_ms,
            cycles=params.cycles, mode=params.mode,
        )
        self.state_changed.emit(self._state)
        self.progress.emit(self._pairs_done, self._total_pairs)

        # 边界：起始 == 终止（零任务）
        if params.start_angle == params.end_angle:
            self.log.warning(
                "Loop no-op: start_angle == end_angle (%.2f°)",
                params.start_angle,
            )
            self._finish()
            return True

        # 先到起点
        try:
            pwm0 = self._angle_to_pwm(params.start_angle, params.mode)
        except (ProtocolError, ValueError) as exc:
            self._abort(f"angle_to_pwm failed: {exc}")
            return False
        time_ms = _safe_time_ms(params.action_ms)
        if not self._client.move(params.servo_id, pwm0, time_ms):
            self._abort(f"move(start) failed for id={params.servo_id}")
            return False

        # 先完成到起点的动作和端点停滞，再开始第一个循环单程。
        self._schedule(self._advance, params.action_ms + params.dwell_ms)
        return True

    def stop(self) -> None:
        """立即中止循环并下发硬件 ``stop``。

        行为：
        - 把状态切到 ``Aborted``，取消尚未触发的定时器。
        - 对当前舵机下发 ``stop``（按协议层语义为 ``#PDST!``）。
        - 发出 ``aborted`` 信号（reason = "user requested stop"）。
        """
        if not self.is_running:
            self.log.debug("stop() called while not running (state=%s)", self._state)
            return
        self._cancel_timer()
        sid = self._params.servo_id if self._params else -1
        try:
            self._client.stop(sid)
        except Exception as exc:  # noqa: BLE001 - 防御性兜底
            self.log.error("stop: client.stop raised %s", exc)
        self.log.info(
            "Loop aborted by user after %d/%d pairs (id=%d)",
            self._pairs_done, self._total_pairs, sid,
        )
        audit(
            "loop_stop",
            resource=f"servo:{sid}",
            reason="user requested",
            pairs_done=self._pairs_done, total_pairs=self._total_pairs,
        )
        self._state = STATE_ABORTED
        self.state_changed.emit(self._state)
        self.aborted.emit("user requested stop")
        # 释放 params，允许下一次 start()
        self._params = None

    # ---- 内部：定时器推进 -------------------------------------------------

    def _schedule(self, callback: Any, delay_ms: int) -> None:
        """在指定延迟后执行下一程或完成回调。"""
        self._cancel_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.start(max(0, int(delay_ms)))
        self._advance_timer = timer

    def _cancel_timer(self) -> None:
        """取消尚未触发的 advance 定时器。"""
        if self._advance_timer is not None:
            try:
                self._advance_timer.stop()
                self._advance_timer.deleteLater()
            except Exception:  # noqa: BLE001
                pass
            self._advance_timer = None

    def _advance(self) -> None:
        """定时器回调：下发下一步的 PWM。"""
        self._advance_timer = None
        if self._state != STATE_RUNNING:
            # 兜底：旧定时器在 Pausing / Completed / Aborted 下被残触发
            return
        if self._params is None:
            return

        p = self._params
        target_angle = p.end_angle if self._step == 0 else p.start_angle
        try:
            pwm = self._angle_to_pwm(target_angle, p.mode)
        except (ProtocolError, ValueError) as exc:
            self._abort(f"angle_to_pwm failed: {exc}")
            return
        time_ms = _safe_time_ms(p.action_ms)

        ok = self._client.move(p.servo_id, pwm, time_ms)
        if not ok:
            self._abort(
                f"move failed at step={self._step}, target={target_angle:.2f}°"
            )
            return

        # 半步切换：step 0 -> 1 时刚发出"去终点"；step 1 -> 0 时刚发出"回起点"。
        self._half_steps_done += 1
        self._step ^= 1
        if self._step == 0:
            self._pairs_done += 1
            self.progress.emit(self._pairs_done, self._total_pairs)

        delay_ms = p.action_ms + p.dwell_ms
        if self._half_steps_done >= p.cycles:
            # 最后一程也必须等动作和端点停滞都完成后才标记 Completed。
            self._schedule(self._finish, delay_ms)
        else:
            self._schedule(self._advance, delay_ms)

    # ---- 内部：完成 / 中止 ------------------------------------------------

    def _finish(self) -> None:
        """完成路径：打 STOP 日志，emit ``finished``。"""
        self._cancel_timer()
        if self._params is None:
            return
        elapsed = time.monotonic() - self._t0
        # 包含启动时先移动到起点，以及每次到达端点后的停滞。
        expected = (
            (self._half_steps_done + 1)
            * (self._params.action_ms + self._params.dwell_ms)
            / 1000.0
        )
        drift = ((elapsed - expected) / expected * 100.0) if expected > 0 else 0.0

        # ---- STOP 日志（含量化指标） ---------------------------------
        self.log.info(
            "Loop completed: %d/%d pairs in %.2fs "
            "(target=%.2fs, drift=%+.1f%%, id=%d, action=%dms, dwell=%dms)",
            self._pairs_done, self._total_pairs,
            elapsed, expected, drift,
            self._params.servo_id, self._params.action_ms, self._params.dwell_ms,
        )
        audit(
            "loop_completed",
            resource=f"servo:{self._params.servo_id}",
            pairs_done=self._pairs_done, total_pairs=self._total_pairs,
            elapsed_s=round(elapsed, 2), target_s=round(expected, 2),
            drift_pct=round(drift, 1),
        )
        self._state = STATE_COMPLETED
        self.state_changed.emit(self._state)
        self.finished.emit()
        self._params = None

    def _abort(self, reason: str) -> None:
        """中止路径：尝试下发硬件 ``stop``，打日志，emit ``aborted``。"""
        self._cancel_timer()
        if self._params is not None:
            try:
                self._client.stop(self._params.servo_id)
            except Exception as exc:  # noqa: BLE001
                self.log.error("abort: client.stop raised %s", exc)
            sid = self._params.servo_id
        else:
            sid = -1
        self.log.warning(
            "Loop aborted (%s) after %d/%d pairs (id=%d)",
            reason, self._pairs_done, self._total_pairs, sid,
        )
        audit(
            "loop_aborted",
            resource=f"servo:{sid}" if sid >= 0 else "-",
            reason=reason,
            pairs_done=self._pairs_done, total_pairs=self._total_pairs,
        )
        self._state = STATE_ABORTED
        self.state_changed.emit(self._state)
        self.aborted.emit(reason)
        self._params = None

    # ---- 内部：工具 -------------------------------------------------------

    @staticmethod
    def _angle_to_pwm(angle: float, mode: int) -> int:
        """按 ``mode`` 选 270° 或 180° 转换器。"""
        if mode in (ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW):
            return angle_to_pwm_270(angle)
        if mode in (ServoMode.SERVO_180_CW, ServoMode.SERVO_180_CCW):
            return angle_to_pwm_180(angle)
        raise ProtocolError(f"mode={mode} 不在 1/2/3/4 范围")



# ---------------------------------------------------------------------------
#  正弦曲线控制
# ---------------------------------------------------------------------------


def _safe_time_ms(action_ms: int) -> int:
    """把动作时间限制到协议层允许的 [0, TIME_MAX] 范围。"""
    if action_ms < TIME_FAST:
        return TIME_FAST
    if action_ms > TIME_MAX:
        return TIME_MAX
    return int(action_ms)

@dataclass
class SineParams:
    """正弦曲线参数。

    Attributes:
        servo_id:     舵机 ID，0–254。
        amplitude:    振幅（度）。摆幅从中心向两侧的最大角度。
        center_angle: 中心角（度）。正弦波的中位。
        period_ms:    一个完整正弦周期的时间（毫秒）。如 2000ms = 2 秒一个周期。
        cycles:       循环次数。cycles=0 表示无限循环（直到手动停止）。
        mode:         270° 模式 (1/2) 或 180° 模式 (3/4)。
        steps:        每周期插值点数（默认 50）。越大越平滑，但总线负担越重。
    """

    servo_id: int
    amplitude: float
    center_angle: float
    period_ms: int
    cycles: int = 0          # 0 = 无限
    mode: int = 1
    steps: int = 50

    def validate(self) -> None:
        """校验所有参数。越界抛 :class:`ValueError`。"""
        if not (ID_MIN <= self.servo_id <= ID_MAX):
            raise ValueError(
                f"servo_id={self.servo_id} 必须在 {ID_MIN}-{ID_MAX} 范围"
            )
        if self.mode not in (
            ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW,
            ServoMode.SERVO_180_CW, ServoMode.SERVO_180_CCW,
        ):
            raise ValueError(f"mode={self.mode} 不在 1/2/3/4 范围")
        ang_max = 135.0 if self.mode in (1, 2) else 90.0
        if self.amplitude <= 0:
            raise ValueError(f"amplitude={self.amplitude} 必须 > 0")
        if self.amplitude > ang_max:
            raise ValueError(f"amplitude={self.amplitude} 超出 {ang_max}°")
        low = self.center_angle - self.amplitude
        high = self.center_angle + self.amplitude
        if low < -ang_max or high > ang_max:
            raise ValueError(
                f"center={self.center_angle}° ± amplitude={self.amplitude}° "
                f"超出 ±{ang_max}° 范围"
            )
        if self.period_ms < 100:
            raise ValueError(f"period_ms={self.period_ms} 必须 >= 100ms")
        if self.steps < 10:
            raise ValueError(f"steps={self.steps} 必须 >= 10")


class SineRunner(QObject):
    """正弦曲线控制引擎（基于 QTimer 的非阻塞状态机）。

    算法：
        1. 把 period_ms 按 steps 切分为 step_interval_ms = period_ms // steps。
        2. 每个 step 计算 angle = center + amplitude * sin(2π * step / steps)。
        3. 下发 move 指令，目标为该角度，运动时间 = step_interval_ms。
        4. 通过 QTimer.singleShot 在 step_interval_ms 后触发下一步。
    """

    state_changed = pyqtSignal(str)
    progress = pyqtSignal(int, int)    # (current_step, total_steps)
    finished = pyqtSignal()
    aborted = pyqtSignal(str)

    def __init__(self, client: ServoClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.log = get_logger(__name__ + ".Sine")

        self._params: SineParams | None = None
        self._state: str = STATE_IDLE
        self._step: int = 0
        self._total_steps: int = 0
        self._t0: float = 0.0
        self._advance_timer: QTimer | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == STATE_RUNNING

    # ---- 启动 -----------------------------------------------------------

    def start(self, params: SineParams) -> bool:
        if self.is_running:
            self.log.warning("start() ignored: already running")
            return False
        params.validate()
        if not self._client.ensure_mode(params.servo_id, params.mode):
            self.log.error(
                "Sine start failed: cannot set hardware mode (id=%d, mode=%d)",
                params.servo_id, params.mode,
            )
            return False
        self._params = params
        self._state = STATE_RUNNING
        self._step = 0
        self._total_steps = params.steps * params.cycles if params.cycles > 0 else -1
        self._t0 = time.monotonic()

        step_ms = max(params.period_ms // params.steps, 20)  # Qt timer 在 Windows ≈16ms，20ms 保底
        self.log.info(
            "Sine started (id=%d, amp=%.1f°, center=%.1f°, period=%dms, "
            "cycles=%d, steps=%d -> %dms/step)",
            params.servo_id, params.amplitude, params.center_angle,
            params.period_ms, params.cycles, params.steps, step_ms,
        )
        audit(
            "sine_start",
            resource=f"servo:{params.servo_id}",
            id=params.servo_id,
            amplitude=params.amplitude, center=params.center_angle,
            period_ms=params.period_ms, cycles=params.cycles,
            steps=params.steps,
        )
        self.state_changed.emit(self._state)

        # 持久 QTimer，不再反复取消+新建
        self._advance_timer = QTimer(self)
        self._advance_timer.setInterval(max(10, step_ms))
        self._advance_timer.timeout.connect(self._advance)
        self._advance_timer.start()
        # 立刻执行第一步
        self._advance()
        return True

    # ---- 停止 -----------------------------------------------------------

    def stop(self) -> None:
        if not self.is_running:
            return
        self._cancel_timer()
        sid = self._params.servo_id if self._params else -1
        self.log.info("Sine aborted by user (id=%d, step=%d)", sid, self._step)
        audit("sine_stop", resource=f"servo:{sid}", reason="user", step=self._step)
        self._state = STATE_ABORTED
        self.state_changed.emit(self._state)
        self.aborted.emit("user requested stop")
        self._params = None

    # ---- 内部 -----------------------------------------------------------

    def _advance(self) -> None:
        if self._state != STATE_RUNNING or self._params is None:
            # 兜底：状态已变（如被 stop），停掉 timer
            self._cancel_timer()
            return
        p = self._params

        # 检查是否到达终点（非无限模式）
        if p.cycles > 0 and self._step >= self._total_steps:
            self._cancel_timer()
            self._finish()
            return

        # 计算正弦角度并发送
        phase = (self._step % p.steps) / p.steps * 2.0 * math.pi
        angle = p.center_angle + p.amplitude * math.sin(phase)
        try:
            pwm = self._angle_to_pwm(angle, p.mode)
        except (ProtocolError, ValueError) as exc:
            self._cancel_timer()
            self._abort(f"angle_to_pwm failed: {exc}")
            return
        step_ms = max(p.period_ms // p.steps, 10)
        time_ms = _safe_time_ms(step_ms)
        frame = f"#{p.servo_id:03d}P{pwm:04d}T{time_ms:04d}!"
        ok = self._client.send_raw(frame)
        if not ok:
            self._cancel_timer()
            self._abort(f"send failed at step={self._step}")
            return

        self._step += 1
        if self._step % 10 == 0:
            self.progress.emit(self._step, self._total_steps)

    def _finish(self) -> None:
        if self._params is None:
            return
        elapsed = time.monotonic() - self._t0
        expected = self._total_steps * (self._params.period_ms // self._params.steps) / 1000.0
        drift = ((elapsed - expected) / expected * 100.0) if expected > 0 else 0.0
        self.log.info(
            "Sine completed: %d steps in %.2fs (target=%.2fs, drift=%+.1f%%)",
            self._step, elapsed, expected, drift,
        )
        audit(
            "sine_completed",
            resource=f"servo:{self._params.servo_id}",
            steps=self._step, elapsed_s=elapsed, target_s=expected,
        )
        self._state = STATE_COMPLETED
        self.state_changed.emit(self._state)
        self.finished.emit()
        self._params = None

    def _abort(self, reason: str) -> None:
        sid = self._params.servo_id if self._params else -1
        self.log.warning("Sine aborted (%s) at step=%d (id=%d)", reason, self._step, sid)
        audit("sine_aborted", resource=f"servo:{sid}" if sid >= 0 else "-",
              reason=reason, step=self._step)
        self._state = STATE_ABORTED
        self.state_changed.emit(self._state)
        self.aborted.emit(reason)
        self._params = None

    def _cancel_timer(self) -> None:
        if self._advance_timer is not None:
            try:
                self._advance_timer.stop()
                self._advance_timer.timeout.disconnect(self._advance)
            except (TypeError, RuntimeError):
                pass
            self._advance_timer.deleteLater()
            self._advance_timer = None

    @staticmethod
    def _angle_to_pwm(angle: float, mode: int) -> int:
        if mode in (ServoMode.SERVO_270_CW, ServoMode.SERVO_270_CCW):
            return angle_to_pwm_270(angle)
        if mode in (ServoMode.SERVO_180_CW, ServoMode.SERVO_180_CCW):
            return angle_to_pwm_180(angle)
        raise ProtocolError(f"mode={mode} 不在 1/2/3/4 范围")


__all__ = [
    "LoopParams",
    "LoopRunner",
    "SineParams",
    "SineRunner",
    "BasicController",
    "STATE_IDLE",
    "STATE_RUNNING",
    "STATE_PAUSING",
    "STATE_COMPLETED",
    "STATE_ABORTED",
]
