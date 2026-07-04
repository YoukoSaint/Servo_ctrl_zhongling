# 串口通讯日志使用指南

舵机控制上位机启动后会在 `logs/` 目录下生成 4 个日志文件，覆盖所有通讯事件的完整记录。本文档说明如何查阅、解读、定位故障。

## 1. 日志文件布局

```
logs/
├── servo_ctrl_YYYYMMDD_HHMMSS.log          主日志（连接/断开/错误/HEALTH）
├── servo_ctrl_YYYYMMDD_HHMMSS_protocol.log 协议流量（TX/RX 字节+帧）
├── servo_ctrl_YYYYMMDD_HHMMSS_audit.log    审计（每条用户操作）
└── servo_ctrl_YYYYMMDD_HHMMSS_health.log   周期 HEALTH 报告
```

每次启动生成一组新文件（按时间戳），不轮转覆盖。

| 文件 | 写入 logger | 级别 | 内容密度 |
|------|------------|------|----------|
| `*.log` | `servo.*` | DEBUG+ | 较少；连接、断开、错误、HEALTH |
| `*_protocol.log` | `servo.protocol` | DEBUG+ | **高**；每条 TX/RX 字节与帧都记 |
| `*_audit.log` | `servo.audit` | INFO | 较少；每条用户操作一行 |
| `*_health.log` | `servo.health` | INFO | 极少；每 60s 一行 |

> **HEALTH** 既写主日志（`*.log`）又写 `*_health.log`，便于同时按时间线（主日志）和按指标（health 文件）查阅。

## 2. 协议日志格式（`*_protocol.log`）

每行一条事件，时间戳精确到毫秒。

```
19:12:22.864 TX 14 bytes: #000P1500T1000!
19:12:22.864 RX 6 bytes
19:12:22.866 RX frame: #001P1500!
19:12:22.866 RX parsed: id=1 pwm=1500
```

DEBUG 级别还会写**字节级 HEX 转储**（默认关闭，DEBUG 开启时显示）：

```
19:12:22.864 TX HEX:
0000  23 30 30 30 50 31 35 30  30 54 31 30 30 30 21 0D  |#000P1500T1000.|
0010  0A                                                |.|
```

| 字段 | 含义 |
|------|------|
| `TX 14 bytes: #000P1500T1000!` | 发送 14 字节，帧文本 |
| `RX 6 bytes` | 收到 6 字节原始数据（**未解析**） |
| `RX frame: #OK!` | 解析出一个完整帧（已通过 `FrameParser`） |
| `RX parsed: id=1 pwm=1500` | 帧被 `parse_response` 成功识别并提取字段 |
| `RX unrecognized frame: ...` | 帧无法识别（看 `protocol.parse_response` 规则排查） |

### 故障定位示例

**症状 1：UI 提示"无应答"**

打开 `*_protocol.log`，找最近一次 `TX ...!` 之后的内容：

```
# 情况 A：完全没有 RX —— 总线不通
19:12:22.864 TX 14 bytes: #000P1500T1000!
（后面再无任何 RX）

# 情况 B：有 RX 字节但没解析出帧 —— 协议不匹配
19:12:22.864 TX 14 bytes: #000P1500T1000!
19:12:22.866 RX 4 bytes
（没有 RX frame 行）→ 看 RX HEX 确认实际收到的内容

# 情况 C：有 RX 帧但 parse_response 拒识
19:12:22.866 RX frame: #001???
19:12:22.866 RX unrecognized frame: #001???
```

**症状 2：循环控制"启动了但没动"**

1. 查 `*_audit.log` 是否有 `action=loop_start` 行 → 没有 = 程序根本没收到启动指令
2. 有 loop_start 但舵机没动 → 查 `*_protocol.log` 看 `loop_start` 之后有没有对应的 `TX` 帧

## 3. 审计日志格式（`*_audit.log`）

每行一条用户操作。所有字段都是 `key=value` 形式，便于 `grep` 筛选。

```
2026-07-03 19:12:22,864 [AUDIT] action=connect resource=COM3 result=OK baudrate=115200 kind=serial
2026-07-03 19:12:22,865 [AUDIT] action=move resource=servo:1 result=OK frame=#001P1500T1000! id=1 pwm=1500 time_ms=1000
2026-07-03 19:12:22,867 [AUDIT] action=loop_start resource=servo:1 result=OK id=1 start_angle=0 end_angle=90 period_ms=1000 cycles=4
2026-07-03 19:12:22,867 [AUDIT] action=loop_completed resource=servo:1 result=OK pairs_done=2 total_pairs=2 elapsed_s=4.02 target_s=4.0 drift_pct=0.5
```

