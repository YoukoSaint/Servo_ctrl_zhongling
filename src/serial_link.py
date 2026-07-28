# -*- coding: utf-8 -*-
"""
舵机串口通信层 — 带完整诊断日志。

本模块对 UI / 业务层屏蔽底层串口与协议细节，向上提供两层抽象：

1. ``ServoTransport`` —— 物理/虚拟传输层抽象基类
   - ``SerialTransport``：基于 ``pyserial`` 的真实串口实现。
   - ``MockTransport``：无硬件自测用，用 ``QTimer.singleShot`` 模拟总线延迟，
     按已知的众灵协议格式合成应答帧入队。

2. ``ServoClient(QObject)`` —— 业务级客户端
   - 用 :class:`FrameParser` 处理粘包/群控。
   - 暴露 ``pyqtSignal`` 上报 ``frame_sent`` / ``frame_received`` /
     ``position_updated`` / ``error`` / ``connected`` / ``disconnected``。
   - 每个动作方法（``move`` / ``release_torque`` / ``query_*`` / ...）**不等应答**，
     立即返回 ``bool`` 表示是否成功发出；真实结果通过 signal 异步回调。
   - **维护 TX/RX 计数器与最近一次通讯时间戳**，便于诊断。

诊断日志布局
------------
所有通讯事件落到 4 个不同 logger：

================  ==================================================
Logger             内容
================  ==================================================
``servo``          主日志：连接/断开/错误/HEALTH 报告
``servo.protocol`` 协议层：TX 完整帧 + RX 字节级 HEX 转储 + 解析出的完整帧
``servo.audit``    审计：每条用户操作一行 ``action=... resource=...``
``servo.health``   周期：每 60s 一行 ``HEALTH | ...``
================  ==================================================

典型故障定位流程
----------------
1. 看到 UI 上"无应答"：
   - 打开 ``*_protocol.log`` → 找到 ``TX #000P1500T1000!`` 之后是否有 ``RX``
   - 没有任何 ``RX`` 字节 → 总线不通：检查串口线、波特率、供电
   - 有 ``RX`` 字节但没有解析出 ``#…!`` 完整帧 → 协议格式不匹配（手册里协议描述或兼容性问题）
   - 有 ``RX`` 完整帧但 ``parse_response`` 拒识 → 看 protocol.py 的 ``parse_response`` 规则
2. 看 ``*_health.log``：TX 计数 ≥ RX 计数且差值持续增大 → 真的丢应答

依赖：
- ``PyQt5.QtCore``（QObject / pyqtSignal / QTimer）
- ``pyserial``（仅 ``SerialTransport`` 需要）
- 同包 ``protocol``（``FrameParser``、``parse_response``、``cmd_*``）
- 同包 ``logger``（LOGGER_PROTOCOL / LOGGER_AUDIT / LOGGER_HEALTH / audit / health / hex_dump）
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Deque, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from .logger import LOGGER_AUDIT, LOGGER_HEALTH, LOGGER_PROTOCOL, audit, health, hex_dump
from .protocol import (
    FrameParser,
    ID_BROADCAST,
    ParsedResponse,
    cmd_move,
    cmd_pause,
    cmd_read_id,
    cmd_read_mode,
    cmd_read_position,
    cmd_read_version,
    cmd_recover_torque,
    cmd_release_torque,
    cmd_resume,
    cmd_set_mode,
    cmd_stop,
    parse_response,
)

# 协议流量 logger
_PROTO_LOG = logging.getLogger(LOGGER_PROTOCOL)
_AUDIT_LOG = logging.getLogger(LOGGER_AUDIT)
_HEALTH_LOG = logging.getLogger(LOGGER_HEALTH)
_LOG = logging.getLogger("servo.serial")


# 健康报告周期：60 秒（按规范短任务不发；长运行才打 HEALTH）
HEALTH_INTERVAL_S = 60


# ---------------------------------------------------------------------------
#  传输层抽象
# ---------------------------------------------------------------------------

class ServoTransport(ABC):
    """物理/虚拟串口传输层抽象基类。

    实现必须暴露阻塞/非阻塞读接口；写接口是文本帧（带 ``\\r\\n`` 终止符）。
    """

    @abstractmethod
    def open(self) -> bool:
        """打开底层端口。失败抛异常或返回 ``False``。"""

    @abstractmethod
    def close(self) -> None:
        """关闭底层端口。"""

    @abstractmethod
    def is_open(self) -> bool:
        """当前是否处于已打开状态。"""

    @abstractmethod
    def send(self, frame: str) -> None:
        """发送一帧文本（实现内部负责追加 ``\\r\\n``）。"""

    @abstractmethod
    def bytes_available(self) -> int:
        """待读取的字节数（非阻塞）。"""

    @abstractmethod
    def read_all(self) -> bytes:
        """读走所有待处理字节；无数据返回 ``b""``。"""

    @staticmethod
    @abstractmethod
    def available_ports() -> list[str]:
        """返回当前可用串口名列表。"""


# ---------------------------------------------------------------------------
#  真实实现
# ---------------------------------------------------------------------------

class SerialTransport(ServoTransport):
    """基于 ``pyserial`` 的真实串口实现。"""

    def __init__(self, port: str, baudrate: int, timeout: float = 0.05):
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._ser = None  # type: ignore[assignment]

    # ---- 生命周期 -------------------------------------------------------

    def open(self) -> bool:
        # 延迟导入：mock 路径下不需要 pyserial
        import serial  # type: ignore[import-untyped]

        _LOG.info("Opening serial port %s @ %d baud (timeout=%.3fs)",
                  self._port, self._baudrate, self._timeout)
        try:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=self._timeout,
            )
        except Exception as exc:
            _LOG.error("Failed to open %s: %s", self._port, exc)
            raise
        ok = self._ser.is_open
        _LOG.info("Serial port %s opened successfully: is_open=%s",
                  self._port, ok)
        return ok

    def close(self) -> None:
        if self._ser is not None and self._ser.is_open:
            name = self._ser.port
            self._ser.close()
            _LOG.info("Serial port %s closed", name)
        else:
            _LOG.debug("close() called but port not open")

    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    # ---- 数据 IO --------------------------------------------------------

    def send(self, frame: str) -> None:
        if self._ser is None or not self._ser.is_open:
            raise RuntimeError("SerialTransport not open")
        raw = (frame + "\r\n").encode("ascii")
        n = self._ser.write(raw)
        _PROTO_LOG.info("TX %d bytes: %s", n, frame)
        _PROTO_LOG.debug("TX HEX:\n%s", hex_dump(raw))

    def bytes_available(self) -> int:
        if self._ser is None or not self._ser.is_open:
            return 0
        return int(self._ser.in_waiting)

    def read_all(self) -> bytes:
        if self._ser is None or not self._ser.is_open:
            return b""
        chunks: list[bytes] = []
        # 1. 读 in_waiting 报告的字节
        n = self._ser.in_waiting
        if n > 0:
            chunks.append(self._ser.read(n))
        # 2. 非阻塞兜底：timeout=0 的 read(1)，立即返回不卡事件循环
        #    应对 CH340 等驱动 in_waiting 滞后的问题
        orig = self._ser.timeout
        try:
            self._ser.timeout = 0       # 非阻塞
            b = self._ser.read(1)
            if b:
                chunks.append(b)
                more = self._ser.in_waiting
                if more > 0:
                    chunks.append(self._ser.read(more))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._ser.timeout = orig    # 恢复
        data = b"".join(chunks)
        if data:
            _PROTO_LOG.info("RX %d bytes", len(data))
            _PROTO_LOG.debug("RX HEX:\n%s", hex_dump(data))
        return data

    # ---- 静态工具 -------------------------------------------------------

    @staticmethod
    def available_ports() -> list[str]:
        from serial.tools import list_ports  # type: ignore[import-untyped]
        return [p.device for p in list_ports.comports()]


# ---------------------------------------------------------------------------
#  Mock 实现
# ---------------------------------------------------------------------------

# 写命令 echo 与读取应答的最小语法识别正则
_RE_WRITE_MOVE = re.compile(r"^#(\d{3})P(\d{4})T(\d{4})!$")
_RE_READ_PRAD = re.compile(r"^#(\d{3})PRAD!$")
_RE_READ_PMOD = re.compile(r"^#(\d{3})PMOD!$")
_RE_READ_PVER = re.compile(r"^#(\d{3})PVER!$")
_RE_READ_PID = re.compile(r"^#000PID!$")
_RE_SET_PMOD = re.compile(r"^#(\d{3})PMOD([1-8])!$")
_RE_SET_PBD = re.compile(r"^#(\d{3})PBD([0-7])!$")
_RE_SET_PID = re.compile(r"^#(\d{3})PID(\d{3})!$")
_RE_GENERIC_WRITE = re.compile(
    r"^#\d{3}P(?:ULK|ULR|DPT|DCT|DST|SCK|CL|CLS|CLSO|CSD\d{4})!$"
)
_RE_ANY_WRITE = re.compile(r"^#\d{3}P[A-Z0-9]+!$")


class MockTransport(ServoTransport):
    """无硬件自测用传输层。

    - 维护一个 ``collections.deque`` 作为接收队列。
    - ``send(frame)`` 时按协议规则合成应答（读取类用结构化帧，写命令用 echo 或 ``#OK!``），
      并通过 ``QTimer.singleShot(latency_ms, ...)`` 异步入队。
    - 群控帧 / 广播帧：广播无应答，群控按内部指令逐一回 echo。
    """

    MOCK_PORTS = ["MOCK-COM1", "MOCK-COM2", "MOCK-COM3"]

    def __init__(self, latency_ms: int = 5):
        super().__init__()
        self._latency_ms = int(latency_ms)
        self._rx: Deque[bytes] = deque()
        self._open = False
        self._current_id: int = 1

    # ---- 生命周期 -------------------------------------------------------

    def open(self) -> bool:
        _LOG.info("Opening MOCK transport (latency=%dms)", self._latency_ms)
        self._open = True
        _LOG.info("MOCK transport opened")
        return True

    def close(self) -> None:
        if self._open:
            _LOG.info("MOCK transport closed")
        self._open = False
        self._rx.clear()

    def is_open(self) -> bool:
        return self._open

    # ---- 数据 IO --------------------------------------------------------

    def send(self, frame: str) -> None:
        if not self._open:
            raise RuntimeError("MockTransport not open")
        raw = (frame + "\r\n").encode("ascii")
        _PROTO_LOG.info("TX %d bytes: %s", len(raw), frame)
        _PROTO_LOG.debug("TX HEX:\n%s", hex_dump(raw))
        for piece in self._dispatch(frame):
            self._enqueue_delayed(piece)

    def bytes_available(self) -> int:
        return sum(len(b) for b in self._rx)

    def read_all(self) -> bytes:
        if not self._rx:
            return b""
        chunks = list(self._rx)
        self._rx.clear()
        data = b"".join(chunks)
        _PROTO_LOG.info("RX %d bytes", len(data))
        _PROTO_LOG.debug("RX HEX:\n%s", hex_dump(data))
        return data

    # ---- 静态工具 -------------------------------------------------------

    @staticmethod
    def available_ports() -> list[str]:
        return list(MockTransport.MOCK_PORTS)

    # ---- 内部：帧解析 + 应答合成 ---------------------------------------

    def _enqueue_delayed(self, frame: str) -> None:
        payload = (frame + "\r\n").encode("ascii")
        QTimer.singleShot(self._latency_ms, lambda p=payload: self._rx.append(p))

    def _dispatch(self, frame: str) -> list[str]:
        # 1) 群控帧
        if frame.startswith("{G"):
            inner = frame[2:-1] if frame.endswith("}") else frame[2:]
            out: list[str] = []
            for sub in self._split_simple(inner):
                out.extend(self._dispatch(sub))
            return out

        # 2) 广播写指令：255 视为广播，无应答
        m_255 = re.match(r"^#(\d{3})", frame)
        if m_255 and int(m_255.group(1)) == ID_BROADCAST:
            _PROTO_LOG.debug("MOCK: broadcast frame %s -> no reply", frame)
            return []

        # 3) 读取类（对齐 ZL-ZServo v2.x 固件实际行为）
        m = _RE_READ_PRAD.match(frame)
        if m:
            rid = int(m.group(1))
            return [f"#{rid:03d}P1500!"]
        # PMOD 读在 v2.x 固件不支持，静默无应答
        m = _RE_READ_PMOD.match(frame)
        if m:
            return []
        m = _RE_READ_PVER.match(frame)
        if m:
            # v2.x 实际格式: #000@ ZL-ZServo_AD_CBM V2.1.16STG!
            return [f"#000@ ZL-ZServo_AD_CBM V2.1.16STG!"]
        if _RE_READ_PID.match(frame):
            return [f"#{self._current_id:03d}P!"]

        # 4) 设置类
        m = _RE_SET_PMOD.match(frame)
        if m:
            return []  # v2.x 静默执行
        m = _RE_SET_PBD.match(frame)
        if m:
            return ["#OK!"]
        m = _RE_SET_PID.match(frame)
        if m:
            new_id = int(m.group(2))
            self._current_id = new_id
            return [f"#{new_id:03d}P!"]

        # 5) 写命令（v2.x 固件全部静默，无应答）
        if _RE_WRITE_MOVE.match(frame):
            return []  # 静默执行
        # PULK/PULR/PDPT/PDCT/PDST — 静默
        if _RE_GENERIC_WRITE.match(frame):
            return []
        if _RE_ANY_WRITE.match(frame):
            return []

        _PROTO_LOG.warning("MOCK: unrecognized frame, ignoring: %s", frame)
        return []

    @staticmethod
    def _split_simple(text: str) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == "#":
                end = text.find("!", i)
                if end < 0:
                    break
                out.append(text[i: end + 1])
                i = end + 1
            else:
                i += 1
        return out


# ---------------------------------------------------------------------------
#  业务级客户端（带完整诊断）
# ---------------------------------------------------------------------------

class ServoClient(QObject):
    """对 UI 屏蔽协议细节的高层客户端，带完整诊断能力。

    状态变量（均只读）：
        - ``tx_count``        累计成功发送帧数
        - ``rx_byte_count``   累计接收字节数
        - ``rx_frame_count``  累计接收完整帧数
        - ``error_count``     累计错误数（open/send/read 失败）
        - ``last_tx_ts``      最近一次发送时间戳（monotonic）
        - ``last_rx_ts``      最近一次收到任何字节的时间戳
        - ``last_rx_frame_ts`` 最近一次收到完整帧的时间戳
        - ``start_ts``        连接打开时间戳
        - ``port_name``       当前串口名
        - ``baudrate``        当前波特率
    """

    # ---- 信号 -----------------------------------------------------------

    frame_sent = pyqtSignal(str)             # 发送的完整帧
    frame_received = pyqtSignal(str)         # 收到的完整帧
    position_updated = pyqtSignal(int, int)  # (id, pwm)
    error = pyqtSignal(str)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    health_updated = pyqtSignal(str)         # HEALTH 报告文本

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._transport: Optional[ServoTransport] = None
        self._parser = FrameParser()
        self._reader: Optional[QTimer] = None
        self._health_timer: Optional[QTimer] = None
        self._poll_interval_ms = 20
        # 当前连接期间由本应用确认过的硬件模式。断线后清空，避免设备重启后
        # 继续沿用失效缓存。
        self._configured_modes: dict[int, int] = {}

        # 诊断计数器
        self.tx_count = 0
        self.rx_byte_count = 0
        self.rx_frame_count = 0
        self.error_count = 0
        self.last_tx_ts: float = 0.0
        self.last_rx_ts: float = 0.0
        self.last_rx_frame_ts: float = 0.0
        self.start_ts: float = 0.0
        self.port_name: str = "-"
        self.baudrate: int = 0
        self.transport_kind: str = "-"  # "serial" / "mock"

    # ---- 传输接入 -------------------------------------------------------

    def connect_transport(self, transport: ServoTransport, *,
                          port_name: str = "?", baudrate: int = 0) -> bool:
        """绑定一个 ``ServoTransport`` 并尝试打开。

        ``port_name`` / ``baudrate`` 仅用于诊断显示（实际参数已在 transport 内）。
        """
        if transport is None:
            self.error.emit("transport is None")
            audit("connect", resource=port_name, baudrate=baudrate, result="FAIL",
                  reason="transport is None")
            return False
        self._transport = transport
        self.port_name = port_name
        self.baudrate = baudrate
        self.transport_kind = type(transport).__name__

        _LOG.info("Connecting transport: port=%s baud=%d kind=%s",
                  port_name, baudrate, self.transport_kind)
        try:
            ok = transport.open()
        except Exception as exc:  # noqa: BLE001
            self.error_count += 1
            err = f"open failed: {exc}"
            self.error.emit(err)
            _LOG.error("Transport open failed: %s", exc)
            audit("connect", resource=port_name, baudrate=baudrate, result="FAIL",
                  reason=str(exc))
            self._transport = None
            return False
        if not ok:
            self.error_count += 1
            self.error.emit("open failed")
            audit("connect", resource=port_name, baudrate=baudrate, result="FAIL",
                  reason="open() returned False")
            self._transport = None
            return False

        # 重置计数器
        self.tx_count = 0
        self.rx_byte_count = 0
        self.rx_frame_count = 0
        self.error_count = 0
        self.start_ts = time.monotonic()
        self.last_rx_ts = 0.0
        self.last_rx_frame_ts = 0.0
        self.last_tx_ts = 0.0
        self._configured_modes.clear()

        self.connected.emit()
        _LOG.info("Transport connected: port=%s baud=%d kind=%s",
                  port_name, baudrate, self.transport_kind)
        audit("connect", resource=port_name, baudrate=baudrate,
              kind=self.transport_kind, result="OK")

        # 启动 HEALTH 周期
        self._start_health_timer()
        return True

    def disconnect(self) -> None:
        if self._transport is None and self._reader is None:
            return
        self._stop_health_timer()
        self.stop_reader()
        # 最后一条 HEALTH
        self._emit_health(reason="disconnect")
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("close() raised: %s", exc)
            self._transport = None
        self._configured_modes.clear()
        self.disconnected.emit()
        _LOG.info("Disconnected from %s @ %d", self.port_name, self.baudrate)
        audit("disconnect", resource=self.port_name,
              tx=self.tx_count, rx_frames=self.rx_frame_count, rx_bytes=self.rx_byte_count,
              errors=self.error_count)

    # ---- 轮询读取 -------------------------------------------------------

    def start_reader(self) -> None:
        """启动 20ms 轮询的接收解析循环。重复调用幂等。"""
        if self._reader is not None:
            return
        self._reader = QTimer(self)
        self._reader.setInterval(self._poll_interval_ms)
        self._reader.timeout.connect(self._poll_once)
        self._reader.start()
        _LOG.debug("Reader started (interval=%dms)", self._poll_interval_ms)

    def stop_reader(self) -> None:
        if self._reader is not None:
            self._reader.stop()
            self._reader.deleteLater()
            self._reader = None
            _LOG.debug("Reader stopped")

    def _poll_once(self) -> None:
        if self._transport is None:
            return
        try:
            chunk = self._transport.read_all()
        except Exception as exc:  # noqa: BLE001
            self.error_count += 1
            self.error.emit(f"read error: {exc}")
            _LOG.error("read error: %s", exc)
            return
        if not chunk:
            return
        self.rx_byte_count += len(chunk)
        self.last_rx_ts = time.monotonic()
        for frame in self._parser.feed(chunk):
            self.rx_frame_count += 1
            self.last_rx_frame_ts = time.monotonic()
            _PROTO_LOG.info("RX frame: %s", frame)
            self._handle_frame(frame)

    def _handle_frame(self, frame: str) -> None:
        self.frame_received.emit(frame)
        parsed: Optional[ParsedResponse] = parse_response(frame)
        if parsed is None:
            _PROTO_LOG.warning("RX unrecognized frame: %s", frame)
            return
        if parsed.kind == "pwm" and parsed.pwm is not None:
            _PROTO_LOG.info("RX parsed: id=%d pwm=%d", parsed.id, parsed.pwm)
            self.position_updated.emit(parsed.id, parsed.pwm)
        elif parsed.kind == "ok":
            _PROTO_LOG.info("RX parsed: #OK!")
        elif parsed.kind == "mode" and parsed.mode is not None:
            _PROTO_LOG.info("RX parsed: id=%d mode=%d", parsed.id, parsed.mode)
        elif parsed.kind == "ver" and parsed.version is not None:
            _PROTO_LOG.info("RX parsed: id=%d version=%s", parsed.id, parsed.version)
        elif parsed.kind == "id_only":
            _PROTO_LOG.info("RX parsed: id=%d (id-only)", parsed.id)
        elif parsed.kind == "baud" and parsed.baud is not None:
            _PROTO_LOG.info("RX parsed: id=%d baud=%d", parsed.id, parsed.baud)

    # ---- HEALTH 报告 ----------------------------------------------------

    def _start_health_timer(self) -> None:
        if self._health_timer is not None:
            return
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(HEALTH_INTERVAL_S * 1000)
        self._health_timer.timeout.connect(lambda: self._emit_health(reason="periodic"))
        self._health_timer.start()
        _LOG.debug("HEALTH timer started (interval=%ds)", HEALTH_INTERVAL_S)

    def _stop_health_timer(self) -> None:
        if self._health_timer is not None:
            self._health_timer.stop()
            self._health_timer.deleteLater()
            self._health_timer = None

    def _emit_health(self, reason: str = "periodic") -> None:
        now = time.monotonic()
        uptime = (now - self.start_ts) if self.start_ts else 0.0
        since_tx = (now - self.last_tx_ts) if self.last_tx_ts else -1.0
        since_rx_frame = (now - self.last_rx_frame_ts) if self.last_rx_frame_ts else -1.0
        line = (
            f"reason={reason} port={self.port_name} baud={self.baudrate} "
            f"kind={self.transport_kind} "
            f"uptime={uptime:.1f}s "
            f"tx={self.tx_count} rx_bytes={self.rx_byte_count} "
            f"rx_frames={self.rx_frame_count} errors={self.error_count} "
            f"since_last_tx={since_tx:.1f}s since_last_rx_frame={since_rx_frame:.1f}s"
        )
        _HEALTH_LOG.info("HEALTH | %s", line)
        _LOG.info("HEALTH | %s", line)
        self.health_updated.emit(line)
        # logger.health() 同写入 health log（保证 audit/health 都在 self-contained 的 log 文件里）
        try:
            health(line)
        except Exception:  # noqa: BLE001
            pass

    # ---- 业务指令（写） --------------------------------------------------

    def _write(self, frame: str, *, action: str, resource: str, **audit_fields) -> bool:
        if self._transport is None or not self._transport.is_open():
            self.error_count += 1
            err = "transport not connected"
            self.error.emit(err)
            _LOG.warning("%s dropped: %s", action, err)
            audit(action, resource=resource, result="FAIL", reason=err, **audit_fields)
            return False
        try:
            self._transport.send(frame)
        except Exception as exc:  # noqa: BLE001
            self.error_count += 1
            err = f"send failed: {exc}"
            self.error.emit(err)
            _LOG.error("%s send failed: %s", action, exc)
            audit(action, resource=resource, result="FAIL", reason=str(exc), **audit_fields)
            return False
        self.tx_count += 1
        self.last_tx_ts = time.monotonic()
        self.frame_sent.emit(frame)
        audit(action, resource=resource, frame=frame, result="OK", **audit_fields)
        return True

    def move(self, id_: int, pwm: int, time_ms: int) -> bool:
        return self._write(cmd_move(id_, pwm, time_ms),
                           action="move", resource=f"servo:{id_}",
                           id=id_, pwm=pwm, time_ms=time_ms)

    def release_torque(self, id_: int) -> bool:
        return self._write(cmd_release_torque(id_),
                           action="release_torque", resource=f"servo:{id_}",
                           id=id_)

    def recover_torque(self, id_: int) -> bool:
        return self._write(cmd_recover_torque(id_),
                           action="recover_torque", resource=f"servo:{id_}",
                           id=id_)

    def pause(self, id_: int) -> bool:
        return self._write(cmd_pause(id_),
                           action="pause", resource=f"servo:{id_}", id=id_)

    def resume(self, id_: int) -> bool:
        return self._write(cmd_resume(id_),
                           action="resume", resource=f"servo:{id_}", id=id_)

    def stop(self, id_: int) -> bool:
        return self._write(cmd_stop(id_),
                           action="stop", resource=f"servo:{id_}", id=id_)

    def query_position(self, id_: int) -> bool:
        return self._write(cmd_read_position(id_),
                           action="query_position", resource=f"servo:{id_}", id=id_)

    def query_mode(self, id_: int) -> bool:
        return self._write(cmd_read_mode(id_),
                           action="query_mode", resource=f"servo:{id_}", id=id_)

    def query_version(self, id_: int) -> bool:
        return self._write(cmd_read_version(id_),
                           action="query_version", resource=f"servo:{id_}", id=id_)

    def query_id(self) -> bool:
        return self._write(cmd_read_id(),
                           action="query_id", resource="servo:any")

    def set_mode(self, id_: int, mode: int) -> bool:
        ok = self._write(cmd_set_mode(id_, mode),
                         action="set_mode", resource=f"servo:{id_}",
                         id=id_, mode=mode)
        if ok:
            self._configured_modes[id_] = int(mode)
        return ok

    def ensure_mode(self, id_: int, mode: int) -> bool:
        """确保硬件模式与 UI 选择一致；同一连接中相同设置只发送一次。"""
        if self._configured_modes.get(id_) == int(mode):
            return True
        return self.set_mode(id_, mode)

    def send_raw(self, frame: str) -> bool:
        """高频发送通道：只写串口不打 audit，专供 SineRunner 等高频步进。

        与 ``_write`` 的区别：不写审计日志、不 emit frame_sent 信号，
        仅做 transport 检查 + 发送 + TX 计数 + 协议日志。
        """
        if self._transport is None or not self._transport.is_open():
            self.error_count += 1
            return False
        try:
            self._transport.send(frame)
        except Exception:  # noqa: BLE001
            self.error_count += 1
            return False
        self.tx_count += 1
        self.last_tx_ts = time.monotonic()
        self.frame_sent.emit(frame)
        return True

    def emergency_stop_all(self) -> bool:
        return self._write(f"#{ID_BROADCAST:03d}PDST!",
                           action="emergency_stop_all",
                           resource="broadcast:255",
                           note="broadcast PDST (no reply)")


__all__ = [
    "ServoTransport",
    "SerialTransport",
    "MockTransport",
    "ServoClient",
    "HEALTH_INTERVAL_S",
]
