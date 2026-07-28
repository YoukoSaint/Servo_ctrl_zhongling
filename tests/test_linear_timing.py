"""Linear loop timing tests using the project's actual PyQt5 event loop."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.controller import LoopParams, LoopRunner, STATE_COMPLETED


class _RecordingClient:
    def __init__(self) -> None:
        self.moves: list[tuple[float, int, int, int]] = []
        self.modes: list[tuple[int, int]] = []

    def ensure_mode(self, servo_id: int, mode: int) -> bool:
        self.modes.append((servo_id, mode))
        return True

    def move(self, servo_id: int, pwm: int, action_ms: int) -> bool:
        self.moves.append((time.monotonic(), servo_id, pwm, action_ms))
        return True

    def stop(self, _servo_id: int) -> bool:
        return True


class TestLinearTiming(unittest.TestCase):
    ACTION_MS = 30
    DWELL_MS = 50

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication(sys.argv)

    def test_action_and_dwell_are_applied_to_each_leg(self) -> None:
        client = _RecordingClient()
        runner = LoopRunner(client)  # type: ignore[arg-type]
        finished_at: list[float] = []
        event_loop = QEventLoop()
        runner.finished.connect(lambda: finished_at.append(time.monotonic()))
        runner.finished.connect(event_loop.quit)

        params = LoopParams(
            servo_id=0,
            start_angle=-90,
            end_angle=90,
            action_ms=self.ACTION_MS,
            dwell_ms=self.DWELL_MS,
            cycles=2,
            mode=1,
        )
        self.assertTrue(runner.start(params))
        QTimer.singleShot(1000, event_loop.quit)
        event_loop.exec_()

        # 到起点、去终点、回起点，共 3 条运动指令。
        self.assertEqual(len(client.moves), 3)
        self.assertEqual(client.modes, [(0, 1)])
        self.assertTrue(finished_at)
        self.assertEqual(runner.state, STATE_COMPLETED)
        self.assertTrue(all(move[3] == self.ACTION_MS for move in client.moves))

        expected_gap_s = (self.ACTION_MS + self.DWELL_MS) / 1000.0
        move_gaps = [
            client.moves[index + 1][0] - client.moves[index][0]
            for index in range(len(client.moves) - 1)
        ]
        for gap in move_gaps:
            self.assertGreaterEqual(gap, expected_gap_s * 0.75)

        final_gap = finished_at[0] - client.moves[-1][0]
        self.assertGreaterEqual(final_gap, expected_gap_s * 0.75)

    def test_one_cycle_is_single_trip_without_return(self) -> None:
        client = _RecordingClient()
        runner = LoopRunner(client)  # type: ignore[arg-type]
        event_loop = QEventLoop()
        runner.finished.connect(event_loop.quit)
        params = LoopParams(
            servo_id=0,
            start_angle=-45,
            end_angle=45,
            action_ms=10,
            dwell_ms=10,
            cycles=1,
            mode=1,
        )
        self.assertTrue(runner.start(params))
        QTimer.singleShot(500, event_loop.quit)
        event_loop.exec_()

        self.assertEqual(len(client.moves), 2)
        self.assertEqual(runner.state, STATE_COMPLETED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
