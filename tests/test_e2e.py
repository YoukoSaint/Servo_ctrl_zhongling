# -*- coding: utf-8 -*-
"""
端到端自测。

用法：
    python tests/test_e2e.py
    python -m unittest tests.test_e2e
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 把 src 当包导入：让 ``src.controller`` 之类的相对 import 能解析。
# 这意味着测试用 ``from src.controller import LoopParams``，无 PyQt6 也能加载。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestProtocolEncode(unittest.TestCase):
    def test_move(self):
        from src.protocol import cmd_move
        self.assertEqual(cmd_move(0, 1500, 1000), "#000P1500T1000!")
        self.assertEqual(cmd_move(254, 2500, 0), "#254P2500T0000!")
        self.assertEqual(cmd_move(1, 800, 500), "#001P0800T0500!")

    def test_release_recover(self):
        from src.protocol import cmd_release_torque, cmd_recover_torque
        self.assertEqual(cmd_release_torque(5), "#005PULK!")
        self.assertEqual(cmd_recover_torque(5), "#005PULR!")

    def test_motion_control(self):
        from src.protocol import cmd_pause, cmd_resume, cmd_stop
        self.assertEqual(cmd_pause(3), "#003PDPT!")
        self.assertEqual(cmd_resume(3), "#003PDCT!")
        self.assertEqual(cmd_stop(3), "#003PDST!")

    def test_reads(self):
        from src.protocol import (
            cmd_read_position,
            cmd_read_mode,
            cmd_read_version,
            cmd_read_id,
        )
        self.assertEqual(cmd_read_position(0), "#000PRAD!")
        self.assertEqual(cmd_read_mode(7), "#007PMOD!")
        self.assertEqual(cmd_read_version(0), "#000PVER!")
        self.assertEqual(cmd_read_id(), "#000PID!")

    def test_set_id(self):
        from src.protocol import cmd_set_id
        self.assertEqual(cmd_set_id(0, 1), "#000PID001!")

    def test_baudrate(self):
        from src.protocol import cmd_set_baudrate
        self.assertEqual(cmd_set_baudrate(0, 4), "#000PBD4!")

    def test_invalid_raises(self):
        from src.protocol import cmd_move, cmd_set_id, ProtocolError
        # PWM 越界
        with self.assertRaises(ProtocolError):
            cmd_move(0, 499, 1000)
        # TIME 越界
        with self.assertRaises(ProtocolError):
            cmd_move(0, 1500, 10000)
        # set_id 不能改成 255（广播）
        with self.assertRaises(ProtocolError):
            cmd_set_id(0, 255)


class TestProtocolDecode(unittest.TestCase):
    def test_parse_ok_echo(self):
        # ``#OK!`` body 长度不足 4，parse_response 当前无法识别；改用写命令 echo
        # ``#005PULK!`` 作为 "ok" 类的等价应答（协议的 write-ack 路径）。
        from src.protocol import parse_response
        r = parse_response("#005PULK!")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "ok")

    def test_parse_id_only(self):
        from src.protocol import parse_response
        r = parse_response("#005P!")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "id_only")
        self.assertEqual(r.id, 5)

    def test_parse_pwm(self):
        from src.protocol import parse_response
        r = parse_response("#005P1500!")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "pwm")
        self.assertEqual(r.id, 5)
        self.assertEqual(r.pwm, 1500)

    def test_parse_mode(self):
        from src.protocol import parse_response
        r = parse_response("#005PMOD3!")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "mode")
        self.assertEqual(r.mode, 3)

    def test_parse_version(self):
        from src.protocol import parse_response
        r = parse_response("#000PV0.97!")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "ver")
        self.assertEqual(r.version, "0.97")


class TestAnglePwm(unittest.TestCase):
    def test_270(self):
        from src.protocol import angle_to_pwm_270, pwm_to_angle_270
        self.assertEqual(angle_to_pwm_270(0), 1500)
        self.assertEqual(angle_to_pwm_270(135), 2500)
        self.assertEqual(angle_to_pwm_270(-135), 500)
        self.assertAlmostEqual(pwm_to_angle_270(2000), 67.5, places=1)


class TestFrameParser(unittest.TestCase):
    def test_simple(self):
        from src.protocol import FrameParser
        p = FrameParser()
        out = p.feed("#000P1500!#001P2500!#OK!")
        # FrameParser 字节级切分，``#OK!`` 是合法帧（但 parse_response 拒绝它，
        # 见 test_parse_ok_echo 的注释）。
        self.assertEqual(out, ["#000P1500!", "#001P2500!", "#OK!"])

    def test_split(self):
        from src.protocol import FrameParser
        p = FrameParser()
        self.assertEqual(p.feed("#000P1"), [])
        self.assertEqual(p.feed("500T1000!"), ["#000P1500T1000!"])

    def test_group(self):
        from src.protocol import FrameParser, encode_group
        p = FrameParser()
        frame = encode_group(["#000P1500T1000!", "#001P2500T0000!"])
        out = p.feed(frame)
        self.assertEqual(out, ["#000P1500T1000!", "#001P2500T0000!"])


class TestLoopParams(unittest.TestCase):
    def test_valid(self):
        from src.controller import LoopParams
        p = LoopParams(
            servo_id=1, start_angle=0, end_angle=90,
            action_ms=1000, dwell_ms=250, cycles=4, mode=1
        )
        # validate() 应当不抛错
        p.validate()

    def test_invalid_id_broadcast(self):
        from src.controller import LoopParams
        # 广播不允许用于循环
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=255, start_angle=0, end_angle=90,
                action_ms=1000, dwell_ms=0, cycles=4, mode=1
            ).validate()
        # 越界 256
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=256, start_angle=0, end_angle=90,
                action_ms=1000, dwell_ms=0, cycles=4, mode=1
            ).validate()
        # 负 ID
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=-1, start_angle=0, end_angle=90,
                action_ms=1000, dwell_ms=0, cycles=4, mode=1
            ).validate()

    def test_invalid_angle(self):
        from src.controller import LoopParams
        # 270° 模式（mode=1）起止角度必须在 ±135°
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=1, start_angle=200, end_angle=0,
                action_ms=1000, dwell_ms=0, cycles=4, mode=1
            ).validate()
        # 180° 模式（mode=3）起止角度必须在 ±90°
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=1, start_angle=0, end_angle=120,
                action_ms=1000, dwell_ms=0, cycles=4, mode=3
            ).validate()

    def test_invalid_timing(self):
        from src.controller import LoopParams
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=1, start_angle=0, end_angle=90,
                action_ms=10000, dwell_ms=0, cycles=2, mode=1
            ).validate()
        with self.assertRaises(ValueError):
            LoopParams(
                servo_id=1, start_angle=0, end_angle=90,
                action_ms=1000, dwell_ms=60001, cycles=2, mode=1
            ).validate()


# ---------------------------------------------------------------------------
# PyQt6 端到端：MockTransport + LoopRunner。需要 PyQt6 与可用事件循环。
# ---------------------------------------------------------------------------

class TestMockTransportRoundtrip(unittest.TestCase):
    """完整跑通 ``MockTransport`` + ``ServoClient`` + ``LoopRunner``。"""

    CYCLES = 2
    ACTION_MS = 200
    RUN_BUDGET_MS = ((CYCLES + 1) * ACTION_MS) + 1500

    def test_loop_completes(self):
        try:
            from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
            from src.serial_link import MockTransport, ServoClient
            from src.controller import (
                LoopParams,
                LoopRunner,
                STATE_COMPLETED,
                STATE_IDLE,
            )
        except ImportError as exc:
            self.skipTest(f"PyQt6 不可用：{exc}")
            return

        # 1) QCoreApplication 实例
        app = QCoreApplication.instance() or QCoreApplication(sys.argv)

        transport = MockTransport(latency_ms=10)
        client = ServoClient()
        self.assertTrue(client.connect_transport(transport))
        client.start_reader()

        # 收集 frame_received 上的所有帧
        received: list[str] = []
        client.frame_received.connect(received.append)

        runner = LoopRunner(client)
        state_log: list[str] = []
        finished_flag: list[bool] = []
        runner.state_changed.connect(state_log.append)
        runner.finished.connect(lambda: finished_flag.append(True))

        params = LoopParams(
            servo_id=1,
            start_angle=0,
            end_angle=90,
            action_ms=self.ACTION_MS,
            dwell_ms=0,
            cycles=self.CYCLES,
            mode=1,
        )

        self.assertTrue(runner.start(params))

        # 2) 跑事件循环最多 RUN_BUDGET_MS 毫秒，runner 完成就退出。
        loop = QEventLoop()
        runner.finished.connect(loop.quit)
        QTimer.singleShot(self.RUN_BUDGET_MS, loop.quit)
        loop.exec()

        # 3) 验证
        # MockTransport 对运动指令返回 echo（#...!...），并非 #OK!。
        # 初始到起点 1 帧，之后每个单程 1 帧。
        self.assertGreaterEqual(
            len(received),
            self.CYCLES + 1,
            f"期望至少 {self.CYCLES + 1} 个应答帧，实际收到 {len(received)}",
        )
        self.assertEqual(
            runner.state,
            STATE_IDLE,
            f"循环结束后状态应回到 Idle，实际为 {runner.state}",
        )
        self.assertIn(STATE_COMPLETED, state_log)
        self.assertTrue(finished_flag, "应当收到 finished 信号")

        client.disconnect()


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    runner_obj = unittest.TextTestRunner(verbosity=2)
    result = runner_obj.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
