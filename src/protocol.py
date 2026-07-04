# -*- coding: utf-8 -*-
"""
众灵舵机通信协议 — 文本编解码层。

详见同目录 protocol.md。

约定：
- 所有数字字段宽度固定，左侧补 0。
- 写入指令以 `#` 开头、`!` 结尾。
- 读取应答根据指令不同格式略有差异。
- 群控帧用 `{G ... }` 包裹多个独立指令。
- 255 = 广播地址，**仅对写指令有效**。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

FRAME_HEAD = "#"
FRAME_TAIL = "!"
GROUP_PREFIX = "{G"
GROUP_SUFFIX = "}"

ID_MIN = 0
ID_MAX = 254
ID_BROADCAST = 255

PWM_MIN = 500
PWM_MAX = 2500
PWM_CENTER = 1500

TIME_MIN = 0
TIME_MAX = 9999
TIME_FAST = 0  # 最快速度

BAUDRATES = {
    0: 9600,
    1: 19200,
    2: 38400,
    3: 57600,
    4: 115200,
    5: 128000,
    6: 256000,
    7: 1000000,
}


class ServoMode(IntEnum):
    """舵机工作模式 1–8。"""

    SERVO_270_CW = 1      # 270° 顺时针
    SERVO_270_CCW = 2     # 270° 逆时针
    SERVO_180_CW = 3      # 180° 顺时针
    SERVO_180_CCW = 4     # 180° 逆时针
    TURNS_360_CW = 5      # 360° 多圈 顺时针
    TURNS_360_CCW = 6     # 360° 多圈 逆时针
    TIMED_360_CW = 7      # 360° 定时 顺时针
    TIMED_360_CCW = 8     # 360° 定时 逆时针


# ---------------------------------------------------------------------------
#  异常
# ---------------------------------------------------------------------------

class ProtocolError(ValueError):
    """协议层参数错误。"""


# ---------------------------------------------------------------------------
#  数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServoCmd:
    """单条舵机指令。"""

    id: int
    pwm: int
    time_ms: int
    is_broadcast: bool = False

    def __post_init__(self):
        if self.is_broadcast:
            if self.id != ID_BROADCAST:
                raise ProtocolError(f"广播帧 id 必须为 {ID_BROADCAST}")
        else:
            if not (ID_MIN <= self.id <= ID_MAX):
                raise ProtocolError(f"id={self.id} 超出 0–254 范围")
        if not (PWM_MIN <= self.pwm <= PWM_MAX):
            raise ProtocolError(f"pwm={self.pwm} 超出 {PWM_MIN}–{PWM_MAX} 范围")
        if not (TIME_MIN <= self.time_ms <= TIME_MAX):
            raise ProtocolError(f"time_ms={self.time_ms} 超出 {TIME_MIN}–{TIME_MAX} 范围")

    def encode(self) -> str:
        """编码为单条指令字符串。"""
        return f"{FRAME_HEAD}{self.id:03d}P{self.pwm:04d}T{self.time_ms:04d}{FRAME_TAIL}"


@dataclass(frozen=True)
class ParsedResponse:
    """对舵机应答的解析结果。"""

    id: int
    raw: str
    kind: str             # 'ok' | 'pwm' | 'mode' | 'ver' | 'id_only' | 'baud'
    pwm: int | None = None
    mode: int | None = None
    version: str | None = None
    baud: int | None = None

    def __repr__(self) -> str:
        return f"ParsedResponse({self.raw!r}, kind={self.kind!r})"


# ---------------------------------------------------------------------------
#  编码器
# ---------------------------------------------------------------------------

def cmd_move(id_: int, pwm: int, time_ms: int) -> str:
    """运动指令：`#000P1500T1000!`。"""
    return ServoCmd(id_, pwm, time_ms).encode()


def cmd_release_torque(id_: int) -> str:
    """释放扭矩：`#000PULK!`（失力，可手动拨动）。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PULK{FRAME_TAIL}"