| 字段 | 含义 |
|------|------|
| `action` | 操作类型：`connect` / `disconnect` / `move` / `query_position` / `loop_start` / `loop_completed` / `loop_aborted` / `emergency_stop_all` / ... |
| `resource` | 操作目标：串口名（`COM3`）/ 舵机 ID（`servo:1`）/ 广播（`broadcast:255`） |
| `result` | `OK` / `FAIL` |
| `reason` | 失败原因（仅 FAIL 时存在） |
| `frame` | 协议帧文本（仅写指令时存在） |
| 其它业务字段 | `id` / `pwm` / `time_ms` / `start_angle` / `cycles` / `period_ms` / `drift_pct` / ... |

### 常用查询

```bash
# 查所有 FAIL 操作
grep "result=FAIL" logs/servo_ctrl_*_audit.log

# 查所有 loop 相关
grep "action=loop" logs/servo_ctrl_*_audit.log

# 查特定舵机 ID 的所有操作
grep "resource=servo:3" logs/servo_ctrl_*_audit.log

# 查某次会话的所有操作（按时间）
less logs/servo_ctrl_20260703_191222_audit.log
```

## 4. HEALTH 报告格式（`*_health.log`）

每 60 秒自动输出一行（或在断开时输出一行）。

```
2026-07-03 19:12:22,867 HEALTH | reason=periodic port=COM3 baud=115200 kind=serial uptime=180.0s tx=12 rx_bytes=87 rx_frames=12 errors=0 since_last_tx=2.1s since_last_rx_frame=2.1s
```

| 字段 | 含义 |
|------|------|
| `reason` | `periodic`（周期）/ `disconnect`（断开时） |
| `port` | 串口名 |
| `baud` | 波特率 |
| `kind` | `serial`（真实） / `MockTransport`（自测） |
| `uptime` | 连接时长（秒） |
| `tx` | 累计成功发送帧数 |
| `rx_bytes` | 累计接收字节数 |
| `rx_frames` | 累计接收完整帧数 |
| `errors` | 累计错误数（open/send/read 失败） |
| `since_last_tx` | 距最近一次发送的秒数（-1 = 从未发送） |
| `since_last_rx_frame` | 距最近一次收到完整帧的秒数（-1 = 从未收到） |

### 故障信号

| 现象 | 可能问题 |
|------|----------|
| `since_last_rx_frame` 持续增长 | 总线没回应——查接线、波特率、供电 |
| `tx >> rx_frames` 且差值持续增大 | 真的丢应答 |
| `errors > 0` | 串口读写有异常，看主日志的 ERROR 详情 |
| `rx_bytes > 0` 但 `rx_frames = 0` | 收到字节但没有形成完整帧——查 `*_protocol.log` 的 RX HEX 找原因 |

## 5. 主日志（`*.log`）

完整时间线视图，含 `INFO`/`WARNING`/`ERROR`/`CRITICAL`。典型内容：

```
19:12:22,864 [INFO    ] servo.serial:131 - Opening serial port COM3 @ 115200 baud (timeout=0.050s)
19:12:22,866 [INFO    ] servo.serial:135 - Serial port COM3 opened successfully: is_open=True
19:12:22,866 [INFO    ] servo.serial:215 - Connecting transport: port=COM3 baud=115200 kind=SerialTransport
19:12:22,866 [INFO    ] servo.serial:251 - Transport connected: port=COM3 baud=115200 kind=SerialTransport
19:12:25,123 [WARNING ] servo.serial:347 - move dropped: transport not connected
19:12:25,123 [ERROR   ] servo.serial:229 - send failed: SerialException
```

## 6. 调试模式（启用 HEX 字节级转储）

`hex_dump` 写在 DEBUG 级别。开启方法：

```bash
# 方法 1：环境变量（启动前）
export SERVO_DEBUG=1
python -m src.main

# 方法 2：修改 logger.py 中 file_level
setup_logging(file_level=logging.DEBUG)   # 默认就是 DEBUG
```

启用后 `*_protocol.log` 会多出 `TX HEX:` / `RX HEX:` 段，每行 16 字节，可直接对照协议手册校验。

## 7. 实战故障排查清单

按优先级检查：

1. **看 `*_health.log`**：最近 1 行 HEALTH 的 `since_last_rx_frame` 是多少？
2. **看 `*_protocol.log` 最近 100 行**：有没有 `TX` 之后没有 `RX`？有没有 `RX unrecognized`？
3. **看 `*_audit.log`**：最近 1 个 `result=FAIL` 的 reason 是什么？
4. **看 `*.log` 主日志**：有没有 `ERROR` / `CRITICAL` 级别的堆栈？
5. **如果 1+2 都正常但舵机不动**：查硬件——是不是总线上有多个 ID 冲突？是不是供电不足？是不是 ID 在 controller 与 device 间不一致？
