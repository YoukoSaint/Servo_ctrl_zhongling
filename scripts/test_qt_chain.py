#!/usr/bin/env python3
"""绕过 UI，直接测 PyQt5 的 ServoClient 链能不能控制舵机。

用法：
    python scripts/test_qt_chain.py

如果这个脚本能控制舵机但 UI 不能，bug 在 UI 层（信号/按钮/绑定）。
如果这个脚本也不能，bug 在 serial_link.py 层（transport/client/controller）。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须先创建 QApplication 才能用 QTimer（Signal/Slot 事件循环）
from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from src.logger import setup_logging
from src.serial_link import SerialTransport, ServoClient
from src.controller import BasicController
from PyQt5.QtCore import QTimer
import time

setup_logging(log_dir="logs")

print("=== 第 1 步：创建 transport + client ===")
transport = SerialTransport("COM8", 115200, timeout=0.05)
client = ServoClient()
ok = client.connect_transport(
    transport,
    port_name="COM8",
    baudrate=115200,
)
print(f"  connect_transport -> {ok}")
print(f"  transport.is_open() -> {transport.is_open()}")
print(f"  client.tx_count = {client.tx_count}")

print("\n=== 第 2 步：创建 controller + start_reader ===")
ctrl = BasicController(client)
client.start_reader()
# 给 events 一点时间跑
app.processEvents()

print("\n=== 第 3 步：读位置 ===")
ok = ctrl.query_position(0)
print(f"  query_position -> {ok}")
time.sleep(0.15)
app.processEvents()

print("\n=== 第 4 步：运动到 2500（大范围移动，肉眼可确认）===")
ok = ctrl.move_to_pwm(0, 2500, 2000)
print(f"  move_to_pwm(0, 2500, 2000) -> {ok}")
# 等舵机转完 + 等 reader 读到数据
for i in range(5):
    time.sleep(0.4)
    app.processEvents()

print("\n=== 第 5 步：读位置确认 ===")
ok = ctrl.query_position(0)
print(f"  query_position -> {ok}")
time.sleep(0.15)
app.processEvents()

print(f"\n=== 第 6 步：移回中位 ===")
ok = ctrl.move_to_pwm(0, 1500, 2000)
print(f"  move_to_pwm(0, 1500) -> {ok}")
time.sleep(2.5)
app.processEvents()

print(f"\n=== 统计 ===")
print(f"  tx_count      = {client.tx_count}")
print(f"  rx_byte_count = {client.rx_byte_count}")
print(f"  rx_frame_count = {client.rx_frame_count}")
print(f"  error_count   = {client.error_count}")
print(f"  transport kind = {client.transport_kind}")

if client.tx_count >= 1 and client.error_count == 0:
    print("\n*** OK: ServoClient/BasicController 链正常 ***")
    print("    如果 UI 不能控制，排查 MainWindow 的信号连接和按钮绑定")
else:
    print("\n*** FAIL: 链本身有问题 ***")
    if client.error_count > 0:
        print(f"    错误计数={client.error_count}，看 logs/ 下最新日志中的 ERROR 行")
    if client.tx_count == 0:
        print("    tx=0! transport.send() 从未被调用")

# 清理
client.stop_reader()
transport.close()
print("\nDone.")