def cmd_recover_torque(id_: int) -> str:
    """恢复扭矩：`#000PULR!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PULR{FRAME_TAIL}"


def cmd_read_version(id_: int) -> str:
    """读取固件版本：`#000PVER!`。"""
    _check_id_readable(id_)
    return f"{FRAME_HEAD}{id_:03d}PVER{FRAME_TAIL}"


def cmd_read_id() -> str:
    """读取 ID：`#000PID!`（固定 ID 0 询问）。"""
    return f"{FRAME_HEAD}000PID{FRAME_TAIL}"


def cmd_set_id(current_id: int, new_id: int) -> str:
    """修改 ID：`#000PID001!`（current=0 → new=1）。

    注意：手册未定义对非 0 ID 的修改规则；这里通用化为对任意当前 ID 的写入。
    """
    if not (ID_MIN <= current_id <= ID_MAX):
        raise ProtocolError(f"current_id={current_id} 超出 0–254 范围")
    if not (ID_MIN <= new_id <= ID_MAX):
        raise ProtocolError(f"new_id={new_id} 超出 0–254 范围（不能改为 255）")
    return f"{FRAME_HEAD}{current_id:03d}PID{new_id:03d}{FRAME_TAIL}"


def cmd_read_position(id_: int) -> str:
    """读取当前位置 PWM：`#000PRAD!`。"""
    _check_id_readable(id_)
    return f"{FRAME_HEAD}{id_:03d}PRAD{FRAME_TAIL}"


def cmd_read_mode(id_: int) -> str:
    """读取工作模式：`#000PMOD!`。"""
    _check_id_readable(id_)
    return f"{FRAME_HEAD}{id_:03d}PMOD{FRAME_TAIL}"


def cmd_set_mode(id_: int, mode: int) -> str:
    """设置工作模式：`#000PMOD1!`。"""
    _check_id_writable(id_)
    if mode not in ServoMode._value2member_map_:
        raise ProtocolError(f"mode={mode} 不在 1–8 范围")
    return f"{FRAME_HEAD}{id_:03d}PMOD{mode}{FRAME_TAIL}"


def cmd_pause(id_: int) -> str:
    """暂停：`#000PDPT!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PDPT{FRAME_TAIL}"


def cmd_resume(id_: int) -> str:
    """继续：`#000PDCT!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PDCT{FRAME_TAIL}"


def cmd_stop(id_: int) -> str:
    """停止：`#000PDST!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PDST{FRAME_TAIL}"


def cmd_set_baudrate(id_: int, code: int) -> str:
    """设置波特率：`#000PBD4!` (code 0–7)。"""
    _check_id_writable(id_)
    if code not in BAUDRATES:
        raise ProtocolError(f"baud_code={code} 必须在 0–7 范围")
    return f"{FRAME_HEAD}{id_:03d}PBD{code}{FRAME_TAIL}"


def cmd_calibrate_center(id_: int) -> str:
    """校准当前位置为中位：`#000PSCK!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PSCK{FRAME_TAIL}"


def cmd_set_center(id_: int, pwm: int) -> str:
    """设置自定义中位：`#000PCSD1500!` (pwm 0500–2500)。"""
    _check_id_writable(id_)
    if not (PWM_MIN <= pwm <= PWM_MAX):
        raise ProtocolError(f"中位 pwm={pwm} 超出 {PWM_MIN}–{PWM_MAX}")
    return f"{FRAME_HEAD}{id_:03d}PCSD{pwm:04d}{FRAME_TAIL}"


