# -*- coding: utf-8 -*-
"""
冒烟测试脚本自身的单元测试 —— 不依赖硬件。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# 让 scripts 目录可作为模块导入
import importlib.util
spec = importlib.util.spec_from_file_location(
    "serial_smoke", ROOT / "scripts" / "serial_smoke.py"
)
serial_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serial_smoke)  # type: ignore


class TestArgparse(unittest.TestCase):
    def test_help(self):
        # --help argparse 会主动 SystemExit(0)
        with self.assertRaises(SystemExit) as cm:
            serial_smoke.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_no_port_no_cmd(self):
        # 无 --port 也无子命令：返回 2
        code = serial_smoke.main([])
        self.assertEqual(code, 2)

    def test_unknown_port_returns_2(self):
        # 给一个不存在的串口：应返回 2
        code = serial_smoke.main(["--port", "NONE_EXISTENT_PORT", "read-id"])
        self.assertEqual(code, 2)


class TestActionGuards(unittest.TestCase):
    """动作参数边界检查（不发任何真实串口数据）。"""

    def test_move_id_out_of_range(self):
        # 用一个永远不开的 Link，验证参数校验在 open 之前就拒
        link = serial_smoke.Link("NEVER_OPEN", 115200)
        ok = serial_smoke.action_move(link, 255, 1500, 1000)
        self.assertFalse(ok)
        ok = serial_smoke.action_move(link, -1, 1500, 1000)
        self.assertFalse(ok)

    def test_move_pwm_out_of_range(self):
        link = serial_smoke.Link("NEVER_OPEN", 115200)
        self.assertFalse(serial_smoke.action_move(link, 1, 499, 1000))
        self.assertFalse(serial_smoke.action_move(link, 1, 2501, 1000))

    def test_move_time_out_of_range(self):
        link = serial_smoke.Link("NEVER_OPEN", 115200)
        self.assertFalse(serial_smoke.action_move(link, 1, 1500, 10000))
        self.assertFalse(serial_smoke.action_move(link, 1, 1500, -1))

    def test_set_mode_out_of_range(self):
        link = serial_smoke.Link("NEVER_OPEN", 115200)
        self.assertFalse(serial_smoke.action_set_mode(link, 1, 0))
        self.assertFalse(serial_smoke.action_set_mode(link, 1, 9))


class TestOkDetection(unittest.TestCase):
    def test_ok_detection(self):
        from src.protocol import parse_response
        # 模拟收到一帧 #000P1500!
        frames = ["#000P1500!"]
        ok = serial_smoke._ok(frames)
        self.assertTrue(ok)
        # 模拟收到一帧 #OK!
        ok = serial_smoke._ok(["#OK!"])
        self.assertTrue(ok)
        # 收到乱码
        ok = serial_smoke._ok(["garbage"])
        self.assertFalse(ok)
        # 收到空列表
        ok = serial_smoke._ok([])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
