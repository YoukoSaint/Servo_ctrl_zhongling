"""Tests for the localhost end-effector HTTP API."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.http_api import EndEffectorApiServer, StartResult


class TestEndEffectorApiServer(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = 0
        self.result = StartResult(True, "启动成功")

        def start_callback() -> StartResult:
            self.calls += 1
            return self.result

        self.server = EndEffectorApiServer(start_callback, port=0)
        self.server.start()
        host, port = self.server.address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.stop()

    def test_health(self) -> None:
        with urlopen(f"{self.base_url}/health", timeout=2.0) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "servo_ctrl")

    def test_start_uses_preconfigured_parameters(self) -> None:
        request = Request(f"{self.base_url}/start", data=b"", method="POST")
        with urlopen(request, timeout=2.0) as response:
            payload = json.load(response)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.calls, 1)
        self.assertEqual(payload, {"ok": True, "message": "启动成功"})

    def test_rejected_start_returns_conflict(self) -> None:
        self.result = StartResult(False, "串口未连接")
        request = Request(f"{self.base_url}/start", data=b"", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2.0)
        self.assertEqual(caught.exception.code, 409)
        payload = json.load(caught.exception)
        self.assertEqual(payload["error"], "串口未连接")

    def test_start_rejects_request_parameters(self) -> None:
        request = Request(f"{self.base_url}/start", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2.0)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
