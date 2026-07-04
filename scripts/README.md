# 串口冒烟测试脚本 — `scripts/serial_smoke.py`

**在 UI 上线之前，必须先用这个脚本在终端里直接跟舵机对话，验证物理层与协议层都正常工作。**

## 用途

这是 UI 与 PyQt 业务代码之外的**最低验证层**。它：

- 不依赖 PyQt5
- 只依赖 `pyserial` + `src.protocol.py`
- 能在终端里**逐帧**显示 TX / RX 字节、解析结果、是否成功
- 是隔离"通讯问题"和"UI 问题"的最快方法

## 两种入口

### A) CLI —— 单条命令直接发

```bash
# 读 ID
python scripts/serial_smoke.py --port COM8 read-id

# 读位置
python scripts/serial_smoke.py --port COM8 read-position --id 1

# 读模式 / 版本
python scripts/serial_smoke.py --port COM8 read-mode --id 1
python scripts/serial_smoke.py --port COM8 read-version --id 1

# 运动到 1500 PWM，1 秒到位
python scripts/serial_smoke.py --port COM8 move --id 1 --pwm 1500 --time 1000

# 释放 / 恢复扭矩
python scripts/serial_smoke.py --port COM8 release --id 1
python scripts/serial_smoke.py --port COM8 recover --id 1

# 暂停 / 继续 / 停止
python scripts/serial_smoke.py --port COM8 pause --id 1
python scripts/serial_smoke.py --port COM8 resume --id 1
python scripts/serial_smoke.py --port COM8 stop --id 1

# 设置工作模式 (1..8)
python scripts/serial_smoke.py --port COM8 set-mode --id 1 --mode 3

# 探测单个 ID：读位置+模式+版本
python scripts/serial_smoke.py --port COM8 probe --id 1

# 扫描 0..254 找在总线上的 ID（最常用的首步操作）
python scripts/serial_smoke.py --port COM8 scan

# 扫描指定范围
python scripts/serial_smoke.py --port COM8 scan --start 0 --end 10
```

### B) 交互 REPL —— 持续对话

```bash
python scripts/serial_smoke.py --port COM8
```

```
=== 舵机串口冒烟测试 REPL ===
输入 'help' 查看命令，'quit' 退出。
servo> open COM8
[link] open COM8 @ 115200 (timeout=0.05s)
[link] opened: is_open=True
servo> scan
[scan] 0..254, timeout_per=0.2
[scan]   id=  0  no reply
[scan]   id=  1  -> ['#001P1500!']
[scan]   id=  2  no reply
...
[scan]   id=254  no reply
[scan] done, found 1: [1]
servo> probe 1
[probe] id=1
[TX]  14 bytes  '#001PRAD!'
     HEX  23 30 30 31 50 52 41 44 21 0D 0A
[RX]   9 bytes  HEX 23 30 30 31 50 31 35 30 30 21 0D 0A
     FRAME '#001P1500!'
[result] OK
servo> move 1 1500 1000
[TX]  14 bytes  '#001P1500T1000!'
[RX]   6 bytes  HEX 23 4F 4B 21 0D 0A
     FRAME '#OK!'
[result] OK
servo> status
port=COM8 baud=115200 open=True tx=4 rx_bytes=21 rx_frames=4
servo> quit
bye.
```

## 典型排查流程

按"从硬件到协议"顺序逐项排除：

### 1. 串口能否打开？

```bash
python scripts/serial_smoke.py --port COM8 read-id
```

- **打开失败** → 看错误提示里的 5 条排查步骤（线序、占用、波特率、供电、COM 号）
- **能打开但没收到任何字节** → 串口硬件层 OK，问题在协议层（ID 不对 / 波特率不对 / 舵机没上电）

### 2. 哪个 ID 在线？

```bash
python scripts/serial_smoke.py --port COM8 scan
```

返回 `found [...]` 列表就是总线上有响应的 ID。

### 3. 单个 ID 健康度

```bash
python scripts/serial_smoke.py --port COM8 probe --id 1
```

- 位置+模式+版本三个都 OK → 通讯完全正常，可以上 UI
- 只有一个 OK → 看是哪个失败，对照 `protocol.md` 排查

### 4. 验证运动指令

```bash
python scripts/serial_smoke.py --port COM8 move --id 1 --pwm 1500 --time 1000
```

看舵机是否真的转动到中位。回到 ID=0 时再发同样的指令应能回到原位（视模式而定）。

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 命令成功（应答被识别） |
| 1 | 命令执行了但应答未被识别（如舵机没接、超时、协议不匹配） |
| 2 | 串口未打开 / 参数错误 / argparse 报错 |

## 日志

脚本**不写文件**，所有诊断信息打到 stdout。如果需要归档，把输出重定向：

```bash
python scripts/serial_smoke.py --port COM8 scan > scan_$(date +%Y%m%d_%H%M%S).log
```

如果脚本能跑通但 UI 不行，问题**一定在 PyQt5 那一侧**（信号未连接、按钮未绑、QSS 遮挡等），与通讯无关。

## 故障对照表

| 现象 | 可能原因 | 怎么查 |
|------|----------|--------|
| `could not open port` | 串口被占用 / 设备没插 / COM 号错 | 设备管理器 |
| `[TX] 14 bytes` 后无 `[RX]` | 接线错（TX/RX 交叉）/ 舵机没上电 | 查线序 |
| `[RX]` 但无 `FRAME` | 波特率不一致 / 数据是噪声 | 用示波器或逻辑分析仪看 |
| `[RX] FRAME '#000Pxxx!'` 但舵机没动 | ID 错（总线不是这个 ID） | 跑 `scan` 找真实 ID |
| 收到乱码 | 电压不稳 / 接触不良 | 重新插拔、换线 |
| 一切正常但循环不工作 | 协议正确，业务逻辑问题 | 进 UI / 看 `logs/*_protocol.log` |
