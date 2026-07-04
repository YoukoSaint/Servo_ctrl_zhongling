#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 诊断脚本 —— 不用 PyQt5，模拟 UI 代码路径，定位 UI 无法控制舵机的断点。

用法：
    python scripts/ui_diag.py --port COM8

输出每一步是否成功，找出 UI 卡在哪一步。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serial
from src.protocol import (
    FrameParser, parse_response,
    cmd_move, cmd_read_position, cmd_read_id, cmd_read_version,
    cmd_release_torque, cmd_recover_torque, cmd_pause, cmd_resume, cmd_stop,
    cmd_set_mode,
)
from src.logger import setup_logging


def check(step: str, ok: bool, detail: str = "") -> bool:
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {step}{' — ' + detail if detail else ''}")
    return ok


def main():
    p = argparse.ArgumentParser(description="UI 诊断")
    p.add_argument("--port", default="COM8")
    p.add_argument("--baud", type=int, default=115200)
    args = p.parse_args()

    setup_logging(log_dir="logs")
    port = args.port
    baud = args.baud
    all_ok = True

    # ── 第 1 步：串口能否打开？──────────
    print("\n=== 第 1 步：打开串口 ===")
    try:
        s = serial.Serial(port, baud, timeout=0.05)
        all_ok &= check(f"打开 {port}", s.is_open)
    except Exception as e:
        all_ok &= check(f"打开 {port}", False, str(e))
        print(f"\n*  串口打不开，UI 也无法打开。排查：")
        print("   1) 设备管理器里 COM8 是否在？")
        print("   2) 厂家上位机是否还开着（会占用串口）？")
        print("   3) USB-TTL 线是否插好？")
        return 2
    if not s.is_open:
        return 2

    # ── 第 2 步：读 ID ──────────
    print("\n=== 第 2 步：读 ID（验证舵机在线）===")
    frame = cmd_read_id()
    raw = (frame + "\r\n").encode()
    s.write(raw); s.flush()
    print(f"  TX: {frame!r}")
    time.sleep(0.1)
    n = s.in_waiting
    rx = s.read(n) if n else b""
    parser = FrameParser()
    frames = parser.feed(rx)
    print(f"  RX [{len(rx)} bytes]: {rx!r}")
    print(f"  解析帧: {frames}")
    all_ok &= check("舵机在线", len(frames) > 0, f"收到 {len(frames)} 帧")

    # ── 第 3 步：读位置 ──────────
    print("\n=== 第 3 步：读位置（验证查询协议）===")
    frame = cmd_read_position(0)
    s.write((frame + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    rx = s.read(s.in_waiting or 1)
    parser = FrameParser()
    frames = parser.feed(rx)
    parsed = parse_response(frames[0]) if frames else None
    pwm_val = parsed.pwm if parsed and parsed.kind == "pwm" else "?"
    print(f"  TX: {frame!r} → RX: {rx!r} → PWM={pwm_val}")
    all_ok &= check("读位置", parsed is not None and parsed.kind == "pwm",
                    f"PWM={pwm_val}")

    # ── 第 4 步：读版本 ──────────
    print("\n=== 第 4 步：读版本（验证 v2.x 格式）===")
    frame = cmd_read_version(0)
    s.write((frame + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    rx = s.read(s.in_waiting or 1)
    parser = FrameParser()
    frames = parser.feed(rx)
    parsed = parse_response(frames[0]) if frames else None
    ver = parsed.version if parsed and parsed.kind == "ver" else "?"
    print(f"  TX: {frame!r} → RX: {rx!r} → version={ver!r}")
    all_ok &= check("读版本", parsed is not None and parsed.kind == "ver",
                    f"version={ver!r}")

    # ── 第 5 步：运动指令 ──────────
    print("\n=== 第 5 步：运动到 PWM=2000（模拟 UI '运动到目标 PWM' 按钮）===")
    before_frame = cmd_read_position(0)
    s.write((before_frame + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    rx_before = s.read(s.in_waiting or 1)
    frames_before = FrameParser().feed(rx_before)
    pwm_before = parse_response(frames_before[0]).pwm if frames_before else -1
    print(f"  运动前 PWM={pwm_before}")

    frame = cmd_move(0, 2000, 1000)  # ← 这就是 UI 的 BasicController.move_to_pwm() 做的事
    s.write((frame + "\r\n").encode()); s.flush()
    print(f"  发送: {frame!r}")
    time.sleep(1.5)  # 等舵机转完

    after_frame = cmd_read_position(0)
    s.write((after_frame + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    rx_after = s.read(s.in_waiting or 1)
    frames_after = FrameParser().feed(rx_after)
    pwm_after = parse_response(frames_after[0]).pwm if frames_after else -1
    print(f"  运动后 PWM={pwm_after}")

    moved = pwm_after != pwm_before
    all_ok &= check("舵机转动", moved,
                    f"PWM {pwm_before} → {pwm_after}")

    # 移回去
    s.write((cmd_move(0, pwm_before, 500) + "\r\n").encode()); s.flush()
    time.sleep(1.0)

    # ── 第 6 步：释放/恢复 ──────────
    print("\n=== 第 6 步：释放扭矩 + 恢复扭矩 ===")
    s.write((cmd_release_torque(0) + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    all_ok &= check("释放扭矩（已发送）", True, "静默指令")
    s.write((cmd_recover_torque(0) + "\r\n").encode()); s.flush()
    time.sleep(0.1)
    all_ok &= check("恢复扭矩（已发送）", True, "静默指令")

    s.close()

    # ── 结论 ──────────
    print("\n" + "=" * 50)
    if all_ok:
        print("*  所有步骤通过 —— 串口通讯完全正常")
        print()
        print(">  问题在 PyQt5 UI 侧。可能原因：")
        print("   1) 端口下拉框没选 COM8，或选了但没点 '连接'")
        print("   2) '连接' 按钮点击后输出什么？看状态栏")
        print("   3) 连接成功后 '基础控制' 标签页的按钮是否可用？")
        print("   4) 点了 '运动到目标 PWM' 后，状态栏显示什么？")
        print("   5) \\src\\ui\\main_window.py 是否有 import 报错？")
        print()
        print(">  排查下一步：启动 UI 后打开 logs/ 下最新的 4 个文件，")
        print("   看 *_protocol.log 有没有 TX/RX 记录")
    else:
        print("*  部分步骤失败 —— 通讯有问题")
        print("   检查：线序、供电、波特率、ID 是否正确")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