def cmd_reset_partial(id_: int) -> str:
    """部分复位：`#000PCLEO!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PCLEO{FRAME_TAIL}"


def cmd_reset_full(id_: int) -> str:
    """全部复位：`#000PCLE!`。"""
    _check_id_writable(id_)
    return f"{FRAME_HEAD}{id_:03d}PCLE{FRAME_TAIL}"


def encode_group(cmds: Iterable[str]) -> str:
    """把多条指令打包为群控帧：`{G#000P...!#001P...!}`。"""
    parts = list(cmds)
    if not parts:
        raise ProtocolError("群控帧至少含 1 条指令")
    return f"{GROUP_PREFIX}{''.join(parts)}{GROUP_SUFFIX}"


# ---------------------------------------------------------------------------
#  解码器
# ---------------------------------------------------------------------------

class FrameParser:
    """流式帧解析器：处理粘包与群控。

    用法：
        parser = FrameParser()
        for chunk in serial.iter_bytes():
            for frame in parser.feed(chunk):
                handle(frame)
    """

    def __init__(self):
        self._buf = ""

    def feed(self, chunk: str | bytes) -> list[str]:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("ascii", errors="replace")
        self._buf += chunk
        out: list[str] = []
        while True:
            # 优先匹配群控帧
            if self._buf.startswith(GROUP_PREFIX):
                end = self._buf.find(GROUP_SUFFIX)
                if end < 0:
                    break
                # 群控内可能有多条 #…! 指令，按 `!` 切分后剥离开头的 `{G`
                inner = self._buf[len(GROUP_PREFIX): end]
                self._buf = self._buf[end + len(GROUP_SUFFIX):]
                for piece in self._extract_simple_frames(inner):
                    out.append(piece)
                continue
            if self._buf.startswith(FRAME_HEAD):
                end = self._buf.find(FRAME_TAIL)
                if end < 0:
                    break
                frame = self._buf[: end + 1]
                self._buf = self._buf[end + 1:]
                out.append(frame)
                continue
            # 跳过垃圾字符
            if not self._buf:
                break
            if self._buf[0] in ("{", "G", "}"):
                self._buf = self._buf[1:]
                continue
            # 其它未知字符：丢弃一字节
            self._buf = self._buf[1:]
        return out

    def _extract_simple_frames(self, text: str) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == FRAME_HEAD:
                end = text.find(FRAME_TAIL, i)
                if end < 0:
                    break
                out.append(text[i: end + 1])
                i = end + 1
            else:
                i += 1
        return out


def parse_response(frame: str) -> ParsedResponse | None:
    """把单条应答帧解析成结构化对象。无法识别返回 None。"""
    if not frame.startswith(FRAME_HEAD) or not frame.endswith(FRAME_TAIL):
        return None
    body = frame[1:-1]
    # 特殊情况：`#OK!` 是总线级成功应答（不带 ID），长度为 2
    if body == "OK":
        return ParsedResponse(id=-1, raw=frame, kind="ok")
    if len(body) < 4:
        return None
    try:
        rid = int(body[:3])
    except ValueError:
        return None
    rest = body[3:]

    # `#000P!` — ID-only
    if rest == "P":
        return ParsedResponse(id=rid, raw=frame, kind="id_only")
    # `#000PULK!` 之类的写命令 echo 视为 ok
    if rest in ("PULK", "PULR", "PDPT", "PDCT", "PDST", "PSCK", "PCLE", "PCLEO"):
        return ParsedResponse(id=rid, raw=frame, kind="ok")
    # `#000Pxxxx!` — PWM
    if rest.startswith("P") and rest[1:].isdigit() and len(rest) == 5:
        return ParsedResponse(id=rid, raw=frame, kind="pwm", pwm=int(rest[1:]))
    # `#000PMODn!`
    if rest.startswith("PMOD") and rest[4:].isdigit():
        return ParsedResponse(id=rid, raw=frame, kind="mode", mode=int(rest[4:]))
    # `#000@ <model> Vx.x.x!` — ZL-ZServo_AD_CBM 固件 v2.1.x 实际格式
    if rest.startswith("@"):
        return ParsedResponse(id=rid, raw=frame, kind="ver", version=rest[1:].strip())
    # `#000PVx.xx!` — 旧版固件格式（手册描述）
    if rest.startswith("PV"):
        return ParsedResponse(id=rid, raw=frame, kind="ver", version=rest[2:])
    # `#000PBDn!` 或 `#000PBDxxxxx!`
    if rest.startswith("PBD"):
        tail = rest[3:]
        if tail.isdigit():
            if tail in {str(c) for c in BAUDRATES}:
                return ParsedResponse(
                    id=rid, raw=frame, kind="baud", baud=BAUDRATES[int(tail)]
                )
            return ParsedResponse(id=rid, raw=frame, kind="baud", baud=int(tail))
    return None


# ---------------------------------------------------------------------------
#  工具：角度 ↔ PWM
# ---------------------------------------------------------------------------

def angle_to_pwm_270(angle_deg: float) -> int:
    """270° 模式：angle=-135..+135 → PWM 500..2500。"""
    if not -135.0 <= angle_deg <= 135.0:
        raise ProtocolError(f"angle={angle_deg} 超出 270° 模式 ±135° 范围")
    return int(round(1500 + angle_deg / 135.0 * 1000))


def pwm_to_angle_270(pwm: int) -> float:
    """270° 模式：PWM 500..2500 → angle=-135..+135。"""
    return (pwm - 1500) / 1000.0 * 135.0


def angle_to_pwm_180(angle_deg: float) -> int:
    """180° 模式：angle=-90..+90 → PWM 500..2500。"""
    if not -90.0 <= angle_deg <= 90.0:
        raise ProtocolError(f"angle={angle_deg} 超出 180° 模式 ±90° 范围")
    return int(round(1500 + angle_deg / 90.0 * 1000))


def pwm_to_angle_180(pwm: int) -> float:
    """180° 模式：PWM 500..2500 → angle=-90..+90。"""
    return (pwm - 1500) / 1000.0 * 90.0


# ---------------------------------------------------------------------------
#  内部
# ---------------------------------------------------------------------------

def _check_id_writable(id_: int) -> None:
    if not (ID_MIN <= id_ <= ID_MAX) and id_ != ID_BROADCAST:
        raise ProtocolError(f"id={id_} 超出 0–254 范围（255 仅限广播）")


def _check_id_readable(id_: int) -> None:
    if not (ID_MIN <= id_ <= ID_MAX):
        raise ProtocolError(f"读取 id={id_} 必须在 0–254 范围（无广播读取）")
