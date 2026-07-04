#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口冒烟测试脚本 —— 在终端里直接跟舵机对话。

两种入口：
  A) CLI 单条命令：
       python scripts/serial_smoke.py --port COM8 move --id 1 --pwm 1500 --time 1000
       python scripts/serial_smoke.py --port COM8 read-position --id 1
       python scripts/serial_smoke.py --port COM8 read-id
       python scripts/serial_smoke.py --port COM8 probe           # 探测总线上有哪些 ID
       python scripts/serial_smoke.py --port COM8 sweep           # 扫描 0..254 找在线的 ID

  B) 交互 REPL：
       python scripts/serial_smoke.py --port COM8
       servo> help
       servo> move 1 1500 1000
       servo> read 1
       servo> scan
       servo> quit

所有 TX / RX 字节都打 stdout 实时显示；可直接看到通讯是否成功。

**这是 UI 上线前的最低验证层**。先跑通它，再做 GUI。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# 让脚本可作为独立入口运行（不依赖 src 包安装）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serial  # type: ignore[import-untyped]

from src.protocol import (
    BAUDRATES,
    ID_MAX,
    ID_MIN,
    PWM_MAX,
    PWM_MIN,
    TIME_MAX,
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


# ---------------------------------------------------------------------------
#  串口封装（最小、纯阻塞、便于诊断）
# ---------------------------------------------------------------------------

class Link:
    """最小串口封装：open / send / recv_some / close。

    设计原则：
    - 每次 ``send()`` 后立即 ``recv_all(timeout)``，等够指定时长后**必定**返回；
    - 收集到的所有原始字节打印到 stdout，便于肉眼对照协议；
    - ``recv_all`` 在超时前会**累积**所有已收到的字节并尝试解析若干完整帧。
    """

    def __init__(self, port: str, baudrate: int, timeout_read: float = 0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout_read = timeout_read
        self.ser: serial.Serial | None = None
        self.tx_count = 0
        self.rx_byte_count = 0
        self.rx_frame_count = 0

    def open(self) -> None:
        print(f"[link] open {self.port} @ {self.baudrate} (timeout={self.timeout_read}s)")
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_read,
        )
        print(f"[link] opened: is_open={self.ser.is_open}")

    def close(self) -> None:
        if self.ser is not None and self.ser.is_open:
            name = self.ser.port
            self.ser.close()
            print(f"[link] closed {name}")
        self.ser = None

    def _assert_open(self) -> serial.Serial:
        if self.ser is None or not self.ser.is_open:
            raise RuntimeError(f"link not open: port={self.port}")
        return self.ser

    def send(self, frame: str) -> None:
        s = self._assert_open()
        raw = (frame + "\r\n").encode("ascii")
        n = s.write(raw)
        s.flush()
        self.tx_count += 1
        print(f"[TX] {n:>3} bytes  {frame!r}")
        print(f"     HEX  {' '.join(f'{b:02X}' for b in raw)}")

    def recv_until_idle(self, idle_s: float = 0.10, max_s: float = 0.50) -> list[str]:
        """等总线空闲（``idle_s`` 内无新字节），最长 ``max_s``，返回解析到的帧列表。

        用 FrameParser 处理粘包。
        """
        from src.protocol import FrameParser
        s = self._assert_open()
        parser = FrameParser()
        frames: list[str] = []
        deadline = time.monotonic() + max_s
        last_rx = time.monotonic()
        while time.monotonic() < deadline:
            waiting = s.in_waiting
            if waiting > 0:
                chunk = s.read(waiting)
                self.rx_byte_count += len(chunk)
                last_rx = time.monotonic()
                # 字节级显示（短串）
                preview = chunk[:60]
                hex_str = " ".join(f"{b:02X}" for b in preview)
                if len(chunk) > len(preview):
                    hex_str += f" ... (+{len(chunk) - len(preview)} bytes)"
                print(f"[RX] {len(chunk):>3} bytes  HEX {hex_str}")
                for f in parser.feed(chunk):
                    self.rx_frame_count += 1
                    frames.append(f)
                    print(f"     FRAME {f!r}")
            else:
                # 已 ``idle_s`` 静默，认为这一轮通讯结束
                if frames and (time.monotonic() - last_rx) > idle_s:
                    break
                # 没收到任何字节也允许 max_s 上限
                time.sleep(self.timeout_read)
        return frames

    def roundtrip(self, frame: str, idle_s: float = 0.10, max_s: float = 0.50) -> list[str]:
        """发一帧、等回应。返回解析后的原始帧列表。"""
        self.send(frame)
        return self.recv_until_idle(idle_s=idle_s, max_s=max_s)


# ---------------------------------------------------------------------------
#  高层动作（带成功/失败判定）
# ---------------------------------------------------------------------------

def _ok(frames: list[str]) -> bool:
    """判定读指令/配置指令应答是否视为成功。

    注意：众灵 ZL-ZServo v2.x 固件中，运动/扭矩类**写指令**（move/release/recover/
    pause/resume/stop）**静默执行，无任何应答**。因此 write 类函数不通过 _ok 判定，
    只要 roundtrip 正常完成（无异常）即视为成功。
    """
    for f in frames:
        parsed = parse_response(f)
        if parsed is not None and parsed.kind in ("ok", "pwm", "mode", "ver", "id_only", "baud"):
            return True
    return False


def _ok_sent(n_sent: int, expected: int = 1) -> bool:
    """写指令成功与否只看是否发出去了（TX 计数 >= 预期）。"""
    return n_sent >= expected


def action_move(link: Link, id_: int, pwm: int, time_ms: int) -> bool:
    if not (ID_MIN <= id_ <= ID_MAX):
        print(f"[ERR] id {id_} not in 0..{ID_MAX}")
        return False
    if not (PWM_MIN <= pwm <= PWM_MAX):
        print(f"[ERR] pwm {pwm} not in {PWM_MIN}..{PWM_MAX}")
        return False
    if not (0 <= time_ms <= TIME_MAX):
        print(f"[ERR] time {time_ms} not in 0..{TIME_MAX}")
        return False
    tx_before = link.tx_count
    link.roundtrip(cmd_move(id_, pwm, time_ms))
    # ZL-ZServo v2.x 写指令静默执行，只看是否发出
    return _ok_sent(link.tx_count, tx_before + 1)


def action_read_position(link: Link, id_: int) -> bool:
    if not (ID_MIN <= id_ <= ID_MAX):
        print(f"[ERR] id {id_} not in 0..{ID_MAX}")
        return False
    frames = link.roundtrip(cmd_read_position(id_))
    return _ok(frames)


def action_read_mode(link: Link, id_: int) -> bool:
    if not (ID_MIN <= id_ <= ID_MAX):
        print(f"[ERR] id {id_} not in 0..{ID_MAX}")
        return False
    frames = link.roundtrip(cmd_read_mode(id_))
    # ZL-ZServo v2.x 不支持 PMOD 读 → 此命令预期无应答，不算失败
    if not frames:
        print("[info] 该固件不支持读取工作模式（v2.x 已知限制）")
    return _ok(frames)


def action_read_version(link: Link, id_: int) -> bool:
    if not (ID_MIN <= id_ <= ID_MAX):
        print(f"[ERR] id {id_} not in 0..{ID_MAX}")
        return False
    frames = link.roundtrip(cmd_read_version(id_))
    return _ok(frames)


def action_read_id(link: Link) -> bool:
    """读 ID：用地址 0 发 ``#000PID!``。"""
    frames = link.roundtrip(cmd_read_id())
    return _ok(frames)


def action_release(link: Link, id_: int) -> bool:
    tx_before = link.tx_count
    link.roundtrip(cmd_release_torque(id_))
    # 写指令静默
    return _ok_sent(link.tx_count, tx_before + 1)


def action_recover(link: Link, id_: int) -> bool:
    tx_before = link.tx_count
    link.roundtrip(cmd_recover_torque(id_))
    return _ok_sent(link.tx_count, tx_before + 1)


def action_pause(link: Link, id_: int) -> bool:
    tx_before = link.tx_count
    link.roundtrip(cmd_pause(id_))
    return _ok_sent(link.tx_count, tx_before + 1)


def action_resume(link: Link, id_: int) -> bool:
    tx_before = link.tx_count
    link.roundtrip(cmd_resume(id_))
    return _ok_sent(link.tx_count, tx_before + 1)


def action_stop(link: Link, id_: int) -> bool:
    tx_before = link.tx_count
    link.roundtrip(cmd_stop(id_))
    return _ok_sent(link.tx_count, tx_before + 1)


def action_set_mode(link: Link, id_: int, mode: int) -> bool:
    if mode not in (1, 2, 3, 4, 5, 6, 7, 8):
        print(f"[ERR] mode {mode} not in 1..8")
        return False
    tx_before = link.tx_count
    link.roundtrip(cmd_set_mode(id_, mode))
    return _ok_sent(link.tx_count, tx_before + 1)


def action_probe(link: Link, id_: int) -> bool:
    """对单 ID 做健康检查：读位置 + 读 ID + 读版本。

    注意：PMOD 读在此固件 (ZL-ZServo v2.x) 不支持，已用 PID 替代。
    """
    print(f"[probe] id={id_}")
    ok_pos = action_read_position(link, id_)
    ok_id = action_read_id(link)
    ok_ver = action_read_version(link, id_)
    print(f"[probe] id={id_} pos={'OK' if ok_pos else 'FAIL'} "
          f"pid={'OK' if ok_id else 'FAIL'} "
          f"ver={'OK' if ok_ver else 'FAIL'}")
    return ok_pos and ok_id and ok_ver


def action_scan(link: Link, start: int = 0, end: int = ID_MAX,
                timeout_per: float = 0.20) -> list[int]:
    """扫描 0..end 找在总线上有响应的 ID。"""
    print(f"[scan] 0..{end}, timeout_per={timeout_per}s")
    found: list[int] = []
    for id_ in range(start, end + 1):
        link.send(cmd_read_position(id_))
        frames = link.recv_until_idle(idle_s=0.08, max_s=timeout_per)
        if _ok(frames):
            print(f"[scan]   id={id_:>3}  -> {frames}")
            found.append(id_)
        else:
            print(f"[scan]   id={id_:>3}  no reply")
    print(f"[scan] done, found {len(found)}: {found}")
    return found


# ---------------------------------------------------------------------------
#  交互 REPL
# ---------------------------------------------------------------------------

HELP_TEXT = """\
交互命令（不区分大小写）：

  open <port> [baud]              打开串口（默认 115200）
  close                           关闭串口
  status                          报告当前 TX/RX 计数、串口状态
  move <id> <pwm> <time_ms>       运动到目标 PWM
  read <id>                       读当前位置
  mode <id>                       读工作模式
  version <id>                    读固件版本
  whoami                          用 #000PID 询问总线 ID
  release <id>                    释放扭矩
  recover <id>                    恢复扭矩
  pause <id>                      暂停
  resume <id>                     继续
  stop <id>                       停止
  setmode <id> <1..8>             设置工作模式
  probe <id>                      读位置+模式+版本
  scan [start] [end]              扫描 0..end（默认 0..254）
  help                            显示本帮助
  quit / exit                     退出

示例：
  servo> open COM8
  servo> read 1
  servo> move 1 1500 1000
  servo> scan
"""


def repl(link: Link, initial_port: str | None, initial_baud: int) -> int:
    print("=== 舵机串口冒烟测试 REPL ===")
    print("输入 'help' 查看命令，'quit' 退出。")
    if initial_port:
        try:
            link.baudrate = initial_baud
            link.open()
        except Exception as exc:
            print(f"[ERR] 打开失败: {exc}")
            return 2

    while True:
        try:
            line = input("servo> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(HELP_TEXT)
            elif cmd == "open":
                if len(parts) < 2:
                    print("[ERR] 用法: open <port> [baud]")
                    continue
                link.port = parts[1]
                link.baudrate = int(parts[2]) if len(parts) > 2 else 115200
                if link.ser is not None and link.ser.is_open:
                    link.close()
                link.open()
            elif cmd == "close":
                link.close()
            elif cmd == "status":
                open_ = link.ser is not None and link.ser.is_open
                print(f"port={link.port} baud={link.baudrate} open={open_} "
                      f"tx={link.tx_count} rx_bytes={link.rx_byte_count} "
                      f"rx_frames={link.rx_frame_count}")
            elif cmd == "move":
                if len(parts) != 4:
                    print("[ERR] 用法: move <id> <pwm> <time_ms>")
                    continue
                ok = action_move(link, int(parts[1]), int(parts[2]), int(parts[3]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "read":
                if len(parts) != 2:
                    print("[ERR] 用法: read <id>")
                    continue
                ok = action_read_position(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "mode":
                if len(parts) != 2:
                    print("[ERR] 用法: mode <id>")
                    continue
                ok = action_read_mode(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "version":
                if len(parts) != 2:
                    print("[ERR] 用法: version <id>")
                    continue
                ok = action_read_version(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "whoami":
                ok = action_read_id(link)
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "release":
                ok = action_release(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "recover":
                ok = action_recover(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "pause":
                ok = action_pause(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "resume":
                ok = action_resume(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "stop":
                ok = action_stop(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "setmode":
                if len(parts) != 3:
                    print("[ERR] 用法: setmode <id> <1..8>")
                    continue
                ok = action_set_mode(link, int(parts[1]), int(parts[2]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "probe":
                ok = action_probe(link, int(parts[1]))
                print(f"[result] {'OK' if ok else 'FAIL'}")
            elif cmd == "scan":
                start = int(parts[1]) if len(parts) > 1 else 0
                end = int(parts[2]) if len(parts) > 2 else ID_MAX
                found = action_scan(link, start=start, end=end)
                print(f"[result] found {len(found)}: {found}")
            else:
                print(f"[ERR] 未知命令: {cmd}（输入 help 查看）")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] 异常: {type(exc).__name__}: {exc}")

    link.close()
    print("bye.")
    return 0


# ---------------------------------------------------------------------------
#  CLI 入口
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="serial_smoke",
        description="舵机串口冒烟测试（CLI + 交互 REPL）",
    )
    p.add_argument("--port", help="串口名（如 COM8）")
    p.add_argument("--baud", type=int, default=115200,
                   help=f"波特率（默认 115200；可选 {sorted(set(BAUDRATES.values()))}）")
    p.add_argument("--timeout", type=float, default=0.05,
                   help="单次 read 超时（秒，默认 0.05）")
    p.add_argument("--idle", type=float, default=0.10,
                   help="判定一帧应答结束的静默时长（秒，默认 0.10）")
    p.add_argument("--max-wait", type=float, default=0.50,
                   help="一次 roundtrip 最长等多久（秒，默认 0.50）")
    sub = p.add_subparsers(dest="cmd")

    def add_id(p_):
        p_.add_argument("--id", type=int, required=True, help="舵机 ID (0..254)")

    sp = sub.add_parser("move", help="运动到目标 PWM")
    add_id(sp)
    sp.add_argument("--pwm", type=int, required=True, help=f"目标 PWM ({PWM_MIN}..{PWM_MAX})")
    sp.add_argument("--time", type=int, required=True, help=f"单程时间 ms (0..{TIME_MAX})")

    sp = sub.add_parser("read-position", help="读当前位置 PWM")
    add_id(sp)

    sp = sub.add_parser("read-mode", help="读工作模式")
    add_id(sp)

    sp = sub.add_parser("read-version", help="读固件版本")
    add_id(sp)

    sub.add_parser("read-id", help="用 #000PID 询问总线 ID")

    sp = sub.add_parser("release", help="释放扭矩")
    add_id(sp)
    sp = sub.add_parser("recover", help="恢复扭矩")
    add_id(sp)
    sp = sub.add_parser("pause", help="暂停")
    add_id(sp)
    sp = sub.add_parser("resume", help="继续")
    add_id(sp)
    sp = sub.add_parser("stop", help="停止")
    add_id(sp)

    sp = sub.add_parser("set-mode", help="设置工作模式 (1..8)")
    add_id(sp)
    sp.add_argument("--mode", type=int, required=True, help="1..8")

    sp = sub.add_parser("probe", help="读位置+模式+版本")
    add_id(sp)

    sp = sub.add_parser("scan", help="扫描 0..end 找在线 ID")
    sp.add_argument("--start", type=int, default=0, help="起始 ID（默认 0）")
    sp.add_argument("--end", type=int, default=ID_MAX, help=f"终止 ID（默认 {ID_MAX}）")

    sub.add_parser("repl", help="强制进入交互 REPL（默认无子命令也是 REPL）")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    # 无 --port：给清晰提示
    if not args.port and not args.cmd:
        print("ERROR: 至少需要 --port 打开串口，或用 --help 查看用法。", file=sys.stderr)
        print("  示例: python scripts/serial_smoke.py --port COM8", file=sys.stderr)
        return 2

    link = Link(args.port or "?", args.baud, timeout_read=args.timeout)
    # 让 roundtrip 用 cli 参数控制
    global _LINK_IDLE, _LINK_MAX
    _LINK_IDLE = args.idle
    _LINK_MAX = args.max_wait

    # 单条 CLI 命令
    if args.cmd and args.cmd != "repl":
        try:
            link.open()
        except Exception as exc:
            print(f"ERROR: 打开 {args.port} 失败: {exc}", file=sys.stderr)
            print("\n排查步骤：", file=sys.stderr)
            print("  1) 确认 USB 转 TTL 串口线已插入，设备管理器里能看到 COM8", file=sys.stderr)
            print("  2) 确认没有别的程序（如串口助手）占用 COM8", file=sys.stderr)
            print("  3) 确认线序：TX 接舵机 RX、RX 接舵机 TX、GND 接 GND", file=sys.stderr)
            print("  4) 确认波特率与舵机一致（默认 115200）", file=sys.stderr)
            print("  5) 确认舵机已上电（手册要求 5-8.4V）", file=sys.stderr)
            return 2
        try:
            ok = _run_cli(link, args)
            return 0 if ok else 1
        finally:
            link.close()

    # 交互 REPL
    return repl(link, args.port, args.baud)


# 在 Link.roundtrip 上 hook CLI 的 idle/max
_LINK_IDLE = 0.10
_LINK_MAX = 0.50


def _patched_roundtrip(self, frame: str) -> list[str]:
    self.send(frame)
    return self.recv_until_idle(idle_s=_LINK_IDLE, max_s=_LINK_MAX)


Link.roundtrip = _patched_roundtrip  # type: ignore[assignment]


def _run_cli(link: Link, args: argparse.Namespace) -> bool:
    c = args.cmd
    if c == "move":
        return action_move(link, args.id, args.pwm, args.time)
    if c == "read-position":
        return action_read_position(link, args.id)
    if c == "read-mode":
        return action_read_mode(link, args.id)
    if c == "read-version":
        return action_read_version(link, args.id)
    if c == "read-id":
        return action_read_id(link)
    if c == "release":
        return action_release(link, args.id)
    if c == "recover":
        return action_recover(link, args.id)
    if c == "pause":
        return action_pause(link, args.id)
    if c == "resume":
        return action_resume(link, args.id)
    if c == "stop":
        return action_stop(link, args.id)
    if c == "set-mode":
        return action_set_mode(link, args.id, args.mode)
    if c == "probe":
        return action_probe(link, args.id)
    if c == "scan":
        action_scan(link, start=args.start, end=args.end)
        return True
    print(f"未知命令: {c}", file=sys.stderr)
    return False


if __name__ == "__main__":
    sys.exit(main())
